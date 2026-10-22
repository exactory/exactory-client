"""Bind observed native proof reviews and their delivered bytes to openness."""

import base64
import binascii
import hashlib

from .errors import ResearchError
from .evidence import digest
from .native_review import load_packet, require_assignment_identity
from .operations import fields, iso_date, strings, text
from .problem_attempt import find_current_attempt, validate_attempt
from .workspace import strict_json

MISMATCH = "openness_native_review_mismatch"
MISSING = "openness_native_evidence_missing"
UNDELIVERED = "openness_native_evidence_undelivered"


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


def required_evidence(artifacts, packet, native):
    """Follow analytical manifests and accepted dependencies, without awarding proof credit."""
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
        require(manifest["kind"] == "analytical" and manifest["verification"] is None,
                "Native openness support currently requires analytical manifests without execution verification",
                "openness_native_evidence_unsupported")
        if expected_claim is not None:
            require(manifest["claim_digest"] == expected_claim, "The proof manifest must bind the reviewed claim")
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
    return sorted(required)


def actual_delivery(records, artifacts, state, packet, required):
    """Inspect the authenticated current request, including its exact binary attachments."""
    from .review_transport import PACKET_LABEL
    attempt = records["review_attempt"][state["attempt_id"]]
    request = strict_json(artifacts.read(attempt["request"]))
    messages = request.get("input", [])
    require(isinstance(messages, list) and bool(messages) and messages[-1].get("role") == "user",
            "The observed request must deliver its current packet", UNDELIVERED)
    content = messages[-1].get("content")
    attachments = []
    if isinstance(content, list):
        texts = [part["text"] for part in content if part.get("type") == "input_text"]
        require(len(texts) == 1, "The observed request must identify one current packet", UNDELIVERED)
        for part in content:
            data = part.get("file_data") if part.get("type") == "input_file" else part.get("image_url")
            if isinstance(data, str) and data.startswith("data:") and ";base64," in data:
                media_type, encoded = data[5:].split(";base64,", 1)
                try:
                    attachments.append((media_type, base64.b64decode(encoded, validate=True)))
                except (ValueError, binascii.Error) as error:
                    raise ResearchError(UNDELIVERED, "The observed attachment is not exact base64 content") from error
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
    supplied_entries = supplied.get("evidence", [])
    attached = {(media_type, hashlib.sha256(data).hexdigest()) for media_type, data in attachments}
    for identity in required:
        require(identity in entries, "The openness packet must retain every scientific dependency", MISSING, digest=identity)
        reference = entries[identity]["artifact"]
        expected = artifacts.read(reference)
        candidates = [row for row in supplied_entries if isinstance(row, dict)
                      and row.get("artifact", {}).get("sha256") == identity]
        delivered = any(row.get("encoding") == "utf-8" and isinstance(row.get("content"), str)
                        and row["content"].encode("utf-8") == expected for row in candidates)
        delivered = delivered or ((reference["media_type"], identity) in attached and bool(candidates))
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
