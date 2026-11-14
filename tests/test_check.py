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
from unittest import mock
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
        self.assertEqual(report["counts"], {"verified": 2, "blocking": 1, "warning": 0, "incomplete": 0})
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
        self.assertEqual(report["counts"], {"verified": 0, "blocking": 1, "warning": 0, "incomplete": 0})

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
        self.assertEqual(report["counts"], {"verified": 0, "blocking": 1, "warning": 0, "incomplete": 0})

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
        self.assertEqual(report["counts"], {"verified": 1, "blocking": 0, "warning": 1, "incomplete": 0})
        self.assertTrue(report["ok"])
        self.assertTrue(report["lookup_complete"])
        self.assertFalse(report["all_verified"])
        self.assertEqual(report["incomplete_keys"], [])

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
             "blocking", "nothing_verified", "ok", "manuscript", "lookup_complete",
             "all_verified", "incomplete_keys"},
        )
        self.assertEqual(set(report["manuscript"]),
                         {"main", "tex_sha256", "uncited_keys", "prior_art_without_citation"})
        self.assertEqual(report["version"], 2)
        self.assertRegex(report["checked_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        for entry in report["entries"]:
            self.assertEqual(set(entry), {"key", "doi", "arxiv_id", "title", "status", "detail", "searches"})
        self.assertEqual(set(report["counts"]), {"verified", "blocking", "warning", "incomplete"})

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

        cache = json.loads((self.out_path.parent / "citation-cache.json").read_text())["records"]
        self.assertIn("doi:10.1234/exact.5678", cache)
        self.assertIn("arxiv:2401.01234", cache)
        self.assertTrue(all(key.split(":", 1)[0] in ("doi", "arxiv", "title") for key in cache))
        # Positive-only: the unresolved entry never enters the cache.
        self.assertFalse([key for key in cache if "invented" in key])

        # Positive cache hits do not make an uncached failed lookup complete.
        _check._open_url = _refuse_network
        report = self._lookup(["--bib", str(bib_path)], 1)
        statuses = {entry["key"]: entry["status"] for entry in report["entries"]}
        self.assertEqual(statuses["example2024deterministic"], "verified")
        self.assertEqual(statuses["instance2023predicting"], "verified")
        self.assertEqual(statuses["nobody2022invented"], "network_error")
        self.assertFalse(report["nothing_verified"])
        self.assertFalse(report["lookup_complete"])
        self.assertFalse(report["ok"])
        self.assertEqual(report["incomplete_keys"], ["nobody2022invented"])


class TestRefsJsonInput(_CheckTestCase):
    def test_incident_arxiv_titles_with_tex_braces_verify(self) -> None:
        # Synthetic titles retain the TeX shapes from
        # 20261112210200-citation-check-title-with-tex-braces.
        titles = [
            ("1601.03705", r"Synthetic counting example in $\mathrm{SL}_2(\mathbf{Z})$"),
            ("2605.02518", r"Synthetic expansion example in $\text{SL}_2(\mathbb Z/q\mathbb Z)$"),
        ]
        refs = []
        for identifier, title in titles:
            refs.append({"referenceString": title, "bibliography": {
                "arxivId": identifier, "title": title, "authors": ["Alice Example", "Bob Sample"]}})

        def route(url, accept="application/json"):
            for identifier, title in titles:
                if "id_list=" + identifier in url:
                    return 200, _ARXIV_ENTRY_TEXT.replace("2401.01234", identifier).replace(
                        "Deterministic Citation Verification for Automated Research", title)
            raise AssertionError(url)

        _check._open_url = route
        refs_path = self.scratch_dir / "refs.json"
        refs_path.write_text(json.dumps(refs))
        report = self._lookup(["--refs-json", str(refs_path)], None)
        self.assertEqual([entry["status"] for entry in report["entries"]], ["verified", "verified"])
        # A second pass must verify against cached metadata too.
        _check._open_url = _refuse_network
        self.assertTrue(self._lookup(["--refs-json", str(refs_path)], None)["all_verified"])
        # Grouping braces must not erase the arguments of TeX commands.
        resolved = r"$\mathrm{SL}_2(\mathbf{Z})$"
        meta = {"title": resolved, "titles": [resolved], "authors": ["Alice Example"]}
        entry = {"title": resolved, "author": "Alice Example"}
        self.assertEqual(_check._compare_entry_with_meta(entry, meta)["status"], "verified")
        entry["title"] = r"$\mathrm{GL}_2(\mathbf{Q})$"
        self.assertEqual(_check._compare_entry_with_meta(entry, meta)["status"], "title_mismatch")

    def test_doi_tex_titles_verify_and_keep_chapter_title_matching(self) -> None:
        resolved = r"$\mathrm{SL}_2(\mathbf{Z})$"
        crossref = json.loads(_CROSSREF_WORK_TEXT)
        crossref["message"]["title"] = [resolved]
        _check._open_url = lambda url, accept="application/json": (200, json.dumps(crossref))
        refs_path = self.scratch_dir / "refs.json"
        refs_path.write_text(json.dumps([{"referenceString": resolved, "bibliography": {
            "doi": "10.1234/exact.5678", "title": resolved, "authors": ["Carol Instance", "Dana Case"]}}]))
        self.assertTrue(self._lookup(["--refs-json", str(refs_path)], None)["all_verified"])
        crossref["message"]["title"] = ["Chapter {5}: " + resolved]
        meta = _check._normalize_crossref_work(crossref["message"])
        self.assertEqual(_check._compare_entry_with_meta({"title": resolved,
            "author": "Carol Instance and Dana Case"}, meta)["status"], "verified")

    def test_title_mismatch_quotes_both_original_titles(self) -> None:
        resolved = r"Registry title in $\mathrm{SL}_2(\mathbf{Z})$"
        supplied = r"Invented title in $\text{GL}_3(\mathbf{Q})$"
        _check._open_url = lambda url, accept="application/json": (200, _ARXIV_ENTRY_TEXT.replace(
            "Deterministic Citation Verification for Automated Research", resolved))
        refs_path = self.scratch_dir / "refs.json"
        refs_path.write_text(json.dumps([{"referenceString": supplied, "bibliography": {
            "arxivId": "2401.01234", "title": supplied, "authors": ["Alice Example", "Bob Sample"]}}]))
        entry = self._lookup(["--refs-json", str(refs_path)], 1)["entries"][0]
        self.assertEqual(entry["status"], "title_mismatch")
        self.assertIn("'" + resolved + "'", entry["detail"])
        self.assertIn("'" + supplied + "'", entry["detail"])

    def test_doi_mismatch_quotes_registry_markup_and_the_original_bibtex_title(self) -> None:
        resolved = "A {registry} <i>title</i>"
        supplied = r"A fabricated title with $\mathrm{GL}_3$"
        bib_path = self._write_bib("@article{raw, title={" + supplied + "}, doi={10.1234/exact.5678}}")
        crossref = json.loads(_CROSSREF_WORK_TEXT)
        crossref["message"]["title"] = [resolved]
        datacite = json.loads(_DATACITE_DOI_TEXT)
        datacite["data"]["attributes"]["titles"] = [{"title": resolved}]
        for registry in ("crossref", "datacite"):
            with self.subTest(registry=registry):
                def route(url, accept="application/json"):
                    if url.startswith(_check._CROSSREF_API_URL):
                        return (200, json.dumps(crossref)) if registry == "crossref" else (404, None)
                    return 200, json.dumps(datacite)
                _check._open_url = route
                result = self._lookup(["--bib", str(bib_path)], 1)["entries"][0]
                self.assertEqual(result["status"], "title_mismatch")
                self.assertIn("'" + resolved + "'", result["detail"])
                self.assertIn("'" + supplied + "'", result["detail"])

    def test_prescribed_identifier_title_authors_shape_verifies_doi_and_arxiv(self) -> None:
        _check._open_url = _route_fixture_registry
        refs_path = self.scratch_dir / "refs.json"
        refs_path.write_text(json.dumps([
            {"referenceString": "Carol Instance, Dana Case. Predicting Citation Impact with Cohort Percentiles",
             "bibliography": {"doi": "10.1234/exact.5678", "title": "Predicting Citation Impact with Cohort Percentiles",
                              "authors": ["Carol Instance", "Dana Case"]}},
            {"referenceString": "Alice Example, Bob Sample. Deterministic Citation Verification for Automated Research",
             "bibliography": {"arxivId": "2401.01234", "title": "Deterministic Citation Verification for Automated Research",
                              "authors": ["Alice Example", "Bob Sample"]}},
        ]))
        self.assertTrue(self._lookup(["--refs-json", str(refs_path)], None)["all_verified"])
        refs = json.loads(refs_path.read_text())
        for item in refs:
            del item["bibliography"]["authors"]
        refs_path.write_text(json.dumps(refs))
        report = self._lookup(["--refs-json", str(refs_path)], 1)
        self.assertEqual([row["status"] for row in report["entries"]], ["author_mismatch", "author_mismatch"])

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
        report = self._lookup(["--refs-json", str(refs_path)], 1)
        self.assertEqual([entry["key"] for entry in report["entries"]], ["ref-1", "ref-2"])
        self.assertEqual(
            [entry["status"] for entry in report["entries"]], ["verified", "no_query"]
        )
        self.assertEqual(report["counts"], {"verified": 1, "blocking": 0, "warning": 0, "incomplete": 1})
        self.assertFalse(report["ok"])
        self.assertFalse(report["lookup_complete"])
        self.assertFalse(report["all_verified"])
        self.assertEqual(report["incomplete_keys"], ["ref-2"])
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


class TestRegistryRequests(_CheckTestCase):
    def test_default_admission_paces_independent_arxiv_calls(self):
        from research_fixtures import Clock
        clock = Clock()
        starts = []
        initial = clock.monotonic()

        def respond(request, timeout):
            starts.append(clock.monotonic())
            response = io.BytesIO(_ARXIV_ENTRY_TEXT.encode())
            response.getcode = lambda: 200
            return response

        # Each call constructs its own admission object; only durable state can
        # preserve the interval between them.
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.scratch_dir)}), \
                mock.patch.object(_check.urllib.request, "urlopen", side_effect=respond):
            for identifier in ("2401.01234", "2401.01235"):
                code, _ = _check._open_url(_check._ARXIV_API_URL + "?id_list=" + identifier, clock=clock)
                self.assertEqual(code, 200)
        self.assertEqual(starts, [initial, initial + 3.0])
        self.assertEqual(clock.sleeps, [3.0])

    def install_registry(self, statuses=(), retry_after=None):
        from research_fixtures import Clock
        from research_harness.provider_pacing import ProviderPacing
        clock = Clock()
        pacing = ProviderPacing(clock=clock, state_dir=self.scratch_dir / "provider-state")
        open_url = _check._open_url
        starts, requests = [], []
        remaining = iter(statuses)
        opened_bodies = []
        admit = pacing.admit

        @contextlib.contextmanager
        def admit_request(url, **options):
            with admit(url, **options):
                try:
                    yield
                finally:
                    self.assertTrue(all(body.closed for body in opened_bodies),
                                    "close each response before releasing provider admission")

        pacing.admit = admit_request

        def respond(request, timeout):
            import urllib.error
            import urllib.parse
            starts.append(clock.monotonic())
            requests.append(request.full_url)
            status = next(remaining, 200)
            if status != 200:
                body = io.BytesIO(b"Registry error response")
                opened_bodies.append(body)
                raise urllib.error.HTTPError(request.full_url, status, "registry unavailable",
                                             {"Retry-After": retry_after} if retry_after is not None else {}, body)
            identifier = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)["id_list"][0]
            response = io.BytesIO(_ARXIV_ENTRY_TEXT.replace("2401.01234", identifier).encode())
            opened_bodies.append(response)
            response.getcode = lambda: 200
            return response

        patcher = mock.patch.object(_check.urllib.request, "urlopen", side_effect=respond)
        patcher.start()
        self.addCleanup(patcher.stop)
        _check._open_url = lambda url, accept="application/json", **options: open_url(
            url, accept, clock=clock, pacing=pacing, **options)
        return clock, starts, requests

    def test_lookup_paces_forty_arxiv_entries_and_retries_429_and_503(self):
        clock, starts, requests = self.install_registry([429, 503])
        initial = clock.monotonic()
        refs_path = self.scratch_dir / "refs.json"
        refs_path.write_text(json.dumps([{
            "referenceString": "Alice Example, Bob Sample. Deterministic Citation Verification for Automated Research",
            "bibliography": {"arxivId": "2401.%05d" % index,
                "title": "Deterministic Citation Verification for Automated Research",
                "authors": ["Alice Example", "Bob Sample"]}} for index in range(40)]))
        report = self._lookup(["--refs-json", str(refs_path)], None)
        self.assertTrue(report["all_verified"])
        self.assertEqual(len(starts), 42)
        self.assertEqual(starts[0], initial)
        self.assertTrue(all(later - earlier >= 3.0 for earlier, later in zip(starts, starts[1:])))
        self.assertEqual(requests[:3], [requests[0]] * 3)
        self.assertTrue(clock.sleeps)

    def test_arxiv_retry_exhaustion_is_bounded_and_remains_incomplete(self):
        clock, starts, _ = self.install_registry([503] * 10)
        refs_path = self.scratch_dir / "refs.json"
        refs_path.write_text(json.dumps([{"referenceString": "Unavailable work", "bibliography": {
            "arxivId": "2401.01234", "title": "Unavailable work", "authors": ["Alice Example"]}}]))
        report = self._lookup(["--refs-json", str(refs_path)], 1)
        self.assertEqual(report["entries"][0]["status"], "network_error")
        self.assertIn("HTTP 503", report["entries"][0]["detail"])
        self.assertFalse(report["lookup_complete"])
        self.assertEqual(len(starts), 4)
        self.assertTrue(all(later - earlier >= 3.0 for earlier, later in zip(starts, starts[1:])))

    def test_arxiv_retry_after_waits_and_nonretryable_http_is_not_retried(self):
        clock, starts, _ = self.install_registry([429, 200, 404], retry_after="7")
        initial = clock.monotonic()
        self.assertIn("meta", _check._resolve_arxiv_id("2401.01234"))
        self.assertEqual(starts[:2], [initial, initial + 7.0])
        self.assertEqual(_check._resolve_arxiv_id("2401.01235")["status"], "network_error")
        self.assertEqual(len(starts), 3)
        self.assertEqual(starts[-1], initial + 10.0)


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
        main, sources = _check._read_manuscript_sources(self.scratch_dir / "draft", None)
        entries = [{"key": entry["key"], "status": "verified"}
                   for entry in _check._parse_bib(self.bib_path.read_text())]
        if entries and blocking:
            entries[-1]["status"] = "unresolved"
        if nothing_verified:
            for entry in entries:
                entry["status"] = "no_query"
        report = {
            "version": 2,
            "bib_sha256": bib_sha256 if bib_sha256 is not None
            else hashlib.sha256(self.bib_path.read_bytes()).hexdigest(),
            "checked_at": "2026-08-08T00:00:00Z",
            "entries": entries,
            "counts": {"verified": sum(entry["status"] == "verified" for entry in entries),
                       "blocking": blocking, "warning": 0,
                       "incomplete": len(entries) if nothing_verified else 0},
            "blocking": blocking,
            "nothing_verified": nothing_verified,
            "ok": blocking == 0 and not nothing_verified,
            "lookup_complete": not nothing_verified,
            "all_verified": not blocking and not nothing_verified,
            "incomplete_keys": [entry["key"] for entry in entries] if nothing_verified else [],
            "manuscript": {"main": main, "tex_sha256": _check._hash_manuscript_sources(sources)},
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

    def test_a_version_one_report_requires_a_fresh_lookup(self) -> None:
        self._write_passing_state()
        report = json.loads(self.report_path.read_text())
        report["version"] = 1
        self.report_path.write_text(json.dumps(report))
        self.assertIn("version 2", self._gate(1))

    def test_gate_derives_incompleteness_from_entries_despite_passing_flags(self) -> None:
        for status in ("no_query", "network_error", "search_incomplete", "unrecognized"):
            with self.subTest(status=status):
                self._write_passing_state()
                report = json.loads(self.report_path.read_text())
                report["entries"][-1]["status"] = status
                self.report_path.write_text(json.dumps(report))
                self._gate(1)

    def test_gate_rejects_omitted_entries_and_hidden_mismatches(self) -> None:
        for change in ("omit", "mismatch", "malformed"):
            with self.subTest(change=change):
                self._write_passing_state()
                report = json.loads(self.report_path.read_text())
                if change == "omit":
                    report["entries"].pop()
                elif change == "mismatch":
                    report["entries"][-1]["status"] = "author_mismatch"
                else:
                    report["entries"] = {"status": "verified"}
                self.report_path.write_text(json.dumps(report))
                self._gate(1)

    def test_gate_passes_a_complete_lookup_with_a_year_warning(self) -> None:
        self._write_passing_state()
        report = json.loads(self.report_path.read_text())
        report["entries"][-1]["status"] = "year_mismatch"
        report["all_verified"] = False
        report["counts"]["verified"] -= 1
        report["counts"]["warning"] = 1
        self.report_path.write_text(json.dumps(report))
        output = self._gate(None)
        self.assertIn("warning", output.lower())

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

    def test_a_report_without_manuscript_checks_is_stale(self) -> None:
        self._write_passing_state()
        report = json.loads(self.report_path.read_text())
        del report["manuscript"]
        self.report_path.write_text(json.dumps(report))
        self.assertIn("predates the manuscript checks", self._gate(1))

    def test_a_changed_manuscript_source_makes_the_report_stale(self) -> None:
        self.bib_path.write_text(_FIXTURE_BIB_TEXT)
        tex_path = self.scratch_dir / "draft" / "paper.tex"
        tex_path.write_text("\\documentclass{article}\n\\input{appendix}\n")
        self._write_report(blocking=0, nothing_verified=False)
        self.assertEqual(json.loads(self.report_path.read_text())["manuscript"]["main"], "paper.tex")
        self.assertIn("Citation gate: pass", self._gate(None))
        (self.scratch_dir / "draft" / "unrelated.tex").write_text("An unrelated file.\n")
        self.assertIn("Citation gate: pass", self._gate(None))
        (self.scratch_dir / "draft" / "appendix.tex").write_text("Included text.\n")
        self.assertIn("manuscript", self._gate(1))
        (self.scratch_dir / "draft" / "appendix.tex").unlink()
        tex_path.write_text("\\documentclass{article}\n\\cite{example2024deterministic}\n")
        self.assertIn("manuscript", self._gate(1))

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


_FIXTURE_DOI_TITLE_KEY = "title:predicting citation impact with cohort percentiles"


class TestCacheIdentity(_CheckTestCase):
    """The cache answers an entry only under the identity it is resolved by."""

    def test_old_brace_stripped_cache_is_refreshed_before_reporting_a_mismatch(self) -> None:
        resolved = r"Synthetic counting example in $\mathrm{SL}_2(\mathbf{Z})$"
        stripped = resolved.replace("{", "").replace("}", "")
        cache_path = self.out_path.parent / "citation-cache.json"
        cache_path.parent.mkdir(parents=True)
        cache_path.write_text(json.dumps({"version": 2, "records": {"arxiv:1601.03705": {
            "source": "arxiv", "title": stripped, "titles": [stripped],
            "authors": ["Alice Example", "Bob Sample"]}}}))
        refs_path = self.scratch_dir / "refs.json"
        refs = [{"referenceString": stripped, "bibliography": {"arxivId": "1601.03705", "title": stripped,
            "authors": ["Alice Example", "Bob Sample"]}}]
        refs_path.write_text(json.dumps(refs))
        _check._open_url = _refuse_network
        self.assertTrue(self._lookup(["--refs-json", str(refs_path)], None)["all_verified"])
        refs[0]["bibliography"]["title"] = "An entirely different and fabricated title"
        refs_path.write_text(json.dumps(refs))
        _check._open_url = lambda url, accept="application/json": (200, _ARXIV_ENTRY_TEXT.replace(
            "Deterministic Citation Verification for Automated Research", resolved))
        result = self._lookup(["--refs-json", str(refs_path)], 1)["entries"][0]
        self.assertEqual(result["status"], "title_mismatch")
        self.assertIn("'" + resolved + "'", result["detail"])

    def test_a_doi_entry_ignores_a_preprint_record_cached_under_its_title(self) -> None:
        # A replaced arXiv entry of the same paper left its record under the title key.
        cache_path = self.out_path.parent / "citation-cache.json"
        cache_path.parent.mkdir(parents=True)
        cache_path.write_text(json.dumps({"version": 2, "records": {_FIXTURE_DOI_TITLE_KEY: {
            "source": "arxiv", "title": "Predicting Citation Impact with Cohort Percentiles",
            "titles": ["Predicting Citation Impact with Cohort Percentiles"],
            "authors": ["Instance Carol", "Case Dana"], "year": 2022,
        }}}))
        _check._open_url = _route_fixture_registry
        doi_entry = _FIXTURE_BIB_TEXT.split("\n\n")[1]
        report = self._lookup(["--bib", str(self._write_bib(doi_entry))], None)
        self.assertEqual(report["entries"][0]["status"], "verified")

    def test_records_are_cached_under_the_primary_identity_only(self) -> None:
        _check._open_url = _route_fixture_registry
        self._lookup(["--bib", str(self._write_bib(_FIXTURE_BIB_TEXT))], 1)
        cache = json.loads((self.out_path.parent / "citation-cache.json").read_text())
        self.assertEqual(cache["version"], 2)
        self.assertEqual(sorted(cache["records"]), ["arxiv:2401.01234", "doi:10.1234/exact.5678"])

    def test_a_cache_written_before_the_identity_rule_is_ignored(self) -> None:
        # 0.42.2 stored the journal record of an entry with a DOI and an eprint under its arXiv key.
        cache_path = self.out_path.parent / "citation-cache.json"
        cache_path.parent.mkdir(parents=True)
        cache_path.write_text(json.dumps({"arxiv:2401.01234": {
            "source": "crossref", "title": "A Different Journal Title", "titles": ["A Different Journal Title"],
            "authors": ["Alice Example", "Bob Sample"], "year": 2025}}))
        _check._open_url = _route_fixture_registry
        preprint_entry = _FIXTURE_BIB_TEXT.split("\n\n")[0]
        report = self._lookup(["--bib", str(self._write_bib(preprint_entry))], None)
        self.assertEqual(report["entries"][0]["status"], "verified")


def _crossref_work_text(title: str, doi: str = "10.1234/exact.5678",
                        work_type: str = "journal-article",
                        family: str = "Example") -> str:
    return json.dumps({
        "title": [title], "author": [{"given": "Alice", "family": family},
                                     {"given": "Bob", "family": "Sample"}],
        "issued": {"date-parts": [[2025]]}, "DOI": doi,
        "container-title": ["Journal of Tests"], "type": work_type,
        "volume": "7", "issue": "2", "page": "11-19",
    })


class TestRegistryMarkup(_CheckTestCase):
    """Crossref and DataCite titles carry JATS/HTML inline markup."""

    def _add_title(self, title: str) -> str:
        def route(url: str, accept: str = "application/json"):
            if url.startswith("https://api.crossref.org/works/"):
                return 200, json.dumps({"message": json.loads(_crossref_work_text(title))})
            raise AssertionError(f"unexpected URL in test: {url}")

        _check._open_url = route
        bib_path = self.scratch_dir / "draft" / "references.bib"
        self._run_command(["add", "--bib", str(bib_path), "--doi", "10.1234/exact.5678"], None)
        title_line = next(line for line in bib_path.read_text().splitlines() if line.startswith("  title={"))
        return title_line[len("  title={"):-len("},")]

    def test_inline_markup_renders_as_latex(self) -> None:
        self.assertEqual(
            self._add_title("Transport in Sb<sub>2</sub>Te<sub>3</sub> films with"
                            " <i>ab initio</i> <scp>dft</scp> and x<sup>2</sup><b>!</b>"),
            "Transport in Sb$_{2}$Te$_{3}$ films with \\textit{ab initio} \\textsc{dft}"
            " and x$^{2}$\\textbf{!}")

    def test_space_before_a_subscript_tag_is_removed_and_unknown_tags_dropped(self) -> None:
        self.assertEqual(
            self._add_title("Coupling in a Bi <sub>2</sub> Te <sub>3</sub> <span>nanoplate</span>"),
            "Coupling in a Bi$_{2}$ Te$_{3}$ nanoplate")

    def test_angle_brackets_that_are_not_registry_tags_stay_text(self) -> None:
        self.assertEqual(_check._convert_markup_to_latex("When x<y and y>z holds"), "When x<y and y>z holds")
        self.assertNotEqual(_check._normalize_for_match("Title <and a fabricated extension>"),
                            _check._normalize_for_match("Title"))
        _check._open_url = _route_fixture_registry
        entry = ("@article{fabricated2023,\n  title={Predicting Citation Impact with Cohort Percentiles"
                 " <and a fabricated extension to quantum gravity in eleven dimensions>},\n"
                 "  author={Carol Instance and Dana Case},\n  year={2023},\n  doi={10.1234/exact.5678}\n}\n")
        report = self._lookup(["--bib", str(self._write_bib(entry))], 1)
        self.assertEqual(report["entries"][0]["status"], "title_mismatch")

    def test_a_leading_superscript_keeps_its_space(self) -> None:
        self.assertEqual(self._add_title("Observation of <sup>3</sup>He films"), "Observation of $^{3}$He films")

    def test_nested_scripts_use_one_math_group_and_crossed_tags_keep_text(self) -> None:
        self.assertEqual(_check._convert_markup_to_latex("E<sub>g<sup>*</sup></sub> gap"), "E$_{g^{*}}$ gap")
        self.assertEqual(_check._convert_markup_to_latex("<i>a<sub>b</sub></i> c"), "\\textit{a$_{b}$} c")
        self.assertEqual(_check._convert_markup_to_latex("<i>a<sub>b</i></sub> c"), "ab c")
        mathml = ('<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML"><mml:msub><mml:msub>'
                  '<mml:mi>x</mml:mi><mml:mi>a</mml:mi></mml:msub><mml:mi>b</mml:mi></mml:msub></mml:math>')
        self.assertEqual(_check._convert_markup_to_latex("Case" + mathml + "here"), "Case ${x_{a}}_{b}$ here")

    def test_isotope_prescripts_and_face_markup_render(self) -> None:
        isotope = ('<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML"><mml:mmultiscripts>'
                   '<mml:mi>Pb</mml:mi><mml:mprescripts/><mml:none/><mml:mn>208</mml:mn>'
                   '</mml:mmultiscripts></mml:math>')
        self.assertEqual(_check._convert_markup_to_latex("Spectra of" + isotope + "nuclei"),
                         "Spectra of $^{208}$Pb nuclei")
        self.assertEqual(_check._normalize_for_match("Spectra of" + isotope + "nuclei"),
                         _check._normalize_for_match("Spectra of $^{208}$Pb nuclei"))
        self.assertEqual(_check._convert_markup_to_latex("A <tt>code</tt> and <ovl>x</ovl> bar"),
                         "A \\texttt{code} and x bar")

    def test_attributes_that_carry_text_are_not_markup(self) -> None:
        self.assertNotEqual(_check._normalize_for_match('Real title <span title="fabricated">'),
                            _check._normalize_for_match("Real title"))
        self.assertEqual(_check._convert_markup_to_latex("A <i class='x'>word</i> here"),
                         "A \\textit{word} here")

    def test_a_script_inside_text_inside_a_script_opens_its_own_math_group(self) -> None:
        self.assertEqual(_check._convert_markup_to_latex("<sub>a<i>b<sup>c</sup></i></sub>"),
                         "$_{a\\textit{b$^{c}$}}$")

    def test_mathml_renders_as_latex_with_word_boundaries(self) -> None:
        mathml = ('<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML" display="inline">\n'
                  '  <mml:mrow><mml:msub><mml:mrow><mml:mi>Bi</mml:mi></mml:mrow><mml:mrow><mml:mn>2</mml:mn>'
                  '</mml:mrow></mml:msub><mml:msub><mml:mi>Se</mml:mi><mml:mn>3</mml:mn></mml:msub>'
                  '<mml:msup><mml:mi>x</mml:mi><mml:mn>2</mml:mn></mml:msup></mml:mrow>\n</mml:math>')
        self.assertEqual(self._add_title("Coupling in the Insulator" + mathml + "Using Photoemission"),
                         "Coupling in the Insulator Bi$_{2}$Se$_{3}$x$^{2}$ Using Photoemission")

    def test_malformed_mathml_keeps_its_text_between_word_boundaries(self) -> None:
        self.assertEqual(self._add_title("Films of<mml:math><mml:msub><mml:mi>Bi</mml:mi></mml:math>Grown"),
                         "Films of Bi Grown")

    def test_titles_match_across_markup_and_latex_forms(self) -> None:
        registry_title = "Transport in Sb<sub>2</sub>Te<sub>3</sub> Thin Films"

        def route(url: str, accept: str = "application/json"):
            if url.startswith("https://api.crossref.org/works/"):
                return 200, json.dumps({"message": json.loads(_crossref_work_text(registry_title))})
            raise AssertionError(f"unexpected URL in test: {url}")

        _check._open_url = route
        for title in ("Transport in Sb$_2$Te$_3$ Thin Films", registry_title):
            with self.subTest(title=title):
                entry = ("@article{example2025transport,\n  title={" + title + "},\n"
                         "  author={Alice Example and Bob Sample},\n  year={2025},\n"
                         "  doi={10.1234/exact.5678}\n}\n")
                self.out_path.parent.joinpath("citation-cache.json").unlink(missing_ok=True)
                report = self._lookup(["--bib", str(self._write_bib(entry))], None)
                self.assertEqual(report["entries"][0]["status"], "verified")


_TWO_REAL_ENTRIES_BIB_TEXT = "\n\n".join(_FIXTURE_BIB_TEXT.split("\n\n")[:2]) + "\n"


class TestManuscriptChecks(_CheckTestCase):
    """lookup reads the LaTeX sources next to the bibliography."""

    def setUp(self) -> None:
        super().setUp()
        _check._open_url = _route_fixture_registry
        self.draft_dir = self.scratch_dir / "draft"
        self.draft_dir.mkdir()
        self.bib_path = self.draft_dir / "references.bib"
        self.bib_path.write_text(_TWO_REAL_ENTRIES_BIB_TEXT)

    def _lookup_tex(self, tex: str, expected_exit_code: int | None) -> dict:
        (self.draft_dir / "paper.tex").write_text(tex)
        return self._lookup(["--bib", str(self.bib_path)], expected_exit_code)

    def test_an_entry_the_manuscript_never_cites_blocks(self) -> None:
        report = self._lookup_tex("Shown by \\citet{example2024deterministic}.\n"
                                  "% \\cite{instance2023predicting} in a comment only\n", 1)
        self.assertEqual(report["manuscript"]["uncited_keys"], ["instance2023predicting"])
        self.assertEqual(report["blocking"], 1)
        self.assertEqual(report["counts"], {"verified": 2, "blocking": 0, "warning": 0, "incomplete": 0})
        self.assertFalse(report["ok"])
        output = self._run_command(["lookup", "--out", str(self.out_path), "--bib", str(self.bib_path)], 1)
        self.assertIn("uncited: instance2023predicting", output)

    def test_every_citation_command_form_counts(self) -> None:
        report = self._lookup_tex("As in \\citet[see][p.~3]{instance2023predicting} and"
                                  " \\parencite*{example2024deterministic}.\n", None)
        self.assertEqual(report["manuscript"]["uncited_keys"], [])
        self.assertTrue(report["ok"])
        forms = ("See \\cites[p.~2]{example2024deterministic}{instance2023predicting}.\n",
                 "See \\cites{example2024deterministic}[p.~5]{instance2023predicting}.\n",
                 "See \\cites(See)()[p.~2]{example2024deterministic}{instance2023predicting}.\n",
                 "See \\cite<e.g.,>{example2024deterministic} and \\Citet{instance2023predicting}.\n",
                 "\\url{https://x.org/a%20b} \\cite{example2024deterministic}. \\cite{instance2023predicting}\n")
        for tex in forms:
            with self.subTest(tex=tex):
                self.assertEqual(self._lookup_tex(tex, None)["manuscript"]["uncited_keys"], [])

    def test_nocite_comments_and_disabled_blocks_do_not_cite(self) -> None:
        forms = ("\\cite{example2024deterministic}\\nocite{*}\n",
                 "\\cite{example2024deterministic}\\nocite{instance2023predicting}\n",
                 "\\cite{example2024deterministic} a \\\\% \\cite{instance2023predicting}\n",
                 "\\cite{example2024deterministic}\n\\iffalse\n\\cite{instance2023predicting}\n\\fi\n",
                 "\\cite{example2024deterministic}\n\\begin{comment}\n\\cite{instance2023predicting}\n\\end{comment}\n")
        for tex in forms:
            with self.subTest(tex=tex):
                self.assertEqual(self._lookup_tex(tex, 1)["manuscript"]["uncited_keys"], ["instance2023predicting"])

    def test_a_prior_art_sentence_without_a_citation_is_a_warning(self) -> None:
        report = self._lookup_tex(
            "\\begin{abstract}\nThe classical law is corrected.\n\\end{abstract}\n"
            "The classical treatment is well known.\n"
            "It has been shown to hold \\citep{instance2023predicting}. We cite"
            " \\citet{example2024deterministic}.\n", None)
        findings = report["manuscript"]["prior_art_without_citation"]
        self.assertEqual([(finding["file"], finding["line"]) for finding in findings], [("paper.tex", 4)])
        self.assertEqual(findings[0]["sentence"], "The classical treatment is well known.")
        self.assertEqual(report["blocking"], 0)
        self.assertEqual(report["counts"]["warning"], 0)
        self.assertTrue(report["ok"])

    def test_the_report_records_the_main_file_and_the_files_it_includes(self) -> None:
        (self.draft_dir / "sections").mkdir()
        (self.draft_dir / "sections" / "intro.tex").write_text("\\cite{instance2023predicting}\n")
        (self.draft_dir / "checkpoints").mkdir()
        (self.draft_dir / "checkpoints" / "old.tex").write_text(
            "\\documentclass{article}\n\\cite{nobody}. The classical case is well known.\n")
        report = self._lookup_tex("\\documentclass{article}\n\\input{sections/intro}\n"
                                  "\\cite{example2024deterministic}\n", None)
        self.assertEqual(report["manuscript"]["main"], "paper.tex")
        self.assertEqual(sorted(report["manuscript"]["tex_sha256"]), ["paper.tex", "sections/intro.tex"])
        self.assertEqual(report["manuscript"]["prior_art_without_citation"], [])

    def test_includes_resolve_from_the_main_file_directory(self) -> None:
        (self.draft_dir / "local.tex").write_text("\\documentclass{article}\n\\cite{example2024deterministic}\n")
        (self.draft_dir / "cumulative" / "sections").mkdir(parents=True)
        (self.draft_dir / "cumulative" / "main.tex").write_text(
            "\\documentclass{article}\n\\input{sections/a}\n\\cite{example2024deterministic}\n")
        (self.draft_dir / "cumulative" / "sections" / "a.tex").write_text("\\cite{instance2023predicting}\n")
        report = self._lookup(["--bib", str(self.bib_path), "--main", "cumulative/main.tex"], None)
        self.assertEqual(sorted(report["manuscript"]["tex_sha256"]),
                         ["cumulative/main.tex", "cumulative/sections/a.tex"])
        self.assertEqual(report["manuscript"]["uncited_keys"], [])

    def test_conditional_branches_verbatim_and_brace_free_input_are_read_as_latex(self) -> None:
        (self.draft_dir / "body.tex").write_text("\\cite{instance2023predicting}\n")
        forms = ("\\iffalse \\cite{nothing}\\else \\cite{instance2023predicting}\\fi \\cite{example2024deterministic}\n",
                 "\\iffalse \\ifx a b x\\fi \\cite{nothing}\\fi \\cite{example2024deterministic}"
                 " \\cite{instance2023predicting}\n",
                 "\\cite[{p. [5]}]{example2024deterministic} \\cite{instance2023predicting}\n",
                 "\\cite{example2024deterministic}\n\\input body\n",
                 "\\verb|%| \\cite{example2024deterministic} \\nolinkurl{a%b} \\cite{instance2023predicting}\n")
        for tex in forms:
            with self.subTest(tex=tex):
                self.assertEqual(self._lookup_tex(tex, None)["manuscript"]["uncited_keys"], [])
        report = self._lookup_tex("\\cite{example2024deterministic}\n\\iffalse \\ifx a b x\\fi"
                                  " \\cite{instance2023predicting}\\fi\n", 1)
        self.assertEqual(report["manuscript"]["uncited_keys"], ["instance2023predicting"])

    def test_several_root_files_need_a_main_file(self) -> None:
        for name in ("local-paper.tex", "cumulative-paper.tex"):
            (self.draft_dir / name).write_text("\\documentclass{article}\n\\cite{example2024deterministic}\n")
        output = self._run_command(["lookup", "--out", str(self.out_path), "--bib", str(self.bib_path)], 2)
        self.assertIn("--main", output)
        report = self._lookup(["--bib", str(self.bib_path), "--main", "local-paper.tex"], 1)
        self.assertEqual(report["manuscript"]["main"], "local-paper.tex")
        self.assertEqual(report["manuscript"]["uncited_keys"], ["instance2023predicting"])

    def test_a_bibliography_without_latex_sources_checks_only_the_registries(self) -> None:
        report = self._lookup(["--bib", str(self.bib_path)], None)
        self.assertEqual(report["manuscript"], {"main": None, "tex_sha256": {}, "uncited_keys": [],
                                                "prior_art_without_citation": []})


_ARXIV_TITLE = "Deterministic Citation Verification for Automated Research"


def _arxiv_feed(published_doi: str | None) -> str:
    doi_element = (f'<arxiv:doi xmlns:arxiv="http://arxiv.org/schemas/atom">{published_doi}</arxiv:doi>'
                   if published_doi else "")
    return _ARXIV_ENTRY_TEXT.replace("</title>", "</title>" + doi_element)


class TestVersionOfRecord(_CheckTestCase):
    """add --arxiv-id cites the published version when the registries name one."""

    def setUp(self) -> None:
        super().setUp()
        self.bib_path = self.scratch_dir / "draft" / "references.bib"

    def _route(self, published_doi: str | None, search_items: list[dict]):
        def route(url: str, accept: str = "application/json"):
            if url.startswith("https://export.arxiv.org/api/query"):
                return 200, _arxiv_feed(published_doi)
            if url.startswith("https://api.crossref.org/works?"):
                return 200, json.dumps({"message": {"items": search_items}})
            if url.startswith("https://api.crossref.org/works/"):
                return 200, json.dumps({"message": json.loads(
                    _crossref_work_text("Deterministic citation verification for automated research"))})
            raise AssertionError(f"unexpected URL in test: {url}")
        return route

    def _add(self, argv_tail: list[str]) -> tuple[str, dict]:
        stdout_text = self._run_command(["add", "--bib", str(self.bib_path), *argv_tail], None)
        return stdout_text, _check._parse_bib(self.bib_path.read_text())[0]

    def test_the_arxiv_record_doi_selects_the_published_version(self) -> None:
        _check._open_url = self._route("10.1234/exact.5678", [])
        stdout_text, entry = self._add(["--arxiv-id", "2401.01234"])
        self.assertEqual(entry["type"], "article")
        self.assertEqual(entry["doi"], "10.1234/exact.5678")
        self.assertEqual((entry["eprint"], entry["archiveprefix"]), ("2401.01234", "arXiv"))
        self.assertIn("published version", stdout_text)

    def test_a_unique_crossref_title_and_first_author_match_selects_the_published_version(self) -> None:
        item = json.loads(_crossref_work_text(_ARXIV_TITLE))
        preprint = json.loads(_crossref_work_text(_ARXIV_TITLE, doi="10.9999/preprint.1",
                                                  work_type="posted-content"))
        _check._open_url = self._route(None, [preprint, item])
        stdout_text, entry = self._add(["--arxiv-id", "2401.01234"])
        self.assertEqual(entry["doi"], "10.1234/exact.5678")
        self.assertIn("Crossref", stdout_text)

    def test_a_different_first_author_or_an_ambiguous_match_keeps_the_preprint(self) -> None:
        cases = {"other author": [json.loads(_crossref_work_text(_ARXIV_TITLE, family="Other"))],
                 "two matches": [json.loads(_crossref_work_text(_ARXIV_TITLE)),
                                 json.loads(_crossref_work_text(_ARXIV_TITLE, doi="10.1234/exact.9"))],
                 "none": []}
        for name, items in cases.items():
            with self.subTest(case=name):
                self.bib_path.unlink(missing_ok=True)
                _check._open_url = self._route(None, items)
                stdout_text, entry = self._add(["--arxiv-id", "2401.01234"])
                self.assertEqual(entry["type"], "misc")
                self.assertEqual(entry["eprint"], "2401.01234")
                self.assertIn("--doi", stdout_text)

    def test_a_crossref_match_needs_the_authors_and_a_plausible_year(self) -> None:
        cases = {"other given name": [json.loads(_crossref_work_text(_ARXIV_TITLE).replace('"Alice"', '"Jian"'))],
                 "other second author": [json.loads(_crossref_work_text(_ARXIV_TITLE).replace('"Sample"', '"Other"'))],
                 "much older record": [json.loads(_crossref_work_text(_ARXIV_TITLE).replace("[[2025]]", "[[1999]]"))]}
        for name, items in cases.items():
            with self.subTest(case=name):
                self.bib_path.unlink(missing_ok=True)
                _check._open_url = self._route(None, items)
                stdout_text, entry = self._add(["--arxiv-id", "2401.01234"])
                self.assertEqual(entry["type"], "misc")

    def test_the_arxiv_doi_field_is_checked_and_may_name_several_dois(self) -> None:
        dataset = json.dumps({"message": json.loads(_crossref_work_text("Raw data", doi="10.5555/data",
                                                                        work_type="dataset"))})
        other_authors = json.dumps({"message": json.loads(_crossref_work_text(
            "Another paper", doi="10.5555/other", family="Unrelated"))})
        journal = json.dumps({"message": json.loads(_crossref_work_text(
            "Deterministic citation verification for automated research"))})

        def route(url: str, accept: str = "application/json"):
            if url.startswith("https://export.arxiv.org/api/query"):
                return 200, _arxiv_feed("10.5555/data 10.5555/other 10.1234/exact.5678")
            for doi, body in (("10.5555%2Fdata", dataset), ("10.5555%2Fother", other_authors),
                              ("10.1234%2Fexact.5678", journal)):
                if url.startswith("https://api.crossref.org/works/" + doi):
                    return 200, body
            if url.startswith("https://api.crossref.org/works?"):
                return 200, json.dumps({"message": {"items": []}})
            raise AssertionError(f"unexpected URL in test: {url}")

        _check._open_url = route
        stdout_text, entry = self._add(["--arxiv-id", "2401.01234"])
        self.assertEqual(entry["doi"], "10.1234/exact.5678")
        self.assertIn("arXiv record", stdout_text)

    def test_an_unreachable_crossref_is_named_and_the_preprint_kept(self) -> None:
        def route(url: str, accept: str = "application/json"):
            if url.startswith("https://export.arxiv.org/api/query"):
                return 200, _arxiv_feed(None)
            return None, None

        _check._open_url = route
        stdout_text, entry = self._add(["--arxiv-id", "2401.01234"])
        self.assertEqual(entry["type"], "misc")
        self.assertIn("Crossref was not reachable", stdout_text)

    def test_a_work_already_in_the_bibliography_is_refused_under_any_key(self) -> None:
        self.bib_path.parent.mkdir(parents=True, exist_ok=True)
        self.bib_path.write_text(_FIXTURE_BIB_TEXT.split("\n\n")[0] + "\n")
        _check._open_url = self._route("10.1234/exact.5678", [])
        output = self._run_command(["add", "--bib", str(self.bib_path), "--arxiv-id", "2401.01234"], 1)
        self.assertIn("example2024deterministic", output)
        self.assertEqual(len(_check._parse_bib(self.bib_path.read_text())), 1)

    def test_a_preprint_of_a_published_entry_is_refused(self) -> None:
        self.bib_path.parent.mkdir(parents=True, exist_ok=True)
        self.bib_path.write_text(_FIXTURE_BIB_TEXT.split("\n\n")[1] + "\n")
        _check._open_url = self._route("10.1234/exact.5678", [])
        output = self._run_command(["add", "--bib", str(self.bib_path), "--arxiv-id", "2401.01234",
                                    "--preprint"], 1)
        self.assertIn("instance2023predicting", output)

    def test_preprint_flag_keeps_the_arxiv_entry(self) -> None:
        _check._open_url = self._route("10.1234/exact.5678", [])
        stdout_text, entry = self._add(["--arxiv-id", "2401.01234", "--preprint"])
        self.assertEqual(entry["type"], "misc")
        self.assertIn("10.1234/exact.5678", stdout_text)
        self.bib_path.unlink()
        _check._open_url = self._route(None, [])
        stdout_text, entry = self._add(["--arxiv-id", "2401.01234", "--preprint"])
        self.assertIn("because of --preprint", stdout_text)


class TestParserStrictness(unittest.TestCase):
    def test_an_abbreviated_flag_is_rejected(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                _check._build_parser().parse_args(["verify", "--refs", "x"])
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
