# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
import json
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from species_reference import SpeciesReferences, ReferenceError, exact_entity, permitted_license, plain, official_url


def statement(value):
    return {"mainsnak": {"snaktype": "value", "datavalue": {"value": value}}, "rank": "normal"}


def entity(name, rank="Q7432"):
    return {"id": "Q1", "claims": {"P225": [statement(name)], "P105": [statement({"id": rank})]}}


class ReferenceTests(unittest.TestCase):
    def test_exact_taxon_not_search_label_genus_or_subspecies(self):
        exact = entity("Copsychus saularis")
        self.assertIs(exact_entity("Copsychus saularis", [entity("Copsychus"), exact]), exact)
        self.assertIsNone(exact_entity("Copsychus saularis", [entity("Copsychus saularis saularis", "Q68947")]))
        self.assertIsNone(exact_entity("Copsychus saularis", [entity("Copsychus saularis", "Q34740")]))
        self.assertIsNone(exact_entity("Copsychus saularis", [exact, entity("Copsychus saularis")]))

    def test_free_license_attribution_and_version_must_match(self):
        meta = {key: {"value": value} for key, value in {
            "Artist": '<a href="https://example.invalid">Test author</a>', "LicenseShortName": "CC BY-SA 3.0",
            "LicenseUrl": "https://creativecommons.org/licenses/by-sa/3.0/", "Restrictions": ""}.items()}
        self.assertEqual(permitted_license(meta)["author"], "Test author")
        meta["LicenseShortName"]["value"] = "CC BY-NC 3.0"
        self.assertIsNone(permitted_license(meta))
        meta["LicenseShortName"]["value"] = "CC BY-SA 4.0"
        self.assertIsNone(permitted_license(meta))
        self.assertEqual(plain("<b>plain</b> &amp; text"), "plain & text")

    def test_public_fetch_is_not_an_arbitrary_url_proxy(self):
        for url in ["http://www.wikidata.org/w/api.php", "https://127.0.0.1/secret",
                    "https://www.wikidata.org.evil.invalid/", "https://user:password@www.wikidata.org/"]:
            with self.assertRaises(ReferenceError):
                official_url(url, {"www.wikidata.org"})

    def test_hot_cache_dedup(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = SpeciesReferences(Path(folder), ["Copsychus saularis"])
            ready = {"status": "ready", "scientificName": "Copsychus saularis", "article": None, "image": None, "message": "No licensed image"}
            with patch.object(manager, "_lookup", return_value=ready) as lookup:
                self.assertEqual(manager.get("Copsychus saularis"), ready)
                self.assertEqual(manager.get("copsychus saularis"), ready)
                self.assertEqual(lookup.call_count, 1)

    def test_article_without_image_and_concurrent_same_key(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = SpeciesReferences(Path(folder), ["Copsychus saularis"])
            taxon = entity("Copsychus saularis")
            taxon["sitelinks"] = {"enwiki": {"title": "Oriental magpie-robin"}}
            with patch("species_reference.api", side_effect=[{"search": [{"id": "Q1"}]}, {"entities": {"Q1": taxon}}]):
                result = manager._lookup("Copsychus saularis")
                self.assertEqual(result["status"], "ready")
                self.assertIsNone(result["image"])
                self.assertEqual(result["article"]["language"], "en")
            with patch.object(manager, "_lookup", return_value=result) as lookup:
                with ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(pool.map(manager.get, ["Copsychus saularis"] * 8))
                self.assertEqual(lookup.call_count, 1)
                self.assertTrue(all(row == result for row in results))

    def test_budget_deletes_only_registered_public_derivatives(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = SpeciesReferences(Path(folder), [])
            original = Path(folder) / "original.jpg"
            original.write_bytes(b"untouched")
            unregistered = manager.root / "unregistered.jpg"
            unregistered.write_bytes(b"untouched")
            old = manager.root / ("a" * 64 + ".jpg")
            old.write_bytes(b"old")
            old.with_suffix(".image.json").write_text("{}")
            current = manager.root / ("b" * 64 + ".jpg")
            current.write_bytes(b"current")
            current.with_suffix(".image.json").write_text("{}")
            with patch("species_reference.CACHE_BUDGET", 7):
                manager.prune(current)
            self.assertFalse(old.exists())
            self.assertTrue(current.exists())
            self.assertEqual(original.read_bytes(), b"untouched")
            self.assertEqual(unregistered.read_bytes(), b"untouched")

    def test_input_domain_and_transient_errors(self):
        ready = {"status": "ready", "scientificName": "Copsychus saularis", "article": None, "image": None, "message": None}
        with tempfile.TemporaryDirectory() as folder:
            manager = SpeciesReferences(Path(folder), ["Copsychus saularis"])
            with self.assertRaises(ValueError):
                manager.get("Copsychus unknown")
            with self.assertRaises(ValueError):
                manager.get("../../private")
            with self.assertRaises(FileNotFoundError):
                manager.media("a" * 64 + ".jpg")
        with tempfile.TemporaryDirectory() as folder:
            manager = SpeciesReferences(Path(folder), ["Copsychus saularis"])
            with patch("species_reference.time.time", return_value=100), patch.object(manager, "_lookup", side_effect=ReferenceError("timeout")):
                self.assertEqual(manager.get("Copsychus saularis")["status"], "error")
            with patch("species_reference.time.time", return_value=131), patch.object(manager, "_lookup", return_value=ready) as lookup:
                self.assertEqual(manager.get("Copsychus saularis")["status"], "ready")
                self.assertEqual(lookup.call_count, 1)

    def test_missing_derived_image_refreshes_but_corrupt_json_is_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = SpeciesReferences(Path(folder), ["Copsychus saularis"])
            ready = {"status": "ready", "scientificName": "Copsychus saularis", "article": None, "image": None, "message": None}
            with patch.object(manager, "_lookup", return_value=ready) as lookup:
                manager.get("Copsychus saularis")
                metadata = next(manager.root.glob("*.json"))
                data = json.loads(metadata.read_text(encoding="utf-8"))
                data["result"]["image"] = {"url": "/species-media/" + "a" * 64 + ".jpg"}
                metadata.write_text(json.dumps(data), encoding="utf-8")
                self.assertEqual(manager.get("Copsychus saularis"), ready)
                self.assertEqual(lookup.call_count, 2)
                metadata.write_text("{", encoding="utf-8")
                with self.assertRaises(ReferenceError):
                    manager.get("Copsychus saularis")


if __name__ == "__main__":
    unittest.main()
