# SPDX-License-Identifier: AGPL-3.0-only
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from comparison_prepare import sha256
from comparison_dongniao import run, clean


class Response:
    status_code = 200
    def __init__(self, value):
        self.value = value
    def json(self):
        return self.value


class Session:
    def __init__(self):
        self.headers = {}
        self.uploads = 0
        self.polls = 0
    def post(self, url, data, **kwargs):
        if "files" in kwargs:
            self.uploads += 1
            return Response([1000, "fixed-result-id"])
        self.polls += 1
        return Response([1000, [{"box": [0, 0, 20, 20], "list": [[75, "测试名|Test Bird|Testus birdus", 1, "B"]]}]])


class ComparisonTests(unittest.TestCase):
    def test_api_ledger_does_not_resubmit_completed_images(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tools") as folder:
            job = Path(folder)
            image = job / "sample.jpg"
            Image.new("RGB", (20, 20), "gray").save(image)
            (job / "samples.json").write_text(json.dumps({"samples": [{"id": "test", "image": image.name, "sha256": sha256(image)}]}))
            session = Session()
            with patch.dict(os.environ, {"DONGNIAO_API_KEY": "controlled-test-credential"}), \
                    patch("comparison_dongniao.requests.Session", return_value=session), patch("comparison_dongniao.time.sleep"):
                run(job)
                run(job)
            self.assertEqual(session.uploads, 1)
            self.assertEqual(session.polls, 1)
            report = json.loads((job / "dongniao.json").read_text(encoding="utf-8"))
            self.assertEqual(report["submissionCount"], 1)
            self.assertNotIn("controlled-test-credential", json.dumps(report))

    def test_preexisting_uncertain_submission_is_not_blindly_retried(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tools") as folder:
            job = Path(folder)
            (job / "samples.json").write_text(json.dumps({"samples": [{"id": "test"}]}))
            (job / "dongniao.json").write_text(json.dumps({"predictions": [{"sampleId": "test", "status": "uncertain-submission"}],
                                                          "submissionCount": 1, "pollCount": 0}))
            session = Session()
            with patch.dict(os.environ, {"DONGNIAO_API_KEY": "controlled-test-credential"}), \
                    patch("comparison_dongniao.requests.Session", return_value=session):
                run(job)
            self.assertEqual(session.uploads, 0)

    def test_response_sanitizer_removes_secret_fields_and_echoes(self):
        secret = "controlled-secret"
        result = clean({"api_key": secret, "message": "echo " + secret, "nested": [secret]}, secret)
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn("api_key", result)


if __name__ == "__main__":
    unittest.main()
