"""Form outcomes use the shared admissible-evidence validators unchanged."""

import copy

from research_harness import cli, development, problem_attempt
from test_research_problem_attempt import build_acceptance, build_attempt, build_native_attempt, put_native_attempt
from test_research_problem_claims import ProblemCase
from test_research_problem_forming import build_forming, build_outcome
from test_research_problem_tools import ProblemToolsCase


def build_evidence(reference):
    return {"kind": "established support", "description": "The cited record establishes this conclusion.",
            "standing": "admissible", "date": "2026-11-09T01:00:00Z", "reference": reference, "counterexample": None}


class FormingResultEvidenceTests(ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.statement = self.select_statement()
        self.mutate(cli.OPERATIONS["forming"], build_forming())

    def bind_result(self, status="accepted", outcome="reduction"):
        native = build_native_attempt(self.statement["statement"], [build_acceptance(status=status, outcome=outcome)], {})
        identifier = "attempt-" + status + outcome
        self.mutate(problem_attempt.record_attempt, build_attempt(identifier, put_native_attempt(self, native)))
        return {"kind": "result", "attempt_id": identifier, "acceptance_id": "acceptance-1"}

    def test_accepted_result_is_admissible_for_each_established_claim_standing(self):
        reference = self.bind_result()
        for standing in ("refuted", "proved", "implied_by_known_results", "found_in_literature", "supported"):
            saved = self.mutate(cli.OPERATIONS["form-outcome"], build_outcome(standing, standing=standing,
                evidence=[build_evidence(reference)]))["result"]
            self.assertEqual(saved["evidence"][0]["reference"], reference)
            self.assertEqual(saved["refutation_basis"], "admissible_evidence" if standing == "refuted" else None)

    def test_result_validator_refuses_missing_invalidated_and_hypothesis_results(self):
        for status, outcome in (("invalidated", "reduction"), ("hypothesis", "reduction"), ("accepted", "hypothesis")):
            reference = self.bind_result(status, outcome)
            self.assert_error("problem_value_result_missing", lambda: self.mutate(cli.OPERATIONS["form-outcome"],
                build_outcome(evidence=[build_evidence(reference)])))
        reference = {"kind": "result", "attempt_id": "missing", "acceptance_id": "acceptance-1"}
        self.assert_error("problem_value_result_missing", lambda: self.mutate(cli.OPERATIONS["form-outcome"],
            build_outcome(evidence=[build_evidence(reference)])))

    def test_outcome_refuses_a_non_full_reading(self):
        from research_harness.reading import record_reading
        version = self.metadata()
        self.mutate(record_reading, self.abstract_note(version, note_id="abstract"))
        reference = {"kind": "reading", "version_id": version, "reading_id": "abstract"}
        self.assert_error("problem_value_reading_missing", lambda: self.mutate(cli.OPERATIONS["form-outcome"],
            build_outcome(evidence=[build_evidence(reference)])))

    def test_model_performer_must_hold_both_exact_artifacts(self):
        prompt = self.artifacts.put(b"Propose forms.", "text/plain")
        output = self.artifacts.put(b"A proposed form.", "text/plain")
        performer = {"kind": "model_lane", "lane_id": "model-lane", "model": "reported-model", "effort": "reported-effort",
                     "prompt": prompt, "output": output}
        for key in ("model", "effort", "prompt", "output"):
            bad = dict(performer)
            bad.pop(key)
            self.assert_error("invalid_input", lambda: self.mutate(cli.OPERATIONS["forming"],
                build_forming("model-call", forms=[], performer=bad)))
        for key in ("prompt", "output"):
            bad = copy.deepcopy(performer)
            bad[key]["size"] += 1
            self.assert_error("artifact_corrupt", lambda: self.mutate(cli.OPERATIONS["forming"],
                build_forming("model-call", forms=[], performer=bad)))


class FormingComputationEvidenceTests(ProblemToolsCase):
    def test_admitted_assessed_computation_is_admissible_and_unassessed_one_is_refused(self):
        plan, observed = self.run_problem()
        self.mutate(cli.OPERATIONS["forming"], build_forming())
        reference = {"kind": "computation", "execution_id": observed["id"], "output_id": "result", "formal_check": None}
        payload = build_outcome(standing="supported", evidence=[build_evidence(reference)])
        self.assert_error("problem_value_computation_missing", lambda: self.mutate(cli.OPERATIONS["form-outcome"], payload))
        assessment = self.assessment(plan, observed, development=False)
        assessment.update(objective_status="open", disposition="continue", remaining_obligations=["The general form remains open."])
        self.mutate(development.assess_cycle, assessment)
        saved = self.mutate(cli.OPERATIONS["form-outcome"], payload)["result"]
        self.assertEqual(saved["evidence"][0]["reference"], reference)
        self.assertEqual(saved["standing"], "supported")
