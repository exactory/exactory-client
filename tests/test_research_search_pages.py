"""Captured query pagination uses authored provider responses, without network."""

import json
from urllib.parse import urlencode

from literature_fixtures import LiteratureCase
from research_fixtures import atom, entry
from research_harness.acquisition import import_response
from research_harness.literature import record_search


class SearchPageTests(LiteratureCase):
    def setUp(self):
        super().setUp()
        self.scope([self.metadata()])

    def page(self, provider, data, parameters):
        endpoint = {"arxiv": "https://export.arxiv.org/api/query", "crossref": "https://api.crossref.org/works",
                    "openalex": "https://api.openalex.org/works"}[provider]
        self.sequence += 1
        return import_response(self.store, provider, data if isinstance(data, bytes) else json.dumps(data).encode(),
            source_url=endpoint + "?" + urlencode(parameters), captured_at="2026-09-07T12:00:00Z",
            expected_revision=self.store.revision, request_id="native-" + str(self.sequence))

    def search(self, pages):
        return self.mutate(record_search, {"id": "search-" + str(self.sequence), "profile": "research", "purpose": "direct",
            "queries": ["bounded"], "responses": [{"source_id": p["source_ids"][0], "query": "bounded"} for p in pages],
            "captured_at": "2026-09-07T12:00:00Z", "scope": "All results of the specified authored query and filters.",
            "found_work_ids": sorted({w for p in pages for w in p["work_ids"]}), "verdict": "nothing-new", "cited_work_ids": [],
            "dispositions": [{"work_id": w, "disposition": "out_of_scope", "reason": "Authored result outside the objective."}
                             for w in sorted({w for p in pages for w in p["work_ids"]})],
            "impact": "This judgment covers only the saved query scope.", "gaps": []})["result"]

    def crossref(self, numbers, total=4, cursor="*", next_cursor="second", **extra):
        data = {"message": {"total-results": total, "next-cursor": next_cursor,
                "items": [{"DOI": "10.1234/authored." + str(i), "title": ["Authored result " + str(i)]} for i in numbers]}}
        return self.page("crossref", data, dict(query="bounded", rows="2", cursor=cursor, **extra))

    def test_empty_arxiv_query_is_complete(self):
        page = self.page("arxiv", atom([], total=0, size=2), {"search_query": "bounded", "start": "0", "max_results": "2"})
        self.assertFalse(self.search([page])["pending"])

    def test_first_crossref_page_does_not_complete_four_result_query(self):
        self.assertTrue(self.search([self.crossref([1, 2])])["pending"])

    def test_terminal_crossref_page_without_cursor_prefix_stays_pending(self):
        self.assertTrue(self.search([self.crossref([3], total=3, cursor="second", next_cursor="ignored")])["pending"])

    def test_complete_crossref_chain_can_include_a_full_final_page(self):
        pages = [self.crossref([1, 2]), self.crossref([3, 4], cursor="second", next_cursor="third")]
        self.assertFalse(self.search(pages)["pending"])

    def test_complete_arxiv_offset_chain_is_aggregated(self):
        pages = [self.page("arxiv", atom([entry("2601.%05dv1" % i) for i in numbers], total=3, start=start, size=2),
            {"search_query": "bounded", "start": str(start), "max_results": "2", "sortBy": "submittedDate"})
            for numbers, start in (([2, 3], 0), ([4], 2))]
        self.assertFalse(self.search(pages)["pending"])

    def test_different_filter_chains_cannot_supply_each_others_missing_page(self):
        pages = [self.crossref([1, 2], filter="type:journal-article"),
                 self.crossref([3], total=3, cursor="second", filter="type:book")]
        self.assertTrue(self.search(pages)["pending"])

    def test_openalex_empty_and_complete_cursor_chain_are_admitted(self):
        empty = self.page("openalex", {"meta": {"count": 0, "next_cursor": None}, "results": []},
            {"search": "bounded", "cursor": "*", "per_page": "2"})
        self.assertFalse(self.search([empty])["pending"])
        pages = [self.page("openalex", {"meta": {"count": 3, "next_cursor": following},
                    "results": [{"id": "https://openalex.org/W" + str(i), "title": "Authored work " + str(i)} for i in numbers]},
                    {"search": "bounded", "cursor": cursor, "per_page": "2"})
                 for numbers, cursor, following in (([1, 2], "*", "second"), ([3], "second", None))]
        self.assertFalse(self.search(pages)["pending"])

    def test_search_report_preserves_native_missing_pages(self):
        self.search([self.crossref([1, 2])])
        self.assertIn("search_pending", self.codes())

    def test_duplicate_results_changed_counts_and_missing_middle_page_stay_pending(self):
        cases = [([1, 2], [2, 3], 4, "second"), ([1, 2], [3], 3, "second"), ([1, 2], [3, 4], 4, "third")]
        for first, second, total, cursor in cases:
            pages = [self.crossref(first), self.crossref(second, total=total, cursor=cursor)]
            self.assertTrue(self.search(pages)["pending"])

    def test_offset_page_sizes_and_duplicate_capture_receipts_preserve_complete_query(self):
        pages = [self.page("crossref", {"message": {"total-results": 3, "items": [
            {"DOI": "10.1234/offset." + str(i), "title": ["Authored offset result"]} for i in numbers]}},
            {"query": "bounded", "offset": str(offset), "rows": str(size)})
            for numbers, offset, size in (([1, 2], 0, 2), ([3], 2, 1))]
        self.assertFalse(self.search(pages + [pages[0]])["pending"])

    def test_invalid_requested_pagination_has_a_typed_error(self):
        for params in ({"query": "bounded", "rows": "many"}, {"query": "bounded", "cursor": "*", "offset": "2"}):
            page = self.page("crossref", {"message": {"total-results": 0, "items": []}}, params)
            self.assert_error("invalid_search", lambda: self.search([page]))

    def test_repeated_query_filters_cannot_define_an_ambiguous_request_group(self):
        page = self.page("crossref", {"message": {"total-results": 0, "items": []}},
            [("query", "bounded"), ("filter", "type:book"), ("filter", "type:journal-article")])
        self.assert_error("invalid_search", lambda: self.search([page]))
