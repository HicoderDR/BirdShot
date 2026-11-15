# SPDX-License-Identifier: AGPL-3.0-only
"""Read only registered JPEG location tags and exact-name paired metadata sidecars."""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
import sys

from comparison_prepare import atomic, sha256
from inspect_paired_raw import SELECTORS, PACKAGE_SHA256, TOOL_VERSION, base_name, position_fields, valid_coordinates, nonempty

SIDECARS = {".xmp", ".dop", ".pp3", ".aae", ".xml", ".mie", ".exv"}


def paired_sidecars(source, photos):
    directories = {}
    for photo in photos:
        primary = Path(photo["primary"]["relative"])
        parent = (source / primary.parent).resolve()
        names = {primary.stem.casefold(), primary.name.casefold(),
                 *[Path(record["relative"]).name.casefold() for record in photo["companions"]]}
        entry = directories.setdefault(parent, {})
        for name in names:
            entry.setdefault(name, set()).add(photo["id"])
    found, unpaired = [], 0
    for directory, allowed in directories.items():
        if not directory.is_relative_to(source):
            raise RuntimeError("Registered parent directory is outside the authorized source")
        for path in directory.iterdir():
            if not path.is_file() or path.suffix.casefold() not in SIDECARS:
                continue
            if path.stem.casefold() not in allowed:
                unpaired += 1
                continue
            if not path.resolve().is_relative_to(source):
                raise RuntimeError("Paired sidecar resolves outside the authorized source")
            found.append({"path": path, "photoIds": sorted(allowed[path.stem.casefold()])})
    return found, unpaired


def targeted_text_fields(path):
    """Conservative text-only fallback for DxO/PP3; no unrelated metadata is returned."""
    if path.suffix.lower() not in {".dop", ".pp3"} or path.stat().st_size > 2 * 1024 * 1024:
        return None
    pattern = re.compile(r"""^\s*["']?(GPS[A-Za-z]*|Latitude|Longitude|Location|LocationName|City|Sublocation|SubLocation|Province|State|Country|CountryCode)["']?\s*[:=]\s*(.*?)\s*[,;]?\s*$""", re.I)
    fields = {}
    with path.open("r", encoding="utf-8-sig", errors="strict") as stream:
        for line in stream:
            match = pattern.match(line)
            if match:
                fields["PairedText:" + match[1]] = match[2].strip().strip('"\'')
    return fields


def inspect(job, tool, package):
    if sha256(package) != PACKAGE_SHA256 or subprocess.check_output([str(tool), "-ver"], text=True).strip() != TOOL_VERSION:
        raise RuntimeError("Official ExifTool distribution/version verification failed")
    manifest = json.loads((job / "manifest.json").read_text(encoding="utf-8"))
    source = Path(manifest["source"]).resolve(strict=True)
    inputs = []
    for photo in manifest["photos"]:
        original = photo["primary"]
        path = (source / original["relative"]).resolve()
        if not path.is_relative_to(source):
            raise RuntimeError("Registered JPEG path is outside the source")
        stat = path.stat()
        if stat.st_size != original["size"] or stat.st_mtime_ns != original["mtimeNs"]:
            raise RuntimeError("Registered original JPEG changed; cannot reuse the original pairing")
        inputs.append({"kind": "jpeg", "path": path, "photoIds": [photo["id"]],
                       "before": {"size": stat.st_size, "mtimeNs": stat.st_mtime_ns}})
    sidecars, unpaired = paired_sidecars(source, manifest["photos"])
    for sidecar in sidecars:
        stat = sidecar["path"].stat()
        inputs.append({"kind": "sidecar", **sidecar, "before": {"size": stat.st_size, "mtimeNs": stat.st_mtime_ns}})
    counts = Counter({"registeredJPEG": len(manifest["photos"]), "jpegParsed": 0, "jpegGPSFiles": 0,
                      "jpegValidCoordinateFiles": 0, "jpegLocationTextFiles": 0, "jpegEmptyOrInvalidGPSFiles": 0,
                      "jpegErrors": 0, "jpegUnsupported": 0, "jpegWarnings": 0,
                      "pairedSidecars": len(sidecars), "unpairedSidecarsSkipped": unpaired,
                      "sidecarParsed": 0, "sidecarToolUnsupported": 0,
                      "sidecarTextFallbackChecked": 0, "sidecarReadErrors": 0, "sidecarGPSFiles": 0,
                      "sidecarLocationTextFiles": 0, "sidecarUnresolved": 0, "sourceChanged": 0})
    result = {"version": "registered-jpeg-and-sidecar-location-v1", "status": "running",
              "startedAt": datetime.now().isoformat(),
              "tool": {"name": "ExifTool", "version": TOOL_VERSION, "distributionSha256": PACKAGE_SHA256,
                       "checksumSource": "https://exiftool.org/checksums-13.63.txt"},
              "scope": {"jpeg": "registered originals only", "sidecars": "same directory, exact registered stem or full photo filename",
                        "selectors": SELECTORS, "uploads": False, "pixelDecode": False,
                        "fallback": "DOP/PP3 UTF-8 bounded line-based geographic-key check only; not a full editor-settings parser"},
              "statistics": dict(counts), "files": []}
    destination = job / "revisions" / "jpeg-sidecar-location-v1.json"
    atomic(destination, result)
    for offset in range(0, len(inputs), 64):
        batch = inputs[offset:offset + 64]
        command = [str(tool), "-json", "-G1:3:4", "-s", "-n", "-a", "-q", "-api", "GeoLocation=0",
                   *SELECTORS, *[str(record["path"]) for record in batch]]
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=240, check=False)
        try:
            rows = json.loads(completed.stdout)
        except ValueError as error:
            raise RuntimeError("Location parser failed; no absent-location conclusion may be inferred") from error
        if completed.returncode not in {0, 1} or not isinstance(rows, list):
            raise RuntimeError("Location parser did not complete successfully")
        returned = {str(Path(row["SourceFile"]).resolve()).casefold(): row for row in rows}
        expected = {str(record["path"]).casefold() for record in batch}
        if set(returned) != expected or len(returned) != len(rows):
            raise RuntimeError("Metadata parser returned missing, duplicate or unregistered sources")
        for record in batch:
            path = record["path"]
            tags = returned[str(path).casefold()]
            errors = [value for key, value in tags.items() if base_name(key) == "Error"]
            warnings = [value for key, value in tags.items() if base_name(key) == "Warning"]
            file_type = next((value for key, value in tags.items() if base_name(key) == "FileType"), None)
            gps, locations = position_fields(tags)
            row = {"kind": record["kind"], "photoIds": record["photoIds"], "relative": path.relative_to(source).as_posix(),
                   "actualFileType": file_type, "status": "parsed" if not errors else "unsupported" if any(
                       "Unknown file type" in str(error) for error in errors) else "error",
                   "gpsTags": gps, "locationTags": locations, "warnings": warnings, "errors": errors,
                   "before": record["before"]}
            prefix = record["kind"]
            if record["kind"] == "jpeg":
                counts["jpegWarnings"] += bool(warnings)
                if row["status"] == "parsed" and file_type == "JPEG":
                    counts["jpegParsed"] += 1
                else:
                    counts["jpegUnsupported" if row["status"] == "unsupported" else "jpegErrors"] += 1
                    if not errors:
                        row.update(status="error", errors=["Actual file type is not JPEG"])
            elif row["status"] == "parsed":
                counts["sidecarParsed"] += 1
            else:
                counts["sidecarToolUnsupported" if row["status"] == "unsupported" else "sidecarReadErrors"] += 1
                try:
                    fallback = targeted_text_fields(path)
                    if fallback is not None:
                        counts["sidecarTextFallbackChecked"] += 1
                        row["fallbackFields"] = fallback
                        fallback_gps, fallback_locations = position_fields(fallback)
                        row["gpsTags"].update(fallback_gps)
                        row["locationTags"].update(fallback_locations)
                        row["status"] = "checked-text-selectors"
                        if fallback:
                            counts["sidecarUnresolved"] += 1
                    else:
                        counts["sidecarUnresolved"] += 1
                except (OSError, UnicodeError) as error:
                    counts["sidecarUnresolved"] += 1
                    row["fallbackError"] = str(error)
            gps, locations = row["gpsTags"], row["locationTags"]
            counts[prefix + "GPSFiles"] += bool(gps)
            counts[prefix + "LocationTextFiles"] += any(nonempty(value) for value in locations.values())
            coordinates, note = valid_coordinates(gps)
            row.update(coordinates=coordinates, positionNote=note)
            if prefix == "jpeg":
                counts["jpegValidCoordinateFiles"] += coordinates is not None
                counts["jpegEmptyOrInvalidGPSFiles"] += bool(gps) and coordinates is None
            stat = path.stat()
            row["after"] = {"size": stat.st_size, "mtimeNs": stat.st_mtime_ns}
            row["sourceUnchanged"] = row["after"] == row["before"]
            counts["sourceChanged"] += not row["sourceUnchanged"]
            result["files"].append(row)
        result.update(statistics=dict(counts), processed=len(result["files"]))
        atomic(destination, result)
        print(f"JPEG/paired-sidecar location {result['processed']}/{len(inputs)}", flush=True)
    if any(not row["sourceUnchanged"] for row in result["files"]):
        raise RuntimeError("A location source changed during inspection")
    result.update(status="completed", finishedAt=datetime.now().isoformat(), statistics=dict(counts))
    atomic(destination, result)
    return result["statistics"]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--exiftool", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(inspect(args.job.resolve(strict=True), args.exiftool.resolve(strict=True),
                             args.package.resolve(strict=True)), ensure_ascii=False))
