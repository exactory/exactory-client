"""JATS originals retain source text without fetching or expanding XML dependencies."""

import copy
import tempfile
import unittest
from pathlib import Path

from research_harness.acquisition import acquire_fulltext, acquire_work
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.fulltext import extract
from research_harness.source_links import span_locator, validate_link
from research_harness.storage import Store
from research_fixtures import client, json_response, openalex


FIXTURE = Path(__file__).parent / "fixtures/research/jats-article.xml"
MEDIA_TYPES = ("application/xml", "text/xml")
BODY = "An authored result for an α-oxide device with 2 × 3 sites & no gap."


class JatsExtractionTests(unittest.TestCase):
    def test_saved_jats_retains_abstract_front_headings_references_and_graphic_names(self):
        for media_type in MEDIA_TYPES:
            with self.subTest(media_type=media_type):
                result = extract(FIXTURE.read_bytes(), media_type)
                self.assertEqual(result["status"], "extracted")
                self.assertTrue(result["includes_abstract"])
                self.assertEqual(result["extractor"], "jats")
                self.assertEqual(result["media_type"], media_type)
                self.assertEqual(result["format_detection"], "reported_media_type")
                self.assertTrue(result["visual_inspection_required"])
                self.assertEqual(result["visual_asset_names"], ["fig-1.tif", "fig-2.png"])
                for text in (BODY, "An authored abstract sentence.", "An authored JATS article.", "A. Author",
                             "Authored metadata.", "Results and Discussion", "Conclusion", "References",
                             "An authored reference entry.", "An authored figure caption."):
                    self.assertIn(text, result["text"])
                self.assertLess(result["text"].index("Conclusion"), result["text"].index("References"))

    def test_xml_media_spellings_extract_the_same_text_without_rewriting_the_reported_type(self):
        raw = FIXTURE.read_bytes()
        baseline = extract(raw, "application/xml")
        self.assertEqual(baseline["status"], "extracted")
        self.assertEqual(baseline["extractor"], "jats")
        self.assertIn(BODY, baseline["text"])
        for media_type in ("application/xml; charset=utf-8", "Text/XML"):
            with self.subTest(media_type=media_type):
                result = extract(raw, media_type)
                self.assertEqual(result["media_type"], media_type)
                self.assertEqual(result["status"], "extracted")
                self.assertEqual(result["extractor"], "jats")
                self.assertEqual(result["text"], baseline["text"])
                self.assertEqual(result["format_detection"], "reported_media_type")

    def test_default_and_prefixed_namespaces_match_all_jats_local_names(self):
        source = ('<article><front><abstract><p>A summary.</p></abstract></front><body><sec>'
                  '<title>Results</title><p>A body.</p><fig><graphic href="figure.png"/></fig>'
                  '</sec></body></article>')
        variants = [source.replace('<article>', '<article xmlns="urn:authored:jats">'),
                    source.replace('<', '<j:').replace('<j:/', '</j:')
                    .replace('<j:article>', '<j:article xmlns:j="urn:authored:jats">')]
        for variant in variants:
            with self.subTest(variant=variant):
                result = extract(variant.encode(), "application/xml")
                self.assertEqual(result["status"], "extracted")
                self.assertTrue(result["includes_abstract"])
                self.assertIn("Results\n", result["text"])
                self.assertIn("A body.", result["text"])
                self.assertEqual(result["visual_asset_names"], ["figure.png"])

    def test_inline_text_and_paragraph_boundaries_preserve_exact_readable_spans(self):
        data = (b'<article><body><p>Mixed <bold>inline</bold> text preserves H<sub>2</sub>O '
                b'and x<sup>2</sup>.</p><p>Next paragraph.</p></body></article>')
        result = extract(data, "text/xml")
        self.assertEqual(result["status"], "extracted")
        self.assertIn("Mixed inline text preserves H2O and x2.", result["text"])
        self.assertRegex(result["text"], r"x2\.\n+Next paragraph\.")

    def test_abstract_presence_requires_saved_nonheading_content(self):
        for abstract, included in (("", False), ("<abstract/>", False),
                                   ("<abstract><title>Abstract</title> </abstract>", False),
                                   ("<abstract><!-- An absent abstract. --></abstract>", False),
                                   ("<abstract><p>A summary.</p></abstract>", True),
                                   ("<trans-abstract><p>A translated summary.</p></trans-abstract>", True)):
            with self.subTest(abstract=abstract):
                data = '<article><front>' + abstract + '</front><body><p>A body.</p></body></article>'
                result = extract(data.encode(), "application/xml")
                self.assertEqual(result["status"], "extracted")
                self.assertIs(result["includes_abstract"], included)

    def test_front_abstract_headings_and_references_cannot_certify_body_content(self):
        excluded = ('', '<title><p>Only a heading.</p></title>',
                    '<abstract><p>Only an abstract.</p></abstract>',
                    '<article-meta><p>Only metadata.</p></article-meta>',
                    '<front><p>Only front matter.</p></front>',
                    '<ref-list><ref><p>Only a reference.</p></ref></ref-list>',
                    '<back><p>Only back matter.</p></back>', '<fig><graphic href="figure.png"/></fig>')
        for content in excluded:
            with self.subTest(content=content):
                data = ('<article><front><abstract><p>A summary.</p></abstract><p>Front text.</p></front>'
                        '<body>' + content + '</body><back><p>Back text.</p></back></article>')
                result = extract(data.encode(), "application/xml")
                self.assertEqual(result["status"], "landing_page")
                self.assertIsNone(result["text"])
                self.assertIsNone(result["includes_abstract"])
        result = extract(b'<article><front><abstract><p>A summary.</p></abstract></front></article>', 'text/xml')
        self.assertEqual(result["status"], "landing_page")

    def test_lists_and_table_cells_are_substantive_body_content(self):
        for content in ('<list><list-item>A substantive list item.</list-item></list>',
                        '<table-wrap><table><tr><td>A substantive table cell.</td></tr></table></table-wrap>'):
            with self.subTest(content=content):
                result = extract(('<article><body>' + content + '</body></article>').encode(), "application/xml")
                self.assertEqual(result["status"], "extracted")
                self.assertFalse(result["includes_abstract"])

    def test_malformed_xml_never_returns_partial_text_or_graphic_names(self):
        for data in (b'', b'<article><body><p>Unclosed.', b'<article><body><p>Mismatch.</body></article>',
                     b'<article><body><p>Valid.</p></body></article><other/>',
                     b'<article><body><p>&undefined;</p></body></article>',
                     b'<article><body><p>Invalid \xff encoding.</p></body></article>'):
            with self.subTest(data=data):
                result = extract(data, "application/xml")
                self.assertEqual(result["status"], "malformed_xml")
                self.assertIsNone(result["text"])
                self.assertNotIn("visual_asset_names", result)

    def test_xml_declared_encodings_are_decoded_before_saving_utf8_text(self):
        for encoding in ("utf-16", "utf-16-be", "iso-8859-1"):
            with self.subTest(encoding=encoding):
                declaration = "UTF-16" if encoding.startswith("utf-16") else encoding
                data = ('<?xml version="1.0" encoding="' + declaration + '"?>'
                        '<article><body><p>A café result.</p></body></article>').encode(encoding)
                result = extract(data, "application/xml")
                self.assertEqual(result["status"], "extracted")
                self.assertEqual(result["text"], "A café result.")

    def test_internal_general_and_parameter_entities_are_refused_before_expansion(self):
        declarations = ('<!ENTITY a "expanded text">', '<!ENTITY % p "expanded text">',
                        '<!ENTITY a "x"><!ENTITY b "&a;&a;&a;"><!ENTITY c "&b;&b;&b;">')
        for declaration in declarations:
            for encoding in ("utf-8", "utf-16", "utf-16-be"):
                with self.subTest(declaration=declaration, encoding=encoding):
                    data = ('<!DOCTYPE article [' + declaration + ']>'
                            '<article><body><p>No entity reference is needed.</p></body></article>')
                    result = extract(data.encode(encoding), "application/xml")
                    self.assertEqual(result["status"], "xml_internal_entity")
                    self.assertIsNone(result["text"])
                    self.assertIsNone(result["includes_abstract"])

    def test_external_doctype_is_ignored_without_reading_its_declarations(self):
        with tempfile.TemporaryDirectory() as directory:
            dtd = Path(directory) / "article.dtd"
            dtd.write_text('<!ENTITY injected "External content must never be read.">', encoding="utf-8")
            for doctype in ('SYSTEM "' + dtd.as_uri() + '"',
                            'PUBLIC "-//Authored JATS//EN" "missing-article.dtd"',
                            '[<!ATTLIST article source CDATA "authored">'
                            '<!-- <!ENTITY fake "comment"> -->]'):
                with self.subTest(doctype=doctype):
                    data = ('<!DOCTYPE article ' + doctype + '>'
                            '<article><body><p>Self-contained &#945; &amp; text.</p></body></article>')
                    result = extract(data.encode(), "text/xml")
                    self.assertEqual(result["status"], "extracted")
                    self.assertEqual(result["text"], "Self-contained α & text.")

    def test_predefined_entity_redeclarations_are_still_internal_entity_declarations(self):
        for name, value in (("amp", "&#38;"), ("quot", "&#34;"), ("apos", "&#39;")):
            with self.subTest(name=name):
                data = ('<!DOCTYPE article [<!ENTITY ' + name + ' "' + value + '">]>'
                        '<article><body><p>A body.</p></body></article>')
                result = extract(data.encode(), "application/xml")
                self.assertEqual(result["status"], "xml_internal_entity")
                self.assertIsNone(result["text"])

    def test_internal_declarations_after_skipped_parameter_entities_are_refused(self):
        for encoding in ("utf-8", "utf-16", "utf-16-be"):
            with self.subTest(encoding=encoding):
                data = ('<!DOCTYPE article [<!ENTITY % p SYSTEM "unused.dtd">%p;<!ENTITY a "x">]>'
                        '<article><body><p>A body.</p></body></article>')
                result = extract(data.encode(encoding), "application/xml")
                self.assertEqual(result["status"], "xml_internal_entity")
                self.assertIsNone(result["text"])

    def test_external_and_skipped_entity_references_stay_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "private.txt"
            secret.write_text("Private content must never be read.", encoding="utf-8")
            declarations = ('[<!ENTITY remote SYSTEM "' + secret.as_uri() + '">]',
                            'SYSTEM "missing-article.dtd"')
            for declaration in declarations:
                with self.subTest(declaration=declaration):
                    data = ('<!DOCTYPE article ' + declaration + '>'
                            '<article><body><p>Before &remote; after.</p></body></article>')
                    result = extract(data.encode(), "application/xml")
                    self.assertEqual(result["status"], "xml_external_entity")
                    self.assertIsNone(result["text"])

    def test_entity_like_comments_and_cdata_are_text_not_declarations(self):
        data = (b'<!-- <!ENTITY fake "comment"> --><article><body>'
                b'<p><![CDATA[Literal <!ENTITY fake "data">]]></p></body></article>')
        result = extract(data, "application/xml")
        self.assertEqual(result["status"], "extracted")
        self.assertEqual(result["text"], 'Literal <!ENTITY fake "data">')


class JatsAcquisitionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = Store(self.root, create=True)
        self.artifacts = ArtifactStore(self.root)
        http, _, _ = client([json_response(openalex())])
        acquire_work(self.store, "W123", request_id="metadata", expected_revision=0, http=http)
        self.version = "openalex:W123"
        self.url = "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13104172/fullTextXML"

    def acquire(self, data, media_type="application/xml", request_id="jats"):
        http, _, _ = client([(200, {"Content-Type": media_type}, data)], max_retries=0)
        return acquire_fulltext(self.store, self.version, self.url, http=http,
                                request_id=request_id, expected_revision=self.store.revision)

    def test_xml_capture_preserves_original_provenance_and_validates_a_body_span(self):
        for index, media_type in enumerate(MEDIA_TYPES):
            with self.subTest(media_type=media_type):
                raw = FIXTURE.read_bytes()
                result = self.acquire(raw, media_type + "; charset=utf-8", "xml-" + str(index))
                self.assertEqual(result["status"], "complete")
                capture = result["capture"]
                self.assertEqual(capture["extraction_status"], "extracted")
                self.assertTrue(capture["includes_abstract"])
                self.assertTrue(capture["visual_inspection_required"])
                self.assertEqual(capture["visual_asset_names"], ["fig-1.tif", "fig-2.png"])
                self.assertEqual(capture["original"]["media_type"], media_type)
                self.assertEqual(self.artifacts.read(capture["original"]), raw)
                self.assertEqual(capture["extraction"]["extractor"], "jats")
                self.assertEqual(capture["extraction"]["format_detection"], "reported_media_type")
                content = self.artifacts.read(capture["text"]).decode("utf-8")
                start = content.index(BODY)
                link = {"version_id": self.version, "source_id": capture["source_id"],
                        "artifact": capture["text"], "locator": span_locator(content, start, start + len(BODY))}
                records = self.store.snapshot()["records"]
                self.assertEqual(validate_link(records, self.artifacts, link)["value"], BODY)
                link["locator"]["sha256"] = "0" * 64
                with self.assertRaises(ResearchError) as raised:
                    validate_link(records, self.artifacts, link)
                self.assertEqual(raised.exception.code, "invalid_locator")
                self.assertNotIn("reading", records)

    def test_unsafe_and_malformed_captures_keep_the_original_and_explicit_pending_reason(self):
        cases = [(b'<!DOCTYPE article [<!ENTITY a "x">]><article><body><p>A body.</p></body></article>',
                  "xml_internal_entity"), (b'<article><body><p>Unclosed.', "malformed_xml"),
                 (b'<article><front><abstract><p>A summary.</p></abstract></front></article>', "landing_page")]
        for index, (raw, reason) in enumerate(cases):
            with self.subTest(reason=reason):
                result = self.acquire(raw, request_id="pending-" + str(index))
                self.assertEqual(result["pending"], [{"code": reason}])
                self.assertEqual(result["capture"]["extraction_status"], reason)
                self.assertEqual(result["capture"]["availability"], "pending")
                self.assertIsNone(result["capture"]["text"])
                self.assertEqual(self.artifacts.read(result["capture"]["original"]), raw)

    def test_nonarticle_xml_root_cannot_be_promoted_by_a_nested_article(self):
        for index, raw in enumerate((b'<feed><article><body><p>A body.</p></body></article></feed>',
                                     b'<Article><body><p>A body.</p></body></Article>')):
            with self.subTest(raw=raw):
                result = self.acquire(raw, request_id="nonarticle-" + str(index))
                self.assertEqual(result["pending"], [{"code": "unsupported_fulltext"}])
                self.assertIsNone(result["capture"]["text"])

    def test_pre_xml_capture_and_operation_replay_remain_unchanged_after_new_capture(self):
        legacy_http, _, _ = client([(200, {"Content-Type": "application/xml"}, FIXTURE.read_bytes())])
        original_get = legacy_http.get

        def pre_xml_get(url, **kwargs):
            kwargs["accept"] = ("application/pdf", "text/html", "application/xhtml+xml", "application/octet-stream")
            return original_get(url, **kwargs)

        legacy_http.get = pre_xml_get
        old = acquire_fulltext(self.store, self.version, self.url, http=legacy_http,
                               request_id="before-xml", expected_revision=self.store.revision)
        self.assertEqual(old["pending"], [{"code": "unexpected_mime"}])
        saved = copy.deepcopy(old["capture"])
        self.assertNotIn("visual_asset_names", saved)
        fresh = self.acquire(FIXTURE.read_bytes(), request_id="after-xml")
        self.assertEqual(fresh["status"], "complete")
        reopened = Store(self.root)
        no_requests, wire, _ = client([])
        replay = acquire_fulltext(reopened, self.version, self.url, http=no_requests,
                                  request_id="before-xml", expected_revision=0)
        self.assertEqual(replay, old)
        self.assertEqual(wire.requests, [])
        captures = reopened.snapshot()["records"]["work"][self.version]["fulltexts"]
        self.assertEqual(captures[0], saved)
        self.assertEqual(captures[1], fresh["capture"])


if __name__ == "__main__":
    unittest.main()
