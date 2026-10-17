"""Claim currency, separate from the complete historical audit composition."""

from .strategy import managed_research
from .evidence import digest
from .errors import ResearchError


_ADVISORY = frozenset(("configuration", "constitution", "preparation", "preparation_digest",
                       "synthesis", "literature_digest", "foundation_digest"))


def uses_claim_dependencies(records):
    return (records.get("configuration", {}).get("research", {}).get("profile") == "research"
            and managed_research(records))


def collect_source_ids(value):
    identifiers = set()

    def collect(item):
        if isinstance(item, dict):
            if isinstance(item.get("source_id"), str):
                identifiers.add(item["source_id"])
            identifiers.update(item.get("source_ids", []))
            for child in item.values():
                collect(child)
        elif isinstance(item, list):
            for child in item:
                collect(child)

    collect(value)
    return identifiers


def project_source_inputs(sources, evidence):
    """Bind the checked readings and their inspected bundles, using records only.

    Evidence validation has already established that each full reading's bundle
    has the currently required units. Its checked reading digest also holds the
    recomputed coverage and visual dependencies. Other reading/bundle history
    remains in the source inventory for audit, outside claim currency.
    """
    identifiers = {item["reading"]["reading_id"] for item in evidence if item["reference"]["kind"] == "source"}
    readings = {key: sources["reading"][key] for key in sorted(identifiers)}
    bundles = {reading["bundle_id"] for reading in readings.values() if reading.get("bundle_id") is not None}
    selected = {key: value for key, value in sources.items() if key != "source"}
    selected.update(reading=readings, source_bundle={key: sources["source_bundle"][key] for key in sorted(bundles)})
    selected["source"] = {key: sources["source"][key] for key in sorted(collect_source_ids(selected))}
    return selected


def project_claim_dependencies(value, *, managed, kind="dependencies", project_readings=True):
    """Project internal dependency compositions; retain the original maps for audit.

    Traverse known report fields only. Record IDs, runtime names, source records
    and scientific payloads may themselves contain names such as "synthesis".
    Source inventories are selected by the caller before hashing; immutable
    plan, execution and checkpoint identities retain their original audit bytes.
    """
    if not managed:
        return value
    if kind in ("dependencies", "candidate"):
        return {key: item for key, item in value.items() if key not in _ADVISORY}
    if kind == "configuration":
        return {key: item for key, item in value.items() if key != "constitution"}
    if kind == "record":
        return dict(value, dependencies=project_claim_dependencies(value["dependencies"], managed=True))
    if kind == "branches":
        return {identifier: dict(branch, assessment=project_claim_dependencies(
                    branch["assessment"], managed=True, kind="record") if branch["assessment"] is not None else None)
                for identifier, branch in value.items()}
    if kind == "readiness":
        projected = {key: item for key, item in value.items() if key != "synthesis"}
        if projected.get("candidate") is not None:
            projected["candidate"] = project_claim_dependencies(projected["candidate"], managed=True, kind="candidate")
        if projected.get("review_inputs") is not None:
            inputs = {key: item for key, item in projected["review_inputs"].items() if key != "synthesis"}
            if project_readings and "sources" in inputs:
                evidence = []
                for branch in inputs["branches"].values():
                    evidence.extend(branch["plan_evidence"])
                    if branch["assessment"] is not None:
                        evidence.extend(branch["assessment"]["evidence"])
                inputs["sources"] = project_source_inputs(inputs["sources"], evidence)
            if inputs.get("candidate") is not None:
                inputs["candidate"] = project_claim_dependencies(inputs["candidate"], managed=True, kind="candidate")
            inputs["branches"] = project_claim_dependencies(inputs["branches"], managed=True, kind="branches")
            projected["review_inputs"] = inputs
        return projected
    raise ValueError("Unknown claim dependency composition: " + kind)


def accept_claim_dependencies(recorded, current, *, managed, recorded_inputs=None,
                              recorded_claim=None, kind="dependencies"):
    """Authenticate historical inputs before comparing their scientific projection.

    Callers reconstruct opaque subjects from recorded snapshots, never current
    global state. A separately derived claim composition permits transitive
    digest aliases, but cannot bypass authentication of the original object.
    """
    if not managed:
        return recorded == current
    if recorded_inputs is None:
        if isinstance(recorded, dict) and isinstance(current, dict):
            return project_claim_dependencies(recorded, managed=True, kind=kind) == current
        return recorded == current
    projected = project_claim_dependencies(recorded_inputs, managed=True, kind=kind)
    if recorded not in (digest(recorded_inputs), digest(projected)):
        return False
    claim = projected if recorded_claim is None else recorded_claim
    return (digest(claim) if isinstance(current, str) else claim) == current


def verify_record_dependency(records, kind, identifier, recorded_digest):
    """Keep generic configuration references from restoring global policy currency."""
    current = records.get(kind, {}).get(identifier)
    if current is None:
        return False
    if digest(current) == recorded_digest:
        return True
    if kind != "configuration" or identifier != "research" or not uses_claim_dependencies(records):
        return False
    read_record = getattr(records, "read_record", None)
    saved = None if read_record is None else read_record(kind, identifier, recorded_digest)
    if saved is None:
        raise ResearchError("claim_reassessment_required", "Reassess the record whose historical configuration is unavailable.",
                            {"record_id": kind + "/" + identifier})
    return accept_claim_dependencies(recorded_digest,
        project_claim_dependencies(current, managed=True, kind="configuration"), managed=True,
        recorded_inputs=saved, kind="configuration")
