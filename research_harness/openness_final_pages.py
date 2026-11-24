"""Artifact-backed final turns of a v7 reading chain.

The capacity plan and inventory stay fixed. A page plan freezes sizes and turn
identities; state retains responses, pending work and the original block entries.
Only bounded views of those artifacts enter reviewer requests.
"""

import copy
import json
import math

from . import review_composition as composition, review_condensing as condensing
from . import review_final_pages as pages, review_route_capacity as capacity
from .errors import ResearchError
from .evidence import digest
from .problem_openness import serialize_record

# The message of capacity_forecast.by_kind.final in the receipt of a turn of a final in pages. It serves a chain of
# protocol v7 and a chain of the math protocol v2.
FINAL_PAGES_RECEIPT = (
    'final_form is pages: the final of this chain is taken in pages. pages, condensing_turns and closing_pages '
    'count the turns of its present page plan: the pages, the condensing turns, which reduce entries that do not '
    'fit a page or the closing turn, and the closing turn. A turn that asks for text or stores an argument is '
    'assigned again. The counts hold for the present plan alone: after a capacity event or a denser token ratio, '
    'review-series plans a smaller task under a new page plan, the same turn once more, or a stop or a handoff '
    'with its own message. Run review-series after each turn and take the step that it plans, until the closing '
    'turn has its answer.')
# The message of artifact_corrupt for the page plan of a final, of protocol v7 or of the math protocol v2.
PAGE_PLAN_INVALID_MESSAGE = (
    'The page plan of this final does not verify, so the harness cannot use it for this operation. The stored plan '
    'differs from its recorded digest or protocol, or the plan, the position of a turn or the packet of a turn '
    'differs from what the harness builds again from the stored capacity plan, the chain state, the native packet, '
    'the inventory and the recorded attempts. The turns that are recorded stay in the store. Check the record with '
    'record-check.')
# The member message of output_refusal.composed_finding, which the replacement of a refused closing turn carries to
# the reviewer. The notice holds it beside check, path, closing_answer and, for a field of a page, decision_row.
COMPOSED_REFUSAL_TEXT = (
    'The harness composed the finding from the answers of the pages and your closing answer, and a check refused the '
    'composed finding. For this refusal, path of the last entry of output_refusal.refused and path of this member '
    'point into the composed finding, not into your closing answer. The composed finding is '
    '{schema_version,subject_digest,claim_digest,reviewer,findings:{statement,assumptions,scope,dependencies,policy,'
    'openness:{outcome,coverage,reason,grounds}}}: coverage holds the coverage that the pages wrote, in the order of '
    'the targets, and grounds holds the grounds of the pages in that order, followed by the grounds of your closing '
    'answer. closing_answer is the refused closing answer; an object with head and complete false holds its start. '
    'decision_row, where it is present, is the decision row of the target whose page wrote the field at path. The '
    'answers of the pages stand. Return the closing answer again, so that the finding composed with it passes the '
    'check.')


def read_binding(packet):
    return packet.get('reading_turn', packet.get('reading_final', packet.get('reading_combine', {})))


def read_page_plan(artifacts, reference):
    value = json.loads(artifacts.read(reference['artifact']))
    if digest(value) != reference['digest'] or value['protocol'] != pages.FINAL_PAGE_PLAN_PROTOCOL:
        raise ResearchError('artifact_corrupt', PAGE_PLAN_INVALID_MESSAGE)
    return value


def read_final_currency(native, inventory):
    return dict(packet_digest=digest(native), inventory_digest=digest(inventory))


def measure_page_room(records, artifacts, prior, plan, limits):
    """Use only pinned limits and earlier measurements, including ratio-only changes."""
    from .review_failure_class import list_capacity_chain_assignments
    ratio = plan['measure']['characters_per_token']
    if ratio is None:
        # Byte-measured plans already pin a conservative input room.
        return min(plan['allocation']['page_room'], limits['material_allowance'])
    identifiers = list_capacity_chain_assignments(records, prior)
    for attempt in records.get('review_attempt', {}).values():
        if attempt['assignment_id'] in identifiers:
            measured = capacity.measure_attempt_ratio(attempt, margin=plan['measure']['ratio_margin'])
            if measured is not None:
                ratio = min(ratio, measured)
    available = math.floor(plan['limits']['input_tokens']['value'] * ratio)
    room = min(plan['allocation']['page_room'], limits['material_allowance'],
        available - plan['frame']['final']['bound'] - plan['task']['final']['notice_characters'])
    return max(0, room)


def build_full_final(artifacts, route, logical, chain, number, inventory, state, plan, limits):
    from . import openness_reading_v7 as v7
    # This transient inventory view is never transmitted. The paged builder
    # selects its material before assigning the ordinary request bounds.
    packet = v7.build_turn_packet(artifacts, route, logical, chain, number, inventory,
        dict(state, notes=state['notes'][-1:]), plan, dict(limits, material_allowance=10 ** 18), 'final',
        include_turn_members=False)
    packet['reading_final']['state_digest'] = digest(state)
    return packet


def build_final_inputs(artifacts, route, logical, chain, number, inventory, state, plan, limits, room):
    """Price entries with handles and all fields exactly as the request serializes them."""
    full = build_full_final(artifacts, route, logical, chain, number, inventory, state, plan, limits)
    binding = full['reading_final']
    catalog = {}

    def describe_entry(handle, value):
        catalog[handle] = copy.deepcopy(value)
        return dict(handle=handle, characters=capacity.measure_capacity_value(route, value) + 1)

    pool_handles = set(state['pool'])
    pool = [describe_entry(row['handle'], row) for row in binding['entries'] if row['handle'] in pool_handles]
    material_entries = [describe_entry(row['handle'], row) for row in binding['material']]
    pool.extend(material_entries)
    material_cost = sum(row['characters'] for row in material_entries)
    notes = capacity.measure_capacity_value(route, binding['notes']) - capacity.measure_capacity_value(route, '' if isinstance(binding['notes'], str) else None)
    notes = max(0, notes)
    minimum = capacity.EXCERPT_TEXT_CHARACTERS + capacity.EXCERPT_FIELDS_CHARACTERS
    if notes + minimum > room:
        handle = binding['notes_handle'] or 'notes'
        content = binding['notes']
        catalog[handle] = dict(handle=handle, text=content)
        offset, segments = 0, []
        while offset < len(content):
            low, high = offset, len(content)
            while low < high:
                end = (low + high + 1) // 2
                value = dict(handle=handle + '@' + str(offset), entry=handle, start=offset, end=end, text=content[offset:end])
                if capacity.measure_capacity_value(route, value) + 1 <= room - minimum:
                    low = end
                else:
                    high = end - 1
            if low == offset:
                raise ResearchError('review_route_capacity_below_floor', capacity.BELOW_FLOOR_MESSAGE)
            value = dict(handle=handle + '@' + str(offset), entry=handle, start=offset, end=low, text=content[offset:low])
            artifacts.put(serialize_record(value), 'application/json')
            segments.append(describe_entry(value['handle'], value))
            offset = low
        pool[0:0] = segments
        notes = 0
    targets = []
    for handle in plan['targets']['handles']:
        entries = [describe_entry(row['handle'], row) for row in binding['entries']
                   if handle in row['targets'] and row['handle'] not in pool_handles]
        gaps = [describe_entry(row['handle'], row) for row in binding['gaps']
                if row.get('target', row.get('formulation')) == handle]
        units = {row['unit'] for row in binding['entries'] if handle in row['targets'] or row['handle'] in pool_handles}
        sources = [describe_entry('source:' + row['handle'], row) for row in binding['sources'] if row['handle'] in units]
        targets.append(dict(handle=handle, entries=entries, rows=gaps + sources))
    for handle in plan['targets']['handles']:
        catalog['decision:' + handle] = None  # Filled from terminal page outputs at assignment time.
    inputs = dict(page_room=room, target_allowance=limits['target_allowance'], target_minimum=minimum,
        notes=notes, pool=pool, targets=targets, closing_room=max(1, room - min(material_cost, max(0, room - minimum))),
        row_characters=capacity.DECISION_ROW_CHARACTERS, condense=condensing.plan_condensing)
    return inputs, catalog, full


def list_final_progress(state, plan):
    progress = state.get('final_progress', {})
    completed = [turn['number'] for turn in plan['turns'] if progress.get(str(turn['number']), {}).get('terminal')]
    pending = [turn['number'] for turn in plan['turns'] if turn['number'] not in completed
               and str(turn.get('pending_of') or turn['number']) in progress
               and not progress[str(turn.get('pending_of') or turn['number'])]['terminal']
               and turn['kind'] in ('page', 'closing', 'final')]
    return completed, pending


def prepare_final_packet(records, artifacts, prior, route, logical, number, inventory, state, plan, limits,
                         *, reference=None, attempt_id=None):
    """Freeze the first final plan, or append a replayable successor, and assign its next turn."""
    from . import openness_reading_v7 as v7
    room = measure_page_room(records, artifacts, prior, plan, limits)
    chain = prior['reading_chain']['first_assignment_id']
    reference = reference or prior['reading_chain'].get('final_page_plan')
    if attempt_id is None and reference is not None:
        attempt_id = state['turns'][-1]['attempt_id']
    inputs, catalog, full = build_final_inputs(artifacts, route, logical, chain, number, inventory, state, plan, limits, room)
    changed = False
    if reference is None:
        turns = pages.plan_final_turns(**inputs)
        if len(turns) == 1 and turns[0]['kind'] == 'final':
            return v7.build_turn_packet(artifacts, route, logical, chain, number, inventory, state, plan,
                                       limits, 'final', requests=state.get('pending_requests', [])), None
        page_plan = pages.build_page_plan(chain, digest(plan), digest(state), read_final_currency(logical['native'], inventory), turns)
        changed = True
    else:
        page_plan = read_page_plan(artifacts, reference)
        completed, pending = list_final_progress(state, page_plan)
        previous = page_plan['turns'][-1]['context']
        prior_binding = read_binding(json.loads(artifacts.read(prior['packet'])))
        old_level = prior_binding.get('capacity', {}).get('level_material_allowance', prior_binding.get('capacity', {}).get('material_allowance'))
        cause_kind = ('level' if limits['target_allowance'] != previous['target_allowance'] or
                      limits['material_allowance'] != old_level else
                      'ratio' if room < previous['page_room'] else None)
        if cause_kind is not None:
            cause = dict(kind=cause_kind, attempt_id=attempt_id, page_room=room,
                         completed=completed, pending=pending)
            if cause_kind == 'level':
                cause['target_allowance'] = limits['target_allowance']
            turns = pages.repack_final_turns(page_plan['turns'], completed, pending, room, limits['target_allowance'],
                **{key: value for key, value in inputs.items() if key not in ('page_room', 'target_allowance')})
            page_plan = pages.build_page_plan(chain, digest(plan), digest(state), page_plan['currency'], turns,
                                              digest(page_plan), cause)
            changed = True
    if changed:
        reference = publish_page_plan(artifacts, page_plan, previous=reference)
    completed, pending = list_final_progress(state, page_plan)
    selected = next((turn for turn in page_plan['turns'] if turn['number'] not in completed), None)
    if selected is None:
        raise ResearchError('review_resampling_forbidden',
            'Every turn of this final has its answer, the closing turn included, so the chain takes no further turn. '
            'Run review-series on the chain and take the step that it plans.')
    floor = min(row['material_allowance'] for row in plan['task']['final']['levels'])
    while True:
        prepared = pages.prepare_final_turn(page_plan['turns'], selected['number'], room, limits['target_allowance'],
            floor, condensing.plan_condensing, pending=pending, completed=completed, inputs=inputs)
        if prepared == page_plan['turns']:
            break
        if prepared is None:
            raise ResearchError('review_route_capacity_below_floor', capacity.BELOW_FLOOR_MESSAGE)
        cause = dict(kind='split', turn=selected['number'], attempt_id=attempt_id,
                     completed=completed, pending=pending)
        page_plan = pages.build_page_plan(chain, digest(plan), digest(state), page_plan['currency'], prepared,
                                          digest(page_plan), cause)
        reference = publish_page_plan(artifacts, page_plan, previous=reference)
        completed, pending = list_final_progress(state, page_plan)
        selected = next(turn for turn in prepared if turn['number'] not in completed)
    packet = build_final_packet(records, artifacts, route, logical, chain, number, inventory, state, plan, limits,
                                page_plan, selected, catalog=catalog, full=full)
    return packet, reference


def prepare_capacity_packet(records, artifacts, prior, route, logical, number, inventory, state, plan, limits):
    """Split the failed final task before applying its observed level change."""
    frozen = json.loads(artifacts.read(prior['packet']))
    binding = read_binding(frozen)
    reference = prior['reading_chain'].get('final_page_plan')
    if reference is None:
        historical = dict(records, review_attempt={key: row for key, row in records.get('review_attempt', {}).items()
            if row['recorded_revision'] < prior['recorded_revision']})
        old_limits = capacity.derive_turn_task_limits(historical, artifacts, prior, kind='final')
        room = measure_page_room(historical, artifacts, prior, plan, old_limits)
        inputs, _, _ = build_final_inputs(artifacts, route, logical, prior['reading_chain']['first_assignment_id'],
            number, inventory, state, plan, old_limits, room)
        page_plan = pages.build_page_plan(prior['reading_chain']['first_assignment_id'], digest(plan), digest(state),
            read_final_currency(logical['native'], inventory), pages.plan_final_turns(**inputs))
        reference = publish_page_plan(artifacts, page_plan)
        selected = page_plan['turns'][0]['number']
    else:
        page_plan = read_page_plan(artifacts, reference)
        selected = binding['final_turn']
    completed, pending = list_final_progress(state, page_plan)
    floor = min(row['material_allowance'] for row in plan['task']['final']['levels'])
    turns = pages.split_final_turn(page_plan['turns'], selected, floor, condensing.plan_condensing, pending=pending)
    if turns is None:
        return None
    attempt = max((row for row in records['review_attempt'].values() if row['assignment_id'] == prior['id']),
                  key=lambda row: row['recorded_revision'])
    cause = dict(kind='split', turn=selected, attempt_id=attempt['id'], completed=completed, pending=pending)
    successor = pages.build_page_plan(page_plan['chain'], digest(plan), digest(state), page_plan['currency'],
                                      turns, digest(page_plan), cause)
    reference = publish_page_plan(artifacts, successor, previous=reference)
    return prepare_final_packet(records, artifacts, prior, route, logical, number, inventory, state, plan, limits,
                                reference=reference, attempt_id=attempt['id'])


def publish_page_plan(artifacts, plan, *, previous=None):
    reference = dict(protocol=pages.FINAL_PAGE_PLAN_PROTOCOL, digest=digest(plan),
                artifact=artifacts.put(serialize_record(plan), 'application/json'))
    if previous is not None:
        reference['predecessors'] = copy.deepcopy(previous.get('predecessors', [])) + [
            {key: copy.deepcopy(previous[key]) for key in ('protocol', 'digest', 'artifact')}]
    return reference


def read_final_position(artifacts, reference, number, *, include_currency=False):
    """Condensation keeps its page/closing position; target splits start positions."""
    from .evaluation import Evaluation

    def read_position_plan(item):
        if isinstance(artifacts, Evaluation):
            return artifacts.once(('final_position_plan', item['digest'], digest(item['artifact'])),
                                  lambda: read_page_plan(artifacts, item))
        return read_page_plan(artifacts, item)

    current = read_position_plan(reference)
    selected = next(turn for turn in current['turns'] if turn['number'] == number)
    for item in reversed(reference.get('predecessors', [])):
        plan = read_position_plan(item)
        if include_currency and plan['currency'] != current['currency']:
            break
        parent = next((turn for turn in plan['turns'] if turn['number'] == selected.get('split_of')), None)
        if (parent is not None and selected['kind'] in ('page', 'closing')
                and parent['kind'] in (selected['kind'], 'final') and parent['targets'] == selected['targets']):
            selected = parent
    if include_currency:
        return digest(current['currency']), selected['number']
    return selected['number']


def build_validation_envelope(artifacts, assignment, packet, targets=None):
    """Keep the complete native science context and explicitly select the v7 finding contract.

    The envelope holds the complete finding index of the plan, selected for the formulations of a page (G8.7).
    """
    envelope = dict(packet, native=json.loads(artifacts.read(assignment['context']['native_packet'])))
    binding = copy.deepcopy(packet.get('reading_final', {}))
    if targets is not None:
        binding['page'] = dict(targets=list(targets))
    if 'finding_index' in packet:
        from . import openness_reading_v7 as v7
        from .review_search_delivery import select_finding_index
        index = capacity.read_chain_capacity_plan(artifacts, assignment)['finding_index']
        page = packet.get('reading_final', {}).get('page')
        if page is not None:
            labels = {row['handle']: row['formulation'] for row in v7.list_target_labels(envelope['native'])}
            index = select_finding_index(index, [labels[handle] for handle in page['targets']])
        envelope['finding_index'] = index
        state = json.loads(artifacts.read(assignment['reading_chain']['state']))
        binding['entries'] = [copy.deepcopy(row) for row in state['entries']
                              if row['status'] in ('verified', 'delivered')]
    envelope['reading_final'] = binding
    return envelope


def list_page_outputs(state):
    rows = [row for row in state.get('final_progress', {}).values() if row['kind'] in ('page', 'final') and row['terminal']]
    rows.sort(key=lambda row: int(row['targets'][0][1:]))
    return [dict(targets=row['targets'], output=row['output']) for row in rows]


def build_final_packet(records, artifacts, route, logical, chain, number, inventory, state, plan, limits,
                       page_plan, turn, *, catalog=None, full=None):
    from . import openness_reading_v7 as v7
    if catalog is None:
        _, catalog, full = build_final_inputs(artifacts, route, logical, chain, number, inventory, state, plan, limits,
                                              turn['context']['page_room'])
    rule_packet = dict(native=logical['native'], reading_final=dict(protocol=inventory['protocol'],
        page=dict(targets=plan['targets']['handles']), page_plan_digest=digest(page_plan)))
    handle_assignment = dict(capacity_plan=dict(protocol=capacity.CAPACITY_PLAN_PROTOCOL, digest=digest(plan),
        artifact=artifacts.put(serialize_record(plan), 'application/json')), reading_chain=dict(
        inventory=artifacts.put(serialize_record(inventory), 'application/json'),
        state=artifacts.put(serialize_record(state), 'application/json')))
    rule_packet['handle_table'] = v7.build_handle_packet(full, artifacts, handle_assignment)['handle_table']
    rule = composition.find_rule('native_math', rule_packet)
    rows = composition.build_decision_rows(rule, rule_packet, list_page_outputs(state),
        measure=lambda row: capacity.measure_capacity_value(route, dict(handle='decision:' + row[0], value=row)) + 1)
    for row in rows:
        catalog['decision:' + row[0]] = dict(handle='decision:' + row[0], value=row)
    for handle, value in state.get('final_catalog', {}).items():
        catalog.setdefault(handle, value)
    for handle, value in state.get('combinations', {}).items():
        catalog[handle] = copy.deepcopy(value)

    def read_values(entries):
        return [copy.deepcopy(catalog[row['handle']]) for row in entries]

    binding = full['reading_final']
    recorded_material = copy.deepcopy(binding['material'])
    recorded_sources = copy.deepcopy(binding['sources'])
    recorded_gaps = copy.deepcopy(binding['gaps'])
    binding.update(page_plan_digest=digest(page_plan), final_turn=turn['number'])
    binding['capacity'] = dict(output_tokens=plan['limits']['output_tokens']['value'], answer_bound=limits['answer_bound'],
        material_allowance=min(turn['context']['page_room'], limits['material_allowance']),
        level_material_allowance=limits['material_allowance'])
    binding['fill'] = []
    unread_value = None
    requests = state.get('pending_requests', [])
    if turn['kind'] == 'combine':
        block = copy.deepcopy(turn['block'])
        block['artifact'] = artifacts.put(serialize_record(dict(block=turn['block'], entries=[dict(handle=row['handle'], value=value)
            for row, value in zip(turn['entry_sizes'], read_values(turn['entry_sizes']))])), 'application/json')
        binding = {key: binding[key] for key in ('protocol', 'chain', 'turn', 'plan_digest', 'inventory_digest',
            'state_digest', 'subject_digest', 'claim_digest', 'reviewer', 'page_plan_digest', 'final_turn', 'capacity')}
        binding['block'] = dict(block, entries=read_values(turn['entry_sizes']))
        binding['capacity']['answer_bound'] = condensing.describe_combination_bound(turn['block'], limits['answer_bound'])
        packet = {key: full[key] for key in ('protocol', 'role', 'reviewer_id', 'dossier_id')}
        packet.update(reading_combine=binding, evidence=[])
    else:
        unread_value = v7.read_unread_value(artifacts, state)
        binding['notes'] = binding['notes'] if turn['context']['notes'] else None
        common = read_values(turn['pool'])
        binding['material'] = [row for row in common if isinstance(row, dict) and row.get('handle', '').startswith('m')]
        binding['entries'] = [row for row in common if row not in binding['material']]
        binding['gaps'], binding['sources'] = [], []
        if turn['kind'] in ('page', 'final'):
            binding['page'] = dict(number=next(index for index, row in enumerate(
                [row for row in page_plan['turns'] if row['kind'] in ('page', 'final')], 1) if row['number'] == turn['number']),
                pages=sum(row['kind'] in ('page', 'final') for row in page_plan['turns']), targets=turn['targets'])
            for part in turn['parts']:
                binding['entries'].extend(read_values(part['entries']))
                for entry in part['rows']:
                    value = catalog[entry['handle']]
                    binding['sources' if entry['handle'].startswith('source:') else 'gaps'].append(copy.deepcopy(value))
            if unread_value is not None:
                unread = v7.describe_unread_packet(artifacts, state, allowance=0, route=route,
                                                   unread_value=unread_value)
                binding['unread_summary'] = {key: copy.deepcopy(unread[key]) for key in ('digest', 'count', 'causes')}
        else:
            binding['material'] = recorded_material
            if capacity.measure_capacity_value(route, binding['material']) > turn['context']['page_room'] - 6700:
                common_turn = next((row for row in reversed(page_plan['turns']) if row['kind'] in ('page', 'final')), None)
                binding['material'] = read_values(common_turn['pool']) if common_turn is not None else []
            binding = {key: binding[key] for key in ('protocol', 'chain', 'turn', 'plan_digest', 'inventory_digest',
                'state_digest', 'subject_digest', 'claim_digest', 'reviewer', 'page_plan_digest', 'final_turn', 'capacity',
                'material', 'allowances')}
            binding['capacity']['answer_bound'] = min(limits['answer_bound'], rule['limits']['closing'])
            binding['closing'] = dict(rows=[value['value'] if isinstance(value, dict) and value.get('handle', '').startswith('decision:')
                                          else value for value in read_values(turn['rows'])])
            if state.get('material_state', {}).get('pending'):
                binding['material_request'] = copy.deepcopy(state['material_request'])
            # The decision rows, the retained material, the unread rows and a pending delivery share one allowance.
            used = (capacity.measure_capacity_value(route, binding['closing'])
                    + capacity.measure_capacity_value(route, binding['material']))
            if unread_value is not None:
                room = min(turn['context']['closing_room'] - turn['characters'],
                           binding['capacity']['material_allowance'] - used)
                binding['unread'] = v7.describe_unread_packet(artifacts, state, route=route, unread_value=unread_value,
                    allowance=max(0, room) - (v7.FINAL_DELIVERY_ROOM_CHARACTERS if requests else 0))
                used += v7.compute_unread_rows_charge(route, binding['unread'])
        if turn['kind'] in ('page', 'final'):
            original_handles = {row['handle'] for item in page_plan['turns'] for target in item['inputs']
                                for row in target['entries'] + target['rows']}
            for entry in state['entries']:
                if (entry['handle'] not in original_handles and entry['handle'] not in state['pool']
                        and entry['status'] in ('verified', 'delivered') and set(entry['targets']) & set(turn['targets'])):
                    binding['entries'].append(copy.deepcopy(entry))
            original_pool = {row['handle'] for item in page_plan['turns'] for row in item['pool_inputs']}
            binding['material'].extend(copy.deepcopy(row) for row in recorded_material
                if row['handle'] not in original_pool and row not in binding['material'])
            binding['gaps'].extend(copy.deepcopy(row) for row in recorded_gaps
                if row['handle'] not in original_handles and row.get('target', row.get('formulation')) in turn['targets'])
            units = {row['unit'] for row in binding['entries'] if isinstance(row, dict) and 'unit' in row}
            binding['sources'].extend(row for row in recorded_sources if row['handle'] in units and row not in binding['sources'])
            if not state.get('pending_requests'):
                material = {key: binding[key] for key in v7.FINAL_MATERIAL_FIELDS}
                for note in reversed(state['notes'][:-1]):
                    if note['classification'] != 'current':
                        continue
                    candidate = dict(material, fill=material['fill'] + [dict(handle='n%d' % note['turn'], text=note['text'])])
                    if v7._measure_final_material(route, candidate) > binding['capacity']['material_allowance']:
                        break
                    material = candidate
                binding['fill'] = material['fill']
        full['reading_final'] = binding
        full['evidence'] = []
        packet = full
    if requests and turn['kind'] != 'combine':
        if turn['kind'] != 'closing':
            used = v7._measure_final_material(route, {key: binding.get(key, []) for key in v7.FINAL_MATERIAL_FIELDS})
        now, evidence, _ = v7._pack_deliveries(artifacts, route, inventory, dict(state, _plan=plan),
            max(0, binding['capacity']['material_allowance'] - used), requests, include_remaining=False,
            unread_value=unread_value)
        binding['plan'] = dict(now=now)
        packet['evidence'] = evidence
    packet.pop('digest', None)
    if 'finding_index' in packet and turn['kind'] in ('page', 'final'):
        labels = {row['handle']: row['formulation'] for row in v7.list_target_labels(logical['native'])}
        packet['finding_index'] = v7.fit_packet_finding_index(route, inventory, plan['finding_index'],
                                                              formulations=[labels[key] for key in turn['targets']])
    if turn['kind'] != 'combine':
        v7.append_turn_members(binding, plan, 'final', state, artifacts, unread_value=unread_value)
    packet['digest'] = digest(packet)
    return packet


def project_final_output(packet, output, projection, state):
    """Store terminal answers separately from intermediate requests and material parts."""
    binding = read_binding(packet)
    if 'page_plan_digest' not in binding:
        return projection
    kind = 'combine' if 'reading_combine' in packet else 'closing' if 'closing' in binding else 'page'
    terminal = output.get('stage') not in ('reading', 'material')
    progress = dict(assignment_id=projection['turn']['assignment_id'], attempt_id=projection['turn']['attempt_id'],
        kind=kind, targets=binding.get('page', {}).get('targets', []), terminal=terminal, output=copy.deepcopy(output))
    projection['final_progress'] = {str(binding['final_turn']): progress}
    if kind == 'combine' and terminal:
        projection['combinations'] = {output['block']: copy.deepcopy(output)}
        projection['final_catalog'] = {}
        # The artifact stores the handle beside its value; packets carry the
        # value's own handle (or its block handle) without repeating it.
        projection['final_blocks'] = {output['block']: binding['block']['artifact']}
    return projection


def list_composite_turns(records, artifacts, assignment):
    from . import openness_reading as reading
    reference = assignment.get('reading_chain', {}).get('final_page_plan')
    if reference is None:
        return None
    chain = reading._list_chain(records, assignment)
    # A member of an earlier plan authenticates against the final's latest plan.
    first = assignment['reading_chain']['first_assignment_id']
    members = [row for row in records['review_assignment'].values()
               if row.get('reading_chain', {}).get('first_assignment_id') == first]
    latest = max(members, key=lambda row: row['recorded_revision'])
    page_plan = read_page_plan(artifacts, latest['reading_chain']['final_page_plan'])
    selected = {}
    from .review_protocol import read_assignment_output
    for member in sorted(members, key=lambda row: row['recorded_revision']):
        packet = json.loads(artifacts.read(member['packet']))
        binding = read_binding(packet)
        if 'page_plan_digest' in binding:
            attempt, output = read_assignment_output(records, artifacts, member['id'])
            if output is not None and output.get('stage') not in ('reading', 'material'):
                selected[binding['final_turn']] = member['id']
    turns = dict(chain=first, pages=[], condensing=[], closing=None)
    for turn in page_plan['turns']:
        row = dict(assignment_id=selected.get(turn['number'], latest['id']), targets=turn['targets'])
        if turn['kind'] in ('page', 'final'):
            turns['pages'].append(row)
        elif turn['kind'] == 'combine':
            turns['condensing'].append(row)
        else:
            turns['closing'] = row
    turns['packet'] = build_validation_envelope(artifacts, latest, json.loads(artifacts.read(latest['packet'])),
        capacity.read_chain_capacity_plan(artifacts, latest)['targets']['handles'])
    # Expanded page references need no live handle lookup during composition.
    turns['packet']['handle_table'] = {}
    return turns


def read_composite_currency(records, artifacts, assignment):
    page_plan = read_page_plan(artifacts, assignment['reading_chain']['final_page_plan'])
    packet = json.loads(artifacts.read(assignment['packet']))
    native = json.loads(artifacts.read(assignment['context']['native_packet']))
    inventory = json.loads(artifacts.read(assignment['reading_chain']['inventory']))
    currency = read_final_currency(native, inventory)
    if page_plan['currency'] != currency or read_binding(packet)['page_plan_digest'] != digest(page_plan):
        raise ResearchError('artifact_corrupt', PAGE_PLAN_INVALID_MESSAGE)
    return currency


def validate_composite_output(records, artifacts, assignment, packet, output):
    from .openness_reading_v7 import describe_protocol
    from .review_refusal import refuse_unreadable_answer
    try:
        with refuse_unreadable_answer():
            describe_protocol(packet=packet)['validate_final'](packet, output, artifacts=artifacts, assignment=assignment)
    except ResearchError as error:
        error.details['composed_finding'] = True
        raise


def prepare_closing_composite(records, artifacts, assignment, attempt, output):
    """The closing observation and its valid composite share one store mutation."""
    from . import review_composite
    from .operations import immutable_record
    packet = json.loads(artifacts.read(assignment['packet']))
    if 'closing' not in packet.get('reading_final', {}):
        return []
    from .review_failure_class import classify_final_capacity_attempt
    if classify_final_capacity_attempt(records, artifacts, attempt) is not None:
        return []
    from .openness_reading_v7 import validate_turn_output
    from .review_handles import expand_observed_output
    from .review_refusal import contract_check
    output = expand_observed_output(packet, output, attempt, artifacts=artifacts, assignment=assignment)
    with contract_check('native.output'):
        validate_turn_output(packet, output, artifacts=artifacts, assignment=assignment, expanded=True)
    if output.get('stage') in ('reading', 'material'):
        return []
    composite, (reference, content) = review_composite.prepare_composite(records, artifacts, assignment, attempt, output)
    artifacts.put(content, reference['media_type'])
    return [immutable_record(records, 'review_composite', composite['id'], composite)]


def describe_composed_refusal(records, artifacts, attempt, check, path):
    """Locate a composed field's owning target using only this reviewer's answers."""
    certificate = records.get('review_output_refusal', {}).get(attempt['id'], {})
    if check != 'openness.finding' or not certificate.get('details', {}).get('composed_finding'):
        return None
    assignment = records['review_assignment'][attempt['assignment_id']]
    packet = json.loads(artifacts.read(assignment['packet']))
    if 'closing' not in packet.get('reading_final', {}):
        return None
    result = dict(check=check, path=path, closing_answer=json.loads(artifacts.read(attempt['output'])),
                  message=COMPOSED_REFUSAL_TEXT)
    state = json.loads(artifacts.read(assignment['reading_chain']['state']))
    outputs = list_page_outputs(state)
    items = [item for page in outputs for item in page['output']['targets']]
    components = path.split('/')
    target_index = None
    if len(components) > 4 and components[1:3] == ['findings', 'openness'] and components[4].isdigit():
        index = int(components[4])
        owned = items if components[3] == 'coverage' else [item for item in items for _ in item.get('grounds', [])] if components[3] == 'grounds' else []
        if index < len(owned):
            target_index = items.index(owned[index])
    if target_index is not None:
        from .openness_reading_v7 import build_handle_packet
        envelope = build_validation_envelope(artifacts, assignment, packet,
            capacity.read_chain_capacity_plan(artifacts, assignment)['targets']['handles'])
        envelope['handle_table'] = build_handle_packet(packet, artifacts, assignment)['handle_table']
        rule = composition.find_rule(assignment['role'], envelope)
        route = records['review_route'][assignment['route_id']]
        rows = composition.build_decision_rows(rule, envelope, outputs, measure=lambda row:
            capacity.measure_capacity_value(route, dict(handle='decision:' + row[0], value=row)) + 1)
        result['decision_row'] = rows[target_index]
    return result


def verify_final_page_plans(records, artifacts, assignment, native, inventory, plan, route):
    """Replay each immutable plan with the state and observations that preceded it."""
    from . import openness_reading as reading
    chain = sorted((member for member in records['review_assignment'].values()
        if member.get('reading_chain', {}).get('first_assignment_id') == assignment['reading_chain']['first_assignment_id']
        and member['recorded_revision'] <= assignment['recorded_revision']), key=lambda row: row['recorded_revision'])
    values, owners = [], {}
    for member in chain:
        reference = member['reading_chain'].get('final_page_plan')
        if reference is not None:
            for ancestor in reference.get('predecessors', []) + [reference]:
                if ancestor['digest'] not in owners:
                    values.append(read_page_plan(artifacts, ancestor))
                    owners[ancestor['digest']] = member
    if not values:
        return

    def read_inputs(value):
        member = owners[digest(value)]
        state = json.loads(artifacts.read(member['reading_chain']['state']))
        if (value['state_digest'] != digest(state) or value['plan_digest'] != digest(plan)
                or value['chain'] != chain[0]['id'] or value['currency'] != read_final_currency(native, inventory)):
            raise ResearchError('artifact_corrupt', PAGE_PLAN_INVALID_MESSAGE)
        historical = dict(records, review_attempt={key: row for key, row in records['review_attempt'].items()
            if row['recorded_revision'] < member['recorded_revision']})
        prior_id = member.get('capacity_replacement_of') or member['context'].get('output_refusal', {}).get('assignment_id') or member['context'].get('prior_assignment_id') or member['context']['unread']['assignment_id']
        prior = records['review_assignment'][prior_id]
        if 'cause' not in value and member.get('capacity_replacement_of') and not prior['reading_chain'].get('final_page_plan'):
            historical = dict(records, review_attempt={key: row for key, row in records['review_attempt'].items()
                if row['recorded_revision'] < prior['recorded_revision']})
        limits = capacity.derive_turn_task_limits(historical, artifacts, prior, kind='final')
        room = measure_page_room(historical, artifacts, prior, plan, limits)
        logical = dict(protocol=json.loads(artifacts.read(member['packet']))['protocol'], role=member['role'],
            dossier_id=member['dossier_id'], reviewer_id=member['reviewer_id'], native=native)
        inputs, _, _ = build_final_inputs(artifacts, route, logical, chain[0]['id'], member['reading_chain']['turn'],
                                          inventory, state, plan, limits, room)
        if 'cause' in value:
            previous = values[values.index(value) - 1]
            completed, pending = list_final_progress(state, previous)
            cause = value['cause']
            attempt = historical['review_attempt'].get(cause['attempt_id'])
            if (cause['completed'] != completed or cause['pending'] != pending or attempt is None
                    or cause['attempt_id'] != state['turns'][-1]['attempt_id'] and attempt['assignment_id'] != prior_id):
                raise ResearchError('artifact_corrupt', PAGE_PLAN_INVALID_MESSAGE)
        return dict(inputs, floor=min(row['material_allowance'] for row in plan['task']['final']['levels']))
    pages.verify_page_plans(values, read_inputs)


def verify_final_turn_binding(records, artifacts, assignment, packet, logical, inventory, state, plan, limits):
    """Page targets, block entries and decision rows are functions of pinned artifacts."""
    reference = assignment['reading_chain']['final_page_plan']
    page_plan = read_page_plan(artifacts, reference)
    binding = read_binding(packet)
    if binding['page_plan_digest'] != digest(page_plan) or page_plan['currency'] != read_final_currency(logical['native'], inventory):
        raise ResearchError('artifact_corrupt', PAGE_PLAN_INVALID_MESSAGE)
    selected = next((turn for turn in page_plan['turns'] if turn['number'] == binding['final_turn']), None)
    if selected is None:
        raise ResearchError('artifact_corrupt', PAGE_PLAN_INVALID_MESSAGE)
    position = read_final_position(artifacts, reference, selected['number'])
    if assignment['reading_chain'].get('final_position', position) != position:
        raise ResearchError('artifact_corrupt', PAGE_PLAN_INVALID_MESSAGE)
    expected = build_final_packet(records, artifacts, records['review_route'][assignment['route_id']], logical,
        assignment['reading_chain']['first_assignment_id'], assignment['reading_chain']['turn'], inventory, state, plan,
        limits, page_plan, selected)
    if packet.get('output_refusal'):
        from .review_refusal_notice import attach_refusal_notice
        expected = attach_refusal_notice(expected, packet['output_refusal'])
    if expected != packet:
        raise ResearchError('artifact_corrupt', PAGE_PLAN_INVALID_MESSAGE)


def supply_block_rows(plan, state, artifacts, handle):
    block = json.loads(artifacts.read(state['final_blocks'][handle]))
    return [dict(id=row['handle'], handle=row['handle'],
                 characters=len(serialize_record(row['value']).decode('utf-8'))) for row in block['entries']]


def resolve_entry_request(state, artifacts, request):
    handle = request['unit']
    value = state.get('final_catalog', {}).get(handle, state.get('combinations', {}).get(handle))
    if value is None:
        return None
    content = serialize_record(value)
    reference = artifacts.put(content, 'application/json')
    return dict(handle=handle, kind='final_entry', label=handle, artifact=reference,
                sha256=reference['sha256'], characters=len(content.decode('utf-8')), units=[])
