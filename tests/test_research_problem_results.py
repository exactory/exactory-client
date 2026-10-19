"""Only current accepted solver results provide mathematical value evidence."""

import copy
import json
from unittest.mock import patch

from research_harness import problem_attempt, problem_evidence, research_decisions, review_protocol
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from test_research_problem_attempt import build_acceptance, build_attempt, build_native_attempt, put_native_attempt
from test_research_problem_claims import ProblemCase
from test_research_problem_delivery import ProblemDeliveryCase


class ProblemResultTests(ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.statement = self.select_statement()

    def bind_result(self, acceptance, identifier="attempt-1"):
        native = build_native_attempt(self.statement["statement"], [acceptance], {})
        payload = build_attempt(identifier, put_native_attempt(self, native))
        self.mutate(problem_attempt.record_attempt, payload)
        return {"kind": "result", "attempt_id": identifier, "acceptance_id": acceptance["id"]}

    def test_result_evidence_accepted_for_an_accepted_reduction(self):
        for outcome in ("proof", "reduction", "obstruction"):
            item = self.bind_result(build_acceptance(outcome=outcome), "attempt-" + outcome)
            saved = self.mutate(self.load_problem_module().record_value, self.build_value([item], "value-" + outcome))["result"]
            self.assertEqual(saved["motivation"][0]["evidence"], [item])

    def test_result_evidence_refused_for_an_invalidated_or_hypothesis_acceptance(self):
        for status, outcome in (("invalidated", "reduction"), ("hypothesis", "reduction"),
                                ("accepted", "hypothesis"), ("accepted", "counterexample")):
            with self.subTest(status=status, outcome=outcome):
                item = self.bind_result(build_acceptance(status=status, outcome=outcome), status + outcome)
                with self.assertRaises(ResearchError) as caught:
                    self.mutate(self.load_problem_module().record_value, self.build_value([item], status + outcome))
                self.assertEqual(caught.exception.code, "problem_value_result_missing")
                self.assertEqual(caught.exception.details, {key: item[key] for key in ("attempt_id", "acceptance_id")})
        item = self.bind_result(build_acceptance(), "current")
        item["acceptance_id"] = "absent"
        self.assert_error("problem_value_result_missing", lambda: self.mutate(
            self.load_problem_module().record_value, self.build_value([item], "absent-acceptance")))

    def test_result_evidence_refused_without_a_current_solver_attempt(self):
        item = {"kind": "result", "attempt_id": "absent", "acceptance_id": "acceptance-1"}
        self.assert_error("problem_value_result_missing", lambda: self.mutate(
            self.load_problem_module().record_value, self.build_value([item])))
        item = self.bind_result(build_acceptance())
        records = self.store.snapshot()["records"]
        for variant in ("missing-native", "stale", "literature", "another-attempt"):
            with self.subTest(variant=variant):
                changed = copy.deepcopy(records)
                if variant == "missing-native":
                    changed.pop("native_attempt")
                elif variant == "stale":
                    changed["problem_attempt"]["attempt-1"]["statement_id"] = "old-statement"
                elif variant == "literature":
                    changed["problem_attempt"]["attempt-1"].update(kind="literature", solver=None,
                        literature={"readings": [], "description": "An assessed literature attempt."})
                else:
                    newer = dict(changed["problem_attempt"]["attempt-1"], id="attempt-2", recorded_revision=self.store.revision + 1)
                    changed["problem_attempt"]["attempt-2"] = newer
                self.assert_error("problem_value_result_missing", lambda: problem_evidence.validate_value(
                    changed, self.artifacts, self.build_value([item])))


class ProblemResultReviewTests(ProblemDeliveryCase):
    def test_empty_attempt_and_results_fields_must_be_present_in_review_packets(self):
        self.initialize_problem()
        self.selected = self.select_statement()
        self.observe_bars()
        original = review_protocol.add_problem_evidence
        def omit_fields(records, artifacts, packet):
            original(records, artifacts, packet)
            self.assertIsNone(packet["problem_evidence"].pop("attempt"))
            self.assertEqual(packet["problem_evidence"].pop("results"), [])
        with patch.object(review_protocol, "add_problem_evidence", side_effect=omit_fields):
            for reviewer in ("first", "second"):
                self.observe("result", reviewer)
        assessed = research_decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)
        pending = [row for row in assessed["obligations"] if row["code"] == "problem_evidence_unassessed"]
        self.assertEqual(len(pending), 2)
        self.assertTrue(all(row["missing"] == ["attempt", "results"] for row in pending), pending)

    def test_results_enter_binding_coverage_and_reviewer_evidence(self):
        self.prepare_ready(solver=True)
        claims = self.load_problem_module()
        before = claims.compute_problem_evidence_digest(self.store.snapshot()["records"], self.artifacts)
        acceptance = build_acceptance()
        native = build_native_attempt(self.selected["statement"], [acceptance], {})
        self.mutate(problem_attempt.record_attempt, build_attempt("result-attempt", put_native_attempt(self, native)))
        item = {"kind": "result", "attempt_id": "result-attempt", "acceptance_id": "acceptance-1"}
        value = self.build_value([item], "result-value")
        self.mutate(claims.record_value, value)
        records = self.store.snapshot()["records"]
        self.assertNotEqual(before, claims.compute_problem_evidence_digest(records, self.artifacts))
        self.assertEqual(problem_evidence.describe_binding(records, self.artifacts)["results"], [
            {"attempt_id": "result-attempt", "acceptance_id": "acceptance-1", "digest": digest(acceptance)}])
        expected = [{"attempt_id": "result-attempt", "acceptance_id": "acceptance-1",
                     "claim": native["checkpoints"][0]["claim"], "outcome": "reduction", "standard": "reviewed"}]
        self.assertEqual(problem_evidence.describe_evidence(records, self.artifacts)["results"], expected)
        original = review_protocol.add_problem_evidence
        for omitted in (None, "results", "attempt"):
            if omitted is not None:
                value = self.build_value([item], "value-without-" + omitted)
                value["motivation"][0]["text"] += " Updated to explain " + omitted + "."
                self.mutate(claims.record_value, value)
            def deliver(records, artifacts, packet):
                original(records, artifacts, packet)
                if omitted is not None:
                    packet["problem_evidence"].pop(omitted)
            with patch.object(review_protocol, "add_problem_evidence", side_effect=deliver):
                for reviewer in ("first", "second"):
                    identifier = "results-" + str(omitted) + "-" + reviewer
                    self.observe("result", reviewer, identifier=identifier)
                    assignment = self.store.snapshot()["records"]["review_assignment"][identifier]
                    packet = json.loads(self.artifacts.read(assignment["packet"]))
                    if omitted is None:
                        self.assertEqual(packet["problem_evidence"]["results"], expected)
            assessed = research_decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)
            if omitted is None:
                self.assertTrue(assessed["ready"], assessed["obligations"])
            else:
                pending = [row for row in assessed["obligations"] if row["code"] == "problem_evidence_unassessed"]
                self.assertEqual({row["review_id"] for row in pending}, {
                    "review-results-" + omitted + "-first", "review-results-" + omitted + "-second"})
                self.assertTrue(all(row["missing"] == [omitted] for row in pending), pending)
