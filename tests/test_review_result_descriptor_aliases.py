"""Result descriptors sharing bytes keep their delivery and validation identities."""

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_harness import review_delivery, review_input_projection, review_protocol, review_transport
from research_harness import review_supplied_evidence
from research_harness import research_decisions
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from test_research_binary_results import PNG
from test_research_binary_pdf import authored_pdf


class ResultDescriptorAliasTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.artifacts = ArtifactStore(Path(temporary.name))

    def test_generic_and_typed_binary_descriptors_are_delivered_in_both_orders(self):
        for data, media in ((PNG, "image/png"), (authored_pdf(), "application/pdf")):
            typed = self.artifacts.put(data, media)
            generic = self.artifacts.put(data, "application/octet-stream")
            for references in ([typed, generic], [generic, typed]):
                for text_only in (False, True):
                    with self.subTest(media=media, references=references, text_only=text_only):
                        with review_transport.text_only_delivery(text_only):
                            entries = review_protocol._evidence(self.artifacts, references)
                        self.assertCountEqual([entry["artifact"] for entry in entries], references)
                        for entry in entries:
                            if text_only:
                                self.assertEqual(entry["encoding"], "omitted")
                                self.assertEqual(entry["content"], "")
                            else:
                                self.assertEqual(entry["encoding"], "base64")
                                self.assertEqual(base64.b64decode(entry["content"]), data)

    def test_duplicate_bytes_do_not_hide_a_corrupt_descriptor(self):
        valid = self.artifacts.put(PNG, "image/png")
        corrupt = dict(valid, size=1)
        for references in ([valid, corrupt], [corrupt, valid]):
            with self.subTest(references=references):
                with self.assertRaises(ResearchError) as raised:
                    review_protocol._evidence(self.artifacts, references)
                self.assertEqual(raised.exception.code, "artifact_corrupt")

    def test_generic_raster_signatures_reach_native_attachments(self):
        route = {"adapter": "openai_responses_v1", "model": "fixture-model",
                 "endpoint": "https://api.example.test/v1/responses",
                 "configuration": {"max_output_tokens": 1000, "timeout_seconds": 5}}
        for data, media in ((PNG, "image/png"), (b"\xff\xd8\xff\xe0fixture", "image/jpeg"),
                            (b"GIF89a\x01\x00\x01\x00fixture", "image/gif"),
                            (b"RIFF\x0c\x00\x00\x00WEBPVP8 fixture", "image/webp")):
            for generic in ("application/octet-stream", "binary/octet-stream"):
                with self.subTest(media=media, generic=generic):
                    reference = self.artifacts.put(data, generic)
                    entries = review_protocol._evidence(self.artifacts, reference)
                    request = review_transport.request_body(route, "Review.", {"evidence": entries})
                    parts = request["input"][-1]["content"]
                    self.assertEqual(parts[1]["image_url"], "data:" + media + ";base64,"
                                     + base64.b64encode(data).decode("ascii"))
                    attached = review_input_projection.collect_attachments(parts)
                    self.assertEqual(review_input_projection.find_attachment(attached, reference), entries[0]["content"])

    def test_file_delivery_preserves_all_descriptors_of_the_same_object(self):
        typed = self.artifacts.put(PNG, "image/png")
        generic = self.artifacts.put(PNG, "application/octet-stream")
        for references in ([typed, generic], [generic, typed]):
            with self.subTest(references=references):
                self.assertCountEqual(review_delivery.references(references), references)

    def test_native_attachment_lookup_keeps_distinct_media_of_identical_bytes(self):
        encoded = base64.b64encode(PNG).decode("ascii")
        typed = self.artifacts.put(PNG, "image/png")
        variant = self.artifacts.put(PNG, "image/PNG")
        parts = [{"type": "input_image", "image_url": "data:" + ref["media_type"] + ";base64," + encoded}
                 for ref in (typed, variant)]
        for ordered in (parts, list(reversed(parts))):
            with self.subTest(parts=ordered):
                attached = review_input_projection.collect_attachments(ordered)
                self.assertEqual(review_input_projection.find_attachment(attached, typed), encoded)
                self.assertEqual(review_input_projection.find_attachment(attached, variant), encoded)

    def test_supplied_correction_retains_each_delivered_descriptor(self):
        typed = self.artifacts.put(PNG, "image/png")
        generic = self.artifacts.put(PNG, "application/octet-stream")
        for references in ([typed, generic], [generic, typed]):
            packet = {"evidence": [{"artifact": ref, "encoding": "base64",
                                    "content": base64.b64encode(PNG).decode("ascii")} for ref in references]}
            with self.subTest(references=references), patch.object(
                    review_supplied_evidence, "lineage_packets", return_value=[packet]):
                supplied, mappings = review_supplied_evidence.supplied_context({}, self.artifacts, ["original"])
                self.assertCountEqual(list(supplied.values()), references)
                review_supplied_evidence.validate_correction({}, self.artifacts, references, supplied, mappings)

    def test_visual_supplied_context_retains_each_exact_descriptor(self):
        typed = self.artifacts.put(PNG, "image/png")
        generic = self.artifacts.put(PNG, "application/octet-stream")
        for references in ([typed, generic], [generic, typed]):
            packet = {"evidence": [{"artifact": ref, "encoding": "base64",
                                    "content": base64.b64encode(PNG).decode("ascii")} for ref in references]}
            with self.subTest(references=references):
                supplied, _ = research_decisions._visual_supplied_context({}, self.artifacts, [packet])
                self.assertCountEqual(list(supplied.values()), references)
                self.assertIn(typed["sha256"], supplied)

    def test_omitted_binary_bytes_cannot_authorize_a_correction(self):
        reference = self.artifacts.put(PNG, "image/png")
        packet = {"evidence": [{"artifact": reference, "encoding": "omitted", "content": "",
                                "delivery": "bytes-omitted-on-text-route"}]}
        with patch.object(review_supplied_evidence, "lineage_packets", return_value=[packet]):
            supplied, mappings = review_supplied_evidence.supplied_context({}, self.artifacts, ["original"])
        with self.assertRaises(ResearchError) as raised:
            review_supplied_evidence.validate_correction({}, self.artifacts, [reference], supplied, mappings)
        self.assertEqual(raised.exception.code, "invalid_review_correction")

    def test_objection_version_carries_the_descriptor_actually_supplied_for_cited_bytes(self):
        typed = self.artifacts.put(PNG, "image/png")
        generic = self.artifacts.put(PNG, "application/octet-stream")
        records = {"value_review": {"review": {"assignment_id": "original", "payload": {"objections": []}}}}
        for cited, delivered in ((typed, generic), (generic, typed)):
            supplied = {digest(delivered): delivered}
            version = {"review_id": "review", "evidence": [cited]}
            with self.subTest(cited=cited), patch.object(
                    review_supplied_evidence, "supplied_context", return_value=(supplied, [])), patch.object(
                    review_protocol, "collect_carried_records", return_value=[]):
                delivery = review_protocol.describe_version_evidence(records, self.artifacts, version)
                self.assertEqual(delivery["version_evidence"], [delivered])

    def test_opaque_result_aliases_reach_review_without_becoming_image_attachments(self):
        data = b"\x93NUMPY\x01\x00\xff\x00finite-result"
        typed = self.artifacts.put(data, "application/x-npy")
        generic = self.artifacts.put(data, "application/octet-stream")
        result = {"kind": "result", "execution_id": "run", "output_id": "array",
                  "artifact": typed, "locator": {"kind": "artifact"}}
        route = {"adapter": "openai_responses_v1", "model": "fixture-model",
                 "endpoint": "https://api.example.test/v1/responses",
                 "configuration": {"max_output_tokens": 1000, "timeout_seconds": 5}}
        for value in ([result, generic], [generic, result]):
            for transitive in (False, True):
                evidence = [generic, self.artifacts.put(json.dumps(value).encode(), "application/json")] if transitive else value
                for text_only in (False, True):
                    with self.subTest(value=value, transitive=transitive, text_only=text_only):
                        with review_transport.text_only_delivery(text_only):
                            entries = review_protocol._evidence(self.artifacts, evidence, transitive=transitive)
                        request = review_transport.request_body(route, "Review.", {"evidence": entries})
                        content = request["input"][-1]["content"]
                        self.assertIsInstance(content, str)
                        actual = json.loads(content[len(review_transport.PACKET_LABEL):])
                        review_input_projection.project_evidence(actual, {})
                        binary_entries = [entry for entry in actual["evidence"]
                                          if entry["artifact"]["sha256"] == typed["sha256"]]
                        self.assertEqual(len(binary_entries), 2)
                        for entry in binary_entries:
                            if text_only:
                                self.assertEqual(entry["encoding"], "omitted")
                                self.assertNotIn("content", entry)
                            else:
                                self.assertEqual(entry["encoding"], "base64")
                                self.assertEqual(base64.b64decode(entry["content"]), data)

    def test_forged_inline_result_bytes_do_not_receive_delivery_credit(self):
        reference = self.artifacts.put(b"\x93NUMPY\xff", "application/x-npy")
        for content in ("not base64!", base64.b64encode(b"different bytes").decode("ascii")):
            packet = {"evidence": [{"artifact": reference, "encoding": "base64", "content": content,
                                    "delivery": "inline-result-bytes"}]}
            review_input_projection.project_evidence(packet, {})
            self.assertNotIn("content", packet["evidence"][0])

    def test_inline_marker_cannot_replace_a_native_image_attachment(self):
        reference = self.artifacts.put(PNG, "image/png")
        packet = {"evidence": [{"artifact": reference, "encoding": "base64",
                                "content": base64.b64encode(PNG).decode("ascii"),
                                "delivery": "inline-result-bytes"}]}
        review_input_projection.project_evidence(packet, {})
        self.assertNotIn("content", packet["evidence"][0])

    def test_descriptor_only_focused_evidence_keeps_recorded_opaque_result_bytes(self):
        data = b"\x93NUMPY\xff\x00finite-result"
        typed = self.artifacts.put(data, "application/x-npy")
        generic = self.artifacts.put(data, "application/octet-stream")
        records = {"execution": {"run": {"payload": {"outputs": [{"id": "array", "artifact": typed}]}}}}
        for references in ([typed, generic], [generic, typed]):
            for text_only in (False, True):
                with self.subTest(references=references, text_only=text_only):
                    with review_transport.text_only_delivery(text_only):
                        entries = review_protocol._evidence(self.artifacts, {"original_evidence": references}, records=records)
                    self.assertCountEqual([entry["artifact"] for entry in entries], references)
                    for entry in entries:
                        self.assertEqual(entry["encoding"], "omitted" if text_only else "base64")
                        if not text_only:
                            self.assertEqual(base64.b64decode(entry["content"]), data)

    def test_result_shaped_json_data_does_not_become_a_native_artifact_reference(self):
        for artifact in (None, "A table caption", {"label": "A plot annotation"}):
            data = json.dumps({"kind": "result", "execution_id": "row", "output_id": "cell",
                               "artifact": artifact, "locator": {}}).encode()
            reference = self.artifacts.put(data, "application/json")
            with self.subTest(artifact=artifact):
                entries = review_protocol._evidence(self.artifacts, reference, transitive=True)
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0]["artifact"], reference)
                self.assertEqual(entries[0]["content"].encode(), data)


if __name__ == "__main__":
    unittest.main()
