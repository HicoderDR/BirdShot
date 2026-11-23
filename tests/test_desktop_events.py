# SPDX-License-Identifier: AGPL-3.0-only
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from desktop_backend import LibraryController


class EventOrderingTests(unittest.TestCase):
    def test_completed_event_sees_committed_aggregate_not_previous_source(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tools", prefix="incremental-events-") as folder:
            root = Path(folder)
            job = root / "job"
            job.mkdir()
            (root / "job.worker.json").write_text(json.dumps({"status": "completed", "stage": "gallery"}))
            aggregate = root / "gallery.json"
            aggregate.write_text('{"sources":1}')
            source = {"id": "source-1", "status": "processing", "job": str(job),
                      "jobs": [{"path": str(job)}], "progress": {}}
            controller = LibraryController.__new__(LibraryController)
            controller.worker = Mock()
            controller.worker.poll.return_value = 0
            controller.worker_source = source["id"]
            controller.find_source = lambda _id: source
            controller.rebuild = lambda: aggregate.write_text('{"sources":2}')
            completed = []
            def notify():
                if source["status"] == "completed":
                    completed.append(json.loads(aggregate.read_text())["sources"])
            controller.changed = notify
            controller.poll_worker()
            self.assertEqual(completed, [2])

    def test_partial_publication_rebuilds_before_event_but_progress_does_not(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tools", prefix="incremental-events-") as folder:
            root = Path(folder)
            job = root / "job"
            job.mkdir()
            source = {"id": "source-1", "status": "processing", "job": str(job), "jobs": [],
                      "progress": {"stage": "detect", "publishedRevision": 0}}
            controller = LibraryController.__new__(LibraryController)
            controller.worker = Mock()
            controller.worker.poll.return_value = None
            controller.worker_source = source["id"]
            controller.find_source = lambda _id: source
            committed = []
            controller.rebuild = lambda: committed.append(source["progress"]["publishedRevision"])
            observed = []
            controller.changed = lambda: observed.append(list(committed))
            def poll(revision, processed):
                (root / "job.worker.json").write_text(json.dumps({
                    "status": "processing", "stage": "detect",
                    "progress": {"stage": "detect", "processedPhotos": processed, "publishedRevision": revision}}))
                controller.poll_worker()
            poll(0, 1)
            self.assertEqual(committed, [])
            poll(1, 2)
            self.assertEqual(observed[-1], [1])
            poll(1, 3)
            self.assertEqual(committed, [1])
            poll(2, 3)
            self.assertEqual(observed[-1], [1, 2])


if __name__ == "__main__":
    unittest.main()
