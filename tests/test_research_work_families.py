"""D3 R15: explicit links between a found record and an exact source version."""

import copy
import json

from literature_fixtures import LiteratureCase
from research_harness import identities
from research_harness.acquisition import acquire_work, import_response
from research_harness.evidence import digest
from research_fixtures import atom, client, entry, json_response, xml_response


class WorkFamilyTests(LiteratureCase):
    def import_work(self, identifier, aliases=(), authors=("A. Author", "B. Author")):
        payload = {"id": identifier, "title": "An authored work " + identifier,
                   "aliases": list(aliases), "authors": list(authors)}
        self.sequence += 1
        import_response(
            self.store, "mcp", json.dumps(payload).encode(),
            source_url="https://example.org/metadata", captured_at="2026-09-07T12:00:00Z",
            media_type="application/json", mappings=[{key: "/" + key for key in payload}],
            expected_revision=self.store.revision, request_id="family-" + str(self.sequence))
        return identifier

    def acquire_shared_doi_pair(self):
        found, version, doi = "openalex:W123", "arxiv:2601.00001v1", "doi:10.1234/shared"
        registry = {"id": "https://openalex.org/W123", "doi": "https://doi.org/10.1234/shared",
                    "title": "An authored registry title", "publication_date": "2026-01-03",
                    "abstract_inverted_index": {"An": [0], "authored": [1], "abstract.": [2]},
                    "authorships": [{"author": {"display_name": "A. Researcher"}}]}
        http, _, _ = client([json_response(registry), xml_response(atom([entry(doi=doi[4:])], total=1))])
        for identifier in (found, version):
            self.sequence += 1
            result = acquire_work(self.store, identifier, http=http, expected_revision=self.store.revision,
                                  request_id="family-acquisition-" + str(self.sequence))
            self.assertEqual(result["status"], "complete")
        return found, version, doi

    def find_link(self, records, found, version):
        function = getattr(identities, "find_work_family_link", None)
        self.assertTrue(callable(function), "The R15 work-family relation is missing")
        return function(records, found, version)

    def test_versionless_citation_hit_links_to_its_acquired_version(self):
        # Shape from incident 20261112135015-openness-reading-turn-stopped-by-provider-safeguards, T9.
        response = {"data": [{"citingPaper": {"externalIds": {"ArXiv": "2601.00001"},
                     "title": "An authored citation-list hit", "authors": [{"name": "A. Author"}]}}]}
        import_response(
            self.store, "mcp", json.dumps(response).encode(),
            source_url="https://example.org/citations", captured_at="2026-09-07T12:00:00Z",
            media_type="application/json",
            mappings=[{"id": "/data/0/citingPaper/externalIds/ArXiv", "title": "/data/0/citingPaper/title"}],
            expected_revision=self.store.revision, request_id="citation-list")
        version = self.metadata()
        records = self.store.snapshot()["records"]
        self.assertEqual(self.find_link(records, "arxiv:2601.00001", version),
                         {"kind": "arxiv_version", "identifier": "arxiv:2601.00001"})

    def test_versionless_link_ignores_titles_and_authors(self):
        found = self.import_work("arxiv:2601.00001", authors=["Other Author"])
        version = self.import_work("arxiv:2601.00001v2")
        self.assertEqual(self.find_link(self.store.snapshot()["records"], found, version),
                         {"kind": "arxiv_version", "identifier": found})

    def test_datacite_doi_as_work_id_links_to_the_arxiv_version(self):
        doi = self.import_work("doi:10.48550/arxiv.2601.00001")
        version = self.import_work("arxiv:2601.00001v1")
        self.assertEqual(self.find_link(self.store.snapshot()["records"], doi, version),
                         {"kind": "datacite_doi", "identifier": doi})

    def test_datacite_doi_alias_links_a_registry_record_to_the_arxiv_version(self):
        doi = "doi:10.48550/arxiv.2601.00001"
        found = self.import_work("openalex:W123", aliases=[doi])
        version = self.import_work("arxiv:2601.00001v1")
        self.assertEqual(self.find_link(self.store.snapshot()["records"], found, version),
                         {"kind": "datacite_doi", "identifier": doi})

    def test_datacite_doi_links_old_style_arxiv_identifiers(self):
        found = self.import_work("doi:10.48550/arxiv.math.gt/0301001")
        version = self.import_work("arxiv:math.GT/0301001v2")
        self.assertEqual(self.find_link(self.store.snapshot()["records"], found, version),
                         {"kind": "datacite_doi", "identifier": found})

    def test_shared_doi_links_exact_versions_with_one_asserted_family(self):
        doi = "doi:10.1234/shared"
        found = self.import_work("arxiv:2601.00001v1", aliases=[doi])
        version = self.import_work("arxiv:2601.00001v2", aliases=[doi])
        self.assertEqual(self.find_link(self.store.snapshot()["records"], found, version),
                         {"kind": "shared_doi", "identifier": doi})

    def test_shared_doi_may_be_a_work_id(self):
        doi = self.import_work("doi:10.1234/shared")
        version = self.import_work("openalex:W123", aliases=[doi])
        records = self.store.snapshot()["records"]
        for found, target in ((doi, version), (version, doi)):
            with self.subTest(found=found):
                self.assertEqual(self.find_link(records, found, target),
                                 {"kind": "shared_doi", "identifier": doi})

    def test_conflicting_assertions_on_the_found_or_version_identifier_link_nothing(self):
        found = self.import_work("arxiv:2601.00001")
        version = self.import_work("arxiv:2601.00001v1")
        other = self.import_work("openalex:W999", aliases=[found, version])
        for identifier in (found, version):
            with self.subTest(identifier=identifier):
                records = self.store.snapshot()["records"]
                records["alias"] = {identifier: records["alias"][identifier]}
                self.assertEqual(records["alias"][identifier]["assertions"][0]["work_id"], other)
                self.assertIsNone(self.find_link(records, found, version))

    def test_conflicting_datacite_doi_assertions_link_nothing(self):
        doi = "doi:10.48550/arxiv.2601.00001"
        found = self.import_work("openalex:W123", aliases=[doi])
        version = self.import_work("arxiv:2601.00001v1")
        self.import_work("openalex:W999", aliases=[doi])
        self.assertIsNone(self.find_link(self.store.snapshot()["records"], found, version))

    def test_datacite_link_checks_the_resolved_arxiv_identifier(self):
        found = self.import_work("doi:10.48550/arxiv.2601.00001")
        version = self.import_work("arxiv:2601.00001v1")
        self.import_work("openalex:W999", aliases=["arxiv:2601.00001"])
        self.assertIsNone(self.find_link(self.store.snapshot()["records"], found, version))

    def test_conflicting_arxiv_base_does_not_block_an_independent_shared_doi(self):
        doi = "doi:10.1234/shared"
        found = self.import_work("arxiv:2601.00001v1", aliases=[doi, "doi:10.48550/arxiv.2601.00001"])
        version = self.import_work("arxiv:2601.00001v2", aliases=[doi])
        self.import_work("openalex:W999", aliases=["arxiv:2601.00001"])
        self.assertEqual(self.find_link(self.store.snapshot()["records"], found, version),
                         {"kind": "shared_doi", "identifier": doi})

    def test_shared_doi_links_normally_acquired_openalex_and_arxiv_records(self):
        found, version, doi = self.acquire_shared_doi_pair()
        records = self.store.snapshot()["records"]
        self.assertEqual({assertion["work_id"] for assertion in records["alias"][doi]["assertions"]},
                         {"openalex:W123", "arxiv:2601.00001"})
        self.assertEqual(self.find_link(records, found, version), {"kind": "shared_doi", "identifier": doi})

    def test_shared_doi_asserted_also_by_a_third_family_links_nothing(self):
        found, version, doi = self.acquire_shared_doi_pair()
        self.assertEqual(self.find_link(self.store.snapshot()["records"], found, version),
                         {"kind": "shared_doi", "identifier": doi})
        self.import_work("openalex:W999", aliases=[doi], authors=["A. Researcher"])
        self.assertIsNone(self.find_link(self.store.snapshot()["records"], found, version))

    def test_shared_doi_between_provider_families_still_requires_matching_authors(self):
        found, version, doi = self.acquire_shared_doi_pair()
        self.assertEqual(self.find_link(self.store.snapshot()["records"], found, version),
                         {"kind": "shared_doi", "identifier": doi})
        for authors in (["Other Author"], ["A. Researcher", "Other Author"]):
            with self.subTest(authors=authors):
                records = self.store.snapshot()["records"]
                records["work"][found]["authors"] = authors
                self.assertIsNone(self.find_link(records, found, version))

    def test_found_and_version_identifiers_may_be_asserted_by_either_linked_family(self):
        doi = "doi:10.1234/shared"
        found = self.import_work("openalex:W123", aliases=[doi, "arxiv:2601.00001v1"])
        version = self.import_work("arxiv:2601.00001v1", aliases=[doi, found])
        self.assertEqual(self.find_link(self.store.snapshot()["records"], found, version),
                         {"kind": "shared_doi", "identifier": doi})

    def test_datacite_doi_and_resolved_arxiv_id_may_be_asserted_by_either_linked_family(self):
        doi = "doi:10.48550/arxiv.2601.00001"
        found = self.import_work("openalex:W123", aliases=[doi, "arxiv:2601.00001"])
        version = self.import_work("arxiv:2601.00001v1", aliases=[doi])
        self.assertEqual(self.find_link(self.store.snapshot()["records"], found, version),
                         {"kind": "datacite_doi", "identifier": doi})

    def test_repeated_assertions_of_one_family_are_unambiguous(self):
        doi = "doi:10.1234/shared"
        found = self.import_work("arxiv:2601.00001v1", aliases=[doi])
        version = self.import_work("arxiv:2601.00001v2", aliases=[doi])
        records = self.store.snapshot()["records"]
        self.assertEqual(len(records["alias"][doi]["assertions"]), 2)
        self.assertEqual(self.find_link(records, found, version), {"kind": "shared_doi", "identifier": doi})

    def test_doi_links_compare_author_names_after_whitespace_and_case_normalization(self):
        doi = "doi:10.1234/shared"
        found = self.import_work("arxiv:2601.00001v1", aliases=[doi])
        version = self.import_work("arxiv:2601.00001v2", aliases=[doi])
        datacite = self.import_work("doi:10.48550/arxiv.2601.00001")
        records = self.store.snapshot()["records"]
        records["work"][version]["authors"] = [{"name": "  a.\tAUTHOR  "}, {"name": "b. author"}]
        for source, kind, identifier in ((found, "shared_doi", doi), (datacite, "datacite_doi", datacite)):
            with self.subTest(kind=kind):
                self.assertEqual(self.find_link(records, source, version), {"kind": kind, "identifier": identifier})

    def test_different_author_names_membership_or_order_block_doi_links(self):
        doi = "doi:10.1234/shared"
        found = self.import_work("arxiv:2601.00001v1", aliases=[doi])
        version = self.import_work("arxiv:2601.00001v2", aliases=[doi])
        datacite = self.import_work("doi:10.48550/arxiv.2601.00001")
        for authors in (["Other Author"], ["A. Author"], ["B. Author", "A. Author"]):
            for source in (found, datacite):
                with self.subTest(authors=authors, source=source):
                    records = self.store.snapshot()["records"]
                    records["work"][source]["authors"] = authors
                    self.assertIsNone(self.find_link(records, source, version))

    def test_missing_authors_on_either_side_permit_doi_links(self):
        doi = "doi:10.1234/shared"
        found = self.import_work("arxiv:2601.00001v1", aliases=[doi])
        version = self.import_work("arxiv:2601.00001v2", aliases=[doi])
        datacite = self.import_work("doi:10.48550/arxiv.2601.00001")
        for source, kind, identifier in ((found, "shared_doi", doi), (datacite, "datacite_doi", datacite)):
            for side in (source, version, "both"):
                for absent in (False, True):
                    with self.subTest(source=source, side=side, absent=absent):
                        records = self.store.snapshot()["records"]
                        for target in (source, version) if side == "both" else (side,):
                            if absent:
                                records["work"][target].pop("authors")
                            else:
                                records["work"][target]["authors"] = []
                        self.assertEqual(self.find_link(records, source, version),
                                         {"kind": kind, "identifier": identifier})

    def test_shared_title_or_plain_arxiv_alias_establishes_no_link(self):
        version = self.import_work("arxiv:2601.00001v1")
        for found in (self.import_work("openalex:W123"),
                      self.import_work("doi:10.1234/journal", aliases=["arxiv:2601.00001"])):
            with self.subTest(found=found):
                records = self.store.snapshot()["records"]
                records["work"][found]["title"] = records["work"][version]["title"]
                self.assertIsNone(self.find_link(records, found, version))

    def test_different_arxiv_papers_and_versions_without_a_doi_establish_no_link(self):
        version = self.import_work("arxiv:2601.00001v2")
        for found in (self.import_work("arxiv:2601.00002"), self.import_work("arxiv:2601.00001v1")):
            with self.subTest(found=found):
                self.assertIsNone(self.find_link(self.store.snapshot()["records"], found, version))

    def test_unrelated_datacite_doi_and_unversioned_target_establish_no_link(self):
        found = self.import_work("doi:10.48550/arxiv.2601.00002")
        version = self.import_work("arxiv:2601.00001v1")
        unversioned = self.import_work("arxiv:2601.00002")
        for target in (version, unversioned):
            with self.subTest(target=target):
                self.assertIsNone(self.find_link(self.store.snapshot()["records"], found, target))

    def test_an_independent_clean_doi_can_link_when_another_doi_is_ambiguous(self):
        aliases = ["doi:10.1234/ambiguous", "doi:10.1234/clean"]
        found = self.import_work("arxiv:2601.00001v1", aliases=aliases)
        version = self.import_work("arxiv:2601.00001v2", aliases=aliases)
        self.import_work("openalex:W999", aliases=aliases[:1])
        self.assertEqual(self.find_link(self.store.snapshot()["records"], found, version),
                         {"kind": "shared_doi", "identifier": "doi:10.1234/clean"})

    def test_multiple_shared_dois_select_deterministically_without_changing_stored_records(self):
        aliases = ["doi:10.1234/z", "doi:10.1234/a"]
        found = self.import_work("arxiv:2601.00001v1", aliases=aliases)
        version = self.import_work("arxiv:2601.00001v2", aliases=list(reversed(aliases)))
        records = self.store.snapshot()["records"]
        before = copy.deepcopy(records)
        before_digest = digest(records)
        for source, target in ((found, version), (version, found)):
            self.assertEqual(self.find_link(records, source, target),
                             {"kind": "shared_doi", "identifier": "doi:10.1234/a"})
        self.assertEqual(records, before)
        self.assertEqual(digest(records), before_digest)
