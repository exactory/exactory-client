"""Canonical native mathematical review packets, without strategic-value gates."""

from datetime import date
import re

from .artifacts import validate_reference
from .errors import ResearchError, describe_error_at
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

FINDING_INVALID_MESSAGE = "Return the openness finding required by this assignment's forecast and answer contract."
GROUND_RULES = {
    "reviewer_gap": dict(
        description="The evidence lacks something beyond the forecast's gaps, such as a needed unread work or a sufficiently broad search.",
        next="Repair by search, fulltext and reading, then export."),
    "source_settles": dict(
        description="A decisive bound source, read in full, proves or refutes the statement while the forecast does not offer already_solved; work_id names that source.",
        next="Export with this work as settling_work_id after repairing the gaps, or record a new statement."),
    "settling_source_rejected": dict(
        description="The declared settling work does not settle the statement.",
        next="Export without the declared settling work."),
    "own_argument": dict(
        description="An argument of the reviewer, in no bound source, proves or refutes the statement.",
        next="Check the argument, for example in a solver objective whose accepted result can be bound, or record a new statement."),
    "packet_defect": dict(
        description="The packet lacks or contradicts what a finding needs.",
        next="Record the packet defect in the harness improvement records, then export again."),
}
GROUND_NEXT_STEPS = {kind: rule["next"] for kind, rule in GROUND_RULES.items()}
# Both the validator and X1 read these rows in order.
FINDING_RULES = (
    dict(number=1, outcome="already_solved", forecast="already_solved", grounds="empty",
         condition="The forecast offers already_solved as possible and the declared settling work, read in full, settles the statement"),
    dict(number=2, outcome="supported_open", forecast="supported_open", grounds="empty",
         condition="The forecast offers supported_open as possible and the reviewer holds no ground"),
    dict(number=3, outcome="unresolved", forecast=None, grounds="present_or_both_blocked",
         condition="Every other case: report each ground held; grounds may be empty when the forecast blocks both resolved outcomes"),
)


def render_finding_rule(row):
    return "Rule {number}: {condition}. Return {outcome}; grounds: {grounds}.".format(**row)


def render_finding_prompt():
    """Render X1 from the same ordered rules and ground vocabulary used for validation."""
    return (
        "Report what the delivered evidence establishes about whether this exact statement is open. "
        "An unresolved finding with its grounds is a complete and correct answer. "
        "This finding assesses neither the worth of the problem nor permission to post it. "
        "Return exactly {schema_version:3,subject_digest,claim_digest,reviewer,findings:{statement,assumptions,"
        "scope,dependencies,policy,openness:{outcome,coverage,reason,grounds:[{kind,text,work_id?,turns?}]}}}. "
        "Use the exact native.subject_digest, native.claim_digest and native.reviewer bindings. "
        "The five text findings and reason are nonempty strings. coverage is a nonempty string or a nonempty "
        "list with one entry for every formulation. outcome is supported_open, already_solved or unresolved. "
        "native.scientific_context.audit_forecast reports possible and blocked outcomes, the minimum gaps and "
        "searched_through. You may add grounds from your assessment of the delivered evidence. "
        "The harness supplies the recorded statement digest, evidence, search date and minimum. "
        + " ".join(render_finding_rule(row) for row in FINDING_RULES) + " "
        + " ".join(kind + ": " + GROUND_RULES[kind]["description"] + " " + step
                   for kind, step in GROUND_NEXT_STEPS.items())
        + " Each ground has nonempty text. Optional turns names distinct reading assignment ids in this chain's "
        "reading_record.turns whose notes hold the details; put an argument arising in this final turn in text."
    )


PROBLEM_OPENNESS_PROMPT = render_finding_prompt()


def uses_finding_contract(assignment):
    """The saved prompt, rather than today's packet shape, selects the answer contract."""
    template = assignment.get("prompt", {}).get("template_id", "")
    return template in ("exactory-openness-reading-v6/final", "exactory-review-v7/native_math")


def _require_finding(condition, rule, path, forecast):
    if not condition:
        raise ResearchError("openness_finding_invalid", FINDING_INVALID_MESSAGE,
                            dict(rule=rule, path=path, forecast=forecast))


def validate_finding(packet, output):
    """Check a v3 answer using only its immutable assignment packet and output."""
    from .review_refusal import contract_check
    native = packet.get("native", packet)
    context = native["scientific_context"]
    forecast = context["audit_forecast"]
    with contract_check("openness.finding"):
        def require(condition, rule, path):
            _require_finding(condition, rule, path, forecast)

        require(isinstance(output, dict) and set(output) == {
            "schema_version", "subject_digest", "claim_digest", "reviewer", "findings"}, "answer_fields", "")
        require(type(output["schema_version"]) is int and output["schema_version"] == 3,
                "schema_version", "/schema_version")
        for key in ("subject_digest", "claim_digest", "reviewer"):
            require(output[key] == native[key], "binding", "/" + key)
        findings = output["findings"]
        require(isinstance(findings, dict) and set(findings) == set(RESULT_FINDINGS) | {"openness"},
                "finding_fields", "/findings")
        for key in RESULT_FINDINGS:
            require(_nonempty(findings[key]), "nonempty_text", "/findings/" + key)
        openness = findings["openness"]
        base = "/findings/openness"
        require(isinstance(openness, dict) and set(openness) == {"outcome", "coverage", "reason", "grounds"},
                "openness_fields", base)
        require(_nonempty(openness["reason"]), "nonempty_text", base + "/reason")
        coverage = openness["coverage"]
        minimum = context["openness_minimum"]
        require(_nonempty(coverage) or isinstance(coverage, list)
                and len(coverage) == 1 + len(minimum["neighbours"]), "coverage", base + "/coverage")
        grounds = openness["grounds"]
        require(isinstance(grounds, list), "grounds_list", base + "/grounds")
        turns = {row["assignment_id"] for row in packet.get("reading_record", {}).get("turns", [])
                 if row.get("stage") != "final"}
        for index, ground in enumerate(grounds):
            path = base + "/grounds/" + str(index)
            require(isinstance(ground, dict) and {"kind", "text"} <= set(ground)
                    and set(ground) <= {"kind", "text", "work_id", "turns"}, "ground_fields", path)
            require(isinstance(ground["kind"], str) and ground["kind"] in GROUND_NEXT_STEPS, "ground_kind", path + "/kind")
            require(_nonempty(ground["text"]), "nonempty_text", path + "/text")
            if "work_id" in ground:
                require(_nonempty(ground["work_id"]), "work_id", path + "/work_id")
            if ground["kind"] == "source_settles":
                require(ground.get("work_id") in context["decisive_work_ids"], "decisive_work", path + "/work_id")
            if "turns" in ground:
                references = ground["turns"]
                require(isinstance(references, list) and all(isinstance(item, str) for item in references)
                        and len(set(references)) == len(references) and set(references) <= turns,
                        "reading_turns", path + "/turns")
        outcomes = forecast["outcomes"]
        rule = next((row for row in FINDING_RULES if row["outcome"] == openness["outcome"]), None)
        require(rule is not None, "outcome", base + "/outcome")
        if rule["grounds"] == "empty":
            accepted = outcomes[rule["forecast"]] == "possible" and not grounds
        else:
            accepted = bool(grounds) or all(value == "blocked" for value in outcomes.values())
        require(accepted, rule["number"], base + "/outcome")


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
    """The packet names the assignment, or the first turn of its reading or parts series."""
    first = assignment.get("reading_chain", {}).get("first_assignment_id",
        assignment.get("review_parts", {}).get("replaces_assignment_id",
            assignment.get("review_parts", {}).get("series_assignment_id", assignment["id"])))
    expected = {"source": "host", "actor_id": assignment["reviewer_id"],
                "attestation_id": ATTESTATION_PREFIX + first}
    if packet["reviewer"] != expected or assignment["dossier_id"] is not None:
        raise ResearchError("invalid_native_review_packet", "Native review binds one exact assignment without a dossier")


PROPOSAL_FINDINGS = ("root_connection", "mathematical_substance", "assumptions", "scope", "equivalence", "necessity",
                     "novelty", "significance", "renewal_basis")
RESULT_FINDINGS = ("statement", "assumptions", "scope", "dependencies", "policy")
RECOVERY_FINDINGS = ("read_only_origin", "delta", "authority", "preservation", "quiescence")


def _require(condition, message, *, path):
    if not condition:
        raise ResearchError("invalid_native_review", message, {"path": path})


def _closed(value, keys, message, *, path):
    _require(isinstance(value, dict) and set(value) == set(keys), message, path=path)


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _validate_objections(value, *, empty=False, path='/unresolved_objections'):
    _require(isinstance(value, list) and (not empty or not value), "unresolved_objections must be a list" +
             (" and empty in an approval" if empty else ""), path=path)
    for index, objection in enumerate(value):
        item_path = path + '/' + str(index)
        _closed(objection, ("blocking", "description"), "Each unresolved objection is exactly {blocking,description}",
                path=item_path)
        _require(type(objection["blocking"]) is bool,
                 "An objection needs a boolean blocking flag and a nonempty description", path=item_path + '/blocking')
        _require(_nonempty(objection["description"]),
                 "An objection needs a boolean blocking flag and a nonempty description", path=item_path + '/description')


def _validate_openness(openness, claim_digest, *, structured_coverage, path='/findings/openness'):
    _require(isinstance(openness, dict) and set(openness) - {"minimum"} == {
        "outcome", "statement_digest", "evidence", "searched_at", "coverage", "reason"},
        "openness is exactly {outcome,statement_digest,evidence,searched_at,coverage,reason}, with minimum when supplied",
        path=path)
    _require(openness["outcome"] in ("supported_open", "already_solved", "unresolved"), "Unknown openness outcome",
             path=path + '/outcome')
    _require(openness["statement_digest"] == claim_digest, "openness.statement_digest must equal native.claim_digest",
             path=path + '/statement_digest')
    evidence = openness["evidence"]
    _require(isinstance(evidence, list) and evidence and len(set(map(str, evidence))) == len(evidence)
             and all(isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item) for item in evidence),
             "openness.evidence is a nonempty list of distinct SHA-256 digests", path=path + '/evidence')
    for key in ("searched_at", "reason"):
        _require(_nonempty(openness[key]), "searched_at and reason must be nonempty strings", path=path + '/' + key)
    # problem-openness records a problem record's coverage as given, also as a nonempty list or object.
    coverage = openness["coverage"]
    _require(_nonempty(coverage) or (structured_coverage and isinstance(coverage, (list, dict)) and bool(coverage)),
             "coverage must be a nonempty string" + (", list or object" if structured_coverage else ""),
             path=path + '/coverage')
    try:
        _require(date.fromisoformat(openness["searched_at"]).isoformat() == openness["searched_at"],
                 "openness.searched_at must be YYYY-MM-DD", path=path + '/searched_at')
    except ValueError as error:
        raise ResearchError("invalid_native_review", "openness.searched_at must be YYYY-MM-DD",
                            {"path": path + '/searched_at'}) from error
    if "minimum" in openness:
        from .openness_minimum import validate_minimum
        with describe_error_at(path + '/minimum'):
            validate_minimum(openness["minimum"])


def validate_output(packet, output):
    """Check an observed native review against the shape that its prompt states and the native controller records:
    the exact subject, claim and reviewer bindings of the packet, the decision vocabulary and the closed fields. A
    result or recovery that is not an approval is retained only as an assessment and authorizes nothing, so it needs
    only its decision and a reason."""
    _require(isinstance(output, dict), "A native review is one JSON object", path='')
    kind = packet["kind"]
    if kind != "proposal" and output.get("decision") != "approve":
        _require(output.get("decision") in ("revise", "reject"),
                 "A result or recovery that is not approved needs decision revise or reject and a nonempty reason",
                 path='/decision')
        _require(_nonempty(output.get("reason")),
                 "A result or recovery that is not approved needs decision revise or reject and a nonempty reason",
                 path='/reason')
        return
    _require(output.get("subject_digest") == packet["subject_digest"], "Use the exact native.subject_digest",
             path='/subject_digest')
    _require(output.get("reviewer") == packet["reviewer"], "Use the exact native.reviewer object", path='/reviewer')
    if kind == "proposal":
        _closed(output, ("schema_version", "subject_digest", "claim_digest", "reviewer", "decision", "findings",
                         "unresolved_objections"), "A proposal review has exactly the fields of the native Review", path='')
        _require(output["schema_version"] == 1 and type(output["schema_version"]) is int,
                 "Proposal reviews use schema_version 1", path='/schema_version')
        _require(output["claim_digest"] == packet["claim_digest"], "Use the exact native.claim_digest", path='/claim_digest')
        _require(output["decision"] in ("approve", "revise", "reject"), "Use decision approve, revise or reject", path='/decision')
        _closed(output["findings"], PROPOSAL_FINDINGS, "Proposal findings are exactly the nine native findings", path='/findings')
        for key in PROPOSAL_FINDINGS:
            _require(isinstance(output["findings"][key], str), "Proposal findings are strings", path='/findings/' + key)
        _validate_objections(output["unresolved_objections"])
    elif kind == "result":
        version = output.get("schema_version", 1)
        _require(type(version) is int and version in (1, 2), "Result reviews use schema_version 1 or 2", path='/schema_version')
        _closed(output, ("subject_digest", "claim_digest", "reviewer", "decision", "findings")
                + (("schema_version",) if "schema_version" in output else ()),
                "A result approval has exactly the fields of the native result review", path='')
        _require(output["claim_digest"] == packet["claim_digest"], "Use the exact native.claim_digest", path='/claim_digest')
        _closed(output["findings"], RESULT_FINDINGS + (("openness",) if version == 2 else ()),
                "Result findings are statement, assumptions, scope, dependencies and policy, with openness in version 2",
                path='/findings')
        for key in RESULT_FINDINGS:
            _require(_nonempty(output["findings"][key]),
                     "findings.statement, findings.assumptions, findings.scope, findings.dependencies and findings.policy "
                     "must each be a nonempty string; in schema_version 2, findings.openness is an object", path='/findings/' + key)
        if version == 2:
            _validate_openness(output["findings"]["openness"], packet["claim_digest"],
                               structured_coverage=packet["contract"].get("orchestration") == "problem_creation")
    else:
        _closed(output, ("schema_version", "subject_digest", "reviewer", "decision", "findings", "unresolved_objections"),
                "A recovery approval has exactly the fields of the native recovery review", path='')
        _require(output["schema_version"] == 1 and type(output["schema_version"]) is int,
                 "Recovery reviews use schema_version 1", path='/schema_version')
        _closed(output["findings"], RECOVERY_FINDINGS, "Recovery findings are exactly the five native findings", path='/findings')
        for key in RECOVERY_FINDINGS:
            _require(_nonempty(output["findings"][key]), "Every recovery finding is a nonempty string", path='/findings/' + key)
        _validate_objections(output["unresolved_objections"], empty=True)
