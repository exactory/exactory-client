"""The search after a slate that selects nothing: search steps, the next step that `next` names, and the origin that
keeps in the next record what an earlier exploration learned.

A search step is the decision of what the search does next after the latest slate, recorded before its work. Its
end is computed: the first later slate or the next later step ends it, and the candidates recorded in between are
its product. No gate and no reviewer reads a search record.
"""

from pathlib import Path

from .errors import ResearchError
from .operations import fields, immutable_record, prepared_mutation, strings, text
from .problem_candidates import find_slate
from .problem_claims import find_latest, require_problem
from .problem_forming import count_forming
from .research_decisions import list_stated_objections
from .storage import Store
from . import strategy

# The five ways of a search step, in their order: from the evidence that the record holds to the way that leaves it.
WAYS = ("raise_deferred", "pose_stated_item", "study_refutation", "attack_stated_question", "next_exploration")
TEXT_CHARACTER_LIMIT = 4000
REASON_CHARACTER_LIMIT = 1000
LEAD_COUNT_LIMIT = 16
CARRIED_ROW_LIMIT = 32
EVIDENCED_STANDINGS = ("refuted", "proved", "implied_by_known_results", "found_in_literature")

NO_POST_EXPLANATION = "The latest slate selects no candidate; a new slate that selects one reopens the post path."
STEP_REQUIRED_EXPLANATION = ("Record the next search step with problem-search-step. The member next gives it. The "
                             "current slate selects no candidate, and the record holds no post.")
STEP_OPEN_EXPLANATION = ("Do the work of the open search step. Then record its candidates and a new slate, or the "
                         "next search step. The member then gives that step.")
CONTINUED_EXPLANATION = ("The search continues in another record. Run status in the workspace that "
                         "successor_workspace names. A post in that run ends the run.")
SLATE_MISMATCH_ERR_MSG = ("A search step follows the latest slate, and only when that slate selects nothing. The "
                          "value of slate_id does not name such a slate. Read slate_id and candidates in "
                          "exactory-research status. If the latest slate selects a candidate, formulate that "
                          "candidate.")
WAY_INAPPLICABLE_ERR_MSG = ("The record holds no ground for this way. The way raise_deferred needs a candidate that "
                            "the slate defers. The way study_refutation needs a form whose latest outcome has the "
                            "standing refuted. Choose a way that exactory-research next shows with applicable true.")
CANDIDATE_MISMATCH_ERR_MSG = ("The candidates in candidate_ids do not fit this way. For raise_deferred, name one or "
                              "more candidates that the slate defers. For next_exploration, send an empty list. For "
                              "each other way, name candidates of the slate, or send an empty list.")
WAY_PASSED_OVER_ERR_MSG = ("This step takes a later way than the first way that applies. Add one {way, reason} entry "
                           "to passed_over for each way that the details name. Each of these ways applies and has no "
                           "step after this slate.")
EVIDENCE_MISSING_ERR_MSG = ("A search step already follows this slate, and evidence cites no record of its work. Cite "
                            "in evidence one or more records that the work of that step made. Examples are a search, "
                            "a reading, a form outcome, a probe and an accepted result. A record that states its "
                            "revision counts only when that revision is later than the step. If that step left no "
                            "record, do its work first.")
SUCCESSOR_SELF_ERR_MSG = ("successor_workspace is the workspace of this record. Give the absolute path of the "
                          "workspace of the next record.")
WAY_MEMBERS_ERR_MSG = ("Only a step with the way next_exploration has a successor_workspace and other_leads. For each "
                       "other way, send successor_workspace as null and other_leads as an empty list.")
SUCCESSOR_PATH_ERR_MSG = ("Give successor_workspace as the absolute path of the workspace of the next record. If that "
                          "record does not exist, open it with problem-init first.")
PASSED_OVER_ENTRY_ERR_MSG = ("Each passed_over entry names one way that is earlier in the order than the way of this "
                             "step. Name each way one time.")
TEXT_LIMIT_ERR_MSG = "{name} has more characters than its limit of {limit}. Make the text shorter."
LEAD_LIMIT_ERR_MSG = "other_leads has more entries than its limit of 16. Keep the leads that are still open."
ORIGIN_SELF_ERR_MSG = ("source_workspace is the workspace of this record. Give the absolute path of the workspace of "
                       "the earlier record.")
SOURCE_UNAVAILABLE_ERR_MSG = ("The harness cannot open source_workspace as a problem record. Make sure that "
                              "source_workspace is the absolute path of the workspace of the earlier record. If that "
                              "workspace moved, give its new path.")
SOURCE_REVISION_ERR_MSG = ("source_revision is not the current revision of the source. Read the revision with "
                           "exactory-research status in the source workspace. Then run this command again with that "
                           "revision.")
ORIGIN_UNLINKED_ERR_MSG = ("The source does not name this record as the next exploration. In the source, record a "
                           "search step with the way next_exploration and this workspace as successor_workspace. Then "
                           "run this command again.")
ORIGIN_INPUT_ERR_MSG = "Give source_workspace as an absolute path and source_revision as a nonnegative integer."


def order_rows(records, kind):
    return sorted(records.get(kind, {}).values(), key=lambda row: (row.get("recorded_revision", 0), row["id"]))


def list_steps_after(records, slate):
    return [row for row in order_rows(records, "problem_search_step") if row["slate_id"] == slate["id"]]


def describe_ways(records, slate):
    """The five ways after slate, in their order, each with its ground and the steps of that way after the slate."""
    steps = list_steps_after(records, slate)
    refuted = count_forming(records)["by_standing"]["refuted"]
    origin = find_latest(records, "problem_search_origin")
    rows = []
    for way in WAYS:
        row = dict(way=way, applicable={"raise_deferred": bool(slate["deferred"]),
                                        "study_refutation": refuted > 0}.get(way, True),
                   step_ids=[step["id"] for step in steps if step["way"] == way])
        if way == "raise_deferred":
            row["candidates"] = slate["deferred"]
        elif way == "study_refutation":
            row["refuted_forms"] = refuted
        elif way == "next_exploration":
            row["leads"] = [] if origin is None else origin["source"]["other_leads"]
        rows.append(row)
    return rows


def describe_objections(records, review_ids):
    """The objections of the named reviews under their record-wide identities, with the stated id where it differs."""
    rows = []
    for identifier in review_ids:
        for objection in list_stated_objections(records["value_review"][identifier]):
            row = dict(review_id=identifier, id=objection["id"], candidate_ids=objection.get("candidate_ids", []),
                       claim=objection["claim"], resolution_condition=objection["resolution_condition"])
            if "stated_id" in objection:
                row["stated_id"] = objection["stated_id"]
            rows.append(row)
    return rows


def build_step(slate, ways):
    """The step to record: the first applicable way without a step after the slate, else next_exploration."""
    way = next((row["way"] for row in ways if row["applicable"] and not row["step_ids"]), "next_exploration")
    candidate_ids = [row["candidate_id"] for row in slate["deferred"]] if way == "raise_deferred" else []
    chooses = ["id", "learned", "evidence", "plan"]
    if way == "next_exploration":
        chooses += ["successor_workspace", "other_leads"]
    return dict(command="problem-search-step", payload=dict(
        slate_id=slate["id"], way=way, candidate_ids=candidate_ids, passed_over=[], successor_workspace=None,
        other_leads=[]), chooses=chooses)


def describe_next_step(records, slate):
    """`next` at a current slate that selects nothing."""
    if records.get("problem_post"):
        return {"code": "problem_no_post", "reason": slate["reason"], "explanation": NO_POST_EXPLANATION}
    steps = list_steps_after(records, slate)
    if not steps:
        ways = describe_ways(records, slate)
        return dict(code="problem_search_step_required", slate_id=slate["id"], reason=slate["reason"],
                    explanation=STEP_REQUIRED_EXPLANATION, deferred=slate["deferred"], rejected=slate["rejected"],
                    objections=describe_objections(records, slate["review_ids"]), ways=ways,
                    next=[build_step(slate, ways)])
    latest = steps[-1]
    if latest["way"] == "next_exploration":
        return dict(code="problem_search_continued", slate_id=slate["id"], reason=slate["reason"],
                    explanation=CONTINUED_EXPLANATION, step_id=latest["id"],
                    successor_workspace=latest["successor_workspace"])
    ways = describe_ways(records, slate)
    return dict(code="problem_search_step_open", slate_id=slate["id"], reason=slate["reason"],
                explanation=STEP_OPEN_EXPLANATION, step_id=latest["id"], way=latest["way"], plan=latest["plan"],
                ways=ways, then=[build_step(slate, ways)])


def has_left_record(records, slate):
    """Whether the latest step after slate opened the next exploration."""
    steps = list_steps_after(records, slate)
    return bool(steps) and steps[-1]["way"] == "next_exploration"


def describe_steps(records):
    """Every step in recorded order, with what ended it and the candidates recorded during it."""
    slates = order_rows(records, "problem_slate")
    steps = order_rows(records, "problem_search_step")
    candidates = order_rows(records, "problem_candidate")
    rows = []
    for index, step in enumerate(steps):
        revision = step["recorded_revision"]
        endings = [("slate", row) for row in slates if row["recorded_revision"] > revision]
        endings += [("step", row) for row in steps[index + 1:]]
        kind, end = min(endings, key=lambda pair: pair[1]["recorded_revision"], default=(None, None))
        limit = None if end is None else end["recorded_revision"]
        rows.append(dict(step, ended_by=None if end is None else dict(kind=kind, id=end["id"]),
                         produced_candidate_ids=[row["id"] for row in candidates if row["recorded_revision"] > revision
                                                 and (limit is None or row["recorded_revision"] < limit)]))
    return rows


def describe_search(records):
    """The `search` member of status and of a report, or None when the store holds no search record."""
    if not records.get("problem_search_step") and not records.get("problem_search_origin"):
        return None
    slates = order_rows(records, "problem_slate")
    stepped = {row["slate_id"] for row in records.get("problem_search_step", {}).values()}
    return dict(steps=describe_steps(records),
                # Every slate but the latest was followed by a later slate.
                slates_without_step=[row["id"] for row in slates[:-1]
                                     if not row["selected"] and row["id"] not in stepped],
                origin=find_latest(records, "problem_search_origin"))


def check_text(value, name, limit):
    text(value, name)
    if len(value) > limit:
        raise ResearchError("invalid_input", TEXT_LIMIT_ERR_MSG.format(name=name, limit=limit))


def resolve_successor(value):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ResearchError("invalid_input", SUCCESSOR_PATH_ERR_MSG)
    try:
        path = Path(value).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as error:
        raise ResearchError("invalid_input", SUCCESSOR_PATH_ERR_MSG) from error
    if not path.is_dir():
        raise ResearchError("invalid_input", SUCCESSOR_PATH_ERR_MSG)
    return path


def validate_step_shape(value):
    """The member checks of a search step; returns the resolved successor workspace or None."""
    fields(value, ("id", "slate_id", "way", "candidate_ids", "learned", "evidence", "plan", "passed_over",
                   "successor_workspace", "other_leads"))
    text(value["id"], "Search step ID")
    text(value["slate_id"], "Search step slate_id")
    strategy.choice(value["way"], WAYS, "Search step way", "invalid_input")
    strings(value["candidate_ids"], "Search step candidate_ids")
    check_text(value["learned"], "learned", TEXT_CHARACTER_LIMIT)
    check_text(value["plan"], "plan", TEXT_CHARACTER_LIMIT)
    strategy.items(value["evidence"], "Search step evidence", code="invalid_input")
    position = WAYS.index(value["way"])
    named = []
    for entry in strategy.items(value["passed_over"], "Search step passed_over", code="invalid_input"):
        fields(entry, ("way", "reason"))
        strategy.choice(entry["way"], WAYS, "Passed over way", "invalid_input")
        if WAYS.index(entry["way"]) >= position or entry["way"] in named:
            raise ResearchError("invalid_input", PASSED_OVER_ENTRY_ERR_MSG)
        named.append(entry["way"])
        check_text(entry["reason"], "reason", REASON_CHARACTER_LIMIT)
    if value["way"] != "next_exploration":
        if value["successor_workspace"] is not None or value["other_leads"] != []:
            raise ResearchError("invalid_input", WAY_MEMBERS_ERR_MSG)
        return None
    successor = resolve_successor(value["successor_workspace"])
    leads = strategy.items(value["other_leads"], "Search step other_leads", code="invalid_input")
    if len(leads) > LEAD_COUNT_LIMIT:
        raise ResearchError("invalid_input", LEAD_LIMIT_ERR_MSG)
    for entry in leads:
        fields(entry, ("field", "reason"))
        check_text(entry["field"], "field", REASON_CHARACTER_LIMIT)
        check_text(entry["reason"], "reason", REASON_CHARACTER_LIMIT)
    return successor


def admit_step_choice(records, value):
    """The rules of the slate, the way, the candidates, the ways passed over and the evidence of an earlier step."""
    slate = find_slate(records)
    if slate is None or value["slate_id"] != slate["id"] or slate["selected"]:
        raise ResearchError("problem_search_slate_mismatch", SLATE_MISMATCH_ERR_MSG,
                            dict(slate_id=None if slate is None else slate["id"],
                                 selected=[] if slate is None else slate["selected"]))
    ways = describe_ways(records, slate)
    position = WAYS.index(value["way"])
    if not ways[position]["applicable"]:
        raise ResearchError("problem_search_way_inapplicable", WAY_INAPPLICABLE_ERR_MSG, dict(way=value["way"]))
    if value["way"] == "raise_deferred":
        allowed = {row["candidate_id"] for row in slate["deferred"]}
    elif value["way"] == "next_exploration":
        allowed = set()
    else:
        allowed = set(slate["candidate_ids"])
    unfit = [identifier for identifier in value["candidate_ids"] if identifier not in allowed]
    if unfit or (value["way"] == "raise_deferred" and not value["candidate_ids"]):
        raise ResearchError("problem_search_candidate_mismatch", CANDIDATE_MISMATCH_ERR_MSG,
                            dict(candidate_ids=unfit))
    named = {entry["way"] for entry in value["passed_over"]}
    unreasoned = [row["way"] for row in ways[:position]
                  if row["applicable"] and not row["step_ids"] and row["way"] not in named]
    if unreasoned:
        raise ResearchError("problem_search_way_passed_over", WAY_PASSED_OVER_ERR_MSG, dict(ways=unreasoned))
    steps = list_steps_after(records, slate)
    if steps and not value["evidence"]:
        raise ResearchError("problem_search_evidence_missing", EVIDENCE_MISSING_ERR_MSG,
                            dict(step_id=steps[-1]["id"]))


def is_dated_before(records, reference, revision):
    """Whether reference names a record of this store that states a revision at or below revision. A source link, an
    artifact, an imported record and a record that states no revision carry no date in the store."""
    if reference.get("kind") != "record" or "origin" in reference:
        return False
    from .output_views import _read_revision
    stated = _read_revision(records[reference["record_kind"]][reference["id"]])
    return stated is not None and stated <= revision


def require_new_evidence(records, value):
    """A step that follows another step after the same slate cites a record of the work after that step."""
    steps = list_steps_after(records, find_slate(records))
    if steps and all(is_dated_before(records, reference, steps[-1]["recorded_revision"])
                     for reference in value["evidence"]):
        raise ResearchError("problem_search_evidence_missing", EVIDENCE_MISSING_ERR_MSG,
                            dict(step_id=steps[-1]["id"]))


def record_search_step(store, payload, *, expected_revision, request_id):
    artifacts = store.build_artifact_store()

    def prepare(records, value):
        require_problem(records)
        successor = validate_step_shape(value)
        admit_step_choice(records, value)
        strategy.evidence(records, artifacts, value["evidence"], required=False)
        require_new_evidence(records, value)
        if successor is not None and successor == store.root.resolve():
            raise ResearchError("problem_search_successor_self", SUCCESSOR_SELF_ERR_MSG)
        record = dict(value, successor_workspace=None if successor is None else str(successor),
                      recorded_revision=expected_revision + 1)
        return [immutable_record(records, "problem_search_step", value["id"], record)], record

    return prepared_mutation(store, "problem.search_step", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def keep_latest(rows):
    """The latest rows within the carried limit, and the number of the rows left out."""
    return rows[-CARRIED_ROW_LIMIT:], max(0, len(rows) - CARRIED_ROW_LIMIT)


def describe_forms_with_evidence(records):
    """The forms whose latest outcome has an evidenced standing, in the order of their forming calls."""
    latest = {row["form_id"]: row for row in order_rows(records, "form_outcome") if row["target"]["kind"] == "form"}
    return [dict(id=form["id"], text=form["text"], standing=latest[form["id"]]["standing"])
            for call in order_rows(records, "forming_call") for form in call["forms"]
            if form["id"] in latest and latest[form["id"]]["standing"] in EVIDENCED_STANDINGS]


def describe_exploration(records, successor_step):
    """The `source` of an origin: what the exploration of records learned, with each list cut to its latest rows."""
    marker = records["workspace"]["problem"]
    slate = find_slate(records)
    candidates = []
    for status, detail in (("deferred", "evidence_needed"), ("rejected", "reason")):
        for row in slate[status]:
            candidate = records["problem_candidate"][row["candidate_id"]]
            candidates.append({"id": candidate["id"], "statement": candidate["statement"],
                               "source_kind": candidate["source"]["kind"], "disposition": status, detail: row[detail]})
    # Each list is in recorded order before the cut, so that the cut keeps the latest rows.
    candidates.sort(key=lambda row: (records["problem_candidate"][row["id"]]["recorded_revision"], row["id"]))
    review_ids = sorted(slate["review_ids"], key=lambda identifier: records["value_review"][identifier]["recorded_revision"])
    lists = dict(candidates=candidates, objections=describe_objections(records, review_ids),
                 forms=describe_forms_with_evidence(records),
                 steps=[{key: row[key] for key in ("id", "slate_id", "way", "candidate_ids", "learned", "plan")}
                        for row in order_rows(records, "problem_search_step")])
    kept, omitted = {}, {}
    for name, rows in lists.items():
        kept[name], omitted[name] = keep_latest(rows)
    report = find_latest(records, "problem_report")
    return dict(area=marker["area"], field=marker["field"],
                slate={key: slate[key] for key in ("id", "reason", "comparison", "review_ids")},
                candidates=kept["candidates"], objections=kept["objections"], forms=kept["forms"], steps=kept["steps"],
                other_leads=successor_step["other_leads"],
                report=None if report is None else {key: report[key] for key in ("id", "outcome", "reopen_when")},
                omitted=omitted)


def describe_earlier(records):
    """The rows of the explorations before the source of records, the oldest first, and the number left out."""
    origin = find_latest(records, "problem_search_origin")
    if origin is None:
        return [], 0
    source = origin["source"]
    rows = origin["earlier"] + [dict(workspace=origin["source_workspace"], revision=origin["source_revision"],
                                     area=source["area"], field=source["field"],
                                     ways=[step["way"] for step in source["steps"]],
                                     other_leads=source["other_leads"], report=source["report"])]
    kept, omitted = keep_latest(rows)
    return kept, origin["earlier_omitted"] + omitted


def open_source(path):
    try:
        snapshot = Store(path).snapshot()
        require_problem(snapshot["records"])
    except (OSError, RuntimeError, ResearchError) as error:
        if isinstance(error, ResearchError) and error.code == "store_busy":
            raise
        cause = getattr(error, "code", type(error).__name__)
        raise ResearchError("problem_search_source_unavailable", SOURCE_UNAVAILABLE_ERR_MSG,
                            dict(source_workspace=str(path), cause=cause)) from error
    return snapshot


def record_search_origin(store, payload, *, expected_revision, request_id):
    def prepare(records, value):
        require_problem(records)
        fields(value, ("id", "source_workspace", "source_revision"))
        text(value["id"], "Search origin ID")
        if (not isinstance(value["source_workspace"], str) or not Path(value["source_workspace"]).is_absolute()
                or type(value["source_revision"]) is not int or value["source_revision"] < 0):
            raise ResearchError("invalid_input", ORIGIN_INPUT_ERR_MSG)
        workspace = store.root.resolve()
        try:
            path = Path(value["source_workspace"]).resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as error:
            raise ResearchError("problem_search_source_unavailable", SOURCE_UNAVAILABLE_ERR_MSG,
                                dict(source_workspace=value["source_workspace"], cause=type(error).__name__)) from error
        if path == workspace:
            raise ResearchError("problem_search_origin_self", ORIGIN_SELF_ERR_MSG)
        snapshot = open_source(path)
        if snapshot["revision"] != value["source_revision"]:
            raise ResearchError("problem_search_source_revision_mismatch", SOURCE_REVISION_ERR_MSG,
                                dict(source_revision=snapshot["revision"],
                                     expected_source_revision=value["source_revision"]))
        foreign = snapshot["records"]
        slate = find_slate(foreign)
        steps = [] if slate is None or slate["selected"] else list_steps_after(foreign, slate)
        named = steps[-1] if steps and steps[-1]["way"] == "next_exploration" else None
        if named is None or named["successor_workspace"] != str(workspace):
            raise ResearchError("problem_search_origin_unlinked", ORIGIN_UNLINKED_ERR_MSG,
                                dict(source_workspace=str(path),
                                     successor_workspace=None if named is None else named["successor_workspace"]))
        earlier, earlier_omitted = describe_earlier(foreign)
        record = dict(value, source_workspace=str(path), source=describe_exploration(foreign, named), earlier=earlier,
                      earlier_omitted=earlier_omitted, recorded_revision=expected_revision + 1)
        return [immutable_record(records, "problem_search_origin", value["id"], record)], record

    return prepared_mutation(store, "problem.search_origin", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)
