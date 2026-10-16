"""Mechanical evidence minimums evaluated on the current claim snapshot."""

from .challenge import find_current_challenge
from .evaluation import Evaluation
from .evidence import digest
from .graph import format_search_requirement_id, obligation
from .literature import find_search_integrity_errors, foundation_state
from .synthesis import synthesis_state


def describe_direction(records, artifacts):
    """Assess the minimum and identify the exact decisive evidence on one snapshot."""
    evaluation = Evaluation.of(records, artifacts)
    scope = records.get("literature_scope", {}).get("research", {})
    selection = records.get("search_selection", {}).get("research:direct", {})
    search_id = selection.get("search_id")
    search = records.get("literature_search", {}).get(search_id)
    obligations = []
    current = (search is not None and search["profile"] == "research" and search["purpose"] == "direct"
               and search["scope_digest"] == digest(scope))
    if not current:
        obligations.append(obligation("direction_search_missing", "Select a direct research search against the current literature scope.",
                                      search_id=search_id, **({"coverage": search["coverage"]} if search and "coverage" in search else {})))
    elif find_search_integrity_errors(search) or "dispositions" not in search:
        obligations.append(obligation("direction_search_incomplete", "Account for captured results and resolve direct-search integrity errors; bounded enumeration is allowed.",
            search_id=search_id, integrity_errors=find_search_integrity_errors(search),
            **({"coverage": search["coverage"]} if "coverage" in search else {})))
    decisive = {item["work_id"] for item in search.get("dispositions", [])
                if item["disposition"] in ("relevant", "contradictory")} if current else set()
    # A relevant disposition need not have been cited by the author. Give the
    # foundation evaluator the claim's full-text requirements in a derived view;
    # no requirement or reading is written to the source ledger.
    requirements = dict(records.get("fulltext_requirement", {}))
    for version in decisive:
        identifier = format_search_requirement_id(search_id, version)
        requirements.setdefault(identifier, {"id": identifier, "profile": "research", "version_id": version,
            "purpose": "novelty", "critical": True, "reason": "Decisive source in the direction's selected direct search."})
    reading_records = dict(records, fulltext_requirement=requirements) if requirements != records.get("fulltext_requirement", {}) else records
    foundation = foundation_state(reading_records, evaluation, "research")
    inventory = {item["version_id"]: item for item in foundation["inventory"]}
    reading_gaps = foundation["obligations"] + foundation.get("deferred_obligations", [])
    for versions, code in ((decisive, "direction_reading_missing"), (scope.get("roots", []), "direction_root_reading_missing")):
        for version in sorted(versions):
            if not inventory.get(version, {}).get("fulltext_read", False):
                deferral = records.get("source_deferral_selection", {}).get(version, {})
                obligations.append(obligation(code, "Read every required unit of this decisive exact source; a deferral leaves the gap open.",
                    version_id=version, deferral_id=deferral.get("deferral_id"),
                    obligations=[item for item in reading_gaps if item.get("version_id") == version]))
    selected = records.get("synthesis_selection", {}).get("research:standards")
    if selected is None:
        obligations.append(obligation("direction_standards_missing", "Select a source-grounded field standards section.", section="standards"))
    else:
        section = synthesis_state(records, evaluation, "research")["sections"]["standards"]
        current_dependencies = {key: value for key, value in section.get("dependencies", {}).items() if key != "foundation"}
        bound_dependencies = {key: value for key, value in section.get("bound_dependencies", {}).items() if key != "foundation"}
        findings = [item for item in section["obligations"]
                    if item["code"] != "synthesis_dependencies_stale" or current_dependencies != bound_dependencies]
        if findings:
            obligations.append(obligation("direction_standards_unresolved", "Resolve the selected standards section's evidence and applicability findings.",
                                          section_id=selected["id"], obligations=findings))
    challenge = find_current_challenge(records)
    if challenge is None:
        obligations.append(obligation("grand_challenge_missing", "Record the study's current Grand Challenge."))
    def collect_readings(versions):
        return [{"version_id": version, "reading_ids": sorted((inventory.get(version, {}).get("body_coverage") or {}).get("readings", {}))}
                for version in sorted(versions)]

    standard = records.get("synthesis", {}).get(selected["id"]) if selected else None
    evidence = {"direct_search_id": search_id if current else None, "decisive": collect_readings(decisive),
                "roots": collect_readings(scope.get("roots", [])),
                "standards": {"id": standard["id"], "digest": digest(standard)} if standard else None,
                "grand_challenge_id": challenge["id"] if challenge else None}
    return {"obligations": sorted(obligations, key=lambda item: (item["code"], digest(item))),
            "evidence": evidence, "digest": digest(evidence)}


def assess_direction(records, artifacts):
    """Return the direction's gaps without granting reading or review credit."""
    return describe_direction(records, artifacts)["obligations"]
