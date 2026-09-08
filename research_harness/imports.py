"""Evidence-preserving web/MCP normalization from explicit source mappings.

Mappings are a list of {id, title, authors?, abstract?, publication_date?,
category?, aliases?, references?, type?, version?}. Each value is a JSON Pointer
into an original JSON response, or {start, end} character offsets into an
original UTF-8 text/HTML response. Values are extracted, never accepted as
caller-supplied normalized replacements. Required id/title and every optional
field must match saved bytes through their locators. For non-registry HTTPS IDs
the canonical id is url:HTTPS and type defaults to unknown. References are an
array of objects with optional id and original unstructured content; unresolved
entries remain distinct occurrences. Importing does not assert reading or a
complete bibliography. Original response bytes and mappings are both retained.
Mapped abstracts are partial excerpts with representation mapped_excerpt. They
do not replace an original abstract, fill a missing cohort abstract, certify
origin article capture, or create a fulltext record. Import of an entire native
registry response instead uses that provider's documented metadata parser.
"""

import re
from datetime import date, datetime

from .errors import ResearchError
from .identities import normalize_identifier
from .providers import Page, _json, _reference, _references, _work


def _pointer(document, pointer):
    if not isinstance(pointer, str) or pointer and not pointer.startswith("/"):
        raise ResearchError("invalid_import", "A JSON Pointer must be empty or begin with a slash")
    value = document
    try:
        for part in pointer.split("/")[1:]:
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list):
                if not part.isdigit() or str(int(part)) != part:
                    raise ValueError()
                value = value[int(part)]
            else:
                value = value[part]
        return value
    except (KeyError, IndexError, ValueError, TypeError) as error:
        raise ResearchError("invalid_import", "Import mapping does not identify a value in the original response") from error


def parse_mapped(data, media_type, mappings):
    if not isinstance(mappings, list):
        raise ResearchError("invalid_import", "Web/MCP imports require explicit record mappings")
    try:
        text = data.decode("utf-8-sig")
        document = _json(data, require_object=False) if media_type == "application/json" else None
    except (UnicodeError, ValueError, ResearchError) as error:
        raise ResearchError("invalid_import", "Import response is not valid UTF-8 or JSON") from error

    def value(locator):
        if media_type == "application/json":
            return _pointer(document, locator)
        if (not isinstance(locator, dict) or set(locator) != {"start", "end"}
                or type(locator["start"]) is not int or type(locator["end"]) is not int
                or not 0 <= locator["start"] < locator["end"] <= len(text)):
            raise ResearchError("invalid_import", "Text import mappings require a valid character span")
        return text[locator["start"]:locator["end"]]

    page = Page(returned_count=len(mappings), complete=True)
    allowed = {"id", "title", "authors", "abstract", "publication_date", "category", "aliases", "references", "type", "version"}
    for index, mapping in enumerate(mappings):
        if not isinstance(mapping, dict) or not {"id", "title"} <= set(mapping) or not set(mapping) <= allowed:
            raise ResearchError("invalid_import", "Each mapping must identify id/title and only supported fields")
        fields = {key: value(locator) for key, locator in mapping.items()}
        identifier = fields["id"]
        try:
            identifier = normalize_identifier(identifier)
        except ResearchError:
            if not isinstance(identifier, str) or not identifier.startswith("https://"):
                raise
            identifier = normalize_identifier("url:" + identifier)
        if not isinstance(fields["title"], str) or not fields["title"].strip():
            raise ResearchError("invalid_import", "Mapped title must be a nonempty string")
        authors = fields.get("authors", [])
        if not isinstance(authors, list) or any(not isinstance(a, str) for a in authors):
            raise ResearchError("invalid_import", "Mapped authors must be an array of names")
        work = _work(identifier, fields["title"], authors, locator={"type": "mapped", "index": index, "fields": mapping},
                     kind=fields.get("type", "unknown"))
        if "version" in fields and fields["version"] != work["version"]:
            raise ResearchError("invalid_import", "Mapped version does not match its exact identifier")
        for key in ("publication_date", "category", "type"):
            if key in fields:
                if fields[key] is not None and not isinstance(fields[key], str):
                    raise ResearchError("invalid_import", "Mapped metadata scalar must be a string or null")
                work[key] = fields[key]
        if fields.get("publication_date"):
            value = fields["publication_date"]
            try:
                if re.fullmatch(r"[0-9]{4}(?:-[0-9]{2}){0,2}", value):
                    parts = [int(p) for p in value.split("-")]
                    date(*(parts + [1] * (3 - len(parts))))
                else:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as error:
                raise ResearchError("invalid_import", "Mapped publication date must be an ISO date or timestamp") from error
            work["date_assertions"] = {"publication_date": fields["publication_date"]}
        if "abstract" in fields:
            abstract = fields["abstract"]
            if abstract is not None and not isinstance(abstract, str):
                raise ResearchError("invalid_import", "Mapped abstract must be a string or null")
            if abstract and abstract.strip():
                work["abstract_text"], work["abstract_status"] = abstract, "partial"
                work["abstract_representation"] = "mapped_excerpt"
        aliases = fields.get("aliases", [])
        if not isinstance(aliases, list):
            raise ResearchError("invalid_import", "Mapped aliases must be an array")
        work["aliases"] = [normalize_identifier(alias) for alias in aliases]
        references = fields.get("references")
        if references is not None and not isinstance(references, list):
            raise ResearchError("invalid_import", "Mapped references must be an array")
        work["references"] = [_reference(i, raw, raw.get("id") if isinstance(raw, dict) else None)
                              for i, raw in enumerate(references or [])]
        work["reference_metadata"] = _references(references, None)
        page.works.append(work)
    return page
