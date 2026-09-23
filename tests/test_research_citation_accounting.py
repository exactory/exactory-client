"""Citation accounting: a manuscript cites, or explains not citing, what the study read and searched."""

import json
import unittest

from development_fixtures import DevelopmentCase
from integration_fixtures import observed_candidate
from research_fixtures import atom, entry
from research_harness.acquisition import import_response
from research_harness.citations import account_citations, accountable_works, parse_bibliography
from research_harness.errors import ResearchError
from research_harness.publication import prepare_publication


def _fulltext_records(*numbers, aliases=None):
    records = {"reading": {"reading-%d" % n: {"version_id": "arxiv:2601.%05dv1" % n, "depth": "fulltext"}
                           for n in numbers}}
    records["alias"] = {identifier: {"identifier": identifier, "assertions": [
        {"work_id": work_id, "source_id": "source-1", "locator": None, "relation": "same_work"}]}
        for identifier, work_id in (aliases or {}).items()}
    return records


class BibliographyParsingTests(unittest.TestCase):
    def test_doi_arxiv_and_url_fields_name_work_families(self):
        data = (b"@article{journal, title={A {Nested} title}, doi={10.1103/PhysRevB.105.085410}}\n"
                b"@misc{preprint,\n  eprint={2112.09662v2},\n  archivePrefix={arXiv}\n}\n"
                b"@misc{linked, url={https://arxiv.org/abs/2402.14112v3}}\n"
                b"@article{resolver, url = \"https://doi.org/10.1038/S41535-022-00535-6\"}\n"
                b"@misc{datacite, doi={10.48550/arXiv.2607.02120}}\n"
                b"@book{plain, title={No identifier}}\n"
                b"@comment{ignored, doi={10.1/ignored}}\n")
        self.assertEqual(parse_bibliography(data), {
            "journal": {"doi:10.1103/physrevb.105.085410"}, "preprint": {"arxiv:2112.09662"},
            "linked": {"arxiv:2402.14112"}, "resolver": {"doi:10.1038/s41535-022-00535-6"},
            "datacite": {"arxiv:2607.02120"}, "plain": set()})


class AccountableWorkTests(unittest.TestCase):
    def test_full_readings_and_selected_search_citations_carry_their_reasons(self):
        records = _fulltext_records(1, 2)
        records["reading"]["abstract-3"] = {"version_id": "arxiv:2601.00003v1", "depth": "abstract"}
        records["reading"]["reading-1b"] = {"version_id": "arxiv:2601.00001v2", "depth": "fulltext"}
        records["search_selection"] = {"research:direct": {"search_id": "direct-2"},
                                       "research:theory": {"search_id": "theory"},
                                       "verification:direct": {"search_id": "verify-direct"}}
        records["literature_search"] = {
            "direct-1": {"cited_work_ids": ["arxiv:2601.00009v1"]},
            "direct-2": {"cited_work_ids": ["arxiv:2601.00001v1", "doi:10.5555/cited"]},
            "theory": {"cited_work_ids": ["doi:10.5555/cited"]},
            "verify-direct": {"cited_work_ids": ["arxiv:2601.00008v1"]}}
        self.assertEqual(accountable_works(records), {
            "arxiv:2601.00001": ["fulltext", "search:direct"],
            "arxiv:2601.00002": ["fulltext"],
            "doi:10.5555/cited": ["search:direct", "search:theory"]})


class AccountCitationsTests(unittest.TestCase):
    BIBLIOGRAPHY = (b"@misc{one, eprint={2601.00001}, archivePrefix={arXiv}}\n"
                    b"@article{two, doi={10.5555/two}}\n@article{three, doi={10.5555/three}}\n")

    def records(self):
        return _fulltext_records(1, 2, 3, 4, aliases={"doi:10.5555/two": "arxiv:2601.00002"})

    def test_identifier_alias_declared_and_reason_account_for_every_work(self):
        result = account_citations(self.records(), self.BIBLIOGRAPHY, [
            {"work_id": "arxiv:2601.00004v1", "reason": "Read only to choose the model class."},
            {"work_id": "arxiv:2601.00003", "cited_as": "three"}])
        self.assertEqual(result, {
            "cited": [{"work_id": "arxiv:2601.00001", "key": "one", "basis": "identifier"},
                      {"work_id": "arxiv:2601.00002", "key": "two", "basis": "alias"},
                      {"work_id": "arxiv:2601.00003", "key": "three", "basis": "declared"}],
            "not_cited": [{"work_id": "arxiv:2601.00004", "reason": "Read only to choose the model class."}]})

    def test_an_unaccounted_work_is_refused_with_its_reasons(self):
        with self.assertRaises(ResearchError) as raised:
            account_citations(self.records(), self.BIBLIOGRAPHY, [
                {"work_id": "arxiv:2601.00003", "cited_as": "three"}])
        self.assertEqual(raised.exception.code, "citation_accounting_incomplete")
        self.assertEqual(raised.exception.details, {"works": [{"work_id": "arxiv:2601.00004", "reasons": ["fulltext"]}]})

    def test_a_manuscript_that_cites_every_work_needs_no_items(self):
        records = _fulltext_records(1)
        self.assertEqual(account_citations(records, self.BIBLIOGRAPHY, None)["not_cited"], [])

    def test_invalid_items_are_refused(self):
        valid_three = {"work_id": "arxiv:2601.00003", "cited_as": "three"}
        valid_four = {"work_id": "arxiv:2601.00004", "reason": "Model choice only."}
        cases = {"not a list": {"work_id": "arxiv:2601.00004"},
                 "unknown work": [valid_three, valid_four, {"work_id": "arxiv:2601.00099", "reason": "x"}],
                 "already cited": [valid_three, valid_four, {"work_id": "arxiv:2601.00001", "reason": "x"}],
                 "duplicate": [valid_three, valid_four, dict(valid_four)],
                 "both": [valid_three, dict(valid_four, cited_as="three")],
                 "neither": [valid_three, {"work_id": "arxiv:2601.00004"}],
                 "blank reason": [valid_three, {"work_id": "arxiv:2601.00004", "reason": " "}],
                 "unknown key": [{"work_id": "arxiv:2601.00003", "cited_as": "absent"}, valid_four],
                 "extra field": [valid_three, dict(valid_four, note="x")],
                 "bad identifier": [valid_three, valid_four, {"work_id": "not an id", "reason": "x"}]}
        for name, items in cases.items():
            with self.subTest(case=name), self.assertRaises(ResearchError) as raised:
                account_citations(self.records(), self.BIBLIOGRAPHY, items)
            self.assertEqual(raised.exception.code, "invalid_citation_accounting")


class ManuscriptPinTests(DevelopmentCase):
    ALIAS_DOI = "10.5555/published-two"

    def metadata(self, number=1, version=1, abstract=None, references=None):
        identifier = super().metadata(number, version, abstract, references)
        if number == 2:
            self.sequence += 1
            import_response(self.store, "arxiv", atom([entry(identifier[6:], doi=self.ALIAS_DOI)], total=1),
                            source_url="https://export.arxiv.org/api/query?id_list=" + identifier[6:],
                            captured_at="2026-09-07T12:00:00Z", expected_revision=self.store.revision,
                            request_id="published-alias-" + str(self.sequence))
        return identifier

    def setUp(self):
        super().setUp()
        self.prepared_study()
        self.execution_payload = observed_candidate(self)
        (self.root / "draft").mkdir()
        (self.root / "evidence").mkdir()
        (self.root / "draft/paper.pdf").write_bytes(b"%PDF-1.4\n% Authored citation accounting fixture.\n%%EOF")
        (self.root / "draft/abstract.txt").write_text("The exact finite bound was enumerated.")
        (self.root / "draft/references.bib").write_text(
            "@misc{first, eprint={2601.00001}, archivePrefix={arXiv}}\n"
            "@article{second, doi={" + self.ALIAS_DOI + "}}\n")
        (self.root / "evidence/claims.json").write_text(json.dumps([{"id": "bound", "claim": "The maximum is 9.",
                                                                     "source": "The recorded finite enumeration."}]))

    def payload(self, accounting=None):
        payload = {"id": "paper-1", "files": {"pdf": "draft/paper.pdf", "abstract": "draft/abstract.txt",
                   "bibliography": "draft/references.bib", "claims": "evidence/claims.json", "sources": None},
                   "claim_evidence": [{"claim_id": "bound", "evidence": [self.result_evidence(self.execution_payload)]}]}
        if accounting is not None:
            payload["citation_accounting"] = accounting
        return payload

    def test_the_pin_requires_every_read_work_to_be_cited_or_explained(self):
        with self.assertRaises(ResearchError) as raised:
            self.mutate(prepare_publication, self.payload())
        self.assertEqual(raised.exception.code, "citation_accounting_incomplete")
        self.assertEqual([work["work_id"] for work in raised.exception.details["works"]],
                         ["arxiv:2601.%05d" % n for n in range(3, 7)])
        self.assertTrue(all(work["reasons"] == ["fulltext"] for work in raised.exception.details["works"]))

    def test_the_bundle_records_the_accounting(self):
        reasons = [{"work_id": "arxiv:2601.%05d" % n, "reason": "Read source %d; not part of the argument." % n}
                   for n in range(3, 7)]
        bundle = self.mutate(prepare_publication, self.payload(reasons))["result"]
        self.assertEqual(bundle["citation_accounting"], {
            "cited": [{"work_id": "arxiv:2601.00001", "key": "first", "basis": "identifier"},
                      {"work_id": "arxiv:2601.00002", "key": "second", "basis": "alias"}],
            "not_cited": reasons})
        saved = self.store.snapshot()["records"]["publication_bundle"]["paper-1"]
        self.assertEqual(saved["citation_accounting"], bundle["citation_accounting"])
