# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from batch_culling import initialize, inventory
from desktop_backend import LibraryController, REVISION, aggregate_library, atomic_json, read_json
from gallery_navigation import enrich_navigation
from preview_cache import PreviewCache

NODE = ROOT / ".tools" / "node-v24.11.1-win-x64" / "node.exe"
OPENER = build_opener(ProxyHandler({}))


class DesktopLibraryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=ROOT / ".tools", prefix="desktop-library-test-")
        self.root = Path(self.folder.name)
        self.resources = self.root / "resources"
        self.resources.mkdir()
        self.controllers = []

    def tearDown(self):
        for controller in self.controllers:
            controller.shutdown()
        self.folder.cleanup()

    def photo(self, root, name="one.jpg"):
        root.mkdir(parents=True, exist_ok=True)
        path = root / name
        Image.new("RGB", (64, 48), "green").save(path)
        return path

    def job(self, source, name, with_crop=True):
        job = self.root / name
        manifest = inventory(source)
        manifest["output"] = str(job)
        atomic_json(job / "manifest.json", manifest)
        groups, items, records = [], [], []
        photo = manifest["photos"][0]
        if with_crop:
            group = manifest["groups"][0]
            crop = "crops/g00001/p000001_b01.jpg"
            self.photo(job / "crops" / "g00001", "p000001_b01.jpg")
            self.photo(job / "crops" / "g00001", "p000001_b01_thumb.jpg")
            record = {"crop": crop, "groupId": group["id"], "photoId": photo["id"], "instanceId": 1,
                      "sourceRelative": photo["primary"]["relative"], "selected": True}
            records.append(record)
            items.append({"id": "p000001_b1", "frameId": photo["id"], "groupId": group["id"],
                          "instanceId": 1, "crop": crop, "thumbnail": crop[:-4] + "_thumb.jpg",
                          "sourceLabel": record["sourceRelative"], "selectedBefore": True,
                          "decision": "keep", "rank": 1, "preferredRank": 1,
                          "birdVerification": {"status": "bird"}, "species": {"top5": []},
                          "quality": {"tier": 0, "cohortSize": 7, "withinGroupRelative": .7}})
            detail = {"group": group, "frames": [{"id": photo["id"], "group": group["id"], "width": 64,
                       "height": 48, "instances": [{"id": 1, "body": [2, 3, 10, 12], "crop": crop,
                                                   "mask": {"runs": [0, 4], "width": 2, "height": 2}}]}]}
            atomic_json(job / REVISION / "groups" / (group["id"] + ".json"), detail)
            groups.append(group)
        atomic_json(job / "mapping.json", {"sourceRoot": str(source), "crops": records})
        atomic_json(job / "groups.json", groups)
        atomic_json(job / REVISION / "gallery.json", {"version": 1, "status": "completed", "items": items,
                                                     "shootGroups": [{"id": "legacy", "name": "原分组"}]})
        captures = {photo["id"]: {"capture": {"time": "2026-11-15T10:00:00", "location": None}}
                    for photo in manifest["photos"]}
        atomic_json(job / "revisions" / "capture-exif-v1.json", {"photos": captures})
        return job

    def controller(self, data="data"):
        controller = LibraryController(self.root / data, self.resources, NODE, "controlled-instance")
        self.controllers.append(controller)
        return controller

    def copy_service_resources(self):
        shutil.copytree(ROOT / "scripts", self.resources / "scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))

    def packaged_controller(self):
        resources = self.root / "package" / "resources"
        required = (
            "backend/public/culling/models/bird-yolo11s-seg.onnx", "backend/public/culling/models/eye.onnx",
            "backend/models/birder/hieradet_d_small_dino-v2-inat21.pt",
            "backend/models/birder/hieradet_d_small_dino-v2-inat21.json",
            "backend/assets/taxonomy/candidates.json", "backend/assets/taxonomy/taxonomy-ebird2025-zh-SIM.json",
            "runtime/node/node.exe", "runtime/python/python.exe",
        )
        assets = []
        for relative in required:
            path = resources / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("controlled asset: " + relative).encode("utf-8"))
            assets.append({"path": relative, "bytes": path.stat().st_size,
                           "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        backend = resources / "backend"
        atomic_json(backend / "assets-manifest.json", {"version": "0.2.0", "assets": assets})
        controller = LibraryController(self.root / "data", backend, resources / "runtime" / "node" / "node.exe",
                                       "eight-asset-fixture")
        self.controllers.append(controller)
        return controller, resources, assets

    def get(self, url):
        with OPENER.open(url, timeout=5) as response:
            return response.read()

    def test_metadata_navigation_merges_dates_separates_folders_counts_uncropped_and_keeps_decisions(self):
        first = self.root / "first"
        second = self.root / "second"
        self.photo(first)
        self.photo(first, "uncropped.jpg")
        self.photo(second)
        jobs = [self.job(first, "job-first"), self.job(second, "job-second")]
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                  for job in jobs for path in job.rglob("*") if path.is_file()}
        sources = [{"id": str(index), "name": source.name, "path": str(source), "status": "completed",
                    "jobs": [{"path": str(job), "managed": False}]}
                   for index, (source, job) in enumerate(zip((first, second), jobs))]
        gallery = aggregate_library(self.root / "aggregate", sources)
        self.assertEqual(len(gallery["navigation"]["groups"]["date"]), 1)
        self.assertEqual(gallery["navigation"]["groups"]["date"][0]["photoCount"], 3)
        self.assertEqual(gallery["navigation"]["groups"]["date"][0]["cropCount"], 2)
        self.assertEqual(gallery["navigation"]["groups"]["date"][0]["preferredCount"], 2)
        self.assertEqual(len(gallery["navigation"]["groups"]["folder"]), 2)
        self.assertEqual(gallery["navigation"]["groups"]["place"][0]["name"], "未知地点")
        self.assertEqual(len({item["frameId"] for item in gallery["items"]}), 2)
        self.assertEqual(len({item["groupId"] for item in gallery["items"]}), 2)
        self.assertTrue(all(item["quality"]["cohortSize"] == 7 for item in gallery["items"]))
        self.assertTrue(all(item["originalGroupId"] == "g00001" for item in gallery["items"]))
        self.assertTrue(all(frame["navigationGroups"] for frame in gallery["frames"]))
        self.assertEqual([row["id"] for row in gallery["shootGroups"]], ["legacy", "legacy"])
        self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before})
        again = aggregate_library(self.root / "aggregate", sources)
        self.assertEqual([item["id"] for item in again["items"]], [item["id"] for item in gallery["items"]])
        detail = read_json(self.root / "aggregate" / REVISION / "groups" / (gallery["items"][0]["groupId"] + ".json"))
        self.assertEqual(detail["frames"][0]["instances"][0]["body"], [2, 3, 10, 12])
        self.assertEqual(detail["frames"][0]["instances"][0]["mask"]["runs"], [0, 4])

    def test_place_requires_metadata_provenance_not_user_region_or_folder_name(self):
        source = self.root / "假想公园"
        self.photo(source)
        manifest = inventory(source)
        gallery = {"items": [], "shootGroups": [{"id": "unchanged"}], "userRegionReference": "假想地区"}
        metadata = {"photos": {"p000001": {"capture": {"placeName": "不能猜"}}}}
        result = enrich_navigation(gallery, manifest, metadata)
        self.assertEqual(result["navigation"]["groups"]["place"][0]["name"], "未知地点")
        metadata["photos"]["p000001"]["capture"]["placeSource"] = "jpeg:IPTC:Location"
        result = enrich_navigation(gallery, manifest, metadata)
        self.assertEqual(result["navigation"]["groups"]["place"][0]["name"], "不能猜")
        self.assertNotIn("navigation", gallery)
        self.assertEqual(result["shootGroups"], gallery["shootGroups"])

    def test_duplicate_folder_labels_use_shortest_unique_parent_without_changing_groups_or_counts(self):
        for index, suffix in enumerate((Path(), Path("2026"))):
            with self.subTest(shared_parent=str(suffix)):
                first = self.root / str(index) / "north" / suffix / "photos"
                second = self.root / str(index) / "south" / suffix / "photos"
                self.photo(first)
                self.photo(first, "uncropped.jpg")
                self.photo(second)
                jobs = [self.job(first, f"north-job-{index}"), self.job(second, f"south-job-{index}")]
                sources = [{"id": str(number), "name": source.name, "path": str(source), "status": "completed",
                            "jobs": [{"path": str(job)}]}
                           for number, (source, job) in enumerate(zip((first, second), jobs))]
                single = aggregate_library(self.root / f"single-{index}", sources[:1])
                self.assertEqual(single["navigation"]["groups"]["folder"][0]["name"], "photos")
                original_id = single["navigation"]["groups"]["folder"][0]["id"]
                result = aggregate_library(self.root / f"aggregate-{index}", sources)
                folders = result["navigation"]["groups"]["folder"]
                tail = "" if suffix == Path() else " / 2026"
                self.assertEqual({row["name"] for row in folders},
                                 {f"photos（north{tail}）", f"photos（south{tail}）"})
                self.assertEqual(len(folders), 2)
                first_row = next(row for row in folders if row["id"] == original_id)
                self.assertEqual((first_row["photoCount"], first_row["cropCount"], first_row["preferredCount"]), (2, 1, 1))
                self.assertEqual([sum(row[key] for row in folders)
                                  for key in ("photoCount", "cropCount", "preferredCount")], [3, 2, 2])
                self.assertTrue(all(str(self.root) not in row["name"] for row in folders))
                self.assertEqual({item["navigationGroups"]["folder"] for item in result["items"]},
                                 {row["id"] for row in folders})

    def test_completed_unknown_results_count_as_processed_without_changing_saved_selection(self):
        source = self.root / "photos"
        self.photo(source)
        job = self.job(source, "completed-old-job")
        path = job / REVISION / "gallery.json"
        saved = read_json(path)
        base = saved["items"][0]
        saved["items"] = []
        for index in range(1, 5505):
            item = dict(base)
            crop = f"crops/g00001/p000001_b{index:02d}.jpg"
            status = "bird" if index <= 544 else "nonBird" if index <= 3402 else "unknown"
            item.update(id=f"p000001_b{index}", instanceId=index, crop=crop, thumbnail=crop[:-4] + "_thumb.jpg",
                        decision="keep" if index <= 544 else "review", rank=index if index <= 544 else None,
                        preferredRank=index if index <= 544 else None, selectedBefore=index <= 544,
                        birdVerification={"status": status, "evidence": {"status": "completed"}})
            saved["items"].append(item)
        saved["progress"] = {"processed": 5504, "total": 5504}
        saved["items"][-1]["birdVerification"].pop("evidence")
        atomic_json(path, saved)
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        selected_ids = {item["id"] for item in saved["items"] if item["decision"] == "keep"}
        controller = self.controller()
        controller.attach_job(job)
        result = read_json(controller.library / REVISION / "gallery.json")
        self.assertEqual(result["progress"], {"processed": 5504, "total": 5504, "processedUnknown": 2102, "pending": 0})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["summary"]["preferred"], 544)
        self.assertEqual({item["originalItemId"] for item in result["items"] if item["decision"] == "keep"}, selected_ids)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_mapping_without_verification_is_explicitly_pending_not_processed_unknown(self):
        source = self.root / "photos"
        self.photo(source)
        job = self.job(source, "unclassified-old-job")
        (job / REVISION / "gallery.json").unlink()
        controller = self.controller()
        controller.attach_job(job)
        result = read_json(controller.library / REVISION / "gallery.json")
        self.assertEqual(result["progress"], {"processed": 0, "total": 1, "processedUnknown": 0, "pending": 1})
        self.assertEqual(result["summary"]["pending"], 1)
        self.assertEqual(result["summary"]["unknown"], 0)
        self.assertEqual(result["status"], "processing")
        self.assertEqual(result["items"][0]["birdVerification"]["status"], "unknown")
        self.assertEqual(result["items"][0]["processingStatus"], "pending")
        self.assertEqual(result["items"][0]["decision"], "review")

    def test_nested_inventory_and_aggregate_deduplicate_original_identity(self):
        parent = self.root / "photos"
        child = parent / "nested"
        photo = self.photo(child)
        self.photo(parent, "other.jpg")
        child_job = self.job(child, "child-job")
        parent_job = self.job(parent, "parent-job")
        remaining = inventory(parent, [str(photo)])
        self.assertEqual([photo["primary"]["relative"] for photo in remaining["photos"]], ["other.jpg"])
        sources = [{"id": str(index), "name": source.name, "status": "completed",
                    "jobs": [{"path": str(job)}]} for index, (source, job) in
                   enumerate(((child, child_job), (parent, parent_job)))]
        gallery = aggregate_library(self.root / "aggregate", sources)
        self.assertEqual(gallery["originalPhotoCount"], 2)
        self.assertEqual(len(gallery["items"]), 1)
        self.assertEqual(sum(row["photoCount"] for row in gallery["navigation"]["groups"]["folder"]), 2)

    def test_explicit_initialize_never_writes_resource_pointer_or_inside_originals(self):
        source = self.root / "photos"
        self.photo(source)
        versions = {"inference": "controlled", "policy": "controlled"}
        (self.resources / "scripts").mkdir()
        shutil.copyfile(ROOT / "scripts" / "batch-report.html", self.resources / "scripts" / "batch-report.html")
        with patch("batch_culling.versions", return_value=versions), patch("batch_culling.ROOT", self.resources):
            initialize(source, self.root / "managed" / "job")
        self.assertTrue((self.root / "managed" / "job" / "manifest.json").is_file())
        self.assertFalse((self.resources / ".tools").exists())
        with self.assertRaises(ValueError):
            initialize(source, source / "forbidden")
        self.assertFalse((source / "forbidden").exists())

    def test_preview_roots_are_whitelisted_per_frame_and_cannot_escape(self):
        first, second = self.root / "first", self.root / "second"
        one, two = self.photo(first), self.photo(second)
        photos = {"p1": {"sourceRoot": str(first), "supported": True, "primary": {"relative": one.name}},
                  "p2": {"sourceRoot": str(second), "supported": True, "primary": {"relative": two.name}}}
        cache = PreviewCache(self.root, photos, self.root / "cache", source_roots=[first, second])
        self.assertEqual(cache.describe("p1").path, one)
        self.assertEqual(cache.describe("p2").path, two)
        self.assertTrue(cache.get("p2", 960).data.startswith(b"\xff\xd8"))
        photos["p2"]["sourceRoot"] = str(self.root)
        with self.assertRaises(ValueError):
            cache.describe("p2")
        photos["p2"].update(sourceRoot=str(second), primary={"relative": "../first/one.jpg"})
        with self.assertRaises(ValueError):
            cache.describe("p2")
        with self.assertRaises(ValueError):
            PreviewCache(first, {}, second / "cache", source_roots=[first, second])

    def test_persistence_unbinding_and_output_safety_do_not_write_originals(self):
        source = self.root / "photos"
        self.photo(source)
        job = self.job(source, "old-job")
        original_files = sorted(path.relative_to(source) for path in source.rglob("*"))
        controller = self.controller()
        attached = controller.attach_job(job)
        controller.attach_job(job)
        self.assertEqual(len(controller.sources), 1)
        self.assertEqual(len(controller.sources[0]["jobs"]), 1)
        with self.assertRaises(ValueError):
            controller.set_output(source / "output")
        controller.remove_source(attached["id"])
        self.assertEqual(controller.state()["sources"], [])
        self.assertTrue((job / "manifest.json").is_file())
        restored = controller.add_source(source)
        self.assertEqual(restored["status"], "completed")
        self.assertEqual(restored["job"], str(job))
        controller.shutdown()
        restarted = self.controller()
        self.assertEqual(restarted.state()["sources"][0]["job"], str(job))
        self.assertEqual(sorted(path.relative_to(source) for path in source.rglob("*")), original_files)
        with self.assertRaises(FileNotFoundError):
            restarted.add_source(self.root / "missing")

    def test_occupied_port_is_not_adopted_or_terminated(self):
        controller = self.controller()
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            service = controller.configure_service(port=port)
            self.assertEqual(service["status"], "error")
            self.assertIn(str(port), service["error"])
            self.assertIsNone(service["localUrl"])
            self.assertIsNotNone(service["suggestedPort"])
            self.assertIsNone(controller.server)
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                pass

    def test_output_rejects_entire_packaged_resources_tree_before_creating_directories(self):
        packaged_resources = self.root / "app" / "resources"
        backend = packaged_resources / "backend"
        backend.mkdir(parents=True)
        controller = LibraryController(self.root / "data", backend, NODE, "readonly-resources")
        self.controllers.append(controller)
        original_output = controller.output_root
        for output in (packaged_resources, backend, backend / "new-output",
                       packaged_resources / "runtime" / "new-output"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                controller.set_output(output)
        self.assertEqual(controller.output_root, original_output)
        self.assertFalse((backend / "new-output").exists())
        self.assertFalse((packaged_resources / "runtime").exists())

    def test_interrupted_managed_job_is_restored_to_queue_without_touching_job(self):
        source = self.root / "photos"
        self.photo(source)
        job = self.job(source, "interrupted-job")
        data = self.root / "data"
        atomic_json(data / "desktop-library.json", {"sources": [{
            "id": "interrupted-source", "path": str(source), "name": source.name,
            "status": "processing", "job": str(job),
            "jobs": [{"path": str(job), "managed": True, "completed": False}],
        }]})
        before = (job / "manifest.json").read_bytes()
        controller = self.controller()
        self.assertEqual(controller.state()["sources"][0]["status"], "queued")
        self.assertEqual(controller.sources[0]["jobs"][0]["path"], str(job))
        self.assertEqual((job / "manifest.json").read_bytes(), before)

    def test_unreadable_legacy_index_is_an_explicit_persisted_source_error(self):
        source = self.root / "photos"
        self.photo(source)
        job = self.job(source, "broken-old-job")
        (job / "mapping.json").write_text("invalid controlled JSON", encoding="utf-8")
        controller = self.controller()
        controller.attach_job(job)
        saved = read_json(controller.config_path)
        self.assertEqual(controller.state()["sources"][0]["status"], "error")
        self.assertTrue(saved["sources"][0]["error"])
        self.assertEqual((job / "mapping.json").read_text(encoding="utf-8"), "invalid controlled JSON")

    def test_packaged_models_require_manifest_hashes_and_reject_corruption(self):
        with patch("desktop_backend.importlib.util.find_spec", return_value=True):
            controller, _, assets = self.packaged_controller()
        self.assertEqual(len(assets), 8)
        self.assertTrue(controller.runtime["ready"], controller.runtime)
        self.assertEqual(controller.runtime["mode"], "packaged-verified")
        self.assertTrue(all(model["ready"] and model["verified"] for model in controller.runtime["models"]))
        model = Path(controller.runtime["models"][0]["path"])
        model.write_bytes(b"x" * model.stat().st_size)
        changed = controller.runtime_state()
        self.assertFalse(changed["ready"])
        self.assertFalse(changed["models"][0]["ready"])
        self.assertIn("SHA256", changed["models"][0]["error"])
        self.assertTrue(changed["models"][1]["ready"])

    def test_both_packaged_runtime_executables_remain_sha256_verified(self):
        with patch("desktop_backend.importlib.util.find_spec", return_value=True):
            controller, resources, _ = self.packaged_controller()
            for relative in ("runtime/node/node.exe", "runtime/python/python.exe"):
                with self.subTest(asset=relative):
                    path = resources / relative
                    original = path.read_bytes()
                    path.write_bytes(b"x" * len(original))
                    changed = controller.runtime_state()
                    self.assertFalse(changed["ready"])
                    self.assertTrue(all(model["ready"] for model in changed["models"]))
                    self.assertTrue(any(error["path"] == relative and "SHA256" in error["error"]
                                        for error in changed["assetErrors"]))
                    path.write_bytes(original)
                    self.assertTrue(controller.runtime_state()["ready"])

    def test_packaged_manifest_rejects_absolute_traversal_and_unregistered_paths(self):
        with patch("desktop_backend.importlib.util.find_spec", return_value=True):
            controller, resources, assets = self.packaged_controller()
            for invalid in (str(resources / "runtime" / "node" / "node.exe"),
                            "../resources/runtime/node/node.exe", r"C:\outside\node.exe",
                            "runtime/node/unregistered.exe"):
                with self.subTest(path=invalid):
                    changed_assets = [dict(asset) for asset in assets]
                    changed_assets[-2]["path"] = invalid
                    atomic_json(controller.resources / "assets-manifest.json", {"assets": changed_assets})
                    changed = controller.runtime_state()
                    self.assertFalse(changed["ready"])
                    self.assertTrue(all(not model["ready"] for model in changed["models"]))

    def test_packaged_manifest_rejects_runtime_directory_junction_escape(self):
        with patch("desktop_backend.importlib.util.find_spec", return_value=True):
            controller, resources, _ = self.packaged_controller()
            node_dir = resources / "runtime" / "node"
            outside = self.root / "outside-runtime"
            outside.mkdir()
            shutil.copyfile(node_dir / "node.exe", outside / "node.exe")
            shutil.rmtree(node_dir)
            if os.name == "nt":
                result = subprocess.run([os.environ["COMSPEC"], "/c", "mklink", "/J", str(node_dir), str(outside)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            else:
                node_dir.symlink_to(outside, target_is_directory=True)
            changed = controller.runtime_state()
            self.assertFalse(changed["ready"])
            self.assertTrue(all("越界" in model["error"] for model in changed["models"]))

    def test_service_health_aggregate_media_uncropped_original_and_removal_revocation(self):
        self.copy_service_resources()
        source = self.root / "photos"
        self.photo(source)
        self.photo(source, "uncropped.jpg")
        job = self.job(source, "old-job")
        controller = self.controller()
        attached = controller.attach_job(job)
        port = controller.available_port(15274)
        service = controller.configure_service(port=port)
        self.assertEqual(service["status"], "ready", service)
        url = service["localUrl"]
        self.assertEqual(json.loads(self.get(url + "health.json"))["instanceId"], "controlled-instance")
        gallery = json.loads(self.get(url + "gallery.json"))
        self.assertEqual(gallery["originalPhotoCount"], 2)
        frame_id = gallery["frames"][1]["id"]
        self.assertTrue(self.get(url + f"preview/{frame_id}/960.jpg").startswith(b"\xff\xd8"))
        with Image.open(io.BytesIO(self.get(url + f"original/{frame_id}"))) as image:
            self.assertEqual(image.size, (64, 48))
        item = gallery["items"][0]
        self.assertTrue(self.get(url + "media/" + item["crop"]).startswith(b"\xff\xd8"))
        group_url = url + "gallery/group/" + item["groupId"] + ".json"
        self.assertIn("frames", json.loads(self.get(group_url)))
        with self.assertRaises(HTTPError) as forbidden:
            OPENER.open(Request(url + "cancel", data=b"", method="POST"), timeout=5)
        self.assertEqual(forbidden.exception.code, 403)
        self.assertFalse((controller.library / "cancel.request").exists())
        controller.remove_source(attached["id"])
        self.assertEqual(json.loads(self.get(url + "gallery.json"))["items"], [])
        for revoked in (group_url, url + f"original/{frame_id}", url + "media/" + item["crop"]):
            with self.assertRaises(HTTPError) as error:
                self.get(revoked)
            self.assertEqual(error.exception.code, 404)

    @unittest.skipUnless(os.name == "nt", "Windows ownership guarantee")
    def test_windows_job_kills_owned_children_on_hard_controller_crash(self):
        import ctypes
        from ctypes import wintypes
        helper = (
            "import sys,time,pathlib,os;"
            "sys.path.insert(0,sys.argv[1]);"
            "from desktop_backend import ProcessOwner;"
            "owner=ProcessOwner(include_current=True);"
            "log=open(sys.argv[2],'ab');"
            "p=owner.launch([sys.executable,'-c','import time; time.sleep(120)'],log,pathlib.Path.cwd(),os.environ.copy());"
            "print(p.pid,flush=True);time.sleep(120)"
        )
        parent = subprocess.Popen([sys.executable, "-B", "-c", helper, str(ROOT / "scripts"), str(self.root / "child.log")],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = None
        try:
            child_pid = int(parent.stdout.readline().strip())
            handle = kernel.OpenProcess(0x00100000, False, child_pid)
            self.assertTrue(handle)
            parent.kill()
            parent.wait(timeout=5)
            self.assertEqual(kernel.WaitForSingleObject(handle, 10000), 0)
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait()
            parent.stdout.close()
            parent.stderr.close()
            if handle:
                kernel.CloseHandle(handle)

    def test_controller_jsonl_protocol_errors_and_shutdown_are_framed(self):
        self.copy_service_resources()
        data = self.root / "protocol-data"
        data.mkdir()
        port = LibraryController.available_port(16274)
        atomic_json(data / "desktop-library.json", {"service": {"mode": "local", "port": port}, "sources": []})
        commands = [
            {"id": 1, "action": "state", "params": {}},
            {"id": 2, "action": "not_a_command", "params": {}},
            {"id": 3, "action": "shutdown", "params": {}},
        ]
        result = subprocess.run(
            [sys.executable, "-B", str(self.resources / "scripts" / "desktop_backend.py"),
             "--data", str(data), "--resources", str(self.resources), "--node", str(NODE), "--instance", "jsonl-test"],
            input="\n".join(json.dumps(command) for command in commands) + "\n", text=True,
            capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        messages = [json.loads(line) for line in result.stdout.splitlines()]
        ready = next(message for message in messages if message.get("event") == "controller-ready")
        self.assertEqual(ready["instanceId"], "jsonl-test")
        self.assertEqual(ready["state"]["service"]["status"], "starting")
        self.assertFalse(ready["state"]["runtime"]["ready"])
        self.assertFalse(any(message.get("event") == "ready" for message in messages))
        responses = {message["id"]: message for message in messages if "id" in message}
        self.assertTrue(responses[1]["ok"])
        self.assertFalse(responses[2]["ok"])
        self.assertTrue(responses[3]["ok"])
        self.assertEqual(responses[3]["result"], {"stopped": True})

    @unittest.skipUnless(os.environ.get("BIRDSHOT_RUN_MODEL_TESTS") == "1", "Opt-in one public-image real inference")
    def test_public_image_real_detection_pose_birder_gallery_and_restart(self):
        self.copy_service_resources()
        shutil.copytree(ROOT / "src", self.resources / "src")
        detector_root = self.resources / "public" / "culling" / "models"
        detector_root.mkdir(parents=True)
        for name in ("bird-yolo11s-seg.onnx", "eye.onnx"):
            os.link(ROOT / "public" / "culling" / "models" / name, detector_root / name)
        birder_root = self.resources / "models" / "birder"
        birder_root.mkdir(parents=True)
        for path in (ROOT / ".tools" / "species-models" / "birder").glob("*"):
            if path.name.startswith("hieradet_d_small_dino-v2-inat21") and path.is_file():
                os.link(path, birder_root / path.name)
        taxonomy = self.resources / "assets" / "taxonomy"
        taxonomy.mkdir(parents=True)
        for name in ("candidates.json", "taxonomy-ebird2025-zh-SIM.json"):
            shutil.copyfile(ROOT / ".tools" / "species-comparison-10" / name, taxonomy / name)
        resource_files = {path.relative_to(self.resources): (path.stat().st_size, path.stat().st_mtime_ns)
                          for path in self.resources.rglob("*") if path.is_file()}
        source = self.root / "public-sample"
        source.mkdir()
        shutil.copyfile(ROOT / "public" / "culling" / "samples" / "kingfisher" / "frame-00120.jpg", source / "bird.jpg")
        before = hashlib.sha256((source / "bird.jpg").read_bytes()).hexdigest()
        controller = self.controller()
        self.assertTrue(controller.runtime["ready"], controller.runtime)
        source_record = controller.add_source(source)
        controller.start_worker(source_record)
        deadline = time.monotonic() + 180
        while controller.worker and time.monotonic() < deadline:
            time.sleep(.5)
            controller.poll_worker()
        self.assertEqual(source_record["status"], "completed",
                         source_record.get("error", source_record.get("progress")))
        gallery = read_json(controller.library / REVISION / "gallery.json")
        self.assertTrue(gallery["items"])
        self.assertTrue(gallery["items"][0]["species"]["top3"])
        self.assertTrue(gallery["items"][0]["navigationGroups"])
        self.assertEqual(hashlib.sha256((source / "bird.jpg").read_bytes()).hexdigest(), before)
        self.assertEqual(resource_files, {path.relative_to(self.resources): (path.stat().st_size, path.stat().st_mtime_ns)
                                         for path in self.resources.rglob("*") if path.is_file()})
        job = source_record["job"]
        controller.shutdown()
        restarted = self.controller()
        self.assertEqual(restarted.state()["sources"][0]["status"], "completed")
        self.assertEqual(restarted.state()["sources"][0]["job"], job)


if __name__ == "__main__":
    unittest.main()
