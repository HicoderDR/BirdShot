# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded range download, always verified against Electron's official npm hashes."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    version = json.loads((ROOT / "package.json").read_text())["devDependencies"]["electron"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimeError("Pin a stable Electron version before preparing the binary")
    filename = f"electron-v{version}-win32-x64.zip"
    expected = json.loads((ROOT / "node_modules" / "electron" / "checksums.json").read_text())[filename]
    target = ROOT / ".tools" / "electron-download" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and hashlib.file_digest(target.open("rb"), "sha256").hexdigest() == expected:
        install(target)
        return
    # This optional mirror never receives credentials; the official hash is mandatory.
    url = f"https://cdn.npmmirror.com/binaries/electron/{version}/{filename}"
    with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30) as response:
        total = int(response.headers["Content-Length"])
    block = 4 * 1024 * 1024
    parts = target.parent / (filename + ".parts")
    parts.mkdir(exist_ok=True)
    if target.exists():
        with target.open("rb") as source:
            for index in range(target.stat().st_size // block):
                path = parts / str(index)
                data = source.read(block)
                if not path.exists():
                    path.write_bytes(data)
    count = (total + block - 1) // block
    def download(index):
        path = parts / str(index)
        start, end = index * block, min(total, (index + 1) * block) - 1
        if path.exists() and path.stat().st_size == end - start + 1:
            return
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}", "User-Agent": "BirdShot-build/0.2.0"})
                with urllib.request.urlopen(request, timeout=60) as response:
                    if response.status != 206 or response.headers.get("Content-Range") != f"bytes {start}-{end}/{total}":
                        raise RuntimeError("Mirror did not honor the exact bounded range")
                    data = response.read(end - start + 2)
                    if len(data) != end - start + 1:
                        raise RuntimeError("Incomplete Electron binary part")
                path.write_bytes(data)
                return
            except (OSError, RuntimeError):
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(download, range(count)))
    temporary = target.with_suffix(".complete")
    with temporary.open("wb") as stream:
        for index in range(count):
            stream.write((parts / str(index)).read_bytes())
    with temporary.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected:
        raise RuntimeError("Electron official checksum mismatch; refusing to extract or run")
    temporary.replace(target)
    install(target)
    print(json.dumps({"electron": version, "bytes": total, "sha256": actual, "verified": True}))


def install(target):
    with zipfile.ZipFile(target) as archive:
        archive.extractall(ROOT / "node_modules" / "electron" / "dist")
    (ROOT / "node_modules" / "electron" / "path.txt").write_text("electron.exe", encoding="utf-8")


if __name__ == "__main__":
    main()
