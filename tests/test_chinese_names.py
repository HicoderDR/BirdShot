# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from chinese_bird_names import localize


class ChineseNamesTests(unittest.TestCase):
    def test_exact_name_preserves_full_head_probability_and_provenance(self):
        source = {"source": "official-taxonomy-test-fixture"}
        row = {"scientificName": "Copsychus saularis", "commonName": "Oriental Magpie-Robin", "probability": .37, "isBird": True}
        result = localize(row, {"copsychus saularis": "鹊鸲"}, source)
        self.assertEqual(result["commonName"], "鹊鸲")
        self.assertEqual(result["probability"], .37)
        self.assertEqual(result["originalName"], row["commonName"])
        self.assertEqual(result["nameStatus"], "exact")
        self.assertEqual(result["nameSource"], source["source"])

    def test_unknown_or_split_taxon_is_not_guessed_from_a_similar_name(self):
        row = {"scientificName": "Parus major", "commonName": "Great Tit", "probability": .2, "isBird": True}
        result = localize(row, {"parus minor": "远东山雀"}, {"source": "controlled-taxonomy-test-fixture"})
        self.assertEqual(result["commonName"], "中文名待补")
        self.assertEqual(result["scientificName"], "Parus major")
        self.assertIsNone(result["nameSource"])
        self.assertEqual(result["probability"], .2)


if __name__ == "__main__":
    unittest.main()
