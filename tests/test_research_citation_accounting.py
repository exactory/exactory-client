"""Citation accounting: a manuscript cites, or explains not citing, what the study read and searched."""

import json
import time
import unittest

from development_fixtures import DevelopmentCase
from integration_fixtures import observed_candidate
from research_fixtures import atom, entry
from research_harness.acquisition import import_response
from research_harness.citations import (account_citations, collect_accountable_works, find_citing_entry,
                                        find_citation_tokens, read_bibliography)
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
    def test_datacite_arxiv_doi_contributes_the_arxiv_family_token(self):
        work = _build_work("openalex:W7213271184", ["doi:10.48550/arxiv.2609.14520"], "Recorded paper title")
        self.assertEqual(find_citation_tokens(work), ["10.48550/arxiv.2609.14520", "2609.14520"])
        bibliography = read_bibliography(b"@misc{arxiv-only, note={arXiv:2609.14520}, title={A different title}}")
        self.assertEqual(find_citing_entry(bibliography, [work]), (True, "arxiv-only"))

    def test_datacite_doi_in_an_entry_still_cites_an_arxiv_work(self):
        bibliography = read_bibliography(b"@misc{doi-only, doi={10.48550/arXiv.2609.14520}}")
        self.assertEqual(find_citing_entry(bibliography, [_build_work("arxiv:2609.14520v1")]), (True, "doi-only"))

    def test_arxiv_tokens_do_not_match_without_an_arxiv_context(self):
        fields = ("doi={10.9999/2609.14520}", "doi={10.9999/x-2609.14520}", "doi={10.9999/x_2609.14520}",
                  "doi={10.9999/x:2609.14520}", "doi={10.9999/x;2609.14520}", "doi={10.9999/(2609.14520)}",
                  "url={https://example.org/2609.14520}", "url={https://notarxiv.org/abs/2609.14520}",
                  "doi={10.9999/x10.48550/arxiv.2609.14520}", "note={foo.2609.14520}",
                  "note={2609.14520}", "note={myarxiv:2609.14520}", "note={1arxiv 2609.14520}",
                  "url={https://other.arxiv.org/abs/2609.14520}", "url={https://x-arxiv.org/abs/2609.14520}",
                  "url={https://arxiv.org.example/abs/2609.14520}", "myeprint={2609.14520}",
                  "eprint=2609.14520", "note={110.48550/arxiv.2609.14520}",
                  "doi={10.9999/arxiv:2609.14520}", "doi={10.9999/x(arxiv:2609.14520)}",
                  "url={https://example.org/arxiv.org/abs/2609.14520}",
                  "url={https://example.org/?u=arxiv:2609.14520}",
                  "url={https://foo.arxiv.org/abs/2609.14520}", "note={x-arxiv:2609.14520}",
                  "note={_arxiv:2609.14520}", "note={x/arxiv:2609.14520}",
                  "note={x.arxiv:2609.14520}", "_eprint={2609.14520}",
                  "doi={10.48550/arxiv.2609.14520v1}", "doi={10.48550/arxiv.2609.14520-extra}",
                  "url={https://doi.org/10.48550/arxiv.2609.14520v1}")
        for index, field in enumerate(fields):
            data = ("@article{unrelated, " + field + ", title={Unrelated context entry %d}}" % index).encode()
            for identifier in ("arxiv:2609.14520v1", "doi:10.48550/arxiv.2609.14520"):
                with self.subTest(identifier=identifier, field=field):
                    work = _build_work(identifier, title="Recorded paper title")
                    self.assertEqual(find_citing_entry(read_bibliography(data), [work]), (False, None))

    def test_an_arxiv_token_in_a_bibtex_key_does_not_cite_a_work(self):
        for key in ("ref:2609.14520", "arxiv:2609.14520", "10.48550/arxiv.2609.14520"):
            data = ("@article{" + key + ", title={Unrelated bibliography key}}").encode()
            for identifier in ("arxiv:2609.14520v1", "doi:10.48550/arxiv.2609.14520"):
                with self.subTest(identifier=identifier, key=key):
                    self.assertEqual(find_citing_entry(read_bibliography(data),
                                                       [_build_work(identifier, title="Recorded paper title")]), (False, None))

    def test_a_doi_in_a_bibtex_key_does_not_cite_a_doi_work(self):
        for data in (b"@article{doi:10.1063/1.365928, title={Another publication}}",
                     b"@ARTICLE (10.1063/1.365928, title={Another publication})"):
            with self.subTest(data=data):
                self.assertEqual(find_citing_entry(read_bibliography(data),
                                                   [_build_work("doi:10.1063/1.365928", title="Recorded paper title")]),
                                 (False, None))

    def test_arxiv_tokens_match_only_in_explicit_reference_contexts(self):
        fields = ("note={arXiv:2609.14520}", "note={arXiv: 2609.14520}", "note={arXiv 2609.14520}",
                  "journal={arXiv preprint arXiv:2609.14520v2}", "eprint={2609.14520}",
                  'eprint = "2609.14520v1"', "url={https://arxiv.org/abs/2609.14520v2}",
                  "url={http://export.arxiv.org/abs/2609.14520}", "url={https://www.arxiv.org/abs/2609.14520}",
                  "url={https://arxiv.org/pdf/2609.14520v2.pdf}", "url={https://arxiv.org/pdf/2609.14520v2}",
                  "url={https://arxiv.org/html/2609.14520v1}",
                  "url={https://arxiv.org/ftp/arxiv/papers/2609/2609.14520.pdf}",
                  "doi={10.48550/arXiv.2609.14520}", "url={https://doi.org/10.48550/arXiv.2609.14520}",
                  "url={https://arxiv.org/format/2609.14520}", "note={arXiv \t: \t2609.14520}",
                  "eprint \t= { \t2609.14520}", "eprint={arXiv:2609.14520}",
                  r"howpublished={\url{https://arxiv.org/abs/2609.14520}}",
                  "url={https://arxiv.org/abs/2609.14520?context=math}",
                  "url={https://arxiv.org/abs/2609.14520v2.pdf/}",
                  "note={arxiv.org/abs/2609.14520}", "note={www.arxiv.org/pdf/2609.14520.pdf}",
                  "note={export.arxiv.org/html/2609.14520/}",
                  "url={https://dx.doi.org/10.48550/arxiv.2609.14520}")
        for index, field in enumerate(fields):
            data = ("@article{cited, " + field + ", title={Explicit reference entry %d}}" % index).encode()
            for identifier in ("arxiv:2609.14520v1", "doi:10.48550/arxiv.2609.14520"):
                with self.subTest(identifier=identifier, field=field):
                    work = _build_work(identifier, title="Recorded paper title")
                    self.assertEqual(find_citing_entry(read_bibliography(data), [work]), (True, "cited"))

    def test_arxiv_urls_require_an_exact_host_and_identifier_path(self):
        urls = ("https://arxiv.org/abs/2609.14520/extra", "https://arxiv.org/abs/2609.14520.pdf/extra",
                "https://arxiv.org/?id=2609.14520", "https://arxiv.org/other/2609.14520",
                "https://doi.org/10.9999/arxiv:2609.14520", "https://dx.doi.org/10.9999/arxiv:2609.14520",
                "https://example.org/?u=https://arxiv.org/abs/2609.14520",
                "https://[invalid/arxiv:2609.14520", "xarxiv.org/abs/2609.14520",
                "x.arxiv.org/abs/2609.14520", "x/arxiv.org/abs/2609.14520", "x-arxiv.org/abs/2609.14520")
        for url in urls:
            data = ("@article{unrelated, url={" + url + "}, title={Another publication}}").encode()
            for identifier in ("arxiv:2609.14520v1", "doi:10.48550/arxiv.2609.14520"):
                with self.subTest(identifier=identifier, url=url):
                    self.assertEqual(find_citing_entry(read_bibliography(data),
                                                       [_build_work(identifier, title="Recorded paper title")]), (False, None))

    def test_nested_identifiers_do_not_cite_arxiv_works(self):
        fields = ("doi={10.9999/https://arxiv.org/abs/2609.14520}",
                  "doi={10.9999/<arxiv:2609.14520>}",
                  "url={ftp://example.org/?q=arxiv:2609.14520}",
                  "doi={10.9999/x(arxiv:2609.14520)}",
                  "doi={10.9999/x<https://arxiv.org/abs/2609.14520>}",
                  "url={https://example.org/?u=https://arxiv.org/abs/2609.14520}")
        for field in fields:
            data = ("@article{unrelated, " + field + ", title={Another publication}}").encode()
            for identifier in ("arxiv:2609.14520v1", "doi:10.48550/arxiv.2609.14520"):
                with self.subTest(identifier=identifier, field=field):
                    self.assertEqual(find_citing_entry(read_bibliography(data),
                                                       [_build_work(identifier, title="Recorded paper title")]), (False, None))

    def test_brackets_inside_urls_and_dois_do_not_expose_nested_arxiv_labels(self):
        references = ("url={https://[::1]/?q=arxiv:2609.14520}",
                      "doi={10.9999/x]arxiv:2609.14520}",
                      "https://[::1]/?q=arxiv:2609.14520",
                      "10.9999/x]arxiv:2609.14520")
        for reference in references:
            data = ("@article{unrelated, " + reference + ", title={Another publication}}"
                    if "={" in reference else "Another publication, " + reference).encode()
            for identifier in ("arxiv:2609.14520v1", "doi:10.48550/arxiv.2609.14520"):
                with self.subTest(identifier=identifier, reference=reference):
                    self.assertEqual(find_citing_entry(read_bibliography(data),
                                                       [_build_work(identifier, title="Recorded paper title")]),
                                     (False, None))

    def test_a_doi_nested_inside_another_doi_does_not_cite_its_work(self):
        cases = (("10.9999/x/10.48550/arxiv.2609.14520", "doi:10.48550/arxiv.2609.14520"),
                 ("10.9999/10.1234/abc", "doi:10.1234/abc"))
        for doi, identifier in cases:
            for reference in ("@article{unrelated, doi={" + doi + "}, title={Another publication}}", doi):
                with self.subTest(identifier=identifier, reference=reference):
                    self.assertEqual(find_citing_entry(read_bibliography(reference.encode()),
                                                       [_build_work(identifier, title="Recorded paper title")]),
                                     (False, None))

    def test_whole_dois_cite_through_fields_labels_registry_urls_and_markdown(self):
        references = ("doi={10.1234/abc}", "doi:10.1234/abc", "https://doi.org/10.1234/abc",
                      "https://dx.doi.org/10.1234%2Fabc", "[10.1234/abc](https://doi.org/10.1234/abc)",
                      "[https://doi.org/10.1234/abc](https://doi.org/10.1234/abc)")
        for reference in references:
            for punctuation in ("", "."):
                for bibtex in (False, True):
                    data = (("@article{cited, note={" + reference + punctuation + "}, title={Another publication}}")
                            if bibtex else "Another publication, " + reference + punctuation).encode()
                    with self.subTest(reference=reference, punctuation=punctuation, bibtex=bibtex):
                        self.assertEqual(find_citing_entry(read_bibliography(data),
                                                           [_build_work("doi:10.1234/abc", title="Recorded paper title")]),
                                         (True, "cited" if bibtex else None))

    def test_a_doi_only_inside_a_publisher_url_path_no_longer_cites_by_identifier(self):
        data = (b"@article{unrelated, url={https://link.springer.com/article/10.1007/s00222-019-00895-1},"
                b" title={Another publication}}")
        self.assertEqual(find_citing_entry(read_bibliography(data),
                                           [_build_work("doi:10.1007/s00222-019-00895-1", title="Recorded paper title")]),
                         (False, None))

    def test_internal_square_brackets_belong_to_the_whole_doi(self):
        data = b"@article{cited, doi={10.1234/a[b]c}, title={Another publication}}"
        bibliography = read_bibliography(data)
        self.assertEqual(find_citing_entry(bibliography, [_build_work("doi:10.1234/a[b]c")]), (True, "cited"))
        self.assertEqual(find_citing_entry(bibliography, [_build_work("doi:10.1234/a")]), (False, None))

    def test_invalid_arxiv_candidates_are_skipped_without_losing_valid_evidence(self):
        fields = ("note={arxiv:2613.14520 arxiv:2609.14520}",
                  "eprint={2609.14520v0}, note={arxiv:2609.14520}",
                  "note={hep-th/9313002 math.GT/0309136v0 arxiv:2609.14520}",
                  "url={https://arxiv.org/abs/2613.14520}, note={arxiv:2609.14520}",
                  "doi={10.48550/arxiv.2613.14520}, note={arxiv:2609.14520}")
        for field in fields:
            with self.subTest(field=field):
                data = ("@article{cited, " + field + ", title={Another publication}}").encode()
                self.assertEqual(find_citing_entry(read_bibliography(data), [_build_work("arxiv:2609.14520v1")]),
                                 (True, "cited"))

    def test_adversarial_64kb_fields_are_read_and_checked_against_50_works_in_under_one_second(self):
        works = [_build_work("arxiv:2609.%05dv1" % (14520 + index),
                             ["doi:10.1234/recorded-%d" % index], "Recorded paper title")
                 for index in range(50)]
        for repeated in ("a-", "10.1234/"):
            data = ("@article{unrelated, note={" + repeated * (64 * 1024 // len(repeated))
                    + "}, title={Another publication}}").encode()
            with self.subTest(repeated=repeated):
                started = time.perf_counter()
                bibliography = read_bibliography(data)
                for work in works:
                    self.assertEqual(find_citing_entry(bibliography, [work]), (False, None))
                    self.assertLess(time.perf_counter() - started, 1.0)

    def test_identifier_spans_cannot_create_an_arxiv_label_match(self):
        references = (b"[1] Another publication, arXiv:10.9999/nope 2609.14520",
                      b"Another publication, arXiv:2609.14520https://example.org/x")
        for data in references:
            for identifier in ("arxiv:2609.14520v1", "doi:10.48550/arxiv.2609.14520"):
                with self.subTest(identifier=identifier, data=data):
                    self.assertEqual(find_citing_entry(read_bibliography(data),
                                                       [_build_work(identifier, title="Recorded paper title")]), (False, None))

    def test_plain_text_arxiv_links_cite_their_work(self):
        references = (b"Another publication, [https://arxiv.org/abs/2609.14520](https://arxiv.org/abs/2609.14520)",
                      b"Another publication, <https://arxiv.org/abs/2609.14520>")
        for data in references:
            for identifier in ("arxiv:2609.14520v1", "doi:10.48550/arxiv.2609.14520"):
                with self.subTest(identifier=identifier, data=data):
                    self.assertEqual(find_citing_entry(read_bibliography(data),
                                                       [_build_work(identifier, title="Recorded paper title")]), (True, None))

    def test_a_plain_text_old_style_arxiv_link_cites_its_work(self):
        data = b"Another publication, [hep-th/9304002](https://arxiv.org/abs/hep-th/9304002)"
        self.assertEqual(find_citing_entry(read_bibliography(data),
                                           [_build_work("arxiv:hep-th/9304002v1", title="Recorded archive title")]),
                         (True, None))

    def test_an_arxiv_label_outside_a_url_still_cites_its_work(self):
        data = b"@article{cited, note={See arXiv:2609.14520, https://example.org/x}, title={Another publication}}"
        for identifier in ("arxiv:2609.14520v1", "doi:10.48550/arxiv.2609.14520"):
            with self.subTest(identifier=identifier):
                self.assertEqual(find_citing_entry(read_bibliography(data),
                                                   [_build_work(identifier, title="Recorded paper title")]), (True, "cited"))

    def test_arxiv_urls_accept_schemes_and_schemeless_urls_with_nested_queries(self):
        urls = ("ftp://arxiv.org/abs/2609.14520", "git+ssh://export.arxiv.org/abs/2609.14520",
                "arxiv.org/abs/2609.14520?u=https://example.org/x",
                "www.arxiv.org/abs/2609.14520?u=https://example.org/x",
                "export.arxiv.org/abs/2609.14520?u=https://example.org/x")
        for url in urls:
            data = ("@article{cited, url={" + url + "}, title={Another publication}}").encode()
            for identifier in ("arxiv:2609.14520v1", "doi:10.48550/arxiv.2609.14520"):
                with self.subTest(identifier=identifier, url=url):
                    self.assertEqual(find_citing_entry(read_bibliography(data),
                                                       [_build_work(identifier, title="Recorded paper title")]), (True, "cited"))

    def test_a_legacy_sici_doi_cites_only_its_own_work(self):
        data = (b"@article{sici, doi={10.1002/(SiCi)1099-1638(199903/04)15:2<123::AiD-qRe211>3.0.Co;2-f},"
                b" title={Another publication}}")
        bibliography = read_bibliography(data)
        doi = "10.1002/(sici)1099-1638(199903/04)15:2<123::aid-qre211>3.0.co;2-f"
        self.assertEqual(find_citing_entry(bibliography, [_build_work("doi:" + doi, title="Recorded journal title")]),
                         (True, "sici"))
        for identifier in ("arxiv:2609.14520v1", "doi:10.48550/arxiv.2609.14520"):
            with self.subTest(identifier=identifier):
                self.assertEqual(find_citing_entry(bibliography, [_build_work(identifier, title="Recorded paper title")]),
                                 (False, None))

    def test_arxiv_url_paths_cannot_supply_doi_identifiers(self):
        for path in ("abs", "pdf", "html", "format"):
            for suffix in ("10.48550/arxiv.2609.14520", "doi:10.48550/arxiv.2609.14520"):
                data = ("@article{entry, url={https://arxiv.org/" + path + "/" + suffix
                        + "}, title={Another publication}}").encode()
                for identifier, cited in (("arxiv:2609.14520v1", False),
                                           ("doi:10.48550/arxiv.2609.14520", False)):
                    with self.subTest(path=path, suffix=suffix, identifier=identifier):
                        self.assertEqual(find_citing_entry(read_bibliography(data),
                                                           [_build_work(identifier, title="Recorded paper title")]),
                                         (cited, "entry" if cited else None))

    def test_old_style_arxiv_tokens_match_reference_contexts(self):
        cases = {"note={arXiv:hep-th/9711200}": True,
                 "url={https://arxiv.org/abs/hep-th/9711200}": True,
                 "eprint={hep-th/9711200}": True,
                 "doi={10.48550/arXiv.hep-th/9711200}": True,
                 "doi={10.9999/hep-th/9711200}": False}
        for index, (field, cited) in enumerate(cases.items()):
            with self.subTest(field=field):
                data = ("@article{entry, " + field + ", title={Old archive entry %d}}" % index).encode()
                self.assertEqual(find_citing_entry(read_bibliography(data),
                                                   [_build_work("arxiv:hep-th/9711200v2", title="Recorded archive title")]),
                                 (True, "entry") if cited else (False, None))

    def test_old_style_arxiv_tokens_cite_without_a_label_outside_urls_and_dois(self):
        cases = ((b"[17] A. Author, hep-th/9304002.", True, None),
                 (b"@article{cited, note={hep-th/9304002}, title={Another publication}}", True, "cited"),
                 (b"@article{cited, url={https://arxiv.org/ftp/hep-th/papers/9304/9304002.pdf},"
                  b" title={Another publication}}", True, "cited"),
                 (b"@article{uncited, doi={10.9999/hep-th/9304002}, title={Another publication}}", False, None),
                 (b"@article{uncited, url={https://example.org/hep-th/9304002},"
                  b" title={Another publication}}", False, None),
                 (b"@article{hep-th/9304002, title={Another publication}}", False, None),
                 (b"@article{uncited, note={x-hep-th/9304002}, title={Another publication}}", False, None))
        for data, cited, key in cases:
            with self.subTest(data=data):
                self.assertEqual(find_citing_entry(read_bibliography(data),
                                                   [_build_work("arxiv:hep-th/9304002v1", title="Recorded archive title")]),
                                 (cited, key))

    def test_subject_class_arxiv_tokens_still_match(self):
        data = b"@article{subject, note={arXiv:math.GT/0309136}, title={Unrelated subject class entry}}"
        self.assertEqual(find_citing_entry(read_bibliography(data),
                                           [_build_work("arxiv:math.GT/0309136v2", title="Recorded subject title")]),
                         (True, "subject"))

    def test_a_legacy_ftp_url_cites_its_arxiv_work(self):
        data = (b"@article{legacy, url={https://arxiv.org/ftp/arxiv/papers/2006/2006.11098.pdf},"
                b" title={Unrelated legacy URL entry}}")
        self.assertEqual(find_citing_entry(read_bibliography(data),
                                           [_build_work("arxiv:2006.11098v1", title="Recorded legacy title")]),
                         (True, "legacy"))

    def test_plain_text_arxiv_tokens_require_an_arxiv_context(self):
        cases = {b"See arXiv:2609.14520.": True, b"See 10.9999/x:2609.14520.": False}
        for data, cited in cases.items():
            with self.subTest(data=data):
                self.assertEqual(find_citing_entry(read_bibliography(data),
                                                   [_build_work("arxiv:2609.14520v1", title="Recorded text title")]),
                                 (cited, None))

    def test_plain_text_arxiv_urls_and_dois_strip_trailing_punctuation(self):
        references = ("https://arxiv.org/abs/2609.14520", "arxiv.org/abs/2609.14520",
                      "https://doi.org/10.48550/arxiv.2609.14520", "10.48550/arxiv.2609.14520")
        for reference in references:
            for punctuation in (".", ",", ";", ":", ")", "]", ">", ")."):
                data = ("[3] " + reference + punctuation).encode()
                for identifier in ("arxiv:2609.14520v1", "doi:10.48550/arxiv.2609.14520"):
                    with self.subTest(identifier=identifier, reference=reference, punctuation=punctuation):
                        self.assertEqual(find_citing_entry(read_bibliography(data),
                                                           [_build_work(identifier, title="Recorded paper title")]), (True, None))

    def test_versioned_or_malformed_datacite_dois_do_not_contribute_arxiv_tokens(self):
        for suffix in ("2609.14520v1", "2609.14520-extra", "not-an-id"):
            with self.subTest(suffix=suffix):
                doi = "10.48550/arxiv." + suffix
                self.assertEqual(find_citation_tokens(_build_work("doi:" + doi)), [doi])

    def test_bibtex_entries_keep_file_order_and_their_titles(self):
        bibliography = read_bibliography(
            b"@article{zeta2020, title={A {Nested}  Title}, doi={10.1103/PhysRevB.105.085410}}\n"
            b"@misc{alpha2021,\n  eprint={2112.09662v2},\n  archivePrefix={arXiv}\n}\n"
            b"@comment{ignored, doi={10.1/ignored}}\n")
        self.assertEqual(list(bibliography["entries"]), ["zeta2020", "alpha2021"])
        self.assertEqual(bibliography["entries"]["zeta2020"]["title"], "a nested title")

    def test_bibliography_entries_store_whole_identifiers_without_their_heads(self):
        bibliography = read_bibliography(
            b"@article{arxiv:2601.00001, title={Another publication}, doi={10.1234/AbC},"
            b" note={arXiv:math.GT/0309136v2}, eprint={2609.14520v3}}\n"
            b"@article{10.1234/key-only, title={A separate publication}}")
        self.assertEqual(bibliography["entries"]["arxiv:2601.00001"]["identifiers"],
                         {"doi:10.1234/abc", "arxiv:math.gt/0309136", "arxiv:2609.14520"})
        self.assertEqual(bibliography["entries"]["10.1234/key-only"]["identifiers"], set())

    def test_plain_text_bibliographies_store_doi_and_arxiv_family_identifiers(self):
        bibliography = read_bibliography(
            b"Another publication, https://dx.doi.org/10.48550%2FarXiv.2609.14520.\n"
            b"Other publication, math.GT/0309136v2 and doi:10.1234/AbC.")
        self.assertEqual(bibliography["identifiers"],
                         {"doi:10.48550/arxiv.2609.14520", "arxiv:2609.14520", "arxiv:math.gt/0309136",
                          "doi:10.1234/abc"})

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
                 "doi then pdf in a publisher url no longer cites by identifier":
                     (b"@article{a, url={https://x.org/10.1063/1.365928/pdf}}", False),
                 "doi dotted extension": (b"@article{a, doi={10.1063/1.365928.5}}", False)}
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

    def test_a_reason_for_a_work_cited_through_its_datacite_arxiv_doi_is_refused(self):
        identifier = "doi:10.48550/arxiv.2609.14520"
        records = _build_fulltext_records({identifier: ([], "Recorded paper title")})
        bibliography = b"@misc{arxiv-only, note={arXiv:2609.14520}, title={Another publication}}"
        with self.assertRaises(ResearchError) as raised:
            account_citations(records, bibliography, [{"work_id": identifier, "reason": "Model choice only."}])
        self.assertEqual(raised.exception.code, "invalid_citation_accounting")
        self.assertEqual(raised.exception.details, {"index": 0, "cause": "already_cited",
                                                    "work_id": identifier, "key": "arxiv-only"})

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
