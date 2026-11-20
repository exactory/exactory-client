"""Choose a main extraction without changing captures or their source links."""

import json

from .components import is_component
from .errors import ResearchError
from .evidence import digest
from .operations import fields, normalized_text, prepared_mutation, text
from .source_links import exact_work, fulltext_capture
from .storage import overlay_kinds


PROTOCOL = "exactory-main-capture-selection-v1"
SELECTION_KIND = "main_capture_selection"
PACKET_KEY = "main_capture_selections"


def find_capture_selections(records, version_id=None):
    """Find the current explicit choices, optionally for one exact version."""
    return [choice for choice in records.get(SELECTION_KIND, {}).values()
            if version_id is None or choice["version_id"] == version_id]


def select_main_capture(store, payload, *, expected_revision, request_id):
    def prepare(records, value):
        fields(value, ("version_id", "source_id", "reason"))
        for field in ("version_id", "source_id", "reason"):
            text(value[field], field)
        work = exact_work(records, value["version_id"])
        capture = fulltext_capture(work, value["source_id"])
        if (capture is None or is_component(capture) or capture.get("availability") != "available"
                or capture.get("extraction_status") != "extracted"
                or capture.get("original") is None or capture.get("text") is None):
            raise ResearchError("invalid_main_capture",
                                "source_id does not name a capture that can be the main capture of this version. "
                                "A main capture is a full-text capture of this exact version that is available, has "
                                "an extracted text and is not a component. Give the source_id of such a capture.")
        from .graph import index_bundles
        supplements = index_bundles(records).get(work["id"], {}).get("supplements", set())
        if capture["source_id"] in supplements:
            raise ResearchError("invalid_main_capture",
                                "source_id names a capture that a source bundle of this version links as a "
                                "supplement, and a supplement cannot be the main capture. Give the source_id of a "
                                "capture that no source bundle of this version links as a supplement.")
        artifacts = store.build_artifact_store()
        artifacts.read(capture["original"])
        artifacts.read(capture["text"])
        choice = dict(value, version_id=work["id"], original_sha256=capture["original"]["sha256"],
                      text_sha256=capture["text"]["sha256"], protocol=PROTOCOL)
        key = digest([work["id"], choice["original_sha256"]])
        return [(SELECTION_KIND, key, choice)], choice

    return prepared_mutation(store, "fulltext.select_main", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def describe_capture_selections(records):
    """Bind the choices used to construct a new packet under their own protocol."""
    choices = find_capture_selections(records)
    return {PACKET_KEY: {"protocol": PROTOCOL, "choices": sorted(
        choices, key=lambda choice: (choice["version_id"], choice["original_sha256"]))}} if choices else {}


def bind_packet_selections(records, packet, *, artifacts=None, review=None):
    """Keep explicit bindings, or derive a legacy packet's captures from its own review.

    The bound view keeps the history of the store, which the verification of a carried selection reads.
    """
    if SELECTION_KIND not in records:
        return records
    inventory = packet.get("source_inventory", {})
    binding = packet.get(PACKET_KEY, inventory.get(PACKET_KEY, {}))
    choices = (binding.get("choices", []) if PACKET_KEY in packet or PACKET_KEY in inventory else
               derive_review_selections(records, packet, artifacts, review))
    return overlay_kinds(records, {SELECTION_KIND: {
        digest([choice["version_id"], choice["original_sha256"]]): choice for choice in choices}})


def list_bound_captures(records, packet):
    """Group eligible main extractions by the bound reading version and original bytes."""
    from .graph import main_captures, index_bundles
    bound = set(packet.get("openness_minimum", {}).get("reading_digests", []))
    versions = {row["version_id"] for row in records.get("reading", {}).values() if digest(row) in bound}
    first_records = dict(records, **{SELECTION_KIND: {}})
    bundles = index_bundles(records)
    candidates = {}
    for version in versions:
        work = records.get("work", {}).get(version)
        if work is None:
            continue
        originals = {capture["original"]["sha256"] for capture in main_captures(first_records, work)}
        supplements = bundles.get(version, {}).get("supplements", set())
        for capture in work["fulltexts"]:
            if (not is_component(capture) and capture.get("availability") == "available"
                    and capture.get("extraction_status") == "extracted" and capture.get("text")
                    and capture["source_id"] not in supplements and capture.get("original")
                    and capture["original"]["sha256"] in originals):
                candidates.setdefault((version, capture["original"]["sha256"]), []).append(capture)
    return candidates


def derive_review_selections(records, packet, artifacts, review):
    """Resolve capture inventory from the contract, recorded plans, then planless observed inputs."""
    candidates = list_bound_captures(records, packet)
    if not candidates:
        return []
    delivery = packet.get("openness_delivery")
    stored, requests, carried = ([], [], set()) if delivery is not None else read_review_packets(records, artifacts, review)
    plans = [value["reading_turn"]["reading_plan"] for value in stored + requests
             if "reading_plan" in value.get("reading_turn", {})]
    planned = find_plan_texts(plans, carried)
    choices = []
    for (version, original), captures in candidates.items():
        if delivery is not None:
            source_ids = {row["source_id"] for source in delivery["sources"] if source["version_id"] == version
                          for row in source["representations"] if row["main_representation"]
                          and row["original_descriptor"]["sha256"] == original}
            shown = [capture for capture in captures if capture["source_id"] in source_ids]
        elif plans:
            shown = [capture for capture in captures if (version, capture["text"]["sha256"]) in planned]
        else:
            shown = [capture for capture in captures
                     if any(has_delivered_capture(artifacts, sent, capture) for sent in requests)]
        if len({capture["text"]["sha256"] for capture in shown}) == 1:
            capture = shown[0]
            choices.append(dict(version_id=version, original_sha256=original, source_id=capture["source_id"],
                                text_sha256=capture["text"]["sha256"]))
    return choices


def read_review_packets(records, artifacts, review):
    """Read digest-bound stored packets regardless of readiness, and authenticated ready requests."""
    from .review_protocol import assignment_state
    from .review_excerpts import read_sent_packet
    if artifacts is None or review is None:
        return [], [], set()
    native = json.loads(artifacts.read(review["context"]["native_packet"]))
    carried = {row["artifact"]["sha256"] for row in native["evidence"]}
    stored, requests, seen = [], [], set()
    current = review
    while current is not None and current["id"] not in seen:
        seen.add(current["id"])
        if (current.get("context", {}).get("native_packet") != review["context"].get("native_packet")
                or any(current.get(key) != review.get(key) for key in ("role", "route_id"))
                or any(normalized_text(current[key]) != normalized_text(review[key])
                       for key in ("reviewer_id", "author_id"))):
            break
        if records.get("review_assignment", {}).get(current["id"]) == current:
            saved = json.loads(artifacts.read(current["packet"]))
            if (saved.get("digest") == current["packet_digest"]
                    and digest({key: value for key, value in saved.items() if key != "digest"}) == current["packet_digest"]):
                stored.append(saved)
            state = assignment_state(records, artifacts, current["id"])
            if state["ready"]:
                attempt = records["review_attempt"][state["attempt_id"]]
                sent = read_sent_packet(json.loads(artifacts.read(attempt["request"])))
                if sent is not None:
                    requests.append(sent)
        current = records.get("review_assignment", {}).get(current.get("context", {}).get("prior_assignment_id"))
    return stored, requests, carried


def find_plan_texts(plans, carried):
    """Identify every carried full text named as delivered or pending by this chain's recorded plans."""
    return {(row["version_id"], row["text_sha256"]) for plan in plans
            for field in ("delivered_before", "delivered_now", "remaining") for row in plan.get(field, [])
            if row.get("kind", "fulltext") == "fulltext" and row.get("text_sha256") in carried}


def has_delivered_capture(artifacts, sent, capture):
    """Recognize exact whole bodies or exact parts for inventory selection in a review without plans."""
    from .review_excerpts import find_complete_bodies, compute_part_digests
    complete = find_complete_bodies(sent)
    reference = capture["text"]
    return (reference["sha256"] in complete or bool(complete.intersection(
        compute_part_digests(artifacts.read(reference).decode("utf-8")))))


def find_replaced_texts(records, work):
    """Find extraction bodies replaced for delivery, retaining any identical selected body."""
    from .graph import index_bundles
    choices = {choice["original_sha256"]: choice for choice in find_capture_selections(records, work["id"])}
    if not choices:
        return set()
    supplements = index_bundles(records).get(work["id"], {}).get("supplements", set())
    kept = {capture["text"]["sha256"] for capture in work.get("fulltexts", [])
            if capture.get("original") and capture.get("text") and capture.get("availability") == "available"
            and (is_component(capture) or capture["source_id"] in supplements or capture["original"]["sha256"] not in choices
                 or capture["source_id"] == choices[capture["original"]["sha256"]]["source_id"])}
    return {capture["text"]["sha256"] for capture in work.get("fulltexts", [])
            if not is_component(capture) and capture["source_id"] not in supplements
            and capture.get("original") and capture.get("text")
            and capture["original"]["sha256"] in choices
            and capture["source_id"] != choices[capture["original"]["sha256"]]["source_id"]} - kept
