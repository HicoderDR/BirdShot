# SPDX-License-Identifier: AGPL-3.0-only
"""Enrich only local metadata and place/day navigation, never models or originals."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata

from comparison_prepare import atomic, sha256

VERSION = "raw-reviewed-place-camera-day-v1"


def shoot_group(capture):
    time = capture.get("time")
    camera_date = None
    if isinstance(time, str) and re.match(r"^\d{4}-\d{2}-\d{2}T", time):
        try:
            camera_date = date.fromisoformat(time[:10]).isoformat()
        except ValueError:
            pass
    place = capture.get("placeName")
    place = unicodedata.normalize("NFC", place.strip()) if isinstance(place, str) and place.strip() else None
    location = capture.get("location")
    if place:
        place_key = {"name": place}
    elif location:
        place_key = {"latitude": location["latitude"], "longitude": location["longitude"], "crs": location["crs"]}
    else:
        place_key = None
    key = json.dumps({"place": place_key, "date": camera_date}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    suffix = camera_date[2:4] + camera_date[5:7] + camera_date[8:10] if camera_date else "日期未知"
    return {"id": "shoot-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16],
            "name": f"{place or '未知地点'} - {suffix}", "date": camera_date, "placeName": place}


def raw_place(record):
    if record.get("status") not in {"parsed", "checked-text-selectors"}:
        return None, None
    fields = record.get("locationTags", {})
    for wanted in ["sublocation", "sub-location", "locationname", "location", "city"]:
        for key, value in fields.items():
            if key.rsplit(":", 1)[-1].casefold() == wanted and isinstance(value, str) and value.strip() \
                    and value.strip().casefold() not in {"null", "none", "n/a", "{}", "{", "}"}:
                return value.strip(), key
    return None, None


def enrich(job):
    revisions = job / "revisions"
    raw_path = revisions / "paired-raw-location-v1.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    if raw["status"] != "completed":
        raise RuntimeError("Paired RAW inspection is incomplete")
    manifest = json.loads((job / "manifest.json").read_text(encoding="utf-8"))
    registered = {(p["id"], c["relative"]) for p in manifest["photos"] for c in p["companions"]}
    actual = {(record["photoId"], record["rawRelative"]) for record in raw["files"]}
    if registered != actual or len(actual) != len(raw["files"]):
        raise RuntimeError("RAW results do not exactly cover registered pairs")
    if any(not record["sourceUnchanged"] for record in raw["files"] if record["status"] == "parsed"):
        raise RuntimeError("RAW source integrity changed")
    base_path = revisions / "capture-exif-v1.json"
    base = json.loads(base_path.read_text(encoding="utf-8"))
    metadata = deepcopy(base)
    records = {record["photoId"]: record for record in raw["files"]}
    additional_path = revisions / "jpeg-sidecar-location-v1.json"
    additional = json.loads(additional_path.read_text(encoding="utf-8")) if additional_path.exists() else None
    sources = {}
    if additional is not None:
        if additional["status"] != "completed":
            raise RuntimeError("Registered JPEG/sidecar location inspection is incomplete")
        for record in additional["files"]:
            for photo_id in record["photoIds"]:
                sources.setdefault(photo_id, []).append(record)
    groups, photo_to_group = {}, {}
    restored_locations, restored_names = 0, 0
    for photo in manifest["photos"]:
        entry = metadata["photos"][photo["id"]]
        capture = entry["capture"]
        record = records.get(photo["id"])
        capture.setdefault("placeName", None)
        capture.setdefault("placeSource", None)
        capture.setdefault("placeNote", None)
        for extra in sorted(sources.get(photo["id"], []), key=lambda row: row["kind"] != "jpeg"):
            if extra["status"] not in {"parsed", "checked-text-selectors"} or not extra["sourceUnchanged"]:
                continue
            coordinates = extra.get("coordinates")
            if capture.get("location") is None and coordinates and coordinates["crs"] == "WGS84":
                capture["location"] = {"latitude": coordinates["latitude"], "longitude": coordinates["longitude"],
                                       "crs": "WGS84", "source": "exif-gps"}
                capture["locationNote"] = "来自原JPEG或确切配对元数据的GPS：" + extra["relative"]
            place, tag = raw_place(extra)
            if capture["placeName"] is None and place:
                capture.update(placeName=place, placeSource=f"{extra['kind']}:{extra['relative']}:{tag}",
                               placeNote="直接记录的元数据地名，未在线反查或推测公园范围")
        if record:
            entry["pairedRawReview"] = {"rawRelative": record["rawRelative"], "status": record["status"],
                                        "tool": "ExifTool " + raw["tool"]["version"]}
            if record["status"] != "parsed" or record.get("warnings") or record.get("independentDisagreement"):
                capture["placeNote"] = "配对RAW位置元数据仍需待审：" + (record.get("positionNote") or "读取警告或独立解析分歧")
            else:
                coordinates = record.get("coordinates")
                if capture.get("location") is None and coordinates and coordinates["crs"] == "WGS84":
                    capture["location"] = {"latitude": coordinates["latitude"], "longitude": coordinates["longitude"],
                                           "crs": "WGS84", "source": "exif-gps"}
                    capture["locationNote"] = f"来自确切配对RAW {record['rawRelative']} 的EXIF GPS；ExifTool {raw['tool']['version']}，JPEG时间保留"
                    entry["rawLocationSourceTags"] = coordinates["sourceTags"]
                    restored_locations += 1
                name, tag = raw_place(record)
                if capture["placeName"] is None and name:
                    capture.update(placeName=name, placeSource=f"paired-raw:{tag}",
                                   placeNote=f"场所文字来自配对RAW {record['rawRelative']} 的 {tag}，未经在线地名反查")
                    restored_names += 1
                elif capture["placeName"] is None:
                    capture["placeNote"] = "配对RAW未记录可用场所文字；未套用用户示例地名，也未在线反向地理编码"
                if capture.get("location") is None:
                    capture["locationNote"] = ("配对RAW有坐标但基准未知，待确认，不生成地图标点"
                                               if coordinates else "原JPEG与已核查的配对RAW均无可用GPS")
        else:
            entry["pairedRawReview"] = {"status": "not-paired", "rawRelative": None}
            capture["placeNote"] = capture["placeNote"] or "仅JPEG，无配对RAW；不套用其他照片地点"
            if capture.get("location") is None:
                capture["locationNote"] = "原JPEG未记录GPS，且没有配对RAW"
        if additional is not None:
            if not capture.get("location"):
                capture["locationNote"] += "；原JPEG完整GPS/IPTC/XMP及明确配对sidecar亦已核查"
            if capture["placeName"] is None:
                capture["placeNote"] = "原JPEG、已登记配对RAW及明确配对sidecar未提供场所名；示例地名未套用"
        grouping = shoot_group(capture)
        entry["shootGroup"] = grouping
        photo_to_group[photo["id"]] = grouping["id"]
        if grouping["id"] not in groups:
            groups[grouping["id"]] = {**grouping, "photoCount": 0, "cropCount": 0, "preferredCount": 0}
        groups[grouping["id"]]["photoCount"] += 1
        if capture.get("time") != base["photos"][photo["id"]]["capture"].get("time"):
            raise RuntimeError("Original JPEG capture time must not be overwritten")
    mapping = json.loads((job / "mapping.json").read_text(encoding="utf-8"))["crops"]
    gallery_path = revisions / "verified-birds-birder-v2" / "gallery.json"
    gallery = json.loads(gallery_path.read_text(encoding="utf-8"))
    if gallery["status"] != "completed":
        raise RuntimeError("Navigation enrichment expects completed classification, not a rescore")
    old_selections = {item["id"]: {"decision": item["decision"], "rank": item["rank"], "quality": item["quality"],
                                  "species": item["species"]} for item in gallery["items"]}
    for record in mapping:
        groups[photo_to_group[record["photoId"]]]["cropCount"] += 1
    for item in gallery["items"]:
        if item["decision"] == "keep" and item["birdVerification"]["status"] == "bird":
            groups[photo_to_group[item["frameId"]]]["preferredCount"] += 1
    metadata.update(version=VERSION, enrichedAt=datetime.now().isoformat(),
                    baseMetadataSha256=sha256(base_path), rawInspectionSha256=sha256(raw_path),
                    rawInspectionSummary=raw["statistics"],
                    shootGroups=sorted(groups.values(), key=lambda row: (row["date"] or "", row["name"]), reverse=True))
    if additional is not None:
        metadata["additionalLocationAuditSha256"] = sha256(additional_path)
        metadata["additionalLocationSummary"] = additional["statistics"]
    metadata["statistics"].update(pairedRawChecked=len(raw["files"]), rawLocationsRestored=restored_locations,
                                   rawPlaceNamesRestored=restored_names, shootGroups=len(groups),
                                   locationAvailable=sum(row["capture"].get("location") is not None for row in metadata["photos"].values()),
                                   placeNamesAvailable=sum(bool(row["capture"].get("placeName")) for row in metadata["photos"].values()))
    atomic(revisions / "capture-enriched-v2.json", metadata)
    atomic(revisions / "shoot-groups-v1.json", {
        "version": VERSION, "basis": "Camera original date, without timezone conversion; actual metadata place or unknown",
        "groups": metadata["shootGroups"], "photos": {key: value["shootGroup"] for key, value in metadata["photos"].items()},
        "countUnits": {"photoCount": "registered original JPEGs", "cropCount": "all registered bird-instance crops",
                       "preferredCount": "revised preferred crops, not unique photos"},
        "originalPhotoCount": len(manifest["photos"]), "cropCount": len(mapping),
    })
    baseline = revisions / "verified-birds-birder-v2" / "pre-shoot-group-decisions.json"
    if not baseline.exists():
        atomic(baseline, old_selections)
    raw["conclusion"] = ("全部登记配对ARW已由ExifTool读取，且独立TIFF目录GPSInfo核查一致；未发现GPS/位置文字，不能恢复示例场所名。"
                         if raw["statistics"]["parsed"] == len(raw["files"]) and not any(
                             raw["statistics"][key] for key in ["gpsTagFiles", "locationTextFiles", "readErrors", "unsupportedFiles",
                                                                "warningFiles", "independentErrors", "independentUnsupported", "independentDisagreements"])
                         else "存在位置或待审结果，请按逐文件记录与坐标基准解释，不能把读取失败当作GPS缺失。")
    raw["reviewedAt"] = datetime.now().isoformat()
    atomic(raw_path, raw)
    # Retain the final reviewed report fingerprint rather than the pre-review wording.
    metadata["rawInspectionSha256"] = sha256(raw_path)
    atomic(revisions / "capture-enriched-v2.json", metadata)
    return {"raw": raw["statistics"], "shootGroups": metadata["shootGroups"], "statistics": metadata["statistics"]}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(enrich(args.job.resolve(strict=True)), ensure_ascii=False))
