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

    def observe_claude_review(self, value, context=None):
        from claude_review_fixtures import build_recorded_response, build_recorded_runtime
        from research_harness import review_claude_transport as claude, review_protocol, review_transport
        from research_harness.storage import Store
        from test_research_claude_transport import UI_INVALIDATE, clean_stream, completed
        identifier = 'claude-' + value['reviewer']['actor_id']
        value = copy.deepcopy(value)
        value['reviewer'].update(source='host', attestation_id='review-assignment:' + identifier)
        exported = self.controller.command('review-packet', {'kind': 'proposal',
            'subject': self.proposed['proposal'], 'claim_digest': value['claim_digest'],
            'reviewer': value['reviewer'], 'inputs': []}, None, None)
        store = Store(exported['workspace'])
        pinned_runtime = claude.runtime() if context is None else build_recorded_runtime(context[1])

        def respond(route, request, credential):
            is_probe = request['instructions'].startswith('Return JSON with allowed_control')
            output = value
            if is_probe:
                packet = json.loads(request['input'][0]['content'][len(review_transport.PACKET_LABEL):])
                output = {'allowed_control': packet['allowed_control'], 'evidence_control': packet['evidence_control'],
                          'excluded_controls': []}
            if context is not None:
                probe_context = (context[0], context[1], True, [], None)
                return build_recorded_response(route, request, output, probe_context if is_probe else context)
            events = clean_stream(request, json.dumps(output), model=route['model'])
            events.insert(2, dict(UI_INVALIDATE))
            with patch.object(claude, '_cli', return_value=('/fixture/claude', '2.1.310 (Claude Code)')), \
                    patch.object(claude, '_run_owned', return_value=completed(events)):
                return claude.send_request(route, request)

        with patch.object(claude, 'runtime', return_value=pinned_runtime), \
                patch.object(review_transport, 'send_request', side_effect=respond):
            mutate(store, review_protocol.record_route, {'id': identifier, 'adapter': 'claude_cli_v1',
                'model': 'claude-opus-5-5', 'endpoint': 'claude://local',
                'configuration': {'effort': 'max', 'max_output_tokens': 20000, 'timeout_seconds': 1800}})
            mutate(store, review_protocol.probe_route, {'id': identifier + '-probe', 'route_id': identifier})
            mutate(store, review_protocol.record_assignment, {'id': identifier, 'route_id': identifier,
                'dossier_id': None, 'role': 'native_math', 'reviewer_id': value['reviewer']['actor_id'],
                'author_id': 'author', 'context': {'native_packet': exported['native_packet']}})
            mutate(store, review_protocol.invoke_assignment,
                   {'id': identifier + '-attempt', 'assignment_id': identifier})
        return value

    def test_standalone_admits_with_recorded_old_and_current_claude_reviews(self):
        from claude_review_fixtures import RECORDED_CONTEXTS
        from research_harness import review_protocol
        from research_harness.artifacts import ArtifactStore
        from research_harness.storage import Store
        from search_controller.reviewer import node_reviews
        from tests.search_fixtures import proposal
        original_root = self.root
        for context in RECORDED_CONTEXTS:
            with self.subTest(context=context[0]):
                self.root = original_root / context[0]
                self.root.mkdir()
                self.controller = Controller(self.root)
                self.controller.command('init', {'contract': contract()}, 0, 'initialize')
                self.proposed = self.prepared_proposal()
                candidate = proposal('standalone')
                candidate.update(schema_version=3, computation=None, foundation=None,
                                 studies=self.proposed['proposal']['studies'])
                candidate['contribution']['standalone']['primary_sources'] = [candidate['studies']['problem']]
                self.proposed['proposal'] = candidate
                self.controller.command('propose', self.proposed, 1, 'propose')
                for index, old_context in enumerate((context, None)):
                    value = self.observe_claude_review(
                        review(candidate, reviewer='reviewer-' + str(index)), old_context)
                    self.controller.command('review', {'proposal_id': 'proposal-000001', 'review': value, 'inputs': []},
                                            2 + index, 'bind-' + str(index))
                self.controller.command('admit', {}, 4, 'mixed-admission', 'proposal-000001')
                state = self.controller.status()
                node = state['nodes']['node-000001']
                self.assertEqual(node['status'], 'admitted')
                self.assertEqual(len(node['admission']['review_ids']), 2)
                node_reviews(self.root, state, self.controller.store, node)
                store = Store(self.root)
                records = store.snapshot()['records']
                attempt = copy.deepcopy(records['review_attempt']['claude-reviewer-0-attempt'])
                assignment = records['review_assignment'][attempt['assignment_id']]
                attempt['runtime']['implementation_sha256'] = '0' * 64
                self.assertFalse(review_protocol.authenticate_attempt(records, ArtifactStore(self.root),
                                                                      assignment, attempt))

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
        # A proposal review needs the native Review shape for every decision, so the run already refused this output
        # (check native.output) and the observed review that the submission names is not verified.
        with self.assertRaises(SearchError) as caught:
            self.submit(value)
        self.assertEqual(caught.exception.code, "review_context_unverified")

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
