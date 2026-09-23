"""OCR boundaries with authored external-tool executables."""

import sys
import tempfile
import unittest
from pathlib import Path

from research_harness.errors import ResearchError
from research_harness.fulltext import extract
from research_harness.ocr import OcrPdfExtractor


class OcrTests(unittest.TestCase):
    def create_extractor(self, *, pages=2, render_code=None, ocr_code=None, **kwargs):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        codes = {
            "pdfinfo": "print('Pages: %d')\n" % pages,
            "pdftoppm": render_code or "import sys\nsys.stdout.buffer.write(b'P5\\n1 1\\n255\\n\\xff')\n",
            "tesseract": ocr_code or "import pathlib, sys\nassert pathlib.Path(sys.argv[1]).read_bytes().startswith(b'P5')\nprint('Recognized page text.')\n",
        }
        executables = {}
        for name, code in codes.items():
            executable = root / name
            executable.write_text("#!" + sys.executable + "\nimport sys\n"
                "if sys.argv[1:] in (['-v'], ['--version']):\n"
                "    print('%s version 1.2.3'); raise SystemExit(0)\n" % name + code)
            executable.chmod(0o700)
            executables[name] = str(executable)
        return OcrPdfExtractor(executables=executables, **kwargs)

    def test_complete_ocr_records_actual_tools_and_every_pdf_page(self):
        extractor = self.create_extractor()
        result = extract(b"%PDF-1.4\n%%EOF", "application/pdf", extractor=extractor)
        self.assertEqual(result["status"], "extracted")
        self.assertEqual(result["text"], "Recognized page text.\n\fRecognized page text.\n\f")
        self.assertEqual(result["extractor"], "pdftoppm+tesseract")
        self.assertIn("tesseract version 1.2.3", result["version"])
        self.assertTrue(result["options"]["ocr"])
        self.assertTrue(result["visual_inspection_required"])

    def test_missing_oversized_and_incomplete_ocr_remain_pending(self):
        self.assertEqual(OcrPdfExtractor(executables={})(b"PDF")["status"], "extractor_missing")
        for extractor, expected in (
                (self.create_extractor(pages=201), "extraction_too_large"),
                (self.create_extractor(render_code="import sys\nsys.stdout.write('x' * 10000)\n", max_image_bytes=128), "extraction_too_large"),
                (self.create_extractor(ocr_code="print('x' * 10000)\n", max_text_bytes=128), "extraction_too_large"),
                (self.create_extractor(ocr_code="import signal\nsignal.pause()\n", timeout=0.5), "extraction_timeout"),
                (self.create_extractor(ocr_code="raise SystemExit(1)\n"), "extraction_failed"),
                (self.create_extractor(ocr_code="print('   ')\n"), "empty_text")):
            with self.subTest(expected=expected):
                result = extractor(b"PDF")
                self.assertEqual(result["status"], expected)
                self.assertIsNone(result["text"])

    def test_unbounded_or_invalid_ocr_configuration_is_rejected(self):
        for settings in ({"timeout": float("inf")}, {"timeout": 0}, {"max_pages": 0},
                         {"max_text_bytes": -1}, {"max_image_bytes": -1}, {"max_dimension": 0}):
            with self.subTest(settings=settings), self.assertRaises(ResearchError):
                OcrPdfExtractor(**settings)


if __name__ == "__main__":
    unittest.main()
