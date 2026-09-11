"""Source-specific reading evidence and explicit depth/availability obligations.

record_reading accepts {id, version_id, depth, bundle_id?, inspections, notes}.
Depth is abstract, fulltext or passage. An inspection is {unit_id: str|null,
link: Link, note: str}. Each of problem/claims/assumptions/methods/evidence/
limitations/relevance is {text, status: present|absent|not_applicable,
inspections: [zero-based indices]}. Absence notes need source anchors, not invented
methods or word-count padding. Partial inspections remain recorded as partial.

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
from .evidence import digest
from .graph import main_captures, obligation, selected_bundle
from .operations import fields, immutable_record, iso_date, prepared_mutation, profile_name, strings, text
from .source_links import complete_original, contains, covers_text, exact_work, link_identity, original_identity, validate_link
from .visual_assets import asset_dependencies


NOTE_FIELDS = ("problem", "claims", "assumptions", "methods", "evidence", "limitations", "relevance")
FULLTEXT_PURPOSES = ("major_claim", "novelty", "innovation", "validity")


def bundle_digest(bundle, records):
    """Reading dependency: original bytes and exact required-unit inventory.

Capture receipt IDs and bundle labels do not require rereading identical bytes.
Adding/changing a unit, abstract inclusion or bibliography changes this digest.
"""
    return digest({"version_id": bundle["version_id"], "original_sha256": bundle.get("original_sha256"),
                   "scope": bundle["scope"], "completeness": bundle["completeness"],
                   "includes_abstract": bundle.get("includes_abstract"),
                   "visual_dependencies": [{"unit_id": u["unit_id"], "resources": asset_dependencies(
                       records, bundle["version_id"], u["original_sha256"], u["resources"])} for u in bundle.get("visual_resources", [])],
                   "units": [{"id": u["id"], "kind": u["kind"], "required": u["required"],
                              "link": link_identity(u["link"], records) if u["link"] else None,
                              "reason": u.get("reason"), "url": u.get("url")} for u in bundle["units"]],
                   "bibliography": {"complete": bundle["bibliography"]["complete"], "unit_id": bundle["bibliography"]["unit_id"],
                                    "entries": [{"target": e["target"], "kind": e["kind"], "link": link_identity(e["link"], records)}
                                                for e in bundle["bibliography"]["entries"]]}})


def required_unit_obligations(records, artifacts, bundle):
    """Shared completeness boundary, actionable before any reading is written."""
    pending = []
    for unit in bundle["units"]:
        if not unit["required"]:
            continue
        affected = {"version_id": bundle["version_id"], "unit_id": unit["id"]}
        if unit["link"] is None:
            pending.append(obligation("required_unit_missing", "Acquire the required source unit and import the extended bundle.",
                                      url=unit.get("url"), **affected))
            continue
        context = validate_link(records, artifacts, unit["link"])
        if context["visual"] is not None:
            pending.extend(obligation(p["code"], "Acquire, link and inspect the complete static visual bytes.",
                **dict(affected, **{k: v for k, v in p.items() if k != "code"})) for p in context["visual"]["pending"])
        if not complete_original(context) or (unit["kind"] == "supplement" and not covers_text(artifacts, unit["link"])):
            pending.append(obligation("required_unit_incomplete", "Acquire the complete original required unit; a scoped passage remains partial.",
                                      paths=[unit["link"]["artifact"]["path"]], **affected))
    return pending


def _assess(records, artifacts, value):
    fields(value, ("id", "version_id", "depth", "inspections", "notes"), ("bundle_id", "assessment"), code="invalid_reading")
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
        context = validate_link(records, artifacts, inspection["link"])
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
        pending.extend(required_unit_obligations(records, artifacts, bundle))
        for unit in units.values():
            if unit["required"] and unit["link"] is not None and not any(
                    i["unit_id"] == unit["id"] and contains(i["link"], unit["link"], records) for i in inspections):
                pending.append(obligation("required_unit_uninspected", "Inspect the complete required text or visual unit.",
                                          version_id=value["version_id"], unit_id=unit["id"], paths=[unit["link"]["artifact"]["path"]]))
        included_abstract = bundle.get("includes_abstract") is not False and any(
            u["kind"] == "abstract" and u["link"] is not None and u["link"]["locator"]["kind"] == "text"
            and original_identity(records, u["link"]) == bundle["original_sha256"]
            and complete_original(validate_link(records, artifacts, u["link"])) and any(
                i["unit_id"] == u["id"] and contains(i["link"], u["link"], records) for i in inspections) for u in units.values())
        dependency = bundle_digest(bundle, records)
    elif value["depth"] == "abstract":
        if value.get("bundle_id") is not None:
            raise ResearchError("invalid_reading", "Abstract reading links the original abstract directly")
        complete = [c["abstract"] is not None and c["abstract"]["completeness"] == "complete"
                    and covers_text(artifacts, i["link"]) for c, i in zip(contexts, inspections)]
        if not any(complete):
            pending.append(obligation("abstract_reading_incomplete", "Read the complete acquired original abstract; excerpts remain partial.", version_id=value["version_id"]))
        included_abstract = any(complete)
    return {"status": "partial" if pending else "complete", "pending": pending,
            "includes_abstract": included_abstract, "bundle_digest": dependency}


def record_reading(store, payload, *, expected_revision, request_id):
    def prepare(records, value):
        fields(value, ("id", "version_id", "depth", "inspections", "notes"), ("bundle_id",), code="invalid_reading")
        assessment = _assess(records, ArtifactStore(store.root), value)
        value = dict(value, assessment=assessment)
        return [immutable_record(records, "reading", value["id"], value)], dict(id=value["id"], **assessment)

    return prepared_mutation(store, "literature.reading", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def current_readings(records, artifacts, version_id, *, target=None):
    """Recheck current source bytes and return complete readings of this version."""
    selected = selected_bundle(records, version_id, target)
    accepted, partial = [], []
    for value in records.get("reading", {}).values():
        if value["version_id"] != version_id:
            continue
        assessment = _assess(records, artifacts, value)
        if value["depth"] == "fulltext":
            if selected is None or assessment["bundle_digest"] != bundle_digest(selected, records):
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
    context = validate_link(records, artifacts, link)
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
        readings, _ = current_readings(records, artifacts, link["version_id"], target=pin)
        for reading in readings:
            eligible = (reading["depth"] == "fulltext" and complete_original(context) if depth == "fulltext" else
                        reading["assessment"]["includes_abstract"] if depth == "abstract" else True)
            if eligible and any(contains(i["link"], link, records) for i in reading["inspections"]):
                return {"reading_id": reading["id"], "digest": digest(reading), "depth": depth, "mechanical_only": True}
    raise ResearchError("reading_missing", "Read the linked exact source at the required depth before relying on it",
                        {"version_id": link["version_id"], "depth": depth, "path": link["artifact"]["path"]})


def fulltext_coverage(records, artifacts, version_id, *, target=None):
    """Every distinct available main body needs its current required-unit bundle."""
    work = exact_work(records, version_id)
    accepted, missing = {}, []
    captures = main_captures(records, work, target)
    for capture in captures:
        pin = {"id": version_id, "sha256": capture["original"]["sha256"]}
        bundle = selected_bundle(records, version_id, pin)
        readings, partial = current_readings(records, artifacts, version_id, target=pin)
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
