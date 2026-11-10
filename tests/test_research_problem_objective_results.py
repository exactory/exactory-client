"""Delivered objectives support their accepted conclusions beside the current attempt."""

import copy
import json
from pathlib import Path

from research_harness import problem_attempt, problem_evidence, research_decisions
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from test_research_problem_attempt import build_acceptance, build_attempt, build_native_attempt, put_native_attempt
from test_research_problem_claims import ProblemCase
from test_research_problem_delivery import ProblemDeliveryCase


def build_objective_native(outcome="reduction"):
    native = build_native_attempt("The stronger form fails on the balanced family.",
                                  [build_acceptance(outcome=outcome)], {})
    native["attack_root"] = "/native/stronger-form"
    native["objective_id"] = "objective-stronger-form"
    native["checkpoints"][0]["claim"]["statement"] = "The balanced family refutes the stronger form."
    native["checkpoints"][0]["kind"] = outcome
    return native


def build_objective_result(identifier, **changes):
    return dict({"kind": "result", "native_attempt_digest": identifier, "acceptance_id": "acceptance-1",
                 "purpose": "Test the stronger form as a necessary boundary.",
                 "extension_reason": None}, **changes)


class ProblemObjectiveResultTests(ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.statement = self.select_statement()

    def deliver_result(self, native=None, **changes):
        native = build_objective_native() if native is None else native
        return build_objective_result(put_native_attempt(self, native), **changes)

    def test_each_accepted_outcome_can_support_a_value_without_a_current_attempt(self):
        for outcome in ("proof", "reduction", "obstruction", "counterexample"):
            with self.subTest(outcome=outcome):
                native = build_objective_native(outcome)
                native["contract"]["original_claim"]["scope"] = {"kind": "case_ids", "case_ids": ["balanced"]}
                item = self.deliver_result(native)
                records = self.store.snapshot()["records"]
                self.assertEqual(problem_evidence.validate_result(records, item), native["acceptances"][0])
                value = self.build_value([item], "value-" + outcome)
                saved = self.mutate(self.load_problem_module().record_value, value)["result"]
                self.assertEqual(saved["accounts"]["explanatory_use"][0]["evidence"], [item])
                self.assertEqual(saved["accounts"]["explanatory_use"][0]["evidence"], [item])

    def test_result_validator_needs_no_current_statement(self):
        item = self.deliver_result()
        self.mutate(self.load_problem_module().record_value, self.build_value([item]))
        records = self.store.snapshot()["records"]
        records.pop("problem_statement")
        records.pop("problem_selection", None)
        self.assertEqual(problem_evidence.validate_result(records, item)["id"], "acceptance-1")
        for project in (problem_evidence.describe_binding, problem_evidence.describe_evidence):
            projected = project(records, self.artifacts)
            self.assertIsNone(projected["claim"])
            self.assertIsNone(projected["attempt"])
            self.assertEqual(projected["results"][0]["native_attempt_digest"], item["native_attempt_digest"])

    def test_resolution_of_another_objective_does_not_resolve_the_current_statement(self):
        current = build_native_attempt(self.statement["statement"], [], {})
        self.mutate(problem_attempt.record_attempt, build_attempt(native_attempt_digest=put_native_attempt(self, current)))
        for outcome, status in (("proof", "proved"), ("counterexample", "disproved")):
            with self.subTest(outcome=outcome):
                native = build_objective_native(outcome)
                native["acceptances"][0]["obligation_id"] = native["contract"]["root_obligation"]
                native["state"]["proof_status"] = status
                item = self.deliver_result(native)
                self.mutate(self.load_problem_module().record_value, self.build_value([item], "value-" + outcome))
                records = self.store.snapshot()["records"]
                self.assertEqual(problem_evidence.validate_result(records, item)["outcome"], outcome)
                self.assertEqual(problem_attempt.assess_attempt(records, self.artifacts), [])

    def test_unaccepted_unknown_and_nonmathematical_acceptances_are_refused(self):
        for status, outcome in (("invalidated", "proof"), ("hypothesis", "proof"),
                                ("accepted", "hypothesis"), ("accepted", "reduction")):
            with self.subTest(status=status, outcome=outcome):
                native = build_objective_native(outcome)
                native["acceptances"][0]["status"] = status
                item = self.deliver_result(native)
                if status == "accepted" and outcome == "reduction":
                    item["acceptance_id"] = "absent"
                with self.assertRaises(ResearchError) as caught:
                    problem_evidence.validate_result(self.store.snapshot()["records"], item)
                self.assertEqual(caught.exception.code, "problem_result_missing")
                self.assertEqual(caught.exception.details, {key: item[key] for key in (
                    "native_attempt_digest", "acceptance_id")})

    def test_result_from_an_unrelated_store_is_unbound(self):
        other = ProblemCase()
        other.setUp()
        self.addCleanup(other.doCleanups)
        other.initialize_problem()
        item = build_objective_result(put_native_attempt(other, build_objective_native()))
        with self.assertRaises(ResearchError) as caught:
            problem_evidence.validate_result(self.store.snapshot()["records"], item)
        self.assertEqual(caught.exception.code, "problem_result_unbound")
        self.assertEqual(caught.exception.details, {"native_attempt_digest": item["native_attempt_digest"]})

    def test_result_requires_a_current_pointer_to_the_same_attack_and_objective(self):
        item = self.deliver_result()
        records = self.store.snapshot()["records"]
        pointer_id = next(iter(records["native_attempt_current"]))
        for variant in ("absent-pointer", "absent-current-snapshot", "other-attack", "other-objective"):
            with self.subTest(variant=variant):
                changed = copy.deepcopy(records)
                if variant == "absent-pointer":
                    changed.pop("native_attempt_current")
                elif variant == "absent-current-snapshot":
                    changed["native_attempt_current"][pointer_id]["native_attempt_digest"] = "absent"
                else:
                    native = copy.deepcopy(changed["native_attempt"][item["native_attempt_digest"]])
                    native["attack_root" if variant == "other-attack" else "objective_id"] = "unrelated"
                    changed["native_attempt"]["other"] = native
                    changed["native_attempt_current"][pointer_id]["native_attempt_digest"] = "other"
                with self.assertRaises(ResearchError) as caught:
                    problem_evidence.validate_result(changed, item)
                self.assertEqual(caught.exception.code, "problem_result_unbound")

    def test_superseded_result_has_its_own_refusal_and_redelivery_restores_it(self):
        native = build_objective_native()
        first = self.deliver_result(native)
        later = copy.deepcopy(native)
        later["totals"]["moves"] -= 1
        second = self.deliver_result(later)
        with self.assertRaises(ResearchError) as caught:
            problem_evidence.validate_result(self.store.snapshot()["records"], first)
        self.assertEqual(caught.exception.code, "problem_result_superseded")
        self.assertEqual(caught.exception.details, {"native_attempt_digest": first["native_attempt_digest"],
            "latest_native_attempt_digest": second["native_attempt_digest"]})
        self.deliver_result(native)
        self.assertEqual(problem_evidence.validate_result(self.store.snapshot()["records"], first)["id"], "acceptance-1")

    def test_attack_roots_and_objectives_have_independent_current_results(self):
        first = self.deliver_result()
        for key in ("attack_root", "objective_id"):
            with self.subTest(key=key):
                other = build_objective_native()
                other[key] += "-other"
                second = self.deliver_result(other)
                for item in (first, second):
                    self.assertEqual(problem_evidence.validate_result(self.store.snapshot()["records"], item)["id"],
                                     "acceptance-1")

    def test_extension_reason_is_required_for_each_total_above_the_normal_limits(self):
        for name, limit in (("moves", 24), ("runs", 24), ("workers", 1), ("timeout_seconds", 300)):
            with self.subTest(total=name):
                native = build_objective_native()
                native["totals"][name] = limit + 1
                item = self.deliver_result(native)
                for reason in (None, "", " \t "):
                    item["extension_reason"] = reason
                    with self.assertRaises(ResearchError) as caught:
                        problem_evidence.validate_result(self.store.snapshot()["records"], item)
                    self.assertEqual(caught.exception.code, "problem_result_extension_unexplained")
                    self.assertEqual(caught.exception.details, {"totals": native["totals"], "exceeded": [name]})
                item["extension_reason"] = "One further check establishes the remaining boundary case."
                self.assertEqual(problem_evidence.validate_result(self.store.snapshot()["records"], item)["id"],
                                 "acceptance-1")

    def test_totals_at_the_limits_and_unaccounted_elapsed_time_need_no_extension(self):
        native = build_objective_native()
        native["totals"]["elapsed_seconds"] = 1000
        native["totals"]["historical_usage"] = "unknown"
        for reason in (None, "", " "):
            item = self.deliver_result(native, extension_reason=reason)
            self.assertEqual(problem_evidence.validate_result(self.store.snapshot()["records"], item)["id"],
                             "acceptance-1")

    def test_new_shape_requires_exact_fields_and_nonempty_identifiers_and_purpose(self):
        item = self.deliver_result()
        variants = [None, 4, [], dict(item, attempt_id="attempt-1"), dict(item, extra="unrecognized")]
        variants.extend({key: value for key, value in item.items() if key != missing}
                        for missing in ("native_attempt_digest", "acceptance_id", "purpose", "extension_reason"))
        variants.extend(dict(item, **{key: value}) for key in ("native_attempt_digest", "acceptance_id", "purpose")
                        for value in (None, "", "  ", 4))
        variants.extend(dict(item, extension_reason=value) for value in (False, 4, []))
        for variant in variants:
            with self.subTest(item=variant):
                with self.assertRaises(ResearchError) as caught:
                    problem_evidence.validate_result(self.store.snapshot()["records"], variant)
                self.assertEqual(caught.exception.code, "invalid_input")

    def test_legacy_result_does_not_gain_snapshot_freshness_or_extension_requirements(self):
        native = build_native_attempt(self.statement["statement"], [build_acceptance()], {})
        native["totals"]["moves"] = 25
        identifier = put_native_attempt(self, native)
        attempt = build_attempt(native_attempt_digest=identifier,
                                proportion={"extension_reason": "A final run checks the boundary."})
        self.mutate(problem_attempt.record_attempt, attempt)
        item = {"kind": "result", "attempt_id": "attempt-1", "acceptance_id": "acceptance-1"}
        later = copy.deepcopy(native)
        later["totals"]["moves"] = 26
        put_native_attempt(self, later)
        self.assertEqual(problem_evidence.validate_result(self.store.snapshot()["records"], item), native["acceptances"][0])

    def test_legacy_value_and_attempt_keep_the_0820_validation_digests_and_projection_bytes(self):
        fixture = json.loads((Path(__file__).parent / "fixtures" / "problem_result_0820.json").read_text(encoding="utf-8"))
        records = fixture["records"]
        before = copy.deepcopy(records)
        value = records["problem_value"]["value-1"]
        problem_evidence.validate_value(records, self.artifacts, value)
        problem_attempt.validate_attempt(records, self.artifacts, records["problem_attempt"]["attempt-1"])
        self.assertEqual(digest(value), fixture["value_digest"])
        for name, project in (("binding", problem_evidence.describe_binding), ("evidence", problem_evidence.describe_evidence)):
            projected = project(records, self.artifacts)
            actual = json.dumps(projected, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            self.assertEqual(actual, fixture[name + "_bytes"].encode("utf-8"))
        self.assertEqual(self.load_problem_module().compute_problem_evidence_digest(records, self.artifacts),
                         fixture["evidence_digest"])
        self.assertEqual(records, before)

    def test_objective_projection_carries_both_claims_purpose_and_extension_without_an_attempt(self):
        native = build_objective_native("counterexample")
        native["totals"]["runs"] = 25
        item = self.deliver_result(native, extension_reason="A final run checks the counterexample.")
        self.mutate(self.load_problem_module().record_value, self.build_value([item]))
        records = self.store.snapshot()["records"]
        expected = {key: item[key] for key in ("native_attempt_digest", "acceptance_id", "purpose", "extension_reason")}
        expected.update(objective_claim=native["contract"]["original_claim"], claim=native["checkpoints"][0]["claim"])
        binding = problem_evidence.describe_binding(records, self.artifacts)
        delivered = problem_evidence.describe_evidence(records, self.artifacts)
        self.assertEqual(binding["results"], [dict(expected, digest=digest(native["acceptances"][0]))])
        self.assertEqual(delivered["results"], [dict(expected, outcome="counterexample", standard="reviewed")])
        self.assertIsNone(delivered["attempt"])
        self.assertEqual(delivered["claim"]["statement"], self.statement["statement"])

    def test_different_purposes_and_extension_reasons_are_preserved_and_repeats_are_deduplicated(self):
        item = self.deliver_result()
        other = dict(item, purpose="Locate the necessary boundary.")
        explained = dict(item, extension_reason="The normal limits were sufficient.")
        self.mutate(self.load_problem_module().record_value, self.build_value([item, other, explained, item]))
        results = problem_evidence.describe_evidence(self.store.snapshot()["records"], self.artifacts)["results"]
        self.assertEqual(len(results), 3)
        self.assertEqual({(row["purpose"], row["extension_reason"]) for row in results}, {
            (item["purpose"], None), (other["purpose"], None), (explained["purpose"], explained["extension_reason"])})

    def test_objective_results_still_project_when_a_legacy_attempt_belongs_to_a_previous_statement(self):
        native = build_native_attempt(self.statement["statement"], [build_acceptance()], {})
        self.mutate(problem_attempt.record_attempt, build_attempt(native_attempt_digest=put_native_attempt(self, native)))
        legacy = {"kind": "result", "attempt_id": "attempt-1", "acceptance_id": "acceptance-1"}
        item = self.deliver_result()
        self.mutate(self.load_problem_module().record_value, self.build_value([legacy, item]))
        self.select_statement(identifier="statement-2", statement="A second exact question.")
        for project in (problem_evidence.describe_binding, problem_evidence.describe_evidence):
            result = project(self.store.snapshot()["records"], self.artifacts)
            self.assertIsNone(result["attempt"])
            self.assertEqual(len(result["results"]), 1)
            self.assertEqual(result["results"][0]["native_attempt_digest"], item["native_attempt_digest"])


class ProblemObjectiveResultReviewTests(ProblemDeliveryCase):
    def test_both_shapes_arrive_in_the_result_review_packet_and_its_coverage(self):
        self.prepare_ready(solver=True)
        native = build_native_attempt(self.selected["statement"], [build_acceptance()], {})
        self.mutate(problem_attempt.record_attempt, build_attempt("result-attempt", put_native_attempt(self, native)))
        legacy = {"kind": "result", "attempt_id": "result-attempt", "acceptance_id": "acceptance-1"}
        objective = build_objective_native("counterexample")
        item = build_objective_result(put_native_attempt(self, objective))
        self.mutate(self.load_problem_module().record_value, self.build_value([legacy, item], "both-results"))
        for reviewer in ("first", "second"):
            identifier = "both-results-" + reviewer
            self.observe("result", reviewer, identifier=identifier)
            assignment = self.store.snapshot()["records"]["review_assignment"][identifier]
            packet = json.loads(self.artifacts.read(assignment["packet"]))
            results = packet["problem_evidence"]["results"]
            self.assertEqual(len(results), 2)
            old = next(row for row in results if "attempt_id" in row)
            self.assertEqual(old, {"attempt_id": "result-attempt", "acceptance_id": "acceptance-1",
                "claim": native["checkpoints"][0]["claim"], "outcome": "reduction", "standard": "reviewed"})
            new = next(row for row in results if "native_attempt_digest" in row)
            self.assertEqual(new, dict({key: item[key] for key in (
                "native_attempt_digest", "acceptance_id", "purpose", "extension_reason")},
                objective_claim=objective["contract"]["original_claim"], claim=objective["checkpoints"][0]["claim"],
                outcome="counterexample", standard="reviewed"))
        assessment = research_decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)
        self.assertTrue(assessment["ready"], assessment["obligations"])
