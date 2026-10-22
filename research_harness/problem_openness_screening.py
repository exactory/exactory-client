"""Deliver exact captured screening evidence without asserting full reading."""

import json

from .artifacts import describe_artifact
from .errors import ResearchError
from .evidence import digest
from .operations import fields, text
from .source_links import captured_source, read_locator

MISMATCH = "openness_screening_evidence_mismatch"
LIMITATION = (
    "This evidence supplies only the captured response or its explicitly selected span. "
    "It does not establish complete article access or fulltext reading. "
    "The source record retains the acquisition method and its actual origin verification status."
)


def require(condition, message):
    if not condition:
        raise ResearchError(MISMATCH, message)


def resolve_source_evidence(records, artifacts, bindings, *, materialize=False):
    """Recompute immutable bindings from current common records and original bytes."""
    require(isinstance(bindings, list) and bool(bindings), "Source evidence must be a nonempty list")
    result, evidence, seen = [], {}, set()

    def include(data, media_type):
        reference = artifacts.put(data, media_type) if materialize else describe_artifact(data, media_type)
        evidence[reference["sha256"]] = {"digest": reference["sha256"], "kind": "artifact", "artifact": reference}
        return reference

    for binding in bindings:
        fields(binding, ("source_id", "reason"), ("locator",), code=MISMATCH)
        text(binding["source_id"], "Screening source ID", code=MISMATCH)
        text(binding["reason"], "Screening evidence reason", code=MISMATCH)
        require(binding["source_id"] not in seen, "Bind each screening source once")
        seen.add(binding["source_id"])
        source = captured_source(records, artifacts, binding["source_id"])
        original = source["response"]
        data = artifacts.read(original)
        scope, media_type = "complete_captured_response", original["media_type"]
        if "locator" in binding:
            locator = binding["locator"]
            require(isinstance(locator, dict) and locator.get("kind") == "span",
                    "Select an exact captured UTF-8 span using its bounds and hash")
            data = read_locator(artifacts, original, locator).encode("utf-8")
            scope, media_type = "exact_captured_response_span", "text/plain"
        supplied = include(data, media_type)
        provenance = include(json.dumps(source, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False).encode("utf-8"), "application/json")
        result.append(dict(binding, source_digest=digest(source), source_record=provenance,
            delivered_artifact=supplied, scope=scope, reading_credit=False, limitation=LIMITATION))
    return result, evidence


def required_evidence(context):
    """The screening text and exact acquisition record must survive route fitting."""
    if "source_evidence" not in context:
        return set()
    rows = context["source_evidence"]
    require(isinstance(rows, list) and bool(rows), "Source evidence must retain its canonical bindings")
    required = set()
    for row in rows:
        require(isinstance(row, dict), "Source evidence bindings must be objects")
        for key in ("source_record", "delivered_artifact"):
            reference = row.get(key)
            require(isinstance(reference, dict) and isinstance(reference.get("sha256"), str),
                    "Source evidence must identify its exact supplied artifacts")
            required.add(reference["sha256"])
    return required


def validate_source_evidence(records, artifacts, packet, state):
    if "source_evidence" not in packet["scientific_context"]:
        return None
    supplied = packet["scientific_context"]["source_evidence"]
    required = required_evidence(packet["scientific_context"])
    bindings = []
    for row in supplied:
        require("source_id" in row and "reason" in row, "Retain the exact screening-source selection")
        bindings.append({key: row[key] for key in ("source_id", "reason", "locator") if key in row})
    current, _ = resolve_source_evidence(records, artifacts, bindings)
    require(current == supplied, "Reassess changed source records, selections or original response bytes")
    from .problem_openness_reviews import actual_delivery
    actual = actual_delivery(records, artifacts, state, packet, required)
    require(actual.get("native", {}).get("scientific_context", {}).get("source_evidence") == supplied,
            "The authenticated request must retain the exact screening-source context")
    return current
