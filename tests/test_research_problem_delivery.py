"""The post is a record-authoritative projection of observed problem evidence."""

import json
from pathlib import Path

from test_research_problem_candidates import build_candidate, build_slate
from test_research_problem_claims import ProblemCase
import test_research_problem_reviews as reviewers
from research_harness import problem_candidates, research_decisions, review_protocol
from research_harness.cli import status_report
from research_harness.errors import ResearchError
from research_harness.gates import gate_state
from research_harness.evidence import digest


class ProblemDeliveryCase(ProblemCase):
    prepare_review_route = reviewers.ProblemReviewTests.prepare_review_route
    build_provider_response = reviewers.ProblemReviewTests.build_provider_response
    build_review_output = reviewers.ProblemReviewTests.build_review_output
    observe = reviewers.ProblemReviewTests.observe
    deliver_supporting = reviewers.ProblemReviewTests.deliver_supporting
    observe_openness = reviewers.ProblemOpennessReviewTests.observe_openness

    def assign(self, role, reviewer, author="problem-author", identifier=None, **context):
        return self.mutate(review_protocol.record_assignment, {"id": identifier or role + "-" + reviewer,
            "route_id": "route", "dossier_id": None, "role": role, "reviewer_id": reviewer,
            "author_id": author, "context": context})["result"]

    def record_candidate(self, identifier="candidate-1", **changes):
        return self.mutate(problem_candidates.record_candidate, build_candidate(identifier, **changes))["result"]

    def record_slate(self, identifier="slate-1", **changes):
        return self.mutate(problem_candidates.record_slate, build_slate(identifier, **changes))["result"]

    def observe_bars(self):
        self.supporting = self.artifacts.put(b"Field evidence used by the authored review fixture.", "text/plain")
        self.prepare_review_route()
        for reviewer in ("first", "second"):
            self.observe("bar", reviewer)

    def prepare_ready(self, formal=False, solver=False):
        self.initialize_problem()
        self.record_candidate()
        self.observe_bars()
        self.record_slate()
        self.selected = self.select_statement(candidate_id="candidate-1",
            formal_statement={"language": "Lean", "text": "theorem bound : True := by trivial"} if formal else None)
        item = self.record_full_reading()
        from research_harness.problem_attempt import record_attempt
        from test_research_problem_attempt import build_attempt, record_solver_attempt
        if solver:
            record_solver_attempt(self, self.selected)
        else:
            self.mutate(record_attempt, build_attempt(reading=item))
        self.observe_openness()
        self.mutate(self.load_problem_module().record_openness, {"assignment_id": "openness-assignment"})
        self.mutate(self.load_problem_module().record_value, self.build_value([item]))
        from test_research_problem_assessment import build_assessment
        self.mutate(self.load_problem_module().record_assessment, build_assessment(self.selected))
        for reviewer in ("first", "second"):
            self.observe("result", reviewer)

    def record_post_decision(self, identifier="decision"):
        return self.mutate(self.load_problem_module().record_decision, {"id": identifier, "action": "post",
            "review_ids": ["review-result-first", "review-result-second"],
            "bar_review_ids": ["review-bar-first", "review-bar-second"], "reason": "Both independent findings support the exact evidence."})["result"]


class ProblemDeliveryTests(ProblemDeliveryCase):
    def test_post_decision_export_and_posted_are_bound_to_the_observed_claim(self):
        from research_harness.problem_delivery import export_problem
        self.prepare_ready(formal=True)
        self.assertEqual(status_report(self.store)["milestone"], "openness")
        self.assertEqual(status_report(self.store)["next"]["code"], "problem_decision_missing")
        decision = self.record_post_decision()
        report = status_report(self.store)
        self.assertTrue(report["ready"], report["obligations"])
        self.assertEqual(report["milestone"], "value")
        destination = self.root / "post"
        exported = export_problem(self.store, destination)
        manifest = json.loads((destination / "problem-export.json").read_text())
        self.assertEqual(manifest["decision_id"], decision["id"])
        self.assertEqual(manifest["statement_digest"], self.load_problem_module().compute_statement_digest(self.selected))
        self.assertEqual(manifest["formal_statement_language"], "Lean")
        self.assertEqual(set(manifest["files"]), {"title.txt", "statement.md", "motivation.md", "state.md", "criteria.md", "citations.json", "formal-statement.txt"})
        self.assertEqual((destination / "statement.md").read_text(), self.selected["statement"] + "\n")
        self.assertTrue(json.loads((destination / "citations.json").read_text())[0]["locator"].startswith("2601."))
        post = self.mutate(self.load_problem_module().record_posted, {"problem_id": "posted-id", "export_directory": str(destination)})["result"]
        self.assertEqual(post["statement_id"], self.selected["id"])
        self.assertEqual(status_report(self.store)["milestone"], "post")
        self.assert_error("problem_already_posted", lambda: self.mutate(self.load_problem_module().record_posted,
            {"problem_id": "again", "export_directory": str(destination)}))

    def test_export_refuses_until_post_gate_is_ready(self):
        from research_harness.problem_delivery import export_problem
        self.initialize_problem()
        self.assert_error("readiness_required", lambda: export_problem(self.store, self.root / "not-ready"))

    def test_exported_parts_and_manifest_are_never_the_authority(self):
        from research_harness.problem_delivery import export_problem
        self.prepare_ready()
        self.record_post_decision()
        directory = self.root / "post"
        export_problem(self.store, directory)
        (directory / "statement.md").write_text("An unreviewed claim.\n")
        self.assert_error("problem_export_mismatch", lambda: self.mutate(self.load_problem_module().record_posted,
            {"problem_id": "changed", "export_directory": str(directory)}))
        import hashlib
        manifest = json.loads((directory / "problem-export.json").read_text())
        manifest["files"]["statement.md"] = hashlib.sha256((directory / "statement.md").read_bytes()).hexdigest()
        (directory / "problem-export.json").write_text(json.dumps(manifest))
        self.assert_error("problem_export_mismatch", lambda: self.mutate(self.load_problem_module().record_posted,
            {"problem_id": "forged-manifest", "export_directory": str(directory)}))

    def test_export_defaults_to_final_and_reuses_an_identical_export(self):
        from research_harness.problem_delivery import export_problem
        self.prepare_ready()
        self.record_post_decision()
        exported = export_problem(self.store)
        directory = Path(exported["destination"])
        self.assertEqual(directory.parent, self.store.root / "final/problem/exports")
        self.assertEqual(json.loads((directory / "problem-export.json").read_text())["files"], exported["files"])
        self.assertEqual(export_problem(self.store), exported)
        # The library still exports anywhere, and an identical export there is returned as it is too.
        elsewhere = self.root.parent / "elsewhere"
        self.assertEqual(export_problem(self.store, elsewhere)["destination"], str(elsewhere))
        self.assertEqual(export_problem(self.store, elsewhere)["destination"], str(elsewhere))
        (elsewhere / "statement.md").write_text("An unreviewed claim.\n")
        self.assert_error("review_destination_exists", lambda: export_problem(self.store, elsewhere))
        self.assertEqual((elsewhere / "statement.md").read_text(), "An unreviewed claim.\n")
        # A dot file that a file browser adds leaves the exported set as it is, and the folder is named by the
        # export digest that the post records.
        (directory / ".DS_Store").write_bytes(b"\x00\x01")
        post = self.mutate(self.load_problem_module().record_posted,
                           {"problem_id": "posted-id", "export_directory": str(directory)})["result"]
        self.assertEqual(post["export_digest"], directory.name)

    def test_the_cli_exports_the_problem_only_inside_final(self):
        from research_harness import cli
        self.prepare_ready()
        self.record_post_decision()
        parser = cli.build_parser()

        def export(*arguments):
            return cli.run(parser.parse_args(["export", "--workspace", str(self.root), "--kind", "problem", *arguments]))

        for outside in (self.root / "problem-export", self.root / "final/../problem-export", self.root / "finally",
                        self.root.parent / "elsewhere"):
            with self.subTest(destination=outside):
                with self.assertRaises(ResearchError) as caught:
                    export("--destination", str(outside))
                self.assertEqual(caught.exception.code, "export_destination_outside_final")
                self.assertIn("final/problem/exports/", caught.exception.message)
                self.assertFalse(outside.exists())
        exported = export()
        self.assertEqual(Path(exported["destination"]).parent, self.store.root / "final/problem/exports")
        chosen = self.root / "final/problem/exports/chosen"
        self.assertEqual(export("--destination", str(chosen))["destination"], str(chosen))
        self.assertEqual((chosen / "statement.md").read_bytes(),
                         (Path(exported["destination"]) / "statement.md").read_bytes())

    def test_statement_selection_makes_prior_finding_and_decision_stale(self):
        self.prepare_ready()
        self.record_post_decision()
        self.select_statement(identifier="statement-2", statement="A changed exact mathematical claim.")
        report = status_report(self.store)
        codes = {item["code"] for item in report["obligations"]}
        self.assertIn("problem_openness_stale", codes)
        self.assertIn("problem_decision_stale", codes)
        self.assertEqual(report["milestone"], "formulation")

    def test_decision_refuses_missing_observed_pair_then_records(self):
        self.prepare_ready()
        self.assert_error("problem_review_pair_required", lambda: self.mutate(self.load_problem_module().record_decision, {
            "id": "bad", "action": "post", "review_ids": ["review-result-first"],
            "bar_review_ids": ["review-bar-first", "review-bar-second"], "reason": "One result is missing."}))
        self.assertEqual(self.record_post_decision()["action"], "post")

    def test_gate_and_status_are_read_only_and_extra_unused_readings_keep_currency(self):
        self.prepare_ready()
        self.record_post_decision()
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
