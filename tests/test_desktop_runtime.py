# SPDX-License-Identifier: AGPL-3.0-only
"""Focused packaging tests and optional real, isolated offline model validation."""
import argparse
import csv
import hashlib
import importlib.metadata
import importlib.resources
import importlib.util
import inspect
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import unittest
from unittest.mock import Mock, patch
import uuid
import zipfile
import zipimport

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import desktop_runtime as runtime


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "tests" / (".desktop-runtime-test-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.site = self.root / "source-site"
        self.site.mkdir()
        self.base = self.root / "base"
        for name in runtime.PYTHON_FILES:
            path = self.base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        (self.base / "DLLs").mkdir()
        (self.base / "DLLs" / "_fixture.pyd").write_bytes(b"native")
        (self.base / "Lib" / "test").mkdir(parents=True)
        (self.base / "Lib" / "zipprobe.py").write_text("VALUE = 7\n", encoding="utf-8")
        (self.base / "Lib" / "test" / "test_fixture.py").write_text("unused\n", encoding="utf-8")

    def wheel(self, name, files, requires=()):
        metadata = self.site / (name.replace("-", "_") + "-1.0.dist-info")
        metadata.mkdir()
        header = f"Metadata-Version: 2.4\nName: {name}\nVersion: 1.0\nLicense-Expression: MIT\n"
        (metadata / "METADATA").write_text(
            header + "".join(f"Requires-Dist: {value}\n" for value in requires), encoding="utf-8")
        (metadata / "WHEEL").write_text("Wheel-Version: 1.0\n", encoding="utf-8")
        for relative, content in files.items():
            target = self.site / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        records = [*files, metadata.relative_to(self.site).as_posix() + "/METADATA",
                   metadata.relative_to(self.site).as_posix() + "/WHEEL",
                   metadata.relative_to(self.site).as_posix() + "/RECORD"]
        with (metadata / "RECORD").open("w", newline="", encoding="utf-8") as stream:
            csv.writer(stream).writerows([[value, "", ""] for value in records])
        return metadata

    def prepare(self, version="0.3.0", refresh=False, zip_pure_python=False):
        with patch.object(runtime, "ROOT_PACKAGES", {"torch": "fixture eager runtime"}):
            return runtime.prepare_python(self.root / "resources", version, refresh, self.site, self.base,
                                          zip_pure_python=zip_pure_python)

    def test_dependency_closure_keeps_real_birder_imports_but_not_export_features(self):
        self.wheel("birder", {"birder/__init__.py": b""},
                   ["scikit-learn>=1", "onnx>=1", "torchmetrics>=1", 'missing; extra == "dev"'])
        self.wheel("scikit-learn", {"sklearn/__init__.py": b""}, ["numpy>=1"])
        self.wheel("numpy", {"numpy/__init__.py": b""})
        with patch.object(runtime, "ROOT_PACKAGES", {"birder": "fixture"}):
            selected, _, skipped, _ = runtime.select_distributions(self.site)
        self.assertEqual(set(selected), {"birder", "scikit-learn", "numpy"})
        self.assertEqual({row["dependency"] for row in skipped}, {"onnx", "torchmetrics"})
        self.wheel("torch", {"torch/__init__.py": b""}, ["numpy>=2"])
        with patch.object(runtime, "ROOT_PACKAGES", {"torch": "fixture"}):
            with self.assertRaisesRegex(RuntimeError, "Incompatible"):
                runtime.select_distributions(self.site)

    def test_scoped_selection_preserves_native_resources_metadata_and_all_notices(self):
        self.wheel("torch", {
            "torch/__init__.py": b"VALUE = 3\n", "torch/_C.pyd": b"native",
            "torch/lib/torch_cpu.dll": b"cpu", "torch/lib/torch_cpu.lib": b"link",
            "torch/include/header.h": b"headers", "torch/include/LICENSE.txt": b"header-license",
            "torch/resources/example.onnx": b"runtime-resource",
            "torch/packaging/licenses/__init__.py": b"runtime-code",
            "torch-1.0.dist-info/licenses/third_party/deep/LICENSE": b"third-party",
        })
        saved = self.prepare()
        python = self.root / "resources" / "runtime" / "python"
        site = python / "Lib" / "site-packages"
        self.assertFalse((site / "torch" / "include").exists())
        self.assertFalse((site / "torch" / "lib" / "torch_cpu.lib").exists())
        self.assertEqual((site / "torch" / "lib" / "torch_cpu.dll").read_bytes(), b"cpu")
        self.assertTrue((site / "torch" / "resources" / "example.onnx").is_file())
        self.assertTrue((site / "torch" / "packaging" / "licenses" / "__init__.py").is_file())
        distribution = next(importlib.metadata.distributions(path=[str(site)]))
        self.assertEqual(distribution.version, "1.0")
        self.assertEqual(distribution.metadata["License-Expression"], "MIT")
        self.assertTrue(any(str(path) == "torch/_C.pyd" for path in distribution.files))
        pointer_file = site / "torch-1.0.dist-info" / "BIRDSHOT-LICENSES.json"
        pointer = json.loads(pointer_file.read_text(encoding="utf-8"))
        archive = (pointer_file.parent / pointer["archive"]).resolve()
        with zipfile.ZipFile(archive) as notices:
            self.assertEqual(notices.read("torch/torch/include/LICENSE.txt"), b"header-license")
            self.assertEqual(notices.read("torch/torch-1.0.dist-info/licenses/third_party/deep/LICENSE"), b"third-party")
        importer = zipimport.zipimporter(str(python / "python312.zip"))
        self.assertEqual(importer.get_source("zipprobe").splitlines(), ["VALUE = 7"])
        with zipfile.ZipFile(python / "python312.zip") as stdlib:
            self.assertNotIn("test/test_fixture.py", stdlib.namelist())
        self.assertEqual(saved["profile"], "production")
        self.assertIn("..\\..\\backend\\scripts", (python / "python312._pth").read_text(encoding="utf-8"))

    def test_refresh_detects_tampering_stale_files_old_stamps_and_version_changes(self):
        self.wheel("torch", {"torch/__init__.py": b"original"})
        saved = self.prepare()
        python = self.root / "resources" / "runtime" / "python"
        selection = self.root / "resources" / "runtime" / "python-selection.json"
        source = python / "Lib" / "site-packages" / "torch" / "__init__.py"
        source.write_bytes(b"tampered")
        self.assertFalse(runtime.cached_runtime_valid(python, selection, saved["fingerprint"]))
        stale = python / "Lib" / "site-packages" / "pip.py"
        stale.write_bytes(b"obsolete")
        stamp = self.root / "resources" / "runtime-prepared.json"
        stamp.write_text('{"python":"3.12","preparedSeconds":1}', encoding="utf-8")
        refreshed = self.prepare()
        self.assertEqual(source.read_bytes(), b"original")
        self.assertFalse(stale.exists())
        self.assertEqual(refreshed["fingerprint"], saved["fingerprint"])
        newer = self.prepare(version="0.3.1")
        self.assertNotEqual(newer["fingerprint"], saved["fingerprint"])
        self.assertEqual(self.prepare(version="0.3.1"), newer)
        archive = self.root / "resources" / "licenses" / "python-third-party.zip"
        archive.write_bytes(b"broken")
        self.prepare(version="0.3.1")
        self.assertTrue(zipfile.is_zipfile(archive))

    def test_record_paths_cannot_escape_and_extensions_are_not_globally_removed(self):
        self.assertIsNone(runtime.record_path("../../Scripts/pip.exe"))
        self.assertIsNone(runtime.record_path("C:\\outside\\secret"))
        self.assertIsNone(runtime.record_path("/outside"))
        self.assertEqual(runtime.exclusion_reason("birder", PurePosixPath("birder/data/model.onnx")), None)
        self.assertEqual(runtime.exclusion_reason("scipy", PurePosixPath("scipy/legitimate.h")), None)
        self.assertEqual(runtime.exclusion_reason("numpy", PurePosixPath("numpy/linalg/tests/test_x.py")),
                         "package-tests:numpy")
        self.assertIsNone(runtime.exclusion_reason("numpy", PurePosixPath("numpy/_core/tests/_natype.py")))
        self.assertIsNone(runtime.exclusion_reason("torch", PurePosixPath("torch/testing/_internal/logging_tensor.py")))

    def test_opt_in_zip_keeps_metadata_native_and_path_resources_on_disk(self):
        self.wheel("torch", {"torch/__init__.py": b"", "torch/lib/torch_cpu.dll": b"native"},
                   ["mpmath>=1", "sympy>=1"])
        self.wheel("mpmath", {"mpmath/__init__.py": b"VALUE = 7\n",
                              "mpmath/py.typed": b"", "mpmath.pth": b"# startup hook\n"})
        self.wheel("sympy", {"sympy/__init__.py": b"",
                             "sympy/parsing/grammar.lark": b"real-path-resource"})
        plain = self.prepare()
        zipped = self.prepare(zip_pure_python=True)
        self.assertNotEqual(plain["fingerprint"], zipped["fingerprint"])
        python = self.root / "resources" / "runtime" / "python"
        site = python / "Lib" / "site-packages"
        self.assertFalse((site / "mpmath").exists())
        self.assertTrue((site / "mpmath.pth").is_file())
        self.assertEqual((site / "torch" / "lib" / "torch_cpu.dll").read_bytes(), b"native")
        self.assertTrue((site / "sympy" / "parsing" / "grammar.lark").is_file())
        self.assertEqual(zipimport.zipimporter(str(python / runtime.PURE_ZIP_ARCHIVE))
                         .get_source("mpmath").splitlines(), ["VALUE = 7"])
        metadata = next(d for d in importlib.metadata.distributions(path=[str(site)])
                        if d.metadata["Name"] == "mpmath")
        self.assertEqual(metadata.version, "1.0")
        self.assertTrue((site / "mpmath-1.0.dist-info" / "METADATA").is_file())
        self.assertIn("mpmath/__init__.py", metadata.read_text("RECORD"))
        with zipfile.ZipFile(python / runtime.PURE_ZIP_ARCHIVE) as code:
            zip_metadata = importlib.metadata.PathDistribution(
                zipfile.Path(code) / "mpmath-1.0.dist-info")
            zip_entry = next(entry for entry in zip_metadata.files if str(entry) == "mpmath/__init__.py")
            self.assertEqual(zip_metadata.locate_file(zip_entry).read_bytes(), b"VALUE = 7\n")
        pointer = site / "mpmath-1.0.dist-info" / "BIRDSHOT-LICENSES.json"
        guidance = json.loads(pointer.read_text(encoding="utf-8"))["pythonCode"]
        self.assertTrue((pointer.parent / guidance["archive"]).resolve().is_file())
        self.assertEqual(set(guidance["members"]), {"mpmath/__init__.py", "mpmath/py.typed"})
        self.assertIn(runtime.PURE_ZIP_ARCHIVE, (python / "python312._pth").read_text(encoding="utf-8"))
        self.assertEqual(self.prepare(zip_pure_python=True), zipped)
        self.prepare()
        self.assertTrue((site / "mpmath" / "__init__.py").is_file())
        self.assertFalse((python / runtime.PURE_ZIP_ARCHIVE).exists())
        with self.assertRaisesRegex(RuntimeError, "Unverified ZIP resource"):
            runtime.pure_zip_member("mpmath", PurePosixPath("mpmath/new-native.pyd"))
        self.assertFalse(runtime.pure_zip_member("sympy", PurePosixPath("sympy/grammar.lark")))

    def test_publishing_retries_transient_windows_locks_without_changing_permissions(self):
        error = PermissionError("brief Windows scanner lock")
        error.winerror = 32
        stage = Mock()
        stage.rename.side_effect = [error, self.root]
        with patch.object(runtime.time, "sleep") as pause:
            self.assertEqual(runtime.publish_staged_runtime(stage, self.root), self.root)
            self.assertEqual(stage.rename.call_count, 2)
            pause.assert_called_once_with(0.1)
        stage.rename.side_effect = PermissionError("persistent non-Windows permission error")
        with patch.object(runtime.time, "sleep") as pause:
            with self.assertRaises(PermissionError):
                runtime.publish_staged_runtime(stage, self.root)
            pause.assert_not_called()


def validate_offline(resources, sample):
    """Run with the packaged interpreter, System32-only PATH and OFFLINE=1."""
    import datetime
    import socket
    import time
    python = resources / "runtime" / "python"
    backend = resources / "backend"
    if Path(sys.executable).resolve().parent != python.resolve():
        raise RuntimeError("Validation must use this bundle's python.exe")
    if os.environ.get("BIRDSHOT_OFFLINE") != "1" or not sys.flags.isolated:
        raise RuntimeError("Validation requires isolated Python and BIRDSHOT_OFFLINE=1")
    assert sys.dont_write_bytecode and os.environ.get("PYTHONNOUSERSITE") == "1"
    assert all(".tools" not in path.casefold() for path in sys.path)
    with socket.socket() as connection:
        try:
            connection.connect(("192.0.2.1", 80))
        except OSError as error:
            assert "offline mode" in str(error)
        else:
            raise AssertionError("External network was not blocked")
    manifest = json.loads((backend / "assets-manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["assets"]) == 8
    for row in manifest["assets"]:
        asset = resources / row["path"]
        assert asset.stat().st_size == row["bytes"] and runtime.digest_file(asset) == row["sha256"]
    selection = json.loads((resources / "runtime" / "python-selection.json").read_text(encoding="utf-8"))
    assert selection["profile"] == "production"
    assert runtime.cached_runtime_valid(python, resources / "runtime" / "python-selection.json",
                                        selection["fingerprint"])
    for name in ("pip", "ultralytics", "open_clip", "onnx", "onnxscript"):
        assert importlib.util.find_spec(name) is None, name
    sys.path.insert(0, str(backend / "scripts"))
    import batch_inference
    import desktop_backend
    import batch_culling
    import capture_metadata
    import gallery_navigation
    import gallery_birder
    import preview_cache
    import birder
    import torch
    import cv2
    import numpy as np
    from PIL import Image
    from torchvision import ops
    assert torch.version.cuda is None
    assert ops.nms(torch.tensor([[0., 0., 2., 2.]]), torch.tensor([.9]), .5).tolist() == [0]
    started = time.perf_counter()
    detector = batch_inference.NativeDetector(threads=4)
    image = cv2.imread(str(sample))
    assert image is not None
    height, width = image.shape[:2]
    detections = detector.infer(image, [0, 0, width, height])
    assert detections, "Authorized bird sample must produce a real YOLO detection"
    box = detections[0]["box"]
    pose = detector.infer(image, box, pose=True)
    assert pose, "Authorized bird sample must produce a real eye-pose detection"
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    model_id = "hieradet_d_small_dino-v2-inat21"
    checkpoint = backend / "models" / "birder" / (model_id + ".pt")
    assert runtime.digest_file(checkpoint) == "271fa9ed6a9aa1f4d1fc8bbb4c4cac9d15b264f2ac544efb5cd971412691880d"
    net, info, transform = birder.load_pretrained_model_and_transform(
        model_id, dst=checkpoint, inference=True, progress_bar=False, device=torch.device("cpu"))
    net.eval()
    bird_ids = [index for label, index in info.class_to_idx.items() if "Aves" in label.split("_")]
    assert len(info.class_to_idx) == 10000 and len(bird_ids) == 1486
    with Image.open(sample) as photo:
        x, y, w, h = box
        tensor = transform(photo.convert("RGB").crop((int(x), int(y), int(x + w), int(y + h))))
    with torch.inference_mode():
        logits = net(tensor.unsqueeze(0))
        probabilities = logits.softmax(-1)[0]
    assert list(logits.shape) == [1, 10000] and torch.isfinite(logits).all()
    assert abs(float(probabilities.sum()) - 1) < 1e-5
    inverse = {index: label for label, index in info.class_to_idx.items()}
    top = probabilities.topk(5)
    assert "Aves" in inverse[int(top.indices[0])].split("_")
    assert inspect.getsource(json.JSONEncoder.default)
    assert "python312.zip" in datetime.__file__
    versions = {row["name"]: importlib.metadata.version(row["name"]) for row in selection["distributions"]}
    zip_plan = selection.get("pythonZip", {"enabled": False, "files": []})
    zip_members = {row["path"]: row for row in zip_plan["files"]}
    for name in versions:
        distribution = importlib.metadata.distribution(name)
        assert distribution.files
        for entry in distribution.files:
            if not distribution.locate_file(entry).is_file():
                assert zip_members[str(entry)]["distribution"] == runtime.normalize(name)
            if entry.hash:
                assert entry.hash.mode == "sha256" and len(entry.hash.value) == 43
    zip_validation = {"enabled": zip_plan["enabled"], "files": len(zip_members)}
    if zip_plan["enabled"]:
        import io
        import mpmath
        from mpmath.libmp.libmpf import from_float
        import jinja2
        import filelock
        import fsspec
        import cloudpickle
        import humanfriendly
        import networkx
        import sympy
        from pygments import highlight
        from pygments.lexers import PythonLexer
        from pygments.formatters import HtmlFormatter
        from rich.console import Console
        from rich.markdown import Markdown
        from networkx.generators.atlas import ATLAS_FILE
        package_distributions = importlib.metadata.packages_distributions()
        for distribution_name, roots in runtime.PURE_PYTHON_ZIP.items():
            for name in roots:
                module = importlib.import_module(name)
                assert isinstance(module.__loader__, zipimport.zipimporter), name
                assert zip_plan["archive"] in module.__file__, name
                assert distribution_name in {runtime.normalize(value)
                                             for value in package_distributions[name]}
        for name in ("torch", "birder", "numpy", "scipy", "cv2", "sympy", "networkx"):
            module = importlib.import_module(name)
            assert zip_plan["archive"] not in module.__file__, name
        assert isinstance(ATLAS_FILE, Path) and len(networkx.graph_atlas_g()) == 1253
        assert (Path(sympy.__file__).parent / "parsing" / "latex" / "lark" / "grammar" / "latex.lark").is_file()
        assert abs(float(mpmath.sqrt(2)) ** 2 - 2) < 1e-12
        assert jinja2.Environment().from_string("{{ model }}").render(model="Birder") == "Birder"
        assert importlib.resources.files("jinja2").joinpath("py.typed").read_bytes() == b""
        assert "span" in highlight("import torch", PythonLexer(), HtmlFormatter())
        memory = fsspec.filesystem("memory")
        memory.pipe("zip-probe", b"offline")
        assert memory.cat("zip-probe") == b"offline"
        assert fsspec.filesystem("file").cat_file(str(checkpoint.with_suffix(".json")))
        lock_path = resources / "runtime" / ".pure-zip-validation.lock"
        try:
            with filelock.FileLock(lock_path, timeout=1):
                assert lock_path.is_file()
        finally:
            lock_path.unlink(missing_ok=True)
        assert cloudpickle.loads(cloudpickle.dumps(lambda: 7))() == 7
        assert humanfriendly.parse_size("1 MiB") == 1024 * 1024
        assert zip_plan["archive"] not in humanfriendly.__file__
        assert inspect.getsource(humanfriendly.parse_size)
        console_output = io.StringIO()
        Console(file=console_output).print(Markdown("# offline"))
        assert "offline" in console_output.getvalue()
        for function in (from_float, jinja2.Environment.from_string, filelock.BaseFileLock.acquire,
                         fsspec.core.open_files, PythonLexer.get_tokens):
            assert inspect.getsource(function)
        with zipfile.ZipFile(python / zip_plan["archive"]) as code:
            assert set(code.namelist()) == set(zip_members)
            for member, row in zip_members.items():
                assert hashlib.sha256(code.read(member)).hexdigest() == row["sha256"]
        zip_validation.update(
            zippedDistributions=len(runtime.PURE_PYTHON_ZIP), sourceInspection=True,
            metadataOnDisk=True, metadataPackageMapping=True, fileLock=True, fileAndMemoryFilesystems=True,
            dynamicPygmentsLexer=True, jinjaTemplateAndResource=True,
            richMarkdown=True, cloudpickle=True, sympyAndAtlasResourcesOnDisk=True)
    assert versions["scikit-learn"] and versions["polars"] and versions["setuptools"]
    loaded = sorted({name.split(".")[0] for name in sys.modules})
    license_index = json.loads((resources / "licenses" / "python-license-files.json").read_text(encoding="utf-8"))
    with zipfile.ZipFile(resources / "licenses" / "python-third-party.zip") as notices:
        for row in license_index["files"]:
            assert hashlib.sha256(notices.read(row["archivePath"])).hexdigest() == row["sha256"]
    return {
        "offline": True, "isolated": bool(sys.flags.isolated), "sysPath": sys.path,
        "python": sys.executable, "versions": versions, "loadedTopLevelModules": loaded,
        "assetsVerified": len(manifest["assets"]), "runtimeFingerprint": selection["fingerprint"],
        "runtimeFiles": selection["runtimeFiles"], "runtimeBytes": selection["runtimeBytes"],
        "sampleSha256": runtime.digest_file(sample),
        "yoloDetections": len(detections), "poseDetections": len(pose),
        "classes": len(info.class_to_idx), "birdClasses": len(bird_ids),
        "logitsShape": list(logits.shape), "birdMass": float(probabilities[bird_ids].sum()),
        "top5": [{"class": inverse[int(index)], "probability": float(score)}
                 for index, score in zip(top.indices, top.values)],
        "transform": str(transform), "stdlibSourceInspection": True,
        "metadataDistributionsVerified": len(versions),
        "licenseFilesVerified": len(license_index["files"]),
        "purePythonZip": zip_validation,
        "pipelineImports": [desktop_backend.__name__, batch_culling.__name__, capture_metadata.__name__,
                            gallery_navigation.__name__, gallery_birder.__name__, batch_inference.__name__],
        "seconds": round(time.perf_counter() - started, 2),
    }


if __name__ == "__main__":
    if "--validate-runtime" in sys.argv:
        parser = argparse.ArgumentParser()
        parser.add_argument("--validate-runtime", type=Path, required=True)
        parser.add_argument("--sample", type=Path, required=True)
        parser.add_argument("--report", type=Path, required=True)
        args = parser.parse_args()
        report = validate_offline(args.validate_runtime.resolve(), args.sample.resolve())
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({key: report[key] for key in (
            "offline", "isolated", "runtimeFiles", "runtimeBytes", "assetsVerified",
            "yoloDetections", "poseDetections", "classes", "birdClasses",
            "licenseFilesVerified", "seconds")}))
    else:
        unittest.main()
