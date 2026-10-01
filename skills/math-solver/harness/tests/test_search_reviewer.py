"""Native independence is tied to an observed exact-evidence assignment."""

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from search_controller.errors import SearchError
from search_controller.service import Controller
from tests.search_fixtures import contract, review
from tests.test_search_cli import SearchCLIWorkspace
from tests.native_reviewer_support import mutate, observe_review


class NativeReviewerTests(SearchCLIWorkspace, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.controller = Controller(self.root)
        self.controller.command("init", {"contract": contract()}, 0, "initialize")
        self.proposed = self.prepared_proposal()
        self.controller.command("propose", self.proposed, 1, "propose")

    def test_declared_host_identity_cannot_supply_new_independent_approval(self):
        before = self.controller.store.read()
        with self.assertRaises(SearchError) as caught:
            self.controller.command("review", {"proposal_id": "proposal-000001",
                "review": review(self.proposed["proposal"]), "inputs": []}, 2, "unobserved-review")
        self.assertEqual(caught.exception.code, "review_assignment_required")
        self.assertEqual(self.controller.store.read(), before)

    def approved(self, **options):
        return observe_review(self.controller, review(self.proposed["proposal"]),
                              self.proposed["proposal"], **options)

    def submit(self, value):
        return self.controller.command("review", {"proposal_id": "proposal-000001",
            "review": value, "inputs": []}, 2, "observed-review")

    def test_observed_exact_review_uses_unchanged_schema_without_strategic_gates(self):
        value = self.approved()
        self.submit(value)
        self.controller.command("admit", {}, 3, "admit", "proposal-000001")
        state = self.controller.status()
        self.assertEqual(state["reviews"]["review-000001"]["record"], value)
        from research_harness.storage import Store
        records = Store(self.root).snapshot()["records"]
        self.assertNotIn("research_intent", records)
        self.assertNotIn("strategy_dossier", records)
        self.assertNotIn("value_review", records)

    def test_changed_scientific_output_cannot_reuse_an_observed_attestation(self):
        value = self.approved()
        value["findings"]["scope"] = "An author-written replacement finding."
        with self.assertRaises(SearchError) as caught:
            self.submit(value)
        self.assertEqual(caught.exception.code, "review_output_mismatch")

    def test_native_author_cannot_use_an_identity_variant_in_observed_review(self):
        value = review(self.proposed["proposal"])
        value["reviewer"]["actor_id"] = " AUTHOR "
        value = observe_review(self.controller, value, self.proposed["proposal"])
        before = self.controller.store.read()
        with self.assertRaises(SearchError) as caught:
            self.submit(value)
        self.assertEqual(caught.exception.code, "review_not_independent")
        self.assertEqual(self.controller.store.read(), before)

    def test_rejected_observed_result_cannot_be_changed_into_native_approval(self):
        value = self.approved(output={"decision": "reject", "reason": "The exact evidence does not prove this claim.",
                                      "objections": ["A required case is missing."]})
        with self.assertRaises(SearchError) as caught:
            self.submit(value)
        self.assertEqual(caught.exception.code, "review_output_mismatch")

    def verifier_packet(self, scientific_bytes):
        from search_controller.schema import digest
        source = self.root / "checker-input"
        source.write_bytes(scientific_bytes)
        shell = Path("/bin/sh").resolve()
        shell_digest = hashlib.sha256(shell.read_bytes()).hexdigest()
        subject = {"node_id": "node-000001", "task": self.proposed["proposal"]["task"],
            "spec_without_input_review": {"kind": "certificate", "step_dir": "check",
                "artifacts": [{"path": source.name, "digest": hashlib.sha256(scientific_bytes).hexdigest(), "role": "input"}],
                "external_dependencies": [{"path": str(shell), "digest": shell_digest}]}}
        value = self.controller.command("review-packet", {"kind": "result", "subject": subject,
            "claim_digest": digest(self.proposed["proposal"]["claim"]),
            "reviewer": {"source": "host", "actor_id": "input-checker", "attestation_id": "review-assignment:input-check"},
            "inputs": []}, None, None)
        from research_harness.artifacts import ArtifactStore
        packet = json.loads(ArtifactStore(Path(value["workspace"])).read(value["native_packet"]))
        return packet, shell_digest

    def test_native_interpreter_has_explicit_provenance_and_source_is_readable(self):
        source = b"A readable exact finite certificate.\n"
        packet, shell_digest = self.verifier_packet(source)
        self.assertEqual(packet["execution_provenance"], [{"path": str(Path("/bin/sh").resolve()),
            "digest": shell_digest, "size": Path("/bin/sh").stat().st_size, "role": "native_verifier_executable"}])
        self.assertNotIn(shell_digest, packet["unmaterialized_digests"])
        self.assertIn(hashlib.sha256(source).hexdigest(), {entry["digest"] for entry in packet["evidence"]})

    def test_binary_scientific_input_is_not_reclassified_as_executable_provenance(self):
        with self.assertRaises(SearchError) as caught:
            self.verifier_packet(b"\x00\xff\x01Binary scientific input without a supported representation")
        self.assertEqual(caught.exception.code, "review_evidence_unreadable")

    def test_packet_cannot_omit_a_pinned_native_study(self):
        missing = self.proposed["proposal"]["studies"]["problem"]
        value = self.approved(alter_packet=lambda packet: packet.update(
            evidence=[entry for entry in packet["evidence"] if entry["digest"] != missing]))
        with self.assertRaises(SearchError) as caught:
            self.submit(value)
        self.assertEqual(caught.exception.code, "review_packet_mismatch")

    def test_later_contamination_blocks_admission_without_rewriting_history(self):
        from research_harness import review_protocol
        from research_harness.artifacts import ArtifactStore
        from research_harness.storage import Store
        value = self.approved()
        self.submit(value)
        before = self.controller.store.read()
        store = Store(self.root)
        mutate(store, review_protocol.record_context_event, {"id": "native-exposure",
            "assignment_id": value["reviewer"]["attestation_id"].split(":", 1)[1],
            "kind": "contamination", "source": "prior_assessments", "reason": "An earlier score was exposed.",
            "evidence": [ArtifactStore(store.root).put(b"Synthetic retained exposure report.", "text/plain")]})
        with self.assertRaises(SearchError) as caught:
            self.controller.command("admit", {}, 3, "admit", "proposal-000001")
        self.assertEqual(caught.exception.code, "review_context_unverified")
        self.assertEqual(self.controller.store.read(), before)
        self.assertEqual(self.controller.status()["reviews"]["review-000001"]["record"], value)

    def test_current_admission_contamination_blocks_begin(self):
        from research_harness import review_protocol
        from research_harness.artifacts import ArtifactStore
        from research_harness.storage import Store
        from tests.search_execution_support import begin_spec
        value = self.approved()
        self.submit(value)
        self.controller.command("admit", {}, 3, "admit", "proposal-000001")
        store = Store(self.root)
        mutate(store, review_protocol.record_context_event, {"id": "admission-exposure",
            "assignment_id": value["reviewer"]["attestation_id"].split(":", 1)[1],
            "kind": "contamination", "source": "author_history", "reason": "The claimed independent input was contaminated.",
            "evidence": [ArtifactStore(store.root).put(b"Retained admission context observation.", "text/plain")]})
        with self.assertRaises(SearchError) as caught:
            self.controller.command("begin", begin_spec(), 4, "begin", "node-000001")
        self.assertEqual(caught.exception.code, "review_context_unverified")

    def test_shared_review_snapshot_remains_locked_through_native_commit(self):
        from research_harness.artifacts import ArtifactStore
        from research_harness.storage import Store
        from tests.research_support import PLUGIN
        value = self.approved()
        store = Store(self.root)
        revision = store.revision
        event = {"id": "commit-exposure", "assignment_id": value["reviewer"]["attestation_id"].split(":", 1)[1],
            "kind": "contamination", "source": "author_history", "reason": "Concurrent reviewer exposure was recorded.",
            "evidence": [ArtifactStore(store.root).put(b"Retained concurrent context event.", "text/plain")]}
        program = """
import json, sys
from pathlib import Path
from research_harness.errors import ResearchError
from research_harness.review_protocol import record_context_event
from research_harness.storage import Store
try:
    receipt = record_context_event(Store(Path(sys.argv[1])), json.loads(sys.argv[2]),
        expected_revision=int(sys.argv[3]), request_id='native-commit-exposure')
    result = {'receipt': receipt}
except ResearchError as error:
    result = {'error': error.code}
print(json.dumps(result))
"""
        def writer():
            environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            environment.pop("PYTHONPATH", None)
            result = subprocess.run([sys.executable, "-B", "-c", program, str(self.root),
                json.dumps(event), str(revision)], cwd=PLUGIN, env=environment,
                text=True, capture_output=True, timeout=15)
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            return json.loads(result.stdout)
        original = self.controller.store._atomic_replace
        observations = []
        def committing(path, raw):
            if path == self.controller.store.tree_path:
                observations.append(writer())
            return original(path, raw)
        with patch.object(self.controller.store, "_atomic_replace", side_effect=committing):
            self.submit(value)
        self.assertEqual(observations, [{"error": "store_busy"}])
        self.assertIn("receipt", writer())
        with self.assertRaises(SearchError) as caught:
            self.controller.command("admit", {}, 3, "admit-after-exposure", "proposal-000001")
        self.assertEqual(caught.exception.code, "review_context_unverified")

    def test_historical_review_replay_keeps_original_attestation(self):
        from search_controller.model import apply_event, replay
        from tests.search_fixtures import digest
        value = review(self.proposed["proposal"])
        self.controller.store.put_blob(value)
        self.controller.store.append("review_recorded", {"proposal_id": "proposal-000001",
            "review": value, "digest": digest(value)}, 2, "historical-review",
            validate=lambda document, event: apply_event(replay(document), event))
        self.assertEqual(self.controller.status()["reviews"]["review-000001"]["record"], value)

    def test_historical_unobserved_review_cannot_authorize_new_admission(self):
        from search_controller.model import apply_event, replay
        from tests.search_fixtures import digest
        value = review(self.proposed["proposal"])
        self.controller.store.put_blob(value)
        self.controller.store.append("review_recorded", {"proposal_id": "proposal-000001",
            "review": value, "digest": digest(value)}, 2, "historical-review",
            validate=lambda document, event: apply_event(replay(document), event))
        with self.assertRaises(SearchError) as caught:
            self.controller.command("admit", {}, 3, "new-admission", "proposal-000001")
        self.assertEqual(caught.exception.code, "review_assignment_required")

    def test_current_acceptance_audit_detects_later_result_review_contamination(self):
        from research_harness import review_protocol
        from research_harness.artifacts import ArtifactStore
        from research_harness.storage import Store
        from search_controller.evidence import audit_state
        from tests.test_search_evidence import EvidenceTests
        self.artifact = lambda path, text: EvidenceTests.artifact(self, path, text)
        spec = EvidenceTests.external_checkpoint(self)
        self.controller.command("checkpoint", spec, 2, "checkpoint")
        state = self.controller.status()
        checkpoint = state["checkpoints"]["checkpoint-000001"]
        value = EvidenceTests.result_review(self, checkpoint, checkpoint["claim"])
        value = observe_review(self.controller, value, checkpoint)
        self.controller.command("accept", {"obligation_id": "obligation-000001", "outcome": "proof",
            "dependency_ids": [], "route_bindings": [], "review": value, "inputs": []},
            3, "accept", "checkpoint-000001")
        before = self.controller.store.read()
        store = Store(self.root)
        mutate(store, review_protocol.record_context_event, {"id": "result-exposure",
            "assignment_id": value["reviewer"]["attestation_id"].split(":", 1)[1],
            "kind": "contamination", "source": "author_history", "reason": "Author conversation was exposed.",
            "evidence": [ArtifactStore(store.root).put(b"Synthetic result exposure receipt.", "text/plain")]})
        failures = audit_state(self.root, self.controller.status(), self.controller.store)
        self.assertIn("acceptance-000001", failures)
        self.assertEqual(failures["acceptance-000001"]["code"], "review_context_unverified")
        self.assertEqual(self.controller.store.read(), before)


if __name__ == "__main__":
    unittest.main()
