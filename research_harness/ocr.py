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


def read_pdfinfo_page_count(info):
    """Read one positive Pages field; multiline metadata must not supply another one."""
    counts = re.findall(rb"(?m)^Pages:([^\r\n]*)", info)
    if len(counts) != 1 or re.fullmatch(rb"[ \t]*[0-9]+[ \t]*", counts[0]) is None:
        return None
    count = int(counts[0])
    return count if count > 0 else None


def run_bounded(argv, limit, deadline, *, include_stderr=False):
    """Collect bounded output and always terminate a timed-out child."""
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if include_stderr else subprocess.DEVNULL)
    output = bytearray()
    started = time.monotonic()
    allowance = max(0, deadline - started)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise ResearchError("extraction_timeout", "OCR exceeded its extraction deadline",
                                {"usage": {"elapsed_seconds": time.monotonic() - started},
                                 "limit": {"elapsed_seconds": allowance}})
                chunk = os.read(process.stdout.fileno(), min(65536, limit + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > limit:
                    raise ResearchError("extraction_too_large", "OCR output exceeded its byte limit",
                                        {"usage": {"output_bytes": len(output)}, "limit": {"output_bytes": limit}})
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ResearchError("extraction_timeout", "OCR exceeded its extraction deadline",
                                {"usage": {"elapsed_seconds": time.monotonic() - started},
                                 "limit": {"elapsed_seconds": allowance}})
        if process.wait(timeout=remaining) != 0:
            raise ResearchError("extraction_failed", "An OCR tool did not complete successfully")
        return bytes(output)
    except subprocess.TimeoutExpired as error:
        raise ResearchError("extraction_timeout", "OCR exceeded its extraction deadline",
                            {"usage": {"elapsed_seconds": time.monotonic() - started},
                             "limit": {"elapsed_seconds": allowance}}) from error
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired as error:
            raise ResearchError("extraction_cleanup_failed", "OCR process did not stop after termination") from error
        finally:
            process.stdout.close()


def run_page_tool(argv, limit, deadline, *, run=None, measure="output_bytes", include_stderr=False):
    """Apply one page operation's bounded runner and preserve its measured refusal."""
    if not argv[0]:
        raise ResearchError("extractor_missing", "A required PDF tool is unavailable")
    try:
        return (run or run_bounded)(argv, limit, deadline, include_stderr=include_stderr)
    except ResearchError as error:
        details = dict(error.details or {})
        usage = dict(details.get("usage", {}))
        limits = dict(details.get("limit", {}))
        if "output_bytes" in usage:
            usage[measure] = usage.pop("output_bytes")
            limits[measure] = limits.pop("output_bytes")
        error.details = dict(details, usage=usage, limit=limits)
        raise


def read_pdf_tool_versions(executables, *, run=None, deadline=None):
    deadline = deadline if deadline is not None else time.monotonic() + 15
    versions = {}
    for name, executable in executables.items():
        data = run_page_tool([executable, "--version" if name == "tesseract" else "-v"],
                             65536, deadline, run=run, include_stderr=True)
        lines = data.decode("utf-8").splitlines()
        if not lines or not lines[0].strip():
            raise ResearchError("extraction_failed", "A PDF tool did not report its version")
        versions[name] = lines[0]
    return versions


def count_pdf_pages(original, executable, *, run=None, deadline=None):
    info = run_page_tool([executable, str(original)], 65536,
                         deadline if deadline is not None else time.monotonic() + 30, run=run)
    count = read_pdfinfo_page_count(info)
    if count is None:
        raise ResearchError("malformed_pdf", "PDF page count could not be established")
    return count


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

    def get_page_options(self):
        return {"ocr": True, "language": "eng", "dpi": 300,
                "page_segmentation_mode": 3, "max_dimension": self.max_dimension}

    def read_page_tool_versions(self, *, run=None, deadline=None):
        return read_pdf_tool_versions({name: self.executables.get(name)
                                       for name in ("pdfinfo", "pdftoppm", "tesseract")},
                                      run=run, deadline=deadline)

    def count_pages(self, original, *, run=None, deadline=None):
        return count_pdf_pages(original, self.executables.get("pdfinfo"), run=run,
                               deadline=deadline if deadline is not None else time.monotonic() + self.timeout)

    def extract_page(self, original, page, *, run=None, deadline=None):
        """OCR one verified input page, with a fresh per-page image/text/time budget."""
        if type(page) is not int or page < 1:
            raise ResearchError("invalid_input", "PDF page index must be a positive integer")
        deadline = min(deadline, time.monotonic() + self.timeout) if deadline is not None else time.monotonic() + self.timeout
        rendered = run_page_tool([self.executables.get("pdftoppm"), "-f", str(page), "-l", str(page),
                                  "-singlefile", "-gray", "-r", "300", "-scale-to", str(self.max_dimension),
                                  str(original)], self.max_image_bytes, deadline, run=run, measure="image_bytes")
        if not rendered.startswith(b"P5"):
            raise ResearchError("extraction_failed", "PDF rendering did not return a grayscale page")
        with tempfile.TemporaryDirectory(prefix="exactory-ocr-page-", dir=Path(original).parent) as directory:
            image = Path(directory) / "page.pgm"
            image.write_bytes(rendered)
            recognized = run_page_tool([self.executables.get("tesseract"), str(image), "stdout", "-l", "eng",
                                        "--psm", "3"], self.max_text_bytes, deadline, run=run)
        return recognized.decode("utf-8").replace("\f", "\n")

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
                page_count = read_pdfinfo_page_count(info)
                if page_count is None:
                    raise ResearchError("malformed_pdf", "PDF page count could not be established")
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
