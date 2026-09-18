"""Generic binary PDFs retain provenance and still require actual extraction."""

import copy
import tempfile
import unittest
from pathlib import Path

from research_harness.acquisition import acquire_fulltext, acquire_work
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.source_links import validate_link
from research_harness.storage import Store
from research_fixtures import atom, entry, client, xml_response


def authored_pdf():
    """Build a small valid, one-page PDF without an external document dependency."""
    stream = b"BT /F1 12 Tf 72 720 Td (An authored full text for acquisition testing.) Tj ET\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"endstream",
    ]
    body, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, value in enumerate(objects, 1):
        offsets.append(len(body))
        body.extend(str(number).encode() + b" 0 obj\n" + value + b"\nendobj\n")
    xref = len(body)
    body.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets:
        body.extend(f"{offset:010d} 00000 n \n".encode())
    body.extend(b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n")
    return bytes(body)


AUTHORED_PDF_TEXT = "An authored full text for acquisition testing.\n\f"


def extract_authored_pdf(data):
    """Stand in for pdftotext on the authored document, so no test needs the system tool."""
    return {"status": "extracted", "text": AUTHORED_PDF_TEXT, "extractor": "pdftotext",
            "version": None, "options": {"layout": True}}


class BinaryPdfTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = Store(self.root, create=True)
        http, _, _ = client([xml_response(atom([entry()], total=1))])
        acquire_work(self.store, "2601.00001v1", request_id="metadata", expected_revision=0, http=http)

    def acquire(self, body, headers=None, request_id="binary", extractor=None, **kwargs):
        headers = {"Content-Type": "application/octet-stream"} if headers is None else headers
        http, _, _ = client([(200, headers, body)])
        return acquire_fulltext(self.store, "2601.00001v1", "https://arxiv.org/pdf/2601.00001v1",
                                request_id=request_id, expected_revision=self.store.revision,
                                http=http, max_requests=1, extractor=extractor or extract_authored_pdf, **kwargs)

    def test_binary_pdf_is_parsed_without_rewriting_reported_media(self):
        original = authored_pdf()
        result = self.acquire(original)
        self.assertEqual(result["status"], "complete")
        capture = result["capture"]
        records = self.store.snapshot()["records"]
        source = records["source"][capture["source_id"]]
        self.assertEqual(source["headers"]["content-type"], "application/octet-stream")
        self.assertEqual(ArtifactStore(self.root).read(capture["original"]), original)
        self.assertIn(b"An authored full text for acquisition testing.", ArtifactStore(self.root).read(capture["text"]))
        self.assertEqual(capture["extraction"]["media_type"], "application/pdf")
        self.assertEqual(capture["extraction"]["format_detection"], "pdf_signature_from_generic_binary")
        self.assertEqual(result["attempts_used"], 1)
        self.assertNotIn("reading", records)

    def test_missing_media_declaration_retains_missing_header(self):
        result = self.acquire(authored_pdf(), headers={})
        self.assertEqual(result["status"], "complete")
        source = self.store.snapshot()["records"]["source"][result["capture"]["source_id"]]
        self.assertNotIn("content-type", source["headers"])

    def test_non_pdf_binary_and_binary_html_stay_unsupported(self):
        for index, body in enumerate((b"unrelated binary content\x00", b"<article class='article-body'><p>A body.</p></article>")):
            with self.subTest(index=index):
                result = self.acquire(body, request_id=f"unsupported-{index}")
                self.assertEqual(result["pending"], [{"code": "unsupported_fulltext"}])
                self.assertIsNone(result["capture"]["text"])

    def test_signature_does_not_accept_malformed_pdf(self):
        """A document without its trailer is refused before extraction; a corrupt one is refused by the parser."""
        for index, (body, extractions) in enumerate(((b"%PDF-1.4\ntruncated", 0),
                                                     (b"%PDF-1.4\nnot a valid document\n%%EOF", 1))):
            with self.subTest(index=index):
                attempted = []

                def refuse_as_malformed(data, seen=attempted):
                    seen.append(data)
                    return {"status": "malformed_pdf", "text": None}

                result = self.acquire(body, request_id=f"malformed-{index}", extractor=refuse_as_malformed)
                self.assertEqual(result["pending"], [{"code": "malformed_pdf"}])
                self.assertIsNone(result["capture"]["text"])
                self.assertEqual(len(attempted), extractions)

    def test_explicit_non_generic_media_remains_rejected(self):
        result = self.acquire(authored_pdf(), headers={"Content-Type": "text/plain"})
        self.assertEqual(result["pending"], [{"code": "unexpected_mime"}])
        self.assertIsNone(result["capture"]["text"])

    def test_pdf_parser_failure_remains_pending(self):
        for index, status in enumerate(("extraction_timeout", "empty_text", "extractor_missing", "extraction_too_large")):
            with self.subTest(status=status):
                result = self.acquire(authored_pdf(), request_id=f"parser-{index}",
                                      extractor=lambda data, status=status: {"status": status, "text": None})
                self.assertEqual(result["pending"], [{"code": status}])
                self.assertIsNone(result["capture"]["text"])

    def test_later_success_preserves_prior_failed_capture_and_replay(self):
        failed = self.acquire(b"%PDF-1.4\ntruncated", request_id="failed")
        result = self.acquire(authored_pdf(), request_id="success")
        self.assertEqual(result["status"], "complete")
        records = self.store.snapshot()["records"]
        captures = records["work"]["arxiv:2601.00001v1"]["fulltexts"]
        self.assertEqual(captures, [failed["capture"], result["capture"]])
        http, wire, _ = client([])
        replay = acquire_fulltext(self.store, "2601.00001v1", "https://arxiv.org/pdf/2601.00001v1",
                                  request_id="success", expected_revision=0, http=http, max_requests=1)
        self.assertEqual(replay, result)
        self.assertEqual(wire.requests, [])
        self.assertNotIn("reading", records)

    def test_parsed_binary_pdf_supports_bounded_visual_locators(self):
        capture = self.acquire(authored_pdf())["capture"]
        records = self.store.snapshot()["records"]
        link = {"version_id": "arxiv:2601.00001v1", "source_id": capture["source_id"],
                "artifact": capture["original"],
                "locator": {"kind": "pdf", "page_index": 0, "printed_page": "1", "region": [0, 0, 1, 1]}}
        try:
            context = validate_link(records, ArtifactStore(self.root), link)
        except ResearchError as error:
            self.fail(f"A successfully parsed binary PDF needs its visual locator: {error.code}")
        self.assertEqual(context["capture"], capture)
        for locator in (
            {"kind": "pdf", "page_index": 1, "printed_page": "2", "region": [0, 0, 1, 1]},
            {"kind": "pdf", "page_index": 0, "printed_page": "1", "region": [0, 0, 2, 1]},
        ):
            with self.subTest(locator=locator):
                with self.assertRaises(ResearchError) as failure:
                    validate_link(records, ArtifactStore(self.root), dict(link, locator=locator))
                self.assertEqual(failure.exception.code, "invalid_locator")

    def test_generic_binary_visual_needs_its_successful_pdf_extraction(self):
        capture = self.acquire(authored_pdf())["capture"]
        link = {"version_id": "arxiv:2601.00001v1", "source_id": capture["source_id"],
                "artifact": capture["original"],
                "locator": {"kind": "pdf", "page_index": 0, "printed_page": None, "region": [0, 0, 1, 1]}}
        for change in ("no-inference", "wrong-format", "no-text", "pending-extraction"):
            with self.subTest(change=change):
                records = copy.deepcopy(self.store.snapshot()["records"])
                altered = records["work"]["arxiv:2601.00001v1"]["fulltexts"][-1]
                if change == "no-inference":
                    altered["extraction"].pop("format_detection")
                elif change == "wrong-format":
                    altered["extraction"]["media_type"] = "text/html"
                elif change == "no-text":
                    altered["text"] = None
                else:
                    altered["extraction_status"] = "malformed_pdf"
                with self.assertRaises(ResearchError) as failure:
                    validate_link(records, ArtifactStore(self.root), link)
                self.assertEqual(failure.exception.code, "invalid_locator")


if __name__ == "__main__":
    unittest.main()
