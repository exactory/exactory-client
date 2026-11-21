#!/usr/bin/env python3
"""List and run the test modules that must pass before the pull request of a release.

The set has three parts. The first two make its near part, the third its far part.

Always: the modules of ALWAYS. They compare the harness with frozen values, they pin a fixture, a version or a
release file, or they read every source file of the harness against a declared table, so a change far from them
can break them. When the diff changes a file of research_harness/ or of
tests/fixtures/, the modules of HARNESS_PINS join them: they pin the certificates of the changed code and take
long.

From the files of tests/ that the diff changes: each changed test module, and each test module that reaches a
changed file of tests/ through the imports between the files of tests/. So a test that uses a changed fixture
builder through two other test modules is in the set.

From the other files that the diff changes: each test module that uses a changed module of research_harness, ci or
tools, and each test module whose text names a changed file of another kind (its file name, the folder of
tests/fixtures that holds it, or the quoted name of a command of bin/). A test module uses a module when it imports
it, at the top or inside a function, or when a string of it holds the dotted name or the path of the module: the
target of mock.patch, the argument of importlib.import_module, a path with a function name. A change also reaches
the tests through the files that read for them: a file of tests/fixtures, compressed or not, or a module of ci or
tools that uses a changed file in the same way, or uses another such file, counts as changed. In this way the
checksum pins of a fixture that a module of ci checks select the test modules that import that module of ci. This
part takes the direct users only. The full suites of the CI cover the rest. They are also the check for a change of
the runner or of the reviewer program guard, which can change the result of every test.

    python3 ci/must_pass.py list [--base origin/main] [--head REVISION] [--near]
    python3 ci/must_pass.py run [--base origin/main] [--head REVISION] [--module NAME ...] [--python EXECUTABLE ...]
                                [--jobs N] [--logs DIRECTORY]

Without --head, the diff is the working tree, with its untracked files, against the merge base of HEAD and the
base. With --head, it is that commit against the merge base of the head and the base, for example to replay a
release.

list prints the whole set. run starts the whole set when it holds at most the share LARGE_SET_SHARE of the test
modules of tests/. A larger set comes from a change of a module that many tests use, and its local run would
take hours of the test processes of a lane and do work of the full suite of the CI a second time: run then starts
its near part only, and prints one line with the count of the modules of its far part, whose check is the full
suite of the CI. list --near prints the modules that run starts.

run groups the tests into processes as the full suite of the CI does. For each Python executable and each shard of
the CI, one process (ci/unittest_shards.py run-group) runs the tests of the set that the shard holds, in the order
of the shard. So a test that leaves state behind breaks the later tests of its group as it does in the CI. With
--module, run takes the named test modules in place of the set: this is the run that an implementer repeats.

run prints each group when it ends, then a table of the modules with the wall time, the processor time and the
status of each module for each Python executable, the slowest tests, the times of the run, and the comparison of
the wall time of the run with its budget in ci/budgets.json: implementer_run with --module, must_pass_run without.
It writes the same report, with the time of each test, as must-pass-report.json into the log directory; its member
far_part holds the modules of the far part and whether this run started them. It ends with status 1 when a group
that it started fails; the budget does not change the status. --jobs is at most test_processes of
ci/budgets.json. The tests of skills/math-solver/harness have their own command and are not in the set.
"""
import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
import gzip
import json
import lzma
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
# The modules that compare with frozen values or pin a fixture, a version or a release file.
ALWAYS = ("test_capacity_packet_inventory", "test_capture_classes", "test_codex", "test_incident_ledger",
          "test_installed_runtime_readers", "test_manifest", "test_packet_call_rows", "test_release_records",
          "test_research_guidance", "test_review_request_parity", "test_series_code_reach")
# The slow modules that pin the certificates of the harness code. The list prints them first.
HARNESS_PINS = ("test_capacity_guard", "test_continuity_guard", "test_continuity_guard_stage3",
                "test_capacity_guard_fixes")
MODULE_PACKAGES = ("research_harness", "ci", "tools")
# The packages whose modules read files for the tests. A module of research_harness carries no change: the modules
# of research_harness import each other, so the change of one would reach most of the suite.
CARRIER_PACKAGES = ("ci", "tools")
# A dotted name or a path of a module of MODULE_PACKAGES in a text, with the names that follow it: an import, the
# target of mock.patch, the argument of importlib.import_module, or a path with a function name.
MODULE_NAME_PATTERN = re.compile(r"\b(?:" + "|".join(MODULE_PACKAGES) + r")(?:[./][A-Za-z_]\w*)+")
# The readers of the compressed fixtures, by file suffix.
DECOMPRESSORS = {".gz": gzip.decompress, ".xz": lzma.decompress}
REPORT_FILE_NAME = "must-pass-report.json"
# run starts only the near part of a set that holds more than this share of the test modules of tests/. Such a set
# comes from a change of a module that many tests use: for the diff of 0.91.11 it held 456 of 554 modules and 97%
# of the test time. The share was one half until 2026-11-21. That day the whole sets of four lanes, each under one
# half, ran against the budget must_pass_run of 2,700 s: 35 modules took 4,002 s with four test processes, 124
# modules took 13,089 s with four, and two sets took about 16,000 s with two. The near parts of the same day (16 to
# 18 modules) took 1,124 to 1,408 s with two. So the whole set runs only when it is small: 0.07 is about 41 of 590
# modules. The full suite of the CI is the check of the far part, and ALWAYS holds the modules that read every
# source file, which a change of any source can fail.
LARGE_SET_SHARE = 0.07
FAR_PART_LINE = ("The set holds %d of %d test modules, above the share of a whole run. This run starts its near "
                 "part, %d modules. The full suite of the CI is the check of the %d modules of its far part, which "
                 "this run does not start.")


def list_changed_files(root, base, head=None):
    """The files that differ from the merge base with the base: those of the head commit, or without a head those
    of the working tree, committed or not, and the untracked files."""
    def read_git(*arguments):
        return subprocess.run(("git",) + arguments, cwd=root, check=True, text=True,
                              stdout=subprocess.PIPE).stdout.split("\n")
    if head:
        names = read_git("diff", "--name-only", read_git("merge-base", base, head)[0], head)
    else:
        merge_base = read_git("merge-base", base, "HEAD")[0]
        names = read_git("diff", "--name-only", merge_base) + read_git("ls-files", "--others", "--exclude-standard")
    return sorted({name for name in names if name})


def read_python_names(source):
    """Each module that the source imports, at the top or inside a function, and each name that it imports from a
    module, as dotted names; and the strings of the source other than its docstrings."""
    imports, strings, docstrings = set(), [], set()
    # ast.walk visits a node before its children, so a docstring is known before its turn comes.
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body \
                and isinstance(node.body[0], ast.Expr):
            docstrings.add(id(node.body[0].value))
        elif isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            imports.add(node.module)
            imports.update(node.module + "." + alias.name for alias in node.names)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            strings.append(node.value)
    return imports, strings


def describe_file(path):
    """The text of a file, decompressed when it is a compressed fixture, the names that it imports when it is Python
    source, and the dotted names of the modules that it uses: those that it imports and those that it names. A
    Python file names a module in a string, not in a comment or a docstring."""
    data = path.read_bytes()
    if path.suffix in DECOMPRESSORS:
        data = DECOMPRESSORS[path.suffix](data)
    source = data.decode("utf-8", errors="replace")
    imports, strings = read_python_names(source) if path.suffix == ".py" else (set(), [source])
    named = {name.replace("/", ".") for text in strings for name in MODULE_NAME_PATTERN.findall(text)}
    return {"source": source, "imports": imports, "names": imports | named}


def describe_test_files(root):
    """The Python files of tests/, by their module name."""
    return {path.stem: describe_file(path) for path in sorted((root / "tests").glob("*.py"))}


def describe_tree(root):
    """The Python files of tests/ by module name, and by their path in the tree the files that can carry a change to
    a test without an import: the files of tests/fixtures and the modules of CARRIER_PACKAGES."""
    paths = [path for path in sorted((root / "tests" / "fixtures").rglob("*"))
             if path.is_file() and "__pycache__" not in path.parts]
    paths += [path for package in CARRIER_PACKAGES for path in sorted((root / package).rglob("*.py"))]
    return {"tests": describe_test_files(root),
            "carriers": {path.relative_to(root).as_posix(): describe_file(path) for path in paths}}


def changes_test_file(name, changed):
    return "tests/" + name + ".py" in changed


def list_file_references(path):
    """The strings by which a text names a file that is not a module: its file name, and for a file in a folder of
    tests/fixtures, that folder as fixtures/<folder>."""
    references = [path.name]
    if path.parts[:2] == ("tests", "fixtures") and len(path.parts) > 3:
        references.append("fixtures/" + path.parts[2])
    return references


def uses_changed_file(file, changed):
    """Whether a file uses a changed module (it imports it or names it), or names a changed file of another kind."""
    for changed_name in changed:
        path = Path(changed_name)
        if path.parent == Path("tests") and path.suffix == ".py":
            continue
        if path.suffix == ".py" and path.parts[0] in MODULE_PACKAGES:
            parts = path.with_suffix("").parts
            module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
            if any(name == module or name.startswith(module + ".") for name in file["names"]):
                return True
        elif path.parts[0] == "bin":
            # A test loads a command of bin/ by its file name, which the text holds as a string of its own. The
            # plain search first keeps the pattern off the large fixtures that do not hold the name at all.
            if path.name in file["source"] and re.search("[\"']" + re.escape(path.name) + "[\"']", file["source"]):
                return True
        elif any(reference in file["source"] for reference in list_file_references(path)):
            return True
    return False


def find_carriers(carriers, changed):
    """The fixtures and the modules of CARRIER_PACKAGES that use a changed file, directly or through other such
    files. Each file is checked against each name once."""
    found = set()
    pending = list(changed)
    while pending:
        reached = {path for path, file in carriers.items() if path not in found and uses_changed_file(file, pending)}
        found |= reached
        pending = sorted(reached)
    return found


def find_importers(files, changed):
    """The test modules that reach a changed file of tests/ through the imports between the files of tests/. A
    changed test module reaches itself."""
    importers = {name: set() for name in files}
    for name, file in files.items():
        for imported in file["imports"]:
            parts = imported.split(".")
            target = parts[1] if parts[0] == "tests" and len(parts) > 1 else parts[0]
            if target in importers and target != name:
                importers[target].add(name)
    reached = {name for name in files if changes_test_file(name, changed)}
    pending = list(reached)
    while pending:
        for importer in importers[pending.pop()] - reached:
            reached.add(importer)
            pending.append(importer)
    return {name for name in reached if name.startswith("test_")}


def find_users(files, changed):
    """The test modules that use a changed file of another kind than the files of tests/ themselves."""
    return {name for name, file in files.items() if name.startswith("test_") and uses_changed_file(file, changed)}


def select_modules(tree, changed):
    """The must-pass set for a diff, with the slow pins first, and its far part, by name.

    The near part holds the fixed modules, the slow pins when they join, and the test modules that reach a changed
    file of tests/ through the imports between the files of tests/. The far part holds the other test modules that
    use a changed file of another kind: a module of research_harness, ci or tools, a fixture, a document or a
    command, directly or through a file that reads for them."""
    tests = tree["tests"]
    missing = [name for name in ALWAYS + HARNESS_PINS if name not in tests]
    if missing:
        raise SystemExit("ALWAYS or HARNESS_PINS names test modules that tests/ does not hold: " + ", ".join(missing))
    pins = HARNESS_PINS if any(name.startswith(("research_harness/", "tests/fixtures/")) for name in changed) else ()
    near = set(ALWAYS) | set(pins) | find_importers(tests, changed)
    far = find_users(tests, list(changed) + sorted(find_carriers(tree["carriers"], changed))) - near
    return list(pins) + sorted((near | far) - set(pins)), sorted(far)


def run_group(root, executable, shard_index, modules, directory):
    """Run the tests of the modules that one CI shard holds, in one process, and measure the wall time and the
    processor time (user and system) of the process and of the child processes that it waited for."""
    log_path = directory / ("shard-%d.log" % shard_index)
    result_path = directory / ("shard-%d.json" % shard_index)
    command = (executable, "ci/unittest_shards.py", "run-group", "--shard-index", str(shard_index),
               "--result", str(result_path)) + tuple(modules)
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as log, \
            subprocess.Popen(command, cwd=root, stdout=log, stderr=subprocess.STDOUT) as process:
        # os.wait4 gives the resource use of this child alone, while the other groups run.
        _, status, usage = os.wait4(process.pid, 0)
        # The child is reaped, so the context manager must not wait for it again.
        process.returncode = os.waitstatus_to_exitcode(status)
    return {"python": executable, "shard_index": shard_index, "exit_status": process.returncode,
            "wall_seconds": round(time.monotonic() - started, 3),
            "processor_seconds": round(usage.ru_utime + usage.ru_stime, 3),
            "log": str(log_path), "result": str(result_path)}


def run_groups(root, executables, modules, shard_count, jobs, logs):
    """Run each shard group with each executable, at most jobs at the same time, and print each group when it ends.
    Each group gets its tests, their times and its failures from its result file."""
    runs = []
    for index, executable in enumerate(executables):
        directory = logs / ("python-%d" % index)
        directory.mkdir(parents=True, exist_ok=True)
        runs.extend((executable, shard_index, directory) for shard_index in range(shard_count))
    groups = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for finished in as_completed([pool.submit(run_group, root, executable, shard_index, modules, directory)
                                      for executable, shard_index, directory in runs]):
            group = finished.result()
            result_path = Path(group["result"])
            result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else {}
            group.update(
                status="ok" if group["exit_status"] == 0 and result.get("success") is True else "FAILED",
                tests_run=result.get("tests_run", 0), test_seconds=result.get("test_seconds", []),
                failed_tests=sorted(set(result.get("failures", []) + result.get("errors", [])
                                        + [row[0] for row in result.get("skipped", [])]
                                        + result.get("expected_failures", [])
                                        + result.get("unexpected_successes", []))))
            print("%-6s  shard %d  %s  %d tests  wall %.1f s  processor %.1f s"
                  % (group["status"], group["shard_index"], group["python"], group["tests_run"],
                     group["wall_seconds"], group["processor_seconds"]), flush=True)
            groups.append(group)
    return sorted(groups, key=lambda group: (executables.index(group["python"]), group["shard_index"]))


def sum_module_times(groups, modules, read_test_module):
    """The tests, the wall time, the processor time and the status of each module for each executable: the sums of
    the times of its tests over the groups."""
    rows = {(group["python"], module): {"python": group["python"], "module": module, "tests": 0, "wall_seconds": 0.0,
                                        "processor_seconds": 0.0, "status": "ok"}
            for group in groups for module in modules}
    for group in groups:
        for test_id, wall_seconds, processor_seconds in group["test_seconds"]:
            row = rows[group["python"], read_test_module(test_id)]
            row.update(tests=row["tests"] + 1, wall_seconds=round(row["wall_seconds"] + wall_seconds, 3),
                       processor_seconds=round(row["processor_seconds"] + processor_seconds, 3))
        for test_id in group["failed_tests"]:
            rows[group["python"], read_test_module(test_id)]["status"] = "FAILED"
    return sorted(rows.values(), key=lambda row: (row["module"], row["python"]))


def print_report(report, format_budget_report):
    """Print the table of the modules, the slowest tests, the times of each executable and of the run, and the
    comparison with the budget."""
    print("%-60s %-32s %6s %10s %12s  %s" % ("Module", "Python", "Tests", "Wall s", "Processor s", "Status"))
    for row in report["module_times"]:
        print("%-60s %-32s %6d %10.1f %12.1f  %s" % (row["module"], row["python"], row["tests"],
                                                     row["wall_seconds"], row["processor_seconds"], row["status"]))
    print("Slowest tests:")
    for python, test_id, wall_seconds, processor_seconds in report["slowest_tests"]:
        print("  wall %8.1f s  processor %8.1f s  %s  %s" % (wall_seconds, processor_seconds, python, test_id))
    for python in report["python"]:
        groups = [group for group in report["groups"] if group["python"] == python]
        print("Python %s: %d groups, %d tests, wall %.1f s and processor %.1f s, summed over the groups."
              % (python, len(groups), sum(group["tests_run"] for group in groups),
                 sum(group["wall_seconds"] for group in groups), sum(group["processor_seconds"] for group in groups)))
    run = report["run"]
    print("%d modules, %d Python executables, %d groups, %d failed groups. Run: wall %.1f s, processor %.1f s."
          % (len(report["modules"]), len(report["python"]), len(report["groups"]),
             sum(group["status"] != "ok" for group in report["groups"]), run["wall_seconds"],
             run["processor_seconds"]))
    budget = report["budget"]
    for line in format_budget_report(budget["name"], budget["wall_seconds"], run["wall_seconds"],
                                     run["processor_seconds"]):
        print(line)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("list", "run"):
        command = commands.add_parser(name)
        command.add_argument("--base", default="origin/main", help="The branch that the release merges into.")
        command.add_argument("--head", help="A commit whose diff selects the set, in place of the working tree.")
    commands.choices["list"].add_argument("--near", action="store_true",
                                          help="Print the modules that run starts: the near part of a set "
                                               "above the share LARGE_SET_SHARE of the test modules, else the "
                                               "whole set.")
    run = commands.choices["run"]
    run.add_argument("--module", action="append",
                     help="A test module of tests/ to run in place of the set, such as test_must_pass. Give the "
                          "option once for each module.")
    run.add_argument("--python", action="append", help="A Python executable. Give the option once for each version.")
    run.add_argument("--jobs", type=int, default=4, help="The count of test processes at the same time.")
    run.add_argument("--logs", type=Path, help="The directory for the logs, the result files and the report.")
    args = parser.parse_args()
    # The runner is the one home of the shards, the budgets and the clock; this script runs it as a module of ci.
    sys.path.insert(0, str(ROOT))
    from ci import unittest_shards
    started = unittest_shards.read_process_clock()
    budgets = unittest_shards.read_budgets()
    if args.command == "run" and not 1 <= args.jobs <= budgets["test_processes"]:
        parser.error("--jobs must be between 1 and %d, the limit test_processes of ci/budgets.json."
                     % budgets["test_processes"])
    if args.command == "run" and args.module:
        unknown = [name for name in args.module
                   if not (name.startswith("test_") and (ROOT / "tests" / (name + ".py")).is_file())]
        if unknown:
            parser.error("--module names no test module of tests/: " + ", ".join(unknown))
        selected, budget_name = sorted(set(args.module)), "implementer_run"
        far_part = {"modules": [], "ran": True}
    else:
        changed = list_changed_files(ROOT, args.base, args.head)
        tree = describe_tree(ROOT)
        (selected, far), budget_name = select_modules(tree, changed), "must_pass_run"
        if any(name.startswith("skills/math-solver/harness/") for name in changed):
            print("The diff changes skills/math-solver/harness. Run its tests with their own command.", file=sys.stderr)
        test_module_count = sum(name.startswith("test_") for name in tree["tests"])
        far_part = {"modules": far, "ran": len(selected) <= LARGE_SET_SHARE * test_module_count}
    # The modules that run starts: the whole set, or the near part of a large set.
    modules = selected if far_part["ran"] else [name for name in selected if name not in far_part["modules"]]
    if args.command == "list":
        print("\n".join(modules if args.near else selected))
        return 0
    if not far_part["ran"]:
        print(FAR_PART_LINE % (len(selected), test_module_count, len(modules), len(far_part["modules"])), flush=True)
    logs = args.logs or Path(tempfile.mkdtemp(prefix="must-pass-"))
    executables = args.python or [sys.executable]
    groups = run_groups(ROOT, executables, modules, unittest_shards.SHARD_COUNT, args.jobs, logs)
    ended = unittest_shards.read_process_clock()
    tests = [[group["python"]] + row for group in groups for row in group["test_seconds"]]
    report = {
        "base": args.base, "head": args.head, "modules": modules, "far_part": far_part, "python": executables,
        "jobs": args.jobs,
        "groups": [{key: value for key, value in group.items() if key != "test_seconds"} for group in groups],
        "module_times": sum_module_times(groups, modules, unittest_shards.read_test_module),
        "test_seconds": tests,
        "slowest_tests": sorted(tests, key=lambda row: -row[2])[:unittest_shards.SLOWEST_TEST_COUNT],
        "run": {"wall_seconds": round(ended[0] - started[0], 3), "processor_seconds": round(ended[1] - started[1], 3)},
        "budget": {"name": budget_name, "wall_seconds": budgets["runs"][budget_name]["wall_seconds"]},
    }
    report["budget"]["over"] = report["run"]["wall_seconds"] > report["budget"]["wall_seconds"]
    unittest_shards.write_json(logs / REPORT_FILE_NAME, report)
    print_report(report, unittest_shards.format_budget_report)
    print("Logs and report: %s" % logs)
    return 1 if any(group["status"] != "ok" for group in groups) else 0


if __name__ == "__main__":
    sys.exit(main())
