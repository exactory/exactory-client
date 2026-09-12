"""Neutral packets for independent reviewers.

A readiness reviewer needs the candidate, its branches, plans, assessments,
checkpoints, sources and the synthesis sections to judge validity, scope,
novelty, contribution, development and branches. A manuscript reviewer needs
the exact files, the claim-to-evidence map, the evidence those claims cite,
the observed executions behind result evidence, and the field standards.
Neither needs revision labels, request identities, launcher tokens, author
names, assessment history, strategy accounts or the author's own plans, so
those never enter a packet. Delivery copies exactly the artifacts a packet
references.
"""

from .evidence import digest


_FORBIDDEN_KEYS = ("request_id", "token")
_FORBIDDEN_SUFFIX = "_revision"


def scrub(value):
    """Remove request identities, launcher tokens and revision labels at every depth."""
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items()
                if key not in _FORBIDDEN_KEYS and not key.endswith(_FORBIDDEN_SUFFIX)}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def readiness_packet(report):
    """The six readiness checks' evidence, without labels, history or author names."""
    inputs = dict(report["review_inputs"])
    inputs["candidate"] = {key: value for key, value in inputs["candidate"].items() if key != "authors"}
    inputs["synthesis"] = {key: value for key, value in inputs["synthesis"].items() if key != "history"}
    return scrub({"kind": "readiness", "inputs": inputs, "execution_observations": report["execution_observations"]})


def _source_closure(records, version_ids):
    works, readings, bundles, source_ids = {}, {}, {}, set()
    for version in sorted(version_ids):
        work = records.get("work", {}).get(version)
        if work is None:
            continue
        works[version] = work
        source_ids.update(work.get("source_ids", []))
        for reading in records.get("reading", {}).values():
            if reading["version_id"] == version:
                readings[reading["id"]] = reading
                source_ids.update(i["link"]["source_id"] for i in reading["inspections"])
        for bundle in records.get("source_bundle", {}).values():
            if bundle["version_id"] == version:
                bundles[bundle["id"]] = bundle
                source_ids.update(u["link"]["source_id"] for u in bundle["units"] if u["link"] is not None)
    sources = {identifier: records["source"][identifier] for identifier in sorted(source_ids) if identifier in records.get("source", {})}
    return {"work": works, "reading": readings, "source_bundle": bundles, "source": sources}


def manuscript_packet(records, bundle):
    """The exact manuscript, its claim evidence closure, observed executions and field standards."""
    versions, executions = set(), set()
    for claim in bundle["claim_evidence"]:
        for item in claim["evidence"]:
            if item.get("kind") == "source":
                versions.add(item["link"]["version_id"])
            elif item.get("kind") == "result":
                executions.add(item["execution_id"])
    results = {}
    for identifier in sorted(executions):
        execution = records.get("execution", {}).get(identifier)
        results[identifier] = {"execution": execution["payload"] if execution else None,
                               "observation": bundle["execution_observations"].get(identifier)}
    standards = bundle["review_inputs"]["synthesis"]["sections"].get("standards", {}).get("payload")
    return scrub({"kind": "manuscript", "bundle_digest": bundle["digest"], "files": bundle["files"],
                  "claim_evidence": bundle["claim_evidence"], "evidence": _source_closure(records, versions),
                  "results": results, "standards": standards,
                  "digest": digest({"bundle": bundle["digest"], "claims": bundle["claim_evidence"]})})
