"""Offline PMC-style figure boundaries and native image acquisition/readings."""

import copy
import hashlib
import json
import unittest
from pathlib import Path

from literature_fixtures import LiteratureCase
from research_fixtures import client
from research_harness.acquisition import acquire_fulltext, import_response
from research_harness.html_visuals import resource_inventory
from research_harness.literature import import_bundle
from research_harness.reading import current_readings, record_reading, validate_read_evidence
from research_harness.source_links import validate_link
from research_harness.visual_assets import acquire_visual_asset


FIXTURES = Path(__file__).parent / "fixtures/research"
PMC_URL = "https://pmc.ncbi.nlm.nih.gov/articles/PMC13104172/"
EUROPE_PMC_URL = "https://europepmc.org/articles/PMC13104172/"
CAPTION = "Authored PMC figure caption."


class PmcResourceInventoryTests(unittest.TestCase):
    def find_resources(self, content, quote):
        start = content.index(quote)
        return resource_inventory(content, PMC_URL, {"start": start, "end": start + len(quote)})

    def test_caption_selects_whole_fig_class_and_only_its_image(self):
        content = (FIXTURES / "pmc-figure-resources.html").read_text(encoding="utf-8")
        inventory = self.find_resources(content, CAPTION)
        self.assertEqual([r["url"] for r in inventory["resources"]], [PMC_URL + "images/figure-1.jpg"])
        self.assertEqual(inventory["start"], content.index('<div class="fig iconblock"'))
        self.assertEqual(inventory["pending"], [])

    def test_fig_wrapper_takes_precedence_over_nested_picture_and_keeps_all_panels(self):
        image = '<img src="panel-one.jpg">'
        for opening, closing in (('<div class="FIG iconblock">', '</div>'),
                                 ('<table class="figure-panels">', '</table>')):
            with self.subTest(opening=opening):
                content = (opening + '<picture>' + image + '</picture><img src="panel-two.jpg">'
                           '<div class="caption">Two authored panels.</div>' + closing)
                inventory = self.find_resources(content, image)
                self.assertEqual([r["url"] for r in inventory["resources"]],
                                 [PMC_URL + "panel-one.jpg", PMC_URL + "panel-two.jpg"])
                self.assertEqual((inventory["start"], inventory["end"]), (0, len(content)))
                self.assertEqual(inventory["pending"], [])


class PmcFigureReadingTests(LiteratureCase):
    def build_figure_bundle(self, page_url=PMC_URL, *, identifier="doi:10.1234/authored-pmc", legacy=False):
        self.sequence += 1
        import_response(self.store, "mcp", json.dumps({"id": identifier, "title": "Authored PMC article"}).encode(),
                        source_url="https://example.org/metadata", media_type="application/json",
                        captured_at="2026-10-24T12:00:00Z", mappings=[{"id": "/id", "title": "/title"}],
                        expected_revision=self.store.revision, request_id="metadata-" + str(self.sequence))
        data = (FIXTURES / "pmc-figure-resources.html").read_bytes()
        if legacy:
            data = data.replace(b'class="fig iconblock"', b'class="figure iconblock"')
        http, _, _ = client([(200, {"Content-Type": "text/html"}, data)], max_retries=0)
        self.sequence += 1
        capture = acquire_fulltext(self.store, identifier, page_url, http=http,
                                   expected_revision=self.store.revision,
                                   request_id="html-" + str(self.sequence))["capture"]
        self.assertEqual(capture["extraction_status"], "extracted")
        bundle = self.bundle(identifier, capture, "bundle-" + str(self.sequence))
        link = {"version_id": identifier, "source_id": capture["source_id"], "artifact": capture["original"],
                "locator": {"kind": "html", "anchor": self.span(capture["original"], CAPTION, "span")}}
        bundle["units"].append({"id": "figure1", "kind": "figure", "required": True, "link": link})
        return bundle, link

    def acquire(self, link, url, responses=None):
        data = (FIXTURES / "authored-grid.jpg").read_bytes()
        http, wire, _ = client(responses if responses is not None else
                               [(200, {"Content-Type": "image/jpeg"}, data)], max_retries=0)
        self.sequence += 1
        revision = self.store.revision
        result = acquire_visual_asset(self.store, link, url, http=http, expected_revision=revision,
                                      request_id="image-" + str(self.sequence))
        return result, wire, revision

    def assert_reading(self, page_url):
        bundle, link = self.build_figure_bundle(page_url)
        image_url = page_url + "images/figure-1.jpg"
        self.mutate(import_bundle, bundle)
        pending = self.mutate(record_reading, self.full_note(bundle, "caption-only"))["result"]
        self.assertEqual(pending["status"], "partial")
        self.assertIn(image_url, [p.get("url") for p in pending["pending"] if p["code"] == "visual_asset_missing"])
        asset, _, _ = self.acquire(link, image_url)
        self.assertEqual(asset["status"], "complete")
        self.assertEqual(asset["capture"]["html_source_id"], link["source_id"])
        self.assertEqual(asset["capture"]["html_sha256"], link["artifact"]["sha256"])
        image = asset["asset_link"]["artifact"]
        self.assertEqual(image["sha256"], hashlib.sha256((FIXTURES / "authored-grid.jpg").read_bytes()).hexdigest())
        self.assertEqual(self.artifacts.read(image), (FIXTURES / "authored-grid.jpg").read_bytes())
        bundle["id"] += "-linked"
        link["locator"]["assets"] = [asset["asset_link"]]
        self.mutate(import_bundle, bundle)
        caption_note = self.full_note(bundle, "image-not-inspected")
        del caption_note["inspections"][-1]["link"]["locator"]["assets"]
        self.assertEqual(self.mutate(record_reading, caption_note)["result"]["status"], "partial")
        self.assertEqual(self.mutate(record_reading, self.full_note(bundle))["result"]["status"], "complete")
        records = self.store.snapshot()["records"]
        self.assertEqual(validate_read_evidence(records, self.artifacts, link)["reading_id"], "full")
        self.assertEqual(records["source_bundle"][bundle["id"]]["visual_resources"][0]["resources"][0]["url"],
                         image_url)

    def test_pmc_direct_image_must_be_linked_and_explicitly_inspected(self):
        self.assert_reading(PMC_URL)

    def test_europe_pmc_html_uses_its_own_referenced_direct_image(self):
        self.assert_reading(EUROPE_PMC_URL)

    def test_pmc_scope_refuses_unrelated_and_archive_urls_and_retains_failed_attempts(self):
        bundle, link = self.build_figure_bundle()
        image_url = PMC_URL + "images/figure-1.jpg"
        visual = validate_link(self.store.snapshot()["records"], self.artifacts, link)["visual"]
        self.assertEqual([r["url"] for r in visual["resources"]], [image_url])
        for suffix in ("images/journal-logo.jpg", "supplementaryFiles", "figure/F1/"):
            with self.subTest(suffix=suffix):
                http, wire, _ = client([])
                before = self.store.snapshot()
                self.assert_error("invalid_visual_asset", lambda: acquire_visual_asset(
                    self.store, link, PMC_URL + suffix, http=http, expected_revision=self.store.revision,
                    request_id="unreferenced-" + suffix))
                self.assertEqual(self.store.snapshot(), before)
                self.assertEqual(wire.requests, [])
        failed, _, failed_revision = self.acquire(link, image_url,
            [(200, {"Content-Type": "image/jpeg"}, b"not a complete JPEG")])
        self.assertEqual(failed["status"], "pending")
        self.assertEqual(failed["capture"]["validation_status"], "malformed_visual_asset")
        self.assertIsNone(failed["asset_link"])
        self.assertEqual(self.artifacts.read(failed["capture"]["artifact"]), b"not a complete JPEG")
        recovered, _, _ = self.acquire(link, image_url)
        self.assertEqual(recovered["status"], "complete")
        bundle["id"] += "-recovered"
        link["locator"]["assets"] = [recovered["asset_link"]]
        self.mutate(import_bundle, bundle)
        self.assertEqual(self.mutate(record_reading, self.full_note(bundle))["result"]["status"], "complete")
        before = self.store.snapshot()
        original_link = copy.deepcopy(link)
        del original_link["locator"]["assets"]
        self.assertEqual(acquire_visual_asset(self.store, original_link, image_url,
            expected_revision=failed_revision, request_id=failed["request_id"]), failed)
        self.assertEqual(self.store.snapshot(), before)

    def test_existing_figure_records_and_reading_survive_new_pmc_acquisition(self):
        legacy_bundle, legacy_link = self.build_figure_bundle(identifier="doi:10.1234/authored-legacy", legacy=True)
        legacy_url = PMC_URL + "images/figure-1.jpg"
        original_link = copy.deepcopy(legacy_link)
        legacy_asset, _, revision = self.acquire(legacy_link, legacy_url)
        legacy_link["locator"]["assets"] = [legacy_asset["asset_link"]]
        self.mutate(import_bundle, legacy_bundle)
        legacy_reading = self.mutate(record_reading, self.full_note(legacy_bundle, "legacy-reading"))
        self.assertEqual(legacy_reading["result"]["status"], "complete")
        legacy_records = self.store.snapshot()["records"]
        _, link = self.build_figure_bundle()
        visual = validate_link(self.store.snapshot()["records"], self.artifacts, link)["visual"]
        self.assertEqual([r["url"] for r in visual["resources"]], [legacy_url])
        acquired, _, _ = self.acquire(link, legacy_url)
        self.assertEqual(acquired["status"], "complete")
        records = self.store.snapshot()["records"]
        for kind, values in legacy_records.items():
            for key, value in values.items():
                self.assertEqual(records[kind][key], value)
        accepted, partial = current_readings(records, self.artifacts, legacy_bundle["version_id"])
        self.assertEqual([r["id"] for r in accepted], ["legacy-reading"])
        self.assertEqual(partial, [])
        before = self.store.snapshot()
        self.assertEqual(acquire_visual_asset(self.store, original_link, legacy_url,
            expected_revision=revision, request_id=legacy_asset["request_id"]), legacy_asset)
        self.assertEqual(self.store.snapshot(), before)
