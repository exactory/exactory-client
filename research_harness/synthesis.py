"""Evidence-linked field standards, ABT reasoning, innovation and context.

Each record_* operation accepts an immutable {id, profile, scope, ...} payload,
binds current configuration/foundation/source-reading dependencies, and selects
it through synthesis_selection/{profile}:{kind}. A new assessment needs a new
ID; old records and operation results remain unchanged. Section readiness is
mechanical only. synthesis_report additionally requires the current foundation,
configuration and all profile-applicable sections.

And must declare established context. Citation roots do not determine a case's
study-specific field relationship.
"""

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evidence import digest
from .graph import obligation
from .identities import resolve_family
from .literature import _historical_status, foundation_state
from .operations import fields, immutable_record, iso_date, prepared_mutation, profile_name, strings, text
from .principles import configuration_state
from .reading import fulltext_coverage, required_unit_obligations, validate_read_evidence
from .source_links import captured_source, exact_work, original_identity, validate_link


_RESEARCH_SECTIONS = ("standards", "rationale", "innovation", "context")
_PAPER_TYPES = ("paper", "article", "journal-article", "proceedings-article", "preprint", "posted-content")
_LIMITS = ("Mechanical source anchoring and documented inspection do not establish comprehension, entailment, "
           "scientific truth, historical priority, field-standard adherence, transfer validity or social benefit. "
           "Support and scientific-status labels are scoped analyst judgments. Quantitative scope and field "
           "applicability require substantive review; no natural-language classifier verifies their completeness. "
           "Verification soundness remains independent of authoring goals and other verdicts.")


def _items(value, name, nonempty=True):
    if not isinstance(value, list) or nonempty and not value:
        raise ResearchError("invalid_synthesis", name + " must be an array" + (" with at least one entry" if nonempty else ""))
    return value


def _text(value, name):
    return text(value, name, code="invalid_synthesis")


def _strings(value, name, nonempty=True):
    return strings(value, name, nonempty=nonempty, code="invalid_synthesis")


def _gaps(value, name, nonempty=False):
    for gap in _items(value, name, nonempty):
        fields(gap, ("scope", "reason", "next_evidence"), code="invalid_synthesis")
        for key in gap:
            _text(gap[key], name + " " + key)


class _Assessment:
    def __init__(self, records, artifacts, target):
        self.records, self.artifacts, self.target = records, artifacts, target
        self.obligations, self.evidence = [], {}

    def linked(self, link, path):
        key = digest(link)
        if key not in self.evidence:
            context = validate_link(self.records, self.artifacts, link)
            work, source = context["work"], context["source"]
            pin = self.target if self.target and self.target.get("kind") == "work" and self.target["id"] == work["id"] else None
            reading, pending = None, []
            try:
                reading = validate_read_evidence(self.records, self.artifacts, link, depth="fulltext", target=pin)
            except ResearchError as error:
                if error.code != "reading_missing":
                    raise
                pending.append(obligation(error.code, error.message, **(error.details or {})))
                # Expose the same current source-unit obligations as the reading
                # boundary, including sources outside the citation graph.
                coverage = fulltext_coverage(self.records, self.artifacts, work["id"], target=pin)
                for missing in coverage["missing"]:
                    pending.extend(missing["pending"])
                    bundle = self.records.get("source_bundle", {}).get(missing["bundle_id"])
                    if bundle is not None:
                        pending.extend(required_unit_obligations(self.records, self.artifacts, bundle))
            aliases = {k: a for k, a in self.records.get("alias", {}).items()
                       if any(x["work_id"] == work["work_id"] for x in a["assertions"])}
            assertions = [self.records["work_assertion"][a] for a in work["assertion_ids"]]
            metadata_ids = {a["source_id"] for a in assertions}
            metadata_ids.update(a["source_id"] for alias in aliases.values() for a in alias["assertions"])
            metadata_sources = []
            for source_id in sorted(metadata_ids):
                metadata = captured_source(self.records, self.artifacts, source_id)
                metadata_sources.append({"source_id": source_id, "artifact": metadata["response"],
                                         "url": metadata["url"], "captured_at": metadata["captured_at"]})
            self.evidence[key] = {"link": link, "work_id": work["work_id"], "original_sha256": original_identity(self.records, link),
                "reading": reading, "pending": pending, "date_assertions": work["date_assertions"],
                "captured_at": source["captured_at"], "source_url": source["url"], "aliases": aliases,
                "metadata_sources": metadata_sources, "work_assertions_digest": digest(assertions),
                "type_assertions": [{"type": a["type"], "source_id": a["source_id"], "assertion_id": a["assertion_id"]} for a in assertions],
                "known_originals": sorted({c["original"]["sha256"] for c in work["fulltexts"] if c["original"] is not None})}
        item = self.evidence[key]
        self.obligations.extend(dict(p, claim_path=path) for p in item["pending"])
        return item

    def claim(self, value, path):
        fields(value, ("statement", "scope", "assumptions", "evidence", "date", "evidence_timing", "source_support",
                       "scientific_status", "uncertainties"), ("quantitative_scope",), code="invalid_synthesis")
        for key in ("statement", "scope"):
            _text(value[key], path + " " + key)
        _strings(value["assumptions"], path + " assumptions", False)
        try:
            iso_date(value["date"])
        except ResearchError as error:
            raise ResearchError("invalid_synthesis", "Claim dates must be explicit ISO calendar dates", {"claim_path": path}) from error
        if value["evidence_timing"] not in ("current", "contemporaneous", "retrospective"):
            raise ResearchError("invalid_synthesis", "Distinguish current, contemporaneous and retrospective evidence")
        if value["source_support"] not in ("supported_as_scoped", "source_not_supported", "source_contradicted", "unresolved"):
            raise ResearchError("invalid_synthesis", "Use an explicit source-support judgment")
        if value["scientific_status"] not in ("established", "proposed", "unresolved", "refuted"):
            raise ResearchError("invalid_synthesis", "Keep the scientific judgment separate from source support")
        _gaps(value["uncertainties"], path + " uncertainties", value["source_support"] != "supported_as_scoped")
        for dimension in _items(value.get("quantitative_scope", []), "Quantitative scope", False):
            fields(dimension, ("dimension", "value", "units", "role", "status", "reason"), code="invalid_synthesis")
            _text(dimension["dimension"], "Quantity dimension")
            _text(dimension["reason"], "Quantity scope or uncertainty")
            if dimension["role"] not in ("observed", "fitted", "external_input", "selection", "derived", "context"):
                raise ResearchError("invalid_synthesis", "Distinguish observed, fitted, external, selected and derived quantities")
            if dimension["status"] not in ("known", "unknown", "not_applicable"):
                raise ResearchError("invalid_synthesis", "State whether the quantity dimension is known, unknown or inapplicable")
            if dimension["status"] == "known":
                _text(dimension["value"], "Known quantity value")
            elif dimension["value"] is not None:
                raise ResearchError("invalid_synthesis", "Unknown or inapplicable quantities cannot claim a known value")
            if dimension["units"] is not None:
                _text(dimension["units"], "Quantity units")
        before = len(self.obligations)
        for link in _items(value["evidence"], path + " evidence"):
            self.linked(link, path)
            if value["evidence_timing"] == "contemporaneous":
                work = exact_work(self.records, link["version_id"])
                temporal = _historical_status(work, value["date"])
                if temporal != "known_before":
                    self.obligations.append(obligation("historical_version_unresolved",
                        "Acquire the content version that existed at the stated time; current text cannot establish earlier prior art.",
                        claim_path=path, version_id=work["id"], cutoff=value["date"], status=temporal))
        if value["source_support"] != "supported_as_scoped":
            self.obligations.append(obligation("claim_source_support_unresolved",
                "Assess the claim's attribution within its stated source scope; this is not a scientific refutation.",
                claim_path=path, source_support=value["source_support"], scientific_status=value["scientific_status"]))
        return len(self.obligations) == before

    def claims(self, values, path):
        for index, claim in enumerate(_items(values, path)):
            self.claim(claim, path + "/" + str(index))

    def events(self, value, path):
        fields(value, ("claims", "gaps"), code="invalid_synthesis")
        for index, claim in enumerate(_items(value["claims"], path + " claims", False)):
            self.claim(claim, path + "/claims/" + str(index))
        _gaps(value["gaps"], path + " gaps")
        if not value["claims"] and not value["gaps"]:
            raise ResearchError("invalid_synthesis", "Record each asserted later event with evidence, or state its scoped gap")


def _value(value):
    fields(value, ("kind", "beneficiaries", "capabilities", "uncertainties"), code="invalid_synthesis")
    if value["kind"] not in ("basic_science", "application", "both"):
        raise ResearchError("invalid_synthesis", "State the scientific or application value honestly")
    _beneficiaries(value)
    _gaps(value["uncertainties"], "Value uncertainties")


def _beneficiaries(value):
    _strings(value["beneficiaries"], "Beneficiaries", False)
    _strings(value["capabilities"], "Downstream scientific capabilities", False)
    if not value["beneficiaries"] and not value["capabilities"]:
        raise ResearchError("invalid_synthesis", "Identify beneficiaries or downstream scientific capabilities")


def _selected(records, profile, kind):
    identifier = records.get("synthesis_selection", {}).get(profile + ":" + kind, {}).get("id")
    return records.get("synthesis", {}).get(identifier)


def _innovation(state, value):
    families = {"external": set(), "within_field": set()}
    seen, eligible = set(), []
    standards = _selected(state.records, value["profile"], "standards")
    field = standards["payload"]["field"].casefold().strip() if standards else None
    if "origin_collection" in value:
        fields(value["origin_collection"], ("id", "version"), code="invalid_synthesis")
        for key in value["origin_collection"]:
            _text(value["origin_collection"][key], "Shared case collection " + key)
    for index, case in enumerate(_items(value["cases"], "Innovation cases")):
        fields(case, ("id", "work_id", "relation", "field", "selection_reason", "original", "bottleneck", "prior_constraint",
                      "conceptual_change", "later_validation", "adoption", "transfer"), ("selection_signals",), code="invalid_synthesis")
        for key in ("id", "field", "selection_reason"):
            _text(case[key], "Case " + key)
        if case["id"] in seen or case["relation"] not in ("external", "within_field"):
            raise ResearchError("invalid_synthesis", "Cases need unique labels and an explicit study-specific field relationship")
        seen.add(case["id"])
        work = exact_work(state.records, case["work_id"])
        if work["id"] != case["work_id"]:
            raise ResearchError("invalid_synthesis", "Case work_id must name the canonical exact original paper version")
        family = resolve_family(state.records, case["work_id"])
        path = "cases/" + str(index)
        original_ready = state.claim(case["original"], path + "/original")
        if not any(link["version_id"] == work["id"] for link in case["original"]["evidence"]):
            raise ResearchError("invalid_synthesis", "An original-result claim must cite the case's exact original paper")
        for key in ("bottleneck", "prior_constraint", "conceptual_change"):
            state.claim(case[key], path + "/" + key)
        for key in ("later_validation", "adoption"):
            state.events(case[key], path + "/" + key)
            for event_index, event in enumerate(case[key]["claims"]):
                if iso_date(event["date"]) < iso_date(case["original"]["date"]):
                    state.obligations.append(obligation("innovation_event_order_unresolved",
                        "Reconcile the event dates and their meanings; preserve the conflicting assertions and original source dates.",
                        claim_path=path + "/" + key + "/claims/" + str(event_index),
                        original_date=case["original"]["date"], event_date=event["date"]))
        transfer = case["transfer"]
        fields(transfer, ("mechanism", "mapping", "assumptions", "test", "failure_conditions", "limits"), code="invalid_synthesis")
        state.claim(transfer["mechanism"], path + "/transfer/mechanism")
        for key in ("mapping", "test"):
            _text(transfer[key], "Study-specific transfer " + key)
        for key in ("assumptions", "failure_conditions", "limits"):
            _strings(transfer[key], "Transfer " + key)
        _strings(case.get("selection_signals", []), "Discovery signals", False)
        types = sorted({state.records["work_assertion"][a]["type"] for a in work["assertion_ids"]} - {"unknown"})
        if not types or any(kind not in _PAPER_TYPES for kind in types):
            state.obligations.append(obligation("innovation_paper_type_unresolved", "External coverage requires original papers, with source-backed document identity.",
                                                case_id=case["id"], version_id=work["id"], types=types))
            original_ready = False
        if case["relation"] == "external" and field == case["field"].casefold().strip():
            state.obligations.append(obligation("external_case_within_field", "A within-field source cannot also supply external diversity.", case_id=case["id"]))
            original_ready = False
        families[case["relation"]].add(family)
        if original_ready:
            eligible.append((case["relation"], family))
    overlap = families["external"] & families["within_field"]
    if overlap:
        state.obligations.append(obligation("case_field_conflict", "Resolve conflicting field relationships for the same original paper family.", work_ids=sorted(overlap)))
    external = {family for relation, family in eligible if relation == "external" and family not in overlap}
    within = {family for relation, family in eligible if relation == "within_field" and family not in overlap}
    if len(external) < 5:
        state.obligations.append(obligation("external_cases_insufficient", "Analyze five to ten distinct full-read external original papers.", count=len(external), minimum=5))
    if len(external) > 10:
        state.obligations.append(obligation("external_cases_excess", "Select five to ten external original papers for this assessment; keep additional works in the corpus.", count=len(external), maximum=10))
    if not within:
        state.obligations.append(obligation("within_field_case_missing", "Analyze at least one full-read within-field original case."))
    return {"cases": len(value["cases"]), "external_papers": len(external), "within_field_papers": len(within)}


def _assess(records, artifacts, kind, value, configuration, foundation):
    required = {
        "standards": ("field", "article_type", "venue", "cohort_doctrine", "methodology", "reporting", "citation", "presentation", "applicability_questions"),
        "rationale": ("and", "but", "therefore", "value"),
        "innovation": ("cases",),
        "context": ("current", "historical", "beneficiaries", "capabilities", "barriers", "uncertainties", "speculative_links"),
    }
    fields(value, ("id", "profile", "scope") + required[kind], ("origin_collection",) if kind == "innovation" else (), code="invalid_synthesis")
    _text(value["id"], "Assessment ID")
    _text(value["scope"], "Study-specific assessment scope")
    profile_name(value["profile"])
    if kind != "standards" and value["profile"] != "research":
        raise ResearchError("profile_inapplicable", "Verification does not require or adopt an author's private research rationale or innovation goals")
    state = _Assessment(records, artifacts, configuration["target"])
    counts = {}
    if kind == "standards":
        _text(value["field"], "Applicable field")
        for key in ("article_type", "venue"):
            if value[key] is not None:
                _text(value[key], key)
        for key in ("cohort_doctrine", "methodology", "reporting", "citation", "presentation"):
            state.claims(value[key], key)
        _gaps(value["applicability_questions"], "Unresolved standard applicability")
        if value["applicability_questions"]:
            state.obligations.append(obligation("standards_applicability_unresolved", "Resolve the applicability questions before treating the field-standard assessment as current."))
    elif kind == "rationale":
        state.claim(value["and"], "and")
        if value["and"]["scientific_status"] != "established":
            state.obligations.append(obligation("and_context_not_established",
                "And requires cited established context; preserve other scientific judgments as pending premises for reassessment.",
                claim_path="and", scientific_status=value["and"]["scientific_status"]))
        state.claim(value["but"], "but")
        fields(value["therefore"], ("proposal", "test", "failure_conditions"), code="invalid_synthesis")
        for key in ("proposal", "test"):
            _text(value["therefore"][key], "Proposed response " + key)
        _strings(value["therefore"]["failure_conditions"], "Response failure conditions")
        _value(value["value"])
    elif kind == "innovation":
        counts = _innovation(state, value)
    else:
        state.claims(value["current"], "current")
        state.claims(value["historical"], "historical")
        _beneficiaries(value)
        _strings(value["barriers"], "Adoption barriers")
        _gaps(value["uncertainties"], "Context uncertainty", True)
        _gaps(value["speculative_links"], "Speculative applications")
    evidence = [state.evidence[k] for k in sorted(state.evidence)]
    dependencies = {"configuration": configuration["digest"], "constitution": configuration["constitution"],
                    "foundation": foundation["digest"], "scope": digest(records.get("literature_scope", {}).get(value["profile"])),
                    "evidence": digest(evidence)}
    if kind == "innovation":
        standard = _selected(records, value["profile"], "standards")
        dependencies["standards"] = digest(standard) if standard else None
    obligations = _unique(state.obligations)
    return {"ready": not obligations, "obligations": obligations, "counts": dict(counts, obligations=len(obligations)),
            "evidence": evidence, "dependencies": dependencies, "digest": digest({"payload": value, "dependencies": dependencies}),
            "mechanical_only": True}


def _unique(obligations):
    return sorted({digest(o): o for o in obligations}.values(), key=lambda o: (o["code"], digest(o)))


def _record(store, kind, payload, *, expected_revision, request_id):
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        profile = profile_name(value.get("profile"))
        configuration = configuration_state(records, artifacts, profile)
        for item in configuration["obligations"]:
            if item["code"] in ("configuration_missing", "profile_mismatch", "constitution_revalidation_required", "objective_mismatch"):
                raise ResearchError(item["code"], item["explanation"])
        foundation = foundation_state(records, artifacts, profile)
        assessment = _assess(records, artifacts, kind, value, configuration, foundation)
        record = {"id": value["id"], "kind": kind, "profile": profile, "payload": value,
                  "dependencies": assessment["dependencies"], "assessment": assessment}
        changes = [immutable_record(records, "synthesis", value["id"], record),
                   ("synthesis_selection", profile + ":" + kind, {"id": value["id"]})]
        return changes, dict(id=value["id"], kind=kind, **assessment)

    return prepared_mutation(store, "synthesis." + kind, payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def record_standards(store, payload, *, expected_revision, request_id):
    return _record(store, "standards", payload, expected_revision=expected_revision, request_id=request_id)


def record_rationale(store, payload, *, expected_revision, request_id):
    return _record(store, "rationale", payload, expected_revision=expected_revision, request_id=request_id)


def record_innovation(store, payload, *, expected_revision, request_id):
    return _record(store, "innovation", payload, expected_revision=expected_revision, request_id=request_id)


def record_context(store, payload, *, expected_revision, request_id):
    return _record(store, "context", payload, expected_revision=expected_revision, request_id=request_id)


def synthesis_state(records, artifacts, profile):
    """Current synthesis on one snapshot, for later managed gates and cycles."""
    profile_name(profile)
    configuration = configuration_state(records, artifacts, profile)
    foundation = foundation_state(records, artifacts, profile)
    obligations = list(configuration["obligations"]) + list(foundation["obligations"])
    sections = {}
    for kind in _RESEARCH_SECTIONS if profile == "research" else ("standards",):
        record = _selected(records, profile, kind)
        if record is None:
            section = {"ready": False, "obligations": [obligation(kind + "_missing", "Record a current source-linked " + kind + " assessment.")]}
        else:
            try:
                section = _assess(records, artifacts, kind, record["payload"], configuration, foundation)
            except ResearchError as error:
                section = {"ready": False, "obligations": [obligation(error.code, error.message, **(error.details or {}))]}
            if section.get("dependencies") != record["dependencies"]:
                section["ready"] = False
                section["obligations"].append(obligation("synthesis_dependencies_stale",
                    "Record a new current assessment against changed policy, study scope, foundation or source readings.", id=record["id"], section=kind))
            section.update(id=record["id"], payload=record["payload"], bound_dependencies=record["dependencies"])
        section["counts"] = dict(section.get("counts", {}), obligations=len(section["obligations"]))
        sections[kind] = section
        obligations.extend(dict(o, section=kind) for o in section["obligations"])
    obligations = _unique(obligations)
    counts = {"sections": len(sections), "current_sections": sum(s["ready"] for s in sections.values()), "obligations": len(obligations)}
    counts.update({k: v for k, v in sections.get("innovation", {}).get("counts", {}).items() if k != "obligations"})
    return {"ready": not obligations, "digest": digest({"configuration": configuration["digest"], "foundation": foundation["digest"], "sections": sections}),
            "obligations": obligations, "counts": counts, "profile": profile, "configuration": configuration,
            "foundation": foundation, "sections": sections,
            "history": [{"id": r["id"], "kind": r["kind"], "dependencies": r["dependencies"]}
                        for r in records.get("synthesis", {}).values() if r["profile"] == profile],
            "next": obligations[0] if obligations else None, "mechanical_only": True, "limits": _LIMITS}


def synthesis_report(store, profile):
    return synthesis_state(store.snapshot()["records"], ArtifactStore(store.root), profile)
