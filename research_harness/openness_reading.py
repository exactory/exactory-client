"""Problem openness reviews that read their full texts over several turns.

When one input of a text route cannot hold every extracted full text of a problem openness packet, the native_math
assignment becomes a reading chain. Each reading turn delivers a group of the full texts with the statement and its
formulations; the reviewer returns notes and verbatim excerpts, which the harness checks against the stored texts. The
final turn is the native result review of the whole packet, with the reading record of the earlier turns. Every turn
is a fresh input; the reading record carries the continuity.
"""

import json

from . import review_excerpts
from . import review_transport as transport
from .bar_sources import _reference, descriptor
from .errors import ResearchError
from .evidence import digest
from .operations import fields, normalized_text, text

PROTOCOL = 'exactory-openness-reading-v1'
TURN_KEY = 'reading_turn'
RECORD_KEY = 'reading_record'
STAGES = ('reading', 'final')
NOTES_CHARACTERS = 20000
INVALID_RESPONSE = 'invalid_reading_response'
RESPONSE_FIELDS = ('schema_version', 'stage', 'subject_digest', 'claim_digest', 'reviewer', 'excerpts', 'notes',
                   'passage_requests')
READING_PROMPT = (
    'This is one reading turn of an openness review of the exact problem statement in reading_turn.statement. The '
    'full texts that the review must read are longer together than one input of this route can hold, so they reach you '
    'in parts, one group in each turn. reading_turn.reading_plan names the texts delivered now, whose bodies are in '
    'evidence, the texts delivered in earlier turns and the texts that remain. A text delivered in this turn will '
    'not come back in a later turn. Each turn is a fresh input without the earlier turns: '
    'reading_turn.reading_record holds your notes, checked excerpts and passages from them, and the final turn rests '
    'on that record. Read every delivered text completely now. Copy verbatim what the openness judgment needs: '
    'definitions, normalizations, every relevant theorem, lemma or proposition with all of its hypotheses, and every '
    'statement that a question is open or has been solved. Return exactly {schema_version:1,stage:"reading",'
    'subject_digest,claim_digest,reviewer,excerpts,notes,passage_requests}, with subject_digest, claim_digest and '
    'reviewer copied from reading_turn. notes is a nonempty string of at most 20,000 characters: what each delivered '
    'work proves, what it leaves open, and how it bears on the statement and on each neighbouring formulation in '
    'reading_turn.formulations. excerpts is a list of {version_id,text,purpose}: text is copied character for '
    'character from the delivered text of version_id and has at least 20 and at most 6,000 characters; purpose says '
    'in a few words why you keep it. The harness looks for each excerpt in the stored text of that version after it '
    'changes each character to its NFKC form and each run of white space to one space. A verified excerpt reaches '
    'the later turns as the exact source characters with their location. An excerpt that is not found, is too short '
    'or too long, or names a version without a stored text is kept with status "unmatched" and a reason; its text is '
    'your own copy and was not checked. Verified excerpts have a total of 200,000 characters in the review, and an '
    'excerpt beyond that total has status "over_allowance". passage_requests is a list, possibly empty, of '
    '{id,version_id,anchor,characters}: anchor is a '
    'verbatim phrase of at least 20 characters from a delivered text, and characters is an integer from 1 to 20,000. '
    'The next turn gives the source text that starts at the first occurrence of the anchor, with at most that many '
    'characters, or status "not_found". Delivered passages have a total of 100,000 characters in the review, and a '
    'passage beyond that total has status "over_allowance". reading_turn.reading_record.allowances shows what is used '
    'of both totals. Do not judge openness in this turn; the final turn does that.')
FINAL_PROMPT = (
    ' This is the final turn of an openness review that read its full texts in earlier reading turns. reading_record '
    'holds those turns: your notes of each turn, your excerpts with their check status, the passages that you '
    'requested and the allowances. A verified excerpt or a delivered passage is the exact text of its source at the '
    'stated location; an unmatched excerpt is your own copy and was not checked. Each full text of the readings was '
    'delivered complete in a reading turn. The evidence of this turn repeats the full texts that this input can hold; '
    'a body that it withholds was read in a reading turn. Base the judgment on your notes, your checked excerpts and '
    'passages, and the full texts that this input holds, and return the native result review described above. When '
    'you need part of a text again before you can judge, return instead {schema_version:1,stage:"reading",'
    'subject_digest,claim_digest,reviewer,excerpts,notes,passage_requests} with subject_digest, claim_digest and '
    'reviewer copied from native and with at least one passage request {id,version_id,anchor,characters}, where '
    'anchor is a verbatim phrase of at least 20 characters and characters is an integer from 1 to 20,000. A further '
    'final turn then delivers those passages. When reading_record.allowances.passages has no characters left, return '
    'the native result review.')


def is_openness_packet(native):
    """Whether a native packet is a problem openness packet exported by `export --kind problem-openness`."""
    return (native['contract'].get('orchestration') == 'problem_creation'
            and 'openness_minimum' in native['scientific_context'])


def find_stage(packet):
    """The reading-chain stage of a native_math packet: 'reading', 'final', or None for a single-turn packet."""
    return 'reading' if TURN_KEY in packet else 'final' if RECORD_KEY in packet else None


def find_binding(packet):
    """The reviewer binding of a native_math packet: its native packet, or a reading turn's copy of the subject,
    claim and reviewer of that native packet."""
    return packet[TURN_KEY] if TURN_KEY in packet else packet['native']


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
    """A reading turn packet whose only evidence is the full texts delivered now."""
    from .review_protocol import _evidence
    native = packet['native']
    minimum = native['scientific_context']['openness_minimum']
    plan = {'turn': turn, 'delivered_before': delivered, 'remaining': remaining,
            'delivered_now': [{'version_id': row['version_id'], 'characters': row['characters'],
                               'artifact': _reference(row['artifact_descriptor'])} for row in now]}
    value = {key: packet[key] for key in ('protocol', 'role', 'reviewer_id', 'dossier_id')}
    value[TURN_KEY] = dict({key: native[key] for key in ('subject_digest', 'claim_digest', 'reviewer')},
                           protocol=PROTOCOL, statement=native['subject'], reading_plan=plan, reading_record=record,
                           formulations={'statement': minimum['statement'], 'neighbours': minimum['neighbours']})
    value['evidence'] = _evidence(artifacts, value)
    value['digest'] = digest(value)
    return value


def _build_reading_turn(artifacts, route, packet, record, delivered, candidates, turn):
    """The next reading turn: the longest prefix of the remaining texts whose actual fitted request fits the route
    and delivers each of them complete."""
    from .review_protocol import canonical_prompt
    prompt = canonical_prompt('native_math', reading_stage='reading')['text']
    chosen = None
    for count in range(1, len(candidates) + 1):
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
    from .review_protocol import canonical_prompt
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


def _read_prior(records, artifacts, value, prior_id):
    """The earlier turn that a chain turn continues, with its packet and its observed reading response."""
    from .review_protocol import assignment_state
    prior = records.get('review_assignment', {}).get(prior_id)
    if prior is None:
        raise ResearchError('review_record_missing', 'Missing review_assignment: ' + str(prior_id))
    if (prior['role'] != 'native_math' or 'reading_chain' not in prior
            or prior['context']['native_packet'] != value['context']['native_packet']
            or prior['route_id'] != value['route_id']
            or normalized_text(prior['reviewer_id']) != normalized_text(value['reviewer_id'])
            or normalized_text(prior['author_id']) != normalized_text(value['author_id'])):
        raise ResearchError('review_source_continuation_mismatch',
                            'Continue a reading chain of the same native packet with the same reviewer, route and '
                            'author')
    if any(saved['role'] == 'native_math' and saved['context'].get('prior_assignment_id') == prior_id
           for saved in records.get('review_assignment', {}).values()):
        raise ResearchError('review_source_continuation_fork', 'Continue the latest turn of the reading chain')
    state = assignment_state(records, artifacts, prior_id)
    if not state['ready']:
        raise ResearchError('review_source_continuation_unverified',
                            'The earlier turn needs an observed, authenticated output in a verified context',
                            {'obligations': state['obligations']})
    prior_packet = json.loads(artifacts.read(prior['packet']))
    validate_turn_output(prior_packet, state['output'])
    if state['output'].get('stage') != 'reading':
        raise ResearchError('review_resampling_forbidden',
                            'A final turn that returned the native result ends the chain')
    return prior, prior_packet, state


def _extend_record(records, artifacts, prior, prior_packet, state):
    """The reading record after an earlier turn: its notes, its checked excerpts and the answers to its passages."""
    reading = find_stage(prior_packet) == 'reading'
    earlier = prior_packet[TURN_KEY][RECORD_KEY] if reading else prior_packet[RECORD_KEY]
    output = state['output']
    excerpts, allowances = review_excerpts.check_excerpts(records, artifacts, output['excerpts'], earlier['allowances'])
    passages, allowances = review_excerpts.answer_passages(records, artifacts, output['passage_requests'], allowances)
    delivered = ([row['version_id'] for row in prior_packet[TURN_KEY]['reading_plan']['delivered_now']]
                 if reading else [])
    turn = {'assignment_id': prior['id'], 'attempt_id': state['attempt_id'], 'delivered': delivered,
            'notes': output['notes']}
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
        from .review_protocol import canonical_prompt
        texts = list_reading_texts(records, native)
        if not texts or _fits(route, canonical_prompt('native_math')['text'], packet,
                              [row['artifact'] for row in texts]):
            return packet, None
        candidates = [{'version_id': row['version_id'],
                       'characters': len(artifacts.read(row['artifact']).decode('utf-8')),
                       'artifact_descriptor': descriptor(row['artifact'])} for row in texts]
        record = {'protocol': PROTOCOL, 'turns': [], 'excerpts': [], 'passages': [],
                  'allowances': review_excerpts.create_allowances()}
        chain = {'first_assignment_id': value['id'], 'turn': 1}
        return _build_reading_turn(artifacts, route, packet, record, [], candidates, 1), chain
    prior, prior_packet, state = _read_prior(records, artifacts, value, prior_id)
    record = _extend_record(records, artifacts, prior, prior_packet, state)
    chain = dict(prior['reading_chain'], turn=prior['reading_chain']['turn'] + 1)
    if find_stage(prior_packet) == 'reading' and prior_packet[TURN_KEY]['reading_plan']['remaining']:
        plan = prior_packet[TURN_KEY]['reading_plan']
        delivered = plan['delivered_before'] + [{'version_id': row['version_id'], 'characters': row['characters'],
                                                 'artifact_descriptor': descriptor(row['artifact'])}
                                                for row in plan['delivered_now']]
        return _build_reading_turn(artifacts, route, packet, record, delivered, plan['remaining'], chain['turn']), chain
    return _build_final_turn(route, packet, record), chain


def _require(condition, message):
    if not condition:
        raise ResearchError(INVALID_RESPONSE, message)


def _validate_reading_response(binding, output):
    _require(isinstance(output, dict) and set(output) == set(RESPONSE_FIELDS),
             'A reading response is exactly {schema_version,stage,subject_digest,claim_digest,reviewer,excerpts,notes,'
             'passage_requests}')
    _require(type(output['schema_version']) is int and output['schema_version'] == 1 and output['stage'] == 'reading',
             'A reading response has schema_version 1 and stage "reading"')
    _require(all(output[key] == binding[key] for key in ('subject_digest', 'claim_digest', 'reviewer')),
             'Copy subject_digest, claim_digest and reviewer exactly')
    _require(isinstance(output['notes'], str) and bool(output['notes'].strip())
             and len(output['notes']) <= NOTES_CHARACTERS, 'notes is a nonempty string of at most 20,000 characters')
    review_excerpts.validate_excerpts(output['excerpts'], INVALID_RESPONSE)
    _require(isinstance(output['passage_requests'], list), 'passage_requests is a list')
    seen = set()
    for request in output['passage_requests']:
        fields(request, ('id', 'version_id', 'anchor', 'characters'), code=INVALID_RESPONSE)
        text(request['id'], 'Passage request ID', code=INVALID_RESPONSE)
        _require(request['id'] not in seen, 'Passage request IDs are unique')
        seen.add(request['id'])
        review_excerpts.validate_passage_request(request, INVALID_RESPONSE)


def validate_turn_output(packet, output):
    """The output contract of a reading-chain turn. A reading turn returns a reading response. A final turn returns the
    native result review, or a reading response with at least one passage request while the passage allowance has
    characters left."""
    from .native_review import validate_output
    final = find_stage(packet) == 'final'
    if final and not (isinstance(output, dict) and output.get('stage') == 'reading'):
        validate_output(packet['native'], output)
        return
    _validate_reading_response(find_binding(packet), output)
    if final:
        _require(bool(output['passage_requests']), 'A reading response in a final turn requests at least one passage')
        allowance = packet[RECORD_KEY]['allowances']['passages']
        _require(allowance['used'] < allowance['characters'],
                 'The passage allowance is used up; return the native result review')


def verify_chain(records, artifacts, assignment):
    """The earlier turns of a final turn's reading chain, each with the versions whose full text its actual recorded
    request delivered complete. Every turn must be ready, the final turn's reading record must name their observed
    attempts, and every full text of the readings must have reached a reading turn complete."""
    from .native_review import load_packet
    from .review_protocol import assignment_state
    native = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
    packet = json.loads(artifacts.read(assignment['packet']))
    chain, current = [], records['review_assignment'].get(assignment['context'].get('prior_assignment_id'))
    while current is not None:
        chain.insert(0, current)
        current = records['review_assignment'].get(current['context'].get('prior_assignment_id'))
    texts = list_reading_texts(records, native)
    turns, read = [], set()
    for turn in chain:
        state = assignment_state(records, artifacts, turn['id'])
        if not state['ready']:
            raise ResearchError('review_context_unverified',
                                'Every turn of the reading chain needs an observed, authenticated output in a verified '
                                'context', {'assignment_id': turn['id'], 'obligations': state['obligations']})
        attempt = records['review_attempt'][state['attempt_id']]
        sent = review_excerpts.read_sent_packet(json.loads(artifacts.read(attempt['request']))) or {}
        complete = review_excerpts.find_complete_bodies(sent)
        delivered = [row['version_id'] for row in texts if row['artifact']['sha256'] in complete]
        if find_stage(json.loads(artifacts.read(turn['packet']))) == 'reading':
            read.update(delivered)
        turns.append({'assignment_id': turn['id'], 'attempt_id': attempt['id'], 'delivered': delivered})
    if ([[row['assignment_id'], row['attempt_id']] for row in packet[RECORD_KEY]['turns']]
            != [[row['assignment_id'], row['attempt_id']] for row in turns]):
        raise ResearchError('openness_reading_incomplete',
                            'The reading record of the final turn no longer names the observed turns of its chain')
    missing = [row['version_id'] for row in texts if row['version_id'] not in read]
    if missing:
        raise ResearchError('openness_reading_incomplete',
                            'Every full text of the readings must reach a reading turn of the chain complete',
                            {'version_ids': missing})
    return turns
