"""Bounded OCR of complete acquired PDFs, with actual tool provenance."""

import math
import os
import re
import selectors
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .errors import ResearchError


def run_bounded(argv, limit, deadline, *, include_stderr=False):
    """Collect bounded output and always terminate a timed-out child."""
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if include_stderr else subprocess.DEVNULL)
    output = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise ResearchError("extraction_timeout", "OCR exceeded its extraction deadline")
                chunk = os.read(process.stdout.fileno(), min(65536, limit + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > limit:
                    raise ResearchError("extraction_too_large", "OCR output exceeded its byte limit")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ResearchError("extraction_timeout", "OCR exceeded its extraction deadline")
        if process.wait(timeout=remaining) != 0:
            raise ResearchError("extraction_failed", "An OCR tool did not complete successfully")
        return bytes(output)
    except subprocess.TimeoutExpired as error:
        raise ResearchError("extraction_timeout", "OCR exceeded its extraction deadline") from error
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired as error:
            raise ResearchError("extraction_cleanup_failed", "OCR process did not stop after termination") from error
        finally:
            process.stdout.close()


class OcrPdfExtractor:
    name = "pdftoppm+tesseract"

    def __init__(self, *, executables=None, timeout=600, max_pages=200,
                 max_text_bytes=32 * 1024 * 1024, max_image_bytes=32 * 1024 * 1024,
                 max_dimension=5000):
        if type(timeout) not in (float, int) or not math.isfinite(timeout) or timeout <= 0:
            raise ResearchError("invalid_input", "OCR timeout must be positive and finite")
        for value in (max_pages, max_text_bytes, max_image_bytes, max_dimension):
            if type(value) is not int or value <= 0:
                raise ResearchError("invalid_input", "OCR resource limits must be positive integers")
        names = ("pdfinfo", "pdftoppm", "tesseract")
        self.executables = {name: shutil.which(name) for name in names} if executables is None else dict(executables)
        self.timeout, self.max_pages = timeout, max_pages
        self.max_text_bytes, self.max_image_bytes = max_text_bytes, max_image_bytes
        self.max_dimension = max_dimension

    def __call__(self, data):
        options = {"ocr": True, "language": "eng", "dpi": 300, "page_segmentation_mode": 3,
                   "max_dimension": self.max_dimension, "max_pages": self.max_pages,
                   "timeout_seconds": self.timeout, "max_text_bytes": self.max_text_bytes,
                   "max_image_bytes": self.max_image_bytes}
        result = {"status": "extractor_missing", "text": None, "extractor": self.name,
                  "version": None, "options": options}
        if any(not self.executables.get(name) for name in ("pdfinfo", "pdftoppm", "tesseract")):
            return result
        deadline = time.monotonic() + self.timeout
        try:
            versions = []
            for name in ("pdfinfo", "pdftoppm", "tesseract"):
                flag = "--version" if name == "tesseract" else "-v"
                version = run_bounded([self.executables[name], flag], 65536, deadline, include_stderr=True)
                lines = version.decode("utf-8", errors="replace").splitlines()
                versions.append(name + "=" + (lines[0] if lines else "unknown"))
            result["version"] = "; ".join(versions)
            with tempfile.TemporaryDirectory(prefix="exactory-ocr-") as directory:
                original, image = Path(directory) / "source.pdf", Path(directory) / "page.pgm"
                original.write_bytes(data)
                info = run_bounded([self.executables["pdfinfo"], str(original)], 65536, deadline)
                match = re.search(rb"(?m)^Pages:\s*(\d+)\s*$", info)
                if not match or int(match.group(1)) < 1:
                    raise ResearchError("malformed_pdf", "PDF page count could not be established")
                page_count = int(match.group(1))
                if page_count > self.max_pages:
                    raise ResearchError("extraction_too_large", "PDF exceeds the OCR page limit")
                pages, size = [], 0
                for page in range(1, page_count + 1):
                    rendered = run_bounded([self.executables["pdftoppm"], "-f", str(page), "-l", str(page),
                        "-singlefile", "-gray", "-r", "300", "-scale-to", str(self.max_dimension),
                        str(original)], self.max_image_bytes, deadline)
                    if not rendered.startswith(b"P5"):
                        raise ResearchError("extraction_failed", "PDF rendering did not return a grayscale page")
                    image.write_bytes(rendered)
                    recognized = run_bounded([self.executables["tesseract"], str(image), "stdout", "-l", "eng",
                        "--psm", "3"], self.max_text_bytes - size, deadline)
                    page_text = recognized.decode("utf-8").replace("\f", "\n") + "\f"
                    size += len(page_text.encode("utf-8"))
                    if size > self.max_text_bytes:
                        raise ResearchError("extraction_too_large", "OCR text exceeds its byte limit")
                    pages.append(page_text)
                text = "".join(pages)
                result.update(status="extracted" if text.strip() else "empty_text", text=text if text.strip() else None)
        except FileNotFoundError:
            result["status"] = "extractor_missing"
        except ResearchError as error:
            if error.code == "extraction_cleanup_failed":
                raise
            result["status"] = error.code
        except (OSError, UnicodeError):
            result["status"] = "extraction_failed"
        return result
