"""Regression tests for the date arithmetic in bin/exactory-cohort."""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
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


_cohort = _load_bin_module("exactory-cohort", "exactory_cohort")


class TestComputeCohortWindow(unittest.TestCase):
    def test_a_mid_month_publication(self) -> None:
        self.assertEqual(
            _cohort._compute_cohort_window("2026-07-15"), ("2026-01-01", "2026-06-30")
        )

    def test_the_first_of_the_month_gives_the_same_window(self) -> None:
        self.assertEqual(
            _cohort._compute_cohort_window("2026-07-01"), ("2026-01-01", "2026-06-30")
        )

    def test_the_last_day_of_the_month_gives_the_same_window(self) -> None:
        self.assertEqual(
            _cohort._compute_cohort_window("2026-07-31"), ("2026-01-01", "2026-06-30")
        )

    def test_a_january_publication_crosses_the_year_boundary(self) -> None:
        self.assertEqual(
            _cohort._compute_cohort_window("2026-01-10"), ("2025-07-01", "2025-12-31")
        )

    def test_a_window_that_ends_in_a_leap_february(self) -> None:
        self.assertEqual(
            _cohort._compute_cohort_window("2024-03-05"), ("2023-09-01", "2024-02-29")
        )

    def test_a_window_that_ends_in_a_common_february(self) -> None:
        self.assertEqual(
            _cohort._compute_cohort_window("2023-03-05"), ("2022-09-01", "2023-02-28")
        )

    def test_a_full_timestamp_reads_as_its_date_part(self) -> None:
        self.assertEqual(
            _cohort._compute_cohort_window("2026-07-15T09:00:00Z"),
            ("2026-01-01", "2026-06-30"),
        )

    def test_the_window_never_reaches_the_publication_month(self) -> None:
        # The cohort must already exist when the paper is read against it.
        _, window_end = _cohort._compute_cohort_window("2026-07-15")
        self.assertLess(window_end, "2026-07-01")


class TestFreezeOutput(unittest.TestCase):
    def test_the_frozen_definition_carries_only_the_population(self) -> None:
        # The measurement ages left with the citation prediction (design 2026-08-21).
        parser = _cohort._build_parser()
        args = parser.parse_args(
            ["freeze", "--corpus", "arxiv", "--category", "cs.LG", "--published", "2026-07-15"]
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            args.handler(args)

        import json

        self.assertEqual(
            json.loads(output.getvalue()),
            {
                "corpus": "arxiv",
                "primaryCategory": "cs.LG",
                "windowStart": "2026-01-01",
                "windowEnd": "2026-06-30",
            },
        )

    def test_an_abbreviated_flag_is_rejected(self) -> None:
        parser = _cohort._build_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["freeze", "--corp", "arxiv"])


if __name__ == "__main__":
    unittest.main()
