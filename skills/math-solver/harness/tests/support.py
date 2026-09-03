"""Shared helpers for the harness tests. Test-only code."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import attack

FIXTURE_STRATEGIES = Path(__file__).parent / "fixtures" / "strategies"


def run(argv, attack_root):
    """Run the CLI in-process; return (exit status, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        status = attack.main(
            ["--strategies", str(FIXTURE_STRATEGIES), "--attack-root", str(attack_root)]
            + list(argv)
        )
    return status, out.getvalue(), err.getvalue()


def make_problem():
    """A problem.json that passes check-problem."""
    return {
        "claim": "For every n there is a prime between n and 2n.",
        "quadruple": {
            "statement": "the claim as stated",
            "stage": "the integers",
            "direction": "true",
            "mode": "existence",
        },
        "shape": {key: "read from the statement" for key in attack.SHAPE_KEYS},
        "known": ["Bertrand's postulate, Chebyshev 1852"],
    }


class WorkspaceTest(unittest.TestCase):
    """A test with a fresh attack root and an initialised workspace."""

    slug = "sample"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.attack_root = Path(self._tmp.name)
        self.workspace = self.attack_root / self.slug
        run(["init", self.slug], self.attack_root)

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *argv):
        return run(argv, self.attack_root)

    def write_json(self, name, data):
        (self.workspace / name).write_text(json.dumps(data))

    def read_json(self, name):
        return json.loads((self.workspace / name).read_text())


# Questions 1 and 2 of every fixture strategy are required, question 3 is optional.
ANSWERS_FOR_VERDICT = {
    "yes": ["yes", "yes", "yes"],
    "unknown": ["yes", "unknown", "yes"],
    "no": ["yes", "no", "yes"],
}


def make_preconditions(verdicts):
    """A preconditions.json whose answers agree with each verdict."""
    return {
        name: {
            "verdict": verdict,
            "answers": [
                {"question": number, "answer": answer, "cites": "shape.objects"}
                for number, answer in enumerate(ANSWERS_FOR_VERDICT[verdict], start=1)
            ],
        }
        for name, verdict in verdicts.items()
    }


ALL_YES = {
    "ladder-the-parameter": "yes",
    "solve-the-model-world-first": "yes",
    "attack-the-negative-side": "yes",
    "prove-the-barrier-first": "yes",
    "reduce-to-a-finite-computation": "yes",
}


RANK_ONE = (
    "attack-the-negative-side+ladder-the-parameter"
    "+reduce-to-a-finite-computation+solve-the-model-world-first"
)


def make_move(move, pass_number=1, failed=False, **overrides):
    """A move under the first strategy of the rank-one composition, which is where an attack starts."""
    record = {
        "move": move,
        "pass": pass_number,
        "composition": RANK_ONE,
        "costs_paid": [],
        "strategy": "attack-the-negative-side",
        "entry": "test-strengthenings-by-counterexample",
        "trigger_features": ["shape.target_quantity"],
        "action": "tested the strengthening on small instances",
        "steps": [],
        "output": "the strengthening holds on every instance searched",
        "failure_signal_fired": failed,
        "problem_changed": False,
        "closes": False,
    }
    record.update(overrides)
    return record


def write_journal(workspace, moves):
    (workspace / "journal.jsonl").write_text("".join(json.dumps(m) + "\n" for m in moves))


def write_ranking(test, cites=("shape.objects",)):
    """A ranking over every composition the current plan emitted, in the plan's own order."""
    order = [
        {"composition": row["id"], "cites": list(cites), "reason": "the record supports this order"}
        for row in test.read_json("compositions.json")["compositions"]
    ]
    test.write_json("ranking.json", {"generated_from": "compositions.json", "order": order})


def prepare_plan(test, verdicts=None):
    """A workspace carrying a problem, a study, a plan, and a ranking over that plan."""
    test.write_json("problem.json", make_problem())
    write_study(test.workspace, "problem")
    test.write_json("preconditions.json", make_preconditions(verdicts or ALL_YES))
    test.run_cli("plan", test.slug)
    write_ranking(test)


def write_study(workspace, name, text="queries: one per first-tier source\n"):
    """A study record the harness accepts; name is "problem" or a strategy name."""
    (workspace / "study").mkdir(exist_ok=True)
    (workspace / "study" / ("%s.md" % name)).write_text(text)


FAKE_BIN = Path(__file__).parent / "fixtures" / "bin"


class StepTest(WorkspaceTest):
    """A workspace with one deterministic step directory and the fake tools first on PATH."""

    step_name = "formal-check-1"

    def setUp(self):
        super().setUp()
        self.step_dir = self.workspace / "deterministic" / self.step_name
        self.step_dir.mkdir()
        self.log = self.attack_root / "lake.log"
        self.set_env(
            PATH=str(FAKE_BIN) + os.pathsep + os.environ["PATH"],
            FAKE_LAKE_LOG=str(self.log),
            FAKE_LAKE_AXIOMS="propext, Classical.choice, Quot.sound",
        )

    def set_env(self, **values):
        patcher = mock.patch.dict(os.environ, values)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_step_file(self, name, content):
        path = self.step_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def read_result(self):
        return json.loads((self.step_dir / "result.json").read_text())
