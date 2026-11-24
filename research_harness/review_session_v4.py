"""Pure inventory, authenticated-turn projection and bounded state for sessions v4.

Builders publish the returned canonical artifacts at assignment admission. A
request carries the latest notes, allowance rows and pending requests; the full
inventory and history stay behind their digests. The command edge authenticates
observations and validates the request response before calling these functions.
Delivery text is transient observation data, never repeated in state receipts.
"""

import copy
import hashlib
import json

from . import bar_sources, review_excerpts, review_route_capacity as capacity
from .artifacts import describe_artifact
from .evidence import digest
from .problem_openness import serialize_record


PROTOCOL = 'exactory-independent-review-sources-v4'
ASSIGNMENT_ID_CHARACTERS = 120
LABEL_CHARACTERS = 120
# The codes with which the capacity plan pinned to a chain refuses to build its next or its replacement turn.
PINNED_CAPACITY_CODES = ('review_route_capacity_below_floor', 'review_route_capacity_plan_violated',
                         'review_input_too_large')
PURPOSE_CHARACTERS = 200
ALLOWANCES_CHARACTERS = 30000
RESPONSE_REQUESTS = 10
PENDING_REQUESTS = 20
REQUEST_ROW_CHARACTERS = 2000
PENDING_REQUESTS_CHARACTERS = PENDING_REQUESTS * (REQUEST_ROW_CHARACTERS + 2) + 2
STATE_FIELDS_CHARACTERS = 512


class _InventoryArtifacts:
    """An invocation-local overlay lets the released source inventory stay pure."""

    def __init__(self, source):
        self.source = source
        self.root = getattr(source, 'root', None)
        self.bodies = {}

    def read(self, reference):
        content = self.bodies.get(reference['sha256'])
        return self.source.read(reference) if content is None else content

    def put(self, content, media_type):
        reference = describe_artifact(content, media_type)
        self.bodies[reference['sha256']] = content
        return reference

    def require_local_read(self, purpose):
        from .evaluation import Evaluation
        source = self.source.store if isinstance(self.source, Evaluation) else self.source
        source.require_local_read(purpose)

    def read_runtime_fingerprint(self, route):
        from .evaluation import Evaluation
        source = self.source.store if isinstance(self.source, Evaluation) else self.source
        return source.read_runtime_fingerprint(route)


def _describe_json(value):
    return describe_artifact(serialize_record(value), 'application/json')


def build_session_inventory(records, artifacts, packet, frame_units=()):
    """Return {inventory, artifact, bodies}, reading frozen inputs without writes.

    Each source has a metadata unit and one unit per captured abstract/full text.
    Packet-record artifacts follow in digest order, then caller-ordered frame
    units ({kind, identity, artifact, label}). Bodies includes generated metadata
    bytes so the caller can publish every reference along with the inventory.
    Text-only admission belongs to the caller; binary bodies cannot enter this
    text inventory. Equal bodies retain their distinct source identities.
    """
    overlay = _InventoryArtifacts(artifacts)
    source_inventory = bar_sources.inventory(records, overlay)
    reference = source_inventory['artifact_descriptor']
    sources = json.loads(overlay.read(dict(path=reference['location'], **{
        key: reference[key] for key in ('sha256', 'size', 'media_type')})))
    inventory = dict(protocol=PROTOCOL, source_inventory_digest=source_inventory['digest'], units=[])
    bodies = {}

    def append_unit(kind, identity, reference, label):
        content = overlay.read(reference)
        inventory['units'].append(dict(handle='u%d' % (len(inventory['units']) + 1), kind=kind,
            identity=copy.deepcopy(identity), artifact=copy.deepcopy(reference), sha256=reference['sha256'],
            characters=len(content.decode('utf-8')), label=label[:LABEL_CHARACTERS]))
        bodies[reference['sha256']] = content

    for source in sources:
        version = source['id']
        label = source.get('title') or version
        reference = overlay.put(serialize_record(source), 'application/json')
        append_unit('source', dict(version_id=version), reference, label)
        work = records['work'][version]
        for kind, captures, field in (('abstract', work.get('abstracts', []), 'artifact'),
                                      ('fulltext', work.get('fulltexts', []), 'text')):
            for capture in sorted((row for row in captures if row.get(field)),
                                  key=lambda row: (row['source_id'], row[field]['sha256'])):
                reference = capture[field]
                append_unit(kind, dict(version_id=version, source_id=capture['source_id'],
                    text_sha256=reference['sha256']), reference, label)
    for descriptor in bar_sources.collect_record_artifacts(packet):
        reference = dict(path=descriptor['location'], **{
            key: descriptor[key] for key in ('sha256', 'size', 'media_type')})
        append_unit('artifact', dict(sha256=reference['sha256']), reference, reference['sha256'])
    if packet.get('context_repair'):
        from .review_protocol import collect_references
        # Historical frames, observations and their bodies are independent
        # material units. The frame carries only their classified descriptors.
        references = {row['sha256']: row for row in collect_references(packet.get('scientific_history', []))}
        for identifier in sorted(references):
            reference = references[identifier]
            append_unit('history', dict(sha256=identifier), reference, identifier)
    for row in frame_units:
        append_unit(row['kind'], row['identity'], row['artifact'], row.get('label', row['kind']))
    return dict(inventory=inventory, artifact=_describe_json(inventory), bodies=bodies)


def project_session_turn(inventory, observation):
    """Project one authenticated observation, without storage or earlier state.

    Observation: {number, assignment_id, attempt_id, deliveries, output,
    list_pages?}. A delivered list page records {list, after, next}; rows remain
    material of that request and are not copied into carried state.
    Deliveries: {unit, part, start, end, text, request_kind?, request_id?,
    request_complete?}; offsets address the inventory unit, and text is the
    authenticated text of this request's part.
    Output: {schema_version:1, stage:'request', notes, excerpts, requests,
    settled_requests}. The edge validates its shape, handles and string bounds.
    An excerpt must occur in a current delivery; unmatched copies stay diagnostic.
    Turn-local e<number>_<index> handles are stable across state reconstruction.
    """
    number = observation['number']
    output = observation['output']
    units = {row['handle']: row for row in inventory['units']}
    deliveries = []
    for row in observation['deliveries']:
        unit = units.get(row['unit'], dict(handle=row['unit']))
        deliveries.append(dict(unit=unit['handle'], part=row['part'], start=row['start'], end=row['end'],
            sha256=hashlib.sha256(row['text'].encode('utf-8')).hexdigest(),
            **{key: row[key] for key in ('request_id', 'request_complete') if key in row}))
    excerpts = []
    for index, value in enumerate(output['excerpts'], 1):
        unit = units[value['unit']]['handle']
        entry = dict(handle='e%d_%d' % (number, index), unit=unit, start=None, end=None,
            sha256=hashlib.sha256(value['text'].encode('utf-8')).hexdigest(), text=value['text'],
            purpose=value['purpose'], targets=copy.deepcopy(value['targets']), status='unmatched')
        for row in observation['deliveries']:
            offset = row['text'].find(value['text']) if row['unit'] == unit else -1
            if offset >= 0:
                entry.update(start=row['start'] + offset, end=row['start'] + offset + len(value['text']),
                             status='verified')
                break
        excerpts.append(entry)
    return dict(turn=dict(number=number, assignment_id=observation['assignment_id'],
                         attempt_id=observation['attempt_id'], kind=output['stage']),
        deliveries=deliveries, notes=dict(turn=number, text=output['notes']), excerpts=excerpts,
        requests=[dict(copy.deepcopy(row), turn=number,
                       status='pending' if index < RESPONSE_REQUESTS else 'over_allowance')
                  for index, row in enumerate(output['requests'])],
        settled_requests=copy.deepcopy(output['settled_requests']),
        completed_requests=list(dict.fromkeys([row['request_id'] for row in deliveries
                                              if row.get('request_complete')] + observation.get('completed_list_requests', []))),
        list_pages=copy.deepcopy(observation.get('list_pages', [])),
        passage_characters=sum(row['end'] - row['start'] for row in observation['deliveries']
                               if row.get('request_kind') == 'passage'))


def build_session_state(inventory, turns, route, plan, *, initial_state=None):
    """Return {state, artifact} from ordered authenticated observations and a plan.

    Accepted excerpts are charged once, at their actual serialized request cost,
    to every named target. If any target lacks room, the entire entry stays
    over_allowance and consumes no target. Settlements and authenticated complete
    deliveries free pending slots before new requests. Each pending row fits
    REQUEST_ROW_CHARACTERS in the native request; larger rows remain diagnostic
    with over_allowance, as do requests above either count limit.
    No allowance or profile is recomputed from live records.
    """
    state = dict(protocol=PROTOCOL, inventory_digest=digest(inventory), turns=[], deliveries=[],
        notes=[], latest_notes=None, excerpts=[], requests=[], pending_requests=[], settled_requests=[],
        delivered_sources=0, allowances=dict(notes=plan['allocation']['notes'],
            targets=[dict(handle=row['handle'], characters=row['excerpts'], used=0)
                     for row in plan['allocation']['targets']],
            passages=dict(characters=plan['allocation'].get('session_passage_characters', review_excerpts.SESSION_PASSAGE_CHARACTERS), used=0)))
    if initial_state is not None:
        state = copy.deepcopy(initial_state)
    allowances = {row['handle']: row for row in state['allowances']['targets']}
    units = {row['handle']: row for row in inventory['units']}
    delivered_sources = {units[row['unit']]['identity']['version_id'] for row in state['deliveries']
                         if row['unit'] in units and units[row['unit']]['kind'] in ('abstract', 'fulltext')}
    pending = {row['id']: row for row in state['requests'] if row['id'] in
               {pending['id'] for pending in state['pending_requests']}}
    for observation in turns:
        projected = project_session_turn(inventory, observation)
        number = projected['turn']['number']
        state['turns'].append(projected['turn'])
        state['deliveries'].extend(dict(row, turn=number) for row in projected['deliveries'])
        for row in projected['deliveries']:
            unit = units.get(row['unit'])
            if unit is not None and unit['kind'] in ('abstract', 'fulltext'):
                delivered_sources.add(unit['identity']['version_id'])
        if not observation.get('delivery_only'):
            state['notes'].append(projected['notes'])
            state['latest_notes'] = projected['notes']['text']
        turn_used = 0
        for entry in projected['excerpts']:
            if entry['status'] == 'verified':
                charge = capacity.measure_capacity_value(route, entry)
                if plan['allocation'].get('session_source_rows'):
                    unit = units[entry['unit']]
                    charge += capacity.measure_capacity_value(route, dict(handle=unit['handle'], kind=unit['kind'], label=unit['label']))
                targets = [allowances[handle] for handle in entry['targets']]
                if (turn_used + charge > observation.get('excerpt_allowance', float('inf'))
                        or any(row['used'] + charge > row['characters'] for row in targets)):
                    entry['status'] = 'over_allowance'
                else:
                    turn_used += charge
                    for row in targets:
                        row['used'] += charge
            state['excerpts'].append(entry)
        for identifier in projected['settled_requests']:
            request = pending.pop(identifier)
            request['status'] = 'settled'
            state['settled_requests'].append(dict(request, settled_turn=number))
        for identifier in projected['completed_requests']:
            request = pending.pop(identifier, None)
            if request is not None:
                request['status'] = 'delivered'
        for request in projected['requests']:
            if plan['allocation'].get('session_source_rows') and any(
                    row['id'] == request['id'] and row['status'] != 'over_allowance' for row in state['requests']):
                request['status'] = 'repeated'
            if request['status'] == 'pending':
                if (len(pending) >= PENDING_REQUESTS or capacity.measure_capacity_value(
                        route, _describe_pending_request(request)) > REQUEST_ROW_CHARACTERS):
                    request['status'] = 'over_allowance'
                else:
                    pending[request['id']] = request
            state['requests'].append(request)
        state['allowances']['passages']['used'] += projected['passage_characters']
        for page in projected['list_pages']:
            state.setdefault('list_pages', {})[page['list']] = dict(next=page['next'], ended=page['next'] is None)
    state['delivered_sources'] = len(delivered_sources)
    state['pending_requests'] = list(pending.values())
    return dict(state=state, artifact=_describe_json(state))


def describe_session_plan(route, frames, units, targets, *, final_pages=False):
    """Declare request/final sizing for prepare_assignment_capacity, without writes.

    Frames are measured role fields; units are the immutable inventory rows and
    targets are the composition rule's handles (or handle/source_rows/gap_rows).
    The capacity manifest binds their sizing fields. The assignment builder also
    binds the complete inventory artifact by inventory_digest, as for v7. The
    target allowance object and pending list have separate serialized reserves.
    """
    # Pending rows are mandatory carried state, so a lowered frame cannot move
    # their reserve into unit_fields. Only the target object has the 30,000 share.
    return dict(kind='request', frames={kind: copy.deepcopy(frames[kind]) for kind in ('request', 'final')},
        inventory=[{key: row[key] for key in ('handle', 'kind', 'characters', 'sha256') if key in row}
                   for row in units], targets=copy.deepcopy(targets), allowance_rows_bound=ALLOWANCES_CHARACTERS,
        pending_requests_bound=PENDING_REQUESTS_CHARACTERS + STATE_FIELDS_CHARACTERS, **(dict(final_pages=True) if final_pages else {}))


def _describe_pending_request(request):
    return {key: copy.deepcopy(value) for key, value in request.items() if key not in ('turn', 'status')}


def describe_session_allowances(route, state, plan, limits):
    """Describe only bounded carried state, never turn/source/delivery histories.

    The target allowance object has a 30,000 serialized-character share, using
    F12.1's count/unused/most-used summary above it. Latest notes have the stored
    plan bound; this answer's notes cap also fits its answer share. Pending rows
    keep their request fields, without historical turns.
    limits is the current request task derived from the stored plan and attempts.
    """
    answer = capacity.divide_room(max(18000, limits['answer_bound']), [
        dict(id='notes', minimum=6000, preferred=20000, maximum=20000),
        dict(id='excerpts', minimum=8000, preferred=40000, maximum=40000),
        dict(id='requests', minimum=2000, preferred=5000, maximum=5000),
        dict(id='fields', minimum=2000, preferred=2000, maximum=2000)])
    parts = {row['id']: row['allowance'] for row in answer['parts']}
    rows = [dict(row, remaining=max(0, row['characters'] - row['used']))
            for row in state['allowances']['targets']]
    result = dict(notes=min(plan['allocation']['notes'], parts['notes']), excerpts=parts['excerpts'], targets=rows,
                  passages=copy.deepcopy(state['allowances']['passages']))
    if capacity.measure_capacity_value(route, result) > ALLOWANCES_CHARACTERS:
        result.update(targets=[], target_count=len(rows), unused_target_allowance=min(
            (row['characters'] for row in rows if row['used'] == 0), default=0), omitted=len(rows))
        for row in sorted((row for row in rows if row['used']), key=lambda row: (-row['used'], row['handle'])):
            candidate = dict(result, targets=result['targets'] + [row], omitted=result['omitted'] - 1)
            if capacity.measure_capacity_value(route, candidate) > ALLOWANCES_CHARACTERS:
                break
            result = candidate
    result.update(latest_notes=state['latest_notes'], delivered_sources=state['delivered_sources'],
                  pending_requests=[_describe_pending_request(row) for row in state['pending_requests']])
    return result


def describe_session_final_entries(state, targets):
    """Return each accepted excerpt naming any requested target once, in state order."""
    selected = set(targets)
    return [copy.deepcopy(row) for row in state['excerpts']
            if row['status'] == 'verified' and selected.intersection(row['targets'])]


def build_session_allowance_page(route, state, plan, after, material_allowance):
    """Return the fixed-order allowance list through the v7 cursor/size contract.

    The usage-sorted summary never changes this list's order. The turn observer
    records the delivered page's {list, after, next} in observation.list_pages;
    rebuilding state pins the next cursor without carrying earlier page rows.
    """
    from .openness_reading_v7 import build_list_page
    return build_list_page('allowances', after, plan, state, route, material_allowance)


# The turn texts of the session rules v4 (rules V1 to V5 of the design session-v4-role-prompt). A turn prompt is the
# base prompt of the role, then the text of its kind of turn, then the common texts that build_turn_prompt adds. The
# base states what the role is and the response shape of the final answer; these texts state how a turn of a
# session works. Each text names the place where its reader finds a fact. The turn text of a kind stays under half
# of FRAME_ENVELOPE_CHARACTERS, which the capacity plan reserves for it beside the binding of the turn.
SESSION_OPENING_PROMPT = (
    ' review runs as a session under the source rules ' + PROTOCOL + ': a sequence of turns. Each turn is a new '
    'input, and of the earlier turns it holds only what its packet shows.')
# The request form: the answer of a request turn, and an answer that a page turn and the closing turn accept.
SESSION_REQUEST_FORM_PROMPT = (
    '\n\nThe request form is exactly {schema_version:1,stage:"request",notes,excerpts,requests,settled_requests}. '
    'allowances below is session_turn.allowances, or session_final.request_allowances in a page turn and the '
    'closing turn. notes is a nonblank string of at most allowances.notes characters as the next request carries '
    'them (a quotation mark, a backslash or a line break costs more). It replaces your earlier notes: keep in it '
    'all that still matters, with the handles and ids that you can need again.')
SESSION_REQUEST_EXCERPTS_PROMPT = (
    ' excerpts is a list, which can be empty, of {unit,text,purpose,targets}: a quotation that the session keeps '
    'for the final answer. unit is the u handle of a row of plan.now of this turn, text an exact copy of 1 to '
    '6,000 consecutive characters of the content of that row, purpose at most 200 characters, and targets a '
    'nonempty list of distinct target handles that the quotation bears on. An excerpt is charged, with a row for '
    'its source, to allowances.excerpts and to the allowance of each named target; one that these cannot hold is '
    'recorded as over_allowance and does not reach the final answer.')
SESSION_REQUEST_LIST_PROMPT = (
    ' requests is a list, which can be empty, of requests with an id of 1 to 120 characters. A used id is refused, '
    'or ignored where the row repeats the earlier request, and a row above 2,000 characters as the next request '
    'carries it is refused. {id,kind:"inventory_page",page} asks for a page of the list of the units, counted from '
    '0; its row holds page: {page,rows,total} with up to ten rows {handle,kind,label,characters}. '
    '{id,kind:"unit",unit} asks for a unit by its handle, {id,kind:"source",version_id,depth} for a source of that '
    'list at the depth "abstract" or "fulltext", and {id,kind:"artifact",sha256} for an artifact of a record or a '
    'field of the frame by its sha256. {id,kind:"passage",unit,anchor,characters} asks for text of a unit again, '
    'from the first occurrence of anchor, a phrase of 1 to 600 characters of the unit, or from its start when '
    'anchor is null; characters is an integer from 1 to 100,000, and the passage requests of one answer take at '
    'most allowances.passages.available together. {id,kind:"list_page",list:"allowances",after} asks for a page of '
    'the allowance rows, with after null for the first page and then the next of the page before; a null next '
    'ends the list, and a list takes one open request. The session keeps the first ten requests of an answer and '
    'at most twenty open requests. A later turn delivers what a request asks for, in parts where it is long, '
    'until a row with request_complete true ends the request. A row '
    '{kind:"request_status",request_id,status,request_complete} ends it without text: status is unavailable where '
    'the session has nothing to deliver, and already_delivered where it delivered all of it before. '
    'settled_requests lists, each once, the ids of open requests that you no longer need.')
SESSION_REQUEST_TURN_PROMPT = (
    ' This turn is a request turn (a source-request turn, where the text above uses that name): you read what it '
    'delivers, keep notes and excerpts, and ask for what you still need. Its answer is the request form below, and '
    'nothing else. An assessment is refused in this turn: it is the answer of the final. The final follows the '
    'first request turn after which every required unit is delivered in full and no request of yours is open. A '
    'request turn inside a final in pages delivers what a turn of the final had no room for, and that turn then '
    'comes again. The final is written from your latest notes and the excerpts that the session kept, and it '
    'takes no new excerpt, so '
    'everything that the assessment needs must pass through your notes and excerpts; an argument of your own is '
    'written in the final, so put its outline into your notes.'
    '\n\nsession_turn holds the turn. frame holds the fields of the packet that the text above describes. A field '
    'that is too long for the frame is a unit, and the frame shows {handle,sha256,characters} in its place; a list '
    'shows {entries,characters}, and each of its entries is a unit. These units, and each artifact that a record or '
    'the history of the packet references, are the required units: the first turns deliver them without a '
    'request, and a later turn shows them only as these counts and handles. An inventory_page request delivers '
    'the list of the units, with the record, the abstract and the full text of each source. targets lists the '
    'targets of the review, the items that the final answer assesses one '
    'by one, as {handle,label}, with id where the label is not the id; when the rows do not fit, it is '
    '{count,first,last}, and every handle from first to last is a target. notes is the text of your latest notes, '
    'and null in the first turn.'
    '\n\nplan.now lists what this turn delivers, one row for each item, and evidence, beside session_turn in the '
    'packet, holds the content of each row at the same position. A row {unit,kind,part,start,end,characters,sha256} '
    'delivers the characters from start to end of that unit; a unit that is longer than the room of a turn arrives '
    'in parts over several turns. For a unit of kind frame the row names in field the JSON pointer of its field '
    'in frame, and for a request it names the request in request_id and request_kind. A row of kind list_page '
    'holds in page the page that a request asked for; a label there that is cut to fit ends with its version id '
    'in brackets. request_complete is true in the row that answers a request '
    'in full. plan.delivered, plan.pending and allowances state the session as it was before the rows of '
    'plan.now: plan.delivered counts the sources and the characters delivered, and plan.pending the open '
    'requests and the required units and characters still to come. Read everything that this turn delivers.'
    '\n\nallowances states what the session can keep. notes bounds the notes of this answer, and excerpts is the '
    'room of this answer for excerpts. targets gives {handle,characters,used,remaining} for each target, the room '
    'of the session for the excerpts of that target; when the targets are too many for one view, it shows the most '
    'used rows, omitted counts the rows that it leaves out, unused_target_allowance is the smallest allowance of a '
    'target without use, and the list allowances holds every row. passages is {characters,used,available}, the '
    'room of the session for passage requests, of which available is left for new ones. delivered_sources counts '
    'the sources of which a turn delivered text. pending_requests lists the open requests.'
    + SESSION_REQUEST_FORM_PROMPT + SESSION_REQUEST_EXCERPTS_PROMPT + SESSION_REQUEST_LIST_PROMPT)
SESSION_FINAL_TURN_PROMPT = (
    ' This turn is the final turn. Its answer is the final answer of the review, complete and once, in the response '
    'shape that the text above states. In place of it the turn accepts one part of an argument (Arguments, below). '
    'It accepts no request, whatever the text above says of requests: an answer that holds schema_version, notes, '
    'excerpts, requests, settled_requests or source_requests is refused.'
    '\n\nsession_final holds what the session kept. notes is the text of your latest notes, and fill holds notes of '
    'earlier turns as {turn,text}, the newest first, as far as the room of this turn allows. entries lists the '
    'excerpts that the session kept, as {handle,unit,start,end,sha256,text,purpose,targets,status}: text is an '
    'exact span of the unit, and start and end are its character offsets there. sources gives {handle,kind,label} '
    'for each unit that an entry quotes. targets lists the targets of the review as {handle,label}, with id where '
    'the label is not the id, or as {count,first,last} when the rows do not fit. frame holds the fields of the '
    'packet that the text above describes; where it shows {handle,sha256,characters} or {entries,characters}, the '
    'request turns delivered that field or the entries of that list as units. This turn delivers no source text. '
    'Judge from your notes and the entries, and do not assume that a text which this input does not hold is '
    'available.')
BAR_REQUEST_PROMPT = 'This bar' + SESSION_OPENING_PROMPT + SESSION_REQUEST_TURN_PROMPT
SLATE_REQUEST_PROMPT = 'This slate' + SESSION_OPENING_PROMPT + SESSION_REQUEST_TURN_PROMPT
RESULT_REQUEST_PROMPT = 'This result' + SESSION_OPENING_PROMPT + SESSION_REQUEST_TURN_PROMPT
BAR_FINAL_PROMPT = 'This bar' + SESSION_OPENING_PROMPT + SESSION_FINAL_TURN_PROMPT
SLATE_FINAL_PROMPT = 'This slate' + SESSION_OPENING_PROMPT + SESSION_FINAL_TURN_PROMPT
RESULT_FINAL_PROMPT = 'This result' + SESSION_OPENING_PROMPT + SESSION_FINAL_TURN_PROMPT
# The message of review_output_over_cap for a turn of the final of a session: the recording, the state of the
# assignment and a continuation give it.
FINAL_CAPACITY_MESSAGE = (
    'The answer of this turn of the final used the whole output limit of its route. An answer at that limit can be '
    'cut, so the harness records no review from it and assigns no turn after it. Run review-series on the '
    'assignment of this turn and take the step that it plans: a smaller turn in place of this one, the same turn '
    'once more, or the decision that the plan states.')
# The reviewer reads this message in the notice of a replacement turn.
TURN_CONTRACT_MESSAGE = (
    'The answer breaks a rule of the answer form of this turn of the session. path names the member of the '
    'answer that fails, and it is empty for the answer as a whole. details holds the values that the rule '
    'compared: for example characters with minimum_characters or maximum_characters, notes_cost with bound, '
    'requested with remaining, expected_type with received_type, missing_fields and unexpected_fields, '
    'pending_requests for the requests that are still open, or predicate for the property that a text lacks. '
    'The instructions of this turn state the form. Correct that member and return the complete answer of the '
    'turn.')
TURN_ID_LIMIT_MESSAGE = (
    'The id of an assignment of a session has at most 120 characters: details.characters gives the length of '
    'this id and details.maximum_characters the limit. Give a shorter id. For a turn that follows another turn, '
    'run review-series on that turn and take the payload that it plans, whose id is within the limit.')
REPLACEMENT_BINDING_MESSAGE = (
    'A capacity replacement of a session turn carries the role, the route, the dossier, the reviewer and the '
    'author of the turn that it replaces, and this payload names another value for one of them. Run '
    'review-series on that turn and take the replacement payload that it plans without a change.')
REPLACEMENT_UNPLANNED_MESSAGE = (
    'The plan of this turn holds no capacity replacement for it. review-series plans one only after a capacity '
    'failure of the turn, when no call of the turn is open and a smaller turn exists; after a refused answer it '
    'plans the turn that carries the refusal notice instead. Run review-series on the turn and take the step '
    'that it plans.')
TURN_BINDING_MESSAGE = (
    'A turn of this session does not verify. The harness rebuilds each turn from what its chain stores (the '
    'inventory, the logical packet, the capacity plan, the state and, for a final in pages, the page plan) and '
    'from the authenticated answers of the earlier turns. The check fails for one of these causes: a stored '
    'record of the session lacks a member that the check reads; an earlier turn has no authenticated answer; '
    'the turn names another role, route, dossier, reviewer, author, inventory, logical packet or capacity plan '
    'than the first turn of its chain; the page plan was made for another logical packet or inventory, or is '
    'not the plan that the turn names; or the stored turn differs from the rebuilt one in its state, its number, '
    'its kind, its packet or its prompt. The harness takes nothing from a turn that does not verify. Check the '
    'record with record-check.')
TURN_LIMITS_PROMPT = (
    '\n\nLimits. capacity, under session_turn, session_final or session_combine, states the limits of this turn. '
    'output_tokens is the output limit of the route in tokens; reasoning and answer share it, and an answer cut at '
    'the limit is lost. Keep the answer within answer_bound characters: a part of an argument and a condensing '
    'answer are refused above it, and a complete final answer that is longer is recorded. material_allowance is '
    'the room of this turn for what it delivers or shows of the session, and the other members are bounds of the '
    'stored plan.'
    '\n\nHandles. A handle names one thing of the session, the same in every turn: u a unit (the record, the '
    'abstract or the full text of a source, an artifact that a record or the history references, or a field of '
    'the frame), t a target, e an excerpt of yours, m a completed argument of yours, and b a block of condensed '
    'entries.'
    '\n\nCitations. In a final, page or closing answer, an evidence entry is the citation of a handle: '
    '{"ref":"u3"} for a unit, {"ref":"u3","part":1} for a part by its number in plan.now, and for a full text '
    'also {"ref":"u3","start":0,"end":40}, the characters from start to end of the unit or of the named part, '
    'with 0 <= start < end <= its length. An excerpt, an argument and a block are cited as {"ref":"e3_2"} alone. '
    'The harness replaces each object with a member ref by the reference object that the text above describes '
    '(about 230 characters for an artifact descriptor, 450 or more for a source link), and a size stated as JSON '
    'text counts the answer without spaces after that. It refuses another member in a citation, a target handle, '
    'a handle that the session does not hold, and text that no turn delivered to you. Only a full text can be '
    'cited in part: by a delivered part, an excerpt or a span inside delivered characters. A whole unit, and a '
    'part or an excerpt of another unit, needs all of that unit delivered. Ask for missing text while a turn '
    'still takes requests.'
    '\n\nArguments. An argument of your own that is too long for a field is stored in parts. A '
    'final, page or closing turn accepts one part in place of its answer: exactly '
    '{stage:"material",title,part,text,more}. title is nonblank, has at most 120 characters and is the same in '
    'every part; part counts from 1; text is nonblank and has at most capacity.answer_bound characters; more is '
    'true while a part follows; an argument has at most eight parts. The same turn then comes again with '
    'material_request: {stage:"material",title,ref,part} while a part is due, and {stage:"final",ref} after the '
    'last part, where ref is the handle of the argument, m1 for the first.')
# The base prompt of the role states the response shape of the final answer; these turns answer in another form.
# The text of each kind of turn states that form, so these two sentences only place the response shape of the base.
SESSION_BASE_SHAPE_PROMPT = (
    '\n\nThe response shape that the text above states is the shape of the final answer of this review. It is not '
    'the answer of this turn.')
SESSION_PART_SHAPE_PROMPT = (
    '\n\nThe response shape that the text above states is that of the whole final answer; this turn returns only '
    'its own part of it.')
# The turn text of a refresh chain (rule F2 of the session refresh), whose logical packet holds problem_refresh.
# The turn prompt records it in its parameter REFRESH_PARAMETER.
SESSION_REFRESH_PROMPT = (
    '\n\nRefresh. This session reviews the record as it is now, in place of an earlier review of yours that no '
    'longer applies to this record. No turn of that review is part of this session. In a turn that shows frame, '
    'frame.problem_refresh names the assignment of that review, and a member of it whose name starts with '
    'previous_ gives the earlier value of the member without that prefix. The entries of '
    'frame.historical_reviews, which the request turns deliver as units, include the earlier reviews of this '
    'slot.')
REFRESH_PARAMETER = 'session_refresh'
# The accepted forms of an evidence entry of a study review (rule S1 of the study forms), for its final, page and
# closing turns under these rules and for the final of a study series of the parts protocol v3 (rule S3). The forms
# are stated in the words of review_refusal.EVIDENCE_FORMS_NOTICE, which a refusal under evidence.shape delivers.
STUDY_HANDLE_PROMPT = (
    '\n\nEvidence entries. Cite each evidence entry by its handle, in the citation form that this prompt states.')
STUDY_WRITTEN_OUT_PROMPT = (
    ' An entry that you write out instead is one reference object. Outside support it has one of three forms.')
STUDY_EVIDENCE_FORMS_PROMPT = (
    ' The outermost members of the entry decide its form. An artifact descriptor: the entry itself is '
    '{"path":"<path>","sha256":"<sha256>","size":0,"media_type":"<media type>"}, with exactly these four members '
    'copied from one descriptor of the packet. A descriptor under a member name, as in {"artifact":{...}}, is '
    'refused. A source link: {"kind":"source","link":{"version_id":"<version id>","source_id":"<source id>",'
    '"artifact":{...},"locator":{"kind":"<as delivered>"}}}, where artifact is an artifact descriptor. The members '
    'version_id, source_id, artifact and locator written as the outermost members of the entry are refused. A '
    'source link is accepted only when its artifact is the original or the extracted text of a full-text capture '
    'that this chain delivered to you. '
    'A record: {"kind":"record","record_kind":"<record kind>","id":"<record id>","digest":"<digest>"}, with the '
    'three values copied from the place where the packet names that record. An entry that is only a digest, only '
    'a path or free text is refused.')
STUDY_SUPPORT_FORMS_PROMPT = (
    ' In a result review, the evidence of a check under support.checks has two forms only: a source link, as '
    'above, and a result reference, exactly {kind:"result",execution_id,output_id,artifact,locator}. Write the '
    'references of the candidate out from their templates in support_contract.evidence, as the text above '
    'describes. A handle is accepted there only where it gives a source link, as the citation of a full text, of '
    'a part of one or of an excerpt from one does. An artifact descriptor, a record and every other handle are '
    'refused there.')
STUDY_FORMS_PROMPT = STUDY_HANDLE_PROMPT + STUDY_WRITTEN_OUT_PROMPT + STUDY_EVIDENCE_FORMS_PROMPT
# Each frame reserves this envelope for the turn text, binding, delivery descriptors and bounded source rows, and
# beside it the base prompt of the role as the turns render it (rule V3).
FRAME_ENVELOPE_CHARACTERS = 20000
# The ordinary envelope includes this much for the outer identity. Any excess
# also travels outside the frame and must be reserved at its serialized size.
OUTER_IDENTITY_CHARACTERS = 1024


def find_session_kind(packet):
    if packet.get('source_rules') == PROTOCOL:
        return 'request' if 'session_turn' in packet else 'combine' if 'session_combine' in packet else 'final' if 'session_final' in packet else None
    return None


def _require_turn(condition, *, path, **details):
    if not condition:
        from .errors import ResearchError
        from .review_refusal import contract_check
        with contract_check('review.session_turn'):
            raise ResearchError('invalid_review_session_turn', TURN_CONTRACT_MESSAGE, dict(path=path, **details))


def require_single_final_members(packet, output):
    if find_session_form(packet) != 'final' or isinstance(output, dict) and output.get('stage') == 'material':
        return
    from .operations import describe_json_type
    _require_turn(isinstance(output, dict), path='', expected_type='object', received_type=describe_json_type(output))
    unexpected = sorted(output.keys() & {'source_requests', 'settled_requests', 'excerpts', 'requests', 'notes',
                                         'schema_version'})
    _require_turn(not unexpected, path='', unexpected_fields=unexpected)


def find_base_orchestration(value):
    """The orchestration of the base prompt of a v4 turn: a problem review, or None for a study review (rule S1).

    An assignment and the logical packet of its chain carry the same subject, so the admission, each builder of a
    turn and the check of a recorded turn decide by this one rule."""
    return 'problem_creation' if value.get('subject', {}).get('kind') == 'problem' else None


def build_base_prompt(role, orchestration, *, common_version=None, repair_suffix=None):
    """Return (descriptor, base) for the review template of the role at the common version of a session turn (rule V1).

    The descriptor names the canonical base as a parts turn of the protocol v3 names it: the arguments of
    canonical_prompt, its template id and its hash. base is the canonical envelope of the common version 5, which
    holds no source rules v2: the turn text of v4 states how sources arrive. A chain takes no lower version, whose
    base holds the source rules v2."""
    from . import review_protocol as protocol, review_context_repair as repair
    from .errors import ResearchError
    if common_version not in (None, protocol.TURN_COMMON_VERSION):
        raise ResearchError('invalid_review_prompt_version', 'Unknown version of the common reviewer prompt text')
    arguments = dict(orchestration=orchestration, repair=repair_suffix is not None, excerpts=False,
                     common_version=protocol.TURN_COMMON_VERSION)
    if repair_suffix == repair.EXHAUSTION_PROMPT_V1:
        arguments['repair_prompt'] = repair_suffix
    canonical = protocol.canonical_prompt(role, **arguments)
    descriptor = dict(parameters=arguments, template_id=canonical['template_id'],
                      rendered_prompt_hash=canonical['rendered_prompt_hash'])
    return descriptor, canonical


def build_turn_prompt(role, kind, bounds, *, orchestration, paragraphs=None, common_version=None, repair_suffix=None,
                      refresh=False):
    """The base prompt of the role, its selected paragraphs, and then the turn text of v4
    (rules V1 and V2). paragraphs holds problem_paragraphs and problem_objection_carry as
    review_prompt_paragraphs.select_prompt_parameters gives them; a refusal turn adds its refusal paragraph.
    repair_suffix is the context-repair suffix of review_context_repair that the base of a repair chain holds.
    refresh adds the turn text of a refresh chain (rule F2)."""
    from . import review_protocol as protocol, review_prompt_paragraphs as selection
    descriptor, base = build_base_prompt(role, orchestration, common_version=common_version, repair_suffix=repair_suffix)
    prompts = dict(bar=dict(request=BAR_REQUEST_PROMPT, final=BAR_FINAL_PROMPT),
                   slate=dict(request=SLATE_REQUEST_PROMPT, final=SLATE_FINAL_PROMPT),
                   result=dict(request=RESULT_REQUEST_PROMPT, final=RESULT_FINAL_PROMPT))
    for name in prompts:
        prompts[name].update(protocol.SESSION_FINAL_PROMPTS[name])
    text = '\n\n' + prompts[role][kind]
    if refresh:
        text += SESSION_REFRESH_PROMPT
    if kind in ('request', 'combine'):
        text += SESSION_BASE_SHAPE_PROMPT
    elif kind in ('page', 'closing'):
        text += SESSION_PART_SHAPE_PROMPT
    text += TURN_LIMITS_PROMPT
    if kind in ('page', 'closing'):
        text += protocol.SESSION_PAGE_REQUESTS_PROMPT
    if orchestration is None and kind in ('final', 'page', 'closing'):
        text += STUDY_FORMS_PROMPT
    if orchestration is None and role == 'result' and kind in ('final', 'page'):
        text += STUDY_SUPPORT_FORMS_PROMPT
    template = PROTOCOL + '/' + role + '/' + kind + ('/context-repair' if repair_suffix is not None else '')
    parameters = dict(source_rules=PROTOCOL, session_stage=kind, base_prompt=descriptor)
    if refresh:
        parameters[REFRESH_PARAMETER] = True
    # The parameters name the repair suffix as the base does.
    parameters.update((key, base['parameters'][key]) for key in ('context_repair', 'context_repair_exhaustion')
                      if key in base['parameters'])
    base_text = base['text']
    if paragraphs:
        rendered = selection.append_prompt_paragraphs(dict(template_id=template, text=base_text, parameters=parameters),
            role, paragraphs.get(selection.CARRY_PARAMETER), paragraphs.get(selection.PARAMETER))
        template, base_text, parameters = rendered['template_id'], rendered['text'], rendered['parameters']
    prompt = protocol._build_prompt(template, base_text + text, parameters, descriptor['parameters']['common_version'])
    # The session bounds share the name of the capacity paragraph's parameter, so they join after the paragraphs.
    prompt['parameters']['capacity'] = copy.deepcopy(bounds)
    return prompt


def _select_repair_suffix(logical):
    """The suffix that the base path gives a repair of the same binding, or None outside a context repair."""
    from . import review_context_repair as repair
    binding = logical.get('context_repair')
    if binding is None:
        return None
    return repair.EXHAUSTION_PROMPT_V1 if 'exhaustion' in binding else repair.PROMPT


def matches_turn_prompt(assignment, packet=None, logical=None):
    """Rebuild the recorded prompt of a v4 turn from its parameters and compare it whole (rule V2). logical, the
    logical packet of the chain, is given where a caller authenticates the turn: the paragraphs that the turn
    recorded, beside a refusal paragraph, are then exactly those that the logical packet selects."""
    from . import review_protocol as protocol, review_prompt_paragraphs as selection, review_context_repair as repair
    from .errors import ResearchError
    parameters = assignment['prompt'].get('parameters', {})
    kind = parameters.get('session_stage')
    bounds = parameters.get('capacity')
    if packet is not None:
        binding = read_session_binding(packet)
        if kind != find_session_form(packet) or bounds != binding.get('capacity'):
            return False
    if kind not in ('request', 'final', 'page', 'closing', 'combine'):
        return False
    suffix = None
    if parameters.get('context_repair') is True:
        suffix = (repair.EXHAUSTION_PROMPT_V1 if 'context_repair_exhaustion' in parameters else repair.PROMPT)
    if (suffix is not None) != (assignment.get('reassessment_required') is True):
        return False
    common_version = protocol.find_common_prompt_version(assignment['prompt'])
    if common_version is None:
        return False
    recorded = {key: parameters[key] for key in (selection.PARAMETER, selection.CARRY_PARAMETER) if key in parameters}
    refresh = parameters.get(REFRESH_PARAMETER) is True
    try:
        expected = build_turn_prompt(assignment['role'], kind, bounds, orchestration=find_base_orchestration(assignment),
            paragraphs=recorded, common_version=common_version, repair_suffix=suffix, refresh=refresh)
    except ResearchError:
        # An unregistered or reordered paragraph version.
        return False
    if expected != assignment['prompt']:
        return False
    if logical is None:
        return True
    if refresh != ('problem_refresh' in logical):
        return False
    refusals = set(selection.REFUSAL_PARAGRAPH_VERSIONS.values())
    own = dict(recorded, **{selection.PARAMETER: [version for version in recorded.get(selection.PARAMETER, [])
                                                  if version not in refusals]})
    if not own[selection.PARAMETER]:
        del own[selection.PARAMETER]
    return own == selection.select_prompt_parameters(assignment['role'], logical, session_rules=PROTOCOL)


def _describe_logical_fields(packet):
    excluded = {'digest', 'evidence', 'source_rules', 'source_inventory', 'reviewer_excerpts',
                'source_deliveries', 'pending_source_requests', 'settled_source_requests', 'source_history'}
    return {key: copy.deepcopy(value) for key, value in packet.items() if key not in excluded}


def _list_frame_values(packet):
    values = {}
    def visit(value, path):
        values['f-' + digest(list(path))] = (path, value)
        for key, child in (value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else ()):
            visit(child, path + (key,))
    visit(packet, ())
    return values


def measure_base_prompt(role, logical):
    """The characters of the base prompt of a new chain as its turns render it: the template of the role with its
    repair suffix and the paragraphs that the logical packet selects (rule V3). It depends on the logical packet only.
    A packet of the base path that holds a capacity notice, which a slate series check admits, counts that notice."""
    from . import review_prompt_paragraphs as selection
    _, base = build_base_prompt(role, find_base_orchestration(logical), repair_suffix=_select_repair_suffix(logical))
    selected = selection.select_prompt_parameters(role, logical, session_rules=PROTOCOL)
    rendered = selection.append_prompt_paragraphs(base, role, selected.get(selection.CARRY_PARAMETER),
        selected.get(selection.PARAMETER), selected.get(selection.CAPACITY_PARAMETER))
    return len(rendered['text'])


def check_v4_admission(records, artifacts, route, role, packet):
    """Plan a frozen session without publishing any artifacts or consulting later currency."""
    from . import review_field_classes as classes, review_composition as composition
    from .errors import ResearchError
    from .openness_reading_v7 import _PlanningArtifacts
    rule = composition.find_rule(role, packet)
    if rule is None or route['adapter'] == 'codex_cli_visual_v1':
        return dict(state='stop', protocol=PROTOCOL, reason='unsupported_role_or_route')
    if 'target' in (packet.get('support_contract') or {}):
        return dict(state='stop', protocol=PROTOCOL, reason='scoped_support_contract')
    if role == 'slate' and bar_sources.find_bound_text_forms(packet):
        # A captured text form delivers a binary original through parts. The
        # v4 artifact inventory addresses the exact original text bytes.
        return dict(state='stop', protocol=PROTOCOL, reason='binary_required_evidence')
    overlay = _PlanningArtifacts(artifacts)
    logical = _describe_logical_fields(packet)
    targets = [dict(handle='t%d' % index, id=row['id'], label=row['label'][:120])
               for index, row in enumerate(composition.list_targets(rule, packet), 1)]
    moved = {kind: set() for kind in ('request', 'final')}
    profile = capacity.read_output_profile(records, route)
    outer_identity = capacity.measure_capacity_value(route, _build_session_identity(packet))
    reserve = (FRAME_ENVELOPE_CHARACTERS - OUTER_IDENTITY_CHARACTERS
               + max(OUTER_IDENTITY_CHARACTERS, outer_identity) + measure_base_prompt(role, packet))
    from .review_prompt_paragraphs import SLATE_DELIVERY_KEY
    cited = {(work['version_id'], work['depth']) for work in packet.get(SLATE_DELIVERY_KEY, {}).get('cited_works', [])}
    try:
        while True:
            frames = classes.describe_role_frame(route, role, logical, ('request', 'final'), unit_fields=moved)
            # Reserve the base prompt, turn text, binding, delivery descriptors and bounded source rows.
            for fields in frames.values():
                fields[classes.FRAME_DESCRIPTOR_FIELD] += reserve
            declarations = {}
            for kind in frames:
                for row in classes.list_frame_units(route, role, logical, frames[kind]):
                    declarations[row['handle']] = row
            values = _list_frame_values(logical)
            frame_units = []
            for handle in declarations:
                path, value = values[handle]
                content = serialize_record(value)
                reference = overlay.put(content, 'application/json')
                frame_units.append(dict(kind='frame', identity=dict(field=list(path), frame_handle=handle),
                                        artifact=reference, label=str(path[-1])))
            built = build_session_inventory(records, overlay, packet, frame_units)
            overlay.stage_bodies(built['bodies'])
            inventory = built['inventory']
            inventory.update(targets=targets, logical_fields=logical)
            for row in inventory['units']:
                # A work that a current candidate cites is required at the depth that the slate delivers it.
                row['required'] = (row['kind'] in ('frame', 'artifact', 'history')
                                   or (row['identity'].get('version_id'), row['kind']) in cited)
            declaration = describe_session_plan(route, frames, inventory['units'], [row['handle'] for row in targets], final_pages=True)
            members = capacity.prepare_assignment_capacity(records, overlay, route, declaration)
            plan = capacity.read_chain_capacity_plan(overlay, members)
            if not any(row['unit_fields'] for row in plan['frame'].values()):
                break
            for kind in frames:
                moved[kind].update(plan['frame'][kind]['unit_fields'])
    except UnicodeDecodeError:
        return dict(state='stop', protocol=PROTOCOL, reason='binary_required_evidence')
    except ResearchError as error:
        if error.code != 'review_route_capacity_below_floor':
            raise
        return dict(state='stop', protocol=PROTOCOL, reason='below_floor', code=error.code, details=error.details)
    plan['allocation']['session_source_rows'] = True
    plan['allocation']['session_passage_characters'] = review_excerpts.SESSION_PASSAGE_CHARACTERS
    members['capacity_plan'] = dict(protocol=capacity.CAPACITY_PLAN_PROTOCOL, digest=digest(plan),
        artifact=overlay.put(serialize_record(plan), 'application/json'))
    built['artifact'] = _describe_json(inventory)
    forecast = describe_session_forecast(overlay, route, inventory, plan, profile)
    return dict(state='record', protocol=PROTOCOL, capacity_plan=plan, capacity_members=members,
                inventory=built, forecast=forecast, bodies=overlay.bodies)


SESSION_BELOW_FLOOR_REASON = 'session_below_floor'


def require_session_capacity(records, artifacts, value, logical, admission, *, prompt, trial_assignment, explain=True):
    """Rule V5: a new chain that the admission of the rules v4 stops for capacity, and that would start as a session
    of an older rule set, which has no capacity plan, ends before its first call, with the stop that a first request
    which does not fit gets and the routes that serve the chain. A stop for another reason takes the next rule set.
    The trial of the public assignment decides each serving route; a serving trial passes explain=False and gets the
    refusal without routes or steps."""
    if admission['state'] != 'stop' or admission['reason'] != 'below_floor':
        return
    from .errors import ResearchError
    from .review_route_capacity import CAPACITY_STOP_MESSAGE, CAPACITY_OWNER_DECISION, UNSERVED_REVIEW_MESSAGE
    from .review_plan_steps import describe_capacity_repair, describe_capacity_steps
    if not explain:
        raise ResearchError('review_route_capacity_below_floor', CAPACITY_STOP_MESSAGE)
    # A new chain of v4 continues no session of its own bar, so its steps start it on a serving route.
    repair_plan = describe_capacity_repair(records, artifacts, value, None)
    serving = capacity.find_serving_routes(records, value['role'], logical, artifacts=artifacts, assignment=value,
        prompt=prompt, exclude_route=value['route_id'], repair_plan=repair_plan, trial_assignment=trial_assignment)
    details = dict(reason=SESSION_BELOW_FLOOR_REASON, admission={key: copy.deepcopy(admission[key])
                   for key in ('protocol', 'reason', 'code', 'details') if key in admission},
                   serving_routes=serving, owner_decides=CAPACITY_OWNER_DECISION,
                   then=describe_capacity_steps(records, artifacts, value, serving, repair_plan=repair_plan))
    if not serving and not any(answer.get('route_id') for answer in details['then']):
        details['coverage'] = UNSERVED_REVIEW_MESSAGE
    raise ResearchError('review_route_capacity_below_floor', CAPACITY_STOP_MESSAGE, details)


def _require_session_members(value, *keys):
    from .errors import ResearchError
    if not isinstance(value, dict) or any(value.get(key) is None for key in keys):
        raise ResearchError('review_invocation_unverified', TURN_BINDING_MESSAGE)


def _require_bound_references(value, *keys):
    _require_session_members(value, *keys)
    for key in keys:
        _require_session_members(value[key], 'sha256', 'path', 'size', 'media_type')


def _require_bound_assignment(assignment):
    from .errors import ResearchError
    _require_session_members(assignment, 'id', 'recorded_revision', 'route_id', 'role', 'reviewer_id', 'author_id',
                             'context', 'prompt', 'session_chain', 'capacity_plan', 'capacity_plan_kind')
    if 'dossier_id' not in assignment or not isinstance(assignment['context'], dict):
        raise ResearchError('review_invocation_unverified', TURN_BINDING_MESSAGE)
    _require_bound_references(assignment, 'packet')
    _require_session_members(assignment['capacity_plan'], 'protocol', 'digest')
    _require_bound_references(assignment['capacity_plan'], 'artifact')
    chain = assignment['session_chain']
    _require_session_members(chain, 'first_assignment_id', 'turn')
    _require_bound_references(chain, 'inventory', 'state', 'logical')
    if 'final_page_plan' in chain:
        _require_session_members(chain['final_page_plan'], 'digest')
        _require_bound_references(chain['final_page_plan'], 'artifact')


def read_bound_session(artifacts, assignment):
    from .review_protocol import _read_json
    _require_session_members(assignment, 'session_chain', 'capacity_plan')
    chain = assignment['session_chain']
    _require_bound_references(chain, 'inventory', 'state')
    _require_session_members(assignment['capacity_plan'], 'protocol', 'digest')
    _require_bound_references(assignment['capacity_plan'], 'artifact')
    return (capacity.read_chain_capacity_plan(artifacts, assignment),
            _read_json(artifacts, chain['inventory']), _read_json(artifacts, chain['state']))


def _find_covered_end(state, unit, start=0):
    position = start
    for row in sorted((row for row in state['deliveries'] if row['unit'] == unit), key=lambda row: row['start']):
        if row['start'] > position:
            break
        position = max(position, row['end'])
    return position


def _list_remaining_units(inventory, state):
    return [(row, _find_covered_end(state, row['handle'])) for row in inventory['units']
            if row.get('required') and _find_covered_end(state, row['handle']) < row['characters']]


def _resolve_requested_units(inventory, request):
    kind = request['kind']
    return [row for row in inventory['units'] if
            (kind == 'source' and row['kind'] == request['depth']
             and row['identity'].get('version_id') == request['version_id'])
            or (kind == 'artifact' and row['sha256'] == request['sha256'])
            or (kind in ('unit', 'passage') and row['handle'] == request['unit'])]


def _build_delivery(artifacts, unit, start, end, request=None):
    content = artifacts.read(unit['artifact']).decode('utf-8')[start:end]
    reference = describe_artifact(content.encode('utf-8'), unit['artifact']['media_type'])
    evidence = dict(artifact=reference, content=content, encoding='utf-8')
    if start or end != unit['characters']:
        evidence.update(original_artifact=unit['artifact'], segment=dict(start=start, end=end, length=unit['characters']))
    row = dict(unit=unit['handle'], kind=unit['kind'], part=start + 1, start=start, end=end,
               characters=end - start, sha256=reference['sha256'])
    if unit['kind'] == 'frame':
        from .review_composition import _escape_field
        row['field'] = '/' + '/'.join(_escape_field(part) for part in unit['identity']['field'])
    if request:
        row.update(request_id=request['id'], request_kind=request['kind'])
    return row, evidence


def _pack_session_deliveries(artifacts, route, inventory, state, plan, allowance):
    """Pack serialized bodies and their rows jointly; a split always makes progress."""
    from .openness_reading_v7 import build_list_page
    rows, evidence, pages = [], [], []
    occupied = 2

    def build_page_pair(request, page):
        data = serialize_record(page)
        content = data.decode('utf-8')
        row = dict(kind='list_page', request_id=request['id'], page=page,
                   characters=len(content), request_complete=True)
        entry = dict(artifact=describe_artifact(data, 'application/json'), content=content, encoding='utf-8')
        return dict(row=row, evidence=entry)

    def build_status_pair(request, status):
        row = dict(kind='request_status', request_id=request['id'], status=status, request_complete=True)
        entry = dict(artifact=describe_artifact(b'{}', 'application/json'), content='{}', encoding='utf-8')
        return dict(row=row, evidence=entry)

    def fit_inventory_page(request, page, room, units):
        pair = build_page_pair(request, page)
        charge = capacity.measure_capacity_value(route, pair)
        if charge <= room:
            return pair, charge
        # Keep all ten logical rows and the executable handle and hash.
        # Only display titles are shortened, and only on the no-fit path.
        # A shortened title ends with the version id of its source, so
        # different sources keep different display texts.
        versions = {unit['handle']: unit['identity']['version_id'] for unit in units
                    if unit['kind'] in ('source', 'abstract', 'fulltext')}

        def shorten_label(row, length):
            if row['handle'] not in versions or len(row['label']) <= length:
                return row
            suffix = ' [' + versions[row['handle']] + ']'
            return dict(row, label=row['label'][:max(0, length - len(suffix))] + suffix)

        low, high = 0, LABEL_CHARACTERS - 1
        while low <= high:
            length = (low + high) // 2
            rows = [shorten_label(row, length) for row in page['rows']]
            trial = build_page_pair(request, dict(page, rows=rows))
            size = capacity.measure_capacity_value(route, trial)
            if size <= room:
                pair, charge = trial, size
                low = length + 1
            else:
                high = length - 1
        return pair, charge

    passage_left = max(0, state['allowances']['passages']['characters'] - state['allowances']['passages']['used'])
    candidates = [(unit, start, unit['characters'], None) for unit, start in _list_remaining_units(inventory, state)]
    for request in state['pending_requests']:
        if request['kind'] in ('inventory_page', 'list_page'):
            if request['kind'] == 'list_page':
                page = build_list_page(request['list'], request['after'], plan, state, route,
                    allowance - occupied, artifacts=artifacts,
                    measure_page=lambda page: capacity.measure_capacity_value(route, build_page_pair(request, page)))
            else:
                start = request['page'] * bar_sources.PAGE_SIZE
                units = inventory['units'][start:start + bar_sources.PAGE_SIZE]
                page = dict(page=request['page'], rows=[{key: row[key] for key in
                    ('handle', 'kind', 'label', 'characters')} for row in units], total=len(inventory['units']))
            if page is None and rows:
                break
            if request['kind'] == 'inventory_page':
                pair, charge = fit_inventory_page(request, page, allowance - occupied, units)
            else:
                pair = build_page_pair(request, page) if page is not None else build_status_pair(request, 'unavailable')
                charge = capacity.measure_capacity_value(route, pair)
            if charge + occupied > allowance:
                break
            rows.append(pair['row'])
            evidence.append(pair['evidence'])
            occupied += charge
            if request['kind'] == 'list_page' and page is not None:
                pages.append({key: page[key] for key in ('list', 'after', 'next')})
            continue
        requested = _resolve_requested_units(inventory, request)
        if not requested and request['kind'] in ('unit', 'passage'):
            from .openness_final_pages import resolve_entry_request
            entry = resolve_entry_request(state, artifacts, request)
            requested = [entry] if entry is not None else []
        unavailable = not requested or (request['kind'] == 'passage' and requested[0]['characters'] == 0)
        if unavailable or (request['kind'] != 'passage' and all(
                _find_covered_end(state, unit['handle']) >= unit['characters'] for unit in requested)):
            pair = build_status_pair(request, 'unavailable' if unavailable else 'already_delivered')
            charge = capacity.measure_capacity_value(route, pair)
            if occupied + charge <= allowance:
                rows.append(pair['row'])
                evidence.append(pair['evidence'])
                occupied += charge
            continue
        for unit in requested:
            start = _find_covered_end(state, unit['handle'])
            end = unit['characters']
            if request['kind'] == 'passage':
                text = artifacts.read(unit['artifact']).decode('utf-8')
                begin = text.find(request['anchor']) if request.get('anchor') else 0
                start = begin + sum(row['end'] - row['start'] for row in state['deliveries']
                                    if row.get('request_id') == request['id'])
                end = min(len(text), begin + request['characters'], start + passage_left)
                passage_left -= max(0, end - start)
            if start < end:
                candidates.append((unit, start, end, request))
    seen = set()
    for unit, start, stop, request in candidates:
        if (unit['handle'], start) in seen:
            continue
        seen.add((unit['handle'], start))
        left, right = 0, stop - start
        selected = None
        while left < right:
            length = (left + right + 1) // 2
            row, entry = _build_delivery(artifacts, unit, start, start + length, request)
            if request:
                row['request_complete'] = (start + length == stop and
                    all(other['handle'] == unit['handle'] or _find_covered_end(state, other['handle']) >= other['characters']
                        for other in _resolve_requested_units(inventory, request)))
            charge = capacity.measure_capacity_value(route, dict(row=row, evidence=entry))
            if occupied + charge <= allowance:
                left, selected = length, (row, entry, charge)
            else:
                right = length - 1
        if not left:
            break
        if selected is None or selected[0]['end'] != start + left:
            row, entry = _build_delivery(artifacts, unit, start, start + left, request)
            if request:
                row['request_complete'] = start + left == stop
            selected = row, entry, capacity.measure_capacity_value(route, dict(row=row, evidence=entry))
        row, entry, charge = selected
        rows.append(row)
        evidence.append(entry)
        occupied += charge
        if row['end'] < stop:
            break
    return rows, evidence, pages


def _replace_frame_handles(value, handles):
    if isinstance(value, list):
        return [_replace_frame_handles(row, handles) for row in value]
    if isinstance(value, dict):
        return {key: handles.get(item, item) if key == 'handle' and isinstance(item, str)
                else _replace_frame_handles(item, handles) for key, item in value.items()}
    return value


def _build_session_identity(logical):
    packet = {key: logical[key] for key in ('protocol', 'role', 'reviewer_id', 'dossier_id') if key in logical}
    packet['source_rules'] = PROTOCOL
    if 'subject' in logical:
        packet['subject'] = copy.deepcopy(logical['subject'])
    return packet


def build_session_packet(artifacts, route, logical, chain, number, inventory, state, plan, limits, kind, *, notice=None):
    from . import review_field_classes as classes
    frame = classes.classify_packet(route, logical['role'], inventory['logical_fields'],
                                     bounded_fields=plan['frame'][kind]['bounded_fields'])['frame']
    handles = {row['identity']['frame_handle']: row['handle'] for row in inventory['units'] if row['kind'] == 'frame'}
    frame = _replace_frame_handles(frame, handles)
    packet = _build_session_identity(logical)
    bounds = dict(output_tokens=plan['limits']['output_tokens']['value'], answer_bound=limits['answer_bound'],
                  material_allowance=limits['material_allowance'],
                  level_material_allowance=limits.get('level_material_allowance', limits['material_allowance']),
                  request_bound=limits['request_bound']['text_characters'])
    binding = dict(protocol=PROTOCOL, chain=chain, turn=number, plan_digest=digest(plan),
                   inventory_digest=digest(inventory), state_digest=digest(state), capacity=bounds, frame=frame)
    if kind == 'request':
        carried = describe_session_allowances(route, state, plan, limits)
        rows, evidence, _ = _pack_session_deliveries(artifacts, route, inventory, state, plan, limits['material_allowance'])
        carried['passages']['available'] = measure_passage_room(state, rows)
        remaining = _list_remaining_units(inventory, state)
        if not rows and (remaining or state['pending_requests']):
            from .errors import ResearchError
            raise ResearchError('review_route_capacity_below_floor', capacity.BELOW_FLOOR_MESSAGE,
                                dict(material_allowance=limits['material_allowance']))
        binding.update(notes=carried.pop('latest_notes'), allowances=carried,
            targets=describe_target_labels(route, inventory['targets']),
            plan=dict(now=rows, delivered=dict(sources=state['delivered_sources'],
                characters=sum(row['end'] - row['start'] for row in state['deliveries'])),
                pending=dict(requests=len(state['pending_requests']), units=len(remaining),
                             characters=sum(unit['characters'] - start for unit, start in remaining))))
        packet.update(session_turn=binding, evidence=evidence)
    else:
        entries = describe_session_final_entries(state, plan['targets']['handles'])
        sources = list(dict.fromkeys(row['unit'] for row in entries))
        source_rows = [dict(handle=row['handle'], kind=row['kind'], label=row['label'])
                       for row in inventory['units'] if row['handle'] in sources]
        binding.update(notes=state['latest_notes'], entries=entries, sources=source_rows, fill=[],
                       targets=describe_target_labels(route, inventory['targets']))
        if state.get('material_request'):
            binding['material_request'] = copy.deepcopy(state['material_request'])
        # Fill holds earlier notes only while the final's material grant has room.
        material = dict(notes=binding['notes'], entries=entries, sources=source_rows, fill=[])
        for notes in reversed(state['notes'][:-1]):
            candidate = dict(material, fill=material['fill'] + [notes])
            if capacity.measure_capacity_value(route, candidate) > limits['material_allowance']:
                break
            material = candidate
        binding['fill'] = material['fill']
        packet.update(session_final=binding, evidence=[])
    if notice is not None:
        from .review_refusal_notice import attach_refusal_notice
        packet = attach_refusal_notice(packet, notice)
    packet.pop('digest', None)
    packet['digest'] = digest(packet)
    return packet


def measure_passage_room(state, now):
    """Return the space for new passage requests after this turn's deliveries and open reservations."""
    passages = state['allowances']['passages']
    used_now = sum(row['characters'] for row in now if row.get('request_kind') == 'passage')
    completed = {row['request_id'] for row in now if row.get('request_complete')}
    reserved = sum(row['characters'] for row in state['pending_requests']
                   if row['kind'] == 'passage' and row['id'] not in completed)
    return max(0, passages['characters'] - passages['used'] - used_now - reserved)


def validate_request_output(artifacts, assignment, packet, output):
    from contextlib import contextmanager
    from .errors import ResearchError, describe_error_at
    from .operations import describe_json_type, fields, text
    from .openness_reading import INVALID_RESPONSE
    from .openness_reading_v7 import _validate_list_cursor

    @contextmanager
    def describe_turn_error(path, *, code='invalid_input', text_value=None, **context):
        try:
            with describe_error_at(path, **context):
                yield
        except ResearchError as error:
            if error.code != code:
                raise
            details = dict(error.details)
            if isinstance(text_value, str):
                details['predicate'] = ('nonblank' if not text_value.strip() else
                                        'no_nul' if '\x00' in text_value else 'valid_utf8')
            _require_turn(False, path=details.pop('path'), **details)

    plan, inventory, state = read_bound_session(artifacts, assignment)
    binding = packet['session_turn']
    route = dict(adapter=plan['route_class']['adapter'], model=plan['route_class']['model'], endpoint='',
                 configuration=dict(max_output_tokens=plan['limits']['output_tokens']['value'],
                                    effort=plan['route_class']['effort']))
    with describe_turn_error(''):
        fields(output, ('schema_version', 'stage', 'notes', 'excerpts', 'requests', 'settled_requests'), path='')
    _require_turn(type(output['schema_version']) is int and output['schema_version'] == 1, path='/schema_version')
    _require_turn(output['stage'] == 'request', path='/stage')
    with describe_turn_error('/notes', text_value=output['notes'], expected_type='string',
                             received_type=describe_json_type(output['notes'])):
        text(output['notes'], 'Session notes')
    notes_cost = capacity.measure_capacity_value(route, output['notes']) - capacity.measure_capacity_value(route, '')
    bound = binding['allowances']['notes']
    _require_turn(notes_cost <= bound, path='/notes', notes_cost=notes_cost, bound=bound)
    for member in ('excerpts', 'requests', 'settled_requests'):
        _require_turn(isinstance(output[member], list), path='/' + member, expected_type='array',
                      received_type=describe_json_type(output[member]))

    now = binding['plan']['now']
    units = {row['handle']: row for row in inventory['units']}
    targets = set(plan['targets']['handles'])
    for index, row in enumerate(output['excerpts']):
        path = '/excerpts/%d' % index
        with describe_turn_error(path):
            fields(row, ('unit', 'text', 'purpose', 'targets'), path=path)
        unit = row['unit']
        _require_turn(isinstance(unit, str) and unit in units, path=path + '/unit', unit=unit)
        for member, minimum, maximum in (('text', 1, 6000), ('purpose', 0, PURPOSE_CHARACTERS)):
            value = row[member]
            _require_turn(isinstance(value, str), path=path + '/' + member, expected_type='string',
                          received_type=describe_json_type(value))
            bounds = dict(minimum_characters=minimum) if minimum else {}
            _require_turn(minimum <= len(value) <= maximum, path=path + '/' + member,
                          characters=len(value), maximum_characters=maximum, **bounds)
        named = row['targets']
        _require_turn(isinstance(named, list), path=path + '/targets', expected_type='array',
                      received_type=describe_json_type(named))
        _require_turn(bool(named), path=path + '/targets', items=len(named), minimum_items=1)
        seen_targets = set()
        for target_index, target in enumerate(named):
            _require_turn(isinstance(target, str) and target in targets and target not in seen_targets,
                          path='%s/targets/%d' % (path, target_index), target=target)
            seen_targets.add(target)
        # Offsets and span digests are projected from this turn's exact bytes.
        delivered = (entry['content'] for delivery, entry in zip(now, packet['evidence'])
                     if delivery.get('unit') == unit)
        _require_turn(any(row['text'] in content for content in delivered), path=path + '/text', unit=unit)

    pending = {row['id'] for row in state['pending_requests']}
    settled = set()
    for index, request_id in enumerate(output['settled_requests']):
        _require_turn(isinstance(request_id, str) and request_id in pending and request_id not in settled,
                      path='/settled_requests/%d' % index, request_id=request_id)
        settled.add(request_id)

    complete = {row['request_id'] for row in now if row.get('request_complete')}
    open_lists = {row['list']: row['id'] for row in state['pending_requests']
                  if row['kind'] == 'list_page' and row['id'] not in complete}
    list_pages = dict(state.get('list_pages', {}))
    for delivery in now:
        if delivery['kind'] == 'list_page' and 'list' in delivery['page']:
            page = delivery['page']
            list_pages[page['list']] = dict(next=page['next'], ended=page['next'] is None)
    cursor_state = dict(state, list_pages=list_pages)
    remaining = measure_passage_room(state, now)
    earlier, accepted_ids = {}, set()
    for request in state['requests']:
        earlier[request['id']] = _describe_pending_request(request)
        if request['status'] != 'over_allowance':
            accepted_ids.add(request['id'])
    request_fields = {
        'source': ('version_id', 'depth'),
        'artifact': ('sha256',),
        'unit': ('unit',),
        'passage': ('unit', 'anchor', 'characters'),
        'inventory_page': ('page',),
        'list_page': ('list', 'after'),
    }
    seen = set()
    for index, row in enumerate(output['requests']):
        path = '/requests/%d' % index
        if not isinstance(row, dict) or not {'id', 'kind'} <= row.keys():
            with describe_turn_error(path):
                fields(row, ('id', 'kind'), optional=None, path=path)
        kind = row['kind']
        _require_turn(isinstance(kind, str) and kind in request_fields, path=path + '/kind')
        with describe_turn_error(path):
            fields(row, ('id', 'kind') + request_fields[kind], path=path)
        request_id = row['id']
        _require_turn(isinstance(request_id, str), path=path + '/id', expected_type='string',
                      received_type=describe_json_type(request_id))
        _require_turn(0 < len(request_id) <= 120, path=path + '/id',
                      characters=len(request_id), minimum_characters=1, maximum_characters=120)
        _require_turn(request_id not in seen and (request_id not in earlier or earlier[request_id] == row),
                      path=path + '/id', request_id=request_id)
        seen.add(request_id)
        characters = capacity.measure_capacity_value(route, row)
        _require_turn(characters <= REQUEST_ROW_CHARACTERS, path=path, characters=characters,
                      maximum_characters=REQUEST_ROW_CHARACTERS)
        if kind == 'source':
            _require_turn(row['depth'] in ('abstract', 'fulltext'), path=path + '/depth')
            version = row['version_id']
            _require_turn(isinstance(version, str)
                          and any(unit['identity'].get('version_id') == version for unit in inventory['units']),
                          path=path + '/version_id')
        elif kind == 'artifact':
            _require_turn(any(unit['sha256'] == row['sha256'] and unit['kind'] in ('artifact', 'frame')
                              for unit in inventory['units']), path=path + '/sha256')
        elif kind in ('unit', 'passage'):
            unit = row['unit']
            if isinstance(unit, str) and unit not in units:
                from .openness_final_pages import resolve_entry_request
                archived = resolve_entry_request(state, artifacts, row)
                if archived is not None:
                    units[unit] = archived
            _require_turn(isinstance(unit, str) and unit in units, path=path + '/unit', unit=unit)
            if kind == 'passage':
                requested = row['characters']
                _require_turn(type(requested) is int and 0 < requested <= 100000, path=path + '/characters',
                              requested=requested, remaining=remaining, minimum_characters=1,
                              maximum_characters=100000)
                if request_id not in accepted_ids:
                    _require_turn(requested <= remaining, path=path + '/characters', requested=requested,
                                  remaining=remaining)
                    remaining -= requested
                anchor = row['anchor']
                if anchor is not None:
                    _require_turn(isinstance(anchor, str), path=path + '/anchor', expected_type='string',
                                  received_type=describe_json_type(anchor))
                    _require_turn(0 < len(anchor) <= 600, path=path + '/anchor', characters=len(anchor),
                                  minimum_characters=1, maximum_characters=600)
                    body = artifacts.read(units[unit]['artifact']).decode('utf-8')
                    _require_turn(anchor in body, path=path + '/anchor', unit=unit, predicate='occurs_in_unit')
        elif kind == 'inventory_page':
            size = bar_sources.PAGE_SIZE
            maximum = max(0, (len(inventory['units']) - 1) // size)
            _require_turn(type(row['page']) is int and 0 <= row['page'] <= maximum, path=path + '/page',
                          minimum_page=0, maximum_page=maximum)
        elif kind == 'list_page':
            name, after = row['list'], row['after']
            _require_turn(isinstance(name, str) and (name == 'allowances' or name in state.get('final_blocks', {})),
                          path=path + '/list', list=name)
            _require_turn(after is None or isinstance(after, str), path=path + '/after', list=name)
            _require_turn(name not in open_lists, path=path + '/list', request_id=open_lists.get(name))
            with describe_turn_error(path, code=INVALID_RESPONSE):
                _validate_list_cursor(name, after, plan, cursor_state, artifacts=artifacts, path=path)
            open_lists[name] = request_id


def _project_completed_turn(artifacts, assignment, packet, attempt, output):
    binding = read_session_binding(packet)
    delivery_only = find_session_kind(packet) != 'request' and output.get('stage') != 'request'
    if delivery_only:
        if not binding.get('page_plan_digest') or not binding.get('plan', {}).get('now'):
            return None
        output = dict(stage=find_session_form(packet), notes='', excerpts=[], requests=[], settled_requests=[])
    if find_session_kind(packet) != 'request':
        binding = dict(binding, plan=binding.get('plan', dict(now=[])), allowances=binding['request_allowances'])
    rows = binding['plan']['now']
    if 'page_plan_digest' in binding:
        plan = capacity.read_chain_capacity_plan(artifacts, assignment)
        route = dict(adapter=plan['route_class']['adapter'], model=plan['route_class']['model'], endpoint='',
            configuration=dict(max_output_tokens=plan['limits']['output_tokens']['value']))
        binding['excerpt_room'] = min(binding['allowances']['excerpts'], max(0,
            binding['capacity']['material_allowance'] - measure_final_material(route, dict(binding, notes=output['notes']))))
    deliveries = [dict(row, text=entry['content']) for row, entry in zip(rows, packet['evidence']) if 'unit' in row]
    observation = dict(number=binding['turn'], assignment_id=assignment['id'], attempt_id=attempt['id'],
        deliveries=deliveries, output=output, delivery_only=delivery_only,
        excerpt_allowance=(binding.get('excerpt_room', binding['allowances']['excerpts'])),
        list_pages=[{key: row['page'][key] for key in ('list', 'after', 'next')}
                    for row in rows if row['kind'] == 'list_page' and 'list' in row['page']])
    # Inventory/list requests have no text unit but settle at the same projection boundary.
    observation['completed_list_requests'] = [row['request_id'] for row in rows
        if 'unit' not in row and row.get('request_complete')]
    return observation


def build_handle_packet(packet, artifacts, assignment):
    from .source_links import span_locator
    plan, inventory, state = read_bound_session(artifacts, assignment)
    table = {}
    delivered = state['deliveries'] + read_session_binding(packet).get('plan', {}).get('now', [])
    for unit in inventory['units']:
        reference = unit['artifact']
        text = artifacts.read(reference).decode('utf-8')
        identity = unit['identity']
        def make_entry(start, end, part=None):
            citation = (dict(kind='source', link=dict(version_id=identity['version_id'], source_id=identity['source_id'],
                        artifact=reference, locator=span_locator(text, start, end, excerpt_length=0)))
                        if unit['kind'] == 'fulltext' else reference)
            entry = dict(reference=citation, text=text[start:end], source_start=start, source_text=text)
            if part is not None:
                entry['part'] = part
            return entry
        entry = make_entry(0, len(text))
        entry['parts'] = {str(row['part']): make_entry(row['start'], row['end'], row['part'])
                          for row in delivered if row.get('unit') == unit['handle']}
        table[unit['handle']] = entry
    for entry in state['excerpts']:
        if entry['status'] == 'verified':
            unit = next(row for row in inventory['units'] if row['handle'] == entry['unit'])
            citation = copy.deepcopy(table[entry['unit']]['reference'])
            if citation.get('kind') == 'source':
                citation['link']['locator'] = span_locator(artifacts.read(unit['artifact']).decode('utf-8'),
                                                          entry['start'], entry['end'], excerpt_length=0)
            table[entry['handle']] = dict(reference=citation)
    for handle, value in state.get('combinations', {}).items():
        table[handle] = dict(reference=artifacts.put(serialize_record(value), 'application/json'))
    table.update(state.get('material_handles', {}))
    result = dict(packet, handle_table=table, capacity=read_session_binding(packet)['capacity'],
                  material_state=state.get('material_state', dict(arguments={}, pending=None)))
    return result


PARTIAL_ABSTRACT_CITATION_HINT = (
    '. Citing the abstract, a part, or an excerpt requires delivery of the entire abstract. '
    'Request the remaining text before proceeding to a single final. '
    'Omit the citation only if the remaining answer meets the evidence requirements. '
    'If the assessment still needs evidence, cite another fully delivered, relevant item that supports it.')


def describe_partial_abstract_citation(delivery, missing, form):
    if 'sha256' not in missing or 'locator' in missing:
        return ''
    for packet in delivery['packets']:
        now = read_session_binding(packet).get('plan', {}).get('now', [])
        for row, entry in zip(now, packet.get('evidence', [])):
            if (row['kind'] == 'abstract' and 'segment' in entry
                    and entry.get('original_artifact', {}).get('sha256') == missing['sha256']):
                hint = PARTIAL_ABSTRACT_CITATION_HINT
                if form in ('page', 'closing'):
                    hint += ' This page or closing turn also accepts a request for the remaining text.'
                return hint
    return ''


def build_initial_session_state(records, artifacts, assignment, inventory, plan, route, logical):
    """Return the state of a first turn: empty, or, for an exhaustion repair, with the bodies that the
    authenticated turns of its predecessor delivered (D2 7.9)."""
    state = build_session_state(inventory, [], route, plan)['state']
    if not logical.get('context_repair'):
        return state
    from .review_delivered_evidence import collect_exhaustion_predecessor_turns, read_attempt_delivery
    bound_records = dict(records, review_assignment=dict(records.get('review_assignment', {}),
                                                        **{assignment['id']: assignment}))
    turns = collect_exhaustion_predecessor_turns(bound_records, artifacts, assignment)
    ranges = {}
    for prior, attempt in turns:
        delivery = read_attempt_delivery(records, artifacts, prior, attempt)['current_input']
        for identifier in delivery['artifacts']:
            ranges.setdefault(identifier, set()).add((0, None))
        for entry in delivery.get('segments', []):
            segment = entry['segment']
            ranges.setdefault(entry['original_artifact']['sha256'], set()).add((segment['start'], segment['end']))
    for unit in inventory['units']:
        spans = ranges.get(unit['sha256'], ())
        if not spans:
            continue
        content = artifacts.read(unit['artifact']).decode('utf-8')
        for number, (start, end) in enumerate(sorted(spans, key=lambda row: row[0]), 1):
            end = len(content) if end is None else end
            state['deliveries'].append(dict(unit=unit['handle'], part=number, start=start, end=end, turn=0,
                sha256=hashlib.sha256(content[start:end].encode('utf-8')).hexdigest()))
    delivered = {row['unit'] for row in state['deliveries']}
    state['delivered_sources'] = len({row['identity']['version_id'] for row in inventory['units']
                                      if row['kind'] in ('abstract', 'fulltext') and row['handle'] in delivered})
    return state


def _require_assignment_id(identifier):
    from .errors import ResearchError
    if len(identifier) > ASSIGNMENT_ID_CHARACTERS:
        raise ResearchError('invalid_input', TURN_ID_LIMIT_MESSAGE,
                            dict(characters=len(identifier), maximum_characters=ASSIGNMENT_ID_CHARACTERS))


def bind_child_identity(value, parent):
    """Use the stored spelling after the command has checked the two identities."""
    return value if parent is None else dict(value, author_id=parent['author_id'], reviewer_id=parent['reviewer_id'])


def prepare_session_assignment(records, artifacts, value, route, revision, *, logical=None, parent=None,
                               admission=None, slot=None, replacement=False, notice=None, refused_ids=(), diagnoses=None,
                               replay=False, replay_state=None, preview=False, bound_parent=None):
    """Publish one deterministic request and its immutable bindings in the command mutation."""
    from . import review_protocol as protocol
    from .operations import immutable_record
    _require_assignment_id(value['id'])
    value = bind_child_identity(value, parent)

    def request_limits_with_notice(limits):
        if notice is None:
            return limits
        return capacity.derive_plan_task_limits(plan, 'request', [dict(material_allowance=
            limits.get('level_material_allowance', limits['material_allowance']))],
            notice_characters=capacity.measure_capacity_value(route, notice))

    if parent is None:
        admission = admission or check_v4_admission(records, artifacts, route, value['role'], logical)
        for content in admission['bodies'].values():
            # Generated JSON/text artifacts are read by their content digest and media type.
            artifacts.put(content, 'application/json')
        built = admission['inventory']
        for unit in built['inventory']['units']:
            artifacts.put(built['bodies'][unit['sha256']], unit['artifact']['media_type'])
        inventory = built['inventory']
        members = admission['capacity_members']
        plan = admission['capacity_plan']
        chain = dict(first_assignment_id=value['id'], turn=1, inventory=protocol._put_json(artifacts, inventory),
                     logical=protocol._put_json(artifacts, logical))
        state = build_initial_session_state(records, artifacts, dict(value, recorded_revision=revision, session_chain=chain),
                                            inventory, plan, route, logical)
        kind = 'request'
        limits = capacity.derive_plan_task_limits(plan, kind)
    else:
        plan, inventory, state = bound_parent if bound_parent is not None else read_bound_session(artifacts, parent)
        logical = protocol._read_json(artifacts, parent['session_chain']['logical'])
        chain = copy.deepcopy(parent['session_chain'])
        members = dict(capacity_plan=parent['capacity_plan'])
        before = protocol._read_json(artifacts, parent['packet'])
        if not replacement:
            state = (copy.deepcopy(replay_state) if replay_state is not None else
                     rebuild_session_state(records, artifacts, parent, inventory, plan, route))
            chain['turn'] += 1
        kind = (find_session_kind(before) if replacement else
                'final' if chain.get('final_page_plan') else
                'request' if _list_remaining_units(inventory, state) or state['pending_requests'] else 'final')
        if kind == 'combine':
            kind = 'final'
        limits = derive_session_task_limits(records, artifacts, parent, kind, replacement=replacement)
        if replacement and kind == 'final' and state.get('material_state', {}).get('pending'):
            limits['answer_bound'] = max(plan['task']['final']['smallest_answer_bound'],
                                         before['session_final']['capacity']['answer_bound'] // 2)
    if kind == 'request':
        limits = request_limits_with_notice(limits)
    chain['state'] = protocol._put_json(artifacts, state)
    if kind == 'final' and not (replacement and state.get('material_state', {}).get('pending')):
        packet, reference = prepare_final_packet(records, artifacts, parent, route, logical, chain['turn'],
            inventory, state, plan, limits, replacement=replacement)
        if packet is None:
            return None, None
        if reference is not None:
            chain['final_page_plan'] = reference
        now = read_session_binding(packet).get('plan', {}).get('now', [])
        list_ids = {row['id'] for row in state['pending_requests'] if row['kind'] == 'list_page'}
        list_needs_room = any(row.get('status') == 'unavailable' and row.get('request_id') in list_ids for row in now)
        if (find_session_form(packet) in ('page', 'closing') and state['pending_requests']
                and not state.get('material_state', {}).get('pending')
                and (not now or list_needs_room)):
            # The held page's material can leave no room even for a status.
            # Keep its position and use the existing request form to make
            # progress before resuming that page or closing turn.
            kind = 'request'
            limits = request_limits_with_notice(
                derive_session_task_limits(records, artifacts, parent, kind, replacement=replacement))
            packet = build_session_packet(artifacts, route, logical, chain['first_assignment_id'], chain['turn'],
                                          inventory, state, plan, limits, kind, notice=notice)
        elif notice is not None:
            from .review_refusal_notice import attach_refusal_notice
            packet = attach_refusal_notice(packet, notice)
    elif kind == 'final' and chain.get('final_page_plan'):
        from .openness_final_pages import read_page_plan
        page_plan = read_page_plan(artifacts, chain['final_page_plan'])
        binding = read_session_binding(before)
        turn = next(row for row in page_plan['turns'] if row['number'] == binding['final_turn'])
        _, catalog, full = build_final_inputs(artifacts, route, logical, chain['first_assignment_id'], chain['turn'],
            inventory, state, plan, limits, turn['context']['page_room'])
        packet = build_paged_packet(artifacts, route, logical, chain['first_assignment_id'], chain['turn'],
            inventory, state, plan, limits, page_plan, turn, catalog, full)
    else:
        packet = build_session_packet(artifacts, route, logical, chain['first_assignment_id'], chain['turn'],
                                      inventory, state, plan, limits, kind, notice=notice)
    from .review_prompt_paragraphs import select_prompt_parameters
    prompt = build_turn_prompt(value['role'], find_session_form(packet), read_session_binding(packet)['capacity'],
        orchestration=find_base_orchestration(value), paragraphs=select_prompt_parameters(value['role'], logical, session_rules=PROTOCOL),
        common_version=protocol.find_common_prompt_version(parent['prompt']) if parent else None,
        repair_suffix=_select_repair_suffix(logical), refresh='problem_refresh' in logical)
    for entry in packet['evidence']:
        artifacts.put(entry['content'].encode('utf-8'), entry['artifact']['media_type'])
    record = dict(value, packet=protocol._put_json(artifacts, packet), packet_digest=packet['digest'], prompt=prompt,
        prompt_hash=prompt['rendered_prompt_hash'], route_digest=digest(route), context_state=protocol.route_state(records, artifacts, value['route_id']),
        assignment_policy=protocol.POLICY, history=[], session_id=chain['first_assignment_id'],
        history_assignment_id=parent.get('history_assignment_id') if replacement else parent['id'] if parent else None,
        reassessment_required=bool(logical.get('context_repair')), recorded_revision=revision, session_chain=chain,
        capacity_plan=members['capacity_plan'], capacity_plan_kind=kind, slot_id=slot or (parent.get('slot_id', parent['id']) if parent else value['id']))
    if replacement:
        record['capacity_replacement_of'] = parent['id']
        record['context'] = copy.deepcopy(parent['context'])
    from .review_codex_invocation import supports_route, KEY as INVOCATION_KEY, PROTOCOL as INVOCATION_PROTOCOL
    if supports_route(route):
        record[INVOCATION_KEY] = INVOCATION_PROTOCOL
    if replay:
        return [], record
    from .review_visual_sources import require_installed_renderer
    require_installed_renderer(records, artifacts, record)
    request = protocol.transport.request_body(route, prompt['text'], packet, [], order=())
    from .review_preflight import build_report, check_request
    preflight = check_request(build_report(records, artifacts, route, value['role'], prompt, packet, request,
        capacity_plan=plan, turn_kind='final' if kind == 'combine' else kind), request, packet=packet, artifacts=artifacts)
    if preview:
        return [], dict(record, preflight=preflight)
    forecast = describe_session_forecast(artifacts, route, inventory, plan, capacity.read_output_profile(records, route))
    return [immutable_record(records, 'review_assignment', value['id'], record)], dict(record, preflight=preflight,
        capacity_forecast=forecast, session_admission=dict(state='record', protocol=PROTOCOL,
            capacity_plan=plan, forecast=forecast))


def rebuild_session_state(records, artifacts, assignment, inventory, plan, route):
    """Advance one authenticated parent; induction authenticates the immutable prefix once."""
    from . import review_protocol as protocol
    from .errors import ResearchError
    packet = protocol._read_json(artifacts, assignment['packet'])
    attempt, output = protocol.read_assignment_output(records, artifacts, assignment['id'])
    if attempt is None or not protocol.authenticate_attempt(records, artifacts, assignment, attempt):
        raise ResearchError('review_invocation_unverified', TURN_BINDING_MESSAGE)
    require_final_capacity(records, artifacts, assignment, attempt)
    _, _, state = read_bound_session(artifacts, assignment)
    observation = _project_completed_turn(artifacts, assignment, packet, attempt, output)
    state = (build_session_state(inventory, [observation], route, plan, initial_state=state)['state']
             if observation is not None else copy.deepcopy(state))
    binding = read_session_binding(packet)
    if binding.get('page_plan_digest'):
        kind = find_session_form(packet)
        terminal = output.get('stage') not in ('request', 'material')
        state.setdefault('final_progress', {})[str(binding['final_turn'])] = dict(assignment_id=assignment['id'],
            attempt_id=attempt['id'], kind=kind, targets=binding.get('page', {}).get('targets', []),
            terminal=terminal, output=copy.deepcopy(output))
        if kind == 'combine' and terminal:
            state.setdefault('combinations', {})[output['block']] = copy.deepcopy(output)
            state.setdefault('final_blocks', {})[output['block']] = binding['block']['artifact']
            block = protocol._read_json(artifacts, binding['block']['artifact'])
            state.setdefault('final_catalog', {}).update((row['handle'], copy.deepcopy(row['value'])) for row in block['entries'])
        if observation is None:
            state['turns'].append(dict(number=binding['turn'], assignment_id=assignment['id'], attempt_id=attempt['id'], kind=kind))
    if output.get('stage') == 'material':
        material = protocol._read_json(artifacts, attempt['material'])
        state.update(material_state=material['material_state'],
            material_handles={key: row for key, row in material['handle_table'].items() if key.startswith('m')},
            material_request=material['material_request'])
    return state


def _authenticate_session_binding(records, artifacts, assignment, packet):
    """Rebuild a stored turn from its frozen plan and earlier observed bytes, never today's priors."""
    from . import review_protocol as protocol
    from .errors import ResearchError
    _require_bound_assignment(assignment)
    plan, inventory, state = read_bound_session(artifacts, assignment)
    chain = assignment['session_chain']
    binding = read_session_binding(packet)
    logical = protocol._read_json(artifacts, chain['logical'])
    assignments = records.get('review_assignment', {})
    root = assignments.get(chain['first_assignment_id'])
    _require_bound_assignment(root)
    route = records.get('review_route', {}).get(assignment['route_id'])
    _require_session_members(route, 'id')
    if (_describe_logical_fields(logical) != inventory['logical_fields']
            or any(chain[key] != root['session_chain'][key] for key in ('inventory', 'logical'))
            or assignment['capacity_plan'] != root['capacity_plan']
            or any(assignment[key] != root[key] for key in
                   ('role', 'reviewer_id', 'author_id', 'route_id', 'dossier_id'))):
        raise ResearchError('review_invocation_unverified', TURN_BINDING_MESSAGE)
    parent_id = assignment.get('history_assignment_id')
    parent = assignments.get(parent_id)
    if parent_id is not None:
        _require_bound_assignment(parent)
    if parent is not None and parent['recorded_revision'] >= assignment['recorded_revision']:
        raise ResearchError('review_invocation_unverified', TURN_BINDING_MESSAGE)
    before = (rebuild_session_state(records, artifacts, parent, inventory, plan, route) if parent_id else
              build_initial_session_state(records, artifacts, root, inventory, plan, route, logical))
    if (state != before or binding.get('plan_digest') != digest(plan)
            or binding.get('inventory_digest') != digest(inventory) or binding.get('state_digest') != digest(state)
            or binding.get('chain') != chain['first_assignment_id'] or binding.get('turn') != chain['turn']):
        raise ResearchError('review_invocation_unverified', TURN_BINDING_MESSAGE)
    kind = find_session_kind(packet)
    # The stored kind chooses which observations size later turns (derive_session_task_limits).
    if assignment['capacity_plan_kind'] != dict(request='request', final='final', combine='final').get(kind):
        raise ResearchError('review_invocation_unverified', TURN_BINDING_MESSAGE)
    if chain.get('final_page_plan'):
        return authenticate_final_binding(records, artifacts, assignment, packet)
    replacement = assignment.get('capacity_replacement_of')
    refusal = assignment.get('context', {}).get('output_refusal')
    if refusal is not None:
        _require_session_members(refusal, 'assignment_id')
    same_position = replacement or (refusal['assignment_id'] if refusal else None)
    prior = assignments.get(same_position) if same_position else parent
    if same_position:
        _require_bound_assignment(prior)
    expected_turn = prior['session_chain']['turn'] + (0 if same_position else 1) if prior else 1
    expected_kind = (find_session_kind(protocol._read_json(artifacts, prior['packet'])) if same_position else
                     'request' if prior is None or _list_remaining_units(inventory, state) or state['pending_requests'] else 'final')
    if chain['turn'] != expected_turn or kind != expected_kind or protocol.read_history(artifacts, assignment) != []:
        raise ResearchError('review_invocation_unverified', TURN_BINDING_MESSAGE)
    # Only observations preceding this assignment choose its allowance.
    if prior is not None:
        limits = derive_session_task_limits(records, artifacts, prior, kind,
                                            before_revision=assignment['recorded_revision'], replacement=bool(replacement))
    else:
        limits = capacity.derive_plan_task_limits(plan, kind)
    if refusal and not replacement:
        notice = packet.get('output_refusal')
        if not isinstance(notice, dict) or not notice:
            raise ResearchError('review_invocation_unverified', TURN_BINDING_MESSAGE)
        previous = protocol._read_json(artifacts, prior['packet'])
        limits = _derive_refusal_limits(plan, kind, previous, notice, route)
    if replacement and kind == 'request' and packet.get('output_refusal'):
        limits = capacity.derive_plan_task_limits(plan, kind, [dict(material_allowance=
            limits.get('level_material_allowance', limits['material_allowance']))],
            notice_characters=capacity.measure_capacity_value(route, packet['output_refusal']))
    if replacement and kind == 'final' and state.get('material_state', {}).get('pending'):
        frozen = protocol._read_json(artifacts, prior['packet'])
        limits['answer_bound'] = max(plan['task']['final']['smallest_answer_bound'],
                                     frozen['session_final']['capacity']['answer_bound'] // 2)
    expected = build_session_packet(artifacts, route, logical, chain['first_assignment_id'], chain['turn'],
                                    inventory, state, plan, limits, kind, notice=packet.get('output_refusal'))
    if expected != packet or not matches_turn_prompt(assignment, packet, logical):
        raise ResearchError('review_invocation_unverified', TURN_BINDING_MESSAGE)
    return True


def plan_turn_recovery(records, artifacts, assignment, outcome):
    """Return a smaller same-position request, or None at the pinned floor."""
    from . import review_protocol as protocol
    from .openness_reading_v7 import _PlanningArtifacts
    packet = protocol._read_json(artifacts, assignment['packet'])
    kind = find_session_kind(packet)
    binding = read_session_binding(packet)
    plan, inventory, state = read_bound_session(artifacts, assignment)
    material = kind == 'final' and state.get('material_state', {}).get('pending')
    if material:
        if binding['capacity']['answer_bound'] <= plan['task']['final']['smallest_answer_bound']:
            return None
    elif kind == 'request' and binding['capacity']['level_material_allowance'] <= plan['task']['request']['levels'][-1]['material_allowance']:
        return None
    value = {key: copy.deepcopy(assignment[key]) for key in
             ('id', 'route_id', 'dossier_id', 'role', 'reviewer_id', 'author_id', 'context')}
    from .review_continuation import _find_unused_id
    value['id'] = _find_unused_id(records, value['id'] + '-replacement', maximum=ASSIGNMENT_ID_CHARACTERS)
    if 'subject' in assignment:
        value['subject'] = assignment['subject']
    route = records['review_route'][assignment['route_id']]
    overlay = _PlanningArtifacts(artifacts)
    _, candidate = prepare_session_assignment(records, overlay, value, route,
        max(row['recorded_revision'] for row in records['review_assignment'].values()) + 1,
        parent=assignment, replacement=True, notice=packet.get('output_refusal'))
    if candidate is None:
        return None
    new = protocol._read_json(overlay, candidate['packet'])
    smaller = read_session_binding(new)['capacity']
    if (smaller['answer_bound'] >= binding['capacity']['answer_bound'] if material else
            kind == 'request' and smaller['level_material_allowance'] >= binding['capacity']['level_material_allowance']):
        return None
    payload = {key: value[key] for key in ('id', 'route_id', 'dossier_id', 'role', 'reviewer_id', 'author_id')}
    payload['context'] = dict(capacity_replacement=assignment['id'])
    return dict(kind='replace', reason='capacity', payload=payload, preflight=candidate['preflight'],
                details=dict(handler=kind + '_session_v4', material_allowance=smaller['material_allowance']))


def prepare_capacity_assignment(records, artifacts, value, revision, parent):
    from . import review_protocol as protocol, review_failure_class as failures
    from .errors import ResearchError
    from .operations import normalized_text
    from .review_refusal_replacement import NOT_SELECTED_MSG
    if (any(parent[key] != value[key] for key in ('role', 'dossier_id', 'route_id'))
            or any(normalized_text(parent[key]) != normalized_text(value[key]) for key in ('author_id', 'reviewer_id'))):
        raise ResearchError('review_source_continuation_mismatch', REPLACEMENT_BINDING_MESSAGE)
    assignments = records['review_assignment']
    latest = max((assignments[key] for key in failures.list_turn_assignments(records, parent)),
                 key=lambda row: row['recorded_revision'])
    if latest['id'] != parent['id']:
        raise ResearchError('review_assignment_replaced', NOT_SELECTED_MSG, dict(assignment_id=latest['id']))
    protocol.context_repair.require_current_slot(records, parent)
    planned = failures.plan_after_failure(records, artifacts, parent['id'])
    recovery = planned.get('details', {}).get('recovery', {})
    context = recovery.get('payload', {}).get('context', {})
    if (protocol.has_pending_invocation(records, parent['id']) or planned['state'] != 'replace'
            or recovery.get('reason') != 'capacity' or context.get('capacity_replacement') != parent['id']):
        raise ResearchError('invalid_review_context', REPLACEMENT_UNPLANNED_MESSAGE)
    return prepare_session_assignment(records, artifacts, value, records['review_route'][parent['route_id']], revision,
                                       parent=parent, replacement=True,
                                       notice=protocol._read_json(artifacts, parent['packet']).get('output_refusal'))


def require_writable_session_continuation(records, artifacts, assignment, payload):
    """Measure the next assignment using the already authenticated snapshot."""
    from .openness_reading_v7 import _PlanningArtifacts
    route = records['review_route'][assignment['route_id']]
    bound = read_bound_session(artifacts, assignment)
    plan, inventory, _ = bound
    state = rebuild_session_state(records, artifacts, assignment, inventory, plan, route)
    # The authenticated parent and its state belong to the caller's evaluation.
    # Use an overlay only once the trial starts writing its artifact references.
    _, candidate = prepare_session_assignment(records, _PlanningArtifacts(artifacts), payload, route,
        assignment['recorded_revision'] + 1, parent=assignment, replay_state=state, preview=True, bound_parent=bound)
    if candidate is None:
        from .errors import ResearchError
        raise ResearchError('review_route_capacity_below_floor', capacity.BELOW_FLOOR_MESSAGE)


def _derive_refusal_limits(plan, kind, frozen, notice, route):
    binding = read_session_binding(frozen)
    current = binding['capacity']
    limits = capacity.derive_plan_task_limits(plan, kind, [dict(material_allowance=current['level_material_allowance'])],
        notice_characters=capacity.measure_capacity_value(route, notice) if kind == 'request' else 0)
    limits['answer_bound'] = current['answer_bound']
    return limits


def prepare_refusal_assignment(records, artifacts, value, revision, prior, frozen, attempt_ids, diagnoses, *,
                               position_binding=None):
    from . import review_protocol as protocol, review_refusal_notice as notices, review_prompt_paragraphs as paragraphs
    from .operations import immutable_record
    _require_assignment_id(value['id'])
    value = bind_child_identity(value, prior)
    plan, inventory, state = read_bound_session(artifacts, prior)
    kind = find_session_kind(frozen)
    plan_kind = 'final' if kind == 'combine' else kind
    route = records['review_route'][prior['route_id']]
    logical = protocol._read_json(artifacts, prior['session_chain']['logical'])
    chain = prior['session_chain']
    def prepare_turn(notice):
        limits = _derive_refusal_limits(plan, plan_kind, frozen, notice, route)
        if prior['session_chain'].get('final_page_plan'):
            packet = copy.deepcopy(frozen)
            packet.pop('output_refusal', None)
            from .review_refusal_notice import attach_refusal_notice
            packet = attach_refusal_notice(packet, notice)
        else:
            packet = build_session_packet(artifacts, route, logical, chain['first_assignment_id'], chain['turn'],
                                       inventory, state, plan, limits, kind, notice=notice)
        chosen = paragraphs.select_prompt_parameters(prior['role'], logical, session_rules=PROTOCOL)
        # The refusal paragraph follows the selected paragraphs in their registered order.
        chosen[paragraphs.PARAMETER] = (chosen.get(paragraphs.PARAMETER, [])
                                        + [paragraphs.REFUSAL_PARAGRAPH_VERSIONS[notice['form']]])
        prompt = build_turn_prompt(prior['role'], find_session_form(packet), read_session_binding(packet)['capacity'],
                                    orchestration=find_base_orchestration(prior), paragraphs=chosen,
                                    common_version=protocol.find_common_prompt_version(prior['prompt']),
                                    repair_suffix=_select_repair_suffix(logical), refresh='problem_refresh' in logical)
        return packet, prompt
    selected = notices.select_refusal_notice(records, artifacts, attempt_ids, diagnoses=diagnoses, route=route,
        role=prior['role'], prompt=None, packet=None, prepare_turn=prepare_turn, task_characters=None,
        task_limit_characters=read_session_binding(frozen)['capacity']['request_bound'],
        capacity_plan=plan, turn_kind=plan_kind, forms=('bounded',), position_binding=position_binding)
    if selected['notice'] is None:
        from .errors import ResearchError
        raise ResearchError('review_input_too_large', notices.NOTICE_DELIVERY_MSG, dict(trials=selected['trials']))
    from .review_excerpts import read_sent_packet
    packet = read_sent_packet(selected['request'])
    for row in packet['evidence']:
        artifacts.put(row['content'].encode('utf-8'), row['artifact']['media_type'])
    record = copy.deepcopy(prior)
    record.pop('capacity_replacement_of', None)
    record.update(value, packet=protocol._put_json(artifacts, packet), packet_digest=packet['digest'],
        prompt=selected['prompt'], prompt_hash=selected['prompt']['rendered_prompt_hash'], history=[], recorded_revision=revision)
    return [immutable_record(records, 'review_assignment', value['id'], record)], record


def authenticate_session_binding(records, artifacts, assignment, packet):
    from .evaluation import Evaluation
    evaluation = Evaluation.of(records, artifacts)
    return evaluation.once(('session_v4_binding', digest(assignment), digest(packet)),
        lambda: _authenticate_session_binding(evaluation.records, evaluation, assignment, packet))


def project_scientific_delivery(artifacts, assignment, delivery):
    """Reconstruct role records only after their classified units actually arrived."""
    plan, inventory, state = read_bound_session(artifacts, assignment)
    if _list_remaining_units(inventory, state):
        return delivery
    from .review_delivered_evidence import project_delivery_view
    scientific = copy.deepcopy(inventory['logical_fields'])
    scientific['evidence'] = []
    projected = project_delivery_view({}, artifacts, [scientific])
    result = dict(delivery, packets=delivery['packets'] + [scientific],
                  carried_records=delivery['carried_records'] + [row for row in projected['carried_records']
                                                                if row not in delivery['carried_records']])
    from .review_supplied_evidence import register_supplied_artifact
    for row in state.get('material_handles', {}).values():
        register_supplied_artifact(result['artifacts'], row['reference'])
    for value in state.get('combinations', {}).values():
        register_supplied_artifact(result['artifacts'], artifacts.put(serialize_record(value), 'application/json'))
    return result


def derive_session_task_limits(records, artifacts, assignment, kind, *, before_revision=None, replacement=False):
    """Read this frozen session's attempts once, without grouping every ancestor position."""
    from . import review_protocol as protocol, review_failure_class as failures
    plan = capacity.read_chain_capacity_plan(artifacts, assignment)
    root = assignment['session_chain']['first_assignment_id']
    members = {key: row for key, row in records.get('review_assignment', {}).items()
               if row.get('session_chain', {}).get('first_assignment_id') == root
               and (before_revision is None or row['recorded_revision'] < before_revision)}
    observations = []
    for attempt in sorted(records.get('review_attempt', {}).values(), key=lambda row: row['recorded_revision']):
        member = members.get(attempt['assignment_id'])
        if member is None or before_revision is not None and attempt['recorded_revision'] >= before_revision:
            continue
        _require_session_members(member, 'capacity_plan_kind')
        if member['capacity_plan_kind'] != kind:
            continue
        packet = protocol._read_json(artifacts, member['packet'])
        binding = read_session_binding(packet)
        if kind == 'final' and binding.get('material_request'):
            continue
        outcome = failures.classify_attempt(records, artifacts, attempt)
        if outcome and outcome['outcome'] == 'response_over_limit' and not replacement:
            continue
        observations.append(dict(output_tokens=(attempt.get('usage') or {}).get('output_tokens'),
            outcome=outcome['outcome'] if outcome else None,
            material_allowance=binding['capacity']['level_material_allowance']))
    return capacity.derive_plan_task_limits(plan, kind, observations)


def describe_session_forecast(artifacts, route, inventory, plan, profile):
    """Forecast discovery/frame turns separately from requested source deliveries."""
    forecast = capacity.describe_capacity_forecast(plan, route, profile, turn_kind='request')
    allowance = plan['task']['request']['material_allowance']
    state = build_session_state(inventory, [], route, plan)['state']
    turns = 0
    # Every session starts with one request, even when its frame needs no units.
    while turns == 0 or _list_remaining_units(inventory, state):
        now, evidence, _ = _pack_session_deliveries(artifacts, route, inventory, state, plan, allowance)
        state['deliveries'].extend(row for row in now if 'unit' in row)
        turns += 1
        if not now:
            break
    requested = [row for row in inventory['units'] if row['kind'] == 'fulltext' or row['kind'] == 'abstract'
                 and not any(other['kind'] == 'fulltext' and other['identity'].get('version_id') == row['identity'].get('version_id')
                             for other in inventory['units'])]
    # Forecast-only requests arrive in batches of the response request cap.
    queue = list(requested)
    while queue or state['pending_requests']:
        while queue and len(state['pending_requests']) < RESPONSE_REQUESTS:
            unit = queue.pop(0)
            state['pending_requests'].append(dict(id=unit['handle'], kind='unit', unit=unit['handle']))
        now, evidence, _ = _pack_session_deliveries(artifacts, route, inventory, state, plan, allowance)
        if not now:
            break
        state['deliveries'].extend(row for row in now if 'unit' in row)
        complete = {row['request_id'] for row in now if row.get('request_complete')}
        state['pending_requests'] = [row for row in state['pending_requests'] if row['id'] not in complete]
        turns += 1
    row = forecast['by_kind']['request']
    row['turns'] = turns
    share = row['at_or_over_limit_share']
    row['expected_calls'] = turns / (1 - share) if share is not None and share < 1 else None
    forecast['turns'] = sum(row['turns'] for row in forecast['by_kind'].values())
    calls = [row['expected_calls'] for row in forecast['by_kind'].values()]
    forecast['expected_calls'] = sum(calls) if all(value is not None for value in calls) else None
    forecast['basis'] = 'initial_frame_then_requested_sources'
    return forecast


def describe_history_status(records, assignment):
    """Current contamination propagates separately from immutable prefix authentication."""
    identifiers = set()
    current = records.get('review_assignment', {}).get(assignment.get('history_assignment_id'))
    unverified = False
    while current is not None and current['id'] not in identifiers:
        identifiers.add(current['id'])
        unverified = unverified or current['context_state']['context_status'] != 'verified_for_route'
        current = records.get('review_assignment', {}).get(current.get('history_assignment_id'))
    if any(row['assignment_id'] in identifiers and row['kind'] == 'contamination'
           for row in records.get('review_context_event', {}).values()):
        return 'contaminated'
    return 'unverified' if unverified else None


def describe_target_labels(route, targets):
    """Fit target labels into the frame; complete target fields travel as required units."""
    def describe(length):
        rows = []
        for target in targets:
            label = target['label']
            row = dict(handle=target['handle'], label=label)
            if target['id'] != label:
                row.update(label=label[:length], id=target['id'])
            rows.append(row)
        return rows
    rows = describe(120)
    if capacity.measure_capacity_value(route, rows) <= 6000:
        return rows
    if capacity.measure_capacity_value(route, describe(0)) > 6000:
        return dict(count=len(targets), first=targets[0]['handle'], last=targets[-1]['handle'])
    low, high = 0, 120
    while low < high:
        middle = (low + high + 1) // 2
        if capacity.measure_capacity_value(route, describe(middle)) <= 6000:
            low = middle
        else:
            high = middle - 1
    return describe(low)


def read_session_binding(packet):
    return packet.get('session_turn', packet.get('session_final', packet.get('session_combine', {})))


def build_composition_packet(artifacts, assignment, packet):
    """The full frozen scientific context is a validation view, never a request."""
    from . import review_protocol as protocol
    _, inventory, _ = read_bound_session(artifacts, assignment)
    logical = protocol._read_json(artifacts, assignment['session_chain']['logical'])
    table = build_handle_packet(packet, artifacts, assignment)['handle_table']
    evidence = list(logical.get('evidence', [])) + [
        {'artifact': unit['artifact'], 'content': table[unit['handle']]['text'], 'encoding': 'utf-8'}
        for unit in inventory['units'] if table[unit['handle']]['parts']]
    return dict(logical, session_targets=inventory['targets'], handle_table=table, evidence=evidence)


def build_final_inputs(artifacts, route, logical, chain, number, inventory, state, plan, limits, room):
    """Charge an excerpt and its source row to every target that it names."""
    from . import review_condensing as condensing
    full = build_session_packet(artifacts, route, logical, chain, number, inventory, state, plan,
                                dict(limits, material_allowance=10 ** 18), 'final')
    binding = full['session_final']
    catalog = {}
    def describe(handle, value):
        catalog[handle] = copy.deepcopy(value)
        return dict(handle=handle, characters=capacity.measure_capacity_value(route, value) + 1)
    targets = []
    for target in plan['targets']['handles']:
        entries = describe_session_final_entries(state, [target])
        units = {row['unit'] for row in entries}
        targets.append(dict(handle=target, entries=[describe(row['handle'], row) for row in entries],
            rows=[describe('source:' + row['handle'], row) for row in binding['sources'] if row['handle'] in units]))
    notes = max(0, capacity.measure_capacity_value(route, binding['notes']) - capacity.measure_capacity_value(route, None))
    minimum = capacity.EXCERPT_TEXT_CHARACTERS + capacity.EXCERPT_FIELDS_CHARACTERS
    pool = []
    if notes + minimum > room:
        # Notes are bounded text but can exceed a ratio-reduced page. Segment
        # them at whole serialized entries before the shared condensing policy.
        text = binding['notes'] or ''
        offset = 0
        while offset < len(text):
            low, high = offset, len(text)
            while low < high:
                end = (low + high + 1) // 2
                value = dict(handle='notes:' + digest(text) + '@' + str(offset), text=text[offset:end])
                if capacity.measure_capacity_value(route, value) + 1 <= room - minimum:
                    low = end
                else:
                    high = end - 1
            if low == offset:
                from .errors import ResearchError
                raise ResearchError('review_route_capacity_below_floor', capacity.BELOW_FLOOR_MESSAGE)
            value = dict(handle='notes:' + digest(text) + '@' + str(offset), text=text[offset:low])
            pool.append(describe(value['handle'], value))
            offset = low
        notes = 0
    return dict(page_room=room, target_allowance=limits['target_allowance'], target_minimum=minimum,
                notes=notes, pool=pool, targets=targets, closing_room=room,
                row_characters=capacity.DECISION_ROW_CHARACTERS, condense=condensing.plan_condensing), catalog, full


def prepare_final_packet(records, artifacts, prior, route, logical, number, inventory, state, plan, limits,
                         *, replacement=False):
    """Freeze or replay successors using shared page packing and progress rules."""
    from . import review_final_pages as pages, review_condensing as condensing
    from .openness_final_pages import (measure_page_room, read_page_plan, publish_page_plan,
                                      list_final_progress)
    from .errors import ResearchError
    chain = prior['session_chain']['first_assignment_id']
    reference = prior['session_chain'].get('final_page_plan')
    room = measure_page_room(records, artifacts, prior, plan, limits)
    inputs, catalog, full = build_final_inputs(artifacts, route, logical, chain, number, inventory, state, plan, limits, room)
    floor = min(row['material_allowance'] for row in plan['task']['final']['levels'])
    currency = dict(packet_digest=digest(logical), inventory_digest=digest(inventory))
    attempt_id = state['turns'][-1]['attempt_id'] if state['turns'] else None
    if reference is None:
        initial_inputs = inputs
        if replacement:
            historical = dict(records, review_attempt={key: row for key, row in records['review_attempt'].items()
                if row['recorded_revision'] < prior['recorded_revision']})
            old_limits = derive_session_task_limits(historical, artifacts, prior, 'final')
            old_room = measure_page_room(historical, artifacts, prior, plan, old_limits)
            initial_inputs, _, _ = build_final_inputs(artifacts, route, logical, chain, number, inventory, state, plan, old_limits, old_room)
        turns = pages.plan_final_turns(**initial_inputs)
        if not replacement and len(turns) == 1 and turns[0]['kind'] == 'final':
            return build_session_packet(artifacts, route, logical, chain, number, inventory, state, plan, limits, 'final'), None
        page_plan = pages.build_page_plan(chain, digest(plan), digest(state), currency, turns)
        reference = publish_page_plan(artifacts, page_plan)
    else:
        page_plan = read_page_plan(artifacts, reference)
    completed, pending = list_final_progress(state, page_plan)
    if replacement:
        frozen = json.loads(artifacts.read(prior['packet']))
        selected = read_session_binding(frozen).get('final_turn', page_plan['turns'][0]['number'])
        turns = pages.split_final_turn(page_plan['turns'], selected, floor, condensing.plan_condensing, pending=pending)
        if turns is None:
            return None, None
        attempt = max((row for row in records['review_attempt'].values() if row['assignment_id'] == prior['id']),
                      key=lambda row: row['recorded_revision'])
        attempt_id = attempt['id']
        cause = dict(kind='split', turn=selected, attempt_id=attempt_id, completed=completed, pending=pending)
        page_plan = pages.build_page_plan(chain, digest(plan), digest(state), currency, turns, digest(page_plan), cause)
        reference = publish_page_plan(artifacts, page_plan, previous=reference)
    previous = page_plan['turns'][-1]['context']
    frozen = json.loads(artifacts.read(prior['packet']))
    old = read_session_binding(frozen)['capacity']
    cause_kind = ('level' if limits['target_allowance'] != previous['target_allowance']
                  or (find_session_kind(frozen) != 'request'
                      and limits['material_allowance'] != old['level_material_allowance']) else
                  'ratio' if room < previous['page_room'] else None)
    if cause_kind is not None:
        completed, pending = list_final_progress(state, page_plan)
        cause = dict(kind=cause_kind, attempt_id=attempt_id, page_room=room, completed=completed, pending=pending)
        if cause_kind == 'level':
            cause['target_allowance'] = limits['target_allowance']
        turns = pages.repack_final_turns(page_plan['turns'], completed, pending, room, limits['target_allowance'],
            **{key: value for key, value in inputs.items() if key not in ('page_room', 'target_allowance')})
        page_plan = pages.build_page_plan(chain, digest(plan), digest(state), currency, turns, digest(page_plan), cause)
        reference = publish_page_plan(artifacts, page_plan, previous=reference)
    while True:
        completed, pending = list_final_progress(state, page_plan)
        selected = next((turn for turn in page_plan['turns'] if turn['number'] not in completed), None)
        if selected is None:
            raise ResearchError('review_resampling_forbidden',
                'Every turn of this final has its answer, the closing turn included, so the session takes no '
                'further turn. Run review-series on an assignment of the session and take the step that it plans.')
        turns = pages.prepare_final_turn(page_plan['turns'], selected['number'], room, limits['target_allowance'],
            floor, condensing.plan_condensing, pending=pending, completed=completed, inputs=inputs)
        if turns == page_plan['turns']:
            break
        if turns is None:
            raise ResearchError('review_route_capacity_below_floor', capacity.BELOW_FLOOR_MESSAGE)
        cause = dict(kind='split', turn=selected['number'], attempt_id=attempt_id, completed=completed, pending=pending)
        page_plan = pages.build_page_plan(chain, digest(plan), digest(state), currency, turns, digest(page_plan), cause)
        reference = publish_page_plan(artifacts, page_plan, previous=reference)
    return build_paged_packet(artifacts, route, logical, chain, number, inventory, state, plan, limits,
                              page_plan, selected, catalog, full, inputs=inputs), reference


def build_paged_packet(artifacts, route, logical, chain, number, inventory, state, plan, limits,
                       page_plan, turn, catalog, full, *, inputs=None):
    from . import review_composition as composition, review_condensing as condensing
    from .openness_final_pages import list_page_outputs
    from .review_final_pages import _apply_completed
    if inputs is None:
        inputs, _, _ = build_final_inputs(artifacts, route, logical, chain, number, inventory, state, plan, limits,
                                           turn['context']['page_room'])
    temporary = dict(capacity_plan=dict(protocol=capacity.CAPACITY_PLAN_PROTOCOL, digest=digest(plan),
        artifact=artifacts.put(serialize_record(plan), 'application/json')), session_chain=dict(
        logical=artifacts.put(serialize_record(logical), 'application/json'),
        inventory=artifacts.put(serialize_record(inventory), 'application/json'),
        state=artifacts.put(serialize_record(state), 'application/json')))
    envelope = build_composition_packet(artifacts, temporary, full)
    rule = composition.find_rule(logical['role'], envelope)
    rows = composition.build_decision_rows(rule, envelope, list_page_outputs(state),
        measure=lambda row: capacity.measure_capacity_value(route, dict(handle='decision:' + row[0], value=row)) + 1)
    catalog.update(('decision:' + row[0], dict(handle='decision:' + row[0], value=row)) for row in rows)
    catalog.update(state.get('combinations', {}))
    def values(entries):
        return [copy.deepcopy(catalog[row['handle']]) for row in entries]
    binding = full['session_final']
    binding.update(page_plan_digest=digest(page_plan), final_turn=turn['number'])
    binding['capacity']['material_allowance'] = min(turn['context']['page_room'], limits['material_allowance'])
    binding['capacity']['level_material_allowance'] = limits['material_allowance']
    binding['fill'] = []
    if turn['kind'] == 'combine':
        binding = {key: binding[key] for key in ('protocol', 'chain', 'turn', 'plan_digest', 'inventory_digest',
                    'state_digest', 'capacity', 'page_plan_digest', 'final_turn')}
        block = copy.deepcopy(turn['block'])
        block['artifact'] = artifacts.put(serialize_record(dict(block=turn['block'], entries=[dict(handle=row['handle'], value=value)
            for row, value in zip(turn['entry_sizes'], values(turn['entry_sizes']))])), 'application/json')
        binding['block'] = dict(block, entries=values(turn['entry_sizes']))
        binding['capacity']['answer_bound'] = condensing.describe_combination_bound(turn['block'], limits['answer_bound'])
        packet = {key: full[key] for key in ('protocol', 'role', 'reviewer_id', 'dossier_id', 'source_rules')}
        packet.update(session_combine=binding, evidence=[])
        if 'subject' in full:
            packet['subject'] = copy.deepcopy(full['subject'])
    else:
        completed = [row for row in page_plan['turns'] if state.get('final_progress', {}).get(str(row['number']), {}).get('terminal')]
        pool = _apply_completed(inputs['pool'], completed, 'pool')
        binding.update(notes=binding['notes'] if not inputs['pool'] else None,
                       entries=values(pool), sources=[])
        if turn['kind'] in ('page', 'final'):
            paged = [row for row in page_plan['turns'] if row['kind'] in ('page', 'final')]
            binding['page'] = dict(number=paged.index(turn) + 1, pages=len(paged), targets=turn['targets'])
            fields = composition.list_page_pointers(rule, envelope, turn['targets'])
            if fields:
                binding['page']['fields'] = fields
            binding['targets'] = describe_target_labels(route, [row for row in inventory['targets'] if row['handle'] in turn['targets']])
            for part in turn['parts']:
                binding['entries'].extend(values(part['entries']))
                binding['sources'].extend(values(part['rows']))
            original = {row['handle'] for item in page_plan['turns'] for target in item['inputs'] for row in target['entries']}
            original.update(state.get('final_catalog', {}))
            binding['entries'].extend(row for row in describe_session_final_entries(state, turn['targets']) if row['handle'] not in original)
            sources = {row.get('unit') for row in binding['entries']}
            binding['sources'].extend(dict(handle=row['handle'], kind=row['kind'], label=row['label'])
                for row in inventory['units'] if row['handle'] in sources and not any(source['handle'] == row['handle'] for source in binding['sources']))
        else:
            binding = {key: binding[key] for key in ('protocol', 'chain', 'turn', 'plan_digest', 'inventory_digest',
                        'state_digest', 'capacity', 'page_plan_digest', 'final_turn', 'frame')}
            binding['closing'] = dict(rows=[row['value'] if isinstance(row, dict) and row.get('handle', '').startswith('decision:') else row for row in values(turn['rows'])])
        if state.get('material_request'):
            binding['material_request'] = copy.deepcopy(state['material_request'])
        full['session_final'] = binding
        packet = full
        now = []
        if state['pending_requests']:
            used = measure_final_material(route, binding)
            now, evidence, _ = _pack_session_deliveries(artifacts, route, inventory, state, plan,
                max(0, binding['capacity']['material_allowance'] - used - 256))
            binding['plan'] = dict(now=now, pending=dict(requests=len(state['pending_requests'])))
            packet['evidence'] = evidence
        allowances = describe_session_allowances(route, state, plan, limits)
        binding['request_allowances'] = dict(excerpts=0)
        binding['request_allowances']['passages'] = dict(allowances['passages'], available=measure_passage_room(state, now))
        # The full allowance history stays in the state artifact. A page exposes
        # only its own target grants, within the pre-existing allowance reserve.
        binding['request_allowances']['targets'] = [dict(row, remaining=max(0, row['characters'] - row['used']))
            for row in state['allowances']['targets']
            if turn['kind'] in ('page', 'final') and row['handle'] in turn['targets']]
        # A held turn keeps its frozen material grant, but its successor plans with the measured room.
        measured_room = inputs['closing_room'] if turn['kind'] == 'closing' else inputs['page_room']
        room = min(binding['capacity']['material_allowance'], measured_room)
        note_framing = capacity.measure_capacity_value(route, '') - capacity.measure_capacity_value(route, None)
        planner_room = room - inputs['target_minimum'] - note_framing
        material_room = room - measure_final_material(route, dict(binding, notes=''))
        binding['request_allowances']['notes'] = min(allowances['notes'], max(1, min(planner_room, material_room)))
    packet.pop('digest', None)
    packet['digest'] = digest(packet)
    return packet


def measure_final_material(route, binding):
    return capacity.measure_capacity_value(route, {key: binding.get(key) for key in
        (('closing',) if 'closing' in binding else ('notes', 'entries', 'sources', 'fill'))})


def validate_final_turn_output(records, artifacts, assignment, packet, output, *, expanded=False):
    """Validate a written or explicitly expanded answer before cross-page validation."""
    if not expanded:
        from .review_handles import expand_handles
        output = expand_handles(packet, output, artifacts=artifacts, assignment=assignment, raw=True)
    from . import review_composition as composition, review_condensing as condensing
    binding = read_session_binding(packet)
    route = records['review_route'][assignment['route_id']]
    if 'session_combine' in packet:
        block = dict(binding['block'], entries=[row.get('handle', row.get('block')) for row in binding['block']['entries']])
        condensing.validate_combination(block, output, lambda value: capacity.measure_capacity_value(route, value),
                                       binding['capacity']['answer_bound'])
    elif isinstance(output, dict) and output.get('stage') == 'request':
        adapted = copy.deepcopy(packet)
        adapted['session_turn'] = dict(binding, allowances=binding['request_allowances'],
                                       plan=binding.get('plan', dict(now=[])))
        from .review_refusal import refuse_unreadable_answer
        with refuse_unreadable_answer():
            validate_request_output(artifacts, assignment, adapted, output)
            _require_turn(not output['excerpts'], path='/excerpts', items=len(output['excerpts']), bound=0)
    else:
        _, _, state = read_bound_session(artifacts, assignment)
        completed = {row['request_id'] for row in binding.get('plan', {}).get('now', [])
                     if row.get('request_complete')}
        pending = [row['id'] for row in state['pending_requests'] if row['id'] not in completed]
        _require_turn(not pending, path='', pending_requests=pending)
        envelope = build_composition_packet(artifacts, assignment, packet)
        rule = composition.find_rule(assignment['role'], envelope)
        if 'closing' in binding:
            composition.validate_closing_output(rule, envelope, binding['closing']['rows'], output)
        else:
            composition.validate_page_output(rule, envelope, binding['page']['targets'], output)


def list_composite_turns(records, artifacts, assignment):
    from . import review_protocol as protocol
    from .openness_final_pages import read_page_plan
    if not assignment.get('session_chain', {}).get('final_page_plan'):
        return None
    first = assignment['session_chain']['first_assignment_id']
    members = sorted((row for row in records['review_assignment'].values()
        if row.get('session_chain', {}).get('first_assignment_id') == first), key=lambda row: row['recorded_revision'])
    latest = members[-1]
    plan = read_page_plan(artifacts, latest['session_chain']['final_page_plan'])
    selected = {}
    for row in members:
        binding = read_session_binding(protocol._read_json(artifacts, row['packet']))
        attempt, output = protocol.read_assignment_output(records, artifacts, row['id'])
        if 'final_turn' in binding and output is not None and output.get('stage') not in ('request', 'material'):
            selected[binding['final_turn']] = row['id']
    turns = dict(chain=first, pages=[], condensing=[], closing=None)
    for turn in plan['turns']:
        row = dict(assignment_id=selected.get(turn['number'], latest['id']), targets=turn['targets'])
        if turn['kind'] in ('page', 'final'):
            turns['pages'].append(row)
        elif turn['kind'] == 'combine':
            turns['condensing'].append(row)
        else:
            turns['closing'] = row
    turns['packet'] = build_composition_packet(artifacts, latest, protocol._read_json(artifacts, latest['packet']))
    return turns


def read_composite_currency(records, artifacts, assignment):
    from . import review_protocol as protocol
    from .errors import ResearchError
    from .openness_final_pages import read_page_plan
    _, inventory, _ = read_bound_session(artifacts, assignment)
    _require_bound_references(assignment, 'packet')
    _require_bound_references(assignment['session_chain'], 'logical')
    _require_session_members(assignment['session_chain'], 'final_page_plan')
    logical = protocol._read_json(artifacts, assignment['session_chain']['logical'])
    reference = assignment['session_chain']['final_page_plan']
    _require_session_members(reference, 'digest')
    _require_bound_references(reference, 'artifact')
    page_plan = read_page_plan(artifacts, reference)
    currency = dict(packet_digest=digest(logical), inventory_digest=digest(inventory))
    if (currency != page_plan.get('currency') or read_session_binding(protocol._read_json(
            artifacts, assignment['packet'])).get('page_plan_digest') != digest(page_plan)):
        raise ResearchError('review_invocation_unverified', TURN_BINDING_MESSAGE)
    return currency


def validate_composite_output(records, artifacts, assignment, packet, output):
    from .research_decisions import validate_review_contract
    from .errors import ResearchError
    from .review_refusal import refuse_unreadable_answer
    try:
        with refuse_unreadable_answer():
            validate_review_contract(records, artifacts, assignment, output, composed=True)
    except ResearchError as error:
        error.details = dict(error.details or {}, composed_review=True)
        raise


def prepare_closing_composite(records, artifacts, assignment, attempt, output):
    from . import review_composite, review_protocol as protocol
    from .operations import immutable_record
    packet = protocol._read_json(artifacts, assignment['packet'])
    if 'closing' not in packet.get('session_final', {}) or output.get('stage') in ('request', 'material'):
        return []
    from .review_failure_class import find_output_cap_signal
    if find_output_cap_signal(records, artifacts, assignment, attempt) is not None:
        return []
    from .review_handles import expand_observed_output
    output = expand_observed_output(packet, output, attempt, artifacts=artifacts, assignment=assignment)
    validate_final_turn_output(records, artifacts, assignment, packet, output, expanded=True)
    composite, (reference, content) = review_composite.prepare_composite(records, artifacts, assignment, attempt, output)
    artifacts.put(content, reference['media_type'])
    return [immutable_record(records, 'review_composite', composite['id'], composite)]


def find_session_form(packet):
    binding = read_session_binding(packet)
    return ('combine' if 'session_combine' in packet else 'closing' if 'closing' in binding else
            'page' if 'page' in binding else find_session_kind(packet))


def authenticate_final_binding(records, artifacts, assignment, packet):
    """Reproduce the page plan and request at this assignment's immutable edge."""
    from . import review_protocol as protocol
    from .openness_reading_v7 import _PlanningArtifacts
    from .errors import ResearchError
    _require_bound_assignment(assignment)
    _require_session_members(assignment['session_chain'], 'final_page_plan')
    historical = {kind: {key: row for key, row in rows.items()
                        if row.get('recorded_revision', 0) < assignment['recorded_revision']}
                  for kind, rows in records.items()}
    refusal = assignment['context'].get('output_refusal')
    if refusal is not None:
        _require_session_members(refusal, 'assignment_id')
    origin = assignment.get('capacity_replacement_of') or (refusal and refusal['assignment_id']) or assignment.get('history_assignment_id')
    prior = historical.get('review_assignment', {}).get(origin)
    _require_bound_assignment(prior)
    route = historical.get('review_route', {}).get(prior['route_id'])
    _require_session_members(route, 'id')
    frozen = protocol._read_json(artifacts, prior['packet'])
    if assignment.get('context', {}).get('output_refusal') and not assignment.get('capacity_replacement_of'):
        notice = packet.get('output_refusal')
        if not isinstance(notice, dict) or not notice:
            raise ResearchError('review_invocation_unverified', TURN_BINDING_MESSAGE)
        expected = copy.deepcopy(frozen)
        expected.pop('output_refusal', None)
        from .review_refusal_notice import attach_refusal_notice
        expected = attach_refusal_notice(expected, notice)
        if (expected != packet or assignment['session_chain'] != prior['session_chain']
                or not matches_turn_prompt(assignment, packet,
                                           protocol._read_json(artifacts, assignment['session_chain']['logical']))):
            raise ResearchError('review_invocation_unverified', TURN_BINDING_MESSAGE)
        return True
    value = {key: copy.deepcopy(assignment[key]) for key in
             ('id', 'route_id', 'dossier_id', 'role', 'reviewer_id', 'author_id', 'context')}
    if 'subject' in assignment:
        value['subject'] = assignment['subject']
    # Preparation consults no current priors for its packet or page plan. The
    # report is intentionally skipped during verification.
    _, rebuilt = prepare_session_assignment(historical, _PlanningArtifacts(artifacts), value,
        route, assignment['recorded_revision'], parent=prior,
        replacement=bool(assignment.get('capacity_replacement_of')), replay=True,
        replay_state=json.loads(artifacts.read(assignment['session_chain']['state'])),
        notice=frozen.get('output_refusal') if assignment.get('capacity_replacement_of') else None)
    if rebuilt is None or any(rebuilt[key] != assignment[key] for key in ('packet', 'session_chain', 'prompt')):
        raise ResearchError('review_invocation_unverified', TURN_BINDING_MESSAGE)
    return True


# The member message of output_refusal.composed_finding, which the closing turn reads when it comes again.
COMPOSED_REFUSAL_TEXT = (
    'The harness composed the final answer from the page answers and the closing answer, and a check refused the '
    'composed answer. check names that check, and path the place in the composed answer, which is empty for the '
    'answer as a whole. closing_answer is the closing answer that was composed; where it has more than 2,000 '
    'characters as the request carries it, it is {head,sha256,characters,complete:false}, and head holds its '
    'start. decision_row, where present, is the row of the target whose page answer holds that place. This turn '
    'changes the closing answer alone, and the page answers stay as they are. Where the place is a member that '
    'the closing answer gives or a binding that it names, correct it. Where decision_row is present, the place '
    'belongs to the page answer of that row, and a closing answer passes only if the check can hold with that '
    'page answer as it is. Return the complete closing answer.')


def describe_composed_refusal(records, artifacts, attempt, check, path):
    from . import review_protocol as protocol, review_composition as composition
    from .openness_final_pages import list_page_outputs
    certificate = records.get('review_output_refusal', {}).get(attempt['id'], {})
    if not certificate.get('details', {}).get('composed_review'):
        return None
    assignment = records['review_assignment'][attempt['assignment_id']]
    packet = protocol._read_json(artifacts, assignment['packet'])
    if 'closing' not in packet.get('session_final', {}):
        return None
    result = dict(check=check, path=path, closing_answer=protocol._read_json(artifacts, attempt['output']),
                  message=COMPOSED_REFUSAL_TEXT)
    _, _, state = read_bound_session(artifacts, assignment)
    outputs = list_page_outputs(state)
    envelope = build_composition_packet(artifacts, assignment, packet)
    rule = composition.find_rule(assignment['role'], envelope)
    rows = composition.build_decision_rows(rule, envelope, outputs)
    for row, item in zip(rows, [item for page in outputs for item in page['output']['targets']]):
        if any(path == field or path.startswith(field + '/') for field in item['fields']):
            result['decision_row'] = row
            break
    return result



def require_final_capacity(records, artifacts, assignment, attempt):
    from .review_failure_class import classify_final_capacity_attempt
    from .errors import ResearchError
    if assignment.get('session_chain') and classify_final_capacity_attempt(records, artifacts, attempt) is not None:
        raise ResearchError('review_output_over_cap', FINAL_CAPACITY_MESSAGE)
