"""Replay base-frozen accounts and forming-import history without rewriting them."""

import base64
import copy
import json
from pathlib import Path

from research_harness import problem_claims, problem_delivery, problem_evidence, problem_forming_import, review_protocol
from research_harness.evidence import digest
from reading_date_fixtures import freeze_recording_clocks, record_reading_import
from test_research_problem_claims import ProblemCase


FIXTURES = Path(__file__).parent / "fixtures"


def encode_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class ReadingDateCompatibilityTests(ProblemCase):
    def test_p8_stored_gain_and_observed_result_review_keep_their_bytes(self):
        fixture = json.loads((FIXTURES / "reading_date_stored_gain.json").read_text())
        for path, content in fixture["artifacts"].items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(content))
        records = fixture["records"]
        before = copy.deepcopy(records)
        value = records["problem_value"]["value-1"]
        problem_evidence.validate_value(records, self.artifacts, value)
        self.assertEqual(encode_bytes(value), fixture["value_bytes"].encode())
        delivered = problem_evidence.describe_evidence(records, self.artifacts)
        self.assertEqual(encode_bytes(delivered["value"]["accounts"]), fixture["accounts_bytes"].encode())
        self.assertEqual(encode_bytes(delivered), fixture["evidence_bytes"].encode())
        self.assertEqual(encode_bytes(problem_evidence.describe_binding(records, self.artifacts)), fixture["binding_bytes"].encode())
        self.assertEqual(problem_claims.compute_problem_evidence_digest(records, self.artifacts), fixture["evidence_digest"])
        assignment = records["review_assignment"]["result-first"]
        attempt = records["review_attempt"]["attempt-result-first"]
        packet = json.loads(self.artifacts.read(assignment["packet"]))
        self.assertEqual(packet.pop("digest"), fixture["packet_digest"])
        self.assertEqual(digest(packet), fixture["packet_digest"])
        self.assertEqual(digest(assignment["prompt"]["text"]), fixture["prompt_digest"])
        self.assertEqual(digest(review_protocol.build_assignment_request(records, self.artifacts, assignment)), fixture["request_digest"])
        self.assertEqual(attempt["request_digest"], fixture["request_digest"])
        self.assertTrue(review_protocol.assignment_state(records, self.artifacts, assignment["id"])["ready"])
        self.assertEqual(problem_delivery.assess_minimum(records, self.artifacts), fixture["minimum"])
        self.assertEqual(records, before)

    def test_p9_stored_import_reproduces_all_members_sources_and_cuts(self):
        fixture = json.loads((FIXTURES / "reading_date_forming_import.json").read_text())
        with freeze_recording_clocks():
            source, destination, imported = record_reading_import(self)
        self.assertEqual(imported["members"], fixture["members"])
        self.assertEqual(imported["sources"], [dict(row, workspace=str(source.root.resolve())) for row in fixture["sources"]])
        records = destination.store.snapshot()["records"]
        self.assertEqual(problem_forming_import.verify_imports(records, destination.artifacts), 1)
        problem_forming_import.require_fresh_sources(records, destination.artifacts)
        rows = [row for member in imported["members"] if member["record_kind"] in ("reading", "form_outcome")
                for row in member["availability"]]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows, [dict(origin=imported["source_origin"], revision=None, date=None)] * 2)
