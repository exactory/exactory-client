"""Exercise complete partition accounting and failures in the CI runner."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CIShardTests(unittest.TestCase):
    def api(self):
        path = ROOT / "ci" / "unittest_shards.py"
        self.assertTrue(path.is_file(), "The exhaustive CI runner is not implemented")
        spec = importlib.util.spec_from_file_location("ci_shards_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def fixture(self, outcome="pass"):
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

        # Discovery can contain the same imported case more than once.
        return unittest.TestSuite([
            unittest.TestSuite([Sample("test_a"), Sample("test_b")]),
            Sample("test_a"), Sample("test_c"), Sample("test_b"),
        ])

    def receipt(self, index, outcome="pass"):
        return self.api().run_shard(
            self.fixture(outcome), index, 4,
            {"client_commit": "client-commit", "client_tree": "client-tree",
             "marketplace_commit": "marketplace-commit", "python_version": "3.9.25"},
            stream=io.StringIO(),
        )

    def test_partition_executes_every_discovered_entry_including_duplicate_ids(self):
        reports = [self.receipt(index) for index in range(4)]
        self.assertEqual([r["selected_indices"] for r in reports], [[0, 4], [1], [2], [3]])
        self.assertEqual([r["tests_run"] for r in reports], [2, 1, 1, 1])
        self.assertEqual(reports[0]["all_test_ids"][0], reports[0]["all_test_ids"][2])
        summary = self.api().verify_reports(reports, ["3.9.25"], 4)
        self.assertTrue(summary["success"], summary)
        self.assertEqual(summary["tests_run"], 5)

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
                api.run_shard(self.fixture(), index, count, {}, stream=io.StringIO())
        with self.assertRaises(ValueError):
            api.run_shard(unittest.TestSuite(), 0, 4, {}, stream=io.StringIO())

    def test_discovery_import_errors_remain_failed_test_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "test_broken_import.py").write_text("raise RuntimeError('broken import')\n")
            suite = unittest.TestLoader().discover(directory)
            report = self.api().run_shard(suite, 0, 1, {}, stream=io.StringIO())
            self.assertFalse(report["success"])
            self.assertEqual(report["tests_run"], 1)
            self.assertEqual(len(report["errors"]), 1)

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

    def test_cli_receipts_reject_an_actual_failure_and_a_missing_shard(self):
        self.api()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            client = base / "client"
            (client / "ci").mkdir(parents=True)
            (client / "tests").mkdir()
            shutil.copyfile(ROOT / "ci/unittest_shards.py", client / "ci/unittest_shards.py")
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
