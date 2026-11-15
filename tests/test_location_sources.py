# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from inspect_location_sources import paired_sidecars, targeted_text_fields


class LocationSourcesTests(unittest.TestCase):
    def test_only_exact_registered_same_directory_sidecars_are_read(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tools") as folder:
            root = Path(folder)
            for name in ["a.ARW.dop", "a.JPG.xmp", "a.xmp", "unregistered.ARW.dop"]:
                (root / name).write_text("controlled sidecar fixture", encoding="utf-8")
            photos = [{"id": "p1", "primary": {"relative": "a.JPG"}, "companions": [{"relative": "a.ARW"}]}]
            found, ignored = paired_sidecars(root.resolve(), photos)
            self.assertEqual({row["path"].name for row in found}, {"a.ARW.dop", "a.JPG.xmp", "a.xmp"})
            self.assertTrue(all(row["photoIds"] == ["p1"] for row in found))
            self.assertEqual(ignored, 1)

    def test_text_fallback_returns_only_geographic_keys_not_owner_or_focus(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tools") as folder:
            path = Path(folder) / "controlled.dop"
            path.write_text('Owner = "not exported"\nFocusLocation = {100, 200}\nCity = "测试市"\n', encoding="utf-8")
            result = targeted_text_fields(path)
            self.assertEqual(result, {"PairedText:City": "测试市"})


if __name__ == "__main__":
    unittest.main()
