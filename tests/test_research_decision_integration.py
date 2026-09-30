"""Strategic decisions use the existing scientific gates and atomic support records."""

import copy
import json

from development_fixtures import DevelopmentCase
from integration_fixtures import account_fixture_citations, observed_candidate
from research_harness import cli, development, integration, principles, publication
from research_harness.cli import status_report
from research_harness.errors import ResearchError
from research_harness.gates import gate_report
from research_harness.operations import prepared_mutation
from strategy_fixtures import StrategyCase


class CombinedSupportTests(DevelopmentCase):
    def setUp(self):
        super().setUp()
        self.prepared_candidate()
        self.execution = self.store.snapshot()["records"]["execution"]["execution-run-1"]["payload"]

    def test_combined_support_commits_the_compatible_review_in_one_transaction(self):
        payload = self.review(self.execution)
        before = self.store.revision

        def prepare(records, value):
            return development.prepare_combined_support(
                records, self.artifacts, value, revision=before + 1, request_id="combined-support")

        result = prepared_mutation(self.store, "fixture.combined-support", payload, prepare,
                                   expected_revision=before, request_id="combined-support")
        self.assertEqual(result["revision"], before + 1)
        self.assertTrue(result["result"]["ready"])
        saved = self.store.snapshot()["records"]["readiness_review"][payload["id"]]
        self.assertEqual(saved["payload"], payload)
        self.assertEqual(saved["reviewed_revision"], before + 1)
        self.assertTrue(development.readiness_report(self.store)["ready"])
        self.assertEqual(prepared_mutation(self.store, "fixture.combined-support", payload, prepare,
                                          expected_revision=before, request_id="combined-support"), result)

    def test_combined_support_retains_a_failed_validity_finding(self):
        payload = self.review(self.execution)
        next(check for check in payload["checks"] if check["kind"] == "validity")["status"] = "failed"
        payload["verdict"] = "not_ready"
        changes, report = development.prepare_combined_support(
            self.store.snapshot()["records"], self.artifacts, payload,
            revision=self.store.revision + 1, request_id="failed-support")
        self.assertFalse(report["ready"])
        self.assertIn("independent_review_pending", {item["code"] for item in report["obligations"]})
        self.assertEqual(changes[0][2]["payload"]["verdict"], "not_ready")

    def test_combined_support_rejects_changed_candidate_and_missing_evidence(self):
        records = self.store.snapshot()["records"]
        payload = self.review(self.execution)
        stale = dict(payload, candidate_digest="0" * 64)
        self.assert_error("readiness_review_stale", lambda: development.prepare_combined_support(
            records, self.artifacts, stale, revision=self.store.revision + 1, request_id="stale-support"))
        missing = copy.deepcopy(payload)
        for check in missing["checks"]:
            check["evidence"] = [self.source_evidence()]
        self.assert_error("review_evidence_incomplete", lambda: development.prepare_combined_support(
            records, self.artifacts, missing, revision=self.store.revision + 1, request_id="missing-support"))


class ManagedBoundaryTests(DevelopmentCase):
    def managed(self, stage="ideate"):
        self.mutate(integration.initialize_workspace, {
            "kind": "study", "state": {"name": "decision-fixture", "stage": stage,
                                        "status": "pending", "waiting": None}})

    def test_managed_target_cannot_commit_without_a_prospective_decision(self):
        self.managed("literature")
        before = self.store.snapshot()
        self.assert_error("research_decision_required", lambda: self.mutate(principles.set_target, {
            "target": {"kind": "objective", "id": "author-proposal", "statement": "An author-selected finite target."},
            "reason": "This is an author-selected target, not a user-fixed problem."}))
        self.assertEqual(self.store.snapshot(), before)

    def test_managed_cycle_cannot_start_without_the_same_prospective_decision(self):
        self.prepared_study()
        self.managed()
        before = self.store.snapshot()
        self.assert_error("research_decision_required", lambda: self.mutate(development.plan_cycle, self.plan()))
        self.assertEqual(self.store.snapshot(), before)

    def test_shared_native_preparation_does_not_acquire_a_strategic_gate(self):
        self.prepared_study()
        planned = self.mutate(development.plan_cycle, self.plan())["result"]
        self.assertEqual(planned["id"], "cycle-1")
        self.assertNotIn("research_decision", planned)

    def test_managed_readiness_and_status_expose_the_same_missing_intent(self):
        self.prepared_study()
        observed_candidate(self)
        self.assertTrue(gate_report(self.store, "readiness")["ready"])
        self.managed("experiment")
        before = self.store.snapshot()
        gate = gate_report(self.store, "readiness")
        status = status_report(self.store)
        self.assertFalse(gate["ready"])
        self.assertIn("research_intent_missing", {item["code"] for item in gate["obligations"]})
        self.assertIn("research_intent_missing", {item["code"] for item in status["obligations"]})
        strategic = gate["research_decision"]
        for action in ("write", "manuscript", "publication", "deposited", "submitted"):
            with self.subTest(action=action):
                report = gate_report(self.store, action)
                self.assertFalse(report["ready"])
                self.assertEqual(report["research_decision"]["obligations"], strategic["obligations"])
        for command in ("status", "next"):
            with self.subTest(command=command):
                report = cli.run(cli.build_parser().parse_args([command, "--workspace", str(self.root)]))
                self.assertEqual(report["research_decision"], strategic)
                self.assertEqual(report["next"], status["next"])
        try:
            publication.validate_upload(self.store, self.root / "unapproved.pdf", None, None)
        except ResearchError as error:
            self.assertEqual(error.code, "readiness_required")
            self.assertIn(strategic["obligations"][0], error.details["obligations"])
        else:
            self.fail("The managed publication upload omitted the current scientific obligation")
        self.assertEqual(self.store.snapshot(), before)

    def test_direct_manuscript_pin_cannot_bypass_the_result_decision(self):
        self.prepared_study()
        execution = observed_candidate(self)
        self.managed("write")
        (self.root / "draft").mkdir(exist_ok=True)
        (self.root / "draft/paper.pdf").write_bytes(b"%PDF-1.4\n% Authored fixture\n%%EOF")
        (self.root / "draft/abstract.txt").write_text("The finite bound holds.")
        (self.root / "draft/references.bib").write_text("@article{fixture,title={Authored bound}}\n")
        (self.root / "draft/claims.json").write_text(json.dumps([{"id": "bound", "claim": "The maximum is 9."}]))
        payload = {"id": "paper", "files": {"pdf": "draft/paper.pdf", "abstract": "draft/abstract.txt",
                   "bibliography": "draft/references.bib", "claims": "draft/claims.json", "sources": None},
                   "claim_evidence": [{"claim_id": "bound", "evidence": [self.result_evidence(execution)]}],
                   "citation_accounting": account_fixture_citations(self)}
        before = self.store.snapshot()
        self.assert_error("research_decision_required", lambda: self.mutate(publication.prepare_publication, payload))
        self.assertEqual(self.store.snapshot(), before)


class TargetCommitmentTests(StrategyCase):
    def test_target_accepts_an_unrelated_source_declaration_in_its_own_transaction(self):
        self.mutate(principles.initialize_research, {"profile": "research", "target": None})
        self.reviewed_dossier()
        self.mutate(self.module("research_decisions").record_research_decision, self.decision())
        self.put("source", "new-source", {"id": "new-source", "status": "captured",
            "response_complete": True, "response": self.evidence})
        impact = copy.deepcopy(self.decision()["source_impact"])
        impact["sources"] = [{"source_id": "new-source", "impact": "unrelated",
            "reason": "An unrelated transport fixture changes no scientific dependency.", "evidence": [self.evidence]}]
        target = {"kind": "objective", "id": "native-target", "statement": self.intent()["objective"]["statement"]}
        wrong = {"target": dict(target, statement="A smaller author-selected target."),
                 "reason": "Attempt a different commitment.", "decision_id": "decision-1", "source_impact": impact}
        self.error("research_objective_commitment_mismatch", lambda: self.mutate(principles.set_target, wrong))
        payload = {"target": target, "reason": "Consume the exact approved objective.", "decision_id": "decision-1", "source_impact": impact}
        revision = self.store.revision
        result = self.mutate(principles.set_target, payload, revision=revision, request="target-commit")
        self.assertEqual(self.mutate(principles.set_target, payload, revision=revision, request="target-commit"), result)
        self.assertEqual(self.store.revision, revision + 1)
        self.assertEqual(result["result"]["target"], target)
        saved = self.store.snapshot()["records"]["research_commitment"]["native-target"]
        self.assertEqual(saved["source_impact"], impact)
        state = self.module("research_decisions").decision_state(self.store.snapshot()["records"], self.artifacts, "cycle")
        self.assertTrue(state["ready"], state["obligations"])
