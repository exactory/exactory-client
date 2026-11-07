"""Harness-observed stops and bounded automatic continuations.

There is no command for authoring a stop. The series driver supplies its own
terminal observation; the automatic-pass command accepts only its identity.
"""

import json

from .errors import ResearchError
from .evidence import digest
from .operations import fields, immutable_record, prepared_mutation, text
from .stop_classes import classify_stop


def _scope(records, artifacts, assignment_id):
    """Bind only supported internal study hops to the actual review material."""
    from .publication_scope import has_publication_scope
    study = records.get("workspace", {}).get("study")
    assignment = records.get("review_assignment", {}).get(assignment_id, {})
    if study is None:
        return None
    source = study["stage"]
    if source == "experiment" and assignment.get("role") == "result":
        packet = json.loads(artifacts.read(assignment["packet"]))
        target = packet.get("support_contract", {}).get("candidate_digest")
        gate = "manuscript-readiness" if has_publication_scope(records) else "readiness"
        destination = "write"
    else:
        return None
    if not isinstance(target, str):
        return None
    return dict(gate=gate, transition={"from": source, "to": destination}, target_digest=target)


def record_series_stop(store, plan, *, repeated=False):
    """Retain a terminal observation at the driver's current store revision."""
    snapshot = store.snapshot()
    identifier = "series-stop:" + digest([snapshot["revision"], plan])
    request_id = identifier
    code = plan["code"]
    retry = None
    if code == "review_series_no_progress":
        if not repeated:
            raise ResearchError("gate_override_retry_pending", "Need a repeated action at unchanged progress.")
        retry = dict(policy="one_execution_per_progress_position", executions=1, exhausted=True,
                     **{key: plan["details"][key] for key in
                        ("action_state", "action_digest", "progress_digest", "successful_request_id")})
    row = dict(id=identifier, origin="harness", code=code, assignment_id=plan["head_id"],
               plan=plan, retry=retry, request_id=request_id, recorded_revision=snapshot["revision"] + 1)
    row["scope"] = _scope(snapshot["records"], store.build_artifact_store(), plan["head_id"])

    def prepare(records, value):
        return [immutable_record(records, "review_series_stop", identifier, row)], row

    return prepared_mutation(store, "review.series_stop", {"stop": row}, prepare,
                             expected_revision=snapshot["revision"], request_id=request_id)["result"]


def _refuse(code, needed, **details):
    raise ResearchError("gate_override_" + code, needed, details or None)


def _trusted_stop(store, records, identifier):
    from .storage import Store
    stop = records.get("review_series_stop", {}).get(identifier)
    if stop is None:
        _refuse("stop_missing", "Need the identity of a recorded review-series stop.")
    # Check the authoritative event receipt, not an agent-supplied origin field.
    # This also works in --check, whose staged store intentionally exposes only
    # its own request. This second reader writes nothing; commit still uses CAS.
    receipt = Store(store.root).committed_request(stop.get("request_id", identifier))
    if (stop.get("origin") != "harness" or receipt is None or receipt["operation"] != "review.series_stop"
            or receipt["payload"] != {"stop": stop} or receipt["response"]["result"] != stop
            or receipt["response"]["revision"] != stop.get("recorded_revision")
            or stop.get("id") != identifier):
        _refuse("stop_untrusted", "Need an unchanged stop written by the review-series harness.")
    classification = classify_stop(stop["code"])
    if classification != "harness_defect":
        _refuse("stop_class", "Need a harness-defect stop; follow its schedule or obtain the user's instruction.",
                stop_code=stop["code"], stop_class=classification)
    retry = stop.get("retry") or {}
    if (retry.get("policy") != "one_execution_per_progress_position" or retry.get("executions") != 1
            or retry.get("exhausted") is not True):
        _refuse("retry_pending", "Need the recorded retry schedule to end.")
    from .review_series import _identify_action
    if (retry.get("action_state") not in ("run", "resume", "continue", "replace", "plan", "record")
            or retry.get("action_digest") != _identify_action(dict(stop["plan"], state=retry["action_state"]))
            or retry.get("progress_digest") != digest(stop["plan"]["progress"])
            or not isinstance(retry.get("successful_request_id"), str)
            or Store(store.root).committed_request(retry["successful_request_id"]) is None):
        _refuse("stop_untrusted", "Need the recorded successful dispatch and repeated progress position.")
    return stop


def _check_limits(records):
    from .resources import obligations
    from .review_budget import read_budget_usage
    from .strategy import current_intent
    usage = read_budget_usage(records)
    from .development import _exhausted
    study = records.get("workspace", {}).get("study", {})
    if (usage["remaining_calls"] == 0 or obligations(records, "research")
            or any(_exhausted(account) for account in records.get("strategy_account", {}).values())
            or study.get("loop", {}).get("budget") == 0):
        _refuse("budget_exhausted", "Need a budget authorized for further work; otherwise report the stop.")
    # Free-text user conditions cannot be certified mechanically. Keep them
    # binding rather than letting the caller assert that one has been met.
    intent = current_intent(records)
    if intent and intent["payload"]["publication"].get("quality_condition") is not None:
        _refuse("quality_condition", "The current intent contains an explicit quality condition, so automatic continuation is unavailable. "
                "Continue through the ordinary gates, or record a user instruction that expressly covers this stop.")


def _check_reviews(store, records, artifacts, stop):
    from .gates import gate_state
    from .review_continuation import plan_next
    scope = stop.get("scope")
    if (scope is None or scope.get("gate") not in ("readiness", "manuscript-readiness")
            or scope.get("transition") != {"from": "experiment", "to": "write"}):
        _refuse("internal_only", "Need a review stop for the next internal study step; public writes need the user's instruction.")
    gate = scope["gate"]
    if any(row.get("authority") == "harness" and row.get("transition") == scope["transition"]
           for row in records.get("gate_override", {}).values()):
        _refuse("user_required", "An automatic pass of this gate is already recorded. To continue on the user's instruction, "
                "record a user override with `exactory-research gate-override`.")
    if _scope(records, artifacts, stop["assignment_id"]) != scope:
        _refuse("stop_stale", "Need a recorded stop for the current stage and exact review target.")
    _check_limits(records)
    report = gate_state(records, artifacts, gate)
    missing = {"readiness": "independent_review_missing", "manuscript-readiness": "scoped_review_missing"}[gate]
    if not report["obligations"]:
        _refuse("gate_ready", "The gate is ready; use the ordinary stage transition. No automatic pass is needed.")
    if any(row["code"] != missing for row in report["obligations"]):
        _refuse("other_obligations", "Need every remaining gate item satisfied except the reviews prevented by the defect.",
                obligations=report["obligations"])
    target = report.get("candidate_digest") if gate == "manuscript-readiness" else (report.get("candidate") or {}).get("digest")
    needed, accepted = 1, set()
    if target != scope["target_digest"]:
        _refuse("stop_stale", "Need a stop for this exact current review target.")
    for kind in ("readiness_review", "scoped_readiness_review"):
        for row in records.get(kind, {}).values():
            review = row["payload"]
            if (review.get("candidate_digest") == target
                    and (review.get("verdict") != "ready"
                         or any(check["status"] != "passed" for check in review.get("checks", [])))):
                _refuse("other_obligations", "Need retained rejecting reviews resolved through the ordinary gate.",
                        review_id=row["id"])

    def current(row):
        plan = plan_next(records, artifacts, row["assignment_id"])
        original = row["plan"]
        return (plan["state"] == row["retry"]["action_state"]
                and plan["head_id"] == row["assignment_id"] and plan["progress"] == original["progress"]
                and plan.get("command") == original.get("command")
                and plan.get("payload") == original.get("payload"))

    if not current(stop):
        _refuse("stop_stale", "Need a new harness stop after the review's progress or next action changed.")
    from .operations import normalized_text
    covered, basis = set(), []
    for identifier, row in sorted(records.get("review_series_stop", {}).items()):
        if row.get("scope") != scope or classify_stop(row.get("code")) != "harness_defect":
            continue
        row = _trusted_stop(store, records, identifier)
        if not current(row):
            continue
        assignment = records["review_assignment"][row["assignment_id"]]
        reviewer = normalized_text(assignment["reviewer_id"])
        if reviewer not in accepted and reviewer not in covered:
            covered.add(reviewer)
            basis.append(identifier)
    if len(covered) < needed:
        _refuse("reviews_uncovered", "Need an exhausted harness-defect stop for every missing independent review.")
    return report, basis


def _write_report(root, path, data):
    from .native_writes import write_atomic
    from .workspace import checked_parent
    with checked_parent(root, path, create=True) as (directory, name):
        write_atomic(name, data, directory_fd=directory)


def record_automatic_override(store, payload, *, expected_revision, request_id, _diagnostic=False):
    """Record and apply one internal continuation, retaining all scientific debt."""
    artifacts = store.build_artifact_store()
    pending_report = None

    def prepare(records, value):
        nonlocal pending_report
        fields(value, ("stop_id",), code="gate_override_invalid")
        text(value["stop_id"], "Recorded stop identity", code="gate_override_invalid")
        stop = _trusted_stop(store, records, value["stop_id"])
        report, basis = _check_reviews(store, records, artifacts, stop)
        scope = stop["scope"]
        identifier = "automatic:" + digest([scope["gate"], stop["id"]])
        path = ".exactory/automatic-passes/" + digest(identifier) + ".json"
        local = dict(override_id=identifier, stop_id=stop["id"], stop_code=stop["code"],
                     cited_stops=basis, gate=scope["gate"], transition=scope["transition"],
                     obligations=report["obligations"], public_write_authorized=False,
                     recorded=True, recorded_revision=expected_revision + 1)
        report_artifact = artifacts.put(json.dumps(local, ensure_ascii=False, sort_keys=True, indent=2).encode(), "application/json")
        row = dict(id=identifier, gate=scope["gate"], transition=scope["transition"], authority="harness",
                   evidence="recorded_stop", stop_id=stop["id"], stop_code=stop["code"], cited_stops=basis,
                   obligations=[{key: item[key] for key in ("code", "explanation")} for item in report["obligations"]],
                   target_digest=scope["target_digest"], recorded_revision=expected_revision + 1,
                   report_path=path, report_artifact=report_artifact)
        if not _diagnostic:
            # Publish a conservative pending marker before commit. A failed or
            # interrupted write grants no stage movement, and a later unreadable
            # store cannot turn the absent final report into public authority.
            pending = dict(local, recorded=False, request_id=request_id)
            pending.pop("recorded_revision")
            try:
                _write_report(store.root, path, json.dumps(pending, ensure_ascii=False, indent=2).encode())
                pending_report = path
            except (OSError, ResearchError) as error:
                raise ResearchError("gate_override_report", "Need a writable local report before the pass can be recorded.",
                                    dict(report_path=path)) from error
        from .integration import now
        from .drafting import prepare_draft
        study = dict(records["workspace"]["study"], stage=scope["transition"]["to"], status="in_progress",
                     waiting=None, updated=now())
        return ([immutable_record(records, "gate_override", identifier, row), ("workspace", "study", study)]
                + prepare_draft(records, store.root, study)), row

    try:
        receipt = prepared_mutation(store, "research.gate_auto_pass", payload, prepare,
                                    expected_revision=expected_revision, request_id=request_id)
    except ResearchError as error:
        if pending_report is not None:
            error.details = dict(error.details or {}, report_path=pending_report)
        raise
    row = receipt["result"]
    try:
        _write_report(store.root, row["report_path"], artifacts.read(row["report_artifact"]))
        from .integration import export_workspace
        export_workspace(store)
    except (OSError, ResearchError) as error:
        raise ResearchError("gate_override_report", "The pass is recorded. Need the same request replayed to restore its local report and views.",
                            dict(override_id=row["id"], report_path=row["report_path"])) from error
    return receipt


def _covers_public_write(rows, gate, public_action):
    return any(row.get("authority") == "user" and (
        row.get("public_action") == public_action if row.get("public_action") is not None else
        gate is not None and row["gate"] == gate) for row in rows)


def require_public_write_instruction(store, gate, *, public_action=None, precheck=False):
    """An automatic continuation never supplies authority for a remote write."""
    if not (store.root / ".exactory/automatic-passes").exists():
        return
    from .overrides import read_unrecorded_overrides
    records = store.snapshot()["records"]
    rows = list(records.get("gate_override", {}).values())
    for entries in (rows, read_unrecorded_overrides(store.root) or []):
        if _covers_public_write(entries, gate, public_action):
            return
        # A managed path has not chosen its concrete requests yet (a deposit
        # can resume several different steps). Admit scoped instructions here;
        # every transport still checks its exact action before sending a write.
        if precheck and public_action is None and any(row.get("authority") == "user"
                            and row.get("public_action") is not None for row in entries):
            return
    _refuse("public_write", "Need the user's instruction recorded for this public write.", gate=gate,
            public_action=public_action, next="exactory-research gate-override")


def check_public_write(gate, *, public_action=None):
    """Check the current workspace before any public transport sends a write."""
    from .storage import Store
    from pathlib import Path
    from .workspace import find_workspace
    start = Path.cwd()
    # A damaged workspace can retain only its pending/committed pass reports.
    # Discover those independently of the ordinary workspace contract.
    root = find_workspace(required=False)
    for directory in (start, *start.parents):
        if (directory / ".exactory/automatic-passes").exists():
            root = directory
            break
        if directory == root:
            break
    # Automatic passes write reports before commit, so no report directory means no pass.
    if root is not None and (root / ".exactory/automatic-passes").exists():
        try:
            store = Store(root)
            store.snapshot()
        except ResearchError:
            if not (root / ".exactory/automatic-passes").exists():
                return
            from .overrides import read_unrecorded_overrides
            if _covers_public_write(read_unrecorded_overrides(root) or [], gate, public_action):
                return
            _refuse("public_write", "Need a readable store or a local user instruction covering this public write.",
                    gate=gate, public_action=public_action)
        require_public_write_instruction(store, gate, public_action=public_action)
