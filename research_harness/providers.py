"""Pure registry request builders and response normalization.

Request(url, headers, accept); Page(works, failures, returned_count, total, start,
next_cursor, complete). Each work has id, work_id, aliases, version, title,
authors, publication_date, category, categories, date_assertions, abstract_text,
abstract_status, abstract_representation, fulltext_urls, references,
reference_metadata, source_locator and type. Acquisition replaces abstract_text
with verified ArtifactStore references and binds locators to captured sources.

Reference occurrences preserve order and duplicates: {ordinal, target: exact
normalized identifier | None, raw: provider value, kind: 'unknown'}. Metadata
coverage is {status: missing|partial|provided, reported_count, returned_count,
bibliography_complete: False, scope: 'provider_metadata'}. Registry coverage
alone cannot certify the actual article's bibliography.

Arxiv uses stable submitted-date ascending pages, max 2000 and a 30000 query
ceiling. Acquisition partitions larger queries. OpenAlex uses cursor paging,
per_page <=100 and an optional bearer token. Crossref uses rows <=1000 and stops
when fewer than rows are returned, even when next-cursor remains present.
"""

import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import quote, urlencode

from .errors import ResearchError
from .identities import family_id, normalize_identifier, version_of


@dataclass
class Request:
    url: str
    headers: dict = field(default_factory=dict, repr=False)
    accept: tuple = ("application/json",)


@dataclass
class Page:
    works: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    returned_count: int = 0
    total: object = None
    start: object = None
    next_cursor: object = None
    complete: bool = False


def _work(identifier, title, authors, *, locator, kind="paper"):
    identifier = normalize_identifier(identifier)
    _string(title, "title")
    _array(authors, "authors")
    for author in authors:
        _string(author, "author")
    _string(kind, "type")
    return {"id": identifier, "work_id": family_id(identifier), "version": version_of(identifier),
            "aliases": [], "title": title, "authors": authors, "publication_date": None,
            "category": None, "categories": [], "date_assertions": {}, "abstract_text": None,
            "abstract_status": "missing", "abstract_representation": "original",
            "fulltext_urls": [], "references": [], "reference_metadata": _references(None, None),
            "source_locator": locator, "type": kind}


def _references(returned, reported):
    if reported is not None and (type(reported) is not int or reported < 0):
        raise ResearchError("invalid_response", "Reference count must be a nonnegative integer")
    count = 0 if returned is None else len(returned)
    status = "missing" if returned is None else "partial" if reported is not None and count != reported else "provided"
    return {"status": status, "reported_count": reported, "returned_count": count,
            "bibliography_complete": False, "scope": "provider_metadata"}


def _reference(ordinal, raw, identifier):
    try:
        target = normalize_identifier(identifier) if identifier else None
    except ResearchError:
        target = None
    return {"ordinal": ordinal, "target": target, "raw": raw, "kind": "unknown"}


def _json(data, *, require_object=True):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError()

    try:
        result = json.loads(data, object_pairs_hook=pairs, parse_constant=nonfinite)
        if require_object and not isinstance(result, dict):
            raise ValueError()
        json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise ResearchError("invalid_response", "Provider returned malformed JSON metadata") from error


def _invalid(field):
    raise ResearchError("invalid_response", "Provider metadata has an invalid field", {"field": field})


def _object(value, field):
    if not isinstance(value, dict):
        _invalid(field)
    return value


def _array(value, field):
    if not isinstance(value, list):
        _invalid(field)
    return value


def _string(value, field, *, nullable=False, empty=False):
    if value is None and nullable:
        return value
    if not isinstance(value, str) or "\x00" in value or (not empty and not value.strip()):
        _invalid(field)
    try:
        value.encode("utf-8")
    except UnicodeError:
        _invalid(field)
    return value


def _count(value, field, *, nullable=False):
    if value is None and nullable:
        return value
    if type(value) is not int or value < 0:
        _invalid(field)
    return value


def _iso_date(value, field):
    if value is not None:
        _string(value, field)
        try:
            if date.fromisoformat(value).isoformat() != value:
                _invalid(field)
        except ValueError:
            _invalid(field)
    return value


def _crossref_date(value, field):
    _object(value, field)
    if "date-parts" in value:
        _array(value["date-parts"], field + ".date-parts")
        if not value["date-parts"]:
            _invalid(field)
        for parts in value["date-parts"]:
            if (not isinstance(parts, list) or not 1 <= len(parts) <= 3
                    or any(type(p) is not int for p in parts)):
                _invalid(field)
            try:
                date(*(parts + [1] * (3 - len(parts))))
            except ValueError:
                _invalid(field)
    return value


def _page_size(value, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ResearchError("invalid_input", "Provider page size is outside its supported range", {"maximum": maximum})


def _query(parameters, allowed):
    if not isinstance(parameters, dict) or not set(parameters) <= allowed:
        raise ResearchError("invalid_input", "Unsupported registry query parameters")
    if any(not isinstance(v, str) or not v.strip() or re.search(r"[\x00-\x1f]", v) for v in parameters.values()):
        raise ResearchError("invalid_input", "Registry query values must be nonempty strings")
    return dict(parameters)


class Arxiv:
    name = "arxiv"
    page_size = 100
    query_ceiling = 30000
    accept = ("application/atom+xml", "application/xml", "text/xml")
    ns = {"a": "http://www.w3.org/2005/Atom", "ar": "http://arxiv.org/schemas/atom",
          "os": "http://a9.com/-/spec/opensearch/1.1/"}

    def work_request(self, identifier):
        canonical = normalize_identifier(identifier)
        if not canonical.startswith("arxiv:"):
            raise ResearchError("invalid_identifier", "arXiv requires an arXiv identifier")
        return Request("https://export.arxiv.org/api/query?" + urlencode({"id_list": canonical[6:]}), accept=self.accept)

    def collection_request(self, category, start_date, end_date, *, start=0, page_size=100):
        _page_size(page_size, 2000)
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]*", category):
            raise ResearchError("invalid_input", "Invalid arXiv primary category")
        if type(start) is not int or start < 0 or start >= self.query_ceiling:
            raise ResearchError("query_ceiling", "arXiv cursor exceeds the query range; partition the dates")
        parameters = {"search_query": "cat:" + category + " AND submittedDate:[" + start_date + " TO " + end_date + "]",
                      "start": start, "max_results": min(page_size, self.query_ceiling - start),
                      "sortBy": "submittedDate", "sortOrder": "ascending"}
        return Request("https://export.arxiv.org/api/query?" + urlencode(parameters), accept=self.accept)

    def parse(self, data, **kwargs):
        try:
            if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
                raise ValueError()
            root = ET.fromstring(data)
            if root.tag != "{" + self.ns["a"] + "}feed":
                raise ValueError()
            def integer(name):
                value = root.findtext("os:" + name, None, self.ns)
                if value is None or not value.strip().isdigit():
                    raise ValueError()
                return int(value)
            page = Page(total=integer("totalResults"), start=integer("startIndex"))
            page_size = integer("itemsPerPage")
            entries = root.findall("a:entry", self.ns)
            page.returned_count = len(entries)
            if len(entries) > page_size:
                raise ValueError()
        except (ET.ParseError, ValueError, TypeError) as error:
            raise ResearchError("invalid_response", "arXiv returned invalid Atom pagination metadata") from error
        for index, node in enumerate(entries):
            try:
                identifier = node.findtext("a:id", None, self.ns)
                if identifier and "/api/errors" in identifier:
                    raise ResearchError("provider_error", "arXiv returned an API error entry")
                title = node.findtext("a:title", None, self.ns)
                if not title or not title.strip():
                    raise ResearchError("invalid_response", "arXiv entry is missing a title")
                work = _work(identifier, title, [e.findtext("a:name", "", self.ns) for e in node.findall("a:author", self.ns)],
                             locator={"type": "atom_entry", "index": index, "identifier": identifier})
                if not work["id"].startswith("arxiv:"):
                    _invalid("id")
                work["date_assertions"] = {key: node.findtext("a:" + key, None, self.ns) for key in ("published", "updated")}
                for key, value in work["date_assertions"].items():
                    if value is not None:
                        try:
                            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                            if parsed.utcoffset() is None:
                                _invalid(key)
                        except ValueError:
                            _invalid(key)
                work["publication_date"] = work["date_assertions"]["published"]
                category = node.find("ar:primary_category", self.ns)
                work["category"] = None if category is None else category.get("term")
                if not work["category"]:
                    page.failures.append({"index": index, "code": "missing_primary_category", "id": work["id"]})
                elif not re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]*", work["category"]):
                    page.failures.append({"index": index, "code": "invalid_primary_category", "id": work["id"]})
                    work["category"] = None
                work["categories"] = [n.get("term") for n in node.findall("a:category", self.ns) if n.get("term")]
                summary = node.find("a:summary", self.ns)
                if summary is not None:
                    text = "".join(summary.itertext())
                    if text.strip():
                        work["abstract_text"], work["abstract_status"] = text, "available"
                doi = node.findtext("ar:doi", None, self.ns)
                if doi:
                    try:
                        alias = normalize_identifier(doi.strip())
                        if not alias.startswith("doi:"):
                            _invalid("doi")
                        work["aliases"] = [alias]
                    except ResearchError:
                        page.failures.append({"index": index, "code": "invalid_alias", "id": work["id"]})
                # These are constructed from the captured exact identifier, never
                # a mutable unversioned link or an unchecked provider URL.
                if work["version"]:
                    work["fulltext_urls"] = ["https://arxiv.org/pdf/" + work["id"][6:],
                                             "https://arxiv.org/html/" + work["id"][6:]]
                page.works.append(work)
            except ResearchError as error:
                page.failures.append({"index": index, "code": error.code})
        return page


class OpenAlex:
    name = "openalex"

    def __init__(self, api_key=None):
        self._key = os.environ.get("OPENALEX_API_KEY") if api_key is None else api_key

    def _request(self, path, parameters=None):
        headers = {"Authorization": "Bearer " + self._key} if self._key else {}
        return Request("https://api.openalex.org/works" + path + ("?" + urlencode(parameters) if parameters else ""), headers)

    def work_request(self, identifier):
        canonical = normalize_identifier(identifier)
        if canonical.startswith("openalex:"):
            key = canonical[9:]
        elif canonical.startswith("doi:"):
            key = "https://doi.org/" + canonical[4:]
        else:
            raise ResearchError("invalid_identifier", "OpenAlex requires an OpenAlex ID or DOI")
        return self._request("/" + quote(key, safe=""))

    def search_request(self, parameters, *, cursor="*", page_size=100):
        _page_size(page_size, 100)
        query = _query(parameters, {"search", "filter", "sort"})
        query.update({"cursor": cursor, "per_page": page_size})
        return self._request("", query)

    def parse(self, data, *, page_size=100):
        document = _json(data)
        multiple = "results" in document
        items = document.get("results") if multiple else [document]
        if not isinstance(items, list):
            raise ResearchError("invalid_response", "OpenAlex results must be an array")
        meta = _object(document.get("meta"), "meta") if multiple else {}
        if multiple:
            _count(meta.get("count"), "meta.count")
            _string(meta.get("next_cursor"), "meta.next_cursor", nullable=True)
        page = Page(returned_count=len(items), total=meta.get("count"), next_cursor=meta.get("next_cursor"))
        page.complete = multiple and page.next_cursor is None
        for index, item in enumerate(items):
            try:
                _object(item, "work")
                identifier = normalize_identifier(item.get("id"))
                if not identifier.startswith("openalex:"):
                    _invalid("id")
                _array(item.get("authorships", []), "authorships")
                authors = []
                for authorship in item.get("authorships", []):
                    author = _object(_object(authorship, "authorship").get("author"), "author")
                    authors.append(_string(author.get("display_name"), "author.display_name"))
                work = _work(item["id"], item.get("title") or item.get("display_name"),
                             authors,
                             locator={"type": "json_pointer", "pointer": "/results/" + str(index) if multiple else ""}, kind=item.get("type", "unknown"))
                if not work["title"]:
                    raise ValueError()
                if item.get("doi"):
                    alias = normalize_identifier(item["doi"])
                    if not alias.startswith("doi:"):
                        _invalid("doi")
                    work["aliases"].append(alias)
                work["publication_date"] = _iso_date(item.get("publication_date"), "publication_date")
                if item.get("publication_year") is not None:
                    _count(item["publication_year"], "publication_year")
                for field in ("updated_date", "created_date"):
                    if item.get(field) is not None:
                        _string(item[field], field)
                        try:
                            datetime.fromisoformat(item[field].replace("Z", "+00:00"))
                        except ValueError:
                            _invalid(field)
                work["date_assertions"] = {k: item[k] for k in ("publication_date", "publication_year", "updated_date", "created_date") if k in item}
                inverted = item.get("abstract_inverted_index")
                work["abstract_representation"] = "reconstructed_inverted_index"
                if inverted is not None:
                    try:
                        positions = {}
                        if not isinstance(inverted, dict):
                            raise ValueError()
                        for word, offsets in inverted.items():
                            if not isinstance(word, str) or not word or not isinstance(offsets, list):
                                raise ValueError()
                            for offset in offsets:
                                if type(offset) is not int or offset < 0 or offset in positions:
                                    raise ValueError()
                                positions[offset] = word
                        if positions and (min(positions) != 0 or max(positions) != len(positions) - 1):
                            raise ValueError()
                        if positions:
                            work["abstract_text"] = " ".join(positions[i] for i in range(len(positions)))
                            work["abstract_status"] = "available"
                    except (ValueError, TypeError):
                        work["abstract_status"] = "invalid"
                references = item.get("referenced_works")
                if references is not None and not isinstance(references, list):
                    raise ValueError()
                work["references"] = [_reference(i, raw, raw) for i, raw in enumerate(references or [])]
                work["reference_metadata"] = _references(references, item.get("referenced_works_count"))
                locations = [item.get("best_oa_location")] + _array(item.get("locations", []), "locations")
                for location in locations:
                    if location is not None:
                        _object(location, "location")
                        _string(location.get("pdf_url"), "location.pdf_url", nullable=True)
                work["fulltext_urls"] = list(dict.fromkeys(location["pdf_url"] for location in locations
                                                         if isinstance(location, dict) and location.get("pdf_url")))
                page.works.append(work)
            except (KeyError, TypeError, ValueError, ResearchError):
                page.failures.append({"index": index, "code": "invalid_work"})
        return page


class Crossref:
    name = "crossref"

    def __init__(self, api_key=None):
        self._key = os.environ.get("CROSSREF_API_KEY") if api_key is None else api_key

    def _request(self, path, parameters=None):
        headers = {"Crossref-Plus-API-Token": "Bearer " + self._key} if self._key else {}
        return Request("https://api.crossref.org/works" + path + ("?" + urlencode(parameters) if parameters else ""), headers)

    def work_request(self, identifier):
        canonical = normalize_identifier(identifier)
        if not canonical.startswith("doi:"):
            raise ResearchError("invalid_identifier", "Crossref requires a DOI")
        return self._request("/" + quote(canonical[4:], safe=""))

    def search_request(self, parameters, *, cursor="*", page_size=100):
        _page_size(page_size, 1000)
        query = _query(parameters, {"query", "query.bibliographic", "query.author", "filter", "sort", "order"})
        query.update({"cursor": cursor, "rows": page_size})
        return self._request("", query)

    def parse(self, data, *, page_size=100):
        document = _json(data)
        message = document.get("message")
        if not isinstance(message, dict):
            raise ResearchError("invalid_response", "Crossref omitted its metadata message")
        multiple = "items" in message
        items = message.get("items") if multiple else [message]
        if not isinstance(items, list):
            raise ResearchError("invalid_response", "Crossref items must be an array")
        page = Page(returned_count=len(items), total=message.get("total-results"))
        if multiple:
            _count(page.total, "total-results")
            _string(message.get("next-cursor"), "next-cursor", nullable=True)
        page.complete = multiple and len(items) < page_size
        page.next_cursor = None if page.complete else message.get("next-cursor")
        for index, item in enumerate(items):
            try:
                _object(item, "work")
                identifier = normalize_identifier(item.get("DOI"))
                if not identifier.startswith("doi:"):
                    _invalid("DOI")
                titles = _array(item.get("title"), "title")
                if not titles:
                    _invalid("title")
                for title in titles:
                    _string(title, "title")
                authors = []
                for author in _array(item.get("author", []), "author"):
                    _object(author, "author")
                    for field in ("given", "family", "name"):
                        _string(author.get(field), "author." + field, nullable=True, empty=True)
                    name = ((author.get("given") or "") + " " + (author.get("family") or "")).strip() or author.get("name")
                    authors.append(_string(name, "author.name"))
                work = _work(item["DOI"], item["title"][0],
                             authors,
                             locator={"type": "json_pointer", "pointer": "/message/items/" + str(index) if multiple else "/message"}, kind=item.get("type", "unknown"))
                if not work["title"]:
                    raise ValueError()
                for key in ("published", "published-print", "published-online", "issued", "created", "deposited", "indexed"):
                    if key in item:
                        _crossref_date(item[key], key)
                        work["date_assertions"][key] = item[key].get("date-parts", item[key])
                parts = item.get("published", item.get("issued", {})).get("date-parts", [[]])[0]
                if parts:
                    if not 1 <= len(parts) <= 3 or any(type(p) is not int for p in parts):
                        raise ValueError()
                    date(*(parts + [1] * (3 - len(parts))))
                    work["publication_date"] = "-".join(str(v).zfill(4 if i == 0 else 2) for i, v in enumerate(parts))
                abstract = item.get("abstract")
                _string(abstract, "abstract", nullable=True, empty=True)
                if isinstance(abstract, str) and abstract.strip():
                    work["abstract_text"], work["abstract_status"] = abstract, "available"
                    work["abstract_representation"] = "original_jats"
                references = item.get("reference")
                if references is not None and not isinstance(references, list):
                    raise ValueError()
                work["references"] = [_reference(i, raw, raw.get("DOI") if isinstance(raw, dict) else None)
                                      for i, raw in enumerate(references or [])]
                work["reference_metadata"] = _references(references, item.get("reference-count"))
                links = _array(item.get("link", []), "link")
                for link in links:
                    _object(link, "link")
                    _string(link.get("URL"), "link.URL")
                    _string(link.get("content-type"), "link.content-type", nullable=True)
                work["fulltext_urls"] = list(dict.fromkeys(link["URL"] for link in links
                    if link.get("content-type") in ("application/pdf", "text/html")))
                page.works.append(work)
            except (KeyError, TypeError, ValueError, IndexError, ResearchError):
                page.failures.append({"index": index, "code": "invalid_work"})
        return page
