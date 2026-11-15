# SPDX-License-Identifier: AGPL-3.0-only
"""Verify paired location audits and navigation without re-running models or pixels."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

from comparison_prepare import atomic, sha256
from inspect_location_sources import paired_sidecars


def verify(job, annotate_sidecar_count=False):
    revisions = job / "revisions"
    manifest = json.loads((job / "manifest.json").read_text(encoding="utf-8"))
    source = Path(manifest["source"]).resolve(strict=True)
    raw_path = revisions / "paired-raw-location-v1.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw_expected = {(photo["id"], record["relative"]) for photo in manifest["photos"] for record in photo["companions"]}
    if raw["status"] != "completed" or {(row["photoId"], row["rawRelative"]) for row in raw["files"]} != raw_expected:
        raise RuntimeError("Not every registered RAW was inspected")
    for row in raw["files"]:
        stat = (source / row["rawRelative"]).stat()
        if not row["sourceUnchanged"] or row["before"] != row["after"] or row["after"] != {"size": stat.st_size, "mtimeNs": stat.st_mtime_ns}:
            raise RuntimeError("RAW source size/mtime changed")
    additional_path = revisions / "jpeg-sidecar-location-v1.json"
    additional = json.loads(additional_path.read_text(encoding="utf-8"))
    actual_jpegs = {row["photoIds"][0] for row in additional["files"] if row["kind"] == "jpeg"}
    if additional["status"] != "completed" or actual_jpegs != {photo["id"] for photo in manifest["photos"]}:
        raise RuntimeError("Not every registered JPEG was location-checked")
    for row in additional["files"]:
        stat = (source / row["relative"]).stat()
        if not row["sourceUnchanged"] or row["before"] != row["after"] or row["after"] != {"size": stat.st_size, "mtimeNs": stat.st_mtime_ns}:
            raise RuntimeError("JPEG/sidecar location source changed")
    sidecars, ignored = paired_sidecars(source, manifest["photos"])
    if {record["path"].relative_to(source).as_posix() for record in sidecars} != {
            row["relative"] for row in additional["files"] if row["kind"] == "sidecar"}:
        raise RuntimeError("Exact-name paired sidecar set changed since inspection")
    if annotate_sidecar_count:
        additional["statistics"]["unpairedSidecarsSkipped"] = ignored
        additional["pairingReviewedAt"] = datetime.now().isoformat()
        atomic(additional_path, additional)
        return {"sourceAuditsVerified": True, "raw": len(raw["files"]), "jpeg": len(actual_jpegs),
                "pairedSidecars": len(sidecars), "unpairedSidecarsSkippedWithoutReading": ignored}
    metadata = json.loads((revisions / "capture-enriched-v2.json").read_text(encoding="utf-8"))
    gallery = json.loads((revisions / "verified-birds-birder-v2" / "gallery.json").read_text(encoding="utf-8"))
    baseline = json.loads((revisions / "verified-birds-birder-v2" / "pre-shoot-group-decisions.json").read_text(encoding="utf-8"))
    if metadata["rawInspectionSha256"] != sha256(raw_path) or metadata["additionalLocationAuditSha256"] != sha256(additional_path):
        raise RuntimeError("Source audit fingerprints do not match enriched metadata")
    group_counts = {row["id"]: {"photoCount": 0, "cropCount": 0, "preferredCount": 0} for row in metadata["shootGroups"]}
    for photo in manifest["photos"]:
        row = metadata["photos"][photo["id"]]
        group_counts[row["shootGroup"]["id"]]["photoCount"] += 1
    for item in gallery["items"]:
        source_row = metadata["photos"][item["frameId"]]
        if item["capture"] != source_row["capture"] or item["shootGroup"] != source_row["shootGroup"]:
            raise RuntimeError("A crop does not inherit capture/shoot grouping from its exact original")
        group_counts[item["shootGroup"]["id"]]["cropCount"] += 1
        if item["decision"] == "keep" and item["birdVerification"]["status"] == "bird":
            group_counts[item["shootGroup"]["id"]]["preferredCount"] += 1
        before = baseline[item["id"]]
        if any(before[key] != item[key] for key in ["decision", "rank", "quality"]):
            raise RuntimeError("Navigation enrichment changed classification or technical selection")
        for field in ["top3", "top5"]:
            projected = [{key: value for key, value in candidate.items() if key != "englishName"}
                         for candidate in item["species"].get(field, [])]
            if before["species"].get(field, []) != projected:
                raise RuntimeError("Display-name enrichment changed the original classification candidates or scores")
    for group in gallery["shootGroups"]:
        if group_counts[group["id"]] != {key: group[key] for key in ["photoCount", "cropCount", "preferredCount"]}:
            raise RuntimeError("Shoot-group original/crop/preferred counts do not match")
    for path in (revisions / "verified-birds-birder-v2" / "groups").glob("g*.json"):
        detail = json.loads(path.read_text(encoding="utf-8"))
        for frame in detail["frames"]:
            expected = metadata["photos"][frame["id"]]
            if frame["capture"] != expected["capture"] or frame["shootGroup"] != expected["shootGroup"]:
                raise RuntimeError("Group detail lost the exact source capture/shoot grouping")
    result = {
        "verified": True, "raw": raw["statistics"], "jpegSidecar": additional["statistics"],
        "shootGroups": len(gallery["shootGroups"]), "originalPhotos": sum(v["photoCount"] for v in group_counts.values()),
        "crops": sum(v["cropCount"] for v in group_counts.values()),
        "preferred": sum(v["preferredCount"] for v in group_counts.values()),
        "noCropPhotoGroupsRetained": sum(row["cropCount"] == 0 for row in gallery["shootGroups"]),
        "allSourcesSizeAndMtimeUnchanged": True, "classificationAndTechnicalSelectionsUnchanged": True,
        "rawAndJPEGLocationTagsNotWritten": True, "uploadsOrReverseGeocoding": False,
    }
    atomic(revisions / "location-and-shoot-groups-validation.json", result)
    return result


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--annotate-sidecar-count", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify(args.job.resolve(strict=True), args.annotate_sidecar_count), ensure_ascii=False))
