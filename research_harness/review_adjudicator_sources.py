"""Complete current source bytes for prospective focused problem adjudications."""

from .errors import ResearchError

PROTOCOL = 'focused-current-sources-v1'
MARKER = 'current_source_delivery'
NOTE = (
    'This focused adjudication receives the complete current sources required by its bound readings. '
    'The current_source_delivery manifest records their exact native provenance and required inspected units; '
    'current_evidence and evidence supply their bytes. These sources are current evidence only. '
    'Original-context correction admissibility still uses only original_evidence and supplied_source_mappings '
    'of the original review pair. Current delivery does not certify prior inspection or scientific support.')


def _incomplete(details):
    raise ResearchError('review_current_source_incomplete',
        'Supply every required current source and inspected unit completely before focused adjudication.', details)


def prepare(records, artifacts, packet):
    """Validate native acquisitions and bind current sources separately from the original review pair."""
    from . import bar_sources
    from .problem_evidence import validate_reading
    from .evidence import digest
    bindings, units = [], []
    for entry in packet['problem_evidence']['readings']:
        reading = validate_reading(records, artifacts, {'kind': 'reading', 'version_id': entry['version_id'],
                                                       'reading_id': entry['id']})
        if digest(reading) != entry['digest']:
            _incomplete({'reading_id': entry['id'], 'reason': 'The current reading digest differs from its packet.'})
        bindings.append({key: entry[key] for key in ('id', 'version_id', 'digest')})
        for index, inspection in enumerate(reading['inspections']):
            link = inspection['link']
            units.append({'reading_id': reading['id'], 'inspection_index': index, 'unit_id': inspection['unit_id'],
                          'version_id': link['version_id'], 'source_id': link['source_id'],
                          'artifact': link['artifact'], 'locator': bar_sources._compact_locator(link['locator']),
                          'assets': link['locator'].get('assets', [])})
    sources = []
    for version in sorted({row['version_id'] for row in bindings}):
        work = records['work'][version]
        original_rows = {version: {'source_capture_digest': bar_sources._capture_digest(work, records)}}
        source = bar_sources._source(records, artifacts,
            {'kind': 'source', 'version_id': version, 'depth': 'fulltext',
             'reason': 'Required current evidence for the focused adjudication.'}, original_rows)
        if source['status'] != 'delivered' or source['source_scope_pending']:
            _incomplete({'version_id': version, 'status': source['status'],
                         'source_scope_pending': source['source_scope_pending']})
        sources.append(source)
    packet[MARKER] = {'protocol': PROTOCOL, 'basis': 'current-bound-full-readings', 'readings': bindings,
                      'sources': sources, 'required_units': units, 'note': NOTE}
    packet['problem_evidence']['delivery'] = {'protocol': PROTOCOL, 'note': NOTE}


def validate(artifacts, packet):
    """Check frozen current requirements against actual complete byte entries, without consulting current records."""
    from .direction_sources import has_attachment
    from .review_protocol import collect_references
    manifest = packet.get(MARKER)
    if manifest is None:
        return
    if not isinstance(manifest, dict) or manifest.get('protocol') != PROTOCOL:
        _incomplete({'reason': 'The current source delivery protocol is missing or unsupported.'})
    expected = [{key: row[key] for key in ('id', 'version_id', 'digest')}
                for row in packet.get('problem_evidence', {}).get('readings', [])]
    if manifest.get('readings') != expected:
        _incomplete({'reason': 'The source requirements do not bind the exact current readings.'})
    expected_units = [(row['id'], index, inspection['unit_id'], inspection['version_id'])
                      for row in packet.get('problem_evidence', {}).get('readings', [])
                      for index, inspection in enumerate(row['reading']['inspections'])]
    supplied_units = [(row.get('reading_id'), row.get('inspection_index'), row.get('unit_id'), row.get('version_id'))
                      for row in manifest.get('required_units', [])]
    if supplied_units != expected_units:
        _incomplete({'reason': 'The required inspected units do not match the frozen reading projections.'})
    sources = manifest.get('sources', [])
    versions = sorted({row['version_id'] for row in expected})
    if [row.get('request', {}).get('version_id') for row in sources] != versions:
        _incomplete({'reason': 'The exact current source versions are incomplete.'})
    for source in sources:
        if (source.get('status') != 'delivered' or source.get('source_scope_pending')
                or source.get('request', {}).get('depth') != 'fulltext' or not source.get('evidence')):
            _incomplete({'version_id': source.get('request', {}).get('version_id'), 'reason': 'Required source scope remains incomplete.'})
        from .bar_sources import _reference
        complete_main = False
        for alternative in source.get('source_alternatives', []):
            if not alternative.get('main_representation') or alternative.get('source_scope_pending'):
                continue
            try:
                reference = _reference(alternative['extraction_descriptor'])
                complete_main = reference in source['evidence'] and has_attachment(artifacts, packet, reference)
            except (OSError, ResearchError, KeyError, TypeError, ValueError):
                complete_main = False
            if complete_main:
                break
        if not complete_main:
            _incomplete({'version_id': source.get('request', {}).get('version_id'),
                         'reason': 'No validated complete main extraction is actually supplied.'})
    required = {reference['sha256']: reference for reference in collect_references(
        [source.get('evidence', []) for source in sources] + [manifest.get('required_units', [])])}
    missing = []
    for sha256, reference in sorted(required.items()):
        try:
            supplied = reference in packet.get('current_evidence', []) and has_attachment(artifacts, packet, reference)
        except (OSError, ResearchError, ValueError):
            supplied = False
        if not supplied:
            missing.append(sha256)
    if missing:
        _incomplete({'artifact_digests': missing})


def validate_request(artifacts, frozen, request):
    """Check the frozen contract against exact model input, including actual inline file/image attachments."""
    if MARKER not in frozen:
        return
    import json
    from .review_input_projection import collect_attachments, find_attachment
    from .review_transport import logical_messages, PACKET_LABEL
    try:
        message = logical_messages(request)[-1]
        content = message['content']
        attachments = {}
        if isinstance(content, list):
            texts = [part['text'] for part in content if part.get('type') == 'input_text'
                     and isinstance(part.get('text'), str) and part['text'].startswith(PACKET_LABEL)]
            if len(texts) != 1:
                _incomplete({'reason': 'The actual input has no unique focused packet.'})
            attachments = collect_attachments(content)
            content = texts[0]
        if message.get('role') != 'user' or not isinstance(content, str) or not content.startswith(PACKET_LABEL):
            _incomplete({'reason': 'The actual focused input is not an inspected packet.'})
        actual = json.loads(content[len(PACKET_LABEL):])
        if actual.get(MARKER) != frozen[MARKER]:
            _incomplete({'reason': 'The actual input does not retain its exact frozen current-source requirements.'})
        binary_digests = {entry['artifact']['sha256'] for entry in frozen.get('evidence', [])
                          if entry.get('encoding') == 'base64'
                          or entry['artifact']['media_type'] == 'application/pdf'
                          or entry['artifact']['media_type'].startswith('image/')}
        for entry in actual.get('evidence', []):
            reference = entry['artifact']
            binary = (reference['sha256'] in binary_digests or reference['media_type'] == 'application/pdf'
                      or reference['media_type'].startswith('image/'))
            if entry.get('encoding') != 'base64' and not binary:
                continue
            entry.pop('content', None)
            if entry.get('encoding') != 'base64' or entry.get('delivery') != 'attached-exact-bytes':
                continue
            matched = find_attachment(attachments, reference)
            if matched is not None:
                entry['content'] = matched
        validate(artifacts, actual)
    except (KeyError, IndexError, TypeError, ValueError) as error:
        _incomplete({'reason': 'The actual focused packet or its binary attachments are malformed.'})


def validate_observed(records, artifacts, assignment, attempt_id):
    """The caller authenticates the attempt; validate its saved actual request rather than pre-transport bytes."""
    import json
    frozen = json.loads(artifacts.read(assignment['packet']))
    if MARKER not in frozen:
        return
    attempt = records.get('review_attempt', {}).get(attempt_id)
    if attempt is None or attempt.get('assignment_id') != assignment['id'] or attempt.get('status') != 'completed':
        _incomplete({'reason': 'Complete actual source input requires its observed focused attempt.'})
    try:
        request = json.loads(artifacts.read(attempt['request']))
    except (OSError, ResearchError, KeyError, TypeError, ValueError):
        _incomplete({'reason': 'The exact observed focused request is unavailable.'})
    validate_request(artifacts, frozen, request)
