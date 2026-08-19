"""Tests for bin/exactory-derive: the safe expression checker for equation manipulations."""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import unittest
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def _load_bin_module(command_name: str, module_name: str):
    loader = importlib.machinery.SourceFileLoader(
        module_name, str(_PLUGIN_ROOT / "bin" / command_name)
    )
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


_derive = _load_bin_module("exactory-derive", "exactory_derive")


class TestSafeEvaluator(unittest.TestCase):
    def evaluate(self, expression: str, values: dict) -> float:
        return _derive._evaluate_expression(expression, values)

    def test_arithmetic_and_power(self) -> None:
        self.assertAlmostEqual(self.evaluate("x**2 + 2*x + 1", {"x": 3.0}), 16.0)

    def test_whitelisted_math_function(self) -> None:
        self.assertAlmostEqual(self.evaluate("sqrt(x)", {"x": 9.0}), 3.0)

    def test_unary_minus(self) -> None:
        self.assertAlmostEqual(self.evaluate("-x + 5", {"x": 2.0}), 3.0)

    def test_attribute_access_is_rejected(self) -> None:
        with self.assertRaises(_derive.UnsafeExpressionError):
            self.evaluate("x.__class__", {"x": 1.0})

    def test_call_to_unlisted_name_is_rejected(self) -> None:
        with self.assertRaises(_derive.UnsafeExpressionError):
            self.evaluate("eval('1')", {})

    def test_unlisted_name_is_rejected(self) -> None:
        with self.assertRaises(_derive.UnsafeExpressionError):
            self.evaluate("y + 1", {"x": 1.0})

    def test_subscript_is_rejected(self) -> None:
        with self.assertRaises(_derive.UnsafeExpressionError):
            self.evaluate("x[0]", {"x": 1.0})

    def test_comprehension_is_rejected(self) -> None:
        with self.assertRaises(_derive.UnsafeExpressionError):
            self.evaluate("[i for i in range(3)]", {})

    def test_syntax_error_is_unsafe(self) -> None:
        with self.assertRaises(_derive.UnsafeExpressionError):
            self.evaluate("x +", {"x": 1.0})


def _run_check(steps: list[dict], test_case: unittest.TestCase,
               extra_argv: list[str] | None = None) -> dict:
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as steps_file:
        json.dump(steps, steps_file)
        steps_path = steps_file.name
    argv = ["check", "--steps-file", steps_path, *(extra_argv or [])]
    args = _derive._build_parser().parse_args(argv)
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        with test_case.assertRaises(SystemExit) as caught:
            args.handler(args)
    test_case.exit_code = caught.exception.code
    return json.loads(sink.getvalue())


class TestCheck(unittest.TestCase):
    def test_true_identity_is_consistent_or_verified(self) -> None:
        report = _run_check([{
            "label": "pythagorean", "from": "sin(x)**2 + cos(x)**2", "to": "1",
            "vars": {"x": [-3.0, 3.0]},
        }], self)
        step = report["steps"][0]
        self.assertIn(step["status"], ("consistent", "verified"))
        self.assertIsNone(step["witness"])
        self.assertEqual(report["invalid"], 0)
        self.assertEqual(self.exit_code, 0)

    def test_false_manipulation_is_invalid_with_a_witness(self) -> None:
        report = _run_check([{
            "label": "wrong expand", "from": "(x + 1)**2", "to": "x**2 + 1",
            "vars": {"x": [1.0, 5.0]},
        }], self)
        step = report["steps"][0]
        self.assertEqual(step["status"], "invalid")
        self.assertIsNotNone(step["witness"])
        self.assertIn("x", step["witness"]["point"])
        self.assertNotAlmostEqual(step["witness"]["value_from"],
                                  step["witness"]["value_to"])
        self.assertEqual(report["invalid"], 1)
        self.assertEqual(self.exit_code, 1)

    def test_constant_identity_with_no_vars_is_checked_directly(self) -> None:
        report = _run_check([{"label": "const", "from": "2 + 2", "to": "4", "vars": {}}],
                            self)
        self.assertIn(report["steps"][0]["status"], ("consistent", "verified"))

    def test_false_constant_identity_is_invalid(self) -> None:
        report = _run_check([{"label": "bad const", "from": "2 + 2", "to": "5", "vars": {}}],
                            self)
        self.assertEqual(report["steps"][0]["status"], "invalid")

    def test_unparseable_expression_is_a_warning_not_a_disproof(self) -> None:
        report = _run_check([{
            "label": "bad", "from": "x.attr", "to": "1", "vars": {"x": [1.0, 2.0]},
        }], self)
        self.assertEqual(report["steps"][0]["status"], "unparseable")
        self.assertEqual(report["invalid"], 0)

    def test_the_check_is_deterministic_across_runs(self) -> None:
        steps = [{"label": "wrong", "from": "(x + 1)**2", "to": "x**2 + 1",
                  "vars": {"x": [1.0, 5.0]}}]
        first = _run_check(steps, self)["steps"][0]["witness"]["point"]
        second = _run_check(steps, self)["steps"][0]["witness"]["point"]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
