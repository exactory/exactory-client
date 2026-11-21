"""The must-pass set of a release holds the fixed modules and the test modules that a diff reaches (ci/must_pass.py),
and its run groups the tests into processes as the shards of the CI do."""

import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from ci import must_pass


GIT_ENVIRONMENT = dict(os.environ, GIT_AUTHOR_NAME="CI fixture", GIT_COMMITTER_NAME="CI fixture",
                       GIT_AUTHOR_EMAIL="ci@example.invalid", GIT_COMMITTER_EMAIL="ci@example.invalid")
EMPTY_TEST_MODULE = "import unittest\n\n\nclass Fixed(unittest.TestCase):\n    def test_runs(self):\n        pass\n"


def write_tree(root, files):
    """Write a small plugin tree: the fixed modules of the set as empty test modules, and the given files by path."""
    for name in must_pass.ALWAYS + must_pass.HARNESS_PINS:
        files.setdefault("tests/%s.py" % name, EMPTY_TEST_MODULE)
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")


def copy_runner(root):
    """Copy the must-pass command, the runner, its guard and the budgets into a small plugin tree."""
    (root / "ci").mkdir(parents=True, exist_ok=True)
    for name in ("must_pass.py", "unittest_shards.py", "reviewer_program_guard.py", "budgets.json"):
        shutil.copyfile(must_pass.ROOT / "ci" / name, root / "ci" / name)


class MustPassSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = must_pass.describe_test_files(must_pass.ROOT)

    def find(self, *changed):
        return must_pass.find_touched_modules(self.files, changed)

    def select(self, *changed):
        # These cases change no file that a fixture or a module of ci names, so the tree needs no carriers.
        return must_pass.select_modules({"tests": self.files, "carriers": {}}, list(changed))

    def test_the_fixed_modules_exist(self):
        for name in must_pass.ALWAYS + must_pass.HARNESS_PINS:
            with self.subTest(module=name):
                self.assertIn(name, self.files)

    def test_an_empty_diff_selects_the_fixed_modules_only(self):
        self.assertEqual(self.find(), set())
        self.assertEqual(self.select(), sorted(must_pass.ALWAYS))

    def test_a_changed_fixture_builder_selects_the_test_that_reaches_it_through_other_test_modules(self):
        # 0.91.8 changed a default text of this file. The parity test reaches the file through the fixtures of other
        # test modules, and it failed in the CI because no list of the release held it.
        found = self.find("tests/test_research_problem_claims.py")
        self.assertIn("test_review_request_parity", found)
        self.assertIn("test_problem_accounts", found)
        self.assertNotIn("test_review_request_parity", self.find("tests/test_must_pass.py"))

    def test_a_changed_test_helper_selects_its_importers(self):
        self.assertEqual(self.find("tests/payload_changes.py"), {"test_problem_payload_changes"})

    def test_a_changed_harness_module_selects_the_test_modules_that_import_it(self):
        found = self.find("research_harness/problem_accounts.py")
        self.assertIn("test_problem_accounts", found)
        # The importers of the importers belong to the full suites, not to this set.
        self.assertNotIn("test_review_request_parity", found)

    def test_a_changed_command_and_a_changed_document_select_the_test_modules_that_name_them(self):
        self.assertIn("test_math_problems_cli", self.find("bin/exactory"))
        self.assertIn("test_research_guidance", self.find("docs/research-cli.md"))

    def test_a_change_of_the_harness_or_of_a_fixture_puts_the_slow_pins_first(self):
        for changed in ("research_harness/problem_accounts.py", "tests/fixtures/capacity_packet_schema.json"):
            with self.subTest(changed=changed):
                selected = self.select(changed)
                self.assertEqual(tuple(selected[:len(must_pass.HARNESS_PINS)]), must_pass.HARNESS_PINS)
                self.assertTrue(set(must_pass.ALWAYS) <= set(selected))
                self.assertEqual(len(selected), len(set(selected)))
        self.assertFalse(set(must_pass.HARNESS_PINS) & set(self.select("bin/exactory")))


class MustPassReachTests(unittest.TestCase):
    """Each way in which a test module uses a harness module without a top-level import, on a small tree."""

    def select(self, files, *changed):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_tree(root, dict(files))
            return set(must_pass.select_modules(must_pass.describe_tree(root), list(changed)))

    def test_a_module_used_without_a_top_level_import_is_selected(self):
        # 0.91.11 changed research_harness/execution.py, and tests/test_research_gates.py imports it inside a test
        # function and patches a function of it. The other forms name the module in a string.
        uses = {
            "an import inside a function": "def test_a(self):\n    from research_harness import alpha\n",
            "a mock.patch target": "PATCH = mock.patch('research_harness.alpha.compute')\n",
            "importlib.import_module": "MODULE = importlib.import_module('research_harness.alpha')\n",
            "a path with a function name": "PIN = 'research_harness/alpha.py:compute'\n",
        }
        # A docstring or a comment that mentions the module does not use it.
        other = ('"""The other test mentions research_harness/alpha.py."""\n'
                 "# research_harness.alpha.compute\nNAME = 'research_harness.alphabet'\n")
        for kind, text in uses.items():
            with self.subTest(kind=kind):
                selected = self.select({"tests/test_user.py": text, "tests/test_other.py": other},
                                       "research_harness/alpha.py")
                self.assertIn("test_user", selected)
                self.assertNotIn("test_other", selected)

    def test_a_fixture_that_names_a_changed_module_selects_the_tests_that_read_it(self):
        selected = self.select({
            "tests/fixtures/pins.json": json.dumps({"research_harness/alpha.py:compute": "digest"}),
            "tests/test_pins.py": "FIXTURE = 'tests/fixtures/pins.json'\n",
            "tests/test_other.py": "FIXTURE = 'tests/fixtures/other.json'\n",
        }, "research_harness/alpha.py")
        self.assertIn("test_pins", selected)
        self.assertNotIn("test_other", selected)

    def test_the_checksum_pins_that_a_ci_module_checks_select_the_tests_that_import_it(self):
        # 0.91.11 changed two functions of research_harness/review_protocol.py whose checksums
        # tests/fixtures/capacity_transport_inventory.json pins. Only ci/capacity_guard.py names that fixture, and
        # tests/test_capacity_guard.py imports ci.capacity_guard.
        selected = self.select({
            "tests/fixtures/inventory.json": json.dumps(
                {"admission_functions": {"research_harness/alpha.py:compute": "digest"}}),
            "ci/guard.py": "INVENTORY_PATH = ROOT / 'tests/fixtures/inventory.json'\n",
            "tests/test_guard.py": "from ci import guard\n",
            "tests/test_other.py": "from ci import other\n",
        }, "research_harness/alpha.py")
        self.assertIn("test_guard", selected)
        self.assertNotIn("test_other", selected)

    def test_a_changed_file_of_a_fixture_folder_selects_the_tests_that_read_the_folder(self):
        selected = self.select({
            "tests/test_groups.py": "FOLDER = ROOT / 'tests/fixtures/groups'\n",
            "ci/groups.py": "def read(root):\n    return sorted((root / 'tests/fixtures/groups').glob('*.json'))\n",
            "tests/test_reach.py": "from ci.groups import read\n",
            "tests/test_other.py": "FOLDER = ROOT / 'tests/fixtures/others'\n",
        }, "tests/fixtures/groups/entry.json")
        self.assertTrue({"test_groups", "test_reach"} <= selected)
        self.assertNotIn("test_other", selected)

    def test_a_compressed_fixture_that_names_a_changed_module_selects_the_tests_that_read_it(self):
        selected = self.select({
            "tests/fixtures/oracle.json.gz": gzip.compress(b'{"reader": "research_harness/alpha.py"}'),
            "tests/test_oracle.py": "ORACLE = 'tests/fixtures/oracle.json.gz'\n",
        }, "research_harness/alpha.py")
        self.assertIn("test_oracle", selected)


class MustPassRunTests(unittest.TestCase):
    """The run of the set on a small plugin tree, through the command as a lane starts it."""

    def run_command(self, root, *arguments):
        return subprocess.run((sys.executable, "ci/must_pass.py") + arguments, cwd=root, capture_output=True,
                              text=True, timeout=120)

    def test_two_modules_of_one_shard_run_in_one_process_and_state_left_behind_fails_the_later_one(self):
        # Test i of the discovery belongs to shard i mod 8. test_a_leaves holds tests 0 to 7, and test_b_reads holds
        # test 8, so shard 0 runs test_a_leaves.test_0 and then test_b_reads in one process.
        leaves = "".join("    def test_%d(self):\n        shared_state.VALUE = 'changed'\n" % index
                         for index in range(8))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_runner(root)
            write_tree(root, {
                "tests/shared_state.py": "VALUE = 'initial'\n",
                "tests/test_a_leaves.py": "import unittest\n\nimport shared_state\n\n\n"
                                          "class LeaveTests(unittest.TestCase):\n" + leaves,
                "tests/test_b_reads.py": "import unittest\n\nimport shared_state\n\n\n"
                                         "class ReadTests(unittest.TestCase):\n"
                                         "    def test_reads_initial_value(self):\n"
                                         "        self.assertEqual(shared_state.VALUE, 'initial')\n",
            })
            alone = subprocess.run((sys.executable, "ci/unittest_shards.py", "run-modules", "tests.test_b_reads"),
                                   cwd=root, capture_output=True, text=True, timeout=60)
            self.assertEqual(alone.returncode, 0, alone.stderr)
            logs = root / "logs"
            run = self.run_command(root, "run", "--module", "test_a_leaves", "--module", "test_b_reads",
                                   "--jobs", "2", "--logs", str(logs))
            self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
            report = json.loads((logs / must_pass.REPORT_FILE_NAME).read_text(encoding="utf-8"))
            groups = {group["shard_index"]: group for group in report["groups"]}
            self.assertEqual(sorted(groups), list(range(8)))
            shard = json.loads(Path(groups[0]["result"]).read_text(encoding="utf-8"))
            self.assertEqual(shard["executed_test_ids"], ["test_a_leaves.LeaveTests.test_0",
                                                          "test_b_reads.ReadTests.test_reads_initial_value"])
            self.assertEqual(groups[0]["failed_tests"], ["test_b_reads.ReadTests.test_reads_initial_value"])
            self.assertEqual([index for index, group in groups.items() if group["status"] != "ok"], [0])
            self.assertEqual({row["module"]: row["status"] for row in report["module_times"]},
                             {"test_a_leaves": "ok", "test_b_reads": "FAILED"})
            self.assertEqual({row["module"]: row["tests"] for row in report["module_times"]},
                             {"test_a_leaves": 8, "test_b_reads": 1})
            self.assertEqual([row[1] for row in report["test_seconds"]][:2], shard["executed_test_ids"])
            for group in report["groups"]:
                self.assertGreater(group["processor_seconds"], 0)
                self.assertGreaterEqual(group["wall_seconds"], 0)
            self.assertEqual(report["budget"], {"name": "implementer_run", "wall_seconds": 600, "over": False})
            self.assertIn("implementer_run", run.stdout)

    def test_a_replay_of_a_commit_runs_the_set_of_its_diff_against_the_must_pass_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_runner(root)
            write_tree(root, {"research_harness/alpha.py": "VALUE = 1\n",
                              "tests/test_alpha.py": "import unittest\n\n\nclass AlphaTests(unittest.TestCase):\n"
                                                     "    def test_a(self):\n"
                                                     "        from research_harness import alpha\n"})

            def commit(message):
                subprocess.run(("git", "add", "."), cwd=root, check=True)
                subprocess.run(("git", "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", message),
                               cwd=root, check=True, env=GIT_ENVIRONMENT)
                return subprocess.check_output(("git", "rev-parse", "HEAD"), cwd=root, text=True).strip()

            subprocess.run(("git", "init", "-q"), cwd=root, check=True)
            base = commit("Record the base of the fixture")
            (root / "research_harness/alpha.py").write_text("VALUE = 2\n", encoding="utf-8")
            head = commit("Record a change of the fixture")
            (root / "tests/test_untracked.py").write_text(EMPTY_TEST_MODULE, encoding="utf-8")
            listed = self.run_command(root, "list", "--base", base, "--head", head)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            selected = listed.stdout.split()
            self.assertIn("test_alpha", selected)
            self.assertNotIn("test_untracked", selected)
            self.assertIn("test_untracked", self.run_command(root, "list", "--base", base).stdout.split())
            logs = root / "logs"
            run = self.run_command(root, "run", "--base", base, "--head", head, "--jobs", "2", "--logs", str(logs))
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            report = json.loads((logs / must_pass.REPORT_FILE_NAME).read_text(encoding="utf-8"))
            self.assertEqual(report["modules"], selected)
            self.assertEqual(report["budget"]["name"], "must_pass_run")
            self.assertEqual(report["budget"]["wall_seconds"], 2700)
            self.assertIn("must_pass_run", run.stdout)

    def test_jobs_above_the_limit_of_the_budgets_and_an_unknown_module_are_refused(self):
        refused = self.run_command(must_pass.ROOT, "run", "--module", "test_must_pass", "--jobs", "9")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("between 1 and 8", refused.stderr)
        self.assertIn("test_processes", refused.stderr)
        unknown = self.run_command(must_pass.ROOT, "run", "--module", "test_no_such_module", "--jobs", "1")
        self.assertEqual(unknown.returncode, 2)
        self.assertIn("test_no_such_module", unknown.stderr)


if __name__ == "__main__":
    unittest.main()
