"""Screened preparation: screenings, audit sample, doctrine coverage, saturation, policy report.

A screening is a recorded judgment about one population member or one Tier 3
family: promote, doctrine, exclude or pending, with a relevance and a reason. A
screen is never a reading. Under screened-v1 the cohort gate requires every
member to be screened, every promoted, doctrine and pending member to be read,
a deterministic sample of excluded members to be read with an audit judgment,
and every month of the window to have its doctrine representatives read. A
strong audit judgment fails the screen: the paper cannot stay excluded, and
every excluded member must be re-screened in a round above the one the audit
was made against. Rounds only rise. A saturation checkpoint after two batches
of pending members whose every item was explicitly judged not consequential
lets the remaining pending members stay inventoried and unread; a later
consequential batch, screening round or policy change removes that effect.
"""

from datetime import date

from .errors import ResearchError
from .evidence import digest
from .graph import obligation
from .operations import fields, prepared_mutation, strings, text
from .principles import preparation_policy
from .reading import RELEVANCE, _usage
from .source_links import exact_work
from . import resources


DISPOSITIONS = ("promote", "doctrine", "exclude", "pending")
PROMOTION_REASONS = ("prior_art", "contradiction", "assumption_or_method", "correction", "standards",
                     "identity_conflict", "unclassifiable")
AUDIT_SAMPLE = 150
DOCTRINE_PER_MONTH = 8
BATCH_LIMIT = 200
SCREENED = "screened-v1"
GRAPH = "graph"


def screening_key(collection_id, work_id):
    return digest([collection_id or GRAPH, work_id])


def _require_screened(records):
    if preparation_policy(records) != SCREENED:
        raise ResearchError("policy_inapplicable", "Screening records belong to the screened-v1 preparation policy",
                            {"policy": preparation_policy(records)})


def _item(records, value):
    fields(value, ("collection_id", "work_id", "version_id", "disposition", "relevance", "reason", "conventions"),
           ("promotion_reasons", "context"), code="invalid_screening")
    text(value["work_id"], "Family", code="invalid_screening")
    text(value["reason"], "Screening reason", code="invalid_screening")
    strings(value["conventions"], "Observed conventions", code="invalid_screening")
    work = exact_work(records, value["version_id"])
    if work["work_id"] != value["work_id"]:
        raise ResearchError("invalid_screening", "The exact version must belong to the screened family")
    if value["collection_id"] is not None:
        text(value["collection_id"], "Collection", code="invalid_screening")
        if not any(m["collection_id"] == value["collection_id"] and m["work_id"] == value["work_id"]
                   for m in records.get("cohort_member", {}).values()):
            raise ResearchError("invalid_screening", "Screen an actual member of the frozen collection")
    else:
        text(value.get("context"), "Citation context", code="invalid_screening")
    if value["disposition"] not in DISPOSITIONS or value["relevance"] not in RELEVANCE:
        raise ResearchError("invalid_screening", "Use a supported disposition and relevance")
    if value["relevance"] == "strong" and value["disposition"] != "promote":
        raise ResearchError("invalid_screening", "A strongly relevant paper is promoted")
    if value["disposition"] == "exclude":
        if value["relevance"] != "none":
            raise ResearchError("invalid_screening", "Only a paper with no relevance can be excluded; weak relevance stays pending")
        if not any(a["completeness"] == "complete" for a in work["abstracts"]):
            raise ResearchError("invalid_screening", "A paper without a complete abstract cannot be excluded")
    reasons = value.get("promotion_reasons", [])
    if value["disposition"] == "promote":
        strings(reasons, "Promotion reasons", nonempty=True, code="invalid_screening")
        if not set(reasons) <= set(PROMOTION_REASONS):
            raise ResearchError("invalid_screening", "Promotion reasons are: " + ", ".join(PROMOTION_REASONS))
    elif reasons:
        raise ResearchError("invalid_screening", "Promotion reasons belong to promoted papers")
    return value


def record_screening_batch(store, payload, *, expected_revision, request_id):
    """Record up to BATCH_LIMIT screenings in one event; a later round replaces a member's screening."""
    def prepare(records, value):
        fields(value, ("id", "items", "screener"), ("round", "usage"), code="invalid_screening")
        text(value["id"], "Batch ID", code="invalid_screening")
        _require_screened(records)
        fields(value["screener"], ("kind", "model"), code="invalid_screening")
        if value["screener"]["kind"] not in ("agent", "human"):
            raise ResearchError("invalid_screening", "The screener is an agent or a human")
        if value["screener"]["model"] is not None:
            text(value["screener"]["model"], "Screener model", code="invalid_screening")
        round_number = value.get("round", 1)
        if type(round_number) is not int or round_number < 1:
            raise ResearchError("invalid_screening", "A screening round is a positive integer")
        usage = _usage(value.get("usage"))
        items = value["items"]
        if not isinstance(items, list) or not 1 <= len(items) <= BATCH_LIMIT:
            raise ResearchError("invalid_screening", "A screening batch holds 1 to " + str(BATCH_LIMIT) + " items")
        changes, results, seen, failures = [], [], set(), []
        for index, item in enumerate(items):
            try:
                _item(records, item)
                key = screening_key(item["collection_id"], item["work_id"])
                if key in seen:
                    raise ResearchError("invalid_screening", "Each member appears once per batch")
                seen.add(key)
                existing = records.get("screening", {}).get(key)
                if existing is not None and existing["round"] >= round_number:
                    raise ResearchError("invalid_screening", "A re-screen needs a round above the member's current round",
                                        {"current_round": existing["round"]})
                record = dict(item, policy={"id": SCREENED}, screener=value["screener"], round=round_number, batch_id=value["id"])
                changes.append(("screening", key, record))
                results.append({"key": key, "work_id": item["work_id"], "disposition": item["disposition"]})
            except ResearchError as error:
                failures.append({"index": index, "code": error.code, "message": error.message})
        if failures:
            raise ResearchError("invalid_screening", "Correct the failing items and resubmit the whole batch", {"items": failures})
        charge = resources.charge(records, "screening", {"screenings": len(results), "model_input_tokens": usage["input_tokens"],
                                                         "model_output_tokens": usage["output_tokens"], "wall_seconds": usage["wall_seconds"]})
        if charge is not None:
            changes.append(charge)
        return changes, {"id": value["id"], "count": len(results), "items": results, "round": round_number, "usage": usage}

    return prepared_mutation(store, "screening.batch", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def screenings(records, collection_id):
    """Current screening per family for one collection, or for the citation graph when None."""
    return {s["work_id"]: s for s in records.get("screening", {}).values()
            if s["collection_id"] == collection_id}


def audit_sample(seed, work_ids, size=AUDIT_SAMPLE):
    """A deterministic sample of excluded families: the same seed and population give the same sample."""
    return sorted(sorted(set(work_ids)), key=lambda work_id: digest([seed, work_id]))[:size]


def current_screening(records, work_id):
    """The family's screening as a cohort member, else as a citation graph reference, else None."""
    collection_id = next((m["collection_id"] for m in records.get("cohort_member", {}).values() if m["work_id"] == work_id), None)
    return screenings(records, collection_id).get(work_id)


def lowest_excluded_round(screened):
    """The lowest round among excluded screenings, or None when nothing is excluded."""
    return min((s["round"] for s in screened.values() if s["disposition"] == "exclude"), default=None)


def _audit_obligations(readings, screening, lowest_round, version_id, work_id, collection_id):
    """Audit obligations of one member from every accepted reading of its version.

An excluded member in the sample owes a reading with an audit. A strong audit
keeps screening_audit_failed until the member is no longer excluded and every
excluded member's round is above the audited round.
"""
    audits = [reading["batch"]["audit"] for reading in readings if (reading.get("batch") or {}).get("audit")]
    found = []
    if screening["disposition"] == "exclude" and not audits:
        found.append(obligation("screening_audit_reading_missing", "Read this sampled excluded paper with an audit judgment.",
                                version_id=version_id, work_id=work_id, collection_id=collection_id))
    for audit in audits:
        if audit["relevance"] != "strong":
            continue
        if screening["disposition"] == "exclude":
            found.append(obligation("screening_audit_failed", "A sampled excluded paper is strongly relevant; promote it and re-screen every excluded member in a new round.",
                                    version_id=version_id, work_id=work_id, collection_id=collection_id, audit_round=audit["round"]))
        elif lowest_round is not None and lowest_round <= audit["round"]:
            found.append(obligation("screening_audit_failed", "A strong audit reopened the screen; re-screen every excluded member in a round above the audited one.",
                                    version_id=version_id, work_id=work_id, collection_id=collection_id, audit_round=audit["round"]))
    return found


def _months(definition):
    start, end = date.fromisoformat(definition["windowStart"]), date.fromisoformat(definition["windowEnd"])
    months, year, month = [], start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append("%04d-%02d" % (year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def member_obligations(records, collection, inventory, accepted):
    """Obligations of one collection's members under screened-v1; inventory items carry reading_id and
accepted maps a version to every accepted reading of it, so an audit counts whichever reading carries it."""
    screened = screenings(records, collection["id"])
    covered = saturation_covers(records)
    excluded = [i["work_id"] for i in inventory if screened.get(i["work_id"], {}).get("disposition") == "exclude"]
    sampled = set(audit_sample(collection["id"], excluded))
    lowest_round = lowest_excluded_round({w: screened[w] for w in excluded})
    obligations, read_doctrine = [], {}
    counts = {"screened": 0, "promote": 0, "doctrine": 0, "pending": 0, "exclude": 0,
              "audit_sample": len(sampled), "inventoried_unread": 0}
    for item in inventory:
        version, work_id, read = item["version_id"], item["work_id"], item["reading_id"] is not None
        screening = screened.get(work_id)
        if screening is None:
            obligations.append(obligation("screening_missing", "Screen this population member before the cohort gate.",
                                          version_id=version, work_id=work_id, collection_id=collection["id"], paths=item["paths"]))
            continue
        counts["screened"] += 1
        disposition = screening["disposition"]
        counts[disposition] += 1
        missing = obligation("cohort_abstract_reading_missing", "Read the selected complete cohort abstract; downloaded content is not a reading.",
                             version_id=version, work_id=work_id, collection_id=collection["id"], paths=item["paths"])
        readings = accepted.get(version, [])
        if disposition in ("promote", "doctrine"):
            if not read:
                obligations.append(missing)
            else:
                work = records.get("work", {}).get(version) or {}
                read_doctrine.setdefault((work.get("publication_date") or "")[:7], set()).add(work_id)
        elif disposition == "pending":
            if read:
                continue
            if covered:
                counts["inventoried_unread"] += 1
            else:
                obligations.append(missing)
        if disposition != "exclude" or work_id in sampled:
            obligations.extend(_audit_obligations(readings, screening, lowest_round, version, work_id, collection["id"]))
    population = {}
    for item in inventory:
        work = records.get("work", {}).get(item["version_id"]) or {}
        population.setdefault((work.get("publication_date") or "")[:7], set()).add(item["work_id"])
    for month in _months(collection["definition"]):
        required = min(DOCTRINE_PER_MONTH, len(population.get(month, ())))
        count = len(read_doctrine.get(month, ()))
        if count < required:
            obligations.append(obligation("doctrine_coverage_missing", "Read enough doctrine or promoted representatives of this month.",
                                          collection_id=collection["id"], month=month, count=count, required=required))
    return obligations, counts


def reference_sample(records, profile):
    """The audited sample over excluded Tier 3 families of the citation graph."""
    excluded = [w for w, s in screenings(records, None).items() if s["disposition"] == "exclude"]
    return set(audit_sample(GRAPH + ":" + profile, excluded))


def reference_obligations(records, profile, version, work, reading, accepted, qualified, sample, lowest_round, paths):
    """Obligations of one Tier 3 version at abstract depth, under either policy; sample and lowest_round
come from reference_sample and lowest_excluded_round over the graph screenings, computed once per report."""
    missing = obligation("abstract_reading_missing", "Read the complete saved abstract for this exact version.", version_id=version, paths=paths)
    if preparation_policy(records) != SCREENED:
        return [missing] if reading is None and not qualified else []
    screening = screenings(records, None).get(work["work_id"])
    if screening is None:
        return [obligation("screening_missing", "Screen this reference family with its citation context.",
                           version_id=version, work_id=work["work_id"], collection_id=None, paths=paths)]
    disposition = screening["disposition"]
    found = []
    if disposition in ("promote", "doctrine") and reading is None and not qualified:
        found.append(missing)
    if disposition == "pending" and reading is None and not qualified and not saturation_covers(records):
        found.append(missing)
    if disposition != "exclude" or work["work_id"] in sample:
        found.extend(_audit_obligations(accepted, screening, lowest_round, version, work["work_id"], None))
    return found


def record_screening_checkpoint(store, payload, *, expected_revision, request_id):
    """Record saturation after the two most recent batches of pending members found nothing consequential."""
    def prepare(records, value):
        fields(value, ("id", "batch_ids", "reason"), code="invalid_screening")
        text(value["id"], "Checkpoint ID", code="invalid_screening")
        text(value["reason"], "Saturation reason", code="invalid_screening")
        _require_screened(records)
        strings(value["batch_ids"], "Batch IDs", nonempty=True, code="invalid_screening")
        batches = sorted(records.get("reading_batch", {}).values(), key=lambda b: b["revision"])
        recent = [b["id"] for b in batches[-2:]]
        if len(value["batch_ids"]) != 2 or sorted(value["batch_ids"]) != sorted(recent):
            raise ResearchError("invalid_screening", "Name the two most recent abstract batches", {"recent": recent})
        for batch in batches[-2:]:
            if batch["consequential"] or not batch.get("judged") or any(m["disposition"] != "pending" for m in batch["members"]):
                raise ResearchError("invalid_screening", "Saturation needs two batches of pending members whose every item was judged not consequential",
                                    {"batch_id": batch["id"]})
        rounds = [s["round"] for s in records.get("screening", {}).values()]
        record = {"id": value["id"], "batch_ids": value["batch_ids"], "reason": value["reason"], "at_revision": expected_revision + 1,
                  "policy": preparation_policy(records), "max_round": max(rounds) if rounds else 0}
        return [("screening_checkpoint", value["id"], record), ("screening_checkpoint_selection", "current", {"id": value["id"]})], record

    return prepared_mutation(store, "screening.checkpoint", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def saturation_covers(records):
    """Whether the selected saturation checkpoint still covers unread pending members."""
    selection = records.get("screening_checkpoint_selection", {}).get("current")
    checkpoint = records.get("screening_checkpoint", {}).get(selection["id"]) if selection else None
    if checkpoint is None or checkpoint["policy"] != preparation_policy(records):
        return False
    if any(b["revision"] > checkpoint["at_revision"] and b["consequential"] for b in records.get("reading_batch", {}).values()):
        return False
    return not any(s["round"] > checkpoint["max_round"] for s in records.get("screening", {}).values())


def policy_report(store, *, policy=None, reference=None):
    """Read-only account of the preparation set under the recorded or a hypothetical policy."""
    from .artifacts import ArtifactStore
    from .evaluation import Evaluation
    from .gates import gate_state
    snapshot = store.snapshot()
    records = snapshot["records"]
    config = records.get("configuration", {}).get("research")
    if config is None:
        raise ResearchError("migration_required", "Initialize or adopt the research contract before a policy report")
    policy = policy or preparation_policy(records)
    if policy not in ("exhaustive-v1", SCREENED):
        raise ResearchError("invalid_input", "Unknown preparation policy")
    evaluation = Evaluation(records, ArtifactStore(store.root))
    cohort = gate_state(records, evaluation, "cohort", profile=config["profile"])
    collections = {}
    selected = set()
    for summary in cohort.get("collections", []):
        items = [i for i in cohort["inventory"] if i["collection_id"] == summary["collection_id"]]
        screened = screenings(records, summary["collection_id"])
        dispositions = {d: 0 for d in DISPOSITIONS}
        unscreened, chosen = 0, set()
        for item in items:
            screening = screened.get(item["work_id"])
            if screening is None:
                unscreened += 1
                if policy == "exhaustive-v1":
                    chosen.add(item["work_id"])
            else:
                dispositions[screening["disposition"]] += 1
                if policy == "exhaustive-v1" or screening["disposition"] != "exclude":
                    chosen.add(item["work_id"])
        selected |= chosen
        excluded = [w for w, s in screened.items() if s["disposition"] == "exclude"]
        collections[summary["collection_id"]] = {
            "families": summary["member_count"], "versions": len(items), "read": sum(i["reading_id"] is not None for i in items),
            "unscreened": unscreened, "dispositions": dispositions,
            "audit_sample": audit_sample(summary["collection_id"], excluded) if policy == SCREENED else [],
            "selected": len(chosen)}
    recall = None
    if reference is not None:
        fields(reference, ("prior_art", "contradictions", "methods", "doctrine"), code="invalid_input")
        recall = {}
        for category, families in reference.items():
            strings(families, category)
            missed = sorted(set(families) - selected)
            recall[category] = {"total": len(families), "found": len(families) - len(missed),
                                "recall": None if not families else round((len(families) - len(missed)) / len(families), 4), "missed": missed}
    return {"revision": snapshot["revision"], "policy": policy, "recorded_policy": preparation_policy(records),
            "collections": collections, "saturation": saturation_covers(records), "recall": recall, "mechanical_only": True}
