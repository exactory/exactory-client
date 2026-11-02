"""Prospective admission checks the exact request before any invocation exists, without changing inputs."""

import copy
import importlib.util
import json
import unittest
from unittest.mock import patch

from codex_route_fixtures import build_codex_response, invoke_codex, record_codex_route
from openness_reading_fixtures import OpennessReadingCase
from research_harness import review_protocol as protocol, review_transport as transport
from research_harness import review_output_correction as correction
from research_harness.errors import ResearchError
from research_harness.evidence import digest


def load_preflight(case):
    case.assertIsNotNone(importlib.util.find_spec('research_harness.review_preflight'),
                         'Operational preflight reports are missing')
    from research_harness import review_preflight
    return review_preflight


def inject_usage(output_tokens=None, wall_seconds=None):
    observe = transport.observe
    def observed(*args):
        result = observe(*args)
        if output_tokens is not None:
            result['usage']['output_tokens'] = output_tokens
            result['response']['usage']['output_tokens'] = output_tokens
        if wall_seconds is not None:
            result['usage']['wall_seconds'] = wall_seconds
        return result
    def respond(route, request, credential):
        response = build_codex_response(route, request, respond.output)
        if output_tokens is not None:
            response['usage']['output_tokens'] = output_tokens
        return response
    return observed, respond


class DispatchTests(unittest.TestCase):
    def build_case(self):
        from test_research_review_protocol import ReviewProtocolTests
        case = ReviewProtocolTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        record_codex_route(case)
        return case

    def test_oversized_ordinary_leaves_no_invocation(self):
        case = self.build_case()
        assignment = case.assign()['result']
        before = case.store.snapshot()
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 10), \
                patch.object(transport, 'send_request', return_value=case.response({'ok': True})):
            with self.assertRaises(ResearchError) as caught:
                case.invoke({'ok': True})
        self.assertEqual(caught.exception.code, 'review_input_too_large')
        self.assertEqual(case.store.snapshot(), before)
        self.assertNotIn('attempt', case.records().get('review_invocation', {}))
        # Prospective admission must not enter historical reconstruction.
        self.assertIsInstance(protocol.build_assignment_request(case.records(), case.artifacts, assignment), dict)

    def test_probe_checks_exact_request_before_creating_invocation(self):
        case = self.build_case()
        before = case.store.snapshot()
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 10), \
                patch.object(transport, 'send_request', return_value=case.response({})):
            with self.assertRaises(ResearchError) as caught:
                case.mutate(protocol.probe_route, {'id': 'too-large', 'route_id': 'route'}, credential='test')
        self.assertEqual(caught.exception.code, 'review_input_too_large')
        self.assertEqual(case.store.snapshot(), before)

    def test_dispatch_report_measures_the_request_and_leaves_estimates_unknown(self):
        case = self.build_case()
        case.assign(identifier='earlier')
        observed, respond = inject_usage(output_tokens=4000, wall_seconds=20)
        respond.output = {'ok': True}
        with patch.object(transport, 'observe', side_effect=observed), \
                patch.object(transport, 'send_request', side_effect=respond):
            invoke_codex(case, 'earlier', {'ok': True}, 'earlier-run')
        case.assign(identifier='ordinary')
        result = invoke_codex(case, 'ordinary', {'ok': True})
        report = result.get('preflight')
        self.assertIsNotNone(report, 'Dispatch results must carry their report')
        self.assertEqual(report['input_size']['kind'], 'measurement')
        self.assertLessEqual(report['input_size']['value'], report['input_size']['limit'])
        for name in ('output_tokens', 'duration_seconds'):
            with self.subTest(estimate=name):
                self.assertEqual(report[name]['kind'], 'unknown')
                self.assertIsNone(report[name]['value'])
                self.assertNotIn('observations', report[name])
        stored = case.records()['review_attempt']['attempt-ordinary']['preflight']
        self.assertEqual(stored, report, 'The stored report stays bounded: no list of earlier observations')

    def test_changed_dependency_invalidates_report_and_does_not_mutate_saved_inputs(self):
        case = self.build_case()
        assignment = case.assign()['result']
        preflight = load_preflight(self)
        records = case.records()
        before = copy.deepcopy(records)
        request = protocol.build_assignment_request(records, case.artifacts, assignment)
        saved = case.artifacts.read(assignment['packet'])
        report = preflight.build_assignment_report(records, case.artifacts, assignment, request)
        self.assertEqual(records, before)
        self.assertEqual(case.artifacts.read(assignment['packet']), saved)
        self.assertEqual(protocol.build_assignment_request(records, case.artifacts, assignment), request)
        changed = copy.deepcopy(records)
        changed['review_route']['route']['configuration']['timeout_seconds'] = 1
        changed_report = preflight.build_assignment_report(changed, case.artifacts, assignment, request)
        self.assertNotEqual(report['dependency_digest'], changed_report['dependency_digest'])
        case.seed('review_route', 'route', changed['review_route']['route'])
        with self.assertRaises(ResearchError) as caught:
            case.invoke({'ok': True})
        self.assertEqual(caught.exception.code, 'review_route_changed')
        self.assertFalse(case.records().get('review_invocation'))

    def test_missing_mandatory_current_body_refuses_before_dispatch(self):
        from test_research_adjudicator_current_sources import AdjudicatorCurrentSourceTests
        case = AdjudicatorCurrentSourceTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        assignment, packet = case.prepare_current_comparator()
        build = transport.request_body
        def omit_body(route, prompt, packet, history=(), *args, **kwargs):
            request = build(route, prompt, packet, history, *args, **kwargs)
            if 'current_source_delivery' in packet:
                messages = transport.logical_messages(request)
                sent = json.loads(messages[-1]['content'][len(transport.PACKET_LABEL):])
                entry = next(row for row in sent['evidence'] if row['artifact'] == case.new_source)
                entry.update(content='', encoding='omitted')
                messages[-1]['content'] = transport.PACKET_LABEL + json.dumps(sent)
                request['input'] = messages
            return request
        with patch.object(transport, 'request_body', side_effect=omit_body), \
                patch.object(transport, 'send_request', return_value=case.build_provider_response({})):
            with self.assertRaises(ResearchError) as caught:
                case.mutate(protocol.invoke_assignment, {'id': 'missing-body', 'assignment_id': assignment['id']},
                            credential='test')
        self.assertEqual(caught.exception.code, 'review_input_too_large')
        self.assertNotIn('missing-body', case.read_records().get('review_invocation', {}))

    def test_refused_adjudicator_anchor_can_be_explicitly_upgraded(self):
        from test_research_problem_adjudication_contract import ProblemAdjudicationContractTests
        case = ProblemAdjudicationContractTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        original = case.make_objection
        case.make_objection = lambda: dict(original(), reason='Unsupported implication. ' + 'x' * 1100000)
        case.prepare_reviews(result_reviewer='first')
        record_codex_route(case, 'local-route', output_tokens=128000, timeout_seconds=600)
        payload = dict(id='anchor', role='adjudicator', reviewer_id='focused-third', author_id='problem-author',
            dossier_id=None, route_id='local-route', context=dict(review_ids=[
                'review-result-first', 'review-result-second'], objection_id='unsupported-implication'))
        anchor = case.mutate(protocol.record_assignment, payload)['result']
        with patch.object(transport, 'send_request', return_value=case.build_provider_response({})):
            with self.assertRaises(ResearchError) as caught:
                case.mutate(protocol.invoke_assignment, {'id': 'blocked', 'assignment_id': anchor['id']}, credential='test')
        self.assertEqual(caught.exception.code, 'review_input_too_large')
        self.assertNotIn('blocked', case.read_records().get('review_invocation', {}))
        successor = case.mutate(protocol.record_assignment, dict(payload, id='upgrade',
            context={'staged_upgrade': {'assignment_id': anchor['id']}}))['result']
        self.assertEqual(successor['staged_review']['kind'], 'controller')


class CorrectionAdmissionTests(unittest.TestCase):
    def setUp(self):
        from test_review_output_correction import CorrectionTests
        self.case = CorrectionTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.prepare_refused()
        self.fixture = self.case.fixture

    def test_oversized_correction_leaves_no_invocation_and_can_be_repaired(self):
        # Model the exact adapter measurement without changing the authenticated original route.
        real_limit = transport.local_input_limit
        with patch.object(transport, 'local_input_limit', side_effect=lambda route: 10), \
                patch.object(transport, 'send_request', return_value=self.fixture.build_provider_response(self.case.delta())):
            with self.assertRaises(ResearchError) as caught:
                self.fixture.mutate(correction.invoke_correction, {'id': 'oversized',
                    'assignment_id': self.case.case.assignment['id'], 'route_id': 'route'}, credential='test')
        self.assertEqual(caught.exception.code, 'review_input_too_large')
        self.assertFalse(self.fixture.read_records().get('review_output_correction_invocation'))
        with patch.object(transport, 'local_input_limit', side_effect=real_limit):
            self.assertEqual(self.case.correct(self.case.delta(), 'repaired')['correction_status'], 'accepted')

    def test_fitted_correction_must_preserve_original_output_and_history(self):
        build = transport.request_body
        def lose_material(route, prompt, packet, *args, **kwargs):
            request = build(route, prompt, packet, *args, **kwargs)
            if prompt == correction.PROMPT:
                sent = copy.deepcopy(packet)
                sent['historical_reviews'] = []
                sent['original_output'] = {}
                request['input'] = [{'role': 'user', 'content': transport.PACKET_LABEL + json.dumps(sent)}]
            return request
        with patch.object(transport, 'request_body', side_effect=lose_material), \
                patch.object(transport, 'send_request', return_value=self.fixture.build_provider_response(self.case.delta())):
            with self.assertRaises(ResearchError) as caught:
                self.fixture.mutate(correction.invoke_correction, {'id': 'lost-content',
                    'assignment_id': self.case.case.assignment['id'], 'route_id': 'route'}, credential='test')
        self.assertEqual(caught.exception.code, 'review_input_too_large')
        self.assertFalse(self.fixture.read_records().get('review_output_correction_invocation'))


class OpennessPlanningTests(OpennessReadingCase):
    text_sizes = (35000,) * 6

    def test_report_includes_supported_final_floor_without_writing_or_changing_inputs(self):
        import hashlib
        self.limit_input(150000)
        native = self.export()
        assignment = self.assign('turn-1', native)
        records = self.store.snapshot()['records']
        before = copy.deepcopy(records)
        request = protocol.build_assignment_request(records, self.artifacts, assignment)
        def read_files():
            return {str(path.relative_to(self.root)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in (self.root / 'research' / 'sources').rglob('*') if path.is_file()}
        files = read_files()
        report = load_preflight(self).build_assignment_report(records, self.artifacts, assignment, request)
        self.assertEqual(report['mandatory_final_input']['kind'], 'measurement')
        self.assertLess(report['mandatory_final_input']['value'], 150000)
        self.assertEqual(records, before)
        self.assertEqual(read_files(), files)
        self.assertEqual(protocol.build_assignment_request(records, self.artifacts, assignment), request)
        self.assertEqual(report['output_tokens']['kind'], 'unknown')
        self.assertEqual(report['duration_seconds']['kind'], 'unknown')


class VisualMeasurementTests(unittest.TestCase):
    def test_native_visual_wrappers_count_toward_the_actual_limit(self):
        import base64
        import hashlib
        from research_harness import review_codex_visual_transport as visual
        from test_research_codex_visual_transport import png
        preflight = load_preflight(self)
        data = png(90)
        route = {'id': 'visual', 'adapter': visual.ADAPTER, 'model': 'fixture-model',
                 'configuration': {'max_output_tokens': 1000, 'timeout_seconds': 5}, 'runtime': {}}
        packet = {'evidence': []}
        reference = {'path': 'page.png', 'sha256': hashlib.sha256(data).hexdigest(),
                     'size': len(data), 'media_type': 'image/png'}
        request = {'instructions': 'Review', 'input': [{'role': 'user',
                   'content': transport.PACKET_LABEL + json.dumps(packet)}],
                   'exactory_image_inputs': [{'artifact': reference,
                       'data_url': 'data:image/png;base64,' + base64.b64encode(data).decode()}]}
        characters = len(visual.packet_text(request))
        report = preflight.build_report({}, None, route, 'standalone', 'Review', packet, request)
        self.assertGreater(report['input_size']['value'], characters)
        self.assertEqual(report['input_size']['native_images'], 1)
        with patch.object(visual, 'MAX_USER_TEXT', characters + 1):
            try:
                report = preflight.build_report({}, None, route, 'standalone', 'Review', packet, request)
            except ResearchError:
                self.fail('An advisory report must measure oversized native text before dispatch checks refuse it')
            self.assertGreater(report['input_size']['value'], report['input_size']['limit'])
            with self.assertRaises(ResearchError) as caught:
                preflight.check_request(report, request)
        self.assertEqual(caught.exception.code, 'review_input_too_large')
        request['exactory_image_inputs'][0]['artifact']['sha256'] = '0' * 64
        with self.assertRaises(ResearchError) as caught:
            preflight.build_report({}, None, route, 'standalone', 'Review', packet, request)
        self.assertEqual(caught.exception.code, 'review_image_input_invalid')


class HistoricalAdmissionTests(unittest.TestCase):
    def test_historical_final_prompt_keeps_a_sound_native_input_floor(self):
        from research_harness import openness_reading
        case = OpennessReadingCase()
        case.setUp()
        self.addCleanup(case.doCleanups)
        native = case.export()
        assignment = copy.deepcopy(case.assign('turn-1', native))
        packet = case.read_packet('turn-1')
        packet[openness_reading.RECORD_KEY] = openness_reading._create_record(openness_reading.PROTOCOL)
        packet.pop('digest')
        packet['digest'] = digest(packet)
        old_prompt = protocol.canonical_prompt('native_math', reading_stage='final',
                                               reading_protocol=openness_reading.PROTOCOL, common_version=1)
        assignment.update(packet=case.artifacts.put(json.dumps(packet).encode(), 'application/json'),
                          packet_digest=packet['digest'], prompt=old_prompt,
                          prompt_hash=old_prompt['rendered_prompt_hash'])
        records = case.store.snapshot()['records']
        request = transport.request_body(records['review_route']['route'], old_prompt['text'], packet)
        preflight = load_preflight(self)
        report = preflight.build_assignment_report(records, case.artifacts, assignment, request)
        self.assertEqual(report['mandatory_final_input']['kind'], 'measurement')
        self.assertLessEqual(report['mandatory_final_input']['value'],
                             openness_reading._measure(records['review_route']['route'], request))
        preflight.check_request(report, request, packet=packet, artifacts=case.artifacts)

    def test_single_turn_native_review_has_no_hypothetical_chain_floor(self):
        from research_harness import openness_reading
        case = OpennessReadingCase()
        case.setUp()
        self.addCleanup(case.doCleanups)
        native = case.export()
        assignment = case.assign('turn-1', native)
        self.assertIsNone(openness_reading.find_stage(case.read_packet('turn-1')))
        report = load_preflight(self).build_assignment_report(case.store.snapshot()['records'], case.artifacts,
                                                             assignment)
        self.assertEqual(report['mandatory_final_input']['kind'], 'unknown',
                         'A single-turn review does not owe a reading-chain final request')

    def test_old_assigned_reading_turn_refuses_a_measured_impossible_final_before_dispatch(self):
        from test_openness_final_preflight import FinalPreflightCase, build_before_preflight
        from research_harness import openness_reading
        case = FinalPreflightCase()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.limit_input(case.input_limit)
        native, _ = case.build_native(proof_characters=200000)
        with patch.object(openness_reading, 'prepare_packet', side_effect=build_before_preflight):
            assignment = case.assign('turn-1', native)
        request = protocol.build_assignment_request(case.store.snapshot()['records'], case.artifacts, assignment)
        self.assertLess(openness_reading._measure(case.store.snapshot()['records']['review_route']['route'], request),
                        case.input_limit)
        with self.assertRaises(ResearchError) as caught:
            case.invoke('turn-1', case.build_reading_response(case.read_packet('turn-1')))
        self.assertEqual(caught.exception.code, 'review_input_too_large')
        self.assertEqual(caught.exception.details['phase'], 'final_preflight')
        records = case.store.snapshot()['records']
        self.assertFalse(records.get('review_invocation'))
        self.assertEqual(protocol.build_assignment_request(records, case.artifacts, assignment), request)


class WorkTypeTests(unittest.TestCase):
    def test_reading_response_count_is_distinct_from_selected_reading_units(self):
        preflight = load_preflight(self)
        route = {'id': 'route', 'adapter': transport.ADAPTER, 'model': 'fixture-model', 'runtime': {},
                 'endpoint': 'https://api.openai.com/v1/responses',
                 'configuration': {'max_output_tokens': 1000, 'timeout_seconds': 5}}
        packet = {'reading_turn': {'reading_plan': {'delivered_now': [{}, {}]}}}
        request = transport.request_body(route, 'Reading prompt', packet)
        report = preflight.build_report({}, None, route, 'native_math', 'Reading prompt', packet, request)
        self.assertEqual(report['required_output_units']['value'], 1,
                         'The reading contract requires one aggregate response, not a note for every unit')
        self.assertEqual(report['required_output_units']['unit'], 'response')
        self.assertEqual(report['work_units']['value'], 2)
        self.assertEqual(report['work_units']['unit'], 'reading_unit')

    def test_native_proposals_and_results_with_the_same_prompt_have_distinct_work_types(self):
        preflight = load_preflight(self)
        route = {'id': 'route', 'adapter': transport.ADAPTER, 'model': 'fixture-model', 'runtime': {},
                 'endpoint': 'https://api.openai.com/v1/responses',
                 'configuration': {'max_output_tokens': 1000, 'timeout_seconds': 5}}
        types = []
        for kind in ('proposal', 'result', 'recovery'):
            packet = {'native': {'kind': kind}}
            request = transport.request_body(route, 'Shared native prompt', packet)
            report = preflight.build_report({}, None, route, 'native_math', 'Shared native prompt', packet, request)
            types.append(report['required_output_units']['type'])
        self.assertEqual(len(set(types)), 3, 'Different native contracts must not share workload observations')
