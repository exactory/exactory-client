"""A rejected strategy identifies the nested input the author must repair."""

from research_harness import strategy
from research_harness.errors import ResearchError
from strategy_fixtures import StrategyCase


class StrategyDiagnosticTests(StrategyCase):
    def check_prerequisite_repair(self, candidate_index, prerequisite_index):
        self.mutate(strategy.record_intent, self.intent())
        value = self.dossier()
        valid = {"description": "Check the held control values.",
                 "end_condition": "The control matches its stated value.",
                 "exit_condition": "A mismatch prevents the proposed deciding test."}
        prerequisites = [dict(valid) for _ in range(prerequisite_index)]
        prerequisites.append("Check the held control values.")
        value["candidates"][candidate_index]["next_test"]["prerequisites"] = prerequisites
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as rejected:
            self.mutate(strategy.record_strategy, value)
        self.assertEqual(rejected.exception.code, "invalid_strategy")
        details = rejected.exception.details or {}
        self.assertEqual(details.get("path"),
                         f"/candidates/{candidate_index}/next_test/prerequisites/{prerequisite_index}")
        self.assertEqual(details.get("received_type"), "string")
        self.assertEqual(set(details.get("missing_fields", [])), set(valid))
        self.assertEqual(self.store.snapshot(), before)
        prerequisites[prerequisite_index] = valid
        accepted = self.mutate(strategy.record_strategy, value)["result"]
        self.assertEqual(accepted["payload"]["candidates"][candidate_index]["next_test"]["prerequisites"], prerequisites)

    def test_prerequisite_error_identifies_the_field_and_the_repaired_strategy_is_recordable(self):
        self.check_prerequisite_repair(0, 0)

    def test_prerequisite_error_preserves_the_actual_candidate_and_list_indexes(self):
        self.check_prerequisite_repair(1, 1)
