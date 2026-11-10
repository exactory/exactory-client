"""Prospective test credit binds the frozen plan, alternative and exact text."""

import copy

from problem_accounts_fixtures import build_account_value, build_alternative, build_relation
from research_harness.evidence import digest
from research_harness import development, problem_claims, problem_evidence, problem_candidates, problem_forming
from test_research_problem_forming import build_form, build_forming
from test_research_problem_candidates import build_candidate
from test_research_problem_tools import ProblemToolsCase


class AccountTestCreditTests(ProblemToolsCase):
    def setUp(self):
        super().setUp()
        self.prepare_problem()
        # A real forming call and candidate precede the run and its evidence.
        call = self.mutate(problem_forming.record_forming, build_forming(forms=[build_form(text=self.objective["statement"])]))["result"]
        candidate = build_candidate("candidate-history")
        candidate["source"]["evidence"] = [{"kind": "record", "record_kind": "forming_call", "id": call["id"], "digest": digest(call)}]
        self.mutate(problem_candidates.record_candidate, candidate)
        self.statement = self.select_statement(identifier="text-for-test", statement=self.objective["statement"],
            candidate_id=candidate["id"], neighbours=["A weaker bound."])
        self.objective = {"id": self.statement["id"], "statement": self.statement["statement"]}
        from research_harness import literature
        search = self.capture_search()
        self.mutate(literature.record_search, search)
        plan = self.plan()
        plan["test_declaration"] = {"alternative": {"kind": "neighbour", "text": "A weaker bound."},
            "excluded_outcome": "The observed extremum is 9.", "prior_knowledge": [{
                "source": {"kind": "planning_model_recall", "description": "Recall of this fixture quantity."},
                "known": "No outcome was known from this source."}], "search_id": search["id"]}
        self.planned = self.mutate(development.plan_cycle, plan)["result"]
        admission = self.admit()
        self.observed = self.execution(admission)
        self.mutate(development.record_execution, self.observed)
        self.mutate(development.assess_cycle, self.assessment(plan, self.observed))
        reference = {"kind": "computation", "execution_id": self.observed["id"], "output_id": "result", "formal_check": None}
        self.value = build_account_value([reference], neighbours=self.statement["neighbours"])
        self.value["statement_id"] = self.statement["id"]
        accounts = self.value["accounts"]
        accounts["alternatives"] = [build_alternative()]
        accounts["relations"] = [build_relation(alternative_id="weaker")]
        accounts["checks"] = [{"id": "test", "target": {"kind": "instance", "description": "The finite range."},
            "contradiction": "A value exceeds 9.", "no_contradiction_reason": None, "result": "The maximum is 9.",
            "standing": "test", "passed": True, "alternative_id": "weaker", "date": "unknown",
            "evidence": [], "evidence_ids": ["use"],
            "independence": {"status": "outcome_not_known", "reason": "No earlier computation or known table of this fixture quantity.",
                "prior_knowledge": [{"source_index": 0, "status": "outcome_not_known", "reason": "No recalled outcome."}]}}]
        accounts["search_coverage"] = [{"id": "coverage", "purpose": "alternatives", "search_ids": [search["id"]], "unreached": "Private notes."}]
        accounts["gain"] = None
        accounts["answer_evidence"] = [{"id": "passed", "fact": "The prospective finite check passed.", "scope": "The finite range.",
            "kind": {"kind": "passed_test", "description": "A frozen prediction tested against the alternative."},
            "direction": "for", "alternative_id": "weaker", "date": "unknown", "evidence": [], "evidence_ids": ["use"], "check_id": "test"}]

    def record(self):
        return self.mutate(problem_claims.record_value, self.value)["result"]

    def test_a_passed_test_is_recorded_and_its_frozen_plan_and_search_are_delivered(self):
        saved = self.record()
        self.assertEqual(saved["accounts"]["checks"][0]["standing"], "test")
        projected = problem_evidence.describe_evidence(self.store.snapshot()["records"], self.artifacts)
        delivered = projected["account_records"]
        self.assertIn("cycle_plan", {row["cite"]["record_kind"] for row in delivered})
        self.assertIn("literature_search", {row["cite"]["record_kind"] for row in delivered})

    def test_a_passed_test_requires_a_frozen_declaration(self):
        records = self.store.snapshot()["records"]
        records["cycle_plan"]["cycle-1"]["payload"].pop("test_declaration")
        self.assert_error("problem_test_run_invalid", lambda: problem_evidence.validate_value(records, self.artifacts, self.value))

    def test_a_test_only_tells_against_the_alternative_named_by_its_plan(self):
        records = self.store.snapshot()["records"]
        records["cycle_plan"]["cycle-1"]["payload"]["test_declaration"]["alternative"]["text"] = "Another alternative."
        self.assert_error("problem_test_run_invalid", lambda: problem_evidence.validate_value(records, self.artifacts, self.value))

    def test_known_or_unestablished_independence_stays_an_observation(self):
        for status in ("outcome_known", "unestablished"):
            with self.subTest(status=status):
                self.value["accounts"]["checks"][0]["independence"]["status"] = status
                self.assert_error("problem_test_run_invalid", self.record)

    def test_a_neighbour_only_revision_keeps_the_test_but_a_changed_text_does_not(self):
        revised = self.select_statement(identifier="same-text", statement=self.statement["statement"], neighbours=["A weaker bound."],
            candidate_id="candidate-history", revises_statement_id=self.statement["id"])
        self.value["statement_id"] = revised["id"]
        self.record()
        changed = self.select_statement(identifier="changed-text", statement="A corrected prediction.", neighbours=["A weaker bound."],
            candidate_id="candidate-history", revises_statement_id=revised["id"])
        self.value.update(id="changed-value", statement_id=changed["id"])
        self.assert_error("problem_test_run_invalid", self.record)

    def test_candidate_history_must_precede_the_test_plan(self):
        records = self.store.snapshot()["records"]
        records["forming_call"]["forming-1"]["recorded_revision"] = self.planned["planned_revision"] + 1
        self.assert_error("problem_accounts_history_missing", lambda: problem_evidence.validate_value(records, self.artifacts, self.value))

    def test_tier_two_can_claim_a_gain_and_a_passed_test_together(self):
        accounts = self.value["accounts"]
        accounts["relations"].append(build_relation("gathered", standing="established", evidence_ids=["use"],
            consequence={"assertion": "The uniform bound.", "instances_or_translation": "Restrict to the finite family.",
                "credited_to": "statement", "weaker_derivations": [{"id": "not-tried", "alternative_id": "weaker",
                    "status": "not_attempted", "reason": "No route from the relaxation has been tried.", "evidence_ids": []}]}))
        accounts["gain"] = {"id": "gain", "form": "common_structure", "claim": "The same construction explains the family.",
            "relation_ids": ["gathered"], "separate_grounds": [{"id": "ground", "relation_id": "gathered", "evidence_ids": ["use"]}],
            "conjunction": {"status": "proved_equivalent", "description": "The conjunction is equivalent; the gain is the common mechanism.", "evidence_ids": ["use"]},
            "establishing_ids": ["use"], "new_object_justification": "The objects are standard."}
        self.value["motivation"][0]["evidence"] = [{"kind": "account", "id": "gain"}]
        saved = self.record()
        self.assertEqual(saved["accounts"]["gain"]["conjunction"]["status"], "proved_equivalent")
        projected = problem_evidence.describe_evidence(self.store.snapshot()["records"], self.artifacts)
        self.assertEqual(projected["value"]["motivation"][0]["delivered_evidence"], accounts["explanatory_use"][0]["evidence"])

    def test_a_failed_or_unresolved_signal_cannot_be_a_passed_test(self):
        for status in ("observed", "unresolved"):
            records = self.store.snapshot()["records"]
            records["cycle_assessment"]["assessment-1"]["payload"]["failures"][0]["status"] = status
            self.assert_error("problem_test_run_invalid", lambda: problem_evidence.validate_value(records, self.artifacts, self.value))

    def test_an_answer_cannot_rename_an_observation_as_a_passed_test(self):
        self.value["accounts"]["checks"] = []
        self.assert_error("problem_test_run_invalid", self.record)

    def test_a_passed_test_fact_derives_its_run_through_the_check_reference(self):
        reading = self.record_full_reading()
        self.value["accounts"]["answer_evidence"][0].update(evidence=[reading], evidence_ids=[])
        self.value["motivation"][0]["evidence"] = [{"kind": "account", "id": "passed"}]
        self.record()
        paragraph = problem_evidence.describe_evidence(self.store.snapshot()["records"], self.artifacts)["value"]["motivation"][0]
        self.assertEqual({item["kind"] for item in paragraph["delivered_evidence"]}, {"reading", "computation"})
        self.assertIn(self.observed["id"], [item.get("execution_id") for item in paragraph["delivered_evidence"]])

    def test_every_frozen_prior_knowledge_source_needs_a_not_known_finding(self):
        records = self.store.snapshot()["records"]
        records["cycle_plan"]["cycle-1"]["payload"]["test_declaration"]["prior_knowledge"] = [
            {"source": {"kind": "planning_model_recall", "description": "Recall of the computed extremum."}, "known": "The maximum was already known."}]
        self.value["accounts"]["checks"][0]["independence"]["prior_knowledge"] = []
        self.assert_error("problem_test_run_invalid", lambda: problem_evidence.validate_value(records, self.artifacts, self.value))

    def record_correction(self, execution_id):
        from test_research_problem_forming import build_form, build_forming
        original = self.store.snapshot()["records"]["forming_call"]["forming-1"]
        execution = self.store.snapshot()["records"]["execution"][self.observed["id"]]
        form = build_form("corrected", text="The corrected finite prediction.", previous={"form_id": "form-1", "reason": "An independent structural correction."},
            grounds=[{"description": "The computation's checked scope is unchanged.", "evidence": [{"kind": "record", "record_kind": "execution", "id": execution["id"], "digest": digest(execution)}]}],
            correction_checks=[{"check_id": "check-1", "was_made": True, "keeps_prediction": True, "chosen_with_outcome": False, "execution_id": execution_id}])
        call = self.mutate(problem_forming.record_forming, build_forming("correction", forms=[form]))["result"]
        candidate = build_candidate("correction-candidate")
        candidate["source"]["evidence"] = [{"kind": "record", "record_kind": "forming_call", "id": row["id"], "digest": digest(row)} for row in (original, call)]
        self.mutate(problem_candidates.record_candidate, candidate)
        selected = self.select_statement(identifier="correction-statement", statement=form["text"], neighbours=["A weaker bound."], candidate_id=candidate["id"], revises_statement_id=self.statement["id"])
        self.value["statement_id"] = selected["id"]
        self.value["accounts"]["checks"][0]["lineage"] = {"form_id": form["id"], "check_id": "check-1"}

    def test_a_grounded_correction_can_keep_the_prediction_of_the_named_run(self):
        self.record_correction(self.observed["id"])
        self.assertEqual(self.record()["accounts"]["checks"][0]["standing"], "test")

    def test_a_correction_cannot_borrow_a_declaration_about_a_different_run(self):
        self.record_correction(self.observed["id"])
        records = self.store.snapshot()["records"]
        records["forming_call"]["correction"]["forms"][0]["correction_checks"][0]["execution_id"] = "other-run"
        # Keep the candidate's reference exact so the failure is the run binding.
        candidate = records["problem_candidate"]["correction-candidate"]
        candidate["source"]["evidence"][1]["digest"] = digest(records["forming_call"]["correction"])
        self.assert_error("problem_test_run_invalid", lambda: problem_evidence.validate_value(records, self.artifacts, self.value))

    def test_a_passed_check_needs_an_actual_alternative(self):
        self.value["accounts"]["answer_evidence"] = []
        self.value["accounts"]["alternatives"][0].update(role="no_alternative", reason="This is only a comparison question.")
        self.assert_error("problem_test_run_invalid", self.record)

    def test_an_object_valued_test_alternative_is_an_input_refusal(self):
        self.value["accounts"]["checks"][0]["alternative_id"] = {}
        self.assert_error("invalid_input", self.record)

    def test_an_object_valued_passed_check_id_is_an_input_refusal(self):
        self.value["accounts"]["answer_evidence"][0]["check_id"] = {}
        self.assert_error("invalid_input", self.record)

    def test_optional_lineage_and_prior_knowledge_findings_refuse_null(self):
        check = self.value["accounts"]["checks"][0]
        check["lineage"] = None
        self.assert_error("invalid_input", self.record)
        check.pop("lineage")
        check["independence"]["prior_knowledge"] = None
        self.assert_error("problem_test_run_invalid", self.record)

    def test_a_correction_declaration_requires_an_existing_execution(self):
        self.assert_error("invalid_input", lambda: self.record_correction("not-recorded"))

    def test_corrections_chosen_with_the_outcome_or_changing_the_prediction_lose_test_credit(self):
        self.record_correction(self.observed["id"])
        for field, value in (("was_made", False), ("keeps_prediction", False), ("chosen_with_outcome", True)):
            with self.subTest(field=field):
                records = self.store.snapshot()["records"]
                records["forming_call"]["correction"]["forms"][0]["correction_checks"][0][field] = value
                records["problem_candidate"]["correction-candidate"]["source"]["evidence"][1]["digest"] = digest(records["forming_call"]["correction"])
                self.assert_error("problem_test_run_invalid", lambda: problem_evidence.validate_value(records, self.artifacts, self.value))
