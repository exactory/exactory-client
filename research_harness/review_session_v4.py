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


# These texts are deliberately separate placeholders for the writer's pass.
BAR_REQUEST_PROMPT = 'Read this bar request turn. Return notes, targeted excerpts and requests; no assessment.'
SLATE_REQUEST_PROMPT = 'Read this slate request turn. Return notes, targeted excerpts and requests; no assessment.'
RESULT_REQUEST_PROMPT = 'Read this result request turn. Return notes, targeted excerpts and requests; no assessment.'
BAR_FINAL_PROMPT = 'Return the complete bar assessment once, grounded in the supplied frame and recorded evidence.'
SLATE_FINAL_PROMPT = 'Return the complete slate assessment once, grounded in the supplied frame and recorded evidence.'
RESULT_FINAL_PROMPT = 'Return the complete result assessment once, grounded in the supplied frame and recorded evidence.'
FINAL_CAPACITY_MESSAGE = 'This final response reached the output limit. Apply its capacity replacement before continuing or recording the review.'
FINAL_NEEDS_PAGES_MESSAGE = 'This session final needs pages. Start this slot under the next admitted source rules until session final pages are installed.'
TURN_CONTRACT_MESSAGE = 'Return the request form of this session turn, with bounded notes, exact excerpts naming target handles, requests and settled request IDs; no assessment.'
TURN_BINDING_MESSAGE = 'The session packet must reproduce its frozen inventory, plan, state and authenticated earlier turns.'
TURN_LIMITS_PROMPT = (
    ' The binding.capacity states output_tokens, answer_bound in characters and material_allowance. Reasoning and '
    'answer share the token limit; an answer cut at that limit is lost. Request responses are '
    '{schema_version:1,stage:"request",notes,excerpts:[{unit,text,purpose,targets}],requests,settled_requests}. '
    'Keep notes within allowances.notes and excerpts within allowances.excerpts and each target remaining share. '
    'At most ten requests per response and twenty pending requests are retained. Request source '
    '{id,kind:"source",version_id,depth:"abstract"|"fulltext"}, artifact {id,kind:"artifact",sha256}, '
    'passage {id,kind:"passage",unit,anchor,characters}, inventory_page {id,kind:"inventory_page",page}, '
    'or list_page {id,kind:"list_page",list:"allowances",after}. Pending requests persist until delivered or settled. '
    'Sources and frame units have u<n> handles; exact excerpts have e<turn>_<index> handles. A final cites '
    '{ref:"u3"} or {ref:"u3",start:0,end:40}, with optional part. Span offsets are relative to that part. '
    'A final may instead record an argument with {stage:"material",title,part,text,more}: consecutive parts '
    'starting at one, fixed title, text within answer_bound, at most eight parts. Resume the pending argument, '
    'then cite its completed m<n> handle. Inventory metadata grants no evidence credit.')
FRAME_ENVELOPE_CHARACTERS = 20000


def find_session_kind(packet):
    if packet.get('source_rules') == PROTOCOL:
        return 'request' if 'session_turn' in packet else 'combine' if 'session_combine' in packet else 'final' if 'session_final' in packet else None
    return None


def _require_turn(condition, **details):
    if not condition:
        from .errors import ResearchError
        raise ResearchError('invalid_review_session_turn', TURN_CONTRACT_MESSAGE, details or None)


def build_turn_prompt(role, kind, bounds, *, common_version=None):
    from . import review_protocol as protocol
    prompts = dict(bar=dict(request=BAR_REQUEST_PROMPT, final=BAR_FINAL_PROMPT),
                   slate=dict(request=SLATE_REQUEST_PROMPT, final=SLATE_FINAL_PROMPT),
                   result=dict(request=RESULT_REQUEST_PROMPT, final=RESULT_FINAL_PROMPT))
    for name in prompts:
        prompts[name].update(protocol.SESSION_FINAL_PROMPTS[name])
    text = protocol._select_common_prompt(common_version or protocol.COMMON_VERSION) + '\n\n' + prompts[role][kind] + TURN_LIMITS_PROMPT
    if kind in ('page', 'closing'):
        text += protocol.SESSION_PAGE_REQUESTS_PROMPT
    return protocol._build_prompt(PROTOCOL + '/' + role + '/' + kind, text,
        dict(source_rules=PROTOCOL, session_stage=kind, capacity=copy.deepcopy(bounds)), common_version or protocol.COMMON_VERSION)


def matches_turn_prompt(assignment, packet=None):
    from . import review_protocol as protocol, review_prompt_paragraphs as paragraphs
    envelope = copy.deepcopy(assignment['prompt'])
    bounds = envelope['parameters'].pop('capacity', None)
    base = paragraphs.strip_prompt_paragraphs(envelope, assignment['role'])
    if base is None:
        return False
    kind = base['parameters'].get('session_stage')
    if packet is not None:
        binding = read_session_binding(packet)
        if kind != find_session_form(packet) or bounds != binding.get('capacity'):
            return False
    if kind not in ('request', 'final', 'page', 'closing', 'combine'):
        return False
    expected = build_turn_prompt(assignment['role'], kind, bounds, common_version=protocol.find_common_prompt_version(base))
    expected['parameters'].pop('capacity')
    return base == expected


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


def check_v4_admission(records, artifacts, route, role, packet):
    """Plan a frozen session without publishing any artifacts or consulting later currency."""
    from . import review_field_classes as classes, review_composition as composition
    from .errors import ResearchError
    from .openness_reading_v7 import _PlanningArtifacts
    overlay = _PlanningArtifacts(artifacts)
    logical = _describe_logical_fields(packet)
    rule = composition.find_rule(role, packet)
    if rule is None or route['adapter'] == 'codex_cli_visual_v1':
        return dict(state='stop', protocol=PROTOCOL, reason='unsupported_role_or_route')
    targets = [dict(handle='t%d' % index, id=row['id'], label=row['label'][:120])
               for index, row in enumerate(composition.list_targets(rule, packet), 1)]
    moved = {kind: set() for kind in ('request', 'final')}
    profile = capacity.read_output_profile(records, route)
    try:
        while True:
            frames = classes.describe_role_frame(route, role, logical, ('request', 'final'), unit_fields=moved)
            # Reserve the prompt, binding, delivery descriptors and bounded source rows.
            for fields in frames.values():
                fields[classes.FRAME_DESCRIPTOR_FIELD] += FRAME_ENVELOPE_CHARACTERS
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
            overlay.bodies.update(built['bodies'])
            inventory = built['inventory']
            inventory.update(targets=targets, logical_fields=logical)
            for row in inventory['units']:
                row['required'] = row['kind'] in ('frame', 'artifact')
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


def read_bound_session(artifacts, assignment):
    chain = assignment['session_chain']
    return (capacity.read_chain_capacity_plan(artifacts, assignment),
            json.loads(artifacts.read(chain['inventory'])), json.loads(artifacts.read(chain['state'])))


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
    if request:
        row.update(request_id=request['id'], request_kind=request['kind'])
    return row, evidence


def _pack_session_deliveries(artifacts, route, inventory, state, plan, allowance):
    """Pack serialized bodies and their rows jointly; a split always makes progress."""
    from .openness_reading_v7 import build_list_page
    rows, evidence, pages = [], [], []
    occupied = 2
    passage_left = max(0, state['allowances']['passages']['characters'] - state['allowances']['passages']['used'])
    candidates = [(unit, start, unit['characters'], None) for unit, start in _list_remaining_units(inventory, state)]
    for request in state['pending_requests']:
        if request['kind'] in ('inventory_page', 'list_page'):
            if occupied >= allowance - 1000:
                break
            if request['kind'] == 'list_page':
                page = build_list_page(request['list'], request['after'], plan, state, route,
                                       allowance - occupied - 500, artifacts=artifacts)
            else:
                units = inventory['units']
                start = request['page'] * bar_sources.PAGE_SIZE
                page = dict(page=request['page'], rows=[{key: row[key] for key in
                    ('handle', 'kind', 'label', 'characters')} for row in units[start:start + bar_sources.PAGE_SIZE]],
                    total=len(units))
            content = serialize_record(page).decode('utf-8')
            reference = describe_artifact(content.encode('utf-8'), 'application/json')
            row = dict(kind='list_page', request_id=request['id'], page=page, characters=len(content), request_complete=True)
            entry = dict(artifact=reference, content=content, encoding='utf-8')
            charge = capacity.measure_capacity_value(route, dict(row=row, evidence=entry))
            if charge + occupied > allowance:
                break
            rows.append(row)
            evidence.append(entry)
            occupied += charge
            if request['kind'] == 'list_page':
                pages.append({key: page[key] for key in ('list', 'after', 'next')})
            continue
        requested = _resolve_requested_units(inventory, request)
        if not requested and request['kind'] in ('unit', 'passage'):
            from .openness_final_pages import resolve_entry_request
            entry = resolve_entry_request(state, artifacts, request)
            requested = [entry] if entry is not None else []
        if (requested and request['kind'] != 'passage' and all(
                _find_covered_end(state, unit['handle']) >= unit['characters'] for unit in requested)):
            row = dict(kind='request_status', request_id=request['id'], status='already_delivered', request_complete=True)
            entry = dict(artifact=describe_artifact(b'{}', 'application/json'), content='{}', encoding='utf-8')
            charge = capacity.measure_capacity_value(route, dict(row=row, evidence=entry))
            if occupied + charge <= allowance:
                rows.append(row)
                evidence.append(entry)
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


def build_session_packet(artifacts, route, logical, chain, number, inventory, state, plan, limits, kind, *, notice=None):
    from . import review_field_classes as classes
    frame = classes.classify_packet(route, logical['role'], inventory['logical_fields'],
                                     bounded_fields=plan['frame'][kind]['bounded_fields'])['frame']
    handles = {row['identity']['frame_handle']: row['handle'] for row in inventory['units'] if row['kind'] == 'frame'}
    frame = _replace_frame_handles(frame, handles)
    packet = {key: logical[key] for key in ('protocol', 'role', 'reviewer_id', 'dossier_id')}
    packet['source_rules'] = PROTOCOL
    if 'subject' in logical:
        packet['subject'] = copy.deepcopy(logical['subject'])
    bounds = dict(output_tokens=plan['limits']['output_tokens']['value'], answer_bound=limits['answer_bound'],
                  material_allowance=limits['material_allowance'],
                  level_material_allowance=limits.get('level_material_allowance', limits['material_allowance']),
                  request_bound=limits['request_bound']['text_characters'])
    binding = dict(protocol=PROTOCOL, chain=chain, turn=number, plan_digest=digest(plan),
                   inventory_digest=digest(inventory), state_digest=digest(state), capacity=bounds, frame=frame)
    if kind == 'request':
        carried = describe_session_allowances(route, state, plan, limits)
        rows, evidence, _ = _pack_session_deliveries(artifacts, route, inventory, state, plan, limits['material_allowance'])
        remaining = _list_remaining_units(inventory, state)
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


def validate_request_output(artifacts, assignment, packet, output):
    from .operations import fields, text
    from .review_refusal import contract_check
    plan, inventory, state = read_bound_session(artifacts, assignment)
    binding = packet['session_turn']
    route = dict(adapter=plan['route_class']['adapter'], model=plan['route_class']['model'], endpoint='',
                 configuration=dict(max_output_tokens=plan['limits']['output_tokens']['value'],
                                    effort=plan['route_class']['effort']))
    with contract_check('review.shape'):
        fields(output, ('schema_version', 'stage', 'notes', 'excerpts', 'requests', 'settled_requests'),
               code='invalid_review_session_turn', path='')
        _require_turn(type(output['schema_version']) is int and output['schema_version'] == 1 and output['stage'] == 'request')
        text(output['notes'], 'Session notes', code='invalid_review_session_turn')
        _require_turn(capacity.measure_capacity_value(route, output['notes'])
                      - capacity.measure_capacity_value(route, '') <= binding['allowances']['notes'])
        _require_turn(isinstance(output['excerpts'], list) and isinstance(output['requests'], list)
                      and isinstance(output['settled_requests'], list))
        units = {row['handle']: row for row in inventory['units']}
        targets = set(plan['targets']['handles'])
        for row in output['excerpts']:
            fields(row, ('unit', 'text', 'purpose', 'targets'), code='invalid_review_session_turn')
            _require_turn(isinstance(row['unit'], str) and row['unit'] in units and isinstance(row['text'], str) and 0 < len(row['text']) <= 6000
                and isinstance(row['purpose'], str) and len(row['purpose']) <= PURPOSE_CHARACTERS
                and isinstance(row['targets'], list) and bool(row['targets'])
                and all(isinstance(handle, str) and handle in targets for handle in row['targets'])
                and len(row['targets']) == len(set(row['targets'])))
            # Offsets and span digests are projected from this turn's exact bytes.
            delivered = [entry['content'] for delivery, entry in zip(binding['plan']['now'], packet['evidence'])
                         if delivery.get('unit') == row['unit']]
            _require_turn(any(row['text'] in content for content in delivered), unit=row['unit'])
        pending = {row['id'] for row in state['pending_requests']}
        complete = {row['request_id'] for row in binding['plan']['now'] if row.get('request_complete')}
        _require_turn(all(isinstance(key, str) and key in pending for key in output['settled_requests'])
                      and len(set(output['settled_requests'])) == len(output['settled_requests']))
        earlier = {row['id']: _describe_pending_request(row) for row in state['requests']}
        seen = set()
        for row in output['requests']:
            _require_turn(isinstance(row, dict) and isinstance(row.get('id'), str)
                          and 0 < len(row['id']) <= 120 and row['id'] not in seen)
            seen.add(row['id'])
            _require_turn(row['id'] not in earlier or earlier[row['id']] == row)
            kind = row.get('kind')
            if kind == 'source':
                fields(row, ('id', 'kind', 'version_id', 'depth'), code='invalid_review_session_turn')
                _require_turn(row['depth'] in ('abstract', 'fulltext') and isinstance(row['version_id'], str))
                _require_turn(any(unit['identity'].get('version_id') == row['version_id'] for unit in inventory['units']))
            elif kind == 'artifact':
                fields(row, ('id', 'kind', 'sha256'), code='invalid_review_session_turn')
                _require_turn(any(unit['sha256'] == row['sha256'] and unit['kind'] in ('artifact', 'frame')
                                  for unit in inventory['units']))
            elif kind in ('unit', 'passage'):
                fields(row, ('id', 'kind', 'unit') + (('anchor', 'characters') if kind == 'passage' else ()),
                       code='invalid_review_session_turn')
                if isinstance(row['unit'], str) and row['unit'] not in units:
                    from .openness_final_pages import resolve_entry_request
                    archived = resolve_entry_request(state, artifacts, row)
                    if archived is not None:
                        units[row['unit']] = archived
                _require_turn(isinstance(row['unit'], str) and row['unit'] in units)
                if kind == 'passage':
                    _require_turn(type(row['characters']) is int and 0 < row['characters'] <= 100000)
                    body = artifacts.read(units[row['unit']]['artifact']).decode('utf-8')
                    remaining = state['allowances']['passages']['characters'] - state['allowances']['passages']['used']
                    remaining -= sum(delivery['characters'] for delivery in binding['plan']['now']
                                     if delivery.get('request_kind') == 'passage')
                    reserved = sum(request.get('characters', 0) for request in state['pending_requests']
                                   if request['kind'] == 'passage' and request['id'] not in complete)
                    reserved += sum(request.get('characters', 0) for request in output['requests']
                                    if request.get('kind') == 'passage' and request['id'] != row['id'] and request['id'] not in earlier
                                    and type(request.get('characters')) is int)
                    _require_turn(row['characters'] <= max(0, remaining - reserved), passage_allowance=remaining)
                    _require_turn((row['anchor'] is None or isinstance(row['anchor'], str)
                        and 0 < len(row['anchor']) <= 600 and row['anchor'] in body)
                        and type(row['characters']) is int and 0 < row['characters'] <= 100000)
            elif kind == 'inventory_page':
                fields(row, ('id', 'kind', 'page'), code='invalid_review_session_turn')
                _require_turn(type(row['page']) is int and 0 <= row['page'] < max(1, (len(units) + 9) // 10))
            elif kind == 'list_page':
                fields(row, ('id', 'kind', 'list', 'after'), code='invalid_review_session_turn')
                _require_turn((row['list'] == 'allowances' or row['list'] in state.get('final_blocks', {}))
                              and (row['after'] is None or isinstance(row['after'], str)))
                from .openness_reading_v7 import _validate_list_cursor
                after = copy.deepcopy(state)
                for delivery in binding['plan']['now']:
                    if delivery['kind'] == 'list_page' and 'list' in delivery['page']:
                        page = delivery['page']
                        after.setdefault('list_pages', {})[page['list']] = dict(next=page['next'], ended=page['next'] is None)
                _validate_list_cursor(row['list'], row['after'], plan, after, artifacts=artifacts)
            else:
                _require_turn(False, kind=kind)


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
                        if unit['kind'] in ('fulltext', 'abstract') else reference)
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


def prepare_session_assignment(records, artifacts, value, route, revision, *, logical=None, parent=None,
                               admission=None, slot=None, replacement=False, notice=None, refused_ids=(), diagnoses=None, replay=False, replay_state=None):
    """Publish one deterministic request and its immutable bindings in the command mutation."""
    from . import review_protocol as protocol
    from .operations import immutable_record
    _require_turn(len(value['id']) <= 120, maximum_characters=120)
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
        state = build_session_state(inventory, [], route, plan)['state']
        chain = dict(first_assignment_id=value['id'], turn=1, inventory=protocol._put_json(artifacts, inventory),
                     logical=protocol._put_json(artifacts, logical))
        kind = 'request'
        limits = capacity.derive_plan_task_limits(plan, kind)
    else:
        plan, inventory, state = read_bound_session(artifacts, parent)
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
    if notice is not None and kind == 'request':
        limits = capacity.derive_plan_task_limits(plan, kind, [dict(material_allowance=
            limits.get('level_material_allowance', limits['material_allowance']))],
            notice_characters=capacity.measure_capacity_value(route, notice))
    chain['state'] = protocol._put_json(artifacts, state)
    if kind == 'final' and not (replacement and state.get('material_state', {}).get('pending')):
        packet, reference = prepare_final_packet(records, artifacts, parent, route, logical, chain['turn'],
            inventory, state, plan, limits, replacement=replacement)
        if packet is None:
            return None, None
        if reference is not None:
            chain['final_page_plan'] = reference
        if notice is not None:
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
    prompt = build_turn_prompt(value['role'], find_session_form(packet), read_session_binding(packet)['capacity'],
        common_version=protocol.find_common_prompt_version(parent['prompt']) if parent else None)
    for entry in packet['evidence']:
        artifacts.put(entry['content'].encode('utf-8'), entry['artifact']['media_type'])
    record = dict(value, packet=protocol._put_json(artifacts, packet), packet_digest=packet['digest'], prompt=prompt,
        prompt_hash=prompt['rendered_prompt_hash'], route_digest=digest(route), context_state=protocol.route_state(records, artifacts, value['route_id']),
        assignment_policy=protocol.POLICY, history=[], session_id=chain['first_assignment_id'],
        history_assignment_id=parent.get('history_assignment_id') if replacement else parent['id'] if parent else None,
        reassessment_required=False, recorded_revision=revision, session_chain=chain,
        capacity_plan=members['capacity_plan'], capacity_plan_kind=kind, slot_id=slot or (parent.get('slot_id', parent['id']) if parent else value['id']))
    if replacement:
        record['capacity_replacement_of'] = parent['id']
        record['context'] = copy.deepcopy(parent['context'])
    from .review_codex_invocation import supports_route, KEY as INVOCATION_KEY, PROTOCOL as INVOCATION_PROTOCOL
    if supports_route(route):
        record[INVOCATION_KEY] = INVOCATION_PROTOCOL
    if replay:
        return [], record
    request = protocol.transport.request_body(route, prompt['text'], packet, [], order=())
    from .review_preflight import build_report, check_request
    preflight = check_request(build_report(records, artifacts, route, value['role'], prompt, packet, request,
        capacity_plan=plan, turn_kind='final' if kind == 'combine' else kind), request, packet=packet, artifacts=artifacts)
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
    plan, inventory, state = read_bound_session(artifacts, assignment)
    chain = assignment['session_chain']
    binding = read_session_binding(packet)
    logical = protocol._read_json(artifacts, chain['logical'])
    root = records['review_assignment'][chain['first_assignment_id']]
    if (_describe_logical_fields(logical) != inventory['logical_fields']
            or any(chain[key] != root['session_chain'][key] for key in ('inventory', 'logical'))
            or assignment['capacity_plan'] != root['capacity_plan']
            or any(assignment[key] != root[key] for key in ('role', 'reviewer_id', 'author_id', 'route_id', 'dossier_id'))):
        raise ResearchError('review_invocation_unverified', TURN_BINDING_MESSAGE)
    parent_id = assignment.get('history_assignment_id')
    if parent_id is not None and (parent_id not in records['review_assignment'] or
            records['review_assignment'][parent_id]['recorded_revision'] >= assignment['recorded_revision']):
        raise ResearchError('review_invocation_unverified', TURN_BINDING_MESSAGE)
    before = (rebuild_session_state(records, artifacts, records['review_assignment'][parent_id], inventory, plan,
                                   records['review_route'][assignment['route_id']]) if parent_id else
              build_session_state(inventory, [], records['review_route'][assignment['route_id']], plan)['state'])
    if (state != before or binding.get('plan_digest') != digest(plan)
            or binding.get('inventory_digest') != digest(inventory) or binding.get('state_digest') != digest(state)
            or binding.get('chain') != chain['first_assignment_id'] or binding.get('turn') != chain['turn']):
        raise ResearchError('review_invocation_unverified', TURN_BINDING_MESSAGE)
    if chain.get('final_page_plan'):
        return authenticate_final_binding(records, artifacts, assignment, packet)
    kind = find_session_kind(packet)
    same_position = assignment.get('capacity_replacement_of') or assignment.get('context', {}).get('output_refusal', {}).get('assignment_id')
    prior = records['review_assignment'].get(same_position or parent_id)
    expected_turn = prior['session_chain']['turn'] + (0 if same_position else 1) if prior else 1
    expected_kind = (find_session_kind(protocol._read_json(artifacts, prior['packet'])) if same_position else
                     'request' if prior is None or _list_remaining_units(inventory, state) or state['pending_requests'] else 'final')
    if chain['turn'] != expected_turn or kind != expected_kind or protocol.read_history(artifacts, assignment) != []:
        raise ResearchError('review_invocation_unverified', TURN_BINDING_MESSAGE)
    # Only observations preceding this assignment choose its allowance.
    replacement = assignment.get('capacity_replacement_of')
    refusal = assignment.get('context', {}).get('output_refusal')
    origin = replacement or (refusal['assignment_id'] if refusal else parent_id)
    route = records['review_route'][assignment['route_id']]
    if origin:
        limits = derive_session_task_limits(records, artifacts, records['review_assignment'][origin], kind,
                                            before_revision=assignment['recorded_revision'], replacement=bool(replacement))
    else:
        limits = capacity.derive_plan_task_limits(plan, kind)
    if refusal and not replacement:
        previous = protocol._read_json(artifacts, records['review_assignment'][origin]['packet'])
        limits = _derive_refusal_limits(plan, kind, previous, packet['output_refusal'], route)
    if replacement and kind == 'request' and packet.get('output_refusal'):
        limits = capacity.derive_plan_task_limits(plan, kind, [dict(material_allowance=
            limits.get('level_material_allowance', limits['material_allowance']))],
            notice_characters=capacity.measure_capacity_value(route, packet['output_refusal']))
    if replacement and kind == 'final' and state.get('material_state', {}).get('pending'):
        frozen = protocol._read_json(artifacts, records['review_assignment'][replacement]['packet'])
        limits['answer_bound'] = max(plan['task']['final']['smallest_answer_bound'],
                                     frozen['session_final']['capacity']['answer_bound'] // 2)
    expected = build_session_packet(artifacts, route, logical, chain['first_assignment_id'], chain['turn'],
                                    inventory, state, plan, limits, kind, notice=packet.get('output_refusal'))
    if expected != packet or not matches_turn_prompt(assignment, packet):
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
    value['id'] = _find_unused_id(records, value['id'] + '-replacement', maximum=120)
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
    from .operations import normalized_text
    _require_turn(all(parent[key] == value[key] for key in ('role', 'dossier_id', 'route_id'))
                  and all(normalized_text(parent[key]) == normalized_text(value[key]) for key in ('author_id', 'reviewer_id')))
    planned = failures.plan_after_failure(records, artifacts, parent['id'])
    _require_turn(not protocol.has_pending_invocation(records, parent['id']) and planned['state'] == 'replace')
    return prepare_session_assignment(records, artifacts, value, records['review_route'][parent['route_id']], revision,
                                       parent=parent, replacement=True,
                                       notice=protocol._read_json(artifacts, parent['packet']).get('output_refusal'))


def _derive_refusal_limits(plan, kind, frozen, notice, route):
    binding = read_session_binding(frozen)
    current = binding['capacity']
    limits = capacity.derive_plan_task_limits(plan, kind, [dict(material_allowance=current['level_material_allowance'])],
        notice_characters=capacity.measure_capacity_value(route, notice) if kind == 'request' else 0)
    limits['answer_bound'] = current['answer_bound']
    return limits


def prepare_refusal_assignment(records, artifacts, value, revision, prior, frozen, attempt_ids, diagnoses):
    from . import review_protocol as protocol, review_refusal_notice as notices, review_prompt_paragraphs as paragraphs
    from .operations import immutable_record
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
        prompt = build_turn_prompt(prior['role'], find_session_form(packet), read_session_binding(packet)['capacity'],
                                    common_version=protocol.find_common_prompt_version(prior['prompt']))
        bounds = prompt['parameters'].pop('capacity')
        prompt = paragraphs.append_prompt_paragraphs(prompt, prior['role'], None,
                                                     [paragraphs.REFUSAL_PARAGRAPH_VERSIONS[notice['form']]])
        prompt['parameters']['capacity'] = bounds
        return packet, prompt
    selected = notices.select_refusal_notice(records, artifacts, attempt_ids, diagnoses=diagnoses, route=route,
        role=prior['role'], prompt=None, packet=None, prepare_turn=prepare_turn, task_characters=None,
        task_limit_characters=read_session_binding(frozen)['capacity']['request_bound'],
        capacity_plan=plan, turn_kind=plan_kind, forms=('bounded',))
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
        if (member is None or member['capacity_plan_kind'] != kind
                or before_revision is not None and attempt['recorded_revision'] >= before_revision):
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
        return [dict(handle=row['handle'], label=row['label'][:length]) for row in targets]
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
    return dict(logical, session_targets=inventory['targets'],
                handle_table=build_handle_packet(packet, artifacts, assignment)['handle_table'])


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
    old = read_session_binding(json.loads(artifacts.read(prior['packet'])))['capacity']
    cause_kind = ('level' if limits['target_allowance'] != previous['target_allowance']
                  or limits['material_allowance'] != old['level_material_allowance'] else
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
            raise ResearchError('review_resampling_forbidden', 'This session already has its closing answer.')
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
    rows = composition.build_decision_rows(composition.find_rule(logical['role'], envelope), envelope, list_page_outputs(state),
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
        if state['pending_requests']:
            used = measure_final_material(route, binding)
            now, evidence, _ = _pack_session_deliveries(artifacts, route, inventory, state, plan,
                max(0, binding['capacity']['material_allowance'] - used - 256))
            binding['plan'] = dict(now=now)
            packet['evidence'] = evidence
        allowances = describe_session_allowances(route, state, plan, limits)
        binding['request_allowances'] = {key: allowances[key] for key in ('notes', 'excerpts', 'targets')}
        # The full allowance history stays in the state artifact. A page exposes
        # only its own target grants, within the pre-existing allowance reserve.
        binding['request_allowances']['targets'] = [dict(row, remaining=max(0, row['characters'] - row['used']))
            for row in state['allowances']['targets']
            if turn['kind'] in ('page', 'final') and row['handle'] in turn['targets']]
        binding['request_allowances']['notes'] = min(binding['request_allowances']['notes'],
            max(1, binding['capacity']['material_allowance'] - measure_final_material(route, binding)))
    packet.pop('digest', None)
    packet['digest'] = digest(packet)
    return packet


def measure_final_material(route, binding):
    return capacity.measure_capacity_value(route, {key: binding.get(key) for key in
        (('closing',) if 'closing' in binding else ('notes', 'entries', 'sources', 'fill'))})


def validate_final_turn_output(records, artifacts, assignment, packet, output):
    """Validate page-owned fields before cross-page validation at the closing turn."""
    from . import review_composition as composition, review_condensing as condensing
    binding = read_session_binding(packet)
    route = records['review_route'][assignment['route_id']]
    if 'session_combine' in packet:
        block = dict(binding['block'], entries=[row.get('handle', row.get('block')) for row in binding['block']['entries']])
        condensing.validate_combination(block, output, lambda value: capacity.measure_capacity_value(route, value),
                                       binding['capacity']['answer_bound'])
    elif output.get('stage') == 'request':
        adapted = copy.deepcopy(packet)
        adapted['session_turn'] = dict(binding, allowances=binding['request_allowances'],
                                       plan=binding.get('plan', dict(now=[])))
        validate_request_output(artifacts, assignment, adapted, output)
        _require_turn(all(set(row['targets']) <= set(binding.get('page', {}).get('targets', [])) for row in output['excerpts']))
    else:
        from .review_refusal import contract_check
        _, _, state = read_bound_session(artifacts, assignment)
        completed = {row['request_id'] for row in binding.get('plan', {}).get('now', [])
                     if row.get('request_complete')}
        with contract_check('review.shape'):
            _require_turn(all(row['id'] in completed for row in state['pending_requests']),
                          pending_requests=[row['id'] for row in state['pending_requests'] if row['id'] not in completed])
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
    from .openness_final_pages import read_page_plan
    plan, inventory, state = read_bound_session(artifacts, assignment)
    logical = protocol._read_json(artifacts, assignment['session_chain']['logical'])
    page_plan = read_page_plan(artifacts, assignment['session_chain']['final_page_plan'])
    currency = dict(packet_digest=digest(logical), inventory_digest=digest(inventory))
    _require_turn(currency == page_plan['currency'] and read_session_binding(protocol._read_json(
        artifacts, assignment['packet']))['page_plan_digest'] == digest(page_plan))
    return currency


def validate_composite_output(records, artifacts, assignment, packet, output):
    from .research_decisions import validate_review_contract
    from .errors import ResearchError
    try:
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
    validate_final_turn_output(records, artifacts, assignment, packet, output)
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
    from .evidence import digest
    historical = {kind: {key: row for key, row in rows.items()
                        if row.get('recorded_revision', 0) < assignment['recorded_revision']}
                  for kind, rows in records.items()}
    origin = assignment.get('capacity_replacement_of') or assignment.get('context', {}).get('output_refusal', {}).get('assignment_id') or assignment.get('history_assignment_id')
    prior = historical['review_assignment'][origin]
    frozen = protocol._read_json(artifacts, prior['packet'])
    if assignment.get('context', {}).get('output_refusal') and not assignment.get('capacity_replacement_of'):
        expected = copy.deepcopy(frozen)
        expected.pop('output_refusal', None)
        from .review_refusal_notice import attach_refusal_notice
        expected = attach_refusal_notice(expected, packet['output_refusal'])
        _require_turn(expected == packet and assignment['session_chain'] == prior['session_chain']
                      and matches_turn_prompt(assignment, packet))
        return True
    value = {key: copy.deepcopy(assignment[key]) for key in
             ('id', 'route_id', 'dossier_id', 'role', 'reviewer_id', 'author_id', 'context')}
    if 'subject' in assignment:
        value['subject'] = assignment['subject']
    # Preparation consults no current priors for its packet or page plan. The
    # report is intentionally skipped during verification.
    _, rebuilt = prepare_session_assignment(historical, _PlanningArtifacts(artifacts), value,
        historical['review_route'][prior['route_id']], assignment['recorded_revision'], parent=prior,
        replacement=bool(assignment.get('capacity_replacement_of')), replay=True,
        replay_state=json.loads(artifacts.read(assignment['session_chain']['state'])),
        notice=frozen.get('output_refusal') if assignment.get('capacity_replacement_of') else None)
    _require_turn(rebuilt is not None and rebuilt['packet'] == assignment['packet']
                  and rebuilt['session_chain'] == assignment['session_chain'] and rebuilt['prompt'] == assignment['prompt'])
    return True


COMPOSED_REFUSAL_TEXT = 'The composed review was refused; correct the closing answer using this check and composed path.'


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
