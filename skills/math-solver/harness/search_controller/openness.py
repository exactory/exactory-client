"""Mechanical openness minimum over exact common search and reading records."""

from . import schema as s


def validate_minimum(value):
    s.closed(value, "schema_version statement neighbours searches reading_digests settling_work_id")
    s.integer(value["schema_version"], 1, 1)
    s.text(value["statement"])
    s.strings(value["neighbours"])
    s.require(value["statement"] not in value["neighbours"], "Declare distinct neighbouring formulations")
    s.strings(value["reading_digests"])
    for identity in value["reading_digests"]:
        s.digest_string(identity)
    s.optional_text(value["settling_work_id"])
    for item in s.records(value["searches"]):
        s.closed(item, "formulation search_digest")
        s.text(item["formulation"])
        s.digest_string(item["search_digest"])


def check_search_coverage(search):
    from research_harness.literature import find_search_integrity_errors
    coverage = search.get("coverage", [])
    return bool(coverage and {row["query"] for row in coverage} == set(search["queries"])
                and all(row.get("kind") in {"complete", "top_k", "bounded_unordered"}
                        and type(row.get("inspected_count")) is int for row in coverage)
                and not find_search_integrity_errors(search) and not search.get("gaps"))


def find_reassessment(search, formulation, selected, *, work_id=None):
    """Retain historical gaps while checking their explicit, pinned resolution."""
    from research_harness.operations import timestamp
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


def audit_minimum(finding, claim, records, artifacts, delivered):
    """Require captured searches and full readings, independently of review prose."""
    from research_harness.operations import timestamp
    from research_harness.reading import fulltext_coverage
    from research_harness.source_deferrals import assess_deferrals

    minimum = finding.get("minimum")
    s.require(minimum is not None, "Resolved openness requires its pinned common search/read minimum", "openness_minimum_required")
    validate_minimum(minimum)
    s.require(minimum["statement"] == claim["statement"], "Openness minimum names a different exact statement", "openness_statement_mismatch")
    formulations = {minimum["statement"], *minimum["neighbours"]}
    covered, required = set(), set()
    searches = {s.digest(row): row for row in records.get("literature_search", {}).values()}
    bound = set(finding["evidence"])
    selected = {row["search_digest"] for row in minimum["searches"]}
    selected_records = [(item, searches[item["search_digest"]]) for item in minimum["searches"]
                        if item["search_digest"] in searches]
    for identity, search in searches.items():
        if not (search.get("question") in formulations or formulations.intersection(search["queries"])):
            continue
        material = search.get("gaps") or any(row["disposition"] in {
            "relevant", "contradictory", "potentially_relevant", "unresolved"} for row in search["dispositions"])
        s.require(not material or identity in selected,
                  "Reassess material search evidence for the declared formulations", "openness_search_stale")
    pinned_readings = {identity: row for identity, row in records.get("reading", {}).items()
                       if s.digest(row) in minimum["reading_digests"]}
    s.require({s.digest(row) for row in pinned_readings.values()} == set(minimum["reading_digests"]),
              "Openness readings must be exact records in the associated common store", "openness_evidence_mismatch")
    for item in minimum["searches"]:
        identity, formulation = item["search_digest"], item["formulation"]
        s.require(formulation in formulations and identity in searches,
                  "Bind each search to a declared formulation and its common record", "openness_search_missing")
        search = searches[identity]
        s.require(search.get("question") == formulation or formulation in search["queries"],
                  "The recorded search does not address this formulation", "openness_statement_mismatch")
        s.require(identity in bound and identity in delivered,
                  "The finding and observed packet must bind the exact search record", "openness_evidence_mismatch")
        s.require(check_search_coverage(search) or find_reassessment(search, formulation, selected_records) is not None,
                  "Search coverage or integrity leaves an openness gap", "openness_search_gap")
        captured = timestamp(search["captured_at"])
        s.require(captured.date().isoformat() <= finding["searched_at"],
                  "The finding predates its captured searches", "openness_search_date")
        for row in search["dispositions"]:
            if row["disposition"] in {"unresolved", "potentially_relevant"}:
                s.require(find_reassessment(search, formulation, selected_records, work_id=row["work_id"]) is not None,
                          "Unresolved search results leave an openness gap", "openness_reading_gap")
                required.add(row["work_id"])
        required.update(row["work_id"] for row in search["dispositions"]
                        if row["disposition"] in {"relevant", "contradictory"})
        covered.add(formulation)
    s.require(covered == formulations, "Search every declared neighbouring formulation and the exact statement", "openness_search_missing")
    s.require(set(minimum["reading_digests"]) <= bound & delivered,
              "The finding and observed packet must bind every full reading", "openness_evidence_mismatch")
    pinned = dict(records, reading=pinned_readings)
    deferred = {row["version_id"] for row in assess_deferrals(records, artifacts)
                if row["status"] in {"active", "stale"}}
    for work in sorted(required):
        s.require(work not in deferred and fulltext_coverage(pinned, artifacts, work)["complete"],
                  "Every relevant, contradictory or settling source needs its pinned full reading; unavailable or deferred sources remain gaps",
                  "openness_reading_gap")
    settling = minimum["settling_work_id"]
    if finding["outcome"] == "already_solved":
        s.require(settling is not None and settling in required,
                  "Already solved requires the fully read settling work from a bound search", "openness_settling_source")
    elif finding["outcome"] == "supported_open":
        s.require(settling is None, "A declared settling source cannot support an open finding", "openness_settling_source")
