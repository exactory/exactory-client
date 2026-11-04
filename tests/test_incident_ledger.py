"""Keep incident coverage explicit and require real reproductions for new fixes."""

from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "tests" / "incidents" / "ledger.json"
NONE_COUNT = 95
SAME_PATH_COUNT = 128

# Resolve in a child process: the native harness has its own package called
# tests, which must not become the plugin suite's tests package during discovery.
RESOLVE_TEST_IDS = """
import importlib
import inspect
import json
from pathlib import Path
import sys
import unittest

payload = json.load(sys.stdin)
root = Path(payload["root"])
sys.path.insert(0, str(root / "tests"))
sys.path.insert(1, str(root))
sys.path.insert(2, str(root / "skills" / "math-solver" / "harness"))
failures = []
for test_id in payload["tests"]:
    try:
        module_name, class_name, method_name = test_id.rsplit(".", 2)
        module = importlib.import_module(module_name)
        test_class = getattr(module, class_name)
        assert inspect.isclass(test_class), "not a class"
        assert issubclass(test_class, unittest.TestCase), "not a test case"
        method = getattr(test_class, method_name)
        assert method_name.startswith("test_"), "not a test method name"
        assert callable(method), "not a callable test method"
    except (ImportError, AttributeError, ValueError, AssertionError) as error:
        failures.append(test_id + ": " + str(error))
if failures:
    sys.exit("\\n".join(failures))
"""


class IncidentLedgerTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(LEDGER_PATH.is_file(), "The incident ledger is missing")
        self.ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        self.incidents = self.ledger["incidents"]

    def test_ledger_parses_with_the_versioned_schema(self):
        self.assertEqual(self.ledger["schema"], "exactory-incident-ledger-v1")
        self.assertIsInstance(self.incidents, list)
        # 260 catalog rows and 12 failure classes plus 7 output-refusal codes.
        self.assertGreaterEqual(len(self.incidents), 279)

    def test_incident_ids_are_unique_stable_kebab_case(self):
        ids = [entry["id"] for entry in self.incidents]
        self.assertEqual(len(ids), len(set(ids)))
        for incident_id in ids:
            with self.subTest(incident=incident_id):
                self.assertRegex(incident_id, r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

    def test_every_incident_has_its_record_summary_and_expected_transition(self):
        for entry in self.incidents:
            with self.subTest(incident=entry["id"]):
                for field in ("record", "class", "summary", "expected"):
                    self.assertIsInstance(entry[field], str)
                    self.assertTrue(entry[field].strip(), field)
                self.assertEqual(Path(entry["record"]).name, entry["record"])

    def test_releases_and_test_kinds_are_explicit(self):
        for entry in self.incidents:
            with self.subTest(incident=entry["id"]):
                self.assertRegex(entry["fixed_in"], r"^(?:open|\d+\.\d+\.\d+)$")
                self.assertIn(entry["test_kind"], ("real_shape", "same_path", "none"))
                self.assertIsInstance(entry["tests"], list)
                self.assertEqual(len(entry["tests"]), len(set(entry["tests"])))
                if entry["test_kind"] == "none":
                    self.assertEqual(entry["tests"], [])
                else:
                    self.assertTrue(entry["tests"])

    def test_every_listed_test_resolves_without_running_the_method(self):
        test_ids = sorted({test_id for entry in self.incidents
                           for test_id in entry["tests"]})
        for test_id in test_ids:
            self.assertRegex(
                test_id,
                r"^[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*\.test_\w+$",
            )
        result = subprocess.run(
            [sys.executable, "-c", RESOLVE_TEST_IDS],
            input=json.dumps({"root": str(ROOT), "tests": test_ids}),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=ROOT,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_fixes_from_0730_onward_have_a_real_shape_regression(self):
        for entry in self.incidents:
            if entry["fixed_in"] == "open":
                continue
            release = tuple(int(part) for part in entry["fixed_in"].split("."))
            if release >= (0, 73, 0):
                with self.subTest(incident=entry["id"]):
                    self.assertEqual(entry["test_kind"], "real_shape")
                    self.assertTrue(entry["tests"])

    def test_uncovered_incident_counts_never_grow(self):
        counts = Counter(entry["test_kind"] for entry in self.incidents)
        self.assertLessEqual(counts["none"], NONE_COUNT)
        self.assertLessEqual(counts["same_path"], SAME_PATH_COUNT)


if __name__ == "__main__":
    unittest.main()
