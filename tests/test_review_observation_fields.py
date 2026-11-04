"""Observation clocks and optional bounded Codex diagnostics are advisory."""

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from research_harness import review_protocol as protocol, review_transport as transport
from research_harness import review_staged_protocol as staged
from research_harness.review_task_execution import identify_call
from research_harness.errors import ResearchError
import test_research_review_protocol as fixtures


class ObservationFieldsTests(LiteratureCase):
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
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.codex_home = Path(temporary.name)
        self.environment = patch.dict(os.environ, {'CODEX_HOME': str(self.codex_home)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def failed(self, *, thread='thread-123', answer='', adapter='codex_cli_v1', status='failed'):
        route = dict(id='local-route', adapter=adapter, runtime={'fixture': True})
        response = self.response({})
        response['status'] = 'incomplete'
        response['output'][0]['content'][0]['text'] = answer
        response['context_evidence'] = {'thread_id': thread}
        observation = dict(status=status, request={}, response=response, output=None,
                           error=dict(kind='JSONDecodeError', code='review_transport_failure'))
        with patch.object(transport, 'runtime', return_value=route['runtime']), \
                patch.object(transport, 'observe', return_value=observation):
            return protocol._observed(self.artifacts, route, {}, None)

    def rollout(self, error=None):
        path = self.codex_home / 'sessions' / '2026' / '11' / '04' / 'rollout-test-thread-123.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        events = [dict(type='event_msg', payload=dict(type='task_complete', error={
            'codex_error_info': 'server_overloaded', 'message': 'Earlier error.'})),
            dict(type='event_msg', payload=dict(type='task_complete', error=error or {
                'codex_error_info': 'usage_limit_exceeded', 'message': 'Synthetic quota message.'}))]
        path.write_text('\n'.join(json.dumps(event) for event in events) + '\n')
        return path

    def test_new_observation_clock_and_last_rollout_error_on_all_codex_routes(self):
        path = self.rollout()
        for adapter in ('codex_cli_v1', 'codex_cli_framed_v1', 'codex_cli_visual_v1'):
            before = datetime.now(timezone.utc).replace(microsecond=0)
            observed = self.failed(adapter=adapter)
            after = datetime.now(timezone.utc)
            self.assertLessEqual(before, datetime.fromisoformat(observed['observed_at']))
            self.assertLessEqual(datetime.fromisoformat(observed['observed_at']), after)
            self.assertEqual(datetime.fromisoformat(observed['observed_at']).microsecond, 0)
            self.assertEqual(observed['provider_diagnostic'], dict(source='codex_rollout', thread_id='thread-123',
                codex_error_info='usage_limit_exceeded', message='Synthetic quota message.',
                rollout_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))

    def test_missing_ambiguous_symlink_and_oversized_rollouts_give_no_diagnostic(self):
        self.assertNotIn('provider_diagnostic', self.failed())
        path = self.rollout()
        duplicate = path.with_name('rollout-second-thread-123.jsonl')
        duplicate.write_bytes(path.read_bytes())
        self.assertNotIn('provider_diagnostic', self.failed())
        duplicate.unlink()
        target = path.with_name('target.jsonl')
        path.rename(target)
        path.symlink_to(target)
        with patch.object(Path, 'read_text', side_effect=AssertionError('Do not read a symlink')):
            self.assertNotIn('provider_diagnostic', self.failed())
        path.unlink()
        with path.open('wb') as stream:
            stream.truncate(64 * 1024 * 1024 + 1)
        self.assertNotIn('provider_diagnostic', self.failed())

    def test_diagnostic_requires_thread_failed_status_and_empty_answer(self):
        self.rollout()
        for values in (dict(thread=None), dict(answer='An answer.'),
                       dict(adapter='claude_cli_v1'), dict(status='completed')):
            self.assertNotIn('provider_diagnostic', self.failed(**values))

    def test_default_codex_home_and_last_completion_without_error(self):
        path = self.rollout()
        default_home = self.codex_home / 'default-home'
        session = default_home / '.codex' / 'sessions'
        session.mkdir(parents=True)
        session.joinpath(path.name).write_bytes(path.read_bytes())
        with patch.dict(os.environ):
            os.environ.pop('CODEX_HOME', None)
            with patch.object(Path, 'home', return_value=default_home):
                self.assertIn('provider_diagnostic', self.failed())
        session.joinpath(path.name).unlink()
        with path.open('a') as stream:
            stream.write(json.dumps(dict(type='event_msg', payload=dict(type='task_complete', error=None))) + '\n')
        self.assertNotIn('provider_diagnostic', self.failed())

    def test_import_validator_accepts_advisory_fields_without_backfilling_old_attempts(self):
        self.run_probe()
        self.assign()
        payload = dict(id='old', assignment_id='assignment', status='failed', output=None,
                       reason='Synthetic interrupted call.', usage={})
        old = self.mutate(protocol.record_attempt, payload)['result']
        self.assertNotIn('observed_at', old)
        self.assertNotIn('provider_diagnostic', old)
        payload.update(id='new', observed_at='2026-11-04T08:00:00+00:00',
                       provider_diagnostic=dict(source='codex_rollout', thread_id='thread',
                           codex_error_info='server_overloaded', message='Synthetic overload.',
                           rollout_sha256='f' * 64))
        new = self.mutate(protocol.record_attempt, payload)['result']
        self.assertEqual(new['observed_at'], payload['observed_at'])
        self.assertEqual(new['provider_diagnostic'], payload['provider_diagnostic'])
        self.assertNotIn('observed_at', self.records()['review_attempt']['old'])

    def test_import_validator_refuses_malformed_advisory_fields(self):
        self.run_probe()
        self.assign()
        payload = dict(id='invalid', assignment_id='assignment', status='failed', output=None,
                       reason='Synthetic interrupted call.', usage={})
        diagnostic = dict(source='codex_rollout', thread_id='thread', codex_error_info='server_overloaded',
                          message='Synthetic overload.', rollout_sha256='f' * 64)
        invalid_values = [dict(observed_at=17), dict(observed_at=None), dict(observed_at='not a date'),
                          dict(provider_diagnostic=[]), dict(provider_diagnostic={}),
                          dict(provider_diagnostic=dict(diagnostic, message=17))]
        for index, invalid in enumerate(invalid_values):
            payload['id'] = 'invalid-' + str(index)
            with self.subTest(invalid=invalid):
                with self.assertRaises(ResearchError) as raised:
                    self.mutate(protocol.record_attempt, dict(payload, **invalid))
                self.assertEqual(raised.exception.code, 'invalid_review_attempt')
        self.assertFalse(self.records().get('review_attempt', {}))

    def test_rollout_with_nontext_message_gives_no_diagnostic(self):
        self.rollout(dict(codex_error_info='usage_limit_exceeded', message=17))
        self.assertNotIn('provider_diagnostic', self.failed())

    def test_staged_capture_and_attempt_keep_the_rollout_diagnostic(self):
        import sys
        import test_review_runtime_retry as runtime_fixtures
        import test_review_staged_frontier as frontier
        from research_harness import review_worker
        fixture = runtime_fixtures.StagedRuntimeRetryTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        controller = fixture.create_controller()
        planned = fixture.command('review-assignment', controller['next'][0]['payload'])
        path = self.rollout()
        replacement = """
import json, os
os.environ['CODEX_HOME'] = %r
def failed_response(route, request, credential):
    return json.loads(%r)
review_transport.send_request = failed_response
""" % (str(self.codex_home), json.dumps(dict(id='thread-123', status='incomplete',
            output=[dict(type='message', content=[dict(type='output_text', text='')])],
            context_evidence=dict(thread_id='thread-123'))))
        script = frontier.WORKER.replace('review_transport.send_request = send', replacement)
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', script]):
            attempt = fixture.command('review-run', planned['next'][0]['payload'])
        records = fixture.case.read_records()
        capture = records['review_observation'][attempt['reservation_id']]
        self.assertEqual(attempt['status'], 'failed')
        for key in ('observed_at', 'provider_diagnostic'):
            self.assertEqual(capture['observation'][key], attempt[key])
        self.assertEqual(attempt['provider_diagnostic']['rollout_sha256'],
                         hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertTrue(records['review_reservation'][attempt['reservation_id']]['released'])

    def test_capture_finish_and_authentication_preserve_advisory_fields(self):
        self.run_probe()
        self.assign()
        response = self.response({'finding': 'A bounded finding.'})
        original_observe = transport.observe
        diagnostic = dict(source='codex_rollout', thread_id='thread', codex_error_info='test',
                          message='Synthetic diagnostic.', rollout_sha256='f' * 64)
        def observe(*args):
            return dict(original_observe(*args), provider_diagnostic=diagnostic)
        with patch.object(transport, 'send_request', return_value=response), \
                patch.object(transport, 'observe', side_effect=observe):
            result = self.mutate(protocol.invoke_assignment, dict(id='attempt', assignment_id='assignment'),
                                 credential='fixture')['result']
        records = self.records()
        capture = records['review_call_observation'][identify_call('review.invoke', 'attempt')]
        for key in ('observed_at', 'provider_diagnostic'):
            self.assertEqual(capture['observation'][key], result[key])
        assignment = records['review_assignment']['assignment']
        self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, assignment, result))
        changed = dict(result, observed_at='irrelevant', provider_diagnostic={'untrusted': True})
        self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, assignment, changed))
        old = {key: value for key, value in result.items() if key not in ('observed_at', 'provider_diagnostic')}
        self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, assignment, old))
        self.assertNotIn('observed_at', old)
        # Advisory provider objects cannot add an artifact dependency to capture authentication.
        from research_harness.review_call_capture import read_call_observation
        changed_records = copy.deepcopy(records)
        changed_capture = changed_records['review_call_observation'][identify_call('review.invoke', 'attempt')]
        phantom = dict(path='research/sources/objects/' + '0' * 64, sha256='0' * 64,
                       media_type='text/plain', size=1)
        changed_capture['observation']['provider_diagnostic']['codex_error_info'] = phantom
        _, observed = read_call_observation(changed_records, self.artifacts, 'review.invoke',
            dict(id='attempt', assignment_id='assignment'), capture['request_id'])
        self.assertEqual(observed['provider_diagnostic']['codex_error_info'], phantom)
        staged_attempt = staged.build_capture_attempt(assignment,
            dict(capture, reservation_id='reservation'), 'staged-attempt')
        for key in ('observed_at', 'provider_diagnostic'):
            self.assertEqual(staged_attempt[key], result[key])
