"""Canonical native mathematical review packets, without strategic-value gates."""

from datetime import date
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


MINIMUM_PROMPT = OPENNESS_PROMPT.replace(
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

RESOLUTIONS_PROMPT = MINIMUM_PROMPT.replace(
    '{schema_version:1,statement,neighbours,searches:[{formulation,search_digest}],reading_digests,settling_work_id}. ',
    '{schema_version:1,statement,neighbours,searches:[{formulation,search_digest}],reading_digests,settling_work_id}, '
    'optionally with duplicate_resolutions:[{search_digest,work_id,canonical_work_id}]. '
    'Keep earlier uncertain hits and their explicit later resolutions in the bound searches. '
    'An out_of_scope resolution removes the uncertain hit\'s reading obligation; duplicate_resolutions '
    'transfers it to the named relevant or contradictory canonical source in that resolving search. '
    'The canonical source needs a complete fulltext reading; a duplicate without that binding remains a gap. '
)

STRATEGY_PROMPT = RESOLUTIONS_PROMPT + (
    "The result review type is also used for policy and strategy subjects; identify the actual subject before judging it. "
    "A native.subject containing context_digest, assessments, plan_bindings, strategy_order, next_hypotheses and "
    "remaining_obligation_ids is a StrategyAssessment. Its subject_digest binds that assessment, while claim_digest "
    "identifies the original objective claim whose unresolved obligations must be preserved. "
    "For this subject, judge whether the proposed dispositions follow from the supplied current context, studies, "
    "retained failures, plan bindings and next hypotheses. Check that each continuing strategy is justified, "
    "that a failed method has concrete changed inputs before reuse, and that the proposed execution order and "
    "success and failure criteria can advance the remaining obligations within the recorded resource rules. "
    "A justified StrategyAssessment may be approved while the original theorem and openness remain unresolved. "
    "Such approval does not accept a proof, establish openness, renew a budget or close an obligation. "
    "Do not demand a proof of the original theorem as a prerequisite for approving a justified research step. "
    "Use the same required result findings to explain the assessment's statement, assumptions, scope, dependencies "
    "and policy; give the openness finding supported by the delivered evidence, using unresolved when necessary. "
    "This distinction does not relax any proof checkpoint, computation, acceptance or root-closure requirement. "
)

# Codex normalizes outer whitespace when loading its instruction file. Keep the
# prior prompt exact for historical records; fresh prompts must survive that load.
TRIMMED_STRATEGY_PROMPT = STRATEGY_PROMPT.rstrip()
PROMPT = TRIMMED_STRATEGY_PROMPT + (
    " For every native result review, findings.statement, findings.assumptions, findings.scope, "
    "findings.dependencies and findings.policy must each be a nonempty JSON string. "
    "findings.openness.coverage and findings.openness.reason must each be a nonempty JSON string, "
    "not an object or an array. Keep structured search details inside those strings when needed."
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
        exported = artifacts.read(entry["artifact"])
        if "raw_identities" in entry["artifact"]:
            originals = entry["artifact"]["raw_identities"]
            if not isinstance(originals, list) or not originals:
                raise ResearchError("invalid_native_review_packet", "Raw identities must name original JSON artifacts")
            normalized = digest(strict_json(exported))
            raw_digests = set()
            for original in originals:
                validate_reference(original)
                if original["sha256"] in raw_digests or digest(strict_json(artifacts.read(original))) != normalized:
                    raise ResearchError("invalid_native_review_packet", "Raw bytes do not establish the exported canonical identity")
                raw_digests.add(original["sha256"])
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


PROPOSAL_FINDINGS = ("root_connection", "mathematical_substance", "assumptions", "scope", "equivalence", "necessity",
                     "novelty", "significance", "renewal_basis")
RESULT_FINDINGS = ("statement", "assumptions", "scope", "dependencies", "policy")
RECOVERY_FINDINGS = ("read_only_origin", "delta", "authority", "preservation", "quiescence")


def _require(condition, message):
    if not condition:
        raise ResearchError("invalid_native_review", message)


def _closed(value, keys, message):
    _require(isinstance(value, dict) and set(value) == set(keys), message)


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _validate_objections(value, *, empty=False):
    _require(isinstance(value, list) and (not empty or not value), "unresolved_objections must be a list" +
             (" and empty in an approval" if empty else ""))
    for objection in value:
        _closed(objection, ("blocking", "description"), "Each unresolved objection is exactly {blocking,description}")
        _require(type(objection["blocking"]) is bool and _nonempty(objection["description"]),
                 "An objection needs a boolean blocking flag and a nonempty description")


def _validate_openness(openness, claim_digest, *, structured_coverage):
    _require(isinstance(openness, dict) and set(openness) - {"minimum"} == {
        "outcome", "statement_digest", "evidence", "searched_at", "coverage", "reason"},
        "openness is exactly {outcome,statement_digest,evidence,searched_at,coverage,reason}, with minimum when supplied")
    _require(openness["outcome"] in ("supported_open", "already_solved", "unresolved"), "Unknown openness outcome")
    _require(openness["statement_digest"] == claim_digest, "openness.statement_digest must equal native.claim_digest")
    evidence = openness["evidence"]
    _require(isinstance(evidence, list) and evidence and len(set(map(str, evidence))) == len(evidence)
             and all(isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item) for item in evidence),
             "openness.evidence is a nonempty list of distinct SHA-256 digests")
    _require(all(_nonempty(openness[key]) for key in ("searched_at", "reason")),
             "searched_at and reason must be nonempty strings")
    # problem-openness records a problem record's coverage as given, also as a nonempty list or object.
    coverage = openness["coverage"]
    _require(_nonempty(coverage) or (structured_coverage and isinstance(coverage, (list, dict)) and bool(coverage)),
             "coverage must be a nonempty string" + (", list or object" if structured_coverage else ""))
    try:
        _require(date.fromisoformat(openness["searched_at"]).isoformat() == openness["searched_at"],
                 "openness.searched_at must be YYYY-MM-DD")
    except ValueError as error:
        raise ResearchError("invalid_native_review", "openness.searched_at must be YYYY-MM-DD") from error
    if "minimum" in openness:
        from .openness_minimum import validate_minimum
        validate_minimum(openness["minimum"])


def validate_output(packet, output):
    """Check an observed native review against the shape that its prompt states and the native controller records:
    the exact subject, claim and reviewer bindings of the packet, the decision vocabulary and the closed fields. A
    result or recovery that is not an approval is retained only as an assessment and authorizes nothing, so it needs
    only its decision and a reason."""
    _require(isinstance(output, dict), "A native review is one JSON object")
    kind = packet["kind"]
    if kind != "proposal" and output.get("decision") != "approve":
        _require(output.get("decision") in ("revise", "reject") and _nonempty(output.get("reason")),
                 "A result or recovery that is not approved needs decision revise or reject and a nonempty reason")
        return
    _require(output.get("subject_digest") == packet["subject_digest"], "Use the exact native.subject_digest")
    _require(output.get("reviewer") == packet["reviewer"], "Use the exact native.reviewer object")
    if kind == "proposal":
        _closed(output, ("schema_version", "subject_digest", "claim_digest", "reviewer", "decision", "findings",
                         "unresolved_objections"), "A proposal review has exactly the fields of the native Review")
        _require(output["schema_version"] == 1 and type(output["schema_version"]) is int, "Proposal reviews use schema_version 1")
        _require(output["claim_digest"] == packet["claim_digest"], "Use the exact native.claim_digest")
        _require(output["decision"] in ("approve", "revise", "reject"), "Use decision approve, revise or reject")
        _closed(output["findings"], PROPOSAL_FINDINGS, "Proposal findings are exactly the nine native findings")
        _require(all(isinstance(value, str) for value in output["findings"].values()), "Proposal findings are strings")
        _validate_objections(output["unresolved_objections"])
    elif kind == "result":
        version = output.get("schema_version", 1)
        _require(type(version) is int and version in (1, 2), "Result reviews use schema_version 1 or 2")
        _closed(output, ("subject_digest", "claim_digest", "reviewer", "decision", "findings")
                + (("schema_version",) if "schema_version" in output else ()),
                "A result approval has exactly the fields of the native result review")
        _require(output["claim_digest"] == packet["claim_digest"], "Use the exact native.claim_digest")
        _closed(output["findings"], RESULT_FINDINGS + (("openness",) if version == 2 else ()),
                "Result findings are statement, assumptions, scope, dependencies and policy, with openness in version 2")
        _require(all(_nonempty(output["findings"][key]) for key in RESULT_FINDINGS),
                 "findings.statement, findings.assumptions, findings.scope, findings.dependencies and findings.policy "
                 "must each be a nonempty string; in schema_version 2, findings.openness is an object")
        if version == 2:
            _validate_openness(output["findings"]["openness"], packet["claim_digest"],
                               structured_coverage=packet["contract"].get("orchestration") == "problem_creation")
    else:
        _closed(output, ("schema_version", "subject_digest", "reviewer", "decision", "findings", "unresolved_objections"),
                "A recovery approval has exactly the fields of the native recovery review")
        _require(output["schema_version"] == 1 and type(output["schema_version"]) is int, "Recovery reviews use schema_version 1")
        _closed(output["findings"], RECOVERY_FINDINGS, "Recovery findings are exactly the five native findings")
        _require(all(_nonempty(value) for value in output["findings"].values()), "Every recovery finding is a nonempty string")
        _validate_objections(output["unresolved_objections"], empty=True)
