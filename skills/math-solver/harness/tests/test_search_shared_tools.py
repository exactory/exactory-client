"""Research readiness, stable review stores and versioned openness findings."""

import copy
import sys
import unittest
from unittest.mock import patch

from research_harness.storage import Store as CommonStore
from search_controller.errors import SearchError
from search_controller.model import apply_event, initial_state, replay
from search_controller.proof import validate_review
from search_controller.reviewer import common_root, kind
from search_controller.service import Controller
from tests.search_execution_support import admit_workspace, begin_spec, command_spec, invoke
from tests.search_fixtures import contract, digest, provenance, review
from tests.support import run
from tests.test_search_cli import SearchCLIWorkspace
from tests import test_search_evidence as evidence_tests
from tests.test_search_proof import acceptance_record, checkpointed, event


def build_openness_review(subject, claim, outcome="supported_open"):
    value = {"schema_version": 2, "subject_digest": digest(subject), "claim_digest": digest(claim),
             "reviewer": provenance("openness-reviewer"), "decision": "approve",
             "findings": {key: "Inspected the exact statement and its pinned evidence."
                          for key in ("statement", "assumptions", "scope", "dependencies", "policy")}}
    value["findings"]["openness"] = {
        "outcome": outcome, "statement_digest": digest(claim),
        "evidence": [subject["origin"]["study_digest"], subject["origin"]["source_digest"]],
        "searched_at": "2026-10-16", "coverage": "Statement and neighbours, first-tier top 10 hits per query.",
        "reason": "The novelty study records searches and full readings, including the settling source when already solved."}
    return value


class SharedToolsFoundationTests(SearchCLIWorkspace, unittest.TestCase):
    def test_complete_problem_inputs_allow_begin_without_the_problem_study_file(self):
        self.assertEqual(run(["init", "sample"], self.root)[0], 0)
        controller = admit_workspace(self.root)
        (self.root / "sample/study/problem.md").unlink()
        self.assertEqual(run(["plan", "sample"], self.root)[0], 0)
        invoke(controller, "begin", begin_spec())
        self.assertEqual(controller.status()["totals"]["reserved_moves"], 1)

    def test_busy_review_store_initialization_preserves_native_history_and_can_retry(self):
        from research_harness.errors import ResearchError
        self.initialize()
        controller = Controller(self.root)
        prepared = self.prepared_proposal()
        before = controller.store.read()
        def open_store(root, *, create=False):
            if create:
                raise ResearchError("store_busy", "Held by another writer.")
            return CommonStore(root)
        with patch("research_harness.storage.Store", side_effect=open_store):
            with self.assertRaises(SearchError) as caught:
                controller.command("propose", prepared, 1, "initialize-review-store")
        self.assertEqual(caught.exception.code, "review_workspace_unavailable")
        self.assertEqual(caught.exception.details, {"cause": "store_busy"})
        self.assertEqual(controller.store.read(), before)
        controller.command("propose", prepared, 1, "initialize-review-store")
        self.assertEqual(controller.status()["service"]["review_workspace"], ".")

    def test_legacy_plan_records_default_review_store_without_preparation(self):
        self.assertEqual(run(["init", "sample"], self.root)[0], 0)
        controller = admit_workspace(self.root, historical=True)
        state = controller.status()
        self.assertEqual(state["service"].get("review_workspace"), ".")
        self.assertEqual(state["proposals"]["proposal-000001"]["record"]["schema_version"], 1)
        self.assertEqual(state["service"]["foundation_amendments"], {})
        self.assertNotIn("foundation", state["proposals"]["proposal-000001"]["record"])
        self.assertEqual(state["totals"]["used_moves"], 0)
        self.assertEqual(state["totals"]["used_runs"], 0)
        self.assertNotIn("configuration", CommonStore(self.root).snapshot()["records"])
        invoke(controller, "begin", begin_spec())
        self.assertEqual(controller.status()["totals"]["reserved_moves"], 1)

    def test_foundationless_research_is_reviewed_admitted_and_run_in_ancestor_store(self):
        self.assertEqual(run(["init", "sample"], self.root)[0], 0)
        controller = admit_workspace(self.root, computational=True, common=False, review_workspace="..")
        state = controller.status()
        self.assertEqual(state["service"]["review_workspace"], "..")
        self.assertEqual(common_root(self.root, state), self.root.parent.resolve())
        self.assertTrue(CommonStore(self.root.parent).snapshot()["records"]["review_assignment"])
        self.assertIsNone(state["proposals"]["proposal-000001"]["record"]["foundation"])
        self.assertNotIn("configuration", CommonStore(self.root.parent).snapshot()["records"])
        invoke(controller, "begin", begin_spec())
        workspace = self.root / "sample"
        step = workspace / "deterministic/job"
        step.mkdir()
        (step / "job.py").write_text("print('bounded diagnostic')\n")
        invoke(controller, "run", command_spec(workspace, [sys.executable, "job.py"]))
        invoke(controller, "reconcile", {}, None)
        state = controller.status()
        self.assertEqual(state["runs"]["run-000001"]["termination"], "exit")
        self.assertEqual(state["totals"]["used_runs"], 1)
        self.assertEqual(state["proof_status"], "open")
        from search_controller.computation import pending_interpretation
        self.assertEqual(pending_interpretation(state)["id"], "run-000001")
        self.assertEqual(state["acceptances"], {})
        run_record = state["runs"]["run-000001"]
        invoke(controller, "interpret", {
            "result_digest": run_record["result_digest"], "computation_digest": run_record["computation_digest"],
            "outcome": "verified", "inconclusive_reason": None, "classification": "observation",
            "root_decision": {"kind": "undecided", "reason": "A diagnostic is no proof."},
            "remaining_obligation_ids": ["obligation-000001"],
            "next_action": "Review the exact correspondence with the proposition"}, "run-000001")
        self.assertIsNone(pending_interpretation(controller.status()))
        self.assertEqual(replay(controller.store.read())["service"]["review_workspace"], "..")
        associations = [operation for item in controller.store.read()["events"]
                        for operation in item["payload"].get("operations", [])
                        if operation["kind"] == "review_workspace_recorded"]
        self.assertEqual(associations, [{"kind": "review_workspace_recorded", "payload": {"workspace": ".."}}])

    def test_conflicting_association_is_refused_without_changing_history(self):
        self.initialize()
        prepared = self.prepared_proposal(common=False)
        prepared["proposal"].update(schema_version=3, computation=None, foundation=None)
        prepared["review_workspace"] = ".."
        self.search("propose", prepared, revision=1)
        controller = Controller(self.root)
        before = controller.store.read()
        other = copy.deepcopy(prepared)
        other["proposal"]["attack_slug"] = "other"
        other["review_workspace"] = "."
        with self.assertRaises(SearchError) as caught:
            controller.command("propose", other, 2, "conflicting-store")
        self.assertEqual(caught.exception.code, "review_workspace_mismatch")
        self.assertEqual(controller.store.read(), before)

    def test_verification_cannot_omit_foundation(self):
        self.initialize()
        prepared = self.prepared_proposal(common=False)
        prepared["proposal"].update(schema_version=3, computation=None, foundation=None, role="verification")
        result = self.search("propose", prepared, revision=1, success=False)
        self.assertEqual(result["error"]["code"], "research_foundation_required")

    def test_changed_common_preparation_does_not_require_research_amendment(self):
        self.assertEqual(run(["init", "sample"], self.root)[0], 0)
        controller = admit_workspace(self.root, computational=True, common=True)
        common = CommonStore(self.root)
        common.mutate("fixture.change", {}, lambda tx: tx.put("configuration", "research", {}),
                      expected_revision=common.revision, request_id="changed-preparation")
        # A historical report must never be evaluated under today's preparation code.
        with patch("research_harness.synthesis.synthesis_state", side_effect=AssertionError("historical evaluator rerun")):
            self.assertEqual(controller.guard_legacy("plan", "sample", {})["id"], "node-000001")
            invoke(controller, "begin", begin_spec())
            workspace = self.root / "sample"
            step = workspace / "deterministic/job"
            step.mkdir()
            (step / "job.py").write_text("print('unchanged pinned evidence')\n")
            invoke(controller, "run", command_spec(workspace, [sys.executable, "job.py"]))
        self.assertEqual(controller.status()["service"]["foundation_amendments"], {})
        proposal = controller.status()["proposals"]["proposal-000001"]["record"]
        from search_controller.research import audit_foundation
        snapshot = controller.store.get_blob(proposal["foundation"]["snapshot_digest"])
        from research_harness.review_delivery import references
        pinned = next(iter(references(snapshot)))
        (self.root / ".search/artifacts" / pinned["sha256"]).write_bytes(b"corrupt pinned bytes")
        with self.assertRaises(SearchError):
            audit_foundation(self.root, controller.status(), proposal, proposal["foundation"], controller.store)


class OpennessAcceptanceTests(SearchCLIWorkspace, unittest.TestCase):
    artifact = evidence_tests.EvidenceTests.artifact
    external_checkpoint = evidence_tests.EvidenceTests.external_checkpoint
    setup_external = evidence_tests.EvidenceTests.setup_external

    def test_external_checkpoint_records_default_review_workspace_once(self):
        self.setup_external()
        controller = Controller(self.root)
        self.assertEqual(controller.status()["service"].get("review_workspace"), ".")
        self.search("accept", self.build_acceptance_spec("unresolved"), target="checkpoint-000001", revision=2)
        associations = [operation for item in controller.store.read()["events"]
                        for operation in item["payload"].get("operations", [])
                        if operation["kind"] == "review_workspace_recorded"]
        self.assertEqual(associations, [{"kind": "review_workspace_recorded", "payload": {"workspace": "."}}])

    def build_acceptance_spec(self, outcome="supported_open", claims_openness=False):
        cp = Controller(self.root).status()["checkpoints"]["checkpoint-000001"]
        from tests.openness_support import attach_minimum
        reviewed = build_openness_review(cp, cp["claim"], outcome)
        if outcome != "unresolved":
            reviewed = attach_minimum(Controller(self.root), reviewed)
        return {"obligation_id": "obligation-000001", "outcome": "proof", "dependency_ids": [],
                "route_bindings": [], "review": reviewed, "inputs": [],
                "claims_openness": claims_openness}

    def test_new_acceptance_requires_v2_but_recorded_v1_replays(self):
        self.setup_external()
        spec = self.build_acceptance_spec(claims_openness=True)
        legacy = copy.deepcopy(spec)
        legacy["review"].pop("schema_version")
        legacy["review"]["findings"].pop("openness")
        result = self.search("accept", legacy, target="checkpoint-000001", revision=2, success=False)
        self.assertEqual(result["error"]["code"], "openness_review_required")
        self.search("accept", spec, target="checkpoint-000001", revision=2)
        self.assertEqual(Controller(self.root).status()["acceptances"]["acceptance-000001"]["review"]["schema_version"], 2)
        state = checkpointed(initial_state(contract(), "objective-000001"), "obligation-000001")
        archived = acceptance_record(state, "obligation-000001")
        replayed = event(state, "result_accepted", {"acceptance": archived, "digest": digest(archived)})
        self.assertEqual(replayed["acceptances"]["acceptance-000001"]["review"], archived["review"])
        cp = copy.deepcopy(state["checkpoints"]["checkpoint-000001"])
        cp.pop("id")
        document = {"schema_version": 1, "objective_id": "objective-000001", "contract": contract(), "events": [
            {"sequence": 1, "request_id": "archived-checkpoint", "kind": "checkpoint_recorded",
             "payload": {"checkpoint": cp, "digest": digest(cp)}},
            {"sequence": 2, "request_id": "archived-acceptance", "kind": "result_accepted",
             "payload": {"acceptance": archived, "digest": digest(archived)}}]}
        self.assertEqual(replay(document)["acceptances"], replayed["acceptances"])

    def test_unresolved_can_approve_proof_but_not_claim_of_openness(self):
        self.setup_external()
        result = self.search("accept", self.build_acceptance_spec("unresolved", True), target="checkpoint-000001", revision=2, success=False)
        self.assertEqual(result["error"]["code"], "openness_not_supported")
        self.search("accept", self.build_acceptance_spec("unresolved"), target="checkpoint-000001", revision=2)

    def test_recorded_v1_acceptance_keeps_its_observed_original_prompt(self):
        from research_harness.native_review import LEGACY_PROMPT
        from tests.native_reviewer_support import observe_review
        self.setup_external()
        controller = Controller(self.root)
        state = controller.status()
        archived = acceptance_record(state, "obligation-000001")
        cp = state["checkpoints"]["checkpoint-000001"]
        with patch("research_harness.native_review.PROMPT", LEGACY_PROMPT):
            archived["review"] = observe_review(controller, archived["review"], cp)
        controller.store.put_blob(archived["review"])
        controller.store.append("result_accepted", {"acceptance": archived, "digest": digest(archived)},
            state["revision"], "archived-result-acceptance",
            validate=lambda doc, candidate: apply_event(replay(doc), candidate))
        historical = controller.store.read()
        audited = controller.status(full_audit=True)
        self.assertEqual(audited["freshness"]["status"], "fresh")
        self.assertEqual(audited["acceptances"]["acceptance-000001"]["review"], archived["review"])
        self.assertEqual(controller.store.read(), historical)

    def test_openness_cannot_cite_evidence_missing_from_observed_packet(self):
        from tests.native_reviewer_support import observe_review
        self.setup_external()
        controller = Controller(self.root)
        cp = controller.status()["checkpoints"]["checkpoint-000001"]
        spec = self.build_acceptance_spec()
        spec["review"]["findings"]["openness"]["evidence"] = ["f" * 64]
        spec["review"] = observe_review(controller, spec["review"], cp)
        before = controller.store.read()
        with self.assertRaises(SearchError) as caught:
            controller.command("accept", spec, 2, "missing-openness-evidence", cp["id"])
        self.assertEqual(caught.exception.code, "review_packet_mismatch")
        self.assertEqual(controller.store.read(), before)

    def test_already_solved_retains_requested_deliverables_and_does_not_close_root(self):
        self.setup_external()
        result = self.search("accept", self.build_acceptance_spec("already_solved", True), target="checkpoint-000001", revision=2, success=False)
        self.assertEqual(result["error"]["code"], "openness_not_supported")
        self.search("accept", self.build_acceptance_spec("already_solved"), target="checkpoint-000001", revision=2)
        state = Controller(self.root).status()
        self.assertEqual(state["contract"]["required_deliverables"], contract()["required_deliverables"])
        self.assertEqual(state["proof_status"], "open")
        self.assertIsNone(state["control"]["closure"])

    def test_v2_review_closed_fields_and_exact_statement_are_validated(self):
        self.setup_external()
        value = self.build_acceptance_spec()["review"]
        self.assertEqual(kind(value), "result")
        validate_review(value, value["subject_digest"], value["claim_digest"], new=True)
        for field in ("evidence", "coverage", "reason", "searched_at"):
            wrong = copy.deepcopy(value)
            wrong["findings"]["openness"][field] = [] if field == "evidence" else ""
            with self.subTest(field=field), self.assertRaises(SearchError):
                validate_review(wrong, wrong["subject_digest"], wrong["claim_digest"], new=True)
        wrong = copy.deepcopy(value)
        wrong["findings"]["openness"]["statement_digest"] = "f" * 64
        with self.assertRaises(SearchError):
            validate_review(wrong, wrong["subject_digest"], wrong["claim_digest"], new=True)


class ProcessIdentityTests(unittest.TestCase):
    def test_native_execution_identity_records_a_stable_kernel_birth_time(self):
        from search_controller.execution import start_identity
        identity = start_identity()
        self.assertEqual(start_identity(), identity)
        self.assertTrue(identity.startswith(("proc:", "proc-bsd:", "ps:")))

    def test_unavailable_kernel_identity_refuses_execution(self):
        from search_controller.execution import start_identity
        with patch("search_controller.execution.sys.platform", "darwin"), \
                patch("search_controller.execution.Path.exists", return_value=False), patch("ctypes.CDLL") as library:
            library.return_value.proc_pidinfo.return_value = 0
            with self.assertRaises(SearchError) as caught:
                start_identity()
        self.assertEqual(caught.exception.code, "process_identity_unavailable")
