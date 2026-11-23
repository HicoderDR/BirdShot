# SPDX-License-Identifier: AGPL-3.0-only
"""Publish one complete local portable ZIP, never a GitHub Release upload."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main(args):
    directory = args.directory.resolve()
    if not (directory / "BirdShot.exe").is_file():
        raise RuntimeError("Build and verify the actual Windows app before finalizing")
    resources = directory / "resources"
    assets = json.loads((resources / "backend" / "assets-manifest.json").read_text(encoding="utf-8"))
    source = json.loads((resources / "licenses" / "source-files.json").read_text(encoding="utf-8"))
    for row in source["files"]:
        path = ROOT / row["path"]
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != row["sha256"]:
                raise RuntimeError("Source changed after bundle refresh: " + row["path"])
    commit = subprocess.check_output(["git", "rev-parse", args.source_ref], cwd=ROOT, text=True).strip()
    for row in source["files"]:
        recorded = subprocess.check_output(["git", "rev-parse", commit + ":" + row["path"]], cwd=ROOT, text=True).strip()
        current = subprocess.check_output(["git", "hash-object", "--path", row["path"], row["path"]], cwd=ROOT, text=True).strip()
        if recorded != current:
            raise RuntimeError("Publication commit does not contain bundled source: " + row["path"])
    build = {"version": assets["version"], "platform": "win32-x64", "sourceCommit": commit,
             "sourceDigest": source["sourceDigest"], "electron": "45.2.3", "python": assets["python"],
             "codeSigned": False, "runtimeIncluded": True, "modelsIncluded": True,
             "privatePhotosIncluded": False, "licenses": "resources/licenses"}
    (directory / "BUILD.json").write_text(json.dumps(build, indent=2), encoding="utf-8")
    destination = ROOT / "desktop-artifacts" / f"BirdShot-{assets['version']}-win-x64.zip"
    temporary = destination.with_suffix(".zip.part")
    long_directory = Path(chr(92) * 2 + "?" + chr(92) + str(directory))
    count, uncompressed = 0, 0
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for path in sorted(long_directory.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}:
                archive.write(path, directory.name + "/" + path.relative_to(long_directory).as_posix())
                count += 1
                uncompressed += path.stat().st_size
    temporary.replace(destination)
    with destination.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    proof = {**build, "filename": destination.name, "bytes": destination.stat().st_size,
             "sha256": digest, "files": count, "uncompressedBytes": uncompressed}
    destination.with_suffix(".manifest.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(json.dumps(proof))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    version = json.loads((ROOT / "package.json").read_text())["desktopVersion"]
    parser.add_argument("--directory", type=Path, default=ROOT / "desktop-artifacts" / f"BirdShot-{version}-win32-x64")
    parser.add_argument("--source-ref", default="refs/birdshot-publications/desktop")
    main(parser.parse_args())
