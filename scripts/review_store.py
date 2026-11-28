# SPDX-License-Identifier: AGPL-3.0-only
"""Private review decisions and one-use cleanup claims; never deletes originals."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import threading
import time
import uuid

REVISION = Path("revisions") / "verified-birds-birder-v2"
RAW = {".arw", ".dng", ".nef", ".cr2", ".cr3", ".orf", ".raf", ".rw2"}
PHOTOS = {".jpg", ".jpeg", ".png"}
EXIT_ACTIONS = {"ask", "exit", "cleanup"}


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".part")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(10):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(.05)
    finally:
        temporary.unlink(missing_ok=True)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _natural(value):
    return tuple((1, int(part)) if part.isdigit() else (0, part.casefold())
                 for part in re.split(r"(\d+)", str(value)))


def _identity(path):
    # Lexical identity survives a missing file or a subsequently replaced link.
    return _hash(os.path.normcase(os.path.abspath(path)))


def _empty():
    return {"version": 1, "revision": 0, "preferences": {"exitAction": "ask"},
            "decisions": {}, "manualRevisionMembers": {}, "trashed": {}, "undo": []}


def _state(path):
    if not Path(path).exists():
        return _empty()
    value = _read(path)
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Unsupported review state schema; no state was overwritten")
    if type(value.get("revision")) is not int or value["revision"] < 0:
        raise ValueError("Invalid review state revision; no state was overwritten")
    result = {**_empty(), **value}
    if result["preferences"].get("exitAction") not in EXIT_ACTIONS:
        raise ValueError("Invalid review preferences")
    for field in ("decisions", "manualRevisionMembers", "trashed"):
        if not isinstance(result[field], dict):
            raise ValueError("Invalid review state " + field)
    if not isinstance(result["undo"], list):
        raise ValueError("Invalid review undo history")
    if any(type(row.get("selected")) is not bool for row in result["decisions"].values()):
        raise ValueError("Invalid manual review decision")
    return result


def _relative(value):
    value = str(value)
    path, windows = Path(value), PureWindowsPath(value)
    if path.is_absolute() or windows.anchor or ".." in path.parts or ".." in windows.parts or "\x00" in value:
        raise ValueError("Unregistered or escaping original path")
    return path


def _registered_file(photo, record, manifest, roots=None):
    if roots is None:
        roots = manifest.get("sourceRoots", [manifest.get("source")])
        roots = {os.path.normcase(os.path.abspath(root)) for root in roots if root}
    root = Path(record.get("root") or photo.get("sourceRoot") or manifest["source"]).absolute()
    if os.path.normcase(str(root)) not in roots:
        raise ValueError("Original root is not explicitly registered")
    relative = record.get("relative")
    if relative is None and record.get("path"):
        path = Path(record["path"])
        relative = path.relative_to(root) if path.is_absolute() else path
    relative = _relative(relative)
    candidate = root / relative
    if record.get("path") and Path(record["path"]).is_absolute() and \
            os.path.normcase(os.path.abspath(record["path"])) != os.path.normcase(str(candidate)):
        raise ValueError("Registered path and relative path disagree")
    return root, candidate


def _secure_stat(root, path, directories=None):
    root, path = Path(root).absolute(), Path(path).absolute()
    relative = path.relative_to(root)
    if not relative.parts:
        raise ValueError("Original must be a regular registered file")
    # Reject reparse points at every level, including registered root ancestors.
    for component in [*reversed(root.parents), root] + [root / Path(*relative.parts[:i])
                                                        for i in range(1, len(relative.parts) + 1)]:
        if directories is not None and component != path and component in directories:
            continue
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Original path contains a symlink or junction")
        if directories is not None and component != path:
            directories[component] = (info.st_dev, info.st_ino, info.st_mode,
                                      getattr(info, "st_file_attributes", 0))
    if not stat.S_ISREG(info.st_mode) or not path.resolve(strict=True).is_relative_to(root.resolve(strict=True)):
        raise ValueError("Original is not a regular file inside its registered root")
    return info


def _file_hash(root, path, record, expected_hash=None):
    before = _secure_stat(root, path)
    if type(record.get("size")) is not int or record.get("mtimeNs") is None or \
            (before.st_size, before.st_mtime_ns) != (record["size"], int(record["mtimeNs"])):
        raise ValueError("Original size/mtime differs from its manifest")
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != \
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
            raise ValueError("Original changed while opening")
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        after = os.fstat(stream.fileno())
    current = _secure_stat(root, path)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != \
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or \
            (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != \
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
        raise ValueError("Original changed during verification")
    sha = digest.hexdigest()
    if expected_hash and sha != expected_hash:
        raise ValueError("Original SHA256 differs from its registered analysis")
    return {"size": before.st_size, "mtimeNs": str(before.st_mtime_ns), "sha256": sha}


def _registry(library, progress=None):
    library = Path(library)
    manifest = _read(library / "manifest.json")
    if not isinstance(manifest.get("photos"), list) or not isinstance(manifest.get("groups"), list):
        raise ValueError("Invalid review manifest")
    gallery_path = library / REVISION / "gallery.json"
    gallery = _read(gallery_path) if gallery_path.exists() else {"items": []}
    details, enhanced = {}, {row["id"]: row for row in gallery.get("frames", [])}
    mapping_path = library / "mapping.json"
    mapping = _read(mapping_path).get("crops", []) if mapping_path.exists() else []
    fingerprints = {row["photoId"]: row["sourceSha256"] for row in mapping if row.get("sourceSha256")}
    report = progress or (lambda *args: None)
    for index, group in enumerate(manifest["groups"]):
        report("review-groups", index, len(manifest["groups"]))
        if not isinstance(group.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]+", group["id"]):
            raise ValueError("Invalid review group ID")
        for folder in (library / REVISION / "groups", library / "groups"):
            path = folder / (str(group["id"]) + ".json")
            if path.exists():
                detail = _read(path)
                details[group["id"]] = detail
                for frame in detail.get("frames", []):
                    enhanced[frame["id"]] = {**enhanced.get(frame["id"], {}), **frame}
                    if frame.get("sourceSha256"):
                        fingerprints[frame["id"]] = frame["sourceSha256"]
                break
    metadata = {}
    for name in ("capture-enriched-v2.json", "capture-exif-v1.json"):
        path = library / "revisions" / name
        if path.exists():
            metadata = _read(path).get("photos", {})
            break
    items = {}
    for item in gallery.get("items", []):
        items.setdefault(item.get("frameId"), []).append(item)
    frames = {}
    registered_roots = {os.path.normcase(os.path.abspath(root))
                        for root in manifest.get("sourceRoots", [manifest.get("source")]) if root}
    directories = {}
    for index, photo in enumerate(manifest["photos"]):
        report("review-photos", index, len(manifest["photos"]))
        frame_id = photo["id"]
        row = enhanced.get(frame_id, {})
        capture = row.get("capture") or photo.get("capture") or metadata.get(frame_id, {}).get("capture") or {}
        root, path = _registered_file(photo, photo["primary"], manifest, registered_roots)
        size = photo.get("encodedSize") or [None, None]
        width, height = row.get("width"), row.get("height")
        if not width or not height:
            width, height = size
            if photo.get("exifOrientation") in {5, 6, 7, 8}:
                width, height = height, width
        frame_items = items.get(frame_id, [])
        issue = bool(photo.get("sourceIssue")) or any(
            issue.get("sourceId") == photo.get("sourceId") and (
                issue.get("path") == photo["primary"].get("relative") or
                issue.get("kind") == "history-tail" and issue.get("groupId") == row.get("group"))
            for issue in gallery.get("sourceIssues", []))
        try:
            info = _secure_stat(root, path, directories)
            available = not issue and (info.st_size, info.st_mtime_ns) == \
                (photo["primary"].get("size"), int(photo["primary"].get("mtimeNs", -1)))
        except (OSError, ValueError, TypeError):
            available = False
        frames[frame_id] = {
            "id": frame_id, "identity": _identity(path), "photo": photo, "root": root, "path": path,
            "sourceSha256": fingerprints.get(frame_id) or photo.get("sourceSha256") or photo["primary"].get("sourceSha256"),
            "sourceIssue": issue, "width": width, "height": height, "capture": deepcopy(capture),
            "sourceLabel": photo["primary"].get("relative") or path.name,
            "supported": bool(photo.get("supported")), "available": available,
            "automatic": any(item.get("decision") == "keep" and
                             item.get("birdVerification", {}).get("status") == "bird" for item in frame_items),
            "items": frame_items,
        }
    # Batch-only ancestor memoization; mutations still perform full fresh stat/hash checks.
    for directory, expected in directories.items():
        info = directory.lstat()
        actual = (info.st_dev, info.st_ino, info.st_mode, getattr(info, "st_file_attributes", 0))
        if actual != expected or stat.S_ISLNK(info.st_mode) or actual[3] & 0x400:
            raise ValueError("Original directory changed during review initialization")
    report("review-photos", len(manifest["photos"]), len(manifest["photos"]))
    groups = []
    seen = set()
    for group in manifest["groups"]:
        members = [frames[frame_id] for frame_id in group.get("photoIds", []) if frame_id in frames]
        if len(members) != len(group.get("photoIds", [])) or group["id"] in seen:
            raise ValueError("Invalid review group membership")
        seen.add(group["id"])
        members.sort(key=_frame_order)
        revision = _hash({"members": sorted(((row["identity"], row["width"], row["height"], row["capture"],
                                            row["supported"], row["automatic"], row["photo"]["primary"],
                                            row["sourceSha256"], row["sourceIssue"]) for row in members),
                                           key=lambda row: row[0]),
                          "name": group.get("name"), "capture": group.get("capturedAt")})
        groups.append({"id": group["id"], "revision": revision,
                       "name": group.get("name") or group.get("capturedAt") or
                               (members[0]["sourceLabel"] if members else str(group["id"])),
                       "frames": members, "capture": members[0]["capture"] if members else {},
                       "sourceLabel": members[0]["sourceLabel"] if members else ""})
    groups.sort(key=lambda group: (_frame_order(group["frames"][0]) if group["frames"] else
                                  (2, "", (), ""), group["id"]))
    signature = _hash([(group["id"], group["revision"], [row["id"] for row in group["frames"]])
                       for group in groups])
    return groups, frames, manifest, signature


def _frame_order(frame):
    stamp = frame["capture"].get("time") or frame["photo"].get("capturedAt")
    if isinstance(stamp, str):
        try:
            # Camera-local ordering, not filesystem mtime or ranking.
            return (0, datetime.fromisoformat(stamp.replace("Z", "+00:00")).replace(tzinfo=None).isoformat(),
                    _natural(frame["sourceLabel"]), frame["identity"])
        except ValueError:
            pass
    timestamp = frame["photo"].get("timestamp")
    if isinstance(timestamp, (int, float)):
        return (0, datetime.fromtimestamp(timestamp / 1000, timezone.utc).replace(tzinfo=None).isoformat(),
                _natural(frame["sourceLabel"]), frame["identity"])
    return (1, "", _natural(frame["sourceLabel"]), frame["identity"])


def _snapshot(state, groups):
    reviewed = {member for members in state["manualRevisionMembers"].values() for member in members}
    output = []
    for group in groups:
        rows = []
        for frame in group["frames"]:
            decision = state["decisions"].get(frame["identity"])
            trashed = frame["identity"] in state["trashed"]
            rows.append({key: deepcopy(frame[key]) for key in
                         ("id", "identity", "sourceLabel", "width", "height", "capture", "supported", "automatic", "available")})
            rows[-1].update(selected=decision["selected"] if decision else frame["automatic"],
                            manual=decision is not None, available=frame["available"] and not trashed)
        manual_reviewed = any(frame["identity"] in reviewed for frame in group["frames"])
        cover_frame = next((frame for frame, row in zip(group["frames"], rows) if row["selected"] and frame["items"]),
                           next((frame for frame in group["frames"] if frame["items"]), None))
        cover = next((item.get("crop") for item in cover_frame["items"] if item.get("crop")), None) if cover_frame else None
        output.append({key: deepcopy(group[key]) for key in ("id", "revision", "name", "capture", "sourceLabel")})
        output[-1].update(frames=rows, manualReviewed=manual_reviewed,
                          newFramesPending=sum(frame["identity"] not in reviewed for frame in group["frames"])
                          if manual_reviewed else 0,
                          selectedCount=sum(row["selected"] for row in rows),
                          automaticCount=sum(row["automatic"] for row in rows),
                          cover={"crop": cover, "frameId": cover_frame["id"] if cover_frame else None})
    all_frames = [frame for group in output for frame in group["frames"]]
    return {"version": 1, "revision": state["revision"], "preferences": deepcopy(state["preferences"]),
            "groups": output, "summary": {
                "groupCount": len(output), "frameCount": len(all_frames),
                "selectedCount": sum(row["selected"] for row in all_frames),
                "automaticCount": sum(row["automatic"] for row in all_frames),
                "manualReviewedCount": sum(row["manualReviewed"] for row in output),
                "fullyReviewedCount": sum(row["manualReviewed"] and not row["newFramesPending"] for row in output),
                "newFramesPending": sum(row["newFramesPending"] for row in output),
                "availableCount": sum(row["available"] for row in all_frames),
                "trashedCount": len(state["trashed"])}}


def read_snapshot(library, state_path=None):
    """Standalone HTTP projection, with no state writes or mutation capability."""
    groups, _, _, _ = _registry(library)
    path = Path(state_path) if state_path else Path(library) / "review-state.json"
    return _snapshot(_state(path), groups)


class ReviewStore:
    def __init__(self, data, library):
        self.path = Path(data) / "review-state-v1.json"
        self.audit_path = Path(data) / "review-audit-v1.jsonl"
        self.library = Path(library)
        self.value = _state(self.path)
        self.disk_revision = self.value["revision"] if self.path.exists() else None
        self.groups, self.frames, self.manifest = [], {}, {}
        self.signature = None
        self.tokens = {}
        self.busy_sources = set()
        self.lock = threading.RLock()
        self.recovery = set()
        self.registry_error = None
        self.cache_path = Path(data) / "review-registry-cache-v1.json"
        if self.audit_path.exists():
            prepared = {}
            for line in self.audit_path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError as error:
                    raise ValueError("Review audit is incomplete; cleanup requires recovery") from error
                key = (row.get("token"), row.get("fileId"))
                if row.get("status") == "prepared":
                    prepared[key] = row
                elif row.get("status") in {"success", "failed"}:
                    previous = prepared.pop(key, None)
                    if row["status"] == "success" and previous:
                        self.value["trashed"][previous["identity"]] = {"frameId": previous["frameId"],
                                                                      "kind": previous["kind"]}
            self.recovery = {row["identity"] for row in prepared.values()}

    def refresh(self, busy_sources=None, progress=None):
        with self.lock:
            if busy_sources is not None:
                self.busy_sources = set(busy_sources)
            try:
                groups, frames, manifest, signature = _registry(self.library, progress=progress)
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.registry_error = str(error)
                if not self.signature:
                    raise
                for frame in self.frames.values():
                    frame["available"] = False
                return self.snapshot()
            self.registry_error = None
            if self.signature != signature:
                previous = self.value.get("viewSignature")
                if previous is not None and previous != signature:
                    self._commit({**deepcopy(self.value), "viewSignature": signature})
                self.signature = signature
            self.groups, self.frames, self.manifest = groups, frames, manifest
            return self.snapshot()

    def snapshot(self):
        with self.lock:
            return _snapshot(self.value, self.groups)

    def cache_registry(self, input_signature):
        with self.lock:
            frames = {key: {**value, "root": str(value["root"]), "path": str(value["path"])}
                      for key, value in self.frames.items()}
            groups = [{**{key: value for key, value in group.items() if key != "frames"},
                       "frameIds": [frame["id"] for frame in group["frames"]]} for group in self.groups]
            payload = {"version": 1, "inputSignature": input_signature,
                       "viewSignature": self.signature, "groups": groups, "frames": frames}
            _atomic(self.cache_path, {**payload, "payloadSha256": _hash(payload)})

    def publish_cached_projection(self):
        """Expose the previous completed view only; this never grants save/cleanup."""
        manifest = _read(self.library / "manifest.json")
        snapshot = _read(self.library / "review.json")
        if snapshot.get("version") != 1 or manifest.get("version") != 1:
            raise ValueError("Unsupported cached view schema")
        photos = {photo["id"]: photo for photo in manifest["photos"]}
        registered = {group["id"]: set(group["photoIds"]) for group in manifest["groups"]}
        seen, reviewed = set(), {member for members in self.value["manualRevisionMembers"].values() for member in members}
        for group in snapshot["groups"]:
            if registered.get(group["id"]) != {frame["id"] for frame in group["frames"]}:
                raise ValueError("Cached projection membership does not match its manifest")
            for frame in group["frames"]:
                if frame["id"] in seen:
                    raise ValueError("Duplicate cached photo")
                seen.add(frame["id"])
                photo = photos[frame["id"]]
                _, path = _registered_file(photo, photo["primary"], manifest)
                frame["identity"] = _identity(path)
                decision = self.value["decisions"].get(frame["identity"])
                frame["selected"] = decision["selected"] if decision else frame["automatic"]
                frame["manual"] = decision is not None
                if frame["identity"] in self.value["trashed"]:
                    frame["available"] = False
            group["manualReviewed"] = any(frame["identity"] in reviewed for frame in group["frames"])
            group["newFramesPending"] = sum(frame["identity"] not in reviewed for frame in group["frames"]) if group["manualReviewed"] else 0
            group["selectedCount"] = sum(frame["selected"] for frame in group["frames"])
        if seen != set(photos) or len(snapshot["groups"]) != len(registered):
            raise ValueError("Cached view is incomplete")
        snapshot["revision"] = self.value["revision"]
        snapshot["preferences"] = deepcopy(self.value["preferences"])
        frames = [frame for group in snapshot["groups"] for frame in group["frames"]]
        snapshot["summary"] = {"groupCount": len(snapshot["groups"]), "frameCount": len(frames),
                               "selectedCount": sum(frame["selected"] for frame in frames),
                               "automaticCount": sum(frame["automatic"] for frame in frames),
                               "manualReviewedCount": sum(group["manualReviewed"] for group in snapshot["groups"]),
                               "fullyReviewedCount": sum(group["manualReviewed"] and not group["newFramesPending"] for group in snapshot["groups"]),
                               "newFramesPending": sum(group["newFramesPending"] for group in snapshot["groups"]),
                               "availableCount": sum(frame["available"] for frame in frames),
                               "trashedCount": len(self.value["trashed"])}
        snapshot["cached"] = True
        _atomic(self.library / "review.json", snapshot)
        return True

    def restore_registry(self, input_signature):
        if not self.cache_path.exists():
            return False
        cached = _read(self.cache_path)
        if not isinstance(cached, dict) or cached.get("version") != 1:
            raise ValueError("Unsupported review cache schema; original state is unchanged")
        if cached.get("inputSignature") != input_signature:
            return False
        if cached.get("payloadSha256") != _hash({key: value for key, value in cached.items() if key != "payloadSha256"}):
            raise ValueError("Cached review payload checksum mismatch")
        manifest = _read(self.library / "manifest.json")
        photos = {photo["id"]: photo for photo in manifest["photos"]}
        frames = {}
        for key, value in cached["frames"].items():
            photo = photos.get(key)
            if photo != value["photo"] or key != value["id"]:
                raise ValueError("Cached photo does not match its registered manifest")
            root, path = _registered_file(photo, photo["primary"], manifest)
            if value["root"] != str(root) or value["path"] != str(path) or value["identity"] != _identity(path):
                raise ValueError("Cached original path is not registered")
            if any(type(value.get(field)) is not bool for field in ("automatic", "available", "supported", "sourceIssue")) or \
                    not isinstance(value.get("items"), list):
                raise ValueError("Invalid cached photo state")
            frames[key] = {**value, "root": root, "path": path}
        registered = {group["id"]: set(group["photoIds"]) for group in manifest["groups"]}
        groups = []
        for group in cached["groups"]:
            if registered.get(group["id"]) != set(group["frameIds"]) or len(group["frameIds"]) != len(set(group["frameIds"])):
                raise ValueError("Cached group membership changed")
            groups.append({**{key: value for key, value in group.items() if key != "frameIds"},
                           "frames": [frames[key] for key in group["frameIds"]]})
        signature = _hash([(group["id"], group["revision"], [frame["id"] for frame in group["frames"]]) for group in groups])
        if signature != cached["viewSignature"] or len(groups) != len(registered):
            raise ValueError("Cached review signature changed")
        self.frames, self.groups, self.manifest, self.signature = frames, groups, manifest, signature
        return True

    def validate_cached_sources(self, progress=None):
        directories = {}
        with self.lock:
            for index, frame in enumerate(self.frames.values()):
                if progress:
                    progress("review-photos", index, len(self.frames))
                try:
                    info = _secure_stat(frame["root"], frame["path"], directories)
                    primary = frame["photo"]["primary"]
                    frame["available"] = not frame["sourceIssue"] and (info.st_size, info.st_mtime_ns) == (
                        primary["size"], int(primary["mtimeNs"]))
                except (OSError, ValueError, TypeError, KeyError):
                    frame["available"] = False
            for directory, expected in directories.items():
                info = directory.lstat()
                if (info.st_dev, info.st_ino, info.st_mode, getattr(info, "st_file_attributes", 0)) != expected:
                    raise ValueError("Original directory changed during cached review validation")
            if progress:
                progress("review-photos", len(self.frames), len(self.frames))

    def publish(self, cached=False):
        snapshot = self.snapshot()
        if cached:
            snapshot["cached"] = True
        _atomic(self.library / "review.json", snapshot)
        return snapshot

    def _expected(self, revision):
        if type(revision) is not int or revision != self.value["revision"]:
            raise ValueError("Review revision conflict; reload review_state")
        live = _state(self.path) if self.path.exists() else None
        if (live["revision"] if live else None) != self.disk_revision:
            raise ValueError("Review revision conflict on disk; reload controller")

    def _commit(self, value):
        self._expected(self.value["revision"])
        value["revision"] = self.value["revision"] + 1
        value["viewSignature"] = value.get("viewSignature", self.signature)
        _atomic(self.path, value)
        self.value, self.disk_revision = value, value["revision"]

    def _group(self, group_id):
        group = next((group for group in self.groups if group["id"] == group_id), None)
        if not group:
            raise ValueError("Unknown registered review group")
        return group

    def decide(self, expectedRevision, groupId, changes, action="set"):
        with self.lock:
            self.refresh()
            self._expected(expectedRevision)
            group = self._group(groupId)
            if action not in {"set", "restoreAutomatic", "undo"} or not isinstance(changes, list):
                raise ValueError("Invalid review decision action")
            members = {frame["id"]: frame for frame in group["frames"]}
            seen = set()
            for change in changes:
                if not isinstance(change, dict) or set(change) != {"frameId", "selected"} or \
                        change["frameId"] not in members or type(change["selected"]) is not bool or change["frameId"] in seen:
                    raise ValueError("Changes must contain unique registered frame IDs and boolean selections")
                seen.add(change["frameId"])
            value = deepcopy(self.value)
            identities = [frame["identity"] for frame in group["frames"]]
            before = {identity: deepcopy(value["decisions"].get(identity)) for identity in identities}
            old_selected = {frame["identity"]: (before[frame["identity"]]["selected"]
                            if before[frame["identity"]] else frame["automatic"]) for frame in group["frames"]}
            if action == "undo":
                if changes:
                    raise ValueError("Undo does not accept arbitrary changes")
                index = next((i for i in range(len(value["undo"]) - 1, -1, -1)
                              if value["undo"][i]["groupId"] == groupId or
                              set(value["undo"][i]["members"]) == set(identities)), None)
                if index is None:
                    return self.snapshot()
                previous = value["undo"].pop(index)["decisions"]
                for identity in identities:
                    if identity not in previous:
                        continue
                    if previous[identity] is None:
                        value["decisions"].pop(identity, None)
                    else:
                        value["decisions"][identity] = previous[identity]
            elif action == "restoreAutomatic":
                for identity in identities:
                    value["decisions"].pop(identity, None)
            else:
                for change in changes:
                    frame = members[change["frameId"]]
                    if old_selected[frame["identity"]] != change["selected"]:
                        value["decisions"][frame["identity"]] = {"selected": change["selected"]}
            after = {identity: value["decisions"].get(identity) for identity in identities}
            if before == after:
                return self.snapshot()
            changed = any(old_selected[frame["identity"]] != (
                after[frame["identity"]]["selected"] if after[frame["identity"]] else frame["automatic"])
                for frame in group["frames"])
            if changed:
                existing = set(value["manualRevisionMembers"].get(groupId, []))
                value["manualRevisionMembers"][groupId] = sorted(existing | set(identities))
            if action != "undo":
                value["undo"].append({"groupId": groupId, "members": identities, "decisions": before})
                value["undo"] = value["undo"][-100:]
            self._commit(value)
            return self.publish()

    def save(self, expectedRevision, groups):
        """Commit an explicit draft batch against the already registered view."""
        with self.lock:
            self._expected(expectedRevision)
            if self.registry_error or not self.signature:
                raise ValueError("Review registry unavailable; keep the unsaved draft and retry")
            if not isinstance(groups, list) or not groups or len(groups) > 2000:
                raise ValueError("Invalid review save batch")
            registered = {group["id"]: group for group in self.groups}
            seen_groups, seen_frames = set(), set()
            value = deepcopy(self.value)
            total = 0
            for entry in groups:
                if not isinstance(entry, dict) or set(entry) != {"groupId", "groupRevision", "changes"}:
                    raise ValueError("Invalid review save group")
                group = registered.get(entry["groupId"])
                if not group or group["id"] in seen_groups:
                    raise ValueError("Unknown or duplicate registered review group")
                if entry["groupRevision"] != group["revision"]:
                    raise ValueError("Review group revision conflict; inspect changed or new frames")
                seen_groups.add(group["id"])
                changes = entry["changes"]
                if not isinstance(changes, list):
                    raise ValueError("Invalid review changes")
                total += len(changes)
                if total > 20000:
                    raise ValueError("Review save batch is too large")
                members = {frame["id"]: frame for frame in group["frames"]}
                before = {frame["identity"]: deepcopy(value["decisions"].get(frame["identity"]))
                          for frame in group["frames"]}
                for change in changes:
                    if not isinstance(change, dict) or set(change) != {
                            "frameId", "identity", "selected", "manual", "baseSelected", "baseManual"}:
                        raise ValueError("Invalid review draft change")
                    frame = members.get(change["frameId"])
                    if not frame or change["identity"] != frame["identity"] or frame["identity"] in seen_frames or any(
                            type(change[key]) is not bool for key in ("selected", "manual", "baseSelected", "baseManual")):
                        raise ValueError("Draft must use unique registered photo identities and boolean states")
                    if not frame["available"] or frame["sourceIssue"]:
                        raise ValueError("Original changed or unavailable; keep draft and inspect the source")
                    seen_frames.add(frame["identity"])
                    previous = value["decisions"].get(frame["identity"])
                    selected = previous["selected"] if previous else frame["automatic"]
                    if (selected, previous is not None) != (change["baseSelected"], change["baseManual"]):
                        raise ValueError("Review selection conflict; keep draft and inspect this photo")
                    if change["manual"]:
                        value["decisions"][frame["identity"]] = {"selected": change["selected"]}
                    elif change["selected"] == frame["automatic"]:
                        value["decisions"].pop(frame["identity"], None)
                    else:
                        raise ValueError("An automatic state must match the registered recommendation")
                identities = [frame["identity"] for frame in group["frames"]]
                existing = set(value["manualRevisionMembers"].get(group["id"], []))
                value["manualRevisionMembers"][group["id"]] = sorted(existing | set(identities))
                if before != {identity: value["decisions"].get(identity) for identity in identities}:
                    value["undo"].append({"groupId": group["id"], "members": identities, "decisions": before})
            value["undo"] = value["undo"][-100:]
            if value != self.value:
                self._commit(value)
            return self.publish()

    def preferences(self, expectedRevision, exitAction):
        with self.lock:
            self.refresh()
            self._expected(expectedRevision)
            if exitAction not in EXIT_ACTIONS:
                raise ValueError("Invalid review exit action")
            if self.value["preferences"]["exitAction"] != exitAction:
                value = deepcopy(self.value)
                value["preferences"]["exitAction"] = exitAction
                self._commit(value)
            return self.publish()

    def _stable_group(self, group):
        if self.registry_error:
            raise ValueError("Review manifest unavailable: " + self.registry_error)
        for frame in group["frames"]:
            if frame["photo"].get("sourceId") in self.busy_sources:
                raise ValueError("Source is queued or processing")
            if frame["sourceIssue"] or frame["identity"] in self.recovery:
                raise ValueError("Source issue or unresolved prepared cleanup")
            if frame["identity"] in self.value["trashed"]:
                continue
            _file_hash(frame["root"], frame["path"], frame["photo"]["primary"], frame["sourceSha256"])

    @staticmethod
    def _supported_primary(frame):
        return frame["supported"] and frame["path"].suffix.lower() in PHOTOS and \
            not frame["photo"]["primary"].get("blocked")

    def cleanup_preview(self, expectedRevision, includeRaw=False, includeZero=False):
        with self.lock:
            self.refresh()
            self._expected(expectedRevision)
            if type(includeRaw) is not bool or type(includeZero) is not bool:
                raise ValueError("Cleanup options must be explicit booleans")
            snapshot = self.snapshot()
            token = uuid.uuid4().hex
            plan = {"revision": snapshot["revision"], "expires": time.monotonic() + 120,
                    "expiresAt": int((time.time() + 120) * 1000), "files": {}, "groups": {},
                    "includeZero": includeZero, "includeRaw": includeRaw, "cancelled": False}
            skipped, public = [], []
            keep_count = photo_count = raw_count = zero_skipped = new_skipped = unstable_skipped = 0
            for row in snapshot["groups"]:
                reason = None
                group = self._group(row["id"])
                if not any(self._supported_primary(frame) for frame in group["frames"]):
                    reason = "rawOnly" if group["frames"] and all(
                        frame["path"].suffix.lower() in RAW for frame in group["frames"]) else "unsupportedOriginal"
                elif not row["manualReviewed"]:
                    reason = "notReviewed"
                elif row["newFramesPending"]:
                    reason = "newFramesPending"
                    new_skipped += 1
                elif not row["selectedCount"] and not includeZero:
                    reason = "zeroSelected"
                    zero_skipped += 1
                if reason is None:
                    try:
                        self._stable_group(group)
                    except (OSError, ValueError) as error:
                        reason = "unstable: " + str(error)
                        unstable_skipped += 1
                if reason:
                    skipped.append({"groupId": row["id"], "reason": reason})
                    continue
                plan["groups"][row["id"]] = group["revision"]
                keep_count += row["selectedCount"]
                for frame, decision in zip(group["frames"], row["frames"]):
                    if decision["selected"] or frame["identity"] in self.value["trashed"]:
                        continue
                    if not self._supported_primary(frame):
                        skipped.append({"groupId": row["id"], "reason": "unsupportedOriginal"})
                        continue
                    candidates = [("primary", frame["photo"]["primary"])]
                    if includeRaw:
                        for raw in frame["photo"].get("companions", []):
                            try:
                                root, path = _registered_file(frame["photo"], raw, self.manifest)
                                if root != frame["root"] or path.parent != frame["path"].parent or \
                                        path.stem.casefold() != frame["path"].stem.casefold() or path.suffix.lower() not in RAW:
                                    raise ValueError("Not a registered same-stem RAW companion")
                                candidates.append(("raw", raw))
                            except (ValueError, TypeError):
                                continue
                    for kind, record in candidates:
                        try:
                            root, path = _registered_file(frame["photo"], record, self.manifest)
                            identity = _identity(path)
                            if identity in self.value["trashed"] or identity in self.recovery:
                                continue
                            fingerprint = _file_hash(root, path, record,
                                                     frame["sourceSha256"] if kind == "primary" else record.get("sourceSha256"))
                        except (OSError, ValueError) as error:
                            skipped.append({"groupId": row["id"], "reason": "unstableFile: " + str(error)})
                            unstable_skipped += 1
                            continue
                        file_id = uuid.uuid4().hex
                        item = {"id": file_id, "frameId": frame["id"], "groupId": row["id"],
                                "name": path.name, "kind": kind}
                        plan["files"][file_id] = {**item, **fingerprint, "path": path, "root": root,
                                                  "identity": identity, "record": deepcopy(record), "status": "pending"}
                        public.append(item)
                        photo_count += kind == "primary"
                        raw_count += kind == "raw"
            self.tokens[token] = plan
            return {"token": token, "revision": plan["revision"], "expiresAt": plan["expiresAt"],
                    "groups": [{"groupId": group_id, "revision": revision} for group_id, revision in plan["groups"].items()],
                    "keepCount": keep_count, "photoCount": photo_count, "rawCount": raw_count,
                    "zeroSkipped": zero_skipped, "newFramesSkipped": new_skipped,
                    "unstableSkipped": unstable_skipped, "files": public, "skipped": skipped}

    def _plan(self, token, pending=True):
        plan = self.tokens.get(token) if isinstance(token, str) else None
        if not plan:
            raise ValueError("Unknown cleanup token")
        if pending and (plan["cancelled"] or time.monotonic() >= plan["expires"]):
            raise ValueError("Cleanup token cancelled or expired")
        return plan

    def _audit(self, token, file, status, error=None):
        row = {"version": 1, "at": datetime.now(timezone.utc).isoformat(), "token": token,
               "fileId": file["id"], "frameId": file["frameId"], "groupId": file["groupId"],
               "identity": file["identity"], "kind": file["kind"], "status": status,
               "revision": self.value["revision"]}
        if error:
            row["error"] = str(error)[:2000]
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def cleanup_claim(self, token, fileId):
        with self.lock:
            self.refresh()
            plan = self._plan(token)
            self._expected(plan["revision"])
            file = plan["files"].get(fileId)
            if not file or file["status"] != "pending":
                raise ValueError("Unknown or already prepared cleanup file")
            group = self._group(file["groupId"])
            frame = next((frame for frame in group["frames"] if frame["id"] == file["frameId"]), None)
            if not frame or not self._supported_primary(frame):
                raise ValueError("Only supported JPEG/PNG primaries and their paired RAW companions may be cleaned")
            root, path = _registered_file(frame["photo"], file["record"], self.manifest)
            if root != file["root"] or path != file["path"] or root != frame["root"]:
                raise ValueError("Cleanup file is not registered to its source frame")
            if file["kind"] == "primary":
                if path != frame["path"] or file["record"] != frame["photo"]["primary"]:
                    raise ValueError("Cleanup primary does not match its registered JPEG/PNG frame")
            elif file["kind"] == "raw":
                if not plan.get("includeRaw") or file["record"] not in frame["photo"].get("companions", []) or \
                        path.parent != frame["path"].parent or path.stem.casefold() != frame["path"].stem.casefold() or \
                        path.suffix.lower() not in RAW:
                    raise ValueError("Cleanup RAW is not an explicitly authorized registered same-stem companion")
            else:
                raise ValueError("Unknown cleanup file kind")
            row = next(row for row in self.snapshot()["groups"] if row["id"] == group["id"])
            if group["revision"] != plan["groups"][group["id"]] or not row["manualReviewed"] or \
                    row["newFramesPending"] or not row["selectedCount"] and not plan["includeZero"]:
                raise ValueError("Cleanup group revision or authorization changed")
            if next(decision for decision in row["frames"] if decision["id"] == frame["id"])["selected"]:
                raise ValueError("Selected source frame cannot be cleaned")
            self._stable_group(group)
            current = _file_hash(file["root"], file["path"], file["record"], file["sha256"])
            if current != {key: file[key] for key in ("size", "mtimeNs", "sha256")}:
                raise ValueError("Cleanup file changed")
            # Once prepared, neither an audit failure nor a crash permits a retry.
            file["status"] = "prepared"
            self.recovery.add(file["identity"])
            self._audit(token, file, "prepared")
            return {key: str(file[key]) if key in {"path", "root"} else file[key] for key in
                    ("id", "path", "root", "size", "mtimeNs", "sha256", "frameId", "kind")}

    def cleanup_result(self, token, fileId, success, error=None):
        with self.lock:
            plan = self._plan(token, pending=False)
            file = plan["files"].get(fileId)
            if type(success) is not bool or not file or file["status"] != "prepared":
                raise ValueError("Only a prepared file accepts one explicit cleanup result")
            file["status"] = "success" if success else "failed"
            self._audit(token, file, file["status"], error)
            self.recovery.discard(file["identity"])
            previous_revision = self.value["revision"]
            value = deepcopy(self.value)
            if success:
                value["trashed"][file["identity"]] = {"frameId": file["frameId"], "kind": file["kind"]}
            self._commit(value)
            if plan["revision"] == previous_revision:
                plan["revision"] = self.value["revision"]
            return {"snapshot": self.publish(), "successCount": sum(row["status"] == "success" for row in plan["files"].values()),
                    "failedCount": sum(row["status"] == "failed" for row in plan["files"].values()),
                    "pendingCount": sum(row["status"] == "pending" for row in plan["files"].values())}

    def cleanup_cancel(self, token):
        with self.lock:
            plan = self._plan(token, pending=False)
            plan["cancelled"] = True
            return {"cancelled": True, "revision": self.value["revision"],
                    "pendingCount": sum(row["status"] == "pending" for row in plan["files"].values())}
