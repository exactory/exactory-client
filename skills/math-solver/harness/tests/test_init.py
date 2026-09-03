import json
import tempfile
import unittest
from pathlib import Path

import attack
from tests.support import run


class InitTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.attack_root = Path(self._tmp.name)
        self.workspace = self.attack_root / "sample"

    def tearDown(self):
        self._tmp.cleanup()

    def test_creates_the_workspace_files_and_directories(self):
        status, out, err = run(["init", "sample"], self.attack_root)
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(out, "created %s\n" % self.workspace)
        self.assertEqual((self.workspace / "novelty.md").read_text(), "")
        self.assertEqual((self.workspace / "journal.jsonl").read_text(), "")
        self.assertTrue((self.workspace / "deterministic").is_dir())
        self.assertTrue((self.workspace / "units").is_dir())

    def test_creates_the_study_directory(self):
        run(["init", "sample"], self.attack_root)
        self.assertTrue((self.workspace / "study").is_dir())

    def test_prefills_the_problem_skeleton(self):
        run(["init", "sample"], self.attack_root)
        problem = json.loads((self.workspace / "problem.json").read_text())
        self.assertEqual(problem["claim"], "")
        self.assertEqual(
            problem["quadruple"],
            {"statement": "", "stage": "", "direction": "undecided", "mode": "undecided"},
        )
        self.assertEqual(list(problem["shape"]), list(attack.SHAPE_KEYS))
        self.assertEqual(set(problem["shape"].values()), {"unknown"})
        self.assertEqual(problem["known"], [])

    def test_refuses_to_overwrite_an_existing_workspace(self):
        run(["init", "sample"], self.attack_root)
        (self.workspace / "novelty.md").write_text("searched\n")
        status, out, err = run(["init", "sample"], self.attack_root)
        self.assertEqual(status, 1)
        self.assertEqual(err, "workspace already exists: %s\n" % self.workspace)
        self.assertEqual((self.workspace / "novelty.md").read_text(), "searched\n")
