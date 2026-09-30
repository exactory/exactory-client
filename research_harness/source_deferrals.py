"""Explicit, reversible source gaps. A deferral is never a source reading.

Decisions bind the exact source state and its requirements. Immutable records
retain authorization, acquisition evidence, excluded claims and continuing work;
the selected decision alone may defer source-access and reading obligations.
"""

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from .graph import citation_graph, main_captures, selected_bundle
from .operations import fields, immutable_record, prepared_mutation, strings, text
from .reading import required_unit_obligations
from .source_links import exact_work


ACCESS_CODES = frozenset((
    "critical_source_unavailable", "fulltext_reading_missing", "source_bundle_missing",
    "source_bundle_incomplete", "required_unit_missing", "required_unit_incomplete",
    "required_unit_uninspected", "reading_bundle_stale", "bibliography_incomplete",
    "component_requirement_missing", "component_page_missing",
    "visual_asset_missing",
))
VISUAL_ACCESS_FAILURES = frozenset(("http_status", "network_error", "timeout", "rate_limited",
                                   "incomplete_response", "partial_response"))


def _is_visual_access_gap(item):
    return item["code"] == "visual_asset_missing" or (
        item["code"] == "visual_asset_pending" and item.get("reason") in VISUAL_ACCESS_FAILURES
        and bool(item.get("source_ids")))


def is_source_access_obligation(item):
    """Reading work may be postponed only once a separate acquisition gap exists."""
    return item["code"] in ACCESS_CODES or _is_visual_access_gap(item)


def _has_acquisition_gap(records, evaluation, version):
    work = records["work"][version]
    captures = main_captures(records, work)
    if not captures:
        return True
    for capture in captures:
        bundle = selected_bundle(records, version, {"id": version, "sha256": capture["original"]["sha256"]})
        if bundle is None:
            continue
        for unit in bundle["units"]:
            # Missing locators within an acquired main original are inventory
            # work. Only a declared external supplement can establish a missing
            # component here; linked external visuals are assessed separately.
            if not unit["required"] or unit["kind"] != "supplement" or unit["link"] is not None:
                continue
            # A captured component awaiting inventory or linking is available.
            acquired = False
            for candidate in work["fulltexts"]:
                if candidate["availability"] != "available":
                    continue
                spec = candidate.get("component", {}).get("spec", {})
                if ((unit.get("url") and candidate["url"] == unit["url"])
                        or (spec.get("unit_id") == unit["id"]
                            and spec.get("parent_original_sha256") == bundle["original_sha256"])):
                    acquired = True
                    break
            if not acquired:
                return True
        if any(_is_visual_access_gap(item) for item in required_unit_obligations(records, evaluation, bundle)):
            return True
    return False


def _is_ineligible(records, version):
    config = records.get("configuration", {}).get("research", {})
    scope = records.get("literature_scope", {}).get("research", {})
    verification = records.get("literature_scope", {}).get("verification", {}).get("target") or {}
    if config.get("profile", "research") != "research" or version == verification.get("id"):
        return True
    work = records.get("work", {}).get(version)
    return not work or any(records.get("work", {}).get(root, {}).get("work_id") == work["work_id"]
                           for root in scope.get("roots", []))


def _build_binding(records, evaluation, version):
    graph = evaluation.once(("deferral_graph",), lambda: citation_graph(records, "research"))
    work = records.get("work", {}).get(version)
    bundles = {i: b for i, b in records.get("source_bundle", {}).items() if b["version_id"] == version}
    source_ids = set((work or {}).get("source_ids", []))
    source_ids.update(c["source_id"] for c in (work or {}).get("fulltexts", []) if c["source_id"])
    for bundle in bundles.values():
        source_ids.add(bundle["source_id"])
        source_ids.update(u["link"]["source_id"] for u in bundle["units"] if u["link"])
    return {"work": work, "ineligible": _is_ineligible(records, version),
            "requirements": {i: r for i, r in records.get("fulltext_requirement", {}).items()
                             if r["profile"] == "research" and r["version_id"] == version},
            "tier": next((n["tier"] for n in graph["nodes"] if version in n["version_ids"]), None),
            "historical_cutoff": records.get("literature_scope", {}).get("research", {}).get("historical_cutoff"),
            "sources": {i: records.get("source", {}).get(i) for i in sorted(source_ids)},
            "bundles": bundles, "selection": records.get("bundle_selection", {}).get(version),
            "readings": {i: r for i, r in records.get("reading", {}).items() if r["version_id"] == version},
            "visual_assets": {i: a for i, a in records.get("visual_asset", {}).items() if a["version_id"] == version}}


def _check_artifact(evaluation, reference):
    if not evaluation.read(reference).strip():
        raise ResearchError("invalid_source_deferral", "Source decisions require nonempty checked evidence artifacts")


def assess_deferrals(records, artifacts, profile="research"):
    """All historical decisions, with active, stale, resumed or superseded status."""
    evaluation = Evaluation.of(records, artifacts)

    def compute():
        result = []
        for saved in sorted(records.get("source_deferral", {}).values(), key=lambda r: r["sequence"]):
            if saved["profile"] != profile:
                continue
            _check_artifact(evaluation, saved["authorization"])
            for reference in saved["acquisition_evidence"]:
                _check_artifact(evaluation, reference)
            selected = records.get("source_deferral_selection", {}).get(saved["version_id"], {})
            current = digest(_build_binding(records, evaluation, saved["version_id"]))
            if selected.get("deferral_id") != saved["id"]:
                status = "superseded"
            elif selected.get("resume_id"):
                status = "resumed"
            else:
                status = "active" if current == saved["dependency_digest"] else "stale"
            result.append(dict(saved, status=status, current_dependency_digest=current,
                               resume_id=selected.get("resume_id") if status == "resumed" else None))
        return result

    return evaluation.once(("source_deferrals", profile), compute)


def defer_source(store, payload, *, expected_revision, request_id):
    def prepare(records, value):
        fields(value, ("id", "profile", "version_id", "reason", "authorization", "acquisition_evidence",
                       "dependent_claims", "continuing_work"), code="invalid_source_deferral")
        for key in ("id", "version_id", "reason"):
            text(value[key], key, code="invalid_source_deferral")
        if value["profile"] != "research":
            raise ResearchError("source_deferral_inapplicable", "Only research dependencies may be deferred")
        exact_work(records, value["version_id"])
        if _is_ineligible(records, value["version_id"]):
            raise ResearchError("source_deferral_inapplicable", "Research roots and verification targets cannot be deferred")
        existing = records.get("source_deferral", {}).get(value["id"])
        if existing is not None:
            raise ResearchError("record_conflict", "A source decision ID cannot be reused; replay its original request receipt")
        evaluation = Evaluation(records, ArtifactStore(store.root))
        _check_artifact(evaluation, value["authorization"])
        if not isinstance(value["acquisition_evidence"], list) or not value["acquisition_evidence"]:
            raise ResearchError("invalid_source_deferral", "Retain the actual acquisition evidence artifacts")
        for reference in value["acquisition_evidence"]:
            _check_artifact(evaluation, reference)
        for key in ("dependent_claims", "continuing_work"):
            strings(value[key], key, nonempty=True, code="invalid_source_deferral")
        # The ordinary foundation owns the requirement and reading assessment.
        # Existing deferrals are included when explicitly reassessing a decision.
        from .literature import foundation_state
        foundation = foundation_state(records, evaluation, "research")
        item = next((i for i in foundation["inventory"] if i["version_id"] == value["version_id"]), None)
        if item is None or item["required_depth"] != "fulltext" or item["fulltext_read"]:
            raise ResearchError("source_deferral_inapplicable", "Defer only a current missing full-text requirement")
        if not _has_acquisition_gap(records, evaluation, value["version_id"]):
            raise ResearchError("source_deferral_inapplicable", "Available originals and units require inventory and reading, not source deferral")
        pending = [o for o in foundation["obligations"] + foundation.get("deferred_obligations", [])
                   if o.get("version_id") == value["version_id"] and is_source_access_obligation(o)]
        if not pending:
            raise ResearchError("source_deferral_inapplicable", "No current source-access or reading obligation is missing")
        binding = _build_binding(records, evaluation, value["version_id"])
        saved = dict(value, dependencies=binding, dependency_digest=digest(binding),
                     deferred_obligations=pending, sequence=len(records.get("source_deferral", {})) + 1)
        return [immutable_record(records, "source_deferral", value["id"], saved),
                ("source_deferral_selection", value["version_id"], {"deferral_id": value["id"]})], saved

    return prepared_mutation(store, "literature.defer_source", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def resume_source(store, payload, *, expected_revision, request_id):
    def prepare(records, value):
        fields(value, ("id", "profile", "version_id", "deferral_id", "reason"), code="invalid_source_deferral")
        for key in ("id", "version_id", "deferral_id", "reason"):
            text(value[key], key, code="invalid_source_deferral")
        selected = records.get("source_deferral_selection", {}).get(value["version_id"], {})
        if value["profile"] != "research" or selected != {"deferral_id": value["deferral_id"]}:
            raise ResearchError("source_deferral_inapplicable", "Resume the currently selected active or stale source decision")
        return [immutable_record(records, "source_resumption", value["id"], value),
                ("source_deferral_selection", value["version_id"],
                 {"deferral_id": value["deferral_id"], "resume_id": value["id"]})], value

    return prepared_mutation(store, "literature.resume_source", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def build_source_gap_disclosure(deferrals):
    """Blind-safe scientific limitations, without private instructions or logs."""
    return [{"version_id": item["version_id"], "status": item["status"], "fulltext_read": False,
             "dependent_claims": item["dependent_claims"],
             "limitation": "The deferred source cannot supply claim evidence. Use actually read alternative evidence.",
             "missing_requirements": [{k: o[k] for k in ("code", "version_id", "unit_id") if k in o}
                                      for o in item["deferred_obligations"]]}
            for item in deferrals if item["status"] in ("active", "stale")]
