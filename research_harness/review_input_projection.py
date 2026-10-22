"""Project packet content solely from the actual native request representation."""

import base64
import binascii
import hashlib

from . import review_transport as transport
from .errors import ResearchError
from .workspace import strict_json


def _attachments(parts):
    """Index only actual inline native file and image inputs by exact media and bytes."""
    attached = {}
    for part in parts:
        if not isinstance(part, dict):
            continue
        kind = part.get('type')
        value = part.get('file_data') if kind == 'input_file' else part.get('image_url') if kind == 'input_image' else None
        if not isinstance(value, str) or not value.startswith('data:') or ';base64,' not in value:
            continue
        media, encoded = value[5:].split(';base64,', 1)
        if (kind == 'input_file' and media != 'application/pdf') or (kind == 'input_image' and not media.startswith('image/')):
            continue
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            continue
        attached[(media, len(data), hashlib.sha256(data).hexdigest())] = encoded
    return attached


def _require_problem_packet(packet):
    """A packet label cannot stand in for a native scientific review identity."""
    subject = packet.get('subject') if isinstance(packet, dict) else None
    identifier = packet.get('digest') if isinstance(packet, dict) else None
    valid = (isinstance(packet, dict)
             and packet.get('protocol') in ('exactory-problem-review-v1', 'exactory-problem-review-v2')
             and packet.get('role') in ('bar', 'slate', 'result')
             and isinstance(packet.get('reviewer_id'), str) and bool(packet['reviewer_id'].strip())
             and 'dossier_id' in packet and packet['dossier_id'] is None
             and isinstance(subject, dict) and set(subject) == {'kind', 'statement_id'}
             and subject['kind'] == 'problem'
             and (isinstance(subject['statement_id'], str) or subject['statement_id'] is None)
             and (packet['role'] != 'result' or bool(subject['statement_id']))
             and isinstance(identifier, str) and len(identifier) == 64
             and all(character in '0123456789abcdef' for character in identifier)
             and isinstance(packet.get('evidence', []), list))
    if not valid:
        raise ResearchError('review_input_unreadable', 'Use a native problem review packet with its exact identity')


def _binary(reference):
    media = reference.get('media_type')
    if not isinstance(media, str):
        raise ResearchError('review_input_unreadable', 'Evidence requires an explicit native media type')
    media = media.split(';', 1)[0].strip().lower()
    return media in ('application/pdf', 'application/octet-stream') or media.startswith('image/')


def packets_from_request(request, *, native_attachments=False):
    """Decode exact logical input without consulting saved packets or local source bytes.

    A descriptor or base64 string inside JSON does not establish a native file
    input. Only matching inline parts on a supported native attachment route can
    restore the content field that request construction moved into that input.
    """
    packets = []
    for message in transport.logical_messages(request):
        if not isinstance(message, dict) or message.get('role') != 'user':
            continue
        content = message.get('content')
        parts = content if isinstance(content, list) else []
        if parts:
            texts = [part['text'] for part in parts if isinstance(part, dict)
                     and part.get('type') == 'input_text' and isinstance(part.get('text'), str)]
            if len(texts) != 1:
                raise ResearchError('review_input_unreadable', 'A native packet has one exact text input')
            content = texts[0]
        if not isinstance(content, str) or not content.startswith(transport.PACKET_LABEL):
            continue
        packet = strict_json(content[len(transport.PACKET_LABEL):].encode('utf-8'))
        _require_problem_packet(packet)
        # A native compaction marker delivers no content at this position.
        # Only an actual later packet can supply the omitted part.
        repeated = {'delivery': 'repeated-in-the-current-packet', 'note': transport.REPEATED_PART_NOTE}
        packet = {key: value for key, value in packet.items() if value != repeated}
        attached = _attachments(parts) if native_attachments else {}
        for entry in packet.get('evidence', []):
            if not isinstance(entry, dict) or not isinstance(entry.get('artifact'), dict):
                raise ResearchError('review_input_unreadable', 'Evidence entries require native artifact descriptors')
            reference = entry['artifact']
            if entry.get('encoding') != 'base64' and not _binary(reference):
                continue
            key = (reference.get('media_type'), reference.get('size'), reference.get('sha256'))
            entry.pop('content', None)
            if entry.get('delivery') == 'attached-exact-bytes' and key in attached:
                entry.update(content=attached[key], encoding='base64')
            else:
                entry['encoding'] = 'omitted'
                entry['delivery'] = 'bytes-absent-from-actual-native-input'
        packets.append(packet)
    return packets


def packets_from_visual_request(request):
    """Restore only PNG bytes explicitly attached to this exact visual invocation."""
    from .review_visual_inputs import project_request
    actual = project_request(request)
    images = {entry['artifact']['sha256']: entry for entry in actual['images']}
    packets = []
    for index, message in enumerate(actual['messages']):
        visible = packets_from_request({'input': [message]})
        allowed = actual['packet_images'].get(index, ())
        for packet in visible:
            for entry in packet.get('evidence', []):
                reference = entry['artifact']
                identifier = reference.get('sha256')
                image = images.get(identifier)
                if (identifier in allowed and image is not None and reference == image['artifact']
                        and reference.get('media_type') == 'image/png'):
                    entry.update(encoding='base64', content=image['content'], delivery='attached-exact-image-bytes')
        packets.extend(visible)
    return packets
