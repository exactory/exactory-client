"""Exact captured screening evidence reaches reviews without full-reading credit."""

import hashlib
import json
import unittest
from unittest.mock import patch

import test_problem_openness_native_reviews as native_fixtures
import test_problem_openness_source_delivery as delivery_fixtures
import test_research_problem_openness_minimum as minimum_fixtures
from research_harness import problem_openness, review_transport
from research_harness.acquisition import import_response
from research_harness.errors import ResearchError
from research_harness.evidence import digest


class ScreeningEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.case = native_fixtures.NativeOpennessReviewTests()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        f = self.case
        self.body = "Publisher preview\nTheorem α: only bipartite graphs.\nUnrelated second hit.\n"
        result = import_response(f.store, "web", self.body.encode(),
            source_url="https://example.org/publisher-preview", captured_at="2026-10-21T12:00:00Z",
            media_type="text/plain", mappings=[], expected_revision=f.store.revision, request_id="screen-capture")
        self.source = f.store.snapshot()["records"]["source"][result["source_ids"][0]]
        self.binding = {"source_id": self.source["id"], "reason": "Inspect the captured theorem's graph class."}
        self.locator = {"kind": "span", "start": 18, "end": 51,
            "sha256": hashlib.sha256("Theorem α: only bipartite graphs.".encode()).hexdigest()}

    def export(self, identifier="screen-review", *, bindings=None, native=False):
        f = self.case
        spec = {"assignment_id": identifier, "reviewer_id": "screen-assessor",
            "minimum": {"searches": [{"formulation": f.selected["statement"], "search_id": f.search["id"]}],
                        "settling_work_id": None, "duplicate_resolutions": []},
            "source_evidence": [self.binding] if bindings is None else bindings}
        if native:
            spec["native_reviews"] = [{"attempt_id": f.attempt["id"], "assignment_id": "proof-review"}]
        f.sequence += 1
        try:
            result = problem_openness.export_openness(f.store, spec, f.root / ("screen-packet-" + str(f.sequence)))
        except ResearchError as error:
            if error.code == "invalid_input":
                self.fail("The exporter must support exact captured source evidence: " + error.message)
            raise
        return json.loads(f.artifacts.read(result["native_packet"]))

    def supplied(self, identifier):
        f = self.case
        record = f.store.snapshot()["records"]["review_attempt"]["observed-" + identifier]
        request = json.loads(f.artifacts.read(record["request"]))
        return json.loads(request["input"][-1]["content"].removeprefix(review_transport.PACKET_LABEL))

    def test_complete_capture_and_honest_provenance_reach_actual_review_without_reading_credit(self):
        f = self.case
        packet = self.export()
        [binding] = packet["scientific_context"]["source_evidence"]
        self.assertEqual(json.loads(f.artifacts.read(binding["source_record"])), self.source)
        self.assertEqual(binding["source_digest"], digest(self.source))
        self.assertFalse(json.loads(f.artifacts.read(binding["source_record"]))["origin_verified"])
        self.assertEqual(binding["scope"], "complete_captured_response")
        self.assertFalse(binding["reading_credit"])
        saved = f.record_openness(packet, "screen-review")
        self.assertEqual(saved["source_evidence"], [binding])
        supplied = self.supplied("screen-review")
        self.assertEqual(supplied["native"]["scientific_context"]["source_evidence"], [binding])
        entry = next(row for row in supplied["evidence"] if row["artifact"]["sha256"] == self.source["response"]["sha256"])
        self.assertEqual(entry["content"], self.body)
        self.assertEqual(packet["scientific_context"]["openness_minimum"]["reading_digests"], [])
        self.assertNotIn("reading", f.store.snapshot()["records"])
        self.assertEqual(f.assessment(), [])

    def test_exact_unicode_span_supplies_only_selected_original_text(self):
        f = self.case
        packet = self.export(bindings=[dict(self.binding, locator=self.locator)])
        [binding] = packet["scientific_context"]["source_evidence"]
        self.assertEqual(binding["scope"], "exact_captured_response_span")
        self.assertEqual(binding["locator"], self.locator)
        self.assertEqual(json.loads(f.artifacts.read(binding["source_record"]))["response"], self.source["response"])
        self.assertEqual(f.artifacts.read(binding["delivered_artifact"]), "Theorem α: only bipartite graphs.".encode())
        self.assertNotIn(self.source["response"]["sha256"], {row["digest"] for row in packet["evidence"]})
        f.record_openness(packet, "screen-review")
        self.assertNotIn("Unrelated second hit.", json.dumps(self.supplied("screen-review")))
        self.assertEqual(f.assessment(), [])

    def test_invalid_empty_duplicate_or_unknown_bindings_are_rejected(self):
        for bindings in (None, [], [self.binding, self.binding], [{"source_id": "absent", "reason": "Inspect."}],
                         [dict(self.binding, reason="")], [dict(self.binding, locator=None)]):
            with self.subTest(bindings=bindings), self.assertRaises(ResearchError):
                # Explicitly test malformed null through the public exporter.
                if bindings is None:
                    f = self.case
                    problem_openness.export_openness(f.store, {"assignment_id": "null", "reviewer_id": "reviewer",
                        "minimum": {"searches": [], "settling_work_id": None, "duplicate_resolutions": []},
                        "source_evidence": None}, f.root / "null")
                else:
                    self.export(bindings=bindings)

    def test_mismatched_and_out_of_range_locators_are_rejected(self):
        for locator in (dict(self.locator, end=50), dict(self.locator, sha256="0" * 64),
                        dict(self.locator, start=True), dict(self.locator, end=1000),
                        {"kind": "json", "pointer": "", "value": self.body}):
            with self.subTest(locator=locator), self.assertRaises(ResearchError):
                self.export(bindings=[dict(self.binding, locator=locator)])

    def test_changed_source_record_or_missing_original_stales_registered_finding(self):
        f = self.case
        packet = self.export(bindings=[dict(self.binding, locator=self.locator)])
        f.record_openness(packet, "screen-review")
        f.put("source", self.source["id"], dict(self.source, url="https://different.example/source"))
        self.assertEqual(f.assessment()[0]["code"], "problem_openness_stale")
        f.put("source", self.source["id"], self.source)
        (f.root / self.source["response"]["path"]).unlink()
        self.assertEqual(f.assessment()[0]["code"], "problem_openness_stale")

    def test_changed_source_record_before_registration_is_rejected(self):
        f = self.case
        packet = self.export()
        f.observe_packet(packet, "screen-review")
        f.put("source", self.source["id"], dict(self.source, captured_at="2026-10-20T12:00:00Z"))
        f.assert_error("openness_screening_evidence_mismatch",
            lambda: f.mutate(f.load_problem_module().record_openness, {"assignment_id": "screen-review"}))

    def test_tampered_binding_and_missing_or_changed_actual_bytes_are_rejected(self):
        f = self.case
        original = review_transport.request_body
        for mode in ("binding", "missing", "bytes", "provenance"):
            identifier = "screen-" + mode
            packet = self.export(identifier, bindings=[dict(self.binding, locator=self.locator)])
            def changed(route, prompt, outer, history=()):
                request = original(route, prompt, outer, history)
                supplied = json.loads(request["input"][-1]["content"].removeprefix(review_transport.PACKET_LABEL))
                binding = supplied["native"]["scientific_context"]["source_evidence"][0]
                if mode == "binding":
                    binding["reading_credit"] = True
                else:
                    for row in supplied["evidence"]:
                        target = binding["source_record"] if mode == "provenance" else binding["delivered_artifact"]
                        if row["artifact"]["sha256"] == target["sha256"]:
                            row["content"] = "Changed theorem" if mode == "bytes" else ""
                request["input"][-1]["content"] = review_transport.PACKET_LABEL + json.dumps(supplied)
                return request
            with self.subTest(mode=mode), patch.object(review_transport, "request_body", side_effect=changed):
                with self.assertRaises(ResearchError):
                    f.record_openness(packet, identifier)

    def test_unobserved_review_stales_source_evidence_without_native_support(self):
        f = self.case
        packet = self.export()
        f.record_openness(packet, "screen-review")
        f.corrupt_observation("screen-review")
        self.assertEqual(f.assessment()[0]["code"], "problem_openness_stale")

    def test_route_fitting_preserves_screening_and_mandatory_proof_bytes(self):
        f = self.case
        packet = self.export(native=True)
        f.record_openness(packet, "screen-review")
        supplied = self.supplied("screen-review")
        supplied["native"]["scientific_context"]["text_delivery_priority"] = [self.source["response"]["sha256"], f.proof["sha256"]]
        request = review_transport._fit_local_input({"model": "test", "configuration": {"max_output_tokens": 100}},
            "Return JSON.", supplied, [], 1, lambda value: json.dumps(value))
        final = json.loads(request["input"][-1]["content"].removeprefix(review_transport.PACKET_LABEL))
        entries = {row["artifact"]["sha256"]: row for row in final["evidence"]}
        for ref in (self.source["response"], f.proof):
            self.assertEqual(entries[ref["sha256"]]["content"].encode(), f.artifacts.read(ref))

    def test_screening_original_cannot_be_omitted_as_an_alternate_fulltext_body(self):
        case = delivery_fixtures.SourceDeliveryTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        f = case.case
        spec = {"assignment_id": "screen-alternate", "reviewer_id": "screen-assessor",
            "minimum": {"searches": [{"formulation": f.selected["statement"], "search_id": f.search["id"]}],
                        "settling_work_id": None, "duplicate_resolutions": []},
            "native_reviews": [{"attempt_id": f.attempt["id"], "assignment_id": "contained-proof"}],
            "source_evidence": [{"source_id": case.html["source_id"], "reason": "Inspect this exact original representation."}]}
        result = problem_openness.export_openness(f.store, spec, f.root / "screen-alternate-export")
        packet = json.loads(f.artifacts.read(result["native_packet"]))
        self.assertNotIn(case.html["original"]["sha256"], packet["scientific_context"]["openness_delivery"]["alternate_body_digests"])
        f.record_openness(packet, "screen-alternate")
        self.assertEqual(f.assessment(), [])

    def test_screening_evidence_cannot_replace_required_full_reading(self):
        f = self.case
        work = f.metadata()
        f.search = minimum_fixtures.ProblemOpennessMinimumTests.search(f, [work])
        packet = self.export()
        f.assert_error("openness_reading_gap", lambda: f.record_openness(packet, "screen-review"))

    def test_legacy_packet_has_no_screening_contract(self):
        f = self.case
        packet = f.export(include=False)
        self.assertNotIn("source_evidence", packet["scientific_context"])
        saved = f.record_openness(packet)
        self.assertNotIn("source_evidence", saved)
        self.assertEqual(f.assessment(), [])
