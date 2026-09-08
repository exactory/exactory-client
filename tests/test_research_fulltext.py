"""Extraction boundaries use authored executables, not a system PDF dependency."""

import sys
import tempfile
import unittest
from pathlib import Path

from research_harness.errors import ResearchError
from research_harness.fulltext import PdfExtractor, extract


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
        self.assertEqual(result, {"status": "extracted", "text": "The complete authored extraction.\n"})

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
