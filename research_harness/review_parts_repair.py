"""Context reassessments over fresh inputs, wrapping the registered role adapter."""

import copy
import json

from . import review_parts as parts, review_parts_prompts as prompts
from .evidence import digest
from .errors import ResearchError


def applies(value, packet):
    return (value.get('role') in ('bar', 'slate', 'result', 'adjudicator')
            and 'context_repair' in packet)


def find_role_adapter(packet):
    from .review_parts_roles import find_adapter, get_adapter

    ordinary = dict(packet)
    ordinary.pop('context_repair', None)
    value = dict(role=packet.get('role'), context={})
    name = find_adapter(value, ordinary)
    return get_adapter(name) if name is not None else None


def list_role_units(packet):
    adapter = find_role_adapter(packet)
    if adapter is None:
        return []
    sources = copy.deepcopy(adapter['enumerate_units'](packet))
    for source in sources:
        if 'citations' in adapter:
            source['citations'] = adapter['citations'](source)
    # An inherited final, including its exact payload and qualified objections,
    # is a frame obligation, never a projected collection.
    return [source for source in sources if all(occurrence['path'][0] != 'historical_reviews'
                                               for occurrence in source['occurrences'])]


def prepare_history(records, artifacts, packet, history=None):
    """Turn historical packets' frames and outputs into independently citable originals."""
    from .review_protocol import _evidence
    from .review_supplied_evidence import list_delivered_parts
    from .review_transport import OMITTED_BYTES_NOTE

    logical = copy.deepcopy(packet)
    history = logical.pop('scientific_history', []) if history is None else history
    logical.pop('scientific_history', None)
    logical.pop('previously_supplied_evidence', None)
    logical.pop(parts.KEY, None)
    logical.pop(parts.STATE_KEY, None)
    logical.pop('problem_evidence_parts', None)
    logical.pop('staged_review', None)
    logical.pop('digest', None)
    logical.pop('evidence', None)
    logical['evidence'] = _evidence(artifacts, logical, records=records)
    bodies = {digest(entry['artifact']): entry for entry in logical['evidence']}
    owned = {body['artifact']['sha256'] for source in list_role_units(logical)
             for body in source.get('bodies', [])}
    frame_bodies = {entry['artifact']['sha256'] for entry in logical['evidence']
                    if entry['artifact']['sha256'] not in owned}
    mappings = list(logical.get('prior_context', []))
    turns = []
    for ancestor in history:
        actual = ancestor['packet']
        original = actual
        if parts.find_kind(actual) is not None:
            original = parts.read_logical_packet(artifacts, actual[parts.KEY])
        # Keep citation bounds visible to the delivery reader. The excerpt
        # bytes still have to arrive through their owning historical units.
        mappings.extend(original.get('prior_context', []))
        mappings.extend(original.get('supplied_source_mappings', []))
        mappings.extend(original.get('version_source_mappings', []))
        mappings.extend(list_delivered_parts(original))
        sources = list_role_units(original)
        frame = parts.build_frame(original, sources)
        ancestor_bodies = [entry for entry in frame.pop('evidence', [])
                           if entry['artifact']['sha256'] not in frame_bodies]
        if parts.find_kind(actual) is not None:
            frame[parts.KEY] = copy.deepcopy(actual[parts.KEY])
            frame[parts.STATE_KEY] = copy.deepcopy(actual[parts.STATE_KEY])
            frame['inventory'] = parts.read_inventory(
                artifacts, actual[parts.KEY]['inventory'], actual[parts.KEY]['inventory_digest'])
        turn = dict(assignment_id=ancestor['assignment_id'],
                    independence=ancestor.get('independence', 'no_longer_qualified'),
                    packet=dict(units=[], bindings=[], frames=[]), outputs=[])
        for source in sources:
            turn['packet']['units'].append(copy.deepcopy(source['item']))
            binding = {key: copy.deepcopy(source[key]) for key in
                       ('occurrences', 'citations', 'carded_under') if key in source}
            binding['bodies'] = [copy.deepcopy(entry['artifact']) for entry in source.get('bodies', [])
                                 if entry['artifact']['sha256'] not in frame_bodies]
            turn['packet']['bindings'].append(binding)
            for entry in source.get('bodies', []):
                bodies[digest(entry['artifact'])] = entry
        texts = [('frames', (frame, ancestor_bodies))]
        texts.extend(('outputs', (ancestor[key], [])) for key in
                     ('observed_output', 'intermediate_output', 'raw_observations',
                      'effective_binding', 'selected_observations', 'intermediate_findings')
                     if ancestor.get(key) is not None)
        for collection, (content, owned) in texts:
            rendered = parts.render_json(content)
            reference = artifacts.put(rendered.encode('utf-8'), 'application/json')
            entry = dict(artifact=reference, content=rendered, encoding='utf-8')
            bodies[digest(reference)] = entry
            for body in owned:
                bodies[digest(body['artifact'])] = body
            target = turn['packet']['frames'] if collection == 'frames' else turn['outputs']
            target.append(dict(artifact=reference, bodies=[body['artifact'] for body in owned]))
        turns.append(turn)
    logical['scientific_history'] = turns
    if mappings:
        logical['prior_context'] = list({digest(mapping): mapping for mapping in mappings}.values())
    # Every repair parts route is text only. Keep native descriptors and their
    # explicit omissions, including bodies inherited from a native route.
    logical['evidence'] = [dict(entry, content='', encoding='omitted',
        delivery='bytes-omitted-on-text-route', note=OMITTED_BYTES_NOTE)
        if entry.get('encoding') == 'base64' else entry for entry in bodies.values()]
    logical['digest'] = digest(logical)
    return logical


def list_units(packet):
    units = list_role_units(packet)
    bodies = {digest(entry['artifact']): entry for entry in packet.get('evidence', [])}
    for index, turn in enumerate(packet.get('scientific_history', [])):
        history_packet = turn['packet']
        for number, (item, binding) in enumerate(zip(history_packet['units'], history_packet['bindings'])):
            source = dict(item=item, occurrences=[dict(path=['scientific_history', index, 'packet', 'units'],
                                                      index=number)],
                          citations=binding.get('citations', []),
                          bodies=[bodies[digest(reference)] for reference in binding['bodies']])
            if binding.get('carded_under'):
                source['carded_under'] = binding['carded_under']
            units.append(source)
        for collection, path in ((history_packet['frames'], ['scientific_history', index, 'packet', 'frames']),
                                 (turn['outputs'], ['scientific_history', index, 'outputs'])):
            for number, item in enumerate(collection):
                references = [item['artifact']] + item['bodies']
                units.append(dict(item=item, occurrences=[dict(path=path, index=number)],
                                  citations=[item['artifact']],
                                  bodies=[bodies[digest(reference)] for reference in references]))
    for unit in units:
        unit['bodies'] = [body for body in unit.get('bodies', []) if body.get('encoding') != 'omitted']
    supporting = list_supporting_digests(packet, units)
    units.sort(key=lambda source: digest(source['item']) not in supporting)
    return units


def list_supporting_digests(packet, units):
    pending = [packet.get('historical_reviews', [])]
    artifact_digests, citation_digests = set(), set()
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if 'sha256' in value and ('path' in value or 'location' in value):
                artifact_digests.add(value['sha256'])
            if value.get('kind') in ('record', 'source'):
                citation_digests.add(digest(value))
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return {digest(unit['item']) for unit in units
            if any(body['artifact']['sha256'] in artifact_digests for body in unit.get('bodies', []))
            or any(digest(citation) in citation_digests for citation in unit.get('citations', []))}


def prioritize_fill(packet, inventory, planned, measure, input_limit):
    """Keep requested originals first, then the inherited finals' supporting originals."""
    if parts.find_kind(planned) != 'final':
        return planned
    units = parts.enumerate_units(packet, 'context_repair', inventory)
    supporting = list_supporting_digests(packet, units)
    if not supporting:
        return planned
    header = copy.deepcopy(planned[parts.KEY])
    answered = header.get('answered', [])
    answered_handles = {entry['unit'] for entry in answered}
    priorities = answered + [dict(unit=unit['handle']) for unit in units
                            if unit['digest'] in supporting and unit['handle'] not in answered_handles]
    priorities += header['fill']
    priorities += [dict(unit=unit['handle']) for unit in units if unit['handle'] not in answered_handles]
    priorities = list({digest(original): original for original in priorities}.values())
    frame = parts.build_frame(packet, units)
    allowance = header['original_characters']
    while allowance >= 0:
        selections, characters = parts.select_originals(units, allowance, requests=priorities)
        if any(not parts.covers_original(selections, original, inventory) for original in answered):
            return planned
        header['fill'] = parts.describe_selections([entry for entry in selections
                                                   if entry['unit'] not in answered_handles])
        header['original_characters'] = characters
        candidate = parts.project_packet(frame, units, selections, header, planned[parts.STATE_KEY])
        measured = measure(candidate)
        if measured <= input_limit:
            return candidate
        if allowance == 0:
            return planned
        allowance = min(allowance - 1, max(allowance // 2, allowance - (measured - input_limit)))
    return planned


def list_targets(packet):
    adapter = find_role_adapter(packet)
    targets = copy.deepcopy(adapter['targets'](packet)) if adapter is not None else []
    seen = {row['id'] for row in targets}
    for review in packet.get('historical_reviews', []):
        if review['id'] not in seen:
            targets.append(dict(id=review['id'], label=review['id']))
            seen.add(review['id'])
    return targets


def compute_series_key(packet):
    adapter = find_role_adapter(packet)
    if adapter is not None:
        role_key = adapter['series_key'](packet)
    else:
        frame = parts.build_frame(packet, list_units(packet))
        for key in ('continuation', 'reviewer_excerpts', 'source_deliveries',
                    'pending_source_requests', 'prior_context', 'evidence'):
            frame.pop(key, None)
        role_key = digest(frame)
    return dict(role=role_key, repair=digest(packet['context_repair']))


def build_final_prompt(role, parameters):
    packet = dict(role=role, subject=dict(kind='problem' if parameters.get('orchestration') ==
                                         'problem_creation' else 'study'))
    adapter = find_role_adapter(packet)
    texts = [adapter['final_prompt']] if adapter is not None else []
    return '\n\n'.join(texts + [prompts.FINAL_REPAIR])


def validate_final(records, artifacts, assignment, output, *, attempt=None):
    from .research_decisions import validate_review_contract

    packet = json.loads(artifacts.read(assignment['packet']))
    if parts.find_kind(packet) is not None:
        packet = parts.read_logical_packet(artifacts, packet[parts.KEY])
    adapter = find_role_adapter(packet)
    validator = adapter['validate_final'] if adapter is not None else validate_review_contract
    return validator(records, artifacts, assignment, output, attempt=attempt)


def has_over_cap_attempt(records, value, slot_id, predecessor=None):
    """Only a completed invocation of this repair binding can force a fresh series."""
    from .review_context_repair import _lineage, slot_id as find_slot

    predecessor = predecessor or value.get('context', {}).get('context_repair', {}).get('assignment_id')
    if predecessor is None:
        return False
    for assignment in records.get('review_assignment', {}).values():
        if (assignment.get('role') != value['role'] or assignment.get('dossier_id') != value['dossier_id']
                or find_slot(records, assignment) != slot_id):
            continue
        binding = next((ancestor['context']['context_repair']
                        for ancestor in reversed(_lineage(records, assignment))
                        if 'context_repair' in ancestor.get('context', {})), {})
        if binding.get('assignment_id') != predecessor:
            continue
        maximum = records['review_route'][assignment['route_id']]['configuration']['max_output_tokens']
        for attempt in records.get('review_attempt', {}).values():
            if attempt['assignment_id'] != assignment['id']:
                continue
            if ((attempt.get('error') or {}).get('code') == 'review_incomplete'
                    or ((attempt.get('usage') or {}).get('output_tokens') or 0) >= maximum):
                return True
    return False


def has_exhausted_over_cap_repair(records, artifacts, assignment):
    from .review_context_repair import _lineage
    from .review_refusal import refused_attempts

    if not any('context_repair' in ancestor.get('context', {})
               for ancestor in _lineage(records, assignment)):
        return False
    attempts = [attempt for attempt in records.get('review_attempt', {}).values()
                if attempt['assignment_id'] == assignment['id']]
    if any(invocation['assignment_id'] == assignment['id']
           and invocation['id'] not in records.get('review_attempt', {})
           for invocation in records.get('review_invocation', {}).values()):
        return False
    completed = [attempt for attempt in attempts if attempt['status'] == 'completed']
    maximum = records['review_route'][assignment['route_id']]['configuration']['max_output_tokens']
    return (any((attempt.get('error') or {}).get('code') == 'review_incomplete'
                or ((attempt.get('usage') or {}).get('output_tokens') or 0) >= maximum
                for attempt in attempts)
            and len(refused_attempts(records, artifacts, completed)) == len(completed))


def build_continuation_packet(records, artifacts, value, parent):
    from .review_context_repair import _lineage
    from .review_protocol import build_packet, _evidence

    frozen = json.loads(artifacts.read(parent['packet']))
    previous = parts.read_logical_packet(artifacts, frozen[parts.KEY])
    context = dict(review_ids=[review['id'] for review in previous['assessments'][:2]],
                   objection_id=previous['objection']['id'])
    if previous.get('correction') is not None:
        origin = next(ancestor for ancestor in reversed(_lineage(records, parent))
                      if ancestor.get('context', {}).get('correction') is not None)
        context['correction'] = copy.deepcopy(origin['context']['correction'])
    packet = build_packet(records, artifacts, value['dossier_id'], value['role'],
                          value['reviewer_id'], **context)
    for key in ('context_repair', 'scientific_history', 'historical_reviews'):
        packet[key] = copy.deepcopy(previous[key])
    packet.pop('evidence', None)
    packet.pop('digest', None)
    packet['evidence'] = _evidence(artifacts, packet, records=records)
    packet['digest'] = digest(packet)
    return packet


def _supports_parts_route(records, value, route, packet):
    from . import review_transport as transport

    if (route['adapter'] not in ('claude_cli_v1', 'codex_cli_v1', 'codex_cli_framed_v1')
            or transport.local_input_limit(route) is None):
        if (value['role'] == 'adjudicator' and (packet.get('subject') or {}).get('kind') == 'problem'
                and packet.get('context_repair') is not None):
            predecessor = records.get('review_assignment', {}).get(
                packet['context_repair'].get('assignment_id'), {})
            if predecessor.get('staged_review') is None:
                raise ResearchError('review_parts_route_unsupported',
                    'Problem adjudicator repairs without a staged predecessor require a supported parts v2 '
                    'route with a local input limit')
        return False
    return True


def requires_parts(records, artifacts, value, route, packet, prompt, history, slot_id):
    from . import review_transport as transport
    from .review_problem_parts import _measure, preserves_ordinary_packet

    if not _supports_parts_route(records, value, route, packet):
        return False
    ordinary = transport.request_body(route, prompt['text'], packet, history)
    return (has_over_cap_attempt(records, value, slot_id, packet.get('context_repair', {}).get('assignment_id'))
            or value['role'] == 'adjudicator' and packet.get('context_repair') is not None
            and not packet.get('staged_review')
            or _measure(route, ordinary) > transport.local_input_limit(route)
            or not preserves_ordinary_packet(artifacts, packet, ordinary))


def prepare_packet(records, artifacts, value, route, packet, prompt, history, *,
                   base_prompt_parameters, slot_id=None, repair=None):
    """Plan a repair using the engine, including entry after ordinary output overflow."""
    from . import review_transport as transport
    from .review_problem_parts import _measure

    if not _supports_parts_route(records, value, route, packet):
        return packet
    prior = records.get('review_assignment', {}).get(value.get('context', {}).get('prior_assignment_id'))
    continuing = prior is not None and prior.get(parts.KEY, {}).get('adapter') == 'context_repair'
    if not continuing and not requires_parts(records, artifacts, value, route, packet, prompt, history, slot_id):
        return packet
    if continuing:
        previous = parts.read_logical_packet(artifacts, json.loads(
            artifacts.read(prior['packet']))[parts.KEY])
        current = packet
        packet = dict(previous, **current)
        for key in ('scientific_history', 'historical_reviews', 'context_repair'):
            packet[key] = previous[key]
        evidence = {digest(entry['artifact']): entry for entry in previous['evidence']}
        evidence.update({digest(entry['artifact']): entry for entry in current.get('evidence', [])})
        packet['evidence'] = list(evidence.values())
        packet.pop('continuation', None)
        if not packet.get('prior_context') and previous.get('prior_context'):
            packet['prior_context'] = previous['prior_context']
    else:
        if repair is not None:
            packet = dict(packet, context_repair=dict(packet['context_repair'],
                          predecessor_binding=repair['predecessor_binding']))
            history = repair['scientific_history']
        elif prior is not None:
            from . import review_context_repair as context_repair
            from .review_protocol import assignment_state
            frozen = context_repair.frozen_reassessment_packet(records, artifacts, prior)
            chain = context_repair._exposure_lineage(records, prior)
            history = context_repair.collect_scientific_history(
                records, artifacts, chain, context_repair.ROLES + ('adjudicator',))
            predecessor = records['review_assignment'][packet['context_repair']['assignment_id']]
            identifiers = {ancestor['id'] for ancestor in context_repair._exposure_lineage(records, predecessor)}
            binding = context_repair.bind_predecessor(records, predecessor,
                assignment_state(records, artifacts, predecessor['id']),
                [turn for turn in history if turn['assignment_id'] in identifiers])
            packet = dict(frozen, **packet)
            packet.update(historical_reviews=frozen.get('historical_reviews', []),
                          context_repair=dict(frozen['context_repair'], predecessor_binding=binding))
            if (packet.get('subject', {}).get('kind') == 'problem' and value['role'] == 'result'
                    and packet.get('problem_evidence', {}).get('delivery') == 'delivered-in-an-earlier-turn'):
                from .review_protocol import add_problem_evidence
                add_problem_evidence(records, artifacts, packet)
            packet.pop('history_delivery', None)
        else:
            history = None
        packet = prepare_history(records, artifacts, packet, history)
    # Normalized history may itself fit. Entry was already decided using the
    # ordinary request, so plan directly rather than asking the entry test again.
    series_key = compute_series_key(packet)
    observations = parts.observe_series(records, artifacts, prior['id']) if continuing else []
    previous_inventory = observations[-1]['inventory'] if observations else None
    try:
        inventory = parts.build_inventory(packet, 'context_repair', previous_inventory)
    except ResearchError as error:
        if error.code != 'review_parts_inventory_changed':
            raise
        observations = []
        inventory = parts.build_inventory(packet, 'context_repair')
    if observations and observations[-1]['packet'][parts.KEY]['series_key'] != series_key:
        observations = []
        inventory = parts.build_inventory(packet, 'context_repair')
    packet = dict(packet)
    packet.pop('digest', None)
    packet['digest'] = digest(packet)
    logical_reference = artifacts.put(parts.render_json(packet).encode('utf-8'), 'application/json')
    inventory_reference = parts.store_inventory(artifacts, inventory)
    series_id = observations[0]['packet'][parts.KEY]['series_assignment_id'] if observations else value['id']
    replacement = parts.find_failed_replacement(records, artifacts, value, 'context_repair', series_key,
        slot_id, value.get('context', {}).get('prior_assignment_id'), route['configuration']['max_output_tokens'])

    def measure(candidate):
        turn_prompt = parts.build_turn_prompt(value['role'], base_prompt_parameters, candidate[parts.KEY])
        return _measure(route, transport.request_body(route, turn_prompt['text'], candidate, [], order=()))

    planned = parts.plan_turn(packet, inventory, observations, series_id=series_id,
        input_limit=transport.local_input_limit(route), measure=measure,
        output_tokens=route['configuration']['max_output_tokens'], replacement=replacement,
        inventory_reference=inventory_reference, logical_reference=logical_reference, series_key=series_key)
    planned = prioritize_fill(packet, inventory, planned, measure, transport.local_input_limit(route))
    for entry in planned.get('evidence', []):
        if entry.get('segment'):
            reference = artifacts.put(entry['content'].encode('utf-8'), entry['artifact']['media_type'])
            if reference != entry['artifact']:
                raise ResearchError('review_parts_delivery_invalid', 'The stored repair segment differs')
    return planned


ADAPTER = dict(applies=applies, enumerate_units=list_units, targets=list_targets,
               series_key=compute_series_key, validate_final=validate_final,
               covers_items=parts.covers_items, final_prompt=build_final_prompt,
               source_requests=True)
