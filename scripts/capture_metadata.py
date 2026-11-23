# SPDX-License-Identifier: AGPL-3.0-only
"""Minimal original-JPEG capture metadata, without location uploads or unrelated EXIF."""
import argparse
from collections import Counter
from datetime import datetime
import json
import math
from pathlib import Path
import re
import sys

from PIL import Image

from comparison_prepare import atomic

VERSION = "original-exif-capture-v1"


def text(value):
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="strict")
    return str(value).strip("\0 ") if value is not None else None


def number(value):
    if isinstance(value, (tuple, list)) and len(value) == 2:
        if not value[1]:
            raise ValueError("GPS rational denominator is zero")
        value = value[0] / value[1]
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("GPS coordinate contains a non-finite value")
    return result


def coordinate(value, reference, latitude):
    reference = text(reference)
    if reference not in ({"N", "S"} if latitude else {"E", "W"}):
        raise ValueError("GPS hemisphere reference is missing or invalid")
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError("GPS coordinate is not a degree/minute/second triplet")
    degrees, minutes, seconds = [number(part) for part in value]
    if degrees < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise ValueError("GPS degree/minute/second value is invalid")
    result = degrees + minutes / 60 + seconds / 3600
    if result > (90 if latitude else 180):
        raise ValueError("GPS coordinate is outside the WGS84 range")
    return -result if reference in {"S", "W"} else result


def parse_capture(detail, gps):
    capture = {"status": "ready", "time": None, "timeZone": None, "timeSource": None,
               "location": None, "locationNote": None}
    errors = []
    stamp = text(detail.get(36867))
    if stamp:
        try:
            parsed = datetime.strptime(stamp, "%Y:%m:%d %H:%M:%S")
            value = parsed.isoformat(timespec="seconds")
            source = "EXIF DateTimeOriginal"
            fraction = text(detail.get(37521))
            if fraction:
                if not fraction.isdigit():
                    raise ValueError("SubSecTimeOriginal is invalid")
                value += "." + fraction
                source += " + SubSecTimeOriginal"
            offset = text(detail.get(36881))
            if offset:
                if not re.fullmatch(r"[+-]\d{2}:\d{2}", offset) or int(offset[1:3]) > 23 or int(offset[4:]) > 59:
                    raise ValueError("OffsetTimeOriginal is invalid")
                capture["timeZone"] = offset
                value += offset
                source += " + OffsetTimeOriginal"
            capture.update(time=value, timeSource=source)
        except ValueError as error:
            errors.append(str(error))
    if gps:
        if all(key in gps for key in [1, 2, 3, 4]):
            try:
                capture["location"] = {"latitude": coordinate(gps[2], gps[1], True),
                                       "longitude": coordinate(gps[4], gps[3], False),
                                       "crs": "WGS84", "source": "exif-gps"}
            except (TypeError, ValueError, ZeroDivisionError) as error:
                errors.append(str(error))
        else:
            capture["locationNote"] = "原JPEG的GPS字段不完整，未猜测坐标；配对RAW元数据未读取"
    else:
        capture["locationNote"] = "原JPEG未记录GPS；配对RAW元数据未读取，用户地区参考不替代拍摄地点"
    if errors:
        capture.update(status="error", locationNote="EXIF读取/解析错误：" + "; ".join(errors))
    return capture


def extract(job, photo_ids=None, include_region=True):
    manifest = json.loads((job / "manifest.json").read_text(encoding="utf-8"))
    source = Path(manifest["source"]).resolve(strict=True)
    target = job / "revisions" / "capture-exif-v1.json"
    rows = json.loads(target.read_text(encoding="utf-8"))["photos"] if photo_ids is not None and target.exists() else {}
    wanted = set(photo_ids) if photo_ids is not None else None
    for photo in manifest["photos"]:
        if wanted is not None and (photo["id"] not in wanted or photo["id"] in rows):
            continue
        original = photo["primary"]
        path = (source / original["relative"]).resolve()
        try:
            if not path.is_relative_to(source):
                raise ValueError("Original path is outside the authorized source")
            stat = path.stat()
            if stat.st_size != original["size"] or stat.st_mtime_ns != original["mtimeNs"]:
                raise ValueError("Original file changed since batch inventory")
            with Image.open(path) as image:
                exif = image.getexif()
                detail = exif.get_ifd(0x8769)
                gps = exif.get_ifd(0x8825)
                capture = parse_capture(detail, gps)
            after = path.stat()
            if after.st_size != stat.st_size or after.st_mtime_ns != stat.st_mtime_ns:
                raise ValueError("Original file changed while metadata was read")
        except (OSError, ValueError, TypeError, UnicodeError) as error:
            capture = {"status": "error", "time": None, "timeZone": None, "timeSource": None,
                       "location": None, "locationNote": f"{original['relative']}: {error}"}
        rows[photo["id"]] = {"sourceRelative": original["relative"], "capture": capture,
                             "sourceSizeBytes": original["size"], "sourceMtimeNs": original["mtimeNs"]}
    counts = Counter()
    for row in rows.values():
        capture = row["capture"]
        counts["photos"] += 1
        counts["timeAvailable"] += bool(capture["time"])
        counts["timeZoneAvailable"] += bool(capture["timeZone"])
        counts["gpsAvailable"] += capture["location"] is not None
        counts["errors"] += capture["status"] == "error"
    result = {"version": VERSION, "source": "Original JPEG EXIF; paired RAW not parsed",
              "userRegionReference": "中国江苏（用户提供的地区参考，非EXIF拍摄地点）",
              "statistics": dict(counts), "photos": rows}
    if not include_region:
        result.pop("userRegionReference", None)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic(target, result)
    return result["statistics"]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(extract(args.job.resolve(strict=True)), ensure_ascii=False))
