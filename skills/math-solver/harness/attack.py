#!/usr/bin/env python3
"""Deterministic harness for an attack workspace. The contract is SPEC.md."""

import argparse
import collections
import hashlib
import itertools
import json
import os
import re
import subprocess
import sys
from pathlib import Path

MOVES_PER_PASS = 8
PASS_COUNT = 3
MOVE_HARD_CAP = 24
STALL_AFTER_CONSECUTIVE_FAILURES = 3

COMPOSITION_MAX_LENGTH = 4
COMPOSITION_LIMIT = 20

DEFAULT_STRATEGIES_DIR = Path(__file__).resolve().parent.parent / "strategies"
DEFAULT_ATTACK_ROOT = Path("attack")

VERDICTS = ("yes", "no", "unknown")
# What a move under a strategy can take away. Declared per strategy in its front
# matter, paid per move in the journal, and read again when a unit is written.
COSTS = (
    "implication",      # only one direction is proved, so the claim can no longer be refuted this way
    "effectivity",      # a constant stops being computable
    "constructivity",   # existence is proved without an example
    "bound_quality",    # the bound's type degrades
    "axioms",           # the argument borrows strength beyond the recorded base theory
    "object",           # the statement proved is no longer the original one
    "obligations",      # the move adds statements that must themselves be proved
)
STRATEGY_FRONT_KEYS = ("name", "component", "description", "entries", "precedes", "excludes", "costs")
# A declared cost is what a move under the strategy can take away, not what every
# move does, so it never drops the strategy. `journal add` refuses the move that
# pays one of these against the quadruple field it contradicts.
COST_GATES = (
    ("constructivity", "mode", "construction"),
    ("implication", "direction", "false"),
)
PRECONDITION_KEYS = ("verdict", "answers", "note", "failed_after_move")
DIRECTIONS = ("true", "false", "unreachable", "undecided")
MODES = ("existence", "construction", "computation", "certificate", "undecided")

UNIT_FORMS = (
    "conditional-or-special-case",
    "quantitative-improvement",
    "reduction-or-equivalence",
    "barrier",
    "counterexample-or-computational-evidence",
    "new-machinery",
    "survey-or-problem-paper",
    "counterexample",
    "algorithm",
    "formalisation",
    "formal-proof-write-up",
    "full-proof",
    "second-proof",
)

# The forms the ledger and the evidence constrain (CASHOUT.md): a run that did not pass
# makes the unit evidence; a closing form owes nothing and proves the claim itself; a
# form that exhibits the object cannot rest on a non-constructive argument.
EVIDENCE_FORM = "counterexample-or-computational-evidence"
CLOSING_FORMS = ("full-proof", "second-proof")
EXHIBITING_FORMS = ("algorithm", "counterexample")
UNIT_STAMP_FILE = "check-unit.json"
UNIT_DRAFT_FILE = "draft.md"
UNIT_EVALUATION_FILE = "evaluation.md"

FAIL_NOTE = "set to no by fail: the strategy ended in its failure signal"

OUTPUT_HEAD_LINES = 20
AXIOMS_CHECK_FILE = "axioms-check.lean"
AXIOMS_LINE_PATTERN = re.compile(r"depends on axioms: \[(.*)\]")
NO_AXIOMS_LINE_PATTERN = re.compile(r"does not depend on any axioms")
STANDARD_AXIOMS = ("propext", "Classical.choice", "Quot.sound")
NATIVE_EVALUATION_AXIOMS = ("Lean.trustCompiler", "Lean.ofReduceBool", "Lean.ofReduceNat")
NATIVE_DECIDE_AXIOM_MARKER = "._native.native_decide.ax_"

SHAPE_KEYS = (
    "objects",
    "quantifiers",
    "target_quantity",
    "ambient_structure",
    "symmetries",
    "configuration",
    "extremal_candidate",
    "finite_certificates",
    "monotonicity",
    "uniformity_parameter",
    "proof_shape",
    "neighbours",
    "known_bounds",
    "missing_input",
    "base_theory",
)


FRONT_MATTER_PATTERN = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


class ValidationError(Exception):
    """One line per problem; main prints them to stderr and exits 1."""

    def __init__(self, problems):
        super().__init__("\n".join(problems))
        self.problems = list(problems)


def parse_front_matter(text):
    """The `key: value` block between the leading `---` fences; None when there is none."""
    fenced = FRONT_MATTER_PATTERN.match(text)
    if not fenced:
        return None
    front = {}
    for line in fenced.group(1).splitlines():
        key, _, value = line.partition(":")
        front[key.strip()] = parse_front_matter_value(value.strip())
    return front


def parse_front_matter_value(value):
    if value.startswith("[") and value.endswith("]"):
        items = value[1:-1].split(",")
        return [item.strip() for item in items if item.strip()]
    return value


def enumerate_compositions(front_matters, verdicts, quadruple):
    """Rank every admissible ordered selection of the strategies the scan admits.

    A declared cost is what a move under the strategy can take away, not what every
    move does, so it never drops the strategy here. `journal add` refuses the move
    that pays a cost the attack cannot afford.
    """
    candidates = sorted(name for name, verdict in verdicts.items() if verdict != "no")
    admissible = [
        chosen
        for length in range(1, COMPOSITION_MAX_LENGTH + 1)
        for chosen in itertools.permutations(candidates, length)
        if is_admissible(chosen, front_matters, verdicts)
    ]
    admissible.sort(key=lambda chosen: composition_sort_key(chosen, front_matters, verdicts))
    return [
        describe_composition(rank, chosen, front_matters, verdicts)
        for rank, chosen in enumerate(spread_over_leaders(admissible), start=1)
    ]


def spread_over_leaders(admissible):
    """Which compositions survive the cap, in rank order.

    Taken by round: the best composition led by each strategy, then the second
    best led by each, and so on. At equal rank the sort key ends in the names, so
    a plain cut leaves the alphabetically first leader holding every slot and the
    other candidates unreachable through a list the solver takes in rank order.
    A list that fits under the cap is returned unchanged.
    """
    led = collections.Counter()
    rounds = []
    for position, chosen in enumerate(admissible):
        rounds.append((led[chosen[0]], position, chosen))
        led[chosen[0]] += 1
    kept = sorted(rounds)[:COMPOSITION_LIMIT]
    return [chosen for _, position, chosen in sorted(kept, key=lambda row: row[1])]


def is_admissible(chosen, front_matters, verdicts):
    if sum(verdicts[name] == "unknown" for name in chosen) > 1:
        return False
    for earlier, later in itertools.combinations(chosen, 2):
        if later in front_matters[earlier]["excludes"] or earlier in front_matters[later]["excludes"]:
            return False
        if earlier in front_matters[later]["precedes"]:
            return False
    return True


def composition_sort_key(chosen, front_matters, verdicts):
    yes_count = sum(verdicts[name] == "yes" for name in chosen)
    return (-yes_count, -len(distinct_components(chosen, front_matters)), chosen)


def distinct_components(chosen, front_matters):
    return sorted({front_matters[name]["component"] for name in chosen})


def describe_composition(rank, chosen, front_matters, verdicts):
    unknowns = [name for name in chosen if verdicts[name] == "unknown"]
    return {
        "rank": rank,
        "id": "+".join(chosen),
        "strategies": list(chosen),
        "yes": len(chosen) - len(unknowns),
        "unknown": len(unknowns),
        "components": distinct_components(chosen, front_matters),
        "costs": sorted({cost for name in chosen for cost in front_matters[name].get("costs", ())}),
        "assumption": unknowns[0] if unknowns else None,
    }


def run_init(args):
    workspace = args.attack_root / args.slug
    if workspace.exists():
        raise ValidationError(["workspace already exists: %s" % workspace])
    (workspace / "deterministic").mkdir(parents=True)
    (workspace / "units").mkdir()
    (workspace / "study").mkdir()
    (workspace / "novelty.md").write_text("")
    (workspace / "journal.jsonl").write_text("")
    write_json(workspace / "problem.json", build_problem_skeleton())
    print("created %s" % workspace)


def build_problem_skeleton():
    return {
        "claim": "",
        "quadruple": {"statement": "", "stage": "", "direction": "undecided", "mode": "undecided"},
        "shape": {key: "unknown" for key in SHAPE_KEYS},
        "known": [],
    }


def run_check_problem(args):
    problem = read_json(args.attack_root / args.slug / "problem.json")
    defects = list(find_problem_defects(problem))
    if defects:
        raise ValidationError(defects)
    print("problem.json: ok")


def find_problem_defects(problem):
    for key in ("claim", "quadruple", "shape", "known"):
        if key not in problem:
            yield "%s: missing" % key
    if "claim" in problem:
        yield from check_text(problem["claim"], "claim")
    if "quadruple" in problem:
        yield from find_quadruple_defects(problem["quadruple"])
    if "shape" in problem:
        yield from find_text_defects(problem["shape"], "shape", SHAPE_KEYS)
    if "known" in problem:
        yield from find_known_defects(problem["known"])


def find_quadruple_defects(quadruple):
    yield from find_text_defects(quadruple, "quadruple", ("statement", "stage"))
    for key, allowed in (("direction", DIRECTIONS), ("mode", MODES)):
        label = "quadruple.%s" % key
        if key not in quadruple:
            yield "%s: missing" % label
        else:
            yield from check_choice(quadruple[key], label, allowed)


def find_text_defects(record, prefix, keys):
    for key in keys:
        label = "%s.%s" % (prefix, key)
        if key not in record:
            yield "%s: missing" % label
        else:
            yield from check_text(record[key], label)


def find_known_defects(known):
    if not isinstance(known, list):
        yield "known: not a list"
        return
    for index, line in enumerate(known):
        yield from check_text(line, "known[%d]" % index)


def check_text(value, label):
    if not isinstance(value, str):
        yield "%s: not a string" % label
    elif not value.strip():
        yield "%s: empty string" % label


def check_choice(value, label, allowed):
    if value not in allowed:
        yield "%s: %r is not one of %s" % (label, value, ", ".join(allowed))


def run_plan(args):
    workspace = args.attack_root / args.slug
    defects = find_study_defects(workspace, "problem", "the problem-level study", "plan")
    if defects:
        raise ValidationError(defects)
    front_matters = load_strategies(args.strategies)
    requirements = load_question_requirements(args.strategies)
    problem = read_json(workspace / "problem.json")
    preconditions = read_json(workspace / "preconditions.json")
    defects = list(find_precondition_defects(preconditions, front_matters, problem, requirements))
    if defects:
        raise ValidationError(defects)
    verdicts = {name: record["verdict"] for name, record in preconditions.items()}
    quadruple = problem["quadruple"]
    compositions = enumerate_compositions(front_matters, verdicts, quadruple)
    write_json(
        workspace / "compositions.json",
        {
            "generated_from": "preconditions.json",
            "problem_digest": compute_problem_digest(problem),
            "compositions": compositions,
        },
    )
    print_compositions(compositions)


def compute_problem_digest(problem):
    """The digest of problem.json as it stands, so a later command can tell whether it moved."""
    canonical = json.dumps(problem, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def load_strategies(directory):
    """Front matter of every strategy file, keyed by name. A file without front matter is not a strategy."""
    front_matters, defects = {}, []
    for path in sorted(directory.glob("*.md")):
        front = parse_front_matter(path.read_text())
        if not front:
            continue
        defects.extend(find_front_matter_defects(path.name, front))
        front_matters[front.get("name", path.stem)] = front
    if defects:
        raise ValidationError(defects)
    return front_matters


def find_front_matter_defects(file_name, front):
    for key in STRATEGY_FRONT_KEYS:
        if key not in front:
            yield "%s: front matter is missing %s" % (file_name, key)
    for cost in front.get("costs", ()):
        if cost not in COSTS:
            yield "%s: cost %r is not one of %s" % (file_name, cost, ", ".join(COSTS))


def load_question_requirements(directory):
    """Question number to required or optional, per strategy, from its Precondition procedure."""
    requirements = {}
    for path in sorted(directory.glob("*.md")):
        text = path.read_text()
        front = parse_front_matter(text)
        if front:
            requirements[front["name"]] = parse_question_requirements(text)
    return requirements


def parse_question_requirements(text):
    """Read the numbered questions of the Precondition procedure and how each is marked."""
    section = re.search(r"^## Precondition procedure\n(.*?)(?=^## )", text, re.DOTALL | re.MULTILINE)
    requirements, number = {}, None
    for line in section.group(1).splitlines() if section else []:
        opener = re.match(r"^(\d+)\.\s", line)
        if opener:
            number = int(opener.group(1))
        mark = re.search(r"\(from:[^)]*;\s*(required|optional)\)", line)
        if mark and number is not None:
            requirements[number] = mark.group(1)
    return requirements


def find_precondition_defects(preconditions, front_matters, problem, requirements):
    for name in front_matters:
        if name not in preconditions:
            yield "preconditions.json: missing strategy %s" % name
    for name, record in preconditions.items():
        if name not in front_matters:
            yield "preconditions.json: no strategy file for %s" % name
        else:
            yield from find_verdict_defects(name, record, problem, requirements.get(name, {}))


def find_verdict_defects(name, record, problem, questions):
    unknown_keys = [key for key in sorted(record) if key not in PRECONDITION_KEYS]
    if unknown_keys:
        for key in unknown_keys:
            yield "%s: unknown key %s" % (name, key)
        return
    missing = find_missing_keys(record, ("verdict", "answers"))
    if missing:
        yield "%s: missing %s" % (name, ", ".join(missing))
        return
    answer_defects = [
        defect for answer in record["answers"] for defect in find_answer_defects(name, answer, problem)
    ]
    if answer_defects:
        yield from answer_defects
        return
    yield from find_question_defects(name, record["answers"], questions)
    verdict = record["verdict"]
    if verdict not in VERDICTS:
        yield "%s: verdict %r is not one of %s" % (name, verdict, ", ".join(VERDICTS))
        return
    given = [answer["answer"] for answer in record["answers"] if questions.get(answer["question"]) == "required"]
    if verdict == "yes" and any(answer != "yes" for answer in given):
        yield "%s: verdict yes requires every required answer yes" % name
    if verdict == "unknown" and ("no" in given or "unknown" not in given):
        yield "%s: verdict unknown requires no required answer no and at least one required unknown" % name
    if verdict == "no" and "no" not in given and "note" not in record:
        yield "%s: verdict no requires at least one required answer no" % name


def find_question_defects(name, answers, questions):
    """Every question the strategy file asks is answered, and no other."""
    given = {answer["question"] for answer in answers}
    for number in sorted(given - set(questions)):
        yield "%s: question %s is not in the strategy file" % (name, number)
    for number in sorted(set(questions) - given):
        yield "%s: question %s is not answered" % (name, number)


def find_answer_defects(name, answer, problem):
    missing = find_missing_keys(answer, ("question", "answer", "cites"))
    if missing:
        yield "%s answer %s: missing %s" % (name, json.dumps(answer), ", ".join(missing))
        return
    label = "%s question %s" % (name, answer["question"])
    if answer["answer"] not in VERDICTS:
        yield "%s: answer %r is not one of %s" % (label, answer["answer"], ", ".join(VERDICTS))
    if not has_field(problem, answer["cites"]):
        yield "%s: cites %s, which is not in problem.json" % (label, answer["cites"])


def find_missing_keys(record, keys):
    return [key for key in keys if key not in record]


MISSING_FIELD = object()


def read_field(record, dotted_path):
    """The value at a dotted path into a JSON record, or MISSING_FIELD."""
    for part in dotted_path.split("."):
        if not isinstance(record, dict) or part not in record:
            return MISSING_FIELD
        record = record[part]
    return record


def has_field(problem, dotted_path):
    return read_field(problem, dotted_path) is not MISSING_FIELD


def print_compositions(compositions):
    if not compositions:
        print("no admissible composition")
    for composition in compositions:
        print(format_composition(composition))


def format_composition(composition):
    line = "%d. %s  yes=%d unknown=%d components=%s" % (
        composition["rank"],
        " -> ".join(composition["strategies"]),
        composition["yes"],
        composition["unknown"],
        ",".join(composition["components"]),
    )
    if composition["assumption"]:
        line += " assumption=%s" % composition["assumption"]
    return line


RANKING_ROW_KEYS = ("composition", "cites", "reason")


def run_rank(args):
    workspace = args.attack_root / args.slug
    defects = list(find_ranking_defects(workspace))
    if defects:
        raise ValidationError(defects)
    for position, row in enumerate(read_json(workspace / "ranking.json")["order"], start=1):
        print("%d. %s" % (position, row["composition"]))


def find_ranking_defects(workspace):
    """ranking.json orders exactly the compositions the current plan emitted, each row citing what it read."""
    order = read_json(workspace / "ranking.json").get("order")
    problem = read_json(workspace / "problem.json")
    emitted = [row["id"] for row in read_json(workspace / "compositions.json")["compositions"]]
    if not isinstance(order, list):
        yield "ranking.json: order is not a list"
        return
    costs = {row["id"]: row["costs"] for row in read_json(workspace / "compositions.json")["compositions"]}
    ordered = []
    for position, row in enumerate(order, start=1):
        yield from find_ranking_row_defects(position, row, emitted, problem, ordered, costs)
        if isinstance(row, dict):
            ordered.append(row.get("composition"))
    for identifier in emitted:
        if identifier not in ordered:
            yield "ranking.json: composition %s is not ordered" % identifier


def find_ranking_row_defects(position, row, emitted, problem, ordered, costs):
    label = "ranking.json row %d" % position
    if not isinstance(row, dict):
        yield "%s: not an object" % label
        return
    missing = find_missing_keys(row, RANKING_ROW_KEYS)
    if missing:
        yield "%s: missing %s" % (label, ", ".join(missing))
        return
    identifier = row["composition"]
    if identifier not in emitted:
        yield "ranking.json: %s is not in compositions.json" % identifier
    elif identifier in ordered:
        yield "ranking.json: %s is ordered twice" % identifier
    yield from check_text(row["reason"], "%s: reason" % label)
    if not is_str_list(row["cites"]) or not row["cites"]:
        yield "%s: cites nothing" % label
        return
    for citation in row["cites"]:
        if not is_citation(citation, problem):
            yield "%s: cites %s, which is not a problem.json field or a cost" % (label, citation)
        elif citation.startswith("cost:") and citation[len("cost:"):] not in costs.get(identifier, ()):
            yield "%s: cites %s, which no strategy of that composition declares" % (label, citation)


def is_citation(citation, problem):
    """A row cites a field of the problem record, or a cost from the vocabulary."""
    if citation.startswith("cost:"):
        return citation[len("cost:"):] in COSTS
    return has_field(problem, citation)


def run_budget(args):
    workspace = args.attack_root / args.slug
    print_budget(compute_budget(read_journal(workspace), read_failure_window_start(workspace)))


def run_journal_add(args):
    workspace = args.attack_root / args.slug
    move = parse_move_json(args.json)
    moves = read_journal(workspace)
    window_start = read_failure_window_start(workspace)
    problem = read_json(workspace / "problem.json")
    budget = compute_budget(moves, window_start)
    defects = (
        list(find_move_defects(move))
        or list(find_problem_defects(problem))
        or find_study_defects(workspace, move["strategy"], "the strategy's study", "its first move")
        or find_move_cost_defects(move, problem["quadruple"])
        or list(find_step_defects(move["steps"], workspace))
        or list(find_closing_defects(move, problem["quadruple"]))
        or list(find_problem_change_defects(move, moves, budget, workspace, problem))
        or list(find_ranking_defects(workspace))
        or list(
            find_move_flow_defects(
                move, moves, read_ordered_ids(workspace), load_strategies(args.strategies), problem
            )
        )
        or list(find_budget_defects(move, budget))
    )
    if defects:
        raise ValidationError(defects)
    line = dict(move, problem_digest=compute_problem_digest(problem))
    with (workspace / "journal.jsonl").open("a") as journal:
        journal.write(json.dumps(line) + "\n")
    print_budget(compute_budget(moves + [line], window_start))


def find_step_defects(steps, workspace):
    """Every deterministic step the move names has run and been verified."""
    for name in steps:
        step_dir = workspace / "deterministic" / name
        if not step_dir.is_dir():
            yield "move: steps names deterministic/%s, which does not exist" % name
        elif not (step_dir / "result.json").exists():
            yield "move: steps names deterministic/%s, which has no result.json; run verify first" % name


def find_closing_defects(move, quadruple):
    """A move that closes the attack finds the direction and the mode decided."""
    if not move["closes"]:
        return
    for key in ("direction", "mode"):
        if quadruple[key] == "undecided":
            yield "move: closes, and quadruple.%s is undecided" % key


def find_problem_change_defects(move, moves, budget, workspace, problem):
    """`problem_changed` says whether problem.json moved since the last move (or since
    plan), and a new pass runs on a plan made over the problem as it now stands."""
    digest = compute_problem_digest(problem)
    planned = read_json(workspace / "compositions.json")["problem_digest"]
    if moves and "problem_digest" in moves[-1]:
        baseline, since = moves[-1]["problem_digest"], "move %d" % moves[-1]["move"]
    else:
        baseline, since = planned, "plan"
    changed = digest != baseline
    if move["problem_changed"] != changed:
        yield "move: problem_changed is %s, and problem.json %s since %s" % (
            str(move["problem_changed"]).lower(), "changed" if changed else "is unchanged", since
        )
    if moves and move["pass"] > budget["pass"] and digest != planned:
        yield (
            "move: problem.json changed in pass %d, and compositions.json predates the change;"
            " run plan and rank before pass %d" % (budget["pass"], move["pass"])
        )


def find_move_cost_defects(move, quadruple):
    """A cost the move paid that contradicts what the attack requires."""
    return [
        "move: pays %s, and quadruple.%s is %s" % (cost, key, value)
        for cost, key, value in COST_GATES
        if cost in move["costs_paid"] and quadruple.get(key) == value
    ]


def read_ordered_ids(workspace):
    return [row["composition"] for row in read_json(workspace / "ranking.json")["order"]]


def find_move_flow_defects(move, moves, ordered_ids, front_matters, problem):
    """The move sits where the attack stands: in the composition the order gives, under the
    strategy that composition has reached, applying an entry the strategy dispatches, where
    the entry's trigger matched a shape field that carries a value."""
    identifier = move["composition"]
    if identifier not in ordered_ids:
        yield "move: composition %s is not in ranking.json" % identifier
        return
    strategies = identifier.split("+")
    if move["strategy"] not in strategies:
        yield "move: strategy %s is not in composition %s" % (move["strategy"], identifier)
        return
    if move["entry"] not in front_matters[move["strategy"]]["entries"]:
        yield "move: entry %s is not dispatched by %s" % (move["entry"], move["strategy"])
    yield from find_trigger_defects(move["trigger_features"], problem)
    executed = {past["strategy"] for past in moves}
    yield from find_composition_order_defects(identifier, moves, ordered_ids, executed)
    yield from find_strategy_order_defects(move["strategy"], strategies, identifier, moves, executed)


def find_trigger_defects(features, problem):
    if not features:
        yield "move: trigger_features is empty; a move exists only where the entry's trigger matches a shape field"
    for feature in features:
        value = read_field(problem, feature)
        if value is MISSING_FIELD:
            yield "move: trigger_features cites %s, which is not a problem.json field" % feature
        elif value == "unknown":
            yield "move: trigger_features cites %s, whose value is unknown" % feature


def find_composition_order_defects(identifier, moves, ordered_ids, executed):
    """The move continues the previous move's composition, or starts the first ordered
    composition that carries a strategy with no move yet."""
    current = []
    if moves and moves[-1]["composition"] in ordered_ids:
        current.append(moves[-1]["composition"])
    first_open = next(
        (candidate for candidate in ordered_ids if not executed.issuperset(candidate.split("+"))),
        None,
    )
    if first_open is not None and first_open not in current:
        current.append(first_open)
    if identifier not in current:
        yield "move: composition %s is not the current one; the order gives %s" % (
            identifier, " or ".join(current) or "nothing"
        )


def find_strategy_order_defects(strategy, strategies, identifier, moves, executed):
    """Within a composition the strategies run in its order, each once every strategy
    before it has a move; a composition never returns to a strategy it has left."""
    index = strategies.index(strategy)
    reached = [past["strategy"] for past in moves if past["composition"] == identifier]
    if reached and strategies.index(reached[-1]) > index:
        yield "move: composition %s has moved on from %s to %s" % (identifier, strategy, reached[-1])
    for earlier in strategies[:index]:
        if earlier not in executed:
            yield "move: composition %s reaches %s after %s, which has no move yet" % (
                identifier, strategy, earlier
            )


def read_failure_window_start(workspace):
    """The move count at the last `fail`; the consecutive-failure window starts there."""
    path = workspace / "preconditions.json"
    if not path.exists():
        return 0
    stamps = [
        record["failed_after_move"]
        for record in read_json(path).values()
        if isinstance(record, dict) and isinstance(record.get("failed_after_move"), int)
    ]
    return max(stamps, default=0)


def find_study_defects(workspace, name, what, before):
    """The study record study/<name>.md must exist and hold text before the step named by `before`."""
    path = workspace / "study" / ("%s.md" % name)
    if path.exists() and path.read_text().strip():
        return []
    return ["study/%s.md: missing or empty; write %s (STUDY.md) before %s" % (name, what, before)]


def parse_move_json(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise ValidationError(["--json: not valid JSON (%s)" % error]) from error


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def is_str(value):
    return isinstance(value, str)


def is_bool(value):
    return isinstance(value, bool)


def is_str_list(value):
    return isinstance(value, list) and all(is_str(item) for item in value)


MOVE_FIELDS = {
    "move": ("int", is_int),
    "pass": ("int", is_int),
    "composition": ("str", is_str),
    "costs_paid": ("a list of str", is_str_list),
    "strategy": ("str", is_str),
    "entry": ("str", is_str),
    "trigger_features": ("a list of str", is_str_list),
    "action": ("str", is_str),
    "steps": ("a list of str", is_str_list),
    "output": ("str", is_str),
    "failure_signal_fired": ("bool", is_bool),
    "problem_changed": ("bool", is_bool),
    "closes": ("bool", is_bool),
}


def find_move_defects(move):
    for field in MOVE_FIELDS:
        if field not in move:
            yield "move: missing %s" % field
    for field in move:
        if field not in MOVE_FIELDS:
            yield "move: unknown field %s" % field
    for field, (type_name, has_type) in MOVE_FIELDS.items():
        if field in move and not has_type(move[field]):
            yield "move: %s must be %s" % (field, type_name)
    if is_str_list(move.get("costs_paid")):
        for cost in move["costs_paid"]:
            if cost not in COSTS:
                yield "move: costs_paid %r is not one of %s" % (cost, ", ".join(COSTS))


def find_budget_defects(move, budget):
    if budget["stall_reason"]:
        yield "move: stall is due (%s)" % budget["stall_reason"]
        return
    if move["move"] != budget["moves_total"] + 1:
        yield "move: move must be %d" % (budget["moves_total"] + 1)
    allowed_passes = [n for n in (budget["pass"], budget["pass"] + 1) if 1 <= n <= PASS_COUNT]
    if move["pass"] not in allowed_passes:
        yield "move: pass must be %s" % " or ".join(str(n) for n in allowed_passes)
    elif move["pass"] == budget["pass"] and budget["moves_in_pass"] >= MOVES_PER_PASS:
        yield "move: pass %d is spent; start pass %d" % (budget["pass"], budget["pass"] + 1)


def read_journal(workspace):
    path = workspace / "journal.jsonl"
    if not path.exists():
        raise ValidationError(["journal.jsonl: missing"])
    return [json.loads(line) for line in path.read_text().splitlines()]


def compute_budget(moves, window_start=0):
    current_pass = moves[-1]["pass"] if moves else 0
    moves_in_pass = sum(move["pass"] == current_pass for move in moves)
    return {
        "pass": current_pass,
        "moves_in_pass": moves_in_pass,
        "moves_total": len(moves),
        "stall_reason": find_stall_reason(moves, current_pass, moves_in_pass, window_start),
    }


def find_stall_reason(moves, current_pass, moves_in_pass, window_start=0):
    if moves and moves[-1]["closes"]:
        return "the attack closed at move %d" % moves[-1]["move"]
    if len(moves) >= MOVE_HARD_CAP:
        return "hard cap of %d moves reached" % MOVE_HARD_CAP
    if current_pass >= PASS_COUNT and moves_in_pass >= MOVES_PER_PASS:
        return "last pass spent"
    recent = moves[window_start:][-STALL_AFTER_CONSECUTIVE_FAILURES:]
    all_recent_failed = all(move["failure_signal_fired"] for move in recent)
    if len(recent) == STALL_AFTER_CONSECUTIVE_FAILURES and all_recent_failed:
        return "%d consecutive failure signals" % STALL_AFTER_CONSECUTIVE_FAILURES
    return None


def print_budget(budget):
    reason = budget["stall_reason"]
    print("moves this pass: %d/%d" % (budget["moves_in_pass"], MOVES_PER_PASS))
    print("moves overall: %d/%d" % (budget["moves_total"], MOVE_HARD_CAP))
    print("passes used: %d/%d" % (budget["pass"], PASS_COUNT))
    print("stall due: %s" % ("yes (%s)" % reason if reason else "no"))


def run_fail(args):
    workspace = args.attack_root / args.slug
    path = workspace / "preconditions.json"
    preconditions = read_json(path)
    if args.strategy not in preconditions:
        raise ValidationError(["preconditions.json: no entry for %s" % args.strategy])
    preconditions[args.strategy].update(
        verdict="no", note=FAIL_NOTE, failed_after_move=len(read_journal(workspace))
    )
    write_json(path, preconditions)
    run_plan(args)


def run_stall(args):
    workspace = args.attack_root / args.slug
    moves = read_journal(workspace)
    rule = find_cash_out_rule(workspace, moves)
    if rule is None:
        raise ValidationError([
            "stall: no rule started the cash-out (stall due: no; %s); continue at stage 5"
            % describe_plan(workspace)
        ])
    (workspace / "units" / "INVENTORY.md").write_text(format_inventory(args.slug, moves))
    fired = sum(move["failure_signal_fired"] for move in moves)
    print(
        "wrote units/INVENTORY.md (%d moves, %d ended in a failure signal); rule: %s"
        % (len(moves), fired, rule)
    )


def find_cash_out_rule(workspace, moves):
    """The rule that started the cash-out: a stall fell due (a closing move makes one),
    or the plan emitted no composition. None while the attack is open."""
    budget = compute_budget(moves, read_failure_window_start(workspace))
    if budget["stall_reason"]:
        return budget["stall_reason"]
    if count_planned_compositions(workspace) == 0:
        return "no admissible composition"
    return None


def count_planned_compositions(workspace):
    """How many compositions the current plan emitted; None before plan has run."""
    path = workspace / "compositions.json"
    if not path.exists():
        return None
    return len(read_json(path)["compositions"])


def describe_plan(workspace):
    planned = count_planned_compositions(workspace)
    if planned is None:
        return "no plan yet"
    return "%d compositions planned" % planned


def format_inventory(slug, moves):
    sections = [
        "# Inventory: %s\n\n"
        "Every journal move and what its output leaves in the record, grouped by\n"
        "strategy. A move marked as a fired failure signal leaves what that\n"
        "strategy's Failure signal names. Convert each into a unit under\n"
        "CASHOUT.md or discard it.\n" % slug
    ]
    paid = sorted({cost for move in moves for cost in move["costs_paid"]})
    if paid:
        sections.append("Costs paid across the attack: %s.\n" % ", ".join(paid))
    if not moves:
        sections.append("No move is journalled.\n")
    for strategy in sorted({move["strategy"] for move in moves}):
        lines = [format_move_line(move) for move in moves if move["strategy"] == strategy]
        sections.append("## %s\n\n%s" % (strategy, "".join(lines)))
    return "\n".join(sections)


def format_move_line(move):
    marks = []
    if move["failure_signal_fired"]:
        marks.append("failure signal fired")
    if move["closes"]:
        marks.append("closed the attack")
    if move["costs_paid"]:
        marks.append("paid " + join_with_and(move["costs_paid"]))
    return "- move %d (pass %d, %s): %s\n" % (
        move["move"], move["pass"], ", ".join([move["entry"]] + marks), move["output"]
    )


def join_with_and(items):
    """The costs in the order the move recorded them, which is the order they were paid."""
    if len(items) < 2:
        return "".join(items)
    return "%s and %s" % (", ".join(items[:-1]), items[-1])


def run_check_unit(args):
    workspace = args.attack_root / args.slug
    unit_dir = workspace / "units" / args.unit
    stamp_path = unit_dir / UNIT_STAMP_FILE
    if stamp_path.exists():
        stamp_path.unlink()
    unit = read_json(unit_dir / "unit.json")
    moves = read_journal(workspace)
    defects = (
        list(find_inventory_defects(workspace, "check-unit"))
        or list(find_unit_defects(unit, workspace, moves))
        or list(find_form_defects(unit, workspace))
    )
    if defects:
        raise ValidationError(defects)
    write_json(stamp_path, {"unit_sha256": digest_file(unit_dir / "unit.json")})
    print("units/%s/unit.json: ok" % args.unit)


def find_inventory_defects(workspace, command):
    """The cash-out has started: stall wrote the inventory."""
    if not (workspace / "units" / "INVENTORY.md").exists():
        yield "units/INVENTORY.md: missing; run stall before %s" % command


def run_finish(args):
    workspace = args.attack_root / args.slug
    finished_path = workspace / "units" / "FINISHED.json"
    if not read_journal(workspace) and not (workspace / "units" / "INVENTORY.md").exists():
        defects = list(find_stage_three_exit_defects(workspace))
        if defects:
            raise ValidationError(defects)
        write_json(finished_path, {"outcome": "solved-in-literature", "units": []})
        print("finished %s: the statement is in the literature; novelty.md records where" % args.slug)
        return
    numbers = list_unit_numbers(workspace)
    defects = list(find_inventory_defects(workspace, "finish")) or [
        defect for number in numbers for defect in find_finished_unit_defects(workspace, number)
    ]
    if defects:
        raise ValidationError(defects)
    write_json(finished_path, {"outcome": "cashed-out", "units": numbers})
    standing = "1 unit stands" if len(numbers) == 1 else "%d units stand" % len(numbers)
    print("finished %s: %s" % (args.slug, standing))


def find_stage_three_exit_defects(workspace):
    """An attack that ends at stage 3 has a problem study and a novelty record that
    says where the statement is solved."""
    yield from find_study_defects(workspace, "problem", "the problem-level study", "finish")
    novelty_path = workspace / "novelty.md"
    if not novelty_path.exists() or not novelty_path.read_text().strip():
        yield "novelty.md: empty; the stage 3 exit records where the statement is solved"


def list_unit_numbers(workspace):
    """The units under units/: every directory named by a number, in that order."""
    return sorted(
        int(path.name)
        for path in (workspace / "units").iterdir()
        if path.is_dir() and path.name.isdigit()
    )


def find_finished_unit_defects(workspace, number):
    """A finished unit was checked as it now stands, and carries its draft and the draft's evaluation."""
    unit_dir = workspace / "units" / str(number)
    label = "units/%d" % number
    stamp_path = unit_dir / UNIT_STAMP_FILE
    if not (unit_dir / "unit.json").exists():
        yield "%s/unit.json: missing" % label
    elif not stamp_path.exists():
        yield "%s: not checked; run check-unit" % label
    elif read_json(stamp_path)["unit_sha256"] != digest_file(unit_dir / "unit.json"):
        yield "%s: unit.json changed after check-unit; run it again" % label
    for name in (UNIT_DRAFT_FILE, UNIT_EVALUATION_FILE):
        path = unit_dir / name
        if not path.exists() or not path.read_text().strip():
            yield "%s/%s: missing or empty" % (label, name)


def digest_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_form_defects(unit, workspace):
    """What the evidence and the ledger decide about the form, as CASHOUT.md states it."""
    form, evidence, costs = unit["form"], unit["evidence"], unit["costs"]
    if evidence.startswith("deterministic/"):
        status = read_json(workspace / evidence / "result.json")["status"]
        if status != "pass" and form != EVIDENCE_FORM:
            yield (
                "form: %s rests on %s with status %s; a run that did not pass is evidence,"
                " and the form is %s" % (form, evidence, status, EVIDENCE_FORM)
            )
    if form in CLOSING_FORMS and "object" in costs:
        yield (
            "form: %s carries object; a proof of the claim itself pays no object, so this is a"
            " conditional-or-special-case unit plus a reduction-or-equivalence unit" % form
        )
    if form in CLOSING_FORMS and "obligations" in costs:
        yield (
            "form: %s carries obligations; a proof that still owes a statement is a"
            " conditional-or-special-case unit" % form
        )
    if form in EXHIBITING_FORMS and "constructivity" in costs:
        yield "form: %s carries constructivity; the evidence does not exhibit the object" % form


def find_unit_defects(unit, workspace, moves):
    journal_moves = {move["move"] for move in moves}
    for key in ("statement", "form", "evidence", "novelty", "moves", "costs"):
        if key not in unit:
            yield "%s: missing" % key
    if "statement" in unit:
        yield from check_text(unit["statement"], "statement")
    if "form" in unit:
        yield from check_choice(unit["form"], "form", UNIT_FORMS)
    if "evidence" in unit:
        yield from find_evidence_defects(unit["evidence"], workspace)
    if "novelty" in unit:
        yield from check_text(unit["novelty"], "novelty")
    if "moves" in unit:
        yield from find_unit_move_defects(unit["moves"], journal_moves)
    if "costs" in unit:
        yield from find_unit_cost_defects(unit["costs"], unit.get("moves"), moves)


def find_unit_cost_defects(costs, numbers, moves):
    """The ledger is the sum of what the unit's own moves paid, not a fresh judgement."""
    if not is_str_list(costs):
        yield "costs: not a list of str"
        return
    for cost in costs:
        if cost not in COSTS:
            yield "costs: %r is not one of %s" % (cost, ", ".join(COSTS))
            return
    if not is_int_list(numbers):
        return
    listed = [move for move in moves if move["move"] in numbers]
    paid = sorted({cost for move in listed for cost in move["costs_paid"]})
    if paid != sorted(costs):
        yield "costs: moves %s paid %s" % (
            ", ".join(str(number) for number in numbers),
            ", ".join(paid) if paid else "nothing",
        )


def is_int_list(value):
    return isinstance(value, list) and all(is_int(item) for item in value)


def find_evidence_defects(evidence, workspace):
    text_defects = list(check_text(evidence, "evidence"))
    if text_defects:
        yield from text_defects
    elif not (workspace / evidence).exists():
        yield "evidence: %s does not exist" % evidence
    elif evidence.startswith("deterministic/") and not (workspace / evidence / "result.json").exists():
        yield "evidence: %s has no result.json; run verify first" % evidence


def find_unit_move_defects(numbers, journal_moves):
    if not isinstance(numbers, list) or not all(is_int(number) for number in numbers):
        yield "moves: must be a list of int"
        return
    if not numbers:
        yield "moves: empty"
    for number in numbers:
        if number not in journal_moves:
            yield "moves: %d is not a journal move" % number


def run_verify_lean(args):
    step_dir = resolve_step_dir(args)
    step = read_json(step_dir / "step.json")
    defects = list(find_lean_project_defects(step_dir, step))
    if defects:
        raise ValidationError(defects)
    result = check_lean_theorem(step_dir, step["theorem"], step.get("file", "Main.lean"))
    write_json(step_dir / "result.json", result)
    return print_verdict(result, result["reason"] or describe_axioms(result))


def find_lean_project_defects(step_dir, step):
    if "theorem" not in step:
        yield "step.json: missing theorem"
    if not (step_dir / "lakefile.lean").exists() and not (step_dir / "lakefile.toml").exists():
        yield "%s: no lakefile.lean or lakefile.toml" % step_dir.name
    if not (step_dir / "lean-toolchain").exists():
        yield "%s: no lean-toolchain" % step_dir.name
    file_name = step.get("file", "Main.lean")
    if not (step_dir / file_name).exists():
        yield "%s: missing" % file_name


def check_lean_theorem(step_dir, theorem, file_name):
    build = run_in(step_dir, ["lake", "build"])
    if build.returncode != 0:
        return build_lean_result(theorem, [], "fail", "lake build failed", build.stdout)
    module_name = ".".join(Path(file_name).with_suffix("").parts)
    check_path = step_dir / AXIOMS_CHECK_FILE
    check_path.write_text("import %s\n#print axioms %s\n" % (module_name, theorem))
    try:
        printed = run_in(step_dir, ["lake", "env", "lean", AXIOMS_CHECK_FILE])
    finally:
        check_path.unlink()
    axioms = parse_axioms(printed.stdout)
    if axioms is None:
        return build_lean_result(theorem, [], "fail", "no axioms line in the lean output", printed.stdout)
    status, reason = classify_axioms(theorem, axioms)
    return build_lean_result(theorem, axioms, status, reason, printed.stdout)


def parse_axioms(output):
    """The axioms `#print axioms` listed, [] when it listed none, None when the line is absent."""
    listed = AXIOMS_LINE_PATTERN.search(output)
    if listed:
        return [axiom.strip() for axiom in listed.group(1).split(",") if axiom.strip()]
    if NO_AXIOMS_LINE_PATTERN.search(output):
        return []
    return None


def classify_axioms(theorem, axioms):
    """The decision rule of ../strategies/references/lean4.md section 4: (status, reason)."""
    if "sorryAx" in axioms:
        return "fail", "%s depends on sorryAx" % theorem
    custom = [
        axiom for axiom in axioms if axiom not in STANDARD_AXIOMS and not is_native_evaluation_axiom(axiom)
    ]
    if custom:
        return "fail", "%s depends on custom axioms %s" % (theorem, ", ".join(custom))
    native = [axiom for axiom in axioms if is_native_evaluation_axiom(axiom)]
    if native:
        return "evidence", (
            "%s depends on %s: native evaluation, computational evidence"
            " rather than a kernel-checked certificate" % (theorem, ", ".join(native))
        )
    return "pass", None


def is_native_evaluation_axiom(axiom):
    return axiom in NATIVE_EVALUATION_AXIOMS or NATIVE_DECIDE_AXIOM_MARKER in axiom


def build_lean_result(theorem, axioms, status, reason, output):
    return {
        "status": status,
        "theorem": theorem,
        "axioms": axioms,
        "reason": reason,
        "output_head": head_lines(output),
    }


def describe_axioms(result):
    if result["axioms"]:
        return "%s depends on axioms %s" % (result["theorem"], ", ".join(result["axioms"]))
    return "%s depends on no axioms" % result["theorem"]


def run_verify_certificate(args):
    step_dir = resolve_step_dir(args)
    script = step_dir / "check.sh"
    if not script.exists():
        raise ValidationError(["check.sh: missing"])
    if not os.access(script, os.X_OK):
        raise ValidationError(["check.sh: not executable"])
    completed = run_in(step_dir, [str(script.resolve())])
    result = {
        "status": "pass" if completed.returncode == 0 else "fail",
        "exit_status": completed.returncode,
        "output_head": head_lines(completed.stdout),
    }
    write_json(step_dir / "result.json", result)
    return print_verdict(result, "check.sh exited %d" % completed.returncode)


def resolve_step_dir(args):
    step_dir = args.attack_root / args.slug / "deterministic" / args.step_dir
    if not step_dir.is_dir():
        raise ValidationError(["deterministic/%s: missing" % args.step_dir])
    return step_dir


def run_in(directory, command):
    return subprocess.run(
        command, cwd=directory, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )


def head_lines(output):
    return output.splitlines()[:OUTPUT_HEAD_LINES]


def print_verdict(result, detail):
    print("%s: %s" % (result["status"], detail))
    return 1 if result["status"] == "fail" else 0


def read_json(path):
    if not path.exists():
        raise ValidationError(["%s: missing" % path.name])
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise ValidationError(["%s: not valid JSON (%s)" % (path.name, error)]) from error


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")


def build_parser():
    parser = argparse.ArgumentParser(description="Deterministic harness for an attack workspace.")
    parser.add_argument(
        "--strategies", type=Path, default=DEFAULT_STRATEGIES_DIR, help="directory of strategy files"
    )
    parser.add_argument(
        "--attack-root", type=Path, default=DEFAULT_ATTACK_ROOT, help="directory holding <slug>/ workspaces"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="create the workspace")
    init.add_argument("slug")
    init.set_defaults(run=run_init)

    check_problem = commands.add_parser("check-problem", help="validate problem.json")
    check_problem.add_argument("slug")
    check_problem.set_defaults(run=run_check_problem)

    plan = commands.add_parser("plan", help="validate preconditions.json and rank compositions")
    plan.add_argument("slug")
    plan.set_defaults(run=run_plan)

    rank = commands.add_parser("rank", help="validate ranking.json against the current plan")
    rank.add_argument("slug")
    rank.set_defaults(run=run_rank)

    journal = commands.add_parser("journal", help="journal.jsonl commands")
    journal_commands = journal.add_subparsers(dest="journal_command", required=True)
    journal_add = journal_commands.add_parser("add", help="validate and append one move")
    journal_add.add_argument("slug")
    journal_add.add_argument("--json", required=True, help="the move as one JSON object")
    journal_add.set_defaults(run=run_journal_add)

    budget = commands.add_parser("budget", help="print the move budget state")
    budget.add_argument("slug")
    budget.set_defaults(run=run_budget)

    fail = commands.add_parser("fail", help="set a strategy's verdict to no and re-plan")
    fail.add_argument("slug")
    fail.add_argument("strategy")
    fail.set_defaults(run=run_fail)

    stall = commands.add_parser("stall", help="write the inventory skeleton")
    stall.add_argument("slug")
    stall.set_defaults(run=run_stall)

    check_unit = commands.add_parser("check-unit", help="validate units/<n>/unit.json")
    check_unit.add_argument("slug")
    check_unit.add_argument("unit", metavar="n")
    check_unit.set_defaults(run=run_check_unit)

    finish = commands.add_parser("finish", help="close the workspace once every unit is checked, drafted, and evaluated")
    finish.add_argument("slug")
    finish.set_defaults(run=run_finish)

    verify = commands.add_parser("verify", help="run a deterministic step's check")
    verify_commands = verify.add_subparsers(dest="verify_command", required=True)
    for name, run, help_text in (
        ("lean", run_verify_lean, "lake build, then the #print axioms decision rule"),
        ("certificate", run_verify_certificate, "run the step's check.sh"),
    ):
        verify_command = verify_commands.add_parser(name, help=help_text)
        verify_command.add_argument("slug")
        verify_command.add_argument("step_dir", metavar="step-dir")
        verify_command.set_defaults(run=run)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.run(args) or 0
    except ValidationError as error:
        for problem in error.problems:
            print(problem, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
