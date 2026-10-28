"""Reviewer excerpts and bounded passages, checked against the stored source text.

A reviewer that reads its sources over several turns keeps its own verbatim excerpts. The harness finds each excerpt
in the stored text of its exact version and delivers the original characters at their exact span, never the
reviewer's copy. A passage request returns a bounded part of a stored text that starts at a verbatim anchor. The
same stored records always give the same result.
"""

import hashlib
import json
import unicodedata

from .errors import ResearchError
from .operations import fields, text

KEY = 'reviewer_excerpts'
PROTOCOL = 'exactory-review-excerpts-v1'
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
PHASE_PROMPT = (
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


def find_fulltext(records, version_id):
    """The reference of the stored extracted text of the exact version's main full-text capture, or None."""
    from .graph import main_captures
    work = records.get('work', {}).get(version_id)
    if work is None or not work.get('fulltexts'):
        return None
    return next((capture['text'] for capture in main_captures(records, work)
                 if capture.get('extraction_status') == 'extracted' and capture.get('text') is not None), None)


def find_source_text(records, version_id):
    """The reference of the text that excerpts and passages of a version are matched against: its main full text, or
    its abstract capture when no full text is held; None when neither is held."""
    reference = find_fulltext(records, version_id)
    if reference is not None:
        return reference
    abstracts = (records.get('work', {}).get(version_id) or {}).get('abstracts') or []
    return abstracts[0]['artifact'] if abstracts else None


def _read_sources(records, artifacts, version_ids):
    """The stored text of each version that holds one, with its normalized form and offsets."""
    sources = {}
    for version_id in sorted(version_ids):
        reference = find_source_text(records, version_id)
        if reference is not None:
            content = artifacts.read(reference).decode('utf-8')
            normalized, offsets = normalize_with_offsets(content)
            sources[version_id] = {'reference': reference, 'text': content, 'normalized': normalized,
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


def check_excerpts(records, artifacts, excerpts, allowances):
    """Check excerpts in output order against the stored text of their versions. A verified excerpt carries the
    original characters of its first match; the session allowance counts the characters of verified excerpts.
    Return the checked entries and the allowances after them."""
    allowance = dict(allowances['excerpts'])
    sources = _read_sources(records, artifacts, {excerpt['version_id'] for excerpt in excerpts})
    checked = []
    for excerpt in excerpts:
        entry = {'version_id': excerpt['version_id'], 'purpose': excerpt['purpose']}
        source = sources.get(excerpt['version_id'])
        phrase, _ = normalize_with_offsets(excerpt['text'])
        span = None
        if source is None:
            reason = 'no_stored_text'
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


def answer_passages(records, artifacts, requests, allowances):
    """Answer passage requests in order: the original text that starts at the first occurrence of the anchor and has
    at most the requested number of characters, within the session allowance. Return the answers and the
    allowances after them."""
    allowance = dict(allowances['passages'])
    sources = _read_sources(records, artifacts, {request['version_id'] for request in requests})
    answers = []
    for request in requests:
        answer = {'request': request, 'reading_credit': False}
        source = sources.get(request['version_id'])
        span = None if source is None else _locate(source, normalize_with_offsets(request['anchor'])[0])
        if span is None:
            answers.append(dict(answer, status='not_found',
                                reason='no_stored_text' if source is None else 'anchor_not_found'))
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


def validate_excerpts(values, code):
    """The shape of a reviewer's excerpts: a list of {version_id, text, purpose} with nonempty strings."""
    if not isinstance(values, list):
        raise ResearchError(code, 'excerpts is a list of {version_id, text, purpose}')
    for value in values:
        fields(value, ('version_id', 'text', 'purpose'), code=code)
        for key in ('version_id', 'text', 'purpose'):
            text(value[key], 'Excerpt ' + key, code=code)


def validate_passage_request(request, code):
    """The values of a passage request: an exact version, a verbatim anchor of at least 20 normalized characters and
    a whole number of characters from 1 to 20,000."""
    from .bar_sources import require_exact_version
    require_exact_version(request['version_id'], code)
    text(request['anchor'], 'Passage anchor', code=code)
    if len(normalize_with_offsets(request['anchor'])[0]) < MINIMUM_PHRASE_CHARACTERS:
        raise ResearchError(code, 'A passage anchor is a verbatim phrase of at least 20 characters')
    if type(request['characters']) is not int or not 1 <= request['characters'] <= PASSAGE_CHARACTERS:
        raise ResearchError(code, 'A passage has a whole number of characters from 1 to 20,000')


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
