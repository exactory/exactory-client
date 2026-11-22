"""Exercise complete partition accounting and failures in the CI runner."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from ci.reviewer_program_guard import take_machine_reads


ROOT = Path(__file__).resolve().parents[1]


class CIShardTests(unittest.TestCase):
    def api(self):
        path = ROOT / "ci" / "unittest_shards.py"
        self.assertTrue(path.is_file(), "The exhaustive CI runner is not implemented")
        spec = importlib.util.spec_from_file_location("ci_shards_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def fixture(self, outcome="pass", duplicate_ids=False):
        class Sample(unittest.TestCase):
            def test_a(self):
                if outcome == "failure":
                    self.fail("Observed fixture failure")
                if outcome == "skip":
                    self.skipTest("Observed fixture skip")
                if outcome == "error":
                    raise RuntimeError("Observed fixture error")

            def test_b(self):
                pass

            def test_c(self):
                pass

            def test_d(self):
                pass

            def test_e(self):
                pass

        # With duplicate_ids the discovery holds the same case more than once, as when a test module holds an
        # imported test class by name.
        later = ("test_a", "test_c", "test_b") if duplicate_ids else ("test_c", "test_d", "test_e")
        return unittest.TestSuite([unittest.TestSuite([Sample("test_a"), Sample("test_b")])]
                                  + [Sample(name) for name in later])

    def receipt(self, index, outcome="pass", duplicate_ids=False, evidence_tests=()):
        return self.api().run_shard(
            self.fixture(outcome, duplicate_ids), index, 4,
            {"client_commit": "client-commit", "client_tree": "client-tree",
             "marketplace_commit": "marketplace-commit", "python_version": "3.9.25"},
            stream=io.StringIO(), take_machine_reads=take_machine_reads,
            read_evidence_tests=lambda: sorted(evidence_tests),
        )

    def test_partition_executes_every_discovered_entry_including_duplicate_ids(self):
        reports = [self.receipt(index, duplicate_ids=True) for index in range(4)]
        self.assertEqual([r["selected_indices"] for r in reports], [[0, 4], [1], [2], [3]])
        self.assertEqual([r["tests_run"] for r in reports], [2, 1, 1, 1])
        self.assertEqual(reports[0]["all_test_ids"][0], reports[0]["all_test_ids"][2])
        summary = self.api().verify_reports(reports, ["3.9.25"], 4)
        self.assertEqual(summary["tests_run"], 5)
        # A run executes each test once (principle 3): two tests that ran twice fail the verification.
        self.assertFalse(summary["success"], summary)
        self.assertEqual(summary["test_counts"], {"3.9.25": {"executed": 5, "distinct": 3, "evidence": 0}})
        distinct = self.api().verify_reports([self.receipt(index) for index in range(4)], ["3.9.25"], 4)
        self.assertTrue(distinct["success"], distinct)
        self.assertEqual(distinct["test_counts"], {"3.9.25": {"executed": 5, "distinct": 5, "evidence": 0}})

    def test_each_evidence_test_that_a_shard_names_runs_and_passes_in_the_same_run(self):
        api = self.api()
        test_a = next(api.flatten(self.fixture())).id()

        def verify(evidence, outcome="pass"):
            # Shard 0 runs test_a, and shard 3 names the evidence that a guard test of its process required.
            reports = [self.receipt(index, outcome, evidence_tests=evidence if index == 3 else ())
                       for index in range(4)]
            self.assertEqual(reports[3]["evidence_tests"], sorted(evidence))
            return api.verify_reports(reports, ["3.9.25"], 4)

        summary = verify((test_a,))
        self.assertTrue(summary["success"], summary)
        self.assertEqual(summary["test_counts"], {"3.9.25": {"executed": 5, "distinct": 5, "evidence": 1}})
        absent = test_a.rsplit(".", 1)[0] + ".test_absent"
        for evidence, outcome, issue in (((absent,), "pass", "absent from the run: " + absent),
                                         ((test_a,), "skip", "skipped: " + test_a),
                                         ((test_a,), "failure", "did not pass: " + test_a)):
            with self.subTest(outcome=outcome, evidence=evidence):
                summary = verify(evidence, outcome)
                self.assertFalse(summary["success"])
                self.assertIn("3.9.25: evidence test " + issue, summary["issues"])

    def test_the_tests_of_a_shard_group_run_in_one_shard_and_the_others_keep_their_rank(self):
        api = self.api()
        first, second = api.SHARD_GROUPS[0][0], api.SHARD_GROUPS[1][0]
        # A discovery in which the modules of two groups lie between other modules; the discovery failure of a group
        # module belongs to its group.
        ids = (["test_alpha.Case.test_%d" % index for index in range(3)]
               + ["%s.Case.test_%d" % (first, index) for index in range(3)]
               + ["test_beta.Case.test_%d" % index for index in range(4)]
               + ["unittest.loader._FailedTest.%s" % second, "test_gamma.Case.test_0"])
        shards = [api.select_shard_indices(ids, index, 4) for index in range(4)]
        self.assertEqual(sorted(index for shard in shards for index in shard), list(range(len(ids))))
        # Group 0 goes to shard 0 and group 1 to shard 1; the eight other tests take the shards 0 to 3 in turn.
        self.assertEqual(shards, [[0, 3, 4, 5, 7], [1, 8, 10], [2, 9], [6, 11]])
        # Without a group module the partition is test i in shard i mod the count.
        plain = ["test_alpha.Case.test_%d" % index for index in range(9)]
        self.assertEqual([api.select_shard_indices(plain, index, 4) for index in range(4)],
                         [[0, 4, 8], [1, 5], [2, 6], [3, 7]])

    def test_an_evidence_test_is_checked_without_a_run(self):
        from ci.evidence_tests import inspect_evidence_tests
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "test_evidence_sample.py").write_text(
                "import unittest\n"
                "class Sample(unittest.TestCase):\n"
                "    def test_ok(self): pass\n"
                "    @unittest.skip('a mark')\n    def test_skipped(self): pass\n"
                "    @unittest.expectedFailure\n    def test_expected(self): self.fail()\n"
                "@unittest.skip('a mark')\n"
                "class Skipped(unittest.TestCase):\n"
                "    def test_any(self): pass\n")
            sys.path.insert(0, directory)
            self.addCleanup(sys.path.remove, directory)
            self.addCleanup(sys.modules.pop, "test_evidence_sample", None)
            names = ("Sample.test_ok", "Sample.test_skipped", "Sample.test_expected", "Skipped.test_any",
                     "Sample.test_absent", "Sample")
            problems = inspect_evidence_tests(["test_evidence_sample." + name for name in names])
        self.assertEqual(problems, {
            "test_evidence_sample.Sample.test_skipped": "skip_mark",
            "test_evidence_sample.Sample.test_expected": "expected_failure_mark",
            "test_evidence_sample.Skipped.test_any": "skip_mark",
            "test_evidence_sample.Sample.test_absent": "not_one_loadable_test",
            "test_evidence_sample.Sample": "not_one_loadable_test"})

    def test_observed_failure_error_or_skip_prevents_a_successful_shard(self):
        for outcome in ("failure", "error", "skip"):
            with self.subTest(outcome=outcome):
                report = self.receipt(0, outcome)
                self.assertFalse(report["success"])
                self.assertFalse(self.api().verify_reports(
                    [report] + [self.receipt(i) for i in (1, 2, 3)], ["3.9.25"], 4
                )["success"])

    def test_missing_repeated_or_cancelled_shards_cannot_pass_aggregation(self):
        reports = [self.receipt(index) for index in range(4)]
        cancelled = copy.deepcopy(reports)
        cancelled[2]["status"] = "running"
        for incomplete in (reports[:-1], reports + [reports[0]], cancelled):
            with self.subTest(count=len(incomplete)):
                self.assertFalse(self.api().verify_reports(incomplete, ["3.9.25"], 4)["success"])

    def test_changed_selection_or_execution_count_cannot_pass_aggregation(self):
        original = [self.receipt(index) for index in range(4)]
        for field, value in (("selected_indices", [0]), ("tests_run", 0),
                             ("executed_test_ids", []), ("success", False)):
            reports = copy.deepcopy(original)
            reports[1][field] = value
            self.assertFalse(self.api().verify_reports(reports, ["3.9.25"], 4)["success"], field)

    def test_changed_discovery_or_source_revision_cannot_pass_aggregation(self):
        original = [self.receipt(index) for index in range(4)]
        for field in ("all_test_ids", "client_commit", "client_tree", "marketplace_commit"):
            reports = copy.deepcopy(original)
            if field == "all_test_ids":
                reports[2][field] = list(reversed(reports[2][field]))
            else:
                reports[2]["provenance"][field] = "different"
            self.assertFalse(self.api().verify_reports(reports, ["3.9.25"], 4)["success"], field)

    def test_every_requested_python_version_is_required(self):
        reports = [self.receipt(index) for index in range(4)]
        self.assertFalse(self.api().verify_reports(reports, ["3.9.25", "3.12.14"], 4)["success"])

    def test_empty_discovery_and_invalid_partition_are_rejected(self):
        api = self.api()
        for index, count in ((-1, 4), (4, 4), (0, 0)):
            with self.assertRaises(ValueError):
                api.run_shard(self.fixture(), index, count, {}, stream=io.StringIO(),
                              take_machine_reads=take_machine_reads)
        with self.assertRaises(ValueError):
            api.run_shard(unittest.TestSuite(), 0, 4, {}, stream=io.StringIO(), take_machine_reads=take_machine_reads)

    def test_discovery_import_errors_remain_failed_test_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "test_broken_import.py").write_text("raise RuntimeError('broken import')\n")
            suite = unittest.TestLoader().discover(directory)
            report = self.api().run_shard(suite, 0, 1, {}, stream=io.StringIO(), take_machine_reads=take_machine_reads)
            self.assertFalse(report["success"])
            self.assertEqual(report["tests_run"], 1)
            self.assertEqual(len(report["errors"]), 1)

    def test_each_executed_test_has_its_wall_time_and_processor_time_with_its_class_fixture(self):
        class Slow(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                time.sleep(0.2)

            def test_a(self):
                pass

            def test_b(self):
                pass

        report = self.api().run_shard(unittest.TestSuite([Slow("test_a"), Slow("test_b")]), 0, 1, {},
                                      stream=io.StringIO(), take_machine_reads=take_machine_reads)
        self.assertEqual([row[0] for row in report["test_seconds"]], report["executed_test_ids"])
        # The class fixture runs before the first test, so its time counts for that test.
        self.assertGreaterEqual(report["test_seconds"][0][1], 0.2)
        for _, wall_seconds, processor_seconds in report["test_seconds"]:
            self.assertGreaterEqual(wall_seconds, 0)
            self.assertGreaterEqual(processor_seconds, 0)
        self.assertGreaterEqual(report["wall_seconds"], 0.2)
        self.assertGreaterEqual(report["processor_seconds"], 0)

    def test_a_group_keeps_the_tests_of_its_modules_in_the_order_of_its_shard(self):
        def make_tests(module):
            case = type("Case", (unittest.TestCase,),
                        dict({"test_%d" % index: lambda self: None for index in range(3)}, __module__=module))
            return [case("test_%d" % index) for index in range(3)]

        suite = unittest.TestSuite(make_tests("test_one") + make_tests("test_two") + make_tests("test_three"))
        report = self.api().run_shard(suite, 1, 2, {}, modules={"test_one", "test_three"}, stream=io.StringIO(),
                                      take_machine_reads=take_machine_reads)
        # Tests 0 to 8 in the order of discovery; shard 1 of 2 holds 1, 3, 5 and 7.
        self.assertEqual(report["selected_indices"], [1, 7])
        self.assertEqual(report["executed_test_ids"], ["test_one.Case.test_1", "test_three.Case.test_1"])
        self.assertTrue(report["success"])

    def test_a_group_keeps_the_discovery_failure_of_its_module(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "test_broken_import.py").write_text("raise RuntimeError('broken import')\n")
            Path(directory, "test_fine.py").write_text(
                "import unittest\nclass Fine(unittest.TestCase):\n    def test_a(self): pass\n")
            suite = unittest.TestLoader().discover(directory)
            api = self.api()
            kept = api.run_shard(suite, 0, 1, {}, modules={"test_broken_import"}, stream=io.StringIO(),
                                 take_machine_reads=take_machine_reads)
            self.assertEqual(kept["errors"], ["unittest.loader._FailedTest.test_broken_import"])
            other = api.run_shard(unittest.TestLoader().discover(directory), 0, 1, {}, modules={"test_fine"},
                                  stream=io.StringIO(), take_machine_reads=take_machine_reads)
            self.assertEqual(other["executed_test_ids"], ["test_fine.Fine.test_a"])
            self.assertTrue(other["success"])

    def test_the_module_of_each_kind_of_test_id(self):
        api = self.api()
        for test_id in ("test_x.Case.test_a", "test_x.Case.test_a (cause='a.b')", "setUpClass (test_x.Case)",
                        "setUpModule (test_x)", "tearDownClass (test_x.Case)", "unittest.loader._FailedTest.test_x"):
            with self.subTest(test_id=test_id):
                self.assertEqual(api.read_test_module(test_id), "test_x")

    def test_a_module_with_a_test_above_300_seconds_keeps_its_shard_when_tests_are_added_before_it(self):
        api = self.api()
        carried = "test_openness_carried_integration.Record12ShapeTests.test_0"
        slate = "test_review_parts_slate.SlateSeriesTests.test_A1_incident_28600000_character_slate_reaches_value_review"
        shards_of_slate = set()
        for added in range(api.SHARD_COUNT + 1):
            # The discovery of the incident: other tests, the first group, other tests, the slate test.
            ids = (["test_alpha.Case.test_%d" % index for index in range(added)] + [carried]
                   + ["test_beta.Case.test_%d" % index for index in range(3)] + [slate])
            holders = {test_id: shard for shard in range(api.SHARD_COUNT)
                       for index in api.select_shard_indices(ids, shard, api.SHARD_COUNT)
                       for test_id in [ids[index]]}
            self.assertEqual(len(holders), len(ids))
            self.assertNotEqual(holders[slate], holders[carried])
            shards_of_slate.add(holders[slate])
        self.assertEqual(len(shards_of_slate), 1)

    def test_each_module_of_a_shard_group_is_a_test_module_of_one_group_and_no_shard_holds_two_groups(self):
        api = self.api()
        modules = [module for group in api.SHARD_GROUPS for module in group]
        self.assertEqual(len(modules), len(set(modules)))
        for module in modules:
            with self.subTest(module=module):
                self.assertTrue((ROOT / "tests" / (module + ".py")).is_file())
        self.assertLessEqual(len(api.SHARD_GROUPS), api.SHARD_COUNT)

    def test_the_shard_count_is_the_count_of_the_ci_workflow(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        count = self.api().SHARD_COUNT
        self.assertIn("shard: %s" % list(range(count)), workflow)
        # The run of each shard and the verification of the receipts.
        self.assertEqual(workflow.count("--shard-count %d\n" % count), 2)

    def test_the_budgets_file_holds_every_run_budget(self):
        budgets = self.api().read_budgets()
        self.assertEqual(set(budgets), {"test_processes", "runs", "core_operations"})
        self.assertEqual({run: set(members) for run, members in budgets["runs"].items()},
                         {"implementer_run": {"wall_seconds"}, "must_pass_run": {"wall_seconds"},
                          "full_suite_run": {"wall_seconds", "shard_wall_seconds"}})
        self.assertIsInstance(budgets["core_operations"], dict)

    def test_a_budgets_file_without_a_run_budget_or_with_an_invalid_value_is_refused(self):
        api = self.api()
        budgets = api.read_budgets()
        broken = []
        for run, member in api.RUN_BUDGET_MEMBERS:
            for value in (None, 0, True, 1.5):
                variant = copy.deepcopy(budgets)
                if value is None:
                    del variant["runs"][run][member]
                else:
                    variant["runs"][run][member] = value
                broken.append(variant)
        broken += [dict(budgets, test_processes=0), dict(budgets, core_operations=[]), {"runs": budgets["runs"]}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "budgets.json")
            for variant in broken:
                with self.subTest(variant=variant):
                    path.write_text(json.dumps(variant), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        api.read_budgets(path)

    def test_a_run_over_its_budget_prints_one_more_line_with_both_numbers(self):
        api = self.api()
        self.assertEqual(len(api.format_budget_report("must_pass_run", 2700, 2700.0, 4000.0)), 1)
        over = api.format_budget_report("must_pass_run", 2700, 2800.5, 4000.0)
        self.assertEqual(len(over), 2)
        self.assertIn("2800.5", over[1])
        self.assertIn("2700", over[1])

    def test_cli_run_reports_both_times_and_compares_with_the_shard_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            client = base / "client"
            self.copy_runner(client)
            (client / "tests").mkdir()
            (client / "tests/test_sample.py").write_text(
                "import unittest\nclass Sample(unittest.TestCase):\n"
                "    def test_a(self): pass\n    def test_b(self): pass\n")
            self.git_fixture(client)
            self.git_fixture(base / "marketplace")
            result = base / "result.json"
            run = subprocess.run([sys.executable, str(client / "ci/unittest_shards.py"), "run", "--shard-index", "0",
                                  "--shard-count", "1", "--marketplace", str(base / "marketplace"),
                                  "--result", str(result)], cwd=client, capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr)
            report = json.loads(result.read_text())
            self.assertEqual([row[0] for row in report["test_seconds"]], report["executed_test_ids"])
            self.assertGreater(report["wall_seconds"], 0)
            self.assertGreater(report["processor_seconds"], 0)
            self.assertIn("full_suite_run.shard_wall_seconds", run.stderr)
            budgets = json.loads((client / "ci/budgets.json").read_text(encoding="utf-8"))
            self.assertIn(str(budgets["runs"]["full_suite_run"]["shard_wall_seconds"]), run.stderr)
            summary = json.loads(run.stdout)
            self.assertNotIn("test_seconds", summary)
            self.assertEqual(sorted(row[0] for row in summary["slowest_tests"]), sorted(report["executed_test_ids"]))

    def git_fixture(self, path):
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        subprocess.run(["git", "-C", str(path), "add", "."], check=True)
        tree = subprocess.check_output(["git", "-C", str(path), "write-tree"], text=True).strip()
        environment = dict(os.environ, GIT_AUTHOR_NAME="CI fixture", GIT_COMMITTER_NAME="CI fixture",
                           GIT_AUTHOR_EMAIL="ci@example.invalid", GIT_COMMITTER_EMAIL="ci@example.invalid")
        commit = subprocess.check_output(["git", "-C", str(path), "commit-tree", tree],
                                         input="Record CI test fixture\n", text=True, env=environment).strip()
        subprocess.run(["git", "-C", str(path), "update-ref", "HEAD", commit], check=True)
        return commit, tree

    def copy_runner(self, client):
        (client / "ci").mkdir(parents=True)
        for name in ("unittest_shards.py", "reviewer_program_guard.py", "evidence_tests.py", "budgets.json"):
            shutil.copyfile(ROOT / "ci" / name, client / "ci" / name)

    def write_guarded_sample(self, path):
        # test_b catches the stop of the guard, so it passes in a run without the runner.
        path.write_text(
            "import shutil\nimport unittest\nfrom ci.reviewer_program_guard import is_guard_installed\n"
            "class Sample(unittest.TestCase):\n"
            "    def test_a(self): self.assertTrue(is_guard_installed())\n"
            "    def test_b(self):\n"
            "        try:\n            shutil.which('codex')\n"
            "        except BaseException:\n            pass\n"
            "    def test_c(self): self.assertIsNotNone(shutil.which('sh'))\n"
        )

    def test_run_modules_help_describes_the_reviewer_program_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(ROOT / "ci/unittest_shards.py"), "run-modules", "--help"],
                                    cwd=directory, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("reviewer program guard", " ".join(result.stdout.split()))

    def test_cli_run_gives_each_test_the_reviewer_program_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            client = base / "client"
            self.copy_runner(client)
            (client / "tests").mkdir()
            self.write_guarded_sample(client / "tests/test_sample.py")
            self.git_fixture(client)
            self.git_fixture(base / "marketplace")
            result = base / "result.json"
            run = subprocess.run([sys.executable, str(client / "ci/unittest_shards.py"), "run", "--shard-index", "0",
                                  "--shard-count", "1", "--marketplace", str(base / "marketplace"),
                                  "--result", str(result)], cwd=client, capture_output=True, text=True, timeout=60)
            report = json.loads(result.read_text())
            self.assertEqual(run.returncode, 1, run.stderr)
            self.assertEqual(report["tests_run"], 3)
            self.assertEqual(report["errors"], ["test_sample.Sample.test_b"])
            self.assertEqual(report["failures"], [])
            self.assertIn("looked for the reviewer program codex", run.stderr)

    def test_cli_run_modules_gives_named_plugin_and_harness_modules_the_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Path(directory) / "client"
            self.copy_runner(client)
            (client / "tests").mkdir()
            self.write_guarded_sample(client / "tests/test_sample.py")
            (client / "tests/test_other.py").write_text(
                "import unittest\nclass Other(unittest.TestCase):\n    def test_d(self): pass\n")
            # The plugin tests directory is not a package, so the harness package keeps the name tests.
            harness = client / "harness" / "tests"
            harness.mkdir(parents=True)
            (harness / "__init__.py").write_text("")
            (harness / "support.py").write_text("VALUE = 'harness sibling support'\n")
            (harness / "test_inner.py").write_text(
                "import unittest\nfrom ci.reviewer_program_guard import is_guard_installed\n"
                "from tests.support import VALUE\n"
                "class Inner(unittest.TestCase):\n"
                "    def test_e(self):\n"
                "        self.assertTrue(is_guard_installed())\n"
                "        self.assertEqual(VALUE, 'harness sibling support')\n"
            )
            command = [sys.executable, str(client / "ci/unittest_shards.py"), "run-modules"]
            plugin = subprocess.run(command + ["tests.test_sample", "test_other"], cwd=client,
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(plugin.returncode, 1, plugin.stderr)
            summary = json.loads(plugin.stdout)
            self.assertEqual(summary["tests_run"], 4)
            self.assertEqual(summary["errors"], ["tests.test_sample.Sample.test_b"])
            self.assertEqual(summary["failures"], [])
            self.assertIn("looked for the reviewer program codex", plugin.stderr)
            inner = subprocess.run(command + ["--top-level-directory", "harness", "tests.test_inner"], cwd=client,
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(inner.returncode, 0, inner.stderr)
            self.assertEqual(json.loads(inner.stdout)["tests_run"], 1)

    def run_package_fixture(self, outcome, with_top_level=True):
        scratch = tempfile.TemporaryDirectory(prefix="ci package ")
        self.addCleanup(scratch.cleanup)
        base = Path(scratch.name)
        client = base / "client"
        self.copy_runner(client)
        tests = client / "harness" / "tests"
        tests.mkdir(parents=True)
        (tests / "__init__.py").write_text("")
        (tests / "support.py").write_text("VALUE = 'harness sibling support'\n")
        (tests / "test_sample.py").write_text(
            ("from tests.support import VALUE\n" if with_top_level else
             "from support import VALUE\n") +
            "import unittest\n"
            "class Sample(unittest.TestCase):\n"
            "    def test_sibling(self):\n"
            "        self.assertEqual(VALUE, 'harness sibling support')\n" +
            ("        self.skipTest('observed harness skip')\n" if outcome == "skip" else "")
        )
        # The plugin's non-package tests directory must not shadow the harness.
        (client / "tests").mkdir()
        (client / "tests" / "support.py").write_text("raise RuntimeError('wrong tests directory')\n")
        self.git_fixture(client)
        self.git_fixture(base / "marketplace")
        result = base / "result.json"
        command = [sys.executable, str(client / "ci/unittest_shards.py"), "run",
                   "--start-directory", "harness/tests", "--shard-index", "0",
                   "--shard-count", "1", "--marketplace", str(base / "marketplace"),
                   "--result", str(result)]
        if with_top_level:
            command.extend(["--top-level-directory", "harness"])
        process = subprocess.run(command, cwd=client, capture_output=True, text=True, timeout=30)
        return process, json.loads(result.read_text()) if result.is_file() else None

    def test_cli_top_level_package_imports_its_sibling_support(self):
        process, report = self.run_package_fixture("pass")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(report["success"])
        self.assertEqual(report["tests_run"], 1)
        self.assertEqual(report["all_test_ids"], ["tests.test_sample.Sample.test_sibling"])
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["skipped"], [])

    def test_cli_top_level_package_skip_fails_the_shard(self):
        process, report = self.run_package_fixture("skip")
        self.assertIsNotNone(report, process.stderr)
        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertFalse(report["success"])
        self.assertEqual(report["tests_run"], 1)
        self.assertEqual(report["skipped"], [
            ["tests.test_sample.Sample.test_sibling", "observed harness skip"]])
        self.assertEqual(report["errors"], [])

    def test_cli_default_discovery_keeps_top_level_module_names(self):
        process, report = self.run_package_fixture("pass", with_top_level=False)
        self.assertIsNotNone(report, process.stderr)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(report["all_test_ids"], ["test_sample.Sample.test_sibling"])
        self.assertEqual(report["tests_run"], 1)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["skipped"], [])

    def test_cli_verify_reads_the_evidence_that_a_guard_test_required_from_the_run(self):
        # A guard test requires test_evidence of another module, which runs in the same shard. The runner writes the
        # required test into the result of the shard, and verify requires it to have run and passed.
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            client = base / "client"
            self.copy_runner(client)
            (client / "tests").mkdir()
            (client / "tests/test_guard.py").write_text(
                "import unittest\nfrom ci.evidence_tests import require_evidence_tests\n"
                "class Guard(unittest.TestCase):\n"
                "    def test_requires(self):\n"
                "        self.assertEqual(require_evidence_tests(['test_proof.Proof.test_evidence']), {})\n")
            client_commit, client_tree = self.git_fixture(client)
            marketplace_commit, _ = self.git_fixture(base / "marketplace")
            command = [sys.executable, str(client / "ci/unittest_shards.py")]
            summary = base / "summary.json"
            verify = command + ["verify", "--receipts", str(base / "receipts"), "--python-versions",
                                platform.python_version(), "--shard-count", "1", "--client-commit", client_commit,
                                "--client-tree", client_tree, "--marketplace-commit", marketplace_commit,
                                "--result", str(summary)]
            for is_skipped, exit_status in ((False, 0), (True, 1)):
                with self.subTest(is_skipped=is_skipped):
                    # The skip comes at run time, so the check without a run does not see it.
                    (client / "tests/test_proof.py").write_text(
                        "import unittest\nclass Proof(unittest.TestCase):\n"
                        "    def test_evidence(self):\n"
                        + ("        self.skipTest('a run-time skip')\n" if is_skipped else "        pass\n"))
                    run = subprocess.run(command + ["run", "--shard-index", "0", "--shard-count", "1", "--marketplace",
                                                    str(base / "marketplace"), "--result",
                                                    str(base / "receipts/0/shard-result.json")],
                                         cwd=client, capture_output=True, text=True, timeout=60)
                    self.assertEqual(run.returncode, exit_status, run.stderr)
                    receipt = json.loads((base / "receipts/0/shard-result.json").read_text())
                    self.assertEqual(receipt["evidence_tests"], ["test_proof.Proof.test_evidence"])
                    checked = subprocess.run(verify, cwd=client, capture_output=True, text=True, timeout=60)
                    self.assertEqual(checked.returncode, exit_status, checked.stderr)
                    result = json.loads(summary.read_text())
                    self.assertEqual(result["test_counts"][platform.python_version()],
                                     {"executed": 2, "distinct": 2, "evidence": 1})
                    self.assertEqual("%s: evidence test skipped: test_proof.Proof.test_evidence"
                                     % platform.python_version() in result["issues"], is_skipped)

    def test_cli_receipts_reject_an_actual_failure_and_a_missing_shard(self):
        self.api()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            client = base / "client"
            self.copy_runner(client)
            (client / "tests").mkdir()
            (client / "tests/test_sample.py").write_text(
                "import unittest\nclass Sample(unittest.TestCase):\n"
                "    def test_a(self): pass\n"
                "    def test_b(self): self.fail('real failed test')\n"
                "    def test_c(self): pass\n"
                "    def test_d(self): pass\n"
            )
            client_commit, client_tree = self.git_fixture(client)
            marketplace_commit, _ = self.git_fixture(base / "marketplace")
            records = base / "receipts"
            command = [sys.executable, str(client / "ci/unittest_shards.py")]
            exits = []
            for index in range(4):
                run = subprocess.run(command + ["run", "--shard-index", str(index), "--shard-count", "4",
                                     "--marketplace", str(base / "marketplace"), "--result",
                                     str(records / str(index) / "shard-result.json")], cwd=client,
                                     capture_output=True, text=True)
                exits.append(run.returncode)
            self.assertEqual(exits, [0, 1, 0, 0])
            sample = json.loads((records / "0/shard-result.json").read_text())
            self.assertEqual(sample["provenance"]["client_commit"], client_commit)
            summary = base / "summary.json"
            verify = command + ["verify", "--receipts", str(records), "--python-versions",
                                sample["provenance"]["python_version"], "--shard-count", "4",
                                "--client-commit", client_commit, "--client-tree", client_tree,
                                "--marketplace-commit", marketplace_commit, "--result", str(summary)]
            run = subprocess.run(verify, cwd=client, capture_output=True, text=True)
            self.assertEqual(run.returncode, 1, run.stderr)
            self.assertFalse(json.loads(summary.read_text())["success"])
            (records / "1/shard-result.json").unlink()
            run = subprocess.run(verify, cwd=client, capture_output=True, text=True)
            self.assertEqual(run.returncode, 1, run.stderr)
            self.assertIn("Missing or repeated shards", json.loads(summary.read_text())["issues"])
