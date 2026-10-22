"""Pure image binding and deterministic requests for the versioned visual route."""

import base64
import binascii
import copy
import json
from functools import lru_cache

from .artifacts import describe_artifact, validate_reference
from .errors import ResearchError
from . import review_pdf_pages

ADAPTER = 'codex_cli_visual_v1'


def _require(condition, message):
    if not condition:
        raise ResearchError('review_visual_input_mismatch', message)


def _packets(messages):
    from .review_transport import PACKET_LABEL
    for index, message in enumerate(messages):
        text = message.get('content')
        if message.get('role') == 'user' and isinstance(text, str) and text.startswith(PACKET_LABEL):
            yield index, json.loads(text[len(PACKET_LABEL):])


def _expected(messages):
    from .review_visual_sources import MARKER, PROTOCOL
    packets = list(_packets(messages))
    wanted, bindings = [], {}
    # The current packet owns the supplied image order, followed by prior visible page requirements.
    for index, packet in packets[-1:] + packets[:-1]:
        manifest = packet.get(MARKER)
        if manifest is None:
            continue
        _require(manifest.get('protocol') == PROTOCOL, 'Unknown visual page delivery protocol')
        bindings[index] = []
        for page in manifest['pages']:
            reference = page['image']
            validate_reference(reference)
            _require(reference['media_type'] == 'image/png', 'Native PDF pages require PNG images')
            bindings[index].append(reference['sha256'])
            if reference not in wanted:
                _require(not any(row['sha256'] == reference['sha256'] for row in wanted),
                         'One image digest cannot have conflicting descriptors')
                wanted.append(reference)
    return wanted, bindings


def project_request(request):
    """Decode only this request and verify its exact declared ordered image inputs."""
    from .review_transport import logical_messages
    try:
        messages = logical_messages(request)
        wanted, bindings = _expected(messages)
        supplied = request.get('exactory_image_inputs', [])
        _require(len(supplied) == len(wanted), 'All and only visible native page images must be explicitly attached')
        images = []
        for row, reference in zip(supplied, wanted):
            _require(set(row) == {'artifact', 'data_url'} and row['artifact'] == reference,
                     'Actual image descriptors and order must match the visible native page manifest')
            value = row['data_url']
            prefix = 'data:image/png;base64,'
            _require(isinstance(value, str) and value.startswith(prefix), 'An actual inline PNG data URI is required')
            data = base64.b64decode(value[len(prefix):], validate=True)
            _require(describe_artifact(data, 'image/png') == reference, 'Actual PNG bytes must match their exact descriptor')
            review_pdf_pages.dimensions(data)
            images.append({'artifact': reference, 'encoding': 'base64',
                           'content': base64.b64encode(data).decode('ascii')})
        return {'messages': messages, 'images': images, 'packet_images': bindings}
    except (KeyError, TypeError, ValueError, binascii.Error) as error:
        raise ResearchError('review_visual_input_mismatch', 'Malformed actual visual reviewer input') from error


@lru_cache(maxsize=64)
def control_image(text):
    """Render an operational control using the same fixed RGB page renderer, without textual delivery."""
    _require(isinstance(text, str) and text and len(text) <= 64 and all(32 <= ord(c) <= 126 for c in text),
             'Visual control text must be bounded printable ASCII')
    escaped = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
    # Sixteen characters per line keep a 32-digit code comfortably within the image width.
    lines = [escaped[i:i + 16] for i in range(0, len(escaped), 16)]
    stream = ('BT /F1 30 Tf 60 730 Td ' + ' 0 -48 Td '.join('(' + line + ') Tj' for line in lines) + ' ET\n').encode()
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 841.92] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>',
        b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'endstream']
    body, offsets = bytearray(b'%PDF-1.4\n'), []
    for number, value in enumerate(objects, 1):
        offsets.append(len(body)); body.extend(str(number).encode() + b' 0 obj\n' + value + b'\nendobj\n')
    start = len(body)
    body.extend(b'xref\n0 6\n0000000000 65535 f \n')
    for offset in offsets:
        body.extend(f'{offset:010d} 00000 n \n'.encode())
    body.extend(b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n' + str(start).encode() + b'\n%%EOF\n')
    return review_pdf_pages.render_bytes(bytes(body), 0)[0]


def _base_request(route, prompt, messages, images):
    return {'model': route['model'], 'instructions': prompt, 'input': messages, 'tools': [], 'store': False,
            'max_output_tokens': route['configuration']['max_output_tokens'],
            'text': {'format': {'type': 'json_object'}}, 'exactory_image_inputs': images}


def probe_request(route, prompt, packet):
    from .review_transport import PACKET_LABEL
    _require(set(packet) == {'allowed_control', 'evidence_control', 'evidence'}, 'Use the native operational probe shape')
    texts = [packet['allowed_control'], packet['evidence_control']] + [f'padding page {n:02d}' for n in range(3, 20)]
    images = []
    for text in texts:
        data = control_image(text)
        images.append({'artifact': describe_artifact(data, 'image/png'),
                       'data_url': 'data:image/png;base64,' + base64.b64encode(data).decode('ascii')})
    supplied = {'visual_probe': 'two-image-codes-nineteen-inputs-v2', 'image_count': 19,
                'code_image_indices': {'allowed_control': 1, 'evidence_control': 2},
                'code_layout': {'lines': 2, 'characters_per_line': 16}}
    request = _base_request(route, prompt, [{'role': 'user', 'content': PACKET_LABEL + json.dumps(supplied, sort_keys=True)}], images)
    visible = json.dumps(request['input']) + request['instructions'] + json.dumps([row['artifact'] for row in images])
    _require(all(packet[key] not in visible for key in ('allowed_control', 'evidence_control')),
             'Positive visual controls must be absent from all delivered text and metadata')
    return request


def source_request(route, prompt, packet, history=()):
    """Keep complete exact logical text while moving only native PNG bodies into actual image inputs."""
    from .review_transport import PACKET_LABEL, OMITTED_BYTES_NOTE
    from .review_source_complete import MARKER, project
    from .review_exact_text_v2 import encode
    supplied = copy.deepcopy(packet)
    messages = list(history) + [{'role': 'user', 'content': PACKET_LABEL + json.dumps(supplied, sort_keys=True, ensure_ascii=False)}]
    wanted, _ = _expected(messages)
    entries = {entry.get('artifact', {}).get('sha256'): entry for entry in supplied.get('evidence', [])}
    images = []
    for reference in wanted:
        entry = entries.get(reference['sha256'])
        _require(entry is not None and entry.get('artifact') == reference and entry.get('encoding') == 'base64',
                 'Explicitly attach every current and historical visual page before invoking the reviewer')
        data = base64.b64decode(entry['content'], validate=True)
        _require(describe_artifact(data, 'image/png') == reference, 'Image evidence bytes must match the native manifest')
        images.append({'artifact': reference, 'data_url': 'data:image/png;base64,' + base64.b64encode(data).decode('ascii')})
    attached = {reference['sha256'] for reference in wanted}
    for entry in supplied.get('evidence', []):
        if entry.get('encoding') != 'base64':
            continue
        entry.pop('content', None)
        if entry['artifact']['sha256'] in attached:
            entry['delivery'] = 'attached-exact-image-bytes'
        else:
            entry.update(encoding='omitted', delivery='bytes-omitted-on-text-route', note=OMITTED_BYTES_NOTE)
    _require(MARKER in supplied, 'The visual result route requires a native complete-source continuation')
    logical, complete_sources = project(supplied, list(history))
    request = _base_request(route, prompt, [{'role': 'user', 'content': encode(logical, complete_sources)}], images)
    project_request(request)
    return request
