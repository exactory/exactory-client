"""Accounts refuse structural overclaims and preserve graded shortfalls."""

import copy
import json
from pathlib import Path

from problem_accounts_fixtures import build_account_value, build_accounts, build_alternative, build_relation
from research_harness import problem_claims, problem_evidence
from research_harness.errors import ResearchError
from test_research_problem_claims import ProblemCase


class ProblemAccountsTests(ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.statement = self.select_statement(neighbours=["A weaker bound."])
        self.reading = self.record_full_reading()
        work = self.store.snapshot()["records"]["work"][self.reading["version_id"]]
        self.put("work", work["id"], dict(work, year=2026))
        self.value = build_account_value([self.reading], neighbours=self.statement["neighbours"])

    def record(self, value=None):
        return self.mutate(problem_claims.record_value, self.value if value is None else value)["result"]

    def assert_refused(self, code, change):
        value = copy.deepcopy(self.value)
        change(value)
        before = self.store.snapshot()
        self.assert_error(code, lambda: self.record(value))
        self.assertEqual(self.store.snapshot(), before)

    def add_weaker(self):
        self.value["accounts"]["alternatives"] = [build_alternative()]
        self.value["accounts"]["relations"] = [build_relation(alternative_id="weaker")]

    def test_new_command_requires_accounts_but_historical_validation_does_not(self):
        legacy = {key: value for key, value in self.value.items() if key != "accounts"}
        for section in ("motivation", "current_state"):
            legacy[section] = [{"text": "A fact supported by the source.", "evidence": [self.reading]}]
        problem_evidence.validate_value(self.store.snapshot()["records"], self.artifacts, legacy)
        self.assert_error("problem_accounts_required", lambda: self.record(legacy))

    def test_complete_tier_one_derives_dates_and_projects_whole_accounts(self):
        self.add_weaker()
        accounts = self.value["accounts"]
        accounts["answer"] = {"id": "answer", "status": "conjectured", "text": "Yes.", "evidence_ids": ["support"]}
        accounts["answer_evidence"] = [{"id": "support", "kind": {"kind": "a new kind", "description": "A theorem on a subclass."},
            "fact": "The bound holds for the finite subclass.", "scope": "The subclass only.", "direction": "for",
            "alternative_id": "weaker", "evidence": [], "evidence_ids": ["use"]}]
        accounts["boundaries"] = [{"id": "boundary", "fact": "The relaxation fails.", "scope": "One instance.",
            "evidence": [], "evidence_ids": ["use"], "argument": None}]
        accounts["relations"][0].update(standing="established", evidence_ids=["use"])
        saved = self.record()
        year = str(self.store.snapshot()["records"]["work"][self.reading["version_id"]]["year"])
        self.assertEqual(saved["accounts"]["answer_evidence"][0]["date"], year)
        self.assertEqual(saved["accounts"]["boundaries"][0]["date"], year)
        records = self.store.snapshot()["records"]
        projected = problem_evidence.describe_evidence(records, self.artifacts)
        self.assertEqual(projected["value"]["accounts"], saved["accounts"])
        self.assertEqual([row["id"] for row in projected["readings"]], [self.reading["reading_id"]])
        self.assertEqual(problem_evidence.collect_value_items(saved), [self.reading])

    def test_conjectured_answer_requires_supporting_answer_evidence(self):
        self.assert_refused("problem_answer_evidence_missing", lambda v: v["accounts"]["answer"].update(
            status="conjectured", text="Yes."))

    def test_answer_evidence_names_a_nearest_alternative(self):
        self.value["accounts"]["answer_evidence"] = [{"id": "support", "kind": {"kind": "analogy", "description": "A proved analogue."},
            "fact": "The analogue holds.", "scope": "Another setting.", "direction": "for", "alternative_id": "absent",
            "evidence": [], "evidence_ids": ["use"]}]
        self.assert_refused("problem_answer_alternative_missing", lambda v: None)

    def test_declared_neighbour_needs_a_role_or_no_alternative(self):
        self.assert_refused("problem_neighbour_role_missing", lambda v: v["accounts"].update(alternatives=[]))

    def test_stronger_and_weaker_roles_require_their_own_relation(self):
        self.add_weaker()
        for role in ("weaker", "stronger"):
            with self.subTest(role=role):
                self.assert_refused("problem_alternative_relation_missing", lambda v: v["accounts"]["alternatives"][0].update(
                    role=role, relation_id="missing"))
        self.assert_refused("problem_alternative_relation_missing", lambda v: v["accounts"]["relations"][0].update(alternative_id=None))

    def test_supported_surplus_requires_answer_evidence_against_the_same_alternative(self):
        self.add_weaker()
        self.assert_refused("problem_surplus_support_missing", lambda v: v["accounts"]["alternatives"][0]["surplus"].update(
            status="supported", evidence_ids=["use"]))

    def test_used_surplus_requires_a_relation_credited_to_the_statement(self):
        self.add_weaker()
        self.assert_refused("problem_surplus_use_missing", lambda v: v["accounts"]["alternatives"][0]["surplus"].update(
            status="used", relation_ids=["relation"]))

    def test_a_weaker_alternative_requires_a_surplus_object(self):
        self.add_weaker()
        for missing in (True, False):
            with self.subTest(missing=missing):
                value = copy.deepcopy(self.value)
                alternative = value["accounts"]["alternatives"][0]
                if missing:
                    alternative.pop("surplus")
                else:
                    alternative["surplus"] = None
                before = self.store.snapshot()
                with self.assertRaises(ResearchError) as caught:
                    self.record(value)
                self.assertEqual(caught.exception.code, "problem_surplus_missing")
                for field in ("surplus", "description", "status", "evidence_ids", "relation_ids"):
                    self.assertIn(field, caught.exception.message)
                self.assertEqual(self.store.snapshot(), before)

    def test_declared_alternative_search_ids_refuse_null_and_malformed_lists(self):
        for identifiers in (None, "search-1", [None]):
            with self.subTest(search_ids=identifiers):
                self.assert_refused("invalid_input", lambda v: v["accounts"]["alternatives"][0].update(
                    search_ids=identifiers))

    def test_declared_alternative_search_ids_must_exist_before_delivery(self):
        self.assert_refused("problem_account_search_missing", lambda v: v["accounts"]["alternatives"][0].update(
            search_ids=["not-recorded"]))

    def test_optional_search_ids_of_a_declared_neighbour_are_delivered(self):
        from research_harness import literature
        search = self.capture_search()
        search["question"] = "What is the status of the declared neighbour?"
        self.mutate(literature.record_search, search)
        self.value["accounts"]["alternatives"][0]["search_ids"] = [search["id"]]
        self.record()
        projected = problem_evidence.describe_evidence(self.store.snapshot()["records"], self.artifacts)
        self.assertIn(search["id"], [row["cite"]["id"] for row in projected["account_records"]])

    def test_a_used_surplus_requires_an_established_relation_with_statement_credit(self):
        self.add_weaker()
        accounts = self.value["accounts"]
        accounts["answer_evidence"] = [{"id": "support", "kind": {"kind": "analogy", "description": "A proved analogue."},
            "fact": "The analogue has the exact exponent.", "scope": "The neighbouring setting.", "direction": "for",
            "alternative_id": "weaker", "evidence": [], "evidence_ids": ["use"]}]
        consequence = {"assertion": "The exact exponent.", "instances_or_translation": "The proved translation.",
            "credited_to": "statement", "weaker_derivations": [{"id": "not-tried", "alternative_id": "weaker",
                "status": "not_attempted", "reason": "No derivation has been tried.", "evidence_ids": []}]}
        accounts["relations"].append(build_relation("consequence", consequence=consequence))
        for status in ("used", "supported_and_used"):
            accounts["alternatives"][0]["surplus"].update(status=status, evidence_ids=["support"], relation_ids=["consequence"])
            with self.subTest(status=status):
                self.assert_refused("problem_surplus_use_missing", lambda v: None)
                # An established relation elsewhere does not establish the cited one.
                accounts["relations"][0].update(standing="established", evidence_ids=["use"])
                self.assert_refused("problem_surplus_use_missing", lambda v: None)
        accounts["relations"][1].update(standing="established", evidence_ids=["use"])
        accounts["alternatives"][0]["surplus"]["status"] = "used"
        self.assertEqual(self.record()["accounts"]["alternatives"][0]["surplus"]["status"], "used")

    def test_optional_account_lists_and_dates_refuse_null_before_delivery(self):
        for part in ("checks", "search_coverage", "replacement"):
            with self.subTest(part=part):
                self.assert_refused("invalid_input", lambda v: v["accounts"].update({part: None}))
        self.assert_refused("invalid_input", lambda v: v["accounts"]["explanatory_use"][0].update(date=None))
        self.assert_refused("invalid_input", lambda v: v["accounts"]["explanatory_use"][0].update(check_id=None))

    def test_established_relation_requires_admissible_evidence(self):
        self.value["accounts"]["relations"] = [build_relation(standing="established")]
        self.assert_refused("problem_relation_evidence_missing", lambda v: None)

    def test_boundaries_and_explanatory_use_require_evidence(self):
        self.assert_refused("problem_explanatory_evidence_missing", lambda v: v["accounts"]["explanatory_use"][0].update(evidence=[]))
        self.value["accounts"]["boundaries"] = [{"id": "boundary", "fact": "The stronger form fails.", "scope": "One example.",
            "date": "unknown", "evidence": [], "evidence_ids": [], "argument": None}]
        self.assert_refused("problem_boundary_evidence_missing", lambda v: None)

    def test_consequence_accounts_for_every_weaker_alternative(self):
        self.add_weaker()
        self.value["accounts"]["relations"].append(build_relation("consequence", consequence={
            "assertion": "The exact bound.", "instances_or_translation": "Restrict the instances.",
            "credited_to": "statement", "weaker_derivations": []}))
        self.assert_refused("problem_consequence_comparison_missing", lambda v: None)
        self.value["accounts"]["relations"][1]["consequence"]["weaker_derivations"] = [{"id": "not-tried", "alternative_id": "weaker",
            "status": "not_attempted", "reason": "No derivation was attempted.", "evidence_ids": []}]
        saved = self.record()
        self.assertEqual(saved["accounts"]["relations"][1]["consequence"]["weaker_derivations"][0]["status"], "not_attempted")

    def test_expected_relations_alone_cannot_support_a_value_paragraph(self):
        self.value["accounts"]["relations"] = [build_relation()]
        self.assert_refused("problem_value_expected_only", lambda v: v["motivation"][0].update(evidence=[{"kind": "account", "id": "relation"}]))

    def test_direct_reading_is_only_for_a_field_fact(self):
        self.assert_refused("problem_value_account_required", lambda v: v["motivation"][0].update(evidence=[self.reading]))
        self.value["motivation"][0].update(field_fact=True, evidence=[self.reading])
        self.assertEqual(self.record()["motivation"][0]["evidence"], [self.reading])

    def test_entry_ids_are_unique_and_references_exist_and_do_not_cycle(self):
        self.assert_refused("problem_account_id_conflict", lambda v: v["accounts"]["strength"][0].update(id="use"))
        self.assert_refused("problem_account_reference_missing", lambda v: v["motivation"][0]["evidence"][0].update(id="absent"))
        self.assert_refused("problem_account_reference_cycle", lambda v: v["accounts"]["explanatory_use"][0].update(evidence_ids=["use"]))

    def test_strength_records_all_four_heads_and_their_relaxations(self):
        self.assert_refused("problem_strength_incomplete", lambda v: v["accounts"]["strength"].pop())
        self.assert_refused("problem_strength_incomplete", lambda v: v["accounts"]["strength"][0].update(no_alternative_reason=None))

    def test_unknown_and_partial_dates_use_the_shared_validator(self):
        for date in ("unknown", "2026", "2026-11", "2026-11-09", "2026-11-09T00:00:00Z"):
            value = copy.deepcopy(self.value)
            value["accounts"]["relations"] = [build_relation(date=date)]
            problem_evidence.validate_value(self.store.snapshot()["records"], self.artifacts, value)
        self.value["accounts"]["relations"] = [build_relation(date="2026-02-30")]
        self.assert_refused("invalid_input", lambda v: None)

    def test_cli_example_uses_the_same_account_schema(self):
        example = json.loads((Path(__file__).parents[1] / "docs/research-cli-examples.json").read_text())["problem-value"]
        selected = self.select_statement(identifier="example-statement")
        example["statement_id"] = selected["id"]
        # The fixture reading replaces only the example's illustrative reading locator.
        def replace_readings(value):
            if isinstance(value, dict):
                if value.get("kind") == "reading":
                    value.update(self.reading)
                else:
                    for entry in value.values():
                        replace_readings(entry)
            elif isinstance(value, list):
                for entry in value:
                    replace_readings(entry)
        replace_readings(example)
        self.assertIn("accounts", self.record(example))

    def add_gain(self):
        from research_harness import literature
        search = self.capture_search()
        search["question"] = "Which earlier statements and alternatives have the gathered cases?"
        self.mutate(literature.record_search, search)
        coverage = [{"id": "coverage", "purpose": "earlier_statements", "search_ids": [search["id"]], "unreached": "Private notes and correspondence."}]
        self.value["accounts"].update(checks=[], search_coverage=coverage, gain={"id": "gain", "form": "common_structure",
            "claim": "A single construction handles both families.", "relation_ids": ["gathered"],
            "separate_grounds": [{"id": "grounds", "relation_id": "gathered", "evidence_ids": ["use"]}],
            "conjunction": {"status": "not_known", "description": "No converse is known.", "evidence_ids": []},
            "establishing_ids": ["use"], "new_object_justification": None})
        self.value["accounts"]["relations"] = [build_relation("gathered", standing="established", evidence_ids=["use"],
            consequence={"assertion": "The common construction.", "instances_or_translation": "The finite case.",
                         "credited_to": "statement", "weaker_derivations": []})]

    def test_gain_requires_established_gathered_relations_with_statement_credit(self):
        self.add_gain()
        self.assert_refused("problem_gain_relations_invalid", lambda v: v["accounts"]["relations"][0].update(standing="expected"))
        self.assert_refused("problem_gain_relations_invalid", lambda v: v["accounts"]["relations"][0].update(consequence=None))

    def test_gain_requires_evidence_for_its_relation_to_the_conjunction(self):
        self.add_gain()
        self.assert_refused("problem_gain_conjunction_evidence_missing", lambda v: v["accounts"]["gain"]["conjunction"].update(status="proved_equivalent"))

    def test_gain_requires_tier_two_and_an_earlier_form_of_the_candidate(self):
        self.add_gain()
        self.assert_refused("problem_accounts_tier_two_missing", lambda v: v["accounts"].pop("checks"))
        self.assert_refused("problem_accounts_history_missing", lambda v: None)

    def test_replacement_is_required_for_an_author_declared_refuted_conjecture(self):
        self.value["accounts"]["refuted_conjectures"] = [{"id": "refuted", "statement": "The original unrestricted bound.",
            "identified_by": "author", "sources": [], "evidence_ids": ["use"]}]
        self.assert_refused("problem_replacement_missing", lambda v: None)
        self.value["accounts"]["replacement"] = [{"id": "replacement", "refuted_id": "refuted", "form_id": None,
            "evidence_ids": ["use"], "account": "The question describes the size of the failure."}]
        self.assertEqual(self.record()["accounts"]["replacement"][0]["refuted_id"], "refuted")

    def test_replacement_needs_admissible_refuting_evidence(self):
        self.value["accounts"]["replacement"] = [{"id": "replacement", "refuted_id": None, "form_id": "absent",
            "evidence_ids": [], "account": "The statement describes the failure."}]
        self.assert_refused("problem_replacement_evidence_missing", lambda v: None)

    def test_replacement_is_required_for_a_refuted_expected_ancestor_but_not_a_boundary_alternative(self):
        from research_harness import problem_candidates, problem_forming
        from research_harness.evidence import digest
        from test_research_problem_candidates import build_candidate
        from test_research_problem_forming import build_form, build_forming, build_outcome
        first = build_form("original")
        boundary = build_form("boundary-alternative", purpose={"kind": "alternative", "form_id": "original"})
        self.mutate(problem_forming.record_forming, build_forming("first", forms=[first, boundary]))
        refutation = {"kind": "counterexample", "description": "The full source exhibits the failed instance.",
            "standing": "admissible", "date": "2026", "reference": self.reading, "counterexample": "A failing instance."}
        self.mutate(problem_forming.record_form_outcome, build_outcome("boundary-outcome", form_id="boundary-alternative",
            standing="refuted", evidence=[refutation]))
        correction = build_form("correction", previous={"form_id": "original", "reason": "Describe the size of the failure."},
            correction_checks=[{"check_id": "check-1", "was_made": True, "keeps_prediction": False, "chosen_with_outcome": True}])
        call = self.mutate(problem_forming.record_forming, build_forming("correction-call", forms=[correction]))["result"]
        candidate = build_candidate("corrected-candidate")
        candidate["source"]["evidence"] = [{"kind": "record", "record_kind": "forming_call", "id": call["id"], "digest": digest(call)}]
        self.mutate(problem_candidates.record_candidate, candidate)
        selected = self.select_statement(identifier="corrected-statement", candidate_id=candidate["id"], neighbours=self.statement["neighbours"])
        self.value.update(id="boundary-only", statement_id=selected["id"])
        self.record()
        self.mutate(problem_forming.record_form_outcome, build_outcome("original-refuted", form_id="original", standing="refuted", evidence=[refutation]))
        self.value["id"] = "replacement-value"
        self.assert_refused("problem_replacement_missing", lambda v: None)
        self.value["accounts"]["replacement"] = [{"id": "replacement", "refuted_id": None, "form_id": "original",
            "evidence_ids": ["use"], "account": "The new statement describes how strongly the bound fails."}]
        self.assertEqual(self.record()["accounts"]["replacement"][0]["form_id"], "original")

    def test_a_supported_surplus_cannot_borrow_support_for_another_alternative(self):
        self.add_weaker()
        self.value["accounts"]["alternatives"].append(build_alternative("other", neighbour=None, role="other_assertion", form_id="new-form"))
        from research_harness import problem_forming
        from test_research_problem_forming import build_form, build_forming
        self.mutate(problem_forming.record_forming, build_forming(forms=[build_form("new-form")]))
        from research_harness import literature
        search = self.capture_search()
        search["question"] = "Which alternatives have the same content?"
        self.mutate(literature.record_search, search)
        self.value["accounts"]["alternatives"][1]["search_ids"] = [search["id"]]
        self.value["accounts"]["answer_evidence"] = [{"id": "other-support", "kind": {"kind": "analogue", "description": "A proved analogue."},
            "fact": "The analogue holds.", "scope": "Another setting.", "direction": "for", "alternative_id": "other", "evidence": [], "evidence_ids": ["use"]}]
        self.assert_refused("problem_surplus_support_missing", lambda v: v["accounts"]["alternatives"][0]["surplus"].update(status="supported", evidence_ids=["other-support"]))

    def test_a_missing_coverage_search_has_its_own_refusal(self):
        self.value["accounts"]["search_coverage"] = [{"id": "coverage", "purpose": "earlier_statements", "search_ids": ["missing"], "unreached": "Letters."}]
        self.assert_refused("problem_account_search_missing", lambda v: None)

    def test_missing_dates_are_requested_only_when_the_record_has_none(self):
        self.value["accounts"]["relations"] = [build_relation()]
        self.assert_refused("problem_account_date_missing", lambda v: v["accounts"]["relations"][0].pop("date"))
        self.assert_refused("invalid_input", lambda v: v["accounts"]["explanatory_use"][0].update(date="not a date"))

    def test_accounts_travel_whole_in_every_value_frame(self):
        from research_harness import review_parts
        self.record()
        evidence = problem_evidence.describe_evidence(self.store.snapshot()["records"], self.artifacts)
        packet = {"subject": {"kind": "problem"}, "problem_evidence": evidence, "evidence": []}
        units = review_parts.enumerate_units(packet, "problem_result")
        frame = review_parts.build_frame(packet, units)
        self.assertEqual(frame["problem_evidence"]["value"]["accounts"], evidence["value"]["accounts"])
        self.assertFalse(any(occurrence["path"][:2] == ["problem_evidence", "value"]
                             for unit in units for occurrence in unit["occurrences"]))

    def test_schema_holds_the_sidorenko_regrade_accounts_and_their_qualifications(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/problem_accounts_regrade.json").read_text())
        accounts = fixture["accounts"]
        for boundary in accounts["boundaries"]:
            boundary["evidence"] = [self.reading]
        selected = self.select_statement(identifier="sidorenko-schema", neighbours=[entry["neighbour"] for entry in accounts["alternatives"]])
        value = {"id": "sidorenko-schema", "statement_id": selected["id"], "accounts": accounts,
            "motivation": [{"text": "The reported construction marks a necessary hypothesis within its stated scope.", "evidence": [{"kind": "account", "id": "bnd-1"}]}],
            "current_state": [{"text": "The leading-order mechanism stops on the stated class; other constructions remain open.", "evidence": [{"kind": "account", "id": "bnd-2"}]}]}
        saved = self.record(value)
        self.assertEqual(len(saved["accounts"]["alternatives"]), 7)
        self.assertIn("conditional", saved["accounts"]["boundaries"][1]["scope"])
        self.assertTrue(all(entry["standing"] == "expected" for entry in saved["accounts"]["relations"]))
        self.assertEqual(saved["accounts"]["alternatives"][0]["surplus"]["status"], "neither")

    def test_unknown_alternatives_and_unestablished_gain_evidence_are_refused(self):
        self.assert_refused("problem_alternative_missing", lambda v: v["accounts"]["alternatives"].append({"id": "unrecorded", "neighbour": None, "form_id": "absent", "role": "other_assertion"}))
        self.add_gain()
        self.assert_refused("problem_gain_evidence_missing", lambda v: v["accounts"]["gain"].update(establishing_ids=["strength-exactness"]))

    def test_replacement_cannot_claim_that_an_unrecorded_form_was_refuted(self):
        self.value["accounts"]["replacement"] = [{"id": "replacement", "form_id": "absent", "refuted_id": None,
            "evidence_ids": ["use"], "account": "The question describes the failure."}]
        self.assert_refused("problem_form_missing", lambda v: None)

    def test_malformed_account_references_are_input_refusals(self):
        changes = [lambda v: v["accounts"]["explanatory_use"][0]["evidence"][0].update(reading_id=[]),
            lambda v: v["accounts"]["alternatives"][0].update(form_id={}),
            lambda v: v["accounts"]["strength"][0].update(head={})]
        for change in changes:
            with self.subTest(change=change):
                self.assert_refused("invalid_input", change)

    def test_cycles_outside_fact_parts_are_refused_before_recording(self):
        self.value["accounts"]["answer"]["evidence_ids"] = ["refuted"]
        self.value["accounts"]["refuted_conjectures"] = [{"id": "refuted", "statement": "The earlier bound.", "identified_by": "author", "sources": [], "evidence_ids": ["answer"]}]
        self.assert_refused("problem_account_reference_cycle", lambda v: None)

    def test_gain_cannot_date_a_result_by_a_later_attempt_wrapper(self):
        from research_harness import problem_attempt, problem_candidates, problem_forming
        from research_harness.evidence import digest
        from test_research_problem_attempt import build_acceptance, build_attempt, build_native_attempt, put_native_attempt
        from test_research_problem_candidates import build_candidate
        from test_research_problem_forming import build_forming
        native = build_native_attempt(self.statement["statement"], [build_acceptance()], {})
        identifier = put_native_attempt(self, native)
        call = self.mutate(problem_forming.record_forming, build_forming())["result"]
        candidate = build_candidate("later-candidate")
        candidate["source"]["evidence"] = [{"kind": "record", "record_kind": "forming_call", "id": call["id"], "digest": digest(call)}]
        self.mutate(problem_candidates.record_candidate, candidate)
        selected = self.select_statement(identifier="later-statement", candidate_id=candidate["id"], neighbours=self.statement["neighbours"])
        self.mutate(problem_attempt.record_attempt, build_attempt(native_attempt_digest=identifier, statement_id=selected["id"]))
        self.value.update(statement_id=selected["id"])
        self.value["accounts"]["explanatory_use"][0]["evidence"] = [{"kind": "result", "attempt_id": "attempt-1", "acceptance_id": "acceptance-1"}]
        self.add_gain()
        self.assert_refused("problem_accounts_history_missing", lambda v: None)

    def test_supported_and_used_surplus_keeps_both_grounds(self):
        self.add_weaker()
        accounts = self.value["accounts"]
        accounts["answer_evidence"] = [{"id": "support", "kind": {"kind": "analogy", "description": "A proved analogue."},
            "fact": "The analogue has the exact exponent.", "scope": "The neighbouring setting.", "direction": "for",
            "alternative_id": "weaker", "evidence": [], "evidence_ids": ["use"]}]
        accounts["answer"].update(status="conjectured", text="Yes.", evidence_ids=["support"])
        accounts["relations"].append(build_relation("consequence", standing="established", evidence_ids=["use"], consequence={
            "assertion": "The exact exponent.", "instances_or_translation": "The proved translation.", "credited_to": "statement",
            "weaker_derivations": [{"id": "attempted", "alternative_id": "weaker", "status": "not_derived",
                "reason": "The translation needs the stronger exponent.", "evidence_ids": ["use"]}]}))
        accounts["alternatives"][0]["surplus"].update(status="supported_and_used", evidence_ids=["support"], relation_ids=["consequence"])
        saved = self.record()
        self.assertEqual(saved["accounts"]["alternatives"][0]["surplus"]["status"], "supported_and_used")
        self.assertEqual(problem_evidence.describe_evidence(self.store.snapshot()["records"], self.artifacts)["value"]["motivation"][0]["delivered_evidence"], [self.reading])

    def test_an_exploratory_probe_cannot_establish_a_gain(self):
        from research_harness import cli
        from test_research_probe_lifecycle import build_probe_intent, build_probe_outcome
        self.mutate(cli.OPERATIONS["probe-intent"], build_probe_intent(self.artifacts))
        self.mutate(cli.OPERATIONS["probe"], build_probe_outcome(self.artifacts))
        self.add_gain()
        self.value["accounts"]["checks"] = [{"id": "observation", "target": {"kind": "instance", "description": "An exploratory instance."},
            "contradiction": "The instance exceeds the bound.", "no_contradiction_reason": None, "result": "The finite instance agrees.",
            "standing": "observation", "passed": False, "alternative_id": None, "date": "unknown",
            "evidence": [{"kind": "probe", "id": "probe-1"}], "evidence_ids": [], "independence": None}]
        self.value["accounts"]["gain"]["establishing_ids"] = ["observation", "use"]
        self.assert_refused("problem_gain_evidence_missing", lambda v: None)

    def test_malformed_alternative_relation_and_derivation_ids_are_refused(self):
        self.add_weaker()
        self.assert_refused("invalid_input", lambda v: v["accounts"]["alternatives"][0].update(relation_id={}))
        self.value["accounts"]["relations"][0]["consequence"] = {"assertion": "The bound.", "instances_or_translation": "The finite cases.", "credited_to": "statement",
            "weaker_derivations": [{"id": "derivation", "alternative_id": {}, "status": "not_attempted", "reason": "No attempt.", "evidence_ids": []}]}
        self.assert_refused("invalid_input", lambda v: None)

    def test_tier_two_requires_a_nonempty_account_of_search_coverage(self):
        self.add_gain()
        self.assert_refused("problem_accounts_tier_two_missing", lambda v: v["accounts"].update(search_coverage=[]))

    def test_an_observation_cannot_cite_an_unknown_account_alternative(self):
        self.value["accounts"]["checks"] = [{"id": "observation", "target": {"kind": "instance", "description": "An observed instance."},
            "contradiction": "The bound fails.", "no_contradiction_reason": None, "result": "The bounded case holds.",
            "standing": "observation", "passed": False, "alternative_id": "absent", "date": "2026",
            "evidence": [], "evidence_ids": ["use"], "independence": None}]
        self.assert_refused("problem_account_reference_missing", lambda v: None)

    def test_gain_from_a_full_reading_uses_its_year_for_the_form_history(self):
        from datetime import datetime, timezone
        from unittest.mock import patch
        from research_harness import problem_candidates, problem_forming
        from research_harness.evidence import digest
        from test_research_problem_candidates import build_candidate
        from test_research_problem_forming import build_forming
        with patch("research_harness.problem_forming.datetime") as clock:
            clock.now.return_value = datetime(2025, 11, 9, tzinfo=timezone.utc)
            call = self.mutate(problem_forming.record_forming, build_forming())["result"]
        candidate = build_candidate("reading-gain-candidate")
        candidate["source"]["evidence"] = [{"kind": "record", "record_kind": "forming_call", "id": call["id"], "digest": digest(call)}]
        self.mutate(problem_candidates.record_candidate, candidate)
        selected = self.select_statement(identifier="reading-gain", candidate_id=candidate["id"], neighbours=self.statement["neighbours"])
        self.value["statement_id"] = selected["id"]
        self.add_gain()
        self.assertEqual(self.record()["accounts"]["gain"]["id"], "gain")

    def test_gain_history_compares_timestamp_offsets_as_instants(self):
        from datetime import datetime, timezone
        from unittest.mock import patch
        from research_harness import problem_candidates, problem_forming
        from research_harness.evidence import digest
        from test_research_problem_candidates import build_candidate
        from test_research_problem_forming import build_forming
        with patch("research_harness.problem_forming.datetime") as clock:
            clock.now.return_value = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
            call = self.mutate(problem_forming.record_forming, build_forming())["result"]
        candidate = build_candidate("dated-gain-candidate")
        candidate["source"]["evidence"] = [{"kind": "record", "record_kind": "forming_call", "id": call["id"], "digest": digest(call)}]
        self.mutate(problem_candidates.record_candidate, candidate)
        selected = self.select_statement(identifier="dated-gain", candidate_id=candidate["id"], neighbours=self.statement["neighbours"])
        self.value["statement_id"] = selected["id"]
        work = self.store.snapshot()["records"]["work"][self.reading["version_id"]]
        self.put("work", work["id"], dict(work, year=None))
        self.value["accounts"]["explanatory_use"][0]["date"] = "2026-11-09T05:00:00-08:00"
        self.add_gain()
        self.assertEqual(self.record()["accounts"]["gain"]["id"], "gain")

    def test_a_result_date_is_derived_but_history_keeps_its_precise_time(self):
        from datetime import datetime, timezone
        from unittest.mock import patch
        from research_harness import problem_attempt, problem_candidates, problem_forming
        from research_harness.evidence import digest
        from test_research_problem_attempt import build_acceptance, build_attempt, build_native_attempt, put_native_attempt
        from test_research_problem_candidates import build_candidate
        from test_research_problem_forming import build_forming
        with patch("research_harness.problem_forming.datetime") as clock:
            clock.now.return_value = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
            call = self.mutate(problem_forming.record_forming, build_forming())["result"]
        candidate = build_candidate("dated-result-candidate")
        candidate["source"]["evidence"] = [{"kind": "record", "record_kind": "forming_call", "id": call["id"], "digest": digest(call)}]
        self.mutate(problem_candidates.record_candidate, candidate)
        selected = self.select_statement(identifier="dated-result", candidate_id=candidate["id"], neighbours=self.statement["neighbours"])
        native = build_native_attempt(selected["statement"], [build_acceptance(accepted_at="2026-11-09T13:00:00Z")], {})
        identifier = put_native_attempt(self, native)
        self.mutate(problem_attempt.record_attempt, build_attempt(native_attempt_digest=identifier, statement_id=selected["id"]))
        self.value["statement_id"] = selected["id"]
        self.value["accounts"]["explanatory_use"][0]["evidence"] = [{"kind": "result", "attempt_id": "attempt-1", "acceptance_id": "acceptance-1"}]
        self.add_gain()
        saved = self.record()
        self.assertEqual(saved["accounts"]["explanatory_use"][0]["date"], "2026-11-09")

    def test_a_later_form_alternative_delivers_its_forming_call_and_search(self):
        from research_harness import literature, problem_forming
        from test_research_problem_forming import build_form, build_forming
        forms = [build_form("posed"), build_form("late-form", purpose={"kind": "alternative", "form_id": "posed"})]
        self.mutate(problem_forming.record_forming, build_forming("later-alternatives", forms=forms))
        search = self.capture_search()
        search["question"] = "What is the status of the later alternative?"
        self.mutate(literature.record_search, search)
        self.value["accounts"]["alternatives"].append({"id": "late-alternative", "neighbour": None, "form_id": "late-form", "role": "other_assertion", "search_ids": [search["id"]]})
        self.record()
        projected = problem_evidence.describe_evidence(self.store.snapshot()["records"], self.artifacts)
        delivered = {(entry["cite"]["record_kind"], entry["cite"]["id"]): entry["record"] for entry in projected["account_records"]}
        self.assertIn(("forming_call", "later-alternatives"), delivered)
        self.assertIn(("literature_search", search["id"]), delivered)
        self.assertEqual(delivered[("forming_call", "later-alternatives")]["forms"][1]["id"], "late-form")

    def test_a_field_reading_does_not_turn_expected_relations_into_value_evidence(self):
        self.value["accounts"]["relations"] = [build_relation()]
        self.assert_refused("problem_value_expected_only", lambda v: v["motivation"][0].update(field_fact=True,
            evidence=[{"kind": "account", "id": "relation"}, self.reading]))
