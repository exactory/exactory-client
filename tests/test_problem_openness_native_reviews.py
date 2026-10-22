"""Observed proof reviews reach openness with exact bytes and current bindings."""

import copy
import json
from contextlib import contextmanager
from unittest.mock import patch

from research_harness import problem_openness, review_protocol, review_transport
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.problem_attempt import record_attempt
from test_research_problem_attempt import build_attempt, build_native_attempt, put_native_attempt
from test_research_problem_claims import ProblemCase
import test_research_problem_openness_minimum as minimum_fixtures
import test_research_problem_reviews as review_fixtures


class NativeOpennessReviewTests(ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.selected = self.select_statement()
        self.search = minimum_fixtures.ProblemOpennessMinimumTests.search(self)
        self.native = build_native_attempt(self.selected["statement"], [], {})
        claim = dict(self.native["contract"]["original_claim"], statement="A weaker finite bound holds.")
        self.proof = self.artifacts.put(b"Synthetic proof: the stated finite inequality follows by counting.\n", "text/plain")
        self.manifest = {"schema_version": 1, "kind": "analytical", "claim_digest": digest(claim),
            "conclusion": {"outcome": "proof", "dependency_ids": [], "route_bindings": []},
            "artifacts": [{"path": "attempt/proof.md", "digest": self.proof["sha256"], "role": "proof"}],
            "dependencies": [], "external_dependencies": [], "verification": None}
        self.manifest_ref = self.pin_json(self.manifest)
        self.checkpoint = {"schema_version": 1, "id": "checkpoint-1", "kind": "proof", "claim": claim,
            "origin": {"kind": "journal_move", "node_id": "node-1", "move": 1, "journal_prefix_digest": "a" * 64},
            "evidence_digests": [self.manifest_ref["sha256"]], "verification_status": "pending",
            "what_changed": "A supporting proof is available.", "remaining_obligation_ids": ["obligation-000001"],
            "next_hypothesis": "Investigate the stronger bound.", "milestone_id": "supporting-proof"}
        self.native["checkpoints"] = [dict({key: self.checkpoint[key] for key in
            ("id", "kind", "claim", "verification_status")}, origin_kind="journal_move")]
        self.native_digest = put_native_attempt(self, self.native)
        self.attempt = self.mutate(record_attempt, build_attempt(native_attempt_digest=self.native_digest,
            statement_id=self.selected["id"]))["result"]
        review_fixtures.ProblemReviewTests.prepare_review_route(self)
        self.support = self.observe_support()

    def build_provider_response(self, output):
        return review_fixtures.ProblemReviewTests.build_provider_response(self, output)

    def pin_json(self, value):
        return self.artifacts.put(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                           separators=(",", ":")).encode(), "application/json")

    def evidence(self, reference, kind="artifact"):
        return {"digest": reference["sha256"], "kind": kind, "artifact": reference}

    def output(self, packet, outcome="unresolved"):
        openness = {"outcome": outcome, "statement_digest": packet["claim_digest"],
            "evidence": [row["digest"] for row in packet["evidence"]], "searched_at": "2026-10-21",
            "coverage": "The exact supplied search and proof evidence was inspected.",
            "reason": "The stronger claim remains a separate question."}
        if outcome != "unresolved":
            openness["minimum"] = packet["scientific_context"]["openness_minimum"]
        return {"schema_version": 2, "subject_digest": packet["subject_digest"],
            "claim_digest": packet["claim_digest"], "reviewer": packet["reviewer"], "decision": "approve",
            "findings": dict({key: "Assessed the exact synthetic subject." for key in
                              ("statement", "assumptions", "scope", "dependencies", "policy")}, openness=openness)}

    def observe_packet(self, packet, identifier, change_output=None, invoke=True):
        reference = self.pin_json(packet)
        assignment = self.mutate(review_protocol.record_assignment, {"id": identifier, "route_id": "route",
            "dossier_id": None, "role": "native_math", "reviewer_id": packet["reviewer"]["actor_id"],
            "author_id": "problem-author", "context": {"native_packet": reference}})["result"]
        output = self.output(packet, "supported_open" if packet["objective_id"] == self.selected["id"] else "unresolved")
        if change_output:
            change_output(output)
        if invoke:
            with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
                self.mutate(review_protocol.invoke_assignment, {"id": "observed-" + identifier,
                    "assignment_id": identifier}, credential="test-only")
        return {"assignment": assignment, "packet": packet, "output": output}

    def observe_support(self, identifier="proof-review", change_packet=None, change_output=None, invoke=True):
        packet = {"protocol": "native-math-review-v1", "kind": "result", "root": self.native["attack_root"],
            "objective_id": self.native["objective_id"], "contract": copy.deepcopy(self.native["contract"]),
            "subject": copy.deepcopy(self.checkpoint), "subject_digest": digest(self.checkpoint),
            "claim_digest": digest(self.checkpoint["claim"]), "reviewer": {"source": "host",
                "actor_id": "proof-reviewer-" + identifier, "attestation_id": "review-assignment:" + identifier},
            "scientific_context": {"scope": "exact_subject_and_dependencies"},
            "evidence": [self.evidence(self.proof), self.evidence(self.manifest_ref, "blob")],
            "execution_provenance": [], "unmaterialized_digests": []}
        if change_packet:
            change_packet(packet)
        return self.observe_packet(packet, identifier, change_output, invoke)

    def export(self, assignment="openness-review", support="proof-review", include=True):
        spec = {"assignment_id": assignment, "reviewer_id": "openness-assessor",
            "minimum": {"searches": [{"formulation": self.selected["statement"], "search_id": self.search["id"]}],
                        "settling_work_id": None, "duplicate_resolutions": []}}
        if include:
            spec["native_reviews"] = [{"attempt_id": self.attempt["id"], "assignment_id": support}]
        self.sequence += 1
        try:
            exported = problem_openness.export_openness(self.store, spec, self.root / ("packet-" + str(self.sequence)))
        except ResearchError as error:
            if error.code == "invalid_input" and "native_reviews" in spec and "optional" not in error.message:
                self.fail("The canonical openness exporter has no observed-native-review input: " + error.message)
            raise
        return json.loads(self.artifacts.read(exported["native_packet"]))

    def record_openness(self, packet=None, identifier="openness-review"):
        packet = self.export(assignment=identifier) if packet is None else packet
        self.observe_packet(packet, identifier)
        return self.mutate(self.load_problem_module().record_openness, {"assignment_id": identifier})["result"]

    def assessment(self):
        return problem_openness.assess_openness(self.store.snapshot()["records"], self.artifacts)

    def corrupt_observation(self, identifier):
        record = self.store.snapshot()["records"]["review_attempt"]["observed-" + identifier]
        self.put("review_attempt", record["id"], dict(record, origin="imported"))

    @contextmanager
    def omitted_request(self, target, *, altered=False):
        original = review_transport.request_body

        def build(route, prompt, packet, history=()):
            request = original(route, prompt, packet, history)
            if packet.get("native", {}).get("reviewer", {}).get("attestation_id") != "review-assignment:" + target:
                return request
            message = request["input"][-1]
            supplied = json.loads(message["content"].removeprefix(review_transport.PACKET_LABEL))
            entry = next(row for row in supplied["evidence"] if row["artifact"]["sha256"] == self.proof["sha256"])
            entry["content"] = "Altered proof bytes" if altered else ""
            if not altered:
                entry["encoding"] = "omitted"
                entry["delivery"] = "body-elided-route-limit"
            message["content"] = review_transport.PACKET_LABEL + json.dumps(supplied, sort_keys=True, ensure_ascii=False)
            return request

        with patch.object(review_transport, "request_body", side_effect=build):
            yield

    def test_export_delivers_actual_review_and_proof_without_native_acceptance_credit(self):
        packet = self.export()
        [support] = packet["scientific_context"]["native_reviews"]
        self.assertEqual(support["status"], "observed_approving_review")
        self.assertEqual(support["checkpoint"], self.checkpoint)
        self.assertEqual(support["review"], self.support["output"])
        self.assertEqual(self.native["acceptances"], [])
        self.assertNotIn("acceptance_id", support)
        delivered = {row["digest"] for row in packet["evidence"]}
        self.assertIn(self.proof["sha256"], delivered)
        self.assertIn(self.manifest_ref["sha256"], delivered)
        saved = self.record_openness(packet)
        self.assertEqual(saved["native_reviews"], [support])
        self.assertEqual(self.assessment(), [])

    def test_legacy_export_and_finding_remain_without_new_fields(self):
        packet = self.export(include=False)
        self.assertNotIn("native_reviews", packet["scientific_context"])
        saved = self.record_openness(packet)
        self.assertNotIn("native_reviews", saved)
        self.corrupt_observation("proof-review")
        self.assertEqual(self.assessment(), [])

    def test_unrelated_native_root_objective_contract_or_claim_is_refused(self):
        changes = {"root": lambda p: p.update(root="/another/attack"),
            "objective": lambda p: p.update(objective_id="objective-other"),
            "contract": lambda p: p["contract"].update(root_attack_slug="different"),
            "claim": lambda p: p.update(claim_digest="d" * 64)}
        for name, change in changes.items():
            with self.subTest(name=name):
                self.observe_support(name, change_packet=change)
                self.assert_error("openness_native_review_mismatch", lambda: self.export(support=name))

    def test_reviewed_checkpoint_must_match_current_native_projection(self):
        def change(packet):
            packet["subject"]["id"] = "missing-checkpoint"
            packet["subject_digest"] = digest(packet["subject"])
        self.observe_support("other-checkpoint", change_packet=change)
        self.assert_error("openness_native_review_mismatch", lambda: self.export(support="other-checkpoint"))

    def test_pending_imported_or_schema_invalid_approval_is_refused(self):
        self.observe_support("pending", invoke=False)
        self.assert_error("openness_native_review_unverified", lambda: self.export(support="pending"))
        self.corrupt_observation("proof-review")
        self.assert_error("openness_native_review_unverified", self.export)
        self.observe_support("bad-schema", change_output=lambda output: output["findings"]["openness"].update(coverage={"bad": "object"}))
        self.assert_error("openness_native_review_mismatch", lambda: self.export(support="bad-schema"))

    def test_missing_proof_from_observed_packet_cannot_be_supplied_as_status(self):
        self.observe_support("missing-proof", change_packet=lambda p: p.update(evidence=[self.evidence(self.manifest_ref, "blob")]))
        self.assert_error("openness_native_evidence_missing", lambda: self.export(support="missing-proof"))

    def test_support_request_elision_and_alteration_are_rejected_despite_ready_provenance(self):
        for altered in (False, True):
            identifier = "elided-proof-" + str(altered)
            with self.subTest(altered=altered), self.omitted_request(identifier, altered=altered):
                self.observe_support(identifier)
                current = review_protocol.assignment_state(self.store.snapshot()["records"], self.artifacts, identifier)
                self.assertTrue(current["ready"], current["obligations"])
                self.assert_error("openness_native_evidence_undelivered", lambda: self.export(support=identifier))

    def test_openness_request_must_deliver_proof_bytes_not_only_descriptors(self):
        packet = self.export()
        with self.omitted_request("openness-review"):
            self.assert_error("openness_native_evidence_undelivered", lambda: self.record_openness(packet))

    def test_caller_cannot_change_bound_review_or_checkpoint_in_openness_packet(self):
        packet = self.export()
        packet["scientific_context"]["native_reviews"][0]["review"]["findings"]["policy"] = "Author substituted policy."
        self.assert_error("openness_native_review_mismatch", lambda: self.record_openness(packet))

    def test_new_native_snapshot_refuses_export_and_stales_recorded_finding(self):
        self.record_openness()
        changed = copy.deepcopy(self.native)
        changed["totals"]["moves"] = 23
        put_native_attempt(self, changed)
        self.assert_error("problem_attempt_superseded", self.export)
        self.assertEqual(self.assessment()[0]["code"], "problem_openness_stale")

    def test_current_support_and_openness_observation_are_rechecked_at_gate(self):
        self.record_openness()
        for identifier in ("proof-review", "openness-review"):
            with self.subTest(identifier=identifier):
                original = self.store.snapshot()["records"]["review_attempt"]["observed-" + identifier]
                self.corrupt_observation(identifier)
                pending = self.assessment()
                self.assertEqual(pending[0]["code"], "problem_openness_stale")
                self.put("review_attempt", original["id"], original)

    def test_tampered_pinned_proof_stales_finding(self):
        self.record_openness()
        path = self.root / self.proof["path"]
        path.chmod(0o600)
        path.write_bytes(b"Different proof bytes")
        self.assertEqual(self.assessment()[0]["code"], "problem_openness_stale")

    def replace_manifest(self, manifest, *, extra_evidence=(), identifier="changed-proof"):
        self.manifest_ref = self.pin_json(manifest)
        self.checkpoint["evidence_digests"] = [self.manifest_ref["sha256"]]
        return self.observe_support(identifier, change_packet=lambda packet: packet["evidence"].extend(extra_evidence))

    def test_wrong_manifest_claim_and_missing_transitive_artifacts_are_refused(self):
        wrong = dict(self.manifest, claim_digest="d" * 64)
        self.replace_manifest(wrong, identifier="wrong-claim")
        self.assert_error("openness_native_review_mismatch", lambda: self.export(support="wrong-claim"))
        child = dict(self.manifest, artifacts=[{"path": "attempt/lemma.md", "digest": "e" * 64, "role": "proof"}])
        child_ref = self.pin_json(child)
        self.replace_manifest(dict(self.manifest, dependencies=[child_ref["sha256"]]),
                              extra_evidence=[self.evidence(child_ref, "blob")], identifier="missing-child-proof")
        self.assert_error("openness_native_evidence_missing", lambda: self.export(support="missing-child-proof"))

    def test_scientific_external_dependency_is_required_but_typed_executable_is_not(self):
        dependency = self.artifacts.put(b"Synthetic external lemma.\n", "text/plain")
        manifest = dict(self.manifest, external_dependencies=[{"path": "/synthetic/lemma.txt", "digest": dependency["sha256"]}])
        self.replace_manifest(manifest, identifier="missing-external")
        self.assert_error("openness_native_evidence_missing", lambda: self.export(support="missing-external"))
        self.observe_support("supplied-external", change_packet=lambda packet: packet["evidence"].append(self.evidence(dependency)))
        packet = self.export(support="supplied-external")
        self.assertIn(dependency["sha256"], packet["scientific_context"]["native_reviews"][0]["required_evidence_digests"])
        self.observe_support("typed-executable", change_packet=lambda packet: packet["execution_provenance"].append(
            {"path": "/synthetic/verifier", "digest": dependency["sha256"], "size": dependency["size"], "role": "native_verifier_executable"}))
        packet = self.export(support="typed-executable")
        self.assertNotIn(dependency["sha256"], packet["scientific_context"]["native_reviews"][0]["required_evidence_digests"])

    def test_current_attempt_identity_is_rechecked_even_for_same_native_snapshot(self):
        self.record_openness()
        self.mutate(record_attempt, build_attempt(id="new-attempt", native_attempt_digest=self.native_digest,
            statement_id=self.selected["id"]))
        self.assert_error("openness_native_review_mismatch", self.export)
        self.assertEqual(self.assessment()[0]["code"], "problem_openness_stale")

    def test_duplicate_or_null_native_support_is_refused(self):
        packet = self.export(assignment="duplicate")
        packet["scientific_context"]["native_reviews"] *= 2
        self.assert_error("openness_native_review_mismatch", lambda: self.record_openness(packet, "duplicate"))
        packet = self.export(assignment="null")
        packet["scientific_context"]["native_reviews"] = None
        self.assert_error("openness_native_review_mismatch", lambda: self.record_openness(packet, "null"))

    def test_binary_proof_requires_actual_exact_attachment(self):
        self.proof = self.artifacts.put(b"\x89PNG\x00\xffsynthetic-proof", "image/png")
        manifest = dict(self.manifest, artifacts=[{"path": "attempt/proof.png", "digest": self.proof["sha256"], "role": "proof"}])
        self.replace_manifest(manifest, identifier="binary-proof")
        packet = self.export(assignment="binary-openness", support="binary-proof")
        saved = self.record_openness(packet, "binary-openness")
        self.assertIn(self.proof["sha256"], saved["native_reviews"][0]["required_evidence_digests"])
        original = review_transport.request_body
        def omit_attachment(route, prompt, supplied, history=()):
            request = original(route, prompt, supplied, history)
            request["input"][-1]["content"] = [part for part in request["input"][-1]["content"]
                                              if part["type"] == "input_text"]
            return request
        with patch.object(review_transport, "request_body", side_effect=omit_attachment):
            self.observe_support("missing-attachment")
            self.assert_error("openness_native_evidence_undelivered", lambda: self.export(support="missing-attachment"))

    def test_accepted_dependency_cycle_is_refused(self):
        manifest = dict(self.manifest, conclusion=dict(self.manifest["conclusion"], dependency_ids=["acceptance-cycle"]))
        self.manifest_ref = self.pin_json(manifest)
        self.native["acceptances"] = [{"id": "acceptance-cycle", "checkpoint_id": self.checkpoint["id"],
            "obligation_id": None, "outcome": "proof", "classification": "analytical", "standard": "analytical",
            "status": "accepted", "claims_openness": False, "evidence_digests": [self.manifest_ref["sha256"]]}]
        self.native_digest = put_native_attempt(self, self.native)
        self.attempt = self.mutate(record_attempt, build_attempt(id="cycle-attempt", native_attempt_digest=self.native_digest,
            statement_id=self.selected["id"]))["result"]
        self.checkpoint["evidence_digests"] = [self.manifest_ref["sha256"]]
        self.observe_support("cycle")
        self.assert_error("openness_native_evidence_missing", lambda: self.export(support="cycle"))

    def test_computational_manifest_with_missing_typed_dependencies_is_refused(self):
        result = self.pin_json({"schema_version": 1, "input_digest": "a" * 64,
            "commands": [{"stdout_digest": "b" * 64, "stderr_digest": "c" * 64}],
            "theorem_type_digest": "d" * 64, "toolchain_digest": "e" * 64,
            "claim_digest": digest(self.checkpoint["claim"])})
        verification = {"run_id": "run-1", "result_digest": result["sha256"], "policy_review": None,
            "requested_declaration": None, "requested_type_digest": None}
        for kind in ("certificate", "lean", "analytical"):
            with self.subTest(kind=kind):
                identifier = "computation-" + kind
                self.replace_manifest(dict(self.manifest, kind=kind, verification=verification),
                                      extra_evidence=[self.evidence(result, "blob")], identifier=identifier)
                self.assert_error("openness_native_evidence_unsupported", lambda: self.export(support=identifier))
