"""Citation accounting: a manuscript cites, or explains not citing, what the study read and searched."""

import json
import unittest

from development_fixtures import DevelopmentCase
from integration_fixtures import observed_candidate
from research_fixtures import atom, entry
from research_harness.acquisition import import_response
from research_harness.citations import (account_citations, collect_accountable_works, find_citing_entry,
                                        read_bibliography)
from research_harness.errors import ResearchError
from research_harness.publication import prepare_publication


def _build_fulltext_records(works):
    """Full-text readings of each {version_id: (aliases, title)} work, with its work record."""
    return {"reading": {"reading-" + version_id: {"version_id": version_id, "depth": "fulltext"} for version_id in works},
            "work": {version_id: {"id": version_id, "aliases": aliases, "title": title}
                     for version_id, (aliases, title) in works.items()}}


def _build_work(identifier, aliases=(), title=""):
    return {"id": identifier, "aliases": list(aliases), "title": title}


class CitationEvidenceTests(unittest.TestCase):
    def test_bibtex_entries_keep_file_order_and_their_titles(self):
        bibliography = read_bibliography(
            b"@article{zeta2020, title={A {Nested}  Title}, doi={10.1103/PhysRevB.105.085410}}\n"
            b"@misc{alpha2021,\n  eprint={2112.09662v2},\n  archivePrefix={arXiv}\n}\n"
            b"@comment{ignored, doi={10.1/ignored}}\n")
        self.assertEqual(list(bibliography["entries"]), ["zeta2020", "alpha2021"])
        self.assertEqual(bibliography["entries"]["zeta2020"]["title"], "a nested title")

    def test_identifiers_match_only_as_whole_identifiers(self):
        cases = {"doi prefix": (_build_work("doi:10.1063/1.365928"), b"@article{a, doi={10.1063/1.3659281}}", False),
                 "doi dotted extension": (_build_work("doi:10.1103/physrevb.1.138"),
                                          b"@article{a, doi={10.1103/PhysRevB.1.138.5}}", False),
                 "doi in url": (_build_work("doi:10.1103/physrevb.1.138"),
                                b"@article{a, url={https://doi.org/10.1103/PhysRevB.1.138}}", True),
                 "arxiv version": (_build_work("arxiv:2601.00001v1"), b"@misc{a, eprint={2601.00001v2}}", True),
                 "arxiv longer id": (_build_work("arxiv:2601.00001v1"), b"@misc{a, eprint={2601.000012}}", False),
                 "comment block": (_build_work("doi:10.5555/dropped"),
                                   b"@article{a, doi={10.5555/kept}}\n@comment{b, doi={10.5555/dropped}}", False)}
        for name, (work, data, cited) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(find_citing_entry(read_bibliography(data), [work])[0], cited)

    def test_a_title_cites_only_as_the_whole_entry_title(self):
        work = _build_work("arxiv:2601.00005v1", title="Weyl semimetals")
        longer = read_bibliography(b"@article{longer, title={Surface Fermi arcs in Weyl semimetals}}")
        self.assertEqual(find_citing_entry(longer, [work]), (False, None))
        same = read_bibliography(b"@article{same, title={{Weyl} Semimetals}}")
        self.assertEqual(find_citing_entry(same, [work]), (True, "same"))
        plain = read_bibliography(b"[1] A. Author, Weyl semimetals, J. Phys. (2020).")
        self.assertEqual(find_citing_entry(plain, [work]), (True, None))

    def test_titles_compare_after_accents_greek_letters_braces_and_scripts(self):
        cases = {"accent command": ('Schr\\"odinger cat states', "Schrödinger Cat States", True),
                 "greek letters": ("$\\alpha$-RuCl$_3$ magnets", "$\\beta$-RuCl$_3$ magnets", False),
                 "unicode greek": ("α-RuCl3 magnets", "$\\alpha$-RuCl$_{3}$ magnets", True),
                 "braced capital": ("Weyl semimetals", "{W}eyl Semimetals", True),
                 "cjk": ("拓扑半金属的输运", "拓扑半金属的输运", True)}
        for name, (work_title, entry_title, cited) in cases.items():
            with self.subTest(case=name):
                data = ("@article{entry, title={" + entry_title + "}}").encode()
                self.assertEqual(find_citing_entry(read_bibliography(data),
                                                   [_build_work("arxiv:2601.00005v1", title=work_title)])[0], cited)

    def test_text_between_entries_and_quoted_titles_are_read_as_bibtex(self):
        data = (b"@article{kept, doi={10.5555/kept}}\n% dropped: arXiv:2601.00007, doi 10.5555/dropped\n"
                b"@article{quoted, title = \"Schr{\\\"o}dinger operators on graphs\"}\n")
        bibliography = read_bibliography(data)
        self.assertEqual(find_citing_entry(bibliography, [_build_work("doi:10.5555/dropped")]), (False, None))
        self.assertEqual(find_citing_entry(bibliography, [_build_work("arxiv:2601.00007v1")]), (False, None))
        self.assertEqual(find_citing_entry(bibliography, [_build_work(
            "arxiv:2601.00008v1", title="Schrödinger operators on graphs")]), (True, "quoted"))

    def test_identifier_ends_and_pdf_urls(self):
        cases = {"doi then parenthesis": (b"@article{a, doi={10.1063/1.365928(99)}}", False),
                 "doi then underscore": (b"@article{a, doi={10.1063/1.365928_x}}", False),
                 "doi then colon": (b"@article{a, doi={10.1063/1.365928:x}}", False),
                 "doi in parentheses": (b"@article{a, note={(doi:10.1063/1.365928).}}", True),
                 "doi then pdf": (b"@article{a, url={https://x.org/10.1063/1.365928/pdf}}", True)}
        for name, (data, cited) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(find_citing_entry(read_bibliography(data),
                                                   [_build_work("doi:10.1063/1.365928")])[0], cited)
        pdf = read_bibliography(b"@misc{a, url={https://arxiv.org/pdf/2601.00001v1.pdf}}")
        self.assertEqual(find_citing_entry(pdf, [_build_work("arxiv:2601.00001v1")]), (True, "a"))

    def test_the_first_citing_entry_in_file_order_is_reported(self):
        bibliography = read_bibliography(b"@article{zeta2020, doi={10.5555/x}}\n"
                                         b"@article{alpha2021, note={see 10.5555/x}}\n")
        self.assertEqual(find_citing_entry(bibliography, [_build_work("doi:10.5555/x")]), (True, "zeta2020"))


class AccountableWorkTests(unittest.TestCase):
    def test_full_readings_and_selected_search_citations_carry_their_reasons(self):
        records = _build_fulltext_records({"arxiv:2601.00001v1": ([], ""), "arxiv:2601.00002v1": ([], "")})
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
        self.assertEqual(collect_accountable_works(records), {
            "arxiv:2601.00001": {"reasons": ["fulltext", "search:direct"],
                                 "version_ids": ["arxiv:2601.00001v1", "arxiv:2601.00001v2"]},
            "arxiv:2601.00002": {"reasons": ["fulltext"], "version_ids": ["arxiv:2601.00002v1"]},
            "doi:10.5555/cited": {"reasons": ["search:direct", "search:theory"], "version_ids": ["doi:10.5555/cited"]}})


class AccountCitationsTests(unittest.TestCase):
    BIBLIOGRAPHY = (b"@misc{one, eprint={2601.00001}, archivePrefix={arXiv}}\n"
                    b"@article{two, doi={10.5555/two}}\n"
                    b"@article{three, title={The Published Title}, doi={10.5555/three}}\n"
                    b"@article{five, title={Coupled Transport in Thin Films}}\n")

    def build_records(self):
        return _build_fulltext_records({"arxiv:2601.00001v1": ([], "One"),
                                  "arxiv:2601.00002v1": (["doi:10.5555/two"], "One"),
                                  "arxiv:2601.00003v1": ([], "The preprint title"),
                                  "arxiv:2601.00004v1": ([], "An unrelated fourth study"),
                                  "arxiv:2601.00005v1": ([], "Coupled transport in thin films")})

    def test_bibliography_evidence_declared_keys_and_reasons_account_for_every_work(self):
        result = account_citations(self.build_records(), self.BIBLIOGRAPHY, [
            {"work_id": "arxiv:2601.00004v1", "reason": "Read only to choose the model class."},
            {"work_id": "arxiv:2601.00003", "cited_as": "three"}])
        self.assertEqual(result, {
            "cited": [{"work_id": "arxiv:2601.00001", "key": "one", "basis": "bibliography"},
                      {"work_id": "arxiv:2601.00002", "key": "two", "basis": "bibliography"},
                      {"work_id": "arxiv:2601.00003", "key": "three", "basis": "declared"},
                      {"work_id": "arxiv:2601.00005", "key": "five", "basis": "bibliography"}],
            "not_cited": [{"work_id": "arxiv:2601.00004", "reason": "Read only to choose the model class."}]})

    def test_a_non_bibtex_bibliography_cites_without_a_key(self):
        records = _build_fulltext_records({"arxiv:2601.00001v1": ([], "One")})
        result = account_citations(records, b"[1] A. Author, arXiv:2601.00001 (2026).", None)
        self.assertEqual(result["cited"], [{"work_id": "arxiv:2601.00001", "key": None, "basis": "bibliography"}])

    def test_an_unaccounted_work_is_refused_with_its_reasons(self):
        with self.assertRaises(ResearchError) as raised:
            account_citations(self.build_records(), self.BIBLIOGRAPHY, [
                {"work_id": "arxiv:2601.00003", "cited_as": "three"}])
        self.assertEqual(raised.exception.code, "citation_accounting_incomplete")
        self.assertEqual(raised.exception.details, {"works": [{"work_id": "arxiv:2601.00004", "reasons": ["fulltext"]}]})

    def test_a_manuscript_that_cites_every_work_needs_no_items(self):
        records = _build_fulltext_records({"arxiv:2601.00001v1": ([], "One")})
        self.assertEqual(account_citations(records, self.BIBLIOGRAPHY, None)["not_cited"], [])

    def test_invalid_items_are_refused(self):
        valid_three = {"work_id": "arxiv:2601.00003", "cited_as": "three"}
        valid_four = {"work_id": "arxiv:2601.00004", "reason": "Model choice only."}
        cases = {"not a list": {"work_id": "arxiv:2601.00004"},
                 "not an object": [valid_three, valid_four, "arxiv:2601.00099"],
                 "unknown work": [valid_three, valid_four, {"work_id": "arxiv:2601.00099", "reason": "x"}],
                 "already cited": [valid_three, valid_four, {"work_id": "arxiv:2601.00001", "reason": "x"}],
                 "duplicate": [valid_three, valid_four, dict(valid_four)],
                 "both": [valid_three, dict(valid_four, cited_as="three")],
                 "neither": [valid_three, {"work_id": "arxiv:2601.00004"}],
                 "blank reason": [valid_three, {"work_id": "arxiv:2601.00004", "reason": " "}],
                 "unknown key": [{"work_id": "arxiv:2601.00003", "cited_as": "absent"}, valid_four],
                 "key not text": [{"work_id": "arxiv:2601.00003", "cited_as": ["three"]}, valid_four],
                 "extra field": [valid_three, dict(valid_four, note="x")],
                 "bad identifier": [valid_three, valid_four, {"work_id": "not an id", "reason": "x"}]}
        for name, items in cases.items():
            with self.subTest(case=name), self.assertRaises(ResearchError) as raised:
                account_citations(self.build_records(), self.BIBLIOGRAPHY, items)
            self.assertEqual(raised.exception.code, "invalid_citation_accounting")

    def test_each_refused_item_names_its_index_and_cause(self):
        valid_three = {"work_id": "arxiv:2601.00003", "cited_as": "three"}
        valid_four = {"work_id": "arxiv:2601.00004", "reason": "Model choice only."}
        cases = {"unknown_work": ([valid_three, valid_four, {"work_id": "arxiv:2601.00099", "reason": "x"}], 2),
                 "duplicate": ([valid_three, valid_four, dict(valid_four)], 2),
                 "cited_as_and_reason": ([valid_three, dict(valid_four, cited_as="three")], 1),
                 "neither": ([valid_three, {"work_id": "arxiv:2601.00004"}], 1),
                 "fields": ([valid_three, dict(valid_four, note="x")], 1)}
        for cause, (items, index) in cases.items():
            with self.subTest(cause=cause), self.assertRaises(ResearchError) as raised:
                account_citations(self.build_records(), self.BIBLIOGRAPHY, items)
            self.assertEqual((raised.exception.details["index"], raised.exception.details["cause"]), (index, cause))
        with self.assertRaises(ResearchError) as raised:
            account_citations(self.build_records(), self.BIBLIOGRAPHY,
                              [valid_three, valid_four, {"work_id": "arxiv:2601.00001", "reason": "x"}])
        self.assertEqual(raised.exception.details, {"index": 2, "cause": "already_cited",
                                                    "work_id": "arxiv:2601.00001", "key": "one"})


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

    def build_payload(self, accounting=None):
        payload = {"id": "paper-1", "files": {"pdf": "draft/paper.pdf", "abstract": "draft/abstract.txt",
                   "bibliography": "draft/references.bib", "claims": "evidence/claims.json", "sources": None},
                   "claim_evidence": [{"claim_id": "bound", "evidence": [self.result_evidence(self.execution_payload)]}]}
        if accounting is not None:
            payload["citation_accounting"] = accounting
        return payload

    def test_the_pin_requires_every_read_work_to_be_cited_or_explained(self):
        with self.assertRaises(ResearchError) as raised:
            self.mutate(prepare_publication, self.build_payload())
        self.assertEqual(raised.exception.code, "citation_accounting_incomplete")
        self.assertEqual([work["work_id"] for work in raised.exception.details["works"]],
                         ["arxiv:2601.%05d" % n for n in range(3, 7)])
        self.assertTrue(all(work["reasons"] == ["fulltext"] for work in raised.exception.details["works"]))

    def test_the_bundle_records_the_accounting(self):
        reasons = [{"work_id": "arxiv:2601.%05d" % n, "reason": "Read source %d; not part of the argument." % n}
                   for n in range(3, 7)]
        bundle = self.mutate(prepare_publication, self.build_payload(reasons))["result"]
        self.assertEqual(bundle["citation_accounting"], {
            "cited": [{"work_id": "arxiv:2601.00001", "key": "first", "basis": "bibliography"},
                      {"work_id": "arxiv:2601.00002", "key": "second", "basis": "bibliography"}],
            "not_cited": reasons})
        saved = self.store.snapshot()["records"]["publication_bundle"]["paper-1"]
        self.assertEqual(saved["citation_accounting"], bundle["citation_accounting"])
