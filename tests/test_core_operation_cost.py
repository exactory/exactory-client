"""The command ci/core_operation_cost.py measures the core operations of the store on a copy of a record."""

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from ci import core_operation_cost
from research_harness.storage import Store


PLUGIN = Path(__file__).resolve().parents[1]
REPORT_FIELDS = {"record", "size_class", "budgets_file", "python", "harness_root", "harness_version",
                 "stop_after_seconds", "stop_above_bytes", "store_bytes", "history_rows", "operations"}
OPERATION_FIELDS = {"operation", "outcome", "exit_code", "wall_seconds", "processor_seconds", "peak_memory_bytes",
                    "load_average", "error", "budget", "over_budget"}


def read_file_digests(root):
    """{relative path: SHA-256 of its bytes} for each file under root."""
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


class CoreOperationCostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """The smallest record: one store that exactory-research init created. One measurement of it."""
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name).resolve()
        cls.record = cls.root / "record"
        cls.record.mkdir()
        payload = cls.root / "init.json"
        payload.write_text(json.dumps({"profile": "research", "target": None}))
        subprocess.run([sys.executable, str(PLUGIN / "bin" / "exactory-research"), "init", "--workspace",
                        str(cls.record), "--file", str(payload), "--expected-revision", "0", "--request-id", "init"],
                       check=True, capture_output=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        cls.store_path = cls.record / ".exactory" / "research.sqlite3"
        cls.revision = Store(cls.record).revision
        cls.digests = read_file_digests(cls.record)
        cls.budgets = cls.root / "budgets.json"
        cls.budgets.write_text(json.dumps({"core_operations": {"small": {
            "open": {"wall_seconds": 600, "peak_memory_bytes": 10 ** 12},
            "status": {"processor_seconds": 1e-6}}}}))
        report_path = cls.root / "report.json"
        cls.result = subprocess.run(
            [sys.executable, str(PLUGIN / "ci" / "core_operation_cost.py"), "measure", str(cls.record),
             "--size-class", "small", "--budgets", str(cls.budgets), "--json", str(report_path)],
            capture_output=True, text=True)
        cls.report = json.loads(report_path.read_text()) if report_path.exists() else None

    def make_directory(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name)

    def measure_in_process(self, *options):
        """Run the command in this process and return its exit status, its report and its output."""
        report_path = self.make_directory() / "report.json"
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            status = core_operation_cost.main(["measure", str(self.record), "--size-class", "small", "--budgets",
                                               str(self.budgets), "--json", str(report_path), *options])
        return status, json.loads(report_path.read_text()), output.getvalue()

    def test_the_report_gives_the_costs_of_each_operation_and_the_size_of_the_store(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr)
        self.assertEqual(set(self.report), REPORT_FIELDS)
        self.assertEqual(self.report["store_bytes"], self.store_path.stat().st_size)
        self.assertEqual(self.report["history_rows"], self.revision)
        self.assertEqual([row["operation"] for row in self.report["operations"]], list(core_operation_cost.OPERATIONS))
        for row in self.report["operations"]:
            with self.subTest(operation=row["operation"]):
                self.assertEqual(set(row), OPERATION_FIELDS)
                self.assertEqual((row["outcome"], row["exit_code"], row["error"]), ("completed", 0, None))
                for measure in core_operation_cost.MEASURES:
                    self.assertGreater(row[measure], 0)
        # Each row gives the record, the size of its store and the length of its history beside the operation.
        table_rows = [[cell.strip() for cell in line.strip("|").split("|")]
                      for line in self.result.stdout.splitlines() if line.startswith("| ")][2:]
        self.assertEqual([[row[0], row[2], row[3], row[4]] for row in table_rows],
                         [["record", str(self.revision), name, "completed"] for name in core_operation_cost.OPERATIONS])

    def test_a_measure_over_its_budget_has_one_line_and_an_operation_without_a_member_has_no_budget(self):
        open_row, status_row, write_row = self.report["operations"]
        self.assertEqual((open_row["budget"], open_row["over_budget"]),
                         ({"wall_seconds": 600, "peak_memory_bytes": 10 ** 12}, []))
        self.assertEqual(status_row["over_budget"], [{"measure": "processor_seconds",
                                                      "value": status_row["processor_seconds"], "budget": 1e-6}])
        self.assertEqual((write_row["budget"], write_row["over_budget"]), (None, []))
        over_lines = [line for line in self.result.stdout.splitlines() if line.startswith("- ")]
        self.assertEqual(len(over_lines), 1)
        self.assertTrue(over_lines[0].startswith("- status:"), over_lines)
        self.assertEqual(self.result.returncode, 0)

    def test_the_record_keeps_its_bytes(self):
        self.assertEqual(read_file_digests(self.record), self.digests)

    def test_an_operation_past_its_time_limit_is_stopped_and_the_command_ends_with_status_1(self):
        status, report, _ = self.measure_in_process("--stop-after-seconds", "0")
        self.assertEqual(status, 1)
        self.assertEqual([(row["outcome"], row["exit_code"]) for row in report["operations"]],
                         [("stopped_time", -9)] * 3)
        self.assertEqual(read_file_digests(self.record), self.digests)

    def test_an_operation_past_its_memory_limit_is_stopped(self):
        with mock.patch.object(core_operation_cost, "read_resident_bytes", return_value=10 ** 12):
            status, report, _ = self.measure_in_process()
        self.assertEqual(status, 1)
        self.assertEqual([(row["outcome"], row["exit_code"]) for row in report["operations"]],
                         [("stopped_memory", -9)] * 3)

    def test_a_record_without_a_store_and_a_budgets_file_of_another_shape_are_refused(self):
        empty = self.make_directory()
        budgets = empty / "budgets.json"
        budgets.write_text(json.dumps({"core_operations": {"small": {"status": {"wall_minutes": 1}}}}))
        cases = {"record without a store": ["measure", str(empty), "--size-class", "small",
                                            "--budgets", str(self.budgets)],
                 "budgets file of another shape": ["measure", str(self.record), "--size-class", "small",
                                                   "--budgets", str(budgets)],
                 "missing budgets file": ["measure", str(self.record), "--size-class", "small",
                                          "--budgets", str(empty / "absent.json")]}
        for name, arguments in cases.items():
            with self.subTest(name), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    core_operation_cost.main(arguments)
                self.assertEqual(raised.exception.code, 2)
        self.assertEqual(read_file_digests(self.record), self.digests)

    def test_a_temporary_directory_inside_the_record_is_refused(self):
        with mock.patch.object(tempfile, "tempdir", str(self.record)), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                core_operation_cost.main(["measure", str(self.record), "--size-class", "small",
                                          "--budgets", str(self.budgets)])
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(read_file_digests(self.record), self.digests)


if __name__ == "__main__":
    unittest.main()
