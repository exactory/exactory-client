"""Prospective request measurements checked before a reviewer invocation is recorded."""

import json
import os
from pathlib import Path
import tempfile

from . import review_excerpts, review_transport as transport
from .errors import ResearchError
from .evidence import digest

PROTOCOL = 'exactory-review-preflight-v1'
# Output and duration forecasts need a bounded profile of observed usage; 0.71.0 reports them as unknown
# instead of scanning every earlier invocation for each call (staged-review v3 workload work, 0.72.0).
_ESTIMATE_REASON_MSG = 'No bounded usage profile exists yet; the estimate is not computed.'


def describe_work(role, packet):
    """Count existing work units without inventing a new partition protocol."""
    from . import openness_reading, review_problem_parts
    if packet.get('protocol') == 'exactory-reassessment-disposition-correction-v1':
        return {'type': 'correction', 'unit': 'finding', 'count': len(packet['diagnostic']['findings'])}
    plan = packet.get(review_problem_parts.KEY, {})
    if plan.get('stage') == 'part':
        count = sum(end - start for key, ranges in plan['delivered_now'].items() if key != 'passages'
                    for start, end in ranges)
        return {'type': 'result_part', 'unit': 'required_note', 'count': count}
    turn = packet.get(openness_reading.TURN_KEY)
    if turn is not None:
        return {'type': 'openness_reading', 'unit': 'reading_unit',
                'count': len(turn['reading_plan']['delivered_now'])}
    kind = ('result_final' if plan else 'openness_final' if openness_reading.RECORD_KEY in packet
            else 'probe' if role == 'probe' else 'ordinary')
    if kind == 'ordinary' and role == 'native_math':
        kind = 'native_' + str(packet.get('native', {}).get('kind', 'unknown'))
    return {'type': kind, 'unit': 'response', 'count': 1}


def build_report(records, artifacts, route, role, prompt, packet, request):
    """Measure the exact request in the route's own representation.

    Output and duration are reported as unknown. No report enters the reviewer input, and nothing is
    cached or written.
    """
    prompt_text = prompt['text'] if isinstance(prompt, dict) else prompt
    work = describe_work(role, packet)
    limit = transport.local_input_limit(route)
    native_images = None
    if route['adapter'] == 'codex_cli_visual_v1':
        from . import review_codex_visual_transport as visual, review_codex_transport as codex
        # The pinned adapter creates review-visual- plus tempfile's eight-character random name. Its image
        # wrappers contain that resolved path; only the name's length affects the native text measurement.
        environment = codex.clean_environment(os.environ)
        base = environment.get('TMPDIR')
        directory = Path(tempfile.gettempdir() if base is None else base).resolve() / ('review-visual-' + 'x' * 8)
        try:
            parts = visual.native_user_parts(request, directory)
            characters = sum(len(part['text']) for part in parts if part['type'] == 'input_text')
            native_images = sum(part['type'] == 'input_image' for part in parts)
        except ResearchError as error:
            if error.code != 'review_input_too_large':
                raise
            # The pinned validator raises without returning its parts on overflow. Measure the same wrappers for
            # the diagnostic; check_request still refuses this measured overflow before any invocation is saved.
            native_images = len(visual.validated_images(request))
            characters = len(visual.packet_text(request)) + sum(
                len('<image name=[Image #' + str(index) + '] path="'
                    + str(directory / ('image-' + str(index).zfill(3) + '.png')) + '"></image>')
                for index in range(1, native_images + 1))
        limit = visual.MAX_USER_TEXT
    else:
        adapter = transport._local_adapter(route)
        characters = (len(adapter.packet_text(request)) if adapter is not None
                      else len(json.dumps(request, sort_keys=True, ensure_ascii=False)))
    input_size = {'kind': 'measurement', 'unit': 'characters', 'value': characters, 'limit': limit,
                  'representation': 'native_user_text' if native_images is not None else
                                    'adapter_packet_text' if adapter is not None else 'serialized_request'}
    if native_images is not None:
        input_size['native_images'] = native_images
    estimates = {name: {'kind': 'unknown', 'value': None, 'reason': _ESTIMATE_REASON_MSG}
                 for name in ('output_tokens', 'duration_seconds')}
    required = dict(work)
    if work['type'] == 'openness_reading':
        required.update(unit='response', count=1)
    return dict(protocol=PROTOCOL, input_size=input_size,
        mandatory_final_input={'kind': 'unknown', 'value': None, 'reason': 'No supported final projection supplied.'},
        required_output_units=dict(required, kind='measurement', value=required['count']),
        work_units=dict(work, kind='measurement', value=work['count']),
        remaining_calls={'kind': 'measurement', 'value': 1, 'bound': 'lower_bound', 'additional_calls': 'unknown'},
        remaining_cost_usd={'kind': 'unknown', 'value': None},
        dependency_digest=digest({'route': route, 'prompt': prompt_text, 'role': role, 'work': work,
                                  'packet': digest(packet), 'request': digest(request)}), **estimates)


def build_assignment_report(records, artifacts, assignment, request=None):
    """Report a prospective assignment without changing its saved packet, prompt or request."""
    from . import openness_reading as reading, review_protocol as protocol
    route = records['review_route'][assignment['route_id']]
    packet = protocol._read_json(artifacts, assignment['packet'])
    if request is None:
        request = protocol.build_assignment_request(records, artifacts, assignment)
    report = build_report(records, artifacts, route, assignment['role'], assignment['prompt'], packet, request)
    if (assignment['role'] == 'native_math' and reading.find_stage(packet) is not None
            and assignment.get('context', {}).get('native_packet')):
        # This native branch only loads artifacts. Other build_packet branches can publish derivatives and must
        # never be used for diagnostic reconstruction.
        complete = protocol.build_packet(records, artifacts, None, 'native_math', assignment['reviewer_id'],
                                         native_packet=assignment['context']['native_packet'])
        native = complete['native']
        if reading.is_openness_packet(native) or reading.is_math_openness_packet(native):
            rows = reading.list_reading_rows(records, artifacts, native)
            record = (packet.get(reading.TURN_KEY, {}).get('reading_record') or packet.get(reading.RECORD_KEY)
                      or reading._create_record(reading.find_chain_protocol(native)))
            try:
                floor = reading.preflight_final_input(route, transport.local_input_limit(route), complete, rows, record)
            except ResearchError as error:
                if error.code != 'review_input_too_large':
                    raise
                floor = error.details
            report['mandatory_final_input'] = dict(floor, kind='measurement' if floor['status'] == 'measured'
                                                   else 'unknown', value=floor['input_characters'])
            report['dependency_digest'] = digest([report['dependency_digest'], digest(complete), floor])
    return report


def check_request(report, request, *, packet=None, artifacts=None, correction_packet=None):
    """Refuse exact input overflow or loss of required correction material before recording an invocation."""
    size = report['input_size']
    if size['limit'] is not None and size['value'] > size['limit']:
        raise ResearchError('review_input_too_large', 'The exact reviewer request exceeds the route input limit.',
                            {'phase': 'dispatch_preflight', 'preflight': report})
    floor = report['mandatory_final_input']
    if (floor['kind'] == 'measurement' and floor['input_limit_characters'] is not None
            and floor['value'] > floor['input_limit_characters']):
        raise ResearchError('review_input_too_large', 'The mandatory final input exceeds the route input limit.',
                            dict(floor, preflight=report))
    if packet is not None:
        from .review_adjudicator_sources import validate_request
        try:
            validate_request(artifacts, packet, request)
        except ResearchError as error:
            raise ResearchError('review_input_too_large', 'The exact request cannot deliver required current sources.',
                {'phase': 'dispatch_preflight', 'preflight': report,
                 'delivery_error': {'code': error.code, 'details': error.details}}) from error
    if correction_packet is not None:
        sent = review_excerpts.read_sent_packet(request)
        if sent != correction_packet:
            raise ResearchError('review_input_too_large',
                                'The fitted correction request loses required original output or historical material.',
                                {'phase': 'dispatch_preflight', 'preflight': report})
    return report

