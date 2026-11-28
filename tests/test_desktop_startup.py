# SPDX-License-Identifier: AGPL-3.0-only
"""Isolated startup gates and synthetic existing jobs; no user files or inference."""
import hashlib
import io
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener
import uuid

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import batch_culling
import desktop_backend
from desktop_backend import LibraryController, REVISION, aggregate_library, atomic_json, read_json

OPENER = build_opener(ProxyHandler({}))


class DesktopStartupTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "tests" / (".desktop-startup-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.resources = self.root / "resources"
        shutil.copytree(ROOT / "scripts", self.resources / "scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.events = []
        self.controllers = []
        self.addCleanup(self.stop_controllers)
        runtime = patch.object(LibraryController, "runtime_state",
                               return_value={"ready": True, "models": [], "mode": "synthetic-test"})
        runtime.start()
        self.addCleanup(runtime.stop)

    def stop_controllers(self):
        for controller in self.controllers:
            controller.shutdown()
            for thread in (controller.initialization_thread, controller.thread):
                if thread:
                    thread.join(5)

    def port(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]

    def controller(self, sources=None, port=None):
        data = self.root / ("data-" + str(len(self.controllers)))
        atomic_json(data / "desktop-library.json",
                    {"version": 1, "service": {"mode": "local", "port": self.port() if port is None else port},
                     "sources": sources or [], "extensionMetadata": {"preserve": True}})
        controller = LibraryController(data, self.resources, Path(sys.executable), "startup-test",
                                       self.events.append, defer_initialization=True)
        self.controllers.append(controller)
        return controller

    def wait_for(self, predicate, seconds=12):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.02)
        self.fail("Startup condition not reached: " + repr(self.events[-3:]))

    def synthetic_job(self, count=84):
        source, job = self.root / "photos", self.root / "existing-job"
        source.mkdir()
        photos, groups, crops, items = [], [], [], []
        for index in range(count):
            photo_id, group_id = f"p{index + 1:06d}", f"g{index + 1:05d}"
            path = source / (photo_id + ".jpg")
            Image.new("RGB", (16, 12), "green").save(path)
            info = path.stat()
            photo = {"id": photo_id, "primary": {"relative": path.name, "format": ".jpg",
                     "size": info.st_size, "mtimeNs": info.st_mtime_ns}, "companions": [],
                     "supported": True, "timestamp": None, "capturedAt": None,
                     "encodedSize": [16, 12], "exifOrientation": 1}
            photos.append(photo)
            group = {"id": group_id, "photoIds": [photo_id], "count": 1, "name": path.name}
            groups.append(group)
            crop = f"crops/{group_id}/{photo_id}_b01.jpg"
            crop_path = job / crop
            crop_path.parent.mkdir(parents=True)
            shutil.copyfile(path, crop_path)
            shutil.copyfile(path, job / (crop[:-4] + "_thumb.jpg"))
            crops.append({"photoId": photo_id, "groupId": group_id, "instanceId": 1,
                          "crop": crop, "sourceRelative": path.name,
                          "sourceSha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            items.append({"id": photo_id + "_b1", "frameId": photo_id, "groupId": group_id,
                          "instanceId": 1, "crop": crop, "thumbnail": crop[:-4] + "_thumb.jpg",
                          "decision": "keep", "birdVerification": {"status": "bird"}, "species": {"top5": []}})
            atomic_json(job / REVISION / "groups" / (group_id + ".json"),
                        {"group": group, "frames": [{"id": photo_id, "width": 16, "height": 12,
                         "group": group_id, "instances": [{"id": 1, "crop": crop}]}]})
        atomic_json(job / "manifest.json", {"source": str(source), "photos": photos, "groups": groups})
        atomic_json(job / "mapping.json", {"crops": crops})
        atomic_json(job / REVISION / "gallery.json",
                    {"status": "completed", "items": items, "progress": {"processed": count, "total": count}})
        return {"id": "source-existing", "path": str(source), "name": "synthetic",
                "status": "completed", "jobs": [{"path": str(job), "managed": False}]}

    def tree_hashes(self, root):
        return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in root.rglob("*") if path.is_file()}

    def test_state_responds_while_initialization_is_blocked_and_mutations_are_rejected(self):
        source = self.synthetic_job(1)
        controller = self.controller([source])
        entered, release = threading.Event(), threading.Event()
        original = controller.initialize
        def gated():
            entered.set()
            release.wait(10)
            original()
        controller.initialize = gated
        controller.start()
        try:
            self.assertTrue(entered.wait(2))
            finished = threading.Event()
            states = []
            def state_request():
                states.append(controller.dispatch("state", {}))
                finished.set()
            reader = threading.Thread(target=state_request, daemon=True)
            reader.start()
            self.assertTrue(finished.wait(.5), "State blocked behind initialization")
            self.assertEqual(states[0]["sources"][0]["id"], source["id"])
            self.assertFalse(states[0]["runtime"]["ready"])
            self.assertEqual(self.events[0]["event"], "controller-ready")
            self.assertEqual(self.events[0]["state"]["startup"]["status"], "initializing")
            for action in ("remove_source", "review_state", "review_cleanup_claim", "configure_service"):
                with self.assertRaisesRegex(RuntimeError, "初始化"):
                    controller.dispatch(action, {})
            self.assertFalse(any(event["event"] == "ready" for event in self.events))
        finally:
            release.set()
        self.wait_for(lambda: controller.state()["startup"]["status"] == "ready")

    def test_failed_initialization_retries_serially_without_losing_config(self):
        controller = self.controller()
        actual = controller.initialize
        attempts = []
        entered, release = threading.Event(), threading.Event()
        def initialize():
            attempts.append(1)
            if len(attempts) == 1:
                raise ValueError("synthetic damaged projection")
            entered.set()
            release.wait(10)
            actual()
        controller.initialize = initialize
        before = read_json(controller.config_path)
        controller.start()
        self.wait_for(lambda: controller.state()["startup"]["status"] == "error")
        self.assertIn("damaged", controller.state()["startup"]["error"])
        self.assertFalse(controller.runtime["ready"])
        self.assertEqual(read_json(controller.config_path), before)
        controller.dispatch("retry_startup", {})
        try:
            self.assertTrue(entered.wait(2))
            controller.dispatch("retry_startup", {})
            self.assertEqual(len(attempts), 2)
        finally:
            release.set()
        self.wait_for(lambda: controller.state()["startup"]["status"] == "ready")
        self.assertEqual(read_json(controller.config_path)["extensionMetadata"], {"preserve": True})
        self.assertEqual(sum(event["event"] == "controller-ready" for event in self.events), 1)

    def test_shutdown_cancels_blocked_initialization_and_never_retries_or_launches(self):
        controller = self.controller()
        entered, release = threading.Event(), threading.Event()
        actual = controller.initialize
        def initialize():
            entered.set()
            release.wait(10)
            actual()
        controller.initialize = initialize
        controller.launch = Mock(side_effect=AssertionError("Must not launch after shutdown"))
        before = controller.config_path.read_bytes()
        controller.start()
        self.assertTrue(entered.wait(2))
        try:
            self.assertEqual(controller.dispatch("shutdown", {}), {"stopped": True})
            with self.assertRaisesRegex(RuntimeError, "关闭"):
                controller.dispatch("retry_startup", {})
        finally:
            release.set()
        controller.initialization_thread.join(3)
        self.assertFalse(controller.initialization_thread.is_alive())
        controller.launch.assert_not_called()
        self.assertEqual(controller.config_path.read_bytes(), before)
        self.assertEqual(controller.state()["startup"]["status"], "stopped")

    def test_empty_library_reaches_actual_instance_health_ready(self):
        controller = self.controller()
        controller.start()
        self.wait_for(lambda: controller.state()["startup"]["status"] == "ready")
        self.assertTrue(controller.runtime["ready"])
        self.assertEqual(read_json(controller.library / "manifest.json")["photos"], [])
        with OPENER.open(controller.service["localUrl"] + "health.json", timeout=2) as response:
            self.assertEqual(json.load(response)["status"], "ready")
        self.assertEqual(sum(event["event"] == "ready" for event in self.events), 1)

    def test_occupied_port_can_recover_on_explicit_safe_port_without_rebuilding_library(self):
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            controller = self.controller([self.synthetic_job(1)], port=occupied.getsockname()[1])
            controller.start()
            self.wait_for(lambda: controller.state()["startup"]["status"] == "error")
            self.assertTrue(controller.library_initialized)
            self.assertTrue(controller.model_ready)
            self.assertFalse(controller.runtime["ready"])
            self.assertIsNone(controller.server)
            with self.assertRaisesRegex(RuntimeError, "初始化"):
                controller.dispatch("remove_source", {"id": "source-existing"})
            alternative = self.port()
            actual_poll = controller.poll_service
            health_allowed = threading.Event()
            def poll():
                if health_allowed.is_set():
                    actual_poll()
            with patch.object(controller, "initialize", side_effect=AssertionError("Must reuse validated library")), \
                    patch.object(controller, "poll_service", side_effect=poll):
                service = controller.dispatch("configure_service", {"mode": "local", "port": alternative})
                self.assertEqual(service["status"], "starting")
                self.assertEqual(controller.startup["status"], "initializing")
                self.assertEqual(controller.startup["phase"], "service")
                self.assertFalse(controller.runtime["ready"])
                health_allowed.set()
                self.wait_for(lambda: controller.state()["startup"]["status"] == "ready")
            self.assertTrue(controller.initialized)
            self.assertTrue(controller.runtime["ready"])
            with OPENER.open(controller.service["localUrl"] + "health.json", timeout=2) as response:
                health = json.load(response)
            self.assertEqual(health["status"], "ready")
            self.assertEqual(health["instanceId"], controller.instance_id)
            self.assertEqual(controller.service_config["port"], alternative)
            with socket.create_connection(occupied.getsockname(), timeout=1):
                pass

    def test_existing_84_frame_job_is_read_only_and_never_inferred(self):
        source = self.synthetic_job()
        before_photos = self.tree_hashes(Path(source["path"]))
        before_job = self.tree_hashes(Path(source["jobs"][0]["path"]))
        controller = self.controller([source])
        controller.start_worker = Mock(side_effect=AssertionError("Existing completed jobs must not retrain"))
        controller.start()
        self.wait_for(lambda: controller.state()["startup"]["status"] == "ready", seconds=20)
        self.assertEqual(len(read_json(controller.library / "manifest.json")["photos"]), 84)
        self.assertEqual(self.tree_hashes(Path(source["path"])), before_photos)
        self.assertEqual(self.tree_hashes(Path(source["jobs"][0]["path"])), before_job)
        controller.start_worker.assert_not_called()
        phases = {event["state"]["startup"]["phase"] for event in self.events if "state" in event}
        self.assertTrue({"library", "library-groups", "review-photos", "service", "ready"} <= phases)
        media = read_json(controller.library / "manifest.json")["media"][0]["path"]
        with OPENER.open(controller.service["localUrl"] + "media/" + media, timeout=2) as response:
            self.assertEqual(response.status, 200)

    def polling_controller(self):
        controller = self.controller()
        controller.model_ready = True
        controller.library_initialized = True
        controller.server = Mock(pid=987654)
        controller.server.poll.return_value = None
        controller.owner.stop = Mock()
        controller.service_started_at = time.monotonic() - 25
        return controller

    def health_reply(self, instance="startup-test", status="starting"):
        opener = Mock()
        opener.open.return_value = io.StringIO(json.dumps({"instanceId": instance, "status": status,
                                                         "startup": {"processed": 2, "total": 84}}))
        return opener

    def test_starting_health_is_not_ready_and_wrong_instance_stops_only_owned_child(self):
        controller = self.polling_controller()
        child = controller.server
        with patch.object(desktop_backend, "build_opener", return_value=self.health_reply()):
            controller.poll_service()
        self.assertFalse(controller.runtime["ready"])
        self.assertEqual(controller.service["status"], "starting")
        self.assertEqual(controller.startup["processed"], 2)
        controller.owner.stop.assert_not_called()
        with patch.object(desktop_backend, "build_opener",
                          return_value=self.health_reply(instance="another-service", status="ready")):
            controller.poll_service()
        self.assertEqual(controller.service["status"], "error")
        self.assertIn("另一个", controller.service["error"])
        controller.owner.stop.assert_called_once_with(child)

    def test_slow_running_child_survives_health_window_and_eventually_becomes_ready(self):
        controller = self.polling_controller()
        opener = Mock()
        opener.open.side_effect = OSError("Still loading")
        with patch.object(desktop_backend, "build_opener", return_value=opener):
            controller.poll_service()
        self.assertEqual(controller.service["status"], "starting")
        self.assertIn("继续等待", controller.service["warning"])
        controller.owner.stop.assert_not_called()
        with patch.object(desktop_backend, "build_opener", return_value=self.health_reply(status="ready")):
            controller.poll_service()
        self.assertTrue(controller.runtime["ready"])
        self.assertEqual(controller.service["status"], "ready")

    def test_cached_second_start_is_browsable_while_models_are_still_gated(self):
        first = self.controller([self.synthetic_job(3)])
        first.start()
        self.wait_for(lambda: first.service["status"] == "ready")
        data = first.data
        first.shutdown()
        entered, release = threading.Event(), threading.Event()
        def runtime(progress=None):
            entered.set()
            release.wait(12)
            return {"ready": True, "models": []}
        second = LibraryController(data, self.resources, Path(sys.executable), "cached-second",
                                   self.events.append, defer_initialization=True)
        self.controllers.append(second)
        try:
            with patch.object(second, "runtime_state", side_effect=runtime), \
                    patch.object(desktop_backend, "aggregate_library", side_effect=AssertionError("No full rebuild on cache hit")):
                second.start()
                self.assertTrue(entered.wait(5))
                self.wait_for(lambda: second.service["status"] == "cached")
                self.assertFalse(second.runtime["ready"])
                self.assertFalse(second.initialized)
                with OPENER.open(second.service["localUrl"] + "review.json", timeout=3) as response:
                    self.assertEqual(json.load(response)["summary"]["frameCount"], 3)
                with self.assertRaisesRegex(RuntimeError, "初始化"):
                    second.dispatch("review_save", {})
                release.set()
                self.wait_for(lambda: second.service["status"] == "ready")
                self.assertTrue(second.runtime["ready"])
        finally:
            release.set()

    def test_cached_model_error_keeps_read_only_view_and_never_grants_save(self):
        first = self.controller([self.synthetic_job(2)])
        first.start()
        self.wait_for(lambda: first.service["status"] == "ready")
        first.shutdown()
        second = LibraryController(first.data, self.resources, Path(sys.executable), "cached-bad-model",
                                   self.events.append, defer_initialization=True)
        self.controllers.append(second)
        with patch.object(second, "runtime_state", return_value={"ready": False, "models": [],
                                                                 "error": "controlled model checksum mismatch"}):
            second.start()
            self.wait_for(lambda: second.startup["status"] == "error")
            self.wait_for(lambda: second.service["status"] == "cached")
            self.assertFalse(second.runtime["ready"])
            self.assertFalse(second.initialized)
            with OPENER.open(second.service["localUrl"] + "review.json", timeout=3) as response:
                self.assertTrue(json.load(response)["cached"])
            with self.assertRaisesRegex(RuntimeError, "初始化"):
                second.dispatch("review_save", {})

    def test_future_config_is_never_overwritten(self):
        data = self.root / "future-data"
        atomic_json(data / "desktop-library.json", {"version": 99, "sources": [], "preserve": "future"})
        before = (data / "desktop-library.json").read_bytes()
        with self.assertRaisesRegex(RuntimeError, "版本"):
            LibraryController(data, self.resources, sys.executable, "future", defer_initialization=True)
        self.assertEqual((data / "desktop-library.json").read_bytes(), before)

    def test_http_import_does_not_load_inference_but_detector_class_remains_compatible(self):
        check = (
            "import sys; sys.path.insert(0, sys.argv[1]); import batch_culling; "
            "assert 'cv2' not in sys.modules and 'onnxruntime' not in sys.modules; "
            "assert hasattr(batch_culling.NativeDetector, 'analyze')"
        )
        result = subprocess.run([sys.executable, "-B", "-c", check, str(ROOT / "scripts")],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_corrupt_registered_job_fails_without_replacing_previous_projection(self):
        source = self.synthetic_job(1)
        controller = self.controller([source])
        aggregate_library(controller.library, [source])
        previous = self.tree_hashes(controller.library)
        original_manifest = Path(source["jobs"][0]["path"]) / "manifest.json"
        original_manifest.write_text('{"source":"bad","photos":[', encoding="utf-8")
        controller.start()
        self.wait_for(lambda: controller.state()["startup"]["status"] == "error")
        self.assertEqual(self.tree_hashes(controller.library), previous)
        self.assertFalse(controller.runtime["ready"])
        self.assertIsNone(controller.server)
        self.assertTrue(controller.model_ready)
        self.assertFalse(controller.library_initialized)
        before_config = controller.config_path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "初始化"):
            controller.dispatch("configure_service", {"mode": "local", "port": self.port()})
        self.assertEqual(controller.config_path.read_bytes(), before_config)
        self.assertIsNone(controller.server)

    def test_exited_child_reports_error_without_touching_unrelated_process(self):
        controller = self.polling_controller()
        controller.server.poll.return_value = 7
        controller.poll_service()
        self.assertEqual(controller.startup["status"], "error")
        self.assertIn("退出", controller.service["error"])
        self.assertIsNone(controller.server)
        controller.owner.stop.assert_not_called()

    def test_http_listens_starting_while_manifest_validation_is_gated(self):
        controller = self.controller()
        aggregate_library(controller.library, [])
        port = controller.service_config["port"]
        entered, release = threading.Event(), threading.Event()
        actual_read = batch_culling.read_json
        reads = []
        servers = []
        actual_server = batch_culling.ThreadingHTTPServer.__init__
        def server_init(server, *args, **kwargs):
            actual_server(server, *args, **kwargs)
            servers.append(server)
        def read(path):
            if Path(path) == controller.library / "manifest.json":
                reads.append(1)
                if len(reads) == 2:
                    entered.set()
                    release.wait(10)
            return actual_read(path)
        with patch.object(batch_culling, "read_json", side_effect=read), \
                patch.object(batch_culling.ThreadingHTTPServer, "__init__", server_init):
            thread = threading.Thread(target=batch_culling.serve,
                                      args=(controller.library, port), kwargs={"instance_id": "gated-service"},
                                      daemon=True)
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                with OPENER.open(f"http://127.0.0.1:{port}/health.json", timeout=2) as response:
                    health = json.load(response)
                self.assertEqual(health["status"], "starting")
                self.assertEqual(health["instanceId"], "gated-service")
                with self.assertRaises(HTTPError) as error:
                    OPENER.open(f"http://127.0.0.1:{port}/review.json", timeout=2)
                self.assertEqual(error.exception.code, 503)
                release.set()
                def ready():
                    with OPENER.open(f"http://127.0.0.1:{port}/health.json", timeout=2) as response:
                        return json.load(response)["status"] == "ready"
                self.wait_for(ready)
            finally:
                release.set()
                for server in servers:
                    server.shutdown()
                thread.join(3)


if __name__ == "__main__":
    unittest.main()
