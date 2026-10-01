"""Tests for bin/exactory-derive: the safe expression checker for equation manipulations."""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import math
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

    def test_a_range_bound_beyond_the_float_range_is_a_warning_not_a_traceback(self) -> None:
        # A JSON integer beyond the float range (about 1.8e308) has no float value. Sampling the range raised
        # OverflowError before the step caught its evaluation errors, so the check ended in a traceback.
        report = _run_check([{
            "label": "huge bound", "from": "x", "to": "x", "vars": {"x": [0, 10 ** 400]},
        }], self)
        self.assertEqual(report["steps"][0]["status"], "unparseable")
        self.assertEqual(report["invalid"], 0)

    def test_a_wrong_step_whose_samples_have_no_finite_value_is_not_reported_consistent(self) -> None:
        # A JSON bound beyond the float range (1e400) reads as infinity, and a product beyond the float range is
        # infinite. The two sides then differ by NaN, which no tolerance comparison flags, so a wrong step passed.
        report = _run_check([
            {"label": "infinite bound", "from": "x", "to": "x + 1", "vars": {"x": [0, float("inf")]}},
            {"label": "overflowing product", "from": "a*b*a*b", "to": "2*a*a*b*b",
             "vars": {"a": [1e100, 1e200], "b": [1e100, 1e200]}},
        ], self)
        for step in report["steps"]:
            with self.subTest(step=step["label"]):
                self.assertEqual((step["status"], step["witness"]), ("unparseable", None))
                self.assertTrue(step["detail"].startswith("evaluation failed: "))
        self.assertEqual(report["invalid"], 0)

    def test_a_wrong_step_with_one_side_without_a_finite_value_is_not_reported_consistent(self) -> None:
        # With one infinite side the difference is infinite, but so is the tolerance, which scales with the larger
        # side, so the comparison never flags the step either.
        report = _run_check([
            {"label": "infinite " + side + " side", "from": "x" if side == "left" else "1", "to": "1" if side == "left" else "x",
             "vars": {"x": [0, float("inf")]}} for side in ("left", "right")], self)
        self.assertEqual([step["status"] for step in report["steps"]], ["unparseable", "unparseable"])
        self.assertEqual(report["invalid"], 0)

    def test_a_wrong_step_is_invalid_at_its_first_finite_differing_point(self) -> None:
        # y*y*y*y is infinite for y above about 1.16e77, and infinity times 0 is NaN, so some sampled points give the
        # first side no finite value. The check skips those points and compares the others, as 0.49.0 did.
        # The first point that this label samples is one of them.
        step = {"label": "zero term B", "from": "x + y*y*y*y*0", "to": "x + 1",
                "vars": {"x": [0.0, 1.0], "y": [0.0, 2e77]}}
        points = _derive._sample_points(step["vars"], _derive._SAMPLE_COUNT_DEFAULT, _derive._seed_for_step(step["label"]))
        finite = [point for point in points if math.isfinite(_derive._evaluate_expression(step["from"], point))]
        self.assertLess(len(finite), len(points))
        self.assertNotEqual(finite[0], points[0])
        report = _run_check([step], self)
        witness = report["steps"][0]["witness"]
        self.assertEqual(report["steps"][0]["status"], "invalid")
        self.assertEqual(witness["point"], finite[0])
        self.assertAlmostEqual(witness["value_to"] - witness["value_from"], 1.0)
        self.assertEqual((report["invalid"], self.exit_code), (1, 1))

    def test_a_step_with_complex_function_values_is_checked(self) -> None:
        # csqrt and cexp return complex numbers, which the check compares; float() of one raised TypeError.
        report = _run_check([{"label": name, "from": name + "(x)", "to": name + "(x)", "vars": {"x": [1.0, 4.0]}}
                             for name in ("csqrt", "cexp")], self)
        for step in report["steps"]:
            with self.subTest(step=step["label"]):
                self.assertIn(step["status"], ("consistent", "verified"))
        self.assertEqual(self.exit_code, 0)

    def test_a_wrong_step_with_complex_sides_has_a_json_witness(self) -> None:
        # JSON has no complex number, so a complex side of the witness gives its real and imaginary parts.
        report = _run_check([{"label": "complex wrong", "from": "x**0.5", "to": "x**0.5 + 1", "vars": {"x": [-4.0, -1.0]}}],
                            self)
        step = report["steps"][0]
        self.assertEqual(step["status"], "invalid")
        value_from, value_to = step["witness"]["value_from"], step["witness"]["value_to"]
        self.assertEqual(set(value_from), {"real", "imag"})
        self.assertAlmostEqual(value_to["real"] - value_from["real"], 1.0)
        self.assertAlmostEqual(value_from["imag"], math.sqrt(-step["witness"]["point"]["x"]))
        self.assertEqual(self.exit_code, 1)

    def test_a_real_function_of_a_complex_value_is_unparseable(self) -> None:
        # math.sin takes real numbers only and raises TypeError for a complex one, a value it cannot evaluate.
        report = _run_check([{"label": "real of complex", "from": "sin(csqrt(x))", "to": "sin(csqrt(x))",
                              "vars": {"x": [-4.0, -1.0]}}], self)
        step = report["steps"][0]
        self.assertEqual((step["status"], step["witness"]), ("unparseable", None))
        self.assertTrue(step["detail"].startswith("evaluation failed: "))
        self.assertEqual(self.exit_code, 0)

    def test_a_step_with_equal_complex_values_stays_consistent(self) -> None:
        # A negative base to a fractional power gives a complex value with finite parts, which the check compares.
        report = _run_check([{"label": "complex power", "from": "x**0.5", "to": "x**0.5", "vars": {"x": [-4.0, -1.0]}}],
                            self)
        self.assertIn(report["steps"][0]["status"], ("consistent", "verified"))

    def test_the_check_is_deterministic_across_runs(self) -> None:
        steps = [{"label": "wrong", "from": "(x + 1)**2", "to": "x**2 + 1",
                  "vars": {"x": [1.0, 5.0]}}]
        first = _run_check(steps, self)["steps"][0]["witness"]["point"]
        second = _run_check(steps, self)["steps"][0]["witness"]["point"]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
