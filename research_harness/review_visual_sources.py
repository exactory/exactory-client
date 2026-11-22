"""Native PDF page provenance for actual image-capable reviewer inputs."""

import base64
import json

from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from . import review_pdf_pages

MARKER = 'visual_source_delivery'
PROTOCOL = 'pdf-page-images-v1'
# The fixed members of a render profile of this protocol. review_pdf_pages gives the profile of the installed
# renderer the same values; that module is part of the runtime identity of the visual route, so it keeps its bytes.
PROFILE_PARAMETERS = (('renderer', 'pdftoppm'), ('dpi', 150), ('scope', 'full-page'), ('rotation', 'native'),
                      ('format', 'png'), ('color', 'RGB'))


def _require(condition, message):
    if not condition:
        raise ResearchError('review_visual_source_mismatch', message)


def _reading_rows(packet):
    return (packet.get('problem_evidence', {}).get('readings', [])
            + packet.get('direction_evidence', {}).get('readings', []))


def _is_render_profile(profile):
    """Whether a value is a render profile of this protocol, whichever renderer build recorded it."""
    return (isinstance(profile, dict)
            and set(profile) == {name for name, _ in PROFILE_PARAMETERS} | {'version', 'executable_sha256'}
            and all(profile[name] == value for name, value in PROFILE_PARAMETERS)
            and isinstance(profile['version'], str) and bool(profile['version'])
            and isinstance(profile['executable_sha256'], str) and len(profile['executable_sha256']) == 64
            and all(character in '0123456789abcdef' for character in profile['executable_sha256']))


def _read_installed_profile():
    """The profile of the installed PDF renderer, or None when no renderer can be observed."""
    try:
        return review_pdf_pages.profile()
    except ResearchError:
        return None


def _validate_manifest(evaluation, manifest):
    evaluation.store.require_local_read('pdf_render')
    # Only the renderer that a manifest records can derive its pages again. Under another renderer, or without
    # one, the recorded page images are verified as the stored artifacts that the manifest names.
    installed = _read_installed_profile()
    derivable = isinstance(manifest, dict) and installed is not None and manifest.get('render_profile') == installed
    _require(isinstance(manifest, dict) and manifest.get('protocol') == PROTOCOL
             and (derivable or _is_render_profile(manifest.get('render_profile'))),
             'Use the exact native PDF render profile')
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
        if not derivable:
            _require(isinstance(page['image'], dict) and page['image'].get('media_type') == 'image/png'
                     and review_pdf_pages.dimensions(evaluation.read(page['image'])) == (page['width'], page['height']),
                     'The recorded page image must keep its descriptor and its dimensions')
            continue
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
    """Verify a recorded manifest: by a fresh derivation when the installed renderer is the one that it records, and
    from its stored page images otherwise."""
    return evaluation.once(('review_visual_manifest', digest(manifest)), lambda: _validate_manifest(evaluation, manifest))


def validate_delivered_manifest(evaluation, manifest):
    """Verify a manifest that enters a new packet. The installed renderer must be the one that the manifest
    records, so that each of its pages is derived again."""
    _require(isinstance(manifest, dict) and manifest.get('render_profile') == review_pdf_pages.profile(),
             'Use the exact native PDF render profile')
    return validate_manifest(evaluation, manifest)


def _iter_history_manifests(history):
    """The page manifests of the packets that a visible history holds."""
    from .review_transport import PACKET_LABEL
    for message in history:
        content = message.get('content')
        if message.get('role') == 'user' and isinstance(content, str) and content.startswith(PACKET_LABEL):
            manifest = json.loads(content[len(PACKET_LABEL):]).get(MARKER)
            if manifest is not None:
                yield manifest


def require_installed_renderer(records, artifacts, assignment):
    """Admit new reviewer work for an assignment on a visual route only when the installed renderer is the one that
    every page manifest of the work records: the manifests of its packet and of its history, where it has them, and
    of each visual transition in its lineage. New reviewer work is a new assignment or a new call. Only the profiles
    are compared; nothing is rendered."""
    from .review_bodies import read_history
    from .review_context_repair import _exposure_lineage
    from .review_visual_inputs import ADAPTER
    from .review_visual_route import MARKER as TRANSITION_MARKER
    if records['review_route'][assignment['route_id']]['adapter'] != ADAPTER:
        return
    evaluation = Evaluation.of(records, artifacts)
    manifests = []
    for row in _exposure_lineage(records, assignment):
        if 'packet' in row:
            packet = json.loads(evaluation.read(row['packet']))
            if row['id'] == assignment['id'] or TRANSITION_MARKER in packet:
                manifests.append(packet.get(MARKER))
    if 'history' in assignment or 'history_ref' in assignment:
        manifests.extend(_iter_history_manifests(read_history(evaluation, assignment)))
    manifests = [manifest for manifest in manifests if manifest is not None]
    if manifests:
        evaluation.store.require_local_read('pdf_render')
        installed = review_pdf_pages.profile()
        _require(all(isinstance(manifest, dict) and manifest.get('render_profile') == installed
                     for manifest in manifests), 'Use the exact native PDF render profile')


def prepare_packet(records, artifacts, packet, history=()):
    """Attach native pages and explicitly reattach visual pages carried in visible history."""
    evaluation = Evaluation.of(records, artifacts)
    evaluation.store.require_local_read('pdf_render')
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
    validate_delivered_manifest(evaluation, manifest)
    packet[MARKER] = manifest
    manifests = [manifest] + [validate_delivered_manifest(evaluation, prior)
                              for prior in _iter_history_manifests(history)]
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
