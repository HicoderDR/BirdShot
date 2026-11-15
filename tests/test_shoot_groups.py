# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from shoot_groups import shoot_group, raw_place


class ShootGroupingTests(unittest.TestCase):
    def test_camera_day_does_not_shift_at_midnight_or_with_a_different_offset(self):
        group = shoot_group({"time": "2026-01-01T00:05:00+08:00", "placeName": "受控测试场所"})
        self.assertEqual(group["date"], "2026-01-01")
        self.assertEqual(group["name"], "受控测试场所 - 260101")
        same_day = shoot_group({"time": "2026-01-01T23:59:00-10:00", "placeName": "受控测试场所"})
        self.assertEqual(group["id"], same_day["id"])

    def test_different_places_or_days_remain_separate_and_ids_are_stable(self):
        a = shoot_group({"time": "2026-01-01T12:00:00", "placeName": "测试地甲"})
        self.assertEqual(a, shoot_group({"time": "2026-01-01T12:00:00", "placeName": "测试地甲"}))
        self.assertNotEqual(a["id"], shoot_group({"time": "2026-01-01T12:00:00", "placeName": "测试地乙"})["id"])
        self.assertNotEqual(a["id"], shoot_group({"time": "2026-01-02T12:00:00", "placeName": "测试地甲"})["id"])

    def test_unknown_place_and_time_are_not_invented(self):
        group = shoot_group({"time": "2026-01-01T12:00:00+08:00", "placeName": None})
        self.assertEqual(group["name"], "未知地点 - 260101")
        self.assertIsNone(group["placeName"])
        unknown = shoot_group({})
        self.assertIsNone(unknown["date"])
        self.assertEqual(unknown["name"], "未知地点 - 日期未知")
        self.assertEqual(raw_place({"status": "parsed", "locationTags": {}}), (None, None))

    def test_distinct_unnamed_actual_gps_points_are_not_merged_as_one_place(self):
        a = shoot_group({"time": "2026-01-01T12:00:00", "location": {"latitude": 30, "longitude": 120, "crs": "WGS84"}})
        b = shoot_group({"time": "2026-01-01T12:00:00", "location": {"latitude": 31, "longitude": 121, "crs": "WGS84"}})
        self.assertNotEqual(a["id"], b["id"])
        self.assertIsNone(a["placeName"])


if __name__ == "__main__":
    unittest.main()
