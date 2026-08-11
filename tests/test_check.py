"""Tests for bin/exactory-check: parsing, classification, report, cache, and add."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _load_bin_module(command_name: str, module_name: str):
    loader = importlib.machinery.SourceFileLoader(
        module_name, str(_PLUGIN_ROOT / "bin" / command_name)
    )
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


_check = _load_bin_module("exactory-check", "exactory_check")

_CROSSREF_WORK_TEXT = (_FIXTURES_DIR / "crossref_work.json").read_text()
_CROSSREF_SEARCH_EMPTY_TEXT = (_FIXTURES_DIR / "crossref_search_empty.json").read_text()
_ARXIV_ENTRY_TEXT = (_FIXTURES_DIR / "arxiv_entry.xml").read_text()
_OPENALEX_SEARCH_EMPTY_TEXT = (_FIXTURES_DIR / "openalex_search_empty.json").read_text()
_DATACITE_DOI_TEXT = (_FIXTURES_DIR / "datacite_doi.json").read_text()
_PUBMED_ESUMMARY_TEXT = (_FIXTURES_DIR / "pubmed_esummary.json").read_text()

_FIXTURE_BIB_TEXT = (_FIXTURES_DIR / "references.bib").read_text()


def _route_fixture_registry(url: str, accept: str = "application/json"):
    """Answer registry URLs with fixture payloads, like the live APIs would."""
    if url.startswith("https://api.crossref.org/works?"):
        return 200, _CROSSREF_SEARCH_EMPTY_TEXT
    if url.startswith("https://api.crossref.org/works/"):
        return 200, _CROSSREF_WORK_TEXT
    if url.startswith("https://export.arxiv.org/api/query"):
        return 200, _ARXIV_ENTRY_TEXT
    if url.startswith("https://api.openalex.org/works"):
        return 200, _OPENALEX_SEARCH_EMPTY_TEXT
    if url.startswith("https://api.datacite.org/dois/"):
        return 404, None
    if url.startswith("https://eutils.ncbi.nlm.nih.gov/"):
        return 200, _PUBMED_ESUMMARY_TEXT
    raise AssertionError(f"unexpected URL in test: {url}")


def _refuse_network(url: str, accept: str = "application/json"):
    return None, None


def _refuse_doi_registries(url: str, accept: str = "application/json"):
    if url.startswith("https://api.crossref.org/works/"):
        return 404, None
    if url.startswith("https://api.datacite.org/dois/"):
        return 404, None
    raise AssertionError(f"unexpected URL in test: {url}")


class _CheckTestCase(unittest.TestCase):
    """Shared plumbing: a scratch directory and the _open_url patch point."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch_dir = Path(scratch.name)
        self.out_path = self.scratch_dir / ".exactory" / "citation-check.json"
        self.addCleanup(setattr, _check, "_open_url", _check._open_url)

    def _write_bib(self, text: str) -> Path:
        bib_path = self.scratch_dir / "references.bib"
        bib_path.write_text(text)
        return bib_path

    def _run_command(self, argv: list[str], expected_exit_code: int | None) -> str:
        args = _check._build_parser().parse_args(argv)
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            if expected_exit_code is None:
                args.handler(args)
            else:
                with self.assertRaises(SystemExit) as caught:
                    args.handler(args)
                self.assertEqual(caught.exception.code, expected_exit_code)
        return sink.getvalue()

    def _lookup(self, argv_tail: list[str], expected_exit_code: int | None) -> dict:
        self._run_command(["lookup", "--out", str(self.out_path), *argv_tail], expected_exit_code)
        return json.loads(self.out_path.read_text())


class TestBibParsing(unittest.TestCase):
    def test_parses_every_fixture_entry_in_order(self) -> None:
        entries = _check._parse_bib(_FIXTURE_BIB_TEXT)
        self.assertEqual(
            [entry["key"] for entry in entries],
            ["example2024deterministic", "instance2023predicting", "nobody2022invented"],
        )

    def test_extracts_the_arxiv_id_and_the_doi(self) -> None:
        entries = _check._parse_bib(_FIXTURE_BIB_TEXT)
        self.assertEqual(_check._extract_arxiv_id(entries[0]), "2401.01234")
        self.assertEqual(_check._extract_doi(entries[0]), "")
        self.assertEqual(_check._extract_doi(entries[1]), "10.1234/exact.5678")
        self.assertEqual(_check._extract_arxiv_id(entries[2]), "")
        self.assertEqual(_check._extract_doi(entries[2]), "")


class TestVerifyStatuses(_CheckTestCase):
    def test_fixture_bib_verifies_real_entries_and_blocks_the_invented_one(self) -> None:
        _check._open_url = _route_fixture_registry
        bib_path = self._write_bib(_FIXTURE_BIB_TEXT)
        report = self._lookup(["--bib", str(bib_path)], 1)
        statuses = {entry["key"]: entry["status"] for entry in report["entries"]}
        self.assertEqual(statuses["example2024deterministic"], "verified")
        self.assertEqual(statuses["instance2023predicting"], "verified")
        self.assertEqual(statuses["nobody2022invented"], "unresolved")
        self.assertEqual(report["counts"], {"verified": 2, "blocking": 1, "warning": 0})
        self.assertEqual(report["blocking"], 1)
        self.assertFalse(report["nothing_verified"])
        self.assertFalse(report["ok"])

    def test_title_mismatch_blocks(self) -> None:
        _check._open_url = _route_fixture_registry
        bib_path = self._write_bib(
            "@article{wrongtitle2023,\n"
            "  title={Predicting Citation Impact with Fabricated Subtitles: A Longitudinal Study},\n"
            "  author={Carol Instance and Dana Case},\n"
            "  year={2023},\n"
            "  doi={10.1234/exact.5678}\n"
            "}\n"
        )
        report = self._lookup(["--bib", str(bib_path)], 1)
        self.assertEqual(report["entries"][0]["status"], "title_mismatch")
        self.assertEqual(report["counts"], {"verified": 0, "blocking": 1, "warning": 0})

    def test_author_mismatch_blocks(self) -> None:
        _check._open_url = _route_fixture_registry
        bib_path = self._write_bib(
            "@article{wrongauthor2023,\n"
            "  title={Predicting Citation Impact with Cohort Percentiles},\n"
            "  author={Mallory Wrong and Dana Case},\n"
            "  year={2023},\n"
            "  doi={10.1234/exact.5678}\n"
            "}\n"
        )
        report = self._lookup(["--bib", str(bib_path)], 1)
        self.assertEqual(report["entries"][0]["status"], "author_mismatch")
        self.assertEqual(report["counts"], {"verified": 0, "blocking": 1, "warning": 0})

    def test_year_mismatch_warns_without_blocking(self) -> None:
        _check._open_url = _route_fixture_registry
        bib_path = self._write_bib(
            "@article{instance2023predicting,\n"
            "  title={Predicting Citation Impact with Cohort Percentiles},\n"
            "  author={Carol Instance and Dana Case},\n"
            "  year={2023},\n"
            "  doi={10.1234/exact.5678}\n"
            "}\n"
            "\n"
            "@article{yearskew2022,\n"
            "  title={Predicting Citation Impact with Cohort Percentiles},\n"
            "  author={Carol Instance and Dana Case},\n"
            "  year={2022},\n"
            "  doi={10.1234/exact.5678}\n"
            "}\n"
        )
        report = self._lookup(["--bib", str(bib_path)], None)
        statuses = [entry["status"] for entry in report["entries"]]
        self.assertEqual(statuses, ["verified", "year_mismatch"])
        self.assertEqual(report["counts"], {"verified": 1, "blocking": 0, "warning": 1})
        self.assertTrue(report["ok"])

    def test_network_failure_is_never_treated_as_fabrication(self) -> None:
        _check._open_url = _refuse_network
        bib_path = self._write_bib(_FIXTURE_BIB_TEXT)
        report = self._lookup(["--bib", str(bib_path)], 1)
        statuses = {entry["status"] for entry in report["entries"]}
        self.assertEqual(statuses, {"network_error"})
        self.assertEqual(report["blocking"], 0)
        self.assertTrue(report["nothing_verified"])
        self.assertFalse(report["ok"])

    def test_doi_missing_from_both_registries_is_not_found(self) -> None:
        _check._open_url = _refuse_doi_registries
        bib_path = self._write_bib(
            "@misc{ghost2020,\n"
            "  author={Eve Nobody},\n"
            "  year={2020},\n"
            "  doi={10.9999/ghost}\n"
            "}\n"
        )
        report = self._lookup(["--bib", str(bib_path)], 1)
        self.assertEqual(report["entries"][0]["status"], "not_found")

    def test_pmid_entry_verifies_via_pubmed(self) -> None:
        _check._open_url = _route_fixture_registry
        bib_path = self._write_bib(
            "@article{instancepm2023,\n"
            "  title={Predicting Citation Impact with Cohort Percentiles},\n"
            "  author={Carol Instance and Dana Case},\n"
            "  year={2023},\n"
            "  pmid={12345678}\n"
            "}\n"
        )
        report = self._lookup(["--bib", str(bib_path)], None)
        self.assertEqual(report["entries"][0]["status"], "verified")

    def test_missing_bib_file_is_a_usage_error(self) -> None:
        self._run_command(
            ["lookup", "--out", str(self.out_path), "--bib", str(self.scratch_dir / "absent.bib")],
            2,
        )


class TestReportSchema(_CheckTestCase):
    def test_report_carries_exactly_the_contract_fields(self) -> None:
        _check._open_url = _route_fixture_registry
        bib_path = self._write_bib(_FIXTURE_BIB_TEXT)
        report = self._lookup(["--bib", str(bib_path)], 1)
        self.assertEqual(
            set(report),
            {"version", "bib_sha256", "checked_at", "entries", "counts",
             "blocking", "nothing_verified", "ok"},
        )
        self.assertEqual(report["version"], 1)
        self.assertRegex(report["checked_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        for entry in report["entries"]:
            self.assertEqual(set(entry), {"key", "doi", "arxiv_id", "title", "status", "detail"})
        self.assertEqual(set(report["counts"]), {"verified", "blocking", "warning"})

    def test_bib_sha256_is_the_hash_of_the_exact_file_checked(self) -> None:
        _check._open_url = _route_fixture_registry
        bib_path = self._write_bib(_FIXTURE_BIB_TEXT)
        report = self._lookup(["--bib", str(bib_path)], 1)
        self.assertEqual(report["bib_sha256"], hashlib.sha256(bib_path.read_bytes()).hexdigest())
        bib_path.write_text(_FIXTURE_BIB_TEXT + "\n% edited after the check\n")
        self.assertNotEqual(
            report["bib_sha256"], hashlib.sha256(bib_path.read_bytes()).hexdigest()
        )

    def test_bib_sha256_hashes_the_bytes_that_were_parsed_not_a_mid_run_edit(self) -> None:
        bib_path = self._write_bib(_FIXTURE_BIB_TEXT)
        original_digest = hashlib.sha256(bib_path.read_bytes()).hexdigest()

        def _route_and_edit_the_bib(url: str, accept: str = "application/json"):
            bib_path.write_text(_FIXTURE_BIB_TEXT + "\n% edited during the check\n")
            return _route_fixture_registry(url, accept)

        _check._open_url = _route_and_edit_the_bib
        report = self._lookup(["--bib", str(bib_path)], 1)
        self.assertEqual(report["bib_sha256"], original_digest)


class TestCacheRoundTrip(_CheckTestCase):
    def test_second_run_verifies_from_the_cache_when_offline(self) -> None:
        _check._open_url = _route_fixture_registry
        bib_path = self._write_bib(_FIXTURE_BIB_TEXT)
        self._lookup(["--bib", str(bib_path)], 1)

        cache = json.loads((self.out_path.parent / "citation-cache.json").read_text())
        self.assertIn("doi:10.1234/exact.5678", cache)
        self.assertIn("arxiv:2401.01234", cache)
        self.assertTrue(all(key.split(":", 1)[0] in ("doi", "arxiv", "title") for key in cache))
        # Positive-only: the unresolved entry never enters the cache.
        self.assertFalse([key for key in cache if "invented" in key])

        # Offline, the cached entries still verify and the network failure on the
        # uncached one is a warning, not a blocking status, so the check passes.
        _check._open_url = _refuse_network
        report = self._lookup(["--bib", str(bib_path)], None)
        statuses = {entry["key"]: entry["status"] for entry in report["entries"]}
        self.assertEqual(statuses["example2024deterministic"], "verified")
        self.assertEqual(statuses["instance2023predicting"], "verified")
        self.assertEqual(statuses["nobody2022invented"], "network_error")
        self.assertFalse(report["nothing_verified"])


class TestRefsJsonInput(_CheckTestCase):
    def test_refs_json_entries_resolve_and_classify(self) -> None:
        _check._open_url = _route_fixture_registry
        refs_path = self.scratch_dir / "refs.json"
        refs_path.write_text(json.dumps([
            {
                "referenceString": "Instance & Case (2023). Predicting Citation Impact...",
                "bibliography": {
                    "doi": "10.1234/exact.5678",
                    "authors": ["Carol Instance", "Dana Case"],
                    "year": 2023,
                    "title": "Predicting Citation Impact with Cohort Percentiles",
                },
            },
            {
                "referenceString": "an unusable reference with nothing to look up",
                "bibliography": {"doi": None, "authors": [], "year": None},
            },
        ]))
        report = self._lookup(["--refs-json", str(refs_path)], None)
        self.assertEqual([entry["key"] for entry in report["entries"]], ["ref-1", "ref-2"])
        self.assertEqual(
            [entry["status"] for entry in report["entries"]], ["verified", "no_query"]
        )
        self.assertEqual(report["counts"], {"verified": 1, "blocking": 0, "warning": 1})
        self.assertEqual(
            report["bib_sha256"], hashlib.sha256(refs_path.read_bytes()).hexdigest()
        )

    def test_zero_positive_verifications_set_nothing_verified(self) -> None:
        _check._open_url = _route_fixture_registry
        refs_path = self.scratch_dir / "refs.json"
        refs_path.write_text(json.dumps([
            {
                "referenceString": "nothing to look up",
                "bibliography": {"doi": None, "authors": [], "year": None},
            },
        ]))
        report = self._lookup(["--refs-json", str(refs_path)], 1)
        self.assertTrue(report["nothing_verified"])
        self.assertEqual(report["blocking"], 0)
        self.assertFalse(report["ok"])


class TestAddSubcommand(_CheckTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.bib_path = self.scratch_dir / "draft" / "references.bib"

    def _add(self, argv_tail: list[str], expected_exit_code: int | None = None) -> str:
        return self._run_command(["add", "--bib", str(self.bib_path), *argv_tail],
                                 expected_exit_code)

    def test_add_doi_appends_the_rendered_registry_entry(self) -> None:
        _check._open_url = _route_fixture_registry
        stdout_text = self._add(["--doi", "10.1234/exact.5678"])
        bib_text = self.bib_path.read_text()
        self.assertIn("@article{instance2023predicting,", bib_text)
        self.assertIn("title={Predicting Citation Impact with Cohort Percentiles}", bib_text)
        self.assertIn("author={Carol Instance and Dana Case}", bib_text)
        self.assertIn("doi={10.1234/exact.5678}", bib_text)
        self.assertIn("@article{instance2023predicting,", stdout_text)

    def test_add_refuses_a_duplicate_key(self) -> None:
        _check._open_url = _route_fixture_registry
        self._add(["--doi", "10.1234/exact.5678"])
        self._add(["--doi", "10.1234/exact.5678"], expected_exit_code=1)
        self.assertEqual(self.bib_path.read_text().count("instance2023predicting"), 1)

    def test_add_arxiv_id_renders_a_misc_entry(self) -> None:
        _check._open_url = _route_fixture_registry
        self._add(["--arxiv-id", "2401.01234"])
        bib_text = self.bib_path.read_text()
        self.assertIn("@misc{example2024deterministic,", bib_text)
        self.assertIn("eprint={2401.01234}", bib_text)
        self.assertIn("archivePrefix={arXiv}", bib_text)
        self.assertIn("primaryClass={cs.DL}", bib_text)

    def test_add_disambiguates_a_key_collision_between_different_papers(self) -> None:
        def _crossref_response(title: str, doi: str) -> str:
            return json.dumps({"message": {
                "title": [title],
                "author": [{"given": "Alice", "family": "Smith"}],
                "issued": {"date-parts": [[2024]]},
                "DOI": doi,
                "container-title": ["Journal of Tests"],
                "type": "journal-article",
            }})

        def _route_two_smith_papers(url: str, accept: str = "application/json"):
            if "10.1111%2Ffly" in url:
                return 200, _crossref_response("Learning to Fly", "10.1111/fly")
            if "10.1111%2Fswim" in url:
                return 200, _crossref_response("Learning to Swim", "10.1111/swim")
            raise AssertionError(f"unexpected URL in test: {url}")

        _check._open_url = _route_two_smith_papers
        self._add(["--doi", "10.1111/fly"])
        self._add(["--doi", "10.1111/swim"])
        entries = _check._parse_bib(self.bib_path.read_text())
        self.assertEqual(
            [entry["key"] for entry in entries],
            ["smith2024learning", "smith2024learningb"],
        )
        # A re-add of the disambiguated paper is a true duplicate and is refused.
        self._add(["--doi", "10.1111/swim"], expected_exit_code=1)
        self.assertEqual(len(_check._parse_bib(self.bib_path.read_text())), 2)

    def test_make_entry_key_falls_back_to_anon_for_a_non_latin_author(self) -> None:
        key = _check._make_entry_key(
            {"authors": ["田中 太郎"], "title": "Quantum Widgets", "year": 2024}
        )
        self.assertEqual(key, "anon2024quantum")

    def test_add_falls_back_to_datacite_when_crossref_404s(self) -> None:
        def route_with_datacite(url: str, accept: str = "application/json"):
            if url.startswith("https://api.crossref.org/works/"):
                return 404, None
            if url.startswith("https://api.datacite.org/dois/"):
                return 200, _DATACITE_DOI_TEXT
            raise AssertionError(f"unexpected URL in test: {url}")

        _check._open_url = route_with_datacite
        self._add(["--doi", "10.5555/data.999"])
        bib_text = self.bib_path.read_text()
        self.assertIn("@misc{registry2021dataset,", bib_text)
        self.assertIn("doi={10.5555/data.999}", bib_text)


class TestGateSubcommand(_CheckTestCase):
    def setUp(self) -> None:
        super().setUp()
        (self.scratch_dir / ".exactory").mkdir(exist_ok=True)
        (self.scratch_dir / "draft").mkdir()
        (self.scratch_dir / ".exactory" / "draft.json").write_text(json.dumps({
            "version": 1, "title": "Cohort Percentiles", "corpus": "arxiv",
            "category": "cs.MA", "created": "2026-08-08T00:00:00Z",
        }))
        self.bib_path = self.scratch_dir / "draft" / "references.bib"
        self.report_path = self.scratch_dir / ".exactory" / "citation-check.json"

    def _write_report(self, blocking: int, nothing_verified: bool,
                      bib_sha256: str | None = None) -> None:
        report = {
            "version": 1,
            "bib_sha256": bib_sha256 if bib_sha256 is not None
            else hashlib.sha256(self.bib_path.read_bytes()).hexdigest(),
            "checked_at": "2026-08-08T00:00:00Z",
            "entries": [],
            "counts": {"verified": 1, "blocking": blocking, "warning": 0},
            "blocking": blocking,
            "nothing_verified": nothing_verified,
            "ok": blocking == 0 and not nothing_verified,
        }
        self.report_path.write_text(json.dumps(report))

    def _write_passing_state(self) -> None:
        self.bib_path.write_text(_FIXTURE_BIB_TEXT)
        self._write_report(blocking=0, nothing_verified=False)

    def _gate(self, expected_exit_code: int | None = None) -> str:
        return self._run_command(
            ["gate", "--workspace", str(self.scratch_dir)], expected_exit_code
        )

    def test_a_fresh_clean_report_passes(self) -> None:
        self._write_passing_state()
        output = self._gate(None)
        self.assertIn("Citation gate: pass", output)

    def test_a_missing_references_file_fails(self) -> None:
        output = self._gate(1)
        self.assertIn("references.bib", output)
        self.assertIn("exactory-check add", output)

    def test_a_missing_report_fails_and_names_the_lookup_command(self) -> None:
        self.bib_path.write_text(_FIXTURE_BIB_TEXT)
        output = self._gate(1)
        self.assertIn("exactory-check lookup --bib draft/references.bib", output)

    def test_an_unreadable_report_fails(self) -> None:
        self.bib_path.write_text(_FIXTURE_BIB_TEXT)
        self.report_path.write_text("not json")
        output = self._gate(1)
        self.assertIn("exactory-check lookup --bib draft/references.bib", output)

    def test_a_stale_report_hash_fails(self) -> None:
        self._write_passing_state()
        self.bib_path.write_text(_FIXTURE_BIB_TEXT + "\n% edited after the check\n")
        output = self._gate(1)
        self.assertIn("stale", output)

    def test_blocking_findings_fail(self) -> None:
        self.bib_path.write_text(_FIXTURE_BIB_TEXT)
        self._write_report(blocking=1, nothing_verified=False)
        self._gate(1)

    def test_nothing_verified_fails(self) -> None:
        self.bib_path.write_text(_FIXTURE_BIB_TEXT)
        self._write_report(blocking=0, nothing_verified=True)
        self._gate(1)

    def test_a_directory_without_a_workspace_exits_2(self) -> None:
        empty_dir = self.scratch_dir / "elsewhere"
        empty_dir.mkdir()
        self._run_command(["gate", "--workspace", str(empty_dir)], 2)

    def test_the_default_workspace_is_found_by_walking_up_from_cwd(self) -> None:
        self._write_passing_state()
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.scratch_dir / "draft")
        output = self._run_command(["gate"], None)
        self.assertIn("Citation gate: pass", output)


class TestParserStrictness(unittest.TestCase):
    def test_an_abbreviated_flag_is_rejected(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                _check._build_parser().parse_args(["verify", "--refs", "x"])
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
