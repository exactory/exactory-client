"""Choose a main extraction without changing captures or their source links."""

from .components import is_component
from .errors import ResearchError
from .evidence import digest
from .operations import fields, prepared_mutation, text
from .source_links import exact_work, fulltext_capture


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
            raise ResearchError("invalid_main_capture", "Choose an available main-document capture of this exact version")
        from .graph import index_bundles
        supplements = index_bundles(records).get(work["id"], {}).get("supplements", set())
        if capture["source_id"] in supplements:
            raise ResearchError("invalid_main_capture", "A declared supplement cannot be selected as the main document")
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


def bind_packet_selections(records, packet):
    """Match a parent output against that packet's choices; legacy packets used first captures."""
    if SELECTION_KIND not in records:
        return records
    binding = packet.get(PACKET_KEY, packet.get("source_inventory", {}).get(PACKET_KEY, {}))
    choices = binding.get("choices", [])
    return dict(records, **{SELECTION_KIND: {
        digest([choice["version_id"], choice["original_sha256"]]): choice for choice in choices}})


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
