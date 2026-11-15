# SPDX-License-Identifier: AGPL-3.0-only
"""Read JPEG EXIF headers only; do not copy or alter the configured originals."""
import argparse
from datetime import datetime
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def prepare(directory):
    folder = directory.resolve(strict=True)
    files = sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"})
    photos = []
    warnings = []
    raw_count = sum(path.is_file() and path.suffix.lower() == ".arw" for path in folder.iterdir())
    for path in files:
        try:
            with Image.open(path) as image:
                detail = image.getexif().get_ifd(0x8769)
                stamp = detail.get(36867)
                subseconds = str(detail.get(37521, "0")).strip("\0 ")
                when = datetime.strptime(stamp, "%Y:%m:%d %H:%M:%S") if stamp else None
                milliseconds = int((subseconds + "000")[:3]) if subseconds.isdigit() else 0
                photos.append({
                    "name": path.name, "width": image.width, "height": image.height,
                    "capturedAt": when.isoformat(timespec="seconds") + f".{milliseconds:03d}" if when else None,
                    "timestamp": when.timestamp() * 1000 + milliseconds if when else None,
                    "url": f"/culling/local/photo/{path.name}",
                })
        except (OSError, ValueError, TypeError) as error:
            warnings.append(f"{path.name}: {error}")
    timed = sorted((p for p in photos if p["timestamp"] is not None), key=lambda p: (p["timestamp"], p["name"]))
    groups = []
    current = []
    for photo in timed:
        if current and (photo["timestamp"] - current[-1]["timestamp"] > 2000
                        or photo["timestamp"] - current[0]["timestamp"] > 10000):
            groups.append(current)
            current = []
        current.append(photo)
    if current:
        groups.append(current)
    groups.extend([[p] for p in photos if p["timestamp"] is None])
    manifest = {
        "notice": "仅本机只读；按 EXIF 原始拍摄时间与子秒粗分组，分析后再检查主体尺度/位置/朝向。相机时钟可能未校准。",
        "jpegCount": len(photos), "rawCount": raw_count, "warnings": warnings,
        "groups": [
            {"id": f"local-{i + 1}", "title": f"候选组 {i + 1} · {items[0]['capturedAt'] or '无 EXIF 时间'} · {len(items)} 张",
             "photos": items}
            for i, items in enumerate(groups)
        ],
    }
    target = ROOT / ".tools" / "local-manifest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Read {len(photos)} JPEG/PNG headers; {raw_count} RAW files were not loaded.")
    print(f"Found {len(groups)} candidate time groups, {sum(len(g) >= 3 for g in groups)} with 3+ photos.")
    if warnings:
        print(f"{len(warnings)} header errors recorded in the local manifest.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    prepare(args.directory)
