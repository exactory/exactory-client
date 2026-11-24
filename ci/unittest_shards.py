#!/usr/bin/env python3
"""Run and verify exhaustive partitions of ordinary unittest discovery, run the tests of named modules that one
shard holds, and run named test modules. The commands run, run-group and run-modules install the reviewer program
guard (ci/reviewer_program_guard.py) before a test module loads, measure the wall time and the processor time of
each test, and print a summary with the wall time and the processor time of the command and the ten slowest tests.
The commands run and run-group write a result file, which holds the time of every test and names the evidence tests
that the guard tests of the process required (ci/evidence_tests.py); run-modules writes no file. The command run
compares its wall time with the shard budget of ci/budgets.json. The command verify requires, for each Python
version, that the run executed each test once and that each evidence test that a shard named ran and passed in a
shard of the same run."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import unittest


# The count of shards of each Python version in the CI (.github/workflows/ci.yml). ci/must_pass.py groups its set
# into the same shards.
SHARD_COUNT = 8
BUDGETS_PATH = Path(__file__).resolve().parent / "budgets.json"
# The members of ci/budgets.json that the runners read, as (run, member) pairs under runs.
RUN_BUDGET_MEMBERS = (("implementer_run", "wall_seconds"), ("must_pass_run", "wall_seconds"),
                      ("full_suite_run", "wall_seconds"), ("full_suite_run", "shard_wall_seconds"))
INVALID_BUDGETS_ERR_MSG = ("ci/budgets.json needs a positive whole number for test_processes and for each of "
                           "runs.implementer_run.wall_seconds, runs.must_pass_run.wall_seconds, "
                           "runs.full_suite_run.wall_seconds and runs.full_suite_run.shard_wall_seconds, and an "
                           "object core_operations.")
# The count of the slowest tests that the summary of a run prints. The result file holds the time of every test.
SLOWEST_TEST_COUNT = 10
# The test modules with a fixed shard: the tests of the modules of one group run in one shard
# (select_shard_indices). A group has one of two measured reasons.
# - Its modules share a costly fixture, so that the fixture has one build in a run (principle 3). The times of the
#   first three groups are those of the CI of 0.91.14 on Python 3.12.14.
# - Its module holds a test above 300 s. The shard of every other test is its rank among the discovered tests, so
#   each added test moves the tests after it to the next shard. In the CI run of c75977ef the slate test of 3,217 s
#   moved into shard 0 beside the first group, and that shard passed the limit of 180 minutes on Python 3.9.25.
#   With a fixed shard for each such module, the largest shard of that discovery is 6,766 s for every shift of the
#   ranks (10,442 s before). The times of these groups are the largest test of the module and the whole module, in
#   the CI run of d3d6aab7 on Python 3.9.25.
SHARD_GROUPS = (
    # The cut of the verification of record 12 (57 review turns), built once in a process: 1,371 s to 1,645 s.
    ("test_openness_carried_integration", "test_openness_reading_v7_refusal"),
    # The 11 recorded series of ProblemResultHistoryTests: 107 s.
    ("test_problem_result_history",),
    # The deposit workspace of ManagedStreamingTests with two files above 1 MiB: 38 s.
    # The published submission template is built once for TestSubmitWithAPendingRequest: 52 s for the class.
    ("test_publication_streaming", "test_pending_remote_requests"),
    # The slate of incident A1 with 28,600,000 characters, whose size is the cause of the incident: 3,217 s of 3,243 s.
    ("test_review_parts_slate",),
    # The paged finding after 57 carried turns: 857 s of 1,394 s.
    ("test_openness_final_pages",),
    # The final pages for 5,000 and 20,000 targets: 816 s of 1,183 s.
    ("test_route_capacity_properties",),
    # The adapter journeys and the recorded series of the parts protocol v3: 356 s of 1,364 s.
    ("test_review_parts_v3",),
    # The dossier result of a session of the rules v4: 300 s of 582 s.
    ("test_review_session_v4_turns",),
)


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def select_shard_indices(test_ids, shard_index, shard_count):
    """The indices of the discovered tests that one shard runs. The tests of the modules of group j of SHARD_GROUPS
    belong to shard j mod shard_count. Every other test belongs to shard i mod shard_count, where i is its index among
    the tests outside the groups. This is the one definition of the partition of the CI."""
    group_of_module = {module: index for index, group in enumerate(SHARD_GROUPS) for module in group}
    selected, rank = [], 0
    for index, test_id in enumerate(test_ids):
        group = group_of_module.get(read_test_module(test_id))
        if group is None:
            shard, rank = rank % shard_count, rank + 1
        else:
            shard = group % shard_count
        if shard == shard_index:
            selected.append(index)
    return selected


def read_test_module(test_id):
    """The test module of a test id ('test_x.Class.test_a'), of a subtest, of a fixture error such as
    'setUpClass (test_x.Class)', and of a discovery failure ('unittest.loader._FailedTest.test_x')."""
    is_fixture_error = test_id.startswith(("setUp", "tearDown"))
    name = test_id.split(" (")[1].rstrip(")") if is_fixture_error else test_id.split(" ")[0]
    return name.split(".")[-1] if name.startswith("unittest.loader.") else name.split(".")[0]


def read_process_clock():
    """The wall clock, and the processor time (user and system) of this process and of its child processes that
    ended and were waited for."""
    times = os.times()
    return time.monotonic(), times.user + times.system + times.children_user + times.children_system


def read_budgets(path=BUDGETS_PATH):
    """Read ci/budgets.json, the one file of the budgets of the repository, and check the members that the runners
    read, before a test runs."""
    budgets = json.loads(path.read_text(encoding="utf-8"))
    try:
        values = [budgets["test_processes"]] + [budgets["runs"][run][member] for run, member in RUN_BUDGET_MEMBERS]
        is_valid = (all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in values)
                    and isinstance(budgets["core_operations"], dict))
    except (KeyError, TypeError):
        is_valid = False
    if not is_valid:
        raise ValueError(INVALID_BUDGETS_ERR_MSG)
    return budgets


def format_budget_report(budget_name, budget_seconds, wall_seconds, processor_seconds):
    """The lines that compare the wall time of a run with its budget. A run over its budget gets one more line with
    both numbers. The budget is a report: it does not change the exit status of the run."""
    lines = ["Wall time %.1f s, processor time %.1f s. Budget %s: %d s of wall time."
             % (wall_seconds, processor_seconds, budget_name, budget_seconds)]
    if wall_seconds > budget_seconds:
        lines.append("Over budget: the wall time %.1f s is above the budget %s of %d s."
                     % (wall_seconds, budget_name, budget_seconds))
    return lines


def run_shard(suite, shard_index, shard_count, provenance, *, take_machine_reads, stream=None, record_path=None,
              modules=None, started=None, read_evidence_tests=None):
    """Run the selected tests. take_machine_reads returns one error for each stopped lookup and each stub start that
    the reviewer program guard recorded since its last call (ci/reviewer_program_guard.py). The runner reports a
    test that passed after one of them as an error. read_evidence_tests returns, after the last test, the evidence
    tests that the guard tests of this process required (ci/evidence_tests.py); the report holds them as
    evidence_tests, and without the function it holds none.

    With modules, the shard keeps only the tests of those test modules (read_test_module), in the order of the
    shard; the discovery failure of such a module stays in. The report gives for each executed test its wall time
    and its processor time, from the end of the test before it in this process to its own end, so the class and
    module fixtures that run before a test count for it. started is the reading of read_process_clock at the start
    of the command; the report gives the wall time and the processor time from it to the end of the run."""
    if shard_count < 1 or not 0 <= shard_index < shard_count:
        raise ValueError("Invalid shard index or count")
    started = started or read_process_clock()
    tests = list(flatten(suite))
    if not tests:
        raise ValueError("Discovery returned no tests")
    test_ids = [test.id() for test in tests]
    selected = [index for index in select_shard_indices(test_ids, shard_index, shard_count)
                if modules is None or read_test_module(test_ids[index]) in modules]
    report = {
        "schema_version": 1, "status": "running", "success": False,
        "shard_index": shard_index, "shard_count": shard_count,
        "provenance": provenance, "all_test_ids": test_ids,
        "selected_indices": selected, "executed_test_ids": [], "test_seconds": [],
    }
    if modules is not None:
        report["modules"] = sorted(modules)
    if record_path:
        write_json(record_path, report)

    class RecordedResult(unittest.TextTestResult):
        def startTestRun(self):
            self.clock = read_process_clock()
            super().startTestRun()

        def startTest(self, test):
            report["executed_test_ids"].append(test.id())
            super().startTest(test)

        def addSuccess(self, test):
            # A test can pass after a read of the machine. The code under test can catch the stop of the guard. A
            # thread does not pass the stop on. A child process cannot stop the test. A read since the end of the
            # previous test counts against this test too, for example a stub start in setUpClass.
            reads = take_machine_reads()
            if reads:
                self.addError(test, (type(reads[0]), reads[0], reads[0].__traceback__))
            else:
                super().addSuccess(test)

        def stopTest(self, test):
            # A test that did not pass has its outcome already. Its reads are not charged to the next test.
            take_machine_reads()
            super().stopTest(test)
            clock = read_process_clock()
            report["test_seconds"].append([test.id(), round(clock[0] - self.clock[0], 3),
                                           round(clock[1] - self.clock[1], 3)])
            self.clock = clock

    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=RecordedResult).run(
        unittest.TestSuite(tests[index] for index in selected)
    )
    ended = read_process_clock()
    report.update(
        wall_seconds=round(ended[0] - started[0], 3), processor_seconds=round(ended[1] - started[1], 3),
        evidence_tests=read_evidence_tests() if read_evidence_tests else [],
        status="completed", tests_run=result.testsRun,
        failures=[test.id() for test, _ in result.failures],
        errors=[test.id() for test, _ in result.errors],
        skipped=[[test.id(), reason] for test, reason in result.skipped],
        expected_failures=[test.id() for test, _ in result.expectedFailures],
        unexpected_successes=[test.id() for test in result.unexpectedSuccesses],
    )
    expected_ids = [report["all_test_ids"][index] for index in selected]
    report["success"] = (
        result.wasSuccessful() and not result.skipped and not result.expectedFailures
        and result.testsRun == len(selected) and report["executed_test_ids"] == expected_ids
    )
    if record_path:
        write_json(record_path, report)
    return report


def verify_reports(reports, python_versions, shard_count, expected_provenance=None):
    issues = []
    expected_slots = {(version, index) for version in python_versions for index in range(shard_count)}
    observed_slots = []
    source = expected_provenance
    tests_run = 0
    discoveries = {}
    # For each Python version: the executed tests, the evidence tests that the shards named, and the tests that were
    # skipped or did not pass, by the id of the test (a subtest counts for its test).
    executed, evidence, skipped, unpassed = {}, {}, {}, {}
    for report in reports:
        provenance = report.get("provenance", {})
        version = provenance.get("python_version")
        executed.setdefault(version, []).extend(report.get("executed_test_ids") or [])
        evidence.setdefault(version, set()).update(report.get("evidence_tests") or [])
        skipped.setdefault(version, set()).update(row[0].split(" ")[0] for row in report.get("skipped") or [])
        unpassed.setdefault(version, set()).update(
            test_id.split(" ")[0] for field in ("failures", "errors", "expected_failures", "unexpected_successes")
            for test_id in report.get(field) or [])
        index = report.get("shard_index")
        slot = (version, index)
        observed_slots.append(slot)
        label = "%s shard %s" % slot
        if slot not in expected_slots:
            issues.append(label + ": unexpected shard")
        if report.get("schema_version") != 1 or report.get("shard_count") != shard_count:
            issues.append(label + ": invalid receipt contract")
        current_source = {key: provenance.get(key) for key in
                          ("client_commit", "client_tree", "marketplace_commit")}
        if not all(current_source.values()):
            issues.append(label + ": missing source provenance")
        if source is None:
            source = current_source
        if current_source != source:
            issues.append(label + ": different source revision")
        ids = report.get("all_test_ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(item, str) for item in ids):
            issues.append(label + ": invalid discovery")
            continue
        if version in discoveries and discoveries[version] != ids:
            issues.append(label + ": different ordered discovery")
        discoveries.setdefault(version, ids)
        if not isinstance(index, int) or not 0 <= index < shard_count:
            issues.append(label + ": invalid shard index")
            continue
        selected = select_shard_indices(ids, index, shard_count)
        if report.get("selected_indices") != selected:
            issues.append(label + ": incorrect partition")
        if report.get("executed_test_ids") != [ids[offset] for offset in selected]:
            issues.append(label + ": executed tests differ from selection")
        if report.get("tests_run") != len(selected):
            issues.append(label + ": incomplete test execution")
        else:
            tests_run += len(selected)
        if report.get("status") != "completed" or report.get("success") is not True:
            issues.append(label + ": shard did not complete successfully")
        for field in ("failures", "errors", "skipped", "expected_failures", "unexpected_successes"):
            if report.get(field) != []:
                issues.append(label + ": " + field + " are present or unreported")
    if set(observed_slots) != expected_slots or len(observed_slots) != len(expected_slots):
        issues.append("Missing or repeated shards")
    test_counts = {}
    for version, test_ids in executed.items():
        distinct = set(test_ids)
        test_counts[version] = {"executed": len(test_ids), "distinct": len(distinct),
                                "evidence": len(evidence[version])}
        if len(test_ids) != len(distinct):
            issues.append("%s: the run executed %d tests and %d distinct tests"
                          % (version, len(test_ids), len(distinct)))
        for identifier in sorted(evidence[version]):
            if identifier not in distinct:
                issues.append("%s: evidence test absent from the run: %s" % (version, identifier))
            elif identifier in skipped[version]:
                issues.append("%s: evidence test skipped: %s" % (version, identifier))
            elif identifier in unpassed[version]:
                issues.append("%s: evidence test did not pass: %s" % (version, identifier))
    return {
        "success": not issues, "issues": issues, "tests_run": tests_run, "test_counts": test_counts,
        "python_versions": python_versions, "shard_count": shard_count,
        "source": source,
        "discovery_counts": {version: len(ids) for version, ids in discoveries.items()},
        "discovery_sha256": {version: hashlib.sha256(json.dumps(ids).encode()).hexdigest()
                             for version, ids in discoveries.items()},
    }


def git_value(directory, expression):
    return subprocess.check_output(["git", "-C", str(directory), "rev-parse", expression], text=True).strip()


def install_reviewer_program_guard(root):
    """Put the plugin root on the import path, install the reviewer program guard, and return its function
    take_machine_reads. The caller does this before a test module loads."""
    sys.path.insert(0, str(root))
    from ci import reviewer_program_guard
    reviewer_program_guard.install_guard()
    return reviewer_program_guard.take_machine_reads


def main():
    started = read_process_clock()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--start-directory", type=Path, default=Path("tests"))
    run.add_argument("--top-level-directory", type=Path)
    run.add_argument("--shard-index", type=int, required=True)
    run.add_argument("--shard-count", type=int, required=True)
    run.add_argument("--marketplace", type=Path, required=True)
    run.add_argument("--result", type=Path, required=True)
    group = commands.add_parser(
        "run-group",
        help="Run the tests of the named test modules that one CI shard holds, in the order of that shard, with the "
             "reviewer program guard. The command discovers tests/ as the CI shard does. ci/must_pass.py runs its "
             "set through this command.")
    group.add_argument("--shard-index", type=int, required=True)
    group.add_argument("--shard-count", type=int, default=SHARD_COUNT)
    group.add_argument("--result", type=Path, required=True)
    group.add_argument("modules", nargs="+", help="Test modules of tests/, such as test_ci_shards.")
    named_help = ("Run named test modules, classes or methods with the reviewer program guard. Run this command in "
                  "the plugin root. The exit status is 0 only when every named test runs and passes. A skipped test "
                  "and an expected failure give status 1, as in a CI shard.")
    named = commands.add_parser("run-modules", help=named_help, description=named_help)
    named.add_argument("--top-level-directory", type=Path, default=Path("tests"),
                       help="The directory that goes first on the import path (default: %(default)s). For the "
                            "harness tests, use skills/math-solver/harness.")
    named.add_argument("names", nargs="+", help="Test modules, classes or methods, such as tests.test_ci_shards.")
    verify = commands.add_parser("verify")
    verify.add_argument("--receipts", type=Path, required=True)
    verify.add_argument("--python-versions", nargs="+", required=True)
    verify.add_argument("--shard-count", type=int, required=True)
    verify.add_argument("--client-commit", required=True)
    verify.add_argument("--client-tree", required=True)
    verify.add_argument("--marketplace-commit", required=True)
    verify.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    if args.command != "run-modules" and args.shard_count < 1:
        parser.error("Shard count must be positive")
    root = Path(__file__).resolve().parents[1]
    if args.command != "verify":
        take_machine_reads = install_reviewer_program_guard(root)
        # The guard tests import the same module, so their required evidence tests reach the result of this run.
        from ci.evidence_tests import read_required_tests
    if args.command == "run":
        shard_budget_seconds = read_budgets()["runs"]["full_suite_run"]["shard_wall_seconds"]
        provenance = {
            "client_commit": git_value(root, "HEAD"), "client_tree": git_value(root, "HEAD^{tree}"),
            "marketplace_commit": git_value(args.marketplace, "HEAD"),
            "python_version": platform.python_version(),
        }
        top_level = str(args.top_level_directory.resolve()) if args.top_level_directory else None
        suite = unittest.TestLoader().discover(
            str(args.start_directory.resolve()), top_level_dir=top_level,
        )
        report = run_shard(suite, args.shard_index, args.shard_count, provenance, record_path=args.result,
                           take_machine_reads=take_machine_reads, started=started,
                           read_evidence_tests=read_required_tests)
        for line in format_budget_report("full_suite_run.shard_wall_seconds", shard_budget_seconds,
                                         report["wall_seconds"], report["processor_seconds"]):
            print(line, file=sys.stderr)
    elif args.command == "run-group":
        suite = unittest.TestLoader().discover(str(root / "tests"))
        report = run_shard(suite, args.shard_index, args.shard_count,
                           {"python_version": platform.python_version()}, record_path=args.result,
                           take_machine_reads=take_machine_reads, modules=set(args.modules), started=started,
                           read_evidence_tests=read_required_tests)
    elif args.command == "run-modules":
        sys.path.insert(0, str(args.top_level_directory.resolve()))
        suite = unittest.TestLoader().loadTestsFromNames(args.names)
        report = run_shard(suite, 0, 1, {"python_version": platform.python_version()},
                           take_machine_reads=take_machine_reads, started=started,
                           read_evidence_tests=read_required_tests)
    else:
        paths = sorted(args.receipts.rglob("shard-result.json"))
        report = verify_reports(
            [json.loads(path.read_text(encoding="utf-8")) for path in paths],
            args.python_versions, args.shard_count,
            {"client_commit": args.client_commit, "client_tree": args.client_tree,
             "marketplace_commit": args.marketplace_commit},
        )
        report["receipts"] = [str(path) for path in paths]
        write_json(args.result, report)
    summary = {key: value for key, value in report.items()
               if key not in ("all_test_ids", "executed_test_ids", "selected_indices", "test_seconds",
                              "evidence_tests")}
    if "test_seconds" in report:
        summary["slowest_tests"] = sorted(report["test_seconds"], key=lambda row: -row[1])[:SLOWEST_TEST_COUNT]
    print(json.dumps(summary, sort_keys=True))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
