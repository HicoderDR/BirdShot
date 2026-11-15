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
from species_reference import SpeciesReferences, ReferenceError, exact_entity, permitted_license, plain, official_url, chinese_entry, retry_seconds, api, exact_inat, inat_license


def statement(value):
    return {"mainsnak": {"snaktype": "value", "datavalue": {"value": value}}, "rank": "normal"}


def entity(name, rank="Q7432"):
    return {"id": "Q1", "claims": {"P225": [statement(name)], "P105": [statement({"id": rank})]}}


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.backup = patch.object(SpeciesReferences, "_inat_image", return_value=None)
        self.backup.start()
        self.addCleanup(self.backup.stop)

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
            ready = chinese_entry({"status": "ready", "scientificName": "Copsychus saularis", "article": None, "image": None, "message": "No licensed image"}, "Copsychus saularis")
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
                self.assertEqual(result["article"]["language"], "zh")
                self.assertEqual(result["article"]["kind"], "search")
                self.assertEqual(result["sourceArticle"]["language"], "en")
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
        ready = chinese_entry({"status": "ready", "scientificName": "Copsychus saularis", "article": None, "image": None, "message": None}, "Copsychus saularis")
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
            ready = chinese_entry({"status": "ready", "scientificName": "Copsychus saularis", "article": None, "image": None, "message": None}, "Copsychus saularis")
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

    def test_maxlag_retry_after_partial_and_no_retry_storm(self):
        with patch("species_reference.fetch", return_value=(b'{"error":{"code":"maxlag"}}', {"Retry-After": "120"})):
            with self.assertRaises(ReferenceError) as raised:
                api("www.wikidata.org", {"action": "query"})
            self.assertEqual(raised.exception.reason_code, "upstreamBusy")
            self.assertEqual(raised.exception.retry_after, 120)
        with tempfile.TemporaryDirectory() as folder:
            manager = SpeciesReferences(Path(folder), ["Copsychus saularis"])
            taxon = entity("Copsychus saularis")
            taxon["sitelinks"] = {"zhwiki": {"title": "鵲鴝"}}
            with patch("species_reference.api", side_effect=[{"search": [{"id": "Q1"}]}, {"entities": {"Q1": taxon}}]), \
                    patch.object(manager, "_lookup_image", side_effect=ReferenceError("maxlag", "upstreamBusy", 120)) as lookup:
                first = manager.get("Copsychus saularis")
                second = manager.get("Copsychus saularis")
                self.assertEqual(first["status"], "error")
                self.assertTrue(first["partial"])
                self.assertEqual(first["article"]["kind"], "article")
                self.assertIsNone(first["image"])
                self.assertGreaterEqual(first["retryAfterSeconds"], 120)
                self.assertEqual(second["reasonCode"], "upstreamBusy")
                self.assertEqual(lookup.call_count, 1)
        self.assertEqual(retry_seconds("120"), 120)
        self.assertEqual(retry_seconds("invalid"), 30)

    def test_old_english_cache_projects_to_honest_chinese_search(self):
        result = chinese_entry({"status": "ready", "article": {"title": "Example", "url": "https://en.wikipedia.org/wiki/Example", "language": "en"}}, "Copsychus saularis")
        self.assertEqual(result["article"]["kind"], "search")
        self.assertTrue(result["article"]["url"].startswith("https://zh.wikipedia.org/w/index.php?search="))
        self.assertEqual(result["sourceArticle"]["language"], "en")
        missing = chinese_entry({"status": "notFound", "article": None}, "Copsychus saularis")
        self.assertEqual(missing["status"], "notFound")
        self.assertEqual(missing["article"]["kind"], "search")

    def test_inat_rejects_missing_nc_and_non_exact_taxon(self):
        self.backup.stop()
        self.assertIsNone(inat_license({"license_code": None, "attribution": "Public author"}))
        self.assertIsNone(inat_license({"license_code": "cc-by-nc", "attribution": "Public author"}))
        self.assertIsNone(inat_license({"license_code": "cc-by-nd", "attribution": "Public author"}))
        taxon = {"id": 123, "name": "Copsychus saularis", "rank": "species", "is_active": True}
        self.assertIsNone(exact_inat("Copsychus saularis", [{**taxon, "rank": "genus"}]))
        self.assertIsNone(exact_inat("Copsychus saularis", [{**taxon, "name": "Copsychus saularis saularis"}]))
        self.assertIsNone(exact_inat("Copsychus saularis", [{**taxon, "is_active": False}]))
        photo = {"id": 7, "type": "LocalPhoto", "license_code": "cc-by", "attribution": "(c) Public author (CC BY)",
                 "medium_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/7/medium.jpeg"}
        row = lambda image: {"taxon_id": 123, "taxon": taxon, "photo": image}
        with tempfile.TemporaryDirectory() as folder:
            manager = SpeciesReferences(Path(folder), ["Copsychus saularis"])
            detail = {**taxon, "default_photo": {**photo, "license_code": None},
                      "taxon_photos": [row({**photo, "license_code": None}), row({**photo, "license_code": "cc-by-nc"}), row(photo)]}
            with patch("species_reference.inat_api", side_effect=[[taxon], [detail]]), \
                    patch.object(manager, "_cache_remote", return_value=manager.root / ("a" * 64 + ".jpg")) as cache:
                result = manager._inat_image("Copsychus saularis")
                self.assertEqual(result["provider"], "iNaturalist")
                self.assertEqual(result["photoId"], 7)
                self.assertEqual(result["license"], "CC BY 4.0")
                self.assertEqual(cache.call_count, 1)
            detail["taxon_photos"] = [row({**photo, "license_code": "cc-by-nc"})]
            with patch("species_reference.inat_api", side_effect=[[taxon], [detail]]), patch.object(manager, "_cache_remote") as cache:
                self.assertIsNone(manager._inat_image("Copsychus saularis"))
                cache.assert_not_called()

    def test_backup_success_is_exact_and_keeps_primary_warning(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = SpeciesReferences(Path(folder), ["Copsychus saularis"])
            image = {"url": "/species-media/" + "a" * 64 + ".jpg", "provider": "iNaturalist"}
            with patch.object(manager, "_lookup", side_effect=ReferenceError("maxlag", "upstreamBusy", 60)), \
                    patch.object(manager, "_inat_image", return_value=image):
                result = manager.get("Copsychus saularis")
                self.assertEqual(result["status"], "ready")
                self.assertEqual(result["article"]["kind"], "search")
                self.assertEqual(result["sourceWarning"], "upstreamBusy")
                self.assertEqual(result["image"]["provider"], "iNaturalist")


if __name__ == "__main__":
    unittest.main()
