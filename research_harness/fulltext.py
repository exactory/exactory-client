"""Conservative full-text extraction, independent of acquisition and reading.

extract(data, media_type, *, extractor=None, layout=True) -> {status, text,
includes_abstract, visual_inspection_required, extractor, version, options}. PDF
extractor injection accepts bytes and returns {status, text}; its successful
status is 'extracted'. extraction_measures(text, original_size) describes an
extraction (bytes, pages, longest line, whitespace fraction, expansion) so a
coordinator sees layout padding before a reader does; a high value is a signal
to inspect, never a reason to truncate. Missing, scanned, malformed or timed-out extraction remains pending.
The default pdftotext invocation uses explicit argv, a timeout, a private temp
directory, and a bounded output read. HTML requires article-body structure with
nonempty content blocks beyond titles, metadata and abstracts. includes_abstract
only describes nonempty abstract content actually saved in the extraction.
HTTP success or arbitrary extracted text is not evidence of full-text
availability. No function here creates a reading record.
"""

import math
import os
import re
import selectors
import shutil
import subprocess
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path

from .errors import ResearchError


def extraction_measures(text, original_size):
    """Describe an extraction so oversized or padded text is visible before reading."""
    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    encoded = len(text.encode("utf-8"))
    return {"text_bytes": encoded, "page_count": max(1, len(pages)),
            "max_line_length": max((len(line) for line in text.splitlines()), default=0),
            "whitespace_fraction": round(sum(c.isspace() for c in text) / len(text), 4) if text else 0.0,
            "expansion_ratio": round(encoded / original_size, 3) if original_size else None}


class PdfExtractor:
    name = "pdftotext"

    def __init__(self, *, executable=None, timeout=30, max_text_bytes=32 * 1024 * 1024, layout=True):
        if not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout <= 0:
            raise ResearchError("invalid_input", "PDF extraction timeout must be positive and finite")
        if type(max_text_bytes) is not int or max_text_bytes < 0:
            raise ResearchError("invalid_input", "PDF extraction output limit must be a nonnegative integer")
        if type(layout) is not bool:
            raise ResearchError("invalid_input", "PDF layout mode must be a boolean")
        self.executable = executable if executable is not None else shutil.which("pdftotext")
        self.timeout, self.max_text_bytes, self.layout = timeout, max_text_bytes, layout

    def version(self):
        """The extractor's reported version, or None when it cannot be determined."""
        if not self.executable:
            return None
        try:
            result = subprocess.run([self.executable, "-v"], capture_output=True, text=True, timeout=5,
                                    stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError, UnicodeError):
            return None
        match = re.search(r"version\s+(\S+)", result.stdout + result.stderr)
        return match.group(1) if match else None

    def __call__(self, data):
        if not self.executable:
            return {"status": "extractor_missing", "text": None}
        with tempfile.TemporaryDirectory(prefix="exactory-pdf-") as directory:
            original = Path(directory) / "source.pdf"
            original.write_bytes(data)
            process = None
            try:
                deadline = time.monotonic() + self.timeout
                argv = [self.executable, "-enc", "UTF-8"] + (["-layout"] if self.layout else []) + [str(original), "-"]
                process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                text = bytearray()
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not selector.select(remaining):
                            return {"status": "extraction_timeout", "text": None}
                        chunk = os.read(process.stdout.fileno(), min(65536, self.max_text_bytes + 1 - len(text)))
                        if not chunk:
                            break
                        text.extend(chunk)
                        if len(text) > self.max_text_bytes:
                            return {"status": "extraction_too_large", "text": None}
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {"status": "extraction_timeout", "text": None}
                if process.wait(timeout=remaining) != 0:
                    return {"status": "malformed_pdf", "text": None}
                text = text.decode("utf-8")
                return {"status": "extracted" if text.strip() else "empty_text", "text": text if text.strip() else None,
                        "extractor": self.name, "version": self.version(), "options": {"layout": self.layout}}
            except subprocess.TimeoutExpired:
                return {"status": "extraction_timeout", "text": None}
            except FileNotFoundError:
                return {"status": "extractor_missing", "text": None}
            except (OSError, UnicodeError):
                return {"status": "extraction_failed", "text": None}
            finally:
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired as error:
                        raise ResearchError("extraction_cleanup_failed", "PDF extractor did not stop after termination") from error
                    finally:
                        process.stdout.close()


class _Article(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.text = []
        self.all_text = []
        self.body_text = []
        self.included_abstract = False
        self.has_article = False
        self.has_body = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").lower().split())
        properties = set((attributes.get("itemprop") or "").lower().split())
        roles = set((attributes.get("role") or "").lower().split())
        parent = self.stack[-1][1] if self.stack else {"skip": False, "article": False, "abstract": False,
                                                    "body": False, "heading": False, "metadata": False, "content": False}
        state = dict(parent)
        state["skip"] = state["skip"] or tag in ("script", "style", "nav", "form", "noscript")
        state["article"] = state["article"] or tag == "article" or bool(classes & {"ltx_document", "article-body", "article__body", "jats_body"})
        state["abstract"] = (state["abstract"] or tag == "abstract" or "doc-abstract" in roles or "abstract" in properties
                             or bool(classes & {"abstract", "ltx_abstract", "article-abstract"}) or attributes.get("id") in ("abstract", "Abs1"))
        state["body"] = state["body"] or bool(classes & {"article-body", "article__body", "jats_body", "ltx_section"})
        state["heading"] = state["heading"] or tag in ("h1", "h2", "h3", "h4", "h5", "h6", "title") or "heading" in roles or bool(properties & {"headline", "name"}) or bool(
            classes & {"title", "subtitle", "article-title", "article__title", "ltx_title", "jats_title"})
        state["metadata"] = state["metadata"] or tag in ("header", "footer", "aside", "address", "time") or bool(
            properties & {"author", "affiliation", "datepublished", "datemodified", "datecreated", "publisher", "identifier", "keywords", "copyrightnotice"}) or bool(
            classes & {"metadata", "article-meta", "article__meta", "article-info", "authors", "author", "affiliation",
                       "affiliations", "article-author", "article-authors", "byline", "journal", "publication-date", "ltx_authors", "ltx_date"})
        state["content"] = state["content"] or tag in ("p", "li", "dd", "td", "pre", "blockquote") or bool(
            classes & {"ltx_para", "ltx_p", "paragraph"})
        self.has_article |= state["article"]
        self.has_body |= state["body"]
        if tag not in ("area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"):
            self.stack.append((tag, state))
        if tag in ("p", "div", "section", "h1", "h2", "h3", "br", "li", "tr"):
            self.text.append("\n")

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if tag in ("p", "div", "section", "h1", "h2", "h3", "li", "tr"):
            self.text.append("\n")

    def handle_data(self, data):
        state = self.stack[-1][1] if self.stack else {}
        if state.get("skip"):
            return
        self.all_text.append(data)
        if state.get("article"):
            self.text.append(data)
            if state.get("abstract") and not state.get("heading") and data.strip():
                self.included_abstract = True
            if state.get("body") and state.get("content") and not any(state.get(key) for key in ("abstract", "heading", "metadata")):
                self.body_text.append(data)


def extract(data, media_type, *, extractor=None, layout=True):
    result = {"status": "unsupported_fulltext", "text": None, "includes_abstract": None, "visual_inspection_required": True,
              "extractor": None, "version": None, "options": {}}
    if media_type == "application/pdf":
        result.update({"extractor": PdfExtractor.name, "options": {"layout": layout}})
        if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-4096:]:
            result["status"] = "malformed_pdf"
            return result
        extracted = (extractor or PdfExtractor(layout=layout))(data)
        result.update({"status": extracted["status"], "text": extracted["text"],
                       "version": extracted.get("version"), "options": extracted.get("options", {"layout": layout})})
        if result["status"] == "extracted" and (not isinstance(result["text"], str) or not result["text"].strip()):
            result["status"], result["text"] = "empty_text", None
        return result
    if media_type not in ("text/html", "application/xhtml+xml"):
        return result
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        result["status"] = "invalid_encoding"
        return result
    result["extractor"] = "html"
    parser = _Article()
    parser.feed(text)
    parser.close()
    visible = " ".join(" ".join(parser.all_text).lower().split())
    if any(marker in visible for marker in ("checking your browser", "enable javascript and cookies",
            "verify you are human", "checking if the site connection is secure", "access denied", "just a moment...")):
        result["status"] = "challenge"
    elif any(state["article"] for _, state in parser.stack):
        result["status"] = "malformed_html"
    elif not parser.has_article or not parser.has_body or not "".join(parser.body_text).strip():
        result["status"] = "landing_page"
    else:
        result.update({"status": "extracted", "text": re.sub(r"\n[ \t]*\n+", "\n\n", "".join(parser.text)).strip(),
                       "includes_abstract": parser.included_abstract})
    return result
