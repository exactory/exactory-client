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

import json
import re
from datetime import date, datetime
from urllib.parse import unquote, urlsplit

from .errors import ResearchError
from .fulltext import parse_base_media_type
from .identities import normalize_identifier
from .providers import Page, _json, _reference, _references, _work


def bind_import_query(mappings, source_url):
    """Read the declared named URL segment, retaining ordinary list mappings."""
    if isinstance(mappings, list):
        return mappings, None
    if not isinstance(mappings, dict) or set(mappings) != {"records", "query_path"}:
        raise ResearchError("invalid_import",
                            "The payload has no mappings in an accepted form. Give mappings as a list of record "
                            "mappings, or as an object with exactly the fields records (that list) and query_path. "
                            "query_path binds a path segment of the captured URL as the query.")
    path = mappings["query_path"]
    if (not isinstance(path, dict) or set(path) != {"template", "segment"}
            or not isinstance(path["template"], str) or not path["template"].startswith("/")
            or not isinstance(path["segment"], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", path["segment"])):
        raise ResearchError("invalid_import",
                            "query_path does not have the form {template, segment}. Give exactly these two fields. "
                            "template is a URL path that starts with a slash. segment is a name of ASCII letters, "
                            "digits and underscores that does not start with a digit. An example is "
                            '{"template": "/graph/v1/paper/{paper_id}/citations", "segment": "paper_id"}.')
    parts = path["template"].split("/")
    placeholder = "{" + path["segment"] + "}"
    if (parts.count(placeholder) != 1
            or any("{" in part or "}" in part for part in parts if part != placeholder)):
        raise ResearchError("invalid_import",
                            "The template of query_path does not hold " + placeholder + " exactly once as a whole "
                            "path segment, or it holds another brace. Write the template with " + placeholder
                            + " as one whole path segment, once, and with no other brace. It can be the last "
                            "segment of the path.")
    actual = urlsplit(source_url).path.split("/")
    if len(parts) != len(actual) or any(part != value for part, value in zip(parts, actual) if part != placeholder):
        raise ResearchError("invalid_import",
                            "The path of source_url does not match the template of query_path. The two have a "
                            "different number of segments, or a segment outside " + placeholder + " differs. Give "
                            "the template of the captured URL, with every other segment written exactly as the URL "
                            "has it.")
    query = unquote(actual[parts.index(placeholder)])
    if not query.strip():
        raise ResearchError("invalid_import",
                            "The segment of source_url at " + placeholder + " is empty or, after percent-decoding, "
                            "holds only white space, so it gives no query. Give a template whose named segment is "
                            "the part of the URL that holds the query.")
    return mappings["records"], dict(path, kind="url_path", value=query)


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
    media_type = parse_base_media_type(media_type)
    if not isinstance(mappings, list):
        raise ResearchError("invalid_import", "Web/MCP imports require explicit record mappings")
    try:
        if media_type == "application/json":
            if json.detect_encoding(data) not in ("utf-8", "utf-8-sig"):
                raise ValueError("Mapped JSON must use UTF-8")
            document = _json(data, require_object=False)
        else:
            text = data.decode("utf-8-sig")
    except (UnicodeError, ValueError, ResearchError) as error:
        raise ResearchError("invalid_import", "Import response is not valid UTF-8 or JSON") from error

    def read_field(locator):
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
        fields = {key: read_field(locator) for key, locator in mapping.items()}
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


def parse_publisher_capture(data, media_type, source_url, capture):
    """Dispatch an explicitly versioned complete-response parser, without mappings."""
    from .artifacts import describe_artifact
    from .publisher_springer_v1 import FORMAT_ID, parse_capture
    if (not isinstance(capture, dict) or set(capture) != {'format', 'identifier'}
            or capture['format'] != FORMAT_ID or parse_base_media_type(media_type) != 'message/http'):
        raise ResearchError('invalid_publisher_capture', 'Select a supported publisher capture format and its exact identity')
    try:
        describe_artifact(data, media_type)
    except ResearchError as error:
        if error.code != 'invalid_input':
            raise
        raise ResearchError('invalid_publisher_capture', 'The capture media type must be a valid artifact media type') from error
    return parse_capture(data, source_url, capture['identifier'])
