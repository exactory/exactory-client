#!/usr/bin/env python3
"""Run and verify exhaustive partitions of ordinary unittest discovery."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import unittest


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_shard(suite, shard_index, shard_count, provenance, *, stream=None, record_path=None):
    if shard_count < 1 or not 0 <= shard_index < shard_count:
        raise ValueError("Invalid shard index or count")
    tests = list(flatten(suite))
    if not tests:
        raise ValueError("Discovery returned no tests")
    selected = [index for index in range(len(tests)) if index % shard_count == shard_index]
    report = {
        "schema_version": 1, "status": "running", "success": False,
        "shard_index": shard_index, "shard_count": shard_count,
        "provenance": provenance, "all_test_ids": [test.id() for test in tests],
        "selected_indices": selected, "executed_test_ids": [],
    }
    if record_path:
        write_json(record_path, report)

    class RecordedResult(unittest.TextTestResult):
        def startTest(self, test):
            report["executed_test_ids"].append(test.id())
            super().startTest(test)

    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=RecordedResult).run(
        unittest.TestSuite(tests[index] for index in selected)
    )
    report.update(
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
    for report in reports:
        provenance = report.get("provenance", {})
        version = provenance.get("python_version")
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
        selected = [offset for offset in range(len(ids)) if offset % shard_count == index]
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
    return {
        "success": not issues, "issues": issues, "tests_run": tests_run,
        "python_versions": python_versions, "shard_count": shard_count,
        "source": source,
        "discovery_counts": {version: len(ids) for version, ids in discoveries.items()},
        "discovery_sha256": {version: hashlib.sha256(json.dumps(ids).encode()).hexdigest()
                             for version, ids in discoveries.items()},
    }


def git_value(directory, expression):
    return subprocess.check_output(["git", "-C", str(directory), "rev-parse", expression], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--start-directory", type=Path, default=Path("tests"))
    run.add_argument("--shard-index", type=int, required=True)
    run.add_argument("--shard-count", type=int, required=True)
    run.add_argument("--marketplace", type=Path, required=True)
    run.add_argument("--result", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--receipts", type=Path, required=True)
    verify.add_argument("--python-versions", nargs="+", required=True)
    verify.add_argument("--shard-count", type=int, required=True)
    verify.add_argument("--client-commit", required=True)
    verify.add_argument("--client-tree", required=True)
    verify.add_argument("--marketplace-commit", required=True)
    verify.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    if args.shard_count < 1:
        parser.error("Shard count must be positive")
    if args.command == "run":
        root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(root))
        provenance = {
            "client_commit": git_value(root, "HEAD"), "client_tree": git_value(root, "HEAD^{tree}"),
            "marketplace_commit": git_value(args.marketplace, "HEAD"),
            "python_version": platform.python_version(),
        }
        suite = unittest.TestLoader().discover(str(args.start_directory.resolve()))
        report = run_shard(suite, args.shard_index, args.shard_count, provenance, record_path=args.result)
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
    print(json.dumps({key: value for key, value in report.items()
                      if key not in ("all_test_ids", "executed_test_ids", "selected_indices")}, sort_keys=True))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
