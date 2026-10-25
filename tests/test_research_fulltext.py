"""Extraction boundaries use authored executables, not a system PDF dependency."""

import sys
import tempfile
import unittest
from pathlib import Path

from research_harness.errors import ResearchError
from research_harness.fulltext import PdfExtractor, _Article, extract


class FulltextTests(unittest.TestCase):
    def extractor(self, code, **kwargs):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        executable = Path(directory.name) / "authored-pdftotext"
        executable.write_text("#!" + sys.executable + "\n" + code)
        executable.chmod(0o700)
        return PdfExtractor(executable=str(executable), **kwargs)

    def test_extraction_invokes_explicit_utf8_layout_argv_and_captures_bounded_text(self):
        extractor = self.extractor(
            "import pathlib, sys\n"
            "assert sys.argv[1:4] == ['-enc', 'UTF-8', '-layout']\n"
            "assert pathlib.Path(sys.argv[4]).read_bytes() == b'Authored PDF bytes'\n"
            "assert sys.argv[5] == '-'\n"
            "sys.stdout.write('The complete authored extraction.\\n')\n")
        result = extractor(b"Authored PDF bytes")
        self.assertEqual(result, {"status": "extracted", "text": "The complete authored extraction.\n",
                                  "extractor": "pdftotext", "version": None, "options": {"layout": True}})

    def test_extraction_measures_describe_layout_padding(self):
        from research_harness.fulltext import extraction_measures
        text = "a" + " " * 999 + "\n\fb\n"
        measures = extraction_measures(text, original_size=100)
        self.assertEqual(measures["page_count"], 2)
        self.assertEqual(measures["max_line_length"], 1000)
        self.assertGreater(measures["whitespace_fraction"], 0.99)
        self.assertAlmostEqual(measures["expansion_ratio"], len(text.encode()) / 100)
        self.assertEqual(extraction_measures("", 0), {"text_bytes": 0, "page_count": 1, "max_line_length": 0,
                                                     "whitespace_fraction": 0.0, "expansion_ratio": None})

    def test_pdf_extractor_records_options_and_version(self):
        code = ("import sys\n"
                "if sys.argv[1:] == ['-v']:\n"
                "    sys.stderr.write('pdftotext version 9.9\\n'); raise SystemExit(0)\n"
                "assert '-layout' not in sys.argv\n"
                "sys.stdout.write('Plain reading order.\\n')\n")
        extractor = self.extractor(code, layout=False)
        self.assertEqual(extractor.version(), "9.9")
        result = extractor(b"Authored PDF bytes")
        self.assertEqual(result["status"], "extracted")
        self.assertEqual((result["extractor"], result["version"], result["options"]), ("pdftotext", "9.9", {"layout": False}))
        with self.assertRaises(ResearchError):
            PdfExtractor(layout="yes")
        self.assertIsNone(self.extractor("raise SystemExit(1)\n").version())

    def test_subprocess_timeout_and_oversized_output_are_pending(self):
        hanging = self.extractor("import signal\nsignal.pause()\n", timeout=0.05)
        self.assertEqual(hanging(b"PDF")["status"], "extraction_timeout")
        huge = self.extractor("import sys\nsys.stdout.write('a' * 1000000)\n", max_text_bytes=20)
        self.assertEqual(huge(b"PDF"), {"status": "extraction_too_large", "text": None})

    def test_missing_failed_and_scanned_extraction_never_claims_text(self):
        self.assertEqual(PdfExtractor(executable="/nonexistent/authored-tool")(b"PDF")["status"], "extractor_missing")
        failed = self.extractor("raise SystemExit(1)\n")
        self.assertEqual(failed(b"PDF")["status"], "malformed_pdf")
        scanned = self.extractor("print('   ')\n")
        self.assertEqual(scanned(b"PDF")["status"], "empty_text")

    def test_unbounded_extraction_configuration_is_rejected(self):
        for settings in ({"timeout": None}, {"timeout": float("inf")}, {"max_text_bytes": -1}):
            with self.assertRaises(ResearchError):
                PdfExtractor(**settings)

    def test_boolean_html_attribute_and_unclosed_article_never_certify_fulltext(self):
        for raw in (b"<article class><h1>A title</h1></article>", b"<article class='article-body'><p>An unfinished body"):
            with self.subTest(raw=raw):
                result = extract(raw, "text/html")
                self.assertNotEqual(result["status"], "extracted")
                self.assertIsNone(result["text"])

    def test_binary_octet_stream_requires_a_pdf_signature_for_extraction(self):
        pdf = b"%PDF-1.4\nAn authored synthetic PDF.\n%%EOF\n"
        for media in ("application/octet-stream", "binary/octet-stream"):
            with self.subTest(media=media):
                result = extract(pdf, media, extractor=lambda data: {"status": "extracted", "text": "A PDF page.\f"})
                self.assertEqual(result["status"], "extracted")
                self.assertEqual(result["text"], "A PDF page.\f")
                self.assertEqual(result["media_type"], "application/pdf")
                self.assertEqual(result["format_detection"], "pdf_signature_from_generic_binary")
            for body in (b"unrelated binary content\x00", b"<article class='article-body'><p>A body.</p></article>"):
                with self.subTest(media=media, body=body):
                    result = extract(body, media)
                    self.assertEqual(result["status"], "unsupported_fulltext")
                    self.assertIsNone(result["text"])
                    self.assertEqual(result["media_type"], media)
                    self.assertEqual(result["format_detection"], "reported_media_type")

    def test_pmc_main_article_body_retains_references_without_counting_them_as_body(self):
        data = (Path(__file__).parent / "fixtures/research/pmc-main-article-body.html").read_bytes()
        for media in ("text/html", "application/xhtml+xml"):
            with self.subTest(media=media):
                result = extract(data, media)
                self.assertEqual(result["status"], "extracted")
                self.assertTrue(result["includes_abstract"])
                for expected in ("2. Results and Discussion", "3. Conclusion", "An authored result sentence.",
                                 "An authored conclusion sentence.", "An authored abstract sentence."):
                    self.assertIn(expected, result["text"])
                self.assertIn("References", result["text"])
                self.assertIn("An authored reference entry.", result["text"])
                self.assertLess(result["text"].index("An authored conclusion sentence."),
                                result["text"].index("References"))
                self.assertLess(result["text"].index("References"),
                                result["text"].index("An authored reference entry."))
        parser = _Article()
        parser.feed(data.decode())
        parser.close()
        body = "".join(parser.body_text)
        self.assertIn("An authored result sentence.", body)
        for excluded in ("An authored abstract sentence.", "Keywords:", "An authored author and affiliation.",
                         "Results and Discussion", "Conclusion", "References", "An authored reference entry.",
                         "An authored publisher notice."):
            self.assertNotIn(excluded, body)
        # Body markers alone must not promote abstracts, headings, metadata or references to full text.
        for content in (b'<section class="abstract"><h2>Abstract</h2><p>Only an abstract.</p></section>',
                        b'<h2 class="pmc_sec_title">A section heading only.</h2>',
                        b'<header><p>Only metadata.</p></header><div class="article-meta"><p>A date.</p></div>',
                        b'<section class="ref-list"><ol class="ref-list"><li>A reference only.</li></ol></section>'):
            for classes in (b"body main-article-body", b"article-body"):
                raw = b'<article><section class="' + classes + b'">' + content + b'</section></article>'
                with self.subTest(classes=classes, content=content):
                    result = extract(raw, "text/html")
                    self.assertEqual(result["status"], "landing_page")
                    self.assertIsNone(result["text"])

    def test_article_body_class_does_not_turn_title_metadata_or_abstract_into_body(self):
        pages = [
            b'<article class="article-body"><h1>An authored article title.</h1><section class="abstract">Only the original abstract.</section></article>',
            b'<article class="article__body"><header><p>A title and author.</p></header><div class="article-meta"><p>A journal and date.</p></div><section class="abstract"><p>Only the original abstract.</p></section></article>',
            b'<article class="jats_body"><h2>A section title alone.</h2></article>',
            b'<article class="article-body"><p itemprop="headline">A title.</p><p itemprop="author">An author.</p><section role="doc-abstract"><p>Only an abstract.</p></section></article>',
        ]
        for raw in pages:
            with self.subTest(raw=raw):
                result = extract(raw, "text/html")
                self.assertEqual(result["status"], "landing_page")
                self.assertIsNone(result["text"])

    def test_includes_abstract_requires_nonempty_content_in_the_saved_article_text(self):
        body = b'<article class="article-body">%s<p>The complete article body.</p></article>'
        cases = [
            (b'<div id="abstract">An external abstract.</div>' + body % b'', False, b'An external abstract.'),
            (body % b'<section class="abstract"></section>', False, None),
            (body % b'<section class="abstract"><h2>Abstract</h2> </section>', False, None),
            (body % b'<section class="abstract"><script>An omitted abstract.</script></section>', False, b'An omitted abstract.'),
            (body % b'<section class="abstract"><h2>Abstract</h2><p>The included abstract.</p></section>', True, b'The included abstract.'),
        ]
        for raw, included, abstract in cases:
            with self.subTest(included=included, raw=raw):
                result = extract(raw, "text/html")
                self.assertEqual(result["status"], "extracted")
                self.assertEqual(result["includes_abstract"], included)
                self.assertIn("The complete article body.", result["text"])
                if abstract is not None:
                    self.assertEqual(abstract.decode() in result["text"], included)


if __name__ == "__main__":
    unittest.main()
