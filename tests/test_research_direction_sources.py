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
        references = [item["excerpt"] for item in passages] + [asset["artifact"] for item in passages for asset in item.get("assets", [])]
        return {"direction_evidence": {"passages": passages}, "evidence": [
            {"artifact": reference,
             "content": (base64.b64encode(self.artifacts.read(reference)).decode()
                         if reference["media_type"] == "application/pdf" or reference["media_type"].startswith("image/") else self.artifacts.read(reference).decode()),
             "encoding": "base64" if reference["media_type"] == "application/pdf" or reference["media_type"].startswith("image/") else "utf-8"}
            for reference in references]}

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

    def prepare_long_reading(self):
        version = self.metadata(2)
        capture = self.capture(version, body="  DECISIVE START. " + "bounded evidence " * 1000 + " DECISIVE END. References: none.  ")
        bundle = self.bundle(version, capture=capture, bundle_id="large")
        for unit in bundle["units"]:
            link = unit["link"]
            link["locator"] = self.span(link["artifact"], link["locator"]["quote"], kind="span")
        self.mutate(literature.import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle, note_id="large-reading"))
        return version, capture, bundle

    def test_long_inspected_range_is_delivered_in_consecutive_bounded_chunks(self):
        version, capture, bundle = self.prepare_long_reading()
        passages = self.build_passages()
        chunks = [item for item in passages if item["version_id"] == version and item["inspection_index"] == 0]
        self.assertGreater(len(chunks), 1)
        source = self.artifacts.read(capture["text"]).decode()
        position = bundle["units"][0]["link"]["locator"]["start"]
        delivered = []
        for chunk in chunks:
            location = chunk["origin"]["locator"]
            self.assertEqual(location["start"], position)
            data = self.artifacts.read(chunk["excerpt"]).decode()
            self.assertLessEqual(len(data), 2000)
            self.assertEqual(data, source[location["start"]:location["end"]])
            delivered.append(data)
            position = location["end"]
        self.assertEqual(position, bundle["units"][0]["link"]["locator"]["end"])
        self.assertEqual("".join(delivered), source)
        self.assertEqual(self.find_covered_readings(self.build_packet(passages)), {"full", "large-reading"})

    def test_a_prefix_or_a_missing_middle_chunk_does_not_cover_the_inspection(self):
        version, _, _ = self.prepare_long_reading()
        passages = self.build_passages()
        body = [item for item in passages if item["version_id"] == version and item["inspection_index"] == 0]
        for keep in (body[:1], body[:1] + body[2:]):
            with self.subTest(chunks=len(keep)):
                supplied = [item for item in passages if item not in body] + keep
                self.assertEqual(self.find_covered_readings(self.build_packet(supplied)), {"full"})

    def test_chunk_coverage_is_the_union_across_bound_packets(self):
        from research_harness.direction_sources import find_delivered_readings
        _, _, _ = self.prepare_long_reading()
        passages = self.build_passages()
        packets = [self.build_packet(passages[::2]), self.build_packet(passages[1::2])]
        records = self.store.snapshot()["records"]
        self.assertEqual(find_delivered_readings(records, self.artifacts, self.find_readings(), packets), {"full", "large-reading"})

    def test_historical_chunks_with_different_boundaries_cover_by_union(self):
        from research_harness.bar_sources import prior_context
        from research_harness.direction_sources import find_delivered_readings
        version, capture, bundle = self.prepare_long_reading()
        records = self.store.snapshot()["records"]
        source = self.artifacts.read(capture["text"]).decode()
        from research_harness.source_links import span_locator
        link = bundle["units"][0]["link"]
        links = [dict(link, locator=span_locator(source, start, min(len(source), start + 997)))
                 for start in range(0, len(source), 997)]
        legacy = prior_context(records, self.artifacts, links)
        current = [item for item in self.build_passages() if item["version_id"] != version]
        packets = [self.build_packet(current), self.build_packet(legacy)]
        packets[1]["prior_context"] = packets[1].pop("direction_evidence")["passages"]
        self.assertEqual(find_delivered_readings(records, self.artifacts, self.find_readings(), packets), {"full", "large-reading"})

    def test_exact_source_delivery_fills_missing_range_chunks(self):
        from research_harness.direction_sources import find_delivered_readings
        version, capture, _ = self.prepare_long_reading()
        passages = self.build_passages()
        partial = [item for item in passages if item["version_id"] != version or item["inspection_index"] != 0 or item["chunk_index"] == 0]
        packet = self.build_packet(partial)
        self.assertEqual(self.find_covered_readings(packet), {"full"})
        reference = capture["text"]
        supplement = {"source_deliveries": [{"request": {"kind": "source", "version_id": version, "depth": "fulltext"},
                    "status": "delivered", "evidence": [reference]}], "evidence": [
                    {"artifact": reference, "content": self.artifacts.read(reference).decode(), "encoding": "utf-8"}]}
        self.assertEqual(find_delivered_readings(self.store.snapshot()["records"], self.artifacts,
            self.find_readings(), [packet, supplement]), {"full", "large-reading"})

    def test_chunks_preserve_whitespace_and_unicode_through_the_end_of_the_range(self):
        version = self.metadata(11)
        content = "  " * 2500 + "終端の決定的証拠 " * 600 + " References: none.\f"
        capture = self.capture(version, pdf_text=content)
        bundle = self.bundle(version, capture=capture, bundle_id="whitespace")
        self.mutate(literature.import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle, note_id="whitespace-reading"))
        passages = self.build_passages()
        chunks = [item for item in passages if item["version_id"] == version and item["inspection_index"] == 0]
        self.assertTrue(any(not self.artifacts.read(item["excerpt"]).strip() for item in chunks))
        self.assertTrue(all(len(self.artifacts.read(item["excerpt"]).decode()) <= 2000 for item in chunks))
        self.assertEqual(b"".join(self.artifacts.read(item["excerpt"]) for item in chunks), content.encode())
        self.assertEqual(self.find_covered_readings(self.build_packet(passages)), {"full", "whitespace-reading"})

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
        structured = [item for item in passages if item["origin"]["locator"]["kind"] == "json"]
        self.assertTrue(all(item["origin"]["locator"]["pointer"] == "/value" for item in structured))
        self.assertTrue(all(item["excerpt"]["size"] <= 2000 for item in structured))
        self.assertEqual(b"".join(self.artifacts.read(item["excerpt"]) for item in structured), ("checked " * 1000).encode())
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

    def test_large_html_anchor_delivers_all_content_in_bounded_chunks_with_compact_provenance(self):
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
        markup = [item for item in passages if item["origin"]["locator"]["kind"] == "html"]
        self.assertGreater(len(markup), 1)
        self.assertTrue(all(len(self.artifacts.read(item["excerpt"]).decode()) <= 2000 for item in markup))
        self.assertTrue(all(item["origin"]["locator"]["anchor"]["kind"] == "span" for item in markup))
        self.assertTrue(all("quote" not in item["origin"]["locator"]["anchor"] for item in markup))
        self.assertEqual(b"".join(self.artifacts.read(item["excerpt"]) for item in markup), self.artifacts.read(capture["original"]))
        self.assertEqual(self.find_covered_readings(self.build_packet(passages)), {"full", "large-html-reading"})

    def prepare_visual_reading(self, *, acquire=True):
        from research_fixtures import client
        from research_harness.visual_assets import acquire_visual_asset
        from test_research_source_contracts import SourceContractTests
        version = self.metadata(9)
        capture = self.capture(version, body='<figure><img src="/assets/required.png"><figcaption>CHECKED FIGURE.</figcaption></figure> References: none.')
        link = {"version_id": version, "source_id": capture["source_id"], "artifact": capture["original"],
                "locator": {"kind": "html", "anchor": self.span(capture["original"], '<figcaption>CHECKED FIGURE.</figcaption>', kind="span")}}
        asset = None
        if acquire:
            http, _, _ = client([(200, {"Content-Type": "image/png"}, SourceContractTests.png(self))])
            result = acquire_visual_asset(self.store, link, "https://arxiv.org/assets/required.png", http=http,
                expected_revision=self.store.revision, request_id="direction-visual")
            asset = result["asset_link"]
            link["locator"]["assets"] = [asset]
        bundle = self.bundle(version, capture=capture, bundle_id="html-visual")
        bundle["units"].append({"id": "figure", "kind": "figure", "required": True, "link": link})
        self.mutate(literature.import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle, note_id="html-visual-reading"))
        return version, asset

    def test_an_omitted_named_image_leaves_the_reading_uncovered(self):
        _, asset = self.prepare_visual_reading()
        packet = self.build_packet(self.build_passages())
        packet["evidence"] = [item for item in packet["evidence"] if item["artifact"] != asset["artifact"]]
        self.assertEqual(self.find_covered_readings(packet), {"full"})

    def test_a_named_image_is_attached_with_checked_bytes_and_covers_the_reading(self):
        _, asset = self.prepare_visual_reading()
        passages = self.build_passages()
        from research_harness import review_protocol
        evidence = review_protocol._evidence(self.artifacts, {"direction_evidence": {"passages": passages}})
        attached = next((item for item in evidence if item["artifact"] == asset["artifact"]), None)
        self.assertIsNotNone(attached)
        self.assertEqual(base64.b64decode(attached["content"]), self.artifacts.read(asset["artifact"]))
        packet = {"direction_evidence": {"passages": passages}, "evidence": evidence}
        self.assertEqual(self.find_covered_readings(packet), {"full", "html-visual-reading"})
        packet["evidence"] = [item for item in packet["evidence"] if item["artifact"] != asset["artifact"]]
        self.assertEqual(self.find_covered_readings(packet), {"full"})
        # An observed source continuation can supply the image independently.
        supplement = {"evidence": [attached]}
        from research_harness.direction_sources import find_delivered_readings
        self.assertEqual(find_delivered_readings(self.store.snapshot()["records"], self.artifacts,
            self.find_readings(), [packet, supplement]), {"full", "html-visual-reading"})

    def test_an_unacquired_image_remains_a_coverage_gap(self):
        packet = self.build_packet(self.build_passages())
        self.prepare_visual_reading(acquire=False)
        self.assertEqual(self.find_covered_readings(packet), {"full"})

    def test_named_images_obey_the_existing_artifact_byte_bound(self):
        from unittest.mock import patch
        from research_harness.errors import ResearchError
        from research_harness import direction_sources
        _, asset = self.prepare_visual_reading()
        with patch.object(direction_sources, "_MAX_ORIGINAL_BYTES", asset["artifact"]["size"] - 1):
            with self.assertRaises(ResearchError) as caught:
                self.build_passages()
        self.assertEqual(caught.exception.code, "review_evidence_too_large")
