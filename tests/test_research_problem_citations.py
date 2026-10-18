"""Citation projection uses recorded locators in priority order."""

import copy

from test_research_problem_claims import ProblemCase
from research_harness.problem_evidence import project_citations


class ProblemCitationTests(ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.select_statement()
        self.item = self.record_full_reading()
        self.mutate(self.load_problem_module().record_value, self.build_value([self.item]))

    def test_doi_precedes_arxiv_and_citations_are_distinct_by_work(self):
        records = self.store.snapshot()["records"]
        records["work"][self.item["version_id"]]["aliases"].append("doi:10.1234/bound")
        citations = project_citations(records, self.artifacts)
        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]["locator"], "10.1234/bound")
        self.assertIn(records["work"][self.item["version_id"]]["title"], citations[0]["citation"])

    def test_arxiv_locator_is_used_without_doi(self):
        citations = project_citations(self.store.snapshot()["records"], self.artifacts)
        self.assertEqual(citations[0]["locator"], "2601.00001v1")

    def remap_as_https_work(self, url):
        records = self.store.snapshot()["records"]
        identifier = "url:https://example.org/finite-bound"
        work = records["work"].pop(self.item["version_id"])
        work.update(id=identifier, aliases=[])
        records["work"][identifier] = work
        reading = records["reading"]["read-full"]
        reading["version_id"] = identifier
        bundle = records["source_bundle"][reading["bundle_id"]]
        if url is not None:
            bundle["units"][0]["url"] = url
        for section in ("motivation", "current_state"):
            records["problem_value"]["value-1"][section][0]["evidence"][0]["version_id"] = identifier
        return records

    def test_https_bundle_unit_is_the_final_locator_fallback(self):
        citations = project_citations(self.remap_as_https_work("https://example.org/finite-bound"), self.artifacts)
        self.assertEqual(citations[0]["locator"], "https://example.org/finite-bound")

    def test_missing_and_non_https_locators_leave_an_explicit_obligation(self):
        for url in (None, "http://example.org/insecure"):
            with self.subTest(url=url):
                records = self.remap_as_https_work(url)
                self.assert_error("problem_citation_locator_missing", lambda: project_citations(records, self.artifacts))
