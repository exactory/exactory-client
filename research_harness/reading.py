"""Source-specific reading evidence and explicit depth/availability obligations.

record_reading accepts {id, version_id, depth, bundle_id?, inspections, notes}.
Depth is abstract, fulltext or passage. An inspection is {unit_id: str|null,
link: Link, note: str}. Each of problem/claims/assumptions/methods/evidence/
limitations/relevance is {text, status: present|absent|not_applicable,
inspections: [zero-based indices]}. Absence notes need source anchors, not invented
methods or word-count padding. Partial inspections remain recorded as partial.

record_reading_batch accepts {id, depth: abstract, items, usage?}. Each item is
{version_id, note, notes: {seven fields: {text, status}}, screening?, audit?,
consequential?}. The harness derives the inspection as the whole complete
abstract artifact with a span locator, then applies the single-reading rules to
every item. One failing item rejects the whole batch with its index; one event
records every reading. Reading ids are reading:<sha256 of [batch id, version]>.

require_fulltext accepts {id, profile, version_id, purpose, reason,
historical_cutoff?}; purposes are major_claim, novelty, innovation, validity.
These are critical exact-version dependencies, independent of citation tier.

record_availability accepts {id, profile, version_id, depth, source_ids, reason,
policy: {id, minimum_attempts, allowed_statuses, rationale}}. Only explicit
terminal HTTP 403/404/410/451 attempts can qualify under this policy. Qualification
is not reading and cannot satisfy critical dependencies or bibliography coverage.
"""

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from .graph import main_captures, obligation, selected_bundle
from .operations import fields, immutable_record, iso_date, prepared_mutation, profile_name, strings, text
from . import resources
from .source_links import TEXT_KINDS, complete_original, contains, covers_text, exact_work, link_identity, original_identity, span_locator, validate_link
from .visual_assets import asset_dependencies


NOTE_FIELDS = ("problem", "claims", "assumptions", "methods", "evidence", "limitations", "relevance")
FULLTEXT_PURPOSES = ("major_claim", "novelty", "innovation", "validity")
BATCH_LIMIT = 100
RELEVANCE = ("none", "weak", "strong")


def unit_digest(bundle, records):
    """Reading dependency: original bytes and the exact required-unit inventory.

Capture receipt IDs and bundle labels do not require rereading identical bytes.
Adding or changing a unit, its link identity, abstract inclusion or a visual
dependency changes this digest. Parsed bibliography entries do not: they are
reference evidence with their own obligations, not inspected reading units.
"""
    return digest({"version_id": bundle["version_id"], "original_sha256": bundle.get("original_sha256"),
                   "scope": bundle["scope"], "completeness": bundle["completeness"],
                   "includes_abstract": bundle.get("includes_abstract"),
                   "visual_dependencies": [{"unit_id": u["unit_id"], "resources": asset_dependencies(
                       records, bundle["version_id"], u["original_sha256"], u["resources"])} for u in bundle.get("visual_resources", [])],
                   "units": [{"id": u["id"], "kind": u["kind"], "required": u["required"],
                              "link": link_identity(u["link"], records) if u["link"] else None,
                              "reason": u.get("reason"), "url": u.get("url")} for u in bundle["units"]]})


def bibliography_digest(bundle, records):
    """Reference evidence identity: completeness, its unit, and every located entry."""
    return digest({"complete": bundle["bibliography"]["complete"], "unit_id": bundle["bibliography"]["unit_id"],
                   "entries": [{"target": e["target"], "kind": e["kind"], "link": link_identity(e["link"], records)}
                               for e in bundle["bibliography"]["entries"]]})


bundle_digest = unit_digest


def required_unit_obligations(records, artifacts, bundle):
    """Shared completeness boundary, actionable before any reading is written."""
    evaluation = Evaluation.of(records, artifacts)
    pending = []
    for unit in bundle["units"]:
        if not unit["required"]:
            continue
        affected = {"version_id": bundle["version_id"], "unit_id": unit["id"]}
        if unit["link"] is None:
            pending.append(obligation("required_unit_missing", "Acquire the required source unit and import the extended bundle.",
                                      url=unit.get("url"), **affected))
            continue
        context = evaluation.link(unit["link"])
        if context["visual"] is not None:
            pending.extend(obligation(p["code"], "Acquire, link and inspect the complete static visual bytes.",
                **dict(affected, **{k: v for k, v in p.items() if k != "code"})) for p in context["visual"]["pending"])
        if not complete_original(context) or (unit["kind"] == "supplement" and not covers_text(evaluation, unit["link"])):
            pending.append(obligation("required_unit_incomplete", "Acquire the complete original required unit; a scoped passage remains partial.",
                                      paths=[unit["link"]["artifact"]["path"]], **affected))
    return pending


def _assess(records, artifacts, value):
    evaluation = Evaluation.of(records, artifacts)
    evaluation.counters["readings_assessed"] += 1
    fields(value, ("id", "version_id", "depth", "inspections", "notes"), ("bundle_id", "assessment", "batch"), code="invalid_reading")
    text(value["id"], "Reading ID", code="invalid_reading")
    exact_work(records, value["version_id"])
    if value["depth"] not in ("abstract", "fulltext", "passage"):
        raise ResearchError("invalid_reading", "Reading depth must be abstract, fulltext or passage")
    inspections = value["inspections"]
    if not isinstance(inspections, list) or not inspections:
        raise ResearchError("invalid_reading", "A reading needs actual source inspections")
    contexts = []
    for inspection in inspections:
        fields(inspection, ("unit_id", "link", "note"), code="invalid_reading")
        if inspection["unit_id"] is not None:
            text(inspection["unit_id"], "Inspection unit ID", code="invalid_reading")
        text(inspection["note"], "Inspection note", code="invalid_reading")
        context = evaluation.link(inspection["link"])
        if context["work"]["id"] != value["version_id"]:
            raise ResearchError("source_mismatch", "Each inspection must bind the reading's exact version")
        contexts.append(context)
    fields(value["notes"], NOTE_FIELDS, code="invalid_reading")
    for name, note in value["notes"].items():
        fields(note, ("text", "status", "inspections"), code="invalid_reading")
        text(note["text"], name, code="invalid_reading")
        if (note["status"] not in ("present", "absent", "not_applicable") or not isinstance(note["inspections"], list)
                or not note["inspections"] or any(type(i) is not int or not 0 <= i < len(inspections) for i in note["inspections"])):
            raise ResearchError("invalid_reading", "Every source-specific note must cite actual inspection indices")
    pending, included_abstract, dependency = [], False, None
    if value["depth"] == "fulltext":
        text(value.get("bundle_id"), "Bundle ID", code="invalid_reading")
        bundle = records.get("source_bundle", {}).get(value.get("bundle_id"))
        if bundle is None or bundle["version_id"] != value["version_id"]:
            raise ResearchError("invalid_reading", "Full reading requires an imported exact-version source bundle")
        units = {u["id"]: u for u in bundle["units"]}
        for inspection in inspections:
            unit = units.get(inspection["unit_id"])
            if unit is None or unit["link"] is None or not contains(unit["link"], inspection["link"], records):
                raise ResearchError("outside_reading_unit", "An inspection must stay within its declared article unit")
        if bundle["scope"] != "article" or bundle["completeness"] != "complete":
            pending.append(obligation("source_bundle_incomplete", "Complete the article's source bundle before claiming full depth.", version_id=value["version_id"]))
        pending.extend(required_unit_obligations(records, evaluation, bundle))
        for unit in units.values():
            if unit["required"] and unit["link"] is not None and not any(
                    i["unit_id"] == unit["id"] and contains(i["link"], unit["link"], records) for i in inspections):
                pending.append(obligation("required_unit_uninspected", "Inspect the complete required text or visual unit.",
                                          version_id=value["version_id"], unit_id=unit["id"], paths=[unit["link"]["artifact"]["path"]]))
        included_abstract = bundle.get("includes_abstract") is not False and any(
            u["kind"] == "abstract" and u["link"] is not None and u["link"]["locator"]["kind"] in TEXT_KINDS
            and original_identity(records, u["link"]) == bundle["original_sha256"]
            and complete_original(evaluation.link(u["link"])) and any(
                i["unit_id"] == u["id"] and contains(i["link"], u["link"], records) for i in inspections) for u in units.values())
        dependency = unit_digest(bundle, records)
    elif value["depth"] == "abstract":
        if value.get("bundle_id") is not None:
            raise ResearchError("invalid_reading", "Abstract reading links the original abstract directly")
        complete = [c["abstract"] is not None and c["abstract"]["completeness"] == "complete"
                    and covers_text(evaluation, i["link"]) for c, i in zip(contexts, inspections)]
        if not any(complete):
            pending.append(obligation("abstract_reading_incomplete", "Read the complete acquired original abstract; excerpts remain partial.", version_id=value["version_id"]))
        included_abstract = any(complete)
    return {"status": "partial" if pending else "complete", "pending": pending,
            "includes_abstract": included_abstract, "bundle_digest": dependency,
            "bibliography_digest": bibliography_digest(bundle, records) if value["depth"] == "fulltext" else None}


def record_reading(store, payload, *, expected_revision, request_id):
    def prepare(records, value):
        fields(value, ("id", "version_id", "depth", "inspections", "notes"), ("bundle_id",), code="invalid_reading")
        assessment = _assess(records, ArtifactStore(store.root), value)
        value = dict(value, assessment=assessment)
        return [immutable_record(records, "reading", value["id"], value)], dict(id=value["id"], **assessment)

    return prepared_mutation(store, "literature.reading", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def _usage(value):
    if value is None:
        return {"model": None, "input_tokens": None, "output_tokens": None, "wall_seconds": None}
    fields(value, ("model", "input_tokens", "output_tokens", "wall_seconds"), code="invalid_batch")
    if value["model"] is not None:
        text(value["model"], "Usage model", code="invalid_batch")
    for key in ("input_tokens", "output_tokens"):
        if value[key] is not None and (type(value[key]) is not int or value[key] < 0):
            raise ResearchError("invalid_batch", "Token usage is a nonnegative integer or null when unknown")
    seconds = value["wall_seconds"]
    if seconds is not None and (type(seconds) not in (int, float) or seconds < 0):
        raise ResearchError("invalid_batch", "Wall seconds are a nonnegative number or null when unknown")
    return dict(value)


def _batch_extras(item):
    extras = {}
    if "screening" in item:
        screening = item["screening"]
        fields(screening, ("relevance", "reason", "conventions"), code="invalid_batch")
        if screening["relevance"] not in RELEVANCE:
            raise ResearchError("invalid_batch", "Screening relevance is none, weak or strong")
        text(screening["reason"], "Screening reason", code="invalid_batch")
        strings(screening["conventions"], "Observed conventions", code="invalid_batch")
        extras["screening"] = screening
    if "audit" in item:
        audit = item["audit"]
        fields(audit, ("relevance", "reason"), code="invalid_batch")
        if audit["relevance"] not in RELEVANCE:
            raise ResearchError("invalid_batch", "Audit relevance is none, weak or strong")
        text(audit["reason"], "Audit reason", code="invalid_batch")
        extras["audit"] = audit
    if "consequential" in item:
        if type(item["consequential"]) is not bool:
            raise ResearchError("invalid_batch", "consequential is a boolean")
        extras["consequential"] = item["consequential"]
    return extras


def selected_abstract(work):
    """The complete saved abstract a cohort item or batch reads: the version's selected one, else the first complete one."""
    complete = [a for a in work["abstracts"] if a["completeness"] == "complete"]
    return next((a for a in complete if a["artifact"] == work.get("abstract")), complete[0] if complete else None)


def _audit_round(records, work):
    """An audit judges a current exclusion; the reading stores the round it was made against."""
    from .screening import current_screening
    screening = current_screening(records, work["work_id"])
    if screening is None or screening["disposition"] != "exclude":
        raise ResearchError("invalid_batch", "An audit judgment belongs to the reading of a currently excluded member")
    return screening["round"]


def _batch_item(records, evaluation, batch_id, item):
    fields(item, ("version_id", "note", "notes"), ("screening", "audit", "consequential"), code="invalid_batch")
    work = exact_work(records, text(item["version_id"], "Version", code="invalid_batch"))
    text(item["note"], "Inspection note", code="invalid_batch")
    fields(item["notes"], NOTE_FIELDS, code="invalid_batch")
    for name, note in item["notes"].items():
        fields(note, ("text", "status"), code="invalid_batch")
    extras = _batch_extras(item)
    if "audit" in extras:
        extras["audit"] = dict(extras["audit"], round=_audit_round(records, work))
    abstract = selected_abstract(work)
    if abstract is None:
        raise ResearchError("abstract_missing", "The version has no complete saved abstract to read", {"version_id": work["id"]})
    content = evaluation.text(abstract["artifact"])
    link = {"version_id": work["id"], "source_id": abstract["source_id"], "artifact": abstract["artifact"],
            "locator": span_locator(content, 0, len(content))}
    payload = {"id": "reading:" + digest([batch_id, work["id"]]), "version_id": work["id"], "depth": "abstract",
               "inspections": [{"unit_id": None, "link": link, "note": item["note"]}],
               "notes": {name: dict(note, inspections=[0]) for name, note in item["notes"].items()}}
    assessment = _assess(records, evaluation, payload)
    record = dict(payload, assessment=assessment)
    if extras:
        record["batch"] = dict(extras, batch_id=batch_id)
    return record


def _batch_members(records, results, items):
    from .screening import screenings
    members = []
    by_collection = {}
    for result, item in zip(results, items):
        work_id = records["work"][result["version_id"]]["work_id"]
        collection_id = next((m["collection_id"] for m in records.get("cohort_member", {}).values() if m["work_id"] == work_id), None)
        if collection_id not in by_collection:
            by_collection[collection_id] = screenings(records, collection_id)
        screening = by_collection[collection_id].get(work_id)
        members.append({"version_id": result["version_id"], "work_id": work_id,
                        "disposition": screening["disposition"] if screening else None})
    return members


def record_reading_batch(store, payload, *, expected_revision, request_id):
    """Record up to BATCH_LIMIT abstract readings in one event, all or none."""
    def prepare(records, value):
        fields(value, ("id", "depth", "items"), ("usage",), code="invalid_batch")
        text(value["id"], "Batch ID", code="invalid_batch")
        if value["depth"] != "abstract":
            raise ResearchError("invalid_batch", "Batches record abstract readings; a full reading uses read")
        items = value["items"]
        if not isinstance(items, list) or not 1 <= len(items) <= BATCH_LIMIT:
            raise ResearchError("invalid_batch", "A batch holds 1 to " + str(BATCH_LIMIT) + " items")
        usage = _usage(value.get("usage"))
        evaluation = Evaluation(records, ArtifactStore(store.root))
        seen, changes, results, failures = set(), [], [], []
        for index, item in enumerate(items):
            try:
                version = item.get("version_id") if isinstance(item, dict) else None
                if version in seen:
                    raise ResearchError("invalid_batch", "Each version appears once per batch")
                seen.add(version)
                record = _batch_item(records, evaluation, value["id"], item)
                changes.append(immutable_record(records, "reading", record["id"], record))
                results.append({"version_id": record["version_id"], "reading_id": record["id"],
                                "status": record["assessment"]["status"], "includes_abstract": record["assessment"]["includes_abstract"]})
            except ResearchError as error:
                failures.append({"index": index, "code": error.code, "message": error.message})
        if failures:
            raise ResearchError("invalid_batch", "Correct the failing items and resubmit the whole batch", {"items": failures})
        charge = resources.charge(records, "literature", {"readings": len(results), "model_input_tokens": usage["input_tokens"],
                                                          "model_output_tokens": usage["output_tokens"], "wall_seconds": usage["wall_seconds"]})
        if charge is not None:
            changes.append(charge)
        batch = {"id": value["id"], "depth": "abstract", "count": len(results), "revision": expected_revision + 1,
                 "consequential": any(item.get("consequential") is True for item in items),
                 "judged": all("consequential" in item for item in items),
                 "members": _batch_members(records, results, items)}
        changes.append(immutable_record(records, "reading_batch", value["id"], batch))
        return changes, {"id": value["id"], "count": len(results), "items": results, "usage": usage}

    return prepared_mutation(store, "literature.read_batch", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def current_readings(records, artifacts, version_id, *, target=None):
    """Recheck current source bytes and return complete readings of this version."""
    evaluation = Evaluation.of(records, artifacts)
    selected = selected_bundle(records, version_id, target)
    accepted, partial = [], []
    for value in evaluation.readings_for(version_id):
        assessment = evaluation.once(("assessed", value["id"]), lambda value=value: _assess(records, evaluation, value))
        if value["depth"] == "fulltext":
            # Both digests are recomputed with the current identity function, so
            # a reading recorded under an earlier representation stays current
            # while its inspected units are the units required today.
            own = records.get("source_bundle", {}).get(value.get("bundle_id"))
            if selected is None or own is None or unit_digest(own, records) != unit_digest(selected, records):
                partial.append(dict(value, assessment=dict(assessment, status="partial", pending=[obligation(
                    "reading_bundle_stale", "Inspect the currently required source bundle; historical readings are preserved.", version_id=version_id)])))
                continue
            if target and target.get("id") == version_id and (target.get("sha256") is None or selected["original_sha256"] != target["sha256"]):
                continue
        (accepted if assessment["status"] == "complete" else partial).append(dict(value, assessment=assessment))
    return sorted(accepted, key=lambda r: r["id"]), partial


def validate_read_evidence(records, artifacts, link, *, depth="fulltext", target=None):
    """Task 4 claim boundary. Verify an anchored link and an applicable reading.

Returns {reading_id, digest, depth, mechanical_only}. This validates documented
source inspection, not comprehension, entailment, or the truth of a claim.
"""
    evaluation = Evaluation.of(records, artifacts)
    context = evaluation.link(link)
    if depth not in ("fulltext", "abstract", "passage"):
        raise ResearchError("invalid_reading", "Unsupported required evidence depth")
    targets = [target]
    if target is None and context["capture"] is not None:
        original = context["capture"]["original"]["sha256"]
        originals = {original}
        # An explicitly inspected supplement belongs to its main bundle. Its
        # own original pin need not name a separate main-article bundle.
        for bundle in records.get("source_bundle", {}).values():
            if bundle["version_id"] == link["version_id"] and any(u["kind"] == "supplement" and u["link"] is not None
                    and original_identity(records, u["link"]) == original for u in bundle["units"]):
                originals.add(bundle["original_sha256"])
        targets = [{"id": link["version_id"], "sha256": sha} for sha in sorted(originals)]
    for pin in targets:
        readings, _ = current_readings(records, evaluation, link["version_id"], target=pin)
        for reading in readings:
            eligible = (reading["depth"] == "fulltext" and complete_original(context) if depth == "fulltext" else
                        reading["assessment"]["includes_abstract"] if depth == "abstract" else True)
            if eligible and any(contains(i["link"], link, records) for i in reading["inspections"]):
                return {"reading_id": reading["id"], "digest": digest(reading), "depth": depth, "mechanical_only": True}
    raise ResearchError("reading_missing", "Read the linked exact source at the required depth before relying on it",
                        {"version_id": link["version_id"], "depth": depth, "path": link["artifact"]["path"]})


def fulltext_coverage(records, artifacts, version_id, *, target=None):
    """Every distinct available main body needs its current required-unit bundle."""
    evaluation = Evaluation.of(records, artifacts)
    work = exact_work(records, version_id)
    accepted, missing = {}, []
    captures = main_captures(records, work, target)
    for capture in captures:
        pin = {"id": version_id, "sha256": capture["original"]["sha256"]}
        bundle = selected_bundle(records, version_id, pin)
        readings, partial = current_readings(records, evaluation, version_id, target=pin)
        full = next((r for r in readings if r["depth"] == "fulltext"), None)
        if full is not None:
            accepted[full["id"]] = full
        else:
            missing.append({"source_id": capture["source_id"], "original_sha256": capture["original"]["sha256"],
                            "paths": [capture["original"]["path"], capture["text"]["path"]],
                            "bundle_id": bundle["id"] if bundle else None,
                            "pending": [o for r in partial if r.get("bundle_id") == (bundle["id"] if bundle else None)
                                        for o in r["assessment"]["pending"]]})
    if not captures:
        missing.append({"source_id": None, "original_sha256": None, "paths": [], "bundle_id": None, "pending": []})
    return {"complete": not missing, "readings": accepted, "missing": missing}


def require_fulltext(store, payload, *, expected_revision, request_id):
    def prepare(records, value):
        fields(value, ("id", "profile", "version_id", "purpose", "reason"), ("historical_cutoff",))
        profile_name(value["profile"])
        exact_work(records, value["version_id"])
        text(value["reason"], "Full-depth reason")
        if value["purpose"] not in FULLTEXT_PURPOSES:
            raise ResearchError("invalid_input", "A full-depth dependency needs a supported consequential purpose")
        if value.get("historical_cutoff") is not None:
            iso_date(value["historical_cutoff"])
        value = dict(value, critical=True)
        return [immutable_record(records, "fulltext_requirement", value["id"], value)], value

    return prepared_mutation(store, "literature.require_fulltext", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


TERMINAL_ORIGIN_STATUSES = (403, 404, 410, 451)
REGISTRY_PROVIDERS = ("crossref", "openalex")


def _terminal_origin_capture(records, source, version_id, depth, policy):
    return (source.get("capture_method") == "http" and source.get("requested_identifier") == version_id
            and source.get("http_status") in policy["allowed_statuses"] and source.get("response_complete") is True
            and source.get("response") is not None and source.get("status") == "failed"
            and source.get("provider") == ("fulltext" if depth == "fulltext" else "arxiv"))


def registry_abstract_present(records, source):
    """Whether the work a registry capture observed carries a complete abstract."""
    observed = records.get("work", {}).get(source.get("observed_identifier"))
    return observed is not None and any(a["completeness"] == "complete" for a in observed["abstracts"])


def registries_addressing(work):
    """Registries the harness can query for the work's own identifiers: Crossref for a DOI, OpenAlex for a DOI or OpenAlex id."""
    identifiers = [work["id"], *work["aliases"]]
    registries = set()
    if any(i.startswith("doi:") for i in identifiers):
        registries.update(REGISTRY_PROVIDERS)
    if any(i.startswith("openalex:") for i in identifiers):
        registries.add("openalex")
    return registries


def _registry_capture_without_abstract(records, source, work):
    imported = source.get("provider") in ("web", "mcp") and source.get("imported") is True and source.get("id") in work["source_ids"]
    registry = (source.get("capture_method") == "http" and source.get("provider") in REGISTRY_PROVIDERS
                and source.get("requested_identifier") in [work["id"], *work["aliases"]] and source.get("http_status") == 200)
    return ((imported or registry) and source.get("status") == "captured" and source.get("response_complete") is True
            and source.get("response") is not None and not registry_abstract_present(records, source))


def record_availability(store, payload, *, expected_revision, request_id):
    def prepare(records, value):
        fields(value, ("id", "profile", "version_id", "depth", "source_ids", "reason", "policy"), code="invalid_availability")
        profile_name(value["profile"])
        work = exact_work(records, value["version_id"])
        text(value["reason"], "Unavailability reason", code="invalid_availability")
        if value["depth"] not in ("abstract", "fulltext"):
            raise ResearchError("invalid_availability", "Availability policy covers abstract or fulltext retrieval")
        strings(value["source_ids"], "Retrieval attempts", nonempty=True, code="invalid_availability")
        policy = value["policy"]
        fields(policy, ("id", "minimum_attempts", "allowed_statuses", "rationale"), code="invalid_availability")
        text(policy["id"], "Policy ID", code="invalid_availability")
        text(policy["rationale"], "Policy rationale", code="invalid_availability")
        statuses = policy["allowed_statuses"]
        if (type(policy["minimum_attempts"]) is not int or not 1 <= policy["minimum_attempts"] <= len(value["source_ids"])
                or not isinstance(statuses, list) or not statuses or any(type(status) is not int for status in statuses)):
            raise ResearchError("invalid_availability", "The policy must state a minimum attempt count and the captured HTTP statuses it accepts")
        sources = [records.get("source", {}).get(source_id, {}) for source_id in value["source_ids"]]
        if all(status in TERMINAL_ORIGIN_STATUSES for status in statuses):
            if not all(_terminal_origin_capture(records, s, value["version_id"], value["depth"], policy) for s in sources):
                raise ResearchError("invalid_availability", "Rate limits, resource pauses, timeouts, partial captures and unrelated failures are not source unavailability")
        elif statuses == [200] and value["depth"] == "abstract" and not value["version_id"].startswith("arxiv:"):
            if (not all(_registry_capture_without_abstract(records, s, work) for s in sources)
                    or not registries_addressing(work) <= {s["provider"] for s in sources}):
                raise ResearchError("invalid_availability", "Registry abstract absence needs a complete capture of this work from every registry that addresses its identifiers, none carrying an abstract")
        else:
            raise ResearchError("invalid_availability", "The policy must require captured terminal origin failures, or registry abstract absence at abstract depth for a non-arXiv work")
        for source in sources:
            ArtifactStore(store.root).read(source["response"])
        return [immutable_record(records, "availability", value["id"], value)], dict(value, qualification="noncritical_only")

    return prepared_mutation(store, "literature.availability", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)
