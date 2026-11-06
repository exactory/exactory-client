"""Every explicitly pinned strategy input reaches the native review packet."""

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

PLUGIN = Path(__file__).resolve().parents[4]
sys.path.append(str(PLUGIN / "tests"))

from codex_route_fixtures import limit_codex_input, record_codex_route
from research_harness import review_parts, review_protocol
from research_harness.artifacts import ArtifactStore
from research_harness.native_review import load_packet
from research_harness.storage import Store
from search_controller.errors import SearchError
from search_controller.reviewer import packet, validate_packet_context
from search_controller.schema import digest
from search_controller.service import Controller
from tests.native_reviewer_support import observe_review
from tests.search_fixtures import contract, review
from tests.strategy_refresh_support import assessment_spec
from tests.test_search_cli import SearchCLIWorkspace


class SupplementaryInputTests(SearchCLIWorkspace, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.controller = Controller(self.root)
        self.controller.command("init", {"contract": contract()}, 0, "initialize")
        description = self.controller.command("strategy-context", {}, None, None)
        self.subject = assessment_spec(description)["assessment"]
        self.reviewer = {"source": "host", "actor_id": "strategy-reviewer",
                         "attestation_id": "review-assignment:eleven-inputs"}
        self.inputs = []
        for number in range(11):
            path = self.root / ("strategy-input-" + str(number) + ".txt")
            path.write_text("Exact finite strategy input " + str(number) + "\n")
            self.inputs.append({"path": path.name, "kind": "artifact",
                                "digest": hashlib.sha256(path.read_bytes()).hexdigest()})

    def export(self, inputs=None):
        result = self.controller.command("review-packet", {"kind": "result", "subject": self.subject,
            "claim_digest": digest(self.controller.status()["contract"]["original_claim"]),
            "reviewer": self.reviewer, "inputs": self.inputs if inputs is None else inputs}, None, None)
        artifacts = ArtifactStore(result["workspace"])
        return load_packet(artifacts, result["native_packet"], self.reviewer["actor_id"]), artifacts

    def test_eleven_unreferenced_pinned_files_are_delivered_by_digest(self):
        before = self.controller.store.read()
        value, artifacts = self.export()
        delivered = {entry["digest"]: entry for entry in value["evidence"]}
        self.assertTrue({item["digest"] for item in self.inputs} <= delivered.keys())
        for item in self.inputs:
            self.assertEqual(artifacts.read(delivered[item["digest"]]["artifact"]),
                             (self.root / item["path"]).read_bytes())
        self.assertEqual(value["scientific_context"]["supplementary_inputs"]["schema_version"], 1)
        self.assertEqual(self.controller.store.read(), before)
        self.assertFalse(self.controller.status()["acceptances"])

    def submit_observed_inputs(self):
        proposed = self.prepared_proposal()
        self.controller.command("propose", proposed, 1, "propose")
        value = observe_review(self.controller, review(proposed["proposal"]),
                               proposed["proposal"], inputs=self.inputs)
        self.controller.command("review", {"proposal_id": "proposal-000001", "review": value,
                                           "inputs": []}, 2, "observed-review")
        self.assertFalse(self.controller.status()["acceptances"])

    def test_observed_packet_revalidates_frozen_inputs_without_reimporting_files(self):
        self.submit_observed_inputs()

    def test_normalized_blob_keeps_its_raw_identity_when_revalidated(self):
        path = self.root / "supplementary.json"
        path.write_text('{ "finite_values": [1, 2, 3] }\n')
        self.inputs = [{"kind": "blob", "path": path.name, "digest": digest({"finite_values": [1, 2, 3]})}]
        self.submit_observed_inputs()

    def test_corruption_refuses_and_unrelated_artifacts_are_excluded(self):
        unrelated = self.controller.store.put_artifact(b"An unrelated private local artifact")
        value, _ = self.export()
        self.assertNotIn(unrelated, {entry["digest"] for entry in value["evidence"]})
        (self.root / self.inputs[0]["path"]).write_bytes(b"Changed bytes")
        with self.assertRaises(SearchError) as caught:
            self.export()
        self.assertEqual(caught.exception.code, "digest_mismatch")

    def test_binding_and_inventory_changes_are_refused(self):
        expected, _ = self.export()
        for change in ("missing", "digest", "version", "duplicate"):
            altered = copy.deepcopy(expected)
            if change == "missing":
                altered["evidence"] = [entry for entry in altered["evidence"]
                                       if entry["digest"] != self.inputs[0]["digest"]]
            else:
                binding = altered["scientific_context"]["supplementary_inputs"]
                if change == "digest":
                    binding["inputs"][0]["digest"] = "0" * 64
                elif change == "version":
                    binding["schema_version"] = 99
                else:
                    binding["inputs"].append(binding["inputs"][0])
            with self.assertRaises(SearchError):
                validate_packet_context(altered, expected, self.controller.status())

    def test_old_packets_rebuild_unchanged_without_supplementary_bindings(self):
        value, artifacts = self.export([])
        self.assertNotIn("supplementary_inputs", value["scientific_context"])
        rebuilt = packet(self.root, self.controller.status(), self.controller.store, self.subject,
                         "result", value["claim_digest"], self.reviewer, artifacts,
                         common_records={})
        self.assertEqual(value, rebuilt)

    def test_oversized_inputs_remain_units_for_native_parts_planning(self):
        for item in self.inputs:
            path = self.root / item["path"]
            path.write_text(path.read_text() * 10000)
            item["digest"] = hashlib.sha256(path.read_bytes()).hexdigest()
        value, artifacts = self.export()
        reference = artifacts.put(json.dumps(value).encode(), "application/json")
        envelope = review_protocol.build_packet({}, artifacts, None, "native_math", self.reviewer["actor_id"],
                                                native_packet=reference)
        units = review_parts.enumerate_units(envelope, "native_math")
        serialized = json.dumps(units)
        for item in self.inputs:
            self.assertIn(item["digest"], serialized)
        self.assertGreater(sum(len(artifacts.read(entry["artifact"])) for entry in value["evidence"]), 1000000)

        self.store = Store(artifacts.root)
        self.artifacts = artifacts
        record_codex_route(self, output_tokens=20000)
        limit_codex_input(self, 400000)
        assignment = review_protocol.record_assignment(self.store, {
            "id": "eleven-inputs", "route_id": "route", "dossier_id": None, "role": "native_math",
            "reviewer_id": self.reviewer["actor_id"], "author_id": "author",
            "context": {"native_packet": reference}}, expected_revision=self.store.revision,
            request_id="plan-eleven-inputs")["result"]
        delivered = json.loads(artifacts.read(assignment["packet"]))
        self.assertIn("review_parts", delivered)
        inventory = review_parts.read_inventory(artifacts, delivered["review_parts"]["inventory"])
        citations = {citation for unit in inventory["units"] for citation in unit["citations"]}
        self.assertTrue({item["digest"] for item in self.inputs} <= citations)
