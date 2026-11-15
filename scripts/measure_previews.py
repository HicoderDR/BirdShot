# SPDX-License-Identifier: AGPL-3.0-only
"""Measure actual HTTP preview generation/hits without altering original photos."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

from PIL import Image

from comparison_prepare import atomic, sha256
from preview_cache import POLICY


def fetch(url, headers=None):
    start = time.perf_counter()
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=60) as response:
        data = response.read()
        elapsed = time.perf_counter() - start
        return data, dict(response.headers), elapsed


def measure(job, base):
    manifest = json.loads((job / "manifest.json").read_text(encoding="utf-8"))
    photos = manifest["photos"]
    landscape = next(p for p in photos if p["exifOrientation"] == 1 and p["encodedSize"][0] >= p["encodedSize"][1])
    portrait = next(p for p in photos if p["id"] == "p000150")
    remaining = [p for p in photos if p["id"] not in {landscape["id"], portrait["id"]}]
    third = min(remaining, key=lambda p: (p["encodedSize"][0] * p["encodedSize"][1], p["primary"]["size"]))
    source = Path(manifest["source"])
    sizes = {tuple(p["encodedSize"]) for p in photos}
    output = {"version": "http-on-demand-preview-measurements-v1", "base": base,
              "measurementScope": "同一电脑经指定HTTP地址测量，不是独立手机Wi-Fi性能",
              "encodingPolicy": POLICY, "quality": 88, "cacheBudgetBytes": 512 * 1024 * 1024, "concurrentDecodes": 2,
              "sourceSizeNote": "登记原图只有一个编码尺寸；第三张选择文件字节最小者，小图不上采样另由临时fixture验证" if len(sizes) == 1 else "第三张选登记像素尺寸最小者",
              "photos": [], "negativeTests": []}
    for role, photo in [("landscape", landscape), ("orientation8", portrait), ("smallest-available", third)]:
        path = source / photo["primary"]["relative"]
        before, stat = sha256(path), path.stat()
        displayed = photo["encodedSize"][::-1] if photo["exifOrientation"] in {5, 6, 7, 8} else photo["encodedSize"]
        with Image.open(path) as original:
            original_icc = original.info.get("icc_profile")
        record = {"role": role, "frameId": photo["id"], "sourceRelative": photo["primary"]["relative"],
                  "originalBytes": stat.st_size, "originalDisplaySize": displayed,
                  "originalSha256": before, "orientation": photo["exifOrientation"], "previews": []}
        for edge in [960, 1920, 3840]:
            url = f"{base}/preview/{photo['id']}/{edge}.jpg"
            data, headers, first_seconds = fetch(url)
            warm, warm_headers, hit_seconds = fetch(url)
            if headers["Content-Type"] != "image/jpeg" or "private" not in headers["Cache-Control"] or "public" in headers["Cache-Control"]:
                raise RuntimeError("Incorrect preview MIME/cache privacy policy")
            if data != warm or warm_headers.get("X-BirdShot-Preview-Cache") != "hit":
                raise RuntimeError("Cache hit did not reuse the completed preview")
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                dimensions = list(image.size)
                if max(dimensions) > edge or max(dimensions) > max(displayed) or image.getexif():
                    raise RuntimeError("Wrong preview size, upsampling or retained EXIF orientation")
                if abs(dimensions[0] / dimensions[1] - displayed[0] / displayed[1]) > 1 / min(dimensions):
                    raise RuntimeError("Preview aspect ratio changed")
                if original_icc != image.info.get("icc_profile"):
                    raise RuntimeError("RGB source ICC profile was not preserved")
            try:
                fetch(url, {"If-None-Match": headers["ETag"]})
                raise RuntimeError("Conditional request did not return 304")
            except urllib.error.HTTPError as error:
                if error.code != 304:
                    raise
            record["previews"].append({
                "edge": edge, "displaySize": dimensions, "bytes": len(data),
                "coldSeconds": first_seconds if headers.get("X-BirdShot-Preview-Cache") == "generated" else None,
                "firstRequestSeconds": first_seconds, "firstRequestCacheState": headers.get("X-BirdShot-Preview-Cache"),
                "hitSeconds": hit_seconds, "hitReencoded": False, "conditionalStatus": 304,
                "etag": headers["ETag"], "lastModified": headers["Last-Modified"], "cacheControl": headers["Cache-Control"],
            })
        for suffix, attachment in [("", False), ("?download=1", True)]:
            data, headers, _ = fetch(f"{base}/original/{photo['id']}{suffix}")
            if hashlib.sha256(data).hexdigest() != before:
                raise RuntimeError("Original/save response bytes differ from the untouched source")
            if attachment != headers.get("Content-Disposition", "").startswith("attachment;"):
                raise RuntimeError("Incorrect inline/download disposition")
        if sha256(path) != before or (path.stat().st_size, path.stat().st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
            raise RuntimeError("Original changed during preview measurement")
        record["sourceUnchanged"] = True
        record["originalAndSaveBytesExact"] = True
        output["photos"].append(record)
    for route, status in [("/preview/p999999/960.jpg", 404), ("/preview/p000150/999999999.jpg", 400),
                          ("/preview/p000150/1920.jpg?path=C:", 400), ("/preview/../manifest.json", 400),
                          ("/preview/%2e%2e/1920.jpg", 400)]:
        try:
            fetch(base + route)
            raise RuntimeError("Invalid preview request was accepted")
        except urllib.error.HTTPError as error:
            if error.code != status:
                raise
            output["negativeTests"].append({"route": route, "status": status})
    output["verified"] = True
    atomic(job / "revisions" / "image-preview-measurements-v1.json", output)
    return output


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--base", default="http://127.0.0.1:5174")
    args = parser.parse_args()
    print(json.dumps(measure(args.job.resolve(strict=True), args.base.rstrip("/")), ensure_ascii=False))
