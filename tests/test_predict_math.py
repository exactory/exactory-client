"""Regression tests for the arithmetic in bin/exactory-predict."""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
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


_predict = _load_bin_module("exactory-predict", "exactory_predict")


def _call_and_expect_exit(test_case: unittest.TestCase, function, *arguments) -> None:
    with contextlib.redirect_stderr(io.StringIO()):
        with test_case.assertRaises(SystemExit) as caught:
            function(*arguments)
    test_case.assertEqual(caught.exception.code, 1)


class TestComputeCohortWindow(unittest.TestCase):
    def test_a_mid_month_publication(self) -> None:
        self.assertEqual(
            _predict._compute_cohort_window("2026-07-15"), ("2026-01-01", "2026-06-30")
        )

    def test_the_first_of_the_month_gives_the_same_window(self) -> None:
        self.assertEqual(
            _predict._compute_cohort_window("2026-07-01"), ("2026-01-01", "2026-06-30")
        )

    def test_the_last_day_of_the_month_gives_the_same_window(self) -> None:
        self.assertEqual(
            _predict._compute_cohort_window("2026-07-31"), ("2026-01-01", "2026-06-30")
        )

    def test_a_january_publication_crosses_the_year_boundary(self) -> None:
        self.assertEqual(
            _predict._compute_cohort_window("2026-01-10"), ("2025-07-01", "2025-12-31")
        )

    def test_a_window_that_ends_in_a_leap_february(self) -> None:
        self.assertEqual(
            _predict._compute_cohort_window("2024-03-05"), ("2023-09-01", "2024-02-29")
        )

    def test_a_window_that_ends_in_a_common_february(self) -> None:
        self.assertEqual(
            _predict._compute_cohort_window("2026-03-05"), ("2025-09-01", "2026-02-28")
        )

    def test_a_full_timestamp_reads_as_its_date_part(self) -> None:
        self.assertEqual(
            _predict._compute_cohort_window("2026-08-07T00:00:00Z"),
            ("2026-02-01", "2026-07-31"),
        )

    def test_the_window_never_reaches_the_publication_month(self) -> None:
        _, window_end = _predict._compute_cohort_window("2026-07-15")
        self.assertLess(window_end, "2026-07-01")


class TestConvertPercentileToLogit(unittest.TestCase):
    def test_median_maps_to_zero(self) -> None:
        self.assertEqual(_predict._convert_percentile_to_logit(0.5), 0.0)

    def test_top_decile_value(self) -> None:
        self.assertAlmostEqual(
            _predict._convert_percentile_to_logit(0.9), round(math.log(9.0), 4), places=4
        )

    def test_rounds_to_four_decimals(self) -> None:
        self.assertEqual(
            _predict._convert_percentile_to_logit(0.75), round(math.log(3.0), 4)
        )

    def test_percentile_just_inside_the_logit_bound_is_accepted(self) -> None:
        self.assertAlmostEqual(
            _predict._convert_percentile_to_logit(0.9999), 9.2102, places=4
        )

    def test_boundary_percentiles_are_rejected(self) -> None:
        _call_and_expect_exit(self, _predict._convert_percentile_to_logit, 0.0)
        _call_and_expect_exit(self, _predict._convert_percentile_to_logit, 1.0)

    def test_percentile_outside_the_logit_bound_is_rejected(self) -> None:
        _call_and_expect_exit(self, _predict._convert_percentile_to_logit, 0.999999999999)


class TestTransportHygiene(unittest.TestCase):
    def test_the_arxiv_api_url_uses_https(self) -> None:
        self.assertTrue(_predict._ARXIV_API_URL.startswith("https://"))

    def test_an_abbreviated_flag_is_rejected(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                _predict._build_parser().parse_args(["cohort", "--pub", "2026-01-01"])
        self.assertEqual(caught.exception.code, 2)


class TestRequireSigmaInRange(unittest.TestCase):
    def test_the_floor_and_the_ceiling_pass(self) -> None:
        self.assertIsNone(_predict._require_sigma_in_range("--initial-sigma", 0.3))
        self.assertIsNone(_predict._require_sigma_in_range("--initial-sigma", 10.0))

    def test_a_sigma_below_the_floor_is_rejected(self) -> None:
        _call_and_expect_exit(self, _predict._require_sigma_in_range, "--initial-sigma", 0.29)

    def test_a_sigma_above_the_ceiling_is_rejected(self) -> None:
        _call_and_expect_exit(self, _predict._require_sigma_in_range, "--delta-sigma", 10.5)


if __name__ == "__main__":
    unittest.main()
