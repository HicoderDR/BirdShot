# SPDX-License-Identifier: AGPL-3.0-only
"""Pure metadata navigation; no model scoring, source writes or geocoding."""
from copy import deepcopy
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path
import unicodedata


MODES = ("date", "folder", "place")


def stable_id(prefix, value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return prefix + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def canonical(path):
    return os.path.normcase(str(Path(path).resolve()))


def capture_metadata(job):
    for name in ("capture-enriched-v2.json", "capture-exif-v1.json"):
        path = Path(job) / "revisions" / name
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    return {"photos": {}}


def navigation_keys(photo, capture, source):
    stamp = capture.get("time") or photo.get("capturedAt")
    day = None
    if isinstance(stamp, str):
        try:
            day = date.fromisoformat(stamp[:10]).isoformat()
        except ValueError:
            pass
    root = Path(photo.get("sourceRoot") or source)
    relative = Path(photo["primary"]["relative"])
    folder = root / relative.parent
    folder_name = photo.get("sourceName") or root.name or str(root)
    if relative.parent != Path("."):
        folder_name += " / " + relative.parent.as_posix()
    place = capture.get("placeName")
    if isinstance(place, str) and place.strip() and capture.get("placeSource"):
        place = unicodedata.normalize("NFC", place.strip())
        place_key, place_name = ["metadata-name", place], place
    else:
        location = capture.get("location") or {}
        lat, lon = location.get("latitude"), location.get("longitude")
        if location.get("source") and location.get("crs") == "WGS84" \
                and isinstance(lat, (float, int)) and isinstance(lon, (float, int)) \
                and math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
            place_key = ["WGS84", lat, lon]
            place_name = f"GPS {lat:.6f}, {lon:.6f}"
        else:
            place_key, place_name = None, "未知地点"
    return {
        "date": (day, day or "日期未知"),
        "folder": (canonical(folder), folder_name),
        "place": (place_key, place_name),
    }


def disambiguate_folders(rows, contexts):
    collisions = {}
    for row in rows.values():
        collisions.setdefault(unicodedata.normalize("NFC", row["name"]), []).append(row)
    used = {row["name"] for row in rows.values()}
    for matches in collisions.values():
        if len(matches) < 2:
            continue
        for row in sorted(matches, key=lambda value: value["id"]):
            parts = contexts[row["id"]]
            qualifier = None
            for depth in range(1, len(parts) + 1):
                candidate = " / ".join(parts[-depth:])
                label = f"{row['name']}（{candidate}）"
                if label not in used and all(
                        candidate != " / ".join(contexts[other["id"]][-depth:])
                        for other in matches if other["id"] != row["id"]):
                    qualifier = candidate
                    break
            if qualifier is None:
                for length in range(8, 25):
                    qualifier = "来源 " + row["id"].removeprefix("folder-")[:length]
                    if f"{row['name']}（{qualifier}）" not in used:
                        break
            row["name"] += f"（{qualifier}）"
            used.add(row["name"])


def enrich_navigation(gallery, manifest, metadata=None):
    """Return an enriched copy, leaving all original decisions/quality untouched."""
    result = deepcopy(gallery)
    entries = (metadata or {}).get("photos", {})
    groups = {mode: {} for mode in MODES}
    folder_contexts = {}
    photo_groups, captures = {}, {}
    item_captures = {item["frameId"]: item.get("capture") for item in result.get("items", [])
                     if item.get("frameId") and item.get("capture")}
    for photo in manifest.get("photos", []):
        photo_id = photo["id"]
        capture = entries.get(photo_id, {}).get("capture") or photo.get("capture") or item_captures.get(photo_id) or {}
        captures[photo_id] = capture
        memberships = {}
        for mode, (key, name) in navigation_keys(photo, capture, manifest.get("source", "")).items():
            group_id = stable_id(mode + "-", key)
            memberships[mode] = group_id
            row = groups[mode].setdefault(group_id, {
                "id": group_id, "name": name, "photoCount": 0, "cropCount": 0, "preferredCount": 0,
            })
            row["photoCount"] += 1
            if mode == "folder" and group_id not in folder_contexts:
                root = Path(photo.get("sourceRoot") or manifest.get("source", "")).resolve()
                context = root.parent if (photo.get("sourceName") or root.name) == root.name else root
                folder_contexts[group_id] = tuple(part for part in context.parts if part != context.anchor)
        photo_groups[photo_id] = memberships
    for item in result.get("items", []):
        memberships = photo_groups.get(item.get("frameId"))
        if memberships is None:
            continue
        item["navigationGroups"] = memberships.copy()
        item.setdefault("capture", captures[item["frameId"]])
        preferred = item.get("decision") == "keep" and item.get("birdVerification", {}).get("status") == "bird"
        for mode, group_id in memberships.items():
            groups[mode][group_id]["cropCount"] += 1
            groups[mode][group_id]["preferredCount"] += int(preferred)
    frames = result.get("frames", [])
    for group in result.get("groups", []):
        if isinstance(group, dict):
            frames = frames + group.get("frames", [])
    for frame in frames:
        if frame.get("id") in photo_groups:
            frame["navigationGroups"] = photo_groups[frame["id"]].copy()
    disambiguate_folders(groups["folder"], folder_contexts)
    result["navigation"] = {"modes": list(MODES), "groups": {
        mode: sorted(rows.values(), key=lambda row: (row["name"] in {"日期未知", "未知地点"}, row["name"], row["id"]))
        for mode, rows in groups.items()
    }}
    result["originalPhotoCount"] = len(manifest.get("photos", []))
    return result
