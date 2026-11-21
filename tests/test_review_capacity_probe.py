"""Sized probes establish only their own endpoint and runtime's observed room."""
import json
import unittest
from unittest.mock import Mock, patch

from literature_fixtures import LiteratureCase
import test_research_review_protocol as review_fixtures
from test_review_route_capacity import build_route as build_unkeyed_route
from capacity_evidence_fixtures import build_capacity_runtime, build_capacity_prior
from research_harness import review_capacity_evidence as capacity_evidence
from stored_review_fixtures import restore_stored_review
from research_harness import review_protocol as protocol, review_route_capacity as capacity
from research_harness import review_transport as transport
from research_harness import cli, record_check, review_stop
from research_harness.review_task_execution import identify_call
from research_harness.errors import ResearchError


def build_route(*args, **kwargs):
    route = build_unkeyed_route(*args, **kwargs)
    return dict(route, runtime=build_capacity_runtime(route['adapter']))


class CapacityProbeSizingTests(unittest.TestCase):
    def test_unkeyed_probe_cannot_schedule_or_scale_a_new_runtime(self):
        route = build_unkeyed_route('codex_cli_v1', 'unlisted')
        probe = dict(id='old', route_id=route['id'], capacity=dict(input_tokens=10000),
                     capacity_verified=True, usage=dict(input_tokens=10000), recorded_revision=1,
                     request={})
        records = dict(review_route={route['id']: route}, review_route_probe=dict(old=probe))
        self.assertEqual(capacity.select_capacity_probe_size(records, route, 60000), 150000)
        artifacts = Mock()
        artifacts.read.side_effect = AssertionError('An unkeyed probe must not be read for scaling.')
        self.assertEqual(capacity.scale_capacity_probe_size(records, artifacts, route, 150000), 150000)
        artifacts.read.assert_not_called()

    def test_sized_packet_keeps_three_members_and_controls_straddle_evidence(self):
        for adapter in ('claude_cli_v1', 'codex_cli_v1', 'codex_cli_framed_v1', 'openai_responses_v1'):
            with self.subTest(adapter=adapter):
                route = build_route(adapter, 'unlisted-model', output=1000)
                packet = dict(allowed_control='a'*32, evidence_control='b'*32, evidence='original')
                request = capacity.build_capacity_probe_request(route, 'Return JSON.', packet, 5000)
                self.assertEqual(capacity.measure_request(route, request)['tokens_upper'], 5000)
                serialized = transport._local_adapter(route).packet_text(request) if adapter != transport.ADAPTER else request['input'][0]['content']
                self.assertLess(serialized.index('a'*32), serialized.index(packet['evidence']))
                self.assertLess(serialized.index(packet['evidence']), serialized.index('b'*32))
                self.assertEqual(set(packet), {'allowed_control', 'evidence', 'evidence_control'})
                self.assertNotRegex(packet['evidence'], r'[0-9a-f]{32}')

    def test_visual_and_unrepresentable_capacity_sizes_are_refused(self):
        packet = dict(allowed_control='a'*32, evidence_control='b'*32, evidence='original')
        for route, tokens in ((build_route('codex_cli_visual_v1', 'gpt-6-astra'), 5000),
                              (build_route('codex_cli_v1', 'unlisted'), 5000000),
                              (build_route('claude_cli_v1', 'unlisted'), 1)):
            with self.subTest(adapter=route['adapter'], tokens=tokens):
                with self.assertRaises(ResearchError) as caught:
                    capacity.build_capacity_probe_request(route, 'JSON', packet, tokens)
                self.assertEqual(caught.exception.code, 'invalid_review_capacity_probe')

    def test_utf8_filler_measures_the_requested_size_inside_the_text_transport_cap(self):
        from research_harness import review_claude_transport as claude
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 10000), \
                patch.object(claude, 'INPUT_LIMIT_CHARACTERS', 10000):
            for adapter in ('codex_cli_v1', 'codex_cli_framed_v1', 'claude_cli_v1'):
                with self.subTest(adapter=adapter):
                    route = build_route(adapter, 'claude-opus-5-5' if adapter == 'claude_cli_v1' else 'gpt-6-astra')
                    packet = dict(allowed_control='a'*32, evidence_control='b'*32, evidence='')
                    request = capacity.build_capacity_probe_request(route, protocol.build_probe_prompt(route), packet, 20000)
                    measured = capacity.measure_request(route, request)
                    self.assertEqual(measured['tokens_upper'], 20000)
                    self.assertLessEqual(measured['characters'], 10000)
                    self.assertTrue(any(ord(char)>127 for char in packet['evidence']))
                    self.assertEqual(set(packet), {'allowed_control', 'evidence_control', 'evidence'})

    def test_capacity_evidence_never_crosses_endpoint_or_runtime_and_leaves_output_room(self):
        route = dict(build_route('openai_responses_v1', 'unlisted-model', output=8000),
                     runtime=build_capacity_runtime('openai_responses_v1', **dict(implementation_sha256='a'*64, input_contract='explicit-packet-only-v1')))
        sample = dict(id='probe', route_id='route', status='completed', completed_generation=True,
                      verified_input_tokens=30000, capacity_evidence_verified=True, usage=dict(input_tokens=30000, sends=1),
                      task=dict(kind='probe', characters=100), answer={}, recorded_revision=1)
        sample['capacity_evidence'] = dict(key=capacity.describe_capacity_key(route), input_tokens=30000)
        records = dict(review_route={'route': route}, review_route_probe={'probe': sample})
        profile = capacity.read_output_profile(records, route)
        self.assertEqual(capacity.describe_route_capacity(route, profile, records=records)['input_tokens']['value'], 22000)
        for changed in (dict(route, endpoint='https://other.test/responses'),
                        dict(route, runtime=build_capacity_runtime(route['adapter'], **dict(route['runtime'], implementation_sha256='b'*64)))):
            profile = capacity.read_output_profile(records, changed)
            self.assertEqual(capacity.describe_route_capacity(changed, profile, records=records)['input_tokens']['value'], 0)


class CapacityProbeTests(LiteratureCase):
    protocol = review_fixtures.ReviewProtocolTests.protocol
    route = review_fixtures.ReviewProtocolTests.route
    response = review_fixtures.ReviewProtocolTests.response
    records = review_fixtures.ReviewProtocolTests.records

    def build_sized_assignment(self, tokens, identifier, route_id='route'):
        length = tokens
        for _ in range(5):
            reference = self.artifacts.put(b'x'*length, 'text/plain')
            payload = dict(id=identifier, route_id=route_id, role='standalone', dossier_id=None,
                reviewer_id='reviewer', author_id='author', context=dict(artifact_refs=[reference]))
            packet = protocol.build_packet(self.records(), self.artifacts, None, 'standalone',
                'reviewer', **payload['context'])
            route = self.records()['review_route'][route_id]
            request = transport.request_body(route, protocol.canonical_prompt('standalone')['text'], packet)
            measured = capacity.measure_request(route, request)['tokens_upper']
            if measured == tokens:
                return payload
            length += tokens - measured
        self.fail('The synthetic request did not reach its exact byte measure.')

    def run_ordinary_probe(self, identifier='ordinary', route_id='route'):
        def send(route, request, credential):
            packet = json.loads(request['input'][0]['content'][len(transport.PACKET_LABEL):])
            response = self.response(dict(allowed_control=packet['allowed_control'],
                evidence_control=packet['evidence_control'], excluded_controls=[]))
            response['usage']['input_tokens'] = 500
            return response
        with patch.object(transport, 'send_request', side_effect=send) as sender:
            self.mutate(protocol.probe_route, dict(id=identifier, route_id=route_id), credential='fixture')
        self.assertEqual(sender.call_count, 1)

    def run_sized_probe(self, identifier='capacity', tokens=25000, maximum=32000, missing=False):
        def send(route, request, credential):
            if capacity.measure_request(route, request)['tokens_upper'] > maximum:
                raise ResearchError('review_provider_failure', 'The provider returned HTTP 400')
            packet = json.loads(request['input'][0]['content'][len(transport.PACKET_LABEL):])
            response = self.response(dict(allowed_control='missing' if missing else packet['allowed_control'],
                                          evidence_control=packet['evidence_control'], excluded_controls=[]))
            response['usage']['input_tokens'] = tokens
            return response
        with patch.object(transport, 'send_request', side_effect=send):
            return self.mutate(protocol.probe_route, dict(id=identifier, route_id='route',
                               capacity=dict(input_tokens=tokens)), credential='fixture')['result']

    def test_verified_probe_raises_limit_and_refusal_keeps_http_status_and_halves_next_size(self):
        self.route()
        self.run_ordinary_probe()
        payload = self.build_sized_assignment(60000, 'too-large')
        with patch.object(transport, 'send_request') as sender:
            with self.assertRaises(ResearchError) as caught:
                self.mutate(protocol.record_assignment, payload)
            sender.assert_not_called()
        self.assertEqual(caught.exception.details['then'][0]['steps'][0]['payload']['capacity']['input_tokens'], 150000)
        failed = self.run_sized_probe(tokens=150000)
        self.assertFalse(failed['capacity_verified'])
        route = self.records()['review_route']['route']
        self.assertEqual(capacity.describe_route_capacity(route, capacity.read_output_profile(self.records(), route), records=self.records(), artifacts=self.artifacts)['input_tokens']['value'], 0)
        self.assertEqual(capacity.select_capacity_probe_size(self.records(), route, 60000), 75000)
        self.assertEqual(failed['error']['http_status'], 400)
        with patch.object(transport, 'send_request') as sender:
            with self.assertRaises(ResearchError) as caught:
                self.mutate(protocol.record_assignment, payload)
            sender.assert_not_called()
        self.assertEqual(caught.exception.details['then'][0]['steps'][0]['payload']['capacity']['input_tokens'], 75000)
        result = self.run_sized_probe('accepted', 25000)
        self.assertTrue(result['capacity_verified'])
        limit = capacity.describe_route_capacity(route, capacity.read_output_profile(self.records(), route), records=self.records(), artifacts=self.artifacts)['input_tokens']
        self.assertEqual(limit['value'], 24000)
        self.assertEqual(limit['probe_id'], 'accepted')
        self.assertEqual(capacity.select_capacity_probe_size(self.records(), route, 60000), 61000)
        inside = self.build_sized_assignment(24000, 'inside')
        self.mutate(protocol.record_assignment, inside)
        def send_review(route, request, credential):
            measured = capacity.measure_request(route, request)['tokens_upper']
            self.assertEqual(measured, 24000)
            self.assertLessEqual(measured + route['configuration']['max_output_tokens'], 32000)
            response = self.response(dict(verdict='sound'))
            response['usage']['input_tokens'] = measured
            return response
        with patch.object(transport, 'send_request', side_effect=send_review):
            attempt = self.mutate(protocol.invoke_assignment,
                dict(id='inside-attempt', assignment_id=inside['id']), credential='fixture')['result']
        self.assertEqual(attempt['status'], 'completed')
        with patch.object(transport, 'send_request') as sender:
            with self.assertRaises(ResearchError) as caught:
                self.mutate(protocol.record_assignment, payload)
            sender.assert_not_called()
        self.assertEqual(caught.exception.details['then'][0]['steps'][0]['payload']['capacity']['input_tokens'], 61000)

    def test_another_endpoint_and_runtime_cannot_use_the_verified_probe(self):
        self.route()
        self.run_sized_probe('accepted', 200000, maximum=200000)
        records = self.records()
        route = records['review_route']['route']
        second = {key: value for key, value in route.items() if key in ('id', 'adapter', 'model', 'endpoint', 'configuration')}
        self.mutate(protocol.record_route, dict(second, id='endpoint-b', endpoint='https://second.example/responses'))
        self.run_ordinary_probe('endpoint-b-probe', 'endpoint-b')
        reference = self.artifacts.put(b'x'*60000, 'text/plain')
        payload = dict(id='assignment', role='standalone', dossier_id=None, reviewer_id='reviewer', author_id='author',
                       route_id='endpoint-b', context=dict(artifact_refs=[reference]))
        with patch.object(transport, 'send_request') as sender:
            with self.assertRaises(ResearchError) as caught:
                self.mutate(protocol.record_assignment, payload)
            sender.assert_not_called()
        self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')
        self.assertEqual(caught.exception.details['then'][0]['steps'][0]['payload']['capacity']['input_tokens'], 150000)
        records = self.records()
        self.assertEqual(capacity.describe_route_capacity(records['review_route']['endpoint-b'],
            capacity.read_output_profile(records, records['review_route']['endpoint-b']), records=records, artifacts=self.artifacts)['input_tokens']['value'], 0)
        self.assertEqual(capacity.describe_route_capacity(route,
            capacity.read_output_profile(records, route), records=records, artifacts=self.artifacts)['input_tokens']['value'], 199000)
        changed = dict(route['runtime'], implementation_sha256='b'*64)
        with patch.object(transport, 'runtime', return_value=changed):
            self.mutate(protocol.record_route, dict(second, id='another-runtime'))
            with patch.object(transport, 'send_request') as sender:
                with self.assertRaises(ResearchError) as caught:
                    self.mutate(protocol.record_assignment, dict(payload, route_id='another-runtime'))
                sender.assert_not_called()
        self.assertEqual(caught.exception.details['then'][0]['steps'][0]['payload']['capacity']['input_tokens'], 200000)
        self.mutate(protocol.record_assignment, dict(payload, route_id='route'))

    def test_incomplete_probe_cannot_borrow_an_earlier_probes_verified_capacity(self):
        self.route()
        self.run_sized_probe('accepted', 25000)
        with patch.object(protocol, '_observed', return_value=dict(
                status='failed', request=None, response=None, output=None)):
            result = self.mutate(protocol.probe_route, dict(id='incomplete', route_id='route',
                capacity=dict(input_tokens=10000)))['result']
        self.assertFalse(result['capacity_verified'])
        self.assertFalse(result['capacity_evidence_verified'])
        observe = protocol._observed
        def omit_runtime(*args, **kwargs):
            result = observe(*args, **kwargs)
            result.pop('runtime')
            return result
        with patch.object(protocol, '_observed', side_effect=omit_runtime):
            result = self.run_sized_probe('missing-binding', 10000)
        self.assertFalse(result['capacity_verified'])
        records = self.records()
        route = records['review_route']['route']
        self.assertEqual(capacity.read_output_profile(records, route)['verified_input_tokens'], 25000)
        self.assertEqual(capacity.select_capacity_probe_size(records, route, 60000), 5000)

    def test_second_probe_corrects_padding_using_the_first_actual_input_count(self):
        self.route()
        def send(route, request, credential):
            packet = json.loads(request['input'][0]['content'][len(transport.PACKET_LABEL):])
            response = self.response(dict(allowed_control=packet['allowed_control'], evidence_control=packet['evidence_control'], excluded_controls=[]))
            response['usage']['input_tokens'] = capacity.measure_request(route, request)['tokens_upper'] // 2
            return response
        with patch.object(transport, 'send_request', side_effect=send):
            first = self.mutate(protocol.probe_route, dict(id='first', route_id='route', capacity=dict(input_tokens=10000)), credential='fixture')['result']
            second = self.mutate(protocol.probe_route, dict(id='second', route_id='route', capacity=dict(input_tokens=12000)), credential='fixture')['result']
        self.assertTrue(first['capacity_verified'])
        self.assertTrue(second['capacity_verified'])
        self.assertEqual(second['usage']['input_tokens'], 12000)
        route = self.records()['review_route']['route']
        self.assertEqual(capacity.describe_route_capacity(route, capacity.read_output_profile(self.records(), route), records=self.records(), artifacts=self.artifacts)['input_tokens']['value'], 11000)

    def test_small_route_probe_does_not_replace_the_first_capacity_probe_default(self):
        self.route()
        def send(route, request, credential):
            packet = json.loads(request['input'][0]['content'][len(transport.PACKET_LABEL):])
            response = self.response(dict(allowed_control=packet['allowed_control'], evidence_control=packet['evidence_control'], excluded_controls=[]))
            response['usage']['input_tokens'] = 500
            return response
        with patch.object(transport, 'send_request', side_effect=send):
            self.mutate(protocol.probe_route, dict(id='ordinary', route_id='route'), credential='fixture')
        records = self.records()
        route = records['review_route']['route']
        self.assertEqual(capacity.describe_route_capacity(route, capacity.read_output_profile(records, route), records=records, artifacts=self.artifacts)['input_tokens']['value'], 0)
        self.assertEqual(capacity.select_capacity_probe_size(records, route, 60000), 150000)

    def test_short_answer_evidence_reserves_the_routes_full_output_before_assignment(self):
        restore_stored_review(self, 'stored-short-review-0.88.1.json.gz')
        records = self.records()
        historical = records['review_attempt']['historical-attempt']
        assignment = records['review_assignment'][historical['assignment_id']]
        self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, assignment, historical))
        self.assertEqual((historical['usage']['input_tokens'], historical['usage']['output_tokens']), (30000, 500))
        self.assertIsNone(historical['verified_input_tokens'])
        self.mutate(protocol.record_route, dict(id='route', adapter=transport.ADAPTER,
            endpoint='https://api.openai.com/v1/responses', model='fixture-model',
            configuration=dict(max_output_tokens=8000, timeout_seconds=5)))
        def send_probe(route, request, credential):
            packet = json.loads(request['input'][0]['content'][len(transport.PACKET_LABEL):])
            response = self.response(dict(allowed_control=packet['allowed_control'],
                evidence_control=packet['evidence_control'], excluded_controls=[]))
            response['usage']['input_tokens'] = 500
            return response
        with patch.object(transport, 'send_request', side_effect=send_probe):
            self.mutate(protocol.probe_route, dict(id='current-probe', route_id='route'), credential='fixture')
        self.assertEqual(capacity.describe_capacity_key(self.records()['review_route']['route']),
                         capacity.describe_capacity_key(records['review_route']['earlier']))
        with patch.object(transport, 'send_request') as send:
            with self.assertRaises(ResearchError) as caught:
                self.mutate(protocol.record_assignment, self.build_sized_assignment(30000, 'too-large'))
            send.assert_not_called()
        self.assertEqual(caught.exception.details['measure']['token_limit'], 22000)
        self.assertEqual(caught.exception.details['then'][0]['steps'][0]['payload']['capacity']['input_tokens'], 150000)
        self.mutate(protocol.record_assignment, self.build_sized_assignment(22000, 'inside'))
        def respond(route, request, credential):
            measured = capacity.measure_request(route, request)['tokens_upper']
            self.assertEqual(measured, 22000)
            self.assertLessEqual(measured + 8000, 32000)
            response = self.response(dict(verdict='sound', reason='x'*8000))
            response['usage'].update(input_tokens=measured, output_tokens=8000)
            return response
        with patch.object(transport, 'send_request', side_effect=respond):
            attempt = self.mutate(protocol.invoke_assignment, dict(id='inside-attempt', assignment_id='inside'))['result']
        self.assertEqual(attempt['status'], 'completed')
        self.assertEqual(attempt['usage']['output_tokens'], 8000)
        changed = dict(self.records()['review_route']['route'])
        changed['runtime'] = dict(changed['runtime'], implementation_sha256='b'*64)
        self.assertEqual(capacity.select_capacity_probe_size(self.records(), changed, 60000, artifacts=self.artifacts), 30000)

    def test_probe_missing_control_changes_no_limit(self):
        self.route()
        result = self.run_sized_probe(missing=True)
        self.assertFalse(result['capacity_verified'])
        route = self.records()['review_route']['route']
        self.assertEqual(capacity.describe_route_capacity(route, capacity.read_output_profile(self.records(), route), records=self.records(), artifacts=self.artifacts)['input_tokens']['value'], 0)

    def test_invalid_capacity_payloads_refuse_before_any_call(self):
        self.route()
        original = self.store.snapshot()
        for capacity_value in (None, {}, {'input_tokens': 0}, {'input_tokens': True},
                               {'input_tokens': '100'}, {'input_tokens': -1}, {'input_tokens': 100, 'unknown': 1}):
            with self.subTest(capacity=capacity_value), patch.object(transport, 'send_request') as sender:
                with self.assertRaises(ResearchError) as caught:
                    self.mutate(protocol.probe_route, dict(id='invalid', route_id='route', capacity=capacity_value))
                self.assertEqual(caught.exception.code, 'invalid_review_capacity_probe')
                sender.assert_not_called()
                self.assertEqual(self.store.snapshot(), original)

    def test_probe_without_capacity_keeps_original_request(self):
        self.route()
        with patch('research_harness.review_protocol.uuid.uuid4') as uuid, \
                patch.object(transport, 'send_request', return_value=self.response({})) as send, \
                patch.object(protocol, '_observed', wraps=protocol._observed) as observe:
            uuid.return_value.hex = 'a' * 32
            result = self.mutate(protocol.probe_route, dict(id='ordinary', route_id='route'), credential='fixture')['result']
        self.assertFalse(result['capacity_evidence_verified'])
        self.assertEqual(observe.call_args.kwargs, {})
        packet = dict(allowed_control='a'*32, evidence_control='a'*32,
                      evidence='Both control strings are permitted primary evidence.')
        prompt = ('Return JSON with allowed_control and evidence_control copied exactly from the packet, '
                  'and excluded_controls containing any other control strings you received, or an empty array. '
                  'Use only supplied evidence. This is an operational input-boundary probe.')
        self.assertEqual(send.call_args[0][1], transport.request_body(self.records()['review_route']['route'], prompt, packet))

    def test_record_check_authenticates_a_committed_capacity_probe(self):
        # record-check rebuilt the payload of a capacity probe without its capacity member and refused the
        # committed capture with review_invocation_unverified (record 20261119165105).
        self.route()
        self.assertTrue(self.run_sized_probe('capacity', 25000)['capacity_verified'])
        report = record_check.check_record(self.root, isolate=False)
        authentication = next(step for step in report['steps'] if step['step'] == 'authentication')
        self.assertEqual(authentication['status'], 'passed', authentication.get('error'))
        self.assertEqual(authentication['result']['captures'][identify_call('review.route_probe', 'capacity')],
                         {'authenticated': True, 'status': 'completed'})

    def test_resume_step_of_a_captured_capacity_probe_carries_its_capacity(self):
        # The resume step of a captured capacity probe dropped its capacity member, so the replay of its start was
        # refused with request_id_conflict (record 20261119165105).
        self.route()
        with patch.object(protocol, '_finish_call', side_effect=RuntimeError('Lost after capture')):
            with self.assertRaisesRegex(RuntimeError, 'Lost after capture'):
                self.run_sized_probe('capacity', 25000)
        call = dict(operation='review.route_probe', invocation_id='capacity')
        row = review_stop.describe_call(self.records(), call)
        self.assertEqual(row['state'], 'captured')
        self.assertEqual(row['step']['command'], 'review-probe')
        self.assertEqual(row['step']['payload'], dict(id='capacity', route_id='route', capacity=dict(input_tokens=25000)))
        with patch.object(transport, 'send_request', side_effect=AssertionError('The resume sends nothing')):
            receipt = cli.OPERATIONS['review-probe'](self.store, row['step']['payload'],
                request_id=row['step']['request_id'], expected_revision=self.store.revision)
        self.assertTrue(receipt['result']['capacity_verified'])
        self.assertEqual(review_stop.describe_call(self.records(), call)['state'], 'settled')


class CapacityEvidenceTests(unittest.TestCase):
    def test_a_larger_ordinary_review_preserves_the_verified_claude_capacity_probe(self):
        route = build_route('claude_cli_v1', 'unlisted-model', output=8000)
        probe = dict(id='probe', route_id='route', capacity=dict(input_tokens=300000),
            capacity_verified=True, capacity_evidence_verified=True, completed_generation=True,
            verified_input_tokens=300000, usage=dict(sends=1), task=dict(kind='probe', characters=100),
            answer={}, recorded_revision=1)
        attempt = dict(id='attempt', assignment_id='head', origin='instrumented',
            completed_generation=True, verified_input_tokens=400000, usage=dict(sends=1),
            task=dict(kind='single', characters=100), answer={}, recorded_revision=2)
        probe['capacity_evidence'] = dict(key=capacity.describe_capacity_key(route), input_tokens=300000)
        attempt['capacity_evidence'] = dict(key=capacity.describe_capacity_key(route), input_tokens=400000)
        records = dict(review_route=dict(route=route), review_assignment=dict(head=dict(route_id='route')),
                       review_route_probe=dict(probe=probe), review_attempt=dict(attempt=attempt))
        profile = capacity.read_output_profile(records, route)
        self.assertEqual(profile['verified_input_tokens'], 400000)
        self.assertEqual(profile['capacity_probe_id'], 'probe')
        self.assertEqual(capacity.describe_route_capacity(route, profile, records=records)['input_tokens']['value'], 392000)

    def test_codex_prior_applies_only_to_its_version_file_and_controls(self):
        route = dict(build_route('codex_cli_v1', 'gpt-6-astra', output=8000),
                     runtime=build_capacity_runtime('codex_cli_v1', **dict(version='fixture-cli-1', implementation_sha256='a'*64,
                                  controls=['isolated'], input_contract='fixture-contract')))
        prior = build_capacity_prior(route, 200000)
        for field, value in [('version', 'fixture-cli-2'), ('implementation_sha256', 'b'*64),
                             ('controls', ['changed'])]:
            with self.subTest(field=field), patch.object(capacity_evidence, 'CAPACITY_PRIORS', (prior,)):
                self.assertEqual(capacity.describe_route_capacity(route)['input_tokens']['value'], 192000)
                changed = dict(route, runtime=build_capacity_runtime(route['adapter'], **dict(route['runtime'], **{field: value})))
                self.assertEqual(capacity.describe_route_capacity(changed)['input_tokens']['value'], 0)
                self.assertEqual(capacity.select_capacity_probe_size(dict(review_route={'route': changed}), changed, 60000), 200000)
        with patch.object(capacity_evidence, 'CAPACITY_PRIORS', (dict(prior, key=None),)):
            self.assertEqual(capacity.describe_route_capacity(route)['input_tokens']['value'], 0)

    def test_first_probe_hint_requires_verified_evidence_under_another_key(self):
        route = dict(build_route('codex_cli_v1', 'unlisted', output=8000), runtime=build_capacity_runtime('codex_cli_v1', **dict(version='current')))
        own = build_capacity_prior(route, 200000)
        records = dict(review_route={'route': route})
        for prior in (own, dict(own, key=None)):
            with self.subTest(prior=prior), patch.object(capacity_evidence, 'CAPACITY_PRIORS', (prior,)):
                self.assertEqual(capacity.select_capacity_probe_size(records, route, 60000), 150000)
        earlier = dict(route, id='earlier', runtime=build_capacity_runtime(route['adapter'], **dict(version='earlier')))
        records['review_route']['earlier'] = earlier
        records['review_assignment'] = dict(head=dict(route_id='earlier'))
        sample = dict(id='accepted', assignment_id='head', origin='instrumented',
                      verified_input_tokens=180000, completed_generation=True, usage=dict(sends=1))
        for change in (dict(origin='imported'), dict(completed_generation=False), dict(usage=dict(sends=2))):
            records['review_attempt'] = dict(accepted=dict(sample, **change))
            self.assertEqual(capacity.select_capacity_probe_size(records, route, 60000), 150000)
        sample['capacity_evidence'] = dict(key=capacity.describe_capacity_key(earlier), input_tokens=180000)
        records['review_attempt'] = dict(accepted=sample)
        self.assertEqual(capacity.select_capacity_probe_size(records, route, 60000), 180000)
        records['review_attempt'] = {}
        records['review_output_correction'] = dict(accepted=sample)
        self.assertEqual(capacity.select_capacity_probe_size(records, route, 60000), 180000)

    def test_retried_compacted_and_unverified_probe_evidence_changes_no_limit(self):
        route = dict(build_route('codex_cli_v1', 'unlisted', output=8000), runtime=build_capacity_runtime('codex_cli_v1', **dict(version='fixture-cli')))
        sample = dict(id='probe', route_id='route', status='completed', completed_generation=True,
                      capacity=dict(input_tokens=25000), capacity_verified=True, capacity_evidence_verified=True,
                      verified_input_tokens=25000, usage=dict(sends=1),
                      task=dict(kind='probe', characters=100), answer={}, recorded_revision=1)
        for change in (dict(usage=dict(sends=2)), dict(completed_generation=False),
                       dict(capacity_verified=False), dict(capacity_evidence_verified=False)):
            with self.subTest(change=change):
                records = dict(review_route={'route': route}, review_route_probe={'probe': dict(sample, **change)})
                profile = capacity.read_output_profile(records, route)
                self.assertEqual(capacity.describe_route_capacity(route, profile, records=records)['input_tokens']['value'], 0)

    def test_probe_size_halves_each_refusal_and_then_uses_request_plus_reservation(self):
        route = dict(build_route('codex_cli_v1', 'gpt-6-astra', output=8000), runtime=build_capacity_runtime('codex_cli_v1', **dict(version='fixture-cli')))
        records = dict(review_route={'route': route}, review_route_probe={})
        for index, tokens in enumerate((200000, 100000, 50000, 25000)):
            records['review_route_probe'][str(index)] = dict(id=str(index), route_id='route',
                capacity=dict(input_tokens=tokens), capacity_verified=tokens == 25000, recorded_revision=index,
                completed_generation=tokens == 25000, capacity_evidence_verified=tokens == 25000,
                verified_input_tokens=tokens if tokens == 25000 else None, usage=dict(sends=1),
                task=dict(kind='probe', characters=100), answer={})
            if tokens == 25000:
                records['review_route_probe'][str(index)]['capacity_evidence'] = dict(
                    key=capacity.describe_capacity_key(route), input_tokens=25000)
            self.assertEqual(capacity.select_capacity_probe_size(records, route, 60000),
                             68000 if tokens == 25000 else tokens//2)
        profile = capacity.read_output_profile(records, route)
        self.assertEqual(capacity.describe_route_capacity(route, profile, records=records)['input_tokens']['value'], 17000)

class LocalCapacityProbeTests(LiteratureCase):
    def test_another_codex_runtime_probes_down_from_the_prior_then_records_the_boundary_review(self):
        import codex_route_fixtures
        from research_harness import review_codex_transport as codex, review_excerpts
        codex_route_fixtures.install_codex_runtime(self)
        earlier = dict(codex.runtime(), version='fixture-cli-1', implementation_sha256='a'*64,
                       controls=['isolated'])
        codex.runtime.return_value = earlier
        value = dict(id='earlier', adapter=codex.ADAPTER, model='gpt-6-astra', endpoint='codex://local',
                     configuration=dict(max_output_tokens=8000, timeout_seconds=5))
        self.mutate(protocol.record_route, value)
        prior_route = self.store.snapshot()['records']['review_route']['earlier']
        prior = build_capacity_prior(prior_route, 200000)

        def build_assignment(route_id, tokens, identifier):
            size = tokens
            for _ in range(5):
                text = 'é' * (size // 2) + ('x' if size % 2 else '')
                reference = self.artifacts.put(text.encode(), 'text/plain')
                payload = dict(id=identifier, role='standalone', dossier_id=None, route_id=route_id,
                    reviewer_id='reviewer', author_id='author', context=dict(artifact_refs=[reference]))
                records = self.store.snapshot()['records']
                packet = protocol.build_packet(records, self.artifacts, None, 'standalone',
                                               'reviewer', **payload['context'])
                request = transport.request_body(records['review_route'][route_id],
                                                protocol.canonical_prompt('standalone')['text'], packet)
                measured = capacity.measure_request(records['review_route'][route_id], request)['tokens_upper']
                if measured == tokens:
                    return payload
                size += tokens - measured
            self.fail('The synthetic native request did not reach its exact byte measure.')

        def send(route, request, credential):
            packet = review_excerpts.read_sent_packet(request)
            measured = capacity.measure_request(route, request)['tokens_upper']
            if measured > 32000:
                raise ResearchError('review_provider_failure', 'The synthetic runtime refuses inputs above 32000 tokens.')
            output = (dict(allowed_control=packet['allowed_control'], evidence_control=packet['evidence_control'],
                           excluded_controls=[]) if 'allowed_control' in packet else dict(verdict='sound'))
            response = codex_route_fixtures.build_codex_response(route, request, output)
            response['usage']['input_tokens'] = measured
            return response

        with patch.object(capacity_evidence, 'CAPACITY_PRIORS', (prior,)), \
                patch.object(transport, 'send_request', side_effect=send) as sender:
            self.assertEqual(capacity.describe_route_capacity(prior_route)['input_tokens']['value'], 192000)
            for index, (field, changed) in enumerate((('version', 'fixture-cli-2'),
                    ('implementation_sha256', 'b'*64), ('controls', ['changed']))):
                with self.subTest(field=field):
                    codex.runtime.return_value = dict(earlier, **{field: changed})
                    route_id = 'current-' + str(index)
                    self.mutate(protocol.record_route, dict(value, id=route_id))
                    self.mutate(protocol.probe_route, dict(id='ordinary-' + str(index), route_id=route_id))
                    records = self.store.snapshot()['records']
                    route = records['review_route'][route_id]
                    self.assertEqual(capacity.describe_route_capacity(route,
                        capacity.read_output_profile(records, route), records=records, artifacts=self.artifacts)['input_tokens']['value'], 0)
                    payload = build_assignment(route_id, 60000, 'too-large-' + str(index))
                    expected = 200000
                    for turn in range(4):
                        before = sender.call_count
                        with self.assertRaises(ResearchError) as caught:
                            self.mutate(protocol.record_assignment, payload)
                        self.assertEqual(sender.call_count, before)
                        self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')
                        step = caught.exception.details['then'][0]['steps'][0]
                        self.assertEqual(step['payload']['capacity']['input_tokens'], expected)
                        result = self.mutate(protocol.probe_route, dict(step['payload'],
                            id='sized-' + str(index) + '-' + str(turn)))['result']
                        self.assertEqual(result['capacity_verified'], expected == 25000)
                        expected //= 2
                    records = self.store.snapshot()['records']
                    self.assertEqual(capacity.describe_route_capacity(route,
                        capacity.read_output_profile(records, route), records=records, artifacts=self.artifacts)['input_tokens']['value'], 17000)
                    inside = build_assignment(route_id, 17000, 'inside-' + str(index))
                    self.mutate(protocol.record_assignment, inside)
                    attempt = self.mutate(protocol.invoke_assignment, dict(id='attempt-' + str(index),
                        assignment_id=inside['id']))['result']
                    self.assertEqual(attempt['status'], 'completed')
                    self.assertEqual(attempt['usage']['input_tokens'], 17000)
                    before = sender.call_count
                    with self.assertRaises(ResearchError) as caught:
                        self.mutate(protocol.record_assignment, payload)
                    self.assertEqual(sender.call_count, before)
                    self.assertEqual(caught.exception.details['then'][0]['steps'][0]['payload']['capacity']['input_tokens'], 68000)

    def test_each_text_cli_records_a_verified_sized_probe_with_stub_runtime(self):
        import codex_route_fixtures
        from claude_review_fixtures import RECORDED_CONTEXTS, build_recorded_runtime, build_recorded_response
        from research_harness import review_claude_transport as claude, review_excerpts
        codex_route_fixtures.install_codex_runtime(self)
        for index, adapter in enumerate(('codex_cli_v1', 'codex_cli_framed_v1', 'claude_cli_v1')):
            route = dict(build_route(adapter, 'claude-opus-5-5' if adapter == 'claude_cli_v1' else 'unlisted', output=1000),
                         id='route-' + str(index))
            def send(route, request, credential):
                packet = review_excerpts.read_sent_packet(request)
                output = dict(allowed_control=packet['allowed_control'], evidence_control=packet['evidence_control'], excluded_controls=[])
                if route['adapter'] == 'claude_cli_v1':
                    response = build_recorded_response(route, request, output, RECORDED_CONTEXTS[0])
                    response['execution']['system_events'] = dict(events=[], truncated=False)
                else:
                    response = codex_route_fixtures.build_codex_response(route, request, output)
                response['usage']['input_tokens'] = 5000
                return response
            with self.subTest(adapter=adapter), \
                    patch.object(claude, 'runtime', return_value=build_recorded_runtime(RECORDED_CONTEXTS[0][1])), \
                    patch.object(transport, 'send_request', side_effect=send):
                self.mutate(protocol.record_route, {key: value for key, value in route.items() if key != 'runtime'})
                result = self.mutate(protocol.probe_route, dict(id='probe-' + str(index), route_id=route['id'],
                                     capacity=dict(input_tokens=5000)))['result']
                self.assertTrue(result['capacity_verified'])
                request = json.loads(self.artifacts.read(result['request']))
                self.assertEqual(capacity.measure_request(route, request)['tokens_upper'], 5000)

    def test_compacted_or_repeated_native_probe_input_changes_no_capacity(self):
        import copy
        import codex_route_fixtures
        from research_harness import review_codex_transport as codex, review_excerpts
        from research_harness.evidence import digest
        codex_route_fixtures.install_codex_runtime(self)
        for index, defect in enumerate(('compacted', 'repeated')):
            route_id = 'route-' + str(index)
            self.mutate(protocol.record_route, dict(id=route_id, adapter=codex.ADAPTER,
                model='unlisted', endpoint='codex://local',
                configuration=dict(max_output_tokens=1000, timeout_seconds=5)))
            def send(route, request, credential):
                packet = review_excerpts.read_sent_packet(request)
                output = dict(allowed_control=packet['allowed_control'],
                              evidence_control=packet['evidence_control'], excluded_controls=[])
                response = codex_route_fixtures.build_codex_response(route, request, output)
                response['usage']['input_tokens'] = 5000
                events = copy.deepcopy(codex._rollout.return_value)
                events.append(dict(type='compacted') if defect == 'compacted' else copy.deepcopy(events[2]))
                evidence = codex.inspect_rollout(events, request, response['id'], '/fixture', [])
                evidence.update(inspected=True, route_digest=digest(route), runtime=codex.runtime(),
                                event_digest=digest(events))
                response['context_evidence'] = evidence
                codex._rollout.return_value = events
                return response
            with self.subTest(defect=defect), patch.object(transport, 'send_request', side_effect=send):
                result = self.mutate(protocol.probe_route, dict(id='probe-'+str(index), route_id=route_id,
                    capacity=dict(input_tokens=5000)))['result']
            self.assertFalse(result['capacity_verified'])
            records = self.store.snapshot()['records']
            route = records['review_route'][route_id]
            self.assertEqual(capacity.describe_route_capacity(route,
                capacity.read_output_profile(records, route), records=records, artifacts=self.artifacts)['input_tokens']['value'], 0)
