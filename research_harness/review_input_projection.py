"""Project packet content solely from the actual native request representation."""

import base64
import binascii
import hashlib

from . import review_transport as transport
from .errors import ResearchError
from .fulltext import detect_media_type
from .evidence import digest
from .workspace import strict_json


def collect_attachments(parts):
    """Index only actual inline native file and image inputs by the size and SHA-256 of their bytes."""
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
        attached.setdefault((len(data), hashlib.sha256(data).hexdigest()), {})[media] = (encoded, data)
    return attached


def find_attachment(attached, reference):
    """The base64 body of an actual attachment of exactly the descriptor's bytes, attached as the media type that
    request construction attaches for the descriptor: a PDF reported as generic binary is attached as a PDF."""
    reported = reference.get('media_type')
    if not isinstance(reported, str):
        return None
    for media, (encoded, data) in attached.get((reference.get('size'), reference.get('sha256')), {}).items():
        if transport.is_binary_evidence(data, reported):
            expected, _ = detect_media_type(data, reported)
            if media == expected:
                return encoded
    return None


def _has_parts_binding(packet, adapter):
    """Check the same frozen artifact, state and series identities for both roles."""
    from . import review_parts
    from .artifacts import validate_reference

    header = packet.get(review_parts.KEY)
    state = packet.get(review_parts.STATE_KEY)
    if not (isinstance(header, dict) and review_parts.find_kind(packet) is not None
            and header.get('adapter') == adapter
            and {'inventory', 'inventory_digest', 'logical_packet', 'logical_packet_digest',
                 'series_key', 'state_digest', 'receipts', 'units'} <= header.keys()
            and isinstance(header.get('series_assignment_id'), str)
            and bool(header['series_assignment_id'].strip())
            and type(header.get('turn')) is int and header['turn'] >= 1
            and isinstance(header['series_key'], str) and len(header['series_key']) == 64
            and all(character in '0123456789abcdef' for character in header['series_key'])
            and isinstance(header['receipts'], list) and isinstance(header['units'], list)
            and isinstance(state, dict) and header['state_digest'] == digest(state)
            and packet['digest'] == digest({key: value for key, value in packet.items()
                                             if key != 'digest'})):
        return False
    try:
        validate_reference(header['inventory'])
        validate_reference(header['logical_packet'])
    except ResearchError:
        return False
    return (header['inventory_digest'] == header['inventory']['sha256']
            and header['logical_packet_digest'] == header['logical_packet']['sha256'])


def _require_review_packet(packet, assignments=None):
    """A packet label cannot stand in for a native scientific review identity."""
    subject = packet.get('subject') if isinstance(packet, dict) else None
    identifier = packet.get('digest') if isinstance(packet, dict) else None
    valid = (isinstance(packet, dict)
             and isinstance(packet.get('reviewer_id'), str) and bool(packet['reviewer_id'].strip())
             and isinstance(identifier, str) and len(identifier) == 64
             and all(character in '0123456789abcdef' for character in identifier)
             and isinstance(packet.get('evidence', []), list))
    if valid:
        protocol, role = packet.get('protocol'), packet.get('role')
        problem = (protocol in ('exactory-problem-review-v1', 'exactory-problem-review-v2')
                   and 'dossier_id' in packet and packet['dossier_id'] is None
                   and isinstance(subject, dict) and set(subject) == {'kind', 'statement_id'}
                   and subject['kind'] == 'problem'
                   and (isinstance(subject['statement_id'], str) or subject['statement_id'] is None)
                   and (role != 'result' or bool(subject['statement_id'])))
        study = (protocol in ('exactory-independent-review-v1', 'exactory-independent-review-sources-v2')
                 and isinstance(packet.get('dossier_id'), str) and bool(packet['dossier_id'].strip())
                 and 'subject' not in packet)
        valid = (problem or study) and role in ('bar', 'slate', 'result', 'adjudicator')
        if role == 'verification':
            paper = packet.get('paper')
            task = paper.get('task_digest') if isinstance(paper, dict) else None
            valid = (protocol == 'exactory-independent-review-v1'
                     and 'dossier_id' in packet and packet['dossier_id'] is None
                     and 'subject' not in packet and isinstance(paper, dict)
                     and paper.get('kind') == 'verification' and isinstance(task, str)
                     and len(task) == 64 and all(character in '0123456789abcdef' for character in task)
                     and _has_parts_binding(packet, 'study_verification'))
        if role == 'adjudicator':
            assessments, objection = packet.get('assessments'), packet.get('objection')
            valid = (valid and protocol != 'exactory-independent-review-sources-v2'
                     and isinstance(assessments, list) and len(assessments) >= 2
                     and all(isinstance(row, dict) and isinstance(row.get('id'), str) and row['id'].strip()
                             for row in assessments)
                     and len({row['id'] for row in assessments}) == len(assessments)
                     and isinstance(objection, dict) and isinstance(objection.get('id'), str)
                     and bool(objection['id'].strip()))
        if role == 'native_math':
            from . import native_review, review_parts
            native, header = packet.get('native'), packet.get(review_parts.KEY)
            valid = (protocol == 'exactory-independent-review-v1'
                     and 'dossier_id' in packet and packet['dossier_id'] is None and 'subject' not in packet
                     and isinstance(native, dict) and native.get('protocol') == native_review.PROTOCOL
                     and isinstance(native.get('kind'), str) and native['kind'] in native_review.KINDS
                     and isinstance(native.get('subject'), dict)
                     and native.get('subject_digest') == digest(native['subject'])
                     and isinstance(native.get('reviewer'), dict)
                     and _has_parts_binding(packet, 'native_math'))
            if valid:
                try:
                    native_review.require_assignment_identity(native, dict(id=header['series_assignment_id'],
                        reviewer_id=packet['reviewer_id'], dossier_id=None))
                except ResearchError:
                    valid = False
    if valid and assignments is not None:
        from .review_context_repair import is_same_subject
        bound = [assignment for assignment in assignments
                 if packet['digest'] == assignment.get('packet_digest')
                 and all(packet.get(key) == assignment.get(key)
                         for key in ('role', 'reviewer_id', 'dossier_id'))
                 and (assignment.get('subject') is None or isinstance(assignment['subject'], dict))
                 and is_same_subject(packet, assignment)]
        if packet['role'] == 'adjudicator':
            bound = [assignment for assignment in bound
                     if packet['objection']['id'] == assignment.get('context', {}).get('objection_id')
                     and [row['id'] for row in packet['assessments'][:2]]
                     == assignment.get('context', {}).get('review_ids')]
        elif packet['role'] in ('native_math', 'verification'):
            bound = [assignment for assignment in bound
                     if isinstance(assignment.get('review_parts'), dict)
                     and all(packet['review_parts'].get(key) == assignment['review_parts'].get(key)
                             for key in ('protocol', 'adapter', 'kind', 'series_assignment_id', 'turn',
                                         'inventory_digest'))]
        if packet['role'] == 'verification':
            bound = [assignment for assignment in bound
                     if assignment.get('context', {}).get('verification_task_id')
                     == packet['paper']['task_digest']]
        valid = bool(bound)
    if not valid:
        raise ResearchError('review_input_unreadable', 'Use a native review packet with its exact bound identity')


def project_evidence(packet, attached):
    """Retain exact text or native attachments using construction's reported-type and byte decisions."""
    for entry in packet.get('evidence', []):
        if not isinstance(entry, dict) or not isinstance(entry.get('artifact'), dict):
            raise ResearchError('review_input_unreadable', 'Evidence entries require native artifact descriptors')
        reference = entry['artifact']
        media = reference.get('media_type')
        if not isinstance(media, str):
            raise ResearchError('review_input_unreadable', 'Evidence requires an explicit native media type')
        content = entry.get('content')
        if entry.get('delivery') == transport.RESULT_BYTES_DELIVERY and entry.get('encoding') == 'base64':
            try:
                data = base64.b64decode(content, validate=True)
            except (binascii.Error, TypeError, ValueError):
                pass
            else:
                if (len(data) == reference.get('size')
                        and hashlib.sha256(data).hexdigest() == reference.get('sha256')
                        and not transport.is_binary_evidence(data, media)):
                    continue
        if (entry.get('encoding') == 'omitted' and content == ''
                and entry.get('delivery') != 'bytes-omitted-on-text-route'):
            continue
        if entry.get('encoding') == 'utf-8' and isinstance(content, str):
            data = content.encode('utf-8')
            exact = (len(data) == reference.get('size')
                     and hashlib.sha256(data).hexdigest() == reference.get('sha256'))
            if not transport.is_binary_evidence(data, media) and (exact or not content):
                # Empty bodies retain existing omission/containment markers without supplying bytes.
                continue
        elif entry.get('encoding') != 'base64' and not transport.is_binary_evidence(b'', media):
            if entry.get('delivery') != 'bytes-omitted-on-text-route':
                continue
        entry.pop('content', None)
        content = find_attachment(attached, reference) if entry.get('delivery') == 'attached-exact-bytes' else None
        if content is not None:
            entry.update(content=content, encoding='base64')
        else:
            entry['encoding'] = 'omitted'
            entry['delivery'] = 'bytes-absent-from-actual-native-input'


def packets_from_request(request, *, native_attachments=False, assignments=None):
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
        if set(packet) == {'delivery', 'packet_digest', 'history_delivery', 'note'} and packet['delivery'] == 'packet-elided-route-limit':
            # An earlier turn that the route limit reduced to its packet digest delivers nothing at this position.
            continue
        _require_review_packet(packet, assignments)
        # A native compaction marker delivers no content at this position.
        # Only an actual later packet can supply the omitted part.
        repeated = {'delivery': 'repeated-in-the-current-packet', 'note': transport.REPEATED_PART_NOTE}
        packet = {key: value for key, value in packet.items() if value != repeated}
        attached = collect_attachments(parts) if native_attachments else {}
        project_evidence(packet, attached)
        packets.append(packet)
    return packets


def packets_from_visual_request(request, *, assignments=None):
    """Restore only PNG bytes explicitly attached to this exact visual invocation."""
    from .review_visual_inputs import project_request
    actual = project_request(request)
    images = {entry['artifact']['sha256']: entry for entry in actual['images']}
    packets = []
    for index, message in enumerate(actual['messages']):
        visible = packets_from_request({'input': [message]}, assignments=assignments)
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
