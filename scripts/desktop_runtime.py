# SPDX-License-Identifier: AGPL-3.0-only
"""RECORD-selected inference runtime; package sources and native resources stay on disk."""
from collections import defaultdict
import csv
import hashlib
import importlib.metadata
import json
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import sys
import time
import zipfile

from packaging.requirements import Requirement

PROFILE = "production"
SCHEMA = 1
PURE_PYTHON_ZIP = {
    "mpmath": ("mpmath",),
    "jinja2": ("jinja2",),
    "filelock": ("filelock",),
    "fsspec": ("fsspec",),
    "pygments": ("pygments",),
    "rich": ("rich",),
    "mdurl": ("mdurl",),
    "packaging": ("packaging",),
    "cloudpickle": ("cloudpickle",),
    "colorama": ("colorama",),
    "braceexpand": ("braceexpand",),
    "coloredlogs": ("coloredlogs",),
}
PURE_ZIP_ARCHIVE = "python-pure.zip"
ROOT_PACKAGES = {
    "birder": "Official model loader, transforms and full 10000-class architecture registry",
    "torch": "CPU checkpoint deserialization and eager inference",
    "torchvision": "Birder transforms, datasets imports and native operators",
    "onnxruntime": "CPU YOLO segmentation and eye-pose inference",
    "opencv-python": "Image preprocessing, masks and original-pixel metrics",
    "pillow": "Image decoding, EXIF, transforms and color management",
    "psutil": "Imported by birder.common.lib",
    "safetensors": "Imported by birder.common.fs_ops when available",
}
BIRDER_FEATURE_ONLY = {
    "faster-coco-eval": "COCO evaluation, not local still-image inference",
    "matplotlib": "Training/evaluation plotting",
    "onnx": "Model export; ONNX Runtime loads the shipped graphs without it",
    "onnxscript": "Model export",
    "pyarrow": "Dataset export/analysis",
    "pycocotools": "COCO evaluation",
    "tensorboard": "Training logging",
    "torchinfo": "Model summary tools",
    "torchmetrics": "Training/evaluation metrics",
}
# These are package-relative paths, not global filename/extension filters.
EXCLUDED_TREES = {
    "torch": ("torch/include", "torchgen/packaged"),
    "numpy": ("numpy/_core/include", "numpy/core/include", "numpy/_pyinstaller"),
    "onnxruntime": ("onnxruntime/tools", "onnxruntime/datasets",
                    "onnxruntime/transformers", "onnxruntime/quantization"),
}
TEST_PACKAGES = {
    "numpy", "scipy", "scikit-learn", "sympy", "mpmath", "joblib",
    "networkx", "psutil", "setuptools", "fsspec", "webdataset",
}
REQUIRED_TEST_RESOURCES = {
    "numpy": {"numpy/_core/tests/__init__.py", "numpy/_core/tests/_natype.py"},
}
STDLIB_EXCLUDED = {
    "site-packages", "test", "tests", "idlelib", "tkinter", "turtledemo",
    "ensurepip", "venv", "__phello__",
}
PYTHON_FILES = (
    "python.exe", "pythonw.exe", "python3.dll", "python312.dll",
    "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt",
)


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def digest_file(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def record_path(value):
    path = PurePosixPath(str(value).replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or ":" in str(path):
        return None
    return path


def exclusion_reason(package, path):
    if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
        return "bytecode-cache"
    text = path.as_posix()
    for prefix in EXCLUDED_TREES.get(package, ()):
        if text == prefix or text.startswith(prefix + "/"):
            return "unused-build-or-feature-tree:" + prefix
    if package == "torch" and path.parts[:2] == ("torch", "lib") and path.suffix == ".lib":
        return "torch-static-and-import-libraries"
    if (package in TEST_PACKAGES and text not in REQUIRED_TEST_RESOURCES.get(package, set())
            and any(part in {"tests", "test"} for part in path.parts[1:])):
        return "package-tests:" + package
    if package == "opencv-python" and path.name == "opencv_videoio_ffmpeg4120_64.dll":
        return "optional-video-plugin"
    return None


def is_license(path):
    if any(part.endswith(".dist-info") for part in path.parts):
        if any(part.casefold() in {"licenses", "license"} for part in path.parts):
            return True
    # Do not confuse executable packaging/licenses Python modules with notices.
    return path.suffix not in {".py", ".pyi", ".pyc", ".pyd", ".dll"} and bool(
        re.match(r"^(licen[cs]e|copying|notice|thirdpartynotices|copyright)(?:[._-]|$)",
                 path.name, re.IGNORECASE))


def pure_zip_member(package, path):
    roots = PURE_PYTHON_ZIP.get(package, ())
    root = path.parts[0]
    if len(path.parts) == 1 and path.suffix == ".pth":
        return False
    if root not in roots and not (len(path.parts) == 1 and path.stem in roots):
        return False
    if path.suffix not in {".py", ".pyi", ".typed"}:
        raise RuntimeError(f"Unverified ZIP resource in {package}: {path}")
    return True


def select_distributions(site):
    available = {}
    for distribution in importlib.metadata.distributions(path=[str(site)]):
        name = normalize(distribution.metadata["Name"])
        if name in available:
            raise RuntimeError(f"Ambiguous build distribution: {name}")
        available[name] = distribution
    selected, reasons, feature_only = {}, defaultdict(set), []
    pending = list(ROOT_PACKAGES)
    for name, reason in ROOT_PACKAGES.items():
        reasons[name].add(reason)
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        if name not in available:
            raise RuntimeError(f"Required production distribution is missing: {name}")
        distribution = available[name]
        if not distribution.files:
            raise RuntimeError(f"Production distribution has no wheel RECORD: {name}")
        selected[name] = distribution
        for value in distribution.requires or []:
            requirement = Requirement(value)
            if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
                continue
            dependency = normalize(requirement.name)
            if name == "birder" and dependency in BIRDER_FEATURE_ONLY:
                feature_only.append({"dependency": dependency, "requestedBy": name,
                                     "reason": BIRDER_FEATURE_ONLY[dependency]})
                continue
            if dependency not in available:
                raise RuntimeError(f"Missing dependency {dependency}, required by {name}")
            if requirement.specifier and available[dependency].version not in requirement.specifier:
                raise RuntimeError(f"Incompatible production dependency {requirement}")
            reasons[dependency].add("Requires-Dist of " + name)
            pending.append(dependency)
    return selected, reasons, feature_only, available


def runtime_plan(site, version, zip_pure_python=False):
    selected, reasons, feature_only, available = select_distributions(site)
    distributions = []
    for name, distribution in sorted(selected.items()):
        records = [path for path in distribution.files if path.name == "RECORD"
                   and len(path.parts) == 2 and path.parent.name.endswith(".dist-info")]
        if len(records) != 1:
            raise RuntimeError(f"Expected one wheel RECORD for {name}")
        distributions.append({
            "name": name, "version": distribution.version,
            "reasons": sorted(reasons[name]),
            "requiresDist": distribution.requires or [],
            "sourceRecordSha256": digest_file(Path(distribution.locate_file(records[0]))),
        })
    unselected = []
    for name, distribution in sorted(available.items()):
        if name in selected:
            continue
        files = [Path(distribution.locate_file(entry)) for entry in distribution.files or []
                 if record_path(entry) is not None]
        sizes = [path.stat().st_size for path in files if path.is_file()]
        unselected.append({
            "name": name, "version": distribution.version, "files": len(sizes), "bytes": sum(sizes),
            "reason": "Not in the production inference dependency closure",
        })
    payload = {
        "schema": SCHEMA, "profile": PROFILE, "version": version,
        "pythonLayout": "stdlib-and-pure-dependencies-zip" if zip_pure_python else "stdlib-zip",
        "python": platform.python_version(), "platform": "win32-x64",
        "builderSha256": digest_file(Path(__file__)),
        "distributions": distributions, "featureOnlyDependencies": feature_only,
        "policy": {"excludedTrees": EXCLUDED_TREES, "testPackages": sorted(TEST_PACKAGES),
                   "purePythonZipEnabled": zip_pure_python,
                   "purePythonZipWhitelist": PURE_PYTHON_ZIP,
                   "purePythonZipExcludedResources": {
                       "sympy": "LaTeX/Autolev grammar readers use physical __file__ paths",
                       "networkx": "graph_atlas_g passes its atlas resource to gzip.open",
                       "markdown-it-py": "Contains an additional YAML resource; left unchanged",
                       "tqdm": "Contains command-line completion/manpage resources; left unchanged",
                       "humanfriendly": "DeprecationProxy drops module loader/spec; keep physical sources for inspect",
                   },
                   "requiredTestResources": {name: sorted(paths) for name, paths in REQUIRED_TEST_RESOURCES.items()},
                   "retainedDynamicImports": {
                       "torch/testing": "torch.autograd.gradcheck and torch.utils.checkpoint import these utilities",
                       "numpy/f2py": "SciPy array_api_compat clones numpy.__all__",
                       "numpy/_core/tests/_natype.py": "numpy.testing._private.utils imports pd_NA",
                       "setuptools": "torch.utils.cpp_extension is imported by Torch's dynamic modules",
                       "scikit-learn": "birder.results.classification imports sklearn.metrics",
                       "polars": "birder.results.classification imports polars",
                   },
                   "stdlibExcluded": sorted(STDLIB_EXCLUDED),
                   "torchLibraries": "retain all DLLs, remove only torch/lib/*.lib",
                   "licenses": "archive all selected wheels' notices before exclusions",
                   "pythonSources": "stdlib ZIP; third-party sources/resources remain on disk"},
        "unselectedDistributions": unselected,
    }
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return selected, payload, fingerprint


def copy_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def build_stdlib(base, python, removals, zip_pure_python=False):
    for name in PYTHON_FILES:
        copy_file(base / name, python / name)
    for source in sorted((base / "DLLs").rglob("*")):
        if source.is_file():
            relative = source.relative_to(base / "DLLs")
            if source.suffix not in {".pyc", ".pyo"} and "__pycache__" not in relative.parts:
                copy_file(source, python / "DLLs" / relative)
    entries = 0
    with zipfile.ZipFile(python / "python312.zip", "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for source in sorted((base / "Lib").rglob("*")):
            if not source.is_file():
                continue
            relative = source.relative_to(base / "Lib")
            if relative.parts[0] in STDLIB_EXCLUDED:
                removals["stdlib-development-tools"]["files"] += 1
                removals["stdlib-development-tools"]["bytes"] += source.stat().st_size
            elif "__pycache__" in relative.parts or source.suffix in {".pyc", ".pyo"}:
                removals["bytecode-cache"]["files"] += 1
                removals["bytecode-cache"]["bytes"] += source.stat().st_size
            elif source.suffix == ".py":
                archive.write(source, relative.as_posix())
                entries += 1
            else:
                copy_file(source, python / "Lib" / relative)
    # _pth ignores system Python, PYTHONPATH and user sites. Backend scripts need
    # an explicit entry because isolated startup also omits the script directory.
    (python / "python312._pth").write_text(
        "python312.zip\nDLLs\nLib\n"
        + (PURE_ZIP_ARCHIVE + "\n" if zip_pure_python else "")
        + "Lib\\site-packages\n..\\..\\backend\\scripts\nimport site\n",
        encoding="utf-8")
    return entries


def inventory(root):
    return [{"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size,
             "sha256": digest_file(path)}
            for path in sorted(root.rglob("*")) if path.is_file()]


def cached_runtime_valid(python, manifest_path, fingerprint):
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["fingerprint"] != fingerprint or manifest["profile"] != PROFILE:
            return False
        actual = {path.relative_to(python).as_posix() for path in python.rglob("*") if path.is_file()}
        expected = {row["path"] for row in manifest["files"]}
        if actual != expected:
            return False
        return all((python / row["path"]).stat().st_size == row["bytes"]
                   and digest_file(python / row["path"]) == row["sha256"] for row in manifest["files"])
    except (OSError, ValueError, KeyError):
        return False


def publish_staged_runtime(stage, target):
    for attempt in range(21):
        try:
            return stage.rename(target)
        except PermissionError as error:
            if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 20:
                raise
            time.sleep(0.1)


def prepare_python(output, version, refresh=False, site=None, base=None, zip_pure_python=False):
    started = time.monotonic()
    site = Path(site or importlib.metadata.distribution("torch").locate_file(""))
    base = Path(base or sys.base_prefix)
    selected, plan, fingerprint = runtime_plan(site, version, zip_pure_python)
    runtime, licenses = output / "runtime", output / "licenses"
    python = runtime / "python"
    stamp = output / "runtime-prepared.json"
    selection = runtime / "python-selection.json"
    if not refresh and stamp.is_file() and cached_runtime_valid(python, selection, fingerprint):
        saved = json.loads(stamp.read_text(encoding="utf-8"))
        if (saved.get("fingerprint") == fingerprint and saved.get("profile") == PROFILE
                and saved.get("version") == version
                and (licenses / "python-third-party.zip").is_file()
                and saved.get("licenseArchiveSha256") == digest_file(licenses / "python-third-party.zip")):
            return saved
    runtime.mkdir(parents=True, exist_ok=True)
    licenses.mkdir(parents=True, exist_ok=True)
    stage = runtime / ".python-production-staging"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    removals = defaultdict(lambda: {"files": 0, "bytes": 0})
    license_index, license_rows, pure_members = [], {}, []
    pure_archive = None
    try:
        stdlib_entries = build_stdlib(base, stage, removals, zip_pure_python)
        if zip_pure_python:
            pure_archive = zipfile.ZipFile(stage / PURE_ZIP_ARCHIVE, "w", zipfile.ZIP_DEFLATED, compresslevel=6)
        target_site = stage / "Lib" / "site-packages"
        for name, distribution in sorted(selected.items()):
            kept, notices, zipped_paths = [], [], []
            record = next(path for path in distribution.files if path.name == "RECORD"
                          and len(path.parts) == 2 and path.parent.name.endswith(".dist-info"))
            metadata_dir = record.parent
            for entry in sorted(distribution.files, key=str):
                relative = record_path(entry)
                if relative is None:
                    category = "wheel-console-scripts"
                    source = Path(distribution.locate_file(entry))
                else:
                    source = Path(distribution.locate_file(entry))
                    if is_license(relative):
                        if not source.is_file():
                            raise FileNotFoundError(f"Missing license: {name}/{relative}")
                        member = name + "/" + relative.as_posix()
                        notices.append(member)
                        license_rows[member] = source
                        license_index.append({"distribution": name, "path": relative.as_posix(),
                                              "archivePath": member, "bytes": source.stat().st_size,
                                              "sha256": digest_file(source)})
                        category = "archived-license-files"
                    else:
                        category = exclusion_reason(name, relative)
                if category:
                    if source.is_file():
                        removals[category]["files"] += 1
                        removals[category]["bytes"] += source.stat().st_size
                    continue
                if relative == record:
                    continue
                if not source.is_file():
                    raise FileNotFoundError(f"Missing wheel input: {name}/{relative}")
                if pure_archive is not None and pure_zip_member(name, relative):
                    pure_archive.write(source, relative.as_posix())
                    zipped_paths.append(relative.as_posix())
                    pure_members.append({
                        "path": relative.as_posix(), "distribution": name,
                        "bytes": source.stat().st_size, "sha256": digest_file(source), "kind": "code",
                    })
                else:
                    copy_file(source, target_site / relative)
                    if (pure_archive is not None and name in PURE_PYTHON_ZIP
                            and len(relative.parts) == 1 and relative.suffix == ".pth"):
                        pure_archive.write(source, relative.as_posix())
                        pure_members.append({
                            "path": relative.as_posix(), "distribution": name,
                            "bytes": source.stat().st_size, "sha256": digest_file(source),
                            "kind": "startup-hook-mirror",
                        })
                wheel_hash = f"{entry.hash.mode}={entry.hash.value}" if entry.hash else ""
                kept.append([relative.as_posix(), wheel_hash, str(source.stat().st_size)])
            pointer = metadata_dir / "BIRDSHOT-LICENSES.json"
            pointer_file = target_site / pointer
            pointer_data = {
                "archive": "../../../../../licenses/python-third-party.zip",
                "index": "../../../../../licenses/python-license-files.json",
                "entries": notices,
            }
            if zipped_paths:
                pointer_data["pythonCode"] = {
                    "archive": "../../../" + PURE_ZIP_ARCHIVE,
                    "members": zipped_paths,
                    "note": "Wheel RECORD retains original member paths and hashes; "
                            "disk metadata is retained, with a metadata mirror inside the ZIP. "
                            "Relative notice paths resolve from the retained disk metadata directory.",
                }
            pointer_file.write_text(json.dumps(pointer_data, indent=2), encoding="utf-8")
            kept.extend([[pointer.as_posix(), "", str(pointer_file.stat().st_size)],
                         [record.as_posix(), "", ""]])
            record_file = target_site / record
            with record_file.open("w", newline="", encoding="utf-8") as stream:
                csv.writer(stream).writerows(kept)
            if zipped_paths:
                # CPython filters missing RECORD paths. A standard ZIP metadata
                # mirror resolves ZIP members while the disk metadata is retained.
                for metadata_file in sorted((target_site / metadata_dir).rglob("*")):
                    if not metadata_file.is_file():
                        continue
                    member = metadata_file.relative_to(target_site).as_posix()
                    pure_archive.write(metadata_file, member)
                    pure_members.append({
                        "path": member, "distribution": name,
                        "bytes": metadata_file.stat().st_size, "sha256": digest_file(metadata_file),
                        "kind": "metadata",
                    })
        if pure_archive is not None:
            pure_archive.close()
        copy_file(Path(__file__).with_name("desktop_sitecustomize.py"), target_site / "sitecustomize.py")
        archive_path = licenses / "python-third-party.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for member, source in sorted(license_rows.items()):
                archive.write(source, member)
        (licenses / "python-license-files.json").write_text(json.dumps({
            "archive": "python-third-party.zip", "files": license_index,
            "note": "Original wheel license/notice paths are preserved inside the archive. "
                    "Per-distribution BIRDSHOT-LICENSES.json points here.",
        }, indent=2), encoding="utf-8")
        dependencies = []
        for name, distribution in sorted(selected.items()):
            dependencies.append({
                "name": distribution.metadata["Name"], "version": distribution.version,
                "license": distribution.metadata.get("License-Expression") or distribution.metadata.get("License"),
                "source": distribution.metadata.get("Home-page"),
                "projectUrls": distribution.metadata.get_all("Project-URL", []),
                "licenseArchive": "python-third-party.zip",
                "licenseIndex": "python-license-files.json",
            })
        (licenses / "python-dependencies.json").write_text(
            json.dumps(dependencies, ensure_ascii=False, indent=2), encoding="utf-8")
        files = inventory(stage)
        report = {
            **plan, "fingerprint": fingerprint, "stdlibZipEntries": stdlib_entries,
            "files": files, "runtimeFiles": len(files),
            "runtimeBytes": sum(row["bytes"] for row in files),
            "removedCategories": dict(sorted(removals.items())),
            "licenseFiles": len(license_index), "licenseArchive": "licenses/python-third-party.zip",
            "pythonZip": {"enabled": zip_pure_python, "archive": PURE_ZIP_ARCHIVE,
                          "files": pure_members},
        }
        # Publish the stamp last; interrupted refreshes never look prepared.
        stamp.unlink(missing_ok=True)
        if python.exists():
            shutil.rmtree(python)
        publish_staged_runtime(stage, python)
        selection.write_text(json.dumps(report, indent=2), encoding="utf-8")
        saved = {
            "schema": SCHEMA, "profile": PROFILE, "version": version,
            "fingerprint": fingerprint, "python": platform.python_version(),
            "pythonLayout": plan["pythonLayout"],
            "preparedSeconds": round(time.monotonic() - started, 2),
            "runtimeFiles": len(files), "runtimeBytes": report["runtimeBytes"],
            "licenseArchiveSha256": digest_file(archive_path),
        }
        stamp.write_text(json.dumps(saved, indent=2), encoding="utf-8")
        return saved
    finally:
        if pure_archive is not None:
            pure_archive.close()
        if stage.exists():
            shutil.rmtree(stage)
