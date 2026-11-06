"""Certified framing recovery preserves answers and their existing consumers."""

import copy
import hashlib
import importlib
import json
import sys
import time
import unittest
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from research_harness import review_protocol as protocol
from research_harness import review_transport as transport
# Load view dependencies before the problem-review fixture installs its scoped double.
from research_harness import cli, output_views
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from test_review_answer_one_object import build_object, build_response
import test_research_review_protocol as fixtures
import test_review_output_correction as correction_fixtures
import test_review_staged_protocol as staged_fixtures


def recover(answer, **updates):
    module = importlib.import_module('research_harness.review_answer_recovery')
    return module.recover_answer(dict(build_response(answer), **updates))


class RecoveryGrammarTests(unittest.TestCase):
    def assert_recovery(self, answer, expected, rules, offsets):
        result = recover(answer)
        self.assertIsNotNone(result)
        self.assertEqual(result['output'], expected)
        self.assertEqual(result['certificate'], {
            'protocol': 'exactory-review-answer-recovery-v1', 'rules': rules,
            'removed_offsets': offsets,
            'answer_sha256': hashlib.sha256(answer.encode('utf-8')).hexdigest(),
            'output_sha256': digest(expected),
            'error': {'kind': 'JSONDecodeError', 'code': 'review_transport_failure'}})
        return result

    def test_the_23_published_structural_shapes(self):
        suffixes = [dict(excerpts=[])] * 10 + [dict(source_requests=[], excerpts=[])]
        suffixes += [dict(objections_note_unused=None, excerpts=[])]
        suffixes += [dict(reassessment={'findings': []})] * 2
        suffixes += [dict(objections=[], limitations=['A condition remains.'])] * 3
        self.assertEqual(len(suffixes), 17)
        for index, suffix in enumerate(suffixes):
            prefix = {key: value for key, value in build_object().items() if key not in suffix}
            prefix['value']['reason'] += ' Case ' + str(index) + '.'
            text = json.dumps(prefix, ensure_ascii=False)
            answer = text + ',' + json.dumps(suffix)[1:]
            with self.subTest(index=index):
                self.assert_recovery(answer, dict(prefix, **suffix),
                                     ['premature_root_close'], [len(text) - 1])
        for index in range(6):
            output = build_object()
            output['value']['reason'] += ' Case ' + str(index) + '.'
            text = json.dumps(output)
            self.assert_recovery(text + ' \n} \t', output,
                                 ['surplus_closing_braces'], [len(text) + 2])

    def test_two_measured_raw_line_feed_shapes_and_combined_rules(self):
        for output in ({'reason': 'First\nsecond'}, {'value': {'reason': 'First\nsecond'}}):
            answer = json.dumps(output).replace('\\n', '\n')
            self.assert_recovery(answer, output, ['raw_control_characters'], [])
            self.assert_recovery(answer + ' }\n}', output,
                                 ['surplus_closing_braces', 'raw_control_characters'],
                                 [len(answer) + 1, len(answer) + 3])
            self.assert_recovery(answer + ',"more":[]}', dict(output, more=[]),
                                 ['premature_root_close', 'raw_control_characters'],
                                 [len(answer) - 1])

    def test_every_literal_control_character_and_json_whitespace(self):
        for point in range(32):
            answer = ' \t\r\n{"x":"a' + chr(point) + 'b"} \n'
            self.assert_recovery(answer, {'x': 'a' + chr(point) + 'b'},
                                 ['raw_control_characters'], [])

    def test_refuses_ambiguous_incomplete_and_non_json_answers(self):
        refused = [
            '{"x":1,"x":2}}', '{"x":1},"x":2}',
            '{"x":{"a":1,"a":2}}}', '{"x":[{"a":1,"\\u0061":2}]}}',
            '{"x":1,"\\u0078":2}}', '{}{}', '{} prose', 'prose {}',
            '```json\n{}\n``` prose', '{"x":', '{"x":1 "y":2}',
            '{"x":1},"y":2}}', '{"x":{"a":1},"b":2},"c":3',
            '{"x":{"a":1},"b":2}} ,"c":3}',
            '{"x":{"a":1},"b":2},"c":NaN}',
            '{"x":NaN}}', '{"x":Infinity}}', '{"x":-Infinity}}', '{"x":1e400}}',
            '{"x":"\\ud800"}}', '{"x":' + '1' * 4301 + '}}',
            '{"x":' * 513 + '0' + '}' * 513 + '}',
            '\v{}', '{}\v}', '{}\u00a0}', '{}\ufeff}', '{"x":\x001}}',
            '', '[]', '1', 'null', '{}',
            json.dumps(build_object()) + ',"objections":[],"limitations":[]}',
            # Recovery never removes the premature close of a nested array's object.
            '{"outer":[{"a":1},"b":2}]}',
        ]
        for answer in refused:
            with self.subTest(answer=answer[:100]):
                self.assertIsNone(recover(answer))
        for updates in ({'status': 'incomplete'}, {'status': 'failed'}, {'id': ''}, {'id': None}):
            self.assertIsNone(recover('{} }', **updates))
        from research_harness.review_answer_recovery import recover_answer
        response = build_response('{} }')
        del response['id']
        self.assertIsNone(recover_answer(response))

    def test_depth_512_and_python_float_semantics(self):
        answer = '{"x":' * 512 + '1.25' + '}' * 512
        result = recover(answer + '}')
        self.assertIsNotNone(result)
        value = result['output']
        for _ in range(512):
            value = value['x']
        self.assertIs(type(value), float)
        self.assertEqual(value, 1.25)

    def test_large_answer_has_bounded_linear_work(self):
        piece = {'nested': [{'quote': '雪 "quoted" \\ {} 😀', 'n': 1.25}]}
        timings = []
        for count in (500, 4200):
            output = {'evidence': [piece] * count}
            text = json.dumps(output, ensure_ascii=False)
            started = time.perf_counter()
            self.assert_recovery(text + '}', output, ['surplus_closing_braces'], [len(text)])
            timings.append(time.perf_counter() - started)
        self.assertGreater(len(text), 250000)
        self.assertLess(timings[1], max(1.0, timings[0] * 20))
        self.assertLess(timings[1], 5.0)

    def test_derive_output_rejects_every_certificate_tampering(self):
        from research_harness.review_answer_recovery import derive_output
        response = build_response('{"v":true}}')
        result = recover('{"v":true}}')
        observation = {'output': result['output'], 'answer_recovery': result['certificate']}
        self.assertEqual(derive_output(observation, response), {'v': True})
        changes = {'protocol': 'other', 'rules': ['premature_root_close'], 'removed_offsets': [0],
                   'answer_sha256': '0' * 64, 'output_sha256': '0' * 64,
                   'error': {'kind': 'ValueError', 'code': 'review_transport_failure'}}
        for name, value in changes.items():
            changed = copy.deepcopy(observation)
            changed['answer_recovery'][name] = value
            with self.subTest(field=name), self.assertRaises(ResearchError) as caught:
                derive_output(changed, response)
            self.assertEqual(caught.exception.code, 'review_answer_recovery_invalid')
        for change in ({'error': {'kind': 'JSONDecodeError', 'code': 'other'}},
                       {'removed_offsets': [True]}, {'additional_field': True}):
            changed = dict(observation, answer_recovery=dict(result['certificate'], **change))
            with self.assertRaises(ResearchError):
                derive_output(changed, response)
        for changed in (build_response('{"v":true} }'), build_response('{"v":true}'),
                        dict(response, status='incomplete'), dict(response, id=''),
                        dict(response, output=None), dict(response, output=[None])):
            with self.assertRaises(ResearchError):
                derive_output(observation, changed)
        with self.assertRaises(ResearchError):
            derive_output(dict(observation, output={'v': 1}), response)
        parsed = build_response('{"v":1}')
        self.assertEqual(derive_output({}, parsed), transport.response_output(parsed))


class RecoveryObservationTests(LiteratureCase):
    protocol = fixtures.ReviewProtocolTests.protocol
    seed = fixtures.ReviewProtocolTests.seed
    route = fixtures.ReviewProtocolTests.route
    response = fixtures.ReviewProtocolTests.response
    run_probe = fixtures.ReviewProtocolTests.run_probe
    assign = fixtures.ReviewProtocolTests.assign
    records = fixtures.ReviewProtocolTests.records

    def setUp(self):
        super().setUp()
        self.route()
        self.run_probe()
        self.assign()

    def test_recovered_capture_authenticates_exposes_text_and_charges_once(self):
        output = {'finding': '雪\nA retained finding.', 'flag': True}
        response = self.response(output)
        text = response['output'][0]['content'][0]['text'] + '}'
        response['output'][0]['content'][0]['text'] = text
        with patch.object(transport, 'send_request', return_value=response) as send:
            result = self.mutate(protocol.invoke_assignment,
                                 dict(id='recovered', assignment_id='assignment'))['result']
        self.assertEqual(send.call_count, 1)
        self.assertEqual(result['status'], 'completed')
        self.assertIsNone(result['error'])
        self.assertEqual(result['provider_response_id'], response['id'])
        self.assertEqual(result['observed_model'], response['model'])
        self.assertEqual(result['usage']['input_tokens'], 37)
        records = self.records()
        assignment = records['review_assignment']['assignment']
        self.assertTrue(protocol.authenticate_provenance(records, self.artifacts, assignment, result))
        self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, assignment, result))
        captured = next(row for row in records['review_call_observation'].values()
                        if row['invocation_id'] == 'recovered')
        self.assertEqual(captured['observation']['answer_recovery'], result['answer_recovery'])
        raw = protocol.read_raw_observation(records, self.artifacts, 'recovered')
        self.assertEqual(raw['raw_output'], output)
        self.assertEqual(raw['answer_text'], text)
        self.assertEqual(raw['answer_recovery'], result['answer_recovery'])
        state = protocol.assignment_state(records, self.artifacts, 'assignment')
        self.assertEqual(state['answer_recovery'], result['answer_recovery'])
        usage = protocol.usage_report(records)
        self.assertEqual(usage['failed_calls'], 0)
        self.assertEqual(usage['recovered_calls'], 1)
        self.assertEqual(usage['call_ids'].count('recovered'), 1)
        self.assertEqual(usage['observed']['input_tokens'], 74)
        self.assertEqual(cli._review_status(records, self.artifacts)['assignments']['assignment'][
            'answer_recoveries']['recovered'], result['answer_recovery'])
        exported = cli.run(cli.build_parser().parse_args([
            'export', '--kind', 'outputs', '--workspace', str(self.root)]))
        self.assertEqual(exported['errors'], 0)
        files = output_views._build_series(records, self.artifacts, output_views._ViewInputs({}, {}))
        series = json.loads(next(row.content for name, row in files.items() if name.endswith('series.json')))
        self.assertEqual(series['turns'][0]['runs'][0]['answer_recovery'], result['answer_recovery'])
        self.assertEqual(json.loads((self.root / 'intermediate/reviews/usage.json').read_text())[
            'answer_recoveries']['recovered'], result['answer_recovery'])
        for changed in (dict(result, output=protocol._put_json(self.artifacts, {'finding': 1})),
                        dict(result, output=protocol._put_json(self.artifacts, dict(output, flag=1))),
                        dict(result, answer_recovery=dict(result['answer_recovery'], removed_offsets=[0])),
                        dict(result, response=protocol._put_json(self.artifacts, self.response(output)))):
            self.assertFalse(protocol.authenticate_provenance(records, self.artifacts, assignment, changed))
        changed_response = copy.deepcopy(response)
        changed_response['output'][0]['content'][0]['text'] += ' '
        changed = dict(result, response=protocol._put_json(self.artifacts, changed_response))
        self.assertFalse(protocol.authenticate_attempt(records, self.artifacts, assignment, changed))

    def test_old_failed_attempt_stays_failed(self):
        response = self.response({'finding': 'retained'})
        response['output'][0]['content'][0]['text'] += '}'
        with patch.object(transport, 'send_request', return_value=response):
            observed = transport.observe(records_route := self.records()['review_route']['route'],
                                         transport.request_body(records_route, 'JSON', {}), 'test-only')
        attempt = dict(observed, id='old', assignment_id='assignment', origin='instrumented')
        for name in ('response', 'output'):
            attempt[name] = None if attempt[name] is None else protocol._put_json(self.artifacts, attempt[name])
        self.seed('review_attempt', 'old', attempt)
        raw = protocol.read_raw_observation(self.records(), self.artifacts, 'old')
        self.assertEqual(raw['status'], 'failed')
        self.assertIsNone(raw['raw_output'])
        self.assertNotIn('answer_recovery', raw)

    def test_recovered_probe_keeps_positive_control_checks(self):
        self.assign(identifier='second-assignment')
        def provider(route, request, credential):
            packet = json.loads(request['input'][0]['content'][len(transport.PACKET_LABEL):])
            response = self.response({key: packet[key] for key in ('allowed_control', 'evidence_control')})
            response['output'][0]['content'][0]['text'] += ',"excluded_controls":[]}'
            return response
        with patch.object(transport, 'send_request', side_effect=provider):
            result = self.mutate(protocol.probe_route, dict(id='recovered-probe', route_id='route'))['result']
        self.assertEqual(result['status'], 'completed')
        state = protocol.route_state(self.records(), self.artifacts, 'route')
        self.assertEqual(state['context_status'], 'verified_for_route')
        self.assertEqual(state['probe_id'], 'recovered-probe')


class RecoveryContractTests(unittest.TestCase):
    def setUp(self):
        self.case = correction_fixtures.CorrectionTests()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.fixture = self.case.fixture

    def observe(self, output, identifier):
        response = self.fixture.build_provider_response(output)
        response['output'][0]['content'][0]['text'] += '}'
        with patch.object(transport, 'send_request', return_value=response) as send:
            result = self.fixture.mutate(protocol.invoke_assignment, dict(
                id=identifier, assignment_id=self.case.case.assignment['id']))['result']
        self.assertEqual(send.call_count, 1)
        self.assertEqual(result['status'], 'completed')
        return result

    def test_recovered_valid_contract_records_and_credits_delivered_evidence(self):
        from research_harness import research_decisions as decisions
        output = self.case.corrected()
        attempt = self.observe(output, 'accepted-recovery')
        saved = self.fixture.mutate(decisions.record_value_review, self.case.case.payload(output))['result']
        self.assertEqual(saved['payload'], self.case.case.payload(output))
        self.assertEqual(decisions.find_undelivered_review_references(
            self.fixture.read_records(), self.fixture.artifacts, saved), [])
        self.assertTrue(protocol.authenticate_attempt(self.fixture.read_records(), self.fixture.artifacts,
                                                     self.case.case.assignment, attempt))
        with self.assertRaises(ResearchError) as caught:
            self.observe(output, 'resample')
        self.assertEqual(caught.exception.code, 'review_resampling_forbidden')

    def test_two_recovered_refusals_exhaust_and_metadata_correction_recovers(self):
        from research_harness import research_decisions as decisions, review_output_correction as correction
        from research_harness import output_views
        for identifier in ('refused-one', 'refused-two'):
            self.observe(self.case.output, identifier)
            with self.assertRaises(ResearchError) as caught:
                self.fixture.mutate(decisions.record_value_review, self.case.case.payload(self.case.output))
            self.assertEqual(caught.exception.code, 'review_reassessment_mismatch')
        with self.assertRaises(ResearchError) as caught:
            self.observe(self.case.output, 'exhausted')
        self.assertEqual(caught.exception.code, 'review_slot_exhausted')
        response = self.fixture.build_provider_response(self.case.delta())
        response['output'][0]['content'][0]['text'] += '}'
        with patch.object(transport, 'send_request', return_value=response) as send:
            result = self.fixture.mutate(correction.invoke_correction, dict(
                id='correction', assignment_id=self.case.case.assignment['id'], route_id='route'))['result']
        self.assertEqual(send.call_count, 1)
        self.assertEqual(result['correction_status'], 'accepted', result['correction_error'])
        records = self.fixture.read_records()
        state = protocol.assignment_state(records, self.fixture.artifacts, self.case.case.assignment['id'])
        self.assertTrue(state['ready'], state['obligations'])
        self.assertEqual(state['output'], self.case.corrected())
        self.assertEqual(state['answer_recovery'], records['review_attempt']['refused-two']['answer_recovery'])
        self.assertEqual(len(state['refused_attempts']), 2)
        files = output_views._build_series(records, self.fixture.artifacts, output_views._ViewInputs({}, {}))
        series = next(json.loads(row.content) for name, row in files.items()
                      if name.endswith('series.json') and any(
                          turn['assignment_id'] == self.case.case.assignment['id']
                          for turn in json.loads(row.content)['turns']))
        runs = next(turn['runs'] for turn in series['turns']
                    if turn['assignment_id'] == self.case.case.assignment['id'])
        self.assertEqual(next(row for row in runs if row['run_id'] == 'refused-two')['correction']['answer_recovery'],
                         result['answer_recovery'])
        usage = protocol.usage_report(records)
        self.assertEqual(usage['recovered_calls'], 3)
        self.assertEqual(usage['call_ids'].count('correction'), 1)
        # This repair lineage uses the scoped preparation reader in production.
        scoped = protocol._read_context_records(self.fixture.store._read_scoped_preparation().records,
                                                self.case.case.assignment)
        self.assertEqual(scoped['review_attempt']['refused-two']['answer_recovery'],
                         records['review_attempt']['refused-two']['answer_recovery'])

    def test_invented_field_survives_recovery_and_is_refused_by_contract(self):
        from research_harness import research_decisions as decisions
        output = dict(self.case.corrected(), objections_note_unused=None)
        attempt = self.observe(output, 'invented-field')
        self.assertEqual(protocol._read_json(self.fixture.artifacts, attempt['output']), output)
        with self.assertRaises(ResearchError):
            self.fixture.mutate(decisions.record_value_review, self.case.case.payload(output))


class RecoveryStagedTests(unittest.TestCase):
    def test_worker_recovers_accepts_and_preserves_capture_binding(self):
        from research_harness import operations, review_worker, review_staged_protocol
        from research_harness.review_state_evaluation import _fingerprint_selection
        case = staged_fixtures.StagedEntryTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.oversized()
        controller = case.assign_staged('controller')
        child = case.case.assign('result', 'first', identifier='child',
            controller_assignment_id=controller['id'], task_id=controller['staged_review']['task_ids'][0])
        script = staged_fixtures._WORKER_SCRIPT.replace(
            '    return build_codex_response(', '    response = build_codex_response(').replace(
            'review_transport.send_request = send',
            "    response['output'][0]['content'][0]['text'] += '}'\n"
            '    return bind_codex_response(route, request, response)\nreview_transport.send_request = send')
        payload = dict(id='staged-recovered', assignment_id=child['id'])
        revision = case.case.store.revision
        mutate = operations.prepared_task_mutation

        def interrupt_attempt(store, operation, *args, **kwargs):
            if operation == 'review.task_attempt':
                raise ResearchError('store_busy', 'Interrupted after capture')
            return mutate(store, operation, *args, **kwargs)

        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', script]):
            with patch.object(operations, 'prepared_task_mutation', side_effect=interrupt_attempt):
                with self.assertRaises(ResearchError) as caught:
                    protocol.invoke_assignment(case.case.store, payload, expected_revision=revision,
                                               request_id='staged-recovered')
            self.assertEqual(caught.exception.code, 'store_busy')
            pending = case.case.read_records()
            capture = next(iter(pending['review_observation'].values()))
            pending_certificate = capture['observation']['answer_recovery']
            self.assertEqual(pending['review_reservation'][capture['id']]['state'], 'captured')
            self.assertNotIn('staged-recovered', pending['review_attempt'])
            self.assertEqual(cli._review_status(pending, case.case.artifacts)['assignments'][child['id']][
                'answer_recovery'], pending_certificate)
            self.assertEqual(protocol.usage_report(pending)['recovered_calls'], 1)
            result = protocol.invoke_assignment(case.case.store, payload, expected_revision=case.case.store.revision,
                                                request_id='staged-recovered')
            self.assertEqual(protocol.invoke_assignment(case.case.store, payload, expected_revision=revision,
                                                        request_id='staged-recovered'), result)
        self.assertEqual((case.case.root / 'calls').read_text().splitlines(), ['call'])
        records = case.case.read_records()
        attempt = records['review_attempt']['staged-recovered']
        certificate = attempt['answer_recovery']
        self.assertEqual(certificate, pending_certificate)
        capture = records['review_observation'][attempt['reservation_id']]
        self.assertEqual(capture['observation']['answer_recovery'], certificate)
        self.assertEqual(records['review_reservation'][attempt['reservation_id']]['state'], 'accepted')
        self.assertTrue(protocol.authenticate_attempt(records, case.case.artifacts, child, attempt))
        state = protocol.assignment_state(records, case.case.artifacts, child['id'])
        self.assertTrue(state['accepted'])
        self.assertEqual(state['answer_recovery'], certificate)
        changed = copy.deepcopy(attempt)
        changed['answer_recovery']['removed_offsets'] = [True]
        self.assertFalse(review_staged_protocol.authenticate_observation(records, case.case.artifacts, child, changed))
        before = _fingerprint_selection(records, child['id'], 'assignment')
        receipt = records['review_task_acceptance'][attempt['reservation_id']]['receipt']
        receipt['attempt']['answer_recovery']['rules'] = []
        self.assertNotEqual(before, _fingerprint_selection(records, child['id'], 'assignment'))
        receipt['attempt']['answer_recovery'] = copy.deepcopy(certificate)
        files = output_views._build_series(records, case.case.artifacts, output_views._ViewInputs({}, {}))
        series = next(json.loads(row.content) for name, row in files.items()
                      if name.endswith('series.json') and any(turn['assignment_id'] == child['id']
                          for turn in json.loads(row.content)['turns']))
        run = next(turn for turn in series['turns'] if turn['assignment_id'] == child['id'])['runs'][0]
        self.assertEqual(run['answer_recovery'], certificate)
        index = cli._review_status(records, case.case.artifacts)['assignments'][child['id']]
        self.assertEqual(index['answer_recoveries']['staged-recovered'], certificate)
        self.assertEqual(index['answer_recovery'], certificate)
        self.assertEqual(protocol.usage_report(records)['recovered_calls'], 1)
        self.assertEqual(protocol.usage_report(records)['call_ids'].count(attempt['reservation_id']), 1)


class RecoveryPartTests(unittest.TestCase):
    def test_two_recovered_part_refusals_are_recorded_automatically(self):
        from codex_route_fixtures import bind_codex_response, build_codex_response
        case = staged_fixtures.ResultPartsCase()
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.oversized()
        assignment = case.assign()
        output = case.output(assignment)
        output['notes'][0]['note'] = ''

        def provider(route, request, credential):
            response = build_codex_response(route, request, output)
            response['output'][0]['content'][0]['text'] += '}'
            return bind_codex_response(route, request, response)

        with patch.object(transport, 'send_request', side_effect=provider) as send:
            for identifier in ('part-refusal-one', 'part-refusal-two'):
                result = case.case.mutate(protocol.invoke_assignment, dict(
                    id=identifier, assignment_id=assignment['id']))
                self.assertEqual(result['result']['status'], 'completed')
                self.assertIn('output_refusal', result)
            with self.assertRaises(ResearchError) as caught:
                case.case.mutate(protocol.invoke_assignment, dict(
                    id='part-exhausted', assignment_id=assignment['id']))
        self.assertEqual(caught.exception.code, 'review_slot_exhausted')
        self.assertEqual(send.call_count, 2)
        records = case.case.read_records()
        self.assertEqual(set(records['review_output_refusal']), {'part-refusal-one', 'part-refusal-two'})
        self.assertEqual(protocol.usage_report(records)['recovered_calls'], 2)


if __name__ == '__main__':
    unittest.main()
