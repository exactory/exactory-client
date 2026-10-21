"""Deliver and recognize complete inspected ranges and named visual bytes."""

import base64
import hashlib
import json

from . import bar_sources
from .artifacts import describe_artifact
from .evaluation import Evaluation
from .errors import ResearchError
from .reading import validate_read_evidence
from .source_links import covers_text, span_locator

_PASSAGE_CHARACTERS = 2000
# Match the existing scientific-delivery per-artifact bound for original visuals.
_MAX_ORIGINAL_BYTES = 64 * 1024 * 1024
_TEXT_KINDS = ('text', 'span')


def _encode_value(value):
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _compact_locator(locator):
    kind = locator['kind']
    if kind in _TEXT_KINDS:
        return {'kind': 'span', 'start': locator['start'], 'end': locator['end'],
                'sha256': locator.get('sha256') or hashlib.sha256(locator['quote'].encode('utf-8')).hexdigest()}
    if kind == 'html':
        return {'kind': kind, 'anchor': _compact_locator(locator['anchor']),
                'assets': [{'url': asset['url'], 'source_id': asset['source_id'],
                            'artifact_descriptor': bar_sources.descriptor(asset['artifact'])}
                           for asset in locator.get('assets', [])]}
    return dict(locator)


def _find_visual_gaps(context):
    visual = context['visual']
    if visual is None:
        return []
    # A plain HTML text anchor can be outside a figure. With no visual
    # resources, its complete captured markup is the inspected content.
    return [item for item in visual['pending']
            if item['code'] != 'visual_scope_unresolved' or visual['resources']]


def _build_passages(records, evaluation, link, *, whole_text=True):
    """Resolve every inspected byte into bounded chunks without storing objects. With whole_text False, a text
    inspection that covers the complete extracted text yields no chunks (local repair v19, T22)."""
    if not whole_text and link['locator']['kind'] in _TEXT_KINDS and covers_text(evaluation, link):
        return []
    context = evaluation.link(link)
    locator = link['locator']
    kind = locator['kind']
    inspection = validate_read_evidence(records, evaluation, link, depth='passage')
    origin = {'source_id': link['source_id'], 'artifact_descriptor': bar_sources.descriptor(link['artifact'])}
    if kind == 'pdf':
        original = context['capture']['original']
        if original['size'] > _MAX_ORIGINAL_BYTES:
            raise ResearchError('review_evidence_too_large', 'The inspected PDF exceeds the scientific delivery artifact bound')
        # Extraction has no region geometry. The exact original supplies every
        # inspected page/region, including PDFs acquired as generic binary.
        data = evaluation.read(original)
        return [({'version_id': link['version_id'], 'inspection': inspection,
                  'origin': dict(origin, locator=_compact_locator(locator)),
                  'excerpt': describe_artifact(data, 'application/pdf'), 'source_original': True,
                  'truncated': False, 'visuals_require_source_request': False,
                  'scientific_truth_certified': False}, data)]
    assets = locator.get('assets', []) if kind == 'html' else []
    for asset in assets:
        if asset['artifact']['size'] > _MAX_ORIGINAL_BYTES:
            raise ResearchError('review_evidence_too_large', 'The inspected image exceeds the scientific delivery artifact bound')
        # evaluation.link validated the exact work/original binding and container.
        evaluation.read(asset['artifact'])
    content = _encode_value(context['value'])
    offset = locator['start'] if kind in _TEXT_KINDS else 0
    source = evaluation.text(link['artifact']) if kind in _TEXT_KINDS else None
    starts = list(range(0, len(content), _PASSAGE_CHARACTERS)) or [0]
    result = []
    for index, start in enumerate(starts):
        end = min(len(content), start + _PASSAGE_CHARACTERS)
        data, media = content[start:end].encode('utf-8'), 'text/plain'
        chunk_origin = dict(origin)
        if kind in _TEXT_KINDS:
            location = span_locator(source, offset + start, offset + end, excerpt_length=0)
        elif kind == 'json':
            location = {'kind': kind, 'pointer': locator['pointer'], 'sha256': hashlib.sha256(data).hexdigest()}
            chunk_origin['character_range'] = {'start': start, 'end': end}
            if len(starts) == 1 and not isinstance(context['value'], str):
                media = 'application/json'
        else:
            location = _compact_locator(locator)
            chunk_origin['character_range'] = {'start': start, 'end': end}
        chunk_origin['locator'] = location
        passage = {'version_id': link['version_id'], 'inspection': inspection, 'origin': chunk_origin,
                   'excerpt': describe_artifact(data, media), 'chunk_index': index, 'chunk_count': len(starts),
                   'truncated': False, 'scientific_truth_certified': False}
        if kind == 'html':
            passage['visuals_require_source_request'] = bool(_find_visual_gaps(context))
            if index == 0:
                passage['assets'] = assets
        result.append((passage, data))
    return result


def build_reading_passages(records, artifacts, readings, *, whole_text=True):
    """Return complete passage descriptors and bytes without writing or rereading."""
    evaluation = Evaluation.of(records, artifacts)
    return [(dict(passage, reading_id=reading['id'], inspection_index=index), data)
            for reading in readings for index, inspection in enumerate(reading['inspections'])
            for passage, data in _build_passages(records, evaluation, inspection['link'], whole_text=whole_text)]


def build_inline_passages(records, artifacts, readings):
    """The passages of a problem packet (local repair v19, T22), as (passage, bytes) pairs. A text inspection that does
    not cover the complete text is one inline passage {reading_id, inspection_index, version_id, start, end, text}
    with the exact text of its span and bytes None. A text inspection over the complete text yields none: the full
    text is held and is requested by its version. Any other inspection keeps its bounded descriptors."""
    evaluation = Evaluation.of(records, artifacts)
    result = []
    for reading in readings:
        for index, inspection in enumerate(reading['inspections']):
            link = inspection['link']
            locator = link['locator']
            if locator['kind'] not in _TEXT_KINDS:
                result.extend((dict(passage, reading_id=reading['id'], inspection_index=index), data)
                              for passage, data in _build_passages(records, evaluation, link))
                continue
            validate_read_evidence(records, evaluation, link, depth='passage')
            if covers_text(evaluation, link):
                continue
            span_text = evaluation.text(link['artifact'])[locator['start']:locator['end']]
            result.append(({'reading_id': reading['id'], 'inspection_index': index, 'version_id': link['version_id'],
                            'start': locator['start'], 'end': locator['end'], 'text': span_text}, None))
    return result


def has_attachment(evaluation, packet, reference):
    if not isinstance(reference, dict) or not {'path', 'sha256', 'size', 'media_type'} <= reference.keys():
        return False
    data = evaluation.read(reference)
    for entry in packet.get('evidence', []):
        if entry.get('artifact') != reference or not isinstance(entry.get('content'), str):
            continue
        if entry.get('encoding') == 'utf-8' and entry['content'].encode('utf-8') == data:
            return True
        if entry.get('encoding') == 'base64' and entry['content'] == base64.b64encode(data).decode('ascii'):
            return True
    return False


def _has_source_content(evaluation, packet, link):
    # D7 dossiers can already attach native source bytes without an envelope.
    # A different capture of the same version does not supply this artifact.
    reference = link['artifact']
    deliveries = [item for item in packet.get('source_deliveries', []) if reference in item.get('evidence', [])]
    if deliveries and not any(item.get('status') == 'delivered' and item.get('request', {}).get('kind') == 'source'
            and item['request'].get('depth') == 'fulltext' and item['request'].get('version_id') == link['version_id']
            for item in deliveries):
        return False
    return has_attachment(evaluation, packet, reference)


def _matches_span(source, location):
    start, end = location.get('start'), location.get('end')
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source):
        return False
    data = source[start:end].encode('utf-8')
    if 'sha256' in location:
        return hashlib.sha256(data).hexdigest() == location['sha256']
    return location.get('kind') == 'text' and location.get('quote') == source[start:end]


def _find_inline_interval(evaluation, supplied, link):
    """The interval of an inline passage (local repair v19, T22): it names a stored inspection of the same source as the
    expected passage, its offsets lie within that inspection's span, and its text equals the source text there."""
    reading = evaluation.records.get('reading', {}).get(supplied.get('reading_id'))
    index, start, end = supplied.get('inspection_index'), supplied.get('start'), supplied.get('end')
    if reading is None or any(type(value) is not int for value in (index, start, end)):
        return None
    if not 0 <= index < len(reading['inspections']):
        return None
    named = reading['inspections'][index]['link']
    locator = named['locator']
    if (locator['kind'] not in _TEXT_KINDS or named['version_id'] != link['version_id']
            or named['source_id'] != link['source_id'] or named['artifact']['sha256'] != link['artifact']['sha256']
            or not locator['start'] <= start < end <= locator['end']):
        return None
    return (start, end) if evaluation.text(named['artifact'])[start:end] == supplied.get('text') else None


def _find_supplied_interval(evaluation, packet, supplied, expected, link):
    if 'text' in supplied:
        return _find_inline_interval(evaluation, supplied, link)
    if (supplied.get('version_id') != expected['version_id']
            or not has_attachment(evaluation, packet, supplied.get('excerpt'))):
        return None
    origin, wanted = supplied.get('origin', {}), expected['origin']
    if any(origin.get(key) != wanted[key] for key in ('source_id', 'artifact_descriptor')):
        return None
    location = origin.get('locator', {})
    data = evaluation.read(supplied['excerpt'])
    if link['locator']['kind'] == 'json':
        if location.get('kind') != 'json' or location.get('pointer') != link['locator']['pointer']:
            return None
        source = _encode_value(evaluation.link(link)['value'])
        span = origin.get('character_range', {'start': 0, 'end': len(source)})
        if location.get('sha256') != hashlib.sha256(data).hexdigest():
            return None
        start, end = span.get('start'), span.get('end')
    else:
        source = evaluation.text(link['artifact'])
        if location.get('kind') in _TEXT_KINDS:
            if not _matches_span(source, location):
                return None
            start, end = location['start'], location['end']
        elif location.get('kind') == 'html':
            anchor = location.get('anchor', {})
            if not _matches_span(source, anchor):
                return None
            span = origin.get('character_range', {'start': 0, 'end': anchor['end'] - anchor['start']})
            if type(span.get('start')) is not int or type(span.get('end')) is not int:
                return None
            if not 0 <= span['start'] <= span['end'] <= anchor['end'] - anchor['start']:
                return None
            start, end = anchor['start'] + span['start'], anchor['start'] + span['end']
        else:
            return None
    if (type(start) is int and type(end) is int and 0 <= start <= end <= len(source)
            and data == source[start:end].encode('utf-8')):
        return start, end
    return None


def _covers_passage(evaluation, packets, expected, link, ranges):
    if any(_has_source_content(evaluation, packet, link) for packet in packets):
        return True
    wanted = expected['origin']
    location = wanted['locator']
    if location['kind'] == 'pdf':
        # The checked typed PDF has the native original's exact bytes.
        return any(_has_source_content(evaluation, packet, dict(link, artifact=expected['excerpt'])) for packet in packets)
    if location['kind'] == 'html':
        span = wanted['character_range']
        start, end = location['anchor']['start'] + span['start'], location['anchor']['start'] + span['end']
    elif location['kind'] == 'json':
        start, end = wanted['character_range']['start'], wanted['character_range']['end']
    else:
        start, end = location['start'], location['end']
    # Validate and index the supplied ranges once per exact source/pointer in
    # this review history, rather than rereading all chunks for each chunk.
    family = (link['version_id'], link['source_id'], link['artifact']['sha256'],
              'json' if location['kind'] == 'json' else 'text', location.get('pointer'))
    if family not in ranges:
        intervals = []
        for packet in packets:
            for supplied in packet.get('prior_context', []) + packet.get('direction_evidence', {}).get('passages', []):
                interval = _find_supplied_interval(evaluation, packet, supplied, expected, link)
                if interval is not None:
                    intervals.append(interval)
        ranges[family] = sorted(intervals)
    intervals = ranges[family]
    position = start
    for first, last in intervals:
        if first > position:
            break
        position = max(position, last)
        if position >= end:
            return True
    return position >= end and bool(intervals)


def _has_visual_content(evaluation, packets, inspection):
    context = evaluation.link(inspection['link'])
    if context['visual'] is None:
        return True
    return not _find_visual_gaps(context) and all(
        any(has_attachment(evaluation, packet, asset['artifact']) for packet in packets)
        for asset in inspection['link']['locator'].get('assets', []))


def find_delivered_readings(records, artifacts, readings, packets, *, whole_text=True):
    """Require all inspected ranges and visual bytes in the observed history. With whole_text False (local repair v19,
    T22), a text inspection over the complete text is not required as passages, and a reading inspected only that
    way is delivered when the history attached its full text."""
    evaluation = Evaluation.of(records, artifacts)
    eligible = [reading for reading in readings
                if all(_has_visual_content(evaluation, packets, inspection) for inspection in reading['inspections'])]
    expected = build_reading_passages(records, evaluation, eligible, whole_text=whole_text)
    covered, ranges = set(), {}
    for reading in eligible:
        passages = [item for item, _ in expected if item['reading_id'] == reading['id']]
        if passages and all(_covers_passage(evaluation, packets, passage,
                reading['inspections'][passage['inspection_index']]['link'], ranges) for passage in passages):
            covered.add(reading['id'])
        elif not passages and not whole_text and reading['inspections'] and all(
                any(_has_source_content(evaluation, packet, inspection['link']) for packet in packets)
                for inspection in reading['inspections']):
            covered.add(reading['id'])
    return covered
