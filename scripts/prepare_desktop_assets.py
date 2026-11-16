# SPDX-License-Identifier: AGPL-3.0-only
"""Assemble an offline, relocatable Windows runtime. Never copies user photo jobs."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.2.0"
EYE_CARD = "https://huggingface.co/synthet/eye-pose-v0/raw/3025db2b961e2e0c611bcb7d13f179a5d5d085f2/README.md"
BIRDER_SHA = "271fa9ed6a9aa1f4d1fc8bbb4c4cac9d15b264f2ac544efb5cd971412691880d"


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def excluded(folder, names):
    return [name for name in names if name in {"__pycache__", ".pytest_cache", ".git", "_virtualenv.pth", "_virtualenv.py"}
            or name.endswith((".pyc", ".pyo"))]


def copy_tree(source, target):
    if not source.is_dir():
        raise FileNotFoundError(f"Required build input is missing: {source.name}")
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=excluded)


def copy_file(source, target):
    if not source.is_file():
        raise FileNotFoundError(f"Required build asset is missing: {source.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def main(args):
    if os.name != "nt" or sys.version_info[:2] != (3, 12) or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("The verified desktop bundle requires Windows x64 CPython 3.12")
    # Torch ships deeply nested license/header paths; never change Windows policy.
    output = Path("\\\\?\\" + str(args.output.resolve()))
    output.mkdir(parents=True, exist_ok=True)
    backend, runtime, licenses = output / "backend", output / "runtime", output / "licenses"
    python = runtime / "python"
    stamp = output / "runtime-prepared.json"
    if not stamp.exists() or args.refresh_runtime:
        started = time.monotonic()
        base = Path(sys.base_prefix)
        python.mkdir(parents=True, exist_ok=True)
        for name in ["python.exe", "pythonw.exe", "python3.dll", "python312.dll", "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt"]:
            copy_file(base / name, python / name)
        copy_tree(base / "DLLs", python / "DLLs")
        copy_tree(base / "Lib", python / "Lib")
        site = Path(importlib.metadata.distribution("torch").locate_file(""))
        copy_tree(site, python / "Lib" / "site-packages")
        for path in (python / "Lib" / "site-packages").glob("*.pth"):
            if str(ROOT).casefold() in path.read_text(encoding="utf-8", errors="replace").casefold():
                raise RuntimeError(f"Non-relocatable package path in {path.name}")
        stamp.write_text(json.dumps({"python": platform.python_version(), "preparedSeconds": round(time.monotonic() - started, 2)}), encoding="utf-8")
    copy_file(ROOT / "scripts" / "desktop_sitecustomize.py", python / "Lib" / "site-packages" / "sitecustomize.py")
    # This still-image app does not ship the optional FFmpeg video plugin.
    video_plugin = python / "Lib" / "site-packages" / "cv2" / "opencv_videoio_ffmpeg4120_64.dll"
    if video_plugin.is_file():
        video_plugin.unlink()
    node_source = args.node_root
    for name in ["node.exe", "LICENSE"]:
        copy_file(node_source / name, runtime / "node" / name)
    for folder in ["scripts", "src"]:
        copy_tree(ROOT / folder, backend / folder)
    copy_file(ROOT / "package.json", backend / "package.json")
    models = backend / "public" / "culling" / "models"
    provenance = json.loads((ROOT / "public" / "culling" / "models" / "provenance.json").read_text(encoding="utf-8"))
    expected = {("bird-yolo11s-seg.onnx" if row["name"] == "bird-yolo11s-seg" else "eye.onnx"): row["onnxSha256"] for row in provenance}
    for name in ["bird-yolo11s-seg.onnx", "eye.onnx", "provenance.json"]:
        source = ROOT / "public" / "culling" / "models" / name
        if name in expected and sha(source) != expected[name]:
            raise RuntimeError(f"Prepared ONNX provenance mismatch: {name}")
        copy_file(source, models / name)
    for name in ["hieradet_d_small_dino-v2-inat21.pt", "hieradet_d_small_dino-v2-inat21.json"]:
        source = args.model_root / "birder" / name
        if name.endswith(".pt") and sha(source) != BIRDER_SHA:
            raise RuntimeError("Official Birder model hash mismatch")
        copy_file(source, backend / "models" / "birder" / name)
    for name in ["candidates.json", "taxonomy-ebird2025-zh-SIM.json"]:
        copy_file(args.taxonomy / name, backend / "assets" / "taxonomy" / name)
    licenses.mkdir(parents=True, exist_ok=True)
    copy_file(ROOT / "LICENSE-CULLING.txt", licenses / "AGPL-3.0.txt")
    copy_file(args.model_root / "birder" / "README.md", licenses / "Birder-model-card.md")
    card_cache = ROOT / ".tools" / "desktop-notices" / "eye-model-card.md"
    if not card_cache.exists():
        card_cache.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(EYE_CARD, timeout=30) as response:
            card_cache.write_bytes(response.read(1024 * 1024))
    copy_file(card_cache, licenses / "Eye-model-card.md")
    copy_file(ROOT / "docs" / "culling-demo.md", licenses / "Models-and-public-samples.md")
    dependencies = [{"name": distribution.metadata.get("Name"), "version": distribution.version,
                     "license": distribution.metadata.get("License-Expression") or distribution.metadata.get("License"),
                     "source": distribution.metadata.get("Home-page")}
                    for distribution in importlib.metadata.distributions()]
    (licenses / "python-dependencies.json").write_text(json.dumps(dependencies, ensure_ascii=False, indent=2), encoding="utf-8")
    # Wheel license files are retained beside their native libraries and dist-info.
    source_files = [ROOT / "README.md", ROOT / "package.json", ROOT / "package-lock.json", ROOT / "LICENSE-CULLING.txt",
                    ROOT / ".gitignore", ROOT / "vite.config.ts", *ROOT.glob("tsconfig*.json"),
                    *[path for folder in ["desktop", "scripts", "src", "tests", "docs"] for path in (ROOT / folder).rglob("*")
                      if path.is_file() and path.suffix in {".py", ".mjs", ".cjs", ".ts", ".tsx", ".html", ".css", ".md", ".json", ".txt"}
                      and "__pycache__" not in path.parts]]
    with zipfile.ZipFile(licenses / "corresponding-source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in source_files:
            archive.write(path, path.relative_to(ROOT))
    source_manifest = [{"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": sha(path)}
                       for path in sorted(set(source_files))]
    source_digest = hashlib.sha256(json.dumps(source_manifest, sort_keys=True).encode()).hexdigest()
    (licenses / "source-files.json").write_text(json.dumps({"version": VERSION, "sourceDigest": source_digest,
        "files": source_manifest}, indent=2), encoding="utf-8")
    required = [models / "bird-yolo11s-seg.onnx", models / "eye.onnx",
                backend / "models" / "birder" / "hieradet_d_small_dino-v2-inat21.pt",
                backend / "models" / "birder" / "hieradet_d_small_dino-v2-inat21.json",
                backend / "assets" / "taxonomy" / "candidates.json",
                backend / "assets" / "taxonomy" / "taxonomy-ebird2025-zh-SIM.json",
                runtime / "node" / "node.exe", python / "python.exe"]
    manifest = {"version": VERSION, "platform": "win32-x64", "python": platform.python_version(), "sourceDigest": source_digest,
                "assets": [{"path": path.relative_to(output).as_posix(), "bytes": path.stat().st_size, "sha256": sha(path)} for path in required],
                "modelsOffline": True, "encyclopediaOptionalNetwork": True,
                "licenses": "licenses", "modelLimitations": "AGPL weights; CUB training images not included and no commercial dataset rights granted"}
    (backend / "assets-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"prepared": True, "version": VERSION, "requiredAssets": len(required),
                      "runtimeBytes": sum(path.stat().st_size for path in runtime.rglob("*") if path.is_file())}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "desktop-build" / "resources")
    parser.add_argument("--node-root", type=Path, default=ROOT / ".tools" / "node-v24.11.1-win-x64")
    parser.add_argument("--model-root", type=Path, default=ROOT / ".tools" / "species-models")
    parser.add_argument("--taxonomy", type=Path, default=ROOT / ".tools" / "gallery-setup-check")
    parser.add_argument("--refresh-runtime", action="store_true")
    main(parser.parse_args())
