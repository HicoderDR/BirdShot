# SPDX-License-Identifier: AGPL-3.0-only
"""Private stdin JSONL desktop library controller.

Run with --data USER_DATA --resources BACKEND_ROOT --node NODE --instance UUID.
Only JSONL responses/events go to stdout. Inference and gallery service output is
captured in USER_DATA/logs; no HTTP management endpoint is created.

Actions: state, add_source{path}, attach_job{path}, remove_source{id},
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
import socket
import subprocess
import sys
import threading
import time
import traceback
from urllib.request import ProxyHandler, build_opener
import uuid

from gallery_navigation import canonical, capture_metadata, enrich_navigation, stable_id


REVISION = Path("revisions") / "verified-birds-birder-v2"
MODEL_ID = "hieradet_d_small_dino-v2-inat21"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".part")
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
            for photo in manifest["photos"]:
                root = photo.get("sourceRoot") or manifest["source"]
                known[canonical(Path(root) / photo["primary"]["relative"])] = photo["primary"]
    return known


def aggregate_library(root, sources):
    """Derive a registered multi-root view; never copy media or write old jobs."""
    root = Path(root)
    photos, items, details, media, crop_mapping, shoot_groups = [], [], [], {}, [], []
    seen = set()
    processed_ids, failed_ids = set(), set()
    errors = []
    gallery = {"version": 1, "analysisVersion": "desktop-library-v1", "revision": "desktop-library-v1",
               "status": "completed", "updatedAt": datetime.now().isoformat(),
               "items": items, "frames": [], "shootGroups": shoot_groups, "sourceErrors": errors}
    for source in sources:
        for job_record in source.get("jobs", []):
            job = Path(job_record["path"])
            if not (job / "manifest.json").is_file():
                if source.get("status") not in {"queued", "processing"}:
                    errors.append({"sourceId": source["id"], "job": str(job), "error": "批次清单尚不可用"})
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
                frame_ids, group_ids = {}, {}
                def group_id(old_id):
                    return group_ids.setdefault(old_id, stable_id("g", [job_key, old_id]))
                def register_media(relative):
                    if not relative:
                        return relative
                    relative_path = Path(relative)
                    if relative_path.is_absolute() or ".." in relative_path.parts:
                        raise ValueError("批次媒体包含越界路径")
                    relative = relative_path.as_posix()
                    alias = f"jobs/{job_key}/{relative}"
                    media[alias] = {"path": alias, "root": str(job.resolve()), "relative": relative}
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
                                      "birdVerification": {"status": "unknown", "reason": "此旧批次尚无验鸟结果；只读加载，不重跑"}})
                group_dir = job / REVISION / "groups"
                if not group_dir.is_dir():
                    group_dir = job / "groups"
                for group_file in sorted(group_dir.glob("g*.json")):
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
                shoot_groups.extend(deepcopy(old_gallery.get("shootGroups", [])))
                if old_gallery.get("model"):
                    gallery.setdefault("model", old_gallery["model"])
                if old_gallery.get("policy"):
                    gallery.setdefault("policy", old_gallery["policy"])
            except (OSError, ValueError, KeyError, TypeError) as error:
                errors.append({"sourceId": source["id"], "job": str(job), "error": str(error)})
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
    for detail in gallery.pop("groups"):
        atomic_json(root / REVISION / "groups" / (detail["group"]["id"] + ".json"), detail)
        atomic_json(root / "groups" / (detail["group"]["id"] + ".json"), detail)
    atomic_json(root / "mapping.json", {"sourceRoots": manifest["sourceRoots"], "crops": crop_mapping})
    atomic_json(root / "groups.json", manifest["groups"])
    atomic_json(root / "status.json", {"status": gallery["status"], "totalPhotos": len(photos), "cropCount": len(items)})
    atomic_json(root / "manifest.json", manifest)
    atomic_json(root / REVISION / "gallery.json", gallery)
    return gallery


def run_worker(spec):
    """Subprocess-only entry: every write is confined to this managed job."""
    resources, job = Path(spec["resources"]), Path(spec["job"])
    sys.path.insert(0, str(resources / "scripts"))
    os.environ["BIRDSHOT_MODEL_CACHE"] = str(resources / "models")
    from batch_culling import initialize, run
    from capture_metadata import extract
    from gallery_build import build
    result_path = job.parent / (job.name + ".worker.json")
    def stage(name):
        atomic_json(result_path, {"status": "processing", "stage": name, "job": str(job)})
    try:
        stage("scan")
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
            initialize(Path(spec["source"]), job, spec.get("excludePaths", []))
        stage("detection")
        status = read_json(job / "status.json")
        if status["status"] != "completed":
            run(job, Path(spec["node"]))
        stage("metadata")
        if not (job / "revisions" / "capture-exif-v1.json").exists():
            extract(job)
            metadata = read_json(job / "revisions" / "capture-exif-v1.json")
            metadata.pop("userRegionReference", None)
            atomic_json(job / "revisions" / "capture-exif-v1.json", metadata)
        taxonomy = resources / "assets" / "taxonomy"
        stage("birder")
        mapping = read_json(job / "mapping.json")["crops"]
        if mapping:
            from gallery_birder import run as classify
            classify(job, job / REVISION, taxonomy, 4)
            stage("gallery")
            build(job, job / REVISION, Path(spec["node"]), taxonomy)
        else:
            atomic_json(job / REVISION / "gallery.json", {
                "version": 1, "status": "completed", "items": [], "summary": {"preferred": 0},
                "progress": {"processed": 0, "total": 0}})
        atomic_json(result_path, {"status": "completed", "stage": "gallery", "job": str(job)})
    except BaseException as error:
        atomic_json(result_path, {"status": "error", "error": str(error), "job": str(job)})
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
    def __init__(self, data, resources, node, instance_id, emit=None, own_process=False):
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
        self.emit = emit or (lambda value: None)
        self.closed = threading.Event()
        self.owner = ProcessOwner(include_current=own_process)
        self.worker = self.server = None
        self.worker_source = None
        self.verified_assets = {}
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
        self.sources = saved.get("sources", [])
        self.detached_sources = saved.get("detachedSources", [])
        self.output_root = Path(saved.get("outputRoot", self.data / "output")).resolve()
        self.service_config = saved.get("service", {"mode": "local", "port": 5274})
        self.service = {"status": "stopped", **self.service_config, "localUrl": None, "lanUrls": []}
        for source in self.sources:
            if source.get("status") == "processing":
                source["status"] = "queued"
            error = source_error(source["path"])
            if error:
                source.update(status="error", error=error)
        self.validate_output(self.output_root)
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.runtime = self.runtime_state()
        self.persist()
        self.rebuild()

    def runtime_state(self):
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
        for path, asset in assets.items():
            try:
                verify_asset(Path(path), asset)
            except (OSError, ValueError, KeyError, TypeError) as error:
                asset_errors.append({"path": asset.get("path"), "error": str(error)})
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
            return {"version": 1, "instanceId": self.instance_id, "runtime": deepcopy(self.runtime),
                    "sources": [{key: deepcopy(value) for key, value in source.items() if key != "jobs"}
                                for source in self.sources],
                    "outputRoot": str(self.output_root), "service": deepcopy(self.service)}

    def persist(self):
        atomic_json(self.config_path, {"version": 1, "outputRoot": str(self.output_root),
                                      "service": self.service_config, "sources": self.sources,
                                      "detachedSources": self.detached_sources})

    def changed(self):
        self.persist()
        self.emit({"event": "state", "state": self.state()})

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

    def rebuild(self):
        gallery = aggregate_library(self.library, self.sources)
        changed = False
        for error in gallery["sourceErrors"]:
            source = next((source for source in self.sources if source["id"] == error["sourceId"]), None)
            if source and source.get("error") != error["error"]:
                source.update(status="error", error=error["error"])
                changed = True
        if changed:
            self.changed()

    def add_source(self, path):
        path = self.validate_source(path)
        for source in self.sources:
            if canonical(source["path"]) == canonical(path):
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
        if source["status"] != "processing":
            source["status"] = "queued"
            source.pop("error", None)
            self.changed()
        return source

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
        if resumable is None:
            job = self.output_root / "jobs" / ("job-" + uuid.uuid4().hex[:12])
            resumable = {"path": str(job), "managed": True, "completed": False}
            source["jobs"].append(resumable)
        job = Path(resumable["path"])
        excluded = known_photos(self.sources)
        if not (job / "manifest.json").is_file():
            for identity, original in excluded.items():
                original_path = Path(identity)
                if original_path.is_relative_to(Path(source["path"]).resolve()) and original_path.exists():
                    stat = original_path.stat()
                    if (stat.st_size, stat.st_mtime_ns) != (original.get("size"), original.get("mtimeNs")):
                        raise ValueError("已登记原片发生变化，未覆盖或重算旧结果：" + str(original_path))
        spec_path = self.data / ("worker-" + source["id"] + ".json")
        atomic_json(spec_path, {"resources": str(self.resources), "node": str(self.node), "source": source["path"],
                                "job": str(job), "excludePaths": list(excluded)})
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
        progress = read_json(job / "status.json") if (job / "status.json").is_file() else {}
        progress["stage"] = worker_status.get("stage", "scan")
        verification = job / REVISION / "verification.json"
        if progress["stage"] == "birder" and verification.is_file():
            result = read_json(verification)
            progress.update(processed=result.get("processed", 0), total=result.get("total", 0),
                            phase="Birder 全球类别验鸟与鸟种识别")
        if progress != source.get("progress"):
            source["progress"] = progress
            self.changed()
        returncode = self.worker.poll()
        if returncode is None:
            return
        if returncode == 0 and worker_status.get("status") == "completed":
            source["status"] = "completed"
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
                   "serve", "--job", str(self.library), "--port", str(port), "--instance", token, "--read-only"]
        if mode == "lan":
            command += ["--lan-ip", lanIp]
        elif mode == "all":
            command += ["--allow-all"]
        try:
            self.server = self.launch(command, "gallery-" + self.instance_id + ".log")
            opener = build_opener(ProxyHandler({}))
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if self.server.poll() is not None:
                    raise RuntimeError("图库服务退出；详见 userData/logs/gallery-" + self.instance_id + ".log")
                try:
                    with opener.open(f"http://127.0.0.1:{port}/health.json", timeout=.5) as response:
                        health = json.load(response)
                    if health.get("instanceId") != token:
                        raise RuntimeError("端口属于另一个服务实例，拒绝连接")
                    self.service.update(status="ready", localUrl=f"http://127.0.0.1:{port}/",
                                        lanUrls=self.lan_urls(mode, port, lanIp))
                    self.changed()
                    return deepcopy(self.service)
                except (OSError, ValueError):
                    time.sleep(.1)
            raise RuntimeError("图库服务健康检查超时，未连接未确认身份的实例")
        except (OSError, RuntimeError) as error:
            self.owner.stop(self.server)
            self.server = None
            self.service.update(status="error", error=str(error), suggestedPort=self.available_port(port + 1))
            self.changed()
            return deepcopy(self.service)

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
        with self.lock:
            actions = {"state": lambda: self.state(), "add_source": self.add_source, "attach_job": self.attach_job,
                       "remove_source": lambda id: self.remove_source(id), "rescan": lambda id: self.rescan(id),
                       "configure_service": self.configure_service, "set_output": self.set_output,
                       "shutdown": lambda: self.shutdown()}
            if action not in actions:
                raise ValueError("不支持的命令")
            result = actions[action](**params)
            if action in {"add_source", "attach_job", "rescan"}:
                return {key: deepcopy(value) for key, value in result.items() if key != "jobs"}
            return result

    def background(self):
        last_check = 0
        while not self.closed.wait(.5):
            try:
                with self.lock:
                    self.poll_worker()
                    if self.server and self.server.poll() is not None:
                        self.server = None
                        self.service.update(status="error", localUrl=None, lanUrls=[], error="图库服务意外退出，请重新配置服务以重试")
                        self.changed()
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
        self.configure_service(**self.service_config)
        self.emit({"event": "ready", "instanceId": self.instance_id, "state": self.state()})
        self.thread = threading.Thread(target=self.background, name="desktop-library", daemon=True)
        self.thread.start()

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
    output_lock = threading.Lock()
    def emit(value):
        with output_lock:
            print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)
    controller = LibraryController(args.data, args.resources, args.node, args.instance, emit, own_process=True)
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
