# SPDX-License-Identifier: AGPL-3.0-only
from concurrent.futures import ThreadPoolExecutor
from email.utils import formatdate
import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image, ImageCms

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from preview_cache import PreviewCache, not_modified


class PreviewTests(unittest.TestCase):
    def fixture(self, root, size=(120, 80), orientation=1):
        source = root / "source"
        source.mkdir()
        path = source / "controlled.jpg"
        exif = Image.Exif()
        exif[274] = orientation
        icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        Image.new("RGB", size, "green").save(path, exif=exif, icc_profile=icc)
        photos = {"p000001": {"supported": True, "primary": {"relative": path.name}}}
        return path, photos, icc

    def test_exif_orientation_icc_and_no_upsampling_without_source_changes(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tools") as folder:
            root = Path(folder)
            path, photos, icc = self.fixture(root, orientation=8)
            before, stat = path.read_bytes(), path.stat()
            cache = PreviewCache(path.parent, photos, root / "cache")
            result = cache.get("p000001", 960)
            with Image.open(io.BytesIO(result.data)) as preview:
                self.assertEqual(preview.size, (80, 120))
                self.assertEqual(preview.info["icc_profile"], icc)
                self.assertFalse(preview.getexif())
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(path.stat().st_mtime_ns, stat.st_mtime_ns)

    def test_same_key_concurrency_is_one_generation_and_conditional_cache_works(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tools") as folder:
            root = Path(folder)
            path, photos, _ = self.fixture(root)
            os.utime(path, ns=(1700000000123456789, 1700000000123456789))
            cache = PreviewCache(path.parent, photos, root / "cache")
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(lambda _: cache.get("p000001", 960), range(8)))
            self.assertEqual(cache.generated, 1)
            self.assertEqual(sum(not result.hit for result in results), 1)
            self.assertEqual(len({hashlib.sha256(result.data).hexdigest() for result in results}), 1)
            source = results[0].source
            self.assertTrue(not_modified({"If-None-Match": source.etag}, source))
            self.assertTrue(not_modified({"If-Modified-Since": formatdate(source.modified, usegmt=True)}, source))
            self.assertFalse(not_modified({"If-None-Match": '"different"', "If-Modified-Since": formatdate(source.modified + 60, usegmt=True)}, source))
            self.assertFalse(list(cache.root.glob("*.part")))

    def test_source_change_invalidates_key_and_etag_without_touching_other_files(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tools") as folder:
            root = Path(folder)
            path, photos, _ = self.fixture(root)
            cache = PreviewCache(path.parent, photos, root / "cache", budget=1)
            unrelated = cache.root / "not-managed-original.jpg"
            unrelated.write_bytes(b"must not be deleted")
            first = cache.get("p000001", 960)
            stamp = path.stat().st_mtime_ns
            Image.new("RGB", (120, 80), "red").save(path)
            os.utime(path, ns=(stamp + 1000000000, stamp + 1000000000))
            second = cache.get("p000001", 960)
            self.assertNotEqual(first.source.etag, second.source.etag)
            self.assertFalse(not_modified({"If-None-Match": first.source.etag}, second.source))
            self.assertNotEqual(first.data, second.data)
            self.assertEqual(unrelated.read_bytes(), b"must not be deleted")
            self.assertEqual(path.read_bytes()[:2], b"\xff\xd8")
            self.assertFalse(list(cache.root.glob("[0-9a-f]*-960.jpg")))

    def test_invalid_edges_unknown_ids_and_source_boundary_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tools") as folder:
            root = Path(folder)
            path, photos, _ = self.fixture(root)
            cache = PreviewCache(path.parent, photos, root / "cache")
            with self.assertRaises(ValueError):
                cache.get("p000001", 1000000000)
            with self.assertRaises(KeyError):
                cache.get("p999999", 960)
            photos["p000002"] = {"supported": True, "primary": {"relative": "../outside.jpg"}}
            Image.new("RGB", (20, 20)).save(root / "outside.jpg")
            with self.assertRaises(ValueError):
                cache.get("p000002", 960)

    def test_480_is_private_oriented_and_budget_pruned_while_500_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tools") as folder:
            root = Path(folder)
            path, photos, _ = self.fixture(root, size=(1200, 800), orientation=6)
            before = path.read_bytes()
            cache = PreviewCache(path.parent, photos, root / "cache")
            first = cache.get("p000001", 480)
            with Image.open(io.BytesIO(first.data)) as image:
                self.assertEqual(image.size, (320, 480))
            self.assertEqual(path.read_bytes(), before)
            self.assertTrue(cache.get("p000001", 480).hit)
            self.assertNotEqual(first.source.key, cache.describe("p000001", 960).key)
            self.assertNotEqual(first.source.key, cache.describe("p000001").key)
            with self.assertRaises(ValueError):
                cache.get("p000001", 500)
            cache.budget = 0
            cache._prune()
            self.assertFalse(list(cache.root.glob("*-480.jpg")))


if __name__ == "__main__":
    unittest.main()
