"""Public side allocations preserve the existing account and execution gates."""

import hashlib
import json
import unittest

from search_controller.errors import SearchError
from search_controller.strategy_refresh import assessment_status, context
from tests.search_execution_support import admit_workspace, begin_spec, invoke
from tests.search_fixtures import digest, proposal, provenance
from tests.strategy_refresh_support import reassess_fixture
from tests.support import OPENING, WorkspaceTest, make_move
from tests.test_search_proof import checkpoint_record, event
from tests.test_search_standalone_proof import admit_standalone, standalone_proposal


def allocation():
    return {"max_moves": 1, "message_id": "fixture-user-allocation",
        "instruction": "Allocate one move to the independently admitted standalone fixture.",
        "provenance": provenance("fixture-operator")}


class AllocationContextTests(unittest.TestCase):
    def test_new_instruction_requires_reassessment_without_scientific_evidence(self):
        state = admit_standalone()
        before = context(state)
        state = event(state, "control_recorded", dict(allocation(), action="side_interval", node_id="node-000001"))
        self.assertNotEqual(context(state), before)
        self.assertTrue(assessment_status(state)["required"])
        self.assertEqual(context(state)["evidence"], {})
        self.assertEqual(state["totals"]["used_moves"], 0)

    def test_old_side_interval_preserves_its_historical_strategy_context(self):
        state = admit_standalone()
        before = context(state)
        value = allocation()
        del value["instruction"]
        state = event(state, "control_recorded", dict(value, action="side_interval", node_id="node-000001"))
        self.assertEqual(context(state), before)


class AllocationServiceTests(WorkspaceTest):
    def prepare(self, standalone=True):
        def configure(candidate):
            if standalone:
                candidate.update(category="standalone", relationship="standalone", target_obligation=None)
                candidate["contribution"]["standalone"] = proposal("standalone")["contribution"]["standalone"]
                candidate["contribution"]["standalone"]["primary_sources"] = [candidate["studies"]["problem"]]
                candidate["checkpoint_criteria"] = standalone_proposal()["checkpoint_criteria"]
        self.controller = admit_workspace(self.attack_root, self.slug, configure=configure)

    def test_cli_allocation_is_idempotent_and_requires_real_reassessment(self):
        self.prepare()
        before = self.controller.status()
        spec = allocation()
        path = self.attack_root / "allocation.json"
        path.write_text(json.dumps(spec))
        args = ("search", "side-interval", "node-000001", "--spec", str(path),
                "--expected-revision", str(before["revision"]), "--request-id", "allocate")
        first = self.run_cli(*args)
        self.assertEqual(first[0], 0, first)
        self.assertEqual(self.run_cli(*args), first)
        state = self.controller.status()
        self.assertEqual(state["revision"], before["revision"] + 1)
        self.assertEqual(state["accounts"], before["accounts"])
        self.assertEqual(state["totals"], before["totals"])
        self.assertEqual(state["obligations"], before["obligations"])
        self.assertEqual(state["control"]["main_external_block"], None)
        self.assertEqual(state["control"]["side_instruction_ids"], [spec["message_id"]])
        interval = state["control"]["side_interval"]
        self.assertEqual(interval["instruction"], spec["instruction"])
        self.assertEqual(interval["provenance"], spec["provenance"])
        self.assertEqual(self.controller.command("next", {}, None, None)["kind"], "reassess_strategies")
        with self.assertRaises(SearchError):
            invoke(self.controller, "begin", begin_spec())
        self.assertEqual(self.controller.status()["totals"], before["totals"])
        reassess_fixture(self.controller, {("node-000001", OPENING)})
        self.assertEqual(self.controller.command("next", {}, None, None)["node_id"], "node-000001")
        invoke(self.controller, "begin", begin_spec())
        self.assertEqual(self.controller.status()["totals"]["reserved_moves"], 1)

    def test_invalid_allocations_commit_no_event(self):
        self.prepare()
        before = self.controller.status()
        cases = []
        for field, value in [("max_moves", 25), ("max_moves", 0), ("instruction", ""),
                             ("provenance", dict(provenance("model-author"), source="model"))]:
            candidate = allocation()
            candidate[field] = value
            cases.append(candidate)
        missing = allocation()
        del missing["instruction"]
        cases.append(missing)
        extra = allocation()
        extra["action"] = "resume"
        cases.append(extra)
        for number, candidate in enumerate(cases):
            with self.subTest(number=number), self.assertRaises(SearchError):
                self.controller.command("side-interval", candidate, before["revision"],
                                        "invalid-" + str(number), "node-000001")
            self.assertEqual(self.controller.status(), before)

    def test_duplicate_message_and_stale_revision_are_rejected(self):
        self.prepare()
        before = self.controller.status()
        spec = allocation()
        self.controller.command("side-interval", spec, before["revision"], "first", "node-000001")
        allocated = self.controller.status()
        for revision, request in [(before["revision"], "stale"), (allocated["revision"], "same-message")]:
            with self.subTest(request=request), self.assertRaises(SearchError):
                self.controller.command("side-interval", spec, revision, request, "node-000001")
            self.assertEqual(self.controller.status(), allocated)

    def test_main_node_cannot_receive_a_side_interval(self):
        self.prepare(standalone=False)
        before = self.controller.status()
        with self.assertRaisesRegex(SearchError, "standalone"):
            self.controller.command("side-interval", allocation(), before["revision"], "wrong-category", "node-000001")
        self.assertEqual(self.controller.status(), before)

    def test_interval_exhaustion_and_account_remaining_moves_are_enforced(self):
        self.prepare()
        invoke(self.controller, "side-interval", allocation())
        reassess_fixture(self.controller, {("node-000001", OPENING)})
        invoke(self.controller, "begin", begin_spec())
        result = self.run_cli("journal", "add", self.slug, "--json", json.dumps(make_move(1)))
        self.assertEqual(result[0], 0, result)
        state = self.controller.status()
        self.assertEqual(state["totals"]["used_moves"], 1)
        self.assertEqual(self.controller.command("next", {}, None, None)["kind"], "replan")
        with self.assertRaises(SearchError):
            invoke(self.controller, "begin", begin_spec())
        oversized = dict(allocation(), message_id="second-instruction", max_moves=24)
        with self.assertRaisesRegex(SearchError, "remaining allowance"):
            invoke(self.controller, "side-interval", oversized)
        self.assertEqual(self.controller.status(), state)

    def test_begin_standalone_move_while_root_finalization_awaits_review(self):
        from tests.test_search_evidence import EvidenceTests
        self.prepare()
        state = self.controller.status()
        claim = state["contract"]["original_claim"]
        proof = self.attack_root / "root-proof.md"
        proof.write_text("The synthetic root claim True holds by True.intro; it has no additional assumptions.\n")
        proof_digest = hashlib.sha256(proof.read_bytes()).hexdigest()
        manifest = {"schema_version": 1, "kind": "analytical", "claim_digest": digest(claim),
            "conclusion": {"outcome": "proof", "dependency_ids": [], "route_bindings": []},
            "artifacts": [{"path": "root-proof.md", "digest": proof_digest, "role": "proof"}],
            "dependencies": [], "external_dependencies": [], "verification": None}
        (self.attack_root / "root-manifest.json").write_text(json.dumps(manifest))
        checkpoint = checkpoint_record(state, "obligation-000001")
        del checkpoint["verification_status"]
        checkpoint["origin"].update(source_digest=proof_digest, study_digest=proof_digest)
        checkpoint["evidence_digests"] = [digest(manifest)]
        invoke(self.controller, "checkpoint", {"checkpoint": checkpoint, "inputs": [
            {"path": "root-proof.md", "kind": "artifact", "digest": proof_digest},
            {"path": "root-manifest.json", "kind": "blob", "digest": digest(manifest)}]}, None)
        checkpoint = self.controller.status()["checkpoints"]["checkpoint-000001"]
        invoke(self.controller, "accept", {"obligation_id": "obligation-000001", "outcome": "proof",
            "dependency_ids": [], "route_bindings": [], "inputs": [],
            "review": EvidenceTests.result_review(self, checkpoint, claim)}, checkpoint["id"])
        self.assertEqual(self.controller.command("next", {}, None, None)["kind"], "finalize_root")
        invoke(self.controller, "side-interval", allocation())
        reassess_fixture(self.controller, {("node-000001", OPENING)})
        before = self.controller.status()
        invoke(self.controller, "begin", begin_spec())
        state = self.controller.status()
        self.assertEqual(state["totals"]["reserved_moves"], 1)
        self.assertEqual(state["control"]["pending_moves"], ["move-node-000001-1"])
        self.assertEqual(state["acceptances"], before["acceptances"])
        self.assertIsNone(state["control"]["closure"])
        self.assertEqual(state["openness"]["status"], "pending")
        self.assertEqual(state["proof_status"], "open")

    def test_allocation_does_not_resume_paused_execution(self):
        self.prepare()
        invoke(self.controller, "pause", {"reason": "Synthetic explicit user pause"}, None)
        invoke(self.controller, "side-interval", allocation())
        self.assertEqual(self.controller.command("next", {}, None, None)["kind"], "paused")
        with self.assertRaises(SearchError):
            invoke(self.controller, "begin", begin_spec())
        self.assertEqual(self.controller.status()["totals"]["reserved_moves"], 0)
