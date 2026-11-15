"""K10 reads retained evidence even when an outcome classifier calls it general."""

import copy
import contextlib
import io
import tempfile
import sqlite3
from pathlib import Path
import json
import unittest
from unittest.mock import patch

from ci import capacity_guard as guard, route_capacity_profile as profile
from research_harness import review_route_capacity as capacity
import test_review_failure_class as failure_fixtures
from test_review_route_capacity import build_route
from test_review_output_profile import build_request


class ReleaseGuardTests(unittest.TestCase):
    def setUp(self):
        self.case = failure_fixtures.FailureClassTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.records['review_route']['route'] = build_route()

    def prepare_audited_session(self):
        import test_review_continuation as session_fixtures
        import test_research_problem_reviews as problem_fixtures
        from research_harness import review_protocol as protocol, review_transport as transport
        fixture = session_fixtures.ContinuationTests()
        case = problem_fixtures.ProblemReviewTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        build_response = case.build_provider_response
        def respond(output):
            response = build_response(output)
            response['model'] = 'gpt-6-astra'
            response['usage']['input_tokens'] = 37
            return response
        case.build_provider_response = respond
        route = dict(build_route('openai_responses_v1', 'gpt-6-astra', output=1000),
                     endpoint='https://api.openai.com/v1/responses')
        case.mutate(protocol.record_route, route)
        def probe(route, request, credential):
            packet = json.loads(request['input'][0]['content'][len(transport.PACKET_LABEL):])
            return respond(dict(allowed_control=packet['allowed_control'],
                                evidence_control=packet['evidence_control'], excluded_controls=[]))
        with patch.object(transport, 'send_request', side_effect=probe):
            case.mutate(protocol.probe_route, dict(id='probe', route_id='route'))
        case.supporting = case.artifacts.put(b'A field standard.', 'text/plain')
        fixture.case = case
        fixture.assignment = case.assign('bar', 'first', **case.deliver_supporting('first'))
        return fixture

    def assess(self):
        return profile.assess_release_records(self.case.records, self.case.artifacts,
            matrix=guard.read_matrix(), inventory=guard.read_transport_inventory(), check_plans=False)

    def test_real_current_assignment_passes_read_only_audit(self):
        fixture = self.prepare_audited_session()
        records = fixture.case.read_records()
        before = copy.deepcopy(records)
        result = profile.assess_release_records(records, fixture.case.artifacts)
        self.assertEqual(result['result'], 'pass', result['blocks'])
        self.assertEqual(records, before)

    def test_closure_command_applies_the_release_date_and_reports_aggregate_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'roots.md'
            path.write_text('- ' + str(Path(directory)) + '\n')
            row = dict(result='pass', attempts=[], blocks=[], shares=[dict(route_class=dict(
                adapter='claude_cli_v1', model='synthetic', effort='max'), kind='request', calls=20,
                at_or_over_limit=2, result='fail')])
            stream = io.StringIO()
            with patch.object(profile, 'read_root_capacity_check', return_value=row) as reader, contextlib.redirect_stdout(stream):
                status = profile.run_cli(['--roots-file', str(path), '--check-closure', '--since', '2026-11-01T00:00:00Z'])
            self.assertEqual(status, 1)
            reader.assert_called_once_with(Path(directory), since='2026-11-01T00:00:00Z')
            self.assertEqual(json.loads(stream.getvalue())['result'], 'fail')

    def test_unreadable_root_fails_closed_with_the_guard_message(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.exactory').mkdir()
            (root / '.exactory/research.sqlite3').write_bytes(b'not a database')
            result = profile.read_root_capacity_check(root)
            self.assertEqual(result['result'], 'fail')
            self.assertEqual(result['blocks'][0]['reason'], 'audit_input_unreadable')
            self.assertEqual(result['blocks'][0]['message'], profile.RELEASE_EVIDENCE_MESSAGE)

    def test_release_reader_sees_committed_wal_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.exactory').mkdir()
            connection = sqlite3.connect(str(root / '.exactory/research.sqlite3'))
            try:
                connection.execute('pragma journal_mode=wal')
                connection.execute('create table records(kind text,key text,value text)')
                connection.execute('insert into records values(?,?,?)', ('review_route', 'route', json.dumps(
                    dict(id='route', adapter='future_adapter'))))
                connection.execute('insert into records values(?,?,?)', ('review_route_probe', 'probe', json.dumps(
                    dict(id='probe', route_id='route', status='failed'))))
                connection.commit()
                result = profile.read_root_capacity_check(root)
                self.assertIn('unlisted_adapter', {row['reason'] for row in result['blocks']})
            finally:
                connection.close()

    def test_size_evidence_with_a_general_outcome_blocks_and_preserves_records(self):
        attempt = self.case.attempt(code='review_response_too_large')
        before = copy.deepcopy(self.case.records)
        with patch('research_harness.review_failure_class.classify_attempt',
                return_value=dict(outcome='unknown', signal={}, reset_at=None)):
            result = self.assess()
        self.assertIn('size_evidence_without_size_outcome', [row['reason'] for row in result['blocks']])
        self.assertEqual(self.case.records, before)
        self.assertEqual(result['attempts'][0]['entry'], 'A26')
        self.assertEqual(result['attempts'][0]['id'], attempt['id'])

    def test_unlisted_code_and_context_issue_block(self):
        attempt = self.case.attempt(code='review_future_transport_failure')
        response = dict(context_evidence=dict(issues=['future_context_issue']))
        attempt['response'] = self.case.put(response)
        reasons = {row['reason'] for row in self.assess()['blocks']}
        self.assertTrue({'unlisted_transport_code', 'unlisted_context_issue'}.issubset(reasons))

    def test_future_claude_system_event_blocks_even_under_a_declared_prefix(self):
        attempt = self.case.attempt(code='review_provider_failure')
        attempt['response'] = self.case.put(dict(context_evidence=dict(issues=['system_event_future'])))
        self.assertIn('unverified_context_event', {row['reason'] for row in self.assess()['blocks']})

    def test_compaction_beside_another_issue_is_still_retained_size_evidence(self):
        self.case.records['review_route']['route']['adapter'] = 'codex_cli_v1'
        attempt = self.case.attempt('{}', status='completed', provider_status='completed')
        attempt['response'] = self.case.put(dict(status='completed', output=[],
            context_evidence=dict(issues=['uninspected_compaction', 'inherited_reasoning'])))
        with patch('research_harness.review_failure_class.classify_attempt',
                return_value=dict(outcome='context_unverified', signal={}, reset_at=None)):
            result = self.assess()
        self.assertIn('size_evidence_without_size_outcome', {row['reason'] for row in result['blocks']})

    def build_claude_response(self):
        from research_harness import review_claude_transport as claude
        import test_research_claude_transport as stream_fixtures
        route = build_route()
        request = build_request(route, {})
        events = stream_fixtures.clean_stream(request, '{}', model=route['model'])
        events.insert(-1, dict(type='system', subtype='compact_boundary',
            compact_metadata=dict(trigger='auto', pre_tokens=960000)))
        with patch.object(claude, '_cli', return_value=('/fixture/claude', 'synthetic')), patch.object(
                claude, '_run_owned', return_value=stream_fixtures.completed(events)):
            response = claude.send_request(route, request)
        return response

    def test_retained_claude_compaction_is_read_even_when_the_old_issue_list_is_empty(self):
        response = self.build_claude_response()
        response['context_evidence']['issues'] = []
        attempt = self.case.attempt('{}', status='completed', provider_status='completed')
        attempt['response'] = self.case.put(response)
        before = copy.deepcopy(self.case.records)
        with patch('research_harness.review_failure_class.classify_attempt',
                return_value=dict(outcome='context_unverified', signal={}, reset_at=None)):
            result = self.assess()
        self.assertEqual(result['attempts'][0]['entry'], 'A27')
        self.assertIn('size_evidence_without_size_outcome', {row['reason'] for row in result['blocks']})
        self.assertEqual(self.case.records, before)

    def test_invalid_and_truncated_retained_events_block_the_release(self):
        for defect, expected in (('invalid', 'unreadable_context_events'), ('truncated', 'truncated_context_events')):
            with self.subTest(defect=defect):
                self.case.records['review_attempt'].clear()
                response = self.build_claude_response()
                retention = response['execution']['system_events']
                if defect == 'invalid':
                    retention['events'][0]['raw_base64'] = 'invalid'
                else:
                    retention.update(truncated=True, observed_count=retention['observed_count'] + 1,
                        observed_bytes=retention['observed_bytes'] + 1)
                attempt = self.case.attempt('{}', status='completed', provider_status='completed')
                attempt['response'] = self.case.put(response)
                self.assertIn(expected, {row['reason'] for row in self.assess()['blocks']})

    def test_non_size_signal_cannot_hide_independent_compaction_or_output_usage(self):
        answer = ('API Error: response exceeded 64000 output token maximum '
            'for a request sent without streaming')
        for signal, entry in (('compaction', 'A27'), ('output_usage', 'A16')):
            with self.subTest(signal=signal):
                self.case.records['review_attempt'].clear()
                attempt = self.case.attempt(answer, code=None, tokens=128000 if signal == 'output_usage' else 0)
                if signal == 'compaction':
                    response = self.build_claude_response()
                    response['output'] = [dict(type='message', content=[dict(type='output_text', text=answer)])]
                    attempt['response'] = self.case.put(response)
                result = self.assess()
                self.assertEqual(result['attempts'][0]['outcome'], 'execution_incomplete')
                self.assertEqual(result['attempts'][0]['entry'], entry)
                self.assertIn('size_evidence_without_size_outcome', {row['reason'] for row in result['blocks']})

    def test_completed_at_cap_generation_is_allowed_at_the_release_share_ceiling(self):
        fixture = self.prepare_audited_session()
        fixture.invoke(fixture.case.build_review_output('bar'))
        records = fixture.case.read_records()
        original = next(iter(records['review_attempt'].values()))
        records['review_attempt'] = {}
        assignment = records['review_assignment'][original['assignment_id']]
        route = records['review_route'][assignment['route_id']]
        limit = capacity.describe_route_capacity(route)['output_tokens']['value']
        for index in range(20):
            attempt = copy.deepcopy(original)
            attempt['id'] = 'completed-' + str(index)
            attempt['usage']['output_tokens'] = limit if index == 0 else 100
            records['review_attempt'][attempt['id']] = attempt
        result = profile.assess_release_records(records, fixture.case.artifacts, check_plans=False)
        self.assertTrue(all(row['outcome'] is None for row in result['attempts']))
        self.assertEqual(result['result'], 'pass', result['blocks'])
        self.assertEqual(result['shares'][0]['result'], 'pass')

    def test_error_status_precedes_the_response_body_limit(self):
        attempt = self.case.attempt(code='review_response_too_large')
        attempt['error']['details'] = dict(status=503)
        result = self.assess()
        self.assertNotIn('size_evidence_without_size_outcome', {row['reason'] for row in result['blocks']})
        self.assertEqual(result['attempts'][0]['outcome'], 'provider_unavailable')

    def test_inside_input_measure_at_input_limit_blocks(self):
        attempt = self.case.attempt(code='review_input_too_large')
        attempt['request'] = self.case.put(build_request(build_route(), {}))
        result = self.assess()
        self.assertIn('input_limit_inside_measure', {row['reason'] for row in result['blocks']})

    def test_unbounded_binary_input_is_reported_without_comparing_an_absent_token_bound(self):
        from test_review_binary_capacity import build_request as build_binary_request
        route = build_route('openai_responses_v1', 'gpt-6-astra', output=8000)
        self.case.records['review_route']['route'] = route
        attempt = self.case.attempt(code=None, status='completed', provider_status='completed')
        attempt['request'] = self.case.put(build_binary_request('The packet below is JSON.\n{}'))
        attempt['usage']['input_tokens'] = 123
        result = self.assess()
        self.assertEqual(result['result'], 'fail')
        self.assertIn('binary_input_without_bound', {row['reason'] for row in result['blocks']})
        self.assertNotIn('token_measure_below_usage', {row['reason'] for row in result['blocks']})

    def test_input_limit_audit_uses_the_same_keyed_evidence_as_admission(self):
        from capacity_evidence_fixtures import build_capacity_runtime
        from research_harness.review_capacity_evidence import describe_capacity_key
        route = dict(build_route('openai_responses_v1', 'unlisted-model', output=8000),
                     runtime=build_capacity_runtime('openai_responses_v1'))
        self.case.records['review_route']['route'] = route
        attempt = self.case.attempt(code='review_input_too_large')
        attempt['request'] = self.case.put(build_request(route, {}))
        self.case.records['review_route_probe'] = dict(probe=dict(id='probe', route_id='route',
            capacity=dict(input_tokens=100000), capacity_evidence=dict(
                key=describe_capacity_key(route), input_tokens=100000), usage={}))
        self.assertIn('input_limit_inside_measure', {row['reason'] for row in self.assess()['blocks']})

    def test_unknown_packet_member_blocks_without_mutating_artifact(self):
        reference = self.case.assignment['packet']
        original = self.case.artifacts.read(reference)
        packet = json.loads(original)
        # Keep the stored assignment shape and bind a deliberately unregistered member.
        self.case.assignment['packet'] = self.case.put(dict(packet, undeclared_capacity_member='x'))
        result = self.assess()
        self.assertIn('undeclared_packet_member', {row['reason'] for row in result['blocks']})
        self.assertEqual(self.case.artifacts.read(reference), original)

    def test_stored_emitted_packet_is_checked_beside_the_assignment_packet(self):
        from research_harness import review_protocol as protocol, review_transport
        fixture = self.prepare_audited_session()
        fixture.invoke(fixture.case.build_review_output('bar'))
        records = fixture.case.read_records()
        assignment = records['review_assignment'][fixture.assignment['id']]
        packet = protocol._read_json(fixture.case.artifacts, assignment['packet'])
        route = records['review_route'][assignment['route_id']]
        request = review_transport.request_body(route, assignment['prompt']['text'],
            dict(packet, undeclared_emitted_member='synthetic'))
        attempt = next(iter(records['review_attempt'].values()))
        attempt['request'] = fixture.case.artifacts.put(json.dumps(request).encode(), 'application/json')
        result = profile.assess_release_records(records, fixture.case.artifacts, check_plans=False)
        self.assertIn('undeclared_emitted_packet_member', {row['reason'] for row in result['blocks']})

    def test_release_shares_are_aggregated_across_roots(self):
        share = dict(route_class=dict(adapter='claude_cli_v1', model='synthetic', effort='max'),
            kind='request', calls=10, at_or_over_limit=1, result='incomplete')
        check = dict(result='pass', blocks=[], attempts=[], shares=[share])
        result = profile.assess_release_roots([check, copy.deepcopy(check)])
        self.assertEqual(result['shares'][0]['calls'], 20)
        self.assertEqual(result['result'], 'fail')
        third = copy.deepcopy(check)
        third['shares'][0].update(calls=90, at_or_over_limit=0)
        self.assertEqual(profile.assess_release_roots([check, third])['result'], 'pass')
        self.assertEqual(profile.assess_release_roots([])['result'], 'fail')

    def test_release_share_reads_the_criterion_ceiling(self):
        samples = [dict(route_class=dict(adapter='claude_cli_v1', model='synthetic', effort='max'),
            task=dict(kind='request'), origin='instrumented', output_profile_eligible=True,
            completed_generation=True, usage=dict(output_tokens=128000 if i < 2 else 100),
            output_limit=128000, observed_at='2026-11-14T00:00:00Z') for i in range(20)]
        self.assertEqual(profile.assess_release_output_share(samples)[0]['result'], 'fail')
        samples[1]['usage']['output_tokens'] = 100
        self.assertEqual(profile.assess_release_output_share(samples)[0]['result'], 'pass')
        self.assertEqual(capacity.VERIFICATION_CALLS_PER_OVER_LIMIT, 20)
