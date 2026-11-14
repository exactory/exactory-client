"""Selected search captures retain disposition metadata and page provenance.

The fixture is synthetic, from incident
20261112150500-result-review-frame-holds-cited-search-records, task T3.
Live capture failed because the sandbox could not resolve api.openalex.org.
A2 (select preserves meta) still needs a live capture. The modeled shapes are
documented at https://help.openalex.org/api/selecting-fields/ and
https://help.openalex.org/api/paging/; no paper text is copied.
"""

import copy
import json
from pathlib import Path
from urllib.parse import urlencode

from research_harness.acquisition import import_response
from research_harness.search_pages import compute_query_identity
from test_search_receipts import SearchCaptureCase


SELECT_FIXTURE = Path(__file__).parent / "fixtures/research/openalex-select-synthetic.json"
SELECT_FIELDS = ("id", "doi", "title", "display_name", "type", "authorships",
                 "publication_date", "publication_year", "abstract_inverted_index")


class OpenAlexSelectTests(SearchCaptureCase):
    def setUp(self):
        super().setUp()
        self.response_bytes = SELECT_FIXTURE.read_bytes()
        self.response = json.loads(self.response_bytes)

    def test_selected_response_imports_exact_bytes_and_disposition_metadata(self):
        parameters = {"search": "bounded", "page": 1, "per_page": 2, "select": ",".join(SELECT_FIELDS)}
        source_url = "https://api.openalex.org/works?" + urlencode(parameters)
        for item in self.response["results"]:
            self.assertEqual(set(item), set(SELECT_FIELDS))
        capture = import_response(self.store, "openalex", self.response_bytes, source_url=source_url,
            captured_at="2026-09-07T12:00:00Z", expected_revision=0, request_id="selected-response")
        self.assertEqual(capture["status"], "complete")
        self.assertEqual(capture["work_ids"], ["openalex:W101", "openalex:W102"])
        self.assertEqual(capture["pending"], [])
        records = self.store.snapshot()["records"]
        source = records["source"][capture["source_ids"][0]]
        self.assertEqual(source["url"], source_url)
        self.assertEqual(self.artifacts.read(source["response"]), self.response_bytes)
        for item, abstract in zip(self.response["results"],
                                  (b"An authored bounded example.", b"Another authored bounded comparison.")):
            work = records["work"]["openalex:" + item["id"].rsplit("/", 1)[1]]
            self.assertEqual(work["title"], item["title"] or item["display_name"])
            self.assertEqual(work["type"], item["type"])
            self.assertEqual(work["authors"], [item["authorships"][0]["author"]["display_name"]])
            self.assertEqual(work["publication_date"], item["publication_date"])
            self.assertEqual(work["date_assertions"][0]["values"]["publication_year"], item["publication_year"])
            self.assertEqual(self.artifacts.read(work["abstract"]), abstract)
            self.assertEqual(work["reference_metadata"]["status"], "missing")
            self.assertFalse(work["reference_metadata"]["bibliography_complete"])
            self.assertEqual(work["fulltext_urls"], [])
        self.assertIn("doi:10.1234/bounded-example", records["work"]["openalex:W101"]["aliases"])
        receipt, search = self.record_search_payload(self.build_search_payload([capture]))
        self.assertEqual(receipt["pending"], [])
        self.assertEqual(search["coverage"][0]["kind"], "complete")
        self.assertEqual(dict(search["page_groups"][0]["parameters"])["select"], parameters["select"])

    def test_pages_with_different_select_values_report_separate_incomplete_groups(self):
        selected = ",".join(SELECT_FIELDS)
        reduced = ",".join(field for field in SELECT_FIELDS if field != "doi")
        for second_select in (selected, reduced):
            with self.subTest(second_select=second_select):
                captures = []
                for page, fields in ((1, selected), (2, second_select)):
                    response = copy.deepcopy(self.response)
                    response["meta"].update(page=page, per_page=1)
                    response["results"] = [response["results"][page - 1]]
                    if fields == reduced:
                        del response["results"][0]["doi"]
                    captures.append(self.capture_page(response, page=page, per_page=1, select=fields))
                receipt, search = self.record_search_payload(self.build_search_payload(captures))
                records = self.store.snapshot()["records"]
                sources = [records["source"][capture["source_ids"][0]] for capture in captures]
                self.assertEqual(compute_query_identity(sources[0]), compute_query_identity(sources[1]))
                groups = search["page_groups"]
                self.assertEqual({dict(group["parameters"])["select"] for group in groups},
                                 {selected, second_select})
                if second_select == selected:
                    self.assertEqual(len(groups), 1)
                    self.assertTrue(groups[0]["complete"])
                    self.assertEqual(receipt["pending"], [])
                    self.assertEqual(search["coverage"][0]["kind"], "complete")
                else:
                    self.assertEqual(len(groups), 2)
                    self.assertTrue(all(not group["complete"] for group in groups))
                    self.assertEqual(receipt["incomplete_response_count"], 2)
                    self.assertEqual({row["query"] for row in receipt["incomplete_responses"]}, {"bounded"})
                    self.assertEqual({row["code"] for row in receipt["pending"]}, {"search_response_incomplete"})
                    self.assertEqual(search["coverage"][0]["kind"], "bounded_unordered")

    def test_selected_work_without_either_title_is_reported_as_invalid_work(self):
        for missing_title in ("null", "empty", "omitted"):
            with self.subTest(missing_title=missing_title):
                response = copy.deepcopy(self.response)
                response["results"] = response["results"][:1]
                response["meta"].update(count=1, per_page=1)
                item = response["results"][0]
                for field in ("title", "display_name"):
                    if missing_title == "omitted":
                        del item[field]
                    else:
                        item[field] = None if missing_title == "null" else ""
                capture = self.capture_page(response, per_page=1, select=",".join(SELECT_FIELDS))
                self.assertEqual(capture["work_ids"], [])
                self.assertEqual(capture["pending"], [{"index": 0, "code": "invalid_work", "record_type": "article"}])
                self.assertEqual(capture["excluded_item_count"], 0)
                receipt, search = self.record_search_payload(self.build_search_payload([capture]))
                self.assertEqual(receipt["invalid_work_count"], 1)
                invalid = receipt["invalid_works"][0]
                self.assertEqual((invalid["code"], invalid["query"], invalid["record_type"]),
                                 ("invalid_work", "bounded", "article"))
                self.assertEqual(invalid["source_id"], capture["source_ids"][0])
                self.assertEqual([row["code"] for row in search["integrity_errors"]], ["invalid_work"])
