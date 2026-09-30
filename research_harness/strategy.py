"""Versioned research intent and evidence-bound comparative strategy records.

These records preserve attributed scientific judgments. Their validation checks
identity, evidence and continuity; it does not establish scientific truth,
importance, authorship of an instruction, or an unobserved experiment.
"""

from datetime import datetime
import math
from pathlib import PurePosixPath

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evidence import digest
from .operations import fields, immutable_record, normalized_text, prepared_mutation, strings, text, timestamp


TASK_KINDS = ("open_research", "bounded_research", "specified_delivery")
PHASES = ("prospective", "result", "post_measurement")
ACTIONS = ("investigate", "develop_manuscript", "pivot", "close_branch", "deliver_requested")
CLASSIFICATIONS = ("validity", "consequence_critical", "optional")
EXECUTION_STATES = ("unattempted", "planned", "attempted", "validated", "failed", "blocked")
TRANSFER_KINDS = ("direct", "logical", "bounded", "empirically_validated", "conventional_idealization", "unestablished")


def choice(value, choices, name, code="invalid_strategy"):
    if not isinstance(value, str) or value not in choices:
        raise ResearchError(code, name + " must be one of: " + ", ".join(choices))


def items(value, name, *, nonempty=False, code="invalid_strategy"):
    if not isinstance(value, list) or (nonempty and not value):
        raise ResearchError(code, name + " must be an array" + (" with at least one entry" if nonempty else ""))
    return value


def number(value, name, *, positive=False, nullable=False, code="invalid_strategy"):
    if value is None and nullable:
        return
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0 and (not positive or value > 0)
    except OverflowError:
        valid = False
    if not valid:
        raise ResearchError(code, name + " must be a finite " + ("positive" if positive else "nonnegative") + " number")


def get_record(records, kind, identifier, code="strategy_reference_missing"):
    text(identifier, kind + " ID", code=code)
    saved = records.get(kind, {}).get(identifier)
    if saved is None:
        raise ResearchError(code, "Record the referenced " + kind + " before using it", {"id": identifier})
    return saved


def selected(records, key, kind):
    selection = records.get("strategy_selection", {}).get(key)
    return None if selection is None else records.get(kind, {}).get(selection["id"])


def current_intent(records):
    return selected(records, "intent", "research_intent")


def current_dossier(records):
    return selected(records, "dossier", "strategy_dossier")


def managed_research(records):
    """Shared research preparation alone includes native mathematics, not this policy."""
    return bool(set(records.get("workspace", {})) & {"study", "draft"} or records.get("research_intent"))


def existing_record(records, kind, value):
    text(value.get("id"), kind + " ID")
    saved = records.get(kind, {}).get(value["id"])
    if saved is not None and saved["payload"] != value:
        raise ResearchError("record_conflict", "An evidence record ID cannot replace historical content", {"kind": kind, "id": value["id"]})
    return saved


def instruction_provenance(records, artifacts, identifier):
    pinned = records.get("local_artifact", {}).get(identifier) if isinstance(identifier, str) else None
    if (pinned is None or not isinstance(pinned.get("path"), str)
            or PurePosixPath(pinned["path"]).parts[0] != "context"):
        raise ResearchError("intent_instruction_missing", "Name a pinned context/ artifact containing the actual user instruction")
    try:
        content = artifacts.read(pinned["artifact"]).decode("utf-8")
    except UnicodeError as error:
        raise ResearchError("intent_instruction_missing", "The instruction must be saved as UTF-8 text") from error
    if not content.strip():
        raise ResearchError("intent_instruction_missing", "The pinned user instruction cannot be empty")
    return {"id": identifier, "path": pinned["path"], "artifact": pinned["artifact"], "text": content}


def evidence(records, artifacts, values, *, required=True):
    """Verify exact artifacts, read source locators or existing typed record references."""
    for reference in items(values, "Evidence", nonempty=required):
        if not isinstance(reference, dict):
            raise ResearchError("invalid_strategy", "Evidence must identify exact held bytes or a recorded result")
        if {"path", "sha256", "size", "media_type"} <= reference.keys():
            fields(reference, ("path", "sha256", "size", "media_type"), code="invalid_strategy")
            artifacts.read(reference)
        elif reference.get("kind") == "source":
            fields(reference, ("kind", "link"), code="invalid_strategy")
            from .reading import validate_read_evidence
            validate_read_evidence(records, artifacts, reference["link"])
        elif reference.get("kind") == "record":
            fields(reference, ("kind", "record_kind", "id", "digest"), code="invalid_strategy")
            text(reference["record_kind"], "Evidence record kind", code="invalid_strategy")
            saved = get_record(records, reference["record_kind"], reference["id"])
            if digest(saved) != reference["digest"]:
                raise ResearchError("strategy_evidence_stale", "The cited record differs from the bound evidence", {"id": reference["id"]})
            if reference["record_kind"] == "local_artifact":
                artifacts.read(saved["artifact"])
        else:
            raise ResearchError("invalid_strategy", "Use an artifact descriptor, source link or exact record reference")
    return values


def objective_binding(records, artifacts, value):
    if not isinstance(value, dict):
        raise ResearchError("invalid_strategy", "Give a proposed or native objective binding")
    if value.get("kind") == "proposed":
        fields(value, ("kind", "id", "statement", "scope", "instruction"), code="invalid_strategy")
        text(value["id"], "Objective proposal ID")
        text(value["statement"], "Proposed objective statement")
        if not isinstance(value["scope"], dict):
            raise ResearchError("invalid_strategy", "Proposed scope must be an explicit object")
        instruction = instruction_provenance(records, artifacts, value["instruction"])
        return dict(value, content_digest=digest({"statement": value["statement"], "scope": value["scope"]}),
                    instruction_sha256=instruction["artifact"]["sha256"])
    fields(value, ("kind", "id"), code="invalid_strategy")
    choice(value["kind"], ("native",), "Objective binding kind")
    native = get_record(records, "research_objective", value["id"], "strategy_objective_missing")
    commitments = [record for record in records.get("research_commitment", {}).values()
                   if record["objective"]["id"] == value["id"]]
    scope = commitments[-1]["proposal"]["scope"] if commitments else {}
    return dict(value, statement=native["statement"], scope=scope,
                content_digest=digest({"statement": native["statement"], "scope": scope}))


def _same_objective(first, second):
    return first["content_digest"] == second["content_digest"]


def _resource_contract(records, artifacts, values):
    kinds = set()
    for resource in items(values, "Resource contract", code="invalid_intent"):
        fields(resource, ("kind", "limit", "unit", "authorization"), code="invalid_intent")
        for key in ("kind", "unit"):
            text(resource[key], "Resource " + key, code="invalid_intent")
        if resource["kind"] in kinds:
            raise ResearchError("invalid_intent", "A resource kind is declared once")
        kinds.add(resource["kind"])
        number(resource["limit"], "Resource limit", nullable=True, code="invalid_intent")
        if resource["authorization"] is not None:
            instruction_provenance(records, artifacts, resource["authorization"])


def record_intent(store, payload, *, expected_revision, request_id):
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        fields(value, ("id", "previous", "task_kind", "instruction", "recorded_at", "full_objective", "branch",
                       "objective", "user_standard", "deliverables", "publication", "resources", "scope_changes", "authors"), code="invalid_intent")
        old = existing_record(records, "research_intent", value)
        if old is not None:
            return [], old
        choice(value["task_kind"], TASK_KINDS, "Task kind", "invalid_intent")
        instruction = instruction_provenance(records, artifacts, value["instruction"])
        timestamp(value["recorded_at"])
        for key in ("full_objective", "user_standard"):
            text(value[key], key, code="invalid_intent")
        strings(value["deliverables"], "Deliverables", nonempty=True, code="invalid_intent")
        strings(value["authors"], "Scientific authors", nonempty=True, code="invalid_intent")
        branch = value["branch"]
        fields(branch, ("statement", "relation", "remaining_obligations"), code="invalid_intent")
        text(branch["statement"], "Current branch statement", code="invalid_intent")
        choice(branch["relation"], ("full", "partial", "shared_objective"), "Branch relationship", "invalid_intent")
        strings(branch["remaining_obligations"], "Remaining full-objective obligations",
                nonempty=branch["relation"] != "full", code="invalid_intent")
        binding = objective_binding(records, artifacts, value["objective"])
        publication = value["publication"]
        fields(publication, ("instruction", "endpoint", "required", "quality_condition"), code="invalid_intent")
        choice(publication["endpoint"], ("none", "artifact", "deposit", "submit"), "Publication endpoint", "invalid_intent")
        if type(publication["required"]) is not bool:
            raise ResearchError("invalid_intent", "Delivery requirement must be an explicit boolean")
        if publication["endpoint"] == "none" and publication["required"]:
            raise ResearchError("invalid_intent", "A required delivery needs an endpoint")
        if publication["endpoint"] != "none":
            instruction_provenance(records, artifacts, publication["instruction"])
        elif publication["instruction"] is not None:
            instruction_provenance(records, artifacts, publication["instruction"])
        if publication["quality_condition"] is not None:
            text(publication["quality_condition"], "Explicit publication quality condition", code="invalid_intent")
        _resource_contract(records, artifacts, value["resources"])
        changes = []
        for change in items(value["scope_changes"], "Authorized scope changes", code="invalid_intent"):
            fields(change, ("instruction", "reason"), code="invalid_intent")
            text(change["reason"], "Authorized scope-change reason", code="invalid_intent")
            changes.append(instruction_provenance(records, artifacts, change["instruction"]))
        previous = current_intent(records)
        if (previous is None and value["previous"] is not None) or (previous is not None and value["previous"] != previous["id"]):
            raise ResearchError("intent_revision_mismatch", "A new intent revision must continue the current intent")
        if previous is not None:
            earlier = previous["payload"]
            if datetime.fromisoformat(value["recorded_at"].replace("Z", "+00:00")) < datetime.fromisoformat(earlier["recorded_at"].replace("Z", "+00:00")):
                raise ResearchError("intent_time_mismatch", "Later instructions cannot be applied retrospectively")
            changed_scope = normalized_text(value["full_objective"]) != normalized_text(earlier["full_objective"])
            authority_changed = any(value[key] != earlier[key] for key in ("task_kind", "user_standard", "publication", "resources"))
            if changed_scope and not changes:
                raise ResearchError("intent_authority_required", "A changed full objective needs a new pinned user instruction")
            used = {saved["instruction_provenance"]["artifact"]["sha256"] for saved in records.get("research_intent", {}).values()}
            if authority_changed and instruction["artifact"]["sha256"] in used:
                raise ResearchError("intent_authority_required", "A changed user standard, delivery or resource authority needs its actual new instruction")
            if changed_scope and all(change["artifact"]["sha256"] in used for change in changes):
                raise ResearchError("intent_authority_required", "An old instruction cannot be reused to authorize a different objective")
        record = {"id": value["id"], "payload": value, "digest": digest(value), "recorded_revision": expected_revision + 1,
                  "instruction_provenance": instruction, "scope_authorizations": changes, "objective_binding": binding}
        return [immutable_record(records, "research_intent", value["id"], record),
                ("strategy_selection", "intent", {"id": value["id"]})], record

    return prepared_mutation(store, "strategy.intent", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def source_snapshot(records):
    """Capture source content identity, excluding unrelated transport metadata."""
    return {identifier: digest({key: source.get(key) for key in ("status", "response_complete", "response", "original", "extraction")})
            for identifier, source in sorted(records.get("source", {}).items())}


def source_delta(records, dossier):
    current, previous = source_snapshot(records), dossier["source_snapshot"]
    return sorted(identifier for identifier in set(current) | set(previous) if current.get(identifier) != previous.get(identifier))


def _candidate(records, artifacts, value, *, path):
    fields(value, ("id", "question", "target", "scope", "relation", "method", "outcomes", "prior_result", "addition",
                   "transfer", "adequacy", "assumptions", "resources", "next_test", "evidence"), code="invalid_strategy", path=path)
    for key in ("id", "question", "target", "scope", "method", "prior_result", "addition"):
        text(value[key], "Candidate " + key, code="invalid_strategy")
    choice(value["relation"], ("full", "partial", "shared_objective"), "Candidate relation")
    fields(value["outcomes"], ("positive", "negative", "inconclusive"), code="invalid_strategy", path=path + "/outcomes")
    for key, consequence in value["outcomes"].items():
        text(consequence, key + " outcome consequence", code="invalid_strategy")
    transfer = value["transfer"]
    fields(transfer, ("kind", "target_inference", "plan", "evidence"), code="invalid_strategy", path=path + "/transfer")
    choice(transfer["kind"], TRANSFER_KINDS, "Transfer relation")
    text(transfer["target_inference"], "Target inference", code="invalid_strategy")
    if transfer["plan"] is not None:
        text(transfer["plan"], "Transfer plan", code="invalid_strategy")
    evidence(records, artifacts, transfer["evidence"], required=False)
    fields(value["adequacy"], ("distinguishes", "limits", "evidence"), code="invalid_strategy", path=path + "/adequacy")
    strings(value["adequacy"]["distinguishes"], "Decisive alternatives", nonempty=True)
    strings(value["adequacy"]["limits"], "Method adequacy limits")
    evidence(records, artifacts, value["adequacy"]["evidence"])
    assumption_ids = set()
    for index, assumption in enumerate(items(value["assumptions"], "Deciding assumptions")):
        fields(assumption, ("id", "statement", "check", "scope_boundary"), code="invalid_strategy",
               path=path + f"/assumptions/{index}")
        for key in assumption:
            text(assumption[key], "Assumption " + key, code="invalid_strategy")
        if assumption["id"] in assumption_ids:
            raise ResearchError("invalid_strategy", "Assumption identities must be unique")
        assumption_ids.add(assumption["id"])
    fields(value["resources"], ("estimates", "available"), code="invalid_strategy", path=path + "/resources")
    strings(value["resources"]["available"], "Available resources")
    for index, estimate in enumerate(items(value["resources"]["estimates"], "Resource estimates", nonempty=True)):
        fields(estimate, ("unit", "amount", "uncertainty"), code="invalid_strategy", path=path + f"/resources/estimates/{index}")
        text(estimate["unit"], "Estimated resource unit")
        text(estimate["uncertainty"], "Resource estimate uncertainty")
        number(estimate["amount"], "Estimated resource amount", nullable=True)
    test = value["next_test"]
    fields(test, ("question", "method", "success", "failure", "prerequisites"), code="invalid_strategy", path=path + "/next_test")
    for key in ("question", "method", "success", "failure"):
        text(test[key], "Deciding test " + key)
    for index, prerequisite in enumerate(items(test["prerequisites"], "Deciding-test prerequisites")):
        fields(prerequisite, ("description", "end_condition", "exit_condition"), code="invalid_strategy",
               path=path + f"/next_test/prerequisites/{index}")
        for key in prerequisite:
            text(prerequisite[key], "Prerequisite " + key)
    evidence(records, artifacts, value["evidence"])


def _evidence_identities(value, records):
    if isinstance(value, dict):
        if {"path", "sha256", "size", "media_type"} <= value.keys():
            return {"artifact:" + value["sha256"]}
        if value.get("kind") == "source" and set(value) == {"kind", "link"}:
            from .source_links import link_identity
            return {"source:" + digest(link_identity(value["link"], records))}
        if value.get("kind") == "record" and set(value) == {"kind", "record_kind", "id", "digest"}:
            if value["record_kind"] == "local_artifact":
                saved = get_record(records, "local_artifact", value["id"])
                return _evidence_identities(saved["artifact"], records)
            if value["record_kind"] == "research_work_item":
                saved = get_record(records, "research_work_item", value["id"])
                scientific = {key: item for key, item in saved["payload"].items()
                              if key not in ("id", "previous", "evidence")}
                scientific["evidence"] = sorted(_evidence_identities(saved["payload"]["evidence"], records))
                return {"record:research_work_item:" + digest(scientific)}
            return {digest(value)}
        return set().union(*(_evidence_identities(item, records) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_evidence_identities(item, records) for item in value))
    return set()


def _reconsidered_consequence(value, records):
    candidates = value.get("candidates")
    if not isinstance(candidates, list):
        return None
    candidate = next((item for item in candidates if isinstance(item, dict)
                      and item.get("id") == value.get("selected_candidate")), None)
    if candidate is None:
        return None
    # A renamed candidate or a renewed request is not a new consequence.
    consequence = {key: candidate.get(key) for key in ("target", "scope", "relation", "outcomes", "addition")}
    transfer = candidate.get("transfer")
    if isinstance(transfer, dict):
        consequence["transfer"] = {key: transfer.get(key) for key in ("kind", "target_inference", "plan")}
        consequence["transfer"]["evidence"] = sorted(_evidence_identities(transfer.get("evidence"), records))
    else:
        consequence["transfer"] = transfer
    return consequence


def _known_evidence_identities(records, lineage):
    known = set()
    for ancestor in lineage.values():
        payload = ancestor["payload"]
        known.update(_evidence_identities(payload, records))
        for identifier in payload.get("work_items", []):
            saved = get_record(records, "research_work_item", identifier)
            known.update(_evidence_identities({"kind": "record", "record_kind": "research_work_item",
                                              "id": identifier, "digest": digest(saved)}, records))
        for dependency in payload.get("dependencies", []):
            known.update(_evidence_identities({"kind": "record", "record_kind": dependency["kind"],
                                              "id": dependency["id"], "digest": dependency["digest"]}, records))
    for review in records.get("value_review", {}).values():
        if review["dossier_id"] in lineage:
            known.update(_evidence_identities(review["payload"], records))
    return known


def validate_material_change(records, artifacts, value, previous, current):
    if value is None:
        raise ResearchError("strategy_material_change_required", "A successor must declare its material scientific change or explicit reconsideration")
    fields(value, ("kind", "reason", "evidence", "prior_objections"), code="invalid_strategy")
    choice(value["kind"], ("new_evidence", "new_deduction", "new_comparison", "intent_change", "reconsideration", "review_correction"), "Material change kind")
    text(value["reason"], "Material change reason")
    evidence(records, artifacts, value["evidence"])
    lineage, ancestor = {}, previous
    while ancestor is not None and ancestor["id"] not in lineage:
        lineage[ancestor["id"]] = ancestor
        parent = ancestor["payload"]["previous"]
        ancestor = get_record(records, "strategy_dossier", parent) if parent is not None else None
    if value["kind"] == "intent_change":
        old_intent = get_record(records, "research_intent", previous["payload"]["intent_id"])
        new_intent = get_record(records, "research_intent", current["intent_id"])
        scientific_fields = ("task_kind", "full_objective", "branch", "user_standard", "deliverables", "publication", "resources")
        changed = (old_intent["objective_binding"]["content_digest"] != new_intent["objective_binding"]["content_digest"]
                   or any(old_intent["payload"][key] != new_intent["payload"][key] for key in scientific_fields))
        if (old_intent["id"] == new_intent["id"] or not changed
                or old_intent["instruction_provenance"]["artifact"]["sha256"] == new_intent["instruction_provenance"]["artifact"]["sha256"]):
            raise ResearchError("strategy_material_change_unsubstantiated", "An intent-change reassessment requires a substantive changed instruction, not a new label or timestamp")
    if value["kind"] == "review_correction":
        from .review_protocol import adjudication_state
        admitted = []
        for reference in value["evidence"]:
            if reference.get("kind") != "record" or reference.get("record_kind") != "review_adjudication":
                continue
            finding = get_record(records, "review_adjudication", reference["id"])
            state = adjudication_state(records, artifacts, reference["id"])
            if (finding["dossier_id"] == previous["id"] and finding["correction_admissible"] is True
                    and state["ready"]):
                admitted.append(finding["id"])
        if not admitted:
            raise ResearchError("strategy_material_change_unsubstantiated", "A review correction needs a current observed admissibility finding for the preceding dossier")
    if value["kind"] == "reconsideration":
        if current["reconsideration"] is None:
            raise ResearchError("strategy_material_change_unsubstantiated", "A reconsideration must retain its original decision, alternatives and unmet consequence")
        known = _known_evidence_identities(records, lineage)
        consequence = _reconsidered_consequence(current, records)
        if (consequence is not None and not _evidence_identities(value["evidence"], records) - known
                and any(_reconsidered_consequence(ancestor["payload"], records) == consequence for ancestor in lineage.values())):
            raise ResearchError("strategy_material_change_unsubstantiated", "Reconsider a distinct candidate consequence or new scientific grounds; unchanged grounds reuse the recorded findings")
    if value["kind"] in ("new_evidence", "new_deduction", "new_comparison"):
        known = _known_evidence_identities(records, lineage)
        if not _evidence_identities(value["evidence"], records) - known:
            raise ResearchError("strategy_material_change_unsubstantiated", "A new scientific input needs exact new evidence; changed wording cannot reset the decision")
    dispositions = items(value["prior_objections"], "Prior-objection continuity")
    seen = set()
    for disposition in dispositions:
        fields(disposition, ("id", "status", "reason", "evidence"), code="invalid_strategy")
        text(disposition["id"], "Prior objection ID")
        choice(disposition["status"], ("resolved", "continuing"), "Prior objection status")
        text(disposition["reason"], "Prior objection disposition")
        evidence(records, artifacts, disposition["evidence"], required=disposition["status"] == "resolved")
        if disposition["id"] in seen:
            raise ResearchError("invalid_strategy", "Dispose of each prior objection once")
        seen.add(disposition["id"])
    prior = {objection["id"] for review in records.get("value_review", {}).values()
             if review["dossier_id"] in lineage for objection in review["payload"]["objections"]}
    if prior != seen:
        raise ResearchError("strategy_objection_history_missing", "Carry every prior material objection into the next decision", {"required": sorted(prior)})


def _tranche(records, value, previous):
    if value is None:
        return
    fields(value, ("id", "question", "method", "limit", "end_condition", "failure_signal", "expected_evidence",
                   "predecessor", "previous_outcome"), code="invalid_strategy")
    for key in ("id", "question", "method", "end_condition", "failure_signal", "expected_evidence"):
        text(value[key], "Tranche " + key)
    fields(value["limit"], ("unit", "amount"), code="invalid_strategy")
    text(value["limit"]["unit"], "Tranche resource unit")
    number(value["limit"]["amount"], "Tranche resource limit", positive=True)
    if value["predecessor"] is None:
        if value["previous_outcome"] is not None:
            raise ResearchError("invalid_strategy", "An initial tranche has no predecessor outcome")
    else:
        choice(value["previous_outcome"], ("answered", "failed", "exhausted", "unresolved"), "Previous tranche outcome")
        tranches = [saved["payload"]["tranche"] for saved in records.get("strategy_dossier", {}).values()
                    if saved["payload"]["tranche"] is not None and saved["payload"]["tranche"]["id"] == value["predecessor"]]
        if not tranches:
            raise ResearchError("strategy_tranche_missing", "Name the preceding recorded resource tranche")
    for saved in records.get("strategy_dossier", {}).values():
        tranche = saved["payload"]["tranche"]
        if tranche is not None and tranche["id"] == value["id"] and tranche != value:
            raise ResearchError("record_conflict", "A tranche identity cannot reset its recorded question or limits")
        if (tranche is not None and tranche["id"] != value["id"]
                and normalized_text(tranche["question"]) == normalized_text(value["question"])
                and value["predecessor"] != tranche["id"]):
            raise ResearchError("strategy_tranche_history_missing", "A repeated question must name its previous tranche and actual outcome")


def _continuity(records, artifacts, value, previous):
    dispositions, covered = value["continuity"], set()
    for group in items(dispositions, "Claim continuity"):
        fields(group, ("claim_ids", "disposition", "reason", "evidence"), code="invalid_strategy")
        strings(group["claim_ids"], "Explicit claim group", nonempty=True)
        choice(group["disposition"], ("retained", "superseded", "withdrawn", "irrelevant"), "Claim disposition")
        text(group["reason"], "Claim-group disposition reason")
        evidence(records, artifacts, group["evidence"], required=False)
        if covered.intersection(group["claim_ids"]):
            raise ResearchError("strategy_claim_history_missing", "Each old claim belongs to exactly one explicit disposition")
        covered.update(group["claim_ids"])
    if previous is not None:
        prior_claims = {claim["id"] for claim in previous["payload"]["claims"]}
        if covered != prior_claims:
            raise ResearchError("strategy_claim_history_missing", "Explicitly account for every earlier claim", {"required": sorted(prior_claims)})
        old_failures = {failure["id"] for failure in previous["payload"]["failures"]}
        if not old_failures <= {failure["id"] for failure in value["failures"]}:
            raise ResearchError("strategy_failure_history_missing", "Retain earlier failed routes and their relevance to the new approach")
    elif covered:
        raise ResearchError("strategy_claim_history_missing", "Initial continuity cannot name nonexistent prior claims")


def record_strategy(store, payload, *, expected_revision, request_id):
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        fields(value, ("id", "previous", "intent_id", "phase", "objective", "candidates", "selected_candidate", "no_branch",
                       "single_candidate", "claims", "comparators", "dependencies", "work_items", "leads", "failures", "continuity",
                       "material_change", "reconsideration", "tranche", "recommendation", "bundle_digest"), code="invalid_strategy")
        old = existing_record(records, "strategy_dossier", value)
        if old is not None:
            return [], old
        intent = get_record(records, "research_intent", value["intent_id"], "research_intent_missing")
        if current_intent(records)["id"] != intent["id"]:
            raise ResearchError("research_intent_stale", "Use the current actual instruction")
        choice(value["phase"], PHASES, "Decision phase")
        binding = objective_binding(records, artifacts, value["objective"])
        if not _same_objective(binding, intent["objective_binding"]):
            raise ResearchError("strategy_objective_mismatch", "The dossier cannot replace its intent's exact objective")
        previous = None if value["previous"] is None else get_record(records, "strategy_dossier", value["previous"])
        science = {key: val for key, val in value.items() if key not in ("id", "previous", "recommendation")}
        fingerprint = digest(science)
        if any(saved["fingerprint"] == fingerprint for saved in records.get("strategy_dossier", {}).values()):
            raise ResearchError("strategy_duplicate", "Reuse existing findings for identical scientific inputs and intent")
        latest = current_dossier(records)
        if latest is not None and (previous is None or previous["id"] != latest["id"]):
            raise ResearchError("strategy_material_change_required", "Continue the current dossier with its material change and retained scientific history")
        if previous is None and value["material_change"] is not None:
            raise ResearchError("invalid_strategy", "A material change must name its previous dossier")
        if value["phase"] != "prospective" and binding["kind"] != "native":
            raise ResearchError("strategy_objective_missing", "A result decision binds the committed native objective")
        if value["phase"] == "post_measurement":
            from .publication import find_selected_bundle
            bundle = find_selected_bundle(records)
            if bundle is None or bundle.get("digest") != value["bundle_digest"]:
                raise ResearchError("strategy_bundle_missing", "A post-measurement dossier binds an existing exact manuscript bundle")
        elif value["bundle_digest"] is not None:
            raise ResearchError("invalid_strategy", "An early research decision does not require a manuscript bundle")
        candidates = items(value["candidates"], "Comparative candidate slate", nonempty=True)
        identifiers, signatures = set(), set()
        for candidate_index, candidate in enumerate(candidates):
            _candidate(records, artifacts, candidate, path=f"/candidates/{candidate_index}")
            signature = tuple(normalized_text(candidate[key]) for key in ("question", "target", "scope", "method"))
            if candidate["id"] in identifiers or signature in signatures:
                raise ResearchError("strategy_alternative_duplicate", "Candidate alternatives must differ in scientific claim or method")
            identifiers.add(candidate["id"])
            signatures.add(signature)
        if value["selected_candidate"] not in identifiers:
            raise ResearchError("strategy_candidate_missing", "Select an actual candidate from the compared slate")
        text(value["no_branch"], "Comparison with not executing this branch")
        if len(candidates) == 1:
            if value["single_candidate"] is None:
                raise ResearchError("strategy_alternatives_missing", "A single feasible candidate needs evidence why alternatives are unavailable")
            fields(value["single_candidate"], ("reason", "evidence"), code="invalid_strategy")
            text(value["single_candidate"]["reason"], "Alternative unavailability")
            evidence(records, artifacts, value["single_candidate"]["evidence"])
        elif value["single_candidate"] is not None:
            raise ResearchError("invalid_strategy", "Single-candidate justification applies only to a single candidate")
        claim_ids = set()
        for claim in items(value["claims"], "Supported claims"):
            fields(claim, ("id", "statement", "scope", "evidence"), code="invalid_strategy")
            for key in ("id", "statement", "scope"):
                text(claim[key], "Claim " + key)
            if claim["id"] in claim_ids:
                raise ResearchError("invalid_strategy", "Claim identities must be unique")
            claim_ids.add(claim["id"])
            evidence(records, artifacts, claim["evidence"])
        for comparator in items(value["comparators"], "Prior-work comparators"):
            fields(comparator, ("id", "work_id", "relation", "reading_status", "evidence"), code="invalid_strategy")
            for key in ("id", "work_id", "relation"):
                text(comparator[key], "Comparator " + key)
            choice(comparator["reading_status"], ("abstract", "fulltext", "unavailable"), "Comparator reading status")
            evidence(records, artifacts, comparator["evidence"], required=comparator["reading_status"] != "unavailable")
        for dependency in items(value["dependencies"], "Bound record dependencies"):
            fields(dependency, ("kind", "id", "digest"), code="invalid_strategy")
            held = get_record(records, dependency["kind"], dependency["id"])
            if digest(held) != dependency["digest"]:
                raise ResearchError("strategy_dependency_stale", "A bound scientific record changed", {"id": dependency["id"]})
        strings(value["work_items"], "Persistent work-item revisions")
        for identifier in value["work_items"]:
            work = get_record(records, "research_work_item", identifier)
            if work["payload"]["intent_id"] != intent["id"] and intent["payload"]["previous"] is None:
                raise ResearchError("strategy_work_item_mismatch", "Work items must belong to this intent lineage")
        lead_ids = set()
        for link in items(value["leads"], "Research leads"):
            fields(link, ("id", "candidate_id", "reason", "reopen_trigger"), code="invalid_strategy")
            lead = get_record(records, "research_lead", link["id"])
            if link["id"] in lead_ids:
                raise ResearchError("invalid_strategy", "A lead appears once in the candidate comparison")
            lead_ids.add(link["id"])
            text(link["reason"], "Lead disposition")
            if link["candidate_id"] is None:
                text(link["reopen_trigger"], "Unselected lead reopening trigger")
            elif link["candidate_id"] not in identifiers:
                raise ResearchError("strategy_candidate_missing", "A selected lead enters an actual compared candidate")
        required_leads = {lead["id"] for lead in records.get("research_lead", {}).values() if lead["payload"]["intent_id"] == intent["id"]}
        if lead_ids != required_leads:
            raise ResearchError("strategy_lead_missing", "Retain a disposition for every available recorded lead", {"required": sorted(required_leads)})
        for failure in items(value["failures"], "Retained failures"):
            fields(failure, ("id", "reason", "evidence"), code="invalid_strategy")
            text(failure["id"], "Failure ID")
            text(failure["reason"], "Failure relevance and treatment")
            evidence(records, artifacts, failure["evidence"])
        _continuity(records, artifacts, value, previous)
        _tranche(records, value["tranche"], previous)
        if previous is not None:
            validate_material_change(records, artifacts, value["material_change"], previous, value)
        if value["reconsideration"] is not None:
            reconsideration = value["reconsideration"]
            fields(reconsideration, ("decision_id", "reason", "alternatives", "remaining_obligations"), code="invalid_strategy")
            earlier = get_record(records, "research_decision", reconsideration["decision_id"])
            text(reconsideration["reason"], "Reduced-consequence reconsideration")
            strings(reconsideration["alternatives"], "Reconsidered original and subsequent alternatives", nonempty=True)
            strings(reconsideration["remaining_obligations"], "Unfulfilled original consequence", nonempty=True)
            original = get_record(records, "strategy_dossier", earlier["payload"]["dossier_id"])
            if not {candidate["id"] for candidate in original["payload"]["candidates"]} <= set(reconsideration["alternatives"]):
                raise ResearchError("strategy_reconsideration_incomplete", "Reconsider the original slate without treating failed alternatives as available")
            if previous is None or value["material_change"]["kind"] != "reconsideration":
                raise ResearchError("invalid_strategy", "Reduced-consequence reconsideration uses its explicit material-change route")
        fields(value["recommendation"], ("action", "reason"), code="invalid_strategy")
        choice(value["recommendation"]["action"], ACTIONS, "Author recommendation")
        text(value["recommendation"]["reason"], "Author recommendation reason")
        record = {"id": value["id"], "payload": value, "digest": digest(value), "fingerprint": fingerprint,
                  "recorded_revision": expected_revision + 1, "objective_binding": binding, "source_snapshot": source_snapshot(records)}
        return [immutable_record(records, "strategy_dossier", value["id"], record),
                ("strategy_selection", "dossier", {"id": value["id"]})], record

    return prepared_mutation(store, "strategy.dossier", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def record_work_item(store, payload, *, expected_revision, request_id):
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        fields(value, ("id", "previous", "item_id", "intent_id", "requests", "classification", "execution_state", "claim_ids",
                       "disposition", "reason", "evidence", "resource_implications", "reopen_trigger", "cycle_ids"), code="invalid_work_item")
        old = existing_record(records, "research_work_item", value)
        if old is not None:
            return [], old
        get_record(records, "research_intent", value["intent_id"], "research_intent_missing")
        for key in ("item_id", "reason", "resource_implications"):
            text(value[key], "Work item " + key, code="invalid_work_item")
        choice(value["classification"], CLASSIFICATIONS, "Obligation class", "invalid_work_item")
        choice(value["execution_state"], EXECUTION_STATES, "Execution status", "invalid_work_item")
        choice(value["disposition"], ("active", "deferred", "rejected", "resolved", "withdrawn"), "Work disposition", "invalid_work_item")
        strings(value["claim_ids"], "Affected claim IDs")
        strings(value["cycle_ids"], "Observed cycle IDs")
        for identifier in value["cycle_ids"]:
            get_record(records, "cycle", identifier)
        requests = items(value["requests"], "Original reviewer requests")
        for request in requests:
            fields(request, ("origin", "request_id", "reviewer", "statement"), code="invalid_work_item")
            for key in request:
                text(request[key], "Original request " + key)
            for recorded in records.get("research_work_item", {}).values():
                earlier = recorded["payload"]
                if earlier["item_id"] != value["item_id"] and any(
                        request["origin"] == original["origin"] and request["request_id"] == original["request_id"]
                        for original in earlier["requests"]):
                    raise ResearchError("work_item_request_reassigned", "A repeated original reviewer request retains its stable work-item identity")
        previous = None if value["previous"] is None else get_record(records, "research_work_item", value["previous"])
        if previous is not None:
            earlier = previous["payload"]
            if earlier["item_id"] != value["item_id"] or not {digest(request) for request in earlier["requests"]} <= {digest(request) for request in requests}:
                raise ResearchError("work_item_history_missing", "A work-item revision must preserve its stable identity and every original request")
            current = records.get("work_item_selection", {}).get(value["item_id"])
            if current is not None and current["id"] != previous["id"]:
                raise ResearchError("work_item_history_missing", "Continue the current work-item revision")
            if earlier["execution_state"] in ("attempted", "validated", "failed") and value["execution_state"] in ("unattempted", "planned"):
                raise ResearchError("work_item_history_missing", "A later plan cannot erase an observed attempt")
        else:
            if value["item_id"] in records.get("work_item_selection", {}):
                raise ResearchError("work_item_history_missing", "An existing work item requires its previous revision")
        if value["disposition"] == "resolved" and value["execution_state"] != "validated":
            raise ResearchError("work_item_unresolved", "Adoption, rejection and deferral do not resolve scientific work")
        if value["disposition"] in ("deferred", "rejected") and value["reopen_trigger"] is None:
            raise ResearchError("work_item_reopening_required", "Deferred or rejected work needs an evidence-bound reopening trigger")
        if value["reopen_trigger"] is not None:
            text(value["reopen_trigger"], "Work reopening trigger")
        evidence(records, artifacts, value["evidence"], required=value["execution_state"] not in ("unattempted", "planned") or value["disposition"] != "active")
        record = {"id": value["id"], "payload": value, "digest": digest(value), "recorded_revision": expected_revision + 1}
        return [immutable_record(records, "research_work_item", value["id"], record),
                ("work_item_selection", value["item_id"], {"id": value["id"]})], record

    return prepared_mutation(store, "strategy.work_item", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def record_lead(store, payload, *, expected_revision, request_id):
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        fields(value, ("id", "intent_id", "origin", "origin_id", "discrepancy", "contradicts", "verification_status", "input_knowledge",
                       "evidence", "possible_consequence", "next_check", "disposition", "reason", "reopen_trigger"), code="invalid_lead")
        old = existing_record(records, "research_lead", value)
        if old is not None:
            return [], old
        get_record(records, "research_intent", value["intent_id"], "research_intent_missing")
        for key in ("origin_id", "discrepancy", "contradicts", "possible_consequence", "next_check", "reason"):
            text(value[key], "Lead " + key, code="invalid_lead")
        choice(value["origin"], ("prior_verification", "prior_review", "study_review", "failed_check", "unexpected_result", "hypothesis"), "Lead origin", "invalid_lead")
        choice(value["verification_status"], ("suspected", "verified_in_origin", "independently_verified", "refuted"), "Verification status", "invalid_lead")
        if type(value["input_knowledge"]) is not bool or (value["origin"] in ("prior_verification", "prior_review") and not value["input_knowledge"]):
            raise ResearchError("lead_origin_mismatch", "A prior finding remains input knowledge, not a discovery of this study")
        choice(value["disposition"], ("candidate", "deferred", "rejected"), "Lead disposition", "invalid_lead")
        if value["disposition"] != "candidate":
            text(value["reopen_trigger"], "Unselected lead reopening trigger", code="invalid_lead")
        evidence(records, artifacts, value["evidence"])
        fingerprint = digest({key: value[key] for key in ("intent_id", "origin", "origin_id", "discrepancy", "evidence")})
        if any(saved["fingerprint"] == fingerprint for saved in records.get("research_lead", {}).values()):
            raise ResearchError("lead_duplicate", "Reuse the original discrepancy identity")
        record = {"id": value["id"], "payload": value, "digest": digest(value), "fingerprint": fingerprint,
                  "recorded_revision": expected_revision + 1}
        return [immutable_record(records, "research_lead", value["id"], record)], record

    return prepared_mutation(store, "strategy.lead", payload, prepare, expected_revision=expected_revision, request_id=request_id)
