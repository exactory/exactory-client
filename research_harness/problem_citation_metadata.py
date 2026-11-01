"""Immutable citation text supported by exact byte spans of retained captures."""

import re

from .errors import ResearchError
from .evidence import digest
from .operations import fields, immutable_record, prepared_mutation, text
from .problem_claims import require_problem


def find_citation_metadata(records, version_id):
    return max((row for row in records.get("problem_citation_metadata", {}).values()
                if row["version_id"] == version_id),
               key=lambda row: (row["recorded_revision"], row["id"]), default=None)


def _validate_schema(value, *, stored=False):
    fields(value, ("id", "version_id", "title", "authors", "evidence"),
           ("year", "provenance", "recorded_revision") if stored else ("year",))
    for key in ("id", "version_id", "title"):
        text(value[key], "Citation " + key)
    if not isinstance(value["authors"], list) or not value["authors"]:
        raise ResearchError("invalid_input", "Citation authors must be a nonempty ordered array")
    for author in value["authors"]:
        text(author, "Citation author")
    if "year" in value and (not isinstance(value["year"], str) or not re.fullmatch(r"[0-9]{4}", value["year"])):
        raise ResearchError("invalid_input", "Citation year must be four ASCII digits")
    fields(value["evidence"], ("title", "authors", "year") if "year" in value else ("title", "authors"))
    authors = value["evidence"]["authors"]
    if not isinstance(authors, list) or len(authors) != len(value["authors"]):
        raise ResearchError("invalid_input", "Give one evidence span for each author, in order")
    for _, span in _field_spans(value):
        fields(span, ("kind", "source_id", "sha256", "start", "end"))
        text(span["source_id"], "Citation source ID")
        if (span["kind"] not in ("fulltext", "response") or not isinstance(span["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", span["sha256"])
                or type(span["start"]) is not int or type(span["end"]) is not int
                or not 0 <= span["start"] < span["end"]):
            raise ResearchError("invalid_input", "Use a retained fulltext or response and valid UTF-8 byte offsets")


def _field_spans(value):
    yield value["title"], value["evidence"]["title"]
    yield from zip(value["authors"], value["evidence"]["authors"])
    if "year" in value:
        yield value["year"], value["evidence"]["year"]


def _field_map(value, rows):
    rows = iter(rows)
    result = {"title": next(rows), "authors": [next(rows) for _ in value["authors"]]}
    if "year" in value:
        result["year"] = next(rows)
    return result


def _resolve_span(records, artifacts, work, field_value, span):
    affected = {"source_id": span["source_id"], "sha256": span["sha256"]}
    source = records.get("source", {}).get(span["source_id"])
    if source is None:
        raise ResearchError("unknown_source", "Import or capture the original response first", affected)
    if source.get("status") != "captured" or source.get("response_complete") is not True or source.get("response") is None:
        raise ResearchError("source_pending", "Citation evidence requires a complete captured response", affected)
    capture = None
    artifact = source["response"]
    if span["kind"] == "fulltext":
        captures = [row for row in work.get("fulltexts", []) if row.get("source_id") == span["source_id"]
                    and (row.get("text") or {}).get("sha256") == span["sha256"]]
        if not captures:
            raise ResearchError("problem_citation_evidence_unbound", "Bind an extracted text of this exact version", affected)
        capture = next((row for row in captures if row.get("availability") == "available"
                        and row.get("extraction_status") == "extracted"), None)
        if capture is None:
            raise ResearchError("source_pending", "Citation evidence requires an available full-text capture", affected)
        artifact = capture["text"]
    if artifact["sha256"] != span["sha256"]:
        raise ResearchError("problem_citation_evidence_unbound", "Use the retained object's exact hash", affected)
    try:
        # Verify the original response as well as any derived text through the store.
        original = artifacts.read(source["response"])
        data = original if span["kind"] == "response" else artifacts.read(artifact)
    except ResearchError as error:
        if error.code == "artifact_missing":
            raise ResearchError("problem_citation_evidence_unbound", "Retain the captured evidence object", affected) from error
        raise
    try:
        content = data.decode("utf-8")
        if span["end"] > len(data):
            raise ValueError()
        before = data[:span["start"]].decode("utf-8")
        selected = data[span["start"]:span["end"]].decode("utf-8")
    except (UnicodeError, ValueError) as error:
        raise ResearchError("invalid_input", "Citation offsets must be UTF-8 byte boundaries within valid UTF-8", affected) from error
    if data[span["start"]:span["end"]] != field_value.encode("utf-8"):
        raise ResearchError("problem_citation_span_mismatch", "Citation text must equal the selected bytes exactly", affected)
    # Provenance identifies originals without delivering their full bodies as attachments.
    provenance = {"source": {key: source[key] for key in (
        "id", "provider", "url", "captured_at", "capture_method", "content_scope", "origin_verified", "imported") if key in source},
        "source_digest": digest(source), "response_sha256": source["response"]["sha256"]}
    if capture is not None:
        provenance.update(capture_digest=digest(capture), text_sha256=artifact["sha256"],
                          original_sha256=(capture.get("original") or {}).get("sha256"))
    start = max(0, len(before) - 256)
    end = min(len(content), len(before) + len(selected) + 256)
    excerpt = {"text": content[start:end], "start": len(content[:start].encode("utf-8")),
               "end": len(content[:end].encode("utf-8"))}
    return provenance, excerpt


def validate_citation_metadata(records, artifacts, value):
    """Revalidate a payload or stored record; return field provenance and byte-bound excerpts."""
    from .problem_evidence import validate_reading
    require_problem(records)
    _validate_schema(value, stored=True)
    work = records.get("work", {}).get(value["version_id"])
    if (work is None or not value["version_id"].startswith("url:")
            or any(identifier.startswith(("doi:", "arxiv:")) for identifier in [work["id"], *work.get("aliases", [])])
            or work.get("doi") or work.get("arxiv_id")):
        raise ResearchError("problem_citation_work_ineligible", "Use an existing URL work without a DOI or arXiv identity",
                            {"version_id": value["version_id"]})
    resolved = [_resolve_span(records, artifacts, work, field_value, span) for field_value, span in _field_spans(value)]
    provenance = _field_map(value, [row[0] for row in resolved])
    if "provenance" in value and value["provenance"] != provenance:
        raise ResearchError("problem_citation_evidence_unbound", "The retained capture provenance has changed")
    for reading in records.get("reading", {}).values():
        if reading["version_id"] != value["version_id"] or reading["depth"] != "fulltext":
            continue
        try:
            validate_reading(records, artifacts, {"kind": "reading", "version_id": value["version_id"], "reading_id": reading["id"]})
            break
        except ResearchError as error:
            if error.code == "artifact_corrupt":
                raise
    else:
        raise ResearchError("problem_value_reading_missing", "Complete a valid full reading of this exact URL work",
                            {"version_id": value["version_id"]})
    return provenance, _field_map(value, [row[1] for row in resolved])


def record_citation_metadata(store, payload, *, expected_revision, request_id):
    artifacts = store.build_artifact_store()

    def prepare(records, value):
        require_problem(records)
        _validate_schema(value)
        provenance, _ = validate_citation_metadata(records, artifacts, value)
        saved = records.get("problem_citation_metadata", {}).get(value["id"])
        record = dict(value, provenance=provenance,
                      recorded_revision=saved["recorded_revision"] if saved else expected_revision + 1)
        return [immutable_record(records, "problem_citation_metadata", value["id"], record)], record

    return prepared_mutation(store, "problem.citation_metadata", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def describe_citation_metadata(records, artifacts, record):
    provenance, excerpts = validate_citation_metadata(records, artifacts, record)
    return dict({key: record[key] for key in ("id", "version_id", "title", "authors", "evidence", "year") if key in record},
                digest=digest(record), provenance=provenance, excerpts=excerpts,
                cite={"kind": "record", "record_kind": "problem_citation_metadata", "id": record["id"]})
