# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from capture_metadata import parse_capture, coordinate


class CaptureTests(unittest.TestCase):
    def test_timezone_is_preserved_and_not_fabricated_when_missing(self):
        result = parse_capture({36867: "2025:02:03 04:05:06", 37521: "125", 36881: "+08:00"}, {})
        self.assertEqual(result["time"], "2025-02-03T04:05:06.125+08:00")
        self.assertEqual(result["timeZone"], "+08:00")
        without = parse_capture({36867: "2025:02:03 04:05:06"}, {})
        self.assertEqual(without["time"], "2025-02-03T04:05:06")
        self.assertIsNone(without["timeZone"])
        self.assertFalse(without["time"].endswith("Z"))

    def test_gps_rationals_preserve_south_and_west_wgs84(self):
        result = parse_capture({}, {1: "S", 2: ((33, 1), (30, 1), (0, 1)),
                                    3: b"W", 4: ((70, 1), (15, 1), (0, 1))})
        self.assertEqual(result["location"], {"latitude": -33.5, "longitude": -70.25, "crs": "WGS84", "source": "exif-gps"})
        self.assertIsNone(result["time"])

    def test_absent_metadata_is_ready_and_invalid_values_are_errors(self):
        missing = parse_capture({}, {})
        self.assertEqual(missing["status"], "ready")
        self.assertIsNone(missing["location"])
        invalid = parse_capture({36867: "invalid date"}, {})
        self.assertEqual(invalid["status"], "error")
        with self.assertRaises(ValueError):
            coordinate(((1, 0), (0, 1), (0, 1)), "N", True)
        with self.assertRaises(ValueError):
            coordinate((91, 0, 0), "N", True)
        with self.assertRaises(ValueError):
            coordinate((float("nan"), 0, 0), "E", False)


if __name__ == "__main__":
    unittest.main()
