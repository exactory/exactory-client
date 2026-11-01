"""Citation projection uses recorded locators in priority order."""

import copy
import json
import unittest

from test_research_problem_claims import ProblemCase
from research_harness.problem_claims import compute_problem_evidence_digest
from research_harness.problem_evidence import project_citations


def build_citation_records():
    """Six full readings: two registry-linked pairs and two distinct related works."""
    works = [
        {"id": "arxiv:2601.18757v1", "aliases": [], "title": "First recorded paper", "authors": ["A. Author"]},
        {"id": "openalex:W7125830766", "aliases": ["doi:10.48550/arxiv.2601.18757"],
         "title": "First recorded paper", "authors": [{"name": "A. Author"}]},
        {"id": "arxiv:2609.14520v1", "aliases": [], "title": "Second recorded paper", "authors": ["B. Author"]},
        {"id": "openalex:W7213271184", "aliases": ["doi:10.48550/arxiv.2609.14520"],
         "title": "Second recorded paper", "authors": [{"name": "B. Author"}]},
        {"id": "arxiv:1012.4131v2", "aliases": [], "title": "A shared topology title",
         "authors": ["Chuichiro Hayashi", "Miwa Hayashi"]},
        {"id": "openalex:W1234567890", "aliases": ["doi:10.1016/j.topol.2012.01.008"],
         "title": "A shared topology title", "authors": ["Chuichiro Hayashi", "Miwa Hayashi", "Tahl Nowik"]}]
    readings = {"read-%02d" % index: {"id": "read-%02d" % index, "version_id": work["id"], "depth": "fulltext"}
                for index, work in enumerate(works, 1)}
    return {"work": {work["id"]: work for work in works}, "reading": readings,
            "problem_value": {"value": {"motivation": [{"evidence": [
                {"kind": "reading", "reading_id": reading["id"]} for reading in readings.values()]}], "current_state": []}}}


class RegistryCitationTests(unittest.TestCase):
    def test_registry_legacy_citation_bytes_and_binding_digest_are_unchanged(self):
        records = build_citation_records()
        self.assertEqual(json.dumps(project_citations(records, None), sort_keys=True, separators=(",", ":")).encode(),
            b'[{"citation":"A. Author. First recorded paper","locator":"2601.18757v1"},'
            b'{"citation":"B. Author. Second recorded paper","locator":"2609.14520v1"},'
            b'{"citation":"Chuichiro Hayashi, Miwa Hayashi. A shared topology title","locator":"1012.4131v2"},'
            b'{"citation":"Chuichiro Hayashi, Miwa Hayashi, Tahl Nowik. A shared topology title",'
            b'"locator":"10.1016/j.topol.2012.01.008"}]')
        self.assertEqual(compute_problem_evidence_digest(records, None),
                         "38d9e9d48de0b971a3f9e4e0940b7669403dcd43f012288ca7c3f595844fe075")

    def test_registry_pairs_share_a_citation_but_related_works_stay_separate(self):
        records = build_citation_records()
        before = copy.deepcopy(records)
        self.assertEqual(project_citations(records, None), [
            {"citation": "A. Author. First recorded paper", "locator": "2601.18757v1"},
            {"citation": "B. Author. Second recorded paper", "locator": "2609.14520v1"},
            {"citation": "Chuichiro Hayashi, Miwa Hayashi. A shared topology title", "locator": "1012.4131v2"},
            {"citation": "Chuichiro Hayashi, Miwa Hayashi, Tahl Nowik. A shared topology title",
             "locator": "10.1016/j.topol.2012.01.008"}])
        self.assertEqual(records, before)

    def test_a_plain_arxiv_alias_does_not_merge_a_journal_work_with_its_preprint(self):
        records = build_citation_records()
        journal = records["work"].pop("openalex:W7125830766")
        journal.update(id="doi:10.1000/journal", aliases=["arxiv:2601.18757"])
        records["work"][journal["id"]] = journal
        records["reading"]["read-02"]["version_id"] = journal["id"]
        evidence = records["problem_value"]["value"]["motivation"][0]["evidence"]
        records["problem_value"]["value"]["motivation"][0]["evidence"] = evidence[:2]
        self.assertEqual(project_citations(records, None), [
            {"citation": "A. Author. First recorded paper", "locator": "2601.18757v1"},
            {"citation": "A. Author. First recorded paper", "locator": "10.1000/journal"}])

    def test_a_journal_work_with_a_datacite_doi_alias_links_to_its_preprint(self):
        for journal_first in (False, True):
            with self.subTest(journal_first=journal_first):
                records = build_citation_records()
                journal = records["work"].pop("openalex:W7125830766")
                journal.update(id="doi:10.1000/journal", title="The journal title", year=2026)
                records["work"][journal["id"]] = journal
                first, second = records["reading"]["read-01"], records["reading"]["read-02"]
                second["version_id"] = journal["id"]
                if journal_first:
                    first["version_id"], second["version_id"] = second["version_id"], first["version_id"]
                evidence = records["problem_value"]["value"]["motivation"][0]["evidence"]
                records["problem_value"]["value"]["motivation"][0]["evidence"] = evidence[:2]
                expected = {"citation": "A. Author. The journal title. 2026", "locator": "10.1000/journal"} if (
                    journal_first) else {"citation": "A. Author. First recorded paper", "locator": "2601.18757v1"}
                self.assertEqual(project_citations(records, None), [expected])

    def test_conflicting_datacite_and_plain_arxiv_aliases_on_either_work_stay_separate(self):
        for identifier in ("arxiv:2601.18757v1", "openalex:W7125830766"):
            with self.subTest(identifier=identifier):
                records = build_citation_records()
                records["work"][identifier]["aliases"] = [
                    "doi:10.48550/arxiv.2601.18757", "arxiv:2609.14520"]
                records["work"]["openalex:W7125830766"]["title"] = "The provider title"
                evidence = records["problem_value"]["value"]["motivation"][0]["evidence"]
                records["problem_value"]["value"]["motivation"][0]["evidence"] = evidence[:2]
                self.assertEqual(project_citations(records, None), [
                    {"citation": "A. Author. First recorded paper", "locator": (
                        "10.48550/arxiv.2601.18757" if identifier.startswith("arxiv:") else "2601.18757v1")},
                    {"citation": "A. Author. The provider title", "locator": "10.48550/arxiv.2601.18757"}])

    def test_author_names_compare_after_whitespace_and_case_normalization(self):
        records = build_citation_records()
        records["work"]["openalex:W7125830766"]["authors"] = [{"name": "  a.\tAUTHOR  "}]
        self.assertEqual(len(project_citations(records, None)), 4)

    def test_different_author_lists_stay_separate(self):
        for authors in (["Other Author"], ["A. Author", "Other Author"]):
            with self.subTest(authors=authors):
                records = build_citation_records()
                records["work"]["openalex:W7125830766"]["authors"] = authors
                self.assertEqual(len(project_citations(records, None)), 5)

    def test_author_order_matters(self):
        records = build_citation_records()
        records["work"]["arxiv:2601.18757v1"]["authors"] = ["First Author", "Second Author"]
        records["work"]["openalex:W7125830766"]["authors"] = ["Second Author", "First Author"]
        self.assertEqual(len(project_citations(records, None)), 5)

    def test_ambiguous_arxiv_aliases_on_either_work_stay_separate(self):
        for identifier in ("arxiv:2601.18757v1", "openalex:W7125830766"):
            with self.subTest(identifier=identifier):
                records = build_citation_records()
                records["work"][identifier]["aliases"].extend([
                    "doi:10.48550/arxiv.2601.18757", "doi:10.48550/arxiv.2609.14520"])
                self.assertEqual(len(project_citations(records, None)), 5)

    def test_missing_authors_on_either_work_stay_separate(self):
        for identifier in ("arxiv:2601.18757v1", "openalex:W7125830766"):
            for authors in (None, [], [" "], [{"name": ""}]):
                with self.subTest(identifier=identifier, authors=authors):
                    records = build_citation_records()
                    if authors is None:
                        records["work"][identifier].pop("authors")
                    else:
                        records["work"][identifier]["authors"] = authors
                    self.assertEqual(len(project_citations(records, None)), 5)

    def test_two_works_without_authors_stay_separate(self):
        for authors in ([], [" "], [{"name": ""}]):
            with self.subTest(authors=authors):
                records = build_citation_records()
                for identifier in ("arxiv:2601.18757v1", "openalex:W7125830766"):
                    records["work"][identifier]["authors"] = authors
                self.assertEqual(len(project_citations(records, None)), 5)

    def test_the_first_reading_supplies_both_text_and_locator(self):
        records = build_citation_records()
        first, second = records["reading"]["read-01"], records["reading"]["read-02"]
        first["version_id"], second["version_id"] = second["version_id"], first["version_id"]
        records["work"][first["version_id"]].update(title="The first provider title", year=2026)
        self.assertEqual(project_citations(records, None)[0], {
            "citation": "A. Author. The first provider title. 2026", "locator": "10.48550/arxiv.2601.18757"})

    def test_arxiv_versions_keep_the_existing_family_skip_despite_author_differences(self):
        records = build_citation_records()
        version = "arxiv:2601.18757v2"
        records["work"][version] = {"id": version, "aliases": [], "title": "Later version", "authors": ["Other Author"]}
        records["reading"]["read-02"]["version_id"] = version
        self.assertEqual(len(project_citations(records, None)), 4)

    def test_only_cited_readings_establish_registry_matches(self):
        records = build_citation_records()
        version = "arxiv:2601.18757v2"
        records["work"][version] = {"id": version, "aliases": [], "title": "Later version", "authors": ["Other Author"]}
        records["reading"]["read-02"]["version_id"] = version
        records["work"]["openalex:W7213271184"].update(
            aliases=["doi:10.48550/arxiv.2601.18757"], authors=["Other Author"])
        self.assertEqual(len(project_citations(records, None)), 5)

    def test_a_record_without_registry_pairs_keeps_its_citations_and_evidence_digest(self):
        records = build_citation_records()
        records["work"]["openalex:W7125830766"]["aliases"] = ["doi:10.1234/first"]
        records["work"]["openalex:W7213271184"]["aliases"] = ["doi:10.1234/second"]
        self.assertEqual(project_citations(records, None), [
            {"citation": "A. Author. First recorded paper", "locator": "2601.18757v1"},
            {"citation": "A. Author. First recorded paper", "locator": "10.1234/first"},
            {"citation": "B. Author. Second recorded paper", "locator": "2609.14520v1"},
            {"citation": "B. Author. Second recorded paper", "locator": "10.1234/second"},
            {"citation": "Chuichiro Hayashi, Miwa Hayashi. A shared topology title", "locator": "1012.4131v2"},
            {"citation": "Chuichiro Hayashi, Miwa Hayashi, Tahl Nowik. A shared topology title",
             "locator": "10.1016/j.topol.2012.01.008"}])
        # The evidence binding before the registry rule was introduced.
        self.assertEqual(compute_problem_evidence_digest(records, None),
                         "859688c0f1ec23576a382a5d3c4da804542bed728bebdc8572d12450618ddde5")


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
