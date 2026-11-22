"""Inventory, bounded turns and admission for problem reading protocol v7.

Pure admission and planning publish nothing. Assignment builders store their
bound inventory, plan and state after choosing a protocol. Released v5/v6
verifiers own old-chain authentication; D2 supplies carried projections. The
separate v7 verification unit owns scientific chain verification and activation.

The foundation inventory separates logical u units from physical b material.
Prepared packets use even u handles for logical units and odd u handles for physical delivery.
Every material body occurs once by SHA-256. A logical search lists the shared bodies and residual that rebuild it.
Packets bind the inventory and state artifacts by their SHA-256 instead of carrying their growing collections.
"""

import copy
import hashlib
import json
import math
from types import MappingProxyType

from . import openness_reading as reading, review_excerpts
from .artifacts import describe_artifact
from .errors import ResearchError, describe_error_at
from .evidence import digest
from .operations import JSON_TYPE_NAMES, describe_json_type
from .problem_openness import serialize_record
from .review_route_capacity import divide_room
from . import review_route_capacity as capacity
from .review_search_delivery import plan_search_delivery

PROTOCOL = 'exactory-openness-reading-v7'
V7_FINAL_FORMS = ('request',)
# A math chain is new in its release, so both forms of its final are served from its first chain.
MATH_V2_FINAL_FORMS = ('request', 'pages')
VISUAL_TEXT_ROUTE_CODE = 'review_visual_text_route_required'
VISUAL_TEXT_ROUTE_MESSAGE = ('Protocol v7 delivers text units. Register and probe a text route of the same model, '
                             'then export, check and assign the reading there. Text turns on the visual transport '
                             'ship with the transport release 0.93.0.')
VISUAL_TEXT_ROUTE_QUESTION = 'Which text route of this model should read the packet?'
VISUAL_TEXT_ROUTE_ANSWER = 'Register and probe the text route of the same model.'
LABEL_CHARACTERS = 120
SELECTION_MISMATCH_MESSAGE = (
    'The carried selection of this chain does not verify. One of four cases is the cause. First, under '
    'exactory-openness-carried-selection-v2, the stored selection is not the selection that the harness makes again '
    'on the store as it stood before the first assignment. Second, an earlier chain no longer authenticates up to its '
    'last carried turn under the records as they stand now. A contamination event on a carried turn has this effect, '
    'and so has a failed latest probe of the route record of a carried chain. Third, a stored item of a carried turn '
    'does not match the authenticated turn that it comes from or the inventory of this chain. Fourth, the stored '
    'selection has a form or a protocol that this release does not verify. A carried turn whose answer fails the '
    'output check of its turn gives the first case under exactory-openness-carried-selection-v2 and the second case '
    'under exactory-openness-carried-selection-v1. This holds with or without a refusal record for that answer. The '
    'chain gives no finding, and a finding that it recorded is stale. When the latest probe of the route record of a '
    'carried chain failed, probe that route record again. A completed probe of that route record ends the refusal. No '
    'probe can end it when review-probe refuses that route record with review_route_runtime_stale. In that case and '
    'in every other case, check the record with record-check. When record-check reports the record ready, export a '
    'new packet with the same reviewer_id. Assign it for a new finding. The new chain carries the turns of earlier '
    'chains that still authenticate from their first turn on. It carries no turn of this chain, and it reads again '
    'what this chain read itself.')
ALLOWANCES_CHARACTERS = 30000
TARGET_LABELS_CHARACTERS = 100000
# The largest finding index in the frame of a final, in the measure of the route. A chain records its size in
# inventory.fixed_bounds at its first assignment and uses that size to its end; beyond it the longest values other
# than queries stay in the bodies of their bound searches, and the index names those bodies.
FINDING_INDEX_CHARACTERS = 100000
# The label of the inventory unit that holds a finding index too long for any form with unit references.
FINDING_INDEX_LABEL = 'Finding index'
DECISIVE_WORK_IDS_CHARACTERS = 10000
LIST_ROW_CHARACTERS = 2000
PURPOSE_CHARACTERS = 200
GAP_CHARACTERS = 600
PASSAGE_REQUESTS = 10
AFFECTED_INDEX_CHARACTERS = 10000
# The texts below are parts of the v7 reading prompt (review_protocol.V7_READING_PROMPT), which a chain keeps from its
# first assignment to its end. A later change of one of them needs a new protocol or a registered paragraph version.
CARRIED_READINGS_TEXT = (
    '\n\nCarried readings. A chain can start with verified reading turns of earlier chains that the same reviewer ran '
    'over the same statement, the same formulations and the same route. The harness authenticated those turns under '
    'their own protocols. They are your own earlier readings: this chain delivers no range again that they read in '
    'a unit that is unchanged, and plan.delivered counts their ranges. In the first turn, reading_turn.notes holds '
    'the latest carried notes that rest only on unchanged units. The carried excerpts and passage answers reach the '
    'final turn as entries, as far as the pool holds them: the part of the room of the final that the plan sets '
    'aside for carried entries.')
CARRIED_KEEP_TEXT = (
    ' The entries that the pool cannot hold arrive in a unit of kind carried_reading whose content is the JSON text '
    '{entries,origin}. Each of its entries is {handle,kind,unit,start,end,sha256,text,purpose,status,targets}, with '
    'an e handle and no targets. Such an entry reaches the final turn only when you keep it: once the whole unit has '
    'been delivered, add {keep:<its e handle>,targets:[<target handles>]} to excerpts. The harness then charges the '
    'entry to the allowance of every named target, as it charges an excerpt. An entry that the remaining allowance '
    'of a named target cannot hold is not retained and cannot be kept again.')
AFFECTED_NOTES_TEXT = (
    '\n\nAffected notes. Carried notes are affected when a unit that they rest on has changed or has left '
    'the packet. They are not in reading_turn.notes. They arrive in a unit of kind carried_reading whose '
    'content is the JSON text {notes:{classification,text,units},origin}: text holds the notes, and units '
    'lists each unit that they rest on as {identity,sha256,current_sha256}, where current_sha256 differs '
    'from sha256 for a changed unit and is null for a unit that left the packet. The chain delivers the '
    'required affected notes before every other unit: the newest ones, as many as fit the material '
    'allowance with which the chain starts, and at least one. Read them against the present units, and '
    'carry what still holds into your notes. reading_turn.affected_notes gives the number and the '
    'characters of the affected notes that the chain delivers only on request. In the first turn its '
    'index lists them, the newest first, as rows {handle,chain,characters}: handle names the unit of one '
    'note, chain the chain that it comes from, and characters its length. omitted counts the rows that '
    'the index leaves out, and the list affected_notes holds every row. To read a note, send a passage '
    'request with its handle as unit, anchor null, and as characters the characters of its row. The '
    'request is charged to allowances.passages like every passage request, so characters is at most '
    '100,000 and at most what that allowance has left; a smaller number delivers the start of the note.')
# For the author of a new export; the math-conjecturer skill states the same rule. No code reads this constant.
KEEP_REVIEWER_TEXT = ('A new chain carries the verified reading turns of an earlier chain only under the same '
                      'reviewer_id, author, statement, formulations and route. Keep the reviewer_id of the earlier '
                      'chain when you export again for the same statement.')
PASSED_OVER_TEXT = ('The earlier chain {chain} read the same statement on the same route under another reviewer_id, '
                    'so this chain carries none of its turns.')
UNAVAILABLE_CHAIN_TEXT = ('The earlier reading chain {chain} has the author and the route identity of this chain, '
                          'and another reviewer_id. The store cannot give one of its artifacts, and this row '
                          'holds the code and the details of that refusal. So this report cannot say whether that '
                          'chain belongs in passed_over, which lists the earlier chains that differ from this chain '
                          'only in the reviewer_id. This chain carries none of its turns in either case. This needs '
                          'no action for this chain.')
SUPERSEDED_TEXT = ('Each superseded excerpt or passage answer of the chain {chain} quotes text that a later chain read '
                   'again (entries: {entries}, units that they quote: {units}). Such an entry stays in the records of '
                   'its own chain. A chain of exactory-openness-reading-v7 carries the later reading of that text in '
                   'its place. This needs no action.')
FINAL_MATERIAL_FIELDS = ('notes', 'entries', 'gaps', 'material', 'sources', 'fill')
TARGET_PAGE_SENTENCE = (
    ' The list of formulations, the statement record and the other fields of the native packet each '
    'become units on their own when they are too long for the fixed part of a request, and these units '
    'arrive before the full texts and the records. When the list of formulations moves, '
    'reading_turn.formulations is {first,last,count}, and units of kind target_page deliver '
    'the list of {handle,formulation}; every target handle from first to last is valid in an answer. '
    'When the statement record moves, reading_turn.statement is {unit:"statement"}, and a unit of kind '
    'fixed_field with the label statement delivers the record. A unit of kind fixed_field with the label '
    'native_frame delivers the other fields. A unit of kind fixed_field with the label decisive_work_ids '
    'delivers the ids of the decisive works when their list is too long. Read these units first.')
LIST_PAGE_SENTENCE = (
    ' A request {id,kind:"list_page",list,after} asks for a page of a list. '
    'list is allowances, affected_notes, unread or unread_attempts, and an answer holds at most one request for '
    'each list. after is null for the first page, also when the packet already shows rows of the list, and then the '
    'next of the latest page. A later turn delivers the page as a row of kind list_page with page: '
    '{list,after,next,remaining} and the JSON content {list,after,rows,next,remaining}. The rows keep one fixed '
    'order, and next null ends the list. The harness refuses a request for a '
    'list that has ended or holds no row, and a request with another value of after. A page uses room of its turn '
    'and no allowance.')
# What neither a smaller task nor a registered route serves. plan_capacity_recovery gives it for a v7 turn without a
# smaller task, and describe_admission_stop for a packet that no registered route holds.
FINAL_PAGES_STATEMENT = (
    'A chain of exactory-openness-reading-v7 takes its final in one request, and the task of a reading turn is not '
    'sized below the lowest material level of the stored plan. This release holds no smaller task than these: a '
    'final in several pages is not part of it.')
FINAL_PAGE_FLOOR_STATEMENT = 'The smallest v7 final page needs its frame, notice reserve, minimum notes and one target at its minimum.'
INVALID_READING_MESSAGE = (
    'This answer is not the reading response of exactory-openness-reading-v7. Return exactly '
    '{schema_version:2,stage:"reading",subject_digest,claim_digest,reviewer,notes,excerpts,gaps,passage_requests}, '
    'with the three bindings of the packet. notes is a nonblank string within allowances.notes, also as the request '
    'carries it. An excerpt is {unit,text,purpose,targets}, with text copied exactly from the content that this turn '
    'delivers for that unit, or {keep,targets} for a carried entry whose unit was delivered whole. A gap is '
    '{formulation,text}, with a target handle and 1 to 600 characters. passage_requests holds at most ten requests, '
    'each {id,unit,anchor,characters} or {id,kind:"list_page",list,after} with an id of 1 to 120 characters, within '
    'the passage allowance and with the cursor of its list; in a final turn it holds at least one. path names the '
    'member of the answer that failed, and details gives the values that the rule compared. For the notes, characters '
    'is their length, notes_cost the characters that they take in the next request, and bound allowances.notes. For '
    'a list page, rows 0 marks a list that holds no row, ended a list that has ended, and expected_after the cursor '
    'that the list expects. For a passage, requested and remaining are the characters that the request takes and '
    'that the passage allowance has left.')
NOTES_COST_TEXT = (
    ' The same bound holds for the size that the notes add to the next request, where escaping makes a quote, a '
    'backslash or a line break cost several characters.')
ADMISSION_STOP_MESSAGE = (
    'No reading protocol of this release admits this packet on this route, so the harness records no assignment. '
    'details.admissions gives the refusal of each protocol in its order. details.targets is the number of '
    'formulations of the packet. details.largest_one_request_targets, when it is above 0, is a number of '
    'formulations with which exactory-openness-reading-v7 admits the packet on this route: the statement and its '
    'first neighbours, counted up to the first number that the protocol refuses. details.serving_routes lists the '
    'verified registered routes whose input holds this review as one request. It does not show that a route admits a '
    'chain: the check of review-assignment in the steps of an answer does. When the list is empty, '
    'details.unserved_range states the smallest tasks of this release. details.decision states what the owner '
    'decides and the steps of each answer.')
ADMISSION_SMALLER_ANSWER = 'Record a statement with fewer neighbours'
ADMISSION_ROUTE_ANSWER = 'Run the review on another route'
ADMISSION_QUESTION = 'Whether this review runs over a statement with fewer neighbours, or on another route.'


class _PlanningArtifacts:
    """An invocation-local overlay makes admission pure, including derivative artifacts."""
    def __init__(self, source):
        self.source = source
        self.root = getattr(source, 'root', None)
        self.bodies = {}

    def read(self, reference):
        content = self.bodies.get(reference['sha256'])
        return self.source.read(reference) if content is None else content

    def require_local_read(self, purpose):
        from .evaluation import Evaluation
        source = self.source.store if isinstance(self.source, Evaluation) else self.source
        source.require_local_read(purpose)

    def put(self, content, media_type):
        reference = describe_artifact(content, media_type)
        self.bodies[reference['sha256']] = content
        return reference


def derive_inherited_ratio(records, artifacts, native, route):
    """Return the densest applied ratio for this statement and exact route identity.

    Class constants and stored plans already include the policy margin. Only raw
    single-send measurements get the margin, once, before comparison. Outcome,
    reviewer, selected attempt and carried-reading eligibility do not filter it.
    """
    identity = ('adapter', 'model', 'endpoint', 'configuration')
    best = None
    for assignment in sorted(records.get('review_assignment', {}).values(),
                             key=lambda row: (row.get('recorded_revision', 0), row['id'])):
        candidate = records.get('review_route', {}).get(assignment.get('route_id'))
        if (assignment.get('role') != 'native_math' or not assignment.get('reading_chain')
                or candidate is None or any(candidate.get(key) != route.get(key) for key in identity)):
            continue
        reference = assignment.get('context', {}).get('native_packet')
        if reference is None:
            continue
        binding = json.loads(artifacts.read(reference))
        if any(binding.get(key) != native[key] for key in ('subject_digest', 'claim_digest')):
            continue
        chain = assignment['reading_chain']['first_assignment_id']
        values = []
        if assignment.get('capacity_plan'):
            plan = capacity.read_chain_capacity_plan(artifacts, assignment)
            ratio = plan['measure'].get('characters_per_token')
            if ratio is not None:
                values.append(dict(characters_per_token=ratio,
                                   source=dict(chain=chain, plan=assignment['capacity_plan']['digest'])))
        for attempt in sorted(records.get('review_attempt', {}).values(),
                              key=lambda row: (row.get('recorded_revision', 0), row['id'])):
            if attempt['assignment_id'] == assignment['id']:
                # Round down like the class constants (AR17.1), without floating
                # multiplication near a hundredth boundary.
                ratio = capacity.measure_attempt_ratio(attempt)
                if ratio is not None:
                    values.append(dict(characters_per_token=ratio,
                                       source=dict(chain=chain, attempt=attempt['id'])))
        for value in values:
            if best is None or value['characters_per_token'] < best['characters_per_token']:
                best = value
    return best


def list_target_labels(native):
    minimum = native['scientific_context']['openness_minimum']
    return [dict(handle='t%d' % (index + 1), formulation=value)
            for index, value in enumerate([minimum['statement']] + minimum['neighbours'])]


UNREAD_PACKET_CHARACTERS = 30000
UNREAD_CORE_CHARACTERS = 1500
# The reading frame of a chain whose inventory holds finding_units, which is a chain whose final names a value by its
# body, holds this many characters for the member reading_turn.finding_units and the paragraph finding-units (G8.11).
FINDING_UNITS_CHARACTERS = 1500
# One text for the planned step (details.message) and for the refusal of any other payload with context.unread.
UNREAD_FINAL_MESSAGE = (
    'A final over unread ranges is admitted only as the step that review-series plans. It plans that step when the '
    'last permitted sequence of a reading turn has ended with a refusal by the provider at the lowest reading level: '
    'a review-assignment whose context.unread.assignment_id names that turn and which keeps its role, route, '
    'dossier, reviewer and author. Run review-series on the head and take the step that it plans.')


def plan_unread_final(records, artifacts, assignment, position):
    """R72 uses the shared position count and the pinned lowest reading level."""
    if not (assignment.get('capacity_plan') and assignment.get('reading_chain')):
        return None
    packet = json.loads(artifacts.read(assignment['packet']))
    if reading.find_protocol(packet) != PROTOCOL or 'reading_turn' not in packet:
        return None
    plan = capacity.read_chain_capacity_plan(artifacts, assignment)
    floor = plan['task']['reading']['levels'][-1]['material_allowance']
    bounds = packet['reading_turn']['capacity']
    if not (position['sequence_ended'] and position['sequences_remaining'] == 0
            and position['failures'] and position['failures'][-1][1]['outcome'] == 'provider_refused'
            and position['sequence'] == position['permitted_sequences']
            and bounds.get('level_material_allowance', bounds['material_allowance']) <= floor):
        return None
    from .review_continuation import _find_unused_id
    payload = {key: copy.deepcopy(assignment[key]) for key in
               ('role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id')}
    context = dict(native_packet=assignment['context']['native_packet'], unread=dict(assignment_id=assignment['id']))
    if assignment['context'].get('prior_assignment_id'):
        context['prior_assignment_id'] = assignment['context']['prior_assignment_id']
    payload.update(id=_find_unused_id(records, assignment['id'] + '-unread',
        maximum=reading.RESERVED_IDENTIFIER_CHARACTERS), context=context)
    return dict(state='replace', command='review-assignment', payload=payload,
                details=dict(reason='unread', message=UNREAD_FINAL_MESSAGE))


def build_unread_set(records, artifacts, assignment, inventory, state, *, position=None):
    """Describe the missing ranges and the entire position history in fixed-size rows."""
    from .review_failure_class import count_position
    if position is None:
        position = count_position(records, artifacts, assignment)
    packet = json.loads(artifacts.read(assignment['packet']))
    refused = packet['reading_turn']['plan']['now']
    attempts = position['attempts']
    outcomes = {}
    for attempt in attempts:
        outcomes[attempt['outcome']] = outcomes.get(attempt['outcome'], 0) + 1
    summary = dict(count=len(attempts), sequences=len({row['sequence'] for row in attempts}),
        outcomes=outcomes, first=attempts[0]['time'], last=attempts[-1]['time'])
    rows = []
    for body, start, end in _find_remaining_ranges(inventory, state):
        boundaries = {start, end}
        for row in refused:
            if row['handle'] == body['handle']:
                boundaries.update(value for value in (row['start'], row['end']) if start < value < end)
        points = sorted(boundaries)
        for left, right in zip(points, points[1:]):
            cause = ('provider_refused' if any(row['handle'] == body['handle']
                and row['start'] <= left and right <= row['end'] for row in refused) else 'not_delivered')
            row = dict(id='%s:%d:%d' % (body['handle'], left + 1, left), unit=body['handle'],
                label=body.get('label', body['kind'])[:LABEL_CHARACTERS], part=left + 1,
                start=left, end=right, characters=right - left, cause=cause, artifact=body['artifact'])
            if cause == 'provider_refused':
                row['attempts'] = summary
            rows.append(row)
    rows.sort(key=lambda row: (row['cause'] != 'provider_refused', -row['characters'], row['unit'], row['start']))
    refused_row = next((row['id'] for row in rows if row['cause'] == 'provider_refused'), None)
    return dict(rows=rows, attempts=[dict(id=str(index), row=refused_row, **attempt)
        for index, attempt in enumerate(attempts)])


def _supply_unread_rows(plan, state, artifacts):
    return json.loads(artifacts.read(state['unread']['artifact']))['rows'] if 'unread' in state else []


def _supply_unread_attempts(plan, state, artifacts):
    return json.loads(artifacts.read(state['unread']['artifact']))['attempts'] if 'unread' in state else []


def describe_unread_packet(artifacts, state, *, route=None, allowance=None):
    """Keep the complete member, including its prefix and counters, within 30,000 characters."""
    value = json.loads(artifacts.read(state['unread']['artifact']))
    result = dict(handle='x1', artifact=state['unread']['artifact'], digest=state['unread']['digest'],
        causes={cause: dict(rows=sum(row['cause'] == cause for row in value['rows']),
            characters=sum(row['characters'] for row in value['rows'] if row['cause'] == cause))
            for cause in ('provider_refused', 'not_delivered')},
        count=len(value['rows']), rows=[], omitted=len(value['rows']))
    for row in value['rows']:
        candidate = dict(result, rows=result['rows'] + [row], omitted=result['omitted'] - 1)
        size = (capacity.measure_capacity_value(route, candidate) if route is not None
                else len(serialize_record(candidate).decode('utf-8')))
        if (size > UNREAD_PACKET_CHARACTERS
                or allowance is not None and compute_unread_rows_charge(route, candidate) > allowance):
            break
        result = candidate
    return result


def compute_unread_rows_charge(route, unread):
    """Charge the rows as material; the final frame holds the required metadata."""
    return (capacity.measure_capacity_value(route, unread['rows'])
            - capacity.measure_capacity_value(route, []))


def _read_unread_history(records, unread):
    return dict(records, **{kind: {key: row for key, row in records.get(kind, {}).items()
        if row.get('recorded_revision', 0) < unread['admitted_revision']}
        for kind in ('review_assignment', 'review_attempt', 'review_context_event')})


def _bind_unread_state(records, artifacts, assignment, inventory, state, *, publish=True):
    admission = (assignment if publish else min((row for row in records['review_assignment'].values()
        if row['context'].get('unread') == assignment['context']['unread']),
        key=lambda row: row['recorded_revision']))
    unread = dict(assignment_id=assignment['context']['unread']['assignment_id'],
                  admitted_revision=admission['recorded_revision'])
    records = _read_unread_history(records, unread)
    origin = records['review_assignment'][unread['assignment_id']]
    from .review_failure_class import count_position
    position = count_position(records, artifacts, origin)
    _require_verification(plan_unread_final(records, artifacts, origin, position) is not None,
                          COVERAGE_VERIFICATION_MESSAGE)
    value = build_unread_set(records, artifacts, origin, inventory, state, position=position)
    reference = (artifacts.put(serialize_record(value), 'application/json') if publish
                 else describe_artifact(serialize_record(value), 'application/json'))
    return dict(state, unread=dict(unread, artifact=reference, digest=digest(value)))


def prepare_unread_assignment(records, artifacts, value, route, logical):
    """Admit only the exact R72 step selected by the planner at this position."""
    from .review_failure_class import plan_after_failure
    from .review_protocol import find_replacement, require_verified_context
    identifier = value['context']['unread']['assignment_id']
    reading.require_reserved_identifier(value['id'], PROTOCOL)
    prior = records.get('review_assignment', {}).get(identifier)
    if prior is None or find_replacement(records, prior) is not None:
        raise ResearchError('invalid_review_context', UNREAD_FINAL_MESSAGE)
    require_verified_context(records, artifacts, prior)
    action = plan_after_failure(records, artifacts, identifier)
    expected = action.get('payload', {})
    if (action['state'] != 'replace' or action.get('details', {}).get('reason') != 'unread'
            or any(expected.get(key) != value.get(key) for key in
                   ('context', 'role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id'))):
        raise ResearchError('invalid_review_context', UNREAD_FINAL_MESSAGE)
    frozen = json.loads(artifacts.read(prior['packet']))
    plan, inventory, before = read_bound_state(artifacts, prior, frozen)
    state = _bind_unread_state(records, artifacts, value, inventory, before)
    limits = capacity.derive_turn_task_limits(records, artifacts, prior, kind='final')
    final_page_plan = None
    if 'pages' in V7_FINAL_FORMS or plan['allocation'].get('final_form') == 'pages':
        from .openness_final_pages import prepare_final_packet
        packet, final_page_plan = prepare_final_packet(records, artifacts, prior, route, logical,
            prior['reading_chain']['turn'], inventory, state, plan, limits)
    else:
        packet = build_turn_packet(artifacts, route, logical, prior['reading_chain']['first_assignment_id'],
            prior['reading_chain']['turn'], inventory, state, plan, limits, 'final',
            requests=state.get('pending_requests', []))
    chain = dict(first_assignment_id=prior['reading_chain']['first_assignment_id'], turn=prior['reading_chain']['turn'],
        inventory=prior['reading_chain']['inventory'], state=artifacts.put(serialize_record(state), 'application/json'),
        capacity_plan=prior['capacity_plan'], capacity_plan_kind='final')
    if final_page_plan is not None:
        chain['final_page_plan'] = final_page_plan
        from .openness_final_pages import read_binding, read_final_position
        chain['final_position'] = read_final_position(artifacts, final_page_plan, read_binding(packet)['final_turn'])
    return packet, chain


def _supply_allowance_rows(plan, state, artifacts):
    by_handle = {row['handle']: row for row in state['allowances']['targets']}
    return [dict(id=handle, **by_handle[handle]) for handle in plan['targets']['handles']]


def list_required_affected_notes(plan, selection):
    """Return {required, on_request} descriptors in newest-first recorded order.

    Delivery costs are pinned from the actual serialized units at first assignment.
    The stored start level alone divides the longest fitting prefix; attempts and
    the current level never change it. Even an oversized first note is required.
    """
    rows = selection.get('affected_notes', [])
    allowance = plan['task']['reading']['material_allowance']
    used, count = 0, 0
    for row in rows:
        if count and used + row['delivery_characters'] > allowance:
            break
        used += row['delivery_characters']
        count += 1
        if used > allowance:
            break
    return dict(required=copy.deepcopy(rows[:count]), on_request=copy.deepcopy(rows[count:]))


def _supply_affected_note_rows(plan, state, artifacts):
    rows = list_required_affected_notes(plan, state)['on_request']
    return [dict(id=row['handle'], handle=row['handle'], chain=row['origin']['chain'],
                 characters=row['characters']) for row in rows]


def _describe_affected_note_index(plan, state):
    rows = _supply_affected_note_rows(plan, state, None)
    result = dict(rows=[], omitted=len(rows))
    for row in rows:
        candidate = dict(rows=result['rows'] + [{key: row[key] for key in
            ('handle', 'chain', 'characters')}], omitted=result['omitted'] - 1)
        if len(serialize_record(candidate).decode('utf-8')) > AFFECTED_INDEX_CHARACTERS:
            break
        result = candidate
    return result


def describe_affected_note_delivery(route, origin, notes, handle):
    """Pin the exact cost of one complete note unit, including delivery metadata."""
    content = serialize_record(dict(origin=origin, notes=notes))
    reference = describe_artifact(content, 'application/json')
    body = dict(handle=handle, kind='carried_reading', artifact=reference, characters=len(content.decode('utf-8')))
    overlay = _PlanningArtifacts(None)
    overlay.bodies[reference['sha256']] = content
    row, item = _build_delivery(overlay, body, 0, body['characters'])
    return dict(handle=handle, origin=copy.deepcopy(origin), characters=body['characters'],
                delivery_characters=capacity.measure_capacity_value(route, row)
                    + capacity.measure_capacity_value(route, item) + 4)


# Later lanes add suppliers, not cursor rules. allowances/affected_notes fix order
# at first assignment; unread/unread_attempts fix it at unread-final admission.
# Each supplier returns rows with unique immutable string ids in that fixed order,
# with a serialized row bound measured at reserved identifier lengths (<=2000).
LIST_PAGE_SUPPLIERS = {
    'unread': dict(supply=_supply_unread_rows, row_characters=LIST_ROW_CHARACTERS, fixed_at='unread_final_admission'),
    'unread_attempts': dict(supply=_supply_unread_attempts, row_characters=LIST_ROW_CHARACTERS, fixed_at='unread_final_admission'),
    'allowances': dict(supply=_supply_allowance_rows, row_characters=512, fixed_at='first_assignment'),
    'affected_notes': dict(supply=_supply_affected_note_rows, row_characters=512, fixed_at='first_assignment'),
}


def _require_reading(condition, **details):
    if not condition:
        raise ResearchError(reading.INVALID_RESPONSE, INVALID_READING_MESSAGE, details)


def find_list_supplier(name, state):
    if name in LIST_PAGE_SUPPLIERS:
        return LIST_PAGE_SUPPLIERS[name]
    if state is not None and name in state.get('final_blocks', {}):
        from functools import partial
        from .openness_final_pages import supply_block_rows
        return dict(supply=partial(supply_block_rows, handle=name), row_characters=512,
                    fixed_at='first_assignment')
    return None


def _require_type(value, expected, path):
    _require_reading(isinstance(value, expected), path=path, expected_type=JSON_TYPE_NAMES[expected],
                     received_type=describe_json_type(value))


def _require_members(value, required, path):
    _require_type(value, dict, path)
    _require_reading(set(value) == set(required), path=path, expected_type='object', received_type='object',
                     missing_fields=sorted(set(required) - set(value)),
                     unexpected_fields=sorted(set(value) - set(required)))


def _require_targets(value, targets, path):
    _require_type(value, list, path)
    _require_reading(bool(value), path=path, items=0, minimum_items=1)
    named = set()
    for index, target in enumerate(value):
        _require_type(target, str, '%s/%d' % (path, index))
        _require_reading(target in targets and target not in named, path='%s/%d' % (path, index), target=target)
        named.add(target)


def build_list_page(name, after, plan, state, route, material_allowance, *, artifacts=None):
    """Page the whole fixed list, independently of the packet's usage-sorted view."""
    rows = _validate_list_cursor(name, after, plan, state, artifacts=artifacts)
    supplier = find_list_supplier(name, state)
    declared = supplier.get('row_characters')
    if (type(declared) is not int or not 0 < declared <= LIST_ROW_CHARACTERS
            or supplier.get('fixed_at') not in ('first_assignment', 'unread_final_admission')):
        raise ResearchError('review_route_capacity_plan_violated', capacity.PLAN_VIOLATED_MESSAGE,
                            dict(list=name, declared_row_characters=declared, maximum=LIST_ROW_CHARACTERS))
    ids = [row['id'] for row in rows]
    if any(not isinstance(value, str) for value in ids) or len(set(ids)) != len(ids):
        raise ResearchError('review_route_capacity_plan_violated', capacity.PLAN_VIOLATED_MESSAGE, dict(list=name))
    _require_reading(after is None or after in ids, list=name, after=after)
    start = 0 if after is None else ids.index(after) + 1
    _require_reading(start < len(rows), list=name, after=after)
    selected = []
    for row in rows[start:]:
        size = len(serialize_record(row).decode('utf-8'))
        if size > declared:
            raise ResearchError('review_route_capacity_plan_violated', capacity.PLAN_VIOLATED_MESSAGE,
                                dict(list=name, row=row['id'], characters=size, bound=declared))
        count = start + len(selected) + 1
        page = dict(list=name, after=after, rows=selected + [row],
                    next=row['id'] if count < len(rows) else None, remaining=len(rows) - count)
        if capacity.measure_capacity_value(route, serialize_record(page).decode('utf-8')) > material_allowance:
            break
        selected.append(row)
    if start < len(rows) and not selected:
        raise ResearchError('review_route_capacity_plan_violated', capacity.PLAN_VIOLATED_MESSAGE,
                            dict(list=name, material_allowance=material_allowance, row_bound=declared))
    count = start + len(selected)
    return dict(list=name, after=after, rows=selected,
                next=selected[-1]['id'] if count < len(rows) else None, remaining=len(rows) - count)


def _validate_list_cursor(name, after, plan, state, *, artifacts=None, path=None):
    """Check the cursor of a list. path is the JSON Pointer of the request when an answer is checked."""
    def locate(member):
        return {} if path is None else dict(path=path + member)
    supplier = find_list_supplier(name, state)
    _require_reading(supplier is not None, list=name, **locate('/list'))
    previous = state.get('list_pages', {}).get(name)
    expected = (dict(expected_after=None) if previous is None else dict(ended=True) if previous['ended']
                else dict(expected_after=previous['next']))
    _require_reading((previous is None and after is None) or
                     previous is not None and not previous['ended'] and after == previous['next'],
                     list=name, after=after, **expected, **locate('/after'))
    rows = supplier['supply'](plan, state, artifacts)
    _require_reading(bool(rows), list=name, after=after, rows=0, **locate('/list'))
    return rows


def _describe_plan_measure(records, artifacts, native, route, inventory):
    limits = capacity.describe_route_capacity(route)
    inherited = derive_inherited_ratio(records, artifacts, native, route)
    ratio = limits['input_text_characters']['characters_per_token']
    source = dict(route_class=capacity.describe_route_class(route), policy=capacity.POLICY)
    if inherited is not None and (ratio is None or inherited['characters_per_token'] < ratio):
        ratio, source = inherited['characters_per_token'], inherited['source']
    # Arbitrary UTF-8 outside the calibrated region uses a conservative four
    # bytes per character for every allowed future answer and delivery.
    texts = [artifacts.read(row['artifact']).decode('utf-8') for row in inventory['material']]
    texts.append(json.dumps(native, ensure_ascii=False))
    non_ascii = sum(len(value) - len(value.encode('ascii', errors='ignore')) for value in texts)
    total = sum(len(value) for value in texts)
    rule = limits['request_measure']
    if rule == 'bytes' or non_ascii > total * .05:
        rule = 'bytes'
        if ratio is None or ratio > .25:
            ratio, source = .25, dict(rule='utf8_bytes')
    return dict(rule=rule, characters_per_token=ratio, source=source,
                ratio_margin=capacity.TOKEN_RATIO_MARGIN, margin_applied=True)


def compute_allowance_rows_bound(route, handles):
    """Bound the full allowance view or its compact form at the reserved counter widths."""
    return min(ALLOWANCES_CHARACTERS, capacity.measure_capacity_value(route,
        dict(notes=20000, excerpts=40000, targets=[dict(handle=handle, characters=999999999,
            used=999999999, remaining=999999999) for handle in handles],
            passages=dict(characters=100000, used=100000))) + 512)


def measure_finding_descriptor(route):
    """Measure the descriptor form of a finding index at its widest."""
    return capacity.measure_capacity_value(route, dict(unit='u999999999', characters=999999999,
                                                       searches=999999999, read_works=999999999))


def measure_finding_index_frame(route, finding_index, inventory, stated_characters, final_pages):
    """Measure the largest finding_index member that a final packet of the chain can hold (G8.6).

    The complete form within the stated size, else the stated size, else the descriptor: when the stated size is 0,
    or when the last form of the complete index is over it and the chain cannot end in pages.
    """
    from .review_search_delivery import build_last_finding_form
    complete = capacity.measure_capacity_value(route, finding_index)
    if complete <= stated_characters:
        return complete
    if stated_characters > 0 and (final_pages or capacity.measure_capacity_value(
            route, build_last_finding_form(finding_index, inventory)) <= stated_characters):
        return stated_characters
    return measure_finding_descriptor(route)


def measure_turn_frames(route, native, *, fixed_bounds=None, finding_index=None, inventory=None):
    """Measure each fixed field independently; growing bodies are inventory units.

    A finding index needs fixed_bounds['finding_index'] and the inventory whose bodies its references name.
    """
    from .review_protocol import canonical_prompt
    labels = list_target_labels(native)
    fixed_bounds = fixed_bounds or dict(statement=100000, formulations=TARGET_LABELS_CHARACTERS, native_frame=100000,
        decisive_work_ids=DECISIVE_WORK_IDS_CHARACTERS)
    description = describe_protocol(native=native)
    # Each count of the minimum is measured at its widest.
    minimum = {key: 999999999 if type(value) is int else value
               for key, value in description['describe_minimum'](native).items()}
    minimum['digest'] = 'x' * 64
    fixed = dict(bindings=dict(protocol=description['id'], chain='x' * reading.RESERVED_IDENTIFIER_CHARACTERS,
        turn=999999999, plan_digest='x' * 64, inventory_digest='x' * 64, state_digest='x' * 64,
        **{key: native[key] for key in ('subject_digest', 'claim_digest', 'reviewer')}),
        statement=(native.get('subject', {}) if capacity.measure_capacity_value(
            route, native.get('subject', {})) <= fixed_bounds['statement'] else dict(unit='statement')),
        formulations=labels if capacity.measure_capacity_value(route, labels) <= fixed_bounds['formulations']
        else dict(first='t1', last='t%d' % len(labels), count=len(labels)),
        minimum=minimum)
    result = {}
    for kind in ('reading', 'final'):
        fields = {key: capacity.measure_capacity_value(route, value) for key, value in fixed.items()}
        if kind == 'final':
            from .review_refusal_notice import NOTICE_PARAGRAPH_CHARACTERS
            # The outer identity is repeated; full provenance also bounds its actor_id.
            fields['bindings'] += capacity.measure_capacity_value(route, dict(reviewer_id=native['reviewer']))
            value = description['describe_fields'](native)
            compact = {key: native[key] for key in ('subject_digest', 'claim_digest', 'reviewer')}
            compact['context_unit'] = 'u999999999'
            fields['native_frame'] = capacity.measure_capacity_value(route, value if capacity.measure_capacity_value(
                route, value) <= fixed_bounds.get('native_frame', 100000) else compact)
            fields['decisive_work_ids'] = capacity.measure_capacity_value(route,
                native['scientific_context'].get('decisive_work_ids', []) if capacity.measure_capacity_value(
                    route, native['scientific_context'].get('decisive_work_ids', [])) <= fixed_bounds['decisive_work_ids']
                else dict(count=len(native['scientific_context'].get('decisive_work_ids', [])), unit='u999999999'))
            for key, value in description['final_minimum_bounds'].items():
                fields['minimum.' + key] = capacity.measure_capacity_value(route, {key: value})
            if finding_index is not None:
                fields['finding_index'] = measure_finding_index_frame(route, finding_index, inventory,
                    fixed_bounds['finding_index'], 'pages' in description['list_final_forms']())
            fields['material_envelope'] = capacity.measure_capacity_value(route, {
                key: '' if key == 'notes' else [] for key in FINAL_MATERIAL_FIELDS})
            handles = [row['handle'] for row in labels]
            fields.update(allowances=compute_allowance_rows_bound(route, handles),
                          page=capacity.measure_capacity_value(route, dict(number=1, pages=1, targets=handles)),
                          refusal_paragraph=NOTICE_PARAGRAPH_CHARACTERS, unread=UNREAD_CORE_CHARACTERS)
        else:
            fields['affected_notes_index'] = AFFECTED_INDEX_CHARACTERS * 2
            if inventory is not None and 'finding_units' in inventory:
                fields['finding_units'] = FINDING_UNITS_CHARACTERS
        # Bound envelope keys, capacity/allowance scalars, handles and counters;
        # evidence rows and plan.now are charged as material with their text.
        fields.update(envelope=3000, instructions=len(canonical_prompt(
            'native_math', reading_stage=kind, reading_protocol=description['id'],
            finding_version=2 if finding_index is not None else 1)['text']))
        result[kind] = fields
    return result


def check_v7_admission(records, artifacts, route, native, *, finding_version=2):
    """Compute precisely the first assignment's plan without publishing anything."""
    return _check_v7_admission(records, artifacts, route, native, include_largest=True,
                               finding_version=finding_version)


def describe_visual_text_stop(records, route, assignment=None):
    """Keep the visual runtime unchanged and give the complete text-route answer."""
    from .review_continuation import _find_unused_id
    from .review_plan_steps import describe_step
    from .stop_classes import describe_terminal
    assignment = assignment or dict(id='reading-text-first', role='native_math', dossier_id=None,
        reviewer_id=None, author_id=None, context={})
    value = dict(assignment, route_id=route.get('id', 'visual-route'))
    route_id = _find_unused_id(records, value['route_id'] + '-text')
    probe_id = _find_unused_id(records, route_id + '-probe')
    first_id = _find_unused_id(records, assignment['id'], maximum=reading.RESERVED_IDENTIFIER_CHARACTERS)
    route_step = describe_step(records, None, value, 7)
    route_step.pop('then')
    adapter = 'claude_cli_v1' if route['model'].startswith('claude-') else 'codex_cli_v1'
    route_step['payload'] = dict(id=route_id, adapter=adapter, model=route['model'],
        endpoint='claude://local' if adapter == 'claude_cli_v1' else 'codex://local',
        configuration=copy.deepcopy(route['configuration']))
    route_step['chooses'] = []
    probe = dict(command='review-probe', payload=dict(id=probe_id, route_id=route_id),
                 chooses=[], prerequisites=[])
    if not capacity.describe_route_capacity(route)['listed']:
        probe['chooses'] = ['capacity.input_tokens']
        probe['prerequisites'] = [dict(condition='Authorize a capacity probe large enough for the first reading assignment.')]
    export = describe_step(records, None, value, 4)
    assign = export.pop('then')[0]
    export['payload'].update(assignment_id=first_id, reviewer_id=assignment['reviewer_id'])
    export['chooses'] = ['minimum'] + ([] if assignment['reviewer_id'] else ['reviewer_id'])
    assign['payload'].update(id=first_id, route_id=route_id, reviewer_id=assignment['reviewer_id'])
    assign['chooses'] = ['context.native_packet']
    assign['chooses'].extend(key for key in ('reviewer_id', 'author_id') if not assign['payload'].get(key))
    terminal = describe_terminal(VISUAL_TEXT_ROUTE_CODE)
    terminal['details'].update(message=VISUAL_TEXT_ROUTE_MESSAGE, required_unit='codex-transport-release')
    terminal['details']['decision']['then'][0]['steps'] = [route_step, probe, export,
        dict(copy.deepcopy(assign), check=True), assign]
    return terminal


def require_text_reading_route(records, route, assignment):
    """Refuse visual v7 admission before constructing or sending a request."""
    if route['adapter'] == 'codex_cli_visual_v1':
        stop = describe_visual_text_stop(records, route, assignment)
        raise ResearchError(VISUAL_TEXT_ROUTE_CODE, VISUAL_TEXT_ROUTE_MESSAGE, stop['details'])


def _find_largest_one_request_targets(records, artifacts, route, native, *, finding_version=2):
    minimum = native['scientific_context']['openness_minimum']
    maximum = min(1 + len(minimum['neighbours']), max(capacity.describe_material_levels('final')) // (
        capacity.EXCERPT_TEXT_CHARACTERS + capacity.EXCERPT_FIELDS_CHARACTERS) + 1)
    largest = 0
    for count in range(1, maximum + 1):
        smaller = copy.deepcopy(native)
        smaller['scientific_context']['openness_minimum']['neighbours'] = minimum['neighbours'][:count - 1]
        admitted = _check_v7_admission(records, artifacts, route, smaller, include_largest=False,
                                      finding_version=finding_version)
        if admitted['state'] != 'record':
            break
        largest = count
    return largest


def _check_v7_admission(records, artifacts, route, native, *, include_largest, finding_version=2):
    description = describe_protocol(native=native)
    chain_protocol = description['id']
    final_forms = description['list_final_forms']()
    if description['final_rule'] != 'finding':
        finding_version = 1
    if route['adapter'] == 'codex_cli_visual_v1':
        stop = describe_visual_text_stop(records, route)
        return dict(state='stop', protocol=chain_protocol, reason='text_route_required',
                    code=VISUAL_TEXT_ROUTE_CODE, details=stop['details'])
    overlay = _PlanningArtifacts(artifacts)
    from .review_search_delivery import build_finding_index, build_last_finding_form
    finding_index = build_finding_index(records, artifacts, native) if finding_version == 2 else None
    if has_binary_native_evidence(records, artifacts, native):
        return dict(state='stop', protocol=chain_protocol, reason='binary_required_evidence',
                    largest_one_request_targets=0)
    try:
        original = build_inventory(records, artifacts, native)
    except UnicodeDecodeError:
        return dict(state='stop', protocol=chain_protocol, reason='binary_required_evidence',
                    largest_one_request_targets=0)
    labels = list_target_labels(native)
    fixed_bounds = dict(statement=100000, formulations=TARGET_LABELS_CHARACTERS, native_frame=100000,
        decisive_work_ids=DECISIVE_WORK_IDS_CHARACTERS)
    if finding_index is not None:
        fixed_bounds['finding_index'] = FINDING_INDEX_CHARACTERS
    try:
        while True:
            built = prepare_inventory(original, native, route, fixed_bounds=fixed_bounds, finding_index=finding_index)
            overlay.bodies.update(built['bodies'])
            inventory = built['inventory']
            # No source row is retained before an own excerpt exists. Each
            # excerpt's cost includes its bounded source row when it is kept.
            targets = [dict(handle=row['handle'], gap_rows=sum(
                gap.get('formulation') == row['formulation'] for gap in native['scientific_context'].get('minimum_gaps', [])),
                source_rows=0)
                for index, row in enumerate(labels)]
            measure = _describe_plan_measure(records, overlay, native, route, inventory)
            frames = measure_turn_frames(route, native, fixed_bounds=fixed_bounds, finding_index=finding_index,
                                         inventory=inventory)
            declaration = dict(kind='reading', frames=frames,
                inventory=[{key: row[key] for key in ('handle', 'kind', 'characters', 'sha256')}
                           for row in inventory['material']], targets=targets, measure=measure,
                allowance_rows_bound=compute_allowance_rows_bound(route, [row['handle'] for row in targets]),
                final_pages='pages' in final_forms)
            members = capacity.prepare_assignment_capacity(records, overlay, route, declaration)
            plan = capacity.read_chain_capacity_plan(overlay, members)
            if finding_index is not None:
                from .review_protocol import _put_json
                plan['finding_index'] = copy.deepcopy(finding_index)
                members['capacity_plan'] = dict(protocol=capacity.CAPACITY_PLAN_PROTOCOL, digest=digest(plan),
                    artifact=_put_json(overlay, plan))
            if not any(row['unit_fields'] for row in plan['frame'].values()):
                break
            # G8.6. A final frame over its bound first lets the index give up values, then moves one of the other
            # fixed fields to a unit, the largest first, and last reduces the index to its descriptor. A reading
            # frame over its bound takes the middle step alone.
            final_frame = plan['frame']['final']
            room = final_frame['bound'] - final_frame['characters'] + final_frame['fields'].get('finding_index', 0)
            if (final_frame['unit_fields'] and finding_index is not None and room >= capacity.measure_capacity_value(
                    route, build_last_finding_form(finding_index, inventory))):
                fixed_bounds['finding_index'] = room
                continue
            movable = [field for field in fixed_bounds if field != 'finding_index' and fixed_bounds[field] > 0
                       and max(row['fields'].get(field, 0) for row in plan['frame'].values()) > 1000]
            if movable:
                field = max(movable, key=lambda name: max(row.get(name, 0) for row in frames.values()))
                fixed_bounds[field] = 0
            elif (final_frame['unit_fields'] and finding_index is not None and fixed_bounds['finding_index'] > 0
                    and final_frame['fields']['finding_index'] > measure_finding_descriptor(route)):
                fixed_bounds['finding_index'] = 0
            else:
                raise ResearchError('review_route_capacity_below_floor', capacity.BELOW_FLOOR_MESSAGE,
                    dict(reason='fixed_frame', frames=plan['frame'], room=plan['limits']['input_text_characters']['value']))
    except ResearchError as error:
        if error.code != 'review_route_capacity_below_floor':
            raise
        largest = _find_largest_one_request_targets(records, artifacts, route, native,
            finding_version=finding_version) if include_largest else 0
        return dict(state='stop', protocol=chain_protocol, reason='below_floor', code=error.code,
                    details=error.details, measure=measure, targets=len(targets), largest_one_request_targets=largest)
    final = plan['task']['final']
    room = min(plan['allocation']['page_room'], final['material_allowance'])
    pages = capacity.count_capacity_final_turns(room, [dict(handle=row['handle'], allowance=row['allowance'])
        for row in plan['allocation']['targets']], plan['allocation']['notes'], final['target_allowance'])['pages']
    minimum = capacity.EXCERPT_TEXT_CHARACTERS + capacity.EXCERPT_FIELDS_CHARACTERS
    overhead = max((row['rows'] for row in plan['allocation']['targets']), default=0)
    largest = min(final['target_allowance'] if final['state'] != 'unmeasured' else len(targets),
                  max(0, (room - capacity.NOTES_MINIMUM_CHARACTERS) // (minimum + overhead)))
    # Unmeasured final target_allowance is a profile selection, not evidence that
    # targets must be split. Its smallest answer bound is per admitted target.
    if final['state'] == 'unmeasured':
        largest = max(0, (room - capacity.NOTES_MINIMUM_CHARACTERS) // (minimum + overhead))
    if pages > 1 and 'pages' not in final_forms:
        if include_largest:
            largest = _find_largest_one_request_targets(records, artifacts, route, native, finding_version=finding_version)
        return dict(state='stop', protocol=chain_protocol, reason='final_needs_pages', pages=pages,
                    targets=len(targets), largest_one_request_targets=largest, measure=plan['measure'])
    return dict(state='record', protocol=chain_protocol, **(dict(final_form=plan['allocation'].get('final_form', 'request'))
                if 'pages' in final_forms else {}), capacity_plan=plan, capacity_members=members,
                inventory=built, forecast=capacity.describe_capacity_forecast(
                    plan, route, capacity.read_output_profile(records, route)),
                largest_one_request_targets=largest)


def _describe_identity(row):
    if row['kind'] in ('target_page', 'fixed_field'):
        return dict(kind=row['kind'], field=row['field'])
    if row['kind'] == 'carried_reading':
        return dict(kind=row['kind'], origin=row['origin'], content_kind=row['content_kind'])
    return dict(kind=row['kind'], **dict(review_excerpts.find_identity(row)[1:]))


def _describe_json(value):
    return describe_artifact(serialize_record(value), 'application/json')


def _append_unit(inventory, row, content):
    identity = _describe_identity(row)
    number = (len(inventory['units']) + 1) * (2 if inventory.get('packet_handles') else 1)
    unit = dict(handle='u%d' % number, kind=row['kind'], identity=identity,
                sha256=row['artifact']['sha256'], artifact=copy.deepcopy(row['artifact']),
                characters=len(content.decode('utf-8')),
                label=row.get('label', row.get('record_id', row.get('version_id', row['kind'])))[:LABEL_CHARACTERS],
                bodies=[])
    inventory['units'].append(unit)
    return unit


def _bind_body(inventory, material_by_digest, unit, reference, content, kind):
    sha256 = reference['sha256']
    if sha256 not in material_by_digest:
        handle = ('u%d' % (2 * len(inventory['material']) + 1) if inventory.get('packet_handles')
                  else 'b%d' % (len(inventory['material']) + 1))
        body = dict(handle=handle, kind=kind, sha256=sha256,
                    characters=len(content.decode('utf-8')), artifact=copy.deepcopy(reference), units=[])
        inventory['material'].append(body)
        material_by_digest[sha256] = body
    body = material_by_digest[sha256]
    if unit['handle'] not in body['units']:
        body['units'].append(unit['handle'])
    if body['handle'] not in unit['bodies']:
        unit['bodies'].append(body['handle'])


def plan_inventory(rows, bodies, *, search_bindings=(), protocol=PROTOCOL):
    """Build canonical inventory from frozen logical rows and their verified UTF-8 bytes, without publication.

    Returns {inventory, artifact, bodies}. Bodies includes originals for reconstruction and the physical material
    to publish; only inventory.material counts toward reading work. Input row order determines stable handles.
    """
    search_plan = plan_search_delivery(search_bindings, share_all_fields=True)
    manifest = search_plan['manifest']
    content = dict(bodies, **search_plan['bodies'])
    searches = {row['search_digest']: row for row in manifest['searches']}
    shared = {row['artifact']['sha256']: row['artifact'] for row in manifest['shared_fields']}
    inventory = dict(protocol=protocol, units=[], material=[], search_delivery=manifest)
    material_by_digest = {}
    seen = set()
    for row in rows:
        key = digest([_describe_identity(row), row['artifact']['sha256']])
        if key in seen:
            continue
        seen.add(key)
        unit = _append_unit(inventory, row, content[row['artifact']['sha256']])
        search = searches.get(unit['sha256']) if row.get('record_kind') == 'literature_search' else None
        references = ([shared[sha256] for sha256 in dict.fromkeys(search['shared_fields'].values())]
                      + [search['residual']] if search and search['residual'] else [row['artifact']])
        for reference in references:
            _bind_body(inventory, material_by_digest, unit, reference, content[reference['sha256']], row['kind'])
    return dict(inventory=inventory, artifact=_describe_json(inventory), bodies=content)


def has_binary_native_evidence(records, artifacts, native):
    from .review_transport import is_binary_evidence
    for row in describe_protocol(native=native)['list_evidence'](records, native):
        content = artifacts.read(row['artifact'])
        if is_binary_evidence(content, row['artifact']['media_type']):
            return True
        try:
            content.decode('utf-8')
        except UnicodeDecodeError:
            return True
    return False


def build_inventory(records, artifacts, native, *, review=None):
    """Read v6 logical units with the common capture derivation, plus the v7 minimum/gap/notice units (D3 I2).

    Pass review for an earlier unbound packet: list_reading_rows delegates to bind_packet_selections with its
    authenticated stored plans. No second capture-selection algorithm lives here.
    """
    description = describe_protocol(native=native)
    rows = reading.list_reading_rows(records, artifacts, native, review=review)
    context = native['scientific_context']
    included = {row['artifact']['sha256'] for row in rows}
    for row in description['list_evidence'](records, native):
        if row['digest'] not in included:
            rows.append(dict(kind='record', record_kind='native_evidence', record_id=row['digest'],
                             artifact=row['artifact']))
            included.add(row['digest'])
    bodies = {row['artifact']['sha256']: artifacts.read(row['artifact']) for row in rows}
    bindings = [dict(binding, search=json.loads(bodies[binding['search_digest']]))
                for binding in context['openness_minimum']['searches']]
    for field, value in description['list_context_units'](context):
        content = serialize_record(value)
        reference = describe_artifact(content, 'application/json')
        rows.append(dict(kind='record', record_kind=field, record_id=field, artifact=reference))
        bodies[reference['sha256']] = content
    return plan_inventory(rows, bodies, search_bindings=bindings, protocol=description['id'])


def _describe_checked_entries(records, rows, entries):
    sources = reading._collect_unit_sources(rows)
    result = []
    for entry in entries:
        if entry['status'] not in ('verified', 'delivered'):
            continue
        identity = _describe_identity(dict(entry, kind=review_excerpts.find_kind(entry)))
        reference = review_excerpts.find_source_reference(records, review_excerpts.find_identity(entry), sources)
        if identity['kind'] in ('fulltext', 'search_response'):
            identity['text_sha256'] = reference['sha256']
        result.append(dict(identity=identity, source_sha256=reference['sha256'],
                           **{key: entry[key] for key in ('start', 'end', 'sha256', 'text', 'status')},
                           purpose=entry.get('purpose', '')))
    return result


def observe_carried_chain(records, artifacts, assignment):
    """Authenticate a v5/v6 prefix and expose its own ready reading turns for D2's selection.

    Each observation has origin, turn, deliveries, notes, excerpts, passages and searches. A delivery holds the
    logical identity, whole-body sha256, part/parts, start/end, span_sha256 and the exact delivered text. Entry
    source_sha256 is the whole-body binding; sha256 is the checked span. Text is transient observation data.
    Only retained verified excerpts and delivered passage answers are candidates. A pending capture gains no credit.
    """
    from .native_review import load_packet
    reading.verify_problem_coverage(records, artifacts, assignment)
    native = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
    rows = reading.list_reading_rows(records, artifacts, native, review=assignment)
    manifest = reading._plan_search_delivery(artifacts, native)['manifest']
    physical = reading.list_delivery_rows(rows, manifest)
    spans = reading._compute_spans(artifacts, physical)
    chain = reading._list_chain(records, assignment)
    observations = reading._observe_turns(records, artifacts, chain, physical, spans, manifest)
    by_identity = {review_excerpts.find_identity(row): row for row in physical}
    result = []
    for index, (saved, observed) in enumerate(zip(chain, observations)):
        if not observed['reading']:
            continue
        packet = json.loads(artifacts.read(saved['packet']))
        protocol = reading.find_protocol(packet)
        earlier = reading._find_record(packet)
        retained = reading._extend_record(records, artifacts, packet, {'output': observed['output']},
                                          rows, observations[:index + 1], protocol)
        deliveries = []
        for delivered in observed['turn']['delivered_units'] + observed['turn'].get('delivered_parts', []):
            row = by_identity[review_excerpts.find_identity(delivered)]
            text = artifacts.read(row['artifact']).decode('utf-8')
            start, end = delivered.get('start', 0), delivered.get('end', len(text))
            span = text[start:end]
            deliveries.append(dict(identity=_describe_identity(row), sha256=row['artifact']['sha256'],
                part=delivered.get('part', 1), parts=delivered.get('parts', 1), start=start, end=end,
                span_sha256=hashlib.sha256(span.encode('utf-8')).hexdigest(), text=span))
        origin = dict(chain=chain[0]['id'], protocol=protocol, assignment_id=saved['id'],
                      attempt_id=observed['turn']['attempt_id'])
        result.append(dict(origin=origin, turn=dict(number=index + 1, assignment_id=saved['id'],
            attempt_id=origin['attempt_id'], kind='reading'), deliveries=deliveries, notes=observed['output']['notes'],
            excerpts=_describe_checked_entries(records, rows, retained['excerpts'][len(earlier['excerpts']):]),
            passages=_describe_checked_entries(records, rows, retained['passages'][len(earlier['passages']):]),
            searches=[row['search_digest'] for row in observed['turn'].get('completed_searches', [])]))
    return result


def _require_selected(condition):
    if not condition:
        raise ResearchError('openness_reading_incomplete', SELECTION_MISMATCH_MESSAGE)


def project_carried_turn(inventory, observation, selection):
    """Project one stored D2 selection against its authenticated observation and the new inventory.

    Selection: {origin, ranges:[{unit,part,parts,start,end,sha256}], searches:[original digest],
    entries:[{kind:excerpt|passage,index}], notes:{classification:current|affected,
    units:[{identity,sha256,current_sha256}]}}. A null current_sha256 denotes a removed dependency.
    Indices address observation.excerpts/passages. D2 authenticates eligibility and the notes classification;
    this edge checks each selected item's binding. Unselected changed units never invalidate selected items.
    The result is the same turn projection consumed by build_chain_state for own authenticated v7 turns.
    """
    units = {row['handle']: row for row in inventory['units']}
    by_source = {(digest(row['identity']), row['sha256']): row for row in inventory['units']}
    _require_selected(selection['origin'] == observation['origin'])
    deliveries = []
    for selected in selection['ranges']:
        unit = units.get(selected['unit'])
        _require_selected(unit is not None)
        _require_selected(unit['identity'].get('record_kind') != 'literature_search')
        _require_selected(type(selected['part']) is int and type(selected['parts']) is int
                          and 1 <= selected['part'] <= selected['parts'])
        candidates = [row for row in observation['deliveries'] if row['identity'] == unit['identity']
                      and row['sha256'] == unit['sha256']
                      and row['start'] <= selected['start'] < selected['end'] <= row['end']]
        _require_selected(any(hashlib.sha256(row['text'][selected['start'] - row['start']:
            selected['end'] - row['start']].encode('utf-8')).hexdigest() == selected['sha256'] for row in candidates))
        deliveries.append(copy.deepcopy(selected))
    for sha256 in selection['searches']:
        _require_selected(sha256 in observation['searches'])
        unit = next((row for row in inventory['units'] if row['sha256'] == sha256
                     and row['identity'].get('record_kind') == 'literature_search'), None)
        _require_selected(unit is not None)
        deliveries.append(dict(unit=unit['handle'], part=1, parts=1, start=0, end=unit['characters'], sha256=sha256))
    entries = []
    # Selection order never changes the contract's excerpts-before-passages ordering.
    for kind in ('excerpt', 'passage'):
        for selected in selection['entries']:
            _require_selected(selected['kind'] in ('excerpt', 'passage'))
            if selected['kind'] != kind:
                continue
            values = observation[kind + 's']
            index = selected['index']
            _require_selected(type(index) is int and 0 <= index < len(values))
            entry = values[index]
            unit = by_source.get((digest(entry['identity']), entry['source_sha256']))
            _require_selected(unit is not None and entry['status'] == ('verified' if kind == 'excerpt' else 'delivered'))
            entries.append(dict(kind=kind, unit=unit['handle'], targets=[],
                **{key: entry[key] for key in ('start', 'end', 'sha256', 'text', 'purpose', 'status')}))
    _require_selected(selection['notes']['classification'] in ('current', 'affected'))
    return dict(turn=dict(observation['turn'], origin=copy.deepcopy(observation['origin'])),
                deliveries=deliveries, entries=entries,
                notes=dict(copy.deepcopy(selection['notes']), text=observation['notes']),
                searches=copy.deepcopy(selection['searches']), gaps=[], arguments=[],
                allowance_use=dict(targets={}, passages=0))


def describe_carried_pool(plan):
    """Reserve the notes and one target's largest minimum before allocating the shared carried-entry pool."""
    allocation = plan['allocation']
    room = min(allocation['page_room'], plan['task']['final']['material_allowance'])
    parts = allocation['carried']['parts']
    reserved = parts[0]['minimum'] + max((part['minimum'] for part in parts[1:]), default=0)
    allowance = divide_room(room, [dict(id='minimums', minimum=reserved, preferred=reserved, maximum=reserved),
                             dict(id='pool', minimum=0, preferred=room, maximum=room)])['parts'][1]['allowance']
    return min(allowance, allocation.get('pool_characters', allowance))


def _append_carried_reading(inventory, bodies, origin, content_kind, value):
    content = serialize_record(dict(origin=origin, **value))
    reference = describe_artifact(content, 'application/json')
    identity = dict(kind='carried_reading', origin=origin, content_kind=content_kind)
    existing = next((row for row in inventory['units'] if row['identity'] == identity), None)
    if existing is None:
        unit = _append_unit(inventory, dict(kind='carried_reading', origin=origin, content_kind=content_kind,
                            label='Carried ' + content_kind, artifact=reference), content)
        _bind_body(inventory, {row['sha256']: row for row in inventory['material']},
                   unit, reference, content, 'carried_reading')
    else:
        _require_selected(existing['sha256'] == reference['sha256'])
        unit = existing
    bodies[reference['sha256']] = content
    return unit['handle']


def build_chain_state(inventory, turns, *, route, plan, initial_state=None):
    """Rebuild canonical state from ordered authenticated turn projections, without storage or current selection.

    Returns {state, artifact, inventory, inventory_artifact, bodies}. The returned inventory appends deterministic
    carried_reading units for overflow entries and affected notes; bind it at the first assignment. Replaying with
    that enriched inventory yields the same bytes. Later own turns use origin=None and supply checked entries,
    notes, gaps, arguments and their validated allowance_use ({targets:{handle:cost},passages:cost}).
    No growing collection here belongs in a reading packet. It carries only digests and its current bounded rows.
    """
    inventory = copy.deepcopy(inventory)
    pool_characters = describe_carried_pool(plan)
    state = dict(protocol=inventory['protocol'], inventory_digest=None, turns=[], deliveries=[], notes=[], latest_notes=None,
                 entries=[], pool=[], carried_readings=[], searches=[], gaps=[], arguments=[],
                 allowances=dict(notes=plan['allocation']['notes'],
                    targets=[dict(handle=row['handle'], characters=row['excerpts'], used=0)
                             for row in plan['allocation']['targets']],
                    passages=dict(characters=review_excerpts.SESSION_PASSAGE_CHARACTERS, used=0),
                    pool=dict(characters=pool_characters, used=0)))
    if initial_state is not None:
        state = copy.deepcopy(initial_state)
    bodies, pool, affected_notes, entry_units = {}, [], [], []
    for number, projected in enumerate(turns, len(state['turns']) + 1):
        turn = dict(copy.deepcopy(projected['turn']), number=number)
        state['turns'].append(turn)
        state['deliveries'].extend(dict(copy.deepcopy(row), turn=number) for row in projected['deliveries'])
        note = dict(copy.deepcopy(projected['notes']), turn=number)
        state['notes'].append(note)
        state.setdefault('references', {})['n%d' % number] = dict(reference=describe_artifact(
            note['text'].encode('utf-8'), 'text/plain; charset=utf-8'))
        if note['classification'] == 'current':
            state['latest_notes'] = note['text']
        overflow = []
        for value in projected['entries']:
            if 'handle' in value:
                index = next(index for index, row in enumerate(state['entries']) if row['handle'] == value['handle'])
                state['entries'][index] = copy.deepcopy(value)
                continue
            entry = dict(copy.deepcopy(value), handle='e%d' % (len(state['entries']) + 1))
            state['entries'].append(entry)
            if turn['origin'] is not None:
                charge = reading.measure_record_entries(route, pool + [entry])
                if plan['allocation'].get('pool_sources'):
                    by_handle = {row['handle']: row for row in inventory['units']}
                    sources = [dict(handle=handle, kind=by_handle[handle]['kind'], label=by_handle[handle]['label'])
                        for handle in dict.fromkeys(row['unit'] for row in pool + [entry])]
                    charge = _measure_final_material(route, dict(notes='', entries=pool + [entry],
                        gaps=[], material=[], sources=sources, fill=[]))
                if charge <= pool_characters:
                    pool.append(entry)
                    state['pool'].append(entry['handle'])
                    state['allowances']['pool']['used'] = charge
                else:
                    overflow.append(entry)
        if overflow:
            entry_units.append(_append_carried_reading(
                inventory, bodies, turn['origin'], 'entries', dict(entries=overflow)))
        if note['classification'] == 'affected':
            affected_notes.append(_append_carried_reading(
                inventory, bodies, turn['origin'], 'affected_notes', dict(notes=projected['notes'])))
        state['searches'].extend(sha for sha in projected['searches'] if sha not in state['searches'])
        state['gaps'].extend(copy.deepcopy(projected['gaps']))
        state['arguments'].extend(copy.deepcopy(projected['arguments']))
        if projected.get('unread'):
            state['unread'] = copy.deepcopy(projected['unread'])
        if projected.get('references'):
            state.setdefault('references', {}).update(copy.deepcopy(projected['references']))
        for key in ('final_progress', 'combinations', 'final_catalog', 'final_blocks'):
            if key in projected:
                state.setdefault(key, {}).update(copy.deepcopy(projected[key]))
        for page in projected.get('list_pages', []):
            state.setdefault('list_pages', {})[page['list']] = dict(next=page['next'], ended=page['next'] is None)
        if 'material_state' in projected:
            state['material_state'] = copy.deepcopy(projected['material_state'])
            state['material_request'] = copy.deepcopy(projected['material_request'])
        if projected.get('requests') or 'pending_requests' in state:
            pending = copy.deepcopy(state.get('pending_requests', []))
            for delivery in projected.get('request_deliveries', []):
                for request in list(pending):
                    if request['id'] == delivery['id']:
                        if delivery['complete']:
                            pending.remove(request)
                        else:
                            request['offset'] = request.get('offset', 0) + delivery['characters']
            pending.extend(copy.deepcopy(projected.get('requests', [])))
            state['pending_requests'] = pending
        for target in state['allowances']['targets']:
            target['used'] += projected['allowance_use']['targets'].get(target['handle'], 0)
        state['allowances']['passages']['used'] += projected['allowance_use']['passages']
    if initial_state is None:
        state['affected_notes'] = []
        units = {row['handle']: row for row in inventory['units']}
        materials = {row['handle']: row for row in inventory['material']}
        for handle in reversed(affected_notes):
            unit = units[handle]
            body = materials[unit['bodies'][0]]
            value = json.loads(bodies[body['sha256']])
            state['affected_notes'].append(describe_affected_note_delivery(
                route, value['origin'], value['notes'], body['handle']))
        required = list_required_affected_notes(plan, state)['required']
        state['carried_readings'] = [next(unit['handle'] for unit in inventory['units']
            if row['handle'] in unit['bodies']) for row in required] + entry_units
    inventory_artifact = _describe_json(inventory)
    state['inventory_digest'] = inventory_artifact['sha256']
    return dict(state=state, artifact=_describe_json(state), inventory=inventory,
                inventory_artifact=inventory_artifact, bodies=bodies)


def prepare_inventory(built, native, route, *, fixed_bounds=None, finding_index=None):
    """Add oversized fixed fields as first-delivered units, retaining every byte.

    With the complete finding index of a chain, the last form of that index is one more such unit when it measures
    more than fixed_bounds['finding_index'] (G8.5); it is added after the handles are final, so that it names them.
    When the final names values of the index by their bodies, finding_units lists the span of each (G8.11).
    """
    result = copy.deepcopy(built)
    inventory, bodies = result['inventory'], result['bodies']
    fixed_bounds = fixed_bounds or dict(formulations=TARGET_LABELS_CHARACTERS, statement=100000, native_frame=100000,
        decisive_work_ids=DECISIVE_WORK_IDS_CHARACTERS)
    inventory['fixed_bounds'] = copy.deepcopy(fixed_bounds)
    values = [('formulations', list_target_labels(native), TARGET_LABELS_CHARACTERS, 'target_page'),
              ('statement', native.get('subject', {}), 100000, 'fixed_field'),
              ('native_frame', _describe_native_fields(native), 100000, 'fixed_field'),
              ('decisive_work_ids', native['scientific_context'].get('decisive_work_ids', []),
               DECISIVE_WORK_IDS_CHARACTERS, 'fixed_field')]
    for field, value, bound, kind in values:
        if capacity.measure_capacity_value(route, value) <= min(bound, fixed_bounds[field]):
            continue
        content = serialize_record(value)
        reference = describe_artifact(content, 'application/json')
        unit = _append_unit(inventory, dict(kind=kind, field=field, label=field, artifact=reference), content)
        _bind_body(inventory, {row['sha256']: row for row in inventory['material']},
                   unit, reference, content, kind)
        bodies[reference['sha256']] = content
    if inventory['search_delivery']['searches']:
        content = serialize_record(inventory['search_delivery'])
        reference = describe_artifact(content, 'application/json')
        unit = _append_unit(inventory, dict(kind='fixed_field', field='search_delivery',
            label='Search delivery manifest', artifact=reference), content)
        _bind_body(inventory, {row['sha256']: row for row in inventory['material']}, unit, reference, content, 'fixed_field')
        bodies[reference['sha256']] = content
    if not inventory.get('packet_handles'):
        # Logical units use even u handles, physical delivery units odd u
        # handles. Both namespaces can grow without renaming an existing row.
        logical = {row['handle']: 'u%d' % (2 * (index + 1)) for index, row in enumerate(inventory['units'])}
        physical = {row['handle']: 'u%d' % (2 * index + 1) for index, row in enumerate(inventory['material'])}
        for row in inventory['units']:
            row.update(handle=logical[row['handle']], bodies=[physical[handle] for handle in row['bodies']])
        for row in inventory['material']:
            row.update(handle=physical[row['handle']], units=[logical[handle] for handle in row['units']])
        inventory['packet_handles'] = True
    if finding_index is not None:
        from .review_search_delivery import FINDING_INDEX_UNIT_FIELD, build_last_finding_form, list_finding_units
        last = build_last_finding_form(finding_index, inventory)
        if capacity.measure_capacity_value(route, last) > fixed_bounds['finding_index']:
            content = serialize_record(last)
            reference = describe_artifact(content, 'application/json')
            unit = _append_unit(inventory, dict(kind='fixed_field', field=FINDING_INDEX_UNIT_FIELD,
                label=FINDING_INDEX_LABEL, artifact=reference), content)
            _bind_body(inventory, {row['sha256']: row for row in inventory['material']}, unit, reference, content,
                       'fixed_field')
            bodies[reference['sha256']] = content
        named = list_finding_units(finding_index, inventory, bodies,
                                   fit_packet_finding_index(route, inventory, finding_index))
        if named is not None:
            inventory['finding_units'] = named
    units = {row['handle']: row for row in inventory['units']}
    for row in inventory['material']:
        shared = sorted({field['field'] for field in inventory['search_delivery']['shared_fields']
                         if field['artifact']['sha256'] == row['sha256']})
        row['label'] = (('Shared search fields: ' + ', '.join(shared)) if shared else units[row['units'][0]]['label'])[:120]
    # Handles name immutable descriptors in the inventory, never a request table.
    inventory['references'] = {}
    for index, gap in enumerate(native['scientific_context'].get('minimum_gaps', [])):
        content = serialize_record(gap)
        reference = describe_artifact(content, 'application/json')
        inventory['references']['g%d' % index] = dict(reference=reference)
        bodies[reference['sha256']] = content
    result['artifact'] = _describe_json(inventory)
    return result


def _describe_native_fields(native):
    return describe_protocol(native=native)['describe_fields'](native)


def _publish_bodies(artifacts, bodies):
    for content in bodies.values():
        artifacts.put(content, 'application/json')


def describe_allowances(route, state, answer_bound):
    allowances = state['allowances']
    rows = [dict(row, remaining=max(0, row['characters'] - row['used'])) for row in allowances['targets']]
    answer = divide_room(max(18000, answer_bound), [
        dict(id='notes', minimum=6000, preferred=20000, maximum=20000),
        dict(id='excerpts', minimum=8000, preferred=40000, maximum=40000),
        dict(id='requests', minimum=2000, preferred=5000, maximum=5000),
        dict(id='fields', minimum=2000, preferred=2000, maximum=2000)])
    parts = {row['id']: row['allowance'] for row in answer['parts']}
    result = dict(notes=allowances['notes'], excerpts=parts['excerpts'],
                  targets=rows, passages=copy.deepcopy(allowances['passages']))
    if capacity.measure_capacity_value(route, result) <= ALLOWANCES_CHARACTERS:
        return result
    result.update(targets=[], target_count=len(rows), unused_target_allowance=min(
        (row['characters'] for row in rows if row['used'] == 0), default=0), omitted=len(rows))
    for row in sorted((row for row in rows if row['used']), key=lambda row: (-row['used'], row['handle'])):
        candidate = dict(result, targets=result['targets'] + [row], omitted=result['omitted'] - 1)
        if capacity.measure_capacity_value(route, candidate) > ALLOWANCES_CHARACTERS:
            break
        result = candidate
    return result


def remove_finding_spans(inventory, body, spans):
    """Return the parts of spans of the body that lie outside every span of inventory.finding_units (G8.12).

    A value that the final names by its body counts as delivered only when a reading turn of the chain itself
    delivered it, so a carried credit covers the rest of the body alone.
    """
    named = sorted((row['start'], row['end']) for row in inventory.get('finding_units', []) if row['unit'] == body)
    result = []
    for left, right in spans:
        for start, end in named:
            if start < right and left < end:
                if left < start:
                    result.append((left, start))
                left = end
        if left < right:
            result.append((left, right))
    return result


def _find_remaining_ranges(inventory, state):
    deliveries = {}
    for row in state['deliveries']:
        deliveries.setdefault(row['unit'], []).append((row['start'], row['end']))
    units = {row['handle']: row for row in inventory['units']}
    priority = {body: index for index, handle in enumerate(state['carried_readings'])
                for body in units[handle]['bodies']}
    rows = sorted(enumerate(inventory['material']), key=lambda item:
                  (0 if item[1]['handle'] in priority else 1 if item[1]['kind'] in ('target_page', 'fixed_field') else 2,
                   priority.get(item[1]['handle'], item[0])))
    result = []
    for _, row in rows:
        if row['kind'] == 'carried_reading' and row['handle'] not in priority:
            continue
        start = 0
        # Own deliveries name physical bodies, carried projections logical units. A carried credit does not cover a
        # span that the final names (G8.12), so the receipts of a carried search whose body holds such a span wait
        # for an own turn to deliver it (_collect_completed_searches). In a final over unread ranges with such a
        # span unread, the search therefore drops out of the verified searches of the finding.
        spans = list(deliveries.get(row['handle'], []))
        spans.extend(remove_finding_spans(inventory, row['handle'],
                                          [span for unit in row['units'] for span in deliveries.get(unit, [])]))
        for left, right in sorted(spans):
            if left > start:
                result.append((row, start, left))
            start = max(start, right)
        if start < row['characters']:
            result.append((row, start, row['characters']))
    return result


def _describe_binding(native, chain, number, inventory, state, plan):
    return dict(protocol=inventory['protocol'], chain=chain, turn=number, plan_digest=digest(plan),
        inventory_digest=digest(inventory), state_digest=digest(state),
        **{key: native[key] for key in ('subject_digest', 'claim_digest', 'reviewer')},
        minimum=describe_protocol(native=native)['describe_minimum'](native))


def _build_delivery(artifacts, body, start, end, *, passage=None):
    text = artifacts.read(body['artifact']).decode('utf-8')[start:end]
    reference = describe_artifact(text.encode('utf-8'), 'text/plain; charset=utf-8')
    row = dict(handle=body['handle'], kind=body['kind'], label=body.get('label', body['kind'])[:120],
               part=start + 1, start=start, end=end, characters=end - start, artifact=reference,
               sha256=body['artifact']['sha256'])
    if passage is not None:
        row['request'] = passage
        row['request_end'] = body['request_end']
    evidence = dict(handle=body['handle'], artifact=reference, encoding='utf-8', content=text)
    return row, evidence


def resolve_request_part(inventory, artifacts, request, state=None):
    """Resolve a physical or logical handle to the first part holding the anchor."""
    material = {row['handle']: row for row in inventory['material']}
    unit = next((row for row in inventory['units'] if row['handle'] == request['unit']), None)
    handles = unit['bodies'] if unit is not None else [request['unit']]
    for handle in handles:
        row = material.get(handle)
        if row is not None and (request['anchor'] is None
                or request['anchor'] in artifacts.read(row['artifact']).decode('utf-8')):
            return row
    if state is not None:
        from .openness_final_pages import resolve_entry_request
        return resolve_entry_request(state, artifacts, request)
    return None


def _pack_deliveries(artifacts, route, inventory, state, allowance, requests, *, include_remaining=True):
    now, evidence, pages = [], [], []
    remaining = _find_remaining_ranges(inventory, state) if include_remaining else []
    required = {row['handle'] for row in list_required_affected_notes(state['_plan'], state)['required']}
    if any(body['handle'] in required for body, _, _ in remaining):
        requests = ()
    requested_ranges = []
    passage_room = max(0, state['allowances']['passages']['characters'] - state['allowances']['passages']['used'])
    for request in requests:
        if request.get('kind') == 'list_page':
            plan = state['_plan']
            room = allowance - capacity.measure_capacity_value(route, now) - capacity.measure_capacity_value(route, evidence)
            # A row can double when escaped into the page's JSON-string body.
            if room < 4000 + 2 * find_list_supplier(request['list'], state)['row_characters']:
                break
            page = build_list_page(request['list'], request['after'], plan, state, route, room - 2000, artifacts=artifacts)
            content = serialize_record(page).decode('utf-8')
            body = dict(handle='list:' + request['list'], kind='list_page', label=request['list'],
                        artifact=describe_artifact(content.encode('utf-8'), 'application/json'))
            # Its rows are material and charge no passage or excerpt allowance.
            row = dict(handle=body['handle'], kind='list_page', label=request['list'],
                start=0, end=len(content), part=1, characters=len(content), artifact=body['artifact'],
                page={key: page[key] for key in ('list', 'after', 'next', 'remaining')})
            row['request'] = request['id']
            item = dict(handle=body['handle'], artifact=body['artifact'], encoding='utf-8', content=content)
            if capacity.measure_capacity_value(route, now + [row]) + capacity.measure_capacity_value(route, evidence + [item]) > allowance:
                raise ResearchError('review_route_capacity_plan_violated', capacity.PLAN_VIOLATED_MESSAGE,
                                    dict(list=request['list'], material_allowance=allowance))
            now.append(row)
            evidence.append(item)
            pages.append(page)
        else:
            body = resolve_request_part(inventory, artifacts, request, state)
            if body is None:
                continue
            text = artifacts.read(body['artifact']).decode('utf-8')
            start = 0 if request['anchor'] is None else text.find(request['anchor'])
            stop = min(len(text), start + request['characters'])
            start += request.get('offset', 0)
            stop = min(stop, start + passage_room)
            passage_room -= max(0, stop - start)
            requested_ranges.append((dict(body, request=request['id'], request_end=stop), start, stop))
    remaining = requested_ranges + remaining
    for body, start, stop in remaining:
        if stop <= start:
            continue
        # Binary search the serialized row plus body, including quote/Unicode
        # expansion. Metadata and text consume the same material allowance.
        low, high = start, stop
        while low < high:
            middle = (low + high + 1) // 2
            row, item = _build_delivery(artifacts, body, start, middle, passage=body.get('request'))
            size = capacity.measure_capacity_value(route, now + [row]) + capacity.measure_capacity_value(route, evidence + [item])
            if size <= allowance:
                low = middle
            else:
                high = middle - 1
        if low == start:
            break
        row, item = _build_delivery(artifacts, body, start, low, passage=body.get('request'))
        now.append(row)
        evidence.append(item)
        if low < stop:
            break
    return now, evidence, pages


def build_turn_packet(artifacts, route, logical, chain, number, inventory, state, plan, limits, kind,
                      *, requests=(), notice=None, finding_version=None):
    """Render a bounded v7 turn from its pinned artifacts and current allowance.

    A final holds the finding index exactly when the plan of its chain froze one (G8.8), in the form that the stated
    size of the chain admits.
    """
    native = logical['native']
    finding_index = None if finding_version == 1 else plan.get('finding_index')
    binding = _describe_binding(native, chain, number, inventory, state, plan)
    packet = {key: logical[key] for key in ('protocol', 'role', 'reviewer_id', 'dossier_id')}
    bounds = dict(output_tokens=plan['limits']['output_tokens']['value'], answer_bound=limits['answer_bound'],
                  material_allowance=limits['material_allowance'])
    if limits.get('level_material_allowance') is not None:
        bounds['level_material_allowance'] = limits['level_material_allowance']
    binding.update(notes=state['latest_notes'], allowances=describe_allowances(route, state, limits['answer_bound']),
                   capacity=bounds)
    binding['notes_handle'] = next(('n%d' % row['turn'] for row in reversed(state['notes'])
                                  if row['classification'] == 'current'), None)
    if kind == 'reading':
        optional_notes = list_required_affected_notes(plan, state)['on_request']
        binding['affected_notes'] = dict(notes=len(optional_notes),
            characters=sum(row['characters'] for row in optional_notes))
        if number == 1:
            binding['affected_notes']['index'] = _describe_affected_note_index(plan, state)
        if 'finding_units' in inventory:
            binding['finding_units'] = len(inventory['finding_units'])
        labels = list_target_labels(native)
        fixed_bounds = inventory.get('fixed_bounds', dict(formulations=TARGET_LABELS_CHARACTERS, statement=100000))
        binding['formulations'] = (labels if capacity.measure_capacity_value(route, labels) <= fixed_bounds['formulations']
                                   else dict(first=labels[0]['handle'], last=labels[-1]['handle'], count=len(labels)))
        binding['statement'] = (native.get('subject', {}) if
            capacity.measure_capacity_value(route, native.get('subject', {})) <= fixed_bounds['statement'] else dict(unit='statement'))
        now, evidence, _ = _pack_deliveries(artifacts, route, inventory, dict(state, _plan=plan),
                                          limits['material_allowance'], requests)
        remaining = _find_remaining_ranges(inventory, state)
        binding['plan'] = dict(now=now, delivered=dict(units=len(state['deliveries']),
            characters=sum(row['end'] - row['start'] for row in state['deliveries'])),
            remaining=dict(units=len(remaining), characters=sum(end - start for _, start, end in remaining)))
        packet.update(reading_turn=binding, evidence=evidence)
    else:
        if state.get('material_state', {}).get('pending'):
            binding['material_request'] = copy.deepcopy(state['material_request'])
        fields = _describe_native_fields(native)
        if capacity.measure_capacity_value(route, fields) <= inventory.get('fixed_bounds', {}).get('native_frame', 100000):
            frame = fields
        else:
            frame = {key: native[key] for key in ('subject_digest', 'claim_digest', 'reviewer')}
            frame['context_unit'] = next(row['bodies'][0] for row in inventory['units']
                if row['identity'].get('field') == 'native_frame')
        # Collections are counts; bodies remain bound in immutable artifacts.
        frame['subject'] = (copy.deepcopy(native.get('subject', {})) if capacity.measure_capacity_value(
            route, native.get('subject', {})) <= inventory.get('fixed_bounds', {}).get('statement', 100000)
            else dict(unit='statement'))
        context = native['scientific_context']
        rendered = describe_protocol(native=native)['render_final_context'](route, native, inventory)
        frame.update(rendered['frame'])
        binding['minimum'].update(rendered['minimum'])
        for key in ('evidence', 'execution_provenance', 'unmaterialized_digests'):
            if key in native:
                frame[key] = dict(count=len(native[key]))
        target_handles = {row['formulation']: row['handle'] for row in list_target_labels(native)}
        binding.update(page=dict(number=1, pages=1, targets=plan['targets']['handles']),
            entries=[copy.deepcopy(row) for row in state['entries'] if row['status'] in ('verified', 'delivered')
                     and (row['handle'] in state['pool'] or row['targets'])],
            gaps=[dict(handle='g%d' % index, condition=row['condition'], outcomes=copy.deepcopy(row['outcomes']),
                    target=target_handles[row['formulation']])
                  for index, row in enumerate(context.get('minimum_gaps', []))]
                  + [dict(handle=row['handle'], formulation=row['formulation'], text=row['text'])
                    for row in state['gaps'] if row.get('status', 'verified') == 'verified'],
            material=[dict(handle=key, title=row['title'], reference=state['references'][key]['reference'])
                for key, row in state.get('material_state', {}).get('arguments', {}).items()
                if row['complete'] and row.get('status', 'verified') == 'verified'],
            sources=[], fill=[])
        by_handle = {row['handle']: row for row in inventory['material'] + inventory['units']}
        by_handle.update((handle, dict(handle=handle, kind='final_entry', label=handle))
                         for handle in state.get('final_catalog', {}) if handle not in by_handle)
        by_handle.update(('list:' + handle, dict(handle='list:' + handle, kind='list_page', label=handle))
                         for handle in state.get('list_pages', {}))
        binding['sources'] = [dict(handle=handle, kind=by_handle[handle]['kind'],
            label=by_handle[handle].get('label', by_handle[handle]['kind'])[:120])
            for handle in dict.fromkeys(row['unit'] for row in binding['entries'])]
        material = {key: binding[key] for key in FINAL_MATERIAL_FIELDS}
        unread_cost, extra_room = 0, 0
        if 'unread' in state:
            # The required unread metadata is in the frame. Its rows and pages
            # share the remaining material room and the unused notice reserve.
            notice_cost = capacity.measure_capacity_value(route, notice) if notice else 0
            extra_room = plan['task']['final']['notice_characters'] - notice_cost
            room = limits['material_allowance'] + extra_room - _measure_final_material(route, material)
            binding['unread'] = describe_unread_packet(artifacts, state, route=route,
                allowance=room - (8000 if requests else 0))
            unread_cost = compute_unread_rows_charge(route, binding['unread'])
        used = _measure_final_material(route, material) + unread_cost
        for note in (reversed(state['notes'][:-1]) if not requests else ()):

            if note['classification'] != 'current':
                continue
            candidate = dict(material, fill=binding['fill'] + [dict(handle='n%d' % note['turn'], text=note['text'])])
            if _measure_final_material(route, candidate) + unread_cost > limits['material_allowance'] + extra_room:
                break
            binding['fill'] = candidate['fill']
        if used > limits['material_allowance'] + extra_room:
            raise ResearchError('review_route_capacity_plan_violated', capacity.PLAN_VIOLATED_MESSAGE,
                                dict(kind='final', material_characters=used, bound=limits['material_allowance']))
        evidence = []
        if requests:
            now, evidence, _ = _pack_deliveries(artifacts, route, inventory, dict(state, _plan=plan),
                max(0, limits['material_allowance'] + extra_room - unread_cost - _measure_final_material(route,
                    {key: binding[key] for key in FINAL_MATERIAL_FIELDS})), requests, include_remaining=False)
            binding['plan'] = dict(now=now)
        packet.update(native=frame, reading_final=binding, evidence=evidence)
        if finding_index is not None:
            packet['finding_index'] = fit_packet_finding_index(route, inventory, finding_index)
    packet['digest'] = digest(packet)
    return packet


def fit_packet_finding_index(route, inventory, finding_index, *, formulations=None):
    """Return the finding_index member of a final packet in the form that the chain's stated size admits (G8.4)."""
    from .review_search_delivery import fit_finding_index
    return fit_finding_index(finding_index, inventory, inventory['fixed_bounds']['finding_index'],
                             lambda value: capacity.measure_capacity_value(route, value), formulations=formulations)


def _measure_final_material(route, material):
    # Collection keys and empty containers are fixed frame. Only their payload
    # consumes the grants divided among notes and retained reference rows.
    empty = {key: '' if key == 'notes' and isinstance(material['notes'], str)
             else None if key == 'notes' else [] for key in FINAL_MATERIAL_FIELDS}
    return capacity.measure_capacity_value(route, material) - capacity.measure_capacity_value(route, empty)


def _reserve_initial_notes(route, plan, text):
    """Reserve legacy current notes at their actual serialized cost before sizing own turns."""
    cost = capacity.measure_capacity_value(route, text) - capacity.measure_capacity_value(route, '')
    extra = max(0, cost - plan['allocation']['notes'])
    if not extra:
        return
    from .review_refusal_notice import BOUNDED_NOTICE_CHARACTERS
    prior = plan['task']['reading']
    carried = prior['carried_characters'] + extra
    room = min(plan['limits']['input_text_characters']['value'], math.floor(
        plan['limits']['input_tokens']['value'] * plan['measure']['characters_per_token']))
    selected = capacity.select_task_limits(route, plan['profile'], 'reading',
        targets=len(plan['targets']['handles']), frame_characters=prior['frame_characters'],
        carried_characters=carried, calibrated=plan['limits']['request_measure'] == 'calibrated', text_room=room)
    fitting = [row['material_allowance'] for row in selected['levels'] if row['task_characters'] <= room]
    if not fitting:
        raise ResearchError('review_route_capacity_below_floor', capacity.BELOW_FLOOR_MESSAGE,
            dict(kind='reading', initial_notes_characters=cost, room=room,
                 minimum=plan['frame']['reading']['bound'] + BOUNDED_NOTICE_CHARACTERS + carried +
                     capacity.describe_material_levels('reading')[-1], command='review-route'))
    selected.update(carried_characters=carried, notice_characters=prior['notice_characters'],
        adaptation=dict(prior['adaptation'], ceiling=min(prior['adaptation']['ceiling'], max(fitting))),
        request_bound=dict(prior['request_bound'], text_characters=selected['task_characters']))
    plan['task']['reading'] = selected
    plan['allocation']['initial_notes_characters'] = cost


def prepare_first_assignment(records, artifacts, value, route, logical, carried_projections, *, admission=None,
                             carried_selection=None):
    """Build inventory, stored plan and initial state; D2 supplies the projections."""
    reading.require_reserved_identifier(value['id'], describe_protocol(native=logical['native'])['id'])
    admission = admission or check_v7_admission(records, artifacts, route, logical['native'])
    if admission['state'] != 'record':
        raise ResearchError('review_route_capacity_below_floor', capacity.BELOW_FLOOR_MESSAGE, admission)
    built, plan = admission['inventory'], copy.deepcopy(admission['capacity_plan'])
    latest = next((row['notes']['text'] for row in reversed(carried_projections)
                   if row['notes']['classification'] == 'current'), None)
    if latest is not None:
        _reserve_initial_notes(route, plan, latest)
    if carried_projections:
        parts = plan['allocation']['carried']['parts']
        # This release admits a final in one request, hence all target minimums
        # must coexist with the shared pool and the standing notes.
        plan['allocation']['pool_characters'] = max(0, plan['task']['final']['material_allowance']
            - plan['allocation']['notes'] - (max((row['minimum'] for row in parts[1:]), default=0)
              if plan['allocation'].get('final_form') == 'pages' else sum(row['minimum'] for row in parts[1:])))
        plan['allocation']['pool_sources'] = True
    state = build_chain_state(built['inventory'], carried_projections, route=route, plan=plan)
    if carried_projections and plan['allocation'].get('final_form') != 'pages':
        room = plan['task']['final']['material_allowance'] - state['state']['allowances']['pool']['used']
        parts = copy.deepcopy(plan['allocation']['carried']['parts'])
        for part in parts:
            part['preferred'] = part['maximum'] = part['allowance']
        parts[0]['minimum'] = parts[0]['allowance']
        divided = divide_room(room, parts)
        for target, part in zip(plan['allocation']['targets'], divided['parts'][1:]):
            target.update(allowance=part['allowance'], excerpts=part['allowance'] - part['overhead'])
        state['state']['allowances']['targets'] = [dict(handle=row['handle'], characters=row['excerpts'], used=0)
            for row in plan['allocation']['targets']]
        plan['allocation']['carried'] = divided
        plan['allocation']['pool_used'] = state['state']['allowances']['pool']['used']
    _publish_bodies(artifacts, dict(built['bodies'], **state['bodies']))
    for note in state['state']['notes']:
        artifacts.put(note['text'].encode('utf-8'), 'text/plain; charset=utf-8')
    inventory_ref = artifacts.put(serialize_record(state['inventory']), 'application/json')
    state_ref = artifacts.put(serialize_record(state['state']), 'application/json')
    from .review_protocol import _put_json
    plan_ref = dict(protocol=capacity.CAPACITY_PLAN_PROTOCOL, digest=digest(plan), artifact=_put_json(artifacts, plan))
    chain = dict(first_assignment_id=value['id'], turn=1, inventory=inventory_ref, state=state_ref,
                 capacity_plan=plan_ref, capacity_plan_kind='reading')
    from .openness_carried import SELECTION_PROTOCOL
    selection = copy.deepcopy(carried_selection) if carried_selection is not None else dict(
        protocol=SELECTION_PROTOCOL, inventory_digest=digest(built['inventory']), turns=[])
    selection['affected_notes'] = copy.deepcopy(state['state']['affected_notes'])
    chain['carried_selection'] = dict(protocol=SELECTION_PROTOCOL, digest=digest(selection),
        artifact=artifacts.put(serialize_record(selection), 'application/json'))
    packet = build_turn_packet(artifacts, route, logical, value['id'], 1, state['inventory'], state['state'],
                               plan, capacity.derive_plan_task_limits(plan, 'reading'), 'reading')
    _publish_deliveries(artifacts, packet)
    return packet, chain


def _describe_carried_identity(records, value, route, native):
    minimum = native['scientific_context']['openness_minimum']
    revision = value.get('recorded_revision')
    if revision is None:
        # One pass over every record, made only for a value that holds no stamp.
        revision = 1 + max((row.get('recorded_revision', 0) for group in records.values() for row in group.values()),
                           default=0)
    return dict(**describe_protocol(native=native)['carried_identity'],
        reviewer_id=value['reviewer_id'], author_id=value['author_id'], route=route,
        subject_digest=native['subject_digest'], claim_digest=native['claim_digest'],
        formulations=dict(statement=minimum['statement'], neighbours=minimum['neighbours']),
        recorded_revision=revision)


def prepare_carried_assignment(records, artifacts, value, route, logical, admission):
    """Select and authenticate the earlier readings once, before publishing the first v7 assignment."""
    from .openness_carried import build_carried_selection, describe_carried_selection, verify_carried_selection
    inventory = admission['inventory']['inventory']
    identity = _describe_carried_identity(records, value, route, logical['native'])
    selection = build_carried_selection(records, artifacts, inventory, identity)['selection']
    projections = verify_carried_selection(records, artifacts, inventory, identity, selection)
    report = describe_carried_selection(records, artifacts, inventory, identity, selection)
    packet, chain = prepare_first_assignment(records, artifacts, value, route, logical, projections,
        admission=admission, carried_selection=selection)
    for row in report['passed_over']:
        row['message'] = PASSED_OVER_TEXT.format(chain=row['chain'])
    chain['carried_readings'] = report
    return packet, chain


def describe_uncarried_chains(records, artifacts, value, route, native):
    """Report the selection v7 would carry when admission instead chooses v6; credit nothing.

    The report states a refusal of that selection in refusal, and the v6 chain starts.
    """
    if has_binary_native_evidence(records, artifacts, native):
        return dict(chains=[], would_carry=[], passed_over=[])
    from .openness_carried import build_carried_selection, describe_carried_selection
    overlay = _PlanningArtifacts(artifacts)
    built = build_inventory(records, overlay, native, review=value)
    built = prepare_inventory(built, native, route)
    _publish_bodies(overlay, built['bodies'])
    identity = _describe_carried_identity(records, value, route, native)
    try:
        selection = build_carried_selection(records, artifacts, built['inventory'], identity)['selection']
        report = describe_carried_selection(records, artifacts, built['inventory'], identity, selection)
    except ResearchError as error:
        return dict(chains=[], would_carry=[], passed_over=[], refusal=dict(code=error.code, details=error.details))
    uncarried = dict(chains=[], would_carry=[row['chain'] for row in report['chains']],
                     passed_over=[dict(row, message=PASSED_OVER_TEXT.format(chain=row['chain']))
                                  for row in report['passed_over']],
                     outputs=report['outputs'])
    if 'unavailable_chains' in report:
        uncarried['unavailable_chains'] = report['unavailable_chains']
    return uncarried


def read_carried_inventory(artifacts, first):
    """Recover the frozen pre-carried inventory without rerunning an inventory planner."""
    inventory = json.loads(artifacts.read(first['reading_chain']['inventory']))
    inventory['units'] = [row for row in inventory['units'] if row['kind'] != 'carried_reading']
    handles = {row['handle'] for row in inventory['units']}
    for body in inventory['material']:
        body['units'] = [handle for handle in body['units'] if handle in handles]
    inventory['material'] = [row for row in inventory['material'] if row['units']]
    return inventory


def validate_turn_output(packet, output, *, artifacts=None, assignment=None):
    """Validate the response once; retention charges are applied by projection."""
    binding = packet.get('reading_turn', packet.get('reading_final', packet.get('reading_combine')))
    _require_type(output, dict, '')
    if 'reading_combine' in packet:
        from .review_condensing import validate_combination
        from .openness_final_pages import read_page_plan
        page_plan = read_page_plan(artifacts, assignment['reading_chain']['final_page_plan'])
        turn = next(row for row in page_plan['turns'] if row['number'] == binding['final_turn'])
        plan = capacity.read_chain_capacity_plan(artifacts, assignment)
        route = dict(adapter=plan['route_class']['adapter'], model=plan['route_class']['model'], endpoint='',
            configuration=dict(max_output_tokens=plan['limits']['output_tokens']['value'], effort=plan['route_class']['effort']))
        from .review_handles import _resolve_packet
        table = _resolve_packet(packet, artifacts, assignment)['handle_table']
        block = dict(turn['block'], references=[table[handle]['reference'] for handle in turn['block']['entries']
                                               if handle in table])
        validate_combination(block, output, lambda value: capacity.measure_capacity_value(route, value),
                             binding['capacity']['answer_bound'])
        return
    if output.get('stage') == 'material':
        from .review_handles import validate_material
        return validate_material(packet, output, artifacts=artifacts, assignment=assignment)
    if output.get('stage') != 'reading' and 'page_plan_digest' in binding:
        from . import review_composition
        from .native_review import find_finding_version
        from .openness_final_pages import build_validation_envelope
        envelope = build_validation_envelope(artifacts, assignment, packet)
        rule = review_composition.find_rule('native_math', envelope)
        if 'closing' in binding:
            review_composition.validate_closing_output(rule, envelope, binding['closing']['rows'], output,
                finding_version=find_finding_version(assignment))
        else:
            review_composition.validate_page_output(rule, envelope, binding['page']['targets'], output,
                finding_version=find_finding_version(assignment))
        return
    if output.get('stage') != 'reading' and 'reading_final' in packet:
        # The answer is checked against the complete native packet and the complete finding index of the plan (G8.7).
        packet = dict(packet, native=json.loads(artifacts.read(assignment['context']['native_packet'])))
        if 'finding_index' in packet:
            packet['finding_index'] = capacity.read_chain_capacity_plan(artifacts, assignment)['finding_index']
        describe_protocol(packet=packet)['validate_final'](packet, output, artifacts=artifacts, assignment=assignment)
        return
    required = {'schema_version', 'stage', 'subject_digest', 'claim_digest', 'reviewer',
                'notes', 'excerpts', 'gaps', 'passage_requests'}
    _require_members(output, required, '')
    _require_reading(type(output['schema_version']) is int and output['schema_version'] == 2, path='/schema_version')
    _require_reading(output['stage'] == 'reading', path='/stage')
    for key in ('subject_digest', 'claim_digest', 'reviewer'):
        _require_reading(output[key] == binding[key], path='/' + key)
    notes = output['notes']
    bound = binding['allowances']['notes']
    _require_type(notes, str, '/notes')
    from .operations import text
    with describe_error_at('/notes'):
        text(notes, 'Notes', code=reading.INVALID_RESPONSE)
    # The length and, with the store, the cost of the notes are within the bound; one refusal gives each number.
    measured = dict(characters=len(notes), bound=bound)
    if artifacts is not None:
        plan = capacity.read_chain_capacity_plan(artifacts, assignment)
        # This route is used only to price serialized notes, never to admit or send a request.
        route = dict(adapter=plan['route_class']['adapter'], model=plan['route_class']['model'], endpoint='',
            configuration=dict(max_output_tokens=plan['limits']['output_tokens']['value'], effort=plan['route_class']['effort']))
        measured['notes_cost'] = capacity.measure_capacity_value(route, notes) - capacity.measure_capacity_value(route, '')
    _require_reading(max(measured['characters'], measured.get('notes_cost', 0)) <= bound, path='/notes', **measured)
    _require_type(output['excerpts'], list, '/excerpts')
    _require_type(output['gaps'], list, '/gaps')
    targets = set(row['handle'] for row in binding.get('formulations', []) if isinstance(row, dict)) if isinstance(
        binding.get('formulations'), list) else None
    if targets is None:
        count = binding.get('formulations', {}).get('count')
        targets = {'t%d' % number for number in range(1, count + 1)} if count is not None else set(binding.get('page', {}).get('targets', []))
    if 'page_plan_digest' in binding and artifacts is not None:
        targets = set(capacity.read_chain_capacity_plan(artifacts, assignment)['targets']['handles'])
    delivered = {row['handle'] for row in binding.get('plan', {}).get('now', []) if row['kind'] != 'list_page'}
    kept = set()
    available = None
    for index, row in enumerate(output['excerpts']):
        base = '/excerpts/%d' % index
        _require_type(row, dict, base)
        if 'keep' in row:
            _require_members(row, ('keep', 'targets'), base)
            _require_type(row['keep'], str, base + '/keep')
            _require_reading(row['keep'] not in kept and artifacts is not None, path=base + '/keep', keep=row['keep'])
            if available is None:
                available = _list_delivered_carried_entries(packet, artifacts, assignment)
            _require_reading(row['keep'] in available, path=base + '/keep', keep=row['keep'])
            kept.add(row['keep'])
        else:
            _require_members(row, ('unit', 'text', 'purpose', 'targets'), base)
            _require_type(row['unit'], str, base + '/unit')
            _require_type(row['text'], str, base + '/text')
            _require_reading(bool(row['text']), path=base + '/text', characters=0, minimum_characters=1)
            _require_reading(row['unit'] in delivered, path=base + '/unit', unit=row['unit'])
            _require_reading(_find_excerpt_span(packet, row['unit'], row['text']) is not None, path=base + '/text',
                             unit=row['unit'], characters=len(row['text']))
            _require_type(row['purpose'], str, base + '/purpose')
            _require_reading(len(row['purpose']) <= PURPOSE_CHARACTERS, path=base + '/purpose',
                             characters=len(row['purpose']), maximum_characters=PURPOSE_CHARACTERS)
        _require_targets(row['targets'], targets, base + '/targets')
    for index, row in enumerate(output['gaps']):
        base = '/gaps/%d' % index
        _require_members(row, ('formulation', 'text'), base)
        _require_type(row['formulation'], str, base + '/formulation')
        _require_reading(row['formulation'] in targets, path=base + '/formulation', target=row['formulation'])
        _require_type(row['text'], str, base + '/text')
        _require_reading(0 < len(row['text']) <= GAP_CHARACTERS, path=base + '/text', characters=len(row['text']),
                         minimum_characters=1, maximum_characters=GAP_CHARACTERS)
    requests = output['passage_requests']
    _require_type(requests, list, '/passage_requests')
    _require_reading(len(requests) <= PASSAGE_REQUESTS, path='/passage_requests', items=len(requests),
                     maximum_items=PASSAGE_REQUESTS)
    bound_state = None
    if requests and artifacts is not None:
        plan, inventory, bound_state = read_bound_state(artifacts, assignment, packet)
        bound_state = copy.deepcopy(bound_state)
        current = binding.get('plan', {}).get('now', [])
        for delivery in current:
            if delivery['kind'] == 'list_page':
                page = delivery['page']
                bound_state.setdefault('list_pages', {})[page['list']] = dict(
                    next=page['next'], ended=page['next'] is None)
        passage_left = (bound_state['allowances']['passages']['characters'] - bound_state['allowances']['passages']['used']
                        - sum(row['characters'] for row in current if row.get('request') and row['kind'] != 'list_page'))
        pending = {row['id']: max(0, row.get('characters', 0) - row.get('offset', 0))
                   for row in bound_state.get('pending_requests', [])}
        active_requests = {row['id'] for row in bound_state.get('pending_requests', [])}
        for delivery in current:
            if delivery.get('request') in pending:
                if delivery['kind'] == 'list_page' or delivery['end'] >= delivery['request_end']:
                    pending.pop(delivery['request'])
                    active_requests.discard(delivery['request'])
                else:
                    pending[delivery['request']] = max(0, pending[delivery['request']] - delivery['characters'])
        passage_left -= sum(pending.values())
    seen, lists = set(), set()
    for index, request in enumerate(requests):
        base = '/passage_requests/%d' % index
        _require_type(request, dict, base)
        identifier = request.get('id')
        _require_type(identifier, str, base + '/id')
        _require_reading(0 < len(identifier) <= reading.RESERVED_IDENTIFIER_CHARACTERS, path=base + '/id',
                         characters=len(identifier), minimum_characters=1,
                         maximum_characters=reading.RESERVED_IDENTIFIER_CHARACTERS)
        _require_reading(identifier not in seen, path=base + '/id', request_id=identifier)
        seen.add(identifier)
        if bound_state is not None:
            _require_reading(identifier not in active_requests, path=base + '/id', request_id=identifier)
        if request.get('kind') == 'list_page':
            _require_members(request, ('id', 'kind', 'list', 'after'), base)
            _require_type(request['list'], str, base + '/list')
            _require_reading(find_list_supplier(request['list'], bound_state) is not None and request['list'] not in lists,
                             path=base + '/list', list=request['list'])
            if request['after'] is not None:
                _require_type(request['after'], str, base + '/after')
            lists.add(request['list'])
            if artifacts is not None:
                _validate_list_cursor(request['list'], request['after'], plan, bound_state, artifacts=artifacts,
                                      path=base)
        else:
            _require_members(request, ('id', 'unit', 'anchor', 'characters'), base)
            _require_type(request['unit'], str, base + '/unit')
            if request['anchor'] is not None:
                _require_type(request['anchor'], str, base + '/anchor')
                _require_reading(0 < len(request['anchor']) <= 600, path=base + '/anchor',
                                 characters=len(request['anchor']), minimum_characters=1, maximum_characters=600)
            _require_reading(type(request['characters']) is int, path=base + '/characters', expected_type='integer',
                             received_type=describe_json_type(request['characters']))
            _require_reading(0 < request['characters'] <= 100000, path=base + '/characters',
                             characters=request['characters'], minimum_characters=1, maximum_characters=100000)
            if bound_state is not None:
                unit = resolve_request_part(inventory, artifacts, request, bound_state)
                # A handle of the chain whose parts lack the anchor fails at the anchor; any other handle at the unit.
                known = any(row['handle'] == request['unit'] for row in inventory['units'] + inventory['material'])
                _require_reading(unit is not None, path=base + ('/anchor' if known else '/unit'),
                                 unit=request['unit'], anchor=request['anchor'])
                body = artifacts.read(unit['artifact']).decode('utf-8')
                start = 0 if request['anchor'] is None else body.find(request['anchor'])
                amount = min(request['characters'], len(body) - start)
                _require_reading(amount <= passage_left, path=base + '/characters', remaining=passage_left,
                                 requested=amount)
                passage_left -= amount
    if 'reading_final' in packet:
        _require_reading(bool(requests), path='/passage_requests', items=0, minimum_items=1)


def project_own_turn(artifacts, route, packet, output, assignment_id, attempt_id, *, state=None, inventory=None):
    """Project a checked answer into the state builder's own-turn vocabulary."""
    binding = packet.get('reading_turn', packet.get('reading_final', packet.get('reading_combine')))
    rows = binding.get('plan', {}).get('now', [])
    allowances = ({row['handle']: dict(row, remaining=row['characters'] - row['used'])
                   for row in state['allowances']['targets']} if state is not None else
                  {row['handle']: row for row in binding['allowances']['targets']})
    usage, entries = {}, []
    page_remaining = None
    if 'page_plan_digest' in binding:
        page_remaining = max(0, binding['capacity']['material_allowance'] - _measure_final_material(route,
            {key: ([] if key == 'fill' else binding.get(key, [])) for key in FINAL_MATERIAL_FIELDS}))
    for value in output.get('excerpts', []):
        if 'keep' in value:
            entry = dict(next(row for row in state['entries'] if row['handle'] == value['keep']),
                         targets=copy.deepcopy(value['targets']))
            source = next(row for row in inventory['units'] + inventory['material'] if row['handle'] == entry['unit'])
        else:
            start = _find_excerpt_span(packet, value['unit'], value['text'])
            entry = dict(kind='excerpt', unit=value['unit'], targets=value['targets'], start=start,
                end=start + len(value['text']), sha256=hashlib.sha256(value['text'].encode('utf-8')).hexdigest(),
                text=value['text'], purpose=value['purpose'], status='verified')
            source = next(row for row in rows if row['handle'] == value['unit'])
        charge = capacity.measure_capacity_value(route, dict(entry, handle='e999999999'))
        charge += capacity.measure_capacity_value(route, dict(handle=source['handle'], kind=source['kind'],
            label=source.get('label', source['kind'])))
        if (any(usage.get(target, 0) + charge > allowances[target]['remaining'] for target in value['targets'])
                or 'page_plan_digest' in binding and (not set(value['targets']) <= set(binding.get('page', {}).get('targets', []))
                    or charge > page_remaining)):
            entry['status'] = 'over_allowance'
        else:
            for target in value['targets']:
                usage[target] = usage.get(target, 0) + charge
            if page_remaining is not None:
                page_remaining -= charge
        entries.append(entry)
    note_number = len(state['turns']) + 1 if state is not None else binding['turn']
    references = {'n%d' % note_number: dict(reference=artifacts.put(
        (output.get('notes', binding.get('notes', state.get('latest_notes') if state is not None else None)) or '').encode('utf-8'), 'text/plain; charset=utf-8'))}
    gaps = []
    for index, value in enumerate(output.get('gaps', []), len(state['gaps']) + 1 if state is not None else 1):
        handle = 'h%d' % index
        reference = artifacts.put(serialize_record(value), 'application/json')
        references[handle] = dict(reference=reference)
        row = dict(handle=handle, formulation=value['formulation'], text=value['text'])
        status = _charge_reference_row(route, dict(row, handle='h999999999'),
                                       [value['formulation']], allowances, usage)
        gaps.append(dict(value, handle=handle, status=status))
    projected = dict(turn=dict(assignment_id=assignment_id, attempt_id=attempt_id,
                         kind='reading' if 'reading_turn' in packet else 'combine' if 'reading_combine' in packet else 'final', origin=None),
        deliveries=[dict(unit=row['handle'], part=row['part'], parts=1, start=row['start'], end=row['end'],
                        sha256=row['artifact']['sha256']) for row in rows if row['kind'] != 'list_page'],
        entries=entries, notes=dict(classification='current', units=[], text=output.get('notes', binding.get('notes', state.get('latest_notes') if state is not None else None)) or ''),
        searches=[], gaps=gaps, arguments=[], allowance_use=dict(targets=usage,
            passages=sum(row['characters'] for row in rows if row.get('request') and row['kind'] != 'list_page')),
        list_pages=[row['page'] for row in rows if row['kind'] == 'list_page'], references=references,
        requests=copy.deepcopy(output.get('passage_requests', [])),
        request_deliveries=[dict(id=row['request'], characters=row['characters'],
            complete=row['kind'] == 'list_page' or row['end'] >= row['request_end'])
            for row in rows if row.get('request')])
    if state is not None and 'unread' in state:
        projected['unread'] = copy.deepcopy(state['unread'])
    if inventory is not None:
        projected['searches'] = _collect_completed_searches(artifacts, inventory, state, projected['deliveries'])
    from .openness_final_pages import project_final_output
    projected = project_final_output(packet, output, projected, state)
    if 'final_blocks' in projected:
        block = json.loads(artifacts.read(binding['block']['artifact']))
        projected['final_catalog'] = {row['handle']: row['value'] for row in block['entries']}
    return projected


def _list_delivered_carried_entries(packet, artifacts, assignment):
    """Resolve keep handles only after their complete carried-reading unit was delivered."""
    _, inventory, state = read_bound_state(artifacts, assignment, packet)
    binding = packet.get('reading_turn', packet.get('reading_final', packet.get('reading_combine')))
    deliveries = state['deliveries'] + [dict(unit=row['handle'], start=row['start'], end=row['end'])
        for row in binding.get('plan', {}).get('now', []) if row['kind'] != 'list_page']
    entries = {row['handle']: row for row in state['entries'] if not row['targets'] and row['handle'] not in state['pool']}
    result = {}
    for unit in inventory['units']:
        if unit['kind'] != 'carried_reading' or unit['identity']['content_kind'] != 'entries':
            continue
        spans = sorted((row['start'], row['end']) for row in deliveries if row['unit'] in unit['bodies'])
        end = 0
        for left, right in spans:
            if left > end:
                break
            end = max(end, right)
        if end < unit['characters']:
            continue
        value = json.loads(artifacts.read(unit['artifact']))
        result.update({row['handle']: entries[row['handle']] for row in value['entries'] if row['handle'] in entries})
    return result


def _collect_completed_searches(artifacts, inventory, state, current):
    from .review_search_delivery import verify_search_deliveries
    combined = dict(state, deliveries=state['deliveries'] + current)
    incomplete = {row['handle'] for row, _, _ in _find_remaining_ranges(inventory, combined)}
    manifest = inventory['search_delivery']
    required = {row['artifact']['sha256'] for row in manifest['shared_fields']}
    required.update((row['residual'] or row['artifact'])['sha256'] for row in manifest['searches'])
    credited_searches = {row['handle'] for row in inventory['units']
        if row['identity'].get('record_kind') == 'literature_search' and row['sha256'] in state['searches']}
    delivered = {row['sha256']: dict(content=artifacts.read(row['artifact']), deliveries=[
        dict(value, unit=row['handle'], start=0, end=row['characters'], sha256=row['sha256'])
        if value['unit'] in credited_searches else value
        for value in combined['deliveries'] if value['unit'] == row['handle']
            or value['unit'] in credited_searches.intersection(row['units'])])
        for row in inventory['material'] if row['handle'] not in incomplete and row['sha256'] in required}
    originals = {row['search_digest']: artifacts.read(row['artifact']) for row in manifest['searches']}
    return [row['search_digest'] for row in verify_search_deliveries(manifest, delivered, originals)]


def _find_excerpt_span(packet, unit, text):
    binding = packet.get('reading_turn', packet.get('reading_final', packet.get('reading_combine')))
    for row, body in zip(binding.get('plan', {}).get('now', []), packet['evidence']):
        if row['handle'] == unit and row['kind'] != 'list_page' and text in body['content']:
            return row['start'] + body['content'].index(text)
    return None


def _charge_reference_row(route, row, targets, allowances, usage):
    """Retained gap/argument rows consume existing target cost, just like excerpts."""
    charge = capacity.measure_capacity_value(route, row)
    if any(usage.get(target, 0) + charge > allowances[target]['remaining'] for target in targets):
        return 'over_allowance'
    for target in targets:
        usage[target] = usage.get(target, 0) + charge
    return 'verified'


def read_bound_state(artifacts, assignment, packet):
    binding = packet.get('reading_turn', packet.get('reading_final', packet.get('reading_combine')))
    chain = assignment['reading_chain']
    plan = capacity.read_chain_capacity_plan(artifacts, assignment)
    inventory = json.loads(artifacts.read(chain['inventory']))
    state = json.loads(artifacts.read(chain['state']))
    if (digest(plan) != binding['plan_digest'] or digest(inventory) != binding['inventory_digest']
            or digest(state) != binding['state_digest']):
        raise ResearchError('review_invocation_unverified',
            'The packet of this turn does not bind the stored capacity plan, inventory and state of its chain: a '
            'digest in the packet differs from the stored artifact. The harness uses no output of the turn. Check '
            'the record with record-check.')
    return plan, inventory, state


def build_handle_packet(packet, artifacts, assignment):
    """Resolve v7 handles only from the packet's bound artifacts and own bodies.

    This transient view never enters a request. Every other protocol continues
    to supply its existing inline handle_table.
    """
    from .source_links import span_locator
    plan, inventory, state = read_bound_state(artifacts, assignment, packet)
    binding = packet.get('reading_turn', packet.get('reading_final', packet.get('reading_combine')))
    table = copy.deepcopy(inventory.get('references', {}))
    table.update(copy.deepcopy(state.get('references', {})))
    if 'unread' in state:
        table['x1'] = dict(reference=state['unread']['artifact'])
        for row in inventory['units'] + inventory['material']:
            table[row['handle']] = dict(reference=dict(row['artifact'], unit=row['handle']))
    for handle, value in state.get('final_catalog', {}).items():
        table.setdefault(handle, dict(reference=describe_artifact(serialize_record(value), 'application/json')))
    for handle, reference in state.get('final_blocks', {}).items():
        table[handle] = dict(reference=reference)
    for handle, value in state.get('combinations', {}).items():
        table[handle] = dict(reference=describe_artifact(serialize_record(value), 'application/json'))
    for row in state['entries']:
        if row['status'] in ('verified', 'delivered'):
            table[row['handle']] = dict(reference=describe_artifact(row['text'].encode('utf-8'), 'text/plain; charset=utf-8'))
    for row in packet.get('reading_combine', {}).get('block', {}).get('entries', []):
        handle = row.get('handle', row.get('block')) if isinstance(row, dict) else None
        if handle is not None and handle not in table:
            table[handle] = dict(reference=describe_artifact(serialize_record(row), 'application/json'))
    for row in packet.get('evidence', []):
        entry = dict(reference=dict(kind='source', link=dict(
            artifact=row['artifact'], locator=span_locator(row['content'], 0, len(row['content']), excerpt_length=0))),
            text=row['content'])
        part = next((value['part'] for value in binding.get('plan', {}).get('now', [])
                     if value['artifact'] == row['artifact'] and value['handle'] == row['handle']), 1)
        parts = table.get(row['handle'], {}).get('parts', {})
        parts[str(part)] = dict(entry, part=part)
        table[row['handle']] = dict(entry, part=part, parts=parts)
    resolved = dict(packet, handle_table=table, capacity=binding['capacity'],
                    material_state=copy.deepcopy(dict(state.get('material_state', dict(arguments={}, pending=None)),
                        pending=None) if 'reading_combine' in packet else state.get('material_state', dict(arguments={}, pending=None))))
    if describe_protocol(packet=packet)['final_rule'] == 'native_review' and reading.find_stage(packet) in ('final', 'closing'):
        # openness.evidence cites a delivered evidence unit by its handle, or by the handle of a body that belongs
        # to that unit alone, such as the residual of a bound search, and records the digest of the unit.
        from .review_handles import SUPPLIED_MINIMUM
        bodies = {row['handle']: row for row in inventory['material']}
        cited = {unit['handle']: dict(reference=unit['sha256']) for unit in inventory['units']
                 if _is_native_evidence_unit(unit) and all(_has_complete_body(inventory, state, bodies[handle])
                                                           for handle in unit['bodies'])}
        cited.update((body['handle'], cited[body['units'][0]]) for body in inventory['material']
                     if len(body['units']) == 1 and body['units'][0] in cited)
        table.update(cited)
        if reading.find_stage(packet) == 'final':
            # An approval in one request receives the minimum that the packet binds (decision 25).
            minimum = next(row for row in inventory['units'] if row['identity'].get('record_kind') == 'openness_minimum')
            resolved[SUPPLIED_MINIMUM] = json.loads(artifacts.read(minimum['artifact']))
    return resolved


def _is_native_evidence_unit(row):
    """Whether an inventory unit is evidence that the native packet lists: a reading row or a native evidence row.
    The SHA-256 of such a unit is the digest under which the packet lists it."""
    return row['kind'] in ('search_response', 'fulltext') or row['identity'].get('record_kind') in (
        'literature_search', 'reading', 'native_evidence')


def _project_observed_turn(records, artifacts, member, frozen, output, attempt_id, inventory, route, before):
    """Project one authenticated own response, including completed material references."""
    projection = project_own_turn(artifacts, route, frozen, output, member['id'], attempt_id,
                                 state=before, inventory=inventory)
    if output.get('stage') == 'material':
        from .review_handles import advance_material
        material = advance_material(build_handle_packet(frozen, artifacts, member), output, artifacts)
        projection.update(material_state=material['material_state'], material_request=material['material_request'])
        allowances = {row['handle']: dict(row, remaining=row['characters'] - row['used'])
                      for row in before['allowances']['targets']}
        old_arguments = before.get('material_state', {}).get('arguments', {})
        for key, argument in material['material_state']['arguments'].items():
            if argument['complete'] and not old_arguments.get(key, {}).get('complete'):
                row = dict(handle=key, title=argument['title'], reference=material['handle_table'][key]['reference'])
                status = _charge_reference_row(route, dict(row, handle='m999999999'), list(allowances),
                                               allowances, projection['allowance_use']['targets'])
                argument['status'] = status
                if status == 'verified':
                    projection['references'][key] = material['handle_table'][key]
    # Excerpt descriptors and gap details are immutable objects too.
    for entry in projection['entries']:
        artifacts.put(entry['text'].encode('utf-8'), 'text/plain; charset=utf-8')
    return projection


def _rebuild_own_state(records, artifacts, prior, inventory, plan, route):
    from .review_protocol import assignment_state
    from .review_refusal import classify_observed_output
    projections = []
    for member in reading._list_chain(records, prior):
        frozen = json.loads(artifacts.read(member['packet']))
        observed = assignment_state(records, artifacts, member['id'])
        if not observed['ready']:
            raise ResearchError('review_source_continuation_unverified',
                                'The turn that details.assignment_id names has no observed, authenticated '
                                'output that passed its checks, so the chain cannot continue from it. Run '
                                'review-series on the chain and take the step that it plans.',
                                dict(assignment_id=member['id'], obligations=observed['obligations']))
        failure = classify_observed_output(records, artifacts, observed['attempt_id'])
        if failure is not None:
            raise ResearchError('review_source_continuation_unverified',
                                UNCHECKED_HEAD_MESSAGE if member['id'] == prior['id'] else UNCHECKED_EARLIER_MESSAGE,
                                dict(assignment_id=member['id'], attempt_id=observed['attempt_id'], refusal=failure))
        output = observed['output']
        if (output.get('stage') not in ('reading', 'material')
                and not frozen.get('reading_combine')
                and not ('page_plan_digest' in frozen.get('reading_final', {})
                         and 'closing' not in frozen['reading_final'])):
            raise ResearchError('review_resampling_forbidden',
                'A final turn of this chain returned its finding, which ends the chain. Record the finding with '
                'problem-openness, or export a new packet for a new chain.')
        _, _, before = read_bound_state(artifacts, member, frozen)
        projection = _project_observed_turn(records, artifacts, member, frozen, output,
                                            observed['attempt_id'], inventory, route, before)
        projections.append(projection)
    first = records['review_assignment'][prior['reading_chain']['first_assignment_id']]
    initial = json.loads(artifacts.read(first['reading_chain']['state']))
    return build_chain_state(inventory, projections, route=route, plan=plan, initial_state=initial)


def compute_notice_material_charge(kind, notice):
    """Charge reading material for the notice; finals use the stored plan's reserve."""
    if kind == 'final' or notice is None:
        return 0
    return len(json.dumps(notice, ensure_ascii=False, sort_keys=True, allow_nan=False))


def prepare_next_assignment(records, artifacts, value, route, logical, *, prove_replacement=True):
    """Continue authenticated own turns, or rebuild one failed pending position."""
    from .review_protocol import (assignment_state, find_replacement, has_ended_without_output, is_dead_assignment,
                                  require_verified_context)
    from .operations import normalized_text
    reading.require_reserved_identifier(value['id'], describe_protocol(native=logical['native'])['id'])
    replacement = value['context'].get('reading_replacement')
    prior_id = replacement or value['context']['prior_assignment_id']
    prior = records['review_assignment'][prior_id]
    if (prior['route_id'] != value['route_id']
            or any(normalized_text(prior[key]) != normalized_text(value[key]) for key in ('reviewer_id', 'author_id'))
            or prior['context']['native_packet'] != value['context']['native_packet']):
        raise ResearchError('review_source_continuation_mismatch',
            'The next turn of a reading chain keeps the native packet, the route, the reviewer and the author of '
            'the turn that it continues. Repeat these values of that turn.')
    if find_replacement(records, prior) is not None:
        raise ResearchError('review_assignment_replaced',
            'A later assignment replaced the named turn. Continue from that replacement; review-series resolves '
            'the head of the chain from any of its assignments.')
    successors = [row for row in records.get('review_assignment', {}).values()
                  if row.get('context', {}).get('prior_assignment_id') == prior_id
                  and not ((has_ended_without_output(records, row['id']) or is_dead_assignment(records, row))
                           and find_replacement(records, row) is not None)]
    if successors:
        raise ResearchError('review_source_continuation_fork',
            'Another turn already continues the named turn, and a reading chain has one line of turns. Continue '
            'from the latest turn of the chain, which review-series resolves from any of its assignments.')
    require_verified_context(records, artifacts, prior)
    if replacement and prove_replacement:
        from .review_failure_class import plan_after_failure
        recovery = plan_after_failure(records, artifacts, prior_id)
        expected = recovery.get('details', {}).get('recovery', {}).get('payload', {})
        if recovery['state'] != 'replace' or expected.get('context') != value['context']:
            raise ResearchError('invalid_review_context',
                'This payload is not the replacement that the plan of the failed turn gives. A replacement with '
                'context.reading_replacement is admitted only as the step that review-series plans for that turn, '
                'with the context of that step. Run review-series on the head.')
    frozen = json.loads(artifacts.read(prior['packet']))
    handoff = describe_ratio_handoff(records, artifacts, prior)
    if handoff is not None:
        raise ResearchError(reading.PROJECTION_STOP, RATIO_EXPORT_MESSAGE, handoff['details'])
    plan, inventory, before = read_bound_state(artifacts, prior, frozen)
    if replacement:
        state = before
        kind = 'reading' if 'reading_turn' in frozen else 'final'
        number = prior['reading_chain']['turn']
        requests = state.get('pending_requests', [])
    else:
        observed = assignment_state(records, artifacts, prior_id)
        if not observed['ready']:
            raise ResearchError('review_source_continuation_unverified',
                'The named turn has no observed, authenticated output that passed its checks, so the chain cannot '
                'continue from it. Run review-series on the chain and take the step that it plans.')
        rebuilt = _rebuild_own_state(records, artifacts, prior, inventory, plan, route)
        state = rebuilt['state']
        requests = state.get('pending_requests', [])
        notes_cost = (capacity.measure_capacity_value(route, state['latest_notes'])
                      - capacity.measure_capacity_value(route, '')) if state['latest_notes'] is not None else 0
        kind = ('final' if 'unread' in state or prior['reading_chain'].get('final_page_plan') else 'reading' if (_find_remaining_ranges(inventory, state) or requests
                             or notes_cost > state['allowances']['notes']) else 'final')
        number = prior['reading_chain']['turn'] + 1
    notice = frozen.get('output_refusal') if replacement else None
    limits = capacity.derive_turn_task_limits(records, artifacts, prior, kind=kind,
        notice_characters=compute_notice_material_charge(kind, notice),
        response_replacement=prior_id if replacement else None)
    if replacement and kind == 'final' and state.get('material_state', {}).get('pending'):
        limits['answer_bound'] = max(plan['task']['final']['smallest_answer_bound'],
                                    frozen['reading_final']['capacity']['answer_bound'] // 2)
    final_page_plan = None
    if kind == 'final' and ('pages' in describe_protocol(packet=frozen)['list_final_forms']() or plan['allocation'].get('final_form') == 'pages' or prior['reading_chain'].get('final_page_plan')):
        from .openness_final_pages import prepare_final_packet, prepare_capacity_packet, build_final_packet, read_page_plan, read_binding
        if replacement and not state.get('material_state', {}).get('pending'):
            prepared = prepare_capacity_packet(records, artifacts, prior, route, logical,
                number, inventory, state, plan, limits)
            if prepared is None:
                raise ResearchError('review_route_capacity_below_floor', capacity.BELOW_FLOOR_MESSAGE)
            packet, final_page_plan = prepared
        elif replacement and prior['reading_chain'].get('final_page_plan'):
            final_page_plan = prior['reading_chain']['final_page_plan']
            page_plan = read_page_plan(artifacts, final_page_plan)
            selected = next(turn for turn in page_plan['turns'] if turn['number'] == read_binding(frozen)['final_turn'])
            packet = build_final_packet(records, artifacts, route, logical, prior['reading_chain']['first_assignment_id'],
                number, inventory, state, plan, limits, page_plan, selected)
        else:
            packet, final_page_plan = prepare_final_packet(records, artifacts, prior, route, logical,
                number, inventory, state, plan, limits)
    else:
        packet = build_turn_packet(artifacts, route, logical, prior['reading_chain']['first_assignment_id'],
                                   number, inventory, state, plan, limits, kind, requests=requests, notice=notice)
    if notice:
        from .review_refusal_notice import attach_refusal_notice
        packet = attach_refusal_notice(packet, notice)
    _publish_deliveries(artifacts, packet)
    chain = dict(first_assignment_id=prior['reading_chain']['first_assignment_id'], turn=number,
                 inventory=prior['reading_chain']['inventory'],
                 state=artifacts.put(serialize_record(state), 'application/json'),
                 capacity_plan=prior['capacity_plan'], capacity_plan_kind=kind)
    if final_page_plan is not None:
        chain['final_page_plan'] = final_page_plan
        from .openness_final_pages import read_binding, read_final_position
        chain['final_position'] = read_final_position(artifacts, final_page_plan, read_binding(packet)['final_turn'])
    if replacement:
        chain.update(capacity_replacement_of=prior['id'], slot_id=prior.get('slot_id', prior['id']))
    return packet, chain


def _publish_deliveries(artifacts, packet):
    for row in packet['evidence']:
        artifacts.put(row['content'].encode('utf-8'), row['artifact']['media_type'])


def prepare_refusal_assignment(records, artifacts, value, revision, prior, frozen, attempt_ids, diagnoses):
    """Replace a refused position from its unchanged before-state and notice-adjusted grant."""
    from . import review_protocol as protocol, review_prompt_paragraphs as paragraphs
    from . import review_refusal_notice as notices
    from .operations import immutable_record
    reading.require_reserved_identifier(value['id'], reading.find_protocol(frozen))
    protocol.require_verified_context(records, artifacts, prior)
    plan, inventory, state = read_bound_state(artifacts, prior, frozen)
    route = records['review_route'][prior['route_id']]
    stage = reading.find_stage(frozen)
    kind = 'reading' if stage == 'reading' else 'final'
    chain = prior['reading_chain']
    from .native_review import load_packet
    logical = dict(protocol=protocol.PROTOCOL, role=prior['role'], reviewer_id=prior['reviewer_id'],
                   dossier_id=prior['dossier_id'], native=load_packet(
                       artifacts, prior['context']['native_packet'], prior['reviewer_id']))
    parameters = paragraphs.read_paragraph_parameters(prior['prompt'])
    versions = [version for version in parameters.get(paragraphs.PARAMETER, [])
                if version not in paragraphs.REFUSAL_PARAGRAPH_VERSIONS.values()]
    def prepare_turn(notice):
        limits = capacity.derive_turn_task_limits(records, artifacts, prior, kind=kind,
                                                 notice_characters=compute_notice_material_charge(kind, notice))
        try:
            if chain.get('final_page_plan'):
                from .openness_final_pages import build_final_packet, read_page_plan, read_binding
                page_plan = read_page_plan(artifacts, chain['final_page_plan'])
                selected = next(turn for turn in page_plan['turns'] if turn['number'] == read_binding(frozen)['final_turn'])
                packet = build_final_packet(records, artifacts, route, logical, chain['first_assignment_id'], chain['turn'],
                    inventory, state, plan, limits, page_plan, selected)
            else:
                packet = build_turn_packet(artifacts, route, logical, chain['first_assignment_id'], chain['turn'],
                    inventory, state, plan, limits, kind, requests=state.get('pending_requests', []), notice=notice)
        except ResearchError as error:
            if error.code != 'review_route_capacity_plan_violated' or error.details.get('kind') != 'final':
                raise
            raise ResearchError('review_input_too_large', notices.NOTICE_DELIVERY_MSG, error.details)
        base = protocol.canonical_prompt(prior['role'], reading_stage=stage, reading_protocol=reading.find_protocol(frozen),
            common_version=protocol.find_common_prompt_version(prior['prompt']),
            finding_version=2 if 'finding_index' in packet else 1)
        prompt = paragraphs.append_prompt_paragraphs(base, prior['role'],
            parameters.get(paragraphs.CARRY_PARAMETER), versions + [paragraphs.REFUSAL_PARAGRAPH_VERSIONS[notice['form']]])
        prompt['parameters']['capacity'] = packet.get('reading_turn', packet.get('reading_final', packet.get('reading_combine')))['capacity']
        return packet, prompt
    limit = capacity.derive_turn_task_limits(records, artifacts, prior, kind=kind)['request_bound']['text_characters']
    selected = notices.select_refusal_notice(records, artifacts, attempt_ids, route=route, role=prior['role'],
        prompt=None, packet=None, prepare_turn=prepare_turn, task_characters=None,
        task_limit_characters=limit, diagnoses=diagnoses, capacity_plan=plan, turn_kind=kind)
    if selected['notice'] is None:
        raise ResearchError('review_input_too_large', notices.NOTICE_DELIVERY_MSG, dict(trials=selected['trials']))
    packet = review_excerpts.read_sent_packet(selected['request'])
    _publish_deliveries(artifacts, packet)
    record = copy.deepcopy(prior)
    for key in ('capacity_replacement_of', 'history_ref', 'history', 'history_assignment_id'):
        record.pop(key, None)
    record.update(value, packet=protocol._put_json(artifacts, packet), packet_digest=packet['digest'],
        prompt=selected['prompt'], prompt_hash=selected['prompt']['rendered_prompt_hash'], history=[],
        recorded_revision=revision, slot_id=prior.get('slot_id', prior['id']))
    record.update({key: prior[key] for key in ('reviewer_id', 'author_id')})
    return [immutable_record(records, 'review_assignment', value['id'], record)], record


def prepare_notice_capacity_assignment(records, artifacts, value, revision, prior):
    """Keep a frozen notice when capacity recovery reduces its pending turn."""
    from . import review_protocol as protocol, review_prompt_paragraphs as paragraphs
    from .native_review import load_packet
    from .operations import immutable_record
    route = records['review_route'][prior['route_id']]
    native = load_packet(artifacts, prior['context']['native_packet'], prior['reviewer_id'])
    logical = dict(protocol=protocol.PROTOCOL, role=prior['role'], dossier_id=prior['dossier_id'],
                   reviewer_id=prior['reviewer_id'], native=native)
    packet, chain = prepare_next_assignment(records, artifacts, value, route, logical)
    stage = reading.find_stage(packet)
    base = protocol.canonical_prompt(prior['role'], reading_stage=stage, reading_protocol=reading.find_protocol(packet),
        common_version=protocol.find_common_prompt_version(prior['prompt']),
        finding_version=2 if 'finding_index' in packet else 1)
    versions = paragraphs.read_paragraph_parameters(prior['prompt']).get(paragraphs.PARAMETER, [])
    prompt = paragraphs.append_prompt_paragraphs(base, prior['role'], versions=versions)
    prompt['parameters']['capacity'] = packet.get('reading_turn', packet.get('reading_final', packet.get('reading_combine')))['capacity']
    record = copy.deepcopy(prior)
    for key in ('history', 'history_ref', 'history_assignment_id'):
        record.pop(key, None)
    record.update(value, capacity_replacement_of=chain.pop('capacity_replacement_of'), slot_id=chain.pop('slot_id'),
        capacity_plan=chain.pop('capacity_plan'), capacity_plan_kind=chain.pop('capacity_plan_kind'),
        reading_chain=chain, packet=protocol._put_json(artifacts, packet), packet_digest=packet['digest'],
        prompt=prompt, prompt_hash=prompt['rendered_prompt_hash'], history=[], recorded_revision=revision)
    record.update({key: prior[key] for key in ('reviewer_id', 'author_id')})
    return [immutable_record(records, 'review_assignment', value['id'], record)], record


RATIO_EXPORT_MESSAGE = (
    'An attempt of this chain measured fewer characters for each token than its capacity plan assumes, and at the '
    'measured ratio its final, with the allowances that the chain has granted, no longer fits one request. The '
    'harness sends no further request of this chain. details.ratio gives the measurement and its attempt, and '
    'details.required_characters and details.available_characters the size of the final and the room for it. '
    'details.successor holds the admission of a new chain over a new export: its protocol and, for '
    'exactory-openness-reading-v7, the ratio of its plan under measure. '
    'When details.step is present, take it; details.message states what the new chain does. When it is absent, no '
    'protocol admits a new chain on this route, and details.decision states what the owner decides.')


def describe_ratio_handoff(records, artifacts, assignment):
    """End an unsafe grant before dispatch; successor admission uses the same ratio."""
    if not assignment.get('capacity_plan') or not assignment.get('reading_chain'):
        return None
    frozen = json.loads(artifacts.read(assignment['packet']))
    if reading.find_protocol(frozen) not in PROTOCOL_DESCRIPTIONS:
        return None
    plan = capacity.read_chain_capacity_plan(artifacts, assignment)
    from .review_failure_class import list_capacity_chain_assignments
    identifiers = list_capacity_chain_assignments(records, assignment)
    measured = []
    for attempt in sorted(records.get('review_attempt', {}).values(), key=lambda row: (row.get('recorded_revision', 0), row['id'])):
        if attempt['assignment_id'] in identifiers:
            ratio = capacity.measure_attempt_ratio(attempt, margin=plan['measure']['ratio_margin'])
            if ratio is not None:
                measured.append(dict(characters_per_token=ratio, source=dict(
                    chain=assignment['reading_chain']['first_assignment_id'], attempt=attempt['id'])))
    inherited = min(measured, key=lambda row: row['characters_per_token']) if measured else None
    if inherited is None or inherited['characters_per_token'] >= plan['measure']['characters_per_token']:
        return None
    room = math.floor(plan['limits']['input_tokens']['value'] * inherited['characters_per_token'])
    from .review_refusal_notice import BOUNDED_NOTICE_CHARACTERS
    granted = (plan['allocation']['notes'] + sum(row['allowance'] for row in plan['allocation']['targets'])
               + plan['allocation'].get('pool_used', 0))
    required = plan['frame']['final']['bound'] + BOUNDED_NOTICE_CHARACTERS + max(
        capacity.describe_material_levels('final')[-1], granted)
    final_limits = capacity.derive_turn_task_limits(records, artifacts, assignment, kind='final')
    if 'pages' in describe_protocol(packet=frozen)['list_final_forms']() or plan['allocation'].get('final_form') == 'pages' or assignment['reading_chain'].get('final_page_plan'):
        required = (plan['frame']['final']['bound'] + BOUNDED_NOTICE_CHARACTERS
                    + capacity.NOTES_MINIMUM_CHARACTERS + capacity.EXCERPT_TEXT_CHARACTERS + capacity.EXCERPT_FIELDS_CHARACTERS)
        if required <= room:
            return None
    if required <= room and granted <= final_limits['material_allowance']:
        return None
    from .openness_successor import describe_successor_handoff
    return describe_successor_handoff(records, artifacts, assignment, reading.PROJECTION_STOP,
        dict(message=RATIO_EXPORT_MESSAGE, ratio=inherited, plan_digest=digest(plan),
                     required_characters=required, available_characters=room,
                     final_material_allowance=final_limits['material_allowance'], granted_characters=granted))


def has_deliverable_requests(artifacts, packet, output, assignment):
    if output.get('stage') != 'reading' or 'reading_final' not in packet:
        return True
    plan, inventory, state = read_bound_state(artifacts, assignment, packet)
    for request in output['passage_requests']:
        if request.get('kind') == 'list_page':
            return True  # The output boundary already checked its cursor.
        row = resolve_request_part(inventory, artifacts, request, state)
        if row is not None and state['allowances']['passages']['used'] < state['allowances']['passages']['characters']:
            return True
    return False


def describe_turn_receipt(records, artifacts, assignment, route, packet, request):
    plan, inventory, state = read_bound_state(artifacts, assignment, packet)
    remaining = [dict(handle=row['handle'] + '@' + str(start), kind=row['kind'], characters=end - start)
                 for row, start, end in _find_remaining_ranges(inventory, state)]
    observed = {kind: [dict(material_allowance=capacity.derive_turn_task_limits(
        records, artifacts, assignment, kind=kind)['material_allowance'])] for kind in plan['task']}
    forecast = capacity.describe_capacity_forecast(plan, route, capacity.read_output_profile(records, route),
        records=records, turn_kind=assignment['capacity_plan_kind'], remaining_inventory=remaining,
        observations_by_kind=observed)
    if assignment['reading_chain'].get('final_page_plan'):
        from .openness_final_pages import read_page_plan, FINAL_PAGES_RECEIPT
        page_plan = read_page_plan(artifacts, assignment['reading_chain']['final_page_plan'])
        final_turns = page_plan['turns']
        forecast['by_kind']['final'].update(pages=sum(row['kind'] in ('page', 'final') for row in final_turns),
            condensing_turns=sum(row['kind'] == 'combine' for row in final_turns),
            closing_pages=sum(row['kind'] == 'closing' for row in final_turns), final_form='pages',
            message=FINAL_PAGES_RECEIPT)
    final = capacity.derive_turn_task_limits(records, artifacts, assignment, kind='final')['request_bound']['text_characters']
    warning = dict(input=dict(capacity_warning=False, request=capacity.measure_request(route, request, records=records, artifacts=artifacts),
                             projected_input_characters=final))
    warning.update(capacity.describe_output_warning(plan['task']) or {})
    admission = dict(state='record', protocol=inventory['protocol'], plan_digest=digest(plan))
    if 'pages' in describe_protocol(packet=packet)['list_final_forms']() or assignment['reading_chain'].get('final_page_plan'):
        final_counts = forecast['by_kind']['final']
        admission.update(final_form='pages' if assignment['reading_chain'].get('final_page_plan') else
            plan['allocation'].get('final_form', 'request'), pages=final_counts.get('pages', 1),
            condensing_turns=final_counts.get('condensing_turns', 0), closing_pages=final_counts.get('closing_pages', 0))
    return dict(capacity_forecast=forecast, capacity_warning=warning,
        reading_chain_admission=admission,
        diagnostics=dict(final_input=dict(protocol=inventory['protocol'], kind='bound', input_characters=final,
            input_limit_characters=plan['limits']['input_characters']['value'], capacity_warning=False)),
        protocol_selection=assignment.get('protocol_selection'))


def describe_turn_forecast(records, artifacts, assignment, *, request=None):
    """Describe the forecast of a chain over the inputs that remain at the turn of a stored assignment, as its receipt
    and the lines of review-series give it, or the unavailable forecast when the stored inputs of the turn cannot be
    read. request is the request of the turn, when the caller has built it."""
    from .review_protocol import _read_json, build_assignment_request
    try:
        packet = _read_json(artifacts, assignment['packet'])
        if request is None:
            request = build_assignment_request(records, artifacts, assignment)
        route = records['review_route'][assignment['route_id']]
        return describe_turn_receipt(records, artifacts, assignment, route, packet, request)['capacity_forecast']
    except (ResearchError, ValueError) as error:
        return reading.describe_unavailable_forecast(error)


def describe_admission_stop(records, artifacts, assignment, route, packet, selection):
    from .review_plan_steps import describe_step
    from .review_protocol import canonical_prompt
    export = describe_step(records, artifacts, assignment, 4)
    step = export['then'][0]
    export['then'] = [dict(step, check=True), step]
    smaller = copy.deepcopy(export)
    smaller['prerequisites'].insert(0, dict(condition='Record a statement with fewer neighbours, then bind that record in a new export.'))
    route_step = describe_step(records, artifacts, assignment, 7)
    prompt = canonical_prompt('native_math', version=7, finding_version=2)['text']
    serving = capacity.find_serving_routes(records, 'native_math', packet, artifacts=artifacts,
                                           assignment=assignment, prompt=prompt)
    refusal = selection['admissions'][0]
    return dict(message=ADMISSION_STOP_MESSAGE, admissions=selection['admissions'],
        targets=refusal.get('targets', 1 + len(packet['native']['scientific_context']['openness_minimum']['neighbours'])),
        largest_one_request_targets=refusal.get('largest_one_request_targets', 0), serving_routes=serving,
        unserved_range=(FINAL_PAGES_STATEMENT if V7_FINAL_FORMS == ('request',) else FINAL_PAGE_FLOOR_STATEMENT) if not serving else None,
        decision=dict(owner_decides=ADMISSION_QUESTION,
            then=[dict(answer=ADMISSION_SMALLER_ANSWER, steps=[smaller]),
                  dict(answer=ADMISSION_ROUTE_ANSWER, steps=[route_step, export])]))


def plan_turn_recovery(records, artifacts, assignment, outcome):
    """Prove the next pinned reading level or smaller pending material answer."""
    from . import review_preflight, review_protocol
    frozen = json.loads(artifacts.read(assignment['packet']))
    route = records['review_route'][assignment['route_id']]
    kind = assignment['capacity_plan_kind']
    binding = frozen.get('reading_turn', frozen.get('reading_final', frozen.get('reading_combine')))
    plan = capacity.read_chain_capacity_plan(artifacts, assignment)
    if kind == 'final':
        _, _, state = read_bound_state(artifacts, assignment, frozen)
        if state.get('material_state', {}).get('pending'):
            if binding['capacity']['answer_bound'] <= plan['task']['final']['smallest_answer_bound']:
                return None
        elif 'pages' not in describe_protocol(packet=frozen)['list_final_forms']() and not assignment['reading_chain'].get('final_page_plan'):
            return None
    limits = capacity.derive_turn_task_limits(records, artifacts, assignment, kind=kind,
        response_replacement=assignment['id'] if outcome == 'response_over_limit' else None)
    if kind == 'reading' and limits['material_allowance'] >= binding['capacity']['material_allowance']:
        return None
    value = {key: copy.deepcopy(assignment[key]) for key in
             ('id', 'role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id', 'context')}
    from .review_continuation import _find_unused_id
    value['id'] = _find_unused_id(records, value['id'] + '-replacement', maximum=reading.RESERVED_IDENTIFIER_CHARACTERS)
    value['context']['reading_replacement'] = assignment['id']
    from .review_refusal_replacement import read_position_context
    logical = review_protocol.build_packet(records, artifacts, value['dossier_id'], value['role'], value['reviewer_id'],
                                           **read_position_context(value))
    overlay = _PlanningArtifacts(artifacts)
    try:
        packet, chain = prepare_next_assignment(records, overlay, value, route, logical, prove_replacement=False)
    except ResearchError as error:
        if error.code == 'review_route_capacity_below_floor':
            return None
        raise
    from . import review_prompt_paragraphs as paragraphs
    from .openness_final_pages import read_binding
    # A replacement keeps the paragraphs of a refused prompt; otherwise its packet selects them, as at assignment.
    versions = (paragraphs.read_paragraph_parameters(assignment['prompt']).get(paragraphs.PARAMETER, [])
                if packet.get('output_refusal') else
                paragraphs.select_prompt_parameters('native_math', packet).get(paragraphs.PARAMETER, []))
    prompt = review_protocol.canonical_prompt('native_math', reading_stage=reading.find_stage(packet), reading_protocol=reading.find_protocol(packet),
        capacity=read_binding(packet)['capacity'], problem_paragraphs=versions or None,
        finding_version=2 if 'finding_index' in packet else 1)
    candidate = dict(value, prompt=prompt, packet=overlay.put(serialize_record(packet), 'application/json'),
                     reading_chain=chain, capacity_plan=assignment['capacity_plan'], capacity_plan_kind=kind)
    request = review_protocol.transport.request_body(route, prompt['text'], packet)
    preflight = review_preflight.check_request(review_preflight.build_assignment_report(
        records, overlay, candidate, request), request, packet=packet, artifacts=overlay)
    bounds = read_binding(packet)['capacity']
    return dict(kind='replace', reason='capacity', payload=value, preflight=preflight,
                details=dict(material_allowance=bounds['material_allowance'], answer_bound=bounds['answer_bound']))


# The verification of a v7 chain has seven conditions, and a chain that fails one gives no finding. Each condition has
# its own refusal message; condition 6 refuses with the messages of review_refusal_replacement. Condition 1 also
# refuses with SELECTION_MISMATCH_MESSAGE, with a refusal of the artifact store, and with claim_reassessment_required
# for records without a history. Every other refusal of an earlier chain passes through unchanged. In the second part
# of condition 1, a refusal of the artifact store and a refusal in openness_carried.PREFIX_STATE_CODES give
# SELECTION_MISMATCH_MESSAGE. Condition 7: the answer of every own turn passes the output check that its run applies
# (review_refusal.classify_observed_output), also when the store holds no refusal record for it.
ORIGIN_VERIFICATION_MESSAGE = (
    'The harness cannot verify the carried selection of this chain from its first assignment. One of two cases is '
    'the cause. First, the reference carried_selection of that assignment names a protocol or a digest other than '
    'those of the stored selection. Second, under exactory-openness-carried-selection-v2, recorded_revision of the '
    'first assignment is not the revision at which the history of the store shows that assignment recorded. The '
    'chain gives no finding. Check the record with record-check. When record-check reports the record ready, '
    'report this refusal.')
STATE_VERIFICATION_MESSAGE = (
    'A stored inventory or state of this chain differs from what the harness rebuilds from the native packet, the '
    'carried selection and the authenticated turns, or the state_digest in the packet of a turn does not name the '
    'rebuilt state. details.assignment_id names the turn where the check concerns one. The chain gives no finding. '
    'Check the record with record-check.')
BINDING_VERIFICATION_MESSAGE = (
    'The turn that details.assignment_id names is not the turn that this chain gives at its position. Every turn '
    'keeps the role, the native packet, the route, the reviewer, the author, the inventory and the capacity plan of '
    'the first assignment, and its packet and its prompt equal those that the harness rebuilds from the state '
    'before the turn, at the task limits that held when the turn was recorded. The chain gives no finding. Check '
    'the record with record-check.')
COVERAGE_VERIFICATION_MESSAGE = (
    'This chain does not hold everything that its finding needs. A required range or a required affected note is '
    'not delivered (details.ranges), a bound search does not rebuild from the delivered text (details.searches), a '
    'required body of the evidence of the native packet is not delivered whole (details.digests), the native packet '
    'of the finding is not the packet that the chain verified (also when details.digests is empty), or, for a final '
    'over unread ranges, the stored records do not show the refused turn at the end of its last permitted sequence, '
    'or the unread set, the outcome unresolved, the citation of the set in the finding or the recorded finding '
    'differs from what the harness rebuilds. Run review-series '
    'on the chain and take the step that it plans.')
ANSWER_VERIFICATION_MESSAGE = (
    'The answer of the turn that details.assignment_id names fails the output check of its turn, and the store '
    'holds no refusal record for it. details.refusal gives the code, the check and the details of the failure. The '
    'chain gives no finding from that answer or from a turn after it. When a later turn of this chain follows the '
    'named turn, that turn was built on the answer. Then export a new packet with the same reviewer_id and assign '
    'it. The new chain can carry the reading turns of this chain that come before the named turn.')
UNCHECKED_HEAD_MESSAGE = (
    'The answer of the turn that details.assignment_id names fails the output check of its turn, and the store holds '
    'no refusal record for it. details.refusal gives the code, the check and the details of the failure. No turn '
    'continues from that answer. Run review-series on the chain and take the step that it plans. Its step for this '
    'answer is the recorded review-run request, which calls no reviewer and records the refusal. The plan after that '
    'request is the step for a refused answer.')
UNCHECKED_EARLIER_MESSAGE = (
    'The answer of the turn that details.assignment_id names fails the output check of its turn, and the store holds '
    'no refusal record for it. details.refusal gives the code, the check and the details of the failure. A later turn '
    'of this chain was built on that answer. The harness assigns no turn that continues this chain, and the chain '
    'gives no finding. Export a new packet with the same reviewer_id and assign it. The new chain can carry the '
    'reading turns of this chain that come before the named turn.')
FINAL_VERIFICATION_MESSAGE = (
    'The turn that details.assignment_id names has no observed, authenticated output in a verified context, or its '
    'output stands at the wrong place. A chain of exactory-openness-reading-v7 ends with one final turn that '
    'returns the finding, and every turn before it returns a reading response or a part of an argument. The chain '
    'gives no finding. Run review-series on the chain and take the step that it plans.')


def _require_verification(condition, message, **details):
    if not condition:
        raise ResearchError('openness_reading_incomplete', message, details)


def _verify_carried_origins(records, artifacts, first, native, route, plan, evaluation):
    """Condition 1: authenticate named origin prefixes through their protocol observers.

    Versions 2 and 3 first check the complete selection against the authenticated store before this assignment.
    Origin observers recurse only into assignments recorded before this first turn.
    """
    from .openness_carried import CUTOFF_PROTOCOLS, verify_stored_selection
    reference = first['carried_selection']
    selection = json.loads(artifacts.read(reference['artifact']))
    _require_verification(isinstance(selection, dict) and reference['protocol'] == selection.get('protocol')
                          and reference['digest'] == digest(selection),
                          ORIGIN_VERIFICATION_MESSAGE)
    cutoff = None
    if reference['protocol'] in CUTOFF_PROTOCOLS:
        stamp = first['recorded_revision']
        # Records without a history end here with the refusal of the evaluation, which is no state of a chain.
        cutoff = evaluation.read_snapshot(stamp - 1)
        _require_verification(
            evaluation.records.verify_record_version('review_assignment', first['id'], stamp, digest(first)),
            ORIGIN_VERIFICATION_MESSAGE)
        _require_verification(first['id'] not in cutoff.records.get('review_assignment', {}),
                              ORIGIN_VERIFICATION_MESSAGE)
    base = read_carried_inventory(artifacts, first)
    identity = _describe_carried_identity(records, first, route, native)
    projections = verify_stored_selection(records, artifacts, base, identity, selection, cutoff=cutoff, plan=plan)
    return selection, projections


def _verify_rebuilt_state(artifacts, assignment, packet, rebuilt):
    """Condition 2: compare the entire projected state, rather than trusting its digest alone."""
    binding = packet.get('reading_turn', packet.get('reading_final', packet.get('reading_combine', {})))
    stored = json.loads(artifacts.read(assignment['reading_chain']['state']))
    _require_verification(stored == rebuilt and binding.get('state_digest') == digest(rebuilt),
                          STATE_VERIFICATION_MESSAGE, assignment_id=assignment['id'])


def _verify_turn_binding(records, artifacts, first, assignment, packet, native, route,
                         inventory, state, plan, number):
    """Condition 3: rebuild the turn at its historical allowance with its recorded prompt ids."""
    from . import review_protocol as protocol
    from .operations import normalized_text
    stage = reading.find_stage(packet)
    kind = 'final' if stage in ('page', 'closing', 'combine') else stage
    binding = packet.get('reading_turn', packet.get('reading_final', packet.get('reading_combine', {})))
    chain = assignment['reading_chain']
    _require_verification(kind in ('reading', 'final') and reading.find_protocol(packet) == inventory['protocol']
        and assignment['role'] == first['role'] == 'native_math'
        and assignment['context']['native_packet'] == first['context']['native_packet']
        and assignment['route_id'] == first['route_id']
        and all(normalized_text(assignment[key]) == normalized_text(first[key]) for key in ('reviewer_id', 'author_id'))
        and chain['first_assignment_id'] == first['id'] and chain['turn'] == number
        and chain['inventory'] == first['reading_chain']['inventory']
        and assignment['capacity_plan'] == first['capacity_plan']
        and binding.get('plan_digest') == digest(plan) and binding.get('inventory_digest') == digest(inventory),
        BINDING_VERIFICATION_MESSAGE, assignment_id=assignment['id'])
    refusal = assignment['context'].get('output_refusal')
    notice_characters = compute_notice_material_charge(kind, packet['output_refusal'] if refusal else None)
    prior_id = (assignment.get('capacity_replacement_of') or (refusal['assignment_id'] if refusal else
                assignment['context'].get('unread', {}).get('assignment_id') or assignment['context'].get('prior_assignment_id')))
    if prior_id is None:
        limits = capacity.derive_plan_task_limits(plan, kind)
    else:
        # The current output and later measurements cannot alter this request's grants.
        historical = dict(records, review_attempt={key: row for key, row in records.get('review_attempt', {}).items()
            if row['recorded_revision'] < assignment['recorded_revision']})
        prior = records['review_assignment'][prior_id]
        limits = capacity.derive_turn_task_limits(historical, artifacts, prior, kind=kind,
            notice_characters=notice_characters,
            response_replacement=prior_id if assignment.get('capacity_replacement_of') else None)
        if (assignment.get('capacity_replacement_of') and kind == 'final'
                and state.get('material_state', {}).get('pending')):
            frozen = json.loads(artifacts.read(prior['packet']))
            limits['answer_bound'] = max(plan['task']['final']['smallest_answer_bound'],
                                        frozen['reading_final']['capacity']['answer_bound'] // 2)
    logical = dict(protocol=protocol.PROTOCOL, role=first['role'], reviewer_id=first['reviewer_id'],
                   dossier_id=first['dossier_id'], native=native)
    from .native_review import find_finding_version
    finding_version = find_finding_version(assignment)
    if finding_version == 2:
        verify_packet_finding_index(records, artifacts, native, plan, inventory, route, packet)
    if 'page_plan_digest' in binding:
        from .openness_final_pages import verify_final_turn_binding
        verify_final_turn_binding(records, artifacts, assignment, packet, logical, inventory, state, plan, limits)
        expected = packet
    else:
        expected = build_turn_packet(artifacts, route, logical, first['id'], number, inventory, state, plan,
                                     limits, kind, requests=state.get('pending_requests', []),
                                     notice=packet['output_refusal'] if refusal else None,
                                     finding_version=finding_version)
    if refusal:
        from .review_refusal_notice import attach_refusal_notice
        expected = attach_refusal_notice(expected, packet['output_refusal'])
    _require_verification(packet == expected and protocol._prompt_matches(assignment, packet)
        and assignment['prompt']['parameters'].get('reading_stage') == stage
        and assignment['prompt']['parameters'].get('capacity') == binding['capacity'],
        BINDING_VERIFICATION_MESSAGE, assignment_id=assignment['id'])


def verify_packet_finding_index(records, artifacts, native, plan, inventory, route, packet):
    """Require the complete index of the plan to rebuild from the bound originals, with the display fields of
    read_works fixed at the assignment, and the member of a final packet to be its form for that packet (G8.9)."""
    from .review_search_delivery import FINDING_INDEX_MESSAGE, verify_finding_index
    verify_finding_index(records, artifacts, native, plan['finding_index'])
    binding = packet['reading_final']
    formulations = None
    if 'page_plan_digest' in binding and 'page' in binding:
        labels = {row['handle']: row['formulation'] for row in list_target_labels(native)}
        formulations = [labels[handle] for handle in binding['page']['targets']]
    if packet.get('finding_index') != fit_packet_finding_index(route, inventory, plan['finding_index'],
                                                                formulations=formulations):
        raise ResearchError('openness_reading_incomplete', FINDING_INDEX_MESSAGE)


def _verify_replacement_origin(records, artifacts, assignment, packet):
    """Condition 6: authenticate the frozen refusals of this position without rechecking them."""
    from .review_refusal_replacement import validate_replacement_origin
    validate_replacement_origin(records, artifacts, assignment, packet)


def _verify_required_coverage(artifacts, inventory, state, plan, selection, *,
                              records=None, packet=None, output=None, assignment=None):
    """Condition 4: ranges, required affected notes and reconstructed original searches."""
    required = {row['handle'] for row in list_required_affected_notes(plan, selection)['required']}
    remaining = _find_remaining_ranges(inventory, state)
    missing = [dict(unit=body['handle'], start=start, end=end) for body, start, end in remaining]
    # Read the selection policy directly: do not infer required notes from delivery priority.
    for body in inventory['material']:
        if body['handle'] in required and not _has_complete_body(inventory, state, body):
            if not any(row['unit'] == body['handle'] for row in missing):
                missing.append(dict(unit=body['handle'], start=0, end=body['characters']))
    searches = _collect_completed_searches(artifacts, inventory, state, [])
    expected = {row['search_digest'] for row in inventory['search_delivery']['searches']}
    if 'unread' in state:
        from .native_review import accepts_unread_ground, find_finding_version
        records = _read_unread_history(records, state['unread'])
        origin = records['review_assignment'][state['unread']['assignment_id']]
        before = json.loads(artifacts.read(origin['reading_chain']['state']))
        rebuilt = build_unread_set(records, artifacts, origin, inventory, before)
        unread = packet.get('reading_final', {}).get('unread', {})
        finding = output.get('findings', {}).get('openness', {})
        stored = json.loads(artifacts.read(state['unread']['artifact']))
        reference = describe_artifact(serialize_record(rebuilt), 'application/json')
        _require_verification(rebuilt == stored and digest(rebuilt) == unread.get('digest')
            and reference == unread.get('artifact') == state['unread']['artifact']
            and finding.get('outcome') == 'unresolved'
            and any(accepts_unread_ground(ground, find_finding_version(assignment)) and reference in ground.get('refs', [])
                    for ground in finding.get('grounds', [])), COVERAGE_VERIFICATION_MESSAGE)
        recorded = records.get('problem_openness', {}).get(assignment['id']) if assignment is not None else None
        if recorded is not None:
            _require_verification(recorded.get('outcome') == finding['outcome']
                and recorded.get('grounds') == finding['grounds']
                and recorded.get('unread') == dict(artifact=reference, digest=digest(rebuilt), **rebuilt),
                COVERAGE_VERIFICATION_MESSAGE)
        return searches
    _require_verification(not missing and expected <= set(searches), COVERAGE_VERIFICATION_MESSAGE,
                          ranges=missing, searches=sorted(expected - set(searches)))
    return searches


def _has_complete_body(inventory, state, body):
    spans = [(row['start'], row['end']) for row in state['deliveries']
             if row['unit'] == body['handle'] or row['unit'] in body['units']]
    end = 0
    for left, right in sorted(spans):
        if left > end:
            return False
        end = max(end, right)
    return end >= body['characters']


def _verify_final_attempt(records, artifacts, assignment, observed, packet, *, final):
    """Condition 5: a final is one observed authenticated finding, ending this prefix."""
    from .review_protocol import authenticate_attempt
    terminal = reading.find_stage(packet) in ('final', 'closing') and observed['output'].get('stage') not in ('reading', 'material')
    _require_verification(terminal == final and authenticate_attempt(records, artifacts, assignment,
        records['review_attempt'][observed['attempt_id']]), FINAL_VERIFICATION_MESSAGE,
        assignment_id=assignment['id'])


def verify_problem_coverage(records, artifacts, assignment, *, final=False):
    """Verify a v7 prefix/final from its stored selection and plan, without admission or planning."""
    from .native_review import load_packet
    from .review_protocol import assignment_state
    from .review_refusal import classify_observed_output
    from .evaluation import Evaluation
    overlay = _PlanningArtifacts(artifacts)
    chain = reading._list_chain(records, assignment)
    first = records['review_assignment'][assignment['reading_chain']['first_assignment_id']]
    route = records['review_route'][first['route_id']]
    native = load_packet(overlay, first['context']['native_packet'], first['reviewer_id'])
    plan = capacity.read_chain_capacity_plan(overlay, first)
    inventory = json.loads(overlay.read(first['reading_chain']['inventory']))
    base = read_carried_inventory(overlay, first)
    # Inventory reconstruction authenticates its native source bindings; no capacity plan is computed.
    built = prepare_inventory(build_inventory(records, overlay, native, review=first), native, route,
                              fixed_bounds=base['fixed_bounds'], finding_index=plan.get('finding_index'))
    _publish_bodies(overlay, built['bodies'])
    _require_verification(built['inventory'] == base, STATE_VERIFICATION_MESSAGE)
    selection, projections = _verify_carried_origins(records, overlay, first, native, route, plan,
                                                    Evaluation.of(records, artifacts))
    rebuilt = build_chain_state(base, projections, route=route, plan=plan)
    _require_verification(rebuilt['inventory'] == inventory, STATE_VERIFICATION_MESSAGE)
    state = rebuilt['state']
    _publish_bodies(overlay, rebuilt['bodies'])
    turns = [copy.deepcopy(row['turn']) for row in projections]
    own = []
    for number, member in enumerate(chain, 1):
        packet = json.loads(overlay.read(member['packet']))
        _verify_replacement_origin(records, overlay, member, packet)
        if member['context'].get('unread') and 'unread' not in state:
            state = _bind_unread_state(records, overlay, member, inventory, state, publish=False)
        _verify_rebuilt_state(overlay, member, packet, state)
        _verify_turn_binding(records, overlay, first, member, packet, native, route, inventory, state, plan, number)
        observed = assignment_state(records, overlay, member['id'])
        _require_verification(observed['ready'], FINAL_VERIFICATION_MESSAGE,
                              assignment_id=member['id'], obligations=observed['obligations'])
        failure = classify_observed_output(records, overlay, observed['attempt_id'])
        _require_verification(failure is None, ANSWER_VERIFICATION_MESSAGE,
                              assignment_id=member['id'], attempt_id=observed['attempt_id'], refusal=failure)
        terminal = final and number == len(chain)
        _verify_final_attempt(records, overlay, member, observed, packet, final=terminal)
        projected = _project_observed_turn(records, overlay, member, packet, observed['output'],
                                          observed['attempt_id'], inventory, route, state)
        own.append(dict(assignment=member, packet=packet, projection=projected))
        turns.append(dict(projected['turn'], number=number))
        state = build_chain_state(inventory, [projected], route=route, plan=plan, initial_state=state)['state']
    final_output = observed['output']
    if assignment['reading_chain'].get('final_page_plan'):
        from .openness_final_pages import verify_final_page_plans
        verify_final_page_plans(records, overlay, assignment, native, inventory, plan, route)
        if final:
            from .review_composite import read_final_output
            final_output, _ = read_final_output(records, overlay, assignment)
    searches = _verify_required_coverage(overlay, inventory, state, plan, selection,
        records=records, packet=packet, output=final_output, assignment=assignment) if final else state['searches']
    units = [dict(row['identity'], artifact=row['artifact']) for row in inventory['units']
             if (row['sha256'] in searches if row['identity'].get('record_kind') == 'literature_search'
                 else all(_has_complete_body(inventory, state, body) for body in inventory['material']
                          if body['handle'] in row['bodies']))]
    return dict(turns=turns, searches=searches, units=units, inventory=inventory, state=state, own=own, native=native)


def verify_chain(records, artifacts, assignment):
    """Return carried turns first with their origin, then this chain's own turns and final."""
    return verify_problem_coverage(records, artifacts, assignment, final=True)['turns']


def observe_v7_chain(records, artifacts, assignment):
    """Expose only this v7 chain's ready reading turns; carried turns keep their own origin."""
    packet = json.loads(artifacts.read(assignment['packet']))
    from .review_protocol import assignment_state
    terminal = assignment_state(records, artifacts, assignment['id'])
    final = reading.find_stage(packet) in ('final', 'closing') and terminal['output'] is not None and terminal['output'].get('stage') not in ('reading', 'material')
    verified = verify_problem_coverage(records, artifacts, assignment, final=final)
    inventory = verified['inventory']
    units = {row['handle']: row for row in inventory['units']}
    materials = {row['handle']: row for row in inventory['material']}
    result = []
    for own in verified['own']:
        projected = own['projection']
        if projected['turn']['kind'] != 'reading':
            continue
        member = own['assignment']
        before = json.loads(artifacts.read(member['reading_chain']['state']))
        searches = [sha256 for sha256 in projected['searches'] if sha256 not in before['searches']]
        dependencies = {}
        for span in before['deliveries'] + projected['deliveries']:
            handles = [span['unit']] if span['unit'] in units else materials[span['unit']]['units']
            for handle in handles:
                unit = units[handle]
                if unit['kind'] not in ('carried_reading', 'target_page', 'fixed_field'):
                    dependencies[digest(unit['identity'])] = dict(identity=unit['identity'], sha256=unit['sha256'])
        for entry in before['entries']:
            if entry['handle'] not in before['pool'] and not entry['targets']:
                continue
            handles = [entry['unit']] if entry['unit'] in units else materials[entry['unit']]['units']
            for handle in handles:
                unit = units[handle]
                if unit['kind'] not in ('carried_reading', 'target_page', 'fixed_field'):
                    dependencies[digest(unit['identity'])] = dict(identity=unit['identity'], sha256=unit['sha256'])
        deliveries = []
        for span in projected['deliveries']:
            body = materials[span['unit']]
            for handle in body['units']:
                unit = units[handle]
                if unit['kind'] == 'carried_reading' or unit['bodies'] != [body['handle']]:
                    continue
                text = artifacts.read(body['artifact']).decode('utf-8')[span['start']:span['end']]
                # V7 names a range's part by start + 1; the whole character
                # count bounds those ids in the legacy observer vocabulary.
                deliveries.append(dict(identity=unit['identity'], sha256=unit['sha256'], part=span['part'],
                    parts=unit['characters'],
                    start=span['start'], end=span['end'], span_sha256=hashlib.sha256(text.encode('utf-8')).hexdigest(), text=text))
        entries = []
        for entry in projected['entries']:
            if 'handle' in entry or entry['status'] != 'verified':
                continue
            source = materials[entry['unit']]
            for handle in source['units']:
                unit = units[handle]
                if unit['bodies'] == [source['handle']] and unit['kind'] != 'carried_reading':
                    entries.append(dict(identity=unit['identity'], source_sha256=unit['sha256'],
                        **{key: entry[key] for key in ('start', 'end', 'sha256', 'text', 'purpose', 'status')}))
        for sha256 in searches:
            unit = next(row for row in inventory['units'] if row['sha256'] == sha256
                        and row['identity'].get('record_kind') == 'literature_search')
            text = artifacts.read(unit['artifact']).decode('utf-8')
            deliveries.append(dict(identity=unit['identity'], sha256=sha256, part=1, parts=1,
                start=0, end=len(text), span_sha256=sha256, text=text))
        origin = dict(chain=member['reading_chain']['first_assignment_id'], protocol=inventory['protocol'],
                      assignment_id=member['id'], attempt_id=projected['turn']['attempt_id'])
        result.append(dict(origin=origin, turn=dict(projected['turn'], number=member['reading_chain']['turn']),
            deliveries=deliveries, notes=projected['notes']['text'], excerpts=entries, passages=[],
            searches=searches, dependencies=list(dependencies.values())))
    return result


def project_native_delivery(records, artifacts, assignment, native, required):
    """Supply scientific validators a transient projection of authenticated complete v7 delivery.

    The compact final binds the full native packet; source bodies are authenticated by
    the chain rather than repeated in its last request. Uncovered bytes gain no credit.
    """
    from .problem_openness_reviews import require, UNDELIVERED
    from .problem_openness_sources import apply_delivery
    verified = verify_problem_coverage(records, artifacts, assignment, final=True)
    complete = {row['artifact']['sha256'] for row in verified['units']}
    require(native == verified['native'] and set(required) <= complete, COVERAGE_VERIFICATION_MESSAGE, UNDELIVERED,
            digests=sorted(set(required) - complete))
    evidence = []
    for row in native['evidence']:
        reference = row['artifact']
        if row['digest'] in complete:
            evidence.append(dict(artifact=reference, content=artifacts.read(reference).decode('utf-8'), encoding='utf-8'))
        else:
            evidence.append(dict(artifact=reference, content='', encoding='omitted'))
    supplied = dict(native=native, evidence=evidence)
    apply_delivery(supplied)
    return supplied


def matches_turn_prompt(assignment, packet=None):
    """Match the kind's template and applicable paragraphs under their recorded version ids."""
    from . import review_protocol as protocol, review_prompt_paragraphs as paragraphs
    envelope = copy.deepcopy(assignment['prompt'])
    envelope['parameters'].pop('capacity', None)
    base = paragraphs.strip_prompt_paragraphs(envelope, assignment['role'])
    if base is None:
        return False
    kind = base['parameters'].get('reading_stage')
    if packet is not None:
        binding = packet.get('reading_turn', packet.get('reading_final', packet.get('reading_combine', {})))
        if (kind != reading.find_stage(packet)
                or assignment['prompt']['parameters'].get('capacity') != binding.get('capacity')
                or not paragraphs.matches_packet_paragraphs(envelope, assignment['role'],
                    dict(packet, subject=dict(kind='problem')))):
            return False
    return kind in ('reading', 'final', 'page', 'closing', 'combine') and base == protocol.canonical_prompt('native_math',
        reading_stage=kind, reading_protocol=reading.find_protocol(packet) if packet is not None else base['template_id'].rsplit('/', 1)[0],
        common_version=protocol.find_common_prompt_version(envelope),
        finding_version=base['parameters'].get('finding_version', 1))



def _list_problem_evidence(records, native):
    from .problem_openness_sources import required_proof
    from .problem_openness_screening import required_evidence
    required = required_proof(native) | required_evidence(native['scientific_context'])
    return [row for row in native.get('evidence', []) if row['digest'] in required]


def _list_math_evidence(records, native):
    """The evidence rows of a math packet that are units beside its reading rows: the proof artifacts, the
    supplementary inputs and every other record that the controller binds. An original that a bound reading pins is
    delivered by its extracted text, a reading row, and is no unit."""
    bound = set(native['scientific_context']['openness_minimum']['reading_digests'])
    bundles = records.get('source_bundle', {})
    originals = {bundles[row['bundle_id']]['original_sha256'] for row in records.get('reading', {}).values()
                 if digest(row) in bound and row.get('bundle_id') in bundles}
    return [row for row in native['evidence'] if row['artifact']['sha256'] not in originals]


def _list_problem_context_units(context):
    return [('openness_minimum', context['openness_minimum']), ('minimum_gaps', context.get('minimum_gaps', [])),
            ('audit_notices', context.get('audit_forecast', {}).get('notices', []))] + [
            (field, context[field]) for field in ('native_reviews', 'openness_delivery', 'source_evidence')
            if field in context]


def _list_math_context_units(context):
    return [('openness_minimum', context['openness_minimum'])]


def _describe_problem_minimum(native):
    minimum = native['scientific_context']['openness_minimum']
    forecast = native['scientific_context']['audit_forecast']
    return dict(digest=digest(minimum), searches=len(minimum['searches']),
                readings=len(minimum.get('reading_digests', [])), results=len(minimum.get('results', [])),
                gaps=len(native['scientific_context'].get('minimum_gaps', [])),
                outcomes=copy.deepcopy(forecast['outcomes']), searched_through=forecast.get('searched_through'))


def _describe_math_minimum(native):
    minimum = native['scientific_context']['openness_minimum']
    return dict(digest=digest(minimum), schema_version=minimum['schema_version'], searches=len(minimum['searches']),
                readings=len(minimum['reading_digests']))


def _describe_problem_fields(native):
    return {key: copy.deepcopy(value) for key, value in native.items() if key not in
            ('subject', 'scientific_context', 'evidence', 'execution_provenance', 'unmaterialized_digests')}


def _describe_math_fields(native):
    """The native fields of a math final with the scientific context, whose minimum is its own unit."""
    fields = _describe_problem_fields(native)
    fields['scientific_context'] = copy.deepcopy(native['scientific_context'])
    minimum = native['scientific_context']['openness_minimum']
    fields['scientific_context']['openness_minimum'] = dict(schema_version=minimum['schema_version'], statement='t1',
        neighbours=dict(count=len(minimum['neighbours'])), searches=dict(count=len(minimum['searches'])),
        reading_digests=dict(count=len(minimum['reading_digests'])))
    return fields


def _render_problem_context(route, native, inventory):
    """The compact scientific context of a problem final and the unit that holds its minimum gaps."""
    context = native['scientific_context']
    forecast = context['audit_forecast']
    rendered = dict(audit_forecast=dict(outcomes=copy.deepcopy(forecast['outcomes']),
        searched_through=forecast.get('searched_through'), gaps=len(context.get('minimum_gaps', [])),
        notices=len(forecast.get('notices', []))),
        openness_minimum=dict(schema_version=2, statement='t1',
            neighbours=dict(count=len(context['openness_minimum']['neighbours']))),
        decisive_work_ids=(copy.deepcopy(context.get('decisive_work_ids', [])) if capacity.measure_capacity_value(
            route, context.get('decisive_work_ids', [])) <= inventory['fixed_bounds']['decisive_work_ids']
            else dict(count=len(context.get('decisive_work_ids', [])), unit=next(
                row['bodies'][0] for row in inventory['units'] if row['identity'].get('field') == 'decisive_work_ids'))))
    gaps_unit = next(row['bodies'][0] for row in inventory['units']
                     if row['kind'] == 'record' and row['label'] == 'minimum_gaps')
    return dict(frame=dict(scientific_context=rendered), minimum=dict(gaps_unit=gaps_unit))


def _render_math_context(route, native, inventory):
    """A math final renders its scientific context among its native fields, with no forecast of a problem packet."""
    return dict(frame={}, minimum={})


def _validate_problem_final(packet, output, *, artifacts=None, assignment=None):
    from .native_review import validate_finding, find_finding_version
    validate_finding(packet, output, finding_version=find_finding_version(assignment), artifacts=artifacts)


def _validate_math_final(packet, output, *, artifacts=None, assignment=None):
    from .native_review import validate_output
    validate_output(packet['native'], output)


def describe_protocol(*, native=None, packet=None):
    """Return the description of a chain: of its packet's protocol, or of a new chain over a native packet."""
    if packet is not None:
        return PROTOCOL_DESCRIPTIONS[reading.find_protocol(packet)]
    # Every math-solver packet carries its kind and its contract.
    math = native.get('kind') == 'result' and 'contract' in native and reading.is_math_openness_packet(native)
    return PROTOCOL_DESCRIPTIONS[reading.MATH_PROTOCOL_V2 if math else PROTOCOL]


# One description for each bounded reading protocol: what differs between a chain of a problem openness review and a
# chain of a math-solver result review with an openness minimum. Every other part of this module is shared. The stage
# templates of each protocol are review_protocol.BOUNDED_STAGE_PROMPTS[id], because that module imports this one.
# final_rule names the contract of the final: 'finding' is the problem finding with its finding index; 'native_review'
# is the native result review, whose openness.evidence cites delivered units by handle and whose minimum the
# harness supplies.
PROTOCOL_DESCRIPTIONS = MappingProxyType({
    PROTOCOL: MappingProxyType(dict(
        id=PROTOCOL, carried_identity={}, list_evidence=_list_problem_evidence,
        list_context_units=_list_problem_context_units, describe_minimum=_describe_problem_minimum,
        describe_fields=_describe_problem_fields, render_final_context=_render_problem_context,
        final_minimum_bounds=dict(gaps_unit='u999999999'), final_rule='finding',
        list_final_forms=lambda: V7_FINAL_FORMS, validate_final=_validate_problem_final)),
    reading.MATH_PROTOCOL_V2: MappingProxyType(dict(
        id=reading.MATH_PROTOCOL_V2, carried_identity=dict(packet_kind='math'), list_evidence=_list_math_evidence,
        list_context_units=_list_math_context_units, describe_minimum=_describe_math_minimum,
        describe_fields=_describe_math_fields, render_final_context=_render_math_context,
        final_minimum_bounds={}, final_rule='native_review',
        list_final_forms=lambda: MATH_V2_FINAL_FORMS, validate_final=_validate_math_final)),
})
