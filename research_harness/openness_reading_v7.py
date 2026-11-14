"""Inventory and replayable state for the future problem reading protocol v7.

This module does not select a new chain's protocol or authenticate v7 turns. The released v5/v6 verifiers own
old-chain authentication; D2 owns eligibility, selection and notes classification. Planning functions publish
nothing. Their canonical JSON values and body maps can be stored after assignment preflight.

The inventory separates logical units (u handles, including original searches) from physical material (b handles).
Every material body occurs once by SHA-256. A logical search lists the shared bodies and residual that rebuild it.
Packets bind the inventory and state artifacts by their SHA-256 instead of carrying their growing collections.
"""

import copy
import hashlib
import json

from . import openness_reading as reading, review_excerpts
from .artifacts import describe_artifact
from .errors import ResearchError
from .evidence import digest
from .problem_openness import serialize_record
from .review_route_capacity import divide_room
from .review_search_delivery import plan_search_delivery

PROTOCOL = 'exactory-openness-reading-v7'
LABEL_CHARACTERS = 120
SELECTION_MISMATCH_MESSAGE = 'The selected carried item does not match its authenticated turn and the new inventory.'


def _describe_identity(row):
    if row['kind'] == 'carried_reading':
        return dict(kind=row['kind'], origin=row['origin'], content_kind=row['content_kind'])
    return dict(kind=row['kind'], **dict(review_excerpts.find_identity(row)[1:]))


def _describe_json(value):
    return describe_artifact(serialize_record(value), 'application/json')


def _append_unit(inventory, row, content):
    identity = _describe_identity(row)
    unit = dict(handle='u%d' % (len(inventory['units']) + 1), kind=row['kind'], identity=identity,
                sha256=row['artifact']['sha256'], artifact=copy.deepcopy(row['artifact']),
                characters=len(content.decode('utf-8')),
                label=row.get('label', row.get('record_id', row.get('version_id', row['kind'])))[:LABEL_CHARACTERS],
                bodies=[])
    inventory['units'].append(unit)
    return unit


def _bind_body(inventory, material_by_digest, unit, reference, content, kind):
    sha256 = reference['sha256']
    if sha256 not in material_by_digest:
        body = dict(handle='b%d' % (len(inventory['material']) + 1), kind=kind, sha256=sha256,
                    characters=len(content.decode('utf-8')), artifact=copy.deepcopy(reference), units=[])
        inventory['material'].append(body)
        material_by_digest[sha256] = body
    body = material_by_digest[sha256]
    if unit['handle'] not in body['units']:
        body['units'].append(unit['handle'])
    if body['handle'] not in unit['bodies']:
        unit['bodies'].append(body['handle'])


def plan_inventory(rows, bodies, *, search_bindings=()):
    """Build canonical inventory from frozen logical rows and their verified UTF-8 bytes, without publication.

    Returns {inventory, artifact, bodies}. Bodies includes originals for reconstruction and the physical material
    to publish; only inventory.material counts toward reading work. Input row order determines stable handles.
    """
    search_plan = plan_search_delivery(search_bindings, share_all_fields=True)
    manifest = search_plan['manifest']
    content = dict(bodies, **search_plan['bodies'])
    searches = {row['search_digest']: row for row in manifest['searches']}
    shared = {row['artifact']['sha256']: row['artifact'] for row in manifest['shared_fields']}
    inventory = dict(protocol=PROTOCOL, units=[], material=[], search_delivery=manifest)
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


def build_inventory(records, artifacts, native, *, review=None):
    """Read v6 logical units with the common capture derivation, plus the v7 minimum/gap/notice units (D3 I2).

    Pass review for an earlier unbound packet: list_reading_rows delegates to bind_packet_selections with its
    authenticated stored plans. No second capture-selection algorithm lives here.
    """
    rows = reading.list_reading_rows(records, artifacts, native, review=review)
    bodies = {row['artifact']['sha256']: artifacts.read(row['artifact']) for row in rows}
    context = native['scientific_context']
    bindings = [dict(binding, search=json.loads(bodies[binding['search_digest']]))
                for binding in context['openness_minimum']['searches']]
    for field, value in [('openness_minimum', context['openness_minimum']),
                         ('minimum_gaps', context.get('minimum_gaps', [])),
                         ('audit_notices', context.get('audit_forecast', {}).get('notices', []))]:
        content = serialize_record(value)
        reference = describe_artifact(content, 'application/json')
        rows.append(dict(kind='record', record_kind=field, record_id=field, artifact=reference))
        bodies[reference['sha256']] = content
    return plan_inventory(rows, bodies, search_bindings=bindings)


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
    return divide_room(room, [dict(id='minimums', minimum=reserved, preferred=reserved, maximum=reserved),
                             dict(id='pool', minimum=0, preferred=room, maximum=room)])['parts'][1]['allowance']


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


def build_chain_state(inventory, turns, *, route, plan):
    """Rebuild canonical state from ordered authenticated turn projections, without storage or current selection.

    Returns {state, artifact, inventory, inventory_artifact, bodies}. The returned inventory appends deterministic
    carried_reading units for overflow entries and affected notes; bind it at the first assignment. Replaying with
    that enriched inventory yields the same bytes. Later own turns use origin=None and supply checked entries,
    notes, gaps, arguments and their validated allowance_use ({targets:{handle:cost},passages:cost}).
    No growing collection here belongs in a reading packet. It carries only digests and its current bounded rows.
    """
    inventory = copy.deepcopy(inventory)
    pool_characters = describe_carried_pool(plan)
    state = dict(protocol=PROTOCOL, inventory_digest=None, turns=[], deliveries=[], notes=[], latest_notes=None,
                 entries=[], pool=[], carried_readings=[], searches=[], gaps=[], arguments=[],
                 allowances=dict(notes=plan['allocation']['notes'],
                    targets=[dict(handle=row['handle'], characters=row['excerpts'], used=0)
                             for row in plan['allocation']['targets']],
                    passages=dict(characters=review_excerpts.SESSION_PASSAGE_CHARACTERS, used=0),
                    pool=dict(characters=pool_characters, used=0)))
    bodies, pool, affected_notes, entry_units = {}, [], [], []
    for number, projected in enumerate(turns, 1):
        turn = dict(copy.deepcopy(projected['turn']), number=number)
        state['turns'].append(turn)
        state['deliveries'].extend(dict(copy.deepcopy(row), turn=number) for row in projected['deliveries'])
        note = dict(copy.deepcopy(projected['notes']), turn=number)
        state['notes'].append(note)
        if note['classification'] == 'current':
            state['latest_notes'] = note['text']
        overflow = []
        for value in projected['entries']:
            entry = dict(copy.deepcopy(value), handle='e%d' % (len(state['entries']) + 1))
            state['entries'].append(entry)
            if turn['origin'] is not None:
                charge = reading.measure_record_entries(route, pool + [entry])
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
        for target in state['allowances']['targets']:
            target['used'] += projected['allowance_use']['targets'].get(target['handle'], 0)
        state['allowances']['passages']['used'] += projected['allowance_use']['passages']
    state['carried_readings'] = list(reversed(affected_notes)) + entry_units
    inventory_artifact = _describe_json(inventory)
    state['inventory_digest'] = inventory_artifact['sha256']
    return dict(state=state, artifact=_describe_json(state), inventory=inventory,
                inventory_artifact=inventory_artifact, bodies=bodies)
