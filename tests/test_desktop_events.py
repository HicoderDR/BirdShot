# SPDX-License-Identifier: AGPL-3.0-only
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from desktop_backend import LibraryController


class EventOrderingTests(unittest.TestCase):
    def test_completed_event_sees_committed_aggregate_not_previous_source(self):
        with tempfile.TemporaryDirectory() as folder:
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


if __name__ == "__main__":
    unittest.main()
