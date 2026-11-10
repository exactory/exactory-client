"""A later solver delivery leaves reports usable and permits evidence recovery."""

import copy
from contextlib import redirect_stderr
import io
import json

from research_harness import cli, problem_attempt, problem_evidence, research_decisions, review_protocol
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.record_check import check_record
from test_research_problem_attempt import build_acceptance, build_attempt, build_native_attempt, put_native_attempt
from test_research_problem_delivery import ProblemDeliveryCase
from test_research_problem_claims import ProblemCase
from test_research_problem_forming import build_forming, build_outcome
from test_research_problem_objective_results import build_objective_native, build_objective_result
from test_research_record_check import digest_tree


class ProblemResultSupersessionTests(ProblemDeliveryCase):
    def run_command(self, command, *arguments):
        return cli.run(cli.build_parser().parse_args([command, "--workspace", str(self.root), *arguments]))

    def refresh_reviews(self, prefix):
        return [self.observe("result", reviewer, identifier=prefix + "-" + reviewer)["id"]
                for reviewer in ("first", "second")]

    def prepare_cited_result(self, *, legacy=False, outcome=False, cite_value=True):
        self.prepare_ready(solver=True)
        self.native = (build_native_attempt(self.selected["statement"], [build_acceptance()], {})
                       if legacy else build_objective_native())
        identifier = put_native_attempt(self, self.native)
        if legacy:
            self.mutate(problem_attempt.record_attempt, build_attempt("cited-attempt", identifier))
            self.reference = {"kind": "result", "attempt_id": "cited-attempt", "acceptance_id": "acceptance-1"}
        else:
            self.reference = build_objective_result(identifier)
        if cite_value:
            self.mutate(self.load_problem_module().record_value, self.build_value([self.reference], "cited-value"))
        if outcome:
            self.mutate(cli.OPERATIONS["forming"], build_forming())
            self.outcome = build_outcome(evidence=[{
                "kind": "reduction", "description": "The accepted reduction locates the remaining case.",
                "standing": "admissible", "date": "unknown", "reference": self.reference, "counterexample": None}])
            self.mutate(cli.OPERATIONS["form-outcome"], self.outcome)
        reviews = (self.refresh_reviews("cited") if cite_value else
                   ["review-result-first", "review-result-second"])
        self.mutate(self.load_problem_module().record_decision, {
            "id": "cited-decision", "action": "post", "review_ids": reviews,
            "bar_review_ids": ["review-bar-first", "review-bar-second"], "reason": "The observed pair covers the value."})
        records = self.store.snapshot()["records"]
        self.before_binding = problem_evidence.describe_binding(records, self.artifacts)
        self.before_currency = review_protocol.compute_problem_review_currency(records, self.artifacts)
        later = copy.deepcopy(self.native)
        later["totals"]["moves"] -= 1
        self.latest = put_native_attempt(self, later)

    def assert_superseded(self, obligations):
        pending = [row for row in obligations if row["code"] == "problem_result_superseded"]
        self.assertEqual(len(pending), 1, obligations)
        self.assertEqual(pending[0]["native_attempt_digest"], self.reference["native_attempt_digest"])
        self.assertEqual(pending[0]["latest_native_attempt_digest"], self.latest)

    def check_reports(self, *, legacy=False):
        before = self.store.snapshot()
        for command in ("status", "next"):
            with self.subTest(command=command):
                report = self.run_command(command)
                self.assertFalse(report["ready"])
                if legacy:
                    self.assertIn("problem_attempt_stale", {row["code"] for row in report["obligations"]})
                else:
                    self.assert_superseded(report["obligations"])
        code = "problem_attempt_stale" if legacy else "problem_result_superseded"
        page = self.run_command("obligations", "--code", code)
        self.assertIn(code, json.dumps(page))
        if not legacy:
            self.assertIn(self.latest, json.dumps(page))
        for command in ("status", "next"):
            self.assertIn(code, json.dumps(self.run_command(command, "--summary")))
        # gate has always returned a structured readiness refusal for unmet obligations.
        error = io.StringIO()
        with redirect_stderr(error), self.assertRaises(SystemExit) as caught:
            cli.main(["gate", "post", "--workspace", str(self.root)])
        self.assertEqual(caught.exception.code, 1)
        refusal = json.loads(error.getvalue())["error"]
        self.assertEqual(refusal["code"], "readiness_required")
        if not legacy:
            self.assert_superseded(refusal["details"]["obligations"])
        exported = self.run_command("export", "--kind", "outputs")
        self.assertIn("revision", exported)
        self.assertEqual(self.store.snapshot(), before)
        records = before["records"]
        binding = problem_evidence.describe_binding(records, self.artifacts)
        evidence = problem_evidence.describe_evidence(records, self.artifacts)
        currency = review_protocol.compute_problem_review_currency(records, self.artifacts)
        assessed = research_decisions.assess_problem_reviews(records, self.artifacts)
        reviews = [records["value_review"][identifier] for identifier in assessed["review_ids"]]
        coverage = research_decisions._assess_problem_coverage(records, self.artifacts, reviews, evidence,
            self.load_problem_module().compute_problem_evidence_digest(records, self.artifacts))
        if legacy:
            self.assertEqual(binding, self.before_binding)
            self.assertEqual(currency, self.before_currency)
            self.assertTrue(assessed["ready"], assessed["obligations"])
            self.assertEqual(coverage, [])
        else:
            self.assertNotEqual(currency, self.before_currency)
            self.assertNotEqual(digest(binding), digest(self.before_binding))
            self.assertEqual(binding["results"], [])
            self.assertEqual(evidence["results"], [])
            for report in (binding, evidence, assessed):
                self.assert_superseded(report["obligations"])
            self.assert_superseded(coverage)
            self.assertIn("problem_evidence_unassessed", {row["code"] for row in coverage})
        tree = digest_tree(self.root)
        checked = check_record(self.root, isolate=False)
        self.assertTrue(checked["ready"], checked)
        self.assertEqual(digest_tree(self.root), tree)

    def test_value_reports_survive_a_later_objective_delivery(self):
        self.prepare_cited_result()
        self.check_reports()

    def test_value_and_form_outcome_reports_survive_and_recover(self):
        self.prepare_cited_result(outcome=True)
        self.check_reports()
        before = self.store.snapshot()
        self.assert_error("problem_result_superseded", lambda: self.mutate(
            self.load_problem_module().record_value, self.build_value([self.reference], "refused-value")))
        self.assert_error("problem_result_superseded", lambda: self.mutate(
            cli.OPERATIONS["form-outcome"], dict(self.outcome, id="refused-outcome")))
        self.assertEqual(self.store.snapshot(), before)
        latest = build_objective_result(self.latest)
        self.mutate(self.load_problem_module().record_value, self.build_value([latest], "latest-value"))
        pending = self.run_command("status")["obligations"]
        self.assertNotIn("problem_result_superseded", {row["code"] for row in pending})
        self.assertIn("problem_evidence_unassessed", {row["code"] for row in pending})
        reviews = self.refresh_reviews("latest")
        self.mutate(self.load_problem_module().record_decision, {
            "id": "latest-decision", "action": "post", "review_ids": reviews,
            "bar_review_ids": ["review-bar-first", "review-bar-second"], "reason": "The refreshed pair covers the latest result."})
        self.assertTrue(self.run_command("gate", "post")["ready"])
        records = self.store.snapshot()["records"]
        self.assertEqual(records["form_outcome"]["outcome-1"]["evidence"][0]["reference"], self.reference)
        self.assertEqual(records["problem_value"]["cited-value"]["motivation"][0]["evidence"], [self.reference])

    def test_historical_form_outcome_does_not_stale_an_unrelated_value(self):
        self.prepare_cited_result(outcome=True, cite_value=False)
        records = self.store.snapshot()["records"]
        self.assertEqual(problem_evidence.describe_binding(records, self.artifacts), self.before_binding)
        self.assertEqual(review_protocol.compute_problem_review_currency(records, self.artifacts), self.before_currency)
        for command in ("status", "next", "gate"):
            arguments = ("post",) if command == "gate" else ()
            self.assertTrue(self.run_command(command, *arguments)["ready"])
        self.assertEqual(self.run_command("obligations", "--code", "problem_result_superseded")["obligations"], [])
        self.run_command("export", "--kind", "outputs")
        self.assertTrue(research_decisions.assess_problem_reviews(records, self.artifacts)["ready"])
        self.assertTrue(check_record(self.root, isolate=False)["ready"])

    def test_legacy_result_keeps_its_projection_currency_and_reports_after_redelivery(self):
        self.prepare_cited_result(legacy=True)
        self.check_reports(legacy=True)


class ProblemResultProjectionTests(ProblemCase):
    def test_each_later_delivery_changes_the_repair_digest_and_keeps_other_current_results(self):
        self.initialize_problem()
        self.select_statement()
        native = build_objective_native()
        first = build_objective_result(put_native_attempt(self, native))
        other = copy.deepcopy(native)
        other["objective_id"] = "independent-objective"
        current = build_objective_result(put_native_attempt(self, other))
        self.mutate(self.load_problem_module().record_value, self.build_value([
            first, dict(first, purpose="An independently worded purpose."), current]))
        previous = None
        for moves in (23, 22):
            native["totals"]["moves"] = moves
            latest = put_native_attempt(self, native)
            records = self.store.snapshot()["records"]
            binding = problem_evidence.describe_binding(records, self.artifacts)
            evidence = problem_evidence.describe_evidence(records, self.artifacts)
            for report in (binding, evidence):
                self.assertEqual(len(report["obligations"]), 1)
                self.assertEqual(report["obligations"][0]["latest_native_attempt_digest"], latest)
                self.assertEqual(len(report["results"]), 1)
                self.assertEqual(report["results"][0]["native_attempt_digest"], current["native_attempt_digest"])
            self.assertNotEqual(digest(binding), previous)
            previous = digest(binding)

    def test_projection_still_refuses_an_undelivered_result(self):
        self.initialize_problem()
        self.select_statement()
        reference = build_objective_result(put_native_attempt(self, build_objective_native()))
        self.mutate(self.load_problem_module().record_value, self.build_value([reference]))
        records = self.store.snapshot()["records"]
        records.pop("native_attempt_current")
        for project in (problem_evidence.describe_binding, problem_evidence.describe_evidence):
            with self.subTest(project=project.__name__):
                with self.assertRaises(ResearchError) as caught:
                    project(records, self.artifacts)
                self.assertEqual(caught.exception.code, "problem_result_unbound")
