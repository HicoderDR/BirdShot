# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from batch_culling import inventory, write_image
from batch_inference import masked_metric
from verify_batch import verify

NODE = ROOT / ".tools" / "node-v24.11.1-win-x64" / "node.exe"


class BatchTests(unittest.TestCase):
    def policy(self, frames):
        result = subprocess.run([str(NODE), "--experimental-strip-types", str(ROOT / "scripts" / "batch-policy.mjs")],
                                input=json.dumps({"frames": frames}) + "\n", text=True,
                                capture_output=True, encoding="utf-8", check=True)
        return json.loads(result.stdout)["results"]

    def frame(self, photo_id, boxes):
        # Controlled policy fixtures, not bird photographs or segmentation ground truth.
        mask = {"width": 4, "height": 4, "runs": [0, 16], "region": [0, 0, 1000, 800],
                "scale": .64, "padX": 0, "padY": 64, "path": "M0,0L1000,0L1000,800L0,800Z"}
        birds = [{"id": i + 1, "trackId": None, "body": box, "bodyConfidence": .99, "mask": mask,
                  "maskIssue": None, "posePoints": [], "poseIssue": None, "eye": None, "beak": None,
                  "patch": None, "sharpness": None, "eyeIssue": "未定位", "bodySharpness": 100000,
                  "bodyPixels": 1000} for i, box in enumerate(boxes)]
        return {"id": photo_id, "group": "controlled", "width": 1000, "height": 800, "body": None,
                "bodyConfidence": None, "birdCount": len(birds), "eye": None, "beak": None,
                "patch": None, "sharpness": None, "issue": None, "decision": "review", "reason": "",
                "relative": None, "rank": None, "cohortSize": 0, "instances": birds}

    def test_singleton_high_relative_texture_is_not_absolute_keep_evidence(self):
        result = self.policy([self.frame("single", [[100, 100, 400, 400]])])[0]
        self.assertEqual(result["decision"], "review")
        self.assertIsNone(result["relative"])
        self.assertIn("单张", result["reason"])

    def test_ambiguous_cross_bird_matching_cannot_trigger_strict_selection(self):
        results = self.policy([self.frame("before", [[100, 100, 400, 400], [310, 100, 400, 400]]),
                               self.frame("after", [[205, 100, 400, 400]])])
        self.assertTrue(all(frame["decision"] == "review" for frame in results))
        self.assertIn("匹配", results[1]["reason"])

    def test_native_metric_matches_shared_original_pixel_definition(self):
        rgb = np.zeros((17, 17, 3), dtype=np.uint8)
        rgb[:, 8:] = [180, 100, 30]
        mask = np.zeros((17, 17), dtype=np.uint8)
        mask[3:14, 3:14] = 1
        score, count = masked_metric(rgb[:, :, ::-1], mask)
        rgba = np.concatenate([rgb, np.full((17, 17, 1), 255, dtype=np.uint8)], axis=2).ravel().tolist()
        request = {"operation": "metrics", "rgba": rgba, "width": 17, "height": 17, "mask": mask.ravel().tolist()}
        result = subprocess.run([str(NODE), "--experimental-strip-types", str(ROOT / "scripts" / "batch-policy.mjs")],
                                input=json.dumps(request) + "\n", text=True, capture_output=True, encoding="utf-8", check=True)
        actual = json.loads(result.stdout)["result"]
        self.assertAlmostEqual(score, actual["score"], places=8)
        self.assertEqual(count, actual["pixels"])

    def test_jpeg_raw_pairs_are_one_item_and_unsupported_files_are_accounted(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tools") as folder:
            source = Path(folder)
            Image.new("RGB", (16, 16)).save(source / "a.JPG")
            (source / "a.ARW").write_bytes(b"controlled unit fixture, not a RAW sample")
            (source / "b.ARW").write_bytes(b"controlled unsupported fixture")
            (source / "a.dop").write_text("metadata")
            result = inventory(source)
            self.assertEqual(len(result["photos"]), 2)
            self.assertEqual(result["physicalPhotos"], 3)
            self.assertEqual(result["pairedRaw"], 1)
            self.assertEqual(result["rawOnly"], 1)
            self.assertEqual(result["otherFiles"], ["a.dop"])
            self.assertTrue(all(group["count"] == 1 for group in result["groups"]))

    def test_crop_is_derived_and_source_bytes_stay_unchanged(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tools") as folder:
            source = Path(folder) / "source.jpg"
            Image.new("RGB", (100, 80), "green").save(source)
            original = source.read_bytes()
            output = Path(folder) / "crop.jpg"
            pixels = np.zeros((20, 30, 3), dtype=np.uint8)
            checksum = write_image(output, pixels)
            self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), checksum)
            self.assertEqual(source.read_bytes(), original)
            with Image.open(output) as crop:
                self.assertEqual(crop.size, (30, 20))

    def test_incomplete_job_cannot_be_claimed_complete(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tools") as folder:
            job = Path(folder)
            (job / "manifest.json").write_text("{}")
            (job / "status.json").write_text(json.dumps({"status": "running", "processedPhotos": 1, "totalPhotos": 2}))
            with self.assertRaisesRegex(RuntimeError, "全量尚未结束"):
                verify(job)


if __name__ == "__main__":
    unittest.main()
