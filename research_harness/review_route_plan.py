"""Every route record that a plan names takes its command: the one rule for every plan.

A route record takes no assignment, no run, no probe and no output correction once it pins another reviewer runtime
than the installed one, and no assignment, no run and no output correction once it is retired (REFUSES_RETIRED_ROUTE).
plan_next passes every completed plan through apply_route_rule, and every step that names a route record for new
work takes that record from select_current_route, so no plan and no step names a record that its command refuses.
"""

from . import review_protocol as protocol
from .errors import ResearchError

# The four commands that read a stored route record, and whether each refuses a retired one. Each refuses a stale
# one. tests/test_review_route_plan.py compares this table with the commands.
REFUSES_RETIRED_ROUTE = {'review-assignment': True, 'review-run': True, 'review-output-correction': True,
                         'review-probe': False}
# The members of a plan that its action or its handoff owns. A replaced plan keeps its other members.
REPLACED_PLAN_MEMBERS = ('command', 'payload', 'not_before', 'request_id', 'code', 'details', 'stop_class',
                         'capacity_forecast')

# A retirement withdraws the route record for every head on it. A head with a refused answer takes its replacement on
# a renewed route record only while its own route record is stale and not retired.
RETIREMENT_ORDER_TEXT = (
    'Before the retirement, run review-series on every other head of this route record that holds a refused answer. '
    'That head then takes its replacement first.')
STALE_ROUTE_PLAN_MSG = (
    'This handoff has one of two causes. Cause 1: the route record of this head pins another reviewer runtime than '
    'the installed one. Cause 2: the last call of this head ended because the reviewer runtime changed during the '
    'call. The head takes no assignment, no run and no probe on that route record, and its next step cannot move to '
    'another route record. Take the steps of the stale-route procedure in the order of details.steps. The work of '
    'this head continues on a route record with the same adapter, model, endpoint and configuration that pins the '
    'installed runtime. details.renewal says what the store holds for that route record. When details.renewal.lacks '
    'is record, the store holds none: the step with the command review-route records one, and its probe follows. '
    'When details.renewal.lacks is probe, the step with the command review-probe probes the record that '
    'details.renewal.route_id names. When details.renewal.lacks is null, that record is verified. The assignment of '
    'the last step can name the record as route_id. '
    + RETIREMENT_ORDER_TEXT)
STALE_ROUTE_ANSWER_MSG = (
    'This head holds an observed answer, and its context is not verified now. The route record that '
    'details.route_id names pins another reviewer runtime than the installed one. Such a route record takes no '
    'probe, so no probe can verify that context again. Take the steps of details.steps in their order. First retire '
    'the route record. Then run review-series on this head again. It names the step that follows the retirement. '
    + RETIREMENT_ORDER_TEXT)
CORRECTION_ROUTE_MSG = (
    'The output correction of this head needs a route record that pins the installed runtime, and the route record '
    'of the head pins another one. details.renewal.lacks names what the store lacks for the correction. The value '
    'record means a route record with the same adapter, model, endpoint and configuration that pins the installed '
    'runtime. The value probe means a probe that verifies the route record that details.renewal.route_id names. '
    'Take details.step. Then run review-series on this head again. It then names the output correction with the id '
    'of that route record. Nothing was recorded, and the correction stays available.')
SUCCESSOR_ROUTE_MSG = (
    'The new chain needs a route record that pins the installed runtime, and the route record of the stopped chain '
    'pins another one. details.renewal.lacks names what the store lacks. The value record means a route record with '
    'the same adapter, model, endpoint and configuration that pins the installed runtime. The value probe means a '
    'probe that verifies the route record that details.renewal.route_id names. Take details.step. Then run '
    'review-series on this head again. It then states the admission of the new chain on that route record. When '
    'that record admits the new chain, the plan names the export and the assignment there. Otherwise the plan is '
    'the stop of that admission. Until then, details.successor states the admission on the route record of the '
    'stopped chain. Export nothing before the step.')
UNVERIFIED_RECORD_MSG = (
    'The context of this head is not verified now, because the latest probe of the route record that '
    'details.route_id names did not verify that record. An output correction of this head ran on it. That route '
    'record pins another reviewer runtime than the installed one, so it takes no probe. No step of the harness '
    'verifies the context again. details.decision states what the owner decides.')


def read_route_standing(records, artifacts, route_id):
    """Whether a stored route record is retired and whether it is stale, as the admission of an assignment reads
    them: dict(retired, stale). A reviewer runtime that cannot be read leaves the record not stale, as the
    admission does."""
    try:
        stale = protocol.is_route_stale(artifacts, records['review_route'][route_id])
    except ResearchError as error:
        if error.code != 'review_runtime_missing':
            raise
        stale = False
    return dict(retired=route_id in protocol.retired_routes(records), stale=stale)


def list_current_routes(records, artifacts, route):
    """The other route records with the adapter, model, endpoint and configuration of route that pin the installed
    runtime and are not retired, latest first."""
    retired = protocol.retired_routes(records)
    return sorted((row for row in records['review_route'].values()
                   if row['id'] != route['id'] and row['id'] not in retired
                   and protocol.context_repair.is_same_route(route, row)
                   and not protocol.is_route_stale(artifacts, row)),
                  key=lambda row: (row['recorded_revision'], row['id']), reverse=True)


def select_current_route(records, artifacts, route, *, after_revision=0):
    """What the store holds for a step that moves from route to a record of the same identity: dict(route_id,
    lacks). lacks is 'record' when no such record exists, 'probe' when none of them is verified by a probe that was
    recorded after after_revision (route_id then names the latest record), and None when one is (route_id names the
    latest verified one)."""
    candidates = list_current_routes(records, artifacts, route)
    if not candidates:
        return dict(route_id=None, lacks='record')
    probes = records.get('review_route_probe', {})
    for row in candidates:
        state = protocol.route_state(records, artifacts, row['id'])
        if (state['context_status'] == 'verified_for_route'
                and probes[state['probe_id']]['recorded_revision'] > after_revision):
            return dict(route_id=row['id'], lacks=None)
    return dict(route_id=candidates[0]['id'], lacks='probe')


def select_step_route(records, artifacts, route_id):
    """The route record for a step that starts new work of a head on route_id: dict(route_id, lacks). It is the
    record itself while that record takes an assignment, and what select_current_route gives once it is stale. After
    a retirement the route is a choice of the agent: dict(route_id=None, lacks=None)."""
    standing = read_route_standing(records, artifacts, route_id)
    if standing['retired']:
        return dict(route_id=None, lacks=None)
    if not standing['stale']:
        return dict(route_id=route_id, lacks=None)
    return select_current_route(records, artifacts, records['review_route'][route_id])


def find_unverified_route(records, artifacts, assignment):
    """The route record whose probe ends an unverified context of this head: the record of its accepted output
    correction when that record is not verified, otherwise the route of the head."""
    from .review_output_correction import selected_correction
    correction = selected_correction(records, artifacts, assignment['id'])
    if (correction is not None and correction['route_id'] != assignment['route_id'] and protocol.route_state(
            records, artifacts, correction['route_id'])['context_status'] != 'verified_for_route'):
        return correction['route_id']
    return assignment['route_id']


def list_route_uses(assignment, plan):
    """Every (command, route record id) that this plan names for a command that reads a stored route record: its
    own action, and every object of its details, at any depth, that holds such a command and a payload with
    route_id. These objects are the steps of details.step and details.steps, their then rows and the steps of the
    answers of a decision. A replay of a recorded call reads no route record: the state resume, review-run with the
    action reconcile or accept, and every action or step that carries the request_id of the recorded call."""
    uses = []
    payload = plan.get('payload') or {}
    if (plan['state'] in ('continue', 'replace', 'plan', 'run', 'wait')
            and plan.get('command') in REFUSES_RETIRED_ROUTE and 'request_id' not in plan
            and payload.get('action') not in ('reconcile', 'accept')):
        uses.append((plan['command'], payload.get('route_id', assignment['route_id'])))
    pending = [plan.get('details')]
    while pending:
        value = pending.pop()
        if isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, dict):
            command, payload = value.get('command'), value.get('payload')
            if (isinstance(command, str) and command in REFUSES_RETIRED_ROUTE and isinstance(payload, dict)
                    and payload.get('route_id') is not None and 'request_id' not in value):
                uses.append((command, payload['route_id']))
            pending.extend(value.values())
    return uses


def find_refused_use(records, artifacts, assignment, plan):
    """The (command, route record id, standing) of this plan whose route record refuses the command, or None. A use
    of the route of the head comes before a use of another record. A probe of the retired route record of the head
    counts as refused for a head without an observed answer: that probe prepares a run, and a retired record takes
    none. With an observed answer the probe verifies that answer."""
    standings, refused = {}, []
    for command, route_id in list_route_uses(assignment, plan):
        if route_id not in records.get('review_route', {}):
            continue
        if route_id not in standings:
            standings[route_id] = read_route_standing(records, artifacts, route_id)
        standing = standings[route_id]
        if standing['stale'] or standing['retired'] and (
                REFUSES_RETIRED_ROUTE[command] or route_id == assignment['route_id']
                and protocol.assignment_state(records, artifacts, assignment['id']).get('output') is None):
            refused.append((command, route_id, standing))
    refused.sort(key=lambda use: use[1] != assignment['route_id'])
    return refused[0] if refused else None


def plan_stale_route(assignment, renewal=None):
    """The handoff of a head whose route record is stale. Without a renewal the next step cannot move to another
    route record, and complete_plan lists the steps of the stale-route procedure."""
    return dict(state='handoff', command='review-route', code='review_route_runtime_stale',
                details=dict(assignment_id=assignment['id'], route_id=assignment['route_id'],
                             renewal=renewal or dict(admitted=False, route_id=None, lacks=None)))


def plan_retired_route(assignment):
    """The handoff of a head whose route record is retired: its slot continues through a context repair."""
    return dict(state='handoff', command='review-assignment', code='review_route_retired',
                details=dict(assignment_id=assignment['id'], route_id=assignment['route_id']))


def apply_route_rule(records, artifacts, assignment, plan):
    """Return this completed plan, or the plan that replaces it when it names a route record for a command that the
    record refuses, or when a failed call says that the route record is stale and the plan states no renewal.

    The route record of the head gives the handoff of its retirement or the stale-route procedure. Another route
    record is built from select_current_route and takes its command, with one exception: the probe that verifies an
    observed answer on the route record of an accepted output correction (find_unverified_route). When that record
    refuses the probe, the plan is the stop of its code. A plan that names any other refusing record is returned as
    it is."""
    from .review_plan_steps import complete_plan
    details = plan.get('details') or {}
    refused = find_refused_use(records, artifacts, assignment, plan)
    if refused is None:
        if plan.get('code') != 'review_route_runtime_stale' or 'renewal' in details:
            return plan
        standing = read_route_standing(records, artifacts, assignment['route_id'])
        replacement = plan_retired_route(assignment) if standing['retired'] else plan_stale_route(assignment)
    elif refused[1] != assignment['route_id']:
        if (plan.get('code') != 'review_context_unverified'
                or refused[1] != find_unverified_route(records, artifacts, assignment)):
            return plan
        replacement = dict(state='stop', code='review_context_unverified', details=dict(
            assignment_id=assignment['id'], route_id=refused[1], message=UNVERIFIED_RECORD_MSG))
    elif refused[2]['retired']:
        replacement = plan_retired_route(assignment)
    else:
        replacement = plan_stale_route(assignment)
    kept = {key: value for key, value in plan.items() if key not in REPLACED_PLAN_MEMBERS}
    return complete_plan(records, artifacts, assignment, dict(kept, **replacement))
