"""Evidence-linked bundles, five-purpose searches and foundation projections.

import_bundle accepts {id, version_id, source_id, scope: article|passage,
completeness: complete|partial, units, inventory, bibliography, resolutions}.
Each unit is {id, kind, required, link: Link|null, reason?, url?}. A missing unit
requires a reason and retrieval URL. Inventory is {text, links: [Link]}; it is a
source-anchored human assertion, not automatic verification of completeness.
Bibliography is {complete: bool, unit_id: str|null, entries: [{target, kind,
link, reason}]}. Kinds are paper, nonpaper, unknown. Resolutions use the same
fields plus reference_id and explicitly account for earlier observations.

record_search accepts {id, profile, purpose, queries, responses, captured_at,
scope, found_work_ids, verdict, cited_work_ids, impact, gaps, dispositions,
resolved?}. Responses contain {source_id, query, query_locator?, results_pointer?}.
Every found work carries one disposition (relevant, contradictory,
potentially_relevant, out_of_scope, duplicate, unresolved) with a reason; cited
works are relevant or contradictory. A new search for a purpose carries forward
every contradictory or unresolved work of the selected search, or names it under
resolved with a reason. A judgment stays current while its scope, the content of
the works it rests on (roots, required full texts, found and cited works) and the
citation frontier (graph families and tiers) are unchanged; a new version of an
unrelated reference does not invalidate it. Native registry captures
use their parser; web/MCP JSON requires results_pointer into the actual array
and the original import mappings for every result. Query provenance is a saved
response locator or the exact query in its captured URL. Search records describe
bounded searches and do not establish universal novelty.
"""

import copy
import json
import os
import uuid
from urllib.parse import parse_qsl, urlsplit

from .artifacts import ArtifactStore, _Workspace
from .cohort_evidence import cohort_report
from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from .graph import citation_graph, obligation, selected_bundle, validate_target
from .http import safe_url
from .imports import _pointer
from .operations import fields, immutable_record, iso_date, prepared_mutation, profile_name, strings, text, timestamp
from .providers import _json
from . import resources
from . import screening
from .reading import bundle_digest, current_readings, fulltext_coverage, registry_abstract_present, required_unit_obligations
from .search_pages import enumerate_pages, native_page
from .source_links import captured_source, complete_original, contains, covers_text, exact_work, fulltext_capture, original_identity, read_locator, validate_link


SEARCH_PURPOSES = ("direct", "originals", "theory", "adjacent", "recent")
DEVELOPMENT_PURPOSES = ("downstream", "next_step", "exemplars", "changes")
NOVELTY_VERDICTS = ("nothing-new", "scooped", "replicate-extend", "contradicted", "novel-confirmed")
DISPOSITIONS = ("relevant", "contradictory", "potentially_relevant", "out_of_scope", "duplicate", "unresolved")
CARRIED_DISPOSITIONS = ("contradictory", "unresolved")
UNIT_KINDS = ("text", "abstract", "figure", "table", "equation", "supplement", "bibliography")


def _reference(value, records, artifacts, version_id, bibliography_unit):
    fields(value, ("target", "kind", "link", "reason"), ("reference_id",), code="invalid_bibliography")
    if value["kind"] not in ("paper", "nonpaper", "unknown"):
        raise ResearchError("invalid_bibliography", "Classify the reference as paper, nonpaper or unknown")
    text(value["reason"], "Reference classification", code="invalid_bibliography")
    context = validate_link(records, artifacts, value["link"])
    if (context["work"]["id"] != version_id or bibliography_unit is None or bibliography_unit["link"] is None
            or not contains(bibliography_unit["link"], value["link"], records)):
        raise ResearchError("invalid_bibliography", "Each occurrence must be anchored within this version's bibliography unit")
    if value["target"] is not None:
        from .identities import normalize_identifier
        if normalize_identifier(value["target"]) != value["target"]:
            raise ResearchError("invalid_bibliography", "Use a canonical reference target identifier")
    if value["kind"] == "paper" and value["target"] is None:
        raise ResearchError("invalid_bibliography", "An identified paper reference needs its exact identifier or family")


def import_bundle(store, payload, *, expected_revision, request_id):
    def prepare(records, value):
        fields(value, ("id", "version_id", "source_id", "scope", "completeness", "units", "inventory", "bibliography", "resolutions"), code="invalid_bundle")
        text(value["id"], "Bundle ID", code="invalid_bundle")
        work = exact_work(records, value["version_id"])
        artifacts = Evaluation(records, ArtifactStore(store.root))
        source = captured_source(records, artifacts, value["source_id"])
        capture = fulltext_capture(work, source["id"])
        if value["scope"] not in ("article", "passage") or value["completeness"] not in ("complete", "partial"):
            raise ResearchError("invalid_bundle", "Declare article/passage scope and complete/partial bundle inventory")
        if value["scope"] == "article" and (capture is None or capture["availability"] != "available"
                or source["capture_method"] != "http" or source["origin_verified"] is not True):
            raise ResearchError("invalid_bundle", "Article bundles require acquired original full text; tool responses remain passages")
        if value["scope"] == "passage" and value["completeness"] == "complete":
            raise ResearchError("invalid_bundle", "A passage cannot certify a complete article bundle")
        if not isinstance(value["units"], list) or not value["units"]:
            raise ResearchError("invalid_bundle", "Declare the article's actual text, visual and supplement units")
        units, visual_resources = {}, []
        for unit in value["units"]:
            fields(unit, ("id", "kind", "required", "link"), ("reason", "url"), code="invalid_bundle")
            text(unit["id"], "Unit ID", code="invalid_bundle")
            if unit["id"] in units or unit["kind"] not in UNIT_KINDS or type(unit["required"]) is not bool:
                raise ResearchError("invalid_bundle", "Units need unique IDs, supported kinds and an explicit requirement")
            units[unit["id"]] = unit
            if unit["link"] is None:
                text(unit.get("reason"), "Missing unit reason", code="invalid_bundle")
                safe_url(unit.get("url"))
                continue
            context = validate_link(records, artifacts, unit["link"])
            if context["work"]["id"] != value["version_id"]:
                raise ResearchError("source_mismatch", "A bundle cannot borrow another article or version's units")
            if unit["kind"] in ("figure", "table", "equation") and unit["link"]["locator"]["kind"] not in ("pdf", "html"):
                raise ResearchError("invalid_bundle", "Required visual material needs a locator in the original document")
            if context["visual"] is not None and unit["required"]:
                visual_resources.append({"unit_id": unit["id"], "original_sha256": original_identity(records, unit["link"]),
                                         "resources": context["visual"]["resources"]})
        if value["scope"] == "article" and not any(u["kind"] == "text" and u["required"] and u["link"] is not None
                and u["link"]["source_id"] == source["id"] and u["link"]["artifact"] == capture["text"] for u in units.values()):
            raise ResearchError("invalid_bundle", "An article bundle must delimit its acquired main text")
        inventory = value["inventory"]
        fields(inventory, ("text", "links"), code="invalid_bundle")
        text(inventory["text"], "Article inventory note", code="invalid_bundle")
        if not isinstance(inventory["links"], list) or not inventory["links"]:
            raise ResearchError("invalid_bundle", "Anchor the article-boundary and required-material inventory in saved sources")
        for link in inventory["links"]:
            if validate_link(records, artifacts, link)["work"]["id"] != value["version_id"]:
                raise ResearchError("source_mismatch", "The inventory must describe this exact version")
        bibliography = value["bibliography"]
        fields(bibliography, ("complete", "unit_id", "entries"), code="invalid_bibliography")
        if bibliography["unit_id"] is not None:
            text(bibliography["unit_id"], "Bibliography unit ID", code="invalid_bibliography")
        unit = units.get(bibliography["unit_id"])
        if type(bibliography["complete"]) is not bool or not isinstance(bibliography["entries"], list):
            raise ResearchError("invalid_bibliography", "Bibliography completeness and occurrences must be explicit")
        if bibliography["complete"] and (value["scope"] != "article" or unit is None or unit["kind"] != "bibliography" or unit["link"] is None):
            raise ResearchError("invalid_bibliography", "Only a located article bibliography can establish reference coverage")
        if bibliography["complete"]:
            context = validate_link(records, artifacts, unit["link"])
            if context["visual"] is not None and context["visual"]["pending"]:
                raise ResearchError("invalid_bibliography", "Acquire and link the original visual bibliography bytes before declaring completeness",
                                    {"pending": context["visual"]["pending"]})
            if not complete_original(context) or (context["capture"]["original"]["sha256"] != capture["original"]["sha256"]
                    and not any(u["kind"] == "supplement" and u["required"] and u["link"] is not None
                        and covers_text(artifacts, u["link"]) and contains(u["link"], unit["link"], records) for u in units.values())):
                raise ResearchError("invalid_bibliography", "Complete bibliography requires the main original or an explicitly complete original supplement")
        if not isinstance(value["resolutions"], list):
            raise ResearchError("invalid_bibliography", "Reference resolutions must be an array")
        changes, occurrence_ids = [], []
        for index, entry in enumerate(bibliography["entries"]):
            _reference(entry, records, artifacts, value["version_id"], unit)
            identifier = "bib:" + digest([value["id"], index])
            occurrence = {"id": identifier, "source_id": entry["link"]["source_id"], "source_work_id": work["id"],
                          "work_id": work["work_id"], "ordinal": index, "target": entry["target"], "kind": entry["kind"],
                          "raw": entry["link"]["locator"], "bundle_id": value["id"], "evidence": entry}
            changes.append(immutable_record(records, "reference_occurrence", identifier, occurrence))
            occurrence_ids.append(identifier)
        resolved = set()
        for resolution in value["resolutions"]:
            _reference(resolution, records, artifacts, value["version_id"], unit)
            text(resolution.get("reference_id"), "Reference occurrence ID", code="invalid_bibliography")
            occurrence = records.get("reference_occurrence", {}).get(resolution.get("reference_id"))
            if occurrence is None or occurrence["source_work_id"] != work["id"] or occurrence["id"] in resolved:
                raise ResearchError("invalid_bibliography", "Resolve each existing occurrence once within its exact source version")
            resolved.add(occurrence["id"])
            changes.append(("reference_resolution", occurrence["id"], dict(resolution, bundle_id=value["id"])))
        existing = records.get("source_bundle", {}).get(value["id"])
        bundle = dict(value, original_sha256=capture["original"]["sha256"] if capture else source["response"]["sha256"],
                      includes_abstract=capture["includes_abstract"] if capture else False, occurrence_ids=occurrence_ids,
                      visual_resources=visual_resources,
                      sequence=existing["sequence"] if existing else len(records.get("source_bundle", {})) + 1)
        for earlier in records.get("source_bundle", {}).values():
            if earlier["version_id"] == work["id"] and earlier["original_sha256"] == bundle["original_sha256"]:
                for unit in earlier["units"]:
                    if unit["required"] and (unit["id"] not in units or not units[unit["id"]]["required"] or units[unit["id"]]["kind"] != unit["kind"]):
                        raise ResearchError("invalid_bundle", "A new inventory cannot drop or demote previously required units of the same original body",
                                            {"unit_id": unit["id"], "previous_bundle_id": earlier["id"]})
                    if unit["required"] and unit["link"] is not None and unit["link"]["locator"]["kind"] == "html":
                        previous = validate_link(records, artifacts, unit["link"])["visual"]["resources"]
                        retained = next((v["resources"] for v in visual_resources if v["unit_id"] == unit["id"]), [])
                        if not {r["url"] for r in previous} <= {r["url"] for r in retained}:
                            raise ResearchError("invalid_bundle", "An anchor change cannot discard known resources of a required visual",
                                                {"unit_id": unit["id"], "previous_bundle_id": earlier["id"]})
        changes.append(immutable_record(records, "source_bundle", value["id"], bundle))
        changes.append(("bundle_selection", work["id"], {"bundle_id": value["id"]}))
        updated = copy.deepcopy(work)
        for identifier in occurrence_ids:
            if identifier not in updated["references"]:
                updated["references"].append(identifier)
        reference_set = {"source_id": source["id"], "bundle_id": value["id"], "occurrence_ids": occurrence_ids,
                         "metadata": {"status": "provided", "bibliography_complete": bibliography["complete"], "scope": "article_bibliography"}}
        if reference_set not in updated["reference_sets"]:
            updated["reference_sets"].append(reference_set)
        changes.append(("work", work["id"], updated))
        return changes, {"id": value["id"], "version_id": work["id"], "digest": bundle_digest(bundle, records), "occurrence_ids": occurrence_ids}

    return prepared_mutation(store, "literature.bundle", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def _search_response(records, artifacts, response, scope):
    fields(response, ("source_id", "query"), ("query_locator", "results_pointer"), code="invalid_search")
    source = captured_source(records, artifacts, response["source_id"])
    text(response["query"], "Search query", code="invalid_search")
    if response.get("query_locator") is not None:
        if read_locator(artifacts, source["response"], response["query_locator"]) != response["query"]:
            raise ResearchError("invalid_search", "The recorded query must equal its original response value")
    else:
        names = {"arxiv": {"search_query"}, "openalex": {"search"}, "crossref": {"query", "query.bibliographic", "query.author"}}
        names = names.get(source["provider"], {"q", "query", "search", "search_query"})
        actual = [value for key, value in parse_qsl(urlsplit(source["url"]).query) if key in names]
        if response["query"] not in actual:
            raise ResearchError("invalid_search", "Bind the exact search-query parameter or a saved response query value; URL path fragments are insufficient")
    if source["provider"] in ("arxiv", "openalex", "crossref"):
        page = native_page(source, artifacts.read(source["response"]), response["query"], scope)
        found, pending = page["found"], []
    elif source["provider"] in ("web", "mcp"):
        page = None
        try:
            raw = _pointer(_json(artifacts.read(source["response"]), require_object=False), response.get("results_pointer"))
        except ResearchError as error:
            raise ResearchError("invalid_search", "Tool searches require a locator into the original JSON result array") from error
        if not isinstance(raw, list):
            raise ResearchError("invalid_search", "The original search results must be an array, including an actual empty array")
        imports = [r for r in records.get("source_import", {}).values() if r["source_id"] == source["id"]]
        if len(imports) != 1:
            raise ResearchError("invalid_search", "Import the original tool response and result mappings first")
        imported = imports[0]
        pointers = {m["id"] for m in imported["mappings"]}
        prefix = response["results_pointer"].rstrip("/") + "/"
        if len(imported["mappings"]) != len(raw) or any(not any(p.startswith(prefix + str(i) + "/") for p in pointers) for i in range(len(raw))):
            raise ResearchError("invalid_search", "Preserve an explicit imported work mapping for every returned result")
        found, pending = imported["work_ids"], list(imported["failures"])
    else:
        raise ResearchError("invalid_search", "Use a supported registry or web/MCP search-response capture")
    for identifier in found:
        if identifier not in records.get("work", {}) or source["id"] not in records["work"][identifier]["source_ids"]:
            raise ResearchError("invalid_search", "Found works must retain this search response as provenance")
    return source, found, pending, page


def _graph(evaluation, profile):
    def compute():
        evaluation.counters["graph_builds"] += 1
        return citation_graph(evaluation.records, profile)
    return evaluation.once(("graph", profile), compute)


def frontier(evaluation, profile):
    """The candidate frontier a judgment was made against: graph families with their tiers and the
families of required full texts. The works other purposes found enter a section through the
judgments digest, so recording one purpose does not stale the others."""
    records = evaluation.records
    rows = {(node["work_id"], str(node["tier"])) for node in _graph(evaluation, profile)["nodes"]}
    for requirement in records.get("fulltext_requirement", {}).values():
        work = records.get("work", {}).get(requirement["version_id"])
        if requirement["profile"] == profile and work:
            rows.add((work["work_id"], "requirement"))
    return sorted([family, tier] for family, tier in rows)


def frontier_digest(evaluation, profile):
    return digest(frontier(evaluation, profile))


def _relevant_versions(records, scope, found, cited):
    """Versions whose content a judgment rests on: roots, required full texts, found and cited works."""
    families = set()
    for version in list(scope.get("roots", [])) + list(found) + list(cited):
        work = records.get("work", {}).get(version)
        families.add(work["work_id"] if work else version)
    for requirement in records.get("fulltext_requirement", {}).values():
        work = records.get("work", {}).get(requirement["version_id"])
        if requirement["profile"] == scope["profile"] and work:
            families.add(work["work_id"])
    versions = set(found) | set(cited)
    for family in families:
        versions.update(records.get("work_family", {}).get(family, {}).get("version_ids", []))
    return versions


def _search_evidence_digest(evaluation, scope, found, cited=()):
    """Relevant content changes invalidate judgments; receipt-only repeats and unrelated references do not."""
    records = evaluation.records
    graph = _graph(evaluation, scope["profile"])
    versions = _relevant_versions(records, scope, found, cited)
    works = {}
    for version in sorted(versions):
        work = records.get("work", {}).get(version, {})
        content = {k: work.get(k) for k in ("id", "work_id", "version", "title", "authors", "publication_date", "category", "type")}
        content["known_versions"] = records.get("work_family", {}).get(work.get("work_id"), {}).get("version_ids", [])
        content["abstracts"] = sorted({digest([a["artifact"]["sha256"], a["completeness"]]) for a in work.get("abstracts", [])})
        content["date_assertions"] = sorted({digest(a["values"]) for a in work.get("date_assertions", [])})
        content["fulltexts"] = sorted({digest([c["original"]["sha256"] if c["original"] else None,
            c["text"]["sha256"] if c["text"] else None, c["availability"], c["includes_abstract"]]) for c in work.get("fulltexts", [])})
        content["references"] = sorted({digest([r["target"], r["kind"], r["raw"]]) for r in graph["references"] if r["version_id"] == version})
        content["aliases"] = {k: sorted({a["work_id"] for a in record["assertions"]}) for k, record in records.get("alias", {}).items()
                              if k in work.get("aliases", []) or any(a["work_id"] == work.get("work_id") for a in record["assertions"])}
        bundle = selected_bundle(records, version, scope.get("target"))
        content["bundle_digest"] = bundle_digest(bundle, records) if bundle else None
        content["visual_assets"] = sorted({digest([a["html_sha256"], a["url"], a["availability"],
            a["artifact"]["sha256"] if a["artifact"] else None]) for a in records.get("visual_asset", {}).values() if a["version_id"] == version})
        works[version] = content
    return digest([scope, works])


def _dispositions(records, value):
    found = set(value["found_work_ids"])
    judged = {}
    for item in _items_list(value["dispositions"], "Dispositions"):
        fields(item, ("work_id", "disposition", "reason"), code="invalid_search")
        text(item["reason"], "Disposition reason", code="invalid_search")
        if item["disposition"] not in DISPOSITIONS or item["work_id"] not in found or item["work_id"] in judged:
            raise ResearchError("invalid_search", "Judge each found work exactly once with a supported disposition")
        judged[item["work_id"]] = item["disposition"]
    if set(judged) != found:
        raise ResearchError("invalid_search", "Every found work needs a disposition")
    if any(judged[w] not in ("relevant", "contradictory") for w in value["cited_work_ids"]):
        raise ResearchError("invalid_search", "Cited works are the relevant or contradictory found works")
    resolved = {}
    for item in _items_list(value.get("resolved", []), "Resolved findings"):
        fields(item, ("work_id", "reason"), code="invalid_search")
        text(item["work_id"], "Resolved work", code="invalid_search")
        text(item["reason"], "Resolution", code="invalid_search")
        resolved[item["work_id"]] = item["reason"]
    selection = records.get("search_selection", {}).get(value["profile"] + ":" + value["purpose"])
    previous = records.get("literature_search", {}).get(selection["search_id"]) if selection else None
    for item in (previous or {}).get("dispositions", []):
        carried = judged.get(item["work_id"]) in CARRIED_DISPOSITIONS + ("relevant",)
        if item["disposition"] in CARRIED_DISPOSITIONS and not carried and item["work_id"] not in resolved:
            raise ResearchError("search_findings_dropped", "Carry forward or explicitly resolve the selected search's contradictory and unresolved findings",
                                {"work_id": item["work_id"], "disposition": item["disposition"], "search_id": previous["id"]})
    return judged


def _items_list(value, name):
    if not isinstance(value, list):
        raise ResearchError("invalid_search", name + " must be an array")
    return value


def record_search(store, payload, *, expected_revision, request_id):
    def prepare(records, value):
        fields(value, ("id", "profile", "purpose", "queries", "responses", "captured_at", "scope", "found_work_ids", "verdict",
                       "cited_work_ids", "impact", "gaps", "dispositions"), ("resolved",), code="invalid_search")
        profile_name(value["profile"])
        if value["purpose"] not in SEARCH_PURPOSES + DEVELOPMENT_PURPOSES or value["verdict"] not in NOVELTY_VERDICTS:
            raise ResearchError("invalid_search", "Use the five search purposes, the four development purposes, and the existing novelty verdict vocabulary")
        strings(value["queries"], "Queries", nonempty=True, code="invalid_search")
        strings(value["found_work_ids"], "Found works", code="invalid_search")
        strings(value["cited_work_ids"], "Cited works", code="invalid_search")
        strings(value["gaps"], "Remaining gaps", code="invalid_search")
        text(value["scope"], "Search scope", code="invalid_search")
        text(value["impact"], "Search impact", code="invalid_search")
        date = timestamp(value["captured_at"])
        if not isinstance(value["responses"], list) or not value["responses"]:
            raise ResearchError("invalid_search", "An empty user list without original captured responses is not a search")
        found, pending, queries, pages = set(), [], set(), []
        evaluation = Evaluation(records, ArtifactStore(store.root))
        for response in value["responses"]:
            source, identifiers, gaps, page = _search_response(records, evaluation, response, value["scope"])
            if timestamp(source["captured_at"]) > date:
                raise ResearchError("invalid_search", "A search cannot precede its captured responses")
            found.update(identifiers)
            pending.extend(gaps)
            queries.add(response["query"])
            if page is not None:
                pages.append(page)
        page_groups, enumeration_pending = enumerate_pages(pages)
        pending.extend(enumeration_pending)
        if found != set(value["found_work_ids"]) or queries != set(value["queries"]):
            raise ResearchError("invalid_search", "Search results and queries must account for every saved response")
        if not set(value["cited_work_ids"]) <= found or value["verdict"] == "replicate-extend" and not value["cited_work_ids"]:
            raise ResearchError("invalid_search", "A replicate-extend verdict must cite acquired work from the search")
        current_scope = records.get("literature_scope", {}).get(value["profile"])
        if current_scope is None:
            raise ResearchError("roots_missing", "Define the literature scope before recording a dependent search")
        _dispositions(records, value)
        record = dict(value, scope_digest=digest(current_scope), pending=pending, page_groups=page_groups,
                      evidence_digest=_search_evidence_digest(evaluation, current_scope, found, value["cited_work_ids"]),
                      frontier_digest=frontier_digest(evaluation, value["profile"]))
        changes = [immutable_record(records, "literature_search", value["id"], record)]
        changes.append(("search_selection", value["profile"] + ":" + value["purpose"], {"search_id": value["id"]}))
        for version in value["cited_work_ids"]:
            identifier = "search:" + digest([value["id"], version])
            requirement = {"id": identifier, "profile": value["profile"], "version_id": version, "purpose": "novelty",
                           "reason": "Source cited in search judgment " + value["id"] + ": " + value["impact"], "critical": True}
            if current_scope.get("historical_cutoff") and value["purpose"] != "recent":
                requirement["historical_cutoff"] = current_scope["historical_cutoff"]
            changes.append(immutable_record(records, "fulltext_requirement", identifier, requirement))
        return changes, {"id": value["id"], "pending": pending, "found_work_ids": sorted(found)}

    return prepared_mutation(store, "literature.search", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def _historical_status(work, cutoff):
    if cutoff is None:
        return "not_requested"
    cutoff = iso_date(cutoff)
    assertions = [a["values"] for a in work["date_assertions"]]
    # arXiv explicitly distinguishes original submission from this capture's
    # update date. A first submission inside the cohort does not date later text.
    if work["id"].startswith("arxiv:"):
        updates = [a.get("updated") for a in assertions if a.get("updated")]
        if not updates:
            return "unknown"
        return "known_before" if all(timestamp(d).date() <= cutoff for d in updates) else "later_capture"
    # Registry publication dates date an event, not the captured article's text.
    # Without an explicit exact content version, preserve the temporal question.
    return "unknown"


def _work_sources(work, records):
    assets = [records["visual_asset"][a] for a in work.get("visual_asset_ids", [])]
    return sorted(set(work["source_ids"]) | {c["source_id"] for c in work["fulltexts"] if c["source_id"]}
                  | {s for a in assets for s in a["source_ids"]})


def _work_paths(work, records, artifacts):
    references = [a["artifact"] for a in work["abstracts"]]
    references.extend(a for c in work["fulltexts"] for a in (c["original"], c["text"]) if a is not None)
    references.extend(records["source"][s]["response"] for s in _work_sources(work, records) if records["source"][s]["response"] is not None)
    for reference in references:
        artifacts.read(reference)
    return sorted({a["path"] for a in references})


def foundation_report(store, profile):
    """Return mechanical readiness, exact dependencies, inventory and next work.

Reports are read-only and do not create exports. The digest excludes revision,
unrelated notes and unselected corpus/works. It includes relevant work/version/
identity/source assertions, active bundles, used readings, selected collections,
reference resolutions, consequential requirements and purpose-specific searches.
"""
    profile_name(profile)
    records = store.snapshot()["records"]
    return foundation_state(records, Evaluation(records, ArtifactStore(store.root)), profile)


def foundation_state(records, artifacts, profile):
    """The same foundation assessment on a caller-owned consistent snapshot."""
    profile_name(profile)
    evaluation = Evaluation.of(records, artifacts)
    return evaluation.once(("foundation", profile), lambda: _foundation_state(evaluation, profile))


def _foundation_state(evaluation, profile):
    records, artifacts = evaluation.records, evaluation
    scope = records.get("literature_scope", {}).get(profile, {})
    graph = _graph(evaluation, profile)
    obligations = list(graph["obligations"])
    target = scope.get("target")
    if profile == "verification" and scope:
        try:
            validate_target(records, target, scope["roots"])
            if target["source_id"] is None:
                obligations.append(obligation("target_source_pin_missing", "Acquire and pin the exact original target body before completing verification.", version_id=target["id"]))
            else:
                captured_source(records, artifacts, target["source_id"])
        except ResearchError as error:
            obligations.append(obligation("target_source_pin_invalid", "Repair the original-document target pin.", reason=error.code))
        configured = records.get("configuration", {}).get("research", {})
        if configured.get("profile") == "verification" and configured.get("target") != target:
            obligations.append(obligation("target_mismatch", "Reconcile literature with the configured exact verification target."))
    requirements, tiers, reasons = {}, {}, {}
    for node in graph["nodes"]:
        for version in node["version_ids"]:
            tiers[version] = node["tier"]
            requirements[version] = "fulltext" if node["tier"] <= 2 else "abstract"
            reasons.setdefault(version, []).append("tier_" + str(node["tier"]))
    critical = {target["id"]} if target else set()
    full_requirements = {k: r for k, r in records.get("fulltext_requirement", {}).items() if r["profile"] == profile}
    for requirement in full_requirements.values():
        version = requirement["version_id"]
        requirements[version] = "fulltext"
        critical.add(version)
        reasons.setdefault(version, []).append(requirement["purpose"])
    cohort_state = cohort_report(records, artifacts, scope.get("collection_ids", []), target=target)
    collections, cohort, historical = cohort_state["collections"], cohort_state["inventory"], set(requirements)
    obligations.extend(cohort_state["obligations"])
    for item in cohort:
        requirements.setdefault(item["version_id"], "abstract")
        reasons.setdefault(item["version_id"], []).append("cohort")
        historical.add(item["version_id"])
    from .rounds import active_round, fresh_searches, has_round_exemplar, latest_admission
    latest = latest_admission(records) if profile == "research" else None
    active = active_round(records) if latest is not None else None
    searches = {k: s for k, s in records.get("literature_search", {}).items() if s["profile"] == profile}
    selections = records.get("search_selection", {})
    selected_searches = {purpose: selections.get(profile + ":" + purpose, {}).get("search_id") for purpose in SEARCH_PURPOSES}
    # The current round's consequence searches are judgments beside the five purposes. A selection the
    # round opened with is what the round refreshes, and before any round there are no consequence judgments.
    if latest is not None:
        selected_searches.update(fresh_searches(records, latest))
    if active is not None:
        obligations.extend(obligation("round_search_missing", "Record this development round's captured search for the purpose.", purpose=purpose)
                           for purpose in DEVELOPMENT_PURPOSES if purpose not in selected_searches)
    for purpose in selected_searches:
        matches = [s for s in searches.values() if s["id"] == selected_searches[purpose] and s["purpose"] == purpose]
        current = [s for s in matches if s["scope_digest"] == digest(scope)]
        if not current:
            obligations.append(obligation("search_scope_stale" if matches else "search_purpose_missing",
                "Record this purpose's saved search responses against the current literature scope.", purpose=purpose))
        for search in current:
            for response in search["responses"]:
                _search_response(records, artifacts, response, search["scope"])
            if search["pending"]:
                obligations.append(obligation("search_pending", "Complete the captured search enumeration or parser obligations.",
                                              purpose=purpose, search_id=search["id"], pending=search["pending"]))
            if "dispositions" not in search:
                obligations.append(obligation("search_dispositions_missing", "Re-record this search with a disposition for every found work.",
                                              purpose=purpose, search_id=search["id"]))
            if search["evidence_digest"] != _search_evidence_digest(evaluation, scope, search["found_work_ids"], search["cited_work_ids"]):
                obligations.append(obligation("search_evidence_stale", "Reassess this search judgment after relevant source, identity or version changes.",
                                              purpose=purpose, search_id=search["id"]))
            if search.get("frontier_digest") != frontier_digest(evaluation, profile):
                obligations.append(obligation("search_frontier_stale", "Assess the works that entered the citation frontier since this judgment.",
                                              purpose=purpose, search_id=search["id"]))
    if active and not has_round_exemplar(records, active):
        obligations.append(obligation("round_exemplar_missing", "Select a development exemplar with require-fulltext (purpose exemplar) and read it in full."))
    relevant = set(requirements) | {v for s in searches.values() for v in s["found_work_ids"]}
    inventory, used_readings, bundles, availability = [], dict(cohort_state["readings"]), {}, []
    reference_sample = screening.reference_sample(records, profile)
    reference_round = screening.lowest_excluded_round(screening.screenings(records, None))
    for version in sorted(relevant):
        work = records.get("work", {}).get(version)
        if work is None:
            obligations.append(obligation("work_missing", "Acquire the exact required work.", version_id=version))
            continue
        paths = _work_paths(work, records, artifacts)
        if version.startswith("arxiv:") and work["version"] is None:
            accepted, partial = [], []
        else:
            accepted, partial = current_readings(records, artifacts, version, target=target)
        bundle = selected_bundle(records, version, target)
        if bundle is not None:
            bundles[version] = bundle
            for unit in bundle["units"]:
                if unit["link"]:
                    evaluation.link(unit["link"])
        coverage = fulltext_coverage(records, artifacts, version, target=target) if depth_is_exact_fulltext(requirements, work) else None
        if coverage is not None:
            full = next(iter(coverage["readings"].values()), None) if coverage["complete"] else None
        else:
            full = next((r for r in accepted if r["depth"] == "fulltext"), None)
        abstract = next((r for r in accepted if r["assessment"]["includes_abstract"]), None)
        depth = requirements.get(version)
        qualified = [a for a in records.get("availability", {}).values() if a["profile"] == profile
                     and a["version_id"] == version and a["depth"] == depth]
        available = (any(c["availability"] == "available" for c in work["fulltexts"]) if depth == "fulltext" else
                     any(a["completeness"] == "complete" for a in work["abstracts"]))
        if available:
            qualified = []
        if depth == "abstract":
            qualified = [q for q in qualified if not any(registry_abstract_present(records, records["source"][s]) for s in q["source_ids"])]
        for item in qualified:
            for source_id in item["source_ids"]:
                artifacts.read(records["source"][source_id]["response"])
            availability.append(dict(item, qualification="noncritical_only", reading_complete=False))
        if depth == "fulltext" and full is None:
            if version in critical and qualified:
                obligations.append(obligation("critical_source_unavailable", "The critical dependency still needs exact full text; availability qualification is insufficient.", version_id=version, paths=paths))
            if not qualified or version in critical:
                for missing in coverage["missing"] if coverage else [{"paths": paths, "source_id": None, "bundle_id": None, "pending": []}]:
                    obligations.append(obligation("fulltext_reading_missing", "Acquire/import and inspect every required unit of this exact source bundle.",
                        version_id=version, paths=missing["paths"] or paths, source_id=missing["source_id"]))
                    if missing["bundle_id"] is None:
                        obligations.append(obligation("source_bundle_missing", "Import the boundaries and required units for this distinct original body.",
                            version_id=version, source_id=missing["source_id"], paths=missing["paths"] or paths))
                    obligations.extend(missing["pending"])
                if bundle is None:
                    obligations.append(obligation("source_bundle_missing", "Import the exact article boundaries and required source units.", version_id=version, paths=paths))
                else:
                    obligations.extend(required_unit_obligations(records, artifacts, bundle))
                for reading in partial:
                    obligations.extend(reading["assessment"]["pending"])
        if depth == "abstract" and "cohort" not in reasons.get(version, []):
            obligations.extend(screening.reference_obligations(records, profile, version, work, abstract, accepted, qualified,
                                                               reference_sample, reference_round, paths))
        cutoff = scope.get("historical_cutoff") if version in historical else None
        cutoffs = [r["historical_cutoff"] for r in full_requirements.values() if r["version_id"] == version and r.get("historical_cutoff")]
        if cutoffs:
            cutoff = min(cutoffs + ([cutoff] if cutoff else []))
        temporal = _historical_status(work, cutoff)
        if cutoff and temporal != "known_before":
            obligations.append(obligation("historical_version_unresolved", "Acquire the version that existed by the historical cutoff; current text is not proof of earlier content.",
                                          version_id=version, cutoff=cutoff, status=temporal))
        for reading in (full if depth == "fulltext" else abstract, abstract if "cohort" in reasons.get(version, []) else None):
            if reading:
                used_readings[reading["id"]] = reading
        if coverage:
            used_readings.update(coverage["readings"])
        inventory.append({"version_id": version, "work_id": work["work_id"], "title": work["title"], "tier": tiers.get(version),
                          "required_depth": depth, "reasons": sorted(set(reasons.get(version, []))), "source_paths": paths,
                          "date_assertions": work["date_assertions"], "historical_status": temporal,
                          "fulltext_read": full is not None, "abstract_read": abstract is not None,
                          "body_coverage": coverage,
                          "bundle_id": bundle["id"] if bundle else None, "required_units": bundle["units"] if bundle else [],
                          "visual_assets": [records["visual_asset"][a] for a in work.get("visual_asset_ids", [])],
                          "retrievals": [{"source_id": c["source_id"], "availability": c["availability"],
                                          "extraction_status": c["extraction_status"], "url": c["url"],
                                          "next_eligible_at": records.get("source", {}).get(c["source_id"], {}).get("next_eligible_at")}
                                         for c in work["fulltexts"]]})
    # An exhausted development budget is the round gate's obligation, never a foundation or readiness one.
    obligations.extend(o for o in resources.obligations(records, profile) if o["purpose"] != "development")
    # Deduplicate repeated unit obligations while retaining each occurrence and
    # each independent cohort/version obligation.
    obligations = sorted({digest(o): o for o in obligations}.values(), key=lambda o: (o["code"], o.get("version_id", ""), digest(o)))
    families = {records["work"][v]["work_id"] for v in relevant if v in records.get("work", {})}
    aliases = {k: a for k, a in records.get("alias", {}).items() if any(x["work_id"] in families for x in a["assertions"])
               or any(r["target"] == k for r in graph["references"])}
    source_ids = {s for v in relevant if v in records.get("work", {}) for s in _work_sources(records["work"][v], records)}
    source_ids.update(r["source_id"] for s in searches.values() for r in s["responses"])
    source_ids.update(s for a in availability for s in a["source_ids"])
    dependencies = {"scope": scope, "target": records.get("configuration", {}).get("research", {}).get("target") if profile == "verification" else None,
                    "works": {v: records["work"][v] for v in sorted(relevant) if v in records.get("work", {})}, "aliases": aliases,
                    "sources": {s: records["source"][s] for s in sorted(source_ids)}, "graph": graph, "bundles": bundles,
                    "readings": used_readings, "collections": collections, "cohort_digest": cohort_state["digest"], "requirements": full_requirements,
                    "searches": searches, "availability": availability}
    dependencies["search_selection"] = selected_searches
    dependencies["visual_assets"] = {k: a for k, a in records.get("visual_asset", {}).items() if a["version_id"] in relevant}
    dependencies["visual_asset_selection"] = {k: s for k, s in records.get("visual_asset_selection", {}).items()
                                                if s["asset_id"] in dependencies["visual_assets"]}
    population = [{"definition": records["collection"][c]["definition"],
                   "members": sorted(m["work_id"] for m in records.get("cohort_member", {}).values() if m["collection_id"] == c)}
                  for c in scope.get("collection_ids", []) if c in records.get("collection", {})]
    judgments = [{k: s.get(k) for k in ("id", "purpose", "verdict", "found_work_ids", "cited_work_ids", "dispositions", "gaps", "impact")}
                 for s in sorted((searches[i] for i in selected_searches.values() if i in searches), key=lambda s: s["purpose"])]
    counts = {"works": len(families), "versions": len(inventory), "cohort_families": len({x["work_id"] for x in cohort}),
              "reference_occurrences": len(graph["references"]), "obligations": len(obligations),
              "fulltext_read": sum(x["fulltext_read"] for x in inventory), "abstract_read": sum(x["abstract_read"] for x in inventory)}
    counts.update({"tier_" + str(t): sum(n["tier"] == t for n in graph["nodes"]) for t in (1, 2, 3)})
    return {"ready": not obligations, "digest": digest(dependencies), "obligations": obligations, "counts": counts,
            "population_digest": digest(population), "frontier_digest": frontier_digest(evaluation, profile),
            "judgments_digest": digest(judgments), "requirements_digest": digest(full_requirements),
            "passed": {"graph": not graph["obligations"], "cohort": bool(collections) and not any(o["code"] in ("collection_pending", "cohort_abstract_reading_missing") for o in obligations),
                       "searches": not any(o["code"].startswith("search_") for o in obligations)},
            "invalid_references": [r for r in graph["references"] if r["status"] not in ("resolved", "nonpaper")],
            "inventory": inventory, "collections": collections, "cohort": cohort_state, "availability_qualified": availability,
            "searches": [searches[s] for s in selected_searches.values() if s in searches], "search_history": list(searches.values()),
            "next": next((o for o in obligations if o.get("paths")), obligations[0] if obligations else None),
            "limits": "Mechanical source and inspection validation does not establish comprehension, source entailment, scientific truth, or universal novelty."}


def depth_is_exact_fulltext(requirements, work):
    return requirements.get(work["id"]) == "fulltext" and not (work["id"].startswith("arxiv:") and work["version"] is None)


def export_foundation(store, profile):
    """Write safe mutable inventory/coverage projections; never read them as state."""
    report = foundation_report(store, profile)
    inventory = ["# Literature inventory", "", "Profile: " + profile, "", "| Exact version | Tier | Required depth | Saved sources |", "| --- | --- | --- | --- |"]
    for item in report["inventory"]:
        inventory.append("| " + " | ".join((item["version_id"], str(item["tier"]), str(item["required_depth"]), ", ".join(item["source_paths"]))) + " |")
    coverage = ["# Literature coverage", "", "Ready: " + str(report["ready"]), "Digest: " + report["digest"], "", report["limits"], ""]
    coverage.extend("- " + item["code"] + ": " + item["explanation"] + " " + item.get("version_id", "") for item in report["obligations"])
    coverage.extend(["", "## Detailed coverage", "", "```json", json.dumps(report, indent=2, ensure_ascii=False), "```", ""])
    paths = {}
    workspace = _Workspace(store.root)
    with workspace.directory("research/exports", create=True) as directory:
        for kind, lines in (("inventory", inventory), ("coverage", coverage)):
            name = profile + "-" + kind + ".md"
            temporary = ".export-" + uuid.uuid4().hex
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write("\n".join(lines) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
            paths[kind] = "research/exports/" + name
    return dict(paths, digest=report["digest"])
