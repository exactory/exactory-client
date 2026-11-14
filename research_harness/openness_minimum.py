"""Mechanical openness minimum over exact common search and reading records."""

import re

from .errors import ResearchError
from .evidence import digest


class OpennessError(ResearchError):
    """A minimum refusal, distinct from failures of the common evidence store."""


def _require(condition, message, code="invalid_record", details=None):
    if not condition:
        raise OpennessError(code, message, details)


def _require_closed_fields(value, fields):
    _require(isinstance(value, dict) and set(value) == set(fields.split()),
             "Record must contain exactly: " + fields)


def _require_text(value):
    _require(isinstance(value, str) and bool(value.strip()), "Expected nonempty text")


def _require_integer(value, minimum=0, maximum=None):
    _require(type(value) is int and value >= minimum and (maximum is None or value <= maximum),
             "Integer is outside the permitted range")


def _require_strings(value):
    _require(isinstance(value, list), "Expected a list")
    for item in value:
        _require_text(item)
    _require(len(value) == len(set(value)), "Duplicate list values")


def _require_record_list(value):
    _require(isinstance(value, list), "Expected a record list")
    return value


def _require_digest(value):
    _require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
             "Expected a SHA-256 digest")


def _require_optional_text(value):
    if value is not None:
        _require_text(value)


def validate_minimum(value):
    _require_closed_fields(value, "schema_version statement neighbours searches reading_digests settling_work_id"
             + (" duplicate_resolutions" if isinstance(value, dict) and "duplicate_resolutions" in value else ""))
    _require_integer(value["schema_version"], 1, 1)
    _require_text(value["statement"])
    _require_strings(value["neighbours"])
    _require(value["statement"] not in value["neighbours"], "Declare distinct neighbouring formulations")
    _require_strings(value["reading_digests"])
    for identity in value["reading_digests"]:
        _require_digest(identity)
    _require_optional_text(value["settling_work_id"])
    for item in _require_record_list(value["searches"]):
        _require_closed_fields(item, "formulation search_digest")
        _require_text(item["formulation"])
        _require_digest(item["search_digest"])
    seen = set()
    for item in _require_record_list(value.get("duplicate_resolutions", [])):
        _require_closed_fields(item, "search_digest work_id canonical_work_id")
        _require_digest(item["search_digest"])
        _require_text(item["work_id"])
        _require_text(item["canonical_work_id"])
        key = (item["search_digest"], item["work_id"])
        _require(key not in seen and item["work_id"] != item["canonical_work_id"],
                  "Each duplicate needs one distinct canonical source")
        seen.add(key)


def check_search_coverage(search):
    from .literature import find_search_integrity_errors
    coverage = search.get("coverage", [])
    return bool(coverage and {row["query"] for row in coverage} == set(search["queries"])
                and all(row.get("kind") in {"complete", "top_k", "bounded_unordered"}
                        and type(row.get("inspected_count")) is int for row in coverage)
                and not find_search_integrity_errors(search) and not search.get("gaps"))


def find_material_searches(records, formulations):
    """Find searches whose judgments can change the declared openness claim."""
    return [search for search in records.get("literature_search", {}).values()
            if (search.get("question") in formulations or set(formulations).intersection(search["queries"]))
            and (search.get("gaps") or any(row["disposition"] in {
                "relevant", "contradictory", "potentially_relevant", "unresolved", "duplicate"}
                for row in search["dispositions"]))]


def find_reassessment(search, formulation, selected, *, work_id=None):
    """Retain historical gaps while checking their explicit, pinned resolution."""
    from .operations import timestamp
    for item, candidate in selected:
        if (item["formulation"] != formulation or candidate == search
                or timestamp(candidate["captured_at"]) < timestamp(search["captured_at"])
                or not set(search["queries"]) <= set(candidate["queries"])
                or not check_search_coverage(candidate)):
            continue
        if work_id is None or (work_id in {row["work_id"] for row in candidate.get("resolved", [])}
                               and any(row["work_id"] == work_id and row["disposition"] in {
                                   "relevant", "contradictory", "out_of_scope", "duplicate"}
                                       for row in candidate["dispositions"])):
            return candidate
    return None


def validate_minimum_bindings(minimum, statement, records):
    """Require a minimum whose declaration is true of this exact statement and these common records.

    It names the statement, binds every material search of its formulations, names only existing readings, binds each
    search to a declared formulation that the search addresses, and binds every declared formulation. These checks read
    only the minimum and the immutable common records, so an export makes them before it writes a packet, and
    audit_minimum makes them first.
    """
    validate_minimum(minimum)
    _require(minimum["statement"] == statement, "Openness minimum names a different exact statement",
             "openness_statement_mismatch")
    formulations = {minimum["statement"], *minimum["neighbours"]}
    bound = {item["search_digest"] for item in minimum["searches"]}
    unbound = sorted(search["id"] for search in find_material_searches(records, formulations)
                     if digest(search) not in bound)
    _require(not unbound, "Bind each search in search_ids to the declared formulation it addresses: a search with a "
             "relevant, contradictory, potentially_relevant, unresolved or duplicate result, or with gaps, can change "
             "the openness claim", "openness_search_stale", {"search_ids": unbound})
    unknown = sorted(set(minimum["reading_digests"]) - {digest(row) for row in records.get("reading", {}).values()})
    _require(not unknown, "Openness readings must be exact records in the associated common store",
             "openness_evidence_mismatch", {"reading_digests": unknown})
    searches = {digest(row): row for row in records.get("literature_search", {}).values()}
    for item in minimum["searches"]:
        formulation, search = item["formulation"], searches.get(item["search_digest"])
        _require(search is not None, "This binding's search_digest names no literature_search record of the common "
                 "store: bind the digest of a search recorded there",
                 "openness_search_missing", {"formulation": formulation, "search_digest": item["search_digest"]})
        details = {"formulation": formulation, "search_id": search["id"], "search_digest": item["search_digest"]}
        _require(formulation in formulations,
                 "This binding's formulation is neither the exact statement nor a declared neighbour: declare it in "
                 "neighbours or remove the binding", "openness_search_missing", details)
        _require(search.get("question") == formulation or formulation in search["queries"],
                 "The bound search does not address this formulation: record or bind a search whose question is the "
                 "exact formulation text, or whose queries contain that text", "openness_statement_mismatch", details)
    unsearched = sorted(formulations.difference(item["formulation"] for item in minimum["searches"]))
    _require(not unsearched, "Bind a search that addresses each formulation in formulations: its question is that "
             "exact text, or its queries contain it", "openness_search_missing", {"formulations": unsearched})


def audit_minimum(finding, statement, records, artifacts, delivered):
    """Require captured searches and full readings, independently of review prose."""
    from .operations import timestamp

    minimum = finding.get("minimum")
    _require(minimum is not None, "Resolved openness requires its pinned common search/read minimum", "openness_minimum_required")
    validate_minimum_bindings(minimum, statement, records)
    gaps = list_minimum_gaps(minimum, statement, records, artifacts)
    searches = {digest(row): row for row in records.get("literature_search", {}).values()}
    bound = set(finding["evidence"])
    selected_records = [(item, searches[item["search_digest"]]) for item in minimum["searches"]]
    for item, search in selected_records:
        identity = item["search_digest"]
        _require(identity in bound and identity in delivered,
                  "The finding and observed packet must bind the exact search record", "openness_evidence_mismatch")
        require_unblocked_outcome(gaps, finding["outcome"],
                                  {"search_integrity", "search_declared_gap", "search_coverage_unspecified"}, item)
        captured = timestamp(search["captured_at"])
        _require(captured.date().isoformat() <= finding["searched_at"],
                  "The finding predates its captured searches", "openness_search_date")
        require_unblocked_outcome(gaps, finding["outcome"], {"result_uncertain"}, item)
    require_unblocked_outcome(gaps, finding["outcome"], {"duplicate_unbound", "duplicate_source_not_decisive"})
    _require(set(minimum["reading_digests"]) <= bound & delivered,
              "The finding and observed packet must bind every full reading", "openness_evidence_mismatch")
    require_unblocked_outcome(gaps, finding["outcome"], {"reading_missing", "reading_deferred"})
    require_unblocked_outcome(gaps, finding["outcome"],
                              {"settling_undeclared", "settling_not_decisive", "settling_declared"})


# Keep the historic audit messages and their codes in one place. The two
# settling messages differ by outcome for a nondecisive declared source.
GAP_REFUSALS = {
    "search_integrity": ("openness_search_gap", "Search coverage or integrity leaves an openness gap"),
    "search_declared_gap": ("openness_search_gap", "Search coverage or integrity leaves an openness gap"),
    "search_coverage_unspecified": ("openness_search_gap", "Search coverage or integrity leaves an openness gap"),
    "result_uncertain": ("openness_reading_gap", "Unresolved search results leave an openness gap"),
    "duplicate_unbound": ("openness_reading_gap", "Bind every duplicate to its decisive canonical source in the exact search"),
    "duplicate_source_not_decisive": ("openness_reading_gap", "A duplicate's canonical source must be decisive in the bound search"),
    "reading_missing": ("openness_reading_gap", "Every relevant, contradictory or settling source needs its pinned full reading; unavailable or deferred sources remain gaps"),
    "settling_undeclared": ("openness_settling_source", "Already solved requires the fully read settling work from a bound search"),
    "settling_declared": ("openness_settling_source", "A declared settling source cannot support an open finding"),
}


def require_unblocked_outcome(gaps, outcome, conditions, binding=None):
    """Apply record-side gaps at the original positions among answer checks."""
    for gap in gaps:
        if (gap["condition"] not in conditions or outcome not in gap["outcomes"]
                or binding is not None and (gap["search_digest"] != binding["search_digest"]
                                            or gap["formulation"] != binding["formulation"])):
            continue
        error = gap["details"].get("assessment_error")
        if error is not None:
            raise ResearchError(error["code"], error["message"], error.get("details"))
        condition = gap["condition"]
        if condition == "reading_deferred":
            condition = "reading_missing"
        elif condition == "settling_not_decisive":
            condition = "settling_undeclared" if outcome == "already_solved" else "settling_declared"
        code, message = GAP_REFUSALS[condition]
        raise OpennessError(code, message)


def list_minimum_gaps(minimum, statement, records, artifacts):
    """List all record-side barriers after validate_minimum_bindings succeeds.

    Version 1 keeps exact-work readings and the historical order of conditions.
    A gap's position is stable for these pinned inputs; answer-dependent evidence
    and date checks remain in audit_minimum.
    """
    from .literature import find_search_integrity_errors
    from .evaluation import Evaluation
    from .reading import fulltext_coverage
    from .source_deferrals import assess_deferrals

    artifacts = Evaluation.of(records, artifacts)
    searches = {digest(row): row for row in records.get("literature_search", {}).values()}
    selected = [(item, searches[item["search_digest"]]) for item in minimum["searches"]]
    gaps, required, duplicates = [], {}, {}
    both = ["supported_open", "already_solved"]

    def add_gap(condition, binding=None, search=None, work_id=None, outcomes=None, **details):
        code = ("openness_settling_source" if condition == "settling_not_decisive" else
                GAP_REFUSALS["reading_missing" if condition == "reading_deferred" else condition][0])
        entry = {"condition": condition, "code": code, "outcomes": both if outcomes is None else outcomes,
                 "formulation": statement if binding is None else binding["formulation"],
                 "search_id": None if search is None else search["id"],
                 "search_digest": None if binding is None else binding["search_digest"], "details": details}
        if work_id is not None:
            entry["work_id"] = work_id
        if "assessment_error" in details:
            entry["code"] = details["assessment_error"]["code"]
        gaps.append(entry)

    for item, search in selected:
        if find_reassessment(search, item["formulation"], selected) is None:
            errors = find_search_integrity_errors(search)
            if errors:
                add_gap("search_integrity", item, search, command="search",
                        integrity_errors=list_search_items(search, errors, count_items=True))
            if search.get("gaps"):
                add_gap("search_declared_gap", item, search, command="search", gaps=search["gaps"])
            coverage = search.get("coverage", [])
            missing = [query for query in search["queries"] if not any(row["query"] == query for row in coverage)]
            invalid = [row for row in coverage if row["query"] not in search["queries"]
                       or row.get("kind") not in {"complete", "top_k", "bounded_unordered"}
                       or type(row.get("inspected_count")) is not int]
            if missing or invalid or not coverage:
                add_gap("search_coverage_unspecified", item, search, command="search",
                        queries=missing, coverage=invalid)
        for row in search["dispositions"]:
            work = row["work_id"]
            if (row["disposition"] in {"unresolved", "potentially_relevant"}
                    and find_reassessment(search, item["formulation"], selected, work_id=work) is None):
                add_gap("result_uncertain", item, search, work, command="search", disposition=row["disposition"])
            elif row["disposition"] == "duplicate":
                duplicates[(item["search_digest"], work)] = (item, search)
            if row["disposition"] in {"relevant", "contradictory"}:
                required.setdefault(work, (item, search))
    resolutions = {(row["search_digest"], row["work_id"]): row["canonical_work_id"]
                   for row in minimum.get("duplicate_resolutions", [])}
    for key, (item, search) in duplicates.items():
        if key not in resolutions:
            add_gap("duplicate_unbound", item, search, key[1], command="export")
    for key, canonical in resolutions.items():
        identity, work = key
        search = searches.get(identity)
        item = next((item for item, _ in selected if item["search_digest"] == identity),
                    {"formulation": statement, "search_digest": identity})
        if key not in duplicates:
            add_gap("duplicate_unbound", item, search, work, command="export", canonical_work_id=canonical)
    for (identity, work), canonical in resolutions.items():
        search = searches.get(identity)
        item = next((item for item, _ in selected if item["search_digest"] == identity),
                    {"formulation": statement, "search_digest": identity})
        if search is None or not any(row["work_id"] == canonical and row["disposition"] in {"relevant", "contradictory"}
                                     for row in search["dispositions"]):
            add_gap("duplicate_source_not_decisive", item, search, work, command="export", canonical_work_id=canonical)
        else:
            required.setdefault(canonical, (item, search))
    pinned = dict(records, reading={key: row for key, row in records.get("reading", {}).items()
                                   if digest(row) in minimum["reading_digests"]})
    pinned_artifacts = Evaluation.of(pinned, artifacts)
    try:
        deferred = {row["version_id"]: row for row in assess_deferrals(records, artifacts)
                    if row["status"] in {"active", "stale"}}
    except ResearchError as error:
        # Retain the store's error at the historic reading-check position. An
        # unresolved finding can still report it without failing status/next.
        add_gap("reading_deferred", command="resume-source", assessment_error=error.as_dict())
        deferred = {}
    for work in sorted(required):
        item, search = required[work]
        if work in deferred:
            add_gap("reading_deferred", item, search, work, command="resume-source", status=deferred[work]["status"])
        try:
            coverage = fulltext_coverage(pinned, pinned_artifacts, work)
        except ResearchError as error:
            add_gap("reading_missing", item, search, work, command="reading", assessment_error=error.as_dict())
            continue
        if not coverage["complete"]:
            add_gap("reading_missing", item, search, work, command="reading", missing=coverage["missing"])
    settling = minimum["settling_work_id"]
    if settling is None:
        add_gap("settling_undeclared", outcomes=["already_solved"])
    elif settling not in required:
        add_gap("settling_not_decisive", work_id=settling, command="search")
    else:
        item, search = required[settling]
        add_gap("settling_declared", item, search, settling, outcomes=["supported_open"])
    return gaps


def forecast_outcomes(gaps):
    """Forecast resolved outcomes independently of a reviewer's own judgment."""
    return {outcome: "blocked" if any(outcome in gap["outcomes"] for gap in gaps) else "possible"
            for outcome in ("supported_open", "already_solved")}


def list_search_items(search, items, *, count_items=False):
    """Attach the recorded query to each parser, integrity or enumeration item."""
    groups = {row["id"]: row["query"] for row in search.get("page_groups", [])}
    sources = {row["source_id"]: row["query"] for row in search.get("responses", [])}
    return [dict(item, query=item.get("query", groups.get(item.get("group_id"), sources.get(item.get("source_id")))),
                 **({"count": item.get("count", 1)} if count_items else {})) for item in items]


def list_minimum_notices(minimum, records, artifacts):
    """Report coverage and W7 notices without promoting bounded results to gaps."""
    from .literature import build_search_receipt
    from .evaluation import Evaluation
    from .search_pages import native_page

    artifacts = Evaluation.of(records, artifacts)
    searches = {digest(row): row for row in records.get("literature_search", {}).values()}
    notices = []
    for binding in minimum["searches"]:
        search = searches[binding["search_digest"]]
        pages = []
        for response in search.get("responses", []):
            source = records.get("source", {}).get(response["source_id"])
            if source is not None and source["provider"] in {"arxiv", "openalex", "crossref"}:
                page = native_page(source, artifacts.read(source["response"]), response["query"], search["scope"],
                                   parse_version=search.get("native_parse_version", 1))
                pages.append(dict(page, captured_at=source["captured_at"]))
        receipt = build_search_receipt(search, search["found_work_ids"], search.get("pending", []),
                                       search.get("page_groups", []), pages)
        notices.append(dict(binding, search_id=search["id"], coverage=search.get("coverage", []),
                            incomplete_responses=receipt["incomplete_responses"],
                            excluded_item_count=receipt["excluded_item_count"], excluded_items=receipt["excluded_items"],
                            warnings=receipt["warnings"]))
    return notices


def find_searched_through(minimum, records):
    """Return the recorded date of the latest captured search."""
    from .operations import timestamp
    searches = {digest(row): row for row in records.get("literature_search", {}).values()}
    return max(timestamp(searches[binding["search_digest"]]["captured_at"])
               for binding in minimum["searches"]).date().isoformat()
