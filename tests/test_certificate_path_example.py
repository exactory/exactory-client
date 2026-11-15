"""Expose the problem certificate journey through the plugin regression suite."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CertificatePathExampleTests(unittest.TestCase):
    def test_common_probe_examples_retain_the_residue_certificate(self):
        from research_harness import cli
        examples = json.loads((ROOT / "docs/research-cli-examples.json").read_text())
        intent, probe = (examples[name] for name in ("probe-intent", "probe"))
        self.assertEqual(intent["id"], "probe-residues")
        self.assertEqual(probe["id"], intent["id"])
        self.assertEqual(len(probe["outputs"]), 1)
        self.assertEqual(probe["outputs"][0]["sha256"], hashlib.sha256(b"0 0\n1 1\n2 0\n3 1\n").hexdigest())
        self.assertEqual(probe["outputs"][0]["size"], 16)
        self.assertNotEqual(intent["program"]["sha256"], "0" * 64)
        parser = cli.build_parser()
        for operation in ("probe-intent", "probe"):
            self.assertEqual(cli.run(parser.parse_args(["example", operation])), examples[operation])

    def test_native_certificate_journey_and_six_refusals(self):
        environment = dict(os.environ, PYTHONPATH=str(ROOT))
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "tests.test_certificate_path_example"],
            cwd=ROOT / "skills/math-solver/harness", env=environment,
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
