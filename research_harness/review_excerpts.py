"""Reviewer excerpts and bounded passages, checked against the stored source text.

A reviewer that reads its sources over several turns keeps its own verbatim excerpts. The harness finds each excerpt
in the stored text that it names and delivers the original characters at their exact span, never the reviewer's
copy. A passage request returns a bounded part of a stored text that starts at a verbatim anchor. The excerpt rules of
bar, slate and result sessions name a text by its exact version. An openness reading chain names the reading unit of a
text by its identity: a full text by version_id and a captured search response by source_id, each with an optional
text_sha256 of the whole body, and a store record by record_kind and record_id. One turn reads at most
TURN_FULLTEXT_CHARACTERS of new text, so a longer text is read in consecutive parts. The same stored records always
give the same result.
"""

import hashlib
import json
import unicodedata

from .errors import ResearchError, describe_error_at
from .operations import describe_received_value, fields, text

KEY = 'reviewer_excerpts'
PROTOCOL_V1 = 'exactory-review-excerpts-v1'
PROTOCOL = 'exactory-review-excerpts-v2'
# The characters of new full text that one turn delivers at most, so that a reviewer can read it and record excerpts
# within the route's time limit. A longer text arrives in parts of at most this size.
TURN_FULLTEXT_CHARACTERS = 120000
MINIMUM_PHRASE_CHARACTERS = 20
EXCERPT_CHARACTERS = 6000
SESSION_EXCERPT_CHARACTERS = 200000
PASSAGE_CHARACTERS = 20000
SESSION_PASSAGE_CHARACTERS = 100000
UNCHECKED_NOTE = ('The harness did not find this text verbatim in the stored text of its version. It is the '
                  "reviewer's own copy and was not checked against the source.")
EXCERPT_OVER_ALLOWANCE_NOTE = ('This excerpt was found, but the verified excerpts of this session already use the '
                               'session total of characters, so its text is not delivered.')
PASSAGE_OVER_ALLOWANCE_NOTE = ('The anchor of this passage was found, but the delivered passages of this session '
                               'already use the session total of characters, so its text is not delivered.')
# The kinds of reading units, each with the fields that name one: required, then optional.
UNIT_KINDS = ('fulltext', 'search_response', 'record')
IDENTITY_FIELDS = {'fulltext': (('version_id',), ('text_sha256',)),
                   'search_response': (('source_id',), ('text_sha256',)),
                   'record': (('record_kind', 'record_id'), ())}
RECORD_KINDS = ('literature_search', 'reading', 'problem_statement')
# The excerpt rules of 0.64.2 to 0.65.1, kept for the assignments recorded with them.
PHASE_PROMPT_V1 = (
    ' Excerpt rules of this session. The full texts that you request can be longer together than one input of this '
    'route can hold, so they reach you in parts over several turns. A full text that a turn of this session delivers '
    'to you complete is not delivered again: a later request for it is answered with status "already_delivered", '
    'which names that turn, and the replay of the earlier turns can leave out its body. While you read a delivered '
    'text, copy what your assessment needs from it verbatim: definitions, normalizations, every relevant theorem, '
    'lemma or proposition with all of its hypotheses, and every statement that a question is open. Return these '
    'copies in the optional top-level field excerpts: [{version_id,text,purpose}]. text is copied character for '
    'character from the delivered text of version_id and has at least 20 and at most 6,000 characters; purpose '
    'says in a few words why you keep it. The harness looks for each excerpt in the stored text of that version '
    'after it changes each character to its NFKC form and each run of white space to one space. A verified excerpt '
    'comes back in reviewer_excerpts.excerpts of every later turn as the exact source characters with their '
    'location. An excerpt that is not found, is too short or too long, or names a version without a stored text '
    'comes back with status "unmatched" and a reason; its text is your own copy and was not checked. Verified '
    'excerpts have a total of 200,000 characters in this session, and an excerpt beyond that total has status '
    '"over_allowance". When you need part of a text again, request a bounded passage in source_requests: '
    '{id,kind:"passage",version_id,anchor,characters}. anchor is a verbatim phrase of at least 20 characters from '
    'that text, and characters is an integer from 1 to 20,000. The next turn delivers the source text that starts at '
    'the first occurrence of the anchor, with at most that many characters, or status "not_found". Delivered '
    'passages have a total of 100,000 characters in this session, and a passage beyond that total has status '
    '"over_allowance". A passage request never stays pending, and a passage is delivered once, so keep what you need '
    'from it as an excerpt. Your final assessment rests on your own earlier responses, your checked excerpts and '
    'passages, and the full texts that the final input can hold.')
PHASE_PROMPT = (
    ' Excerpt rules of this session. The full texts that you request can be longer together than one input of this '
    'route can hold, and a turn has a limited time, so they reach you in parts over several turns. One turn delivers '
    'at most 120,000 characters of new full text. A requested text that fits what is left of this turn arrives whole. '
    'A longer text arrives in consecutive parts of at most 120,000 characters, one after another in later turns; a '
    'part delivery names its part in part: {version_id,part,parts,start,end,characters}, where start and end are '
    'character offsets in the stored text. A request beyond what is left of this turn has status '
    '"deferred_reading_budget" and a reason. A full-text request stays pending until the whole text or its last part '
    'has arrived; you need not repeat it. A full text that a turn of this session delivers to you complete, whole or '
    'in all its parts, is not delivered again: a later request for it is answered with status "already_delivered", '
    'which names that turn, and the replay of the earlier turns can leave out its body. While you read a delivered '
    'text or part, copy what your assessment needs from it verbatim: definitions, normalizations, every relevant '
    'theorem, lemma or proposition with all of its hypotheses, and every statement that a question is open. Return '
    'these copies in the optional top-level field excerpts: [{version_id,text,purpose}]. text is copied character for '
    'character from a delivered text or part of version_id and has at least 20 and at most 6,000 characters; purpose '
    'says in a few words why you keep it. The harness looks for each excerpt in the whole stored text of that version '
    'after it changes each character to its NFKC form and each run of white space to one space. A verified excerpt '
    'comes back in reviewer_excerpts.excerpts of every later turn as the exact source characters with their '
    'location. An excerpt that is not found, is too short or too long, or names a version without a stored text '
    'comes back with status "unmatched" and a reason; its text is your own copy and was not checked. Verified '
    'excerpts have a total of 200,000 characters in this session, and an excerpt beyond that total has status '
    '"over_allowance". When you need part of a text again, request a bounded passage in source_requests: '
    '{id,kind:"passage",version_id,anchor,characters}. anchor is a verbatim phrase of at least 20 characters from '
    'that text, and characters is an integer from 1 to 20,000. The next turn delivers the source text that starts at '
    'the first occurrence of the anchor, with at most that many characters, or status "not_found". Delivered '
    'passages have a total of 100,000 characters in this session, and a passage beyond that total has status '
    '"over_allowance". A passage request never stays pending, and a passage is delivered once, so keep what you need '
    'from it as an excerpt. Your final assessment rests on your own earlier responses, your checked excerpts and '
    'passages, and the full texts that the final input can hold.')
PHASE_PROMPTS = {PROTOCOL_V1: PHASE_PROMPT_V1, PROTOCOL: PHASE_PROMPT}


def create_allowances():
    """The session totals of verified excerpt characters and delivered passage characters, unused."""
    return {'excerpts': {'characters': SESSION_EXCERPT_CHARACTERS, 'used': 0},
            'passages': {'characters': SESSION_PASSAGE_CHARACTERS, 'used': 0}}


def normalize_with_offsets(value):
    """Normalize a text for matching: each character becomes its NFKC form, each run of white space becomes one space,
    and the text loses its leading and trailing spaces. Return the normalized text and, for each of its characters,
    the offset of the original character that it comes from."""
    characters, offsets = [], []
    for offset, character in enumerate(value):
        for normalized in unicodedata.normalize('NFKC', character):
            if not normalized.isspace():
                characters.append(normalized)
                offsets.append(offset)
            elif characters and characters[-1] != ' ':
                characters.append(' ')
                offsets.append(offset)
    if characters and characters[-1] == ' ':
        characters.pop()
        offsets.pop()
    return ''.join(characters), offsets


def find_fulltext_capture(records, version_id):
    """The exact version's main full-text capture with an extracted text, or None."""
    from .graph import main_captures
    work = records.get('work', {}).get(version_id)
    if work is None or not work.get('fulltexts'):
        return None
    return next((capture for capture in main_captures(records, work)
                 if capture.get('extraction_status') == 'extracted' and capture.get('text') is not None), None)


def find_fulltext(records, version_id):
    """The reference of the stored extracted text of the exact version's main full-text capture, or None."""
    capture = find_fulltext_capture(records, version_id)
    return None if capture is None else capture['text']


def compute_part_spans(value):
    """The consecutive parts [start, end) in which a turn reads a text: the whole text when it fits one turn's budget;
    otherwise parts of at most the budget, each ending after the last blank line in the last quarter of its window,
    else after the last newline there, else exactly at the budget."""
    spans, start = [], 0
    while len(value) - start > TURN_FULLTEXT_CHARACTERS:
        window_end = start + TURN_FULLTEXT_CHARACTERS
        floor = window_end - TURN_FULLTEXT_CHARACTERS // 4
        blank_line = value.rfind('\n\n', floor, window_end)
        newline = value.rfind('\n', floor, window_end)
        end = blank_line + 2 if blank_line >= 0 else newline + 1 if newline >= 0 else window_end
        spans.append((start, end))
        start = end
    spans.append((start, len(value)))
    return spans


def compute_part_digests(value):
    """The SHA-256 of the exact UTF-8 bytes of each part of a text, in order."""
    return [hashlib.sha256(value[start:end].encode('utf-8')).hexdigest() for start, end in compute_part_spans(value)]


def save_part(artifacts, value, reference, start, end, media_type=None):
    """The text artifact of one part: the stored text itself when the part is the whole text, otherwise the exact
    UTF-8 bytes of the part, saved with the given media type or else with the stored text's."""
    if (start, end) == (0, len(value)):
        return reference
    return artifacts.put(value[start:end].encode('utf-8'), media_type or reference['media_type'])


def find_source_text(records, version_id):
    """The reference of the text that excerpts and passages of a version are matched against: its main full text, or
    its abstract capture when no full text is held; None when neither is held."""
    reference = find_fulltext(records, version_id)
    if reference is not None:
        return reference
    abstracts = (records.get('work', {}).get(version_id) or {}).get('abstracts') or []
    return abstracts[0]['artifact'] if abstracts else None


def find_kind(value):
    """The kind of reading unit that an excerpt, a passage request or a unit names by its identity fields."""
    if 'record_kind' in value or 'record_id' in value:
        return 'record'
    return 'search_response' if 'source_id' in value else 'fulltext'


def find_identity(value):
    """The identity of the reading unit that a value names: its kind, then the (field, value) pairs of the identity
    fields that it carries, required fields first."""
    kind = find_kind(value)
    required, optional = IDENTITY_FIELDS[kind]
    return (kind,) + tuple((key, value[key]) for key in required + optional if key in value)


def find_identity_fields(value, kinds=None):
    """The required and optional identity fields of an excerpt or a passage request. Without kinds it names a version
    alone. With kinds it names a reading unit of one of those kinds; a value that names another kind gets the fields of
    a version, which its own fields then fail."""
    kind = find_kind(value) if isinstance(value, dict) else 'fulltext'
    if kinds is None or kind not in kinds:
        return ('version_id',), ()
    return IDENTITY_FIELDS[kind]


def find_source_reference(records, identity, unit_sources=None):
    """The reference of the whole stored text that an identity names, or None. Without unit_sources only a bare version
    names a text, its main full text or else its abstract capture, as the excerpt rules of bar, slate and result
    sessions have it. unit_sources maps the required identity of each unit of a reading chain to its bound whole bodies
    by SHA-256: an identity with text_sha256 names that body, and one without names the only bound body."""
    values = dict(identity[1:])
    if unit_sources is None:
        return find_source_text(records, values['version_id']) if set(values) == {'version_id'} else None
    kind = identity[0]
    bodies = unit_sources.get((kind,) + tuple((key, values[key]) for key in IDENTITY_FIELDS[kind][0]), {})
    if 'text_sha256' in values:
        return bodies.get(values['text_sha256'])
    return next(iter(bodies.values())) if len(bodies) == 1 else None


def _find_missing_reason(identity, unit_sources):
    """Why an identity names no stored text: it names several bound bodies without text_sha256, or none."""
    values = dict(identity[1:])
    if unit_sources is not None and 'text_sha256' not in values:
        required = IDENTITY_FIELDS[identity[0]][0]
        if len(unit_sources.get((identity[0],) + tuple((key, values.get(key)) for key in required), {})) > 1:
            return 'ambiguous_identity'
    return 'no_stored_text'


def _read_sources(records, artifacts, identities, unit_sources=None):
    """The stored text that each identity names, with its normalized form and offsets."""
    sources = {}
    for identity in sorted(identities):
        reference = find_source_reference(records, identity, unit_sources)
        if reference is not None:
            content = artifacts.read(reference).decode('utf-8')
            normalized, offsets = normalize_with_offsets(content)
            sources[identity] = {'reference': reference, 'text': content, 'normalized': normalized,
                                 'offsets': offsets}
    return sources


def _locate(source, phrase):
    """The original span [start, end) of the first occurrence of a normalized phrase, or None."""
    position = source['normalized'].find(phrase)
    if position < 0:
        return None
    return source['offsets'][position], source['offsets'][position + len(phrase) - 1] + 1


def _describe_span(source, start, end):
    from .bar_sources import descriptor
    original = source['text'][start:end]
    return {'text': original, 'locator': {'kind': 'span', 'start': start, 'end': end,
                                          'sha256': hashlib.sha256(original.encode('utf-8')).hexdigest()},
            'artifact_descriptor': descriptor(source['reference'])}


def check_excerpts(records, artifacts, excerpts, allowances, *, unit_sources=None):
    """Check excerpts in output order against the stored text that each names, found as find_source_reference finds
    it. A verified excerpt carries the original characters of its first match; the session allowance counts the
    characters of verified excerpts. Return the checked entries and the allowances after them."""
    allowance = dict(allowances['excerpts'])
    sources = _read_sources(records, artifacts, {find_identity(excerpt) for excerpt in excerpts}, unit_sources)
    checked = []
    for excerpt in excerpts:
        identity = find_identity(excerpt)
        entry = dict(identity[1:], purpose=excerpt['purpose'])
        source = sources.get(identity)
        phrase, _ = normalize_with_offsets(excerpt['text'])
        span = None
        if source is None:
            reason = _find_missing_reason(identity, unit_sources)
        elif len(phrase) < MINIMUM_PHRASE_CHARACTERS:
            reason = 'too_short'
        else:
            span = _locate(source, phrase)
            reason = ('not_found' if span is None else
                      'too_long' if span[1] - span[0] > EXCERPT_CHARACTERS else None)
        if reason is not None:
            checked.append(dict(entry, status='unmatched', reason=reason, text=excerpt['text'], checked=False,
                                note=UNCHECKED_NOTE))
        elif allowance['used'] + span[1] - span[0] > allowance['characters']:
            checked.append(dict(entry, status='over_allowance', characters=span[1] - span[0],
                                note=EXCERPT_OVER_ALLOWANCE_NOTE))
        else:
            allowance['used'] += span[1] - span[0]
            checked.append(dict(entry, status='verified', **_describe_span(source, *span)))
    return checked, dict(allowances, excerpts=allowance)


def answer_passages(records, artifacts, requests, allowances, *, unit_sources=None):
    """Answer passage requests in order: the original text that starts at the first occurrence of the anchor and has
    at most the requested number of characters, within the session allowance; the stored text is found as for
    check_excerpts. Return the answers and the allowances after them."""
    allowance = dict(allowances['passages'])
    sources = _read_sources(records, artifacts, {find_identity(request) for request in requests}, unit_sources)
    answers = []
    for request in requests:
        answer = {'request': request, 'reading_credit': False}
        source = sources.get(find_identity(request))
        span = None if source is None else _locate(source, normalize_with_offsets(request['anchor'])[0])
        if span is None:
            answers.append(dict(answer, status='not_found', reason='anchor_not_found' if source is not None
                                else _find_missing_reason(find_identity(request), unit_sources)))
            continue
        start = span[0]
        end = min(start + request['characters'], len(source['text']))
        if allowance['used'] + end - start > allowance['characters']:
            answers.append(dict(answer, status='over_allowance', characters=end - start,
                                note=PASSAGE_OVER_ALLOWANCE_NOTE))
        else:
            allowance['used'] += end - start
            answers.append(dict(answer, status='delivered', **_describe_span(source, start, end)))
    return answers, dict(allowances, passages=allowance)


def _require_record_kind(value, code, *, path):
    if 'record_kind' in value and value['record_kind'] not in RECORD_KINDS:
        raise ResearchError(code, 'A record_kind is literature_search, reading or problem_statement',
                            {'path': path + '/record_kind'})


def validate_excerpts(values, code, kinds=None, *, path='/excerpts'):
    """The shape of a reviewer's excerpts: a list of {version_id, text, purpose} with nonempty strings. With kinds, an
    excerpt names a reading unit of one of those kinds by the identity fields of that kind instead."""
    if not isinstance(values, list):
        raise ResearchError(code, 'excerpts is a list of {version_id, text, purpose}', {'path': path})
    for index, value in enumerate(values):
        item_path = path + '/' + str(index)
        required, optional = find_identity_fields(value, kinds)
        fields(value, required + ('text', 'purpose'), optional, code=code, path=item_path)
        for key in required + optional + ('text', 'purpose'):
            if key in value:
                with describe_error_at(item_path + '/' + key):
                    text(value[key], 'Excerpt ' + key, code=code)
        _require_record_kind(value, code, path=item_path)


def validate_passage_request(request, code, kinds=None, *, path=''):
    """The values of a passage request: an exact version, or with kinds the identity of a reading unit of one of those
    kinds, a verbatim anchor of at least 20 normalized characters and a whole number of characters from 1 to
    20,000."""
    from .bar_sources import require_exact_version
    required, optional = find_identity_fields(request, kinds)
    for key in required + optional:
        with describe_error_at(path + '/' + key):
            if key == 'version_id':
                require_exact_version(request[key], code)
            elif key in request:
                text(request[key], 'Passage ' + key, code=code)
    _require_record_kind(request, code, path=path)
    with describe_error_at(path + '/anchor'):
        text(request['anchor'], 'Passage anchor', code=code)
    normalized_characters = len(normalize_with_offsets(request['anchor'])[0])
    if normalized_characters < MINIMUM_PHRASE_CHARACTERS:
        raise ResearchError(code, 'A passage anchor is a verbatim phrase of at least 20 characters',
                            {'path': path + '/anchor', 'normalized_characters': normalized_characters,
                             'minimum_characters': MINIMUM_PHRASE_CHARACTERS})
    if type(request['characters']) is not int or not 1 <= request['characters'] <= PASSAGE_CHARACTERS:
        raise ResearchError(code, 'A passage has a whole number of characters from 1 to 20,000',
                            dict(describe_received_value('characters', request['characters']),
                                 path=path + '/characters', minimum_characters=1,
                                 maximum_characters=PASSAGE_CHARACTERS))


def carry_session(records, artifacts, packet, output):
    """The excerpt record that a continuation carries: the parent packet's checked excerpts, then the parent output's
    excerpts checked in order, with the session allowances. A parent outside the excerpt rules starts an empty
    record."""
    earlier = packet.get(KEY)
    if earlier is None:
        return {'protocol': PROTOCOL, 'excerpts': [], 'allowances': create_allowances()}
    excerpts = output.get('excerpts', [])
    validate_excerpts(excerpts, 'invalid_value_review')
    checked, allowances = check_excerpts(records, artifacts, excerpts, earlier['allowances'])
    return {'protocol': PROTOCOL, 'excerpts': earlier['excerpts'] + checked, 'allowances': allowances}


def read_sent_packet(request):
    """The packet of the current turn as a recorded request delivered it, or None for a request of another form. A
    request with attached originals keeps its packet in its one text part."""
    from . import review_transport as transport
    content = transport.logical_messages(request)[-1].get('content')
    if isinstance(content, list):
        texts = [part.get('text') for part in content if isinstance(part, dict) and part.get('type') == 'input_text']
        content = texts[0] if len(texts) == 1 else None
    if not isinstance(content, str) or not content.startswith(transport.PACKET_LABEL):
        return None
    return json.loads(content[len(transport.PACKET_LABEL):])


def find_complete_bodies(packet):
    """The SHA-256 of every evidence body that a sent packet delivers complete, with its exact size and digest."""
    complete = set()
    for entry in packet.get('evidence', []):
        reference = entry.get('artifact') or {}
        content = entry.get('content')
        if entry.get('encoding') == 'utf-8' and isinstance(content, str):
            data = content.encode('utf-8')
            if len(data) == reference.get('size') and hashlib.sha256(data).hexdigest() == reference.get('sha256'):
                complete.add(reference['sha256'])
    return complete


def find_delivered_fulltexts(records, packet):
    """The versions whose stored main full text a sent packet delivered complete on their full-text request."""
    complete = find_complete_bodies(packet)
    versions = set()
    for delivery in packet.get('source_deliveries', []):
        request = delivery.get('request') or {}
        if (request.get('kind') == 'source' and request.get('depth') == 'fulltext'
                and delivery.get('status') == 'delivered'):
            reference = find_fulltext(records, request['version_id'])
            if reference is not None and reference['sha256'] in complete:
                versions.add(request['version_id'])
    return versions
