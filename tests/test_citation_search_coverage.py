"""Deterministic citation traversal and conservative publication association."""

import contextlib
import io
import json
import shlex
from urllib.parse import parse_qs, urlsplit

from test_check import (
    _ARXIV_TITLE, _CheckTestCase, _arxiv_feed, _check, _crossref_work_text,
)


def _make_crossref_work(index, *, matching=False):
    return json.loads(_crossref_work_text(
        _ARXIV_TITLE if matching else "Unrelated registry candidate " + str(index),
        doi="10.1234/candidate." + str(index)))


def _make_openalex_work(index, *, matching=False):
    return {"id": "https://openalex.org/W" + str(index + 1),
            "display_name": _ARXIV_TITLE if matching else "Unrelated registry candidate " + str(index),
            "authorships": [{"author": {"display_name": "Alice Example"}},
                            {"author": {"display_name": "Bob Sample"}}],
            "publication_year": 2025}


def _make_page(provider, items, *, total=0, cursor=None):
    if provider == "crossref":
        metadata = {"items": items, "next-cursor": cursor}
        if total is not None:
            metadata["total-results"] = total
        return json.dumps({"message": metadata})
    metadata = {"next_cursor": cursor}
    if total is not None:
        metadata["count"] = total
    return json.dumps({"meta": metadata, "results": items})


class TestCitationSearchCoverage(_CheckTestCase):
    def setUp(self):
        super().setUp()
        self.bib_path = self._write_bib(
            "@article{searched, title={" + _ARXIV_TITLE + "}, "
            "author={Alice Example and Bob Sample}, year={2025}}\n")
        self.requests = []

    def _route_pages(self, crossref, openalex=None):
        pages = {"crossref": crossref, "openalex": openalex or {"*": _make_page("openalex", [])}}

        def route(url, accept="application/json", **kwargs):
            provider = "crossref" if urlsplit(url).netloc == "api.crossref.org" else "openalex"
            query = parse_qs(urlsplit(url).query)
            self.requests.append((provider, query))
            payload = pages[provider][query.get("cursor", ["*"])[0]]
            return payload if isinstance(payload, tuple) else (200, payload)

        _check._open_url = route

    def _lookup_search(self, *options, exit_code=1):
        return self._lookup(["--bib", str(self.bib_path), *options], exit_code)

    def test_default_bound_discloses_omitted_rows_and_runnable_continuation(self):
        self._route_pages({"*": _make_page("crossref", [_make_crossref_work(i) for i in range(5)],
                                          total=6, cursor="second")})
        report = self._lookup_search()
        self.assertEqual(report["entries"][0]["status"], "search_incomplete")
        self.assertFalse(report["ok"])
        self.assertEqual(report["incomplete_keys"], ["searched"])
        coverage = report["entries"][0]["searches"][0]
        self.assertEqual((coverage["provider"], coverage["inspected_count"], coverage["reported_total"],
                          coverage["omitted_count"], coverage["termination_reason"], coverage["complete"]),
                         ("crossref", 5, 6, 1, "page_limit", False))
        command = shlex.split(coverage["continuation"])
        self.assertEqual(command[0], "exactory-check")
        self.assertEqual(command[1], "lookup")
        self.assertEqual(command[command.index("--bib") + 1], str(self.bib_path))
        self.assertEqual(command[command.index("--out") + 1], str(self.out_path))
        self.assertGreater(int(command[command.index("--search-pages") + 1]), 1)
        self.assertEqual(self.requests[0][1]["rows"], ["5"])
        self.assertEqual(self.requests[0][1]["cursor"], ["*"])

    def test_a_valid_sixth_crossref_candidate_is_verified(self):
        self._route_pages({
            "*": _make_page("crossref", [_make_crossref_work(i) for i in range(5)], total=6, cursor="second"),
            "second": _make_page("crossref", [_make_crossref_work(6, matching=True)], total=6),
        })
        report = self._lookup_search("--search-pages", "2", exit_code=None)
        self.assertEqual(report["entries"][0]["status"], "verified")
        self.assertEqual(report["entries"][0]["searches"][0]["inspected_count"], 6)
        self.assertTrue(report["lookup_complete"])
        self.assertTrue(report["all_verified"])

    def test_a_valid_sixth_openalex_candidate_is_verified(self):
        self._route_pages({"*": _make_page("crossref", [])}, {
            "*": _make_page("openalex", [_make_openalex_work(i) for i in range(5)], total=6, cursor="second"),
            "second": _make_page("openalex", [_make_openalex_work(6, matching=True)], total=6),
        })
        report = self._lookup_search("--search-pages", "2", exit_code=None)
        self.assertEqual(report["entries"][0]["status"], "verified")
        coverage = report["entries"][0]["searches"][1]
        self.assertEqual((coverage["inspected_count"], coverage["omitted_count"], coverage["complete"]), (6, 0, True))
        self.assertEqual(self.requests[-1][1]["cursor"], ["second"])
        self.assertEqual(self.requests[-1][1]["per_page"], ["5"])

    def test_positive_match_does_not_require_exhausting_search(self):
        self._route_pages({"*": _make_page("crossref", [_make_crossref_work(i, matching=i == 0)
                                                                  for i in range(5)], total=100, cursor="more")})
        report = self._lookup_search(exit_code=None)
        self.assertTrue(report["ok"])
        self.assertFalse(report["entries"][0]["searches"][0]["complete"])
        self.assertEqual(report["entries"][0]["searches"][0]["omitted_count"], 95)
        self.assertEqual(len(self.requests), 1)

    def test_unknown_totals_remain_explicit_when_traversal_finishes(self):
        self._route_pages({"*": _make_page("crossref", [], total=None)})
        report = self._lookup_search()
        self.assertEqual(report["entries"][0]["status"], "unresolved")
        coverage = report["entries"][0]["searches"][0]
        self.assertIsNone(coverage["reported_total"])
        self.assertIsNone(coverage["omitted_count"])
        self.assertTrue(coverage["complete"])

    def test_unknown_totals_at_a_bound_do_not_become_a_negative_conclusion(self):
        self._route_pages({"*": _make_page("crossref", [_make_crossref_work(i) for i in range(5)],
                                          total=None, cursor="more")})
        report = self._lookup_search()
        self.assertEqual(report["entries"][0]["status"], "search_incomplete")
        self.assertIsNone(report["entries"][0]["searches"][0]["omitted_count"])

    def test_changing_totals_are_unknown_and_incomplete(self):
        self._route_pages({
            "*": _make_page("crossref", [_make_crossref_work(i) for i in range(5)], total=6, cursor="second"),
            "second": _make_page("crossref", [_make_crossref_work(6)], total=7),
        })
        report = self._lookup_search("--search-pages", "2")
        coverage = report["entries"][0]["searches"][0]
        self.assertEqual(report["entries"][0]["status"], "search_incomplete")
        self.assertIsNone(coverage["reported_total"])
        self.assertIsNone(coverage["omitted_count"])
        self.assertEqual(coverage["termination_reason"], "inconsistent_total")

    def test_repeated_cursors_stop_without_claiming_exhaustion(self):
        for provider in ("crossref", "openalex"):
            with self.subTest(provider=provider):
                make_work = _make_crossref_work if provider == "crossref" else _make_openalex_work
                pages = {"*": _make_page(provider, [make_work(i) for i in range(5)], total=20, cursor="repeat"),
                         "repeat": _make_page(provider, [make_work(i) for i in range(5, 10)], total=20, cursor="repeat")}
                self._route_pages(pages if provider == "crossref" else {"*": _make_page("crossref", [])},
                                  pages if provider == "openalex" else None)
                report = self._lookup_search("--search-pages", "3")
                coverage = next(search for search in report["entries"][0]["searches"]
                                if search["provider"] == provider)
                self.assertEqual(report["entries"][0]["status"], "search_incomplete")
                self.assertEqual(coverage["termination_reason"], "repeated_cursor")
                self.assertEqual(coverage["inspected_count"], 10)

    def test_a_failed_provider_prevents_a_negative_conclusion(self):
        self._route_pages({"*": (None, None)})
        report = self._lookup_search()
        self.assertEqual(report["entries"][0]["status"], "search_incomplete")
        self.assertFalse(report["lookup_complete"])

    def test_a_failed_later_page_preserves_the_inspected_count(self):
        self._route_pages({"*": _make_page("crossref", [_make_crossref_work(i) for i in range(5)],
                                          total=8, cursor="second"), "second": (503, None)})
        report = self._lookup_search("--search-pages", "2")
        coverage = report["entries"][0]["searches"][0]
        self.assertEqual(report["entries"][0]["status"], "search_incomplete")
        self.assertEqual((coverage["inspected_count"], coverage["omitted_count"],
                          coverage["termination_reason"]), (5, 3, "network_error"))

    def test_malformed_responses_cannot_produce_a_negative_conclusion(self):
        for payload in ("[]", "{}", '{"message":{"items":{}}}',
                        _make_page("crossref", [{"title": 12}], total=1),
                        _make_page("crossref", [], total=4)):
            with self.subTest(payload=payload):
                self._route_pages({"*": payload})
                report = self._lookup_search()
                self.assertEqual(report["entries"][0]["status"], "search_incomplete")

    def test_an_author_near_miss_is_not_conclusive_in_an_unfinished_search(self):
        candidate = _make_crossref_work(0, matching=True)
        candidate["author"][0]["family"] = "Unrelated"
        self._route_pages({"*": _make_page("crossref", [candidate] + [_make_crossref_work(i) for i in range(1, 5)],
                                          total=6, cursor="more")})
        report = self._lookup_search()
        self.assertEqual(report["entries"][0]["status"], "search_incomplete")

    def test_search_page_allowance_must_be_a_positive_integer(self):
        for command in (["lookup"], ["add", "--arxiv-id", "2401.01234"]):
            for value in ("0", "-1", "1.5"):
                with self.subTest(command=command, value=value):
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                        _check._build_parser().parse_args([*command, "--search-pages", value])
                    self.assertEqual(caught.exception.code, 2)


class TestPublicationSearchCoverage(_CheckTestCase):
    def setUp(self):
        super().setUp()
        self.bib_path = self.scratch_dir / "draft with spaces" / "references.bib"

    def _add_from_pages(self, pages, *options):
        def route(url, accept="application/json", **kwargs):
            if urlsplit(url).netloc == "export.arxiv.org":
                return 200, _arxiv_feed(None)
            query = parse_qs(urlsplit(url).query)
            return 200, pages[query.get("cursor", ["*"])[0]]

        _check._open_url = route
        output = self._run_command(["add", "--bib", str(self.bib_path), "--arxiv-id", "2401.01234", *options], None)
        return _check._parse_bib(self.bib_path.read_text())[0], output

    def test_one_match_in_a_prefix_does_not_establish_publication_uniqueness(self):
        entry, output = self._add_from_pages({"*": _make_page("crossref", [
            _make_crossref_work(i, matching=i == 0) for i in range(5)], total=6, cursor="second")})
        self.assertEqual(entry["type"], "misc")
        self.assertIn("--search-pages 2", output)
        self.assertIn(shlex.quote(str(self.bib_path)), output)
        self.assertIn('"omitted_count": 1', output)

    def test_complete_search_can_establish_publication_uniqueness(self):
        entry, output = self._add_from_pages({
            "*": _make_page("crossref", [_make_crossref_work(i, matching=i == 0) for i in range(5)], total=6, cursor="second"),
            "second": _make_page("crossref", [_make_crossref_work(6)], total=6),
        }, "--search-pages", "2")
        self.assertEqual(entry["doi"], "10.1234/candidate.0")
        self.assertIn('"inspected_count": 6', output)

    def test_a_later_second_match_keeps_the_preprint(self):
        entry, _ = self._add_from_pages({
            "*": _make_page("crossref", [_make_crossref_work(i, matching=i == 0) for i in range(5)], total=6, cursor="second"),
            "second": _make_page("crossref", [_make_crossref_work(6, matching=True)], total=6),
        }, "--search-pages", "2")
        self.assertEqual(entry["type"], "misc")

    def test_publication_association_requires_all_authors_and_equal_lengths(self):
        preprint = {"authors": ["Alice Example", "Bob Sample", "Carol Third"]}
        candidate = {"type": "journal-article", "doi": "10.1234/published", "authors": list(preprint["authors"])}
        self.assertTrue(_check._is_published_version_of(candidate, preprint))
        for authors in (["Alice Example", "Bob Sample", "Mallory Wrong"],
                        ["Alice Example", "Bob Sample"], [],
                        ["Alice Example", "Bob Sample", "Carol Third", "Dana Fourth"],
                        ["Alice Example", "Bob Sample", ""]):
            with self.subTest(authors=authors):
                self.assertFalse(_check._is_published_version_of(dict(candidate, authors=authors), preprint))

