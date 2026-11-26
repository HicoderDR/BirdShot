# SPDX-License-Identifier: AGPL-3.0-only
"""Sequential, resumable, crop-only local selection and report server."""
import argparse
from collections import Counter
from datetime import datetime
from email.utils import formatdate
import hashlib
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, quote, unquote, urlparse
import uuid

import cv2
import numpy as np
from PIL import Image

from batch_inference import NativeDetector, INFERENCE_VERSION, ROOT
from preview_cache import PreviewCache, not_modified
from review_store import read_snapshot
from species_reference import SpeciesReferences, ReferenceError
from gallery_navigation import capture_metadata, enrich_navigation

POLICY_VERSION = "strict-crop-only-v1"
SUPPORTED = {".jpg", ".jpeg", ".png"}
RAW = {".arw", ".dng", ".nef", ".cr2", ".cr3", ".orf", ".raf", ".rw2"}
PHOTO_FORMATS = SUPPORTED | RAW | {".tif", ".tiff", ".heic", ".heif", ".webp", ".bmp"}


def report_access_allowed(client_ip, host, origin, port, hosts, network=None, allow_all=False, write=False):
    try:
        remote = ipaddress.ip_address(client_ip)
        if remote.version != 4:
            return False
        authority = urlparse("//" + host)
        if authority.username or authority.password or authority.path or authority.query or authority.fragment \
                or authority.port not in {None, port} or not authority.hostname or any(char.isspace() for char in host):
            return False
        hostname = authority.hostname.lower()
        if hostname not in hosts or origin and origin != "http://" + host:
            return False
        local = remote == ipaddress.ip_address("127.0.0.1")
        if write:
            return local and hostname in {"127.0.0.1", "localhost"}
        return allow_all or local or network is not None and remote in network
    except ValueError:
        return False


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def atomic_json(path, value):
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    for attempt in range(10):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(.05)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def versions():
    paths = [ROOT / "scripts" / "batch_inference.py", ROOT / "scripts" / "batch-policy.mjs",
             ROOT / "src" / "culling" / "scoring.ts", ROOT / "src" / "lib" / "tracker.ts"]
    models = ROOT / "public" / "culling" / "models"
    return {
        "inference": INFERENCE_VERSION, "policy": POLICY_VERSION,
        "codeSha256": hashlib.sha256("".join(digest(p) for p in paths).encode()).hexdigest(),
        "models": {name: digest(models / name) for name in ["bird-yolo11s-seg.onnx", "eye.onnx"]},
    }


def inventory(source, exclude_paths=None):
    records, others, formats, warnings = [], [], Counter(), []
    excluded = {os.path.normcase(str(Path(path).resolve())) for path in exclude_paths or []}
    def scan_error(error):
        raise error
    paths = (Path(folder) / name for folder, _, names in os.walk(source, onerror=scan_error) for name in names)
    for path in sorted(paths):
        if not path.is_file():
            continue
        relative = path.relative_to(source).as_posix()
        suffix = path.suffix.lower()
        formats[suffix or "(无扩展名)"] += 1
        if suffix not in PHOTO_FORMATS:
            others.append(relative)
            continue
        if not path.resolve().is_relative_to(source):
            warnings.append(f"路径越出源目录，未读取：{relative}")
            records.append({"relative": relative, "format": suffix, "blocked": True, "size": 0, "mtimeNs": 0})
            continue
        stat = path.stat()
        records.append({"relative": relative, "format": suffix, "size": stat.st_size, "mtimeNs": stat.st_mtime_ns})
    buckets = {}
    for record in records:
        path = Path(record["relative"])
        buckets.setdefault((path.parent.as_posix().casefold(), path.stem.casefold()), []).append(record)
    photos = []
    for entries in buckets.values():
        previews = [r for r in entries if r["format"] in SUPPORTED]
        raws = [r for r in entries if r["format"] in RAW]
        primaries = previews + [r for r in entries if r["format"] not in SUPPORTED | RAW]
        if not previews:
            primaries += raws
        for record in primaries:
            if os.path.normcase(str((source / record["relative"]).resolve())) in excluded:
                continue
            companions = raws if previews and record is previews[0] else []
            photo = {"id": f"p{len(photos) + 1:06d}", "primary": record, "companions": companions,
                     "supported": record["format"] in SUPPORTED and not record.get("blocked"),
                     "capturedAt": None, "timestamp": None, "exifOrientation": 1, "encodedSize": None}
            if photo["supported"]:
                try:
                    with Image.open(source / record["relative"]) as image:
                        exif = image.getexif()
                        detail = exif.get_ifd(0x8769)
                        stamp = detail.get(36867)
                        sub = str(detail.get(37521, "0")).strip("\0 ")
                        milliseconds = int((sub + "000")[:3]) if sub.isdigit() else 0
                        when = datetime.strptime(stamp, "%Y:%m:%d %H:%M:%S") if stamp else None
                        photo["capturedAt"] = when.isoformat() + f".{milliseconds:03d}" if when else None
                        photo["timestamp"] = when.timestamp() * 1000 + milliseconds if when else None
                        photo["exifOrientation"] = int(exif.get(274, 1))
                        photo["encodedSize"] = [image.width, image.height]
                except (OSError, ValueError, TypeError) as error:
                    photo["metadataError"] = str(error)
            photos.append(photo)
    timed = sorted((p for p in photos if p["timestamp"] is not None), key=lambda p: (p["timestamp"], p["primary"]["relative"]))
    groups, current = [], []
    for photo in timed:
        if current and (photo["timestamp"] - current[-1]["timestamp"] > 2000 or photo["timestamp"] - current[0]["timestamp"] > 10000):
            groups.append(current)
            current = []
        current.append(photo)
    if current:
        groups.append(current)
    groups.extend([[p] for p in photos if p["timestamp"] is None])
    group_records = [{"id": f"g{i + 1:05d}", "index": i + 1, "photoIds": [p["id"] for p in group],
                      "count": len(group), "capturedAt": group[0]["capturedAt"],
                      "grouping": "EXIF原始拍摄时间+子秒，相邻≤2秒，总长≤10秒" if group[0]["timestamp"] is not None else "无可信拍摄时间，独立单张",
                      "status": "pending", "thumbnail": None} for i, group in enumerate(groups)]
    return {
        "source": str(source), "photos": photos, "groups": group_records, "formats": dict(formats),
        "physicalPhotos": sum(1 + len(p["companions"]) for p in photos), "pairedRaw": sum(len(p["companions"]) for p in photos),
        "rawOnly": sum(not p["supported"] and p["primary"]["format"] in RAW for p in photos),
        "otherFiles": others, "warnings": warnings, "sourceBytes": sum(r["size"] for r in records),
    }


def initialize(source, output=None, exclude_paths=None, snapshot=None):
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise ValueError("源目录不存在")
    job = Path(output).resolve() if output is not None else source.parent / f"BirdShot_Selection_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
    if job.is_relative_to(source):
        raise ValueError("输出不能位于原片目录内")
    job.parent.mkdir(parents=True, exist_ok=True)
    manifest = snapshot if snapshot is not None else inventory(source, exclude_paths)
    free = shutil.disk_usage(job.parent).free
    if free < 1024 * 1024 * 1024:
        raise RuntimeError("可用空间不足1GiB，未创建输出")
    model_versions = versions()
    job.mkdir()
    for folder in ["groups", "crops", "selected", "report"]:
        (job / folder).mkdir()
    manifest.update({"output": str(job), "createdAt": datetime.now().isoformat(), "versions": model_versions,
                     "storage": "crop-only", "freeBytesAtStart": free,
                     "limitations": "JPEG用于分析；配对RAW只记录溯源，不独立解码或复制。单张无绝对合焦校准，严格策略待审。眼区不参与自动选择。"})
    atomic_json(job / "manifest.json", manifest)
    atomic_json(job / "groups.json", manifest["groups"])
    atomic_json(job / "mapping.json", {"sourceRoot": str(source), "versions": manifest["versions"], "crops": []})
    state = {"status": "pending", "totalGroups": len(manifest["groups"]), "totalPhotos": len(manifest["photos"]),
             "physicalPhotos": manifest["physicalPhotos"], "pairedRaw": manifest["pairedRaw"],
             "completedGroups": 0, "processedPhotos": 0, "selectedPhotos": 0, "reviewPhotos": 0,
             "rejectedPhotos": 0, "failedPhotos": 0, "unsupportedPhotos": 0, "cropCount": 0, "selectedCrops": 0,
             "currentGroup": None, "phase": "已清点，等待开始", "output": str(job), "startedAt": None, "updatedAt": datetime.now().isoformat()}
    atomic_json(job / "status.json", state)
    shutil.copyfile(ROOT / "scripts" / "batch-report.html", job / "report" / "index.html")
    if output is None:
        (ROOT / ".tools").mkdir(exist_ok=True)
        atomic_json(ROOT / ".tools" / "batch-current.json", {"job": str(job)})
    print(json.dumps({"job": str(job), "photos": state["totalPhotos"], "physical": state["physicalPhotos"],
                      "pairedRaw": state["pairedRaw"], "rawOnly": manifest["rawOnly"], "groups": state["totalGroups"]}, ensure_ascii=False), flush=True)
    return job


def write_image(path, image, quality=95):
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("裁剪图编码失败")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    data = encoded.tobytes()
    temporary.write_bytes(data)
    temporary.replace(path)
    expected = hashlib.sha256(data).hexdigest()
    if digest(path) != expected:
        raise RuntimeError("裁剪图写入完整性校验失败")
    return expected


def create_crops(job, image, frame, photo, source_hash):
    height, width = image.shape[:2]
    records = []
    for bird in frame["instances"]:
        bx, by, bw, bh = bird["body"]
        margin = max(bw, bh) * .05
        x, y = max(0, math.floor(bx - margin)), max(0, math.floor(by - margin))
        right, bottom = min(width, math.ceil(bx + bw + margin)), min(height, math.ceil(by + bh + margin))
        relative = f"crops/{frame['group']}/{frame['id']}_b{bird['id']:02d}.jpg"
        target = job / relative
        crop_hash = write_image(target, image[y:bottom, x:right])
        thumb_relative = f"crops/{frame['group']}/{frame['id']}_b{bird['id']:02d}_thumb.jpg"
        crop = image[y:bottom, x:right]
        scale = min(1, 420 / max(crop.shape[:2]))
        thumbnail = cv2.resize(crop, (max(1, round(crop.shape[1] * scale)), max(1, round(crop.shape[0] * scale))))
        write_image(job / thumb_relative, thumbnail, 85)
        bird["crop"] = relative
        bird["thumbnail"] = thumb_relative
        bird["cropRect"] = [x, y, right - x, bottom - y]
        bird["cropSha256"] = crop_hash
        records.append({
            "crop": relative, "cropSha256": crop_hash, "groupId": frame["group"], "photoId": frame["id"], "instanceId": bird["id"],
            "sourceRelative": photo["primary"]["relative"], "sourceSha256": source_hash,
            "rawCompanions": [raw["relative"] for raw in photo["companions"]],
            "sourceSizeBytes": photo["primary"]["size"], "sourceMtimeNs": photo["primary"]["mtimeNs"],
            "encodedOriginalSize": photo["encodedSize"], "displayOriginalSize": [width, height],
            "exifOrientation": photo["exifOrientation"], "coordinateSystem": "JPEG显示方向（已应用EXIF），原像素",
            "cropRect": bird["cropRect"], "detectedBox": bird["body"], "resized": False, "jpegQuality": 95,
            "bodyConfidence": bird["bodyConfidence"], "bodyTenengrad": bird["bodySharpness"], "bodyPixels": bird["bodyPixels"],
            "eyeCandidateTenengrad": bird["sharpness"], "eyeIssue": bird["eyeIssue"], "selected": False,
        })
    return records


class PolicyWorker:
    def __init__(self, node):
        self.process = subprocess.Popen([str(node), "--experimental-strip-types", str(ROOT / "scripts" / "batch-policy.mjs")],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8", cwd=ROOT)
    def rank(self, frames):
        self.process.stdin.write(json.dumps({"frames": frames}, allow_nan=False) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("共享分组策略进程退出")
        result = json.loads(line)
        if not result["ok"]:
            raise RuntimeError(result["error"])
        return result["results"]
    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=15)
        self.process.stdout.close()


def empty_frame(photo, group, issue):
    return {"id": photo["id"], "group": group, "width": 0, "height": 0, "body": None, "bodyConfidence": None,
            "birdCount": 0, "eye": None, "beak": None, "patch": None, "sharpness": None, "issue": issue,
            "decision": "review", "reason": issue, "relative": None, "rank": None, "cohortSize": 0,
            "instances": [], "selectedInstance": None, "bodySharpness": None, "bodyPixels": 0, "basis": None}


def recover_completed(job, manifest, state, skip_source_groups=None):
    source = Path(manifest["source"]).resolve()
    groups = read_json(job / "groups.json")
    mapping = {"sourceRoot": str(source), "versions": manifest["versions"], "crops": []}
    for key in ["completedGroups", "processedPhotos", "selectedPhotos", "reviewPhotos", "rejectedPhotos",
                "failedPhotos", "unsupportedPhotos", "cropCount", "selectedCrops"]:
        state[key] = 0
    for group in groups:
        result_path = job / "groups" / f"{group['id']}.json"
        if not result_path.exists():
            group["status"] = "pending"
            continue
        result = read_json(result_path)
        if result["versions"] != manifest["versions"]:
            raise RuntimeError("已完成组的模型/策略版本不一致")
        frames = result["frames"]
        for frame in frames:
            if frame.get("sourceSha256") and group["id"] not in (skip_source_groups or set()) and (
                    not frame.get("historicalOrigin") or frame["historicalOrigin"]["compatible"]):
                original = (source / frame["source"]["primary"]["relative"]).resolve()
                if not original.is_relative_to(source) or digest(original) != frame["sourceSha256"]:
                    raise RuntimeError("已完成组的原片指纹改变，拒绝混用缓存")
        crop_records = result["cropMapping"]
        for record in crop_records:
            if digest(job / record["crop"]) != record["cropSha256"]:
                raise RuntimeError("缓存裁剪完整性校验失败")
            if record.get("selected") and digest(job / record["selectedPath"]) != record["cropSha256"]:
                raise RuntimeError("缓存选中裁剪完整性校验失败")
        group.update(status="pending" if result.get("phase") == "extending" else "done",
                     thumbnail=next((b["thumbnail"] for f in frames for b in f["instances"] if b.get("thumbnail")), None),
                     selected=sum(f["decision"] == "keep" and bool(f.get("selectedCrop")) for f in frames),
                     review=sum(f["decision"] == "review" and f.get("processingStatus") == "ok" for f in frames),
                     failed=sum(f.get("processingStatus") == "failed" for f in frames))
        state["completedGroups"] += 1
        state["processedPhotos"] += len(frames)
        state["selectedPhotos"] += group["selected"]
        state["reviewPhotos"] += group["review"]
        state["failedPhotos"] += group["failed"]
        state["rejectedPhotos"] += sum(f["decision"] == "reject" and f.get("processingStatus") == "ok" for f in frames)
        state["unsupportedPhotos"] += sum(f.get("processingStatus") == "unsupported" for f in frames)
        state["cropCount"] += len(crop_records)
        state["selectedCrops"] += len(result["selectedCrops"])
        mapping["crops"].extend(crop_records)
    atomic_json(job / "groups.json", groups)
    atomic_json(job / "mapping.json", mapping)
    atomic_json(job / "status.json", state)
    return groups, mapping


def run(job, node, threads=4, on_detected=None, on_group=None, on_progress=None, skip_groups=None):
    manifest, state = read_json(job / "manifest.json"), read_json(job / "status.json")
    if manifest["versions"] != versions():
        raise RuntimeError("模型或策略版本已变化，不能混用旧任务缓存")
    source = Path(manifest["source"]).resolve(strict=True)
    groups, mapping = recover_completed(job, manifest, state, set(skip_groups or []))
    photos = {p["id"]: p for p in manifest["photos"]}
    state.update({"status": "running", "startedAt": state["startedAt"] or datetime.now().isoformat(), "phase": "载入本机CPU模型"})
    atomic_json(job / "status.json", state)
    worker, detector = None, None
    skipped = set(skip_groups or [])
    try:
        (job / "cancel.request").unlink(missing_ok=True)
        for group in groups:
            if group["id"] in skipped:
                continue
            if group["status"] == "done":
                if on_group:
                    on_group(group, read_json(job / "groups" / f"{group['id']}.json"), state)
                continue
            if (job / "cancel.request").exists():
                state.update(status="paused", phase="已安全暂停，已完成组保留，可恢复")
                break
            group["status"] = "running"
            state["currentGroup"] = group["id"]
            atomic_json(job / "groups.json", groups)
            if on_progress:
                on_progress("before_group", "开始连拍组检测", group, state)
            cached_path = job / "groups" / f"{group['id']}.json"
            cached = read_json(cached_path) if cached_path.exists() else {}
            cached_frames = {frame["id"]: frame for frame in cached.get("frames", [])} if cached.get("phase") == "extending" else {}
            if cached_frames:
                mapping["crops"] = [row for row in mapping["crops"] if row["groupId"] != group["id"]]
                state["processedPhotos"] -= len(cached_frames)
                state["completedGroups"] -= 1
                state["cropCount"] -= len(cached.get("cropMapping", []))
                state["selectedPhotos"] -= sum(frame["decision"] == "keep" and bool(frame.get("selectedCrop")) for frame in cached_frames.values())
                state["reviewPhotos"] -= sum(frame["decision"] == "review" and frame.get("processingStatus") == "ok" for frame in cached_frames.values())
                state["rejectedPhotos"] -= sum(frame["decision"] == "reject" and frame.get("processingStatus") == "ok" for frame in cached_frames.values())
                state["failedPhotos"] -= sum(frame.get("processingStatus") == "failed" for frame in cached_frames.values())
                state["unsupportedPhotos"] -= sum(frame.get("processingStatus") == "unsupported" for frame in cached_frames.values())
                state["selectedCrops"] -= len(cached.get("selectedCrops", []))
            if worker is None:
                worker = PolicyWorker(node)
            frames, crop_records, issues = [], [], []
            for index, photo_id in enumerate(group["photoIds"]):
                photo = photos[photo_id]
                if photo_id in cached_frames:
                    frames.append(cached_frames[photo_id])
                    crop_records.extend(row for row in cached["cropMapping"] if row["photoId"] == photo_id)
                    continue
                def progress(message):
                    state["phase"] = f"组{group['index']}/{len(groups)} · 照片{index + 1}/{group['count']} · {message}"
                    state["updatedAt"] = datetime.now().isoformat()
                    atomic_json(job / "status.json", state)
                    if on_progress:
                        on_progress("detect", state["phase"], group, state)
                progress("读取原图，仅读不写")
                path = (source / photo["primary"]["relative"]).resolve()
                if not photo["supported"]:
                    frame = empty_frame(photo, group["id"], "格式未支持或路径越界，已清点但未解码，不生成虚假裁剪")
                    frame["processingStatus"] = "unsupported"
                else:
                    try:
                        if detector is None:
                            if on_progress:
                                on_progress("models", "载入YOLO分割与姿态CPU模型", group, state)
                            detector = NativeDetector(threads)
                            if on_progress:
                                on_progress("models_ready", "YOLO分割与姿态模型已就绪", group, state)
                        if not path.is_relative_to(source):
                            raise RuntimeError("源图片路径越出已授权目录")
                        stat = path.stat()
                        if stat.st_size != photo["primary"]["size"] or stat.st_mtime_ns != photo["primary"]["mtimeNs"]:
                            raise RuntimeError("源文件相对清点时已变化，严格策略不处理此版本")
                        data = path.read_bytes()
                        source_hash = hashlib.sha256(data).hexdigest()
                        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                        if image is None:
                            raise RuntimeError("JPEG/PNG解码失败")
                        if image.shape[0] * image.shape[1] > 60_000_000:
                            raise RuntimeError("超出本机Demo 6000万像素限制")
                        frame = detector.analyze(image, photo_id, group["id"], progress)
                        records = create_crops(job, image, frame, photo, source_hash)
                        after = path.stat()
                        if after.st_size != stat.st_size or after.st_mtime_ns != stat.st_mtime_ns:
                            raise RuntimeError("分析期间源文件发生外部变化，结果不能作为保留依据")
                        frame["sourceSha256"] = source_hash
                        frame["processingStatus"] = "ok"
                        crop_records.extend(records)
                        del image, data
                    except (OSError, ValueError, RuntimeError, cv2.error) as error:
                        frame = empty_frame(photo, group["id"], str(error))
                        frame["processingStatus"] = "failed"
                        issues.append({"photoId": photo_id, "error": str(error)})
                        print(f"{group['id']} {photo_id}: {error}", file=sys.stderr, flush=True)
                frame["source"] = photo
                frames.append(frame)
            if on_detected:
                detected = {"group": {**group, "status": "detected"}, "frames": frames, "errors": issues,
                            "selectedCrops": [], "cropMapping": crop_records, "versions": manifest["versions"],
                            "phase": "detected", "completedAt": datetime.now().isoformat()}
                atomic_json(job / "groups" / f"{group['id']}.json", detected)
                atomic_json(job / "mapping.json", {**mapping, "crops": mapping["crops"] + crop_records})
                on_detected(group, detected, state)
            for frame in frames:
                frame.pop("selectedCrop", None)
            for record in crop_records:
                record["selected"] = False
                record.pop("selectedPath", None)
            comparable = [frame for frame in frames if not frame.get("historicalOrigin") or frame["historicalOrigin"]["compatible"]]
            by_id = {frame["id"]: frame for frame in worker.rank(comparable)}
            ranked = [by_id.get(frame["id"], frame) for frame in frames]
            selected_paths = []
            for frame in ranked:
                for record in (r for r in crop_records if r["photoId"] == frame["id"]):
                    record.update({"decision": frame["decision"], "relative": frame["relative"], "basis": frame["basis"],
                                   "cohortSize": frame["cohortSize"], "reason": frame["reason"],
                                   "isFocusInstance": record["instanceId"] == frame["selectedInstance"]})
                if frame["decision"] != "keep" or frame.get("processingStatus") != "ok":
                    continue
                bird = next(b for b in frame["instances"] if b["id"] == frame["selectedInstance"])
                source_crop = job / bird["crop"]
                selected_relative = f"selected/{group['id']}_{frame['id']}_b{bird['id']:02d}.jpg"
                destination = job / selected_relative
                temporary = destination.with_name(destination.name + ".part")
                try:
                    if shutil.disk_usage(job).free < source_crop.stat().st_size + 100 * 1024 * 1024:
                        raise RuntimeError("剩余空间不足，未保存选中裁剪")
                    shutil.copyfile(source_crop, temporary)
                    if digest(temporary) != bird["cropSha256"]:
                        raise RuntimeError("选中裁剪复制校验失败")
                    if destination.exists() and digest(destination) != bird["cropSha256"]:
                        raise RuntimeError("输出已有不同内容，拒绝覆盖")
                    temporary.replace(destination)
                    frame["selectedCrop"] = selected_relative
                    selected_paths.append(selected_relative)
                    for record in crop_records:
                        if record["crop"] == bird["crop"]:
                            record.update(selected=True, selectedPath=selected_relative)
                except (OSError, RuntimeError) as error:
                    temporary.unlink(missing_ok=True)
                    frame.update(processingStatus="failed", decision="review", reason=str(error))
                    for record in crop_records:
                        if record["photoId"] == frame["id"]:
                            record.update(decision="review", reason=str(error), selected=False)
                    issues.append({"photoId": frame["id"], "error": str(error)})
            completed = {"group": group, "frames": ranked, "errors": issues, "selectedCrops": selected_paths, "cropMapping": crop_records,
                         "versions": manifest["versions"], "completedAt": datetime.now().isoformat()}
            atomic_json(job / "groups" / f"{group['id']}.json", completed)
            group.update(status="done", thumbnail=next((b["thumbnail"] for f in ranked for b in f["instances"] if b.get("thumbnail")), None),
                         selected=sum(f["decision"] == "keep" and bool(f.get("selectedCrop")) for f in ranked),
                         review=sum(f["decision"] == "review" and f.get("processingStatus") == "ok" for f in ranked),
                         failed=sum(f.get("processingStatus") == "failed" for f in ranked))
            state["completedGroups"] += 1
            state["processedPhotos"] += len(ranked)
            state["selectedPhotos"] += sum(f["decision"] == "keep" and bool(f.get("selectedCrop")) for f in ranked)
            state["reviewPhotos"] += sum(f["decision"] == "review" and f.get("processingStatus") == "ok" for f in ranked)
            state["rejectedPhotos"] += sum(f["decision"] == "reject" and f.get("processingStatus") == "ok" for f in ranked)
            state["failedPhotos"] += sum(f.get("processingStatus") == "failed" for f in ranked)
            state["unsupportedPhotos"] += sum(f.get("processingStatus") == "unsupported" for f in ranked)
            state["cropCount"] += len(crop_records)
            state["selectedCrops"] += len(selected_paths)
            state["updatedAt"] = datetime.now().isoformat()
            mapping["crops"].extend(crop_records)
            atomic_json(job / "mapping.json", mapping)
            atomic_json(job / "groups.json", groups)
            atomic_json(job / "status.json", state)
            print(f"{group['id']} done {state['processedPhotos']}/{state['totalPhotos']} selected={state['selectedPhotos']} review={state['reviewPhotos']} crops={state['cropCount']}", flush=True)
            if on_group:
                on_group(group, completed, state)
        else:
            state.update(status="completed", phase="全量已处理；待审/失败/未支持均明确列出", currentGroup=None)
        atomic_json(job / "status.json", state)
    except BaseException as error:
        state.update(status="paused" if isinstance(error, KeyboardInterrupt) else "error", phase=str(error) or "处理已中断")
        atomic_json(job / "status.json", state)
        raise
    finally:
        if worker:
            worker.close()


def serve(job, port, lan_ip=None, prefix=24, allow_all=False, allowed_hosts=None, instance_id=None, read_only=False,
          review_state=None):
    manifest = read_json(job / "manifest.json")
    source = Path(manifest["source"]).resolve()
    photos = {p["id"]: p for p in manifest["photos"]}
    previews = PreviewCache(source, photos, job / "revisions" / "image-preview-cache-v1",
                            source_roots=manifest.get("sourceRoots", [source]))
    manifest_signature = None
    manifest_lock = threading.Lock()
    registered_media = {}
    def refresh_manifest():
        nonlocal manifest, manifest_signature, registered_media
        path = job / "manifest.json"
        with manifest_lock:
            signature = (path.stat().st_mtime_ns, path.stat().st_size)
            if signature == manifest_signature:
                return
            updated = read_json(path)
            roots = {Path(root).resolve() for root in updated.get("sourceRoots", [source])}
            if any(previews.root.is_relative_to(root) for root in roots):
                raise ValueError("Preview cache overlaps a registered original directory")
            media_roots = {Path(root).resolve() for root in updated.get("mediaRoots", [])}
            media = {}
            for record in updated.get("media", []):
                root, relative = Path(record["root"]).resolve(), Path(record["relative"])
                candidate = (root / relative).resolve()
                if root in media_roots and not relative.is_absolute() and ".." not in relative.parts and candidate.is_relative_to(root):
                    media[record["path"]] = candidate
            previews.photos = {photo["id"]: photo for photo in updated["photos"]}
            previews.source_roots = roots
            manifest, manifest_signature, registered_media = updated, signature, media
    refresh_manifest()
    gallery_revision = job / "revisions" / "verified-birds-birder-v2"
    species_references = SpeciesReferences(job, [])
    species_index_signature = None
    species_index_lock = threading.Lock()
    def reference_manager():
        nonlocal species_index_signature
        path = gallery_revision / "gallery.json"
        with species_index_lock:
            signature = (path.stat().st_mtime_ns, path.stat().st_size)
            if signature != species_index_signature:
                gallery = read_json(path)
                species_references.allowed = {candidate["scientificName"].casefold(): candidate["scientificName"]
                    for item in gallery["items"] for candidate in item.get("species", {}).get("top5", [])
                    if candidate.get("isBird") and isinstance(candidate.get("scientificName"), str)}
                species_index_signature = signature
        return species_references
    lan_network = None
    if lan_ip:
        address = ipaddress.IPv4Address(lan_ip)
        if not any(address in ipaddress.IPv4Network(block) for block in ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]):
            raise ValueError("LAN service requires the explicitly selected RFC1918 interface address")
        lan_network = ipaddress.IPv4Network(f"{lan_ip}/{prefix}", strict=False)
    hosts = {"127.0.0.1", "localhost"} | ({lan_ip} if lan_ip else set())
    if allow_all:
        hosts.add(socket.gethostname().lower())
        try:
            hosts.update(row[4][0] for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM))
        except socket.gaierror:
            print("Local IPv4 discovery failed; use --allowed-host for an additional local address", file=sys.stderr)
    for hostname in allowed_hosts or []:
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", hostname):
            raise ValueError("--allowed-host requires an exact hostname or IPv4, without URL/path/port")
        hosts.add(hostname.lower())
    allowed_media = set()
    media_mapping_signature = None
    def refresh_registered_media():
        nonlocal allowed_media, media_mapping_signature
        mapping_path = job / "mapping.json"
        signature = (mapping_path.stat().st_mtime_ns, mapping_path.stat().st_size)
        if signature == media_mapping_signature:
            return
        registered = set()
        for record in read_json(mapping_path)["crops"]:
            registered.add(record["crop"])
            registered.add(record["crop"][:-4] + "_thumb.jpg")
            if record.get("selectedPath"):
                registered.add(record["selectedPath"])
        allowed_media, media_mapping_signature = registered, signature
    refresh_registered_media()
    compressed_cache = {}
    class Handler(BaseHTTPRequestHandler):
        def end_headers(self):
            self.send_header("X-BirdShot-Access-Mode", "all-sources-read-only" if allow_all else "restricted")
            super().end_headers()
        def log_message(self, *_):
            pass
        def allowed(self, write=False):
            return report_access_allowed(self.client_address[0], self.headers.get("Host", ""),
                                         self.headers.get("Origin"), port, hosts, lan_network, allow_all, write)
        def image_headers(self, status, source_info, length=None, download_name=None, cache_state=None, content_type="image/jpeg"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("ETag", source_info.etag)
            self.send_header("Last-Modified", formatdate(source_info.modified, usegmt=True))
            self.send_header("Cache-Control", "private, max-age=0, must-revalidate")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("X-Content-Type-Options", "nosniff")
            if length is not None:
                self.send_header("Content-Length", str(length))
            if cache_state:
                self.send_header("X-BirdShot-Preview-Cache", cache_state)
            if download_name:
                safe_name = re.sub(r'[^A-Za-z0-9._-]', '_', download_name)
                self.send_header("Content-Disposition", f"attachment; filename=\"{safe_name}\"; filename*=UTF-8''{quote(download_name, safe='')}")
            self.end_headers()
        def serve_image(self, frame_id, edge=None, download=False):
            try:
                snapshot_path = job / "review.json"
                if review_state or snapshot_path.exists():
                    snapshot = read_json(snapshot_path) if snapshot_path.exists() else read_snapshot(job, review_state)
                    reviewed_frame = next((frame for group in snapshot["groups"] for frame in group["frames"]
                                           if frame["id"] == frame_id), None)
                    if reviewed_frame and not reviewed_frame["available"]:
                        self.send_error(404, "Registered original unavailable; historical crops remain")
                        return
                info = previews.describe(frame_id, edge)
                photo = previews.photos[frame_id]
                primary = photo["primary"]
                if photo.get("sourceIssue") or (info.size, info.mtime_ns) != (primary["size"], primary["mtimeNs"]):
                    raise RuntimeError("Original differs from the registered analysis; historical crops remain available")
                filename = info.path.name if download else None
                content_type = "image/png" if edge is None and info.path.suffix.lower() == ".png" else "image/jpeg"
                if not_modified(self.headers, info):
                    self.image_headers(304, info, download_name=filename, content_type=content_type)
                    return
                if edge is None:
                    data = info.path.read_bytes()
                    after = info.path.stat()
                    if (after.st_size, after.st_mtime_ns) != (info.size, info.mtime_ns):
                        raise RuntimeError("Original changed while it was read")
                    state = None
                else:
                    preview = previews.get(frame_id, edge)
                    data, info = preview.data, preview.source
                    state = "hit" if preview.hit else "generated"
                self.image_headers(200, info, len(data), filename, state, content_type)
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as error:
                    print(f"Image client disconnected: {type(error).__name__}", file=sys.stderr)
            except KeyError:
                self.send_error(404, "Unknown registered image")
            except ValueError as error:
                print(f"Invalid image preview request: {error}", file=sys.stderr)
                self.send_error(400, "Invalid image preview parameters")
            except FileNotFoundError as error:
                print(f"Registered image/cache path missing for {frame_id}: {error}", file=sys.stderr)
                self.send_error(404, "Registered original is missing")
            except (OSError, RuntimeError, Image.DecompressionBombError) as error:
                print(f"Image preview failed for {frame_id}: {error}", file=sys.stderr)
                self.send_error(500, "Image preview could not be generated")
        def write_report(self, data):
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as error:
                print(f"Report client disconnected: {type(error).__name__}", file=sys.stderr)
        def do_GET(self):
            if not self.allowed():
                self.send_error(403)
                return
            route = unquote(urlparse(self.path).path)
            if "\x00" in route:
                self.send_error(400)
                return
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            if route == "/gallery-refresh-bridge":
                # The file-origin desktop parent cannot impersonate an HTTP origin.
                # This unprivileged sibling emits only a same-origin refresh notice.
                data = b"""<!doctype html><meta charset="utf-8"><script>
                addEventListener('message', event => {
                  if (parent === window || event.source !== parent || event.origin !== 'null' ||
                      !event.data || event.data.type !== 'birdshot:refresh-index' ||
                      Object.keys(event.data).length !== 1 || parent.frames[1] !== window) return;
                  parent.frames[0].postMessage({type:'birdshot:refresh-index'}, location.origin);
                });
                </script>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.write_report(data)
                return
            if route == "/health.json":
                data = json.dumps({"instanceId": instance_id, "status": "ready"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.write_report(data)
                return
            if route == "/review.json":
                try:
                    path = job / "review.json"
                    payload = read_json(path) if path.exists() else read_snapshot(job, review_state)
                    data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
                except (OSError, ValueError, KeyError, TypeError):
                    self.send_error(503, "Review projection unavailable")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.write_report(data)
                return
            refresh_manifest()
            if route == "/species-reference":
                name = query.get("scientificName", [""])[0]
                try:
                    if set(query) != {"scientificName"} or len(query["scientificName"]) != 1:
                        raise ValueError("只接受一个候选学名")
                    result = reference_manager().get(name)
                    status = 502 if result["status"] == "error" else 200
                except ValueError as error:
                    result = {"status": "error", "scientificName": name[:100], "article": None, "image": None, "message": str(error)}
                    status = 400
                except (OSError, ReferenceError) as error:
                    print(f"Species reference failed: {error}", file=sys.stderr)
                    result = {"status": "error", "scientificName": name[:100], "article": None, "image": None,
                              "message": "本地参考资料缓存暂不可用"}
                    status = 502
                data = json.dumps(result, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "private, max-age=300" if result["status"] == "ready" else "no-store")
                if result["status"] == "error" and result.get("retryAfterSeconds"):
                    self.send_header("Retry-After", str(max(1, int(result["retryAfterSeconds"]))))
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    print("Species reference client disconnected", file=sys.stderr)
                return
            if route.startswith("/species-media/"):
                try:
                    path = species_references.media(route[len("/species-media/"):])
                    data = path.read_bytes()
                except (ValueError, FileNotFoundError):
                    self.send_error(404)
                    return
                except OSError as error:
                    print(f"Species image cache failed: {error}", file=sys.stderr)
                    self.send_error(500)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "private, max-age=604800, immutable")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    print("Species media client disconnected", file=sys.stderr)
                return
            if route == "/preview" or route.startswith("/preview/"):
                match = re.fullmatch(r"/preview/(p(?:\d{6}|[0-9a-f]{24}))/(480|960|1920|3840)\.jpg", route)
                if not match or query:
                    self.send_error(400, "Allowed preview edges: 480, 960, 1920, 3840")
                    return
                self.serve_image(match.group(1), int(match.group(2)))
                return
            if route.startswith("/original/"):
                match = re.fullmatch(r"/original/(p(?:\d{6}|[0-9a-f]{24}))", route)
                if not match or query not in ({}, {"download": ["1"]}):
                    self.send_error(400, "Invalid original image request")
                    return
                self.serve_image(match.group(1), download=bool(query))
                return
            path = None
            if route in {"/", "/index.html", "/review", "/review/", "/review-report.html"}:
                review_page = ROOT / "scripts" / "review-report.html"
                gallery_page = ROOT / "scripts" / "gallery-report.html"
                path = review_page if review_page.exists() else (
                    gallery_page if gallery_page.exists() else job / "report" / "index.html")
            elif route in {"/review.js", "/review.css", "/review/review.js", "/review/review.css"}:
                path = ROOT / "scripts" / route.rsplit("/", 1)[-1]
            elif route in {"/gallery-crops", "/gallery-crops/"}:
                gallery_page = ROOT / "scripts" / "gallery-report.html"
                path = gallery_page if gallery_page.exists() else job / "report" / "index.html"
            elif route in {"/legacy", "/legacy/"}:
                path = job / "report" / "index.html"
            elif route == "/gallery.json":
                path = gallery_revision / "gallery.json"
                payload = read_json(path) if path.exists() else {
                    "version": 1, "status": "processing", "progress": {"processed": 0, "total": len(read_json(job / "mapping.json")["crops"])},
                    "summary": {"preferred": 0, "bird": 0, "nonBird": 0, "unknown": 0, "oldSelected": 0,
                                "removedOld": 0, "addedNew": 0}, "items": [],
                    "notice": "候选尚未完成验鸟；不将旧候选当新优选"}
                payload = enrich_navigation(payload, manifest, capture_metadata(job))
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                use_gzip = "gzip" in self.headers.get("Accept-Encoding", "")
                if use_gzip:
                    data = gzip.compress(data, compresslevel=3, mtime=0)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                if use_gzip:
                    self.send_header("Content-Encoding", "gzip")
                    self.send_header("Vary", "Accept-Encoding")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.write_report(data)
                return
            elif re.fullmatch(r"/gallery/group/g(?:\d{5}|[0-9a-f]{24})\.json", route):
                group_name = route.rsplit("/", 1)[-1]
                if group_name[:-5] in {group["id"] for group in manifest["groups"]}:
                    path = gallery_revision / "groups" / group_name
            elif route in {"/status.json", "/groups.json", "/mapping.json", "/manifest.json", "/verification.json"}:
                path = job / route[1:]
            elif re.fullmatch(r"/group/g(?:\d{5}|[0-9a-f]{24})\.json", route):
                group_name = route.rsplit("/", 1)[-1]
                if group_name[:-5] in {group["id"] for group in manifest["groups"]}:
                    path = job / "groups" / group_name
            elif route.startswith("/media/"):
                refresh_registered_media()
                relative = route[len("/media/"):]
                candidate = (job / relative).resolve()
                if relative in registered_media:
                    candidate = registered_media[relative]
                    record = next(row for row in manifest["media"] if row["path"] == relative)
                    if not candidate.resolve().is_relative_to(Path(record["root"]).resolve()):
                        self.send_error(403)
                        return
                    if candidate.suffix.lower() in {".jpg", ".jpeg", ".png"}:
                        path = candidate
                elif relative in allowed_media and candidate.is_relative_to(job.resolve()) and candidate.suffix.lower() in {".jpg", ".jpeg", ".png"}:
                    path = candidate
            if path is None or not path.is_file():
                self.send_error(404)
                return
            use_gzip = path.suffix == ".json" and "gzip" in self.headers.get("Accept-Encoding", "")
            try:
                stat = path.stat()
                cache_key = (str(path), stat.st_mtime_ns, stat.st_size)
                data = compressed_cache.get(cache_key) if use_gzip else None
                if data is None:
                    data = path.read_bytes()
                    if use_gzip:
                        data = gzip.compress(data, compresslevel=3, mtime=0)
                        if len(compressed_cache) >= 16:
                            compressed_cache.clear()
                        compressed_cache[cache_key] = data
            except OSError:
                self.send_error(500)
                return
            self.send_response(200)
            self.send_header("Content-Type", (mimetypes.guess_type(path.name)[0] or "application/octet-stream") + ("; charset=utf-8" if path.suffix in {".json", ".html"} else ""))
            self.send_header("Content-Length", str(len(data)))
            if use_gzip:
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Vary", "Accept-Encoding")
            self.send_header("Cache-Control", "private, max-age=3600" if route.startswith("/media/") else "no-store")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as error:
                print(f"Report client disconnected: {type(error).__name__}", file=sys.stderr)
        def do_POST(self):
            if read_only or not self.allowed(write=True) or self.path != "/cancel":
                self.send_error(403)
                return
            (job / "cancel.request").write_text("requested", encoding="ascii")
            self.send_response(202)
            self.end_headers()
    class ReportServer(ThreadingHTTPServer):
        request_queue_size = 64
        daemon_threads = True
    addresses = ["0.0.0.0"] if allow_all else ["127.0.0.1"] + ([lan_ip] if lan_ip else [])
    servers = []
    try:
        for address in addresses:
            servers.append(ReportServer((address, port), Handler))
    except BaseException:
        for server in servers:
            server.server_close()
        raise
    if allow_all:
        print("REPORT_LISTEN 0.0.0.0; all source IPv4 clients may read registered private photos/originals; no authentication", flush=True)
    for address in ["127.0.0.1"] + ([lan_ip] if lan_ip else []):
        print(f"REPORT_READY http://{address}:{port}/", flush=True)
    for server in servers[1:]:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        servers[0].serve_forever()
    finally:
        for server in servers:
            server.server_close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["init", "run", "serve"])
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path, help="Explicit output job directory, outside the originals; does not write resource pointers")
    parser.add_argument("--job", type=Path)
    parser.add_argument("--node", type=Path, default=ROOT / ".tools" / "node-v24.11.1-win-x64" / "node.exe")
    parser.add_argument("--port", type=int, default=5174)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--lan-ip", help="Explicit physical-interface RFC1918 IPv4; default stays loopback-only")
    parser.add_argument("--lan-prefix", type=int, default=24)
    parser.add_argument("--allow-all", action="store_true", help="Opt-in all-source read access on 0.0.0.0; private originals become reachable; POST stays loopback")
    parser.add_argument("--allowed-host", action="append", default=[], help="Additional exact local hostname/IPv4 accepted in Host")
    parser.add_argument("--instance", help="Desktop controller instance identity, exposed by /health.json")
    parser.add_argument("--read-only", action="store_true", help="Disable the legacy loopback cancel endpoint")
    parser.add_argument("--review-state", type=Path, help="Read-only review state projection; no HTTP write endpoint")
    args = parser.parse_args()
    if args.operation == "init":
        initialize(args.source, args.output)
    elif args.operation == "run":
        job_path = args.job.resolve(strict=True)
        lock = job_path / "run.lock"
        try:
            with lock.open("x", encoding="ascii") as output:
                output.write(str(os.getpid()))
        except FileExistsError:
            raise RuntimeError("任务已有运行锁；确认旧进程已停止后才可移除该任务的 run.lock")
        try:
            run(job_path, args.node, args.threads)
        finally:
            lock.unlink(missing_ok=True)
    else:
        serve(args.job.resolve(strict=True), args.port, args.lan_ip, args.lan_prefix, args.allow_all, args.allowed_host,
              args.instance, args.read_only, args.review_state)
