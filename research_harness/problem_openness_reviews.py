"""Bind observed native proof reviews and their delivered bytes to openness."""

from .artifacts import describe_artifact
from .errors import ResearchError
from .evidence import digest
from .native_review import load_packet, require_assignment_identity
from .operations import fields, iso_date, strings, text
from .problem_attempt import find_current_attempt, validate_attempt
from .workspace import read_file, strict_json

MISMATCH = "openness_native_review_mismatch"
MISSING = "openness_native_evidence_missing"
UNDELIVERED = "openness_native_evidence_undelivered"

ACCEPTED_RESULT_NOTE = (
    "A solver objective of this record accepted this result after the review that review names. The result is "
    "delivered so that the reviewer can read what the search records cite. The acceptance is no finding about the "
    "openness of the statement under review.")
RESULT_UNLINKED_MESSAGE = (
    "The store does not hold exactly one link from this acceptance to the review that it rests on. When the "
    "acceptance rests on an observed review assignment, run search deliver-attempt in the objective of the result, "
    "then export again. The delivery writes no link for an earlier review without an observed assignment: obtain "
    "and deliver a new acceptance that rests on an observed review. When several links match, inspect the store "
    "with record-check.")
RESULT_UNDELIVERED_MESSAGE = (
    "An artifact of this accepted result with the role proof or theorem cannot be delivered as text. The export "
    "reads the artifacts that the accepted checkpoint pins. Obtain and deliver a new accepted checkpoint whose "
    "proof and theorem artifacts are text and bind that acceptance, or export without the result when no bound "
    "search cites it.")
RESULT_CHANGED_MESSAGE = (
    "The latest delivered snapshot of the objective no longer holds this acceptance with the status accepted, the "
    "same checkpoint id, the same checkpoint claim and the same evidence digests. Run search deliver-attempt in "
    "the objective, then export again.")
RESULT_DESCRIBED_MESSAGE = (
    "This part of the accepted result is described by its path, role, media type, size and digest. The row adds "
    "no content as scientific text. Another delivered part can hold the same bytes.")
RESULT_BINDING_MESSAGE = (
    "This accepted result does not match its acceptance-review link, its reviewed checkpoint or its delivered "
    "material. For an export, run search deliver-attempt in the objective of the result and export again. For a "
    "recorded finding, inspect the store with record-check.")
RESULT_MANIFEST_MESSAGE = (
    "A manifest of this accepted result has a form that cannot be delivered. An analytical manifest has "
    "verification null, and a certificate or lean manifest holds its verification record. Deliver the result with "
    "such manifests, or export without the result.")
PROOF_ROLES = frozenset(("proof", "theorem"))


def find_result_metadata_fields(value):
    """Identify descriptor containers whose logical paths do not address stored bytes."""
    if {"cited_as", "native_attempt_digest", "acceptance", "checkpoint", "review", "manifests",
            "artifacts", "note"} <= value.keys():
        return ("artifacts",)
    if value.get("condition") == "result_described":
        return ("artifact",)
    if value.get("condition") == "result_undelivered":
        return ("details",)
    return ()


def has_bound_results(value):
    """Select the new descriptor policy only for a packet or finding that binds v2 results."""
    if not isinstance(value, dict):
        return False
    for key in ("native", "scientific_context"):
        if isinstance(value.get(key), dict):
            value = value[key]
    minimum = value.get("openness_minimum", value.get("minimum", {}))
    return isinstance(minimum, dict) and minimum.get("schema_version") == 2 and bool(minimum.get("results"))


def require(condition, message, code=MISMATCH, **details):
    if not condition:
        raise ResearchError(code, message, details or None)


def observed_review(records, artifacts, assignment_id):
    from .review_protocol import assignment_state
    state = assignment_state(records, artifacts, assignment_id)
    assignment = records.get("review_assignment", {}).get(assignment_id)
    require(state["ready"] and assignment is not None and assignment["role"] == "native_math",
            "Obtain the currently verified observed native review", "openness_native_review_unverified",
            assignment_id=assignment_id, obligations=state["obligations"])
    packet = load_packet(artifacts, assignment["context"]["native_packet"], assignment["reviewer_id"])
    require_assignment_identity(packet, assignment)
    return assignment, state, packet


def validate_approval(packet, output):
    fields(output, ("schema_version", "subject_digest", "claim_digest", "reviewer", "decision", "findings"), code=MISMATCH)
    require(type(output["schema_version"]) is int and output["schema_version"] == 2
            and output["decision"] == "approve" and packet["kind"] == "result"
            and all(output[key] == packet[key] for key in ("subject_digest", "claim_digest", "reviewer")),
            "Bind an observed version 2 approval to the complete proof checkpoint")
    findings = output["findings"]
    fields(findings, ("statement", "assumptions", "scope", "dependencies", "policy", "openness"), code=MISMATCH)
    for key in ("statement", "assumptions", "scope", "dependencies", "policy"):
        text(findings[key], "Native result finding " + key, code=MISMATCH)
    openness = findings["openness"]
    fields(openness, ("outcome", "statement_digest", "evidence", "searched_at", "coverage", "reason"), ("minimum",), code=MISMATCH)
    require(openness["outcome"] in {"supported_open", "already_solved", "unresolved"}
            and openness["statement_digest"] == packet["claim_digest"], "The review must retain its exact openness finding")
    strings(openness["evidence"], "Native openness evidence", nonempty=True, code=MISMATCH)
    require(set(openness["evidence"]) <= {row["digest"] for row in packet["evidence"]},
            "Native openness evidence must be in the observed packet")
    for key in ("coverage", "reason"):
        text(openness[key], "Native openness " + key, code=MISMATCH)
    iso_date(openness["searched_at"])
    if "minimum" in openness:
        from .openness_minimum import validate_minimum
        validate_minimum(openness["minimum"])


def required_evidence(artifacts, packet, native, *, result_kinds=False):
    """Walk bounded scientific dependencies; result mode also returns the manifest identities."""
    entries = {row["digest"]: row for row in packet["evidence"]}
    executables = {row["digest"] for row in packet["execution_provenance"]}
    accepted = {row["id"]: row for row in native["acceptances"]}
    required, visited, visiting = set(), set(), set()

    def include(identity):
        require(identity in entries, "Deliver every scientific proof dependency", MISSING, digest=identity)
        require(len(required) < 4096 or identity in required, "The scientific dependency closure exceeds its bound", MISSING)
        required.add(identity)
        return artifacts.read(entries[identity]["artifact"])

    def visit(identity, depth=0, expected_claim=None):
        require(depth <= 64 and identity not in visiting, "Scientific manifest dependencies must be bounded and acyclic", MISSING)
        manifest = strict_json(include(identity))
        fields(manifest, ("schema_version", "kind", "claim_digest", "conclusion", "artifacts", "dependencies",
                          "external_dependencies", "verification"), code=MISSING)
        require(manifest["schema_version"] == 1, "Use a native scientific evidence manifest", MISSING)
        require((manifest["kind"] == "analytical" and manifest["verification"] is None)
                or (result_kinds and manifest["kind"] in ("certificate", "lean")
                    and isinstance(manifest["verification"], dict)),
                RESULT_MANIFEST_MESSAGE if result_kinds else
                "Native openness support currently requires analytical manifests without execution verification",
                MISSING if result_kinds else "openness_native_evidence_unsupported")
        if expected_claim is not None:
            require(manifest["claim_digest"] == expected_claim, "The proof manifest must bind the reviewed claim",
                    MISSING if result_kinds else MISMATCH)
        if identity in visited:
            return
        visiting.add(identity)
        fields(manifest["conclusion"], ("outcome", "dependency_ids", "route_bindings"), code=MISSING)
        strings(manifest["dependencies"], "Manifest dependencies", code=MISSING)
        strings(manifest["conclusion"]["dependency_ids"], "Accepted dependencies", code=MISSING)
        require(isinstance(manifest["artifacts"], list) and bool(manifest["artifacts"]),
                "A scientific manifest requires artifacts", MISSING)
        for row in manifest["artifacts"]:
            fields(row, ("path", "digest", "role"), code=MISSING)
            include(row["digest"])
        require(isinstance(manifest["external_dependencies"], list), "External dependencies must be explicit", MISSING)
        for row in manifest["external_dependencies"]:
            fields(row, ("path", "digest"), code=MISSING)
            if row["digest"] not in executables:
                include(row["digest"])
        for child in manifest["dependencies"]:
            visit(child, depth + 1)
        for acceptance_id in manifest["conclusion"]["dependency_ids"]:
            acceptance = accepted.get(acceptance_id)
            require(acceptance is not None and acceptance["status"] == "accepted",
                    "Bind scientific dependencies to the current accepted native snapshot", MISSING)
            strings(acceptance["evidence_digests"], "Accepted evidence", nonempty=True, code=MISSING)
            for child in acceptance["evidence_digests"]:
                visit(child, depth + 1)
        visiting.remove(identity)
        visited.add(identity)

    checkpoint = packet["subject"]
    strings(checkpoint["evidence_digests"], "Checkpoint manifests", nonempty=True, code=MISSING)
    for identity in checkpoint["evidence_digests"]:
        visit(identity, expected_claim=packet["claim_digest"])
    return (sorted(required), sorted(visited)) if result_kinds else sorted(required)


def validate_result_specs(bindings):
    """Validate the optional export list once at its command boundary."""
    import re
    require(isinstance(bindings, list), "minimum.results is not a list. Give a list of "
            "{native_attempt_digest, acceptance_id, cited_as} entries, or leave the field out.", "invalid_input")
    seen = set()
    for binding in bindings:
        fields(binding, ("native_attempt_digest", "acceptance_id", "cited_as"))
        for name in binding:
            text(binding[name], "Accepted result " + name)
        require(re.fullmatch(r"[0-9a-f]{64}", binding["native_attempt_digest"]) is not None,
                "native_attempt_digest of a result is not a SHA-256 digest. Give the digest that search "
                "deliver-attempt returned for the objective of the result.", "invalid_input")
        key = (binding["native_attempt_digest"], binding["acceptance_id"])
        require(key not in seen, "minimum.results names one acceptance twice. Bind each accepted result once.",
                "invalid_input")
        seen.add(key)


def resolve_result_review(records, artifacts, binding, *, current=False):
    """Resolve the acceptance's own observed review, independently of the current problem attempt."""
    from .problem_attempt import find_latest_native_attempt
    from .review_protocol import assignment_state
    identifier = binding["native_attempt_digest"]
    native = records.get("native_attempt", {}).get(identifier)
    latest = None if native is None else find_latest_native_attempt(records, native)[0]
    require(native is not None and (not current or latest is not None),
            "The store cannot resolve this delivered attempt, or the current snapshot of its objective. Run "
            "search deliver-attempt in the objective of the result and give the digest that it returns.",
            "problem_result_unbound", native_attempt_digest=identifier)
    require(not current or latest == identifier,
            "The objective of this result has a later delivered snapshot. Give the native_attempt_digest in "
            "details.latest_native_attempt_digest, then export again.",
            "problem_result_superseded", native_attempt_digest=identifier, latest_native_attempt_digest=latest)
    acceptance = next((row for row in native["acceptances"] if row["id"] == binding["acceptance_id"]), None)
    require(acceptance is not None and acceptance["status"] == "accepted"
            and acceptance["outcome"] in ("proof", "reduction", "obstruction", "counterexample"),
            "The snapshot holds no acceptance with this id that has the status accepted and the outcome proof, "
            "reduction, obstruction or counterexample. Bind such an acceptance.",
            "problem_result_missing", native_attempt_digest=identifier, acceptance_id=binding["acceptance_id"])
    links = [row for row in records.get("native_acceptance_review", {}).values()
             if all(row[name] == native[name] for name in ("attack_root", "objective_id"))
             and row["acceptance_id"] == acceptance["id"]
             and row["checkpoint_id"] == acceptance["checkpoint_id"]
             and row["evidence_digests"] == acceptance["evidence_digests"]]
    require(len(links) == 1, RESULT_UNLINKED_MESSAGE, "openness_result_unlinked",
            native_attempt_digest=identifier, acceptance_id=acceptance["id"], command="search deliver-attempt",
            then="export")
    link = links[0]
    review = link["review"]
    final = records.get("review_assignment", {}).get(review["final_assignment_id"])
    state = assignment_state(records, artifacts, review["final_assignment_id"]) if final is not None else {}
    require(final is not None and final["role"] == "native_math" and state.get("ready")
            and state.get("attempt_id") == review["attempt_id"]
            and state.get("output_digest") == review["output_digest"]
            and state["output"].get("decision") == "approve"
            and state["output"].get("reviewer", {}).get("attestation_id") == "review-assignment:" + review["assignment_id"],
            RESULT_BINDING_MESSAGE, MISSING, acceptance_id=acceptance["id"])
    packet = load_packet(artifacts, final["context"]["native_packet"], final["reviewer_id"])
    require_assignment_identity(packet, final)
    checkpoint = next((row for row in native["checkpoints"] if row["id"] == acceptance["checkpoint_id"]), None)
    require(packet["kind"] == "result" and packet["root"] == native["attack_root"]
            and packet["objective_id"] == native["objective_id"] and packet["contract"] == native["contract"]
            and checkpoint is not None and packet["subject"].get("id") == checkpoint["id"]
            and packet["subject"].get("kind") == checkpoint["kind"]
            and packet["subject"].get("claim") == checkpoint["claim"]
            and packet["subject"].get("evidence_digests") == acceptance["evidence_digests"]
            and packet["claim_digest"] == digest(checkpoint["claim"]), RESULT_BINDING_MESSAGE, MISSING)
    return native, acceptance, link, packet


def read_result_artifact(artifacts, identity, media_type):
    """Read a digest-only result pin through the checked workspace filesystem boundary."""
    from .artifacts import _OBJECT_DIRECTORY
    data = read_file(artifacts.root, _OBJECT_DIRECTORY + "/" + identity)
    reference = describe_artifact(data, media_type)
    require(reference["sha256"] == identity, RESULT_BINDING_MESSAGE, MISSING)
    return reference, data


def describe_result_material(artifacts, binding, native, acceptance, link, packet, *, retained=None):
    """Describe the linked closure and prepare its scientific texts, with no filesystem writes.

    A retained descriptor pins a PDF extraction. Replay reads those exact text bytes
    rather than depending on a later installation of the PDF extraction tools.
    """
    from .fulltext import detect_media_type, extract, parse_base_media_type
    _, manifest_ids = required_evidence(artifacts, packet, native, result_kinds=True)
    entries = {row["digest"]: row for row in packet["evidence"]}
    executables = {row["digest"]: row for row in packet["execution_provenance"]}
    manifests, parts, evidence, generated = [], [], {}, {}
    retained_parts = {(row["sha256"], row["path"], row["role"]): row
                      for row in (retained or {}).get("artifacts", [])}

    def include_text(data, media_type):
        reference = describe_artifact(data, media_type)
        evidence[reference["sha256"]] = dict(digest=reference["sha256"], kind="artifact", artifact=reference)
        generated[reference["sha256"]] = (data, media_type)
        return reference

    def describe_part(row):
        identity = row["digest"]
        reference = entries[identity]["artifact"] if identity in entries else None
        part = dict(sha256=identity, path=row["path"], role=row.get("role", "dependency"),
                    media_type=reference["media_type"] if reference is not None else "application/octet-stream",
                    size=reference["size"] if reference is not None else executables[identity]["size"], delivery="described")
        if identity in executables:
            part["role"] = "executable"
        if identity in executables or part["role"] == "toolchain":
            return part
        data = artifacts.read(reference)
        media, _ = detect_media_type(data, parse_base_media_type(reference["media_type"]))
        if media == "application/pdf":
            previous = retained_parts.get((identity, part["path"], part["role"]))
            if retained is not None:
                require(previous is not None, RESULT_BINDING_MESSAGE, MISSING)
                text_id = previous.get("text_sha256")
                if text_id is not None:
                    text_ref, content = read_result_artifact(artifacts, text_id, "text/plain")
                    include_text(content, text_ref["media_type"])
                    part.update(delivery="pdf_text", text_sha256=text_id)
            else:
                extracted = extract(data, reference["media_type"])
                if extracted["status"] == "extracted":
                    text_ref = include_text(extracted["text"].encode("utf-8"), "text/plain")
                    part.update(delivery="pdf_text", text_sha256=text_ref["sha256"])
        else:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                pass
            else:
                part["delivery"] = "text"
                evidence[identity] = entries[identity]
        return part

    for identity in manifest_ids:
        entry = entries[identity]
        manifest = strict_json(artifacts.read(entry["artifact"]))
        manifests.append(dict(sha256=identity, kind=manifest["kind"]))
        evidence[identity] = entry
        for row in manifest["artifacts"]:
            parts.append(describe_part(row))
        for row in manifest["external_dependencies"]:
            parts.append(describe_part(row))
    # Executables need not be in scientific evidence, but their identities still reach the reviewer.
    parts.extend(describe_part(dict(row, role="executable")) for row in packet["execution_provenance"])
    parts = list({(row["sha256"], row["path"], row["role"]): row for row in parts}.values())
    checkpoint = next(row for row in native["checkpoints"] if row["id"] == acceptance["checkpoint_id"])
    body = dict(cited_as=binding["cited_as"], native_attempt_digest=binding["native_attempt_digest"],
        objective_id=native["objective_id"], objective_claim=native["contract"]["original_claim"],
        acceptance={key: acceptance[key] for key in
                    ("id", "outcome", "standard", "status", "classification", "checkpoint_id", "evidence_digests")},
        checkpoint={key: checkpoint[key] for key in ("id", "kind", "claim", "verification_status")},
        review=link["review"], manifests=manifests, artifacts=parts, note=ACCEPTED_RESULT_NOTE)
    if retained is not None:
        require(body == retained, RESULT_BINDING_MESSAGE, MISSING)
    return body, evidence, generated


def bind_results(records, artifacts, bindings):
    """Bind current accepted results and publish their immutable descriptors and extracted texts."""
    from .problem_openness import serialize_record
    pinned, evidence, generated = [], {}, {}
    for binding in bindings:
        native, acceptance, link, packet = resolve_result_review(records, artifacts, binding, current=True)
        body, texts, prepared = describe_result_material(artifacts, binding, native, acceptance, link, packet)
        data = serialize_record(body)
        reference = describe_artifact(data, "application/json")
        pinned.append(dict(binding, link_digest=digest(link), result_digest=reference["sha256"]))
        evidence.update(texts)
        evidence[reference["sha256"]] = dict(digest=reference["sha256"], kind="blob", artifact=reference)
        generated.update(prepared)
        generated[reference["sha256"]] = (data, "application/json")
    for data, media_type in generated.values():
        artifacts.put(data, media_type)
    return pinned, evidence


def validate_bound_results(minimum, records, artifacts):
    """Verify immutable pins against their linked reviews without consulting a later objective snapshot."""
    bodies = []
    for binding in minimum.get("results", []):
        native, acceptance, link, packet = resolve_result_review(records, artifacts, binding)
        require(digest(link) == binding["link_digest"], RESULT_BINDING_MESSAGE, MISSING)
        _, data = read_result_artifact(artifacts, binding["result_digest"], "application/json")
        body = strict_json(data)
        fields(body, ("cited_as", "native_attempt_digest", "objective_id", "objective_claim", "acceptance",
                      "checkpoint", "review", "manifests", "artifacts", "note"), code=MISSING)
        require(isinstance(body["artifacts"], list), RESULT_BINDING_MESSAGE, MISSING)
        for part in body["artifacts"]:
            fields(part, ("sha256", "path", "role", "media_type", "size", "delivery"), ("text_sha256",), code=MISSING)
        describe_result_material(artifacts, binding, native, acceptance, link, packet, retained=body)
        bodies.append(body)
    return bodies


def has_result_changed(records, binding, body):
    """Assess the latest acceptance separately from the immutable evidence delivered at export."""
    from .problem_attempt import find_latest_native_attempt
    native = records.get("native_attempt", {}).get(binding["native_attempt_digest"])
    latest = None if native is None else find_latest_native_attempt(records, native)[1]
    acceptance = next((row for row in (latest or {}).get("acceptances", [])
                       if row["id"] == binding["acceptance_id"]), None)
    checkpoint = next((row for row in (latest or {}).get("checkpoints", [])
                       if row["id"] == body["checkpoint"]["id"]), None)
    return (acceptance is None or acceptance["status"] != "accepted"
            or acceptance["checkpoint_id"] != body["checkpoint"]["id"]
            or acceptance["evidence_digests"] != body["acceptance"]["evidence_digests"]
            or checkpoint is None or checkpoint["claim"] != body["checkpoint"]["claim"])


def actual_delivery(records, artifacts, state, packet, required):
    """Inspect the authenticated current request, including its exact binary attachments."""
    from .review_input_projection import collect_attachments, find_attachment, project_evidence
    from .review_transport import PACKET_LABEL
    attempt = records["review_attempt"][state["attempt_id"]]
    request = strict_json(artifacts.read(attempt["request"]))
    messages = request.get("input", [])
    require(isinstance(messages, list) and bool(messages) and messages[-1].get("role") == "user",
            "The observed request must deliver its current packet", UNDELIVERED)
    content = messages[-1].get("content")
    attachments = {}
    if isinstance(content, list):
        texts = [part["text"] for part in content if part.get("type") == "input_text"]
        require(len(texts) == 1, "The observed request must identify one current packet", UNDELIVERED)
        attachments = collect_attachments(content)
        content = texts[0]
    require(isinstance(content, str) and content.startswith(PACKET_LABEL),
            "The observed request must identify its supplied packet", UNDELIVERED)
    supplied = strict_json(content[len(PACKET_LABEL):].encode())
    actual = supplied.get("native", {})
    # Artifact descriptor presentation can differ, but scientific identities and findings cannot.
    require(all(actual.get(key) == packet[key] for key in
                ("root", "objective_id", "contract", "subject", "subject_digest", "claim_digest", "reviewer")),
            "The actual request must retain the exact reviewed native subject", UNDELIVERED)
    if "native_reviews" in packet["scientific_context"]:
        require(actual.get("scientific_context", {}).get("native_reviews") == packet["scientific_context"]["native_reviews"],
                "The actual request must retain the complete observed supporting reviews", UNDELIVERED)
    if "openness_delivery" in packet["scientific_context"]:
        require(actual.get("scientific_context", {}).get("openness_delivery") == packet["scientific_context"]["openness_delivery"],
                "The actual request must retain the exact source representation contract", UNDELIVERED)
    entries = {row["digest"]: row for row in packet["evidence"]}
    project_evidence(supplied, attachments)
    supplied_entries = supplied.get("evidence", [])
    for identity in required:
        require(identity in entries, "The openness packet must retain every scientific dependency", MISSING, digest=identity)
        reference = entries[identity]["artifact"]
        expected = artifacts.read(reference)
        candidates = [row for row in supplied_entries if isinstance(row, dict)
                      and row.get("artifact", {}).get("sha256") == identity]
        delivered = any(row.get("encoding") == "utf-8" and isinstance(row.get("content"), str)
                        and row["content"].encode("utf-8") == expected for row in candidates)
        delivered = delivered or (find_attachment(attachments, reference) is not None and bool(candidates))
        require(delivered, "Required scientific bytes were omitted or changed in the actual observed request",
                UNDELIVERED, digest=identity)
    return supplied


def resolve_native_reviews(records, artifacts, bindings):
    require(isinstance(bindings, list) and bool(bindings), "Native reviews must be a nonempty list")
    result, evidence, seen = [], {}, set()
    for binding in bindings:
        fields(binding, ("attempt_id", "assignment_id"), code=MISMATCH)
        for key in ("attempt_id", "assignment_id"):
            text(binding[key], key, code=MISMATCH)
        identity = (binding["attempt_id"], binding["assignment_id"])
        require(identity not in seen, "Native review bindings cannot be duplicated")
        seen.add(identity)
        attempt = find_current_attempt(records)
        require(attempt is not None and attempt["id"] == binding["attempt_id"] and attempt["kind"] == "solver",
                "Bind the current exact-statement solver attempt")
        validate_attempt(records, artifacts, attempt)
        native_digest = attempt["solver"]["native_attempt_digest"]
        native = records["native_attempt"][native_digest]
        assignment, state, packet = observed_review(records, artifacts, binding["assignment_id"])
        require(packet["root"] == native["attack_root"] and packet["objective_id"] == native["objective_id"]
                and packet["contract"] == native["contract"], "The review must belong to this exact solver objective")
        checkpoint = packet["subject"]
        fields(checkpoint, ("schema_version", "id", "kind", "claim", "origin", "evidence_digests", "verification_status",
                            "what_changed", "remaining_obligation_ids", "next_hypothesis", "milestone_id"), code=MISMATCH)
        projection = next((row for row in native["checkpoints"] if row["id"] == checkpoint["id"]), None)
        require(checkpoint["schema_version"] == 1 and checkpoint["kind"] in {"proof", "reduction"}
                and isinstance(checkpoint["origin"], dict) and projection is not None
                and all(checkpoint[key] == projection[key] for key in ("id", "kind", "claim", "verification_status"))
                and checkpoint["origin"].get("kind") == projection["origin_kind"]
                and digest(checkpoint["claim"]) == packet["claim_digest"],
                "The reviewed checkpoint must match the current delivered native projection")
        validate_approval(packet, state["output"])
        required = required_evidence(artifacts, packet, native)
        actual_delivery(records, artifacts, state, packet, required)
        evidence.update((row["digest"], row) for row in packet["evidence"])
        result.append(dict(binding, status="observed_approving_review", attempt_digest=digest(attempt),
            native_attempt_digest=native_digest, assignment_digest=digest(assignment),
            review_attempt_id=state["attempt_id"], output_digest=state["output_digest"],
            native_packet_digest=assignment["context"]["native_packet"]["sha256"],
            checkpoint=checkpoint, claim_digest=packet["claim_digest"], review=state["output"],
            required_evidence_digests=required))
    return result, evidence


def validate_native_reviews(records, artifacts, packet, state):
    if "native_reviews" not in packet["scientific_context"]:
        return None
    supplied = packet["scientific_context"]["native_reviews"]
    require(isinstance(supplied, list) and bool(supplied), "Native reviews must be a nonempty list")
    require(all(isinstance(row, dict) and "attempt_id" in row and "assignment_id" in row for row in supplied),
            "Native support must retain its canonical review bindings")
    bindings = [{key: row[key] for key in ("attempt_id", "assignment_id")} for row in supplied]
    current, _ = resolve_native_reviews(records, artifacts, bindings)
    require(current == supplied, "The openness packet's supporting review bindings are stale or changed")
    required = sorted({identity for row in current for identity in row["required_evidence_digests"]})
    actual_delivery(records, artifacts, state, packet, required)
    return current
