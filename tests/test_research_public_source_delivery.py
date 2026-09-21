"""Exact source components and nested source provenance remain source-bound."""

import copy
import json
from pathlib import Path

from literature_fixtures import LiteratureCase
from research_fixtures import client
from research_harness.acquisition import acquire_fulltext
from research_harness.errors import ResearchError
from research_harness.review_delivery import _deliver
from research_harness.scientific_delivery import project_delivery


class PublicSourceDeliveryTests(LiteratureCase):
    def capture_pdf(self):
        identifier = self.metadata()
        jpeg = (Path(__file__).parent / "fixtures/research/authored-grid.jpg").read_bytes()
        prefix = b"%PDF-1.4\n% Authored PDF source fixture\nstream\n"
        data = prefix + jpeg + b"\nendstream\n%%EOF\n"
        http, _, _ = client([(200, {"Content-Type": "application/pdf"}, data)], max_retries=0)
        capture = acquire_fulltext(self.store, identifier, "https://arxiv.org/pdf/" + identifier[6:], http=http,
            extractor=lambda value: {"status": "extracted", "text": "Authored source figure. References: none.\f"},
            expected_revision=self.store.revision, request_id="source-pdf")["capture"]
        ref = self.artifacts.put(jpeg, "image/jpeg")
        projection = {"kind": "source_component", "context": "The unchanged source JPEG figure stream.",
                      "source_sha256": capture["original"]["sha256"], "start": len(prefix), "end": len(prefix) + len(jpeg)}
        return capture, ref, projection

    def project(self, ref, projection, *, records=None, manifest=None, destination="delivery"):
        records = self.store.snapshot()["records"] if records is None else records
        contract = {"payload": {"scientific_delivery": [{"original_sha256": ref["sha256"], "disposition": "project", "projection": projection}]}}
        result, derived = project_delivery(records, self.artifacts, contract, manifest or {"evidence": ref})
        target = self.root / destination
        _deliver(self.store, target, result, derived=derived, transitive=True)
        return result, target

    def test_exact_source_jpeg_component_preserves_bytes_and_provenance(self):
        capture, ref, projection = self.capture_pdf()
        manifest, target = self.project(ref, projection, manifest={"evidence": ref, "original_source": capture["original"]})
        derivative = json.loads((target / manifest["evidence"]["path"]).read_bytes())
        self.assertTrue(derivative["derivative"])
        self.assertEqual(derivative["source_sha256"], capture["original"]["sha256"])
        self.assertEqual(derivative["original_sha256"], ref["sha256"])
        self.assertEqual(derivative["byte_span"], {"start": projection["start"], "end": projection["end"]})
        self.assertEqual((target / derivative["artifact"]["path"]).read_bytes(), self.artifacts.read(ref))
        self.assertEqual((target / capture["original"]["path"]).read_bytes(), self.artifacts.read(capture["original"]))

    def test_source_component_rejects_wrong_span_source_role_and_media(self):
        capture, ref, projection = self.capture_pdf()
        records = self.store.snapshot()["records"]
        variants = [(ref, dict(projection, start=True), records), (ref, dict(projection, start=0), records),
                    (ref, dict(projection, end=10**9), records), (ref, dict(projection, source_sha256="0" * 64), records),
                    (dict(ref, media_type="application/octet-stream"), projection, records)]
        unverified = copy.deepcopy(records)
        unverified["source"][capture["source_id"]]["origin_verified"] = False
        variants.append((ref, projection, unverified))
        stale = copy.deepcopy(records)
        stale["work"][next(iter(stale["work"]))]["fulltexts"][0]["component"] = {"status": "pending"}
        variants.append((ref, projection, stale))
        for private in (ref, capture["original"]):
            internal = copy.deepcopy(records)
            internal["source_deferral"] = {"gap": {"authorization": private, "acquisition_evidence": []}}
            variants.append((ref, projection, internal))
        for index, (component, declaration, current) in enumerate(variants):
            with self.subTest(index=index), self.assertRaises(ResearchError):
                self.project(component, declaration, records=current)
            self.assertFalse((self.root / "delivery").exists())

    def test_required_public_source_descriptors_preserve_exact_values_and_closure(self):
        capture, _, _ = self.capture_pdf()
        value = {"original": capture["original"], "text": capture["text"]}
        ref = self.artifacts.put(json.dumps({"source_evidence": value}).encode(), "application/json")
        locator = {"kind": "json", "pointer": "/source_evidence", "value": value}
        projection = {"kind": "json_locators", "context": "The exact assessed public source provenance.", "locators": [locator]}
        manifest, target = self.project(ref, projection, manifest={"result": {"artifact": ref, "locator": locator}})
        self.assertEqual(manifest["result"]["locator"]["value"], value)
        derivative = json.loads((target / manifest["result"]["artifact"]["path"]).read_bytes())
        self.assertEqual(derivative["entries"][0]["value"], value)
        for original in value.values():
            self.assertEqual((target / original["path"]).read_bytes(), self.artifacts.read(original))
        self.assertFalse((target / ref["path"]).exists())

    def test_nested_source_descriptor_cannot_declassify_private_or_unknown_artifact(self):
        capture, _, _ = self.capture_pdf()
        records = self.store.snapshot()["records"]
        authored = self.artifacts.put(b"Private authored context", "text/plain")
        forged = dict(capture["original"], media_type="text/plain")
        internal = copy.deepcopy(records)
        internal["source_deferral"] = {"gap": {"authorization": capture["original"], "acquisition_evidence": []}}
        for nested, current in ((authored, records), (forged, records), (capture["original"], internal),
                                (json.dumps(capture["original"]), records)):
            ref = self.artifacts.put(json.dumps({"result": nested}).encode(), "application/json")
            projection = {"kind": "json_locators", "context": "An invalid mixed provenance value.",
                          "locators": [{"kind": "json", "pointer": "/result", "value": nested}]}
            with self.subTest(nested=nested), self.assertRaises(ResearchError):
                self.project(ref, projection, records=current)
            self.assertFalse((self.root / "delivery").exists())

    def test_json_in_public_source_text_keeps_validated_transitive_artifacts(self):
        capture, _, _ = self.capture_pdf()
        identifier = self.metadata(2)
        nested = self.capture(identifier, pdf_text=json.dumps({"original_pdf": capture["original"]}))
        value = {"source_text": nested["text"]}
        ref = self.artifacts.put(json.dumps({"result": value}).encode(), "application/json")
        locator = {"kind": "json", "pointer": "/result", "value": value}
        projection = {"kind": "json_locators", "context": "Public provenance includes its actual transitive source.", "locators": [locator]}
        manifest, target = self.project(ref, projection, manifest={"result": {"artifact": ref, "locator": locator}})
        self.assertEqual(manifest["result"]["locator"]["value"], value)
        self.assertEqual((target / nested["text"]["path"]).read_bytes(), self.artifacts.read(nested["text"]))
        self.assertEqual((target / capture["original"]["path"]).read_bytes(), self.artifacts.read(capture["original"]))

    def test_bom_source_text_keeps_exact_public_transitive_artifacts(self):
        capture, _, _ = self.capture_pdf()
        identifier = self.metadata(2)
        nested = self.capture(identifier, pdf_text="\ufeff" + json.dumps({"original_pdf": capture["original"]}))
        value = {"source_text": nested["text"]}
        ref = self.artifacts.put(json.dumps({"result": value}).encode(), "application/json")
        locator = {"kind": "json", "pointer": "/result", "value": value}
        projection = {"kind": "json_locators", "context": "The exact BOM-bearing public source provenance.", "locators": [locator]}
        _, target = self.project(ref, projection, manifest={"result": {"artifact": ref, "locator": locator}})
        self.assertEqual((target / capture["original"]["path"]).read_bytes(), self.artifacts.read(capture["original"]))

    def test_encoded_json_and_mime_parameters_share_checked_public_closure(self):
        capture, _, _ = self.capture_pdf()
        records = self.store.snapshot()["records"]
        contract = {"payload": {"scientific_delivery": []}}
        for index, (encoding, mime) in enumerate((("utf-8-sig", "text/plain"), ("utf-16", "application/json; charset=utf-16"),
                ("utf-16-be", "application/provenance+json"), ("utf-32", "application/octet-stream"))):
            with self.subTest(encoding=encoding):
                ref = self.artifacts.put(json.dumps({"source": capture["original"]}).encode(encoding), mime)
                manifest, derived = project_delivery(records, self.artifacts, contract, {"file": ref}, manuscript_files=[ref])
                target = self.root / ("encoded-public-" + str(index))
                _deliver(self.store, target, manifest, derived=derived, transitive=True)
                self.assertEqual((target / ref["path"]).read_bytes(), self.artifacts.read(ref))
                self.assertEqual((target / capture["original"]["path"]).read_bytes(), self.artifacts.read(capture["original"]))

    def test_encoded_json_cannot_hide_private_or_unknown_nested_descriptors(self):
        capture, _, _ = self.capture_pdf()
        unknown = self.artifacts.put(b"Private directive", "text/plain")
        records = self.store.snapshot()["records"]
        internal = copy.deepcopy(records)
        internal["source_deferral"] = {"gap": {"authorization": capture["original"], "acquisition_evidence": []}}
        for nested, current in ((unknown, records), (capture["original"], internal)):
            for encoding in ("utf-8-sig", "utf-16", "utf-32"):
                ref = self.artifacts.put(json.dumps({"source": nested}).encode(encoding), "application/json; charset=" + encoding)
                with self.subTest(encoding=encoding, nested=nested), self.assertRaises(ResearchError) as raised:
                    project_delivery(current, self.artifacts, {"payload": {"scientific_delivery": []}}, {"file": ref}, manuscript_files=[ref])
                self.assertEqual(raised.exception.code, "private_mixed_artifact_required")

    def test_decoded_structured_strings_check_private_scalar_values_and_keys(self):
        private = self.artifacts.put(b"PRIVATE-AUTHORIZATION-INSTRUCTION", "text/plain")
        records = {"source_deferral": {"gap": {"authorization": private, "acquisition_evidence": []}}}
        escaped = "".join("\\u%04x" % byte for byte in self.artifacts.read(private))
        values = ['{"instruction":"' + escaped + '"}', '{"' + escaped + '":true}']
        values.extend(["\ufeff" + values[0], json.dumps(values[0])])
        for value in values:
            ref = self.artifacts.put(json.dumps({"result": value}).encode(), "application/json")
            projection = {"kind": "json_locators", "context": "A copied log cannot re-encode private instructions.",
                          "locators": [{"kind": "json", "pointer": "/result", "value": value}]}
            with self.subTest(value=value), self.assertRaises(ResearchError) as raised:
                self.project(ref, projection, records=records)
            self.assertEqual(raised.exception.code, "private_mixed_artifact_required")
            self.assertFalse((self.root / "delivery").exists())

    def test_encoded_structure_depth_is_bounded_before_json_parser_recursion(self):
        for depth in (41, 1200):
            value = "[" * depth + '"observation"' + "]" * depth
            ref = self.artifacts.put(json.dumps({"result": value}).encode(), "application/json")
            projection = {"kind": "json_locators", "context": "Structured observations have an explicit depth bound.",
                "locators": [{"kind": "json", "pointer": "/result", "value": value}]}
            with self.subTest(depth=depth), self.assertRaises(ResearchError) as raised:
                self.project(ref, projection)
            self.assertEqual(raised.exception.code, "invalid_scientific_delivery")
            self.assertFalse((self.root / "delivery").exists())
