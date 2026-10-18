"""The post is a record-authoritative projection of observed problem evidence."""

import json
from pathlib import Path

from test_research_problem_claims import ProblemCase
import test_research_problem_reviews as reviewers
from research_harness import research_decisions, review_protocol
from research_harness.cli import status_report
from research_harness.gates import gate_state
from research_harness.evidence import digest


class ProblemDeliveryCase(ProblemCase):
    route = reviewers.ProblemReviewTests.route
    response = reviewers.ProblemReviewTests.response
    output = reviewers.ProblemReviewTests.output
    observe = reviewers.ProblemReviewTests.observe
    observe_openness = reviewers.ProblemOpennessReviewTests.observe_openness

    def assign(self, role, reviewer, author="problem-author", identifier=None, **context):
        return self.mutate(review_protocol.record_assignment, {"id": identifier or role + "-" + reviewer,
            "route_id": "route", "dossier_id": None, "role": role, "reviewer_id": reviewer,
            "author_id": author, "context": context})["result"]

    def prepare_ready(self, formal=False):
        self.initialize_problem()
        self.selected = self.select_statement(formal_statement={"language": "Lean", "text": "theorem bound : True := by trivial"} if formal else None)
        item = self.full_reading()
        self.observe_openness()
        self.mutate(self.problem().record_openness, {"assignment_id": "openness-assignment"})
        self.mutate(self.problem().record_value, self.value([item]))
        self.supporting = self.artifacts.put(b"Field evidence used by the authored review fixture.", "text/plain")
        for role in ("bar", "result"):
            for reviewer in ("first", "second"):
                self.observe(role, reviewer)

    def decision(self, identifier="decision"):
        return self.mutate(self.problem().record_decision, {"id": identifier, "action": "post",
            "review_ids": ["review-result-first", "review-result-second"],
            "bar_review_ids": ["review-bar-first", "review-bar-second"], "reason": "Both independent findings support the exact evidence."})["result"]


class ProblemDeliveryTests(ProblemDeliveryCase):
    def test_post_decision_export_and_posted_are_bound_to_the_observed_claim(self):
        from research_harness.problem_delivery import export_problem
        self.prepare_ready(formal=True)
        self.assertEqual(status_report(self.store)["milestone"], "openness")
        self.assertEqual(status_report(self.store)["next"]["code"], "problem_decision_missing")
        decision = self.decision()
        report = status_report(self.store)
        self.assertTrue(report["ready"], report["obligations"])
        self.assertEqual(report["milestone"], "value")
        destination = self.root / "post"
        exported = export_problem(self.store, destination)
        manifest = json.loads((destination / "problem-export.json").read_text())
        self.assertEqual(manifest["decision_id"], decision["id"])
        self.assertEqual(manifest["statement_digest"], self.problem().statement_digest(self.selected))
        self.assertEqual(manifest["formal_statement_language"], "Lean")
        self.assertEqual(set(manifest["files"]), {"title.txt", "statement.md", "motivation.md", "state.md", "criteria.md", "citations.json", "formal-statement.txt"})
        self.assertEqual((destination / "statement.md").read_text(), self.selected["statement"] + "\n")
        self.assertTrue(json.loads((destination / "citations.json").read_text())[0]["locator"].startswith("2601."))
        post = self.mutate(self.problem().record_posted, {"problem_id": "posted-id", "export_directory": str(destination)})["result"]
        self.assertEqual(post["statement_id"], self.selected["id"])
        self.assertEqual(status_report(self.store)["milestone"], "post")
        self.error("problem_already_posted", lambda: self.mutate(self.problem().record_posted,
            {"problem_id": "again", "export_directory": str(destination)}))

    def test_export_refuses_until_post_gate_is_ready(self):
        from research_harness.problem_delivery import export_problem
        self.initialize_problem()
        self.error("readiness_required", lambda: export_problem(self.store, self.root / "not-ready"))

    def test_exported_parts_and_manifest_are_never_the_authority(self):
        from research_harness.problem_delivery import export_problem
        self.prepare_ready()
        self.decision()
        directory = self.root / "post"
        export_problem(self.store, directory)
        (directory / "statement.md").write_text("An unreviewed claim.\n")
        self.error("problem_export_mismatch", lambda: self.mutate(self.problem().record_posted,
            {"problem_id": "changed", "export_directory": str(directory)}))
        import hashlib
        manifest = json.loads((directory / "problem-export.json").read_text())
        manifest["files"]["statement.md"] = hashlib.sha256((directory / "statement.md").read_bytes()).hexdigest()
        (directory / "problem-export.json").write_text(json.dumps(manifest))
        self.error("problem_export_mismatch", lambda: self.mutate(self.problem().record_posted,
            {"problem_id": "forged-manifest", "export_directory": str(directory)}))

    def test_statement_selection_makes_prior_finding_and_decision_stale(self):
        self.prepare_ready()
        self.decision()
        self.select_statement(identifier="statement-2", statement="A changed exact mathematical claim.")
        report = status_report(self.store)
        codes = {item["code"] for item in report["obligations"]}
        self.assertIn("problem_openness_stale", codes)
        self.assertIn("problem_decision_stale", codes)
        self.assertEqual(report["milestone"], "formulation")

    def test_decision_refuses_missing_observed_pair_then_records(self):
        self.prepare_ready()
        self.error("problem_review_pair_required", lambda: self.mutate(self.problem().record_decision, {
            "id": "bad", "action": "post", "review_ids": ["review-result-first"],
            "bar_review_ids": ["review-bar-first", "review-bar-second"], "reason": "One result is missing."}))
        self.assertEqual(self.decision()["action"], "post")

    def test_gate_and_status_are_read_only_and_extra_unused_readings_keep_currency(self):
        self.prepare_ready()
        self.decision()
        before = self.store.snapshot()
        first = status_report(self.store)
        self.assertTrue(first["ready"], first["obligations"])
        self.assertEqual(self.store.snapshot(), before)
        version = next(iter(before["records"]["work"]))
        from research_harness.reading import record_reading
        self.mutate(record_reading, self.abstract_note(version, "unused-abstract"))
        current = status_report(self.store)
        self.assertTrue(current["ready"], current["obligations"])
        self.assertEqual(first["problem_evidence_digest"], current["problem_evidence_digest"])
