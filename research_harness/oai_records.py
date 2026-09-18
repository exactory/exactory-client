"""Shared original arXivRaw record parsing. GetRecord grants no enumeration."""

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .errors import ResearchError
from .identities import normalize_identifier, version_of
from .providers import Page, _invalid, _iso_date, _string, _work

OAI = "{http://www.openarchives.org/OAI/2.0/}"
RAW = "{http://arxiv.org/OAI/arXivRaw/}"


def child(parent, tag, *, optional=False):
    nodes = parent.findall(tag)
    if optional and not nodes:
        return None
    if len(nodes) != 1:
        _invalid("oai." + tag.rsplit("}", 1)[-1])
    return nodes[0]


def value(parent, tag, *, optional=False, empty=False):
    node = child(parent, tag, optional=optional)
    if node is None:
        return None
    if len(node):
        _invalid("oai.simple_text")
    return _string(node.text or "" if empty else node.text,
                   "oai." + tag.rsplit("}", 1)[-1], empty=empty)


def version_date(text):
    if not re.fullmatch(r"[A-Z][a-z]{2}, [0-9]{1,2} [A-Z][a-z]{2} [0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT", text):
        _invalid("oai.version.date")
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.utcoffset() != timezone.utc.utcoffset(None) or parsed.strftime("%a") != text[:3]:
            _invalid("oai.version.date")
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError) as error:
        raise ResearchError("invalid_response", "Invalid arXiv OAI version date") from error



def parse_get_record(root):
    if root.findall(OAI + "error"):
        raise ResearchError("provider_error", "arXiv OAI returned a protocol error")
    if any(node.tag not in {OAI + "request", OAI + "responseDate", OAI + "GetRecord"} for node in root):
        _invalid("oai.envelope")
    request = child(root, OAI + "request")
    if request.get("verb") != "GetRecord" or request.get("metadataPrefix") != "arXivRaw" or not request.get("identifier"):
        _invalid("oai.request")
    response_date = value(root, OAI + "responseDate")
    try:
        parsed_response = datetime.fromisoformat(response_date.replace("Z", "+00:00"))
        if parsed_response.utcoffset() is None:
            _invalid("oai.responseDate")
    except ValueError as error:
        raise ResearchError("invalid_response", "Invalid arXiv OAI response date") from error
    node = child(child(root, OAI + "GetRecord"), OAI + "record")
    return parse_record(node, response_date, parsed_response, request_identifier=request.get("identifier"))


def parse_record(node, response_date, parsed_response, *, request_identifier=None):
    header = child(node, OAI + "header")
    if header.get("status") is not None:
        raise ResearchError("provider_error", "arXiv OAI record is deleted or has an unknown status")
    header_id = value(header, OAI + "identifier")
    metadata = child(node, OAI + "metadata")
    if len(metadata) != 1:
        _invalid("oai.metadata")
    article = child(metadata, RAW + "arXivRaw")
    article_fields = {RAW + name for name in ("id", "submitter", "version", "title", "authors", "categories",
                       "comments", "proxy", "report-no", "acm-class", "msc-class", "journal-ref", "doi", "license", "abstract")}
    if any(node.tag not in article_fields for node in article):
        _invalid("oai.article_structure")
    identifier = value(article, RAW + "id")
    canonical = normalize_identifier("arxiv:" + identifier)
    if not canonical.startswith("arxiv:"):
        _invalid("oai.identifier")
    expected_header = "oai:arXiv.org:" + canonical[6:]
    if (version_of(canonical) is not None or header_id != expected_header
            or (request_identifier is not None and request_identifier != expected_header)):
        _invalid("oai.identifier")
    datestamp = _iso_date(value(header, OAI + "datestamp"), "oai.datestamp")
    history = []
    for version in article.findall(RAW + "version"):
        if any(node.tag not in {RAW + "date", RAW + "size", RAW + "source_type"} for node in version):
            _invalid("oai.version_structure")
        label = version.get("version", "")
        if not re.fullmatch(r"v[1-9][0-9]*", label):
            _invalid("oai.version")
        date_text = value(version, RAW + "date")
        history.append((int(label[1:]), version_date(date_text), {"version": label, "date": date_text}))
    history.sort(key=lambda item: item[0])
    if not history or [item[0] for item in history] != list(range(1, len(history) + 1)):
        _invalid("oai.version_history")
    diagnostics = [{"code": "nonmonotone_version_dates", "earlier_version": left[2]["version"],
                    "earlier_date": left[2]["date"], "later_version": right[2]["version"], "later_date": right[2]["date"],
                    "earlier_timestamp": left[1].strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "later_timestamp": right[1].strftime("%Y-%m-%dT%H:%M:%SZ")}
                   for left, right in zip(history, history[1:]) if left[1] > right[1]]
    if any(item[1] > parsed_response for item in history):
        _invalid("oai.future_version")
    exact = canonical + "v" + str(history[-1][0])
    work = _work(exact, value(article, RAW + "title"), [],
                 locator={"type": "oai_record", "identifier": header_id, "metadata_prefix": "arXivRaw"})
    work["authors_raw"] = value(article, RAW + "authors", optional=True)
    work["date_assertions"] = {"published": history[0][1].strftime("%Y-%m-%dT%H:%M:%SZ"),
                               "updated": history[-1][1].strftime("%Y-%m-%dT%H:%M:%SZ"),
                               "oai_datestamp": datestamp, "oai_response_date": response_date,
                               "version_history": [item[2] for item in history], "diagnostics": diagnostics,
                               "chronology_status": "nonmonotone" if diagnostics else "observed_nondecreasing"}
    work["publication_date"] = work["date_assertions"]["published"]
    categories = value(article, RAW + "categories", optional=True)
    work["categories"] = categories.split() if categories else []
    if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]*", term) for term in work["categories"]):
        _invalid("oai.categories")
    abstract = value(article, RAW + "abstract", optional=True, empty=True)
    if abstract is not None and abstract.strip():
        work["abstract_text"], work["abstract_status"] = abstract, "available"
    work["fulltext_urls"] = ["https://arxiv.org/pdf/" + exact[6:], "https://arxiv.org/html/" + exact[6:]]
    page = Page(works=[work], returned_count=1)
    page.warnings.extend([{"code": "unstructured_authors", "id": exact},
                          {"code": "primary_category_unasserted", "id": exact}])
    doi = value(article, RAW + "doi", optional=True, empty=True)
    if doi and doi.strip():
        try:
            alias = normalize_identifier(doi.strip())
            if not alias.startswith("doi:"):
                _invalid("doi")
            work["aliases"] = [alias]
        except ResearchError:
            page.warnings.append({"index": 0, "code": "invalid_alias", "id": exact, "raw": doi.strip()})
    return page
