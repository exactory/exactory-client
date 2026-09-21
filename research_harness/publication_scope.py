"""Immutable supported-manuscript targets, separate from the complete objective.

Scientific identity excludes administrative aliases. Exact candidate identity
retains freshness and provenance. An adverse scientific target stays adverse;
independent review of a correction may accept only its different successor.
"""

import json
import unicodedata

from .artifacts import ArtifactStore
from .development import _Context, _Evidence, _candidate, _REVIEW_CHECKS, _unique, cycle_authors
from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from .execution_evidence import _observed, author_readiness_state
from .graph import obligation
from .operations import fields, immutable_record, prepared_mutation, strings, text
from .source_deferrals import assess_deferrals, build_source_gap_disclosure
from .source_links import read_locator
from .workspace import strict_json


POLICY = "source-limited-v1"
_CODE = "invalid_publication_scope"


def _prose(value):
    return " ".join(unicodedata.normalize("NFC", value).split())


def _ordered(values):
    return sorted(values, key=lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False))


def _scientific_set(values):
    return _ordered({digest(value): value for value in values}.values())


def find_publication_scope(records):
    selection = records.get("publication_scope_selection", {}).get("current")
    if selection is None or selection["id"] is None:
        return None
    saved = records.get("publication_scope", {}).get(selection["id"])
    if saved is None:
        raise ResearchError("publication_scope_missing", "The explicitly selected publication scope is missing")
    if not isinstance(saved, dict) or saved.get("digest") != digest({k: v for k, v in saved.items() if k != "digest"}):
        raise ResearchError("publication_scope_corrupt", "The selected immutable scope record failed its content binding")
    return saved


def has_publication_scope(records):
    """An invalid selected ID still opts in and therefore fails closed."""
    return records.get("publication_scope_selection", {}).get("current", {}).get("id") is not None


def _canonical_evidence(context, reference):
    item = _Evidence(context).one(reference)
    if reference["kind"] == "source":
        link = reference["link"]
        return {"kind": "source", "original_sha256": item["original_sha256"],
                "content_sha256": link["artifact"]["sha256"], "version_id": context.artifacts.link(link)["work"]["id"],
                "locator": _canonical_locator(link["locator"])}
    return {"kind": "result", "content_sha256": reference["artifact"]["sha256"],
            "locator": reference["locator"], "role": item["requirement_kind"]}


def _canonical_locator(value):
    if isinstance(value, dict):
        if {"path", "sha256", "size", "media_type"} <= value.keys():
            return {"content_sha256": value["sha256"]}
        return {key: _canonical_locator(item) for key, item in value.items() if key not in ("source_id", "capture_id")}
    if isinstance(value, list):
        return [_canonical_locator(item) for item in value]
    return value


def build_scientific_target(context, payload):
    """Canonical scientific content, never an independent support certificate."""
    limitations = {item["id"]: {"statement": _prose(item["statement"]),
                              "version_ids": sorted(set(item["version_ids"]))}
                   for item in payload["public_limitations"]}
    claims = [{"statement": _prose(c["statement"]), "polarity": c["polarity"],
               "assumptions": sorted({_prose(a) for a in c["assumptions"]}),
               "evidence": _scientific_set([_canonical_evidence(context, e) for e in c["evidence"]]),
               "limitations": _scientific_set([limitations[i] for i in c["limitation_ids"]])}
              for c in payload["supported_claims"]]
    return {"scope": {"statement": _prose(payload["scope"]["statement"]),
                      "assumptions": sorted({_prose(a) for a in payload["scope"]["assumptions"]})},
            "claims": _scientific_set(claims), "limitations": _scientific_set(list(limitations.values())),
            "source_debt": _scientific_set([{k: _prose(d[k]) if k != "version_id" else d[k]
                                       for k in ("obligation", "version_id", "dependent_claim")}
                                      for d in payload["deferred_objective_obligations"]])}


def build_scientific_scope_projection(payload):
    """Public text and exact evidence; private artifact descriptors never enter."""
    projection = {"policy": POLICY, "scope": payload["scope"], "claims": payload["supported_claims"],
                  "public_limitations": payload["public_limitations"], "objective_complete": False}
    return dict(projection, digest=digest(projection))


def _validate_payload(context, payload, candidate):
    fields(payload, ("id", "policy", "objective_digest", "checkpoint_id", "assessment_id", "assessment_digest",
                     "authorization", "scientific_preparers", "scope", "supported_claims", "public_limitations",
                     "deferred_objective_obligations", "correction"), ("scientific_delivery",), code=_CODE)
    text(payload["id"], "Contract ID", code=_CODE)
    if payload["policy"] != POLICY:
        raise ResearchError(_CODE, "Use the explicit source-limited-v1 policy")
    if candidate is None or any(payload[k] != candidate[k] for k in ("checkpoint_id", "assessment_id", "assessment_digest")):
        raise ResearchError("publication_scope_stale", "Bind the selected current assessment and checkpoint")
    if payload["objective_digest"] != digest(context.objective):
        raise ResearchError("publication_scope_stale", "Keep the exact original complete objective")
    if not context.artifacts.read(payload["authorization"]).strip():
        raise ResearchError(_CODE, "Pin the actual existing user directive")
    preparers = payload["scientific_preparers"]
    if not isinstance(preparers, list) or not preparers:
        raise ResearchError(_CODE, "Identify every scientific scope preparer")
    for preparer in preparers:
        fields(preparer, ("id", "kind", "provenance", "contributions"), code=_CODE)
        text(preparer["id"], "Scientific preparer", code=_CODE)
        if preparer["kind"] not in ("human", "agent") or not context.artifacts.read(preparer["provenance"]).strip():
            raise ResearchError(_CODE, "Identify a human or agent preparer with checked provenance")
        strings(preparer["contributions"], "Scientific contributions", nonempty=True, code=_CODE)
        if not set(preparer["contributions"]) <= {"claim_selection", "gap_classification", "scientific_correction"}:
            raise ResearchError(_CODE, "Declare scientific scope contributions")
    fields(payload["scope"], ("id", "statement", "assumptions"), code=_CODE)
    for key in ("id", "statement"):
        text(payload["scope"][key], "Supported scope " + key, code=_CODE)
    strings(payload["scope"]["assumptions"], "Supported assumptions", code=_CODE)
    assessment = context.assessment(candidate["assessment_id"])
    if not set(assessment["payload"]["assumptions"]) <= set(payload["scope"]["assumptions"]):
        raise ResearchError("scope_assumptions_missing", "Retain the assessed scientific assumptions")
    limitations, claims = payload["public_limitations"], payload["supported_claims"]
    if not isinstance(limitations, list) or not limitations or not isinstance(claims, list) or not claims:
        raise ResearchError(_CODE, "Declare supported claims and public source limitations")
    limitation_ids = set()
    for item in limitations:
        fields(item, ("id", "statement", "version_ids"), code=_CODE)
        text(item["id"], "Limitation ID", code=_CODE)
        text(item["statement"], "Public scientific limitation", code=_CODE)
        strings(item["version_ids"], "Limited source versions", code=_CODE)
        if item["id"] in limitation_ids:
            raise ResearchError(_CODE, "Use unique limitation IDs")
        limitation_ids.add(item["id"])
    allowed = {digest(e) for e in candidate["evidence"]}
    seen = set()
    for claim in claims:
        fields(claim, ("id", "statement", "polarity", "assumptions", "evidence", "limitation_ids"), code=_CODE)
        for key in ("id", "statement"):
            text(claim[key], "Supported claim " + key, code=_CODE)
        if claim["id"] in seen or claim["polarity"] not in ("positive", "negative", "limitation"):
            raise ResearchError(_CODE, "Declare unique claims with explicit scientific polarity")
        seen.add(claim["id"])
        strings(claim["assumptions"], "Claim assumptions", code=_CODE)
        strings(claim["limitation_ids"], "Claim limitations", nonempty=True, code=_CODE)
        if not set(claim["limitation_ids"]) <= limitation_ids:
            raise ResearchError(_CODE, "Use declared public limitations")
        if not set(payload["scope"]["assumptions"]) <= set(claim["assumptions"]):
            raise ResearchError("scope_assumptions_missing", "Each claim retains the supported scope assumptions")
        _Evidence(context).many(claim["evidence"], "Supported claim evidence")
        if any(digest(e) not in allowed for e in claim["evidence"]):
            raise ResearchError("publication_evidence_mismatch", "Use actual current assessed candidate evidence")
    _validate_debt(context, payload, assessment)
    if "scientific_delivery" in payload:
        from .scientific_delivery import validate_declarations
        validate_declarations(payload["scientific_delivery"])


def _validate_debt(context, payload, assessment):
    mappings = payload["deferred_objective_obligations"]
    if not isinstance(mappings, list) or not mappings:
        raise ResearchError("publication_scope_debt_mismatch", "Map every remaining objective obligation exactly once")
    active = {d["id"]: d for d in assess_deferrals(context.records, context.artifacts) if d["status"] == "active"}
    seen = set()
    for mapping in mappings:
        fields(mapping, ("obligation", "deferral_id", "version_id", "dependency_digest", "dependent_claim", "reason"),
               code="publication_scope_debt_mismatch")
        for key in mapping:
            text(mapping[key], "Deferred objective " + key, code="publication_scope_debt_mismatch")
        deferred = active.get(mapping["deferral_id"])
        if (mapping["obligation"] in seen or deferred is None
                or any(mapping[k] != deferred[k] for k in ("version_id", "dependency_digest"))
                or mapping["dependent_claim"] not in deferred["dependent_claims"]):
            raise ResearchError("publication_scope_debt_mismatch", "Bind only an exact current active acquisition gap and retained dependent claim")
        seen.add(mapping["obligation"])
    disclosed = {v for item in payload["public_limitations"] for v in item["version_ids"]}
    if (seen != set(assessment["remaining_obligations"])
            or not {d["version_id"] for d in active.values()} <= disclosed
            or not {d["version_id"] for d in mappings} <= disclosed):
        raise ResearchError("publication_scope_debt_mismatch", "Cover the exact remaining obligation union and disclose every active source gap")
    if assessment["payload"]["objective_status"] != "open" or assessment["complete"]:
        raise ResearchError("publication_scope_objective_open", "Source-limited publication retains the incomplete original objective")


def _adverse(payload):
    return payload["verdict"] != "ready" or any(c["status"] != "passed" for c in payload["checks"])


def required_corrections(records):
    """The service derives the retained findings; selection never removes them."""
    result = []
    for kind in ("readiness_review", "scoped_readiness_review"):
        for review in records.get(kind, {}).values():
            payload = review["payload"]
            if not _adverse(payload):
                continue
            findings = [c["kind"] for c in payload["checks"] if c["status"] != "passed"]
            if payload["verdict"] != "ready":
                findings.append("verdict")
            result.append({"kind": kind, "review_id": review["id"], "review_digest": digest(review),
                           "scientific_target_digest": review.get("scientific_target_digest", review["candidate"]["digest"]),
                           "findings": sorted(findings)})
    return sorted(result, key=lambda value: (value["kind"], value["review_id"]))


def _validate_correction(context, payload, projection, required):
    correction = payload["correction"]
    if not required:
        if correction is not None:
            raise ResearchError("publication_scope_correction_mismatch", "There are no predecessor findings to bridge")
        return []
    if correction is None:
        raise ResearchError("publication_scope_correction_required", "Respond to every retained adverse predecessor review",
                            {"required_corrections": required})
    fields(correction, ("response", "predecessors", "changes"), code="publication_scope_correction_mismatch")
    if any(not isinstance(correction[k], list) for k in ("predecessors", "changes")):
        raise ResearchError("publication_scope_correction_mismatch", "List every predecessor and exact scientific change")
    if not context.artifacts.read(correction["response"]).strip():
        raise ResearchError("publication_scope_correction_mismatch", "Pin the scientific response to predecessor findings")
    expected = {(r["kind"], r["review_id"]): r for r in required}
    seen, ancestors = set(), set()
    for item in correction["predecessors"]:
        fields(item, ("kind", "review_id", "review_digest", "scientific_target_digest", "findings"),
               code="publication_scope_correction_mismatch")
        key = item["kind"], item["review_id"]
        target = expected.get(key)
        if key in seen or target is None or any(item[k] != target[k] for k in ("review_digest", "scientific_target_digest")):
            raise ResearchError("publication_scope_correction_mismatch", "Bind each exact service-required predecessor review")
        seen.add(key)
        findings = set()
        for finding in item["findings"]:
            fields(finding, ("check", "response", "disposition", "evidence", "deferred_obligation"),
                   code="publication_scope_correction_mismatch")
            text(finding["response"], "Finding response", code=_CODE)
            if finding["check"] in findings or finding["check"] not in target["findings"]:
                raise ResearchError("publication_scope_correction_mismatch", "Address every adverse check exactly once")
            findings.add(finding["check"])
            if finding["disposition"] == "open_source_debt":
                if item["kind"] != "readiness_review" or finding["deferred_obligation"] not in {
                        d["obligation"] for d in payload["deferred_objective_obligations"]}:
                    raise ResearchError("publication_scope_correction_mismatch", "Only original full-objective findings may remain explicitly mapped source debt")
            elif finding["disposition"] != "addressed" or finding["deferred_obligation"] is not None:
                raise ResearchError("publication_scope_correction_mismatch", "Address scientific findings or retain exact full-objective source debt")
            _Evidence(context).many(finding["evidence"], "Correction finding evidence")
        if findings != set(target["findings"]):
            raise ResearchError("publication_scope_correction_mismatch", "Retain every predecessor finding")
        previous = context.records[item["kind"]][item["review_id"]]
        if strict_json(context.artifacts.read(previous["artifact"])) != previous["payload"]:
            raise ResearchError("publication_scope_correction_mismatch", "The predecessor review artifact differs from its immutable payload")
        if item["kind"] == "scoped_readiness_review":
            ancestors.add(previous["payload"]["target"]["contract_id"])
    if seen != set(expected):
        raise ResearchError("publication_scope_correction_mismatch", "A response cannot omit an adverse predecessor")
    changed_targets = set()
    for change in correction["changes"]:
        fields(change, ("predecessor_target", "field", "before", "after", "evidence"), code="publication_scope_correction_mismatch")
        targets = [r for r in required if r["scientific_target_digest"] == change["predecessor_target"]]
        if not targets or change["field"] not in projection:
            raise ResearchError("publication_scope_correction_mismatch", "Identify an exact changed scientific field")
        predecessor = targets[0]
        old_review = context.records[predecessor["kind"]][predecessor["review_id"]]
        old = old_review.get("scientific_projection", {})
        if (change["before"] != old.get(change["field"]) or change["after"] != projection[change["field"]]
                or change["before"] == change["after"]):
            raise ResearchError("publication_scope_correction_mismatch", "The correction must change the stated scientific field from its actual predecessor value")
        _Evidence(context).many(change["evidence"], "Scientific correction evidence")
        changed_targets.add(change["predecessor_target"])
    if changed_targets != {r["scientific_target_digest"] for r in required}:
        raise ResearchError("publication_scope_correction_mismatch", "Document scientific changes from every adverse predecessor target")
    return sorted(ancestors)


def scientific_authors(records, contract):
    # Scope selection and explicit clearing do not erase scientific authorship
    # within the study, just as cycle selection does not erase cycle authors.
    authors, pending, visited = set(cycle_authors(records)), list(records.get("publication_scope", {}).values()) + [contract], set()
    while pending:
        current = pending.pop()
        if current["id"] in visited:
            continue
        visited.add(current["id"])
        authors.update(p["id"] for p in current["payload"]["scientific_preparers"])
        pending.extend(records["publication_scope"][i] for i in current.get("ancestor_contract_ids", []))
    return sorted(authors)


def record_publication_scope(store, payload, *, expected_revision, request_id):
    def prepare(records, value):
        context = _Context(records, ArtifactStore(store.root))
        context.require_objective()
        candidate, _ = _candidate(context)
        _validate_payload(context, value, candidate)
        projection = build_scientific_target(context, value)
        required = required_corrections(records)
        ancestors = _validate_correction(context, value, projection, required)
        selected = find_publication_scope(records)
        if selected is not None:
            ancestors = sorted(set(ancestors + [selected["id"]]))
        saved = {"id": value["id"], "payload": value, "scientific_projection": projection,
                 "scientific_target_digest": digest(projection), "ancestor_contract_ids": ancestors,
                 "required_corrections": required, "public_projection": build_scientific_scope_projection(value)}
        saved["digest"] = digest(saved)
        if value["id"] in records.get("publication_scope", {}):
            raise ResearchError("record_conflict", "A scope ID cannot replace an immutable scientific contract")
        return [immutable_record(records, "publication_scope", value["id"], saved),
                ("publication_scope_selection", "current", {"id": value["id"]})], saved
    return prepared_mutation(store, "publication.scope", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def select_publication_scope(store, payload, *, expected_revision, request_id):
    def prepare(records, value):
        fields(value, ("id",), code=_CODE)
        if value["id"] is not None and value["id"] not in records.get("publication_scope", {}):
            raise ResearchError("publication_scope_missing", "Select an existing immutable contract or null")
        return [("publication_scope_selection", "current", value)], value
    return prepared_mutation(store, "publication.select_scope", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def _scope_snapshot(records, artifacts):
    context = _Context(records, artifacts)
    candidate, _ = _candidate(context)
    contract = find_publication_scope(records)
    if contract is None:
        raise ResearchError("publication_scope_missing", "Select an explicit immutable source-limited contract")
    _validate_payload(context, contract["payload"], candidate)
    projection = build_scientific_target(context, contract["payload"])
    if projection != contract["scientific_projection"] or digest(projection) != contract["scientific_target_digest"]:
        raise ResearchError("publication_scope_stale", "The contract no longer identifies its checked scientific evidence")
    current_findings = {(r["kind"], r["review_id"]): r for r in required_corrections(records)}
    for required in contract["required_corrections"]:
        if current_findings.get((required["kind"], required["review_id"])) != required:
            raise ResearchError("publication_scope_correction_mismatch", "The retained predecessor record differs from its immutable correction binding")
    _validate_correction(context, contract["payload"], projection, contract["required_corrections"])
    for required in current_findings.values():
        previous = records[required["kind"]][required["review_id"]]
        if strict_json(artifacts.read(previous["artifact"])) != previous["payload"]:
            raise ResearchError("publication_scope_correction_mismatch", "Retained adverse review bytes must match their immutable findings")
    dependencies = {"contract": contract["digest"], "candidate": candidate["digest"],
                    "prerequisites": context.prerequisites, "deferrals": assess_deferrals(records, artifacts)}
    observations, pending = {}, []
    for identifier in sorted({e["execution_id"] for e in candidate["evidence"] if e["kind"] == "result"}):
        if records["execution"][identifier]["payload"]["origin"]["kind"] == "managed":
            try:
                observations[identifier] = _observed(records, artifacts, identifier)
            except ResearchError as error:
                pending.append(obligation(error.code, error.message, execution_id=identifier))
    dependencies["observations"] = observations
    return context, candidate, contract, digest(dependencies), observations, pending


def _review(context, value, candidate, contract, candidate_digest):
    from .publication import validate_assessor
    fields(value, ("id", "target", "candidate_digest", "assessor", "verdict", "checks", "limitations"), code=_CODE)
    text(value["id"], "Scoped review ID", code=_CODE)
    if (value["target"] != {"kind": "source_limited_manuscript", "contract_id": contract["id"],
                           "scientific_target_digest": contract["scientific_target_digest"]}
            or value["candidate_digest"] != candidate_digest):
        raise ResearchError("scoped_review_stale", "Review the exact current scoped scientific target and freshness snapshot")
    validate_assessor(context.artifacts, value["assessor"], scientific_authors(context.records, contract))
    if value["verdict"] not in ("ready", "not_ready", "unresolved"):
        raise ResearchError(_CODE, "Give an explicit scientific readiness verdict")
    strings(value["limitations"], "Review limitations", nonempty=True, code=_CODE)
    expected = set(_REVIEW_CHECKS) | {"source_limits"}
    if contract["required_corrections"]:
        expected.add("corrections")
    evidence, seen = _Evidence(context), set()
    for check in value["checks"]:
        fields(check, ("kind", "status", "reason", "evidence"), code=_CODE)
        if check["kind"] not in expected or check["kind"] in seen or check["status"] not in ("passed", "failed", "unresolved"):
            raise ResearchError(_CODE, "Assess each required scoped scientific check once")
        text(check["reason"], "Independent scientific reason", code=_CODE)
        seen.add(check["kind"])
        evidence.many(check["evidence"], "Scoped independent evidence")
    if seen != expected or not {digest(e) for e in candidate["evidence"]} <= {digest(e["reference"]) for e in evidence.summary()}:
        raise ResearchError("review_evidence_incomplete", "Independently assess every scientific check and candidate evidence reference")


def record_scoped_readiness_review(store, payload, *, expected_revision, request_id):
    def prepare(records, value):
        context, candidate, contract, exact, _, _ = _scope_snapshot(records, Evaluation(records, ArtifactStore(store.root)))
        _review(context, value, candidate, contract, exact)
        artifact = context.artifacts.put(json.dumps(value, sort_keys=True, ensure_ascii=False).encode(), "application/json")
        saved = {"id": value["id"], "payload": value, "artifact": artifact, "candidate": candidate,
                 "scientific_target_digest": contract["scientific_target_digest"],
                 "scientific_projection": contract["scientific_projection"], "reviewed_revision": expected_revision + 1}
        return [immutable_record(records, "scoped_readiness_review", value["id"], saved),
                ("scoped_review_selection", contract["id"], {"id": value["id"]})], saved
    return prepared_mutation(store, "publication.scoped_review", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def assess_manuscript_readiness(records, artifacts):
    artifacts = Evaluation.of(records, artifacts)
    full = author_readiness_state(records, artifacts)
    obligations, contract, exact, observations, review = [], None, None, {}, None
    candidate = full["candidate"]
    remaining = []
    try:
        contract = find_publication_scope(records)
        context, candidate, contract, exact, observations, pending = _scope_snapshot(records, artifacts)
        assessment = context.assessment(candidate["assessment_id"])
        remaining = assessment["remaining_obligations"]
        obligations.extend(context.prerequisites["validity"] + context.prerequisites["workflow"] + pending)
        for cycle in records.get("cycle", {}).values():
            if cycle["assessment_id"] is None:
                obligations.append(obligation("development_assessment_missing", "Assess every retained actual branch", cycle_id=cycle["id"]))
        current_target = contract["scientific_target_digest"]
        adverse = [r for r in records.get("scoped_readiness_review", {}).values()
                   if r["scientific_target_digest"] == current_target and _adverse(r["payload"])]
        if adverse:
            obligations.append(obligation("scoped_target_rejected", "A retained adverse review blocks this identical scientific target",
                                          review_ids=sorted(r["id"] for r in adverse)))
        owed = [r for r in required_corrections(records) if r["scientific_target_digest"] != current_target]
        if owed != contract["required_corrections"]:
            obligations.append(obligation("publication_scope_correction_required", "Bind all newly retained predecessor findings in a new immutable correction"))
        selection = records.get("scoped_review_selection", {}).get(contract["id"])
        if selection is None:
            obligations.append(obligation("scoped_review_missing", "Obtain an independent assessment of this supported manuscript target"))
        else:
            review = records["scoped_readiness_review"][selection["id"]]
            if strict_json(artifacts.read(review["artifact"])) != review["payload"]:
                raise ResearchError("scoped_review_corrupt", "The current independent review artifact differs from its immutable payload")
            _review(context, review["payload"], candidate, contract, exact)
            if _adverse(review["payload"]):
                obligations.append(obligation("scoped_review_pending", "Resolve the scientific finding through a substantively corrected target"))
    except ResearchError as error:
        obligations.append(obligation(error.code, error.message, **(error.details or {})))
    obligations = _unique(obligations)
    result = {"ready": not obligations, "manuscript_ready": not obligations, "manuscript_obligations": obligations,
              "obligations": obligations, "objective_complete": full["ready"], "objective_obligations": full["obligations"],
              "remaining_obligations": remaining, "contract": contract, "candidate": candidate, "candidate_digest": exact,
              "selected_contract_id": records.get("publication_scope_selection", {}).get("current", {}).get("id"),
              "required_corrections": required_corrections(records),
              "scientific_target_digest": contract["scientific_target_digest"] if contract else None,
              "review": review, "review_inputs": full["review_inputs"], "execution_observations": observations,
              "source_gaps": build_source_gap_disclosure((full.get("synthesis") or {}).get("foundation", {}).get("source_deferrals", [])),
              "mechanical_only": True, "native_proof_acceptance": False}
    result["counts"] = {"cycles": len(records.get("cycle", {})), "obligations": len(obligations)}
    return result


def manuscript_readiness_report(store):
    snapshot = store.snapshot()
    return dict(assess_manuscript_readiness(snapshot["records"], ArtifactStore(store.root)), revision=snapshot["revision"])


def validate_manuscript_claims(contract, claims, claim_evidence):
    payload = contract["payload"]
    expected = [{"id": c["id"], "claim": c["statement"], "polarity": c["polarity"],
                 "assumptions": c["assumptions"], "limitation_ids": c["limitation_ids"],
                 "public_limitations": [l for l in payload["public_limitations"] if l["id"] in c["limitation_ids"]]}
                for c in payload["supported_claims"]]
    mappings = [{"claim_id": c["id"], "evidence": c["evidence"]} for c in payload["supported_claims"]]
    if _ordered(claims) != _ordered(expected) or _ordered(claim_evidence) != _ordered(mappings):
        raise ResearchError("publication_scope_claim_mismatch", "The manuscript must retain every exact approved claim, polarity, assumption, evidence mapping and public limitation")


def publication_scope_binding(bundle):
    scope = bundle.get("publication_scope")
    return ({key: scope[key] for key in ("mode", "contract_id", "scientific_target_digest", "objective_complete")}
            if scope is not None else None)


def validate_expected_scope(store, contract_id, target_digest):
    records = store.snapshot()["records"]
    contract = find_publication_scope(records)
    if contract is None:
        raise ResearchError("publication_scope_missing", "Strict scoped publication requires its selected contract")
    if contract["id"] != contract_id or contract["scientific_target_digest"] != target_digest:
        raise ResearchError("publication_scope_mismatch", "The selected scientific contract differs from the caller's explicit publication target")
    report = assess_manuscript_readiness(records, ArtifactStore(store.root))
    if not report["manuscript_ready"]:
        raise ResearchError("manuscript_readiness_required", "The scoped scientific target is not currently ready",
                            {"obligations": report["manuscript_obligations"]})


def validate_scope_binding(bundle, expected):
    if publication_scope_binding(bundle) != expected:
        raise ResearchError("publication_scope_mismatch", "The current bundle differs from the publication intent's exact scientific scope")


def validate_caller_scope(binding, contract_id, target_digest):
    if contract_id is not None or target_digest is not None:
        if binding is None or binding.get("contract_id") != contract_id or binding.get("scientific_target_digest") != target_digest:
            raise ResearchError("publication_scope_mismatch", "The publication intent differs from the caller's explicit contract and scientific target")
