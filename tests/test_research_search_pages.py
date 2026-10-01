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

    def test_different_sort_orders_cannot_supply_each_others_missing_page(self):
        first = self.crossref([1, 2], sort="published")
        self.assertTrue(self.search([first, self.crossref([3, 4], cursor="second", next_cursor="third", sort="relevance")])["pending"])
        self.assertFalse(self.search([first, self.crossref([3, 4], cursor="second", next_cursor="third", sort="published")])["pending"])

    def test_only_the_parameters_that_decide_the_matched_works_enter_the_query_identity(self):
        from research_harness.search_pages import compute_query_identity
        endpoints = {"arxiv": "https://export.arxiv.org/api/query", "crossref": "https://api.crossref.org/works",
                     "openalex": "https://api.openalex.org/works"}

        def compute_identity(provider, query):
            return compute_query_identity({"provider": provider, "url": endpoints[provider] + "?" + query})

        # Per provider: a capture, captures that ask the same query, and captures that each ask another query.
        cases = {"arxiv": ("search_query=all:erasure",
                           ["sortBy=submittedDate&sortOrder=ascending", "start=10&max_results=5", "nocache=1"],
                           ["search_query=all:landauer", "search_query=all:erasure&id_list=2601.00001"]),
                 "crossref": ("query=dram+erasure",
                              ["sort=published&order=asc", "select=DOI,title", "sample=5", "facet=type-name:*",
                               "rows=5&offset=10", "cursor=*", "mailto=a@example.org", "nocache=1"],
                              ["query=landauer", "query=dram+erasure&query.author=Smith", "query=dram+erasure&query.title=Erasure",
                               "query=dram+erasure&filter=type:journal-article"]),
                 "openalex": ("search=dram+erasure",
                              ["sort=cited_by_count:desc", "select=id,title", "sample=5&seed=7", "group_by=type", "per-page=5&page=2",
                               "per_page=5&cursor=*", "mailto=a@example.org&api_key=KEY", "nocache=1"],
                              ["search=landauer", "search.exact=dram+erasure", "search.semantic=dram+erasure",
                               "search=dram+erasure&filter=cites:W123", "search=dram+erasure&corpus=all"])}
        for provider, (base, extras, others) in cases.items():
            for extra in extras:
                self.assertEqual(compute_identity(provider, base + "&" + extra), compute_identity(provider, base),
                                 (provider, extra))
            identities = [compute_identity(provider, query) for query in [base] + others]
            self.assertEqual(len({json.dumps(i, sort_keys=True) for i in identities}), len(identities), provider)
        self.assertIsNone(compute_query_identity({"provider": "web", "url": endpoints["openalex"] + "?search=dram+erasure"}))

    def test_spellings_of_one_request_share_its_query_identity(self):
        from research_harness.search_pages import compute_query_identity

        def compute_identity(provider, url):
            return compute_query_identity({"provider": provider, "url": url})

        # Per provider: a capture's URL, and other spellings of its request that ask the same query.
        cases = {"arxiv": ("https://export.arxiv.org/api/query?search_query=all:erasure&id_list=2601.00001,2601.00002",
                           ["https://export.arxiv.org/api/query/?search_query=all:erasure&id_list=2601.00001,2601.00002",
                            "https://export.arxiv.org:443/api/query?search_query=all:erasure&id_list=2601.00001,2601.00002",
                            "https://export.arxiv.org/api/query?search_query=all:erasure&id_list=2601.00002,2601.00001",
                            "https://export.arxiv.org/api/query?search_query=all:erasure&id_list=2601.00002,2601.00001,2601.00002"]),
                 "crossref": ("https://api.crossref.org/works?query=dram+erasure&filter=type:journal-article,from-pub-date:2020",
                              ["https://api.crossref.org/works/?query=dram+erasure&filter=type:journal-article,from-pub-date:2020",
                               "https://API.Crossref.org/works?query=dram+erasure&filter=type:journal-article,from-pub-date:2020",
                               "https://api.crossref.org/works?query=dram+erasure&filter=from-pub-date:2020,type:journal-article",
                               "https://api.crossref.org/works?query=dram+erasure&query.author=&filter=type:journal-article,from-pub-date:2020"]),
                 "openalex": ("https://api.openalex.org/works?search=dram+erasure&filter=type:article,is_oa:true",
                              ["https://api.openalex.org/works/?search=dram+erasure&filter=type:article,is_oa:true",
                               "https://api.openalex.org:443/works?search=dram+erasure&filter=type:article,is_oa:true",
                               "https://api.openalex.org/works?search=dram+erasure&filter=is_oa:true,type:article",
                               "https://api.openalex.org/works?search=dram+erasure&filter=is_oa:true,type:article,"
                               "&search.exact="])}
        for provider, (base, spellings) in cases.items():
            for spelling in spellings:
                self.assertEqual(compute_identity(provider, spelling), compute_identity(provider, base),
                                 (provider, spelling))
        # An empty list parameter is no parameter, and a list parameter with other items asks another query.
        self.assertEqual(compute_identity("openalex", "https://api.openalex.org/works?search=dram+erasure&filter="),
                         compute_identity("openalex", "https://api.openalex.org/works?search=dram+erasure"))
        self.assertNotEqual(
            compute_identity("openalex", "https://api.openalex.org/works?search=dram+erasure&filter=type:article"),
            compute_identity("openalex",
                             "https://api.openalex.org/works?search=dram+erasure&filter=type:article,is_oa:true"))
        self.assertNotEqual(
            compute_identity("crossref", "https://api.crossref.org/works?query=dram+erasure&filter=type:journal-article"),
            compute_identity("crossref",
                             "https://api.crossref.org/works?query=dram+erasure&filter=type:journal-article,type:book"))
        self.assertNotEqual(compute_identity("arxiv", "https://export.arxiv.org/api/query?id_list=2601.00001"),
                            compute_identity("arxiv", "https://export.arxiv.org/api/query?id_list=2601.00001,2601.00002"))

    def test_an_openalex_filter_value_is_compared_without_letter_case_and_its_alternatives_as_a_set(self):
        from research_harness.search_pages import compute_query_identity
        endpoints = {"crossref": "https://api.crossref.org/works", "openalex": "https://api.openalex.org/works"}

        def compute_identity(provider, query):
            return compute_query_identity({"provider": provider, "url": endpoints[provider] + "?" + query})

        # OpenAlex reads filter values without regard to letter case, and the | alternatives of one filter, after a
        # leading ! that negates them all, as a set (https://help.openalex.org/api/filtering/).
        for query, spelling in (("filter=type:article", "filter=type:Article"),
                                ("filter=cites:W1|W2", "filter=cites:W2|W1"),
                                ("filter=cites:W1|W2", "filter=cites:w2|W1|W2"),
                                ("filter=type:!article|book", "filter=type:!book|article"),
                                ("search=erasure&filter=cites:W1|W2,type:article",
                                 "search=erasure&filter=type:ARTICLE,cites:W2|w1")):
            self.assertEqual(compute_identity("openalex", spelling), compute_identity("openalex", query), (query, spelling))
        # The filter name keeps its letter case, a leading ! negates the filter, other alternatives ask another query,
        # and the values of the other matching parameters keep their letter case.
        for provider, query, other_query in (("openalex", "filter=type:article", "filter=Type:article"),
                                             ("openalex", "filter=type:article", "filter=type:!article"),
                                             ("openalex", "filter=cites:W1|W2", "filter=cites:W1"),
                                             ("openalex", "search=dram+erasure", "search=DRAM+erasure"),
                                             ("crossref", "filter=type:journal-article", "filter=type:Journal-Article")):
            self.assertNotEqual(compute_identity(provider, other_query), compute_identity(provider, query),
                                (provider, query, other_query))

    def test_openalex_empty_and_complete_cursor_chain_are_admitted(self):
        empty = self.page("openalex", {"meta": {"count": 0, "next_cursor": None}, "results": []},
            {"search": "bounded", "cursor": "*", "per_page": "2"})
        self.assertFalse(self.search([empty])["pending"])
        pages = [self.page("openalex", {"meta": {"count": 3, "next_cursor": following},
                    "results": [{"id": "https://openalex.org/W" + str(i), "title": "Authored work " + str(i)} for i in numbers]},
                    {"search": "bounded", "cursor": cursor, "per_page": "2"})
                 for numbers, cursor, following in (([1, 2], "*", "second"), ([3], "second", None))]
        self.assertFalse(self.search(pages)["pending"])

    def test_openalex_filter_is_the_query_of_a_native_citing_capture(self):
        data = {"meta": {"count": 1, "next_cursor": None},
                "results": [{"id": "https://openalex.org/W7", "title": "A work that cites W123"}]}
        self.sequence += 1
        capture = import_response(self.store, "openalex", json.dumps(data).encode(),
            source_url="https://api.openalex.org/works?filter=cites:W123&sort=cited_by_count:desc",
            captured_at="2026-09-07T12:00:00Z", expected_revision=self.store.revision, request_id="native-" + str(self.sequence))

        def build_citing_judgment(query):
            return {"id": "citing-" + query, "profile": "research", "purpose": "recent", "queries": [query],
                    "responses": [{"source_id": capture["source_ids"][0], "query": query}],
                    "captured_at": "2026-09-07T12:00:00Z", "scope": "Every work that cites W123.",
                    "found_work_ids": capture["work_ids"], "verdict": "nothing-new", "cited_work_ids": [],
                    "dispositions": [{"work_id": w, "disposition": "out_of_scope", "reason": "Authored result outside the objective."}
                                     for w in capture["work_ids"]],
                    "impact": "This judgment covers only the saved citing works.", "gaps": []}

        self.assert_error("invalid_search", lambda: self.mutate(record_search, build_citing_judgment("cited_by_count:desc")))
        self.assertFalse(self.mutate(record_search, build_citing_judgment("cites:W123"))["result"]["pending"])

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
