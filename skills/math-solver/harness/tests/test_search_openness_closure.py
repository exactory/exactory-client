"""Fresh root closure needs settled openness as well as an accepted proof."""

import copy
import unittest

from search_controller.errors import SearchError
from search_controller.model import initial_state
from search_controller.service import Controller
from tests import test_search_evidence as evidence_tests
from tests import test_search_proof as proof_tests
from tests.native_reviewer_support import attest_spec
from tests.search_fixtures import contract, digest
from tests.test_search_cli import SearchCLIWorkspace


def build_openness_review(subject, claim, outcome):
    origin = subject.get("origin", {})
    evidence = ([origin["study_digest"], origin["source_digest"]]
                if "study_digest" in origin else subject["evidence_digests"])
    value = {"schema_version": 2, "subject_digest": digest(subject), "claim_digest": digest(claim),
             "reviewer": proof_tests.provenance("independent-openness-reviewer"), "decision": "approve",
             "findings": {key: "Checked the exact original claim and pinned evidence."
                          for key in ("statement", "assumptions", "scope", "dependencies", "policy")}}
    value["findings"]["openness"] = {
        "outcome": outcome, "statement_digest": digest(claim), "evidence": list(dict.fromkeys(evidence)),
        "searched_at": "2026-10-16", "coverage": "Exact statement and each neighbour; recorded top 10 per query.",
        "reason": "Every decisive source in this software fixture was fully read; unavailable sources remain gaps."}
    return value


class OpennessClosureTests(SearchCLIWorkspace, unittest.TestCase):
    artifact = evidence_tests.EvidenceTests.artifact
    external_checkpoint = evidence_tests.EvidenceTests.external_checkpoint
    setup_external = evidence_tests.EvidenceTests.setup_external

    def set_up_accepted_proof(self, outcome="unresolved"):
        self.setup_external()
        cp = Controller(self.root).status()["checkpoints"]["checkpoint-000001"]
        self.search("accept", {"obligation_id": "obligation-000001", "outcome": "proof", "dependency_ids": [],
                              "route_bindings": [], "review": build_openness_review(cp, cp["claim"], outcome),
                              "inputs": []}, target=cp["id"], revision=2)

    def build_closure_spec(self, outcome="already_solved", version=2):
        state = Controller(self.root).status()
        cp = state["checkpoints"]["checkpoint-000001"]
        output = self.artifact("final-proof.md", "The exact requested proof and every requested case.")
        subject = {"schema_version": version, "objective_id": state["objective_id"],
                   "contract_digest": state["contract_digest"], "outcome": "proof",
                   "acceptance_ids": ["acceptance-000001"], "evidence_digests": cp["evidence_digests"],
                   "local_deliveries": [], "deliverables": [
                       {"requirement": requirement, "digest": output["digest"]}
                       for requirement in state["contract"]["required_deliverables"]]}
        return {"subject": subject, "review": build_openness_review(subject, cp["claim"], outcome), "inputs": [output]}

    def test_unresolved_openness_keeps_a_prove_objective_pending_after_proof_acceptance(self):
        self.set_up_accepted_proof()
        spec = self.build_closure_spec("unresolved")
        attest_spec(Controller(self.root), spec)
        result = self.search("complete", spec, revision=3, success=False)
        self.assertEqual(result["error"]["code"], "openness_unresolved")
        state = Controller(self.root).status()
        self.assertEqual(state["acceptances"]["acceptance-000001"]["status"], "accepted")
        self.assertEqual(state.get("openness", {}).get("status"), "pending")
        self.assertEqual(state["proof_status"], "open")
        self.assertIsNone(state["control"]["closure"])

    def test_fresh_closure_cannot_use_the_historical_subject_version(self):
        self.set_up_accepted_proof("already_solved")
        spec = self.build_closure_spec(version=1)
        attest_spec(Controller(self.root), spec)
        result = self.search("complete", spec, revision=3, success=False)
        self.assertEqual(result["error"]["code"], "openness_review_required")

    def test_already_solved_final_review_can_resolve_previously_unresolved_openness(self):
        self.set_up_accepted_proof()
        self.search("complete", self.build_closure_spec(), revision=3)
        state = Controller(self.root).status(full_audit=True)
        self.assertEqual(state["proof_status"], "proved")
        self.assertEqual(state.get("openness", {}).get("status"), "accepted")
        self.assertEqual(state.get("openness", {}).get("outcome"), "already_solved")
        self.assertEqual(state["contract"]["required_deliverables"], contract()["required_deliverables"])

    def test_new_settling_evidence_requires_a_new_observed_final_review(self):
        self.set_up_accepted_proof("already_solved")
        controller = Controller(self.root)
        spec = self.build_closure_spec()
        attest_spec(controller, spec)
        changed = self.external_checkpoint()
        changed["checkpoint"].update(kind="hypothesis", what_changed="New material settling evidence needs assessment")
        changed["inputs"][1] = self.artifact("source.md", "A newly supplied decisive settling source.")
        changed["checkpoint"]["origin"]["source_digest"] = changed["inputs"][1]["digest"]
        self.search("checkpoint", changed, revision=3)
        before = controller.store.read()
        result = self.search("complete", spec, revision=4, success=False)
        self.assertEqual(result["error"]["code"], "review_packet_mismatch")
        self.assertEqual(controller.store.read(), before)
        self.assertEqual(controller.status()["acceptances"]["acceptance-000001"]["status"], "accepted")
        refreshed = self.build_closure_spec()
        refreshed["review"]["findings"]["openness"]["evidence"].append(changed["inputs"][1]["digest"])
        self.search("complete", refreshed, revision=4)
        self.assertEqual(controller.status()["proof_status"], "proved")

    def test_new_settling_evidence_reopens_completed_openness_without_revoking_proof(self):
        self.set_up_accepted_proof("already_solved")
        spec = self.build_closure_spec()
        self.search("complete", spec, revision=3)
        controller = Controller(self.root)
        previous = copy.deepcopy(controller.status()["control"]["closure"])
        changed = self.external_checkpoint()
        changed["checkpoint"].update(kind="hypothesis", what_changed="New material settling evidence after closure")
        changed["inputs"][1] = self.artifact("source.md", "A newly supplied decisive settling source after closure.")
        changed["checkpoint"]["origin"]["source_digest"] = changed["inputs"][1]["digest"]
        self.search("checkpoint", changed, revision=4)
        state = controller.status(full_audit=True)
        self.assertEqual(state["acceptances"]["acceptance-000001"]["status"], "accepted")
        self.assertEqual(state["control"]["closure"], previous)
        self.assertEqual(state["openness"]["status"], "pending")
        self.assertEqual(state["proof_status"], "open")
        self.assertEqual(controller.command("next", {}, None, None)["kind"], "finalize_root")
        result = self.search("complete", spec, revision=5, success=False)
        self.assertEqual(result["error"]["code"], "review_packet_mismatch")
        refreshed = self.build_closure_spec()
        refreshed["review"]["findings"]["openness"]["evidence"].append(changed["inputs"][1]["digest"])
        self.search("complete", refreshed, revision=5)
        completed = controller.status(full_audit=True)
        self.assertEqual(completed["proof_status"], "proved")
        self.assertEqual(completed["openness"]["status"], "accepted")


class OpennessClosureReplayTests(unittest.TestCase):
    def test_unresolved_openness_also_prevents_closing_a_disprove_request(self):
        original = contract()
        original["requested_outcome"] = "decision"
        state = proof_tests.accepted(initial_state(original, "objective-000001"), "obligation-000001", outcome="counterexample")
        closure = proof_tests.closure_record(state)
        closure["subject"]["schema_version"] = 2
        closure["review"] = build_openness_review(closure["subject"], original["original_claim"], "unresolved")
        closure["audit"]["subject_digest"] = digest(closure["subject"])
        with self.assertRaises(SearchError) as caught:
            proof_tests.event(state, "objective_completed", {"closure": closure, "digest": digest(closure)})
        self.assertEqual(caught.exception.code, "openness_unresolved")

    def test_already_solved_keeps_formal_policy_and_all_deliverables_required(self):
        original = contract()
        original.update(proof_policy="lean-kernel", required_deliverables=["Formal verification", "Conventional proof"])
        original["original_claim"]["proof_policy"] = "lean-kernel"
        state = proof_tests.checkpointed(initial_state(original, "objective-000001"), "obligation-000001")
        cp = state["checkpoints"]["checkpoint-000001"]
        acceptance = proof_tests.acceptance_record(state, "obligation-000001")
        acceptance.update(schema_version=2, claims_openness=False,
                          review=build_openness_review(cp, cp["claim"], "already_solved"))
        with self.assertRaises(SearchError):
            proof_tests.event(state, "result_accepted", {"acceptance": acceptance, "digest": digest(acceptance)})
        acceptance["standard"] = "lean-kernel"
        state = proof_tests.event(state, "result_accepted", {"acceptance": acceptance, "digest": digest(acceptance)})
        closure = proof_tests.closure_record(state)
        closure["subject"]["schema_version"] = 2
        closure["review"] = build_openness_review(closure["subject"], original["original_claim"], "already_solved")
        closure["audit"]["subject_digest"] = digest(closure["subject"])
        incomplete = copy.deepcopy(closure)
        incomplete["subject"]["deliverables"].pop()
        incomplete["review"]["subject_digest"] = digest(incomplete["subject"])
        incomplete["audit"]["subject_digest"] = digest(incomplete["subject"])
        with self.assertRaises(SearchError):
            proof_tests.event(state, "objective_completed", {"closure": incomplete, "digest": digest(incomplete)})
        try:
            completed = proof_tests.event(state, "objective_completed", {"closure": closure, "digest": digest(closure)})
        except SearchError as error:
            self.fail("A fully verified already-solved objective must close: " + error.code)
        self.assertEqual(completed["proof_status"], "proved")
        self.assertEqual(len(completed["control"]["closure"]["subject"]["deliverables"]), 2)

    def test_recorded_version_one_closure_replays_unchanged(self):
        state = proof_tests.accepted(initial_state(contract(), "objective-000001"), "obligation-000001")
        closure = proof_tests.closure_record(state)
        completed = proof_tests.event(state, "objective_completed", {"closure": closure, "digest": digest(closure)})
        self.assertEqual(completed["control"]["closure"], closure)
        self.assertEqual(completed["proof_status"], "proved")
        checkpoint = copy.deepcopy(state["checkpoints"]["checkpoint-000001"])
        checkpoint.pop("id")
        checkpoint["what_changed"] = "Historical subsequent checkpoint retains its original replay semantics"
        replayed = proof_tests.event(completed, "checkpoint_recorded",
                                    {"checkpoint": checkpoint, "digest": digest(checkpoint)})
        self.assertEqual(replayed["proof_status"], "proved")
        self.assertEqual(replayed["control"]["closure"], closure)
