"""Attempt delivery preserves native findings without deciding proof credit."""

import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from research_harness.evidence import digest as common_digest
from research_harness.storage import Store as CommonStore
from search_controller.errors import SearchError
from search_controller.reviewer import common_root
from search_controller.service import Controller
from tests import test_search_evidence as evidence_tests
from tests import test_search_openness_closure as closure_tests
from tests.search_fixtures import decomposition_proposal, digest, review
from tests.test_search_cli import CLI, SearchCLIWorkspace


class DeliverAttemptTests(SearchCLIWorkspace, unittest.TestCase):
    artifact = evidence_tests.EvidenceTests.artifact
    external_checkpoint = evidence_tests.EvidenceTests.external_checkpoint
    result_review = evidence_tests.EvidenceTests.result_review
    setup_external = evidence_tests.EvidenceTests.setup_external
    set_up_accepted_proof = closure_tests.OpennessClosureTests.set_up_accepted_proof
    build_closure_spec = closure_tests.OpennessClosureTests.build_closure_spec

    def unresolved_objective(self, review_workspace="."):
        self.initialize()
        prepared = self.prepared_proposal()
        prepared["proposal"]["decomposition"] = decomposition_proposal()["decomposition"]
        prepared["review_workspace"] = review_workspace
        self.search("propose", prepared, revision=1)
        self.search("review", {"proposal_id": "proposal-000001",
                              "review": review(prepared["proposal"]), "inputs": []}, revision=2)
        self.search("admit", {}, target="proposal-000001", revision=3)
        controller = Controller(self.root)
        base = controller.status()["obligations"]["obligation-000002"]["claim"]
        reduced = self.external_checkpoint()
        reduced["checkpoint"].update(kind="reduction", claim=base,
                                     remaining_obligation_ids=["obligation-000001"])
        reduced["checkpoint"]["origin"].update(statement=base["statement"], statement_digest=digest(base))
        manifest = json.loads((self.root / "manifest.json").read_text())
        manifest["claim_digest"] = digest(base)
        manifest["conclusion"]["outcome"] = "reduction"
        (self.root / "manifest.json").write_text(json.dumps(manifest))
        reduced["inputs"][-1]["digest"] = digest(manifest)
        reduced["checkpoint"]["evidence_digests"] = [digest(manifest)]
        self.search("checkpoint", reduced, revision=4)
        cp = controller.status()["checkpoints"]["checkpoint-000001"]
        self.search("accept", {"obligation_id": "obligation-000002", "outcome": "reduction",
                              "dependency_ids": [], "route_bindings": [],
                              "review": self.result_review(cp, cp["claim"]), "inputs": []},
                    target=cp["id"], revision=5)
        hypothesis = self.external_checkpoint()
        hypothesis["checkpoint"].update(kind="hypothesis", what_changed="A remaining route needs investigation")
        self.search("checkpoint", hypothesis, revision=6)
        return controller

    def delivered_record(self, controller, result):
        common = CommonStore(common_root(self.root, controller.status()))
        record = common.snapshot()["records"]["native_attempt"][result["native_attempt_digest"]]
        summary = {key: value for key, value in record.items() if key not in ("provenance", "recorded_revision")}
        self.assertEqual(result["native_attempt_digest"], digest(summary))
        self.assertEqual(result["native_attempt_digest"], common_digest(summary))
        self.assertEqual(result, {"objective_id": "objective-000001", "attack_root": str(self.root.resolve()),
                                 "native_attempt_digest": digest(summary), "workspace": str(common.root),
                                 "state": record["state"]})
        return record

    def test_delivers_an_unresolved_objective_with_its_acceptances_and_checkpoints(self):
        controller = self.unresolved_objective()
        state = controller.status()
        before_native = controller.store.read()
        result = self.search("deliver-attempt", {}, revision=7)
        record = self.delivered_record(controller, result)
        self.assertEqual(record["recorded_revision"], CommonStore(self.root).revision)
        self.assertEqual(set(record), {"schema_version", "objective_id", "attack_root", "contract", "state",
                                       "obligations", "checkpoints", "acceptances", "retreats",
                                       "failed_strategies", "nodes", "totals", "provenance", "recorded_revision"})
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(record["contract"], state["contract"])
        self.assertEqual(record["provenance"], {"source": "operator", "actor_id": "search-controller",
                                               "attestation_id": "filesystem-audit:deliver-attempt"})
        self.assertEqual(record["state"], {"proof_status": "open", "execution_status": state["execution_status"],
                                           "openness": {"status": "pending", "outcome": "unresolved",
                                                        "statement_digest": digest(state["contract"]["original_claim"])},
                                           "closure_digest": None})
        self.assertEqual(record["checkpoints"], [
            {"id": "checkpoint-000001", "kind": "reduction", "claim": state["checkpoints"]["checkpoint-000001"]["claim"],
             "verification_status": "pending", "origin_kind": "external_result"},
            {"id": "checkpoint-000002", "kind": "hypothesis", "claim": state["contract"]["original_claim"],
             "verification_status": "pending", "origin_kind": "external_result"}])
        self.assertEqual(record["acceptances"], [{"id": "acceptance-000001", "checkpoint_id": "checkpoint-000001",
            "obligation_id": "obligation-000002", "outcome": "reduction", "classification": "analytical",
            "standard": "reviewed", "status": "accepted", "claims_openness": False,
            "evidence_digests": state["checkpoints"]["checkpoint-000001"]["evidence_digests"]}])
        self.assertEqual(record["obligations"], [{"id": oid, "status": obligation["status"], "claim": obligation["claim"]}
                                                for oid, obligation in sorted(state["obligations"].items())])
        self.assertEqual(record["nodes"], [{"id": "node-000001", "status": "admitted", "cashout_action": None,
                                           "finish_receipt": None}])
        self.assertEqual(record["retreats"], [])
        self.assertEqual(record["failed_strategies"], [])
        self.assertEqual(record["totals"], {"moves": 0, "runs": 0, "elapsed_seconds": None, "workers": 1, "timeout_seconds": 300,
                                            "historical_usage": "known"})
        self.assertNotIn("An analytical proof covering every requested case.", json.dumps(record))
        self.assertEqual(controller.store.read(), before_native)

    def test_delivers_a_resolved_objective_with_its_closure_digest(self):
        self.set_up_accepted_proof("already_solved")
        self.search("complete", self.build_closure_spec(), revision=3)
        controller = Controller(self.root)
        state = controller.status()
        before_native = controller.store.read()
        result = self.search("deliver-attempt", {}, revision=4)
        record = self.delivered_record(controller, result)
        self.assertEqual(record["state"], {"proof_status": "proved", "execution_status": "resolved",
            "openness": {"status": "accepted", "outcome": "already_solved",
                         "statement_digest": digest(state["contract"]["original_claim"])},
            "closure_digest": digest(state["control"]["closure"])})
        self.assertEqual(controller.store.read(), before_native)
        self.assertNotIn("native_closure", CommonStore(self.root).snapshot()["records"])

    def test_repeat_writes_nothing_and_changed_state_writes_a_new_record(self):
        controller = self.unresolved_objective()
        first = self.search("deliver-attempt", {}, revision=7)
        common = CommonStore(self.root)
        snapshot = common.snapshot()
        before_native = controller.store.read()
        source = self.root / "deliver.json"
        source.write_text("{}")
        repeated = subprocess.run([sys.executable, str(CLI), "--attack-root", str(self.root), "search",
            "deliver-attempt", "--file", str(source), "--expected-revision", "7", "--request-id", "repeat-file",
            "--json"], capture_output=True, text=True)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(json.loads(repeated.stdout), first)
        self.assertEqual(common.snapshot(), snapshot)
        self.assertEqual(controller.store.read(), before_native)
        self.search("pause", {"reason": "User requested a pause"}, revision=7)
        second = self.search("deliver-attempt", {}, revision=8)
        self.assertNotEqual(first["native_attempt_digest"], second["native_attempt_digest"])
        after = common.snapshot()
        self.assertEqual(len(after["records"]["native_attempt"]), 2)
        self.assertGreater(after["records"]["native_attempt"][second["native_attempt_digest"]]["recorded_revision"],
                           after["records"]["native_attempt"][first["native_attempt_digest"]]["recorded_revision"])
        self.assertEqual(after["records"]["native_attempt"][first["native_attempt_digest"]],
                         snapshot["records"]["native_attempt"][first["native_attempt_digest"]])
        self.assertEqual(after["records"]["literature_operation"]["native-attempt:" + second["native_attempt_digest"]]["operation"],
                         "native_math.deliver_attempt")

    def test_pause_resume_repeat_moves_the_current_pointer_without_a_new_snapshot(self):
        from tests.search_fixtures import provenance
        controller = self.unresolved_objective()
        first = self.search("deliver-attempt", {}, revision=7)
        self.search("pause", {"reason": "User requested a pause"}, revision=7)
        paused = self.search("deliver-attempt", {}, revision=8)
        self.search("resume", {"objective_id": "objective-000001", "message_id": "resume-delivery",
            "session_id": "codex:fixture-resume", "instruction": "Continue the original objective",
            "provenance": provenance("operator")}, revision=8)
        common = CommonStore(self.root)
        before = common.snapshot()["records"]["native_attempt"]
        resumed = self.search("deliver-attempt", {}, revision=9)
        self.assertEqual(resumed["native_attempt_digest"], first["native_attempt_digest"])
        self.assertNotEqual(resumed["native_attempt_digest"], paused["native_attempt_digest"])
        records = common.snapshot()["records"]
        self.assertEqual(records["native_attempt"], before)
        self.assertEqual(len(records["native_attempt"]), 2)
        key = {name: first[name] for name in ("attack_root", "objective_id")}
        self.assertEqual(records["native_attempt_current"], {digest(key): dict(key,
            native_attempt_digest=resumed["native_attempt_digest"], recorded_revision=common.revision)})
        before = common.snapshot()
        self.search("deliver-attempt", {}, revision=9)
        self.assertEqual(common.snapshot(), before)

    def test_refuses_a_non_empty_spec_and_a_stale_revision(self):
        controller = self.unresolved_objective()
        common = CommonStore(self.root)
        before = common.snapshot()
        before_native = controller.store.read()
        for spec, revision, code in [({"state": {}}, 7, "invalid_record"), ({}, 6, "revision_conflict")]:
            with self.subTest(code=code):
                result = self.search("deliver-attempt", spec, revision=revision, success=False)
                self.assertEqual(result["error"]["code"], code)
                self.assertEqual(common.snapshot(), before)
                self.assertEqual(controller.store.read(), before_native)

    def test_refuses_while_state_error_is_set(self):
        controller = self.unresolved_objective()
        effect = controller.status()["service"]["effects"][0]
        (self.root / ".search" / ("effect-" + digest(effect) + ".json")).unlink()
        self.assertIsNotNone(controller.status()["control"]["state_error"])
        before = CommonStore(self.root).snapshot()
        before_native = controller.store.read()
        result = self.search("deliver-attempt", {}, revision=7, success=False)
        self.assertEqual(result["error"]["code"], "native_state_pending")
        self.assertEqual(CommonStore(self.root).snapshot(), before)
        self.assertEqual(controller.store.read(), before_native)

    def test_audit_failure_blocks_delivery(self):
        controller = self.unresolved_objective()
        proof_digest = controller.store.put_artifact((self.root / "proof.md").read_bytes())
        artifact = self.root / ".search" / "artifacts" / proof_digest
        original = artifact.read_bytes()
        before_native = controller.store.read()
        # Both a new delivery and an identical repeat must freshly audit acceptance.
        for repeat in [False, True]:
            with self.subTest(repeat=repeat):
                if repeat:
                    self.search("deliver-attempt", {}, revision=7)
                before = CommonStore(self.root).snapshot()
                artifact.write_text("Altered evidence after acceptance")
                result = self.search("deliver-attempt", {}, revision=7, success=False)
                self.assertEqual(result["error"]["code"], "audit_failed")
                self.assertEqual(result["error"]["details"], {"acceptance-000001": {
                    "code": "corrupt_artifact", "message": "artifact digest does not match its name"}})
                self.assertEqual(CommonStore(self.root).snapshot(), before)
                self.assertEqual(controller.store.read(), before_native)
                artifact.write_bytes(original)

    def test_record_lands_in_the_review_workspace_store(self):
        controller = self.unresolved_objective(review_workspace="..")
        alias = self.root.parent / "attack-alias"
        alias.symlink_to(self.root, target_is_directory=True)
        result = Controller(alias).command("deliver-attempt", {}, 7, "deliver-through-alias")
        record = self.delivered_record(controller, result)
        self.assertEqual(result["workspace"], str(self.root.parent.resolve()))
        self.assertEqual(record["attack_root"], str(self.root.resolve()))
        self.assertFalse((self.root / ".exactory/research.sqlite3").exists())

    def test_missing_common_store_is_not_created(self):
        self.initialize()
        controller = Controller(self.root)
        before_native = controller.store.read()
        result = self.search("deliver-attempt", {}, revision=1, success=False)
        self.assertEqual(result["error"]["code"], "store_missing")
        self.assertFalse((self.root / ".exactory/research.sqlite3").exists())
        self.assertEqual(controller.store.read(), before_native)

    def test_common_revision_retry_reaudits_a_late_exploratory_origin(self):
        controller = self.unresolved_objective()
        from search_controller.evidence import audit_state
        from tests.test_search_common_evidence import CommonEvidenceTests
        changed = []

        def audit_then_record_origin(*args, **kwargs):
            failures = audit_state(*args, **kwargs)
            if not changed:
                changed.append(True)
                CommonEvidenceTests.record_probe_output(self, (self.root / "proof.md").read_bytes())
            return failures

        with patch("search_controller.attempt_delivery.audit_state", side_effect=audit_then_record_origin):
            with self.assertRaises(SearchError) as caught:
                controller.command("deliver-attempt", {}, 7, "deliver-with-concurrent-origin")
        self.assertEqual(caught.exception.code, "audit_failed")
        self.assertNotIn("native_attempt", CommonStore(self.root).snapshot()["records"])

    def test_totals_count_consumed_work_and_exclude_reservations(self):
        from tests.search_execution_support import admit_workspace, begin_spec, command_spec, invoke
        from tests.support import make_move, run
        self.assertEqual(run(["init", "sample"], self.root)[0], 0)
        controller = admit_workspace(self.root, computational=True)
        workspace = self.root / "sample"
        step = workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("print('bounded fixture')\n")
        invoke(controller, "begin", begin_spec())
        invoke(controller, "run", command_spec(workspace, [sys.executable, "job.py"]))
        state = controller.status()
        first = controller.command("deliver-attempt", {}, state["revision"], "before-journal")
        self.assertEqual(self.delivered_record(controller, first)["totals"],
                         {"moves": 0, "runs": 1, "elapsed_seconds": None, "workers": 1, "timeout_seconds": 300,
                          "historical_usage": "known"})
        status, out, err = run(["journal", "add", "sample", "--json", json.dumps(make_move(1))], self.root)
        self.assertEqual((status, err), (0, ""))
        second = controller.command("deliver-attempt", {}, controller.status()["revision"], "after-journal")
        self.assertEqual(self.delivered_record(controller, second)["totals"],
                         {"moves": 1, "runs": 1, "elapsed_seconds": None, "workers": 1, "timeout_seconds": 300,
                          "historical_usage": "known"})

    def test_summary_exports_the_largest_admitted_timeout(self):
        import copy
        from search_controller.attempt_delivery import build_attempt_summary
        controller = self.unresolved_objective()
        state = controller.status()
        node = state["nodes"]["node-000001"]
        node["admission"]["limits"]["timeout_seconds"] = 100
        second = copy.deepcopy(node)
        second["admission"]["limits"]["timeout_seconds"] = 600
        unadmitted = copy.deepcopy(node)
        unadmitted["admission"] = None
        state["nodes"].update(second=second, unadmitted=unadmitted)
        self.assertEqual(build_attempt_summary(controller, state)["totals"]["timeout_seconds"], 600)

    def test_summary_preserves_historical_usage(self):
        from search_controller.attempt_delivery import build_attempt_summary
        self.initialize()
        controller = Controller(self.root)
        state = controller.status()
        for historical_usage in ["known", "unknown"]:
            with self.subTest(historical_usage=historical_usage):
                state["totals"]["historical_usage"] = historical_usage
                summary = build_attempt_summary(controller, state)
                self.assertEqual(summary["totals"], {"moves": 0, "runs": 0, "elapsed_seconds": None,
                                                     "workers": 0, "timeout_seconds": 0, "historical_usage": historical_usage})

    def test_finished_node_exports_its_pinned_finish_receipt(self):
        from tests.search_execution_support import admit_workspace
        from tests.support import run
        self.assertEqual(run(["init", "sample"], self.root)[0], 0)
        controller = admit_workspace(self.root)
        status, out, err = run(["finish", "sample"], self.root)
        self.assertEqual((status, err), (0, ""))
        state = controller.status()
        receipt = next(item for item in state["service"]["native_receipts"].values()
                       if item["command"] == "finish" and item["outcome"] == "succeeded")
        # Delivery uses the acknowledged snapshot, even after a local file edit.
        (self.root / "sample" / "units" / "FINISHED.json").write_text("Changed working copy")
        result = controller.command("deliver-attempt", {}, state["revision"], "finished-attempt")
        record = self.delivered_record(controller, result)
        self.assertEqual(record["state"]["proof_status"], "open")
        self.assertEqual(record["nodes"], [{"id": "node-000001", "status": "finished", "cashout_action": None,
            "finish_receipt": {"id": receipt["id"], "post_digest": receipt["post_digest"]}}])

    def test_retreats_and_failed_strategies_keep_their_recorded_scope(self):
        from search_controller.integration import internal_operation
        from tests.test_search_scheduler import facts_record
        self.initialize()
        prepared = self.prepared_proposal()
        prepared["proposal"]["retreat_criteria"] = [{"kind": "strategy_failure", "strategy_id": "induction"}]
        self.search("propose", prepared, revision=1)
        self.search("review", {"proposal_id": "proposal-000001",
                              "review": review(prepared["proposal"]), "inputs": []}, revision=2)
        self.search("admit", {}, target="proposal-000001", revision=3)
        controller = Controller(self.root)
        internal_operation(controller, "reconcile", "record-failure", lambda state, content: [
            {"kind": "node_facts_recorded", "payload": {"facts": facts_record(
                status="admitted", failed_strategies=["induction"], cashout_action="inventory")}}])
        retreat = {"criterion": prepared["proposal"]["retreat_criteria"][0],
                   "failed_hypothesis": "The recurrence coefficient is always nonnegative",
                   "observation": "The recorded strategy failed its coefficient test",
                   "last_checkpoint_id": None, "remaining_assumption_ids": [],
                   "reconsideration": "Reconsider only with a new coefficient bound",
                   "abandoned_route_ids": [], "abandoned_assumption_ids": []}
        self.search("retreat", retreat, target="node-000001", revision=5)
        state = controller.status()
        result = self.search("deliver-attempt", {}, revision=6)
        record = self.delivered_record(controller, result)
        self.assertEqual(record["retreats"], [dict(retreat, node_id="node-000001")])
        self.assertEqual(record["failed_strategies"], list(state["control"]["strategy_refresh"]["failures"].values()))
        self.assertEqual(len(record["failed_strategies"]), 1)
        self.assertEqual(record["nodes"][0]["status"], "retreated")
        self.assertEqual(record["nodes"][0]["cashout_action"], "inventory")


if __name__ == "__main__":
    unittest.main()
