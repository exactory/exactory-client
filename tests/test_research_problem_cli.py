"""Every problem mutation has the same file/CAS CLI contract as shared tools."""

import json
from pathlib import Path

from test_research_problem_claims import ProblemCase
from research_harness import cli


class ProblemCliTests(ProblemCase):
    def test_all_problem_mutations_use_json_file_and_explicit_identity(self):
        parser = cli.build_parser()
        for operation in ("problem-init", "problem-statement", "problem-openness", "problem-value",
                          "problem-formal-check", "problem-decision", "problem-posted"):
            args = parser.parse_args([operation, "--file", "payload.json", "--expected-revision", "0", "--request-id", "one"])
            self.assertEqual((args.file, args.expected_revision, args.request_id), ("payload.json", 0, "one"))

    def test_problem_gate_and_export_kinds_are_available(self):
        parser = cli.build_parser()
        self.assertEqual(parser.parse_args(["gate", "post"]).action, "post")
        args = parser.parse_args(["export", "--kind", "problem-openness", "--spec", "spec.json", "--destination", "packet"])
        self.assertEqual((args.kind, args.spec, args.destination), ("problem-openness", "spec.json", "packet"))
        self.assertEqual(parser.parse_args(["export", "--kind", "problem", "--destination", "post"]).kind, "problem")

    def test_registered_problem_commands_have_concrete_cli_examples(self):
        parser = cli.build_parser()
        for operation, key in (("problem-init", "authors"), ("problem-statement", "statement"),
                               ("problem-openness", "assignment_id"), ("problem-value", "motivation"),
                               ("problem-formal-check", "native_closure_digest"), ("problem-decision", "review_ids"),
                               ("problem-posted", "export_directory")):
            payload = cli.run(parser.parse_args(["example", operation]))
            self.assertIn(key, payload)

    def test_status_summary_names_the_problem_orchestration_and_milestone(self):
        self.initialize_problem()
        from research_harness.report_views import next_summary, status_summary
        report = cli.status_report(self.store)
        for view in (next_summary(report), status_summary(report)):
            self.assertEqual(view["orchestration"], "problem_creation")
            self.assertEqual(view["milestone"], "exploration")
            self.assertEqual(view["next_hint"]["code"], "problem_statement_missing")
            self.assertGreater(view["post_obligations"]["total"], 0)

    def test_other_orchestrations_keep_the_same_summary_schema(self):
        report = {"revision": 0, "ready": False, "obligations": [], "next": None}
        from research_harness.report_views import next_summary, status_summary
        self.assertNotIn("orchestration", next_summary(report))
        self.assertNotIn("milestone", status_summary(report))
