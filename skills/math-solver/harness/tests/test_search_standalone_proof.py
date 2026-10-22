"""Standalone proofs keep their own claim and never discharge the root."""

import copy
import hashlib
import json
import unittest

from search_controller.errors import SearchError
from search_controller.model import initial_state
from search_controller.proof import checkpoint_milestone, root_support
from tests.search_fixtures import contract, digest, proposal, provenance, review
from tests.support import WorkspaceTest, make_move
from tests.test_search_proof import acceptance_record, event


def standalone_proposal():
    value = proposal("standalone")
    value["claim"]["statement"] = "For every integer n from 5 to 15, n + n = 2*n"
    value["checkpoint_criteria"] = [{"kind": "accepted_standalone_claim",
        "criterion_id": "own-proof", "explanation": "Accept the exact standalone theorem"}]
    return value


def admit_standalone(value=None):
    value = standalone_proposal() if value is None else value
    state = initial_state(contract(), "objective-000001")
    state = event(state, "proposal_recorded", {"proposal": value, "digest": digest(value)})
    for actor in ["reviewer-one", "reviewer-two"]:
        checked = review(value, actor)
        state = event(state, "review_recorded", {"proposal_id": "proposal-000001",
            "review": checked, "digest": digest(checked)})
    return event(state, "proposal_admitted", {"proposal_id": "proposal-000001"})


def local_checkpoint(state, kind="proof"):
    node = state["nodes"]["node-000001"]
    return {"schema_version": 1, "kind": kind, "claim": copy.deepcopy(node["claim"]),
        "origin": {"kind": "journal_move", "node_id": node["id"], "move": 1,
                   "journal_prefix_digest": "f" * 64},
        "evidence_digests": ["a" * 64], "verification_status": "pending",
        "what_changed": "A proof of the standalone claim is available",
        "remaining_obligation_ids": ["obligation-000001"],
        "next_hypothesis": "Continue the original unresolved objective",
        "milestone_id": node["checkpoint_criteria"][0]["criterion_id"]}


def record_checkpoint(state, cp=None):
    cp = local_checkpoint(state) if cp is None else cp
    return event(state, "checkpoint_recorded", {"checkpoint": cp, "digest": digest(cp)})


class StandaloneProofTests(unittest.TestCase):
    def test_prior_milestones_cannot_record_a_distinct_standalone_proof(self):
        for criterion, message in [
            ({"kind": "hypothesis_recorded", "criterion_id": "hypothesis", "explanation": "Record only a hypothesis"},
             "Checkpoint kind differs"),
            ({"kind": "accepted_obligation", "criterion_id": "root-proof", "explanation": "Existing root obligation",
              "obligation_id": "obligation-000001"}, "Checkpoint claim differs")]:
            value = standalone_proposal()
            value["checkpoint_criteria"] = [criterion]
            state = admit_standalone(value)
            with self.subTest(kind=criterion["kind"]), self.assertRaisesRegex(SearchError, message):
                record_checkpoint(state)

    def test_own_claim_milestone_requires_an_accepted_proof(self):
        state = record_checkpoint(admit_standalone())
        self.assertFalse(checkpoint_milestone(state, "checkpoint-000001"))
        value = acceptance_record(state, None)
        state = event(state, "result_accepted", {"acceptance": value, "digest": digest(value)})
        self.assertTrue(checkpoint_milestone(state, "checkpoint-000001"))
        self.assertIsNone(root_support(state))
        self.assertEqual(state["proof_status"], "open")
        self.assertEqual(list(state["obligations"]), ["obligation-000001"])
        self.assertEqual(state["routes"], {})
        self.assertFalse(state["acceptances"]["acceptance-000001"]["progress_eligible"])

    def test_main_or_coverage_cannot_declare_a_standalone_milestone(self):
        for category in ["main", "coverage"]:
            value = standalone_proposal()
            value.update(category=category, relationship=category,
                         target_obligation="obligation-000001", claim=contract()["original_claim"])
            value["contribution"]["standalone"] = None
            if category == "coverage":
                from tests.search_fixtures import decomposition_proposal
                replacement = decomposition_proposal()
                replacement["checkpoint_criteria"] = value["checkpoint_criteria"]
                value = replacement
            with self.subTest(category=category), self.assertRaisesRegex(SearchError, "standalone"):
                admit_standalone(value)

    def test_checkpoint_rejects_each_changed_claim_component(self):
        state = admit_standalone()
        changes = {"statement": "A different statement", "quantifiers": "There exists an integer n",
                   "scope": {"kind": "case_ids", "case_ids": ["5"]},
                   "assumption_ids": ["obligation-000001"], "proof_policy": "lean-kernel"}
        for field, replacement in changes.items():
            cp = local_checkpoint(state)
            cp["claim"][field] = replacement
            with self.subTest(field=field), self.assertRaisesRegex(SearchError, "Checkpoint claim differs"):
                record_checkpoint(state, cp)

    def test_standalone_milestone_rejects_nonproof_checkpoints(self):
        state = admit_standalone()
        for kind in ["reduction", "obstruction", "hypothesis"]:
            with self.subTest(kind=kind), self.assertRaisesRegex(SearchError, "Checkpoint kind differs"):
                record_checkpoint(state, local_checkpoint(state, kind))

    def test_even_an_identical_root_claim_cannot_gain_root_credit(self):
        value = standalone_proposal()
        value["claim"] = copy.deepcopy(contract()["original_claim"])
        state = record_checkpoint(admit_standalone(value))
        accepted = acceptance_record(state, "obligation-000001")
        with self.assertRaisesRegex(SearchError, "null target"):
            event(state, "result_accepted", {"acceptance": accepted, "digest": digest(accepted)})

    def test_invalidated_acceptance_does_not_meet_the_milestone(self):
        state = record_checkpoint(admit_standalone())
        value = acceptance_record(state, None)
        state = event(state, "result_accepted", {"acceptance": value, "digest": digest(value)})
        state = event(state, "evidence_invalidated", {"acceptance_ids": ["acceptance-000001"],
            "reason": "Synthetic proof evidence was invalidated", "provenance": provenance("auditor")})
        self.assertFalse(checkpoint_milestone(state, "checkpoint-000001"))
        self.assertIsNone(root_support(state))

    def test_counterexample_does_not_satisfy_a_proof_milestone(self):
        state = record_checkpoint(admit_standalone())
        value = acceptance_record(state, None, outcome="counterexample")
        state = event(state, "result_accepted", {"acceptance": value, "digest": digest(value)})
        self.assertFalse(checkpoint_milestone(state, "checkpoint-000001"))
        self.assertIsNone(root_support(state))

    def test_policy_and_independent_result_review_remain_required(self):
        state = record_checkpoint(admit_standalone())
        value = acceptance_record(state, None)
        value["review"]["reviewer"] = provenance("author")
        with self.assertRaisesRegex(SearchError, "Producer cannot review"):
            event(state, "result_accepted", {"acceptance": value, "digest": digest(value)})
        candidate = standalone_proposal()
        candidate["claim"]["proof_policy"] = "lean-kernel"
        state = record_checkpoint(admit_standalone(candidate))
        value = acceptance_record(state, None)
        with self.assertRaisesRegex(SearchError, "proof policy"):
            event(state, "result_accepted", {"acceptance": value, "digest": digest(value)})


class StandaloneProofServiceTests(WorkspaceTest):
    def prepare_checkpoint(self):
        from tests.search_execution_support import admit_workspace, begin_spec, invoke
        own_statement = "For every integer n, n + n = 2*n"

        def configure(candidate):
            own_claim = copy.deepcopy(candidate["claim"])
            candidate["claim"]["statement"] = "For every integer n, n*n >= 0"
            candidate.update(category="standalone", relationship="standalone",
                             target_obligation=None, claim=own_claim)
            candidate["contribution"]["standalone"] = proposal("standalone")["contribution"]["standalone"]
            candidate["contribution"]["standalone"]["primary_sources"] = [candidate["studies"]["problem"]]
            candidate["checkpoint_criteria"] = standalone_proposal()["checkpoint_criteria"]

        self.controller = admit_workspace(self.attack_root, self.slug,
            exact_claim=own_statement, configure=configure)
        state = self.controller.status()
        allocation = {"max_moves": 1, "message_id": "fixture-side-allocation",
            "instruction": "Allocate one move to the already reviewed standalone fixture proof.",
            "provenance": provenance("fixture-operator")}
        path = self.attack_root / "side-allocation.json"
        path.write_text(json.dumps(allocation))
        result = self.run_cli("search", "side-interval", "node-000001", "--spec", str(path),
            "--expected-revision", str(state["revision"]), "--request-id", "fixture-side-allocation")
        self.assertEqual(result[0], 0, result)
        from tests.strategy_refresh_support import reassess_fixture
        from tests.support import OPENING
        self.assertEqual(self.controller.command("next", {}, None, None)["kind"], "reassess_strategies")
        reassess_fixture(self.controller, {("node-000001", OPENING)})
        invoke(self.controller, "begin", begin_spec())
        result = self.run_cli("journal", "add", self.slug, "--json", json.dumps(make_move(1)))
        self.assertEqual(result[0], 0, result)
        proof = "For every integer n, distributivity gives 2*n = (1+1)*n = n+n.\n"
        (self.workspace / "proof.md").write_text(proof)
        proof_digest = hashlib.sha256(proof.encode()).hexdigest()
        node = self.controller.status()["nodes"]["node-000001"]
        manifest = {"schema_version": 1, "kind": "analytical", "claim_digest": digest(node["claim"]),
            "conclusion": {"outcome": "proof", "dependency_ids": [], "route_bindings": []},
            "artifacts": [{"path": self.slug + "/proof.md", "digest": proof_digest, "role": "proof"}],
            "dependencies": [], "external_dependencies": [], "verification": None}
        (self.workspace / "manifest.json").write_text(json.dumps(manifest))
        state = self.controller.status()
        cp = local_checkpoint(state)
        del cp["verification_status"]
        cp["origin"]["journal_prefix_digest"] = state["service"]["journal_receipts"]["node-000001:1"]["journal_prefix_digest"]
        cp["evidence_digests"] = [digest(manifest)]
        return {"checkpoint": cp, "inputs": [
            {"path": self.slug + "/proof.md", "digest": proof_digest, "kind": "artifact"},
            {"path": self.slug + "/manifest.json", "digest": digest(manifest), "kind": "blob"}]}

    def acceptance(self):
        from tests.test_search_evidence import EvidenceTests
        cp = self.controller.status()["checkpoints"]["checkpoint-000001"]
        return {"obligation_id": None, "outcome": "proof", "dependency_ids": [],
            "route_bindings": [], "review": EvidenceTests.result_review(self, cp, cp["claim"]), "inputs": []}

    def test_public_service_accepts_own_analytical_proof_and_leaves_root_open(self):
        from tests.search_execution_support import invoke
        spec = self.prepare_checkpoint()
        invoke(self.controller, "checkpoint", spec)
        before = self.controller.status()
        self.assertFalse(checkpoint_milestone(before, "checkpoint-000001"))
        accepted = self.acceptance()
        revision = before["revision"]
        with self.assertRaises(SearchError) as rejected:
            self.controller.command("accept", accepted, revision, "unobserved-review", "checkpoint-000001")
        self.assertEqual(rejected.exception.code, "review_assignment_required")
        self.assertEqual(self.controller.status()["acceptances"], {})
        invoke(self.controller, "accept", accepted, "checkpoint-000001")
        state = self.controller.status()
        result = state["acceptances"]["acceptance-000001"]
        self.assertEqual((result["classification"], result["standard"]), ("analytical", "reviewed"))
        self.assertIsNone(result["obligation_id"])
        self.assertFalse(result["progress_eligible"])
        self.assertTrue(checkpoint_milestone(state, "checkpoint-000001"))
        self.assertIsNone(root_support(state))
        self.assertEqual(state["proof_status"], "open")
        self.assertEqual(state["accounts"], before["accounts"])
        self.assertEqual(state["routes"], {})
        self.assertEqual(list(state["obligations"]), ["obligation-000001"])
        self.assertEqual(state["control"]["node_facts"]["node-000001"]["result_action"], None)

    def test_public_checkpoint_rejects_an_unacknowledged_journal_move(self):
        from tests.search_execution_support import invoke
        spec = self.prepare_checkpoint()
        spec["checkpoint"]["origin"]["move"] = 2
        with self.assertRaisesRegex(SearchError, "acknowledged reserved journal move"):
            invoke(self.controller, "checkpoint", spec)
        self.assertEqual(self.controller.status()["checkpoints"], {})

    def test_public_checkpoint_rejects_a_manifest_for_another_claim(self):
        from tests.search_execution_support import invoke
        spec = self.prepare_checkpoint()
        path = self.workspace / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["claim_digest"] = digest(self.controller.status()["contract"]["original_claim"])
        path.write_text(json.dumps(manifest))
        spec["inputs"][-1]["digest"] = digest(manifest)
        spec["checkpoint"]["evidence_digests"] = [digest(manifest)]
        with self.assertRaisesRegex(SearchError, "Evidence binds a different claim"):
            invoke(self.controller, "checkpoint", spec)
        self.assertEqual(self.controller.status()["checkpoints"], {})
