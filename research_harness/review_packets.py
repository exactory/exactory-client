"""Neutral packets for independent reviewers.

A readiness reviewer needs the candidate, its branches, plans, assessments,
checkpoints, sources and the synthesis sections to judge validity, scope,
novelty, contribution, development and branches. A manuscript reviewer needs
the exact files, the claim-to-evidence map, the evidence those claims cite,
the observed executions behind result evidence, and the field standards.
Neither needs revision labels, request identities, launcher tokens, author
names, assessment history, strategy accounts or the author's own plans, so
those never enter a packet. A round assessor judges the research program, not
the paper's quality, so the round packet deliberately carries history: the
manuscript packet, every blind review and prediction of the bundle, the
decision under review, every round's goal and assessment, the closing round's
development blocks, the synthesis sections, the consequence searches and the
resource accounts; author names, request identities, launcher tokens and
revision labels still never enter it. Delivery copies exactly the artifacts a
packet references.
"""

from .evidence import digest
from .predictions import measurement_summary
from .publication import latest_reviews
from .resources import account_report
from .rounds import admissions, assessment_for, latest_admission


_FORBIDDEN_KEYS = ("request_id", "token")
_FORBIDDEN_SUFFIX = "_revision"


def scrub(value, keys=_FORBIDDEN_KEYS):
    """Remove request identities, launcher tokens, revision labels and the named keys at every depth."""
    if isinstance(value, dict):
        return {key: scrub(item, keys) for key, item in value.items()
                if key not in keys and not key.endswith(_FORBIDDEN_SUFFIX)}
    if isinstance(value, list):
        return [scrub(item, keys) for item in value]
    return value


def readiness_packet(report):
    """The six readiness checks' evidence, without labels, history or author names at any depth."""
    inputs = dict(report["review_inputs"])
    inputs["synthesis"] = {key: value for key, value in inputs["synthesis"].items() if key != "history"}
    return scrub({"kind": "readiness", "inputs": inputs, "execution_observations": report["execution_observations"]},
                 _FORBIDDEN_KEYS + ("authors",))


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


def round_packet(records, bundle, decision):
    """The paper, its reviews and predictions, the decision under review, and the program's history."""
    reviews = [{"kind": saved["assessor"]["kind"], "core": saved["core"]} for saved in latest_reviews(records, bundle["digest"]).values()]
    predictions = [saved["prediction"] for saved in records.get("manuscript_prediction", {}).values()
                   if saved["bundle_digest"] == bundle["digest"]]
    history = []
    for admission in admissions(records):
        opened_by = records["round_decision"][admission["decision_id"]]
        assessment = assessment_for(records, admission["id"])
        history.append({"number": admission["number"], "goal": admission["goal"], "objective": admission["objective"],
                        "resource_limits": admission["resource_limits"],
                        "decision": {k: opened_by[k] for k in ("id", "payload", "digest")},
                        "assessment": None if assessment is None else {k: assessment[k] for k in ("payload", "derived", "successful", "unproductive")}})
    # The closing round's cycle assessments are those after the latest admission (every assessment before any
    # round was admitted): the selection the decision's carried developments are checked against.
    latest = latest_admission(records)
    admitted_revision = latest["admitted_revision"] if latest is not None else 0
    development = {identifier: assessment["payload"]["development"] for identifier, assessment in records.get("cycle_assessment", {}).items()
                   if assessment["assessed_revision"] > admitted_revision}
    selection = records.get("synthesis_selection", {})
    synthesis = {kind: records["synthesis"][selection["research:" + kind]["id"]]["payload"]
                 for kind in ("context", "innovation") if "research:" + kind in selection}
    searches = {}
    for purpose in ("downstream", "next_step"):
        selected = records.get("search_selection", {}).get("research:" + purpose)
        if selected is not None:
            search = records["literature_search"][selected["search_id"]]
            searches[purpose] = {k: search[k] for k in ("purpose", "found_work_ids", "dispositions", "impact", "gaps")}
    manifest = {"kind": "round", "manuscript": manuscript_packet(records, bundle), "reviews": reviews, "predictions": predictions,
                "measurement": measurement_summary(records, bundle),
                "decision": {"payload": decision["payload"], "digest": decision["digest"], "closes": decision["closes"]},
                "rounds": history, "development": development, "synthesis": synthesis, "searches": searches,
                "resources": account_report(records, "research"),
                "digest": digest({"bundle": bundle["digest"], "decision": decision["digest"]})}
    return scrub(manifest, _FORBIDDEN_KEYS + ("authors", "author"))
