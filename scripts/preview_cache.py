# SPDX-License-Identifier: AGPL-3.0-only
"""Private on-demand JPEG previews, separate from originals/crops/selection/audits."""
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import threading
import uuid

from PIL import Image, ImageCms, ImageOps, __version__ as PILLOW_VERSION

EDGES = {480, 960, 1920, 3840}
POLICY = f"exif-oriented-jpeg88-progressive-icc-v1-pillow-{PILLOW_VERSION}"
ENTRY = re.compile(r"^[0-9a-f]{64}-(480|960|1920|3840)\.jpg$")


def cache_io_path(path):
    value = str(path)
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        value = "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
    return Path(value)


@dataclass(frozen=True)
class Source:
    path: Path
    size: int
    mtime_ns: int
    key: str
    etag: str

    @property
    def modified(self):
        return self.mtime_ns / 1_000_000_000


@dataclass(frozen=True)
class Preview:
    source: Source
    data: bytes
    hit: bool


def not_modified(headers, source):
    etag = headers.get("If-None-Match")
    if etag is not None:
        return any(value.strip().removeprefix("W/") in {source.etag, "*"} for value in etag.split(","))
    since = headers.get("If-Modified-Since")
    if since:
        try:
            return int(source.modified) <= int(parsedate_to_datetime(since).timestamp())
        except (ValueError, TypeError, OverflowError):
            return False
    return False


class PreviewCache:
    def __init__(self, source_root, photos, cache_root, budget=512 * 1024 * 1024, concurrency=2, source_roots=None):
        self.source_root = Path(source_root).resolve()
        self.source_roots = {Path(root).resolve() for root in
                             (source_roots if source_roots is not None else [self.source_root])}
        self.photos = photos
        self.root = Path(cache_root).resolve()
        if any(self.root.is_relative_to(root) for root in self.source_roots):
            raise ValueError("Preview cache cannot be created inside the original photo directory")
        self.io_root = cache_io_path(self.root)
        self.io_root.mkdir(parents=True, exist_ok=True)
        self.budget = budget
        self.decode_slots = threading.BoundedSemaphore(concurrency)
        self.registry_lock = threading.Lock()
        self.io_lock = threading.Lock()
        self.key_locks = {}
        self.generated = 0

    def describe(self, frame_id, edge=None):
        if edge is not None and edge not in EDGES:
            raise ValueError("Preview edge must be 480, 960, 1920 or 3840")
        photo = self.photos.get(frame_id)
        if not photo or not photo["supported"]:
            raise KeyError("Unknown or unsupported registered frame")
        root = Path(photo.get("sourceRoot") or self.source_root).resolve()
        if root not in self.source_roots:
            raise ValueError("Original root is not explicitly registered")
        relative = Path(photo["primary"]["relative"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Original path is not a registered relative path")
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root) or path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            raise ValueError("Original is outside the registered photo boundary")
        stat = path.stat()
        key = hashlib.sha256(json.dumps([frame_id, str(root), photo["primary"]["relative"], stat.st_size, stat.st_mtime_ns,
                                         edge, POLICY if edge is not None else "unmodified-original-v1"]).encode()).hexdigest()
        return Source(path, stat.st_size, stat.st_mtime_ns, key, '"' + key + '"')

    def _prune(self):
        entries = [path for path in self.io_root.iterdir() if ENTRY.fullmatch(path.name) and path.is_file() and not path.is_symlink()]
        total = sum(path.stat().st_size for path in entries)
        for path in sorted(entries, key=lambda p: p.stat().st_mtime_ns):
            if total <= self.budget:
                break
            size = path.stat().st_size
            path.unlink()
            total -= size

    def get(self, frame_id, edge):
        source = self.describe(frame_id, edge)
        path = self.io_root / f"{source.key}-{edge}.jpg"
        with self.registry_lock:
            lock = self.key_locks.setdefault(source.key, threading.Lock())
        with lock:
            with self.io_lock:
                if path.is_symlink():
                    raise ValueError("Preview cache entry is not a regular cache file")
                if path.exists():
                    data = path.read_bytes()
                    os.utime(path, None)
                    return Preview(source, data, True)
            with self.decode_slots:
                with Image.open(source.path) as original:
                    if original.width * original.height > 60_000_000:
                        raise ValueError("Original exceeds the registered demo pixel limit")
                    icc = original.info.get("icc_profile")
                    original.draft("RGB", (edge, edge))
                    image = ImageOps.exif_transpose(original)
                    if image.mode != "RGB":
                        if icc:
                            try:
                                srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
                                image = ImageCms.profileToProfile(image, ImageCms.ImageCmsProfile(io.BytesIO(icc)), srgb, outputMode="RGB")
                                icc = srgb.tobytes()
                            except ImageCms.PyCMSError as error:
                                raise RuntimeError("Cannot safely convert the source ICC profile") from error
                        elif image.mode == "L":
                            image = image.convert("RGB")
                        else:
                            raise ValueError("Cannot safely convert an unprofiled non-RGB original")
                    image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
                    output = io.BytesIO()
                    image.save(output, "JPEG", quality=88, progressive=True, optimize=True, exif=b"", icc_profile=icc)
                    data = output.getvalue()
                after = source.path.stat()
                if (after.st_size, after.st_mtime_ns) != (source.size, source.mtime_ns):
                    raise RuntimeError("Original changed during preview generation; no unstable cache was saved")
                with self.io_lock:
                    temporary = self.io_root / f"{uuid.uuid4().hex}.part"
                    try:
                        with temporary.open("xb") as target:
                            target.write(data)
                            target.flush()
                            os.fsync(target.fileno())
                        temporary.replace(path)
                    finally:
                        temporary.unlink(missing_ok=True)
                    self.generated += 1
                    self._prune()
                return Preview(source, data, False)
