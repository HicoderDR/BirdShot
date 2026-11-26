# SPDX-License-Identifier: AGPL-3.0-only
"""Assemble an offline, relocatable Windows runtime. Never copies user photo jobs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import urllib.request
import zipfile

from desktop_runtime import PROFILE, prepare_python

ROOT = Path(__file__).resolve().parents[1]
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
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    version = args.version or package.get("desktopVersion") or package["version"]
    requested_output = (args.output or ROOT / "desktop-build" / version / "resources").resolve()
    for legacy in (ROOT / "desktop-build" / "resources", ROOT / "desktop-artifacts" / "BirdShot-win32-x64"):
        if requested_output == legacy.resolve() or legacy.resolve() in requested_output.parents:
            raise RuntimeError("Use a new versioned output; the original 0.2.0 runtime must remain untouched")
    # Extended paths apply only to our output, never to Windows system policy.
    output = Path("\\\\?\\" + str(requested_output))
    output.mkdir(parents=True, exist_ok=True)
    backend, runtime, licenses = output / "backend", output / "runtime", output / "licenses"
    python = runtime / "python"
    prepared = prepare_python(output, version, refresh=args.refresh_runtime,
                              zip_pure_python=args.zip_pure_python)
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
    source_files = [ROOT / "README.md", ROOT / "package.json", ROOT / "package-lock.json", ROOT / "LICENSE-CULLING.txt",
                    ROOT / ".gitignore", ROOT / "vite.config.ts", *ROOT.glob("tsconfig*.json"),
                    *[path for folder in ["desktop", "scripts", "src", "tests", "docs"] for path in (ROOT / folder).rglob("*")
                      if path.is_file() and path.suffix in {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".html", ".css", ".md", ".json", ".txt", ".iss", ".ps1"}
                      and "__pycache__" not in path.parts]]
    with zipfile.ZipFile(licenses / "corresponding-source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in source_files:
            archive.write(path, path.relative_to(ROOT))
    source_manifest = [{"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": sha(path)}
                       for path in sorted(set(source_files))]
    source_digest = hashlib.sha256(json.dumps(source_manifest, sort_keys=True).encode()).hexdigest()
    (licenses / "source-files.json").write_text(json.dumps({"version": version, "sourceDigest": source_digest,
        "files": source_manifest}, indent=2), encoding="utf-8")
    required = [models / "bird-yolo11s-seg.onnx", models / "eye.onnx",
                backend / "models" / "birder" / "hieradet_d_small_dino-v2-inat21.pt",
                backend / "models" / "birder" / "hieradet_d_small_dino-v2-inat21.json",
                backend / "assets" / "taxonomy" / "candidates.json",
                backend / "assets" / "taxonomy" / "taxonomy-ebird2025-zh-SIM.json",
                runtime / "node" / "node.exe", python / "python.exe"]
    manifest = {"version": version, "platform": "win32-x64", "python": platform.python_version(), "sourceDigest": source_digest,
                "runtimeProfile": PROFILE, "runtimeFingerprint": prepared["fingerprint"],
                "pythonLayout": prepared["pythonLayout"],
                "runtimeSelection": "runtime/python-selection.json",
                "assets": [{"path": path.relative_to(output).as_posix(), "bytes": path.stat().st_size, "sha256": sha(path)} for path in required],
                "modelsOffline": True, "encyclopediaOptionalNetwork": True,
                "licenses": "licenses", "modelLimitations": "AGPL weights; CUB training images not included and no commercial dataset rights granted"}
    (backend / "assets-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"prepared": True, "version": version, "requiredAssets": len(required),
                      "runtimeProfile": PROFILE, "runtimeFingerprint": prepared["fingerprint"],
                      "pythonRuntimeFiles": prepared["runtimeFiles"],
                      "runtimeBytes": sum(path.stat().st_size for path in runtime.rglob("*") if path.is_file())}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--version")
    parser.add_argument("--runtime-profile", "--profile", choices=[PROFILE], default=PROFILE)
    parser.add_argument("--node-root", type=Path, default=ROOT / ".tools" / "node-v24.11.1-win-x64")
    parser.add_argument("--model-root", type=Path, default=ROOT / ".tools" / "species-models")
    parser.add_argument("--taxonomy", type=Path, default=ROOT / ".tools" / "gallery-setup-check")
    parser.add_argument("--refresh-runtime", action="store_true")
    parser.add_argument("--zip-pure-python", action="store_true",
                        help="ZIP only the verified fixed pure-Python dependency whitelist; opt-in")
    main(parser.parse_args())
