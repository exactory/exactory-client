"""An authored implication objective uses the released native file format."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/math-search/implication-objective"


class ImplicationObjectiveTests(unittest.TestCase):
    def test_implication_passes_problem_plan_and_rank_with_the_shipped_strategies(self):
        self.assertTrue(FIXTURE.is_dir(), "The implication objective's native files must be retained as a fixture")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "attack"
            workspace = root / "statement-implies-cycle"
            shutil.copytree(FIXTURE, workspace)
            admitted = subprocess.run(
                [sys.executable, "-c", "from pathlib import Path; import json, sys; "
                 "from tests.support import admit_existing_workspace; "
                 "root = Path(sys.argv[1]); slug = sys.argv[2]; "
                 "admit_existing_workspace(root, slug, json.loads((root / slug / 'problem.json').read_text()))",
                 str(root), workspace.name], cwd=ROOT / "skills/math-solver/harness", capture_output=True, text=True,
            )
            self.assertEqual(admitted.returncode, 0, admitted.stdout + admitted.stderr)
            shutil.copytree(FIXTURE, workspace, dirs_exist_ok=True)
            for command in ("check-problem", "plan", "rank"):
                with self.subTest(command=command):
                    result = subprocess.run(
                        [sys.executable, str(ROOT / "bin/exactory-math"), "--attack-root", str(root),
                         command, workspace.name], capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(result.stderr, "")
            openings = json.loads((workspace / "openings.json").read_text())["openings"]
            self.assertEqual([row["strategy"] for row in openings], ["work-conditionally-then-discharge"])
            self.assertEqual(result.stdout, "1. work-conditionally-then-discharge\n")
