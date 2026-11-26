# SPDX-License-Identifier: AGPL-3.0-only
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from desktop_backend import LibraryController, aggregate_library
from review_store import REVISION, ReviewStore, _atomic, _read, read_snapshot

OPENER = build_opener(ProxyHandler({}))


class ReviewStoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=ROOT / ".tools", prefix="review-fixture-")
        self.root = Path(self.folder.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.library = self.root / "library"
        self.library.mkdir()
        self.manifest = {"source": str(self.source), "photos": [], "groups": []}
        self.gallery = {"version": 1, "status": "completed", "items": []}
        self.controllers = []

    def tearDown(self):
        for controller in self.controllers:
            controller.shutdown()
        self.folder.cleanup()

    def photo(self, name, frame_id=None, group="g00001", keep=False, captured=None):
        frame_id = frame_id or "p%06d" % (len(self.manifest["photos"]) + 1)
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (72, 48), "green").save(path)
        info = path.stat()
        photo = {"id": frame_id, "supported": True, "encodedSize": [72, 48], "exifOrientation": 1,
                 "capturedAt": captured, "primary": {
                     "relative": name, "size": info.st_size, "mtimeNs": info.st_mtime_ns, "format": ".jpg"},
                 "companions": []}
        self.manifest["photos"].append(photo)
        target = next((row for row in self.manifest["groups"] if row["id"] == group), None)
        if target is None:
            target = {"id": group, "photoIds": []}
            self.manifest["groups"].append(target)
        target["photoIds"].append(frame_id)
        self.gallery["items"].append({"id": frame_id + "_b1", "frameId": frame_id, "groupId": group,
                                      "instanceId": 1, "crop": "crops/" + frame_id + ".jpg",
                                      "decision": "keep" if keep else "reject",
                                      "birdVerification": {"status": "bird"}})
        return photo

    def raw(self, photo, name):
        path = self.source / name
        path.write_bytes(b"fixture RAW bytes")
        info = path.stat()
        photo["companions"].append({"relative": name, "size": info.st_size, "mtimeNs": info.st_mtime_ns,
                                     "format": path.suffix.lower()})
        return path

    def save(self):
        _atomic(self.library / "manifest.json", self.manifest)
        _atomic(self.library / "mapping.json", {"crops": []})
        _atomic(self.library / REVISION / "gallery.json", self.gallery)

    def store(self):
        self.save()
        store = ReviewStore(self.root / "data", self.library)
        store.refresh()
        return store

    def decide(self, store, frame_id, selected, group="g00001", **kwargs):
        return store.decide(store.snapshot()["revision"], group, [{"frameId": frame_id, "selected": selected}], **kwargs)

    def review_group(self, store, frame_id="p000001"):
        self.decide(store, frame_id, True)
        return self.decide(store, frame_id, False)

    def test_decisions_are_identity_based_across_reorder_ids_and_same_basename(self):
        self.photo("north/same.jpg", keep=True)
        self.photo("south/same.jpg", keep=False)
        store = self.store()
        self.decide(store, "p000001", False)
        self.manifest["photos"].reverse()
        for photo in self.manifest["photos"]:
            photo["id"] = "p000010" if photo["primary"]["relative"].startswith("north") else "p000011"
        self.manifest["groups"][0]["photoIds"] = ["p000011", "p000010"]
        self.gallery["items"] = []
        self.save()
        snapshot = store.refresh()
        north = next(frame for frame in snapshot["groups"][0]["frames"] if frame["sourceLabel"].startswith("north"))
        south = next(frame for frame in snapshot["groups"][0]["frames"] if frame["sourceLabel"].startswith("south"))
        self.assertTrue(north["manual"])
        self.assertFalse(north["selected"])
        self.assertFalse(south["manual"])
        restarted = ReviewStore(self.root / "data", self.library)
        self.assertEqual(restarted.refresh(), snapshot)

    def test_incremental_new_frames_group_revision_and_manual_coverage(self):
        self.photo("a.jpg", keep=True)
        self.photo("b.jpg")
        store = self.store()
        self.decide(store, "p000001", False)
        initial = store.snapshot()
        self.assertTrue(initial["groups"][0]["manualReviewed"])
        self.assertEqual(initial["groups"][0]["newFramesPending"], 0)
        self.photo("c.jpg")
        self.save()
        current = store.refresh()
        self.assertNotEqual(initial["groups"][0]["revision"], current["groups"][0]["revision"])
        self.assertGreater(current["revision"], initial["revision"])
        self.assertEqual(current["groups"][0]["newFramesPending"], 1)
        self.assertFalse(current["groups"][0]["frames"][0]["selected"])
        plan = store.cleanup_preview(current["revision"], includeZero=True)
        self.assertEqual(plan["newFramesSkipped"], 1)
        self.assertFalse(plan["files"])
        selected = self.decide(store, "p000002", True)
        self.assertEqual(selected["groups"][0]["newFramesPending"], 0)
        self.manifest["groups"][0]["id"] = "g00009"
        self.save()
        renamed = store.refresh()["groups"][0]
        self.assertTrue(renamed["manualReviewed"])
        self.assertEqual(renamed["newFramesPending"], 0)
        self.assertTrue(next(frame for frame in renamed["frames"] if frame["id"] == "p000002")["selected"])

    def test_multi_keep_restore_undo_noop_and_ever_reviewed(self):
        self.photo("a.jpg", keep=True)
        self.photo("b.jpg")
        self.gallery["items"].append({**self.gallery["items"][1], "id": "second-bird",
                                      "decision": "keep", "rank": 100})
        store = self.store()
        self.assertEqual(store.snapshot()["groups"][0]["automaticCount"], 2)
        self.decide(store, "p000001", True)
        self.assertFalse(store.path.exists())
        self.assertFalse(store.snapshot()["groups"][0]["manualReviewed"])
        changed = store.decide(0, "g00001", [{"frameId": "p000001", "selected": False},
                                             {"frameId": "p000002", "selected": False}])
        self.assertEqual(changed["groups"][0]["selectedCount"], 0)
        restored = store.decide(changed["revision"], "g00001", [], action="restoreAutomatic")
        self.assertEqual(restored["groups"][0]["selectedCount"], 2)
        self.assertTrue(restored["groups"][0]["manualReviewed"])
        undone = store.decide(restored["revision"], "g00001", [], action="undo")
        self.assertEqual(undone["groups"][0]["selectedCount"], 0)
        self.assertTrue(undone["groups"][0]["manualReviewed"])
        self.assertEqual(self.gallery, _read(self.library / REVISION / "gallery.json"))

    def test_schema_revision_validation_and_atomic_failure_preserve_state(self):
        self.photo("a.jpg")
        store = self.store()
        snapshot = self.decide(store, "p000001", True)
        before = store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "revision conflict"):
            store.preferences(0, "exit")
        with self.assertRaises(ValueError):
            store.decide(snapshot["revision"], "g00001", [{"frameId": "p000001", "selected": 1}])
        with patch("review_store._atomic", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                store.preferences(snapshot["revision"], "cleanup")
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(store.snapshot()["preferences"]["exitAction"], "ask")
        _atomic(store.path, {"version": 99, "revision": 0})
        unknown = store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "schema"):
            ReviewStore(self.root / "data", self.library)
        with self.assertRaisesRegex(ValueError, "schema"):
            store.preferences(snapshot["revision"], "exit")
        self.assertEqual(store.path.read_bytes(), unknown)

    def test_two_instances_detect_disk_conflict_and_preferences_restart(self):
        self.photo("a.jpg")
        first = self.store()
        second = ReviewStore(self.root / "data", self.library)
        second.refresh()
        first.preferences(0, "cleanup")
        with self.assertRaisesRegex(ValueError, "revision conflict"):
            second.preferences(0, "exit")
        restarted = ReviewStore(self.root / "data", self.library)
        self.assertEqual(restarted.refresh()["preferences"]["exitAction"], "cleanup")

    def test_uncropped_frames_sort_camera_then_natural_and_oriented_dimensions(self):
        self.photo("10.jpg", group="g00010")
        self.photo("2.jpg", group="g00002")
        timed = self.photo("z.jpg", group="g00003", captured="2026-11-25T10:00:00")
        self.photo("a.jpg", group="g00003", captured="2026-11-25T10:00:02")
        timed["exifOrientation"] = 6
        self.gallery["items"] = []
        store = self.store()
        snapshot = store.snapshot()
        self.assertEqual([group["id"] for group in snapshot["groups"]], ["g00003", "g00002", "g00010"])
        first = snapshot["groups"][0]
        self.assertEqual([frame["sourceLabel"] for frame in first["frames"]], ["z.jpg", "a.jpg"])
        self.assertEqual((first["frames"][0]["width"], first["frames"][0]["height"]), (48, 72))
        self.assertEqual(first["cover"], {"crop": None, "frameId": None})
        self.assertFalse(store.path.exists())
        self.assertFalse((self.library / "review-state.json").exists())
        self.assertEqual(read_snapshot(self.library), snapshot)

    def test_aggregate_retains_unprocessed_and_no_crop_groups_with_detail_routes(self):
        self.photo("a.jpg")
        self.photo("b.jpg")
        self.photo("c.jpg", group="g00002")
        self.gallery["items"] = []
        self.save()
        _atomic(self.library / "groups" / "g00001.json",
                {"group": deepcopy(self.manifest["groups"][0]),
                 "frames": [{"id": "p000001", "width": 72, "height": 48, "instances": []}]})
        aggregate = self.root / "aggregate"
        gallery = aggregate_library(aggregate, [{"id": "source", "name": "source", "path": str(self.source),
                                                "status": "completed", "jobs": [{"path": str(self.library)}]}])
        snapshot = read_snapshot(aggregate)
        self.assertEqual(len(snapshot["groups"]), 2)
        self.assertEqual(snapshot["summary"]["frameCount"], 3)
        self.assertFalse(gallery["items"])
        for group in snapshot["groups"]:
            detail = _read(aggregate / REVISION / "groups" / (group["id"] + ".json"))
            self.assertEqual(len(detail["frames"]), len(group["frames"]))

    def test_cleanup_defaults_raw_zero_new_frames_and_no_renderer_paths(self):
        primary = self.photo("a.jpg", keep=True)
        self.raw(primary, "a.nef")
        self.photo("b.jpg")
        store = self.store()
        self.decide(store, "p000002", True)
        self.decide(store, "p000002", False)
        plan = store.cleanup_preview(store.snapshot()["revision"])
        self.assertEqual((plan["photoCount"], plan["rawCount"], plan["keepCount"]), (1, 0, 1))
        self.assertEqual(plan["files"][0]["name"], "b.jpg")
        self.assertNotIn(str(self.root), json.dumps(plan))
        self.assertNotIn(str(self.root), json.dumps(store.snapshot()))
        self.decide(store, "p000001", False)
        skipped = store.cleanup_preview(store.snapshot()["revision"])
        self.assertEqual(skipped["zeroSkipped"], 1)
        self.assertFalse(skipped["files"])
        authorized = store.cleanup_preview(store.snapshot()["revision"], includeRaw=True, includeZero=True)
        self.assertEqual((authorized["photoCount"], authorized["rawCount"]), (2, 1))
        self.assertRegex(authorized["token"], r"^[A-Za-z0-9_-]{16,128}$")

    def test_raw_only_primary_is_skipped_even_with_both_cleanup_authorizations(self):
        photo = self.photo("raw-only.jpg")
        path = self.source / "raw-only.jpg"
        path.rename(self.source / "raw-only.nef")
        photo["primary"].update(relative="raw-only.nef", format=".nef")
        photo["supported"] = False
        store = self.store()
        self.review_group(store)
        default = store.cleanup_preview(store.snapshot()["revision"], includeZero=True)
        self.assertFalse(default["files"])
        authorized = store.cleanup_preview(store.snapshot()["revision"], includeRaw=True, includeZero=True)
        self.assertFalse(authorized["files"])
        self.assertFalse(authorized["groups"])
        self.assertEqual((authorized["photoCount"], authorized["rawCount"]), (0, 0))
        self.assertEqual(authorized["skipped"], [{"groupId": "g00001", "reason": "rawOnly"}])
        self.assertTrue((self.source / "raw-only.nef").is_file())

    def test_cleanup_claim_independently_rejects_raw_only_and_unsupported_primary_candidates(self):
        self.photo("keep.jpg", keep=True)
        self.photo("reject.jpg")
        for name, group in (("raw-only.nef", "g00002"), ("unsupported.tiff", "g00003")):
            photo = self.photo(name + ".jpg", group=group)
            (self.source / (name + ".jpg")).rename(self.source / name)
            photo["primary"].update(relative=name, format=Path(name).suffix)
            photo["supported"] = False
        store = self.store()
        self.review_group(store, "p000002")
        self.decide(store, "p000003", True, group="g00002")
        self.decide(store, "p000003", False, group="g00002")
        self.decide(store, "p000004", True, group="g00003")
        self.decide(store, "p000004", False, group="g00003")
        preview = store.cleanup_preview(store.snapshot()["revision"], includeRaw=True, includeZero=True)
        self.assertEqual([row["name"] for row in preview["files"]], ["reject.jpg"])
        self.assertIn({"groupId": "g00003", "reason": "unsupportedOriginal"}, preview["skipped"])
        plan = store.tokens[preview["token"]]
        template = deepcopy(plan["files"][preview["files"][0]["id"]])
        for frame_id, group_id, kind in (("p000003", "g00002", "raw"), ("p000004", "g00003", "primary")):
            frame = store.frames[frame_id]
            forged = {**template, "id": frame_id, "frameId": frame_id, "groupId": group_id, "kind": kind,
                      "root": frame["root"], "path": frame["path"], "record": deepcopy(frame["photo"]["primary"])}
            plan["files"][frame_id] = forged
            plan["groups"][group_id] = store._group(group_id)["revision"]
            with self.assertRaisesRegex(ValueError, "supported JPEG/PNG"):
                store.cleanup_claim(preview["token"], frame_id)
        self.assertFalse(store.audit_path.exists())

    def test_cleanup_rejects_busy_source_changed_bytes_stamp_and_source_issue(self):
        self.photo("a.jpg", keep=True)
        second = self.photo("b.jpg")
        store = self.store()
        self.review_group(store, "p000002")
        revision = store.snapshot()["revision"]
        plan = store.cleanup_preview(revision)
        path = self.source / "b.jpg"
        previous = path.stat()
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)
        os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, "SHA256"):
            store.cleanup_claim(plan["token"], plan["files"][0]["id"])
        _atomic(self.library / "mapping.json", {"crops": [{"photoId": second["id"],
                 "sourceSha256": hashlib.sha256(b"old source").hexdigest()}]})
        store.refresh()
        skipped = store.cleanup_preview(store.snapshot()["revision"])
        self.assertEqual(skipped["unstableSkipped"], 1)
        self.assertFalse(skipped["files"])
        second["sourceIssue"] = True
        self.save()
        store.refresh()
        self.assertEqual(store.cleanup_preview(store.snapshot()["revision"])["unstableSkipped"], 1)
        second.pop("sourceIssue")
        second["sourceId"] = "busy"
        self.save()
        store.refresh({"busy"})
        self.assertEqual(store.cleanup_preview(store.snapshot()["revision"])["unstableSkipped"], 1)

    def test_cleanup_claim_rejects_revision_cancel_expiry_and_processing_race(self):
        self.photo("a.jpg", keep=True)
        second = self.photo("b.jpg")
        second["sourceId"] = "source"
        store = self.store()
        self.review_group(store, "p000002")
        plan = store.cleanup_preview(store.snapshot()["revision"])
        store.busy_sources = {"source"}
        with self.assertRaisesRegex(ValueError, "processing"):
            store.cleanup_claim(plan["token"], plan["files"][0]["id"])
        store.busy_sources.clear()
        store.preferences(store.snapshot()["revision"], "exit")
        with self.assertRaisesRegex(ValueError, "revision conflict"):
            store.cleanup_claim(plan["token"], plan["files"][0]["id"])
        for reason in ("cancelled", "expired"):
            fresh = store.cleanup_preview(store.snapshot()["revision"])
            if reason == "cancelled":
                store.cleanup_cancel(fresh["token"])
            else:
                store.tokens[fresh["token"]]["expires"] = 0
            with self.assertRaisesRegex(ValueError, reason):
                store.cleanup_claim(fresh["token"], fresh["files"][0]["id"])

    def test_cleanup_partial_result_audit_one_use_and_remaining_claim_survives_own_revision(self):
        self.photo("a.jpg", keep=True)
        self.photo("b.jpg")
        self.photo("c.jpg")
        store = self.store()
        self.review_group(store, "p000002")
        plan = store.cleanup_preview(store.snapshot()["revision"])
        first, second = plan["files"]
        claim = store.cleanup_claim(plan["token"], first["id"])
        self.assertEqual(set(claim), {"id", "path", "root", "size", "mtimeNs", "sha256", "frameId", "kind"})
        self.assertIsInstance(claim["mtimeNs"], str)
        self.assertTrue(Path(claim["path"]).is_file(), "Python must not delete originals")
        self.assertEqual(json.loads(store.audit_path.read_text().splitlines()[-1])["status"], "prepared")
        with self.assertRaises(ValueError):
            store.cleanup_claim(plan["token"], first["id"])
        Path(claim["path"]).rename(self.root / "fixture-recycled.jpg")
        result = store.cleanup_result(plan["token"], first["id"], True)
        self.assertEqual(result["successCount"], 1)
        selected = next(row for row in result["snapshot"]["groups"][0]["frames"] if row["id"] == first["frameId"])
        self.assertFalse(selected["available"])
        store.cleanup_claim(plan["token"], second["id"])
        failed = store.cleanup_result(plan["token"], second["id"], False, "native denied")
        self.assertEqual((failed["successCount"], failed["failedCount"]), (1, 1))
        self.assertTrue(next(row for row in failed["snapshot"]["groups"][0]["frames"]
                             if row["id"] == second["frameId"])["available"])
        with self.assertRaises(ValueError):
            store.cleanup_result(plan["token"], first["id"], True)
        statuses = [json.loads(line)["status"] for line in store.audit_path.read_text().splitlines()]
        self.assertEqual(statuses, ["prepared", "success", "prepared", "failed"])
        restarted = ReviewStore(self.root / "data", self.library)
        self.assertEqual(restarted.refresh()["summary"]["trashedCount"], 1)

    def test_cleanup_prepared_crash_and_audit_failure_cannot_retry(self):
        self.photo("a.jpg", keep=True)
        self.photo("b.jpg")
        store = self.store()
        self.review_group(store, "p000002")
        plan = store.cleanup_preview(store.snapshot()["revision"])
        store.cleanup_claim(plan["token"], plan["files"][0]["id"])
        restarted = ReviewStore(self.root / "data", self.library)
        restarted.refresh()
        self.assertFalse(restarted.cleanup_preview(restarted.snapshot()["revision"])["files"])
        store.cleanup_result(plan["token"], plan["files"][0]["id"], False)
        new = store.cleanup_preview(store.snapshot()["revision"])
        with patch.object(store, "_audit", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                store.cleanup_claim(new["token"], new["files"][0]["id"])
        with self.assertRaises(ValueError):
            store.cleanup_claim(new["token"], new["files"][0]["id"])

    def test_cleanup_does_not_enumerate_sidecars_or_unregistered_raw_and_rejects_escape(self):
        photo = self.photo("a.jpg")
        self.raw(photo, "a.nef")
        self.raw(photo, "other.nef")
        self.raw(photo, "a.xmp")
        (self.source / "a.cr3").write_bytes(b"unregistered raw")
        store = self.store()
        self.review_group(store)
        plan = store.cleanup_preview(store.snapshot()["revision"], includeRaw=True, includeZero=True)
        self.assertEqual({row["name"] for row in plan["files"]}, {"a.jpg", "a.nef"})
        photo["primary"]["relative"] = "../outside.jpg"
        self.save()
        snapshot = store.refresh()
        self.assertEqual(snapshot["summary"]["frameCount"], 1)
        self.assertFalse(store.cleanup_preview(snapshot["revision"], includeZero=True)["files"])
        self.assertTrue(store.path.exists())

    def test_cleanup_rejects_symlink_or_junction_replacement(self):
        self.photo("nested/a.jpg", keep=True)
        self.photo("nested/b.jpg")
        store = self.store()
        self.review_group(store, "p000002")
        plan = store.cleanup_preview(store.snapshot()["revision"])
        nested, moved = self.source / "nested", self.root / "moved"
        nested.rename(moved)
        if os.name == "nt":
            result = subprocess.run([os.environ["COMSPEC"], "/c", "mklink", "/J", str(nested), str(moved)],
                                    capture_output=True)
            if result.returncode:
                moved.rename(nested)
                self.skipTest("Junction creation unavailable")
        else:
            nested.symlink_to(moved, target_is_directory=True)
        try:
            with self.assertRaisesRegex(ValueError, "symlink|junction"):
                store.cleanup_claim(plan["token"], plan["files"][0]["id"])
        finally:
            if os.name == "nt":
                nested.rmdir()
            else:
                nested.unlink()
            moved.rename(nested)

    def test_controller_dispatch_refresh_and_readonly_http_review_projection(self):
        self.photo("10.jpg")
        self.photo("2.jpg", group="g00002")
        self.gallery["items"] = []
        self.save()
        resources = self.root / "resources"
        resources.mkdir()
        controller = LibraryController(self.root / "controller", resources, Path(sys.executable), "review-fixture")
        self.controllers.append(controller)
        controller.attach_job(self.library)
        snapshot = controller.dispatch("review_state", {})
        self.assertEqual(snapshot["summary"]["frameCount"], 2)
        group = snapshot["groups"][0]
        changed = controller.dispatch("review_decide", {"expectedRevision": snapshot["revision"], "groupId": group["id"],
                        "changes": [{"frameId": group["frames"][0]["id"], "selected": True}]})
        self.assertEqual(_read(controller.library / "review.json"), changed)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.assertNotEqual(port, 5174)
        process = subprocess.Popen([sys.executable, "-B", str(ROOT / "scripts" / "batch_culling.py"),
                                   "serve", "--job", str(controller.library), "--port", str(port),
                                   "--review-state", str(controller.review.path), "--read-only"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        base = f"http://127.0.0.1:{port}"
        try:
            for attempt in range(100):
                if process.poll() is not None:
                    self.fail(process.stderr.read().decode())
                try:
                    with OPENER.open(base + "/health.json", timeout=.2):
                        break
                except (URLError, TimeoutError):
                    time.sleep(.05)
            with OPENER.open(base + "/review.json") as response:
                self.assertEqual(json.load(response), changed)
            for route in ("/review.json", "/review/decide", "/review_cleanup_claim", "/cancel"):
                with self.assertRaises(HTTPError) as denied:
                    OPENER.open(Request(base + route, data=b"{}", headers={"Origin": "http://remote.invalid"}))
                self.assertEqual(denied.exception.code, 403)
            frame_id = group["frames"][0]["id"]
            with OPENER.open(base + f"/preview/{frame_id}/480.jpg") as response:
                self.assertTrue(response.read().startswith(b"\xff\xd8"))
            with self.assertRaises(HTTPError) as invalid:
                OPENER.open(base + f"/preview/{frame_id}/500.jpg")
            self.assertEqual(invalid.exception.code, 400)
            with OPENER.open(base + "/gallery-crops/") as response:
                self.assertIn(b"html", response.read().lower())
            for route, content_type in (("/review-report.html", "text/html"),
                                         ("/review.js", "javascript"), ("/review.css", "text/css")):
                with OPENER.open(base + route) as response:
                    self.assertIn(content_type, response.headers["Content-Type"])
                    self.assertTrue(response.read())
        finally:
            process.terminate()
            process.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main()
