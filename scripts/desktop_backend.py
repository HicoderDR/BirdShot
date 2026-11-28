# SPDX-License-Identifier: AGPL-3.0-only
"""Private stdin JSONL desktop library controller.

Run with --data USER_DATA --resources BACKEND_ROOT --node NODE --instance UUID.
Only JSONL responses/events go to stdout. Inference and gallery service output is
captured in USER_DATA/logs; no HTTP management endpoint is created.

Actions: state, retry_startup, add_source{path}, attach_job{path}, remove_source{id},
rescan{id}, configure_service{mode,port,lanIp?}, set_output{path}, shutdown.
Sources/configuration persist in desktop-library.json; jobs live beneath the
selected output root, while the registered aggregate and caches stay in userData.
Removing a source only detaches it, including from the HTTP media allowlist.
Missing bundle manifests are explicitly marked development-unverified; packaged
model hashes must match assets-manifest.json before any new inference is queued.
"""
import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path, PureWindowsPath
import signal
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
from urllib.request import ProxyHandler, build_opener
import uuid

from gallery_navigation import canonical, capture_metadata, enrich_navigation, stable_id
from review_store import ReviewStore


REVISION = Path("revisions") / "verified-birds-birder-v2"
MODEL_ID = "hieradet_d_small_dino-v2-inat21"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex[:12] + ".part")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        for attempt in range(10):
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(.05)
    finally:
        temporary.unlink(missing_ok=True)


def source_error(path):
    try:
        path = Path(path)
        if not path.is_dir():
            return "原片目录不存在或不可访问：" + str(path)
        with os.scandir(path) as entries:
            next(entries, None)
    except OSError as error:
        return "原片目录不可读取：" + str(error)
    return None


def read_job(path):
    job = Path(path).resolve(strict=True)
    if job.name == "manifest.json":
        job = job.parent
    manifest = read_json(job / "manifest.json")
    if manifest.get("storage") == "registered-multi-root":
        raise ValueError("请选择原始单来源批次，不要将派生聚合库作为批次加载")
    if not isinstance(manifest.get("source"), str) or not isinstance(manifest.get("photos"), list):
        raise ValueError("不是有效的 BirdShot 批次目录")
    for photo in manifest["photos"]:
        relative = Path(photo["primary"]["relative"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("批次包含越界原片路径")
    return job, manifest


def known_photos(sources):
    known = {}
    for source in sources:
        for record in source.get("jobs", []):
            path = Path(record["path"]) / "manifest.json"
            if not path.is_file():
                continue
            manifest = read_json(path)
            mapping_path = Path(record["path"]) / "mapping.json"
            fingerprints = {row["photoId"]: row.get("sourceSha256") for row in read_json(mapping_path).get("crops", [])} if mapping_path.exists() else {}
            for photo in manifest["photos"]:
                root = photo.get("sourceRoot") or manifest["source"]
                known[canonical(Path(root) / photo["primary"]["relative"])] = {
                    **photo["primary"], "sourceSha256": fingerprints.get(photo["id"])}
    return known


def history_tail_plans(source, additions):
    plans, claimed = {}, set()
    for record in reversed(source.get("jobs", [])):
        if record.get("managed"):
            continue
        job, manifest = read_job(record["path"])
        if canonical(manifest["source"]) != canonical(source["path"]):
            continue
        photos = {photo["id"]: photo for photo in manifest["photos"]}
        for group in reversed(manifest["groups"]):
            members = [photos[photo] for photo in group["photoIds"]]
            stamps = [photo.get("timestamp") for photo in members]
            if not stamps or any(stamp is None for stamp in stamps):
                continue
            tails = [photo["primary"]["relative"] for photo in additions
                     if photo["primary"]["relative"] not in claimed and photo.get("timestamp") is not None
                     and 0 <= photo["timestamp"] - max(stamps) <= 2000
                     and photo["timestamp"] - min(stamps) <= 10000]
            if tails:
                key = stable_id("history-tail-", [canonical(job), group["id"]])
                plans[key] = {"id": key, "job": str(job), "originalGroupId": group["id"],
                              "newPhotos": tails, "status": "pending"}
                claimed.update(tails)
    return list(plans.values())


def seed_history_tails(job, plans, session):
    """Copy only affected cached groups; historical jobs are never written."""
    from batch_culling import digest
    manifest = read_json(job / "manifest.json")
    seeded = {tail["id"] for tail in manifest.get("historyTails", [])}
    groups = read_json(job / "groups.json")
    mapping = read_json(job / "mapping.json")
    capture_path = job / "revisions" / "capture-exif-v1.json"
    captures = read_json(capture_path) if capture_path.exists() else {"photos": {}, "statistics": {}}
    tails = list(manifest.get("historyTails", []))
    cached_crops = {row["crop"] for row in session.result["rows"]}
    for plan in plans:
        if plan["id"] in seeded:
            continue
        original, old_manifest = read_job(plan["job"])
        if canonical(old_manifest["source"]) != canonical(manifest["source"]):
            raise RuntimeError("history-tail 原片根目录不一致，拒绝混入缓存")
        saved = read_json(original / "groups" / (plan["originalGroupId"] + ".json"))
        old_photos = {photo["id"]: photo for photo in old_manifest["photos"]}
        verified_path = original / REVISION / "verification.json"
        verified = read_json(verified_path) if verified_path.exists() else {"rows": []}
        evidence = {row["crop"]: row for row in verified["rows"]}
        detector_ok = saved.get("versions") == manifest["versions"] == old_manifest.get("versions")
        classifier_ok = verified.get("fingerprint") == session.fingerprint and all(
            evidence.get(row["crop"], {}).get("status") == "completed"
            and evidence[row["crop"]].get("cropSha256") == row["cropSha256"]
            and evidence[row["crop"]].get("scoreType") == "birder-fullclass"
            and evidence[row["crop"]].get("classCount") == 10000 for row in saved["cropMapping"])
        def unchanged(frame):
            try:
                return bool(frame.get("sourceSha256")) and frame.get("processingStatus") == "ok" and digest(
                    Path(manifest["source"]) / old_photos[frame["id"]]["primary"]["relative"]) == frame["sourceSha256"]
            except OSError:
                return False
        source_ok = all(unchanged(frame) for frame in saved["frames"])
        compatible = detector_ok and classifier_ok and source_ok and not saved.get("errors")
        reasons = ([] if detector_ok else ["历史检测/技术指纹不兼容"]) + (
            [] if classifier_ok else ["历史Birder/中文名策略指纹不兼容"]) + (
            [] if source_ok else ["历史原片指纹或处理状态不可确认"])
        index = max((group["index"] for group in groups), default=0) + 1
        new_group = {**deepcopy(saved["group"]), "id": f"g{index:05d}", "index": index, "photoIds": [],
                     "status": "done", "thumbnail": None}
        tail = {**plan, "groupId": new_group["id"], "cachedPhotos": len(saved["frames"]),
                "status": "compatible" if compatible else "pending",
                "reason": "兼容缓存，复用历史像素与分类；仅检测新增照片" if compatible else "history-tail 待确认：" + "；".join(reasons),
                "compatibility": {"detector": detector_ok, "classifier": classifier_ok, "source": source_ok}}
        new_group["historyTail"] = tail
        frames, records = [], []
        old_capture = capture_metadata(original).get("photos", {})
        def old_media(relative):
            path = (original / relative).resolve(strict=True)
            if not path.is_relative_to(original):
                raise RuntimeError("history-tail 缓存媒体路径越界")
            return path
        for old_frame in saved["frames"]:
            photo = deepcopy(old_photos[old_frame["id"]])
            photo_id = f"p{max((int(row['id'][1:]) for row in manifest['photos']), default=0) + 1:06d}"
            origin = {"job": str(original), "groupId": plan["originalGroupId"], "photoId": old_frame["id"],
                      "compatible": compatible, "versions": saved.get("versions")}
            photo.update(id=photo_id, historicalOrigin=origin)
            manifest["photos"].append(photo)
            new_group["photoIds"].append(photo_id)
            frame = deepcopy(old_frame)
            frame.update(id=photo_id, group=new_group["id"], source=photo, historicalOrigin=origin)
            frame.pop("selectedCrop", None)
            if not compatible:
                frame.update(decision="review", rank=None, relative=None, cohortSize=0)
            for bird in frame["instances"]:
                record = deepcopy(next(row for row in saved["cropMapping"] if row["photoId"] == old_frame["id"]
                                       and row["instanceId"] == bird["id"]))
                old_crop = record["crop"]
                if digest(old_media(old_crop)) != record["cropSha256"]:
                    raise RuntimeError("history-tail 缓存裁剪SHA不一致，不能安全复用")
                relative = f"crops/{new_group['id']}/{photo_id}_b{bird['id']:02d}.jpg"
                target = job / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(old_media(old_crop), target)
                if digest(target) != record["cropSha256"]:
                    raise RuntimeError("history-tail 缓存裁剪复制SHA校验失败")
                thumb = relative[:-4] + "_thumb.jpg"
                old_thumb = original / bird.get("thumbnail", old_crop[:-4] + "_thumb.jpg")
                if old_thumb.exists():
                    shutil.copyfile(old_media(old_thumb.relative_to(original).as_posix()), job / thumb)
                else:
                    from PIL import Image
                    with Image.open(target) as image:
                        image.thumbnail((420, 420))
                        image.convert("RGB").save(job / thumb, quality=85)
                provenance = {**origin, "crop": old_crop, "selected": bool(record.get("selected"))}
                record.update(crop=relative, groupId=new_group["id"], photoId=photo_id, selected=False,
                              historicalOrigin=provenance)
                record.pop("selectedPath", None)
                bird.update(crop=relative, thumbnail=thumb, historicalOrigin=provenance)
                records.append(record)
                if compatible and relative not in cached_crops:
                    session.result["rows"].append({**deepcopy(evidence[old_crop]), "crop": relative,
                                                   "groupId": new_group["id"], "photoId": photo_id})
                    cached_crops.add(relative)
            if old_frame["id"] in old_capture:
                captures["photos"][photo_id] = deepcopy(old_capture[old_frame["id"]])
            frames.append(frame)
        new_group["count"] = len(frames)
        atomic_json(job / "groups" / (new_group["id"] + ".json"), {
            "group": new_group, "frames": frames, "cropMapping": records, "selectedCrops": [], "errors": [],
            "versions": manifest["versions"], "historicalVersions": saved.get("versions")})
        groups.append(new_group)
        mapping["crops"].extend(records)
        tails.append(tail)
    manifest.update(groups=groups, historyTails=tails, physicalPhotos=len(manifest["photos"]))
    captures["statistics"] = {"photos": len(captures["photos"])}
    atomic_json(capture_path, captures)
    session.save()
    atomic_json(job / "mapping.json", mapping)
    atomic_json(job / "groups.json", groups)
    atomic_json(job / "manifest.json", manifest)
    return tails


def finish_extension(job, transaction):
    if transaction.get("formatVersion") != 1:
        raise RuntimeError("不支持的增量清点checkpoint格式；未覆盖已有结果")
    atomic_json(job / "manifest.json", transaction["manifest"])
    atomic_json(job / "groups.json", transaction["groups"])
    if transaction.get("checkpoint"):
        atomic_json(job / REVISION / "pipeline.json", transaction["checkpoint"])
    atomic_json(job / "status.json", transaction["status"])
    (job / REVISION / "pending-extension.json").unlink(missing_ok=True)


def extend_job(job, incoming):
    """Append stable photos to a managed job; reuse pixels for affected tail bursts."""
    manifest = read_json(job / "manifest.json")
    by_relative = {photo["primary"]["relative"]: photo for photo in manifest["photos"]}
    added = [deepcopy(photo) for photo in incoming if photo["primary"]["relative"] not in by_relative]
    if not added:
        return []
    checkpoint_path = job / REVISION / "pipeline.json"
    checkpoint = read_json(checkpoint_path) if checkpoint_path.exists() else None
    revision = (checkpoint or {}).get("publishedRevision", 0)
    history = job / REVISION / "history" / str(revision)
    for path in [job / "manifest.json", job / "groups.json", job / REVISION / "gallery.json"]:
        if path.exists():
            atomic_json(history / path.name, read_json(path))
    groups = read_json(job / "groups.json")
    affected = set()
    for photo in sorted(added, key=lambda row: (row["timestamp"] is None, row["timestamp"] or 0, row["primary"]["relative"])):
        photo["id"] = f"p{max((int(row['id'][1:]) for row in manifest['photos']), default=0) + 1:06d}"
        target = None
        if photo["timestamp"] is not None:
            for group in reversed(groups):
                members = [row for row in manifest["photos"] if row["id"] in group["photoIds"]]
                stamps = [row["timestamp"] for row in members if row["timestamp"] is not None]
                if len(stamps) == len(members) and stamps and 0 <= photo["timestamp"] - max(stamps) <= 2000 \
                        and photo["timestamp"] - min(stamps) <= 10000:
                    unchanged = True
                    for member in members:
                        try:
                            stat = (Path(manifest["source"]) / member["primary"]["relative"]).stat()
                            unchanged = unchanged and (stat.st_size, stat.st_mtime_ns) == (
                                member["primary"]["size"], member["primary"]["mtimeNs"])
                        except OSError:
                            unchanged = False
                    if not unchanged:
                        continue
                    target = group
                    break
        if target is None:
            index = max((group["index"] for group in groups), default=0) + 1
            target = {"id": f"g{index:05d}", "index": index, "photoIds": [], "count": 0,
                      "capturedAt": photo["capturedAt"], "grouping": incoming[0].get("grouping") or
                      ("EXIF原始拍摄时间+子秒，相邻≤2秒，总长≤10秒" if photo["timestamp"] is not None else "无可信拍摄时间，独立单张"),
                      "thumbnail": None, "status": "pending"}
            groups.append(target)
        path = job / "groups" / (target["id"] + ".json")
        if path.exists() and target["id"] not in affected:
            saved = read_json(path)
            atomic_json(history / "groups" / path.name, saved)
            verified_path = job / REVISION / "groups" / path.name
            if verified_path.exists():
                atomic_json(history / "verified-groups" / path.name, read_json(verified_path))
            saved["phase"] = "extending"
            atomic_json(path, saved)
        target["photoIds"].append(photo["id"])
        target.update(count=len(target["photoIds"]), status="pending")
        manifest["photos"].append(photo)
        affected.add(target["id"])
    manifest.update(groups=groups, physicalPhotos=sum(1 + len(photo["companions"]) for photo in manifest["photos"]))
    if checkpoint:
        checkpoint["completedGroups"] = [group for group in checkpoint["completedGroups"] if group not in affected]
    status = read_json(job / "status.json")
    status.update(status="pending", totalPhotos=len(manifest["photos"]), totalGroups=len(groups))
    transaction = {"formatVersion": 1, "manifest": manifest, "groups": groups, "checkpoint": checkpoint, "status": status}
    atomic_json(job / REVISION / "pending-extension.json", transaction)
    finish_extension(job, transaction)
    return sorted(affected)


def aggregate_library(root, sources, progress=None, strict=False):
    """Derive a registered multi-root view; never copy media or write old jobs."""
    root = Path(root)
    photos, items, details, media, crop_mapping, shoot_groups = [], [], [], {}, [], []
    seen = set()
    processed_ids, failed_ids = set(), set()
    errors = []
    report = progress or (lambda *args: None)
    total = sum(len(source.get("jobs", [])) for source in sources)
    processed = 0
    report("library", 0, total)
    gallery = {"version": 1, "analysisVersion": "desktop-library-v1", "revision": "desktop-library-v1",
               "status": "completed", "updatedAt": datetime.now().isoformat(),
               "items": items, "frames": [], "shootGroups": shoot_groups, "sourceErrors": errors}
    gallery["sourceIssues"] = [{"sourceId": source["id"], **issue} for source in sources for issue in source.get("sourceIssues", [])]
    for source in sources:
        for job_record in sorted(source.get("jobs", []), key=lambda row: not row.get("overlay", False)):
            report("library", processed, total)
            job = Path(job_record["path"])
            if not (job / "manifest.json").is_file():
                if source.get("status") not in {"queued", "processing"}:
                    errors.append({"sourceId": source["id"], "job": str(job), "error": "批次清单尚不可用"})
                processed += 1
                continue
            try:
                _, manifest = read_job(job)
                metadata = capture_metadata(job)
                old_gallery_path = job / REVISION / "gallery.json"
                old_gallery = read_json(old_gallery_path) if old_gallery_path.is_file() else {"items": []}
                old_mapping = read_json(job / "mapping.json") if (job / "mapping.json").is_file() else {"crops": []}
                saved_progress = old_gallery.get("progress") or {}
                saved_items = old_gallery.get("items", [])
                verification_complete = old_gallery.get("status") == "completed" and (
                    not saved_progress or saved_progress.get("total") == len(saved_items)
                    and isinstance(saved_progress.get("processed"), (int, float))
                    and saved_progress["processed"] >= len(saved_items))
                job_key = stable_id("", canonical(job))
                job_root = str(job.resolve())
                frame_ids, group_ids = {}, {}
                def group_id(old_id):
                    return group_ids.setdefault(old_id, stable_id("g", [job_key, old_id]))
                def register_media(relative):
                    if not relative:
                        return relative
                    relative_path, windows = Path(relative), PureWindowsPath(relative)
                    if relative_path.is_absolute() or windows.anchor or ".." in relative_path.parts or ".." in windows.parts:
                        raise ValueError("批次媒体包含越界路径")
                    relative = relative_path.as_posix()
                    alias = f"jobs/{job_key}/{relative}"
                    media[alias] = {"path": alias, "root": job_root, "relative": relative}
                    return alias
                for photo in manifest["photos"]:
                    original_root = photo.get("sourceRoot") or manifest["source"]
                    identity = canonical(Path(original_root) / photo["primary"]["relative"])
                    if identity in seen:
                        continue
                    seen.add(identity)
                    new_id = stable_id("p", identity)
                    frame_ids[photo["id"]] = new_id
                    capture = metadata.get("photos", {}).get(photo["id"], {}).get("capture") or photo.get("capture") or next(
                        (item["capture"] for item in old_gallery.get("items", [])
                         if item.get("frameId") == photo["id"] and item.get("capture")), {})
                    updated = deepcopy(photo)
                    if photo["primary"]["relative"] in source.get("associations", {}):
                        updated["companions"] = deepcopy(source["associations"][photo["primary"]["relative"]])
                    if any(issue["path"] == photo["primary"]["relative"] for issue in source.get("sourceIssues", [])):
                        updated["sourceIssue"] = True
                    updated.update(id=new_id, originalPhotoId=photo["id"], sourceId=source["id"],
                                   sourceRoot=original_root, sourceName=source["name"], capture=capture)
                    photos.append(updated)
                    gallery["frames"].append({"id": new_id, "originalFrameId": photo["id"], "sourceId": source["id"],
                                              "sourceLabel": photo["primary"]["relative"], "capture": capture,
                                              "supported": photo.get("supported", False), "instances": []})
                for original in old_gallery.get("items", []):
                    old_frame = original.get("frameId")
                    if old_frame not in frame_ids:
                        continue
                    item = deepcopy(original)
                    item.update(id=frame_ids[old_frame] + "_b" + str(item["instanceId"]),
                                frameId=frame_ids[old_frame], groupId=group_id(original["groupId"]),
                                sourceId=source["id"], originalItemId=original["id"],
                                originalFrameId=old_frame, originalGroupId=original["groupId"])
                    for field in ("crop", "thumbnail", "selectedPath"):
                        if item.get(field):
                            item[field] = register_media(item[field])
                    items.append(item)
                    evidence_status = (item.get("birdVerification", {}).get("evidence") or {}).get("status")
                    if evidence_status in {"completed", "error"} or evidence_status is None and verification_complete:
                        processed_ids.add(item["id"])
                    if evidence_status == "error":
                        failed_ids.add(item["id"])
                indexed_crops = {item["crop"] for item in items}
                for original in old_mapping.get("crops", []):
                    if original["photoId"] not in frame_ids:
                        continue
                    record = deepcopy(original)
                    if original.get("sourceRelative") in source.get("associations", {}):
                        record["rawCompanions"] = [row["relative"] for row in source["associations"][original["sourceRelative"]]]
                    record.update(photoId=frame_ids[original["photoId"]], groupId=group_id(original["groupId"]),
                                  originalPhotoId=original["photoId"], originalGroupId=original["groupId"], sourceId=source["id"])
                    record["crop"] = register_media(original["crop"])
                    register_media(original.get("thumbnail") or original["crop"][:-4] + "_thumb.jpg")
                    if original.get("selectedPath"):
                        record["selectedPath"] = register_media(original["selectedPath"])
                    crop_mapping.append(record)
                    if record["crop"] not in indexed_crops:
                        photo = next(photo for photo in photos if photo["id"] == record["photoId"])
                        items.append({"id": record["photoId"] + "_b" + str(record["instanceId"]),
                                      "frameId": record["photoId"], "groupId": record["groupId"],
                                      "instanceId": record["instanceId"], "sourceId": source["id"],
                                      "originalFrameId": original["photoId"], "originalGroupId": original["groupId"],
                                      "originalItemId": original["photoId"] + "_b" + str(record["instanceId"]),
                                      "crop": record["crop"], "thumbnail": register_media(original["crop"][:-4] + "_thumb.jpg"),
                                      "sourceLabel": record["sourceRelative"], "capture": photo["capture"],
                                      "decision": "review", "rank": None, "preferredRank": None,
                                      "processingStatus": "pending",
                                      "selectedBefore": bool(record.get("selected")), "species": {"top5": [], "top3": []},
                                      "birdVerification": {"status": "unknown", "evidence": {"status": "pending"},
                                                           "reason": "验鸟证据尚未完成；裁剪可审核，不作为优选"}})
                group_dir = job / REVISION / "groups"
                if not group_dir.is_dir():
                    group_dir = job / "groups"
                group_files = sorted(group_dir.glob("g*.json"))
                for index, group_file in enumerate(group_files):
                    report("library-groups", index, len(group_files))
                    original = read_json(group_file)
                    frames = [frame for frame in original.get("frames", []) if frame["id"] in frame_ids]
                    if not frames:
                        continue
                    detail = deepcopy(original)
                    old_id = original["group"]["id"]
                    new_id = group_id(old_id)
                    detail["group"]["id"] = new_id
                    detail["group"]["originalId"] = old_id
                    detail["group"]["photoIds"] = [frame_ids[frame["id"]] for frame in frames]
                    detail["group"]["count"] = len(frames)
                    if detail["group"].get("thumbnail"):
                        detail["group"]["thumbnail"] = register_media(detail["group"]["thumbnail"])
                    detail["frames"] = []
                    for original_frame in frames:
                        frame = deepcopy(original_frame)
                        frame.update(id=frame_ids[original_frame["id"]], originalFrameId=original_frame["id"],
                                     group=new_id, originalGroupId=old_id, sourceId=source["id"])
                        for field in ("selectedCrop",):
                            if frame.get(field):
                                frame[field] = register_media(frame[field])
                        for bird in frame.get("instances", []):
                            for field in ("crop", "thumbnail"):
                                if bird.get(field):
                                    bird[field] = register_media(bird[field])
                        detail["frames"].append(frame)
                    detail["cropMapping"] = [record for record in crop_mapping if record["groupId"] == new_id]
                    detail["selectedCrops"] = [register_media(path) for path in original.get("selectedCrops", [])]
                    details.append(detail)
                indexed_groups = {detail["group"]["id"]: detail for detail in details}
                for original_group in manifest.get("groups", []):
                    new_id = group_id(original_group["id"])
                    member_ids = [frame_ids[photo_id] for photo_id in original_group.get("photoIds", [])
                                  if photo_id in frame_ids]
                    if not member_ids:
                        continue
                    existing = indexed_groups.get(new_id)
                    group = {**deepcopy(original_group), "id": new_id, "originalId": original_group["id"],
                             "photoIds": member_ids, "count": len(member_ids), "thumbnail": None}
                    frames = existing["frames"] if existing else []
                    existing_ids = {frame["id"] for frame in frames}
                    for frame_id in member_ids:
                        if frame_id in existing_ids:
                            continue
                        frame = deepcopy(next(row for row in gallery["frames"] if row["id"] == frame_id))
                        photo = next(row for row in photos if row["id"] == frame_id)
                        width, height = photo.get("encodedSize") or [None, None]
                        if photo.get("exifOrientation") in {5, 6, 7, 8}:
                            width, height = height, width
                        frame.update(group=new_id, originalGroupId=original_group["id"], width=width, height=height)
                        frames.append(frame)
                    if existing:
                        existing["group"].update(photoIds=member_ids, count=len(member_ids))
                    else:
                        details.append({"group": group, "frames": frames, "cropMapping": [], "selectedCrops": []})
                shoot_groups.extend(deepcopy(old_gallery.get("shootGroups", [])))
                if old_gallery.get("model"):
                    gallery.setdefault("model", old_gallery["model"])
                if old_gallery.get("policy"):
                    gallery.setdefault("policy", old_gallery["policy"])
            except (OSError, ValueError, KeyError, TypeError) as error:
                errors.append({"sourceId": source["id"], "job": str(job), "error": str(error)})
            processed += 1
            report("library", processed, total)
    if strict and errors:
        raise RuntimeError("; ".join(row["error"] for row in errors))
    manifest = {"version": 1, "source": str(root), "sourceRoots": sorted({photo["sourceRoot"] for photo in photos}),
                "photos": photos, "groups": [detail["group"] for detail in details],
                "mediaRoots": sorted({record["root"] for record in media.values()}), "media": list(media.values()),
                "storage": "registered-multi-root", "output": str(root), "physicalPhotos": len(photos)}
    for item in items:
        for field in ("rank", "preferredRank"):
            if item.get(field) is not None:
                item["original" + field[0].upper() + field[1:]] = item[field]
    preferred = [item for item in items if item.get("decision") == "keep"
                 and item.get("birdVerification", {}).get("status") == "bird"]
    preferred.sort(key=lambda item: (
        item.get("quality", {}).get("tier", 99),
        item.get("quality", {}).get("groupPresentationRound", 999),
        item.get("quality", {}).get("withinGroupRank") or 999,
        -(item.get("quality", {}).get("withinGroupRelative") or 0), item["id"]))
    for rank, item in enumerate(preferred, 1):
        item["rank"] = item["preferredRank"] = rank
    counts = {status: sum(item.get("birdVerification", {}).get("status") == status for item in items)
              for status in ("bird", "nonBird", "unknown")}
    processed_unknown = sum(item["id"] in processed_ids and item.get("birdVerification", {}).get("status") == "unknown"
                            for item in items)
    pending = len(items) - len(processed_ids)
    gallery["summary"] = {**counts, "unknown": processed_unknown, "processedUnknown": processed_unknown,
                          "preferred": len(preferred), "pending": pending, "failed": len(failed_ids),
                          "oldSelected": sum(bool(item.get("selectedBefore")) for item in items),
                          "removedOld": sum(bool(item.get("selectedBefore")) and item not in preferred for item in items),
                          "addedNew": sum(not item.get("selectedBefore") for item in preferred)}
    if errors or any(source.get("status") == "error" for source in sources):
        gallery["status"] = "error"
    elif pending or any(source.get("status") in {"queued", "processing"} for source in sources):
        gallery["status"] = "processing"
    gallery["progress"] = {"processed": len(processed_ids), "total": len(items),
                           "processedUnknown": processed_unknown, "pending": pending}
    gallery["originalPhotoCount"] = len(photos)
    gallery["groups"] = details
    gallery = enrich_navigation(gallery, manifest)
    published_groups = gallery.pop("groups")
    for index, detail in enumerate(published_groups):
        report("library-publish", index, len(published_groups))
        atomic_json(root / REVISION / "groups" / (detail["group"]["id"] + ".json"), detail)
        atomic_json(root / "groups" / (detail["group"]["id"] + ".json"), detail)
    report("library-publish", len(published_groups), len(published_groups))
    atomic_json(root / "mapping.json", {"sourceRoots": manifest["sourceRoots"], "crops": crop_mapping})
    atomic_json(root / "groups.json", manifest["groups"])
    atomic_json(root / "status.json", {"status": gallery["status"], "totalPhotos": len(photos), "cropCount": len(items)})
    atomic_json(root / "manifest.json", manifest)
    atomic_json(root / REVISION / "gallery.json", gallery)
    return gallery


def run_worker(spec, observer=None):
    """Subprocess-only entry: every write is confined to this managed job."""
    resources, job = Path(spec["resources"]), Path(spec["job"])
    sys.path.insert(0, str(resources / "scripts"))
    os.environ["BIRDSHOT_MODEL_CACHE"] = str(resources / "models")
    from batch_culling import initialize, run
    from capture_metadata import extract
    from gallery_build import build, DECISION_VERSION, POLICY_VERSION, THRESHOLDS
    result_path = job.parent / (job.name + ".worker.json")
    progress = {"stage": "scan", "phase": "清点照片目录，形成稳定连拍组", "currentGroup": None,
                "totalPhotos": 0, "processedPhotos": 0, "totalGroups": 0, "completedGroups": 0,
                "cropCount": 0, "publishedRevision": 0, "stages": [
                    {"id": name, "label": label, "status": "pending", "processed": 0, "total": 0, "unit": unit}
                    for name, label, unit in (
                        ("scan", "清点与稳定分组", "photos"), ("models", "加载本地模型", "groups"),
                        ("detect", "检测 · 分割 · 姿态 · 原像素裁剪", "photos"),
                        ("metadata", "读取拍摄元数据", "photos"), ("classify", "Birder全10000类验鸟", "crops"),
                        ("select", "同组技术比较与严格优选", "groups"), ("publish", "发布可审核图库", "groups"))]}
    def stage(name, phase, processed=None, total=None, done=False, event=None):
        progress.update(stage=name, phase=phase)
        row = next(row for row in progress["stages"] if row["id"] == name)
        if processed is not None:
            row["processed"] = processed
        if total is not None:
            row["total"] = total
        row["status"] = "completed" if done else "partial" if row["processed"] else "running"
        atomic_json(result_path, {"status": "processing", "stage": name, "job": str(job), "progress": progress})
        if observer and event:
            observer(event, deepcopy(progress))
    try:
        saved_checkpoint_path = job / REVISION / "pipeline.json"
        if saved_checkpoint_path.exists() and read_json(saved_checkpoint_path).get("formatVersion") != 1:
            raise RuntimeError("不支持的流水checkpoint格式；未覆盖已有结果")
        extension_path = job / REVISION / "pending-extension.json"
        if extension_path.exists():
            finish_extension(job, read_json(extension_path))
        stage("scan", progress["phase"])
        new_photos = spec.get("additionalPhotos") or (
            (spec.get("inventory") or {}).get("photos", []) if spec.get("historyTails") else [])
        if not (job / "manifest.json").exists():
            if job.exists():
                # An interrupted initialization has no committed inference to preserve.
                for name in ("groups", "crops", "selected", "report"):
                    folder = job / name
                    if folder.exists() and any(folder.iterdir()):
                        raise RuntimeError("未提交清单的批次包含文件，拒绝覆盖：" + str(job))
                    if folder.is_dir():
                        folder.rmdir()
                job.rmdir()
            snapshot = spec.get("inventory")
            if spec.get("historyTails"):
                if snapshot is None:
                    from batch_culling import inventory
                    snapshot = inventory(Path(spec["source"]), spec.get("excludePaths", []))
                new_photos = snapshot["photos"]
                snapshot = {**snapshot, "photos": [], "groups": [], "physicalPhotos": 0}
            initialize(Path(spec["source"]), job, spec.get("excludePaths", []), snapshot=snapshot)
        manifest = read_json(job / "manifest.json")
        progress.update(totalPhotos=len(manifest["photos"]), totalGroups=len(manifest["groups"]))
        stage("scan", "清点完成，按真实EXIF连拍边界逐组处理", progress["totalPhotos"], progress["totalPhotos"], done=True)
        taxonomy = resources / "assets" / "taxonomy"
        stage("models", "准备本地模型与持久分类策略（尚未加载则无百分比）", 0, 2)
        from gallery_birder import ClassifierSession
        session = ClassifierSession(job, job / REVISION, taxonomy, 4)
        if spec.get("historyTails"):
            seed_history_tails(job, spec["historyTails"], session)
        if new_photos:
            extend_job(job, new_photos)
        manifest = read_json(job / "manifest.json")
        progress.update(totalPhotos=len(manifest["photos"]), totalGroups=len(manifest["groups"]))
        stage("scan", "清点完成；历史尾组只复用缓存，新照片逐组处理", progress["totalPhotos"], progress["totalPhotos"], done=True)
        fingerprint = hashlib.sha256(json.dumps({
            "detector": manifest["versions"], "classifier": session.fingerprint, "decision": DECISION_VERSION,
            "gatePolicy": POLICY_VERSION, "gateThresholds": THRESHOLDS,
            "ranking": hashlib.sha256((resources / "scripts" / "gallery_rank.mjs").read_bytes()).hexdigest(),
            "builder": hashlib.sha256((resources / "scripts" / "gallery_build.py").read_bytes()).hexdigest(),
        }, sort_keys=True).encode()).hexdigest()
        checkpoint_path = job / REVISION / "pipeline.json"
        checkpoint = read_json(checkpoint_path) if checkpoint_path.exists() else {
            "formatVersion": 1, "fingerprint": fingerprint, "completedGroups": [], "publishedRevision": 0}
        if checkpoint["fingerprint"] != fingerprint:
            raise RuntimeError("增量图库策略指纹改变，拒绝混用已完成组")
        completed = set(checkpoint["completedGroups"])
        progress.update(completedGroups=len(completed), publishedRevision=checkpoint["publishedRevision"])
        loaded = set()
        def counts():
            groups = [read_json(path) for path in (job / "groups").glob("g*.json")]
            failures = [error for group in groups for error in group.get("errors", [])]
            progress["processedPhotos"] = sum(len(group["frames"]) for group in groups)
            progress["cropCount"] = len(read_json(job / "mapping.json")["crops"])
            detect = next(row for row in progress["stages"] if row["id"] == "detect")
            detect.update(processed=progress["processedPhotos"], total=progress["totalPhotos"],
                          status="error" if failures else "completed" if progress["processedPhotos"] == progress["totalPhotos"] else
                          "partial" if progress["processedPhotos"] else "pending")
            if failures:
                detect["error"] = "检测/裁剪失败：" + json.dumps(failures, ensure_ascii=False)
        def publish(group, final):
            counts()
            stage("publish", "发布本组最终图库" if final else "裁剪已提交，可立即待审；验鸟与相对名次尚未完成",
                  len(completed), progress["totalGroups"])
            build(job, job / REVISION, Path(spec["node"]), taxonomy, finalized_groups=completed, status="running")
            progress["publishedRevision"] += 1
            checkpoint.update(completedGroups=sorted(completed), publishedRevision=progress["publishedRevision"])
            atomic_json(checkpoint_path, checkpoint)
            progress["completedGroups"] = len(completed)
            stage("publish", "本组最终结果已发布" if final else "本组真实裁剪已发布至待审",
                  len(completed), progress["totalGroups"], event="final_published" if final else "pending_published")
        def detected(group, result, state):
            progress["currentGroup"] = group["id"]
            publish(group, False)
        def group_ready(group, result, state):
            if group["id"] in completed:
                return
            progress["currentGroup"] = group["id"]
            counts()
            # Recovered detection commits also become readable before classification.
            if result.get("phase") == "detected":
                publish(group, False)
            stage("metadata", "读取本组原片必要EXIF元数据", total=progress["totalPhotos"])
            stats = extract(job, group["photoIds"], include_region=False)
            stage("metadata", "本组拍摄元数据已提交", stats.get("photos", 0), progress["totalPhotos"])
            rows = [row for row in read_json(job / "mapping.json")["crops"] if row["groupId"] == group["id"]]
            rows = [row for row in rows if not row.get("historicalOrigin") or row["historicalOrigin"]["compatible"]]
            def classification_event(event, result):
                if event == "loading":
                    stage("models", "载入Birder全10000类CPU模型", len(loaded), 2)
                else:
                    loaded.add("birder")
                    model_stage = next(row for row in progress["stages"] if row["id"] == "models")
                    model_stage.update(processed=len(loaded), status="completed" if len(loaded) == 2 else "partial")
                    stage("classify", "本组Birder全类竞争证据已持久化", result["processed"], progress["cropCount"])
            stage("classify", "本组Birder全10000类验鸟与鸟种识别", session.result["processed"], progress["cropCount"])
            verified = session.classify(rows, total=progress["cropCount"], on_progress=classification_event)
            failures = result.get("errors", []) + [row for row in verified if row["status"] == "error"]
            stage("select", "复用同主体匹配与技术策略，完整本组后才形成相对名次", len(completed), progress["totalGroups"])
            if failures:
                publish(group, False)
                raise RuntimeError("连拍组处理失败，已完成图库保留：" + json.dumps(failures, ensure_ascii=False))
            completed.add(group["id"])
            publish(group, True)
            stage("select", "本组验鸟与严格技术优选已完成", len(completed), progress["totalGroups"])
        def detection_event(event, message, group, state):
            progress["currentGroup"] = group["id"]
            if event == "models":
                stage("models", message, len(loaded), 2)
            elif event == "models_ready":
                loaded.add("detector")
                stage("models", message, len(loaded), 2, done=len(loaded) == 2)
            else:
                counts()
                stage("detect", message, progress["processedPhotos"], progress["totalPhotos"],
                      event="before_group" if event == "before_group" else None)
        run(job, Path(spec["node"]), on_detected=detected, on_group=group_ready,
            on_progress=detection_event, skip_groups=completed)
        counts()
        session.classify([], total=progress["cropCount"], final=True)
        build(job, job / REVISION, Path(spec["node"]), taxonomy, finalized_groups=completed, status="completed")
        gallery = read_json(job / REVISION / "gallery.json")
        pending_tails = [tail for tail in gallery.get("historyTails", []) if tail["status"] == "pending"]
        progress["currentGroup"] = None
        for row in progress["stages"]:
            row["status"] = "completed"
            if row["id"] == "models":
                row.update(processed=len(loaded), total=len(loaded))
                if not loaded:
                    row["label"] = "复用持久缓存，本轮无需加载模型"
            else:
                row["processed"] = row["total"]
            if row["id"] == "classify" and session.result["processed"] < progress["cropCount"]:
                row.update(processed=session.result["processed"], total=progress["cropCount"], status="partial")
            if row["id"] == "select" and pending_tails:
                row.update(processed=len(completed) - len(pending_tails), status="partial")
        progress["publishedRevision"] += 1
        checkpoint.update(publishedRevision=progress["publishedRevision"])
        atomic_json(checkpoint_path, checkpoint)
        progress.update(stage="publish", phase="所有稳定组已完成；已处理未知与尚未处理分开统计")
        if pending_tails:
            progress["phase"] = "新增照片已处理；history-tail 待确认，请打开该组裁剪查看新旧鸟体对比"
        atomic_json(result_path, {"status": "completed", "stage": "publish", "job": str(job), "progress": progress,
                                 "historyTails": gallery.get("historyTails", [])})
    except BaseException as error:
        row = next(row for row in progress["stages"] if row["id"] == progress["stage"])
        row.update(status="error", error=str(error))
        atomic_json(result_path, {"status": "error", "stage": progress["stage"], "error": str(error),
                                 "job": str(job), "progress": progress})
        raise


class ProcessOwner:
    """Own only launched children; Windows jobs also clean up on controller crash."""
    def __init__(self, include_current=False):
        self.job = None
        self.owns_current = False
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            class Basic(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
            class IO(ctypes.Structure):
                _fields_ = [(name, ctypes.c_ulonglong) for name in
                            ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                             "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
            class Extended(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
            self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            self.kernel.GetCurrentProcess.restype = wintypes.HANDLE
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.job = self.kernel.CreateJobObjectW(None, None)
            limits = Extended()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self.job or not self.kernel.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                self.close()
                raise OSError(ctypes.get_last_error(), "Cannot establish private child-process ownership")
            if include_current:
                if not self.kernel.AssignProcessToJobObject(self.job, self.kernel.GetCurrentProcess()):
                    self.close()
                    raise OSError(ctypes.get_last_error(), "Cannot isolate controller in its private Windows job")
                self.owns_current = True

    def launch(self, command, log, cwd, env):
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   cwd=cwd, env=env, start_new_session=os.name != "nt",
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if self.job and not self.owns_current and not self.kernel.AssignProcessToJobObject(self.job, int(process._handle)):
            process.terminate()
            process.wait()
            raise OSError("Cannot assign launched process to the private desktop job")
        return process

    def stop(self, process):
        if process is None or process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait(timeout=5)

    def close(self):
        if self.owns_current:
            # Closing the last handle would kill this controller before its JSONL
            # shutdown response. Keep it until OS process teardown; children have
            # already been explicitly stopped on the normal shutdown path.
            return
        if self.job:
            self.kernel.CloseHandle(self.job)
            self.job = None


class LibraryController:
    def __init__(self, data, resources, node, instance_id, emit=None, own_process=False,
                 defer_initialization=False):
        self.data = Path(data).resolve()
        self.resources = Path(resources).resolve(strict=True)
        self.read_only_resources = self.resources.parent if self.resources.name.casefold() == "backend" else self.resources
        self.node = Path(node).resolve()
        self.instance_id = instance_id
        if self.data.is_relative_to(self.read_only_resources):
            raise ValueError("userData 必须位于只读应用 resources 之外")
        self.data.mkdir(parents=True, exist_ok=True)
        (self.data / "logs").mkdir(exist_ok=True)
        self.library = self.data / "library"
        self.lock = threading.RLock()
        self.action_lock = threading.RLock()
        self.start_lock = threading.Lock()
        self.emit = emit or (lambda value: None)
        self.closed = threading.Event()
        self.owner = ProcessOwner(include_current=own_process)
        self.worker = self.server = None
        self.worker_source = None
        self.pending_inventories = {}
        self.verified_assets = {}
        self.defer_initialization = defer_initialization
        self.initialized = False
        self.library_initialized = False
        self.model_ready = False
        self.cached_view_available = False
        self.model_ready = False
        self.initialization_thread = None
        self.thread = None
        self.review = None
        self.started_at = time.monotonic()
        self.progress_emitted_at = 0
        self.startup = {"status": "initializing", "phase": "config", "processed": 0, "total": 0,
                        "message": "已载入配置，等待验证本地资源", "seconds": 0}
        self.runtime = {"ready": False, "status": "initializing", "models": []}
        self.config_path = self.data / "desktop-library.json"
        self.instance_lock = (self.data / "controller.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.instance_lock.seek(0)
                if not self.instance_lock.read(1):
                    self.instance_lock.write(b"0")
                    self.instance_lock.flush()
                self.instance_lock.seek(0)
                msvcrt.locking(self.instance_lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.instance_lock.close()
            self.owner.close()
            raise RuntimeError("此 userData 已有桌面控制器运行，未启动第二个实例")
        saved = read_json(self.config_path) if self.config_path.exists() else {}
        if not isinstance(saved, dict) or saved and saved.get("version", 1) != 1:
            self.instance_lock.close()
            self.owner.close()
            raise RuntimeError("不支持的照片库配置版本；未覆盖配置或已有结果")
        self.config_extra = {key: deepcopy(value) for key, value in saved.items()
                             if key not in {"version", "outputRoot", "service", "sources", "detachedSources"}}
        self.sources = saved.get("sources", [])
        self.detached_sources = saved.get("detachedSources", [])
        if not isinstance(self.sources, list) or not isinstance(self.detached_sources, list) or any(
                not isinstance(source, dict) or not isinstance(source.get("path"), str)
                or not isinstance(source.get("id"), str) or not isinstance(source.get("jobs", []), list)
                for source in self.sources + self.detached_sources):
            self.instance_lock.close()
            self.owner.close()
            raise ValueError("照片库来源配置无效；未覆盖已有结果")
        self.sources_view = self.source_view()
        self.output_root = Path(saved.get("outputRoot", self.data / "output")).resolve()
        self.service_config = saved.get("service", {"mode": "local", "port": 5274})
        self.service = {"status": "starting" if defer_initialization else "stopped",
                        **self.service_config, "localUrl": None, "lanUrls": []}
        self.view_revision = 0
        self.validate_output(self.output_root)
        if not defer_initialization:
            try:
                self.initialize()
            except Exception:
                self.instance_lock.close()
                self.owner.close()
                raise

    def initialize(self):
        self.library_initialized = False
        self.model_ready = False
        self.cached_view_available = False
        self.startup_progress("sources", 0, len(self.sources))
        for index, source in enumerate(self.sources):
            self.startup_progress("sources", index, len(self.sources))
            if source.get("status") == "processing":
                source["status"] = "queued"
            if source.get("scanRequested") and source.get("status") != "queued":
                source["status"] = "queued"
            error = source_error(source["path"])
            if error:
                source.update(status="error", error=error)
        with self.lock:
            self.sources_view = self.source_view()
        self.startup_progress("sources", len(self.sources), len(self.sources))
        self.output_root.mkdir(parents=True, exist_ok=True)
        input_signature = self.review_input_signature()
        self.review = ReviewStore(self.data, self.library)
        cached = False
        try:
            cached = self.review.restore_registry(input_signature)
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.startup_progress("cache", 0, 1, warning="缓存未采用，保留原状态并重新检查：" + str(error))
            print("Review cache unavailable: " + str(error), file=sys.stderr, flush=True)
        previous_view = cached
        if not cached and (self.library / "review.json").is_file():
            try:
                manifest = read_json(self.library / "manifest.json")
                roots = {Path(source["path"]).resolve() for source in self.sources}
                media_roots = {Path(job["path"]).resolve() for source in self.sources for job in source.get("jobs", [])}
                media_roots.add(self.library.resolve())
                if any(not any(Path(root).resolve().is_relative_to(registered) for registered in roots)
                       for root in manifest.get("sourceRoots", [])) or \
                        not {Path(root).resolve() for root in manifest.get("mediaRoots", [])}.issubset(media_roots):
                    raise ValueError("Cached source/media roots are not registered in this library")
                previous_view = self.review.publish_cached_projection()
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.startup_progress("cache", 0, 1, warning="旧视图未采用，等待重新检查：" + str(error))
                print("Previous cached view unavailable: " + str(error), file=sys.stderr, flush=True)
        if previous_view:
            self.cached_view_available = True
            if cached:
                self.review.publish(cached=True)
            self.startup_progress("cache", 1, 1, message="上次完成的内容已缓存，正在检查来源与模型；保存/清理尚不可用")
            self.configure_service(**self.service_config)
        self.startup_progress("models", 0, 0)
        runtime = self.runtime_state(progress=self.startup_progress)
        self.model_ready = runtime["ready"]
        if self.defer_initialization and not runtime["ready"]:
            with self.lock:
                self.runtime = {**runtime, "ready": False, "status": "error"}
            errors = [model["error"] for model in runtime["models"] if model.get("error")]
            errors += [asset["error"] for asset in runtime.get("assetErrors", [])]
            raise RuntimeError(runtime.get("error") or "; ".join(errors) or "随包模型未就绪")
        with self.lock:
            self.runtime = {**runtime, "ready": False, "status": "initializing"} if self.defer_initialization else runtime
        self.startup_progress("review-state", 0, 1)
        if cached:
            self.review.validate_cached_sources(progress=self.startup_progress)
            self.review.publish()
            self.view_revision += 1
        else:
            self.rebuild(progress=self.startup_progress)
        self.startup_progress("review-state", 1, 1)
        if self.closed.is_set():
            return
        self.persist()
        with self.lock:
            if self.closed.is_set():
                return
            self.library_initialized = True
        if not self.defer_initialization:
            self.initialized = True
            self.startup_progress("ready", 1, 1, status="ready")

    def review_input_signature(self):
        def signature(path):
            try:
                info = Path(path).stat()
                return [str(path), info.st_size, str(info.st_mtime_ns)]
            except FileNotFoundError:
                return [str(path), None]
        inputs = []
        for source in self.sources:
            inputs.append([source["id"], source["path"], source.get("scanRequested", False), source.get("sourceIssues", [])])
            for job in source.get("jobs", []):
                root = Path(job["path"])
                inputs.append(job)
                for relative in ("manifest.json", "mapping.json", "metadata.json", "status.json",
                                 "groups", "revisions/verified-birds-birder-v2/gallery.json",
                                 "revisions/verified-birds-birder-v2/groups"):
                    inputs.append(signature(root / relative))
        for relative in ("manifest.json", "mapping.json", "groups.json", "metadata.json",
                         "revisions/verified-birds-birder-v2/gallery.json"):
            inputs.append(signature(self.library / relative))
        policies = []
        for path in (self.resources / "scripts" / "desktop_backend.py",
                     self.resources / "scripts" / "review_store.py",
                     self.resources / "scripts" / "batch_culling.py",
                     self.resources / "assets-manifest.json"):
            policies.append([path.name, hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None])
        return hashlib.sha256(json.dumps({"version": 1, "inputs": inputs, "policies": policies},
                                        sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def startup_progress(self, phase, processed=0, total=0, status="initializing", **extra):
        if self.closed.is_set():
            raise InterruptedError("启动已取消")
        now = time.monotonic()
        with self.lock:
            force = phase != self.startup["phase"] or status != self.startup["status"] or extra
            self.startup = {"status": status, "phase": phase, "processed": processed, "total": total,
                            "seconds": round(now - self.started_at, 2), **extra}
            publish = force or now - self.progress_emitted_at >= .25 or processed == total
            if publish:
                self.progress_emitted_at = now
        if publish:
            self.emit({"event": "state", "instanceId": self.instance_id, "state": self.state()})

    def runtime_state(self, progress=None):
        paths = {
            "bird-segmentation": self.resources / "public" / "culling" / "models" / "bird-yolo11s-seg.onnx",
            "bird-pose": self.resources / "public" / "culling" / "models" / "eye.onnx",
            "birder": self.resources / "models" / "birder" / (MODEL_ID + ".pt"),
        }
        manifest_path = self.resources / "assets-manifest.json"
        assets = {}
        manifest_error = None
        if manifest_path.is_file():
            try:
                asset_root = self.resources.parent.resolve(strict=True)
                required = {
                    "backend/public/culling/models/bird-yolo11s-seg.onnx",
                    "backend/public/culling/models/eye.onnx",
                    "backend/models/birder/" + MODEL_ID + ".pt",
                    "backend/models/birder/" + MODEL_ID + ".json",
                    "backend/assets/taxonomy/candidates.json",
                    "backend/assets/taxonomy/taxonomy-ebird2025-zh-SIM.json",
                    "runtime/node/node.exe", "runtime/python/python.exe",
                }
                registered = set()
                for asset in read_json(manifest_path)["assets"]:
                    relative = Path(asset["path"])
                    windows = PureWindowsPath(asset["path"])
                    if relative.is_absolute() or windows.anchor or ".." in relative.parts or ".." in windows.parts:
                        raise ValueError("资源清单包含越界路径")
                    name = relative.as_posix()
                    if name not in required or name in registered:
                        raise ValueError("资源清单包含未登记或重复路径：" + name)
                    target = (asset_root / relative).resolve(strict=True)
                    boundary = self.resources if relative.parts[0] == "backend" else asset_root / "runtime"
                    if not target.is_relative_to(asset_root) or not target.is_relative_to(boundary):
                        raise ValueError("资源清单链接目标越界：" + name)
                    identity = canonical(target)
                    if identity in assets:
                        raise ValueError("资源清单包含重复链接目标：" + name)
                    assets[identity] = asset
                    registered.add(name)
                if registered != required:
                    raise ValueError("资源清单缺少必需资产：" + ", ".join(sorted(required - registered)))
            except (OSError, ValueError, KeyError, TypeError) as error:
                manifest_error = "资源清单无效：" + str(error)
        models = []
        def verify_asset(path, asset):
            stat = path.stat()
            if stat.st_size != asset["bytes"]:
                raise ValueError("随包资源大小校验失败")
            signature = (canonical(path), stat.st_size, stat.st_mtime_ns, asset["sha256"])
            if signature not in self.verified_assets:
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        if self.closed.is_set():
                            raise InterruptedError("启动已取消")
                        digest.update(block)
                if digest.hexdigest() != asset["sha256"]:
                    raise ValueError("随包资源 SHA256 校验失败")
                self.verified_assets[signature] = True
        for name, path in paths.items():
            model = {"id": name, "path": str(path), "ready": False}
            try:
                stat = path.stat()
                if not stat.st_size:
                    raise ValueError("模型文件为空")
                if manifest_error:
                    raise ValueError(manifest_error)
                if manifest_path.is_file():
                    asset = assets.get(canonical(path))
                    if asset is None:
                        raise ValueError("必需模型未列入随包资源清单")
                    verify_asset(path, asset)
                    model.update(sha256=asset["sha256"], verified=True)
                model["ready"] = True
            except (OSError, ValueError, KeyError, TypeError) as error:
                model["error"] = str(error)
            models.append(model)
        asset_errors = []
        for index, (path, asset) in enumerate(assets.items()):
            if progress:
                progress("models", index, len(assets))
            try:
                verify_asset(Path(path), asset)
            except (OSError, ValueError, KeyError, TypeError) as error:
                asset_errors.append({"path": asset.get("path"), "error": str(error)})
        if progress:
            progress("models", len(assets), len(assets))
        missing = [name for name in ("cv2", "onnxruntime", "PIL", "birder", "torch")
                   if importlib.util.find_spec(name) is None]
        for name in ("candidates.json", "taxonomy-ebird2025-zh-SIM.json"):
            if not (self.resources / "assets" / "taxonomy" / name).is_file():
                missing.append(name)
        if not (paths["birder"].with_suffix(".json")).is_file():
            missing.append(MODEL_ID + ".json")
        if not self.node.is_file():
            missing.append("node")
        return {"ready": all(model["ready"] for model in models) and not missing and not asset_errors, "models": models,
                "mode": "packaged-verified" if manifest_path.is_file() else "development-unverified",
                **({"assetErrors": asset_errors} if asset_errors else {}),
                **({"error": "缺少运行资源：" + ", ".join(missing)} if missing else {})}

    def state(self):
        with self.lock:
            return {"version": 1, "instanceId": self.instance_id, "viewRevision": self.view_revision,
                    "runtime": deepcopy(self.runtime),
                    "startup": {**deepcopy(self.startup), "seconds": round(time.monotonic() - self.started_at, 2)}
                    if self.startup["status"] == "initializing" else deepcopy(self.startup),
                    "sources": deepcopy(self.sources_view),
                    "outputRoot": str(self.output_root), "service": deepcopy(self.service)}

    def persist(self):
        atomic_json(self.config_path, {**self.config_extra, "version": 1, "outputRoot": str(self.output_root),
                                      "service": self.service_config, "sources": self.sources,
                                      "detachedSources": self.detached_sources})

    def source_view(self):
        return [{key: deepcopy(value) for key, value in source.items() if key != "jobs"}
                for source in self.sources]

    def changed(self):
        if self.closed.is_set():
            return
        self.persist()
        with self.lock:
            self.sources_view = self.source_view()
        self.emit({"event": "state", "instanceId": self.instance_id, "state": self.state()})

    def validate_output(self, output, additional=None):
        for source in self.sources + self.detached_sources + ([additional] if additional else []):
            original = Path(source["path"]).resolve()
            output = Path(output).resolve()
            if output.is_relative_to(original) or original.is_relative_to(output / "jobs"):
                raise ValueError("输出目录不能位于任何原片目录内")
        if Path(output).resolve().is_relative_to(self.read_only_resources):
            raise ValueError("输出目录不能位于只读应用 resources 内")

    def validate_source(self, path):
        path = Path(path).resolve(strict=True)
        error = source_error(path)
        if error:
            raise ValueError(error)
        if path == Path(path.anchor):
            raise ValueError("不能扫描磁盘根目录，请选择具体照片文件夹")
        if self.data.is_relative_to(path) or path.is_relative_to(self.data) \
                or self.resources.is_relative_to(path) or path.is_relative_to(self.resources):
            raise ValueError("原片目录不能包含 userData 或应用资源")
        self.validate_output(self.output_root, {"path": str(path)})
        return path

    def rebuild(self, progress=None):
        strict = self.defer_initialization and progress is not None
        gallery = aggregate_library(self.library, self.sources, progress=progress, strict=strict)
        if strict and gallery["sourceErrors"]:
            raise RuntimeError("; ".join(row["error"] for row in gallery["sourceErrors"]))
        if progress:
            progress("review", 0, 0)
        self.review.refresh(self.review_busy_sources(), progress=progress)
        if progress:
            progress("review-publish", 0, 1)
        self.review.publish()
        if not any(source.get("status") in {"queued", "processing"} for source in self.sources):
            try:
                if self.review.cache_path.exists() and read_json(self.review.cache_path).get("version") != 1:
                    raise ValueError("Future cache schema preserved; no cached state overwritten")
                self.review.cache_registry(self.review_input_signature())
            except (OSError, ValueError, KeyError, TypeError) as error:
                print("Review cache was not saved: " + str(error), file=sys.stderr, flush=True)
        self.view_revision += 1
        changed = False
        for error in gallery["sourceErrors"]:
            source = next((source for source in self.sources if source["id"] == error["sourceId"]), None)
            if source and source.get("error") != error["error"]:
                source.update(status="error", error=error["error"])
                changed = True
        if changed:
            self.changed()

    def review_busy_sources(self):
        return {source["id"] for source in self.sources if source.get("status") in {"queued", "processing"}
                or source["id"] == self.worker_source}

    def review_state(self):
        return self.review.snapshot()

    def add_source(self, path):
        path = self.validate_source(path)
        for source in self.sources:
            if canonical(source["path"]) == canonical(path):
                self.rescan(source["id"])
                return source
        detached = next((source for source in self.detached_sources if canonical(source["path"]) == canonical(path)), None)
        if detached:
            self.detached_sources.remove(detached)
            self.sources.append(detached)
            detached.pop("error", None)
            detached["status"] = "queued" if any(record.get("managed") and not record.get("completed")
                                                for record in detached["jobs"]) else "completed"
            self.rebuild()
            self.changed()
            return detached
        source = {"id": stable_id("source-", canonical(path)), "path": str(path),
                  "name": path.name, "status": "queued", "jobs": []}
        self.sources.append(source)
        self.rebuild()
        self.changed()
        return source

    def attach_job(self, path):
        job, manifest = read_job(path)
        original_root = Path(manifest["source"]).resolve()
        self.validate_output(self.output_root, {"path": str(original_root)})
        if self.data.is_relative_to(original_root):
            raise ValueError("批次原片目录包含 userData，不能登记")
        source = next((source for source in self.sources if canonical(source["path"]) == canonical(original_root)), None)
        if source is None:
            source = next((source for source in self.detached_sources
                           if canonical(source["path"]) == canonical(original_root)), None)
            if source:
                self.detached_sources.remove(source)
                self.sources.append(source)
        if source is None:
            source = {"id": stable_id("source-", canonical(original_root)), "path": str(original_root),
                      "name": original_root.name, "status": "completed", "jobs": []}
            self.sources.append(source)
        if not any(canonical(record["path"]) == canonical(job) for record in source["jobs"]):
            source["jobs"].append({"path": str(job), "managed": False})
        source["job"] = str(job)
        if source.get("status") != "processing":
            error = source_error(original_root)
            source["status"] = "error" if error else "completed"
            if error:
                source["error"] = error
        self.rebuild()
        self.changed()
        return source

    def find_source(self, source_id):
        source = next((source for source in self.sources if source["id"] == source_id), None)
        if source is None:
            raise ValueError("未知来源目录")
        return source

    def remove_source(self, source_id):
        source = self.find_source(source_id)
        if self.worker_source == source_id:
            self.owner.stop(self.worker)
            self.worker = self.worker_source = None
        self.sources.remove(source)
        self.detached_sources = [old for old in self.detached_sources if old["id"] != source_id] + [source]
        self.rebuild()
        self.changed()
        return {"removed": source_id}

    def rescan(self, source_id):
        source = self.find_source(source_id)
        self.validate_source(source["path"])
        source["scanRequested"] = True
        source.pop("scanSnapshot", None)
        source.pop("scanAttempts", None)
        source.pop("pendingFiles", None)
        if source["status"] != "processing":
            source["status"] = "queued"
            source.pop("error", None)
        self.changed()
        return source

    def check_additions(self, source):
        from batch_culling import inventory
        snapshot = inventory(Path(source["path"]))
        known = known_photos([source])
        current = {canonical(Path(source["path"]) / photo["primary"]["relative"]): photo for photo in snapshot["photos"]}
        issues = []
        for identity, original in known.items():
            photo = current.get(identity)
            if photo is None:
                # A newly paired RAW is no longer a primary, but is still present.
                path = Path(identity)
                if not path.exists():
                    issues.append({"path": original["relative"], "kind": "missing", "message": "原片已删除或失联；历史裁剪仍保留"})
            elif (photo["primary"]["size"], photo["primary"]["mtimeNs"]) != (original["size"], original["mtimeNs"]) or \
                    original.get("sourceSha256") and hashlib.sha256(Path(identity).read_bytes()).hexdigest() != original["sourceSha256"]:
                issues.append({"path": original["relative"], "kind": "changed", "message": "同路径原片已改变，待确认；不重算或错配旧原图"})
        issues.extend({"kind": "history-tail", "path": ", ".join(tail["newPhotos"]), "message": tail["reason"],
                       "groupId": tail["groupId"]} for tail in source.get("historyTails", [])
                      if tail.get("status") == "pending" and tail.get("groupId") and tail.get("reason"))
        source["sourceIssues"] = issues
        candidates = [photo for identity, photo in current.items() if identity not in known]
        measured = {photo["primary"]["relative"]: [photo["primary"]["size"], photo["primary"]["mtimeNs"]] for photo in candidates}
        previous = source.get("scanSnapshot")
        source["scanSnapshot"] = measured
        source["scanAttempts"] = source.get("scanAttempts", 0) + 1
        stable = [photo for photo in candidates if previous and previous.get(photo["primary"]["relative"]) == measured[photo["primary"]["relative"]]
                  and time.time_ns() - photo["primary"]["mtimeNs"] >= 1_000_000_000]
        waiting = [photo["primary"]["relative"] for photo in candidates if photo not in stable]
        source["pendingFiles"] = waiting
        if waiting and source["scanAttempts"] < 6:
            source["progress"] = {"stage": "scan", "phase": "等待复制中的新增文件稳定（size/mtime复查）"}
            self.changed()
            return None
        source["scanRequested"] = False
        source.pop("scanSnapshot", None)
        source.pop("scanAttempts", None)
        # RAW companions only change provenance, never launch a pixel model.
        associations = {}
        for photo in snapshot["photos"]:
            identity = canonical(Path(source["path"]) / photo["primary"]["relative"])
            if identity in known and photo["companions"] and not any(
                    issue["path"] == photo["primary"]["relative"] for issue in issues):
                associations[photo["primary"]["relative"]] = photo["companions"]
        source["associations"] = associations
        for record in source["jobs"]:
            if not record.get("managed") or not record.get("completed") or not associations:
                continue
            job = Path(record["path"])
            manifest = read_json(job / "manifest.json")
            for photo in manifest["photos"]:
                if photo["primary"]["relative"] in associations:
                    photo["companions"] = associations[photo["primary"]["relative"]]
            atomic_json(job / "manifest.json", manifest)
        wanted = {photo["id"] for photo in stable}
        grouped = []
        for group in snapshot["groups"]:
            ids = [photo for photo in group["photoIds"] if photo in wanted]
            if ids:
                index = len(grouped) + 1
                grouped.append({**group, "id": f"g{index:05d}", "index": index, "photoIds": ids, "count": len(ids)})
        self.pending_inventories[source["id"]] = {
            **snapshot, "photos": stable, "groups": grouped,
            "physicalPhotos": sum(1 + len(photo["companions"]) for photo in stable)}
        source["historyTails"] = history_tail_plans(source, stable)
        return stable

    def environment(self):
        env = os.environ.copy()
        env.update(BIRDSHOT_MODEL_CACHE=str(self.resources / "models"), PYTHONUTF8="1", PYTHONUNBUFFERED="1",
                   PYTHONDONTWRITEBYTECODE="1")
        return env

    def launch(self, command, log_name, offline=False):
        env = self.environment()
        if offline:
            env["BIRDSHOT_OFFLINE"] = "1"
        with (self.data / "logs" / log_name).open("ab", buffering=0) as log:
            return self.owner.launch(command, log, self.data, env)

    def start_worker(self, source):
        self.runtime = self.runtime_state()
        if not self.runtime["ready"]:
            source.update(status="error", error=self.runtime.get("error") or "随包模型未就绪")
            self.changed()
            return
        error = source_error(source["path"])
        if error:
            source.update(status="error", error=error)
            self.changed()
            return
        resumable = next((record for record in reversed(source["jobs"]) if record.get("managed") and not record.get("completed")), None)
        additions = None
        if resumable is None:
            additions = self.check_additions(source)
            if additions is None:
                return
            if not additions:
                self.pending_inventories.pop(source["id"], None)
                source.update(status="error" if source["sourceIssues"] else "completed",
                              progress={"stage": "scan", "phase": "检查完成：没有稳定新增照片" +
                                        ("；复制中的文件仍待下次检查" if source["pendingFiles"] else "")})
                if source["sourceIssues"]:
                    source["error"] = "原片变化/失联需确认；历史裁剪保留"
                self.rebuild()
                self.changed()
                return
            resumable = next((record for record in reversed(source["jobs"]) if record.get("managed") and record.get("completed")), None)
            if resumable:
                resumable["completed"] = False
        if resumable is None:
            job = self.output_root / "jobs" / ("job-" + uuid.uuid4().hex[:12])
            resumable = {"path": str(job), "managed": True, "completed": False}
            source["jobs"].append(resumable)
        if source.get("historyTails"):
            resumable["overlay"] = True
        job = Path(resumable["path"])
        excluded = known_photos(self.sources)
        spec_path = self.data / ("worker-" + source["id"] + ".json")
        previous_spec = read_json(spec_path) if spec_path.exists() else {}
        if previous_spec.get("job") != str(job):
            previous_spec = {}
        inventory_snapshot = self.pending_inventories.pop(source["id"], None) or previous_spec.get("inventory")
        if additions is None:
            additions = previous_spec.get("additionalPhotos")
        atomic_json(spec_path, {"resources": str(self.resources), "node": str(self.node), "source": source["path"],
                                "job": str(job), "excludePaths": list(excluded) +
                                [str(Path(source["path"]) / name) for name in source.get("pendingFiles", [])],
                                "inventory": inventory_snapshot,
                                "historyTails": source.get("historyTails") or previous_spec.get("historyTails"),
                                "additionalPhotos": additions if (job / "manifest.json").exists() else None})
        source.update(status="processing", job=str(job), progress={"stage": "scan", "phase": "清点照片目录"})
        source.pop("error", None)
        self.changed()
        self.worker = self.launch([sys.executable, "-B", str(self.resources / "scripts" / "desktop_backend.py"),
                                   "--worker", str(spec_path)], job.name + ".log", offline=True)
        self.worker_source = source["id"]

    def poll_worker(self):
        if self.worker is None:
            return
        source = self.find_source(self.worker_source)
        job = Path(source["job"])
        status_path = job.parent / (job.name + ".worker.json")
        worker_status = read_json(status_path) if status_path.is_file() else {}
        progress = worker_status.get("progress") or (
            read_json(job / "status.json") if (job / "status.json").is_file() else {})
        progress["stage"] = worker_status.get("stage", "scan")
        verification = job / REVISION / "verification.json"
        if progress["stage"] == "birder" and verification.is_file():
            result = read_json(verification)
            progress.update(processed=result.get("processed", 0), total=result.get("total", 0),
                            phase="Birder 全球类别验鸟与鸟种识别")
        if progress != source.get("progress"):
            published = progress.get("publishedRevision", 0) != (source.get("progress") or {}).get("publishedRevision", 0)
            source["progress"] = progress
            if published:
                self.rebuild()
            self.changed()
        returncode = self.worker.poll()
        if returncode is None:
            return
        if returncode == 0 and worker_status.get("status") == "completed":
            if worker_status.get("historyTails"):
                source["historyTails"] = worker_status["historyTails"]
                source["sourceIssues"] = [issue for issue in source.get("sourceIssues", []) if issue["kind"] != "history-tail"] + [
                    {"kind": "history-tail", "path": ", ".join(tail["newPhotos"]), "message": tail["reason"],
                     "groupId": tail["groupId"]}
                    for tail in source["historyTails"] if tail["status"] == "pending"]
            source["status"] = "queued" if source.get("scanRequested") else "error" if source.get("sourceIssues") else "completed"
            if source.get("sourceIssues"):
                source["error"] = "来源事项待确认；历史缓存只读保留，新照片处理已完成"
            next(record for record in source["jobs"] if record["path"] == str(job))["completed"] = True
        else:
            source.update(status="error", error=worker_status.get("error") or
                          f"处理进程退出 ({returncode})；详见 userData/logs/{job.name}.log")
        self.worker = self.worker_source = None
        self.rebuild()
        self.changed()

    @staticmethod
    def available_port(start):
        for port in range(start, min(start + 100, 65536)):
            try:
                with socket.socket() as probe:
                    if os.name == "nt":
                        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    probe.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
        return None

    def configure_service(self, mode="local", port=5274, lanIp=None):
        if mode not in {"local", "lan", "all"} or isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("服务模式或端口无效")
        if mode == "lan":
            address = ipaddress.IPv4Address(lanIp or "")
            if not any(address in ipaddress.IPv4Network(block) for block in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
                raise ValueError("请选择明确的物理局域网 IPv4 地址")
        if self.closed.is_set():
            raise RuntimeError("控制器已关闭")
        if self.defer_initialization:
            with self.lock:
                self.initialized = False
                self.runtime.update(ready=False, status="initializing")
            self.startup_progress("service", 0, 1)
        self.owner.stop(self.server)
        self.server = None
        self.service_config = {"mode": mode, "port": port, **({"lanIp": lanIp} if mode == "lan" else {})}
        self.service = {"status": "starting", **self.service_config, "localUrl": None, "lanUrls": []}
        self.changed()
        addresses = ["0.0.0.0"] if mode == "all" else ["127.0.0.1"] + ([lanIp] if mode == "lan" else [])
        probes = []
        try:
            for address in addresses:
                probe = socket.socket()
                probes.append(probe)
                if os.name == "nt":
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                probe.bind((address, port))
        except OSError as error:
            self.service.update(status="error", error=f"端口 {port} 已占用或所选地址不可用：{error}",
                                suggestedPort=self.available_port(port + 1))
            self.changed()
            return deepcopy(self.service)
        finally:
            for probe in probes:
                probe.close()
        token = self.instance_id
        command = [sys.executable, "-B", str(self.resources / "scripts" / "batch_culling.py"),
                   "serve", "--job", str(self.library), "--port", str(port), "--instance", token, "--read-only",
                   "--review-state", str(self.review.path)]
        if mode == "lan":
            command += ["--lan-ip", lanIp]
        elif mode == "all":
            command += ["--allow-all"]
        try:
            with self.lock:
                if self.closed.is_set():
                    raise RuntimeError("控制器已关闭")
                self.service_started_at = time.monotonic()
                self.server = self.launch(command, "gallery-" + self.instance_id + ".log")
            if not self.defer_initialization:
                deadline = time.monotonic() + 20
                while not self.closed.is_set() and time.monotonic() < deadline:
                    self.poll_service()
                    if self.service["status"] != "starting":
                        break
                    self.closed.wait(.1)
            return deepcopy(self.service)
        except (OSError, RuntimeError) as error:
            self.owner.stop(self.server)
            self.server = None
            self.service.update(status="error", error=str(error), suggestedPort=self.available_port(port + 1))
            self.changed()
            return deepcopy(self.service)

    def startup_failed(self, error):
        if self.closed.is_set():
            return
        with self.lock:
            self.initialized = False
            self.runtime.update(ready=False, status="error")
            if self.cached_view_available and self.server and self.server.poll() is None:
                self.service.update(status="cached" if self.service.get("localUrl") else "starting", error=str(error))
            elif self.service.get("status") == "cached":
                self.service.update(error=str(error))
            else:
                self.service.update(status="error", error=str(error), localUrl=None, lanUrls=[])
        self.startup_progress(self.startup["phase"], self.startup.get("processed", 0),
                              self.startup.get("total", 0), status="error", error=str(error))
        print("Desktop startup failed: " + str(error), file=sys.stderr, flush=True)

    def poll_service(self):
        server = self.server
        if server is None or self.closed.is_set():
            return
        if server.poll() is not None:
            if self.server is server:
                self.server = None
                self.startup_failed("图库服务退出；详见 userData/logs/gallery-" + self.instance_id + ".log")
            return
        if self.service["status"] not in {"starting", "cached"}:
            return
        try:
            opener = build_opener(ProxyHandler({}))
            port = self.service_config["port"]
            with opener.open(f"http://127.0.0.1:{port}/health.json", timeout=.5) as response:
                try:
                    health = json.load(response)
                except ValueError as error:
                    raise RuntimeError("图库服务健康响应无效，未确认实例身份") from error
            if not isinstance(health, dict) or health.get("instanceId") != self.instance_id:
                raise RuntimeError("端口属于另一个服务实例，拒绝连接")
            if health.get("status") == "error":
                raise RuntimeError(health.get("error") or "图库服务初始化失败")
            if health.get("status") != "ready":
                if health.get("status") != "starting":
                    raise RuntimeError("图库服务健康状态无效，拒绝连接")
                progress = health.get("startup") or {}
                self.startup_progress("service", progress.get("processed", 0), progress.get("total", 1),
                                      message=progress.get("message", "图库服务正在验证注册媒体"))
                return
            if self.server is not server or self.closed.is_set():
                return
            validated = self.library_initialized and (self.model_ready or not self.defer_initialization)
            if self.service["status"] == "cached" and not validated:
                return
            with self.lock:
                self.service.update(status="ready" if validated else "cached", localUrl=f"http://127.0.0.1:{port}/",
                                    lanUrls=self.lan_urls(self.service_config["mode"], port,
                                                          self.service_config.get("lanIp")))
                self.service.pop("warning", None)
                if validated:
                    self.service.pop("error", None)
                if self.defer_initialization and validated:
                    self.initialized = True
                    self.runtime.update(ready=self.model_ready, status="ready")
            self.changed()
            if self.defer_initialization and validated:
                self.startup_progress("ready", 1, 1, status="ready")
                self.emit({"event": "ready", "instanceId": self.instance_id, "state": self.state()})
        except (OSError, ValueError):
            if time.monotonic() - self.service_started_at >= 20 and not self.service.get("warning"):
                self.service["warning"] = "图库服务仍在启动，继续等待本实例健康检查；未停止正在初始化的进程"
                self.startup_progress("service", 0, 1, warning=self.service["warning"])
        except RuntimeError as error:
            if self.server is server:
                self.owner.stop(server)
                self.server = None
                self.startup_failed(error)

    @staticmethod
    def lan_urls(mode, port, lan_ip):
        if mode == "local":
            return []
        if mode == "lan":
            return [f"http://{lan_ip}:{port}/"]
        try:
            addresses = {entry[4][0] for entry in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
            return [f"http://{address}:{port}/" for address in sorted(addresses)
                    if ipaddress.IPv4Address(address).is_private and not ipaddress.IPv4Address(address).is_loopback]
        except OSError:
            return []

    def set_output(self, path):
        output = Path(path).resolve()
        self.validate_output(output)
        output.mkdir(parents=True, exist_ok=True)
        self.output_root = output
        self.changed()
        return {"outputRoot": str(output)}

    def dispatch(self, action, params):
        if action == "state":
            return self.state()
        if action == "shutdown":
            return self.shutdown()
        if action == "retry_startup":
            return self.retry_startup()
        def allowed():
            return self.initialized or action == "configure_service" and self.library_initialized and \
                self.model_ready and self.startup["status"] == "error"
        if not allowed() or self.closed.is_set():
            raise RuntimeError("初始化中或初始化失败；请等待启动完成或重试启动" if not self.closed.is_set()
                               else "控制器已关闭")
        with self.action_lock:
            if not allowed() or self.closed.is_set():
                raise RuntimeError("初始化中或控制器已关闭")
            if action.startswith("review_") and action not in {"review_state", "review_save"}:
                self.review.refresh(self.review_busy_sources())
            actions = {"state": lambda: self.state(), "add_source": self.add_source, "attach_job": self.attach_job,
                       "remove_source": lambda id: self.remove_source(id), "rescan": lambda id: self.rescan(id),
                       "configure_service": self.configure_service, "set_output": self.set_output,
                       "review_state": self.review_state, "review_decide": self.review.decide, "review_save": self.review.save,
                       "review_preferences": self.review.preferences,
                       "review_cleanup_preview": self.review.cleanup_preview,
                       "review_cleanup_claim": self.review.cleanup_claim,
                       "review_cleanup_result": self.review.cleanup_result,
                       "review_cleanup_cancel": self.review.cleanup_cancel,
                       "shutdown": lambda: self.shutdown()}
            if action not in actions:
                raise ValueError("不支持的命令")
            result = actions[action](**params)
            if action == "configure_service" and self.defer_initialization and result["status"] == "error":
                self.startup_failed(result.get("error") or "图库服务启动失败")
            if action in {"add_source", "attach_job", "rescan"}:
                return {key: deepcopy(value) for key, value in result.items() if key != "jobs"}
            return result

    def background(self):
        last_check = 0
        while not self.closed.wait(.5):
            try:
                self.poll_service()
                if not self.initialized:
                    continue
                with self.action_lock:
                    self.poll_worker()
                    if time.monotonic() - last_check > 5:
                        last_check = time.monotonic()
                        for source in self.sources:
                            error = source_error(source["path"])
                            if error and source.get("error") != error:
                                if self.worker_source == source["id"]:
                                    self.owner.stop(self.worker)
                                    self.worker = self.worker_source = None
                                source.update(status="error", error=error)
                                self.changed()
                    if self.worker is None:
                        queued = next((source for source in self.sources if source["status"] == "queued"), None)
                        if queued:
                            try:
                                self.start_worker(queued)
                            except (OSError, ValueError, RuntimeError) as error:
                                queued.update(status="error", error=str(error))
                                self.changed()
            except Exception:
                traceback.print_exc(file=sys.stderr)

    def start(self):
        if self.thread is not None or self.closed.is_set():
            return
        self.emit({"event": "controller-ready", "instanceId": self.instance_id, "state": self.state()})
        self.thread = threading.Thread(target=self.background, name="desktop-library", daemon=True)
        self.thread.start()
        if self.defer_initialization:
            self.retry_startup()
        else:
            self.configure_service(**self.service_config)
            if self.service["status"] == "ready" and self.runtime["ready"]:
                self.emit({"event": "ready", "instanceId": self.instance_id, "state": self.state()})

    def retry_startup(self):
        with self.start_lock:
            if self.closed.is_set():
                raise RuntimeError("控制器已关闭，不能重试启动")
            if self.initialization_thread and self.initialization_thread.is_alive() or self.server and \
                    self.service["status"] == "starting" and self.startup["status"] != "error" or self.initialized:
                return self.state()
            self.started_at = time.monotonic()
            self.runtime.update(ready=False, status="initializing")
            self.service.update(status="starting", localUrl=None, lanUrls=[])
            self.service.pop("error", None)
            self.service.pop("warning", None)
            self.startup_progress("config", 0, 1)
            self.initialization_thread = threading.Thread(target=self.initialize_background,
                                                          name="desktop-startup", daemon=True)
            self.initialization_thread.start()
        return self.state()

    def initialize_background(self):
        try:
            with self.action_lock:
                self.initialize()
                if self.closed.is_set():
                    return
                self.startup_progress("service", 0, 1)
                service = self.service if self.server and self.service["status"] in {"starting", "cached", "ready"} else \
                    self.configure_service(**self.service_config)
                if service["status"] == "error":
                    self.startup_failed(service.get("error", "图库服务启动失败"))
        except Exception as error:
            if not self.closed.is_set():
                traceback.print_exc(file=sys.stderr)
                self.startup_failed(error)

    def shutdown(self):
        with self.lock:
            return self._shutdown()

    def _shutdown(self):
        if self.closed.is_set():
            return {"stopped": True}
        self.closed.set()
        self.owner.stop(self.worker)
        self.owner.stop(self.server)
        self.worker = self.server = None
        for source in self.sources:
            if source["status"] == "processing":
                source["status"] = "queued"
        self.service.update(status="stopped", localUrl=None, lanUrls=[])
        self.runtime.update(ready=False, status="stopped")
        self.startup.update(status="stopped")
        if self.initialized:
            self.persist()
        self.owner.close()
        self.instance_lock.close()
        return {"stopped": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--resources", type=Path)
    parser.add_argument("--node", type=Path)
    parser.add_argument("--instance")
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        run_worker(read_json(args.worker))
        return
    if not all((args.data, args.resources, args.node, args.instance)):
        parser.error("--data, --resources, --node and --instance are required")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    output_lock = threading.Lock()
    def emit(value):
        with output_lock:
            print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)
    controller = LibraryController(args.data, args.resources, args.node, args.instance, emit, own_process=True,
                                   defer_initialization=True)
    try:
        controller.start()
        for line in sys.stdin:
            request_id = None
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("请求必须为 JSON 对象")
                request_id = request.get("id")
                params = request.get("params", {})
                if not isinstance(params, dict):
                    raise ValueError("params 必须为 JSON 对象")
                result = controller.dispatch(request.get("action"), params)
                emit({"id": request_id, "ok": True, "result": result, "error": None})
                if request.get("action") == "shutdown":
                    break
            except Exception as error:
                emit({"id": request_id, "ok": False, "result": None, "error": str(error)})
    finally:
        controller.shutdown()


if __name__ == "__main__":
    main()
