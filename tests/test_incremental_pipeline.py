# SPDX-License-Identifier: AGPL-3.0-only
"""Controlled/public fixtures only; all outputs live in this test's .tools directory."""
from copy import deepcopy
from contextlib import redirect_stderr
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.request import ProxyHandler, build_opener

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = Path(os.environ.get("BIRDSHOT_INCREMENTAL_TEST_RESOURCES", ROOT))
sys.path.insert(0, str(RESOURCES / "scripts"))
from batch_culling import initialize, inventory
from desktop_backend import LibraryController, REVISION, aggregate_library, atomic_json, extend_job, finish_extension, read_json, run_worker

NODE = Path(os.environ.get("BIRDSHOT_INCREMENTAL_TEST_NODE", ROOT / ".tools" / "node-v24.11.1-win-x64" / "node.exe"))
OPENER = build_opener(ProxyHandler({}))


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=ROOT / ".tools", prefix="incremental-test-")
        self.root = Path(self.folder.name)
        self.source = self.root / "photos"
        self.source.mkdir()
        self.controllers = []

    def tearDown(self):
        for controller in self.controllers:
            controller.shutdown()
        self.folder.cleanup()

    def photo(self, name, second=0, public=None):
        path = self.source / name
        image = Image.open(public) if public else Image.new("RGB", (640, 480), "green")
        with image:
            exif = Image.Exif()
            exif[0x8769] = {36867: f"2026:01:02 12:00:{second:02d}"}
            image.save(path, quality=95, exif=exif)
        os.utime(path, (1_700_000_000, 1_700_000_000))
        return path

    def controller(self):
        resources = self.root / "resources"
        resources.mkdir(exist_ok=True)
        controller = LibraryController(self.root / "data", resources, NODE, "incremental-fixture")
        self.controllers.append(controller)
        return controller

    def test_future_config_is_rejected_without_overwrite(self):
        data = self.root / "data"
        path = data / "desktop-library.json"
        atomic_json(path, {"version": 9, "sources": [{"id": "future"}]})
        original = path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "配置版本"):
            self.controller()
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((data / "library" / "manifest.json").exists())

    def test_processing_checks_coalesce_and_survive_restart(self):
        self.photo("one.jpg")
        controller = self.controller()
        source = controller.add_source(self.source)
        source["status"] = "processing"
        controller.rescan(source["id"])
        controller.rescan(source["id"])
        self.assertTrue(read_json(controller.config_path)["sources"][0]["scanRequested"])
        source.update(job=str(self.root / "job"), jobs=[{"path": str(self.root / "job"), "completed": False, "managed": True}])
        atomic_json(self.root / "job.worker.json", {"status": "completed", "stage": "publish"})
        controller.worker = Mock()
        controller.worker.poll.return_value = 0
        controller.worker_source = source["id"]
        controller.poll_worker()
        self.assertEqual(source["status"], "queued")
        self.assertTrue(source["scanRequested"])
        controller.shutdown()
        self.controllers.remove(controller)
        restored = self.controller()
        self.assertEqual(restored.sources[0]["status"], "queued")
        self.assertTrue(restored.sources[0]["scanRequested"])

    def test_future_checkpoint_does_not_overwrite_saved_gallery(self):
        job = self.root / "job"
        path = job / REVISION / "pipeline.json"
        atomic_json(path, {"formatVersion": 9, "completedGroups": ["future"]})
        gallery = job / REVISION / "gallery.json"
        atomic_json(gallery, {"status": "completed", "items": ["future"]})
        before = (path.read_bytes(), gallery.read_bytes())
        with self.assertRaisesRegex(RuntimeError, "checkpoint格式"):
            run_worker({"resources": str(ROOT), "job": str(job), "source": str(self.source), "node": str(NODE)})
        self.assertEqual(before, (path.read_bytes(), gallery.read_bytes()))

    def test_stability_changes_deletions_and_raw_association_do_not_launch_models(self):
        self.photo("one.jpg")
        self.photo("two.jpg", 1)
        self.photo("paired.jpg", 6)
        controller = self.controller()
        source = controller.add_source(self.source)
        job = initialize(self.source, self.root / "job")
        source.update(status="completed", jobs=[{"path": str(job), "managed": True, "completed": True}])
        self.assertEqual(controller.check_additions(source), [])
        self.photo("new.jpg", 2)
        self.assertIsNone(controller.check_additions(source))
        self.photo("new.jpg", 3)
        os.utime(self.source / "new.jpg", (1_700_000_001, 1_700_000_001))
        self.assertIsNone(controller.check_additions(source))
        self.assertEqual(len(controller.check_additions(source)), 1)
        self.photo("one.jpg", 9)
        os.utime(self.source / "one.jpg", (1_700_000_002, 1_700_000_002))
        (self.source / "two.jpg").unlink()
        (self.source / "paired.arw").write_bytes(b"controlled unsupported RAW provenance")
        (self.source / "in-progress.jpg.part").write_bytes(b"not a photo")
        controller.check_additions(source)
        controller.check_additions(source)
        self.assertEqual({row["kind"] for row in source["sourceIssues"]}, {"changed", "missing"})
        self.assertIn("paired.jpg", source["associations"])
        paired = next(photo for photo in read_json(job / "manifest.json")["photos"] if photo["primary"]["relative"] == "paired.jpg")
        self.assertEqual(paired["companions"][0]["relative"], "paired.arw")
        self.assertFalse(any(row["primary"]["relative"].endswith(".part") for row in inventory(self.source)["photos"]))

    def test_no_additions_leave_no_empty_job_and_raw_only_jpeg_is_new(self):
        controller = self.controller()
        source = controller.add_source(self.source)
        controller.runtime_state = lambda: {"ready": True}
        controller.launch = Mock()
        controller.start_worker(source)
        self.assertEqual(source["status"], "completed")
        self.assertEqual(source["jobs"], [])
        self.assertEqual(controller.launch.call_count, 0)
        (self.source / "raw.arw").write_bytes(b"controlled RAW-only fixture")
        job = initialize(self.source, self.root / "raw-job")
        source["jobs"] = [{"path": str(job), "managed": True, "completed": True}]
        self.photo("raw.jpg")
        self.assertIsNone(controller.check_additions(source))
        additions = controller.check_additions(source)
        self.assertEqual(len(additions), 1)
        self.assertTrue(additions[0]["supported"])
        self.assertEqual(source["sourceIssues"], [])

    def historical_fixture(self, incompatible=False, unmatched=False):
        import batch_culling
        import gallery_birder
        self.photo("one.jpg")
        self.photo("two.jpg", 1)
        resources = self.root / "resources"
        shutil.copytree(ROOT / "scripts", resources / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        taxonomy = resources / "assets" / "taxonomy"
        taxonomy.mkdir(parents=True)
        for name in ("candidates.json", "taxonomy-ebird2025-zh-SIM.json"):
            shutil.copyfile(ROOT / ".tools" / "species-comparison-10" / name, taxonomy / name)
        detector = Mock()
        def analyze(image, photo_id, group_id, progress):
            frame = batch_culling.empty_frame({"id": photo_id}, group_id, None)
            frame.update(width=640, height=480, instances=[{
                "id": 1, "trackId": None, "body": [380 if unmatched and photo_id == "p000003" else 100, 100, 200, 200], "bodyConfidence": .99,
                "bodyPixels": 40000, "bodySharpness": 100000, "sharpness": None, "eyeIssue": "controlled",
                "eye": None, "beak": None, "patch": None,
                "mask": {"width": 4, "height": 4, "runs": [0, 16], "region": [100, 100, 200, 200],
                         "scale": 1, "padX": 0, "padY": 0, "path": "M100,100L300,100L300,300L100,300Z"},
                "posePoints": []}])
            return frame
        detector.analyze.side_effect = analyze
        predictions = []
        class CachedSession(gallery_birder.ClassifierSession):
            def classify(session, rows, total=None, final=False, on_progress=None):
                done = {row["crop"] for row in session.result["rows"]}
                for row in rows:
                    if row["crop"] in done:
                        continue
                    predictions.append(row["crop"])
                    session.result["rows"].append({**row, "status": "completed", "scoreType": "birder-fullclass",
                        "classCount": 10000, "birdClassCount": 1486, "birdMass": .99, "nonBirdMass": .01,
                        "maxBird": .9, "maxNonBird": .001, "top1IsBird": True, "margin": .899,
                        "detectionConfidence": .99, "top5": [], "top3": []})
                session.result.update(status="completed" if final else "running", total=total if total is not None else len(rows))
                session.save()
                if on_progress:
                    on_progress("classified", session.result)
                wanted = {row["crop"] for row in rows}
                return [row for row in session.result["rows"] if row["crop"] in wanted]
        historical = self.root / "historical-job"
        spec = {"resources": str(resources), "node": str(NODE), "source": str(self.source), "job": str(historical)}
        with patch.object(batch_culling, "NativeDetector", return_value=detector), \
             patch.object(gallery_birder, "ClassifierSession", CachedSession):
            run_worker(spec)
            if incompatible:
                path = historical / REVISION / "verification.json"
                data = read_json(path)
                data["fingerprint"] = "incompatible historical classifier policy"
                atomic_json(path, data)
            original = {path.relative_to(historical): path.read_bytes() for path in historical.rglob("*") if path.is_file()}
            controller = self.controller()
            source = controller.attach_job(historical)
            self.photo("three.jpg", 2)
            controller.runtime_state = lambda: {"ready": True}
            controller.launch = Mock()
            controller.start_worker(source)
            controller.start_worker(source)
            controller.worker = controller.worker_source = None
            derived = Path(source["job"])
            derived_spec = read_json(controller.data / ("worker-" + source["id"] + ".json"))
            self.assertEqual(len(derived_spec["historyTails"]), 1)
            detector.analyze.reset_mock()
            predictions.clear()
            run_worker(derived_spec)
            self.assertEqual(detector.analyze.call_count, 1)
            self.assertEqual(len(predictions), 1)
            self.assertEqual(original, {path.relative_to(historical): path.read_bytes()
                                      for path in historical.rglob("*") if path.is_file()})
            source["status"] = "completed"
            result = aggregate_library(controller.library, [source])
            self.assertEqual(len(result["frames"]), 3)
            self.assertEqual(len(result["items"]), 3)
            self.assertEqual(len({item["groupId"] for item in result["items"]}), 1)
            group = read_json(controller.library / REVISION / "groups" / (result["items"][0]["groupId"] + ".json"))
            self.assertEqual(len(group["frames"]), 3)
            self.assertEqual(sum(bool(frame.get("historicalOrigin")) for frame in group["frames"]), 2)
            self.assertTrue(all(item["historicalOrigin"]["job"] == str(historical)
                                for item in result["items"] if item.get("historicalOrigin")))
            return group, result

    def test_readonly_historical_tail_reuses_two_frames_and_only_detects_new_photo(self):
        group, result = self.historical_fixture()
        self.assertEqual(group["group"]["historyTail"]["status"], "compatible")
        self.assertTrue(group["group"]["historyTail"]["groupRevision"])
        self.assertTrue(all(item["quality"]["cohortSize"] == 3 for item in result["items"]))
        self.assertEqual(result["progress"]["processed"], 3)

    def test_incompatible_historical_tail_is_three_frame_pending_comparison_without_old_inference(self):
        group, result = self.historical_fixture(incompatible=True)
        self.assertEqual(group["group"]["historyTail"]["status"], "pending")
        self.assertIn("Birder/中文名策略指纹不兼容", group["group"]["historyTail"]["reason"])
        self.assertEqual(result["summary"]["preferred"], 0)
        self.assertEqual(result["summary"]["pending"], 2)
        self.assertTrue(all(item["rank"] is None and item["decision"] == "review" for item in result["items"]))

    def test_unmatched_historical_tail_explicitly_waits_for_identity_confirmation(self):
        group, result = self.historical_fixture(unmatched=True)
        self.assertEqual(group["group"]["historyTail"]["status"], "pending")
        self.assertIn("主体", group["group"]["historyTail"]["reason"])
        self.assertEqual(result["summary"]["preferred"], 0)
        self.assertEqual(result["summary"]["pending"], 0)

    def test_affected_tail_group_preserves_photo_ids_and_cache_history(self):
        self.photo("one.jpg")
        self.photo("two.jpg", 1)
        job = initialize(self.source, self.root / "job")
        manifest = read_json(job / "manifest.json")
        group = manifest["groups"][0]
        atomic_json(job / "groups" / (group["id"] + ".json"), {
            "group": group, "frames": [{"id": photo["id"]} for photo in manifest["photos"]],
            "cropMapping": [], "selectedCrops": [], "versions": manifest["versions"]})
        atomic_json(job / REVISION / "pipeline.json", {
            "formatVersion": 1, "publishedRevision": 2, "completedGroups": [group["id"]], "fingerprint": "controlled"})
        atomic_json(job / REVISION / "gallery.json", {"items": [], "status": "completed"})
        self.photo("third.jpg", 2)
        affected = extend_job(job, inventory(self.source)["photos"])
        self.assertEqual(affected, [group["id"]])
        updated = read_json(job / "manifest.json")
        self.assertEqual([row["id"] for row in updated["photos"]], ["p000001", "p000002", "p000003"])
        self.assertEqual(updated["groups"][0]["count"], 3)
        self.assertEqual(read_json(job / "groups" / (group["id"] + ".json"))["phase"], "extending")
        self.assertEqual(read_json(job / REVISION / "pipeline.json")["completedGroups"], [])
        self.assertTrue((job / REVISION / "history" / "2" / "gallery.json").is_file())
        self.photo("fourth.jpg", 3)
        with patch("desktop_backend.finish_extension", side_effect=RuntimeError("controlled interruption")):
            with self.assertRaisesRegex(RuntimeError, "controlled interruption"):
                extend_job(job, inventory(self.source)["photos"])
        pending = job / REVISION / "pending-extension.json"
        self.assertTrue(pending.exists())
        self.assertEqual(len(read_json(job / "manifest.json")["photos"]), 3)
        finish_extension(job, read_json(pending))
        self.assertFalse(pending.exists())
        self.assertEqual(len(read_json(job / "manifest.json")["photos"]), 4)

    def test_detection_error_cannot_checkpoint_or_claim_success(self):
        import batch_culling
        import gallery_birder
        self.photo("broken.jpg")
        resources = self.root / "resources"
        shutil.copytree(ROOT / "scripts", resources / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        taxonomy = resources / "assets" / "taxonomy"
        taxonomy.mkdir(parents=True)
        for name in ("candidates.json", "taxonomy-ebird2025-zh-SIM.json"):
            shutil.copyfile(ROOT / ".tools" / "species-comparison-10" / name, taxonomy / name)
        job = self.root / "job"
        detector = Mock()
        detector.analyze.side_effect = RuntimeError("controlled broken detection")
        with patch.object(batch_culling, "NativeDetector", return_value=detector), \
             patch.object(gallery_birder.birder, "load_pretrained_model_and_transform") as load:
            with self.assertRaisesRegex(RuntimeError, "连拍组处理失败"):
                run_worker({"resources": str(resources), "job": str(job), "source": str(self.source), "node": str(NODE)})
            self.assertEqual(load.call_count, 0)
        self.assertEqual(read_json(job.parent / "job.worker.json")["status"], "error")
        detect_stage = next(stage for stage in read_json(job.parent / "job.worker.json")["progress"]["stages"] if stage["id"] == "detect")
        self.assertEqual(detect_stage["status"], "error")
        self.assertEqual(read_json(job / REVISION / "pipeline.json")["completedGroups"], [])
        self.assertEqual(read_json(job / REVISION / "gallery.json")["summary"]["preferred"], 0)

    def test_partial_report_client_abort_does_not_change_job_or_swallow_other_errors(self):
        import batch_culling
        self.photo("one.jpg")
        job = initialize(self.source, self.root / "job")
        handlers = []
        class Server:
            def __init__(self, address, handler):
                handlers.append(handler)
            def serve_forever(self):
                return
            def server_close(self):
                return
        with patch.object(batch_culling, "ThreadingHTTPServer", Server):
            batch_culling.serve(job, 60000, read_only=True)
        handler = handlers[0].__new__(handlers[0])
        handler.allowed = lambda: True
        handler.headers = {}
        handler.send_response = handler.send_header = handler.end_headers = Mock()
        before = (job / "status.json").read_bytes()
        for route in ("/gallery.json", "/gallery-refresh-bridge", "/health.json"):
            handler.path = route
            for error in (BrokenPipeError(), ConnectionAbortedError(10053, "controlled"), ConnectionResetError()):
                handler.wfile = Mock()
                handler.wfile.write.side_effect = error
                log = io.StringIO()
                with redirect_stderr(log):
                    handler.do_GET()
                self.assertIn("Report client disconnected:", log.getvalue())
                self.assertEqual((job / "status.json").read_bytes(), before)
        handler.wfile.write.side_effect = OSError("controlled non-disconnect error")
        with self.assertRaises(OSError):
            handler.do_GET()

    def test_failed_verification_is_processed_unknown_not_pending_or_preferred(self):
        from gallery_build import build
        from batch_culling import empty_frame
        self.photo("one.jpg")
        job = initialize(self.source, self.root / "job")
        manifest = read_json(job / "manifest.json")
        photo, group = manifest["photos"][0], manifest["groups"][0]
        frame = empty_frame(photo, group["id"], None)
        crop = f"crops/{group['id']}/{photo['id']}_b01.jpg"
        bird = {"id": 1, "body": [10, 20, 200, 200], "bodyPixels": 32000, "bodySharpness": 100,
                "bodyConfidence": .9, "crop": crop, "thumbnail": crop[:-4] + "_thumb.jpg"}
        target = job / crop
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (200, 200), "green").save(target)
        shutil.copyfile(target, job / bird["thumbnail"])
        frame.update(width=640, height=480, instances=[bird], source=photo, processingStatus="ok")
        atomic_json(job / "groups" / (group["id"] + ".json"), {"group": group, "frames": [frame]})
        atomic_json(job / "mapping.json", {"crops": [{
            "crop": crop, "groupId": group["id"], "photoId": photo["id"], "instanceId": 1,
            "sourceRelative": photo["primary"]["relative"], "selected": False}]})
        atomic_json(job / REVISION / "verification.json", {"status": "running", "processed": 1,
            "model": {}, "fingerprint": "controlled", "rows": [{"crop": crop, "status": "error", "error": "controlled"}]})
        build(job, job / REVISION, NODE, ROOT / ".tools" / "species-comparison-10", finalized_groups=set(), status="running")
        result = read_json(job / REVISION / "gallery.json")
        self.assertEqual(result["summary"]["processedUnknown"], 1)
        self.assertEqual(result["summary"]["pending"], 0)
        self.assertEqual(result["summary"]["failed"], 1)
        self.assertEqual(result["summary"]["preferred"], 0)
        self.assertIsNone(result["items"][0]["rank"])

    @unittest.skipUnless(os.environ.get("BIRDSHOT_REAL_INCREMENTAL") == "1", "opt-in real public CPU models")
    def test_real_models_publish_first_group_before_second_and_append_only_new_pixels(self):
        import batch_culling
        import batch_inference
        import gallery_birder
        if os.environ.get("BIRDSHOT_INCREMENTAL_TEST_RESOURCES"):
            resources = RESOURCES
            model_cache = RESOURCES / "models"
        else:
            resources = self.root / "resources"
            shutil.copytree(ROOT / "scripts", resources / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
            taxonomy = resources / "assets" / "taxonomy"
            taxonomy.mkdir(parents=True)
            for name in ("candidates.json", "taxonomy-ebird2025-zh-SIM.json"):
                shutil.copyfile(ROOT / ".tools" / "species-comparison-10" / name, taxonomy / name)
            model_cache = ROOT / ".tools" / "species-models"
        first = ROOT / "public" / "culling" / "samples" / "kingfisher"
        second = ROOT / "public" / "culling" / "samples" / "mallards"
        self.photo("one.jpg", 0, first / "frame-00120.jpg")
        self.photo("two.jpg", 1, first / "frame-00121.jpg")
        self.photo("duck.jpg", 20, next(second.glob("*.jpg")))
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.source.glob("*.jpg")}
        job = self.root / "job"
        spec = {"resources": str(resources), "job": str(job), "node": str(NODE), "source": str(self.source)}
        pending, release_pending, second_gate, release_second = [threading.Event() for _ in range(4)]
        events, errors, analyses = [], [], []
        native = batch_culling.NativeDetector
        original_load = gallery_birder.birder.load_pretrained_model_and_transform
        original_session = batch_inference.ort.InferenceSession
        forward_batches = []
        def load_bounded(*args, **kwargs):
            self.assertEqual(gallery_birder.torch.get_num_threads(), 4)
            self.assertEqual(gallery_birder.torch.get_num_interop_threads(), 1)
            loaded = original_load(*args, **kwargs)
            loaded[0].register_forward_pre_hook(lambda _net, inputs: forward_batches.append(inputs[0].shape[0]))
            return loaded
        def bounded_native_session(*args, **kwargs):
            self.assertEqual(args[1].intra_op_num_threads, 4)
            self.assertEqual(args[1].inter_op_num_threads, 1)
            return original_session(*args, **kwargs)
        original_analyze = native.analyze
        def analyze(detector, image, photo_id, group_id, progress):
            analyses.append((photo_id, group_id))
            return original_analyze(detector, image, photo_id, group_id, progress)
        def observe(event, progress):
            if event in {"pending_published", "final_published"}:
                events.append((event, deepcopy(read_json(job / REVISION / "gallery.json"))))
            if event == "pending_published" and progress["currentGroup"] == "g00001":
                pending.set()
                if not release_pending.wait(180):
                    raise RuntimeError("controlled pending gate timeout")
            if event == "before_group" and progress["currentGroup"] == "g00002":
                second_gate.set()
                if not release_second.wait(180):
                    raise RuntimeError("controlled second-group gate timeout")
        def execute():
            try:
                run_worker(spec, observer=observe)
            except BaseException as error:
                errors.append(error)
                pending.set()
                second_gate.set()
        with patch.object(gallery_birder, "CACHE", model_cache), \
             patch.object(batch_culling, "NativeDetector", wraps=native) as detector_loads, \
             patch.object(native, "analyze", analyze), \
             patch.object(batch_inference.ort, "InferenceSession", side_effect=bounded_native_session), \
             patch.object(gallery_birder.birder, "load_pretrained_model_and_transform",
                          side_effect=load_bounded) as birder_loads:
            thread = threading.Thread(target=execute)
            thread.start()
            try:
                self.assertTrue(pending.wait(180), str(errors))
                self.assertEqual(errors, [])
                first_pending = events[0][1]
                self.assertEqual(first_pending["status"], "processing")
                self.assertGreater(len(first_pending["items"]), 0)
                self.assertTrue(all(item["decision"] == "review" and item["rank"] is None
                                    and item["birdVerification"]["evidence"]["status"] == "pending" for item in first_pending["items"]))
                self.assertEqual(first_pending["summary"]["unknown"], 0)
                self.assertEqual(first_pending["summary"]["pending"], len(first_pending["items"]))
                for item in first_pending["items"]:
                    with Image.open(job / item["crop"]) as crop:
                        crop.verify()
                aggregate = aggregate_library(self.root / "aggregate", [{"id": "controlled", "name": "公开鸟图",
                    "path": str(self.source), "status": "processing", "jobs": [{"path": str(job)}]}])
                self.assertEqual(aggregate["summary"]["pending"], len(first_pending["items"]))
                self.assertEqual(aggregate["summary"]["processedUnknown"], 0)
                live = LibraryController(self.root / "live-data", resources, NODE, "real-early-publication")
                self.controllers.append(live)
                live_source = live.attach_job(job)
                live_source["status"] = "processing"
                live.rebuild()
                with socket.socket() as reserve:
                    reserve.bind(("127.0.0.1", 0))
                    port = reserve.getsockname()[1]
                service = live.configure_service(port=port)
                self.assertEqual(service["status"], "ready", service)
                base = service["localUrl"].split("/gallery")[0].rstrip("/")
                def served(path):
                    with OPENER.open(base + path, timeout=10) as response:
                        return response.read()
                pending_http = json.loads(served("/gallery.json"))
                self.assertEqual(pending_http["status"], "processing")
                self.assertEqual(pending_http["summary"]["pending"], len(first_pending["items"]))
                for item in pending_http["items"]:
                    with Image.open(io.BytesIO(served("/media/" + item["crop"]))) as crop:
                        crop.verify()
                pending_detail = json.loads(served("/gallery/group/" + pending_http["items"][0]["groupId"] + ".json"))
                self.assertEqual(len(pending_detail["frames"]), 2)
                release_pending.set()
                self.assertTrue(second_gate.wait(180), str(errors))
                self.assertEqual(errors, [])
                self.assertEqual(read_json(job.parent / "job.worker.json")["status"], "processing")
                saved = read_json(job / REVISION / "pipeline.json")
                self.assertEqual(saved["completedGroups"], ["g00001"])
                final_first = events[-1][1]
                self.assertTrue(all(item["birdVerification"]["evidence"]["status"] == "completed" for item in final_first["items"]))
                self.assertTrue(all(item["birdVerification"]["evidence"]["classCount"] == 10000 for item in final_first["items"]))
                self.assertEqual({group for _, group in analyses}, {"g00001"})
                live.rebuild()
                final_http = json.loads(served("/gallery.json"))
                self.assertEqual(final_http["status"], "processing")
                self.assertEqual(final_http["progress"]["processed"], len(final_http["items"]))
                self.assertTrue(all(item["birdVerification"]["evidence"]["classCount"] == 10000 for item in final_http["items"]))
            finally:
                release_pending.set()
                release_second.set()
                thread.join(180)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(detector_loads.call_count, 1)
            self.assertEqual(birder_loads.call_count, 1)
            self.assertEqual(len(analyses), 3)
            self.assertEqual(read_json(job.parent / "job.worker.json")["status"], "completed")
            old_crops = {path: path.read_bytes() for path in (job / "crops").rglob("*.jpg")}
            # A restart performs zero detector or classifier loads.
            run_worker(spec)
            self.assertEqual((detector_loads.call_count, birder_loads.call_count), (1, 1))
            interrupted = read_json(job / REVISION / "pipeline.json")
            interrupted["completedGroups"] = []
            atomic_json(job / REVISION / "pipeline.json", interrupted)
            run_worker(spec)
            self.assertEqual((detector_loads.call_count, birder_loads.call_count), (1, 1),
                             "completed detection/classification caches resume unfinished publication without model reload")
            historical = self.root / "readonly-public-job"
            shutil.copytree(job, historical)
            historical_bytes = {path.relative_to(historical): path.read_bytes() for path in historical.rglob("*") if path.is_file()}
            self.photo("three.jpg", 2, first / "frame-00122.jpg")
            history_controller = LibraryController(self.root / "history-data", resources, NODE, "real-historical-tail")
            self.controllers.append(history_controller)
            history_source = history_controller.attach_job(historical)
            history_controller.runtime_state = lambda: {"ready": True}
            history_controller.launch = Mock()
            history_controller.start_worker(history_source)
            history_controller.start_worker(history_source)
            history_controller.worker = history_controller.worker_source = None
            derived_spec = read_json(history_controller.data / ("worker-" + history_source["id"] + ".json"))
            before_historical = len(analyses)
            before_historical_classification = len(forward_batches)
            run_worker(derived_spec)
            self.assertEqual(len(analyses) - before_historical, 1)
            self.assertEqual(forward_batches[before_historical_classification:], [1])
            derived = Path(derived_spec["job"])
            derived_group = read_json(derived / REVISION / "groups" / "g00001.json")
            self.assertEqual(len(derived_group["frames"]), 3)
            self.assertEqual(derived_group["group"]["historyTail"]["status"], "compatible")
            self.assertTrue(all(bird["quality"]["cohortSize"] == 3 for frame in derived_group["frames"] for bird in frame["instances"]))
            self.assertEqual(historical_bytes, {path.relative_to(historical): path.read_bytes()
                                                for path in historical.rglob("*") if path.is_file()})
            before_managed_append = len(analyses)
            spec["additionalPhotos"] = inventory(self.source)["photos"]
            run_worker(spec)
            self.assertEqual(len(analyses) - before_managed_append, 1)
            self.assertEqual(analyses[-1], ("p000004", "g00001"))
            self.assertEqual(read_json(job / "groups.json")[0]["count"], 3)
            self.assertTrue(all(path.read_bytes() == data for path, data in old_crops.items()))
            self.assertEqual(read_json(job / REVISION / "pipeline.json")["completedGroups"], ["g00001", "g00002"])
            self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before})
            print(json.dumps({"realIncremental": True, "earlyPendingCrops": len(first_pending["items"]),
                              "firstFinalBeforeSecond": True, "firstRunDetectorLoads": 1, "firstRunBirderLoads": 1,
                              "fullClassCount": 10000, "restartModelLoads": 0, "appendAnalyzedPhotos": 1,
                              "earlyPendingAndFinalHttpReadable": True,
                              "historicalTailFrames": 3, "historicalTailNewDetections": 1,
                              "historicalJobBytesUnchanged": True,
                              "boundedModelConstructionThreads": True,
                              "originalSha256Unchanged": True, "privatePhotosRead": False}))


if __name__ == "__main__":
    unittest.main()
