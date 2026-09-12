"""Explicit current abstract selection and the independent cohort reading gate.

select_cohort_abstract accepts {collection_id, work_id: arXiv family,
unresolved_assertion_id, selected_assertion_id, reason}. It selects current
evidence for a versionless member observation, never identifies the old version.
The family, source, primary category and original submission window are checked;
known-version obligations cannot be replaced through this operation.

cohort_reading_report(store, collection_ids) is independent of roots and searches.
foundation_report uses cohort_report(records, artifacts, collection_ids) on its
single snapshot. Collection status uses current_selection to project the same
explicit selection while preserving historical unresolved provenance.
"""

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from .graph import obligation
from .identities import resolve_family
from .operations import fields, prepared_mutation, strings, text, timestamp
from .principles import preparation_policy
from .reading import current_readings
from .source_links import TEXT_KINDS, captured_source, contains, covers_text, exact_work, read_locator


def _selection_values(records, value):
    fields(value, ("collection_id", "work_id", "unresolved_assertion_id", "selected_assertion_id", "reason"), code="invalid_cohort_selection")
    text(value["reason"], "Current abstract selection reason", code="invalid_cohort_selection")
    collection = records.get("collection", {}).get(value["collection_id"])
    member = next((m for m in records.get("cohort_member", {}).values()
                   if m["collection_id"] == value["collection_id"] and m["work_id"] == value["work_id"]), None)
    original = records.get("work_assertion", {}).get(value["unresolved_assertion_id"])
    selected = records.get("work_assertion", {}).get(value["selected_assertion_id"])
    if collection is None or member is None or original is None or selected is None:
        raise ResearchError("invalid_cohort_selection", "Select actual assertions of an existing frozen cohort member")
    if (not value["work_id"].startswith("arxiv:") or original["work_id"] != value["work_id"]
            or original["id"] != value["work_id"] or original["version"] is not None
            or original["source_id"] not in member["source_ids"] or original["id"] not in member["version_ids"]
            or selected["work_id"] != value["work_id"] or selected["version"] is None
            or selected["abstract_status"] != "available" or selected["abstract_representation"] != "original"
            or selected["abstract"] is None):
        raise ResearchError("invalid_cohort_selection", "Only a versionless observation can select a complete original abstract of the same explicit arXiv family")
    work = exact_work(records, selected["id"])
    if resolve_family(records, value["work_id"]) != value["work_id"] or selected["assertion_id"] not in work["assertion_ids"]:
        raise ResearchError("invalid_cohort_selection", "Current evidence must resolve to one unambiguous canonical arXiv family")
    definition = collection["definition"]
    for assertion in (original, selected):
        source = records.get("source", {}).get(assertion["source_id"], {})
        if (source.get("provider") != "arxiv" or source.get("status") != "captured" or source.get("response_complete") is not True
                or assertion["category"] != definition["primaryCategory"]):
            raise ResearchError("invalid_cohort_selection", "Selection cannot repair malformed, partial or category-conflicting observations")
        submitted = timestamp(assertion["publication_date"]).date().isoformat()
        if not definition["windowStart"] <= submitted <= definition["windowEnd"]:
            raise ResearchError("invalid_cohort_selection", "Both observations must match the frozen initial-submission window")
    if original["publication_date"] != selected["publication_date"]:
        raise ResearchError("invalid_cohort_selection", "Conflicting original submission dates need resolution before selecting current evidence")
    return collection, original, selected


def select_cohort_abstract(store, payload, *, expected_revision, request_id):
    def prepare(records, value):
        collection, original, selected = _selection_values(records, value)
        artifacts = ArtifactStore(store.root)
        for assertion in (original, selected):
            captured_source(records, artifacts, assertion["source_id"])
            if assertion["abstract"] is not None:
                if not artifacts.read(assertion["abstract"]).decode("utf-8").strip():
                    raise ResearchError("invalid_cohort_selection", "A selected abstract must contain original text")
        key = digest([value["collection_id"], value["work_id"], value["unresolved_assertion_id"]])
        record = dict(value, version_id=selected["id"], artifact=selected["abstract"], source_id=selected["source_id"],
                      dependency_digest=digest([collection["definition"], original, selected]))
        return [("cohort_abstract_selection", key, record)], record

    return prepared_mutation(store, "cohort.select_abstract", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def current_selection(records, collection_id, member, version_id):
    choices = [s for s in records.get("cohort_abstract_selection", {}).values()
               if s["collection_id"] == collection_id and s["work_id"] == member["work_id"]
               and records.get("work_assertion", {}).get(s["unresolved_assertion_id"], {}).get("id") == version_id]
    if not choices:
        return None
    # Every selection is an explicit current-evidence decision. Conflicting
    # selections for different unresolved observations remain an obligation.
    if len({(s["version_id"], s["artifact"]["sha256"]) for s in choices}) != 1:
        raise ResearchError("cohort_selection_conflict", "Current cohort abstract selections disagree")
    choice = choices[0]
    payload = {k: choice[k] for k in ("collection_id", "work_id", "unresolved_assertion_id", "selected_assertion_id", "reason")}
    collection, original, selected = _selection_values(records, payload)
    if choice["dependency_digest"] != digest([collection["definition"], original, selected]):
        raise ResearchError("cohort_selection_stale", "Revalidate the current abstract selection after its evidence changes")
    return choice


def abstract_reading(records, artifacts, readings, item):
    """Match the selected abstract bytes or the identical included body abstract."""
    if item["artifact"] is None:
        return None
    evaluation = Evaluation.of(records, artifacts)
    abstract_text = " ".join(evaluation.text(item["artifact"]).split())
    for reading in readings:
        if reading["version_id"] != item["version_id"] or not reading["assessment"]["includes_abstract"]:
            continue
        if reading["depth"] == "abstract" and any(i["link"]["artifact"]["sha256"] == item["artifact"]["sha256"]
                and covers_text(evaluation, i["link"]) for i in reading["inspections"]):
            return reading
        if reading["depth"] == "fulltext":
            bundle = records["source_bundle"][reading["bundle_id"]]
            for unit in bundle["units"]:
                if (unit["kind"] == "abstract" and unit["link"] is not None and unit["link"]["locator"]["kind"] in TEXT_KINDS
                        and " ".join(read_locator(evaluation, unit["link"]["artifact"], unit["link"]["locator"]).split()) == abstract_text
                        and any(i["unit_id"] == unit["id"] and contains(i["link"], unit["link"], records) for i in reading["inspections"])):
                    return reading
    return None


def cohort_report(records, artifacts, collection_ids, *, target=None):
    evaluation = Evaluation.of(records, artifacts)
    strings(collection_ids, "Selected collections")
    return evaluation.once(("cohort", tuple(collection_ids), digest(target)),
                           lambda: _cohort_report(evaluation, collection_ids, target))


def _cohort_report(evaluation, collection_ids, target):
    from .acquisition import _collection_summary
    evaluation.counters["cohort_reports"] += 1
    records, artifacts = evaluation.records, evaluation
    summaries, obligations, inventory, dependencies, readings = [], [], [], {}, {}
    screened = preparation_policy(records) == "screened-v1"
    screening_counts = {}
    if not collection_ids:
        obligations.append(obligation("cohort_missing", "Select the complete frozen cohort collection."))
    for collection_id in collection_ids:
        collection = records.get("collection", {}).get(collection_id)
        if collection is None:
            obligations.append(obligation("cohort_missing", "Restore the selected frozen cohort collection.", collection_id=collection_id))
            continue
        summary = _collection_summary(records, collection)
        summaries.append(summary)
        for pending in summary["pending"]:
            obligations.append(obligation("collection_pending", "Resume or resolve acquisition without shrinking the frozen corpus.",
                collection_id=collection_id, reason=pending, next_eligible_at=summary["next_eligible_at"]))
        items, accepted_by_version = [], {}
        for item in summary["reading_obligations"]:
            paths = [item["artifact"]["path"]] if item["artifact"] is not None else []
            for source_id in item["source_ids"]:
                captured_source(records, artifacts, source_id)
                dependencies[source_id] = records["source"][source_id]
            version = item["version_id"]
            work = records.get("work", {}).get(version)
            dependencies[version] = work
            if work and not (version.startswith("arxiv:") and work["version"] is None):
                accepted, _ = current_readings(records, artifacts, version, target=target)
            else:
                accepted = []
            accepted_by_version[version] = accepted
            matched = abstract_reading(records, artifacts, accepted, item)
            if matched:
                readings[matched["id"]] = matched
            elif not screened:
                obligations.append(obligation("cohort_abstract_reading_missing", "Read the selected complete cohort abstract; downloaded content is not a reading.",
                    version_id=version, work_id=item["work_id"], collection_id=collection_id, paths=paths))
            items.append(dict(item, collection_id=collection_id, paths=paths, reading_id=matched["id"] if matched else None))
        if screened:
            from .screening import member_obligations
            found, screening_counts[collection_id] = member_obligations(records, collection, items, accepted_by_version)
            obligations.extend(found)
        inventory.extend(items)
        for historical in summary.get("historical_unresolved", []):
            for source_id in historical["source_ids"]:
                captured_source(records, artifacts, source_id)
                dependencies[source_id] = records["source"][source_id]
            if historical["artifact"]:
                artifacts.read(historical["artifact"])
    counts = {"cohort_families": len({x["work_id"] for x in inventory}), "abstract_obligations": len(inventory),
              "abstracts_read": sum(x["reading_id"] is not None for x in inventory), "obligations": len(obligations)}
    if screened:
        counts["screening"] = screening_counts
    unread = {o.get("version_id") for o in obligations}
    return {"ready": not obligations, "digest": digest([summaries, dependencies, readings, preparation_policy(records)]), "obligations": obligations,
            "counts": counts, "collections": summaries, "inventory": inventory, "readings": readings,
            "next": next((i for i in inventory if i["reading_id"] is None and i["paths"] and i["version_id"] in unread),
                         obligations[0] if obligations else None),
            "limits": "Mechanical source anchoring records inspection; it does not establish comprehension."}


def cohort_reading_report(store, collection_ids):
    records = store.snapshot()["records"]
    configured = records.get("configuration", {}).get("research", {})
    target = configured.get("target") if configured.get("profile") == "verification" else None
    return cohort_report(records, Evaluation(records, ArtifactStore(store.root)), collection_ids, target=target)
