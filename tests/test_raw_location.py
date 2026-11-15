# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from inspect_paired_raw import tiff_gps_probe, valid_coordinates, position_fields


class RawLocationTests(unittest.TestCase):
    def test_signed_gps_and_explicit_wgs84_datum_are_preserved(self):
        coordinates, error = valid_coordinates({"GPS:GPSLatitude": 33.5, "GPS:GPSLatitudeRef": "S",
            "GPS:GPSLongitude": 70.25, "GPS:GPSLongitudeRef": "W", "GPS:GPSMapDatum": "WGS-84"})
        self.assertIsNone(error)
        self.assertEqual(coordinates["latitude"], -33.5)
        self.assertEqual(coordinates["longitude"], -70.25)
        self.assertEqual(coordinates["crs"], "WGS84")

    def test_missing_invalid_or_unknown_datum_are_not_fake_map_locations(self):
        self.assertEqual(valid_coordinates({}), (None, None))
        self.assertIsNone(valid_coordinates({"GPS:GPSLatitude": 91, "GPS:GPSLongitude": 20})[0])
        coordinates, _ = valid_coordinates({"GPS:GPSLatitude": 30, "GPS:GPSLatitudeRef": "N",
            "GPS:GPSLongitude": 120, "GPS:GPSLongitudeRef": "E"})
        self.assertIsNone(coordinates["crs"])
        gps, locations = position_fields({"Sony:FocusLocation": [100, 200], "ExifIFD:OffsetTimeOriginal": "+08:00"})
        self.assertEqual(gps, {})
        self.assertEqual(locations, {})

    def test_independent_probe_finds_gps_pointer_without_image_decoding(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tools") as folder:
            path = Path(folder) / "controlled-tiff-directory-fixture.ARW"
            # Minimal synthetic TIFF directories, not a purported real RAW photograph.
            path.write_bytes(b"II" + struct.pack("<HI", 42, 8) + struct.pack("<H", 1)
                + struct.pack("<HHII", 0x8825, 4, 1, 26) + struct.pack("<I", 0) + struct.pack("<HI", 0, 0))
            result = tiff_gps_probe(path)
            self.assertEqual(result["status"], "parsed")
            self.assertTrue(result["gpsIFDTagPresent"])
            self.assertEqual(result["directoriesChecked"], 2)


if __name__ == "__main__":
    unittest.main()
