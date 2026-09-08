"""Authored regressions for source scope, original identity and visual bytes."""

import copy
import json
import struct
import zlib
from pathlib import Path
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from research_fixtures import client
from research_harness.acquisition import acquire_fulltext, import_response
from research_harness.literature import foundation_report, import_bundle
from research_harness.reading import record_reading, validate_read_evidence


class SourceContractTests(LiteratureCase):
    def png(self, pixel=0):
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(bytes((0, pixel, pixel, pixel)))) + chunk(b"IEND", b""))

    def asset(self, link, pixel=0, responses=None, **kwargs):
        from research_harness.visual_assets import acquire_visual_asset
        self.sequence += 1
        http, wire, _ = client(responses if responses is not None else [(200, {"Content-Type": "image/png"}, self.png(pixel))], max_retries=0)
        result = acquire_visual_asset(self.store, link, "https://arxiv.org/assets/required.png", http=http,
            expected_revision=self.store.revision, request_id="visual-" + str(self.sequence), **kwargs)
        return result, wire

    def mapped_link(self, identifier, field, value):
        raw = {"id": identifier, "title": "Authored partial tool response", field: value}
        self.sequence += 1
        result = import_response(self.store, "mcp", json.dumps(raw).encode(),
            source_url="https://example.org/tool", captured_at="2026-09-07T12:00:00Z",
            media_type="application/json", mappings=[{"id": "/id", "title": "/title", field: "/" + field}],
            expected_revision=self.store.revision, request_id="passage-" + str(self.sequence))
        source = self.store.snapshot()["records"]["source"][result["source_ids"][0]]
        return {"version_id": identifier, "source_id": source["id"], "artifact": source["response"],
                "locator": {"kind": "json", "pointer": "/" + field, "value": value}}

    def test_mapped_supplement_keeps_full_unit_pending_but_remains_readable(self):
        a = self.metadata()
        self.scope([a])
        bundle = self.bundle(a)
        bundle["units"].append({"id": "supplement", "kind": "supplement", "required": True,
                                "link": self.mapped_link(a, "abstract", "Only a supplement excerpt.")})
        self.mutate(import_bundle, bundle)
        result = self.mutate(record_reading, self.full_note(bundle))["result"]
        self.assertEqual(result["status"], "partial")
        self.assertIn("required_unit_incomplete", {p["code"] for p in result["pending"]})
        self.assertIn("fulltext_reading_missing", self.codes())

    def test_mapped_empty_references_cannot_complete_article_bibliography(self):
        a = self.metadata()
        bundle = self.bundle(a)
        bundle["units"][1]["link"] = self.mapped_link(a, "references", [])
        self.assert_error("invalid_bibliography", lambda: self.mutate(import_bundle, bundle))

    def test_optional_mapped_passage_does_not_gain_full_source_evidence_depth(self):
        a = self.metadata()
        bundle = self.bundle(a)
        link = self.mapped_link(a, "abstract", "A scoped tool excerpt.")
        bundle["units"].append({"id": "passage", "kind": "text", "required": False, "link": link})
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle))
        records = self.store.snapshot()["records"]
        self.assert_error("reading_missing", lambda: validate_read_evidence(records, self.artifacts, link, depth="fulltext"))
        self.assertEqual(validate_read_evidence(records, self.artifacts, link, depth="passage")["reading_id"], "full")

    def test_complete_original_supplement_and_its_inspection_remain_usable(self):
        a = self.metadata()
        bundle = self.bundle(a)
        supplement = self.capture(a, "The complete supplementary derivation. References: none.", abstract=False)
        link = self.link(a, supplement["source_id"], supplement["text"])
        bundle["units"].append({"id": "supplement", "kind": "supplement", "required": True, "link": link})
        self.mutate(import_bundle, bundle)
        self.assertEqual(self.mutate(record_reading, self.full_note(bundle))["result"]["status"], "complete")
        records = self.store.snapshot()["records"]
        pin = {"id": a, "sha256": records["source_bundle"][bundle["id"]]["original_sha256"]}
        self.assertEqual(validate_read_evidence(records, self.artifacts, link, target=pin)["reading_id"], "full")
        self.assertEqual(validate_read_evidence(records, self.artifacts, link)["reading_id"], "full")

    def test_complete_supplementary_bibliography_requires_a_complete_original_supplement(self):
        a = self.metadata()
        bundle = self.bundle(a)
        supplement = self.capture(a, "The complete supplementary bibliography. References: none.", abstract=False)
        whole = self.link(a, supplement["source_id"], supplement["text"])
        bundle["units"][1]["link"] = self.link(a, supplement["source_id"], supplement["text"], "References: none.")
        self.assert_error("invalid_bibliography", lambda: self.mutate(import_bundle, bundle))
        bundle["units"].append({"id": "supplement", "kind": "supplement", "required": True, "link": whole})
        self.mutate(import_bundle, bundle)
        self.assertEqual(self.mutate(record_reading, self.full_note(bundle))["result"]["status"], "complete")

    def test_partial_span_of_a_supplement_does_not_supply_full_supplement_depth(self):
        a = self.metadata()
        bundle = self.bundle(a)
        supplement = self.capture(a, "First part. Second essential part. References: none.", abstract=False)
        bundle["units"].append({"id": "supplement", "kind": "supplement", "required": True,
            "link": self.link(a, supplement["source_id"], supplement["text"], "First part.")})
        self.mutate(import_bundle, bundle)
        self.assertIn("required_unit_incomplete", {p["code"] for p in self.mutate(record_reading, self.full_note(bundle))["result"]["pending"]})

    def test_equal_extraction_does_not_cross_distinct_original_pin(self):
        a = self.metadata()
        first = self.bundle(a)
        self.mutate(import_bundle, first)
        self.mutate(record_reading, self.full_note(first))
        original = self.store.snapshot()["records"]["work"][a]["fulltexts"][0]
        changed_bytes = self.artifacts.read(original["original"]).replace(
            b"</article>", b'<figure><img src="new.png"></figure></article>')
        http, _, _ = client([(200, {"Content-Type": "text/html"}, changed_bytes)])
        changed = acquire_fulltext(self.store, a, "https://arxiv.org/html/" + a[6:], http=http,
            expected_revision=self.store.revision, request_id="changed-original")["capture"]
        self.assertEqual(original["text"]["sha256"], changed["text"]["sha256"])
        self.assertNotEqual(original["original"]["sha256"], changed["original"]["sha256"])
        pin = {"kind": "work", "id": a, "source_id": original["source_id"], "sha256": original["original"]["sha256"]}
        unread = self.link(a, changed["source_id"], changed["text"])
        self.assert_error("reading_missing", lambda: validate_read_evidence(
            self.store.snapshot()["records"], self.artifacts, unread, target=pin))

    def test_pdf_complete_inspected_abstract_supplies_selected_cohort_credit(self):
        collection = self.cohort((1,))
        a = "arxiv:2601.00001v1"
        self.scope([a], [collection])
        abstract = self.artifacts.read(self.store.snapshot()["records"]["work"][a]["abstract"]).decode()
        capture = self.capture(a, pdf_text=abstract + "\nThe bounded example. References: none.\f")
        self.assertIsNone(capture["includes_abstract"])
        bundle = self.bundle(a, capture)
        bundle["units"].append({"id": "abstract", "kind": "abstract", "required": True,
            "link": self.link(a, capture["source_id"], capture["text"], abstract)})
        self.mutate(import_bundle, bundle)
        result = self.mutate(record_reading, self.full_note(bundle))["result"]
        self.assertTrue(result["includes_abstract"])
        self.assertNotIn("cohort_abstract_reading_missing", self.codes())

    def test_pdf_abstract_credit_requires_the_corresponding_inspected_unit(self):
        a = self.metadata()
        capture = self.capture(a, pdf_text="The body lacks a delimited abstract. References: none.\f")
        bundle = self.bundle(a, capture)
        self.mutate(import_bundle, bundle)
        self.assertFalse(self.mutate(record_reading, self.full_note(bundle))["result"]["includes_abstract"])
        secondary = self.capture(a, "Supplementary text. References: none.")
        bundle["id"] = "secondary-abstract"
        bundle["units"].append({"id": "abstract", "kind": "abstract", "required": True,
            "link": self.link(a, secondary["source_id"], secondary["text"], "Source 1 studies bounded sequences and reports a finite example.")})
        self.mutate(import_bundle, bundle)
        self.assertFalse(self.mutate(record_reading, self.full_note(bundle, "secondary-read"))["result"]["includes_abstract"])

    def test_malformed_nullable_identifiers_raise_typed_errors_without_mutation(self):
        a = self.metadata()
        bundle = self.bundle(a)
        for invalid in ([], {}):
            bad = copy.deepcopy(bundle)
            bad["bibliography"]["unit_id"] = invalid
            before = self.store.snapshot()
            self.assert_error("invalid_bibliography", lambda: self.mutate(import_bundle, bad))
            self.assertEqual(before, self.store.snapshot())
        self.mutate(import_bundle, bundle)
        for field in ("bundle_id", "unit_id"):
            bad = self.full_note(bundle)
            if field == "bundle_id":
                bad[field] = []
            else:
                bad["inspections"][0][field] = []
            before = self.store.snapshot()
            self.assert_error("invalid_reading", lambda: self.mutate(record_reading, bad))
            self.assertEqual(before, self.store.snapshot())

    def external_figure(self, figure=None, before="", after=""):
        a = self.metadata()
        self.scope([a])
        figure = figure or '<figure id="f1"><img src="/assets/required.png"><figcaption>Figure one.</figcaption></figure>'
        capture = self.capture(a, "The result uses Figure one. References: none.</p>" + before + figure + after + "<p>")
        bundle = self.bundle(a, capture)
        link = {"version_id": a, "source_id": capture["source_id"], "artifact": capture["original"],
                "locator": {"kind": "html", "anchor": self.span(capture["original"], figure)}}
        bundle["units"].append({"id": "figure1", "kind": "figure", "required": True, "link": link})
        return a, bundle, link

    def test_document_style_outside_the_selected_figure_keeps_visual_pending(self):
        figure = '<figure id="f1"><div class="plot">Authored plot.</div><figcaption>Figure one.</figcaption></figure>'
        style = '<style>.plot { background-image: url("https://example.org/uncaptured-plot.png"); width: 100px; height: 100px; }</style>'
        a, bundle, link = self.external_figure(figure, before=style)
        self.mutate(import_bundle, bundle)
        result = self.mutate(record_reading, self.full_note(bundle))["result"]
        self.assertEqual(result["status"], "partial")
        self.assertIn("visual_document_style_unsupported", {p["code"] for p in result["pending"]})
        self.assertIn("fulltext_reading_missing", self.codes())
        self.assert_error("reading_missing", lambda: validate_read_evidence(self.store.snapshot()["records"], self.artifacts, link))
        self.assertFalse(next(i for i in foundation_report(self.store, "research")["inventory"] if i["version_id"] == a)["fulltext_read"])

    def test_inline_svg_animation_stays_pending_after_its_base_image_is_captured(self):
        figure = ('<figure id="f1"><svg xmlns="http://www.w3.org/2000/svg"><image href="/assets/required.png">'
                  '<animate attributeName="href" values="/assets/required.png;https://example.org/uncaptured-frame.png" '
                  'dur="1s" repeatCount="indefinite"/></image></svg></figure>')
        _, bundle, link = self.external_figure(figure)
        asset, _ = self.asset(link)
        link["locator"]["assets"] = [asset["asset_link"]]
        self.mutate(import_bundle, bundle)
        result = self.mutate(record_reading, self.full_note(bundle))["result"]
        self.assertEqual(result["status"], "partial")
        self.assertIn("dynamic_visual_unsupported", {p["code"] for p in result["pending"]})
        self.assertIn("fulltext_reading_missing", self.codes())

    def test_jpeg_without_frame_or_scan_is_saved_as_pending(self):
        _, bundle, link = self.external_figure()
        response = (200, {"Content-Type": "image/jpeg"}, b"\xff\xd8\xff\xff\xd9")
        asset, _ = self.asset(link, responses=[response])
        self.assertEqual(asset["status"], "pending")
        self.assertEqual(asset["capture"]["validation_status"], "malformed_visual_asset")
        self.assertIsNone(asset["asset_link"])
        self.assertEqual(self.artifacts.read(asset["capture"]["artifact"]), response[2])
        self.mutate(import_bundle, bundle)
        self.assertEqual(self.mutate(record_reading, self.full_note(bundle))["result"]["status"], "partial")
        self.assertIn("visual_asset_pending", self.codes())

    def test_no_image_gif_webp_and_png_responses_stay_saved_and_pending(self):
        from test_research_visual_formats import no_image_containers
        _, bundle, link = self.external_figure()
        self.mutate(import_bundle, bundle)
        for media_type, data in no_image_containers():
            with self.subTest(media_type=media_type):
                asset, _ = self.asset(link, responses=[(200, {"Content-Type": media_type}, data)])
                self.assertEqual(asset["status"], "pending")
                self.assertEqual(asset["capture"]["validation_status"], "malformed_visual_asset")
                self.assertEqual(self.artifacts.read(asset["capture"]["artifact"]), data)
                self.assertIsNone(asset["asset_link"])
                self.assertIn("visual_asset_pending", self.codes())

    def test_valid_authored_raster_containers_support_complete_visual_inspections(self):
        _, bundle, link = self.external_figure()
        for index, (name, media_type) in enumerate((("authored-grid.jpg", "image/jpeg"),
                ("authored-grid-progressive.jpg", "image/jpeg"), ("authored-grid.gif", "image/gif"),
                ("authored-grid.webp", "image/webp"), ("authored-grid-lossless.webp", "image/webp"))):
            with self.subTest(name=name):
                body = (Path(__file__).parent / "fixtures/research" / name).read_bytes()
                asset, _ = self.asset(link, responses=[(200, {"Content-Type": media_type}, body)])
                self.assertEqual(asset["status"], "complete")
                bundle["id"] = "raster-" + str(index)
                link["locator"]["assets"] = [asset["asset_link"]]
                self.mutate(import_bundle, bundle)
                self.assertEqual(self.mutate(record_reading, self.full_note(bundle, "raster-read-" + str(index)))["result"]["status"], "complete")
                self.assertNotIn("fulltext_reading_missing", self.codes())

    def test_prior_admitted_malformed_visual_is_rechecked_without_rewriting_history(self):
        from research_harness.reading import current_readings, required_unit_obligations
        from research_harness.visual_assets import acquire_visual_asset
        a, bundle, link = self.external_figure()
        original_link = copy.deepcopy(link)
        revision = self.store.revision
        # Reproduce a historical receipt created before structural validation.
        with patch("research_harness.visual_assets.validate_visual", return_value=None):
            asset, _ = self.asset(link, responses=[(200, {"Content-Type": "image/jpeg"}, b"\xff\xd8\xff\xff\xd9")])
            link["locator"]["assets"] = [asset["asset_link"]]
            self.mutate(import_bundle, bundle)
            self.assertEqual(self.mutate(record_reading, self.full_note(bundle))["result"]["status"], "complete")
        before = self.store.snapshot()
        records = before["records"]
        accepted, partial = current_readings(records, self.artifacts, a)
        self.assertFalse(accepted)
        self.assertEqual(partial[0]["assessment"]["status"], "partial")
        pending = required_unit_obligations(records, self.artifacts, records["source_bundle"][bundle["id"]])
        self.assertTrue(any(p["code"] == "visual_asset_pending" and p["reason"] == "malformed_visual_asset" for p in pending))
        self.assert_error("reading_missing", lambda: validate_read_evidence(records, self.artifacts, link))
        self.assertIn("fulltext_reading_missing", self.codes())
        self.assertEqual(acquire_visual_asset(self.store, original_link, "https://arxiv.org/assets/required.png",
            expected_revision=revision, request_id=asset["request_id"]), asset)
        self.assertEqual(before, self.store.snapshot())

    def legacy_visual_reading(self, bundle, link, pending_code):
        from research_harness.html_visuals import resource_inventory
        from research_harness.reading import current_readings, required_unit_obligations
        source = self.store.snapshot()["records"]["source"][link["source_id"]]
        inventory = resource_inventory(self.artifacts.read(link["artifact"]).decode(), source["url"], link["locator"]["anchor"])
        # Retain the same omission as a previously admitted source inventory.
        inventory["pending"] = []
        inventory["resources"] = [r for r in inventory["resources"] if r["url"] == "https://arxiv.org/assets/required.png"]
        with patch("research_harness.visual_assets.resource_inventory", return_value=inventory):
            self.mutate(import_bundle, bundle)
            self.assertEqual(self.mutate(record_reading, self.full_note(bundle))["result"]["status"], "complete")
            previous_digest = foundation_report(self.store, "research")["digest"]
        before = self.store.snapshot()
        records = before["records"]
        accepted, partial = current_readings(records, self.artifacts, bundle["version_id"])
        self.assertFalse(accepted)
        self.assertEqual(partial[0]["assessment"]["status"], "partial")
        pending = required_unit_obligations(records, self.artifacts, records["source_bundle"][bundle["id"]])
        self.assertIn(pending_code, {p["code"] for p in pending})
        self.assert_error("reading_missing", lambda: validate_read_evidence(records, self.artifacts, link))
        self.assertIn("fulltext_reading_missing", self.codes())
        self.assertNotEqual(previous_digest, foundation_report(self.store, "research")["digest"])
        self.assertEqual(before, self.store.snapshot())

    def test_previous_document_style_reading_is_rechecked_from_original_html(self):
        figure = '<figure id="f1"><div class="plot">Authored plot.</div></figure>'
        _, bundle, link = self.external_figure(figure, before='<style>.plot { background-image: url(/hidden.png) }</style>')
        self.legacy_visual_reading(bundle, link, "visual_document_style_unsupported")

    def test_previous_inline_svg_reading_is_rechecked_from_original_html(self):
        figure = ('<figure id="f1"><svg><image href="/assets/required.png">'
                  '<animate attributeName="href" values="/assets/required.png;/hidden.png" dur="1s"/></image></svg></figure>')
        _, bundle, link = self.external_figure(figure)
        asset, _ = self.asset(link)
        link["locator"]["assets"] = [asset["asset_link"]]
        self.legacy_visual_reading(bundle, link, "dynamic_visual_unsupported")

    def test_external_figure_anchor_without_image_bytes_remains_pending(self):
        _, bundle, _ = self.external_figure()
        self.mutate(import_bundle, bundle)
        assessment = self.mutate(record_reading, self.full_note(bundle))["result"]
        self.assertEqual(assessment["status"], "partial")
        missing = [p for p in assessment["pending"] if p["code"] == "visual_asset_missing"]
        self.assertEqual(missing[0]["url"], "https://arxiv.org/assets/required.png")
        self.assertIn("fulltext_reading_missing", self.codes())

    def test_missing_visual_bytes_are_actionable_before_a_reading_is_recorded(self):
        _, bundle, _ = self.external_figure()
        self.mutate(import_bundle, bundle)
        self.assertIn("visual_asset_missing", self.codes())

    def test_visual_bibliography_needs_its_actual_bytes_before_completeness(self):
        _, bundle, link = self.external_figure()
        bundle["units"][1]["link"] = copy.deepcopy(link)
        self.assert_error("invalid_bibliography", lambda: self.mutate(import_bundle, bundle))

    def test_saved_asset_requires_an_explicit_byte_inspection_and_supports_redirect_replay(self):
        from research_harness.visual_assets import acquire_visual_asset
        a, bundle, link = self.external_figure()
        revision = self.store.revision
        result, wire = self.asset(link, responses=[(302, {"Location": "https://example.org/cdn/figure.png"}, b""),
                                                  (200, {"Content-Type": "image/png"}, self.png())])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["capture"]["source_ids"]), 2)
        self.assertEqual(result["capture"]["final_url"], "https://example.org/cdn/figure.png")
        self.assertEqual(acquire_visual_asset(self.store, link, "https://arxiv.org/assets/required.png",
            expected_revision=revision, request_id=result["request_id"]), result)
        self.assertEqual(len(wire.requests), 2)
        bundle["units"][-1]["link"]["locator"]["assets"] = [result["asset_link"]]
        self.mutate(import_bundle, bundle)
        note = self.full_note(bundle, "anchor-only")
        del note["inspections"][-1]["link"]["locator"]["assets"]
        self.assertEqual(self.mutate(record_reading, note)["result"]["status"], "partial")
        self.assertEqual(self.mutate(record_reading, self.full_note(bundle))["result"]["status"], "complete")
        self.assertNotIn("fulltext_reading_missing", self.codes())
        item = next(x for x in foundation_report(self.store, "research")["inventory"] if x["version_id"] == a)
        self.assertIn(result["asset_link"]["artifact"]["path"], item["source_paths"])

    def test_changed_asset_behind_identical_html_requires_new_bundle_and_reading(self):
        _, bundle, link = self.external_figure()
        first, _ = self.asset(link)
        bundle["units"][-1]["link"]["locator"]["assets"] = [first["asset_link"]]
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle))
        before = foundation_report(self.store, "research")
        second, _ = self.asset(link, pixel=255)
        after = foundation_report(self.store, "research")
        self.assertNotEqual(before["digest"], after["digest"])
        self.assertIn("visual_asset_stale", {o["code"] for o in after["obligations"]})
        self.assertIn("fulltext_reading_missing", {o["code"] for o in after["obligations"]})
        bundle["id"] = "changed-asset"
        bundle["units"][-1]["link"]["locator"]["assets"] = [second["asset_link"]]
        self.mutate(import_bundle, bundle)
        self.assertIn("fulltext_reading_missing", self.codes())
        self.mutate(record_reading, self.full_note(bundle, "changed-read"))
        self.assertNotIn("fulltext_reading_missing", self.codes())
        self.assertEqual(len(self.store.snapshot()["records"]["reading"]), 2)

    def test_identical_asset_bytes_reuse_a_reading_across_receipts(self):
        _, bundle, link = self.external_figure()
        first, _ = self.asset(link)
        bundle["units"][-1]["link"]["locator"]["assets"] = [first["asset_link"]]
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle))
        second, _ = self.asset(link)
        bundle["id"] = "same-asset"
        bundle["units"][-1]["link"]["locator"]["assets"] = [second["asset_link"]]
        self.mutate(import_bundle, bundle)
        self.assertNotIn("fulltext_reading_missing", self.codes())
        self.assertEqual(validate_read_evidence(self.store.snapshot()["records"], self.artifacts,
            bundle["units"][-1]["link"])["reading_id"], "full")

    def test_caption_anchor_cannot_evade_known_external_figure(self):
        _, bundle, link = self.external_figure()
        link["locator"]["anchor"] = self.span(link["artifact"], "Figure one.</figcaption>")
        self.mutate(import_bundle, bundle)
        assessment = self.mutate(record_reading, self.full_note(bundle))["result"]
        self.assertIn("visual_asset_missing", {p["code"] for p in assessment["pending"]})
        changed = copy.deepcopy(bundle)
        changed["id"] = "evaded"
        changed["units"][-1]["link"]["locator"]["anchor"] = self.span(link["artifact"], "The result uses Figure one.")
        self.assert_error("invalid_bundle", lambda: self.mutate(import_bundle, changed))

    def test_unreferenced_asset_url_cannot_be_acquired_for_an_html_unit(self):
        from research_harness.visual_assets import acquire_visual_asset
        _, _, link = self.external_figure()
        http, wire, _ = client([])
        before = self.store.snapshot()
        self.assert_error("invalid_visual_asset", lambda: acquire_visual_asset(self.store, link,
            "https://example.org/unrelated.png", http=http, expected_revision=self.store.revision, request_id="wrong-asset"))
        self.assertEqual(before, self.store.snapshot())
        self.assertFalse(wire.requests)

    def test_failed_partial_and_unsupported_assets_retain_precise_pending_evidence(self):
        _, bundle, link = self.external_figure()
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle))
        for response, code in (((206, {"Content-Type": "image/png"}, self.png()), "partial_response"),
                               ((404, {"Content-Type": "text/plain"}, b"Missing"), "http_status"),
                               ((200, {"Content-Type": "image/png"}, b"not an image"), "malformed_visual_asset")):
            result, _ = self.asset(link, responses=[response])
            self.assertEqual(result["status"], "pending")
            self.assertEqual(result["pending"][0]["code"], code)
            self.assertIn("visual_asset_pending", self.codes())
            self.assertIsNotNone(result["capture"]["artifact"])

    def test_self_contained_svg_is_supported_and_external_or_escaped_dependencies_stay_pending(self):
        _, bundle, link = self.external_figure()
        static = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><path d="M0 0L10 10" stroke="black"/><text x="0" y="9">h(x)</text></svg>'
        result, _ = self.asset(link, responses=[(200, {"Content-Type": "image/svg+xml"}, static)])
        self.assertEqual(result["status"], "complete")
        bundle["units"][-1]["link"]["locator"]["assets"] = [result["asset_link"]]
        self.mutate(import_bundle, bundle)
        self.assertEqual(self.mutate(record_reading, self.full_note(bundle))["result"]["status"], "complete")
        for child in ('<image href="https://example.org/uncaptured.png"/>',
                      '<path style="fill:u\\72l(https://example.org/uncaptured.svg#shape)"/>',
                      '<path style="fill:image-set(&quot;https://example.org/uncaptured.png&quot; 1x)"/>',
                      '<style>path { fill: url(https://example.org/uncaptured.svg#shape) }</style>',
                      '<svg xml:base="https://example.org/external.svg"><use href="#shape"/></svg>'):
            body = ('<svg xmlns="http://www.w3.org/2000/svg">' + child + '</svg>').encode()
            result, _ = self.asset(link, responses=[(200, {"Content-Type": "image/svg+xml"}, body)])
            self.assertEqual(result["status"], "pending")
            self.assertEqual(result["pending"][0]["code"], "visual_asset_dependencies_unsupported")
            self.assertIn("fulltext_reading_missing", self.codes())

    def test_asset_link_cannot_borrow_another_work_with_identical_html_bytes(self):
        from research_harness.source_links import validate_link
        a, _, link = self.external_figure()
        b = self.metadata(2)
        http, _, _ = client([(200, {"Content-Type": "text/html"}, self.artifacts.read(link["artifact"]))])
        capture = acquire_fulltext(self.store, b, "https://arxiv.org/html/" + b[6:], http=http,
            expected_revision=self.store.revision, request_id="other-html")["capture"]
        other = copy.deepcopy(link)
        other.update(version_id=b, source_id=capture["source_id"], artifact=capture["original"])
        result, _ = self.asset(other)
        forged = copy.deepcopy(link)
        forged["locator"]["assets"] = [result["asset_link"]]
        self.assertEqual(forged["version_id"], a)
        self.assert_error("invalid_visual_asset", lambda: validate_link(self.store.snapshot()["records"], self.artifacts, forged))

    def test_asset_budget_pause_and_concurrent_revision_do_not_publish_a_false_capture(self):
        from research_harness.visual_assets import acquire_visual_asset
        _, _, link = self.external_figure()
        result, wire = self.asset(link, max_requests=0)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["pending"][0]["code"], "request_budget")
        self.assertFalse(wire.requests)
        self.assertIsNone(result["capture"]["artifact"])
        http, _, _ = client([(200, {"Content-Type": "image/png"}, self.png())])
        original_get = http.get
        def concurrently_changed(*args, **kwargs):
            response = original_get(*args, **kwargs)
            self.store.mutate("concurrent-note", {}, lambda tx: tx.put("note", "concurrent", {"text": "Concurrent update"}),
                              expected_revision=self.store.revision, request_id="concurrent")
            return response
        http.get = concurrently_changed
        self.assert_error("stale_revision", lambda: acquire_visual_asset(self.store, link, "https://arxiv.org/assets/required.png",
            http=http, expected_revision=self.store.revision, request_id="interrupted-visual"))
        records = self.store.snapshot()["records"]
        self.assertNotIn("interrupted-visual", records["visual_asset"])
        self.assertEqual(records["acquisition_operation"]["interrupted-visual"]["state"], "admitted")

    def test_inline_svg_escaped_resources_are_pending(self):
        a = self.metadata()
        figure = '<figure id="f1"><svg><path style="fill:u\\72l(https://example.org/uncaptured.svg#shape)"/></svg></figure>'
        capture = self.capture(a, "References: none.</p>" + figure + "<p>")
        bundle = self.bundle(a, capture)
        bundle["units"].append({"id": "figure1", "kind": "figure", "required": True,
            "link": {"version_id": a, "source_id": capture["source_id"], "artifact": capture["original"],
                     "locator": {"kind": "html", "anchor": self.span(capture["original"], figure)}}})
        self.mutate(import_bundle, bundle)
        self.assertEqual(self.mutate(record_reading, self.full_note(bundle))["result"]["status"], "partial")

    def test_html_base_and_srcset_retain_every_resolved_resource(self):
        from research_harness.visual_assets import acquire_visual_asset
        a = self.metadata()
        figure = '<figure id="f1"><picture><source srcset="one.png 1x, two.png 2x"><img src="one.png"></picture></figure>'
        capture = self.capture(a, '<base href="https://example.org/figures/">References: none.</p>' + figure + '<p>')
        bundle = self.bundle(a, capture)
        link = {"version_id": a, "source_id": capture["source_id"], "artifact": capture["original"],
                "locator": {"kind": "html", "anchor": self.span(capture["original"], figure)}}
        bundle["units"].append({"id": "figure1", "kind": "figure", "required": True, "link": link})
        self.mutate(import_bundle, bundle)
        result = self.mutate(record_reading, self.full_note(bundle))["result"]
        self.assertEqual({p["url"] for p in result["pending"] if p["code"] == "visual_asset_missing"},
                         {"https://example.org/figures/one.png", "https://example.org/figures/two.png"})
        assets = []
        for index, name in enumerate(("one.png", "two.png")):
            http, _, _ = client([(200, {"Content-Type": "image/png"}, self.png(index))])
            result = acquire_visual_asset(self.store, link, "https://example.org/figures/" + name, http=http,
                expected_revision=self.store.revision, request_id="srcset-" + str(index))
            assets.append(result["asset_link"])
            bundle["id"] = "srcset-bundle-" + str(index)
            link["locator"]["assets"] = assets[:]
            self.mutate(import_bundle, bundle)
            status = self.mutate(record_reading, self.full_note(bundle, "srcset-read-" + str(index)))["result"]["status"]
            self.assertEqual(status, "partial" if index == 0 else "complete")
