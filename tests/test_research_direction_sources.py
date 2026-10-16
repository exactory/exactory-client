"""Direction reading credit requires supplied, source-checked content."""

import copy
import base64
import json

from literature_fixtures import LiteratureCase
from research_harness import literature, reading


class DirectionSourceTests(LiteratureCase):
    def setUp(self):
        super().setUp()
        self.version = self.metadata()
        self.bundle_value = self.bundle(self.version)
        self.mutate(literature.import_bundle, self.bundle_value)
        self.mutate(reading.record_reading, self.full_note(self.bundle_value))

    def find_readings(self):
        return list(self.store.snapshot()["records"]["reading"].values())

    def build_passages(self):
        from research_harness.direction_sources import build_reading_passages
        prepared = build_reading_passages(self.store.snapshot()["records"], self.artifacts, self.find_readings())
        for passage, data in prepared:
            self.assertEqual(self.artifacts.put(data, passage["excerpt"]["media_type"]), passage["excerpt"])
        return [passage for passage, _ in prepared]

    def build_packet(self, passages):
        return {"direction_evidence": {"passages": passages}, "evidence": [
            {"artifact": item["excerpt"],
             "content": (base64.b64encode(self.artifacts.read(item["excerpt"])).decode()
                         if item["excerpt"]["media_type"] == "application/pdf" else self.artifacts.read(item["excerpt"]).decode()),
             "encoding": "base64" if item["excerpt"]["media_type"] == "application/pdf" else "utf-8"}
            for item in passages]}

    def find_covered_readings(self, packet):
        from research_harness.direction_sources import find_delivered_readings
        return find_delivered_readings(self.store.snapshot()["records"], self.artifacts, self.find_readings(), [packet])

    def test_building_expected_passages_describes_bytes_without_storing_objects(self):
        from research_harness.artifacts import describe_artifact
        from research_harness.direction_sources import build_reading_passages
        objects = self.root / "research/sources/objects"
        before = sorted(path.name for path in objects.iterdir())
        prepared = build_reading_passages(self.store.snapshot()["records"], self.artifacts, self.find_readings())
        self.assertEqual(sorted(path.name for path in objects.iterdir()), before)
        self.assertTrue(any(not (self.root / passage["excerpt"]["path"]).exists() for passage, _ in prepared))
        for passage, data in prepared:
            self.assertEqual(passage["excerpt"], describe_artifact(data, passage["excerpt"]["media_type"]))

    def test_checked_passages_cover_but_descriptors_and_wrong_content_do_not(self):
        packet = self.build_packet(self.build_passages())
        self.assertEqual(self.find_covered_readings(packet), {"full"})
        packet["evidence"] = []
        self.assertEqual(self.find_covered_readings(packet), set())
        packet = self.build_packet(self.build_passages())
        for item in packet["evidence"]:
            item["content"] = "The reviewer did not receive the source."
        self.assertEqual(self.find_covered_readings(packet), set())

    def test_legacy_prior_context_counts_only_its_actual_delivered_passages(self):
        from research_harness.bar_sources import prior_context
        passages = prior_context(self.store.snapshot()["records"], self.artifacts,
                                 [item["link"] for item in self.find_readings()[0]["inspections"]])
        packet = self.build_packet(passages)
        packet["prior_context"] = packet.pop("direction_evidence")["passages"]
        self.assertEqual(self.find_covered_readings(packet), {"full"})
        packet["evidence"] = []
        self.assertEqual(self.find_covered_readings(packet), set())

    def test_source_delivery_requires_exact_version_and_attached_captured_text(self):
        capture = self.store.snapshot()["records"]["work"][self.version]["fulltexts"][0]
        reference = capture["text"]
        packet = {"source_deliveries": [{"request": {"kind": "source", "version_id": self.version, "depth": "fulltext"},
                    "status": "delivered", "evidence": [reference]}], "evidence": [
                    {"artifact": reference, "content": self.artifacts.read(reference).decode(), "encoding": "utf-8"}]}
        self.assertEqual(self.find_covered_readings(packet), {"full"})
        packet["source_deliveries"][0]["request"]["version_id"] = self.version[:-1] + "2"
        self.assertEqual(self.find_covered_readings(packet), set())
        packet["source_deliveries"][0]["request"]["version_id"] = self.version
        packet["evidence"] = []
        self.assertEqual(self.find_covered_readings(packet), set())

    def test_partial_source_delivery_does_not_substitute_for_decisive_content(self):
        packet = self.build_packet(self.build_passages())
        packet["direction_evidence"] = {}
        packet["source_deliveries"] = [{"request": {"kind": "source", "version_id": self.version, "depth": "fulltext"},
            "status": "partial", "evidence": [item["artifact"] for item in packet["evidence"]]}]
        self.assertEqual(self.find_covered_readings(packet), set())

    def test_historical_source_delivery_does_not_cover_a_later_capture_of_the_same_version(self):
        reference = self.bundle_value["units"][0]["link"]["artifact"]
        packet = {"source_deliveries": [{"request": {"kind": "source", "version_id": self.version, "depth": "fulltext"},
                    "status": "delivered", "evidence": [reference]}], "evidence": [
                    {"artifact": reference, "content": self.artifacts.read(reference).decode(), "encoding": "utf-8"}]}
        capture = self.capture(self.version, body="LATER DECISIVE SOURCE CONTENT. References: none.")
        bundle = self.bundle(self.version, capture=capture, bundle_id="later-capture")
        self.mutate(literature.import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle, note_id="later-reading"))
        self.assertEqual(self.find_covered_readings(packet), {"full"})

    def test_legacy_dossier_attachment_of_the_exact_source_text_covers_the_reading(self):
        reference = self.bundle_value["units"][0]["link"]["artifact"]
        packet = {"dossier": {"candidates": [{"evidence": [reference]}]}, "evidence": [
            {"artifact": reference, "content": self.artifacts.read(reference).decode(), "encoding": "utf-8"}]}
        self.assertEqual(self.find_covered_readings(packet), {"full"})

    def test_hash_span_passages_stay_bounded_and_keep_source_provenance(self):
        version = self.metadata(2)
        capture = self.capture(version, body="DECISIVE START. " + "bounded evidence " * 10000 + " References: none.")
        bundle = self.bundle(version, capture=capture, bundle_id="large")
        for unit in bundle["units"]:
            link = unit["link"]
            link["locator"] = self.span(link["artifact"], link["locator"]["quote"], kind="span")
        self.mutate(literature.import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle, note_id="large-reading"))
        passages = self.build_passages()
        packet = self.build_packet(passages)
        self.assertLess(len(json.dumps(packet)), 25000)
        self.assertTrue(any(item.get("truncated") for item in passages if item["version_id"] == version))
        self.assertTrue(all(len(self.artifacts.read(item["excerpt"]).decode()) <= 2000 for item in passages))
        self.assertNotIn(capture["text"], [item["artifact"] for item in packet["evidence"]])
        self.assertEqual(self.find_covered_readings(packet), {"full", "large-reading"})

    def test_pdf_inspected_region_delivers_original_visual_content_and_provenance(self):
        version = self.metadata(3)
        capture = self.capture(version, pdf_text="INSPECTED PDF PAGE. References: none.\f")
        bundle = self.bundle(version, capture=capture, bundle_id="pdf")
        bundle["units"].append({"id": "page", "kind": "figure", "required": True,
            "link": {"version_id": version, "source_id": capture["source_id"], "artifact": capture["original"],
                     "locator": {"kind": "pdf", "page_index": 0, "printed_page": "1", "region": [0, 0, 1, 1]}}})
        self.mutate(literature.import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle, note_id="pdf-reading"))
        passages = self.build_passages()
        page = next(item for item in passages if item["origin"]["locator"]["kind"] == "pdf")
        self.assertEqual(self.artifacts.read(page["excerpt"]), self.artifacts.read(capture["original"]))
        self.assertEqual(page["origin"]["artifact_descriptor"]["sha256"], capture["original"]["sha256"])
        self.assertEqual(self.find_covered_readings(self.build_packet(passages)), {"full", "pdf-reading"})
        forged = copy.deepcopy(self.build_packet(passages))
        for item in forged["direction_evidence"]["passages"]:
            if item["version_id"] == version:
                item["origin"]["source_id"] = "different-capture"
        self.assertEqual(self.find_covered_readings(forged), {"full"})
        reference = capture["text"]
        text_only = {"evidence": [{"artifact": reference, "content": self.artifacts.read(reference).decode(), "encoding": "utf-8"}]}
        self.assertEqual(self.find_covered_readings(text_only), set())

    def test_blank_pdf_inspection_delivers_original_instead_of_an_empty_derivative(self):
        version = self.metadata(4)
        capture = self.capture(version, pdf_text="\fReferences: none.\f")
        bundle = self.bundle(version, capture=capture, bundle_id="visual-pdf")
        bundle["units"].append({"id": "figure", "kind": "figure", "required": True,
            "link": {"version_id": version, "source_id": capture["source_id"], "artifact": capture["original"],
                     "locator": {"kind": "pdf", "page_index": 0, "printed_page": None, "region": [0, 0, 1, 1]}}})
        self.mutate(literature.import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle, note_id="visual-reading"))
        passages = self.build_passages()
        page = next(item for item in passages if item["origin"]["locator"]["kind"] == "pdf")
        self.assertEqual(page["excerpt"], capture["original"])
        self.assertEqual(self.find_covered_readings(self.build_packet(passages)), {"full", "visual-reading"})

    def test_inferred_binary_pdf_is_delivered_as_checked_pdf_bytes(self):
        from research_fixtures import client
        from research_harness.acquisition import acquire_fulltext
        from test_research_binary_pdf import authored_pdf
        version = self.metadata(8)
        data = authored_pdf()
        http, _, _ = client([(200, {"Content-Type": "application/octet-stream"}, data)])
        capture = acquire_fulltext(self.store, version, "https://arxiv.org/pdf/" + version[6:], http=http,
            extractor=lambda data: {"status": "extracted", "text": "References: none.\f"},
            expected_revision=self.store.revision, request_id="binary-pdf")["capture"]
        bundle = self.bundle(version, capture=capture, bundle_id="binary-pdf")
        bundle["units"].append({"id": "page", "kind": "figure", "required": True,
            "link": {"version_id": version, "source_id": capture["source_id"], "artifact": capture["original"],
                     "locator": {"kind": "pdf", "page_index": 0, "printed_page": None, "region": [0, 0, 1, 1]}}})
        self.mutate(literature.import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle, note_id="binary-pdf-reading"))
        passages = self.build_passages()
        page = next(item for item in passages if item["origin"]["locator"]["kind"] == "pdf")
        self.assertEqual(page["excerpt"]["media_type"], "application/pdf")
        self.assertEqual(page["origin"]["artifact_descriptor"]["media_type"], "application/octet-stream")
        self.assertEqual(self.artifacts.read(page["excerpt"]), data)
        self.assertEqual(self.find_covered_readings(self.build_packet(passages)), {"full", "binary-pdf-reading"})

    def test_json_inspection_delivers_bounded_source_value_with_its_pointer(self):
        version = self.metadata(5)
        capture = self.capture(version, pdf_text=json.dumps({"value": "checked " * 1000, "references": "References: none."}))
        bundle = self.bundle(version, capture=capture, bundle_id="json-pdf")
        bundle["units"].append({"id": "structured", "kind": "text", "required": False,
            "link": {"version_id": version, "source_id": capture["source_id"], "artifact": capture["text"],
                     "locator": {"kind": "json", "pointer": "/value", "value": "checked " * 1000}}})
        self.mutate(literature.import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle, note_id="json-reading"))
        passages = self.build_passages()
        structured = next(item for item in passages if item["origin"]["locator"]["kind"] == "json")
        self.assertEqual(structured["origin"]["locator"]["pointer"], "/value")
        self.assertEqual(self.artifacts.read(structured["excerpt"]), ("checked " * 250).encode())
        self.assertEqual(self.find_covered_readings(self.build_packet(passages)), {"full", "json-reading"})

    def test_html_inspection_delivers_the_checked_anchor_with_provenance(self):
        version = self.metadata(6)
        capture = self.capture(version, body="INSPECTED HTML ANCHOR. References: none.")
        bundle = self.bundle(version, capture=capture, bundle_id="html")
        bundle["units"].append({"id": "markup", "kind": "figure", "required": False,
            "link": {"version_id": version, "source_id": capture["source_id"], "artifact": capture["original"],
                     "locator": {"kind": "html", "anchor": self.span(capture["original"], "INSPECTED HTML ANCHOR.", kind="span")}}})
        self.mutate(literature.import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle, note_id="html-reading"))
        passages = self.build_passages()
        markup = next(item for item in passages if item["origin"]["locator"]["kind"] == "html")
        self.assertEqual(self.artifacts.read(markup["excerpt"]), b"INSPECTED HTML ANCHOR.")
        self.assertEqual(markup["origin"]["artifact_descriptor"]["sha256"], capture["original"]["sha256"])
        self.assertEqual(self.find_covered_readings(self.build_packet(passages)), {"full", "html-reading"})

    def test_extracted_text_does_not_cover_an_omitted_original_markup_inspection(self):
        version = self.metadata(10)
        capture = self.capture(version, body='<svg><path d="M0 0L8 8" stroke="black"/></svg> References: none.')
        bundle = self.bundle(version, capture=capture, bundle_id="original-markup")
        bundle["units"].append({"id": "geometry", "kind": "text", "required": False,
            "link": self.link(version, capture["source_id"], capture["original"], 'M0 0L8 8', kind="span")})
        self.mutate(literature.import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle, note_id="markup-reading"))
        reference = capture["text"]
        self.assertNotIn(b'M0 0L8 8', self.artifacts.read(reference))
        packet = {"evidence": [{"artifact": reference, "content": self.artifacts.read(reference).decode(), "encoding": "utf-8"}]}
        self.assertEqual(self.find_covered_readings(packet), set())

    def test_large_html_anchor_keeps_only_bounded_content_and_compact_provenance(self):
        version = self.metadata(7)
        capture = self.capture(version, body="HTML BOUNDARY " * 10000 + "References: none.")
        bundle = self.bundle(version, capture=capture, bundle_id="large-html")
        for unit in bundle["units"]:
            link = unit["link"]
            link["locator"] = self.span(link["artifact"], link["locator"]["quote"], kind="span")
        bundle["units"].append({"id": "markup", "kind": "figure", "required": False,
            "link": {"version_id": version, "source_id": capture["source_id"], "artifact": capture["original"],
                     "locator": {"kind": "html", "anchor": self.span(capture["original"])}}})
        self.mutate(literature.import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle, note_id="large-html-reading"))
        passages = self.build_passages()
        self.assertLess(len(json.dumps(self.build_packet(passages))), 30000)
        markup = next(item for item in passages if item["origin"]["locator"]["kind"] == "html")
        self.assertEqual(markup["origin"]["locator"]["anchor"]["kind"], "span")
        self.assertEqual(self.find_covered_readings(self.build_packet(passages)), {"full", "large-html-reading"})

    def test_html_visual_asset_provenance_does_not_attach_unrequested_asset_bytes(self):
        from research_fixtures import client
        from research_harness.visual_assets import acquire_visual_asset
        from test_research_source_contracts import SourceContractTests
        version = self.metadata(9)
        capture = self.capture(version, body='<figure><img src="/assets/required.png"><figcaption>CHECKED FIGURE.</figcaption></figure> References: none.')
        link = {"version_id": version, "source_id": capture["source_id"], "artifact": capture["original"],
                "locator": {"kind": "html", "anchor": self.span(capture["original"], '<figcaption>CHECKED FIGURE.</figcaption>', kind="span")}}
        http, _, _ = client([(200, {"Content-Type": "image/png"}, SourceContractTests.png(self))])
        result = acquire_visual_asset(self.store, link, "https://arxiv.org/assets/required.png", http=http,
            expected_revision=self.store.revision, request_id="direction-visual")
        asset = result["asset_link"]
        link["locator"]["assets"] = [asset]
        bundle = self.bundle(version, capture=capture, bundle_id="html-visual")
        bundle["units"].append({"id": "figure", "kind": "figure", "required": True, "link": link})
        self.mutate(literature.import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle, note_id="html-visual-reading"))
        passages = self.build_passages()
        markup = next(item for item in passages if item["origin"]["locator"]["kind"] == "html")
        self.assertEqual(markup["origin"]["locator"]["assets"][0]["artifact_descriptor"]["sha256"], asset["artifact"]["sha256"])
        from research_harness import review_protocol
        self.assertNotIn(asset["artifact"], list(review_protocol._refs(passages)))
        self.assertEqual(self.find_covered_readings(self.build_packet(passages)), {"full", "html-visual-reading"})
