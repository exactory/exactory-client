"""Post gate, decisions and deterministic record-authoritative delivery."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from .graph import obligation
from .operations import fields, immutable_record, strings, text
from .problem_attempt import assess_attempt
from .problem_candidates import assess_slate, find_current_slate
from .problem_claims import find_current_statement, find_latest, find_value, compute_problem_evidence_digest, require_problem, compute_statement_digest, assess_assessment
from .problem_evidence import assess_formal_checks, project_citations, validate_value
from .problem_openness import assess_openness

_RECORD_RESULT_NOTE = ('Record the observed complete final of the current series with value-review from its '
                       'exact output. No further reviewer call is needed.')


def assess_minimum(records, artifacts):
    return _assess_minimum(records, Evaluation.of(records, artifacts))[0]


def _assess_minimum(records, artifacts):
    pending = assess_slate(records, artifacts)
    statement, value = find_current_statement(records), find_value(records)
    if statement is None:
        pending.append(obligation("problem_statement_missing", "Record the exact statement and resolution criteria."))
    attempt = assess_attempt(records, artifacts)
    pending.extend(attempt)
    openness = assess_openness(records, artifacts)
    pending.extend(openness)
    if value is None or statement is None or value["statement_id"] != statement["id"]:
        pending.append(obligation("problem_value_missing", "Bind motivation and current-state paragraphs to this statement's evidence.",
                                  statement_id=None if statement is None else statement["id"]))
    else:
        try:
            validate_value(records, artifacts, value)
        except ResearchError as error:
            pending.append(obligation(error.code, error.message, **(error.details or {})))
    pending.extend(assess_formal_checks(records))
    pending.extend(assess_assessment(records, artifacts))
    try:
        project_citations(records, artifacts)
    except ResearchError as error:
        pending.append(obligation(error.code, error.message, **(error.details or {})))
    return pending, attempt, openness


def collect_review_outcomes(records, artifacts):
    """Read current assignment science, keeping staged slots separate from raw outputs."""
    from .review_protocol import assignment_state
    from .research_decisions import select_problem_slot_assignments
    result_heads = {assignment['id'] for subject in {
        digest(row.get('subject')): row.get('subject') for row in records.get('review_assignment', {}).values()
        if row['role'] == 'result' and row.get('subject', {}).get('kind') == 'problem'}.values()
        for assignment in select_problem_slot_assignments(records, 'result', subject).values()}
    parents = {parent for row in records.get("review_assignment", {}).values()
               for parent in (row.get("history_assignment_id"), row.get("context", {}).get("prior_assignment_id"),
                              (row.get("context", {}).get("context_repair") or {}).get("assignment_id"))
               if parent is not None}
    rows = {}
    for identifier, assignment in records.get("review_assignment", {}).items():
        binding = assignment.get("staged_review", {})
        if identifier in parents or binding.get("kind") == "task":
            continue
        if (assignment['role'] == 'result' and assignment.get('subject', {}).get('kind') == 'problem'
                and identifier not in result_heads):
            continue
        state = assignment_state(records, artifacts, identifier)
        epoch_id = binding.get("epoch_id") or state.get("epoch_id") or assignment.get("epoch_id")
        epochs = [epoch for epoch in records.get("review_epoch", {}).values()
                  if epoch.get("controller_assignment_id") == identifier]
        epoch = records.get("review_epoch", {}).get(epoch_id)
        if epoch is None and len(epochs) == 1:
            epoch = epochs[0]
        effective = state.get("effective")
        if effective is None and epoch is not None:
            from .review_composition import effective_review_output
            effective = effective_review_output(identifier)
        outcome = (effective or {}).get("incomplete_outcome")
        slot = (epoch or outcome or {}).get("slot_id") or assignment.get("slot_id") or identifier
        key = (assignment["role"], slot)
        row = dict(assignment=assignment, state=state, epoch=epoch, effective=effective)
        if key not in rows or assignment.get("recorded_revision", 0) > rows[key]["assignment"].get("recorded_revision", 0):
            rows[key] = row
    return list(rows.values())


def collect_staged_slots(rows, *, role="result"):
    """Use the shared slot contract, including ordinary peers of staged result slots."""
    from .review_staged import collect_slot_outcomes
    heads = []
    for row in rows:
        assignment, epoch, effective = row["assignment"], row["epoch"], row["effective"] or {}
        if assignment["role"] != role:
            continue
        outcome = effective.get("incomplete_outcome")
        output = effective.get("effective_output") if effective else row["state"].get("output")
        head = dict(slot_id=(epoch or outcome or {}).get("slot_id") or assignment.get("slot_id") or assignment["id"],
                    epoch_id=(epoch or {}).get("id") or (outcome or {}).get("epoch_id") or assignment["id"],
                    role=assignment["role"], state="incomplete" if outcome else "complete" if output is not None else "pending",
                    outcome=outcome or output, current=effective.get("currency_state") not in ("stale", "historical") and
                        not any(item["code"] == "review_epoch_stale" for item in row["state"].get("obligations", [])),
                    inherited_adverse_findings=(epoch or {}).get("manifest", {}).get("inherited_findings", []))
        heads.append(head)
    return collect_slot_outcomes(heads)


def describe_incomplete_work(outcome):
    """Project recovery advice without reoffering an exhausted invocation through a workflow alias."""
    exhausted = {task for entry in outcome["unresolved_scope"] for task in entry["exhausted_tasks"]}
    actions = [action for action in outcome["recovery_work"]
               if not ((action.get("command") == "review-run" or action.get("workflow") == "review-run")
                       and action.get("payload", {}).get("task_id") in exhausted)]
    if actions:
        return actions
    return [dict(kind="research", workflow="review-assignment", scope=entry["scope"], text=entry["cause"],
                 prerequisite="new_evidence_or_eligible_recovery") for entry in outcome["unresolved_scope"]]


def describe_review_tasks(records, epoch, state):
    """Project the execution frontier through the shared finite task-state policy."""
    from .review_staged import task_state
    from .review_staged_protocol import list_epoch_tasks, describe_planning_command
    tasks = list_epoch_tasks(records, epoch)
    executions = records.get("review_task_execution", {})
    accepted = {identifier for identifier, execution in executions.items() if execution["state"] == "accepted"}
    total = sum(executions.get(task["id"], {}).get("attempts", 0) for task in tasks)
    effective = state.get("effective") or {}
    incomplete = effective.get("incomplete_outcome")
    result = []
    for task in tasks:
        execution = executions.get(task["id"], {})
        reservation_id = execution.get("reservation_id")
        reservation = records.get("review_reservation", {}).get(reservation_id)
        captured = (records.get("review_observation", {}).get(reservation_id, {}).get("observation", {}).get("status") == "completed"
                    and execution.get("state") != "refused")
        attempts = execution.get("attempts", 0)
        failures = attempts if execution.get("state") in ("known_failure", "unknown") else max(0, attempts - 1)
        plan = records.get("review_execution_plan", {}).get(execution.get("plan_id"), {})
        if attempts >= plan.get("max_attempts", epoch["budgets"]["max_retries"] + 1) and not captured:
            failures = epoch["budgets"]["max_retries"] + 1
        ledger = dict((incomplete or {}).get("resource_ledger") or state.get("resource_ledger") or {})
        ledger["attempts"] = max(total, ledger.get("attempts", 0))
        progress = task_state(epoch, task, ledger=ledger, failures=failures,
            captured=captured, accepted=task["id"] in accepted,
            worker_unreconciled=reservation is not None and not reservation.get("released", False),
            dependencies_ready=all(identifier in accepted for identifier in task.get("dependencies", [])),
            current=effective.get("currency_state") not in ("stale", "historical") and
                not any(item["code"] == "review_epoch_stale" for item in state.get("obligations", [])),
            incomplete=incomplete)
        if progress["state"] == "exhausted":
            progress["next"] = [describe_planning_command(epoch["controller_assignment_id"])]
        if progress["state"] == "incomplete":
            progress["next"] = describe_incomplete_work(incomplete)
        child = next((assignment for assignment in records.get("review_assignment", {}).values()
                      if assignment.get("staged_review", {}).get("task_id") == task["id"]), None)
        if progress["state"] == "ready" and child is None:
            controller = records["review_assignment"][epoch["controller_assignment_id"]]
            payload = {key: controller.get(key) for key in ("dossier_id", "role", "reviewer_id", "author_id", "route_id")}
            payload.update(id="task-assignment:" + digest([epoch["id"], task["id"]]),
                           context={"controller_assignment_id": controller["id"], "task_id": task["id"]})
            progress["next"] = [dict(kind="command", command="review-assignment", payload=payload)]
        elif progress["state"] == "ready":
            policy = records.get("review_capacity_policy", {}).get("store", {})
            active = [reservation for reservation in records.get("review_reservation", {}).values()
                      if not reservation.get("released", False)]
            route_active = [reservation for reservation in active if reservation["route_id"] == epoch["route_id"]]
            if (len(active) >= policy.get("store_limit", 1) or
                    len(route_active) >= policy.get("route_limits", {}).get(epoch["route_id"], policy.get("store_limit", 1))):
                progress.update(state="waiting", code="review_capacity_wait", next=[])
                for reservation in active:
                    owner = records.get("review_epoch", {}).get(reservation["epoch_id"])
                    if owner is not None:
                        progress["next"].append(dict(kind="command", command="review-run", payload=dict(
                            assignment_id=owner["controller_assignment_id"], epoch_id=owner["id"],
                            task_id=reservation["task_id"], action="reconcile")))
                progress["reservations"] = [reservation["id"] for reservation in active]
        result.append(progress)
    return result


def assess_source_requests(records, artifacts, *, outcomes=None):
    from . import review_problem_parts
    rows = collect_review_outcomes(records, artifacts) if outcomes is None else outcomes
    staged = [row for row in rows if row["epoch"] is not None or (row["effective"] or {}).get("kind") in ("paged", "incomplete")
              or (row["effective"] or {}).get("completion_root")]
    pending = []
    if staged:
        # Validate occupancy through the common contract. An incomplete result is
        # never supplied to the scientific review-pair collector as a review.
        collect_staged_slots(rows)
        collect_staged_slots(rows, role="adjudicator")
    for row in rows:
        assignment, state = row["assignment"], row["state"]
        identifier = assignment["id"]
        if row in staged:
            effective, epoch = row["effective"] or {}, row["epoch"] or {}
            outcome = effective.get("incomplete_outcome")
            identity = dict(assignment_id=identifier, epoch_id=epoch.get("id") or (outcome or {}).get("epoch_id"),
                            slot_id=epoch.get("slot_id") or (outcome or {}).get("slot_id") or assignment.get("slot_id"),
                            role=assignment["role"])
            if outcome is not None:
                pending.append(obligation("assessment_incomplete", "Complete the named scientific or recovery work in this occupied slot.",
                    **identity, outcome_id=outcome["id"], origin=outcome["origin"],
                    unresolved_scope=outcome["unresolved_scope"], resource_ledger=outcome["resource_ledger"],
                    next=describe_incomplete_work(outcome)))
            elif effective.get("kind", "pending") == "pending":
                tasks = describe_review_tasks(records, epoch, state) if epoch else []
                work = [task for task in tasks if task["state"] != "accepted"]
                for task in work:
                    pending.append(obligation(task["code"], "Complete the named next step of the current review task.",
                        **identity, task_id=task["task_id"], state=task["state"], resource_ledger=task["resource_ledger"], next=task["next"]))
                if not work:
                    pending.append(obligation("review_task_pending", "Plan the remaining required work of the current epoch.",
                        **identity, next=state.get("next") or [dict(kind="recovery", workflow="review-assignment",
                            scope={"epoch_id": identity["epoch_id"]}, prerequisite="remaining_required_tasks")]))
            # Qualification is deliberately fail closed. This release has no
            # authenticated passing C-study issuer; an author flag is no study.
            pending.append(obligation("review_quality_measurement_pending", "Qualify this staged protocol and reviewer configuration with the approved passing C study.",
                **identity, route_id=assignment["route_id"], protocol=epoch.get("protocol"),
                calibration_profile_version=epoch.get("calibration_profile_version")))
            pending.extend(item for item in state.get("obligations", []) if item["code"] != "review_task_pending")
            continue
        from . import review_parts
        if review_parts.KEY in assignment:
            verified = review_parts.verify_series(records, artifacts, identifier)
            if not verified['complete']:
                from .review_protocol import has_ended_without_output
                scope = dict(role=assignment['role'], route_id=assignment['route_id'],
                             reviewer_id=assignment['reviewer_id'], author_id=assignment['author_id'],
                             dossier_id=assignment['dossier_id'])
                if state['ready']:
                    scope['prior_assignment_id'] = identifier
                    next_step = dict(kind='review', workflow='review-assignment', scope=scope)
                elif has_ended_without_output(records, identifier):
                    prior = assignment.get('context', {}).get('prior_assignment_id')
                    if prior is not None:
                        scope['prior_assignment_id'] = prior
                    next_step = dict(kind='review', workflow='review-assignment', scope=scope)
                else:
                    next_step = dict(kind='review', workflow='review-run', scope=dict(assignment_id=identifier))
                pending.append(obligation('review_parts_pending', 'Complete the current parts review series.',
                    assignment_id=identifier, **state.get(review_parts.KEY, {}), next=[next_step]))
            elif assignment['role'] == 'result' and not any(
                    review['assignment_id'] == identifier for review in records.get('value_review', {}).values()):
                from .research_decisions import read_effective_result
                output, _ = read_effective_result(state)
                pending.append(obligation('review_result_pending', _RECORD_RESULT_NOTE,
                    assignment_id=identifier, next=[dict(kind='review', workflow='value-review',
                        scope=dict(output, dossier_id=assignment['dossier_id'], assignment_id=identifier))]))
        if state.get('output') is not None and state.get(review_problem_parts.KEY, {}).get('stage') == 'part':
            if not state['ready']:
                pending.extend(state['obligations'])
                continue
            assignment = records['review_assignment'][identifier]
            packet = json.loads(artifacts.read(assignment['packet']))
            plan = packet[review_problem_parts.KEY]
            pending.append(obligation('problem_evidence_part_pending', 'Continue the observed problem evidence part.',
                assignment_id=identifier, turn=plan['turn'], remaining=plan['remaining'],
                next=[dict(kind='review', workflow='review-assignment', scope=dict(
                    {key: assignment[key] for key in ('role', 'route_id', 'reviewer_id', 'author_id', 'dossier_id')},
                    prior_assignment_id=identifier))]))
        if state.get("pending_source_requests"):
            pending.append(obligation("review_sources_pending", "Deliver the sources requested by the observed reviewer.",
                                      assignment_id=identifier, requests=state["pending_source_requests"]))
    return pending


def find_post(records, statement):
    if statement is None:
        return None
    return next((row for row in records.get("problem_post", {}).values()
                 if row["statement_digest"] == compute_statement_digest(statement)), None)


def _read_review_binding(assignment, name):
    value = assignment.get(name, {})
    if value is None and name in ('subject', 'staged_review'):
        return {}
    if not isinstance(value, dict):
        raise ResearchError('invalid_review_context', 'The review binding must be an object',
                            dict(assignment_id=assignment.get('id'), binding=name))
    return value


def find_review_closure_root(records, assignment):
    """Resolve the first assignment of this phase and retained publication slot."""
    assignments = records.get('review_assignment', {})
    visited, active = {}, set()

    def read_assignment(identifier):
        text(identifier, 'Closure predecessor', code='invalid_review_context')
        row = assignments.get(identifier)
        if not isinstance(row, dict) or row.get('id') != identifier:
            raise ResearchError('invalid_review_context', 'The closure predecessor is missing',
                                dict(assignment_id=identifier))
        return row

    def visit(row):
        identifier = row.get('id')
        text(identifier, 'Closure assignment', code='invalid_review_context')
        if identifier in active:
            raise ResearchError('invalid_review_context', 'The closure ancestry contains a cycle',
                                dict(assignment_id=identifier))
        if identifier in visited:
            return
        active.add(identifier)
        context = _read_review_binding(row, 'context')
        if row.get('slot_id') is not None:
            text(row['slot_id'], 'Closure slot', code='invalid_review_context')
        staged = _read_review_binding(row, 'staged_review')
        if staged.get('kind') == 'task':
            controller = read_assignment(staged.get('controller_assignment_id'))
            if (_read_review_binding(controller, 'staged_review').get('kind') != 'controller'
                    or controller.get('role') != row.get('role')
                    or controller.get('dossier_id') != row.get('dossier_id')):
                raise ResearchError('invalid_review_context', 'The staged closure controller is invalid')
            visit(controller)
        else:
            revision = row.get('recorded_revision')
            if type(revision) is not int or revision < 1:
                raise ResearchError('invalid_review_context', 'The closure assignment needs its local revision',
                                    dict(assignment_id=identifier))
            parents = [(context.get('prior_assignment_id'), False),
                       (row.get('history_assignment_id'), True)]
            for key in ('context_repair', 'staged_upgrade'):
                binding = context.get(key)
                if binding is not None:
                    if not isinstance(binding, dict) or not binding.get('assignment_id'):
                        raise ResearchError('invalid_review_context', 'The closure predecessor binding is invalid')
                    parents.append((binding['assignment_id'], False))
            for parent_id, scientific_history in parents:
                if parent_id is None:
                    continue
                parent = read_assignment(parent_id)
                if parent.get('role') == row.get('role') and parent.get('dossier_id') == row.get('dossier_id'):
                    visit(parent)
                elif not scientific_history:
                    raise ResearchError('invalid_review_context', 'The closure predecessor belongs to another phase')
            visited[identifier] = row
        active.remove(identifier)

    visit(assignment)
    controller = min(visited.values(), key=lambda row: row['recorded_revision'])
    slot_ids = {row['slot_id'] for row in visited.values() if row.get('slot_id') is not None}
    if len(slot_ids) > 1:
        raise ResearchError('invalid_review_context', 'The closure lineage changes its retained slot')
    for slot_id in slot_ids:
        text(slot_id, 'Closure slot', code='invalid_review_context')
        for row in assignments.values():
            if ((row.get('slot_id') or row.get('id')) == slot_id and row.get('role') == controller.get('role')
                    and row.get('dossier_id') == controller.get('dossier_id')):
                visit(row)
    if any(row.get('slot_id') not in (None, *slot_ids) for row in visited.values()):
        raise ResearchError('invalid_review_context', 'The closure predecessor changes its retained slot')
    return min(visited.values(), key=lambda row: (row['recorded_revision'], row['id']))


def _find_scoped_review_closure_root(records, assignment, artifacts):
    """Share successful walks only inside the current status command."""
    if artifacts is None:
        return find_review_closure_root(records, assignment)
    evaluation = Evaluation.of(records, artifacts)
    scope = evaluation.find_root()._runtime_scope[0]
    roots = None if scope is None else scope.get('review_closure_roots')
    if roots is None:
        return find_review_closure_root(records, assignment)
    binding = {name: assignment[name] for name in (
        'id', 'role', 'dossier_id', 'context', 'staged_review', 'slot_id',
        'history_assignment_id', 'recorded_revision') if name in assignment}
    key = (id(records), digest(binding))
    if key not in roots:
        roots[key] = (records, find_review_closure_root(records, assignment))
    return roots[key][1]


def find_review_post(records, assignment, artifacts=None, *, check_unbound=True):
    """Classify publication closure by verified claim binding, then chain chronology."""
    posts = records.get('problem_post', {})
    if not posts:
        return None
    from .strategy import find_orchestration
    if find_orchestration(records) != 'problem_creation':
        return None
    role = assignment.get('role')
    staged = _read_review_binding(assignment, 'staged_review')
    if staged.get('kind') == 'task':
        text(staged.get('controller_assignment_id'), 'Closure controller', code='invalid_review_context')
        controller = records.get('review_assignment', {}).get(staged.get('controller_assignment_id'))
        if not isinstance(controller, dict) or controller is assignment:
            raise ResearchError('invalid_review_context', 'The staged closure controller is missing')
        # The dedicated ancestry walk detects controller and predecessor cycles.
        _find_scoped_review_closure_root(records, assignment, artifacts)
        assignment = controller
        role = assignment.get('role')
    if role not in ('bar', 'slate', 'result', 'adjudicator', 'native_math'):
        return None
    context = _read_review_binding(assignment, 'context')
    statement_digest = None
    if role == 'native_math':
        from .native_review import load_packet
        from .openness_reading import is_openness_packet
        reference = context.get('native_packet')
        if reference is None or artifacts is None:
            raise ResearchError('invalid_native_review_packet', 'The native closure binding must be verified',
                                dict(assignment_id=assignment.get('id')))
        packet = load_packet(Evaluation.of(records, artifacts), reference, assignment['reviewer_id'])
        if not is_openness_packet(packet):
            return None
        subject = packet['subject']
        if (packet['kind'] != 'result' or not isinstance(subject.get('statement'), str)
                or not isinstance(subject.get('resolution_criteria'), str)
                or compute_statement_digest(subject) != packet['claim_digest']):
            raise ResearchError('invalid_native_review_packet', 'The openness claim digest must bind its statement')
        statement_digest = packet['claim_digest']
    else:
        subject = _read_review_binding(assignment, 'subject')
        if subject.get('kind') not in (None, 'problem'):
            return None
        identifier = subject.get('statement_id')
        if identifier is not None:
            text(identifier, 'Closure statement', code='invalid_review_context')
            statement = records.get('problem_statement', {}).get(identifier)
            if (not isinstance(statement, dict) or not isinstance(statement.get('statement'), str)
                    or not isinstance(statement.get('resolution_criteria'), str)):
                raise ResearchError('problem_statement_missing', 'The exact bound statement is missing or invalid',
                                    dict(statement_id=identifier))
            statement_digest = compute_statement_digest(statement)
    if statement_digest is not None:
        if check_unbound and (assignment.get('history_assignment_id') is not None
                              or assignment.get('slot_id') is not None
                              or any(key in context for key in
                                     ('prior_assignment_id', 'context_repair', 'staged_upgrade'))):
            _find_scoped_review_closure_root(records, assignment, artifacts)
        return next((post for post in posts.values() if post['statement_digest'] == statement_digest), None)
    if not check_unbound:
        return None
    root = _find_scoped_review_closure_root(records, assignment, artifacts)
    for post in posts.values():
        read_revision = getattr(records, 'read_creation_revision', None)
        created = read_revision('problem_post', post['id']) if callable(read_revision) else None
        revision = post.get('recorded_revision', created)
        if (type(revision) is not int or revision < 1
                or created is not None and revision != created):
            raise ResearchError('problem_post_revision_missing', 'The post needs its authenticated local creation revision',
                                dict(post_id=post['id']))
        if revision > root['recorded_revision']:
            return post
    return None


def require_unposted_review(records, assignment, artifacts=None, *, check_unbound=True):
    """Refuse prospective review work while leaving historical readers usable."""
    post = find_review_post(records, assignment, artifacts, check_unbound=check_unbound)
    if post is not None:
        details = dict(post_id=post['id'])
        subject = _read_review_binding(assignment, 'subject')
        if subject.get('statement_id') is not None:
            details['statement_id'] = subject['statement_id']
        elif check_unbound:
            details['closure_root_id'] = _find_scoped_review_closure_root(
                records, assignment, artifacts)['id']
        raise ResearchError("problem_already_posted", "This exact statement already has a recorded post.",
                            details)


def describe_problem_work(records, pending):
    """Attach research steps to completed scientific defects without resampling the review."""
    from .research_decisions import list_stated_objections
    workflows = {"problem_statement": "problem-statement", "problem_openness": "problem-openness",
                 "problem_value": "problem-value", "problem_citations": "read", "stop": "problem-decision"}
    result = []
    for item in pending:
        row = dict(item)
        if row["code"] == "problem_assurance_pending":
            review = records.get("value_review", {}).get(row.get("review_id"), {})
            checks = review.get("payload", {}).get("value", {}).get("assurances", [])
            row["next"] = [dict(kind="research", workflow=workflows[check["kind"]],
                scope=dict(review_id=row["review_id"], assurance=check["kind"]), text=check["reason"],
                prerequisite="scientific_evidence_addressing_the_recorded_finding") for check in checks
                if check["kind"] in row.get("assurances", []) and check["kind"] in workflows]
        elif row["code"] == "research_objection_unresolved":
            identifier = row["objection_id"]
            stated = next((objection for review in records.get("value_review", {}).values()
                           if review.get("payload", {}).get("objections")
                           for objection in list_stated_objections(review) if objection["id"] == identifier), {})
            row["next"] = [dict(kind="research", workflow="review-assignment", scope=dict(objection_id=identifier),
                prerequisite=stated.get("resolution_condition") or "evidence_resolving_the_exact_recorded_objection",
                text="Address the scientific resolution condition of " + identifier + " before its successor assessment.")]
        result.append(row)
    return result


def assess_post(records, artifacts):
    artifacts = Evaluation.of(records, artifacts)
    require_problem(records)
    from .research_decisions import assess_problem_reviews
    statement = find_current_statement(records)
    minimum, attempt, openness = _assess_minimum(records, artifacts)
    # A packet is judged only when its own evidence is available. Missing claim
    # evidence is already reported with the more useful minimum obligations.
    reviews = assess_problem_reviews(records, artifacts, check_evidence=not minimum)
    pending = list(minimum)
    pending.extend(reviews["obligations"])
    outcomes = collect_review_outcomes(records, artifacts)
    pending.extend(assess_source_requests(records, artifacts, outcomes=outcomes))
    decision = find_latest(records, "problem_decision")
    evidence_digest = None
    # Currency is a digest of explicitly named immutable records. Do not run
    # historical execution assessment under today's preparation snapshot.
    try:
        evidence_digest = compute_problem_evidence_digest(records, artifacts)
    except ResearchError as error:
        if not minimum:
            pending.append(obligation(error.code, error.message, **(error.details or {})))
    if decision is None or decision["action"] != "post":
        pending.append(obligation("problem_decision_missing", "Record a post decision after both observed result reviews."))
    elif decision["problem_evidence_digest"] != evidence_digest:
        pending.append(obligation("problem_decision_stale", "Reassess the changed evidence and record a current post decision.", decision_id=decision["id"]))
    elif not minimum:
        selected = assess_problem_reviews(records, artifacts, decision["review_ids"], decision["bar_review_ids"])
        pending.extend(selected["obligations"])
    posted = find_post(records, statement)
    if posted is not None:
        pending.append(obligation("problem_already_posted", "This exact statement already has a recorded post.", post_id=posted["id"], problem_id=posted["problem_id"]))
    # The collected pair and the decision's pair can each require a regrade.
    regrades = [row for row in pending if row["code"] == "problem_rubric_regrade_required"]
    if regrades:
        pending = [row for row in pending if row["code"] != "problem_rubric_regrade_required"]
        pending.append(dict(regrades[0], review_ids=sorted({identifier for row in regrades
                                                          for identifier in row["review_ids"]})))
    if records.get("review_epoch"):
        pending = describe_problem_work(records, pending)
    unique = list({digest(row): row for row in pending}.values())
    continuation_codes = ('problem_evidence_part_pending', 'review_parts_pending', 'review_result_pending')
    unique.sort(key=lambda row: row['code'] not in continuation_codes)
    slate = find_current_slate(records)
    milestone = "exploration"
    if slate is not None:
        milestone = "slate"
        if slate["selected"] and statement is not None:
            milestone = "formulation"
            if not attempt:
                milestone = "attempt"
                if not openness:
                    milestone = "openness"
    if not minimum and reviews is not None and reviews["ready"] and decision is not None and decision["action"] == "post" and decision["problem_evidence_digest"] == evidence_digest:
        milestone = "value"
    if posted is not None:
        milestone = "post"
    posted_state = {}
    if posted is not None:
        after_post = [dict({key: value for key, value in row.items() if key != "next"},
                           reason=row.get("reason", row["explanation"])) for row in unique
                      if row["code"] != "problem_already_posted"]
        unique = [row for row in unique if row["code"] == "problem_already_posted"]
        shown_post = dict(posted, gate=posted.get("gate", "passed"))
        if posted.get("gate") == "bypassed" and "recorded_at" not in posted:
            shown_post.update(recorded_at=posted["posted_at"], posted_at=None)
            time_statement = records.get("problem_post_time", {}).get(posted["id"])
            if time_statement is not None:
                shown_post.update(posted_at=time_statement["posted_at"], time_statement=time_statement)
        posted_state = dict(after_post=after_post, post=shown_post)
    slots = ({"slot_outcomes": reviews['slot_outcomes']} if 'slot_outcomes' in reviews else
             {"slot_outcomes": collect_staged_slots(outcomes)} if records.get('review_epoch') else {})
    return dict({"ready": not unique, "obligations": unique, "counts": {"obligations": len(unique)},
            "digest": digest({"problem_evidence_digest": evidence_digest, "decision": decision, "obligations": unique}),
            "orchestration": "problem_creation", "milestone": milestone,
            "problem_evidence_digest": evidence_digest, "statement_id": None if statement is None else statement["id"],
            "decision_id": None if decision is None else decision["id"]}, **slots, **posted_state)


def prepare_decision(records, artifacts, value, revision):
    artifacts = Evaluation.of(records, artifacts)
    require_problem(records)
    fields(value, ("id", "action", "review_ids", "bar_review_ids", "reason"))
    text(value["id"], "Problem decision ID")
    text(value["reason"], "Problem decision reason")
    if value["action"] not in {"post", "revise", "withdraw"}:
        raise ResearchError("invalid_input", "Problem decision action must be post, revise or withdraw")
    strings(value["review_ids"], "Problem result review IDs")
    strings(value["bar_review_ids"], "Problem bar review IDs")
    evidence_digest = None
    if value["action"] == "post":
        pending = assess_minimum(records, artifacts)
        if pending:
            raise ResearchError("problem_minimum_unmet", "Complete the current statement, openness and value evidence first", {"obligations": pending})
        from .research_decisions import assess_problem_reviews
        review = assess_problem_reviews(records, artifacts, value["review_ids"], value["bar_review_ids"])
        pending = review["obligations"] + assess_source_requests(records, artifacts)
        if records.get("review_epoch"):
            from .report_views import order_obligations
            pending = order_obligations(pending)
            pending = describe_problem_work(records, pending)
        if pending:
            raise ResearchError(pending[0]["code"], pending[0]["explanation"], {"obligations": pending})
        evidence_digest = compute_problem_evidence_digest(records, artifacts)
    record = dict(value, problem_evidence_digest=evidence_digest, recorded_revision=revision)
    return [immutable_record(records, "problem_decision", value["id"], record)], record


def build_post_files(records, artifacts):
    """Compose the statement, value paragraphs and citations without claiming any decision or review."""
    statement, value = find_current_statement(records), find_value(records)
    files = {"title.txt": statement["title"], "statement.md": statement["statement"],
             "criteria.md": statement["resolution_criteria"],
             "motivation.md": "\n\n".join(row["text"] for row in value["motivation"]),
             "state.md": "\n\n".join(row["text"] for row in value["current_state"]),
             "citations.json": json.dumps(project_citations(records, artifacts), ensure_ascii=False, sort_keys=True, indent=2)}
    formal = statement["formal_statement"]
    if formal is not None:
        files["formal-statement.txt"] = formal["text"]
    return files


def build_projection(records, artifacts):
    statement = find_current_statement(records)
    decision = find_latest(records, "problem_decision")
    files = build_post_files(records, artifacts)
    formal = statement["formal_statement"]
    from .review_protocol import assignment_state
    from .research_decisions import _response
    reviews = []
    staged = False
    for identifier in decision.get("review_ids", []):
        review = records.get("value_review", {}).get(identifier)
        if review is None:
            continue
        state = assignment_state(records, artifacts, review["assignment_id"])
        effective = state.get("effective") or {}
        staged = staged or bool(effective.get("composition_root") or effective.get("completion_root"))
        if effective:
            if effective.get("kind") not in ("ordinary", "paged") or "effective_output" not in effective:
                raise ResearchError("assessment_incomplete", "Export requires a completed effective review.",
                                    {"assignment_id": review["assignment_id"]})
            if _response(review["payload"]) != effective["effective_output"]:
                raise ResearchError("review_output_mismatch", "The recorded review must equal its completed effective result.")
            reviews.append(dict(id=identifier, assignment_id=review["assignment_id"], output=effective["effective_output"],
                                binding={key: value for key, value in effective.items() if key != "effective_output"}))
        else:
            reviews.append(dict(id=identifier, assignment_id=review["assignment_id"], output=_response(review["payload"]),
                                binding=dict(kind="ordinary", effective_output_digest=review.get("output_digest"))))
    if staged:
        files["reviews.json"] = json.dumps(reviews, ensure_ascii=False, sort_keys=True, indent=2)
    files = {name: (content + "\n").encode("utf-8") for name, content in files.items()}
    manifest = {"statement_id": statement["id"], "statement_digest": compute_statement_digest(statement),
                "problem_evidence_digest": compute_problem_evidence_digest(records, artifacts), "decision_id": decision["id"],
                "area": statement["area"], "formal_statement_language": None if formal is None else formal["language"],
                "files": {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())}}
    files["problem-export.json"] = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    return files, manifest


def compute_export_digest(files):
    """The digest of an export's files: the name of its default folder and the export_digest of its post."""
    return digest({name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())})


def has_exported_files(directory, files):
    """Whether directory holds exactly these files with these bytes. A name that starts with a dot, such as the
    .DS_Store that a file browser adds, is not part of an export."""
    return (directory.is_dir() and not directory.is_symlink()
            and {path.name for path in directory.iterdir() if not path.name.startswith(".")} == set(files)
            and all(not (directory / name).is_symlink() and (directory / name).is_file()
                    and (directory / name).read_bytes() == content for name, content in files.items()))


def export_problem(store, destination=None):
    """Write the reviewed parts into destination, by default final/problem/exports/<export digest>/ of the workspace
    root. A destination that already holds exactly these files is returned as it is."""
    from .gates import require_ready
    records = store.snapshot()["records"]
    artifacts = Evaluation.of(records, ArtifactStore(store.root))
    require_ready(assess_post(records, artifacts), "post")
    files, manifest = build_projection(records, artifacts)
    return write_problem_export(store, files, manifest, destination)


def export_problem_override(store, destination=None):
    """Compose the exact post material on a recorded instruction, with no reviewed decision claim."""
    records = store.snapshot()["records"]
    require_problem(records)
    statement = find_current_statement(records)
    statement_digest = None if statement is None else compute_statement_digest(statement)
    override = max((row for row in records.get("gate_override", {}).values()
                    if row["gate"] == "post" and row.get("statement_digest") == statement_digest),
                   key=lambda row: row["recorded_revision"], default=None)
    if override is None:
        raise ResearchError("gate_override_required", "This export needs a recorded post override whose statement digest "
                            "matches the current statement text and resolution criteria. If the user has instructed you to "
                            "pass this stop, record their words with gate-override, gate post; otherwise report the stop")
    if find_value(records) is None:
        raise ResearchError("problem_value_missing", "Record the problem's motivation and current state with problem-value "
                            "before this export")
    artifacts = Evaluation.of(records, ArtifactStore(store.root))
    files = {name: (content + "\n").encode("utf-8") for name, content in build_post_files(records, artifacts).items()}
    formal = statement["formal_statement"]
    manifest = dict(statement_id=statement["id"], statement_digest=statement_digest, area=statement["area"],
                    formal_statement_language=None if formal is None else formal["language"],
                    files={name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())},
                    decision_id=None, problem_evidence_digest=None, override_id=override["id"], gate="bypassed")
    files["problem-export.json"] = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    return write_problem_export(store, files, manifest, destination)


def write_problem_export(store, files, manifest, destination):
    """Write or reuse the exact file set under the export's digest."""
    if destination is None:
        from .output_views import PROBLEM_EXPORTS_FOLDER, find_workspace_root
        destination = find_workspace_root(store.root) / PROBLEM_EXPORTS_FOLDER / compute_export_digest(files)
    directory = Path(destination).absolute()
    result = dict(manifest, destination=str(directory))
    if directory.exists() or directory.is_symlink():
        if has_exported_files(directory, files):
            return result
        raise ResearchError("review_destination_exists",
                            "The destination already holds files that differ from this export. Export into a new directory",
                            {"destination": str(directory)})
    from .workspace import write_projection
    directory.mkdir(mode=0o700, parents=True)
    for name, content in files.items():
        write_projection(directory, name, content)
    return result


def prepare_posted(records, artifacts, value, recorded_revision=None):
    artifacts = Evaluation.of(records, artifacts)
    require_problem(records)
    fields(value, ("problem_id", "export_directory"))
    text(value["problem_id"], "Posted problem ID")
    text(value["export_directory"], "Problem export directory")
    statement = find_current_statement(records)
    earlier = find_post(records, statement)
    if earlier is not None:
        raise ResearchError("problem_already_posted", "This exact statement already has a recorded post", {"post_id": earlier["id"]})
    from .gates import require_ready
    require_ready(assess_post(records, artifacts), "post")
    files, manifest = build_projection(records, artifacts)
    directory = Path(value["export_directory"])
    if not directory.is_absolute():
        directory = artifacts.root / directory
    try:
        valid = has_exported_files(directory, files)
    except OSError as error:
        raise ResearchError("problem_export_mismatch", "Read the complete approved problem projection", {"export_directory": str(directory)}) from error
    if not valid:
        raise ResearchError("problem_export_mismatch", "The exported files must equal the current reviewed records byte for byte", {"export_directory": str(directory)})
    export_digest = compute_export_digest(files)
    identifier = digest({"problem_id": value["problem_id"], "export_digest": export_digest})
    record = {"id": identifier, "problem_id": value["problem_id"], "statement_id": statement["id"],
              "statement_digest": manifest["statement_digest"], "problem_evidence_digest": manifest["problem_evidence_digest"],
              "export_digest": export_digest, "posted_at": datetime.now(timezone.utc).isoformat()}
    if type(recorded_revision) is not int or recorded_revision < 1:
        raise ResearchError('invalid_input', 'A new post needs its local recording revision')
    record['recorded_revision'] = recorded_revision
    return [immutable_record(records, "problem_post", identifier, record)], record
