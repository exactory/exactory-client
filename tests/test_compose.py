"""Tests for claim composition: exactory compose-claim and exactory-predict compose."""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
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


_exactory = _load_bin_module("exactory", "exactory_cli")
_predict = _load_bin_module("exactory-predict", "exactory_predict_compose")


def _run(module, argv):
    args = module._build_parser().parse_args(argv)
    with contextlib.redirect_stdout(io.StringIO()):
        args.handler(args)


class ComposeClaimTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = os.path.join(self._tmp.name, "review.json")

    def _read_claims(self):
        with open(self.out, encoding="utf-8") as handle:
            return json.load(handle)["claims"]

    def test_cross_reference_claim_carries_the_envelope(self):
        _run(_exactory, [
            "compose-claim", "cross-reference",
            "--paper-locator", "Section 5",
            "--referencing-text", "as shown in Figure 7, the loss diverges",
            "--kind", "figure", "--label", "7",
            "--severity", "minor",
            "--source-url", "https://example.org/evidence",
            "--out", self.out,
        ])

        (claim,) = self._read_claims()
        self.assertEqual(claim["dimension"], "consistency")
        self.assertEqual(claim["procedure"], "cross_reference_resolution")
        self.assertEqual(claim["severity"], "minor")
        self.assertEqual(claim["sources"], [{"url": "https://example.org/evidence"}])
        self.assertEqual(claim["payload"]["referencedKind"], "figure")
        self.assertEqual(claim["payload"]["referencedLabel"], "7")
        self.assertNotIn("background", claim)
        self.assertNotIn("plan", claim)

    def _write_text_file(self, name, text):
        path = os.path.join(self._tmp.name, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_claim_carries_background_and_plan_when_the_flags_are_given(self):
        rationale_path = self._write_text_file("rationale.txt", "Figure 7 does not exist.\n")
        background_path = self._write_text_file(
            "background.txt", "Section 5 reads the loss curve off Figure 7.\n")
        plan_path = self._write_text_file(
            "plan.txt", "Renumber the reference to Figure 6, or add the figure.\n")
        _run(_exactory, [
            "compose-claim", "cross-reference",
            "--paper-locator", "Section 5",
            "--referencing-text", "as shown in Figure 7, the loss diverges",
            "--kind", "figure", "--label", "7",
            "--rationale-file", rationale_path,
            "--background-file", background_path,
            "--plan-file", plan_path,
            "--out", self.out,
        ])

        (claim,) = self._read_claims()
        self.assertEqual(claim["rationale"], "Figure 7 does not exist.")
        self.assertEqual(claim["background"],
                         "Section 5 reads the loss curve off Figure 7.")
        self.assertEqual(claim["plan"],
                         "Renumber the reference to Figure 6, or add the figure.")

    def test_claim_refuses_an_empty_background_file(self):
        background_path = self._write_text_file("background.txt", "  \n")
        with self.assertRaises(SystemExit):
            _run(_exactory, [
                "compose-claim", "cross-reference",
                "--paper-locator", "Section 5",
                "--referencing-text", "as shown in Figure 7, the loss diverges",
                "--kind", "figure", "--label", "7",
                "--background-file", background_path,
                "--out", self.out,
            ])

    def test_claim_truncates_background_and_plan_to_the_contract_limit(self):
        background_path = self._write_text_file("background.txt", "b" * 9000)
        plan_path = self._write_text_file("plan.txt", "p" * 9000)
        _run(_exactory, [
            "compose-claim", "cross-reference",
            "--paper-locator", "Section 5",
            "--referencing-text", "as shown in Figure 7, the loss diverges",
            "--kind", "figure", "--label", "7",
            "--background-file", background_path,
            "--plan-file", plan_path,
            "--out", self.out,
        ])

        (claim,) = self._read_claims()
        self.assertEqual(len(claim["background"]), 8000)
        self.assertEqual(len(claim["plan"]), 8000)

    def test_claim_truncation_counts_utf16_units_as_the_server_does(self):
        # U+1D465 (mathematical italic x) is one Python code point but two UTF-16
        # code units, the unit the server's zod .max(8000) counts.
        background_path = self._write_text_file("background.txt", "\U0001d465" * 9000)
        _run(_exactory, [
            "compose-claim", "cross-reference",
            "--paper-locator", "Section 5",
            "--referencing-text", "as shown in Figure 7, the loss diverges",
            "--kind", "figure", "--label", "7",
            "--background-file", background_path,
            "--out", self.out,
        ])

        (claim,) = self._read_claims()
        self.assertEqual(len(claim["background"].encode("utf-16-le")) // 2, 8000)

    def test_claims_append_to_one_review_file(self):
        for label in ("7", "8"):
            _run(_exactory, [
                "compose-claim", "cross-reference",
                "--paper-locator", "Section 5",
                "--referencing-text", "as shown in Figure %s, the loss diverges" % label,
                "--kind", "figure", "--label", label,
                "--out", self.out,
            ])

        self.assertEqual(len(self._read_claims()), 2)

    def test_value_agreement_refuses_a_value_text_outside_its_quote(self):
        with self.assertRaises(SystemExit):
            _run(_exactory, [
                "compose-claim", "value-agreement",
                "--quantity", "corpus size",
                "--locator-a", "Abstract",
                "--quote-a", "we train on thirty million pairs",
                "--value-text-a", "30", "--value-a", "30",
                "--locator-b", "Section 4",
                "--quote-b", "the corpus contains 0.3 million pairs",
                "--value-text-b", "0.3", "--value-b", "0.3",
                "--out", self.out,
            ])

    def test_citation_not_found_needs_a_checkable_handle(self):
        with self.assertRaises(SystemExit):
            _run(_exactory, [
                "compose-claim", "citation",
                "--reference-string", "Nobody, A. (1999). Nothing.",
                "--finding", "not_found",
                "--author", "A. Nobody",
                "--out", self.out,
            ])

    def test_citation_claim_carries_finding_and_bibliography(self):
        _run(_exactory, [
            "compose-claim", "citation",
            "--reference-string", "Nobody, A. (1999). A paper that does not exist.",
            "--finding", "not_found",
            "--title", "A paper that does not exist",
            "--author", "A. Nobody", "--year", "1999",
            "--out", self.out,
        ])

        (claim,) = self._read_claims()
        self.assertEqual(claim["dimension"], "citation")
        self.assertEqual(claim["procedure"], "registry_lookup")
        self.assertEqual(claim["payload"]["finding"], "not_found")
        self.assertEqual(claim["payload"]["bibliography"]["title"],
                         "A paper that does not exist")
        self.assertIsNone(claim["payload"]["bibliography"]["doi"])


class PredictComposeTest(unittest.TestCase):
    def test_compose_emits_the_claim_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            cohort_path = os.path.join(tmp, "cohort.json")
            rationale_path = os.path.join(tmp, "rationale.txt")
            out = os.path.join(tmp, "review.json")
            with open(cohort_path, "w", encoding="utf-8") as handle:
                json.dump({"corpus": "arxiv", "primaryCategory": "cs.LG",
                           "windowStart": "2026-01-01", "windowEnd": "2026-04-01",
                           "initialAgeMonths": 6, "lifelongAgeMonths": 60}, handle)
            with open(rationale_path, "w", encoding="utf-8") as handle:
                handle.write("Strong author record; rising subfield.")

            _run(_predict, [
                "compose",
                "--cohort-file", cohort_path,
                "--initial-percentile", "0.9", "--initial-sigma", "0.8",
                "--delta", "-0.4", "--delta-sigma", "0.6",
                "--rationale-file", rationale_path,
                "--source-url", "https://api.openalex.org/works?filter=example",
                "--out", out,
            ])

            with open(out, encoding="utf-8") as handle:
                (claim,) = json.load(handle)["claims"]
            self.assertEqual(claim["dimension"], "impact")
            self.assertEqual(claim["procedure"], "cohort_prediction")
            self.assertEqual(claim["rationale"], "Strong author record; rising subfield.")
            self.assertEqual(claim["sources"],
                             [{"url": "https://api.openalex.org/works?filter=example"}])
            self.assertNotIn("rationale", claim["payload"])
            self.assertNotIn("claimType", claim)
            self.assertNotIn("background", claim)
            self.assertNotIn("plan", claim)

    def test_compose_truncates_the_rationale_by_utf16_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            cohort_path = os.path.join(tmp, "cohort.json")
            rationale_path = os.path.join(tmp, "rationale.txt")
            out = os.path.join(tmp, "review.json")
            with open(cohort_path, "w", encoding="utf-8") as handle:
                json.dump({"corpus": "arxiv"}, handle)
            with open(rationale_path, "w", encoding="utf-8") as handle:
                handle.write("\U0001d465" * 9000)

            _run(_predict, [
                "compose",
                "--cohort-file", cohort_path,
                "--initial-percentile", "0.9", "--initial-sigma", "0.8",
                "--delta", "-0.4", "--delta-sigma", "0.6",
                "--rationale-file", rationale_path,
                "--out", out,
            ])

            with open(out, encoding="utf-8") as handle:
                (claim,) = json.load(handle)["claims"]
            self.assertEqual(len(claim["rationale"].encode("utf-16-le")) // 2, 8000)

    def test_compose_appends_to_a_review_file_compose_claim_started(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "review.json")
            with open(out, "w", encoding="utf-8") as handle:
                json.dump({"claims": [{"dimension": "citation"}]}, handle)
            cohort_path = os.path.join(tmp, "cohort.json")
            rationale_path = os.path.join(tmp, "rationale.txt")
            with open(cohort_path, "w", encoding="utf-8") as handle:
                json.dump({"corpus": "arxiv"}, handle)
            with open(rationale_path, "w", encoding="utf-8") as handle:
                handle.write("Evidence.")

            _run(_predict, [
                "compose",
                "--cohort-file", cohort_path,
                "--initial-percentile", "0.9", "--initial-sigma", "0.8",
                "--delta", "-0.4", "--delta-sigma", "0.6",
                "--rationale-file", rationale_path,
                "--out", out,
            ])

            with open(out, encoding="utf-8") as handle:
                claims = json.load(handle)["claims"]
            self.assertEqual(len(claims), 2)
            self.assertEqual(claims[0], {"dimension": "citation"})


if __name__ == "__main__":
    unittest.main()
