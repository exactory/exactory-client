"""Original 0.65.2 packet builders, preserved to exercise authenticated historical continuations.

Copied from 448b6da, without replacing their record construction with v3 helpers.
"""

import json
from research_harness import review_excerpts, review_transport as transport
from research_harness.bar_sources import _reference, descriptor
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.openness_reading import _read_prior, find_stage, is_openness_packet

PROTOCOL = 'exactory-openness-reading-v2'
TURN_KEY = 'reading_turn'
RECORD_KEY = 'reading_record'

def list_reading_texts(records, native):
    """The stored extracted full text of each version that the readings of the openness minimum read, ordered by the
    packet's text_delivery_priority and then by version id."""
    reading_digests = set(native['scientific_context']['openness_minimum']['reading_digests'])
    versions = {reading['version_id'] for reading in records.get('reading', {}).values()
                if digest(reading) in reading_digests}
    priority = {sha256: index for index, sha256 in
                enumerate(native['scientific_context'].get('text_delivery_priority', []))}

    def find_rank(version):
        captured = [capture['text']['sha256'] for capture in records['work'][version]['fulltexts']
                    if capture.get('text')]
        return min((priority[sha256] for sha256 in captured if sha256 in priority), default=len(priority))
    texts = [{'version_id': version, 'artifact': review_excerpts.find_fulltext(records, version)}
             for version in versions]
    texts = [row for row in texts if row['artifact'] is not None]
    return sorted(texts, key=lambda row: (find_rank(row['version_id']), row['version_id']))


def list_reading_units(artifacts, texts):
    """The reading units of texts, in order: each text whole when it fits one turn's budget of new full text, otherwise
    its consecutive parts, each saved as its own text artifact. A unit is {version_id, part, parts, start, end,
    characters, artifact_descriptor}, with start and end as character offsets in the stored text."""
    units = []
    for row in texts:
        content = artifacts.read(row['artifact']).decode('utf-8')
        spans = review_excerpts.compute_part_spans(content)
        for part, (start, end) in enumerate(spans, 1):
            reference = review_excerpts.save_part(artifacts, content, row['artifact'], start, end)
            units.append({'version_id': row['version_id'], 'part': part, 'parts': len(spans), 'start': start,
                          'end': end, 'characters': end - start, 'artifact_descriptor': descriptor(reference)})
    return units


def _list_remaining_units(artifacts, remaining):
    """The units of a plan's remaining entries. A chain that 0.64.2 to 0.65.1 started lists whole texts without part
    fields; each of them is divided into units here."""
    units = []
    for row in remaining:
        if 'part' in row:
            units.append(row)
        else:
            units.extend(list_reading_units(artifacts, [{'version_id': row['version_id'],
                                                         'artifact': _reference(row['artifact_descriptor'])}]))
    return units


def _measure(route, request):
    """The number of input characters of a request as the route's adapter measures them."""
    return len(transport._local_adapter(route).packet_text(request))


def _fits(route, prompt, packet, artifacts_now):
    """Whether the route's actual fitted request for a packet fits its input limit and delivers every listed full text
    complete."""
    request = transport.request_body(route, prompt, packet)
    if _measure(route, request) > transport.local_input_limit(route):
        return False
    complete = review_excerpts.find_complete_bodies(review_excerpts.read_sent_packet(request) or {})
    return all(reference['sha256'] in complete for reference in artifacts_now)


def _assemble_reading_turn(artifacts, packet, record, delivered, now, remaining, turn):
    """A reading turn packet whose only evidence is the full texts and parts delivered now."""
    from research_harness.review_protocol import _evidence
    native = packet['native']
    minimum = native['scientific_context']['openness_minimum']
    plan = {'turn': turn, 'delivered_before': delivered, 'remaining': remaining,
            'delivered_now': [dict({key: item for key, item in row.items() if key != 'artifact_descriptor'},
                                   artifact=_reference(row['artifact_descriptor'])) for row in now]}
    value = {key: packet[key] for key in ('protocol', 'role', 'reviewer_id', 'dossier_id')}
    value[TURN_KEY] = dict({key: native[key] for key in ('subject_digest', 'claim_digest', 'reviewer')},
                           protocol=PROTOCOL, statement=native['subject'], reading_plan=plan, reading_record=record,
                           formulations={'statement': minimum['statement'], 'neighbours': minimum['neighbours']})
    value['evidence'] = _evidence(artifacts, value)
    value['digest'] = digest(value)
    return value


def _build_reading_turn(artifacts, route, packet, record, delivered, candidates, turn):
    """The next reading turn: the longest prefix of the remaining units, at least one, whose characters fit one turn's
    budget of new full text and whose actual fitted request fits the route and delivers each of them complete."""
    from research_harness.review_protocol import canonical_prompt
    prompt = canonical_prompt('native_math', reading_stage='reading')['text']
    chosen = None
    for count in range(1, len(candidates) + 1):
        characters = sum(row['characters'] for row in candidates[:count])
        if count > 1 and characters > review_excerpts.TURN_FULLTEXT_CHARACTERS:
            break
        trial = _assemble_reading_turn(artifacts, packet, record, delivered, candidates[:count], candidates[count:],
                                       turn)
        if not _fits(route, prompt, trial, [_reference(row['artifact_descriptor']) for row in candidates[:count]]):
            break
        chosen = trial
    if chosen is None:
        first, limit = candidates[0], transport.local_input_limit(route)
        raise ResearchError('review_input_too_large',
                            'The full text of ' + first['version_id'] + ' has ' + str(first['characters'])
                            + ' characters and does not fit one reading turn within the route input limit of '
                            + str(limit) + ' characters',
                            {'version_id': first['version_id'], 'characters': first['characters'],
                             'input_limit_characters': limit})
    return chosen


def _build_final_turn(route, packet, record):
    """The native packet with the reading record, refused when its actual fitted request does not fit the route."""
    from research_harness.review_protocol import canonical_prompt
    value = {key: item for key, item in packet.items() if key != 'digest'}
    value[RECORD_KEY] = record
    value['digest'] = digest(value)
    request = transport.request_body(route, canonical_prompt('native_math', reading_stage='final')['text'], value)
    characters, limit = _measure(route, request), transport.local_input_limit(route)
    if characters > limit:
        raise ResearchError('review_input_too_large',
                            'The final turn of the reading chain has ' + str(characters) + ' input characters, above '
                            'the route input limit of ' + str(limit) + '. Export the openness packet again with '
                            'minimum.text_priority listing the version ids of the read works, the decisive ones '
                            'first, so that the final turn can withhold full-text bodies that the reading turns '
                            'delivered, and start a new reading chain with it.',
                            {'input_characters': characters, 'input_limit_characters': limit})
    return value


def _extend_record(records, artifacts, prior, prior_packet, state):
    """The reading record after an earlier turn: its notes, its checked excerpts and the answers to its passages."""
    reading = find_stage(prior_packet) == 'reading'
    earlier = prior_packet[TURN_KEY][RECORD_KEY] if reading else prior_packet[RECORD_KEY]
    output = state['output']
    excerpts, allowances = review_excerpts.check_excerpts(records, artifacts, output['excerpts'], earlier['allowances'])
    passages, allowances = review_excerpts.answer_passages(records, artifacts, output['passage_requests'], allowances)
    units = prior_packet[TURN_KEY]['reading_plan']['delivered_now'] if reading else []
    turn = {'assignment_id': prior['id'], 'attempt_id': state['attempt_id'],
            'delivered': [row['version_id'] for row in units if row.get('parts', 1) == 1], 'notes': output['notes']}
    parts = [{key: row[key] for key in ('version_id', 'part', 'parts')} for row in units if row.get('parts', 1) > 1]
    if parts:
        turn['delivered_parts'] = parts
    return {'protocol': PROTOCOL, 'turns': earlier['turns'] + [turn], 'excerpts': earlier['excerpts'] + excerpts,
            'passages': earlier['passages'] + passages, 'allowances': allowances}


def prepare_packet(records, artifacts, value, route, packet):
    """The packet of a native_math assignment and its place in a reading chain. A packet that one input of the route
    holds, with every full text of its readings complete, stays a single turn without a chain. Otherwise the first
    assignment is reading turn 1; a turn that continues a chain is the next reading turn while texts remain, and then
    the final turn."""
    prior_id = value['context'].get('prior_assignment_id')
    if prior_id is None:
        native = packet['native']
        if not is_openness_packet(native) or transport.local_input_limit(route) is None:
            return packet, None
        from research_harness.review_protocol import canonical_prompt
        texts = list_reading_texts(records, native)
        if not texts or _fits(route, canonical_prompt('native_math')['text'], packet,
                              [row['artifact'] for row in texts]):
            return packet, None
        candidates = list_reading_units(artifacts, texts)
        record = {'protocol': PROTOCOL, 'turns': [], 'excerpts': [], 'passages': [],
                  'allowances': review_excerpts.create_allowances()}
        chain = {'first_assignment_id': value['id'], 'turn': 1}
        return _build_reading_turn(artifacts, route, packet, record, [], candidates, 1), chain
    prior, prior_packet, state = _read_prior(records, artifacts, value, prior_id)
    record = _extend_record(records, artifacts, prior, prior_packet, state)
    chain = dict(prior['reading_chain'], turn=prior['reading_chain']['turn'] + 1)
    if find_stage(prior_packet) == 'reading' and prior_packet[TURN_KEY]['reading_plan']['remaining']:
        plan = prior_packet[TURN_KEY]['reading_plan']
        delivered = plan['delivered_before'] + [
            dict({key: item for key, item in row.items() if key != 'artifact'},
                 artifact_descriptor=descriptor(row['artifact'])) for row in plan['delivered_now']]
        candidates = _list_remaining_units(artifacts, plan['remaining'])
        return _build_reading_turn(artifacts, route, packet, record, delivered, candidates, chain['turn']), chain
    return _build_final_turn(route, packet, record), chain

