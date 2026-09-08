"""Minimum paper-family citation tiers with exact-version evidence obligations.

set_roots accepts {profile, roots: [exact IDs], collection_ids: [IDs], target?,
historical_cutoff?}. Verification target is {kind: work, id, source_id, sha256};
an incomplete pin may be prepared but cannot complete the foundation. Scope
changes replace literature_scope/{profile}; Store events preserve prior scopes.

Graph traversal includes every occurrence from Tier 1/2 captured versions.
Registry observations never establish a complete article bibliography. Aliases
resolve families only; an unversioned reference includes all captured versions
of its unambiguous family without claiming their contents are interchangeable.
"""

from .errors import ResearchError
from .identities import family_versions, normalize_identifier
from .operations import fields, iso_date, prepared_mutation, profile_name, strings
from .source_links import exact_work, fulltext_capture


def obligation(code, explanation, **affected):
    return dict(code=code, explanation=explanation, **affected)


def validate_target(records, target, roots):
    fields(target, ("kind", "id", "source_id", "sha256"), code="invalid_target")
    if target["kind"] != "work" or target["id"] not in roots:
        raise ResearchError("invalid_target", "Verification roots must include the exact pinned work")
    work = exact_work(records, target["id"])
    if target["source_id"] is None or target["sha256"] is None:
        if target["source_id"] is not None or target["sha256"] is not None:
            raise ResearchError("invalid_target", "An incomplete source pin must leave both source_id and sha256 null")
        return
    capture = fulltext_capture(work, target["source_id"])
    if (capture is None or capture["original"] is None or capture["original"]["sha256"] != target["sha256"]
            or capture["availability"] != "available"):
        raise ResearchError("invalid_target", "The pin must identify the exact work's available original main document")


def set_roots(store, payload, *, expected_revision, request_id):
    def prepare(records, value):
        fields(value, ("profile", "roots", "collection_ids"), ("target", "historical_cutoff"))
        profile_name(value["profile"])
        roots = strings(value["roots"], "Roots", nonempty=True, code="invalid_roots")
        if len(roots) > 5:
            raise ResearchError("invalid_roots", "Choose one to five closest paper families")
        works = []
        for identifier in roots:
            try:
                work = exact_work(records, identifier)
            except ResearchError as error:
                raise ResearchError("invalid_roots", "Roots must name acquired exact versions", {"identifier": identifier}) from error
            if work["id"] != identifier:
                raise ResearchError("invalid_roots", "Use the canonical exact work ID for each root")
            works.append(work)
        if len({w["work_id"] for w in works}) != len(roots):
            raise ResearchError("invalid_roots", "Root comparisons must be distinct paper families")
        strings(value["collection_ids"], "Collections")
        for collection in value["collection_ids"]:
            if collection not in records.get("collection", {}):
                raise ResearchError("unknown_collection", "Collect the frozen cohort before selecting it")
        if value.get("historical_cutoff") is not None:
            iso_date(value["historical_cutoff"])
        if value["profile"] == "verification":
            validate_target(records, value.get("target"), roots)
            configured = records.get("configuration", {}).get("research", {})
            if configured.get("profile") == "verification" and configured.get("target") != value["target"]:
                raise ResearchError("target_mismatch", "Literature target must match the configured verification target")
        elif value.get("target") is not None:
            raise ResearchError("invalid_target", "Research literature scope does not define an author objective")
        return [("literature_scope", value["profile"], value)], value

    return prepared_mutation(store, "literature.roots", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def selected_bundle(records, version_id, target=None):
    selection = records.get("bundle_selection", {}).get(version_id, {})
    selected = records.get("source_bundle", {}).get(selection.get("bundle_id"))
    if target is None or target.get("id") != version_id or target.get("sha256") is None:
        return selected
    candidates = [b for b in records.get("source_bundle", {}).values()
                  if b["version_id"] == version_id and b.get("original_sha256") == target["sha256"]]
    if selected in candidates:
        return selected
    return max(candidates, key=lambda b: b["sequence"], default=None)


def main_captures(records, work, target=None):
    """Distinct available originals, excluding explicitly inventoried supplements."""
    supplements = {u["link"]["source_id"] for b in records.get("source_bundle", {}).values() if b["version_id"] == work["id"]
                   for u in b["units"] if u["kind"] == "supplement" and u["link"] is not None and u["link"]["source_id"] != b["source_id"]}
    captures = {}
    for capture in work["fulltexts"]:
        if capture["availability"] != "available" or capture["source_id"] in supplements:
            continue
        if target and target.get("id") == work["id"] and capture["original"]["sha256"] != target.get("sha256"):
            continue
        captures.setdefault(capture["original"]["sha256"], capture)
    return list(captures.values())


def citation_graph(records, profile):
    profile_name(profile)
    scope = records.get("literature_scope", {}).get(profile)
    if not scope:
        return {"nodes": [], "references": [], "obligations": [obligation("roots_missing", "Select one to five exact starting works.")]}
    versions, tiers = {}, {}
    obligations, references = [], {}
    for identifier in scope["roots"]:
        work = records.get("work", {}).get(identifier)
        if work is None:
            obligations.append(obligation("root_missing", "Reacquire the selected exact root.", version_id=identifier))
            continue
        versions.setdefault(work["work_id"], set()).add(identifier)
        tiers[work["work_id"]] = 1
    by_source = {}
    for occurrence in records.get("reference_occurrence", {}).values():
        by_source.setdefault(occurrence["source_work_id"], []).append(occurrence)
    processed = {}
    while True:
        pending = [(family, version) for family in sorted(versions) if tiers[family] <= 2
                   for version in sorted(versions[family]) if processed.get(version, 4) > tiers[family]]
        if not pending:
            break
        for family, version in pending:
            tier = tiers[family]
            processed[version] = tier
            for occurrence in sorted(by_source.get(version, []), key=lambda x: x["id"]):
                resolution = records.get("reference_resolution", {}).get(occurrence["id"], {})
                kind, target = resolution.get("kind", occurrence["kind"]), resolution.get("target", occurrence["target"])
                row = {"occurrence_id": occurrence["id"], "version_id": version, "source_id": occurrence["source_id"],
                       "kind": kind, "target": target, "raw": occurrence["raw"], "target_family": None, "status": "unresolved"}
                if kind == "nonpaper":
                    row["status"] = "nonpaper"
                elif target is not None:
                    try:
                        identifier = normalize_identifier(target)
                        candidates = [identifier] if identifier in records.get("work", {}) else family_versions(records, identifier)
                        # Resolve aliases even for an existing exact ID to expose
                        # conflicting registry identity assertions.
                        family_versions(records, identifier)
                        candidates = [v for v in candidates if not v.startswith("arxiv:") or records["work"][v]["version"] is not None]
                        if not candidates:
                            raise ResearchError("missing_version", "Reference has no captured exact version")
                        target_family = records["work"][candidates[0]]["work_id"]
                        row.update(target_family=target_family, status="resolved", version_ids=sorted(candidates))
                        versions.setdefault(target_family, set()).update(candidates)
                        tiers[target_family] = min(tiers.get(target_family, 4), tier + 1)
                    except ResearchError as error:
                        row["status"] = error.code
                references[occurrence["id"]] = row
    for version in sorted(processed):
        captures = main_captures(records, records["work"][version], scope.get("target"))
        for capture in captures or [None]:
            pin = {"id": version, "sha256": capture["original"]["sha256"]} if capture else scope.get("target")
            bundle = selected_bundle(records, version, pin)
            if bundle is None or bundle["scope"] != "article" or not bundle.get("bibliography", {}).get("complete"):
                obligations.append(obligation("bibliography_incomplete", "Import an evidence-linked complete article bibliography; registry counts are insufficient.",
                                              version_id=version, source_id=capture["source_id"] if capture else None))
    for row in references.values():
        if row["status"] not in ("resolved", "nonpaper"):
            code = "reference_identity_ambiguous" if row["status"] == "ambiguous_alias" else "reference_unresolved"
            obligations.append(obligation(code, "Resolve this reference occurrence using saved identity evidence or a supported non-paper classification.",
                version_id=row["version_id"], reference_id=row["occurrence_id"], target=row["target"]))
    return {"nodes": [{"work_id": family, "tier": tiers[family], "version_ids": sorted(versions[family])} for family in sorted(tiers)],
            "references": sorted(references.values(), key=lambda r: r["occurrence_id"]), "obligations": obligations}
