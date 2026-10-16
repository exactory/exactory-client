"""Deliver and recognize checked source passages for decisive full readings."""

import base64
import hashlib
import json

from . import bar_sources
from .artifacts import describe_artifact
from .evaluation import Evaluation
from .errors import ResearchError
from .reading import validate_read_evidence
from .source_links import span_locator

_PASSAGE_CHARACTERS = 2000
# Match the existing scientific-delivery per-artifact bound for a visual PDF.
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


def _build_passage(records, evaluation, link):
    """Resolve checked passage bytes and provenance without storing a derivative."""
    context = evaluation.link(link)
    locator = link['locator']
    kind = locator['kind']
    media, flags = 'text/plain', {}
    if kind in _TEXT_KINDS:
        content = context['value']
        start = locator['start'] + len(content) - len(content.lstrip())
        end = min(locator['end'], start + _PASSAGE_CHARACTERS)
        source = evaluation.read(link['artifact']).decode('utf-8')
        location = span_locator(source, start, end)
        inspection = validate_read_evidence(records, evaluation, dict(link, locator=location), depth='passage')
        data = source[start:end].encode('utf-8')
        flags['truncated'] = end < locator['end']
    else:
        inspection = validate_read_evidence(records, evaluation, link, depth='passage')
        if kind == 'json':
            content = _encode_value(context['value'])
            data = content[:_PASSAGE_CHARACTERS].encode('utf-8')
            location = {'kind': kind, 'pointer': locator['pointer'], 'sha256': hashlib.sha256(data).hexdigest()}
            if not isinstance(context['value'], str) and len(content) <= _PASSAGE_CHARACTERS:
                media = 'application/json'
            flags['truncated'] = len(content) > _PASSAGE_CHARACTERS
        elif kind == 'pdf':
            capture = context['capture']
            # Page extraction has no region geometry and cannot supply an inspected
            # figure. Keep the checked original under the transport's PDF type,
            # including originals captured as generic binary.
            if capture['original']['size'] > _MAX_ORIGINAL_BYTES:
                raise ResearchError('review_evidence_too_large', 'The inspected PDF exceeds the scientific delivery artifact bound')
            data, media = evaluation.read(capture['original']), 'application/pdf'
            location = _compact_locator(locator)
            flags.update(source_original=True, truncated=False, visuals_require_source_request=False)
        else:
            # HTML inspections already have a checked anchor in the captured original.
            content = context['value']
            data = content[:_PASSAGE_CHARACTERS].encode('utf-8')
            location = _compact_locator(locator)
            flags.update(truncated=len(content) > _PASSAGE_CHARACTERS, visuals_require_source_request=True)
    origin = {'source_id': link['source_id'], 'artifact_descriptor': bar_sources.descriptor(link['artifact']),
              'locator': location}
    if kind == 'html' or kind == 'json' and flags['truncated']:
        origin['character_range'] = {'start': 0, 'end': min(len(content), _PASSAGE_CHARACTERS)}
    return ({'version_id': link['version_id'], 'inspection': inspection, 'origin': origin,
             'excerpt': describe_artifact(data, media), 'scientific_truth_certified': False, **flags}, data)


def build_reading_passages(records, artifacts, readings):
    """Return passage descriptors and bytes without writing or repeating reading."""
    evaluation = Evaluation.of(records, artifacts)
    result = []
    for reading in readings:
        for index, inspection in enumerate(reading['inspections']):
            passage, data = _build_passage(records, evaluation, inspection['link'])
            result.append((dict(passage, reading_id=reading['id'], inspection_index=index), data))
    return result


def _has_attachment(evaluation, packet, reference):
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


def _covers_passage(evaluation, packet, supplied, expected, expected_data, link):
    if (supplied.get('version_id') != expected['version_id']
            or not _has_attachment(evaluation, packet, supplied.get('excerpt'))):
        return False
    origin, wanted = supplied.get('origin', {}), expected['origin']
    if any(origin.get(key) != wanted[key] for key in ('source_id', 'artifact_descriptor')):
        return False
    data = evaluation.read(supplied['excerpt'])
    if not data.strip():
        return False
    if origin == wanted and data == expected_data:
        return True
    location, target = origin.get('locator', {}), wanted['locator']
    if location.get('kind') in _TEXT_KINDS and target['kind'] in _TEXT_KINDS:
        start, end = location.get('start'), location.get('end')
        source = evaluation.read(link['artifact']).decode('utf-8')
        return (type(start) is int and type(end) is int and 0 <= start <= target['start'] < target['end'] <= end <= len(source)
                and data == source[start:end].encode('utf-8') and location.get('sha256') == hashlib.sha256(data).hexdigest())
    if location.get('kind') == target['kind'] == 'json' and location.get('pointer') == target['pointer']:
        source = _encode_value(evaluation.link(link)['value'])
        span = origin.get('character_range', {'start': 0, 'end': len(source)})
        wanted_span = wanted.get('character_range', {'start': 0, 'end': len(source)})
        start, end = span.get('start'), span.get('end')
        return (type(start) is int and type(end) is int
                and 0 <= start <= wanted_span['start'] < wanted_span['end'] <= end <= len(source)
                and data == source[start:end].encode('utf-8') and location.get('sha256') == hashlib.sha256(data).hexdigest())
    return False


def _has_source_content(evaluation, packet, link):
    # An observed D7 dossier may already attach the full native source without
    # a source-request envelope. Match its exact inspected capture, not merely
    # the version: a later capture of the same version can contain new content.
    reference = link['artifact']
    deliveries = [item for item in packet.get('source_deliveries', []) if reference in item.get('evidence', [])]
    if deliveries and not any(item.get('status') == 'delivered' and item.get('request', {}).get('kind') == 'source'
            and item['request'].get('depth') == 'fulltext' and item['request'].get('version_id') == link['version_id']
            for item in deliveries):
        return False
    return _has_attachment(evaluation, packet, reference)


def find_delivered_readings(records, artifacts, readings, packets):
    """Count actual exact-capture source text or checked inspected passages only."""
    evaluation = Evaluation.of(records, artifacts)
    expected = build_reading_passages(records, evaluation, readings)
    covered = set()
    for reading in readings:
        passages = [(item, data) for item, data in expected if item['reading_id'] == reading['id']]
        if passages and all(any(
                _has_source_content(evaluation, packet, reading['inspections'][passage['inspection_index']]['link'])
                or any(_covers_passage(evaluation, packet, supplied, passage, data,
                           reading['inspections'][passage['inspection_index']]['link'])
                       for supplied in packet.get('prior_context', []) + packet.get('direction_evidence', {}).get('passages', []))
                for packet in packets) for passage, data in passages):
            covered.add(reading['id'])
    return covered
