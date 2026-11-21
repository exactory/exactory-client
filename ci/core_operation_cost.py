#!/usr/bin/env python3
"""Measure the core operations of the store on a copy of a record, and compare each cost with its budget.

    python3 ci/core_operation_cost.py measure RECORD --size-class {small,large} [--budgets PATH] [--json PATH]
        [--stop-after-seconds SECONDS] [--stop-above-bytes BYTES]

RECORD is a workspace that holds .exactory/research.sqlite3. The command copies it into a new temporary directory,
works on the copy only and never writes RECORD. On the copy it runs the three core operations in this order, each in
a process of its own, with the interpreter of the command and the harness of this tree:

- open: Store() of the copy, which checks the structure of the store and reads nothing more;
- status: the command exactory-research status;
- write: one scoped write (operations.prepared_task_mutation) of one record of a kind that no reader knows, so every
  record accepts it. It replays the history for its preparation and again under its write lock.

For each operation the command reports the wall time, the processor time (user plus system) and the peak of resident
memory of its process, from os.wait4, beside the budget of the size class and the operation in the member
core_operations of the budgets file. It also reports the size of the store file and the count of rows of its
history. The output is a Markdown table, and a JSON file with --json. An operation that passes --stop-after-seconds
or whose resident memory passes --stop-above-bytes is stopped. The exit status is 1 when an operation fails or is
stopped, else 0; it does not depend on the budgets.

The command operation runs one operation in its own process. The command measure starts it.
"""

import argparse
from contextlib import closing
import json
import math
import os
from pathlib import Path
import platform
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUDGETS_PATH = ROOT / "ci" / "budgets.json"
SIZE_CLASSES = ("small", "large")
OPERATIONS = ("open", "status", "write")
# The measures of an operation, as the report and a budget name them, with the words and the unit of the report.
MEASURES = {"wall_seconds": ("wall time", "s", 1), "processor_seconds": ("processor time", "s", 1),
            "peak_memory_bytes": ("peak memory", "MB", 10 ** 6)}
DEFAULT_STOP_AFTER_SECONDS = 45 * 60
DEFAULT_STOP_ABOVE_BYTES = 8 * 10 ** 9
WRITE_OPERATION = "core-operation-cost.write"
WRITE_RECORD_KIND = "core_operation_cost_note"
WRITE_REQUEST_ID = "core-operation-cost-write"
# ru_maxrss counts bytes on macOS (man 2 getrusage) and KiB on Linux
# (https://man7.org/linux/man-pages/man2/getrusage.2.html).
MAXRSS_UNIT_BYTES = 1 if sys.platform == "darwin" else 1024
POLL_SECONDS = 0.01
# A large store grew by up to 0.8 GB between two reads of the resident memory one second apart, so the watch reads it
# four times a second. One read of ps takes about 3 ms.
WATCH_SECONDS = 0.25
ERROR_TAIL_BYTES = 4000
REPORT_HEADING_MSG = ("Core operations on a copy of a {size_class} record, with Python {python} and harness "
                      "{harness_version}. Each cell gives the measured value and its budget.")
TABLE_HEAD_MSG = ("| Record | Store file (MB) | History rows | Operation | Outcome | Wall time (s) | Processor time (s) "
                  "| Peak memory (MB) |\n| --- | ---: | ---: | --- | --- | ---: | ---: | ---: |")
NO_BUDGET_MSG = "no budget"
OVER_BUDGET_MSG = "- {operation}: {measure} {value} {unit} is over its budget of {budget} {unit}."
BUDGETS_SHAPE_ERR_MSG = ("The member core_operations of {path} must map a size class ({size_classes}) to operations "
                         "({operations}), and each operation to positive numbers named {measures}.")
STORE_MISSING_ERR_MSG = "{record} holds no .exactory/research.sqlite3."
SCRATCH_INSIDE_ERR_MSG = "The temporary directory {scratch} lies inside {record}, so the copy would write the record."


def run_open(workspace):
    from research_harness.storage import Store
    Store(workspace)


def run_write(workspace):
    from research_harness import operations
    from research_harness.storage import Store
    operations.prepared_task_mutation(
        Store(workspace), WRITE_OPERATION, {"note": "core operation cost"},
        lambda records, payload: ([(WRITE_RECORD_KIND, "1", {"note": "core operation cost"})], {"recorded": True}),
        request_id=WRITE_REQUEST_ID)


OPERATION_RUNNERS = {"open": run_open, "write": run_write}


def build_operation_command(operation, workspace):
    if operation == "status":
        return [sys.executable, str(ROOT / "bin" / "exactory-research"), "status", "--workspace", str(workspace)]
    return [sys.executable, str(Path(__file__).resolve()), "operation", operation, str(workspace)]


def read_budgets(path, size_class):
    """{operation: {measure: budget}} of the size class. An operation without a member has no budget."""
    document = json.loads(path.read_text(encoding="utf-8"))
    members = document.get("core_operations", {}) if isinstance(document, dict) else None
    if not (isinstance(members, dict) and all(
            size in SIZE_CLASSES and isinstance(operations, dict) and all(
                operation in OPERATIONS and isinstance(budget, dict) and all(
                    measure in MEASURES and type(value) in (int, float) and math.isfinite(value) and value > 0
                    for measure, value in budget.items())
                for operation, budget in operations.items())
            for size, operations in members.items())):
        raise ValueError(BUDGETS_SHAPE_ERR_MSG.format(path=path, size_classes=", ".join(SIZE_CLASSES),
                                                      operations=", ".join(OPERATIONS),
                                                      measures=", ".join(MEASURES)))
    return members.get(size_class, {})


def read_store_size(workspace):
    """The size of the store file in bytes and the count of rows of its history."""
    path = workspace / ".exactory" / "research.sqlite3"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        history_rows = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    return path.stat().st_size, history_rows


def read_resident_bytes(pid):
    """The resident memory of a running process. ps reports it in units of 1024 bytes on macOS and Linux."""
    output = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True).stdout.strip()
    return int(output) * 1024 if output else 0


def run_process(command, stop_after_seconds, stop_above_bytes):
    """Run one process to its end; return its outcome and its resource use.

    os.wait4 gives the resource use of this one child, which subprocess does not report. The parent polls the child
    instead of waiting for it, so that it can stop the child, and it stops only a child that it has not reaped, whose
    process id cannot belong to another process."""
    with tempfile.TemporaryFile() as errors:
        load_average = os.getloadavg()[0]
        started = time.monotonic()
        pid = os.posix_spawn(command[0], command, os.environ, file_actions=[
            (os.POSIX_SPAWN_OPEN, 0, os.devnull, os.O_RDONLY, 0),
            (os.POSIX_SPAWN_OPEN, 1, os.devnull, os.O_WRONLY, 0),
            (os.POSIX_SPAWN_DUP2, errors.fileno(), 2)])
        stop = None
        next_watch = started
        while True:
            finished, status, usage = os.wait4(pid, os.WNOHANG)
            if finished:
                break
            now = time.monotonic()
            if stop is None and now >= next_watch:
                if now - started >= stop_after_seconds:
                    stop = "stopped_time"
                elif read_resident_bytes(pid) > stop_above_bytes:
                    stop = "stopped_memory"
                if stop is not None:
                    os.kill(pid, signal.SIGKILL)
                next_watch = now + WATCH_SECONDS
            time.sleep(POLL_SECONDS)
        wall_seconds = time.monotonic() - started
        errors.seek(0)
        error_tail = errors.read()[-ERROR_TAIL_BYTES:].decode("utf-8", "replace")
    exit_code = os.waitstatus_to_exitcode(status)
    outcome = stop or ("completed" if exit_code == 0 else "failed")
    return {"outcome": outcome, "exit_code": exit_code, "wall_seconds": round(wall_seconds, 3),
            "processor_seconds": round(usage.ru_utime + usage.ru_stime, 3),
            "peak_memory_bytes": usage.ru_maxrss * MAXRSS_UNIT_BYTES, "load_average": round(load_average, 2),
            "error": None if outcome == "completed" else error_tail}


def compare_with_budget(result, budget):
    """The measures of one operation that are over their budget, each with its value and its budget."""
    return [{"measure": measure, "value": result[measure], "budget": budget[measure]}
            for measure in MEASURES if measure in budget and result[measure] > budget[measure]]


def format_amount(measure, value):
    unit_size = MEASURES[measure][2]
    return "{:,.0f}".format(value / unit_size) if unit_size > 1 else "{:,.2f}".format(value)


def format_report(report):
    """The Markdown table of the report, one row for each operation beside the size of the record, then one line for
    each measure over its budget."""
    lines = [REPORT_HEADING_MSG.format(**report), "", TABLE_HEAD_MSG]
    over_lines = []
    for row in report["operations"]:
        cells = [Path(report["record"]).name, "{:,.1f}".format(report["store_bytes"] / 10 ** 6),
                 "{:,}".format(report["history_rows"]), row["operation"], row["outcome"]]
        for measure in MEASURES:
            budget = (row["budget"] or {}).get(measure)
            cells.append("%s (%s)" % (format_amount(measure, row[measure]), NO_BUDGET_MSG if budget is None
                                      else "budget " + format_amount(measure, budget)))
        lines.append("| %s |" % " | ".join(cells))
        for over in row["over_budget"]:
            words, unit = MEASURES[over["measure"]][:2]
            over_lines.append(OVER_BUDGET_MSG.format(
                operation=row["operation"], measure=words, unit=unit,
                value=format_amount(over["measure"], over["value"]),
                budget=format_amount(over["measure"], over["budget"])))
    return "\n".join(lines + ([""] + over_lines if over_lines else []))


def measure_record(record, size_class, budgets, budgets_path, stop_after_seconds, stop_above_bytes):
    """Copy the record, run each operation on the copy, and return the report."""
    from research_harness.provenance import plugin_version
    from research_harness.record_check import copy_snapshot
    report = {"record": str(record), "size_class": size_class, "budgets_file": str(budgets_path),
              "python": platform.python_version(), "harness_root": str(ROOT), "harness_version": plugin_version(ROOT),
              "stop_after_seconds": stop_after_seconds, "stop_above_bytes": stop_above_bytes, "operations": []}
    with tempfile.TemporaryDirectory(prefix="core-operation-cost-") as temporary:
        copy = Path(temporary).resolve() / record.name
        copy_snapshot(record, copy)
        report["store_bytes"], report["history_rows"] = read_store_size(copy)
        for operation in OPERATIONS:
            result = run_process(build_operation_command(operation, copy), stop_after_seconds, stop_above_bytes)
            budget = budgets.get(operation)
            report["operations"].append(dict({"operation": operation}, **result, budget=budget,
                                             over_budget=compare_with_budget(result, budget or {})))
            if result["error"]:
                print("%s %s:\n%s" % (operation, result["outcome"], result["error"]), file=sys.stderr)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    measure = commands.add_parser("measure", help="Measure the core operations on a copy of a record.")
    measure.add_argument("record", type=Path, help="A workspace that holds .exactory/research.sqlite3.")
    measure.add_argument("--size-class", required=True, choices=SIZE_CLASSES)
    measure.add_argument("--budgets", type=Path, default=DEFAULT_BUDGETS_PATH)
    measure.add_argument("--json", type=Path, help="Also write the report to this JSON file.")
    measure.add_argument("--stop-after-seconds", type=float, default=DEFAULT_STOP_AFTER_SECONDS)
    measure.add_argument("--stop-above-bytes", type=int, default=DEFAULT_STOP_ABOVE_BYTES)
    operation = commands.add_parser("operation", help="Run one operation in this process.")
    operation.add_argument("name", choices=sorted(OPERATION_RUNNERS))
    operation.add_argument("workspace", type=Path)
    args = parser.parse_args(argv)
    if args.command == "operation":
        OPERATION_RUNNERS[args.name](args.workspace)
        return 0
    record = args.record.resolve()
    if not (record / ".exactory" / "research.sqlite3").is_file():
        parser.error(STORE_MISSING_ERR_MSG.format(record=record))
    scratch = Path(tempfile.gettempdir()).resolve()
    if scratch == record or record in scratch.parents:
        parser.error(SCRATCH_INSIDE_ERR_MSG.format(scratch=scratch, record=record))
    try:
        budgets = read_budgets(args.budgets, args.size_class)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    report = measure_record(record, args.size_class, budgets, args.budgets, args.stop_after_seconds,
                            args.stop_above_bytes)
    print(format_report(report))
    if args.json is not None:
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if all(row["outcome"] == "completed" for row in report["operations"]) else 1


if __name__ == "__main__":
    # A script has its own directory first on sys.path. The operations and the copy import the harness of this tree.
    sys.path.insert(0, str(ROOT))
    sys.exit(main())
