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
        self.attack_root = Path(self._tmp.name).resolve() / "attack"
        self.attack_root.mkdir()
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


def admit_existing_workspace(attack_root, slug="sample", problem=None):
    """Admit a genuine native fixture without creating its plan or ranking."""
    import hashlib
    from search_controller.service import Controller
    from tests.search_fixtures import contract, proposal, review, digest
    controller = Controller(attack_root, FIXTURE_STRATEGIES)
    if controller.store.tree_path.exists():
        return controller
    workspace = attack_root / slug
    problem = make_problem() if problem is None else problem
    (workspace / "problem.json").write_text(json.dumps(problem))
    write_study(workspace, "problem")
    (workspace / "novelty.md").write_text("This native validation fixture makes no novelty claim.\n")
    original = contract()
    original["original_claim"].update(statement=problem["claim"], quantifiers="The exact quantifiers of the supplied native fixture",
                                      scope={"kind": "named", "name": "native fixture domain"})
    original["root_attack_slug"] = slug
    candidate = proposal()
    candidate.update(attack_slug=slug, claim=original["original_claim"], method=OPENING)
    candidate["anchor"]["digest"] = digest(original)
    candidate["studies"]["strategies"] = []
    inputs = []
    for name, relative in [("problem", "study/problem.md"), ("novelty", "novelty.md")] + [(name, "study/" + name + ".md") for name in ALL_YES]:
        path = workspace / relative
        if not path.exists() or not path.read_text().strip():
            write_study(workspace, name)
        value = hashlib.sha256(path.read_bytes()).hexdigest()
        inputs.append({"path": slug + "/" + relative, "digest": value, "kind": "artifact"})
        if name in {"problem", "novelty"}:
            candidate["studies"][name] = value
        else:
            candidate["studies"]["strategies"].append({"method": name, "digest": value})
    controller.command("init", {"contract": original}, 0, "initialize")
    controller.command("propose", {"proposal": candidate, "inputs": inputs}, 1, "proposal")
    controller.command("review", {"proposal_id": "proposal-000001", "review": review(candidate), "inputs": []}, 2, "review")
    controller.command("admit", {}, 3, "admit", "proposal-000001")
    return controller


def reserved_journal(argv, attack_root):
    """Check native input rules read-only, then reserve before a valid append."""
    from search_controller.errors import SearchError
    from search_controller.service import Controller
    args = attack.build_parser().parse_args(["--strategies", str(FIXTURE_STRATEGIES), "--attack-root", str(attack_root)] + list(argv))
    try:
        line = attack.validated_journal_line(args)
        controller = Controller(attack_root, FIXTURE_STRATEGIES)
        state = controller.status()
        node = next(node for node in state["nodes"].values() if node["attack_slug"] == args.slug)
        if not state["control"]["pending_moves"]:
            records = state["control"]["strategy_refresh"]["assessments"]
            selected = {tuple(row[key] for key in ["node_id", "strategy"])
                        for row in records[-1]["assessment"]["strategy_order"]} if records else None
            if (controller.command("strategy-context", {}, None, None)["required"]
                    or selected is not None and (node["id"], line["strategy"]) not in selected):
                from tests.strategy_refresh_support import reassess_fixture
                reassess_fixture(controller, {(node["id"], line["strategy"])})
                state = controller.status()
            spec = {key: line[key] for key in ["strategy", "entry", "pass", "trigger_features", "step_cites"]}
            controller.command("begin", spec, state["revision"], "fixture-begin-{}-{}".format(node["id"], line["move"]), node["id"])
    except attack.ValidationError as error:
        return 1, "", "".join(problem + "\n" for problem in error.problems)
    except SearchError as error:
        return 1, "", json.dumps({"error": {"code": error.code, "message": error.message, "details": error.details}}) + "\n"
    return run(argv, attack_root)


class AdmittedWorkspaceTest(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.controller = admit_existing_workspace(self.attack_root, self.slug)

    def run_cli(self, *argv):
        if list(argv[:2]) == ["journal", "add"]:
            return reserved_journal(argv, self.attack_root)
        return run(argv, self.attack_root)


def admit_native_child(controller, slug="hypothesis", parent_slug="sample"):
    """Create a child only through the reviewed controller filesystem effect."""
    import copy
    from tests.search_fixtures import review
    from tests.search_execution_support import invoke
    state = controller.status()
    parent = next(node for node in state["nodes"].values() if node["attack_slug"] == parent_slug)
    candidate = copy.deepcopy(state["proposals"][parent["proposal_id"]]["record"])
    candidate.update(attack_slug=slug, native_parent=parent["id"])
    number = state["next_ids"]["proposal"]
    identity = "proposal-{:06d}".format(number)
    invoke(controller, "propose", {"proposal": candidate, "inputs": []}, None)
    invoke(controller, "review", {"proposal_id": identity, "review": review(candidate), "inputs": []}, None)
    invoke(controller, "admit", {}, identity)
    return controller.root / slug


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


OPENING = "attack-the-negative-side"


def make_move(move, pass_number=1, failed=False, **overrides):
    """A move under the opening strategy, which is where an attack starts: the first
    strategy of the ranking, and with every fixture verdict yes that is the first by name."""
    record = {
        "move": move,
        "pass": pass_number,
        "walk": OPENING,
        "costs_paid": [],
        "strategy": OPENING,
        "entry": "test-strengthenings-by-counterexample",
        "trigger_features": ["shape.target_quantity"],
        "action": "tested the strengthening on small instances",
        "steps": [],
        "output": "the strengthening holds on every instance searched",
        "failure_signal_fired": failed,
        "problem_changed": False,
        "closes": False,
        "step_cites": [],
    }
    record.update(overrides)
    return record


def write_journal(workspace, moves):
    if (workspace.parent / ".search/tree.json").exists():
        existing = attack.read_journal(workspace)
        for actual, expected in zip(existing, moves):
            if {key: value for key, value in actual.items() if key != "problem_digest"} != expected:
                raise AssertionError("A controlled fixture must not rewrite an acknowledged journal prefix")
        if len(existing) > len(moves):
            raise AssertionError("A controlled fixture must not truncate its journal")
        for move in moves[len(existing):]:
            status, out, err = reserved_journal(["journal", "add", workspace.name, "--json", json.dumps(move)], workspace.parent)
            if status:
                raise AssertionError(err)
        return
    (workspace / "journal.jsonl").write_text("".join(json.dumps(m) + "\n" for m in moves))


def write_ranking(test, cites=("shape.objects",)):
    """A ranking over every opening the current plan emitted, in the plan's own order."""
    order = [
        {"strategy": row["strategy"], "cites": list(cites), "reason": "the record supports this order"}
        for row in test.read_json("openings.json")["openings"]
    ]
    test.write_json("ranking.json", {"generated_from": "openings.json", "order": order})


def prepare_plan(test, verdicts=None):
    """A workspace carrying a problem, a study, a plan, and a ranking over that plan."""
    test.write_json("problem.json", make_problem())
    write_study(test.workspace, "problem")
    test.write_json("preconditions.json", make_preconditions(verdicts or ALL_YES))
    admit_existing_workspace(test.attack_root, test.slug)
    test.run_cli("plan", test.slug)
    write_ranking(test)


def write_study(workspace, name, text=None):
    """A study record the harness accepts; name is "problem" or a strategy name."""
    (workspace / "study").mkdir(exist_ok=True)
    if text is None:
        text = "queries: one per first-tier source for " + name + "\n"
    (workspace / "study" / ("%s.md" % name)).write_text(text)


FAKE_BIN = Path(__file__).parent / "fixtures" / "bin"


class StepTest(WorkspaceTest):
    """A workspace with one deterministic step directory and the fake tools first on PATH."""

    step_name = "formal-check-1"

    def setUp(self):
        super().setUp()
        from tests.search_execution_support import admit_workspace
        self.controller = admit_workspace(self.attack_root, self.slug)
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

    def prepare_verification(self, kind, step_name=None):
        from tests.search_execution_support import begin_spec, invoke, review_native_inputs
        step_name = step_name or self.step_name
        if not self.controller.status()["control"]["pending_moves"]:
            invoke(self.controller, "begin", begin_spec())
        step = self.workspace / "deterministic" / step_name
        description = step / "step.json"
        if description.exists():
            value = json.loads(description.read_text())
            value.setdefault("requested_type", "True")
            value["environment"] = {key: text for key, text in os.environ.items() if key.startswith("FAKE_LAKE_")}
            value["environment"]["PATH"] = os.environ["PATH"]
            description.write_text(json.dumps(value))
        try:
            review_native_inputs(self.controller, self.slug, step_name, kind)
        except (attack.ValidationError, FileNotFoundError):
            # Invalid-project tests exercise the same native preflight directly.
            pass

    def run_cli(self, *argv):
        if argv and argv[0] == "verify":
            self.prepare_verification(argv[1], argv[3])
        return super().run_cli(*argv)
