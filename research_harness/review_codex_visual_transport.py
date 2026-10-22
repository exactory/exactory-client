"""Isolated Codex reviews with exact, observed PNG inputs and visible text."""

import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import uuid
import zlib

from . import review_codex_transport as text_route
from . import review_exact_text, review_exact_text_v2, review_visual_inputs, review_pdf_pages
from .errors import ResearchError
from .evidence import digest

ADAPTER = 'codex_cli_visual_v1'
INPUT_CONTRACT = 'observed-exec-rollout-visual-v1'
MAX_USER_TEXT = 1048576


def runtime():
    result = text_route.runtime()
    modules = (Path(__file__), Path(text_route.__file__), Path(review_exact_text.__file__),
               Path(review_exact_text_v2.__file__), Path(review_visual_inputs.__file__), Path(review_pdf_pages.__file__))
    result.update(adapter=ADAPTER, input_contract=INPUT_CONTRACT,
                  implementation_sha256=digest({path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                                for path in modules}))
    return result


def validate_route(route):
    if route['adapter'] != ADAPTER or route['endpoint'] != 'codex://local':
        raise ResearchError('invalid_review_route', 'Use codex_cli_visual_v1 at codex://local')
    text_route.validate_route(dict(route, adapter=text_route.ADAPTER))


def packet_text(request):
    messages = request.get('input')
    if not isinstance(messages, list) or not messages or any(
            not isinstance(row, dict) or set(row) != {'role', 'content'}
            or row['role'] not in ('user', 'assistant') or not isinstance(row['content'], str)
            for row in messages):
        raise ResearchError('review_format_unsupported', 'Supply exact text messages and separate native image inputs')
    for row in messages:
        value = row['content']
        if value.startswith('Exact review JSON and visible text'):
            if len(messages) != 1 or row['role'] != 'user':
                raise ResearchError('review_format_unsupported', 'The exact envelope is one complete user input')
            codec = next((module for module in (review_exact_text, review_exact_text_v2)
                          if module.is_encoded(value)), None)
            if codec is None:
                raise ResearchError('review_format_unsupported', 'The exact visible codec is not supported')
            decoded = codec.decode(value)
            if not isinstance(decoded, list) or not decoded:
                raise ResearchError('review_format_unsupported', 'The exact envelope must contain logical messages')
            return value
    return json.dumps({'review_messages': messages}, sort_keys=True, ensure_ascii=False)


def _validate_png(data):
    """Validate the bounded, noninterlaced eight-bit PNG profile used by this route."""
    if not data.startswith(b'\x89PNG\r\n\x1a\n') or len(data) > 33554432:
        raise ValueError('An image must be a PNG within the 32 MiB route bound')
    offset, chunks, compressed, dimensions = 8, [], bytearray(), None
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError('Truncated PNG chunk')
        size = struct.unpack('>I', data[offset:offset + 4])[0]
        end = offset + 12 + size
        if end > len(data):
            raise ValueError('Truncated PNG body')
        kind = data[offset + 4:offset + 8]
        content = data[offset + 8:offset + 8 + size]
        crc = struct.unpack('>I', data[offset + 8 + size:end])[0]
        if zlib.crc32(kind + content) != crc:
            raise ValueError('PNG checksum mismatch')
        chunks.append(kind)
        if kind == b'IHDR':
            if len(chunks) != 1 or size != 13:
                raise ValueError('Invalid PNG header')
            width, height, bits, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', content)
            channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color)
            if (not 0 < width * height <= 16777216 or not channels or bits != 8
                    or compression != 0 or filtering != 0 or interlace != 0):
                raise ValueError('Unsupported PNG dimensions or sample profile')
            dimensions = (width, height, channels)
        elif kind == b'IDAT':
            compressed.extend(content)
        elif kind == b'IEND':
            if size or end != len(data):
                raise ValueError('Invalid PNG end')
        elif kind[:1].isupper() and kind != b'PLTE':
            raise ValueError('Unknown critical PNG chunk')
        offset = end
    if dimensions is None or not compressed or chunks[-1:] != [b'IEND']:
        raise ValueError('Incomplete PNG')
    width, height, channels = dimensions
    stride = 1 + width * channels
    decoder = zlib.decompressobj()
    pixels = decoder.decompress(bytes(compressed), stride * height + 1)
    if (len(pixels) != stride * height or not decoder.eof or decoder.unused_data
            or decoder.unconsumed_tail or any(pixels[index] > 4 for index in range(0, len(pixels), stride))):
        raise ValueError('Invalid PNG image data')


def validated_images(request):
    rows = request.get('exactory_image_inputs')
    if not isinstance(rows, list) or not rows:
        raise ResearchError('review_image_input_invalid', 'A visual route requires exact ordered PNG inputs')
    result = []
    try:
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'artifact', 'data_url'}:
                raise ValueError('Invalid image input fields')
            reference, url = row['artifact'], row['data_url']
            if (not isinstance(reference, dict) or set(reference) != {'path', 'sha256', 'size', 'media_type'}
                    or not isinstance(reference['path'], str) or not reference['path']
                    or reference['media_type'] != 'image/png' or type(reference['size']) is not int
                    or not isinstance(url, str) or not url.startswith('data:image/png;base64,')):
                raise ValueError('Invalid PNG descriptor')
            data = base64.b64decode(url[len('data:image/png;base64,'):], validate=True)
            if len(data) != reference['size'] or hashlib.sha256(data).hexdigest() != reference['sha256']:
                raise ValueError('Image bytes disagree with their descriptor')
            _validate_png(data)
            result.append((reference, data, url))
    except (ValueError, TypeError, KeyError, binascii.Error, struct.error, zlib.error) as error:
        raise ResearchError('review_image_input_invalid', str(error)) from error
    return result


def native_user_parts(request, directory):
    root = Path(directory).resolve()
    parts = []
    for index, (_, _, url) in enumerate(validated_images(request), 1):
        path = root / ('image-' + str(index).zfill(3) + '.png')
        parts.extend([{'type': 'input_text', 'text': f'<image name=[Image #{index}] path="{path}">'},
                      {'type': 'input_image', 'image_url': url, 'detail': 'high'},
                      {'type': 'input_text', 'text': '</image>'}])
    parts.append({'type': 'input_text', 'text': packet_text(request)})
    if sum(len(part['text']) for part in parts if part['type'] == 'input_text') > MAX_USER_TEXT:
        raise ResearchError('review_input_limit', 'Complete native user text and image wrappers exceed the route limit')
    return parts


def inspect_rollout(events, request, thread_id, directory, excluded_controls, excluded_images=()):
    """Authenticate exact actual native parts, model, effort and isolated context."""
    expected = native_user_parts(request, directory)
    excluded_image_values = [base64.b64encode(data).decode('ascii') for data in excluded_images]
    meta = [event['payload'] for event in events if event.get('type') == 'session_meta']
    contexts = [event['payload'] for event in events if event.get('type') == 'turn_context']
    issues, users, developers, environments, outputs = [], [], [], [], []
    seen = False
    if (len(meta) != 1 or meta[0].get('id') != thread_id or meta[0].get('source') != 'exec'
            or meta[0].get('base_instructions', {}).get('text') != request['instructions']
            or meta[0].get('forked_from_id') or meta[0].get('parent_thread_id')):
        issues.append('session_identity_or_instructions')
    if (len(contexts) != 1 or contexts[0].get('model') != request['model']
            or contexts[0].get('effort') != 'high'
            or contexts[0].get('cwd') != str(Path(directory).resolve())):
        issues.append('actual_model_effort_or_turn_context')
    for event in events:
        kind, item = event.get('type'), event.get('payload', {})
        if kind == 'compacted':
            issues.append('uninspected_compaction')
        if kind != 'response_item':
            continue
        serialized = json.dumps(item)
        if any(control in serialized for control in excluded_controls):
            issues.append('excluded_control_exposed')
        if any(value in serialized for value in excluded_image_values):
            issues.append('excluded_image_exposed')
        if item.get('type') not in ('message', 'reasoning'):
            issues.append('unknown_or_tool_input')
            continue
        if item.get('type') == 'reasoning':
            if not seen:
                issues.append('inherited_reasoning')
            continue
        role, parts = item.get('role'), item.get('content')
        if role == 'user' and parts == expected:
            users.append(parts)
            seen = True
            continue
        try:
            text = text_route._message_text(item)
        except ValueError:
            issues.append('unknown_or_mismatched_message_content')
            continue
        if role == 'developer':
            if seen:
                issues.append('late_developer_input')
            developers.append(text_route._native_digest(text, directory))
        elif role == 'assistant':
            if not seen:
                issues.append('inherited_assistant_history')
            outputs.append(text)
        elif role == 'user':
            if text.startswith('<environment_context>') and text.endswith('</environment_context>') and not seen:
                environments.append(text_route._native_digest(text, directory, environment=True))
                if environments[-1] not in text_route.NATIVE_ENVIRONMENTS:
                    issues.append('unexpected_environment')
            else:
                users.append(parts)
                issues.append('unexpected_user_input')
        else:
            issues.append('unknown_input_role')
    if tuple(developers) not in text_route.NATIVE_DECLARATION_SETS:
        issues.append('unknown_developer_input')
    if len(environments) > 1:
        issues.append('repeated_environment_input')
    if users != [expected]:
        issues.append('packet_images_or_history_mismatch')
    return {'input_verified': not issues, 'issues': sorted(set(issues)),
            'request_digest': digest(request), 'packet_digest': digest(expected),
            'rollout_digest': digest(events), 'thread_id': thread_id,
            'developer_digests': developers, 'environment_digests': environments,
            'excluded_controls': list(excluded_controls),
            'excluded_image_digests': [hashlib.sha256(data).hexdigest() for data in excluded_images],
            'assistant_output_digest': digest(outputs),
            'observed_events': len(events), 'input_contract': INPUT_CONTRACT,
            'observed_model': contexts[0].get('model') if len(contexts) == 1 else None,
            'observed_effort': contexts[0].get('effort') if len(contexts) == 1 else None,
            'image_digests': [reference['sha256'] for reference, _, _ in validated_images(request)]}


def send_request(route, request, credential=None):
    validate_route(route)
    if request.get('model') != route['model']:
        raise ResearchError('review_model_mismatch', 'The request must name the exact route model')
    images = validated_images(request)
    packet = packet_text(request)
    environment = text_route.clean_environment(os.environ)
    actual_runtime = runtime()
    control = 'EXCLUDED_PROJECT_' + uuid.uuid4().hex
    image_control = uuid.uuid4().hex
    excluded_image = review_visual_inputs.control_image(image_control)
    with tempfile.TemporaryDirectory(prefix='review-visual-', dir=environment.get('TMPDIR')) as directory:
        root = Path(directory).resolve()
        directory = str(root)
        native_user_parts(request, directory)
        (root / 'AGENTS.md').write_text('This excluded project instruction contains ' + control + '.\n')
        (root / 'excluded-image.png').write_bytes(excluded_image)
        instructions = root / 'review-instructions.txt'
        instructions.write_text(request['instructions'])
        command = [actual_runtime['executable'], 'exec', '--ignore-user-config', '--ignore-rules',
                   '--skip-git-repo-check', '--json', '--color', 'never', '-s', 'read-only', '-m', route['model']]
        for value in text_route.CONTROLS + ('model_instructions_file=' + json.dumps(str(instructions)),):
            command.extend(('-c', value))
        for index, (_, data, _) in enumerate(images, 1):
            path = root / ('image-' + str(index).zfill(3) + '.png')
            path.write_bytes(data)
            command.extend(('--image', str(path)))
        command.extend(('--', '-'))
        result = text_route._run_owned(command, packet=packet, directory=directory, environment=environment,
                                      timeout=route['configuration']['timeout_seconds'])
        event_log, malformed = [], []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError('Event is not an object')
                event_log.append(event)
            except ValueError:
                malformed.append(hashlib.sha256(line.encode()).hexdigest())
        threads = [event['thread_id'] for event in event_log if event.get('type') == 'thread.started']
        thread_id = threads[0] if len(threads) == 1 else None
        messages = [event['item']['text'] for event in event_log if event.get('type') == 'item.completed'
                    and event.get('item', {}).get('type') == 'agent_message']
        usages = [event['usage'] for event in event_log if event.get('type') == 'turn.completed']
        usage = usages[-1] if usages else {}
        evidence = {'input_verified': False, 'issues': ['input_record_unavailable']}
        if thread_id:
            try:
                evidence = inspect_rollout(text_route._rollout(thread_id, environment), request,
                                           thread_id, directory, [control, image_control], [excluded_image])
            except (OSError, ValueError, ResearchError):
                pass
        evidence.update(runtime=actual_runtime, route_digest=digest(route), event_digest=digest(event_log),
                        request_digest=digest(request), inspected=True)
        if evidence.get('assistant_output_digest') != digest(messages):
            evidence['input_verified'] = False
            evidence['issues'].append('provider_output_mismatch')
        complete = (result.returncode == 0 and not malformed and len(messages) == 1 and len(usages) == 1
                    and usage.get('output_tokens', float('inf')) <= route['configuration']['max_output_tokens'])
        return {'id': thread_id or 'failed-' + uuid.uuid4().hex, 'model': route['model'],
                'status': 'completed' if complete else 'incomplete',
                'output': [{'type': 'message', 'role': 'assistant', 'content': [
                    {'type': 'output_text', 'text': messages[-1] if messages else ''}]}],
                'usage': usage, 'context_evidence': evidence,
                'execution': {'returncode': result.returncode, 'timeout': result.returncode is None,
                    'stdout_digest': hashlib.sha256(result.stdout.encode()).hexdigest(),
                    'malformed_line_digests': malformed, 'event_count': len(event_log),
                    'event_types': [event.get('type') for event in event_log]}}


def verify_context(route, request, response):
    evidence = response.get('context_evidence', {})
    return (evidence.get('inspected') is True and evidence.get('input_verified') is True
            and evidence.get('request_digest') == digest(request) and evidence.get('route_digest') == digest(route)
            and evidence.get('input_contract') == INPUT_CONTRACT
            and evidence.get('observed_model') == route['model'] == request.get('model')
            and evidence.get('observed_effort') == 'high'
            and evidence.get('image_digests') == [row['artifact']['sha256'] for row in request.get('exactory_image_inputs', [])]
            and len(evidence.get('excluded_image_digests', [])) == 1
            and evidence['excluded_image_digests'][0] not in evidence.get('image_digests', [])
            and bool(evidence.get('rollout_digest')) and bool(evidence.get('event_digest'))
            and evidence.get('thread_id') == response.get('id') and evidence.get('runtime') == runtime())
