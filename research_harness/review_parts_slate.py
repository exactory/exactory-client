"""Slate records, their occurrences and carried artifact bodies for parts v3."""

from . import review_parts_prompts as prompts
from .errors import ResearchError
from .evidence import digest

RECORD_UNIT_RULE = 'exactory-slate-record-units-v1'
SERIES_PROTOCOL = 'exactory-review-parts-v3'
TEXT_CAPTURE_KIND = 'review_text_capture'
INVALID_TEXT_CAPTURE = (
    'The text form of this body was not recorded. Use the payload of the capture step as it stands, and choose its '
    'path alone: the id is review-text- followed by the SHA-256 of the body that text_form_of names. The path '
    'names a file of the workspace that no probe holds. The file is UTF-8 text that is not blank, holds no NUL '
    'character and is no PDF and no image, also by its media_type. Correct the payload or the file and take the '
    'step again.')


def applies(value, packet):
    from .review_session_v4 import PROTOCOL as SESSION_V4
    return (value.get('role') == 'slate'
            and packet.get('subject', {}).get('kind') == 'problem'
            and 'context_repair' not in packet
            and packet.get('source_rules') != SESSION_V4)


def list_units(packet):
    """Keep record wrappers as occurrences; bodies have one independent owner."""
    units = []

    def visit(value, path):
        if isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict) and {'record', 'cite'} <= item.keys():
                    cite = item['cite']
                    identity = {key: cite[key] for key in ('record_kind', 'id', 'digest')}
                    occurrence = dict(path=path, index=index, record_field='record', digest=digest(item),
                                      fields={key: member for key, member in item.items() if key != 'record'})
                    units.append(dict(item=item['record'], record_identity=identity,
                                      occurrences=[occurrence], citations=[cite], bodies=[]))
                else:
                    visit(item, path + [index])
        elif isinstance(value, dict):
            for key, item in value.items():
                visit(item, path + [key])

    for key in ('forming_records', 'problem_forming_imports', 'candidates', 'account_records', 'problem_evidence'):
        visit(packet.get(key, []), [key])
    for entry in packet.get('evidence', []):
        artifact = entry['artifact']
        item = (dict(artifact=artifact, text_form_of=entry['text_form_of'])
                if 'text_form_of' in entry else artifact)
        units.append(dict(item=item, occurrences=[], citations=[artifact], bodies=[entry]))
    return units


def list_targets(packet):
    from .review_composition import find_rule, list_targets as list_composition_targets
    # The composition rule normally delegates to registered adapters. Its slate
    # fallback owns these same targets, so read it without delegating back here.
    rule = dict(find_rule('slate', packet), adapter=None)
    return list_composition_targets(rule, packet)


def compute_series_key(packet):
    return dict(candidates=packet.get('candidate_set_digest'), bars=digest(packet.get('bars', [])))


def derive_text_capture_id(body):
    return 'review-text-' + body['sha256']


def read_capture_text(data, media_type, *, allow_blank=False):
    from .fulltext import parse_base_media_type
    from .review_transport import is_binary_evidence
    if (is_binary_evidence(data, 'application/octet-stream')
            or is_binary_evidence(data, parse_base_media_type(media_type))
            or b'\x00' in data):
        return None
    try:
        content = data.decode('utf-8')
    except UnicodeDecodeError:
        return None
    return content if allow_blank or content.strip() else None


def find_forming_material(packet):
    return {key: packet[key] for key in ('forming_records', 'problem_forming_imports') if key in packet}


def retain_frozen_import_positions(packet, frozen):
    """Add new origins and records after the existing inventory addresses."""
    old = frozen.get('problem_forming_imports', [])
    current = packet.get('problem_forming_imports', [])
    if not old or not current:
        return packet
    previous = {row['source_origin']: row for row in old}
    positions = {origin: index for index, origin in enumerate(previous)}
    projections = []
    for row in sorted(current, key=lambda item: positions.get(item['source_origin'], len(positions))):
        before = previous.get(row['source_origin'])
        if before is not None:
            records = {digest(item['cite']): index for index, item in enumerate(before['records'])}
            row = dict(row, records=sorted(row['records'],
                key=lambda item: records.get(digest(item['cite']), len(records))))
        projections.append(row)
    return dict(packet, problem_forming_imports=projections)


def prepare_series_bodies(records, artifacts, packet, *, frozen=None, include_text=True, held=None):
    """Bind verified text to each forming original while retaining a stored series' choices.

    With held, the packet of the first turn of a running single chain, each original that it held keeps the form
    that the first turn gave it, and only an added original needs its text."""
    from . import bar_sources
    from .review_protocol import collect_references

    if frozen is not None:
        packet = retain_frozen_import_positions(packet, frozen)
    forming = find_forming_material(packet)
    originals = {row['sha256']: bar_sources._reference(row)
                 for row in bar_sources.collect_record_artifacts(forming)}
    held_originals = {row['sha256'] for row in bar_sources.collect_record_artifacts(find_forming_material(held or {}))}
    held_rows = {row['text_form_of']['sha256']: row for row in (held or {}).get('evidence', []) if 'text_form_of' in row}
    evidence = packet.get('evidence', [])
    if include_text:
        for row in evidence:
            if row.get('encoding') == 'omitted':
                reference = row['artifact']
                originals.setdefault(reference['sha256'], reference)
    if not originals:
        return packet, None
    raw = {ref['sha256'] for ref in collect_references(packet)}
    frozen_rows = {}
    for row in (frozen or {}).get('evidence', []):
        original = row.get('text_form_of', row['artifact'])
        if original['sha256'] in originals and row['encoding'] == 'utf-8':
            frozen_rows[original['sha256']] = row
    selected = []
    for identity, original in originals.items():
        row = frozen_rows.get(identity) or held_rows.get(identity)
        if row is None and identity in held_originals:
            continue
        if row is None:
            content = read_capture_text(artifacts.read(original), original['media_type'], allow_blank=True)
            if content is not None:
                if not include_text and identity not in raw:
                    continue
                row = dict(artifact=original, content=content, encoding='utf-8')
                if identity not in raw:
                    row['delivery'] = 'forming-text-on-request'
            else:
                row = find_text_form(records, artifacts, original)
        if row is None:
            return packet, original
        selected.append(row)
    replaced = ((originals.keys() - held_originals) | {row['artifact']['sha256'] for row in selected}
                | {row['text_form_of']['sha256'] for row in selected if 'text_form_of' in row})
    packet = dict(packet, evidence=[row for row in evidence if row['artifact']['sha256'] not in replaced] + selected)
    packet.pop('digest', None)
    packet['digest'] = digest(packet)
    return packet, None


def find_text_form(records, artifacts, original):
    from .bar_sources import descriptor
    declaration = records.get(TEXT_CAPTURE_KIND, {}).get(derive_text_capture_id(original), {})
    candidates = [declaration['artifact']] if declaration.get('text_form_of') == original else []
    candidates.extend(row['text'] for work in records.get('work', {}).values() for row in work.get('fulltexts', [])
                      if row.get('original') == original and row.get('text'))
    for reference in candidates:
        try:
            content = read_capture_text(artifacts.read(reference), reference['media_type'])
        except ResearchError as error:
            if error.code not in ('artifact_missing', 'artifact_corrupt'):
                raise
            continue
        if content is not None:
            return dict(artifact=reference, encoding='utf-8', content=content, text_form_of=descriptor(original))
    return None


def requires_body_binding(packet):
    return bool(packet.get('role') == 'slate' and find_forming_material(packet))


def bind_assignment_bodies(records, artifacts, value, packet, *, parent=None):
    """Require a text form at entry. A running parts series binds its own additions, and a running single chain
    keeps the forms of its first turn and binds only an added original."""
    from . import review_parts
    from .review_protocol import _read_json, canonical_prompt
    from .review_route_capacity import require_covered_assignment

    if not requires_body_binding(packet):
        return packet
    continuing = parent is not None and not review_parts.starts_refresh(value, packet)
    if continuing and parent.get(review_parts.KEY, {}).get('adapter') == 'problem_slate':
        return packet
    held = _read_json(artifacts, parent['packet']) if continuing else None
    prepared, missing = prepare_series_bodies(records, artifacts, packet, include_text=False, held=held)
    if missing is not None:
        prompt = canonical_prompt('slate', orchestration='problem_creation')
        require_covered_assignment(records, artifacts, value, packet, prompt, series_body=missing)
    return prepared


def supports_series_entry(value, route, packet):
    """Whether the new slate planner may handle a missing forming body."""
    from . import review_parts, review_transport as transport
    return (SERIES_PROTOCOL in review_parts.NEW_SERIES_PROTOCOLS
            and route['adapter'] in transport.TEXT_ONLY_ADAPTERS
            and transport.local_input_limit(route) is not None and applies(value, packet))


def can_start_series(records, artifacts, value, route, packet, prompt, history=()):
    """A new slate enters parts only after its first session request cannot fit."""
    from . import review_parts, review_transport as transport
    from .review_route_capacity import _find_body_without_text, can_send_ordinary_packet
    prior_id = value.get('context', {}).get('prior_assignment_id')
    if prior_id is not None and not review_parts.starts_refresh(value, packet):
        prior = records['review_assignment'][prior_id]
        return prior.get(review_parts.KEY, {}).get('adapter') == 'problem_slate'
    protocols, _ = review_parts.select_series_protocols(records, artifacts, value)
    if SERIES_PROTOCOL not in protocols or not supports_series_entry(value, route, packet):
        return False
    request = transport.request_body(route, prompt['text'], packet, history)
    if (_find_body_without_text(records, packet) is None
            and can_send_ordinary_packet(records, artifacts, route, packet, request)):
        return False
    return True


def validate_final(records, artifacts, assignment, output, *, attempt=None):
    from .research_decisions import validate_review_contract
    return validate_review_contract(records, artifacts, assignment, output, attempt=attempt)


def covers_items(packets, expected, key):
    from .review_parts import covers_items as covers_collection
    return covers_collection(packets, expected, key)


ADAPTER = dict(applies=applies, enumerate_units=list_units, enumerate_record_units=list_units,
               unit_rule=RECORD_UNIT_RULE, targets=list_targets, series_key=compute_series_key,
               validate_final=validate_final, covers_items=covers_items,
               final_prompt_v3=prompts.FINAL_SLATE_V3,
               source_requests=True, protocols=(SERIES_PROTOCOL,), can_start_series=can_start_series)
