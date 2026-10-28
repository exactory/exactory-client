"""Native PDF page provenance for actual image-capable reviewer inputs."""

import base64
import json

from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from . import review_pdf_pages

MARKER = 'visual_source_delivery'
PROTOCOL = 'pdf-page-images-v1'


def _require(condition, message):
    if not condition:
        raise ResearchError('review_visual_source_mismatch', message)


def _reading_rows(packet):
    return (packet.get('problem_evidence', {}).get('readings', [])
            + packet.get('direction_evidence', {}).get('readings', []))


def _validate_manifest(evaluation, manifest):
    evaluation.require_external_effect()
    _require(isinstance(manifest, dict) and manifest.get('protocol') == PROTOCOL
             and manifest.get('render_profile') == review_pdf_pages.profile(), 'Use the exact native PDF render profile')
    sources, pages = manifest['sources'], manifest['pages']
    for source in sources:
        from .source_links import captured_source, fulltext_capture
        capture = fulltext_capture(evaluation.records['work'][source['version_id']], source['source_id'])
        native = captured_source(evaluation.records, evaluation, source['source_id'])
        _require(capture is not None and digest(capture) == source['capture_digest']
                 and capture['original'] == source['original'] and native['response'] == source['original'],
                 'The page original must bind the exact native version and capture')
    for page in pages:
        _require(type(page['source_index']) is int and 0 <= page['source_index'] < len(sources),
                 'A page must name its exact native source')
        source = sources[page['source_index']]
        key = ('review_pdf_page', digest(source['original']), page['page_index'], digest(manifest['render_profile']))
        _, derived = evaluation.once(key, lambda: review_pdf_pages.render_bytes(
            evaluation.read(source['original']), page['page_index']))
        _require(all(page[key] == derived[key] for key in ('image', 'width', 'height')),
                 'The image must equal a fresh derivation of this exact source page')
    used = set()
    for row in manifest['readings']:
        reading = evaluation.records.get('reading', {}).get(row['id'])
        _require(reading is not None and digest(reading) == row['digest'], 'The manifest must retain exact native reading bindings')
        for unit in row['units']:
            index, page_index = unit['inspection_index'], unit['page_index']
            _require(type(index) is int and 0 <= index < len(reading['inspections'])
                     and type(page_index) is int and 0 <= page_index < len(pages), 'Bind an existing inspection to an existing page')
            inspection, page = reading['inspections'][index], pages[page_index]
            source, link = sources[page['source_index']], inspection['link']
            from .reading import validate_read_evidence
            validate_read_evidence(evaluation.records, evaluation, link, depth='passage')
            _require(inspection['unit_id'] == unit['unit_id'] and link['locator']['kind'] == 'pdf'
                     and link['locator']['page_index'] == page['page_index']
                     and link['version_id'] == source['version_id'] and link['source_id'] == source['source_id']
                     and link['artifact'] == source['original'], 'The exact inspected native page must match its rendered page')
            used.add(page_index)
    _require(used == set(range(len(pages))), 'Every rendered source page needs its native reading inspection')
    return manifest


def validate_manifest(evaluation, manifest):
    return evaluation.once(('review_visual_manifest', digest(manifest)), lambda: _validate_manifest(evaluation, manifest))


def prepare_packet(records, artifacts, packet, history=()):
    """Attach native pages and explicitly reattach visual pages carried in visible history."""
    evaluation = Evaluation.of(records, artifacts)
    evaluation.require_external_effect()
    sources, pages, readings = [], [], []
    for row in _reading_rows(packet):
        reading = records.get('reading', {}).get(row['id'])
        _require(reading is not None and row.get('digest') == digest(reading), 'Readings must bind current exact native records')
        units = []
        for index, inspection in enumerate(reading['inspections']):
            link = inspection['link']
            if link['locator']['kind'] != 'pdf':
                continue
            from .reading import validate_read_evidence
            context = evaluation.link(link)
            validate_read_evidence(records, evaluation, link, depth='passage')
            source = {'version_id': link['version_id'], 'source_id': link['source_id'],
                      'capture_digest': digest(context['capture']), 'original': link['artifact']}
            if source not in sources:
                sources.append(source)
            source_index, number = sources.index(source), link['locator']['page_index']
            matching = [i for i, page in enumerate(pages) if page['source_index'] == source_index and page['page_index'] == number]
            if matching:
                page_index = matching[0]
            else:
                result = review_pdf_pages.render(evaluation, link['artifact'], number)
                page_index = len(pages)
                pages.append(dict(source_index=source_index, page_index=number,
                                  **{key: result[key] for key in ('image', 'width', 'height')}))
            units.append({'inspection_index': index, 'unit_id': inspection['unit_id'], 'page_index': page_index})
        if units:
            readings.append({'id': reading['id'], 'digest': digest(reading), 'units': units})
    manifest = {'protocol': PROTOCOL, 'render_profile': review_pdf_pages.profile(),
                'sources': sources, 'pages': pages, 'readings': readings}
    validate_manifest(evaluation, manifest)
    packet[MARKER] = manifest
    manifests = [manifest]
    from .review_transport import PACKET_LABEL
    for message in history:
        content = message.get('content')
        if message.get('role') == 'user' and isinstance(content, str) and content.startswith(PACKET_LABEL):
            prior = json.loads(content[len(PACKET_LABEL):]).get(MARKER)
            if prior is not None:
                manifests.append(validate_manifest(evaluation, prior))
    seen = set()
    for item in manifests:
        for page in item['pages']:
            reference = page['image']
            if reference['sha256'] in seen:
                continue
            seen.add(reference['sha256'])
            entry = {'artifact': reference, 'encoding': 'base64',
                     'content': base64.b64encode(evaluation.read(reference)).decode('ascii')}
            packet.setdefault('evidence', [])[:] = [value for value in packet.get('evidence', [])
                                                   if value.get('artifact', {}).get('sha256') != reference['sha256']]
            packet['evidence'].append(entry)
    return packet


def has_pdf_page(evaluation, packet, link):
    """Only an actually attached, natively derived full page covers a PDF page/region."""
    if link.get('locator', {}).get('kind') != 'pdf' or MARKER not in packet:
        return False
    try:
        evaluation.link(link)
        manifest = validate_manifest(evaluation, packet[MARKER])
        from .direction_sources import has_attachment
        for page in manifest['pages']:
            source = manifest['sources'][page['source_index']]
            if (all(source[key] == link[key] for key in ('version_id', 'source_id'))
                    and source['original'] == link['artifact'] and page['page_index'] == link['locator']['page_index']
                    and has_attachment(evaluation, packet, page['image'])):
                return True
    except (ResearchError, KeyError, TypeError, ValueError, OSError):
        return False
    return False
