"""Shared local-review admission, instrumented capture, and resumable acceptance.

Execution plans adapt frozen scientific tasks without owning their scientific
schema. Dependencies and selectors are {kind, key, digest} references; absence
predicates are {kind, key}. No response projection is an observation authority.

plan_task/reserve_task take task_id, task_generation, epoch_id, route_id and a
request ArtifactRef, with optional dependencies, selectors, absence and
max_attempts (default two, including the initial attempt). The source
review_task must bind generation and epoch_id; review_epoch and review_route
must exist. The request carries history: []. The plan automatically binds all
three source records; callers add their evidence and mutable selection scope.

Records: review_execution_plan/{plan digest} is immutable;
review_task_execution/{task_id} is the task head; review_reservation/{id} owns
one invocation and its lifecycle generation; review_pool_membership/{id}
retains its store/route membership; review_observation/{reservation_id} is the
immutable capture; review_task_acceptance/{reservation_id} is the scientific
receipt. Capacity policy is review_capacity_policy/store, version 1, scope
store, with store_limit, route_limits and covered_adapters. All public writes
return ordinary Store receipts. Scientific acceptance is supplied explicitly
as validate(records, captured_record) -> JSON receipt, and performs no model I/O.
"""

from collections.abc import Mapping
import json
import uuid

from .artifacts import validate_reference
from .errors import ResearchError
from .evidence import digest
from .operations import fields, immutable_record, prepared_task_mutation, text
from .provenance import runtime_provenance
from .storage import _canonical
from .workspace import strict_json


PROTOCOL = "review-task-execution-v1"
COVERED_ADAPTERS = ("claude_cli_v1", "codex_cli_v1", "codex_cli_framed_v1", "codex_cli_visual_v1")


def _positive(value, name):
    if type(value) is not int or value < 1:
        raise ResearchError("invalid_input", name + " must be a positive integer")
    return value


def _get(records, kind, key):
    value = records.get(kind, {}).get(key)
    if value is None:
        raise ResearchError("review_task_dependency_changed", "A required review dependency is absent",
                            {"kind": kind, "key": key})
    return value


class _TransactionKind(Mapping):
    def __init__(self, transaction, kind):
        self._transaction, self._kind = transaction, kind

    def __getitem__(self, key):
        value = self._transaction.get(self._kind, key)
        if value is None:
            raise KeyError(key)
        return value

    def __iter__(self):
        return iter(self._transaction.records(self._kind))

    def __len__(self):
        return len(self._transaction.records(self._kind))

    def values(self):
        return self._transaction.records(self._kind).values()

    def items(self):
        return self._transaction.records(self._kind).items()


class _TransactionRecords(Mapping):
    def __init__(self, transaction):
        self._transaction = transaction

    def __getitem__(self, kind):
        return _TransactionKind(self._transaction, kind)

    def __iter__(self):
        raise ResearchError("invalid_input", "Admission reads explicit record kinds")

    def __len__(self):
        raise ResearchError("invalid_input", "Admission reads explicit record kinds")


def _mutate_current(store, operation, payload, apply, *, request_id, expected_revision=None):
    """Evaluate admission/lifecycle predicates in the serialized write, never in a caller read set."""
    if not isinstance(payload, dict):
        raise ResearchError("invalid_input", "A review operation requires an object")
    payload = json.loads(_canonical(payload))
    snapshot = store._read_scoped_preparation()
    runtime = runtime_provenance()

    def commit(tx, revision):
        result = apply(tx, payload, revision)
        tx.put("literature_operation", request_id, {"operation": operation, "runtime": runtime})
        return result
    return store.mutate_scoped(operation, payload, commit, request_id=request_id,
                               expected_revision=expected_revision, _preparation=snapshot.release_proof())


def _policy(records):
    return records.get("review_capacity_policy", {}).get("store", {
        "version": 1, "scope": "store", "store_limit": 1, "route_limits": {},
        "covered_adapters": list(COVERED_ADAPTERS), "generation": 0})


def record_capacity(store, payload, *, request_id, expected_revision=None):
    """Record store and optional route limits; lowering a limit drains active calls."""
    def apply(tx, value, revision):
        fields(value, ("store_limit", "route_limits"), ("covered_adapters",))
        _positive(value["store_limit"], "Store capacity")
        if not isinstance(value["route_limits"], dict):
            raise ResearchError("invalid_input", "Route limits must be an object")
        for route_id, limit in value["route_limits"].items():
            text(route_id, "Route ID")
            _positive(limit, "Route capacity")
        covered = value.get("covered_adapters", list(COVERED_ADAPTERS))
        if (not isinstance(covered, list) or not covered or any(item not in COVERED_ADAPTERS for item in covered)
                or len(set(covered)) != len(covered)):
            raise ResearchError("invalid_input", "Capacity covers explicitly named supported local adapters")
        previous = _policy(_TransactionRecords(tx))
        record = dict(value, version=1, scope="store", covered_adapters=covered,
                      generation=previous["generation"] + 1, recorded_revision=revision)
        tx.put("review_capacity_policy", "store", record)
        return record
    return _mutate_current(store, "review.capacity", payload, apply,
                           request_id=request_id, expected_revision=expected_revision)


def _references(value, *, absence=False):
    if not isinstance(value, list):
        raise ResearchError("invalid_input", "Task dependency predicates must be arrays")
    seen = set()
    for reference in value:
        fields(reference, ("kind", "key") if absence else ("kind", "key", "digest"))
        text(reference["kind"], "Dependency kind")
        text(reference["key"], "Dependency key")
        if not absence:
            text(reference["digest"], "Dependency digest")
        identity = (reference["kind"], reference["key"])
        if identity in seen:
            raise ResearchError("invalid_input", "Task dependency predicates cannot contain duplicates")
        seen.add(identity)
    return value


def _check_references(records, plan):
    for reference in plan["dependencies"] + plan["selectors"]:
        current = records.get(reference["kind"], {}).get(reference["key"])
        if current is None or digest(current) != reference["digest"]:
            raise ResearchError("review_task_dependency_changed", "The task's frozen dependency is no longer current",
                                {"task_id": plan["task_id"], "dependency": reference})
    for reference in plan["absence"]:
        if records.get(reference["kind"], {}).get(reference["key"]) is not None:
            raise ResearchError("review_task_dependency_changed", "A required task absence predicate no longer holds",
                                {"task_id": plan["task_id"], "dependency": reference})


def _freeze_plan(store, records, value):
    fields(value, ("task_id", "task_generation", "epoch_id", "route_id", "request"),
           ("dependencies", "selectors", "absence", "max_attempts"))
    for key in ("task_id", "epoch_id", "route_id"):
        text(value[key], key)
    _positive(value["task_generation"], "Task generation")
    _positive(value.get("max_attempts", 2), "Maximum attempts")
    task = _get(records, "review_task", value["task_id"])
    epoch = _get(records, "review_epoch", value["epoch_id"])
    route = _get(records, "review_route", value["route_id"])
    if (task.get("generation") != value["task_generation"] or task.get("epoch_id") != value["epoch_id"]):
        raise ResearchError("review_task_dependency_changed", "Task generation or epoch differs from the frozen task")
    if route.get("adapter") not in COVERED_ADAPTERS:
        raise ResearchError("review_route_unsupported", "Owned review execution covers the supported local text routes")
    validate_reference(value["request"])
    request = strict_json(store.build_artifact_store().read(value["request"]))
    if not isinstance(request, dict) or request.get("history") != []:
        raise ResearchError("invalid_input", "A staged review request must carry history: []")
    dependencies = list(_references(value.get("dependencies", [])))
    for kind, key, record in (("review_task", value["task_id"], task),
                               ("review_epoch", value["epoch_id"], epoch),
                               ("review_route", value["route_id"], route)):
        reference = {"kind": kind, "key": key, "digest": digest(record)}
        old = next((r for r in dependencies if (r["kind"], r["key"]) == (kind, key)), None)
        if old is None:
            dependencies.append(reference)
        elif old != reference:
            raise ResearchError("review_task_dependency_changed", "A task's explicit dependency differs from its source")
    plan = {"protocol": PROTOCOL, "task_id": value["task_id"], "task_generation": value["task_generation"],
            "epoch_id": value["epoch_id"], "route_id": value["route_id"], "route_digest": digest(route), "route": route,
            "request": value["request"], "request_digest": digest(request), "dependencies": dependencies,
            "selectors": _references(value.get("selectors", [])),
            "absence": _references(value.get("absence", []), absence=True), "max_attempts": value.get("max_attempts", 2)}
    plan["id"] = digest([PROTOCOL, plan])
    _check_references(records, plan)
    return plan


def _state(plan, revision):
    return {"task_id": plan["task_id"], "task_generation": plan["task_generation"], "plan_id": plan["id"],
            "generation": 1, "attempts": 0, "reservation_id": None, "state": "planned", "recorded_revision": revision}


def plan_task(store, payload, *, request_id, expected_revision=None):
    """Adapt a frozen task into an immutable execution plan without admission."""
    def apply(tx, value, revision):
        records = _TransactionRecords(tx)
        plan = _freeze_plan(store, records, value)
        old = tx.get("review_task_execution", plan["task_id"])
        if old is not None and old["plan_id"] != plan["id"]:
            raise ResearchError("record_conflict", "An execution plan cannot silently replace an existing task plan")
        tx.put(*immutable_record(records, "review_execution_plan", plan["id"], plan))
        result = old if old is not None else _state(plan, revision)
        tx.put("review_task_execution", plan["task_id"], result)
        return result
    return _mutate_current(store, "review.task_plan", payload, apply,
                           request_id=request_id, expected_revision=expected_revision)


def _active_legacy(records, policy):
    routes = records.get("review_route", {})
    attempts = records.get("review_attempt", {})
    return [invocation for key, invocation in records.get("review_invocation", {}).items()
            if key not in attempts and routes.get(invocation.get("route_id"), {}).get("adapter") in policy["covered_adapters"]]


def reserve_task(store, payload, *, request_id, expected_revision=None):
    """Check capacity, currency and duplicate invocation, then reserve every pool atomically."""
    def apply(tx, value, revision):
        records = _TransactionRecords(tx)
        plan = _freeze_plan(store, records, value)
        policy = _policy(records)
        route = _get(records, "review_route", plan["route_id"])
        if route["adapter"] not in policy["covered_adapters"]:
            raise ResearchError("review_route_unsupported", "The capacity policy does not cover this route")
        old = tx.get("review_task_execution", plan["task_id"])
        if old is not None and old["plan_id"] != plan["id"]:
            raise ResearchError("review_task_dependency_changed", "Use a new task identity for a changed frozen execution plan")
        state = old if old is not None else _state(plan, revision)
        if state["reservation_id"] is not None:
            prior = _get(records, "review_reservation", state["reservation_id"])
            observation = records.get("review_observation", {}).get(prior["id"])
            if prior["state"] == "accepted" or (observation is not None and observation["observation"]["status"] == "completed"
                    and prior["state"] != "refused"):
                raise ResearchError("review_resampling_forbidden", "Resume the captured observation instead of repeating its reviewer call")
            if not prior["released"]:
                raise ResearchError("review_invocation_pending", "Reconcile the task's existing reservation before retrying")
        if state["attempts"] >= plan["max_attempts"]:
            raise ResearchError("review_task_exhausted", "The frozen task exhausted its bounded execution attempts")
        epoch = _get(records, 'review_epoch', plan['epoch_id'])
        budgets = epoch.get('budgets', {})
        used = sum(row['epoch_id'] == epoch['id'] for row in records.get('review_reservation', {}).values())
        controller = records.get('review_assignment', {}).get(epoch.get('controller_assignment_id'), {}) if epoch.get('controller_assignment_id') else {}
        import time
        elapsed = time.time() - controller.get('staged_started_at', time.time())
        if used >= budgets.get('max_calls', float('inf')) or elapsed >= budgets.get('max_elapsed_seconds', float('inf')):
            raise ResearchError('assessment_incomplete', 'The epoch exhausted its shared execution budget',
                                dict(epoch_id=epoch['id'], task_id=plan['task_id'], attempts=used, elapsed_seconds=elapsed))
        active = [r for r in records.get("review_reservation", {}).values() if not r["released"]]
        legacy = _active_legacy(records, policy)
        route_active = [r for r in active + legacy if r["route_id"] == plan["route_id"]]
        if len(active) + len(legacy) >= policy["store_limit"] or len(route_active) >= policy["route_limits"].get(
                plan["route_id"], policy["store_limit"]):
            raise ResearchError("review_capacity_wait", "Shared reviewer capacity is occupied; reconcile its reservations",
                                {"reservations": [r["id"] for r in active + legacy], "scope": "store"})
        reservation_id = "review-reservation:" + digest([plan["id"], request_id])
        reservation = {"protocol": PROTOCOL, "id": reservation_id, "plan_id": plan["id"],
                       "task_id": plan["task_id"], "task_generation": plan["task_generation"],
                       "epoch_id": plan["epoch_id"], "route_id": plan["route_id"], "attempt": state["attempts"] + 1,
                       "generation": 1, "state": "reserved", "released": False, "token": uuid.uuid4().hex,
                       "launch": None, "worker": None, "children": [], "termination": None,
                       "observation_id": None, "recorded_revision": revision}
        tx.put(*immutable_record(records, "review_execution_plan", plan["id"], plan))
        tx.put(*immutable_record(records, "review_reservation", reservation_id, reservation))
        tx.put("review_pool_membership", reservation_id, {"reservation_id": reservation_id, "store_pool": "store",
               "route_pool": plan["route_id"], "active": True, "recorded_revision": revision})
        tx.put("review_task_execution", plan["task_id"], dict(state, generation=state["generation"] + 1,
               attempts=reservation["attempt"], reservation_id=reservation_id, state="reserved", recorded_revision=revision))
        return reservation
    return _mutate_current(store, "review.task_reserve", payload, apply,
                           request_id=request_id, expected_revision=expected_revision)


def _transition(tx, reservation, revision, **updates):
    result = dict(reservation, **updates)
    result.update(generation=reservation["generation"] + 1, recorded_revision=revision)
    tx.put("review_reservation", result["id"], result)
    state = tx.get("review_task_execution", result["task_id"])
    if state is not None and state["reservation_id"] == result["id"]:
        tx.put("review_task_execution", result["task_id"], dict(state, state=result["state"],
               generation=state["generation"] + 1, recorded_revision=revision))
    return result


def capture_observation(store, reservation_id, observation, *, request_id, _authority=None):
    """Only a live owned worker may commit its in-memory instrumented observation."""
    from .review_worker import _WorkerAuthority
    if not isinstance(_authority, _WorkerAuthority):
        raise ResearchError("review_instrumented_capture_required", "Only the owned live review worker captures observations")
    def apply(tx, value, revision):
        records = _TransactionRecords(tx)
        reservation = _get(records, "review_reservation", reservation_id)
        _authority.verify(store, reservation)
        if reservation["released"] or reservation["state"] != "running":
            raise ResearchError("review_worker_reconciliation_required", "This reservation cannot capture a new observation")
        if not isinstance(observation, dict) or observation.get("status") not in ("completed", "failed", "partial", "timed_out"):
            raise ResearchError("invalid_input", "Capture requires a transport observation with its execution status")
        plan = _get(records, "review_execution_plan", reservation["plan_id"])
        if (observation.get("request") is None or digest(strict_json(store.build_artifact_store().read(
                observation["request"]))) != plan["request_digest"]):
            raise ResearchError("review_instrumented_capture_required", "Capture must retain the exact admitted request")
        for name in ("request", "response", "output"):
            reference = observation.get(name)
            if reference is not None:
                store.build_artifact_store().read(reference)
        captured = {"id": reservation_id, "protocol": PROTOCOL, "origin": "instrumented", "plan_id": plan["id"],
                    "task_id": plan["task_id"], "task_generation": plan["task_generation"],
                    "reservation_id": reservation_id, "worker": reservation["worker"], "observation": observation,
                    "recorded_revision": revision}
        tx.put(*immutable_record(records, "review_observation", reservation_id, captured))
        _transition(tx, reservation, revision, observation_id=reservation_id,
                    state="captured" if observation["status"] == "completed" else "known_failure")
        return captured
    return _mutate_current(store, "review.task_capture", {"reservation_id": reservation_id, "observation": observation},
                           apply, request_id=request_id)


def accept_observation(store, payload, validate, *, request_id, expected_revision=None):
    """Validate captured science outside the write; commit its receipt without external I/O."""
    if not callable(validate):
        raise ResearchError("invalid_input", "Acceptance requires the task's scientific completion validator")
    def prepare(records, value):
        fields(value, ("reservation_id",))
        reservation = _get(records, "review_reservation", value["reservation_id"])
        captured = records.get("review_observation", {}).get(reservation["id"])
        if captured is None:
            raise ResearchError("review_observation_acceptance_pending", "No committed observation is available for acceptance")
        existing = records.get("review_task_acceptance", {}).get(reservation["id"])
        if existing is not None:
            return [], existing
        refusal = records.get("review_task_refusal", {}).get(reservation["id"])
        if refusal is not None:
            return [], dict(refusal={key:value for key,value in refusal.items() if key != "recorded_revision"},
                            recorded_revision=refusal["recorded_revision"])
        plan = _get(records, "review_execution_plan", reservation["plan_id"])
        _check_references(records, plan)
        state = _get(records, "review_task_execution", plan["task_id"])
        if state["reservation_id"] != reservation["id"]:
            raise ResearchError("review_task_dependency_changed", "A historical capture cannot replace the task's selected work")
        if captured["observation"]["status"] != "completed":
            raise ResearchError("review_task_failed", "A captured execution failure supplies no scientific completion")
        for name in ("request", "response", "output"):
            reference = captured["observation"].get(name)
            if reference is not None:
                store.build_artifact_store().read(reference)
        try:
            scientific = validate(records, captured)
        except ResearchError as error:
            if (error.code not in ('invalid_review_staged', 'review_composition_conflict', 'invalid_value_review',
                                  'invalid_review_adjudication', 'review_adjudication_mismatch', 'review_composition_pending')
                    and not (getattr(error, 'contract_check', None) and not getattr(error, 'contract_external', False))):
                raise
            refusal = dict(reservation_id=reservation['id'], task_id=plan['task_id'], plan_id=plan['id'],
                observation_digest=digest(captured), code=error.code, message=str(error), details=error.details)
            return [('review_task_refusal', reservation['id'], refusal),
                ('review_reservation', reservation['id'], dict(reservation, state='refused', generation=reservation['generation'] + 1)),
                ('review_task_execution', plan['task_id'], dict(state, state='refused', generation=state['generation'] + 1))], dict(refusal=refusal)
        if not isinstance(scientific, dict):
            raise ResearchError("invalid_input", "A task validator returns its scientific receipt object")
        accepted = {"reservation_id": reservation["id"], "task_id": plan["task_id"], "plan_id": plan["id"],
                    "task_generation": plan["task_generation"], "observation_digest": digest(captured), "receipt": scientific}
        return [("review_task_acceptance", reservation["id"], accepted),
                ("review_reservation", reservation["id"], dict(reservation, state="accepted", generation=reservation["generation"] + 1)),
                ("review_task_execution", plan["task_id"], dict(state, state="accepted", generation=state["generation"] + 1))], accepted
    def finalize(changes, result, revision):
        if changes:
            for _, _, record in changes:
                record["recorded_revision"] = revision
            result["recorded_revision"] = revision
        return changes, result
    return prepared_task_mutation(store, "review.task_accept", payload, prepare, request_id=request_id,
                                   expected_revision=expected_revision, finalize=finalize)


def _release(tx, reservation, revision, termination):
    result = _transition(tx, reservation, revision, released=True, termination=termination)
    membership = tx.get("review_pool_membership", reservation["id"])
    tx.put("review_pool_membership", reservation["id"], dict(membership, active=False, recorded_revision=revision))
    return result


def reconcile_worker(store, payload, *, request_id, expected_revision=None):
    """Preserve capture, classify uncaptured death, and release only confirmed dead owners/groups."""
    from .review_worker import _owner, _termination_guard
    fields(payload, ("reservation_id",))
    reservation_id = text(payload["reservation_id"], "Reservation ID")
    with _owner(store.root, reservation_id):
        with _termination_guard(store, reservation_id) as termination:
            def apply(tx, value, revision):
                reservation = tx.get("review_reservation", reservation_id)
                if reservation is None:
                    raise ResearchError("review_task_dependency_changed", "Review reservation is absent")
                if reservation["released"] or termination is None:
                    return reservation
                if reservation["observation_id"] is None:
                    reservation = _transition(tx, reservation, revision,
                        state="unknown" if reservation["state"] == "running" else "known_failure")
                return _release(tx, reservation, revision, termination)
            return _mutate_current(store, "review.worker_reconcile", payload, apply,
                                   request_id=request_id, expected_revision=expected_revision)


def release_reservation(store, payload, *, request_id, expected_revision=None):
    """Release a reconciled reservation after rechecking real process termination."""
    from .review_worker import _owner, _termination_guard
    fields(payload, ("reservation_id",))
    reservation_id = text(payload["reservation_id"], "Reservation ID")
    with _owner(store.root, reservation_id):
        with _termination_guard(store, reservation_id) as termination:
            def apply(tx, value, revision):
                reservation = tx.get("review_reservation", reservation_id)
                if reservation is None:
                    raise ResearchError("review_task_dependency_changed", "Review reservation is absent")
                if reservation["released"]:
                    return reservation
                if termination is None or reservation["state"] in ("planned", "reserved", "running"):
                    raise ResearchError("review_worker_reconciliation_required", "Confirm and reconcile worker termination before releasing capacity")
                return _release(tx, reservation, revision, termination)
            return _mutate_current(store, "review.reservation_release", payload, apply,
                                   request_id=request_id, expected_revision=expected_revision)
