"""Mechanical openness minimum over exact common search and reading records."""

import re

from .errors import ResearchError
from .evidence import digest


class OpennessError(ResearchError):
    """A minimum refusal, distinct from failures of the common evidence store."""


def _require(condition, message, code="invalid_record"):
    if not condition:
        raise OpennessError(code, message)


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


def audit_minimum(finding, statement, records, artifacts, delivered):
    """Require captured searches and full readings, independently of review prose."""
    from .operations import timestamp
    from .reading import fulltext_coverage
    from .source_deferrals import assess_deferrals

    minimum = finding.get("minimum")
    _require(minimum is not None, "Resolved openness requires its pinned common search/read minimum", "openness_minimum_required")
    validate_minimum(minimum)
    _require(minimum["statement"] == statement, "Openness minimum names a different exact statement", "openness_statement_mismatch")
    formulations = {minimum["statement"], *minimum["neighbours"]}
    covered, required, duplicates = set(), set(), set()
    searches = {digest(row): row for row in records.get("literature_search", {}).values()}
    bound = set(finding["evidence"])
    selected = {row["search_digest"] for row in minimum["searches"]}
    selected_records = [(item, searches[item["search_digest"]]) for item in minimum["searches"]
                        if item["search_digest"] in searches]
    for identity, search in searches.items():
        if not (search.get("question") in formulations or formulations.intersection(search["queries"])):
            continue
        material = search.get("gaps") or any(row["disposition"] in {
            "relevant", "contradictory", "potentially_relevant", "unresolved", "duplicate"} for row in search["dispositions"])
        _require(not material or identity in selected,
                  "Reassess material search evidence for the declared formulations", "openness_search_stale")
    pinned_readings = {identity: row for identity, row in records.get("reading", {}).items()
                       if digest(row) in minimum["reading_digests"]}
    _require({digest(row) for row in pinned_readings.values()} == set(minimum["reading_digests"]),
              "Openness readings must be exact records in the associated common store", "openness_evidence_mismatch")
    for item in minimum["searches"]:
        identity, formulation = item["search_digest"], item["formulation"]
        _require(formulation in formulations and identity in searches,
                  "Bind each search to a declared formulation and its common record", "openness_search_missing")
        search = searches[identity]
        _require(search.get("question") == formulation or formulation in search["queries"],
                  "The recorded search does not address this formulation", "openness_statement_mismatch")
        _require(identity in bound and identity in delivered,
                  "The finding and observed packet must bind the exact search record", "openness_evidence_mismatch")
        _require(check_search_coverage(search) or find_reassessment(search, formulation, selected_records) is not None,
                  "Search coverage or integrity leaves an openness gap", "openness_search_gap")
        captured = timestamp(search["captured_at"])
        _require(captured.date().isoformat() <= finding["searched_at"],
                  "The finding predates its captured searches", "openness_search_date")
        for row in search["dispositions"]:
            if row["disposition"] in {"unresolved", "potentially_relevant"}:
                _require(find_reassessment(search, formulation, selected_records, work_id=row["work_id"]) is not None,
                          "Unresolved search results leave an openness gap", "openness_reading_gap")
                # The resolving search supplies the obligation: out_of_scope
                # removes it; duplicate transfers it to its decisive source.
            elif row["disposition"] == "duplicate":
                duplicates.add((identity, row["work_id"]))
        required.update(row["work_id"] for row in search["dispositions"]
                        if row["disposition"] in {"relevant", "contradictory"})
        covered.add(formulation)
    resolutions = {(row["search_digest"], row["work_id"]): row["canonical_work_id"]
                   for row in minimum.get("duplicate_resolutions", [])}
    _require(set(resolutions) == duplicates,
              "Bind every duplicate to its decisive canonical source in the exact search", "openness_reading_gap")
    for (identity, work), canonical in resolutions.items():
        _require(any(row["work_id"] == canonical and row["disposition"] in {"relevant", "contradictory"}
                      for row in searches[identity]["dispositions"]),
                  "A duplicate's canonical source must be decisive in the bound search", "openness_reading_gap")
        required.add(canonical)
    _require(covered == formulations, "Search every declared neighbouring formulation and the exact statement", "openness_search_missing")
    _require(set(minimum["reading_digests"]) <= bound & delivered,
              "The finding and observed packet must bind every full reading", "openness_evidence_mismatch")
    pinned = dict(records, reading=pinned_readings)
    deferred = {row["version_id"] for row in assess_deferrals(records, artifacts)
                if row["status"] in {"active", "stale"}}
    for work in sorted(required):
        _require(work not in deferred and fulltext_coverage(pinned, artifacts, work)["complete"],
                  "Every relevant, contradictory or settling source needs its pinned full reading; unavailable or deferred sources remain gaps",
                  "openness_reading_gap")
    settling = minimum["settling_work_id"]
    if finding["outcome"] == "already_solved":
        _require(settling is not None and settling in required,
                  "Already solved requires the fully read settling work from a bound search", "openness_settling_source")
    elif finding["outcome"] == "supported_open":
        _require(settling is None, "A declared settling source cannot support an open finding", "openness_settling_source")
