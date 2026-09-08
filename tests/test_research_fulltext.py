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


if __name__ == "__main__":
    unittest.main()
