"""A valid constructive entry remains active after its own mode move."""

import contextlib
import io
import json
from pathlib import Path
import re
import shutil
import unittest

import attack
from search_controller.errors import SearchError
from search_controller.strategy_refresh_io import plan_defects
from tests.search_execution_support import admit_workspace, invoke
from tests.strategy_refresh_support import assessment_spec
from tests.search_fixtures import digest
from tests.support import FIXTURE_STRATEGIES, WorkspaceTest, make_move, write_ranking, write_study

STRATEGY = "make-the-proof-constructive"
ENTRY = "pin-the-extremal-candidate-first"


class ConstructiveContinuationTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.strategies = self.attack_root / "strategies"
        shutil.copytree(FIXTURE_STRATEGIES, self.strategies)
        source = Path(attack.__file__).resolve().parent.parent / "strategies" / (STRATEGY + ".md")
        shutil.copy2(source, self.strategies / source.name)

        def configure(candidate):
            candidate["method"] = STRATEGY
            candidate["studies"]["strategies"][0]["method"] = STRATEGY

        self.controller = admit_workspace(self.attack_root, configure=configure)
        self.controller.strategies_dir = self.strategies
        write_study(self.workspace, STRATEGY, "The finite seed and its amplification need an explicit construction.")
        self.set_preconditions(False)
        self.plan()

    def run_cli(self, *arguments):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            status = attack.main(["--strategies", str(self.strategies), "--attack-root", str(self.attack_root)]
                                 + list(arguments))
        return status, output.getvalue(), error.getvalue()

    def set_preconditions(self, constructed):
        preconditions = self.read_json("preconditions.json")
        preconditions[STRATEGY] = {"verdict": "no" if constructed else "yes", "answers": [
            {"question": number, "answer": "no" if constructed and number == 1 else "yes",
             "cites": "quadruple.mode" if number == 1 else "shape.objects"} for number in range(1, 8)]}
        self.write_json("preconditions.json", preconditions)

    def plan(self):
        status, _, error = self.run_cli("plan", self.slug)
        self.assertEqual((status, error), (0, ""))
        write_ranking(self)
        ranking = self.read_json("ranking.json")
        ranking["order"].sort(key=lambda row: row["strategy"] != STRATEGY)
        self.write_json("ranking.json", ranking)

    def construct(self, failed=False):
        invoke(self.controller, "begin", {"strategy": STRATEGY, "entry": ENTRY, "pass": 1,
                                          "trigger_features": ["shape.objects"], "step_cites": []})
        problem = self.read_json("problem.json")
        problem["quadruple"]["mode"] = "construction"
        self.write_json("problem.json", problem)
        line = make_move(1, strategy=STRATEGY, entry=ENTRY, walk=STRATEGY,
                         problem_changed=True, failure_signal_fired=failed, trigger_features=["shape.objects"])
        status, _, error = self.run_cli("journal", "add", self.slug, "--json", json.dumps(line))
        self.assertEqual((status, error), (0, ""))
        self.set_preconditions(True)

    def reassess(self):
        description = self.controller.command("strategy-context", {}, None, None)
        spec = assessment_spec(description)
        spec["review"]["claim_digest"] = digest(self.controller.status()["contract"]["original_claim"])
        invoke(self.controller, "reassess", spec, None)

    def test_entry_construction_replan_reassessment_and_continuation(self):
        self.construct()
        self.plan()
        description = self.controller.command("strategy-context", {}, None, None)
        self.assertTrue(description["native_eligibility"][0]["eligible"], description["native_eligibility"])
        self.reassess()
        invoke(self.controller, "begin", {"strategy": STRATEGY, "entry": ENTRY, "pass": 1,
                                          "trigger_features": ["shape.objects"], "step_cites": []})
        line = make_move(2, strategy=STRATEGY, entry=ENTRY, walk=STRATEGY, trigger_features=["shape.objects"])
        self.assertEqual(self.run_cli("journal", "add", self.slug, "--json", json.dumps(line))[0], 0)
        self.assertEqual(self.controller.status()["accounts"]["account-000001"]["used_moves"], 2)

    def test_legacy_active_entry_is_recovered_from_its_exact_native_reservation(self):
        self.construct()
        historical = self.read_json("openings.json")
        historical.pop("strategy_entries")
        self.write_json("openings.json", historical)
        self.plan()
        description = self.controller.command("strategy-context", {}, None, None)
        self.assertTrue(description["native_eligibility"][0]["eligible"], description["native_eligibility"])
        self.reassess()
        self.assertEqual(self.controller.status()["accounts"]["account-000001"]["used_moves"], 1)

    def test_active_entry_metadata_is_reauthenticated_against_the_reservation(self):
        self.construct()
        self.plan()
        planned = self.read_json("openings.json")
        planned["strategy_entries"][STRATEGY]["mode"] = "computation"
        self.write_json("openings.json", planned)
        state = self.controller.status()
        with self.assertRaises(SearchError) as caught:
            plan_defects(self.controller, state["nodes"]["node-000001"], STRATEGY)
        self.assertEqual(caught.exception.code, "strategy_plan_stale")
        self.assertEqual(self.controller.status(), state)

    def test_new_entry_already_in_construction_is_ineligible(self):
        problem = self.read_json("problem.json")
        problem["quadruple"]["mode"] = "construction"
        self.write_json("problem.json", problem)
        self.set_preconditions(True)
        self.plan()
        state = self.controller.status()
        self.assertTrue(plan_defects(self.controller, state["nodes"]["node-000001"], STRATEGY))

    def test_fail_ends_the_continuation(self):
        self.construct()
        self.assertEqual(self.run_cli("fail", self.slug, STRATEGY)[0], 0)
        node = self.controller.status()["nodes"]["node-000001"]
        self.assertTrue(plan_defects(self.controller, node, STRATEGY))
        self.assertNotIn(STRATEGY, [row["strategy"] for row in self.read_json("openings.json")["openings"]])

    def test_an_entry_signal_on_a_move_does_not_end_the_continuation(self):
        self.construct(failed=True)
        self.plan()
        self.assertIn(STRATEGY, [row["strategy"] for row in self.read_json("openings.json")["openings"]])

    def test_changed_claim_cannot_continue(self):
        self.construct()
        node = self.controller.status()["nodes"]["node-000001"]
        problem = self.read_json("problem.json")
        problem["claim"] = "A different theorem."
        self.write_json("problem.json", problem)
        self.plan()
        self.assertTrue(plan_defects(self.controller, node, STRATEGY))
        self.assertNotIn(STRATEGY, [row["strategy"] for row in self.read_json("openings.json")["openings"]])

    def test_continuation_requires_the_current_reviewed_plan(self):
        self.construct()
        node = self.controller.status()["nodes"]["node-000001"]
        self.assertTrue(plan_defects(self.controller, node, STRATEGY))
        self.plan()
        self.reassess()
        ranking = self.read_json("ranking.json")
        ranking["order"][0]["reason"] = "Changed after the observed review."
        self.write_json("ranking.json", ranking)
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "begin", {"strategy": STRATEGY, "entry": ENTRY, "pass": 1,
                                              "trigger_features": ["shape.objects"], "step_cites": []})
        self.assertEqual(caught.exception.code, "strategy_reassessment_stale")


class OwnComponentEntryQuestionTests(unittest.TestCase):
    def test_strategies_whose_entry_reads_their_own_moved_component_are_pinned(self):
        directory = Path(attack.__file__).resolve().parent.parent / "strategies"
        reading_own = set()
        for path in sorted(directory.glob("*.md")):
            text = path.read_text()
            front = attack.parse_front_matter(text) or {}
            component = front.get("component")
            if component and re.search(r"\(from: quadruple\.%s;\s*required\)" % re.escape(component), text):
                reading_own.add(front["name"])
        # After its move, each of these strategies still answers its own-component entry question yes, except the
        # constructive one, whose continuation derive_strategy_eligibility reads from the recorded entry. A new
        # member needs the same decision before this set changes.
        self.assertEqual(reading_own, {"attack-the-negative-side", "make-the-proof-constructive",
                                       "reduce-to-a-finite-computation", "replace-existence-with-probability"})
        requirements = attack.parse_question_requirements((directory / (STRATEGY + ".md")).read_text())
        self.assertEqual(requirements, {1: "required", 2: "required", 3: "required", 4: "required",
                                        5: "optional", 6: "optional", 7: "required"})
