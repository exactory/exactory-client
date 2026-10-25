"""Exact complete source representations for observed native openness packets."""

import hashlib

from .errors import ResearchError
from .evidence import digest
from .operations import fields, strings

MISMATCH = "openness_source_delivery_mismatch"
CONTAINED = "complete-source-contained-in-required-proof"
ALTERNATE = "alternate-main-representation"


def require(condition, message, **details):
    if not condition:
        raise ResearchError(MISMATCH, message, details or None)


def required_proof(packet):
    reviews = packet["scientific_context"].get("native_reviews", [])
    if not isinstance(reviews, list) or any(not isinstance(row, dict) or "required_evidence_digests" not in row for row in reviews):
        raise ResearchError("openness_native_review_mismatch", "Native reviews must retain their exact required evidence")
    required = set()
    for row in reviews:
        strings(row["required_evidence_digests"], "Required native proof digests", nonempty=True,
                code="openness_native_review_mismatch")
        required.update(row["required_evidence_digests"])
    return required


def _containment(reference, data, containers):
    for identity, content in sorted(containers.items()):
        start = content.find(data)
        if start >= 0:
            return {"sha256": reference["sha256"], "container_sha256": identity,
                    "byte_start": start, "byte_end": start + len(data)}
    return None


def build_delivery(records, artifacts, packet):
    """Reconstruct selection from authenticated exact versions and current full readings."""
    from .bar_sources import _source, _capture_digest
    from .identities import version_of
    from .reading import fulltext_coverage
    from .source_links import captured_source
    required = required_proof(packet)
    references = {row["digest"]: row["artifact"] for row in packet["evidence"]}
    containers = {}
    for identity in sorted(required):
        data = artifacts.read(references[identity])
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        containers[identity] = data
    minimum = packet["scientific_context"]["openness_minimum"]
    selected_readings = {digest(row): row for row in records.get("reading", {}).values()
                         if digest(row) in minimum["reading_digests"]}
    require(set(selected_readings) == set(minimum["reading_digests"]), "Retain every exact full reading")
    from .problem_openness_screening import required_evidence
    sources, contained, alternate = [], {}, set()
    protected = required_evidence(packet["scientific_context"])
    for version in sorted({row["version_id"] for row in selected_readings.values()}):
        coverage = fulltext_coverage(records, artifacts, version)
        reading_digests = {digest(records["reading"][identity]) for identity in coverage["readings"]}
        bound = {identity for identity, row in selected_readings.items() if row["version_id"] == version}
        require(coverage["complete"] and reading_digests == bound,
                "Each held main representation needs its current full reading", version_id=version)
        delivery = _source(records, artifacts, {"id": "openness-source", "kind": "source",
                           "version_id": version, "depth": "fulltext"}, {})
        require(delivery["status"] == "delivered", "Resolve required source units before selecting a representation",
                version_id=version, pending=delivery["source_scope_pending"])
        alternatives = delivery["source_alternatives"]
        main = [row for row in alternatives if row["main_representation"]]
        for row in main:
            if version.startswith("arxiv:") or len(main) > 1:
                source = captured_source(records, artifacts, row["source_id"])
                require(source.get("observed_identifier") == version
                        and (not version.startswith("arxiv:") or version_of(version) is not None),
                        "Do not merge representations with absent or ambiguous exact-version identity", version_id=version)
        eligible = [row for row in alternatives if row["main_representation"] and not row["source_scope_pending"]]
        require(bool(eligible), "Select a complete authenticated main representation", version_id=version)
        spans = {}
        for row in eligible:
            reference = row["extraction_descriptor"]
            identity = reference["sha256"]
            require(identity in references, "The selected complete extraction must remain in the immutable packet")
            if identity not in required:
                span = _containment(reference, artifacts.read(references[identity]), containers)
                if span is not None:
                    spans[identity] = span
        selected = min(eligible, key=lambda row: (row["extraction_descriptor"]["sha256"] not in spans,
            row["original_descriptor"]["media_type"] != "application/pdf", row["source_id"]))
        selected_text = selected["extraction_descriptor"]["sha256"]
        protected.update((selected_text, selected["original_descriptor"]["sha256"]))
        if selected_text in spans:
            contained[selected_text] = spans[selected_text]
        for row in alternatives:
            if row["main_representation"] and row["source_id"] != selected["source_id"]:
                alternate.update((row["original_descriptor"]["sha256"], row["extraction_descriptor"]["sha256"]))
        # Explicit figures, formulas, tables, supplements and their assets are never alternate main bodies.
        for bundle in delivery["declared_bundles"]:
            for unit in bundle["units"]:
                link = unit["link"]
                if link is not None and unit["kind"] in {"figure", "table", "equation", "supplement"}:
                    protected.add(link["artifact_descriptor"]["sha256"])
                    protected.update(identity for row in delivery["source_provenance"]
                                     if row.get("bundle_id") == bundle["id"] and row.get("unit_id") == unit["id"]
                                     for identity in row["delivered_sha256"])
        for identity in bound:
            for inspection in selected_readings[identity]["inspections"]:
                protected.update(asset["artifact"]["sha256"] for asset in inspection["link"]["locator"].get("assets", []))
        representations = []
        for row in alternatives:
            source = captured_source(records, artifacts, row["source_id"])
            representations.append(dict(row, captured_at=source.get("captured_at"),
                capture_method=source["capture_method"], origin_verified=source["origin_verified"],
                observed_identifier=source.get("observed_identifier")))
        sources.append({"version_id": version, "selected_source_id": selected["source_id"],
            "selected_text_sha256": selected_text, "selected_original_sha256": selected["original_descriptor"]["sha256"],
            "capture_digest": _capture_digest(records["work"][version], records),
            "representations": representations})
    return {"schema_version": 1, "sources": sources,
            "contained_sources": [contained[key] for key in sorted(contained)],
            "alternate_body_digests": sorted(alternate - protected - required),
            "protected_source_digests": sorted(protected)}


def protected_evidence(supplied):
    packet = supplied.get("native") or {}
    context = packet.get("scientific_context", {})
    from .problem_openness_screening import required_evidence
    screening = required_evidence(context)
    delivery = context.get("openness_delivery")
    if delivery is None:
        return screening
    return screening | required_proof(packet) | set(delivery["protected_source_digests"])


def _complete(entry):
    from .review_transport import is_binary_evidence
    require(entry is not None and entry.get("encoding") == "utf-8" and isinstance(entry.get("content"), str),
            "Deliver the complete source or its complete required proof container")
    data = entry["content"].encode("utf-8")
    reference = entry["artifact"]
    require(not is_binary_evidence(data, reference["media_type"]), "Binary originals require native delivery")
    require(len(data) == reference["size"] and hashlib.sha256(data).hexdigest() == reference["sha256"],
            "Delivered source bytes must match their exact size and digest")
    return data


def _projection(supplied, *, projected):
    packet = supplied.get("native") or {}
    delivery = packet.get("scientific_context", {}).get("openness_delivery")
    if delivery is None:
        return None
    fields(delivery, ("schema_version", "sources", "contained_sources", "alternate_body_digests",
                     "protected_source_digests"), code=MISMATCH)
    require(type(delivery["schema_version"]) is int and delivery["schema_version"] == 1,
            "Use the exact versioned openness delivery contract")
    entries = {row["artifact"]["sha256"]: row for row in supplied.get("evidence", [])}
    selected = {row["selected_text_sha256"] for row in delivery["sources"]}
    required = required_proof(packet)
    alternate = set(delivery["alternate_body_digests"])
    protected = set(delivery["protected_source_digests"])
    require(not alternate & (required | protected | selected), "Required and selected sources cannot be omitted as alternatives")
    allowed_alternates = set()
    for source in delivery["sources"]:
        choices = [row for row in source["representations"] if row["source_id"] == source["selected_source_id"]]
        require(len(choices) == 1, "Select one exact main source identity")
        choice = choices[0]
        require(choice["main_representation"] and not choice["source_scope_pending"]
                and choice["version_id"] == source["version_id"] and choice["origin_verified"] is True
                and choice["capture_method"] == "http"
                and choice["extraction_descriptor"]["sha256"] == source["selected_text_sha256"]
                and choice["original_descriptor"]["sha256"] == source["selected_original_sha256"],
                "Select a complete authenticated representation of the same exact version")
        for row in source["representations"]:
            require(row["version_id"] == source["version_id"] and row["origin_verified"] is True
                    and row["capture_method"] == "http", "Alternative representations must retain the same authenticated exact version")
            if row["main_representation"] and (source["version_id"].startswith("arxiv:")
                    or sum(item["main_representation"] for item in source["representations"]) > 1):
                require(row.get("observed_identifier") == source["version_id"],
                        "Alternative main representations require an observed exact-version identity")
            if row["main_representation"] and row["source_id"] != source["selected_source_id"]:
                allowed_alternates.update((row["original_descriptor"]["sha256"], row["extraction_descriptor"]["sha256"]))
    require(alternate <= allowed_alternates and alternate <= set(entries), "Only declared alternate main bodies may be omitted")
    spans = {}
    for span in delivery["contained_sources"]:
        fields(span, ("sha256", "container_sha256", "byte_start", "byte_end"), code=MISMATCH)
        identity, container = span["sha256"], span["container_sha256"]
        start, end = span["byte_start"], span["byte_end"]
        require(identity in selected and identity not in required and identity not in spans
                and container in required and container != identity and container not in alternate,
                "Contain a complete selected extraction only in a directly delivered required proof artifact")
        require(type(start) is int and type(end) is int and 0 <= start < end,
                "Containment uses exact nonempty UTF-8 byte bounds")
        data = _complete(entries.get(container))
        require(identity in entries and end <= len(data), "The exact contained source and complete container must be retained")
        reference = entries[identity]["artifact"]
        source = data[start:end]
        require(len(source) == reference["size"] and hashlib.sha256(source).hexdigest() == identity,
                "The complete original source must match the exact container slice")
        if not projected:
            require(_complete(entries[identity]) == source, "The selected source must equal the exact complete container slice")
        else:
            require(entries[identity].get("delivery") == CONTAINED and entries[identity].get("content") == "",
                    "Identify each contained source explicitly in the actual request")
        spans[identity] = span
    for identity in selected - set(spans):
        _complete(entries.get(identity))
    if projected:
        for identity in alternate:
            require(entries[identity].get("delivery") == ALTERNATE and entries[identity].get("content") == "",
                    "Identify omitted alternate bodies explicitly in the actual request")
    return delivery, entries


def apply_delivery(supplied):
    """Project only an explicit new openness contract; retain every evidence descriptor."""
    projection = _projection(supplied, projected=False)
    if projection is None:
        return
    delivery, entries = projection
    for identity in delivery["alternate_body_digests"]:
        entries[identity].update(content="", encoding="omitted", delivery=ALTERNATE,
            note="Alternate main body omitted. The selected exact-version complete extraction is supplied; original layout is not asserted delivered.")
    for span in delivery["contained_sources"]:
        entries[span["sha256"]].update(content="", delivery=CONTAINED,
            note="The complete extraction is present at the declared UTF-8 byte bounds in the complete required proof artifact.")


def validate_delivery(records, artifacts, packet, state):
    delivery = packet["scientific_context"].get("openness_delivery")
    if delivery is None:
        return None
    require(delivery == build_delivery(records, artifacts, packet), "Reassess changed source captures, readings or containment")
    from .problem_openness_reviews import actual_delivery
    supplied = actual_delivery(records, artifacts, state, packet, required_proof(packet))
    _projection(supplied, projected=True)
    return delivery
