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
LABEL_CHARACTERS = 120
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
    for row in frame_units:
        append_unit(row['kind'], row['identity'], row['artifact'], row.get('label', row['kind']))
    return dict(inventory=inventory, artifact=_describe_json(inventory), bodies=bodies)


def project_session_turn(inventory, observation):
    """Project one authenticated observation, without storage or earlier state.

    Observation: {number, assignment_id, attempt_id, deliveries, output}.
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
        unit = units[row['unit']]
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
        completed_requests=list(dict.fromkeys(row['request_id'] for row in deliveries
                                              if row.get('request_complete'))),
        passage_characters=sum(row['end'] - row['start'] for row in observation['deliveries']
                               if row.get('request_kind') == 'passage'))


def build_session_state(inventory, turns, route, plan):
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
            passages=dict(characters=review_excerpts.SESSION_PASSAGE_CHARACTERS, used=0)))
    allowances = {row['handle']: row for row in state['allowances']['targets']}
    units = {row['handle']: row for row in inventory['units']}
    delivered_sources = set()
    pending = {}
    for observation in turns:
        projected = project_session_turn(inventory, observation)
        number = projected['turn']['number']
        state['turns'].append(projected['turn'])
        state['deliveries'].extend(dict(row, turn=number) for row in projected['deliveries'])
        for row in projected['deliveries']:
            unit = units[row['unit']]
            if unit['kind'] in ('abstract', 'fulltext'):
                delivered_sources.add(unit['identity']['version_id'])
        state['notes'].append(projected['notes'])
        state['latest_notes'] = projected['notes']['text']
        for entry in projected['excerpts']:
            if entry['status'] == 'verified':
                charge = capacity.measure_capacity_value(route, entry)
                targets = [allowances[handle] for handle in entry['targets']]
                if any(row['used'] + charge > row['characters'] for row in targets):
                    entry['status'] = 'over_allowance'
                else:
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
            if request['status'] == 'pending':
                if (len(pending) >= PENDING_REQUESTS or capacity.measure_capacity_value(
                        route, _describe_pending_request(request)) > REQUEST_ROW_CHARACTERS):
                    request['status'] = 'over_allowance'
                else:
                    pending[request['id']] = request
            state['requests'].append(request)
        state['allowances']['passages']['used'] += projected['passage_characters']
    state['delivered_sources'] = len(delivered_sources)
    state['pending_requests'] = list(pending.values())
    return dict(state=state, artifact=_describe_json(state))


def describe_session_plan(route, frames, units, targets):
    """Declare request/final sizing for prepare_assignment_capacity, without writes.

    Frames are measured role fields; units are the immutable inventory rows and
    targets are the composition rule's handles (or handle/source_rows/gap_rows).
    The capacity manifest binds their sizing fields. The assignment builder also
    binds the complete inventory artifact by inventory_digest, as for v7. The
    carried-row reserve covers both the bounded target object and pending list.
    """
    rows = [dict(handle=handle, characters=999999999, used=999999999, remaining=999999999)
            for handle in (row if isinstance(row, str) else row['handle'] for row in targets)]
    target_rows_bound = min(ALLOWANCES_CHARACTERS, capacity.measure_capacity_value(route,
        dict(notes=20000, excerpts=40000, targets=rows, passages=dict(characters=100000, used=100000)))
        + STATE_FIELDS_CHARACTERS)
    # Pending rows are mandatory carried state, so a lowered frame cannot move
    # their reserve into unit_fields. Only the target object has the 30,000 share.
    allowance_rows_bound = target_rows_bound + PENDING_REQUESTS_CHARACTERS + STATE_FIELDS_CHARACTERS
    return dict(kind='request', frames={kind: copy.deepcopy(frames[kind]) for kind in ('request', 'final')},
        inventory=[{key: row[key] for key in ('handle', 'kind', 'characters', 'sha256') if key in row}
                   for row in units], targets=copy.deepcopy(targets), allowance_rows_bound=allowance_rows_bound)


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
