"""Every one-node change of a valid problem-value or problem-statement payload ends with an acceptance or a refusal.

The accounts check the shape of every entry before they index the entries or follow a reference (design 0.91.8 D4),
so no change ends with a Python exception. Each fixture is a payload that the command accepts. The changes of a value
payload run through problem_claims.prepare_value, every check of problem-value, on one snapshot of the store and one
evaluation, so that the full reading of the fixture is assessed once and not once for each change.
"""

import copy
import itertools
import sys
import unittest

from payload_changes import REPLACEMENT_VALUES, build_member_additions, find_unrefused_changes
from problem_accounts_fixtures import build_relation
from research_harness import problem_candidates, problem_claims
from research_harness.errors import ResearchError
from research_harness.evaluation import Evaluation
# The account cases are imported as modules, so this module does not collect their tests.
import test_problem_accounts
import test_problem_accounts_tests
from test_research_problem_candidates import build_candidate

# The members by which the entry registry and the walk of the references tell the kind of an entry, each with a value
# of the shape that its own part holds. An entry of another part that holds one of them is refused (0.91.6 review,
# finding 3).
KIND_MEMBERS = {
    "role": "weaker", "relation_id": "use", "check_id": "use", "alternative_ids": [], "relation_ids": [],
    "establishing_ids": ["use"], "separate_grounds": [], "evidence": [], "evidence_ids": [],
    "surplus": {"description": "The exact exponent.", "status": "neither", "evidence_ids": [], "relation_ids": []},
    "conjunction": {"status": "not_known", "description": "No converse is known.", "evidence_ids": []},
    "consequence": {"assertion": "The exact bound.", "instances_or_translation": "Restrict the instances.",
                    "credited_to": "statement", "weaker_derivations": []},
}


class PayloadChangeCase(unittest.TestCase):
    def set_up_case(self, case_class):
        case = case_class()
        self.addCleanup(case.doCleanups)
        case.setUp()
        return case


class ValuePayloadChangeTests(PayloadChangeCase):
    def assert_value_changes(self, case, changes=None):
        records = case.store.snapshot()["records"]
        evaluation = Evaluation.of(records, case.artifacts)
        revision = case.store.revision + 1

        def prepare(changed):
            return problem_claims.prepare_value(records, evaluation, changed, revision)

        prepare(copy.deepcopy(case.value))
        self.assertEqual(find_unrefused_changes(case.value, prepare, changes), [])

    def set_up_gain(self):
        case = self.set_up_case(test_problem_accounts.ProblemAccountsTests)
        case.prepare_dated_gain("2025-11-09T00:00:00+00:00")
        case.value["accounts"]["explanatory_use"][0]["date"] = "2026-02-01"
        return case

    def test_the_value_with_a_place(self):
        self.assert_value_changes(self.set_up_case(test_problem_accounts.ProblemAccountsTests))

    def test_a_value_with_a_gain(self):
        self.assert_value_changes(self.set_up_gain())

    def test_a_value_with_a_replacement(self):
        case = self.set_up_case(test_problem_accounts.ProblemAccountsTests)
        case.value["accounts"]["refuted_conjectures"] = [{"id": "refuted", "statement": "The original unrestricted bound.",
            "identified_by": "author", "sources": [], "evidence_ids": ["use"]}]
        case.value["accounts"]["replacement"] = [{"id": "replacement", "refuted_id": "refuted", "form_id": None,
            "evidence_ids": ["use"], "account": "The question describes the size of the failure."}]
        self.assert_value_changes(case)

    def test_a_value_with_the_parts_of_the_second_tier(self):
        self.assert_value_changes(self.set_up_case(test_problem_accounts_tests.AccountTestCreditTests))

    def test_a_value_with_a_weaker_alternative_and_its_surplus(self):
        case = self.set_up_case(test_problem_accounts.ProblemAccountsTests)
        case.add_weaker()
        accounts = case.value["accounts"]
        accounts["answer_evidence"] = [{"id": "support", "kind": {"kind": "analogy", "description": "A proved analogue."},
            "fact": "The analogue has the exact exponent.", "scope": "The neighbouring setting.", "direction": "for",
            "alternative_id": "weaker", "evidence": [], "evidence_ids": ["use"]}]
        accounts["answer"].update(status="conjectured", text="Yes.", evidence_ids=["support"])
        accounts["relations"].append(build_relation("consequence", standing="established", evidence_ids=["use"],
            consequence={"assertion": "The exact exponent.", "instances_or_translation": "The proved translation.",
                         "credited_to": "statement", "weaker_derivations": []}))
        accounts["relations"][1]["consequence"]["weaker_derivations"] = [{"id": "attempted", "alternative_id": "weaker",
            "status": "not_derived", "reason": "The translation needs the stronger exponent.", "evidence_ids": ["use"]}]
        accounts["alternatives"][0]["surplus"].update(status="supported_and_used", evidence_ids=["support"],
                                                      relation_ids=["consequence"])
        self.assert_value_changes(case)

    def test_a_value_with_a_relation_and_its_consequence(self):
        case = self.set_up_case(test_problem_accounts.ProblemAccountsTests)
        case.add_weaker()
        case.value["accounts"]["relations"].append(build_relation("consequence", consequence={
            "assertion": "The exact bound.", "instances_or_translation": "Restrict the instances.",
            "credited_to": "statement", "weaker_derivations": [{"id": "not-tried", "alternative_id": "weaker",
                "status": "not_attempted", "reason": "No derivation was attempted.", "evidence_ids": []}]}))
        self.assert_value_changes(case)

    def test_an_object_that_holds_a_member_of_another_part_ends_with_a_refusal_or_an_acceptance(self):
        case = self.set_up_gain()
        additions = [change for members in [KIND_MEMBERS] + [dict.fromkeys(KIND_MEMBERS, value) for value in REPLACEMENT_VALUES]
                     for change in build_member_additions(case.value, members)]
        self.assert_value_changes(case, additions)

    def test_a_chain_of_references_longer_than_the_recursion_limit_is_accepted(self):
        case = self.set_up_case(test_problem_accounts.ProblemAccountsTests)
        records = case.store.snapshot()["records"]
        evaluation = Evaluation.of(records, case.artifacts)
        length = sys.getrecursionlimit() + 100
        value = copy.deepcopy(case.value)
        last = value["accounts"]["explanatory_use"][0]["id"]
        value["accounts"]["boundaries"].extend(
            {"id": "chain-%d" % index, "fact": "A fact.", "scope": "A scope.", "argument": None, "evidence": [],
             "evidence_ids": ["chain-%d" % (index + 1) if index + 1 < length else last]}
            for index in range(length))
        problem_claims.prepare_value(records, evaluation, copy.deepcopy(value), case.store.revision + 1)
        # A reference from the end of the chain back to its start is a cycle, at any length.
        value["accounts"]["boundaries"][-1]["evidence_ids"] = ["chain-0"]
        with self.assertRaises(ResearchError) as refusal:
            problem_claims.prepare_value(records, evaluation, value, case.store.revision + 1)
        self.assertEqual(refusal.exception.code, "problem_account_reference_cycle")


class StatementPayloadChangeTests(PayloadChangeCase):
    def test_a_statement_with_every_optional_member(self):
        case = self.set_up_case(test_problem_accounts.ProblemAccountsTests)
        case.mutate(problem_candidates.record_candidate, build_candidate())
        payload = case.build_statement("full-statement", candidate_id="candidate-1",
                                       revises_statement_id=case.statement["id"], neighbours=["A weaker bound."],
                                       formal_statement={"language": "lean4", "text": "theorem bound : True := trivial"})
        payload["statement"] += "\n---\nHere $B(n)$ is the largest size of a finite construction of order $n$."
        case.mutate(problem_claims.record_statement, copy.deepcopy(payload))
        fresh_ids = ("changed-" + str(number) for number in itertools.count())
        refusal_codes = set()

        def record(changed):
            # A saved id skips the checks of a new statement, so each changed payload is a statement of its own.
            if isinstance(changed, dict) and changed.get("id") == payload["id"]:
                changed["id"] = next(fresh_ids)
            try:
                return case.mutate(problem_claims.record_statement, changed)
            except ResearchError as error:
                refusal_codes.add(error.code)
                raise

        self.assertEqual(find_unrefused_changes(payload, record), [])
        # The changes reach the rules of a new statement: a title of four characters is refused by the rule of the post.
        self.assertIn("problem_statement_post_text_invalid", refusal_codes)
        self.assertNotIn("record_conflict", refusal_codes)
