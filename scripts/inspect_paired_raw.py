# SPDX-License-Identifier: AGPL-3.0-only
"""Read registered paired RAW location metadata only; never decode/write image pixels."""
import argparse
from collections import Counter
from datetime import datetime
import json
import math
from pathlib import Path
import re
import struct
import subprocess
import sys

from comparison_prepare import atomic, sha256

VERSION = "paired-raw-location-v1"
TOOL_VERSION = "13.63"
PACKAGE_SHA256 = "7a4bff4b1d6ee15adef882837847f76c0ed23228d294feed285939b6c9b293bd"
LOCATION_NAMES = {
    "location", "locationname", "locationinformation", "locationcreated", "locationshown",
    "city", "sublocation", "sub-location", "province-state", "state", "region", "country", "countrycode",
    "contentlocationname", "contentlocationcode", "contentlocationcodes", "contentlocationnames",
}
SELECTORS = [
    "-FileType", "-FileTypeExtension", "-Error", "-Warning",
    "-DateTimeOriginal", "-SubSecTimeOriginal", "-OffsetTimeOriginal",
    "-GPS*", "-Location*", "-ContentLocation*", "-City", "-Sub-location", "-Province-State",
    "-Country*", "-State", "-Region", "-GeoTiff:all",
]


def tiff_gps_probe(path):
    """Independent TIFF-directory inspection. Only directory tables/pointers are read."""
    size = path.stat().st_size
    with path.open("rb") as stream:
        def read_at(offset, length):
            if offset < 0 or length < 0 or offset + length > size:
                raise ValueError("TIFF metadata pointer is outside the RAW")
            stream.seek(offset)
            data = stream.read(length)
            if len(data) != length:
                raise ValueError("Incomplete TIFF metadata read")
            return data
        header = read_at(0, 8)
        if header[:2] not in {b"II", b"MM"}:
            return {"status": "unsupported", "reason": "Not a TIFF-based RAW"}
        endian = "<" if header[:2] == b"II" else ">"
        if struct.unpack(endian + "H", header[2:4])[0] != 42:
            return {"status": "unsupported", "reason": "Not a classic TIFF directory format"}
        initial = struct.unpack(endian + "I", header[4:8])[0]
        pending, visited, gps_pointers, gps_directories = [(initial, "IFD0")], set(), [], []
        while pending:
            offset, name = pending.pop()
            if not offset or offset in visited:
                continue
            visited.add(offset)
            if len(visited) > 256:
                raise ValueError("Too many TIFF metadata directories")
            count = struct.unpack(endian + "H", read_at(offset, 2))[0]
            if count > 4096:
                raise ValueError("Invalid TIFF metadata entry count")
            table = read_at(offset + 2, count * 12 + 4)
            if name == "GPS":
                gps_directories.append({"offset": offset, "entryCount": count,
                                        "tagIds": [struct.unpack(endian + "H", table[i * 12:i * 12 + 2])[0] for i in range(count)]})
            for index in range(count):
                entry = table[index * 12:(index + 1) * 12]
                tag, kind, length = struct.unpack(endian + "HHI", entry[:8])
                if tag not in {0x8825, 0x8769, 0xA005, 0x014A}:
                    continue
                if kind not in {4, 13} or length > 128:
                    raise ValueError("Unexpected TIFF subdirectory pointer encoding")
                values = entry[8:12] if length == 1 else read_at(struct.unpack(endian + "I", entry[8:12])[0], length * 4)
                pointers = list(struct.unpack(endian + "I" * length, values))
                if tag == 0x8825:
                    gps_pointers.append({"parent": name, "pointers": pointers})
                for pointer in pointers:
                    pending.append((pointer, "GPS" if tag == 0x8825 else "Exif" if tag == 0x8769 else "SubIFD"))
            following = struct.unpack(endian + "I", table[count * 12:])[0]
            if following:
                pending.append((following, name + "-next"))
        return {"status": "parsed", "format": "classic TIFF", "byteOrder": header[:2].decode("ascii"),
                "directoriesChecked": len(visited), "gpsIFDTagPresent": bool(gps_pointers), "gpsIFDPointers": gps_pointers,
                "gpsDirectories": gps_directories,
                "scope": "TIFF GPSInfo directory pointer in main/Exif/SubIFDs; no pixel or proprietary MakerNote decoding"}


def base_name(key):
    return key.rsplit(":", 1)[-1]


def nonempty(value):
    return value is not None and value != "" and value != [] and value != {}


def position_fields(tags):
    gps, locations = {}, {}
    for key, value in tags.items():
        name = base_name(key).lower()
        if name.startswith("gps") or ":geotiff:" in (":" + key.lower() + ":"):
            gps[key] = value
        elif name in LOCATION_NAMES or name.startswith(("locationcreated", "locationshown", "contentlocation")):
            locations[key] = value
    return gps, locations


def valid_coordinates(gps):
    def values(name):
        return [(key, value) for key, value in gps.items() if base_name(key).lower() == name.lower()]
    latitudes, longitudes = values("GPSLatitude"), values("GPSLongitude")
    if not latitudes or not longitudes:
        return None, "GPS标签存在但缺少经纬度" if gps else None
    refs = {base_name(key).lower(): value for key, value in gps.items()}
    for latitude_key, latitude in latitudes:
        for longitude_key, longitude in longitudes:
            try:
                lat, lon = float(latitude), float(longitude)
                if not math.isfinite(lat) or not math.isfinite(lon) or abs(lat) > 90 or abs(lon) > 180:
                    raise ValueError("坐标非有限或越界")
                lat_ref, lon_ref = refs.get("gpslatituderef"), refs.get("gpslongituderef")
                if lat_ref is not None:
                    if lat_ref not in {"N", "S"}:
                        raise ValueError("纬度方向无效")
                    lat = -abs(lat) if lat_ref == "S" else abs(lat)
                elif not any(group in latitude_key.lower() for group in ["composite", "xmp"]):
                    raise ValueError("标准GPS纬度缺少方向")
                if lon_ref is not None:
                    if lon_ref not in {"E", "W"}:
                        raise ValueError("经度方向无效")
                    lon = -abs(lon) if lon_ref == "W" else abs(lon)
                elif not any(group in longitude_key.lower() for group in ["composite", "xmp"]):
                    raise ValueError("标准GPS经度缺少方向")
                datum = str(refs.get("gpsmapdatum", "")).strip()
                crs = "WGS84" if re.sub(r"[^a-z0-9]", "", datum.casefold()) in {"wgs84", "wgs1984"} else None
                return {"latitude": lat, "longitude": lon, "crs": crs, "mapDatum": datum or None,
                        "latitudeRef": lat_ref, "longitudeRef": lon_ref,
                        "sourceTags": [latitude_key, longitude_key]}, None
            except (TypeError, ValueError):
                continue
    return None, "GPS经纬字段为空、格式异常、无方向或超出范围"


def inspect(job, tool, package):
    if sha256(package) != PACKAGE_SHA256:
        raise RuntimeError("Official ExifTool distribution SHA256 mismatch")
    actual_version = subprocess.check_output([str(tool), "-ver"], text=True).strip()
    if actual_version != TOOL_VERSION:
        raise RuntimeError("ExifTool version does not match the verified distribution")
    manifest = json.loads((job / "manifest.json").read_text(encoding="utf-8"))
    source = Path(manifest["source"]).resolve(strict=True)
    pairs = [{"photoId": photo["id"], "jpeg": photo["primary"]["relative"], "raw": companion}
             for photo in manifest["photos"] for companion in photo["companions"]]
    if len({pair["raw"]["relative"].casefold() for pair in pairs}) != len(pairs):
        raise RuntimeError("Registered RAW pairing contains duplicates")
    counts = Counter({"registeredPairedRaw": len(pairs), "jpegOnly": sum(not p["companions"] for p in manifest["photos"]),
                      "parsed": 0, "gpsTagFiles": 0, "validCoordinateFiles": 0, "wgs84CoordinateFiles": 0,
                      "coordinateSystemUnknownFiles": 0, "locationTextFiles": 0, "emptyOrInvalidGPSFiles": 0,
                      "emptyLocationTextFiles": 0, "missingFiles": 0, "readErrors": 0, "unsupportedFiles": 0,
                      "warningFiles": 0, "sourceChangedBefore": 0, "sourceChangedAfter": 0,
                      "independentParsed": 0, "independentGPSIFDFiles": 0, "independentErrors": 0,
                      "independentUnsupported": 0, "independentDisagreements": 0})
    result = {
        "version": VERSION, "status": "running", "startedAt": datetime.now().isoformat(),
        "tool": {"name": "ExifTool", "version": actual_version, "officialPage": "https://exiftool.org/",
                 "distribution": "https://sourceforge.net/projects/exiftool/files/exiftool-13.63_64.zip/download",
                 "checksumSource": "https://exiftool.org/checksums-13.63.txt", "distributionSha256": PACKAGE_SHA256,
                 "executableSha256": sha256(tool)},
        "readOnly": {"pixelDecode": False, "tagWrite": False, "recursiveSourceScan": False, "uploads": False,
                     "integrity": "Each registered RAW checked against inventory size/mtime before and after read"},
        "scope": {"selectors": SELECTORS, "makerNotes": "ExifTool normal Sony ARW support; no -fast/-m skipping or error suppression",
                  "derivedGeolocation": "Disabled via -api GeoLocation=0; no reverse geocoding",
                  "independent": "All registered RAW TIFF main/Exif/SubIFD directory tables checked for GPSInfo pointer"},
        "statistics": dict(counts), "files": [],
    }
    output = job / "revisions" / "paired-raw-location-v1.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic(output, result)
    for start in range(0, len(pairs), 64):
        batch, ready = pairs[start:start + 64], []
        records = {}
        for pair in batch:
            raw = pair["raw"]
            path = (source / raw["relative"]).resolve()
            record = {"photoId": pair["photoId"], "jpegRelative": pair["jpeg"], "rawRelative": raw["relative"],
                      "registeredFormat": Path(raw["relative"]).suffix, "status": "pending", "gpsTags": {},
                      "locationTags": {}, "coordinates": None, "positionNote": None,
                      "before": None, "after": None, "sourceUnchanged": False}
            records[raw["relative"]] = record
            if not path.is_relative_to(source):
                record.update(status="error", positionNote="RAW路径越出已授权源目录")
                counts["readErrors"] += 1
                continue
            if not path.is_file():
                record.update(status="missing", positionNote="配对RAW文件缺失")
                counts["missingFiles"] += 1
                continue
            stat = path.stat()
            record["before"] = {"size": stat.st_size, "mtimeNs": stat.st_mtime_ns}
            if stat.st_size != raw["size"] or stat.st_mtime_ns != raw["mtimeNs"]:
                record.update(status="changed", positionNote="RAW相对原始清点发生变化，未将其当同一输入")
                counts["sourceChangedBefore"] += 1
                continue
            ready.append((pair, path, record))
        if ready:
            arguments = [str(tool), "-json", "-G1:3:4", "-s", "-n", "-a", "-q", "-api", "GeoLocation=0",
                         *SELECTORS, *[str(path) for _, path, _ in ready]]
            completed = subprocess.run(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                       encoding="utf-8", timeout=240, check=False)
            try:
                extracted = json.loads(completed.stdout)
            except ValueError as error:
                raise RuntimeError("ExifTool did not return valid metadata JSON; missing GPS must not be inferred") from error
            if not isinstance(extracted, list):
                raise RuntimeError("Unexpected ExifTool result shape")
            expected = {str(path).casefold(): record for _, path, record in ready}
            returned = set()
            for tags in extracted:
                path = Path(tags["SourceFile"]).resolve()
                key = str(path).casefold()
                if key not in expected or key in returned:
                    raise RuntimeError("ExifTool returned an unregistered/duplicate source")
                returned.add(key)
                record = expected[key]
                errors = [value for name, value in tags.items() if base_name(name) == "Error"]
                warnings = [value for name, value in tags.items() if base_name(name) == "Warning"]
                file_type = next((value for name, value in tags.items() if base_name(name) == "FileType"), None)
                gps, locations = position_fields(tags)
                record.update(actualFileType=file_type, gpsTags=gps, locationTags=locations, warnings=warnings,
                              captureTimeTags={name: value for name, value in tags.items()
                                               if base_name(name) in {"DateTimeOriginal", "SubSecTimeOriginal", "OffsetTimeOriginal"}})
                if errors:
                    record.update(status="unsupported" if any("Unknown file type" in str(e) for e in errors) else "error",
                                  positionNote="; ".join(str(e) for e in errors))
                    counts["unsupportedFiles" if record["status"] == "unsupported" else "readErrors"] += 1
                elif file_type != "ARW":
                    record.update(status="unsupported", positionNote="实际文件格式不为登记的Sony ARW")
                    counts["unsupportedFiles"] += 1
                else:
                    record["status"] = "parsed"
                    counts["parsed"] += 1
                    counts["gpsTagFiles"] += bool(gps)
                    counts["warningFiles"] += bool(warnings)
                    counts["locationTextFiles"] += any(nonempty(value) for value in locations.values())
                    counts["emptyLocationTextFiles"] += bool(locations) and not any(nonempty(value) for value in locations.values())
                    coordinates, note = valid_coordinates(gps)
                    record.update(coordinates=coordinates, positionNote=note)
                    counts["validCoordinateFiles"] += coordinates is not None
                    counts["wgs84CoordinateFiles"] += coordinates is not None and coordinates["crs"] == "WGS84"
                    counts["coordinateSystemUnknownFiles"] += coordinates is not None and coordinates["crs"] is None
                    counts["emptyOrInvalidGPSFiles"] += bool(gps) and coordinates is None
                try:
                    probe = tiff_gps_probe(path)
                    record["independentTIFFProbe"] = probe
                    counts["independentParsed"] += probe["status"] == "parsed"
                    counts["independentUnsupported"] += probe["status"] == "unsupported"
                    counts["independentGPSIFDFiles"] += bool(probe.get("gpsIFDTagPresent"))
                    if probe.get("gpsIFDTagPresent") and not gps:
                        if any(item["entryCount"] for item in probe.get("gpsDirectories", [])):
                            record["independentDisagreement"] = "原始GPS IFD有条目而ExifTool未返回GPS标签，需要待审"
                            counts["independentDisagreements"] += 1
                        else:
                            record["positionNote"] = "存在空GPS IFD指针或空目录，没有可用坐标"
                            counts["emptyGPSIFDFiles"] += 1
                except (OSError, ValueError, struct.error) as error:
                    record["independentTIFFProbe"] = {"status": "error", "reason": str(error)}
                    counts["independentErrors"] += 1
                stat = path.stat()
                record["after"] = {"size": stat.st_size, "mtimeNs": stat.st_mtime_ns}
                record["sourceUnchanged"] = record["before"] == record["after"]
                if not record["sourceUnchanged"]:
                    counts["sourceChangedAfter"] += 1
            for key, record in expected.items():
                if key not in returned:
                    record.update(status="error", positionNote="ExifTool未返回该已登记RAW结果，不能当缺GPS")
                    counts["readErrors"] += 1
            if completed.returncode not in {0, 1}:
                raise RuntimeError(f"ExifTool failed with code {completed.returncode}; metadata inspection is incomplete")
        result["files"].extend(records[pair["raw"]["relative"]] for pair in batch)
        result["statistics"] = dict(counts)
        result["processed"] = len(result["files"])
        atomic(output, result)
        print(f"RAW metadata {result['processed']}/{len(pairs)}", flush=True)
    if len(result["files"]) != len(pairs):
        raise RuntimeError("Registered RAW inspection has missing records")
    result["status"] = "completed"
    result["finishedAt"] = datetime.now().isoformat()
    result["statistics"] = dict(counts)
    result["conclusion"] = "No location conclusion until parsing/errors/independent probes have been reviewed."
    atomic(output, result)
    return output, result


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--exiftool", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    args = parser.parse_args()
    output, result = inspect(args.job.resolve(strict=True), args.exiftool.resolve(strict=True), args.package.resolve(strict=True))
    print(json.dumps({"result": str(output), "statistics": result["statistics"]}, ensure_ascii=False))
