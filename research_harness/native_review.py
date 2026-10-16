"""Canonical native mathematical review packets, without strategic-value gates."""

import re

from .artifacts import validate_reference
from .errors import ResearchError
from .evidence import digest
from .operations import fields, text

PROTOCOL = "native-math-review-v1"
ATTESTATION_PREFIX = "review-assignment:"
KINDS = {"proposal", "result", "recovery"}
LEGACY_PROMPT = (
    "Assess the exact native mathematical subject and its supplied immutable evidence. "
    "The original mathematical objective, proof policy, finite computation obligations and resource rules remain binding. "
    "This task has no strategic-value, intent, dossier or bar prerequisites. "
    "Previously recorded scientific strategy assessments, failures and accepted evidence are deliberately supplied "
    "for continuity; this is not a blind assessment of that scientific history. Do not treat them as proof by authority. "
    "execution_provenance identifies native-selected verifier binaries whose bytes are checked by execution; "
    "their machine code is not supplied as scientific text. Scientific input artifacts remain in evidence. "
    "Use the exact native.subject_digest, native.claim_digest and native.reviewer values in the response. "
    "For native.kind=proposal return exactly {schema_version:1,subject_digest,claim_digest,reviewer,"
    "decision:approve|revise|reject,findings:{root_connection,mathematical_substance,assumptions,scope,"
    "equivalence,necessity,novelty,significance,renewal_basis},unresolved_objections:[{blocking,description}]}. "
    "Findings are strings; inapplicable proposal findings may be empty. "
    "For native.kind=result, an approval is exactly {subject_digest,claim_digest,reviewer,decision:approve,"
    "findings:{statement,assumptions,scope,dependencies,policy}}, with all five findings nonempty. "
    "For native.kind=recovery, an approval is exactly {schema_version:1,subject_digest,reviewer,decision:approve,"
    "findings:{read_only_origin,delta,authority,preservation,quiescence},unresolved_objections:[]}. "
    "A result or recovery which cannot be approved must instead return {decision:revise|reject,reason,objections}; "
    "that response is retained as an operational scientific assessment and cannot authorize the native transition. "
    "Do not invent evidence access, certify omitted dependencies, or transform an unsupported finding into approval."
)
OPENNESS_PROMPT = LEGACY_PROMPT.replace(
    "For native.kind=result, an approval is exactly {subject_digest,claim_digest,reviewer,decision:approve,"
    "findings:{statement,assumptions,scope,dependencies,policy}}, with all five findings nonempty. ",
    "For native.kind=result, an approval is exactly {schema_version:2,subject_digest,claim_digest,reviewer,decision:approve,"
    "findings:{statement,assumptions,scope,dependencies,policy,openness}}, with all findings nonempty. "
    "openness is exactly {outcome:supported_open|already_solved|unresolved,statement_digest,evidence,searched_at,coverage,reason}. "
    "statement_digest equals native.claim_digest, binding the complete exact statement, quantifiers, assumptions and scope. "
    "evidence is a nonempty list of delivered evidence digests for the novelty study and its shared search and reading records. "
    "searched_at is the YYYY-MM-DD search date; coverage records searches for the exact statement and each neighbouring formulation, "
    "with each query's date, sources, depth and top-k versus complete coverage. reason explains the finding. "
    "Fully read every decisive prior or settling source; an unavailable decisive source remains a gap, gives no reading credit "
    "and keeps the outcome unresolved. already_solved must cite the fully read settling source in evidence "
    "and explain it in reason; it never cancels requested formal verification or other deliverables. "
    "unresolved may approve a proof whose claim excludes openness, but cannot approve a claim that the statement was open. "
    "For root closure (native.subject has objective_id and acceptance_ids), unresolved cannot approve: "
    "every math-solver objective, including a request to prove or disprove the proposition, needs supported_open or already_solved. "
    "Proof validity can be approved separately while this openness milestone and root closure remain pending. "
    "Material new settling evidence requires reassessment in a new observed review bound to that exact evidence before closure. "
)


PROMPT = OPENNESS_PROMPT.replace(
    'openness is exactly {outcome:supported_open|already_solved|unresolved,statement_digest,evidence,searched_at,coverage,reason}. ',
    'openness has '
    '{outcome:supported_open|already_solved|unresolved,statement_digest,evidence,searched_at,coverage,reason}, '
    'plus minimum when supplied in native.scientific_context.openness_minimum. minimum is exactly '
    '{schema_version:1,statement,neighbours,searches:[{formulation,search_digest}],reading_digests,settling_work_id}. '
    'Return that exact supplied minimum; do not invent search or reading records. Resolved outcomes require '
    'it: each exact statement and declared neighbour needs a common literature_search with captured_at and '
    'coverage, bound by search_digest in evidence; every relevant or contradictory result needs a bound '
    'complete fulltext reading. already_solved also names its fully read settling work in settling_work_id. '
    'Missing, unavailable or deferred sources, unspecified search coverage and integrity gaps permit only '
    'unresolved. '
)

def _sha(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ResearchError("invalid_native_review_packet", "Native evidence needs exact SHA-256 bindings")


def load_packet(artifacts, reference, reviewer_id):
    """Validate the portable envelope; native ingress verifies its source state."""
    from .workspace import strict_json
    value = strict_json(artifacts.read(reference))
    fields(value, ("protocol", "kind", "root", "objective_id", "contract", "subject", "subject_digest",
                   "claim_digest", "reviewer", "scientific_context", "evidence", "execution_provenance", "unmaterialized_digests"))
    if value["protocol"] != PROTOCOL or value["kind"] not in KINDS:
        raise ResearchError("invalid_native_review_packet", "Unknown native review contract")
    for key in ("root", "objective_id"):
        text(value[key], "Native " + key)
    if not isinstance(value["subject"], dict) or digest(value["subject"]) != value["subject_digest"]:
        raise ResearchError("invalid_native_review_packet", "Native subject bytes differ from their digest")
    if not isinstance(value["contract"], dict) or not isinstance(value["scientific_context"], dict):
        raise ResearchError("invalid_native_review_packet", "Native objective and scientific context must be supplied")
    _sha(value["subject_digest"])
    if value["kind"] == "recovery":
        if value["claim_digest"] is not None:
            raise ResearchError("invalid_native_review_packet", "Recovery uses its original schema without a claim digest")
    else:
        _sha(value["claim_digest"])
    provenance = value["reviewer"]
    fields(provenance, ("source", "actor_id", "attestation_id"))
    if (provenance["source"] != "host" or provenance["actor_id"] != reviewer_id
            or not isinstance(provenance["attestation_id"], str)
            or not provenance["attestation_id"].startswith(ATTESTATION_PREFIX)
            or not provenance["attestation_id"][len(ATTESTATION_PREFIX):]):
        raise ResearchError("invalid_native_review_packet", "Use the exact observed assignment identity")
    seen = set()
    if not isinstance(value["evidence"], list) or not isinstance(value["unmaterialized_digests"], list):
        raise ResearchError("invalid_native_review_packet", "Native evidence closure must be an explicit inventory")
    for entry in value["evidence"]:
        fields(entry, ("digest", "kind", "artifact"))
        _sha(entry["digest"])
        validate_reference(entry["artifact"])
        if (entry["kind"] not in ("blob", "artifact") or entry["digest"] != entry["artifact"]["sha256"]
                or entry["digest"] in seen):
            raise ResearchError("invalid_native_review_packet", "Native evidence is duplicated or identifies different bytes")
        seen.add(entry["digest"])
        artifacts.read(entry["artifact"])
    for item in value["unmaterialized_digests"]:
        _sha(item)
    if not isinstance(value["execution_provenance"], list):
        raise ResearchError("invalid_native_review_packet", "Native executable provenance must be explicit")
    for item in value["execution_provenance"]:
        fields(item, ("path", "digest", "size", "role"))
        text(item["path"], "Native executable path")
        _sha(item["digest"])
        if (item["role"] != "native_verifier_executable" or type(item["size"]) is not int
                or item["size"] < 1 or item["digest"] in seen):
            raise ResearchError("invalid_native_review_packet", "Native executable provenance is invalid or duplicated")
        seen.add(item["digest"])
    if len(set(value["unmaterialized_digests"])) != len(value["unmaterialized_digests"]):
        raise ResearchError("invalid_native_review_packet", "Native unavailable references must be distinct")
    return value


def require_assignment_identity(packet, assignment):
    expected = {"source": "host", "actor_id": assignment["reviewer_id"],
                "attestation_id": ATTESTATION_PREFIX + assignment["id"]}
    if packet["reviewer"] != expected or assignment["dossier_id"] is not None:
        raise ResearchError("invalid_native_review_packet", "Native review binds one exact assignment without a dossier")
