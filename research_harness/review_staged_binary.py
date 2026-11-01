"""Native staged originals through the existing authenticated PDF page contract."""

import copy

from . import review_excerpts, review_visual_inputs, review_visual_sources
from .direction_sources import has_attachment
from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest


def _require(condition, message):
    if not condition:
        raise ResearchError('review_task_dependency_changed', message)


def _raise_unsupported(rows, reason):
    raise ResearchError('review_staged_binary_unsupported',
        'The assigned native originals cannot be completely delivered on this route.',
        dict(source_ids=[row['source_id'] for row in rows], reason=reason))


def _read_assigned_sources(epoch, task):
    sources = {row['id']: row for row in epoch['manifest']['sources']}
    rows = task['binary_sources']
    for row in rows:
        source = sources.get(row.get('source_id'))
        _require(source is not None and source['kind'] == 'binary'
                 and row == dict(source_id=source['id'], descriptor=source['descriptor']),
                 'Every assigned binary must retain its exact frozen source and descriptor')
    return rows


def _select_reading_rows(records, packet, rows):
    references = (packet.get('problem_evidence', {}).get('readings', [])
                  + packet.get('direction_evidence', {}).get('readings', []))
    if review_visual_sources.MARKER in packet:
        references += packet[review_visual_sources.MARKER].get('readings', [])
    result = []
    for reference in references:
        reading = records.get('reading', {}).get(reference.get('id'))
        if reading is not None and any(
                inspection['link'].get('locator', {}).get('kind') == 'pdf'
                and any(inspection['link']['artifact'] == row['descriptor'] for row in rows)
                for inspection in reading['inspections']):
            _require(reference.get('digest') == digest(reading),
                     'Native task readings must retain their exact frozen identity')
            selected = dict(id=reading['id'], digest=digest(reading))
            if selected not in result:
                result.append(selected)
    return result


def _select_manifest(manifest, rows):
    """Keep assigned originals while retaining exact native inspection indexes."""
    result = dict(protocol=manifest['protocol'], render_profile=copy.deepcopy(manifest['render_profile']),
                  sources=[], pages=[], readings=[])
    source_indexes, page_indexes = {}, {}
    for index, page in enumerate(manifest['pages']):
        source = manifest['sources'][page['source_index']]
        if not any(source['original'] == row['descriptor'] for row in rows):
            continue
        old = page['source_index']
        if old not in source_indexes:
            source_indexes[old] = len(result['sources'])
            result['sources'].append(copy.deepcopy(source))
        page_indexes[index] = len(result['pages'])
        result['pages'].append(dict(copy.deepcopy(page), source_index=source_indexes[old]))
    for reading in manifest['readings']:
        units = [dict(copy.deepcopy(unit), page_index=page_indexes[unit['page_index']])
                 for unit in reading['units'] if unit['page_index'] in page_indexes]
        if units:
            result['readings'].append(dict(id=reading['id'], digest=reading['digest'], units=units))
    return result


def _require_complete(evaluation, manifest, rows, packet):
    """An opaque source obligation needs all native pages, not a selected region."""
    from .source_links import _read_output_pdf_page_count
    review_visual_sources.validate_manifest(evaluation, manifest)
    for row in rows:
        pages = [page for page in manifest['pages']
                 if manifest['sources'][page['source_index']]['original'] == row['descriptor']]
        if not pages:
            _raise_unsupported([row], 'native_original_unavailable')
        original = evaluation.read(row['descriptor'])
        if not original.startswith(b'%PDF-'):
            _raise_unsupported([row], 'native_original_unavailable')
        count = evaluation.once(('staged_native_pdf_pages', digest(row['descriptor'])),
                                lambda: _read_output_pdf_page_count(original))
        if sorted({page['page_index'] for page in pages}) != list(range(count)):
            _raise_unsupported([row], 'native_original_pages_incomplete')
        _require(all(has_attachment(evaluation, packet, page['image']) for page in pages),
                 'Every native original page must be present as exact actual image bytes')


def prepare_packet(records, artifacts, route, epoch, task, packet, *, source_packet=None):
    """Attach only assigned native originals; unsupported work gets a typed failure.

    The controller packet supplies exact native reading identities. Text tasks on
    a visual route still need the existing fresh complete-source image envelope.
    """
    rows = _read_assigned_sources(epoch, task)
    if route['adapter'] != review_visual_inputs.ADAPTER:
        if rows:
            _raise_unsupported(rows, 'route_lacks_native_binary_delivery')
        return packet
    evaluation = Evaluation.of(records, artifacts)
    native = dict(problem_evidence=dict(readings=_select_reading_rows(records, source_packet or {}, rows)), evidence=[])
    try:
        review_visual_sources.prepare_packet(records, evaluation, native)
        manifest = _select_manifest(native[review_visual_sources.MARKER], rows)
        native[review_visual_sources.MARKER] = manifest
        images = {page['image']['sha256'] for page in manifest['pages']}
        native['evidence'] = [entry for entry in native['evidence'] if entry['artifact']['sha256'] in images]
        _require_complete(evaluation, manifest, rows, native)
    except ResearchError as error:
        if error.code in ('review_staged_binary_unsupported', 'review_task_dependency_changed'):
            raise
        _raise_unsupported(rows, 'native_original_unavailable')
    from .review_source_complete import MARKER, PROTOCOL
    packet[MARKER] = dict(protocol=PROTOCOL, history_digest=digest([]), sources=[],
        alternate_body_sha256=[], protected_sha256=sorted(images), restored_evidence=[])
    packet[review_visual_sources.MARKER] = manifest
    packet['staged_review']['binary_sources'] = copy.deepcopy(rows)
    for entry in native['evidence']:
        packet.setdefault('evidence', [])[:] = [value for value in packet.get('evidence', [])
                                               if value['artifact'] != entry['artifact']]
        packet['evidence'].append(copy.deepcopy(entry))
    return packet


def _read_actual_packet(records, artifacts, request):
    """Recover native bytes from actual image inputs, retaining all logical text."""
    projected = review_visual_inputs.project_request(request)
    _require(len(projected['messages']) == 1 and projected['messages'][0]['role'] == 'user',
             'A native staged task must use a fresh request without visible history')
    packet = review_excerpts.read_sent_packet(request)
    _require(isinstance(packet, dict), 'The actual request must retain its staged packet')
    packet = copy.deepcopy(packet)
    for entry in projected['images']:
        packet.setdefault('evidence', [])[:] = [value for value in packet.get('evidence', [])
                                               if value.get('artifact') != entry['artifact']]
        packet['evidence'].append(entry)
    return Evaluation.of(records, artifacts), packet


def read_actual_sources(records, artifacts, epoch, task, request):
    """Credit binaries only after checking the observed native request and pages."""
    rows = _read_assigned_sources(epoch, task)
    if not rows:
        return []
    evaluation, packet = _read_actual_packet(records, artifacts, request)
    _require(packet.get('staged_review', {}).get('binary_sources') == rows,
             'Actual native original identities must equal the assigned binary scope')
    manifest = packet.get(review_visual_sources.MARKER)
    _require(isinstance(manifest, dict), 'Actual binary input needs its native page manifest')
    _require_complete(evaluation, manifest, rows, packet)
    return copy.deepcopy(rows)


def preserves_packet(records, artifacts, packet, request):
    """Check exact task text and attachments, including native image input bytes."""
    try:
        evaluation, sent = _read_actual_packet(records, artifacts, request)
        if any(sent.get(key) != value for key, value in packet.items() if key not in ('digest', 'evidence')):
            return False
        manifest = sent.get(review_visual_sources.MARKER)
        if not isinstance(manifest, dict):
            return False
        review_visual_sources.validate_manifest(evaluation, manifest)
        return all(has_attachment(evaluation, sent, entry['artifact']) for entry in packet.get('evidence', [])
                   if entry.get('encoding') in ('utf-8', 'base64'))
    except (ResearchError, KeyError, TypeError, ValueError, OSError):
        return False
