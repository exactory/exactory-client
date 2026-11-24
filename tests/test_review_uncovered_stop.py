"""An unsupported first assignment has a usable next step before any generation."""
import json
from unittest.mock import patch

from literature_fixtures import LiteratureCase
import test_research_review_protocol as fixtures
from research_harness import cli, review_parts as parts, review_protocol as protocol, review_route_capacity as capacity
from research_harness import review_transport as transport
from research_harness.errors import ResearchError
from test_review_capacity_steps import CapacityAnswers


class UncoveredReviewTests(CapacityAnswers, LiteratureCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    protocol = fixtures.ReviewProtocolTests.protocol
    seed = fixtures.ReviewProtocolTests.seed
    route = fixtures.ReviewProtocolTests.route
    response = fixtures.ReviewProtocolTests.response
    records = fixtures.ReviewProtocolTests.records

    def test_unverified_route_is_rejected_before_validating_its_runtime(self):
        from test_review_route_capacity import build_route
        route = dict(build_route(), id='legacy-route')
        records = dict(review_route={route['id']: route})
        assignment = dict(id='head', role='standalone', context={})
        with patch.object(protocol, '_require_route_runtime', side_effect=AssertionError('Unverified runtime validation')) as runtime, \
                patch.object(transport, 'runtime', return_value=dict(adapter='claude_cli_v1', fixture='legacy-route')):
            self.assertEqual(capacity.find_serving_routes(records, 'standalone', dict(evidence=[]),
                artifacts=self.artifacts, assignment=assignment, prompt='Return JSON.'), [])
        runtime.assert_not_called()

    def build_assignment(self, text='Evidence', pdf=False):
        evidence = [self.artifacts.put(text.encode(), 'text/plain')]
        if pdf:
            evidence.append(self.artifacts.put(b'%PDF-1.4 /Type /Page\n' + b'x'*100, 'application/pdf'))
        return dict(id='assignment', route_id='route', dossier_id=None, role='standalone',
                    reviewer_id='reviewer', author_id='author', context=dict(artifact_refs=evidence))

    def capture_stop(self, payload):
        before = self.store.snapshot()
        with patch.object(transport, 'send_request') as sender:
            with self.assertRaises(ResearchError) as caught:
                self.mutate(protocol.record_assignment, payload)
            sender.assert_not_called()
        self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')
        self.assertEqual(self.store.snapshot(), before)
        return caught.exception.details

    def test_unlisted_route_stops_before_call_then_probe_and_assignment_are_accepted(self):
        self.route()
        payload = self.build_assignment('x'*60000)
        details = self.capture_stop(payload)
        self.assertEqual(details['reason'], 'no_parts_delivery')
        self.assertEqual(details['capacity_source'], 'observed')
        steps = details['then'][0]['steps']
        self.assertEqual([step['command'] for step in steps], ['review-probe'])
        self.assertEqual(steps[0]['payload']['capacity']['input_tokens'], 150000)
        def respond(route, request, credential):
            packet = json.loads(request['input'][0]['content'][len(transport.PACKET_LABEL):])
            response = self.response(dict(allowed_control=packet['allowed_control'],
                                          evidence_control=packet['evidence_control'], excluded_controls=[]))
            response['usage']['input_tokens'] = 150000
            return response
        probe = dict(steps[0]['payload'], id='capacity')
        with patch.object(transport, 'send_request', side_effect=respond):
            self.mutate(cli.OPERATIONS[steps[0]['command']], probe)
        receipt = self.mutate(protocol.record_assignment, payload)
        self.assertEqual(receipt['result']['id'], 'assignment')

    def test_probe_answer_of_a_first_entry_names_the_same_command_again(self):
        """Repairs A11. The probe answer of a stopped first assignment says that the same command follows the probe;
        test_unlisted_route_stops_before_call_then_probe_and_assignment_are_accepted takes it."""
        from research_harness.review_plan_steps import CAPACITY_PROBE_CONDITION
        self.route()
        answer = self.capture_stop(self.build_assignment('x'*60000))['then'][0]
        self.assertEqual((answer['answer'], answer['condition']), ('Probe this route', CAPACITY_PROBE_CONDITION))

    def test_pdf_attachment_stops_and_text_route_completes_same_review(self):
        from claude_review_fixtures import RECORDED_CONTEXTS, build_recorded_response, build_recorded_runtime
        from research_harness import review_claude_transport as claude
        self.route()
        # A documented HTTPS model isolates the binary refusal from unknown model capacity.
        self.mutate(protocol.record_route, dict(id='pdf-route', adapter=transport.ADAPTER,
            endpoint='https://api.openai.com/v1/responses', model='gpt-6-astra',
            configuration=dict(max_output_tokens=1000, timeout_seconds=5)))
        def respond(route, request, credential):
            packet = json.loads(request['input'][0]['content'][len(transport.PACKET_LABEL):])
            output = (dict(allowed_control=packet['allowed_control'], evidence_control=packet['evidence_control'], excluded_controls=[])
                      if 'allowed_control' in packet else dict(verdict='sound'))
            return build_recorded_response(route, request, output, RECORDED_CONTEXTS[0])
        with patch.object(claude, 'runtime', return_value=build_recorded_runtime(RECORDED_CONTEXTS[0][1])), \
                patch.object(transport, 'send_request', side_effect=respond):
            self.mutate(protocol.record_route, dict(id='text-route', adapter='claude_cli_v1',
                endpoint='claude://local', model='claude-opus-5-5',
                configuration=dict(max_output_tokens=20000, timeout_seconds=5, effort='high')))
            self.mutate(protocol.probe_route, dict(id='text-probe', route_id='text-route'))
            payload = dict(self.build_assignment(pdf=True), route_id='pdf-route')
            details = self.capture_stop(payload)
            self.assertEqual(details['reason'], 'binary_input_without_bound')
            self.assertEqual([row['route_id'] for row in details['serving_routes']], ['text-route'])
            step = next(row for row in details['then'] if row['answer'] == 'Use a serving route')['steps'][0]
            self.mutate(cli.OPERATIONS[step['command']], step['payload'])
            self.mutate(protocol.invoke_assignment, dict(id='attempt', assignment_id='assignment'))
        records = self.records()
        self.assertTrue(protocol.assignment_state(records, self.artifacts, 'assignment')['ready'])
        request = json.loads(self.artifacts.read(records['review_attempt']['attempt']['request']))
        self.assertIn('bytes-omitted-on-text-route', json.dumps(request))

    def test_page_inspection_without_serving_route_reports_outside_solved_part(self):
        self.route()
        payload = self.build_assignment()
        packet = dict(role='standalone', visual_source_delivery=dict(pages=[dict(image={})]))
        request = transport.request_body(self.records()['review_route']['route'], 'JSON', dict(text='x'*100))
        with self.assertRaises(ResearchError) as caught:
            capacity.require_covered_assignment(self.records(), self.artifacts, payload, packet, 'JSON', request)
        self.assertEqual(caught.exception.details['serving_routes'], [])
        self.assertIn('outside', caught.exception.details['coverage'])
        self.assertIn('closing_unit', caught.exception.details)

    def test_assessment_and_status_share_per_role_unsolved_list(self):
        self.route()
        report = protocol.describe_route_report(self.records(), 'route')
        rows = report['outside_solved_part']
        self.assertIn('manuscript', rows)
        self.assertIn('standalone', rows)
        self.assertEqual({row['unit'] for row in rows['standalone']},
                         {'https-series', 'visual-series', 'paper-series', None})
        status = cli.status_report(self.store)
        self.assertEqual(status['review']['outside_solved_part'], rows)

    def test_large_body_without_text_names_capture_then_a_recorded_review(self):
        self.mutate(protocol.record_route, dict(id='route', adapter=transport.ADAPTER,
            endpoint='https://api.openai.com/v1/responses', model='gpt-6-astra',
            configuration=dict(max_output_tokens=1000, timeout_seconds=5)))
        fixtures.ReviewProtocolTests.run_probe(self)
        original = self.artifacts.put(b'\xff'*700000, 'application/octet-stream')
        self.seed('execution', 'execution', dict(id='execution', payload=dict(outputs=[dict(artifact=original)])))
        payload = dict(self.build_assignment(), context=dict(artifact_refs=[original]))
        details = self.capture_stop(payload)
        self.assertEqual(details['reason'], 'body_without_text_form')
        step = details['then'][0]['steps'][0]
        self.assertEqual(step['command'], 'artifact')
        (self.root / 'capture.txt').write_text('A faithful text form of the synthetic body.')
        captured = self.mutate(cli.OPERATIONS[step['command']],
            dict(step['payload'], id='text-form', path='capture.txt'))['result']['artifact']
        resumed = details['then'][0]['steps'][1]
        resumed['payload']['context']['artifact_refs'] = [captured]
        self.mutate(cli.OPERATIONS[resumed['command']], resumed['payload'])
        with patch.object(transport, 'send_request', return_value=self.response(dict(verdict='sound'))):
            self.mutate(protocol.invoke_assignment, dict(id='attempt', assignment_id='assignment'), credential='fixture')
        self.assertTrue(protocol.assignment_state(self.records(), self.artifacts, 'assignment')['ready'])

    def test_serving_parts_fit_the_real_frame_without_writing_artifacts(self):
        released = patch.object(parts, 'NEW_SERIES_PROTOCOLS', (parts.PROTOCOL,))
        released.start()
        self.addCleanup(released.stop)
        from claude_review_fixtures import RECORDED_CONTEXTS, build_recorded_response, build_recorded_runtime
        from research_harness import review_claude_transport as claude
        def respond(route, request, credential):
            packet = json.loads(request['input'][0]['content'][len(transport.PACKET_LABEL):])
            output = dict(allowed_control=packet['allowed_control'], evidence_control=packet['evidence_control'], excluded_controls=[])
            return build_recorded_response(route, request, output, RECORDED_CONTEXTS[0])
        with patch.object(claude, 'runtime', return_value=build_recorded_runtime(RECORDED_CONTEXTS[0][1])), \
                patch.object(transport, 'send_request', side_effect=respond):
            self.mutate(protocol.record_route, dict(id='route', adapter='claude_cli_v1', endpoint='claude://local',
                model='claude-opus-5-5', configuration=dict(max_output_tokens=128000, timeout_seconds=5, effort='high')))
            self.mutate(protocol.probe_route, dict(id='probe', route_id='route'))
        from research_harness.evidence import digest
        packet = dict(role='verification', paper=dict(kind='verification', target='synthetic-paper',
                      sources=dict(work=[dict(id='source', description='x'*300000)])), evidence=[])
        packet['digest'] = digest(packet)
        assignment = dict(self.build_assignment(), role='verification')
        before = set(self.root.rglob('*'))
        with patch.object(claude, 'INPUT_LIMIT_CHARACTERS', 200000), \
                patch.object(claude, 'runtime', return_value=build_recorded_runtime(RECORDED_CONTEXTS[0][1])):
            serving = capacity.find_serving_routes(self.records(), 'verification', packet, artifacts=self.artifacts,
                assignment=assignment, prompt=protocol.canonical_prompt('verification'))
            self.assertEqual([row['delivery'] for row in serving], ['parts'])
            packet['frame'] = 'x'*300000
            self.assertEqual(capacity.find_serving_routes(self.records(), 'verification', packet, artifacts=self.artifacts,
                assignment=assignment, prompt=protocol.canonical_prompt('verification')), [])
        self.assertEqual(set(self.root.rglob('*')), before)

    def test_combined_https_image_input_is_recorded_or_stops_before_a_call(self):
        from research_harness import review_route_capacity
        from test_research_codex_visual_transport import png
        self.mutate(protocol.record_route, dict(id='route', adapter=transport.ADAPTER,
            endpoint='https://api.openai.com/v1/responses', model='gpt-6-astra',
            configuration=dict(max_output_tokens=1000, timeout_seconds=5)))
        fixtures.ReviewProtocolTests.run_probe(self)
        images = [self.artifacts.put(png(value), 'image/png') for value in range(6)]
        text = self.artifacts.put(b'x'*40000, 'text/plain')
        payload = dict(self.build_assignment(), context=dict(artifact_refs=[text, images[0]]))
        route = self.records()['review_route']['route']
        packet = protocol.build_packet(self.records(), self.artifacts, None, 'standalone', 'reviewer', **payload['context'])
        prompt = protocol.canonical_prompt('standalone')
        measured = capacity.measure_request(route, transport.request_body(route, prompt['text'], packet))
        limit = measured['text_tokens_upper'] + measured['binary_tokens_upper'] + 3000
        self.assertGreater(measured['text_tokens_upper'], limit / 2)
        models = tuple((row[0], limit + 1000, row[2], row[3], row[4]) if row[0] == 'gpt-6-astra' else row
                       for row in review_route_capacity.MODEL_LIMITS)
        with patch.object(review_route_capacity, 'MODEL_LIMITS', models):
            self.mutate(protocol.record_assignment, payload)
            response = dict(self.response(dict(verdict='sound')), model='gpt-6-astra')
            with patch.object(transport, 'send_request', return_value=response):
                self.mutate(protocol.invoke_assignment, dict(id='attempt', assignment_id='assignment'), credential='fixture')
            self.assertTrue(protocol.assignment_state(self.records(), self.artifacts, 'assignment')['ready'])
            details = self.capture_stop(dict(payload, id='oversized', context=dict(artifact_refs=[text] + images)))
        self.assertEqual(details['reason'], 'binary_input_over_measure')
        self.assertGreater(details['measure']['tokens_upper'], details['measure']['token_limit'])

    def test_equal_page_pdfs_stop_before_call_with_the_same_estimate(self):
        from test_review_binary_capacity import build_pdf
        self.mutate(protocol.record_route, dict(id='route', adapter=transport.ADAPTER,
            endpoint='https://api.openai.com/v1/responses', model='gpt-6-astra',
            configuration=dict(max_output_tokens=1000, timeout_seconds=5)))
        estimates = []
        for index, data in enumerate((build_pdf(100), build_pdf(10000, image=True))):
            reference = self.artifacts.put(data, 'application/pdf')
            payload = dict(self.build_assignment(), id='pdf-'+str(index),
                           context=dict(artifact_refs=[reference]))
            details = self.capture_stop(payload)
            self.assertEqual(details['reason'], 'binary_input_without_bound')
            estimates.append(details['measure']['binary_inputs'][0]['estimate'])
        self.assertEqual(estimates[0], estimates[1])

    def test_a_saved_probe_does_not_make_a_stale_runtime_a_serving_route(self):
        self.route()
        fixtures.ReviewProtocolTests.run_probe(self)
        payload = self.build_assignment()
        records = self.records()
        packet = protocol.build_packet(records, self.artifacts, None, 'standalone', 'reviewer', **payload['context'])
        options = dict(artifacts=self.artifacts, assignment=payload, prompt=protocol.canonical_prompt('standalone'))
        self.assertEqual([row['route_id'] for row in capacity.find_serving_routes(records, 'standalone', packet, **options)], ['route'])
        records['review_route']['route']['runtime']['implementation_sha256'] = '0'*64
        self.assertEqual(capacity.find_serving_routes(records, 'standalone', packet, **options), [])

    def test_conditional_route_steps_record_a_new_head_of_the_preserved_bar_slot(self):
        import test_research_context_repair_integration as repair_fixtures
        fixture = self.case = repair_fixtures.ContextRepairIntegrationTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        def respond(route, request, credential):
            packet = json.loads(request['input'][0]['content'][len(transport.PACKET_LABEL):])
            response = fixture.provider_response(dict(allowed_control=packet['allowed_control'],
                evidence_control=packet['evidence_control'], excluded_controls=[]))
            response['usage']['input_tokens'] = 500 if route['id'] == 'tiny' else 23500
            return response
        with patch.object(transport, 'send_request', side_effect=respond):
            for name in ('tiny', 'wide'):
                fixture.mutate(protocol.record_route, dict(id=name, adapter=transport.ADAPTER,
                    endpoint='https://'+name+'.example/responses', model='fixture-model',
                    configuration=dict(max_output_tokens=1000, timeout_seconds=5)))
                fixture.mutate(protocol.probe_route, dict(id=name+'-probe', route_id=name))
            payload = dict(id='stopped-slate', route_id='tiny', dossier_id='dossier-1',
                role='slate', reviewer_id='reviewer-a', author_id='author', context={})
            revision = fixture.store.revision
            with self.assertRaises(ResearchError) as caught:
                fixture.mutate(protocol.record_assignment, payload)
            self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')
            self.assertEqual(fixture.store.revision, revision)
            details = caught.exception.details
            self.assertEqual(details['serving_routes'], [])
            answer = next(row for row in details['then'] if row.get('route_id') == 'wide')
            self.assertTrue(answer['condition'])
            steps = answer['steps']
            self.assertEqual([step['command'] for step in steps], ['review-context', 'review-probe'])
            self.assertEqual(steps[1]['payload']['route_id'], 'wide')

            def check_repair(assignment):
                if assignment['route_id'] == 'wide':
                    packet = json.loads(fixture.artifacts.read(assignment['packet']))
                    self.assertTrue(packet['scientific_history'])
                    self.assertTrue(packet['historical_reviews'])

            self.take_answers(details, reviewer='repair-reviewer', observed=150000, renewal_observed=23500)
            accepted = self.take_probe_then_next_stop(details, 'wide', payload, reviewer='repair-reviewer',
                                                      observed=150000, renewal_observed=23500)
            check_repair(accepted)
        self.assertEqual(accepted['slot_id'], 'assignment-bar-a')
        self.assertEqual(accepted['context']['context_repair']['assignment_id'], 'assignment-bar-a')

    def test_a_route_that_fits_only_the_old_packet_is_not_a_serving_repair_route(self):
        import test_research_context_repair_integration as repair_fixtures
        fixture = repair_fixtures.ContextRepairIntegrationTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        records = fixture.store.snapshot()['records']
        parent = records['review_assignment']['assignment-bar-a']
        assignment = dict(parent, id='repair-head', context=dict(prior_assignment_id=parent['id']))
        packet = dict(evidence=[])
        prompt = protocol.canonical_prompt('bar')
        old_measure = capacity.measure_request(records['review_route']['route'],
            transport.request_body(records['review_route']['route'], prompt['text'], packet))
        limit = old_measure['tokens_upper'] + 1
        fixture.mutate(protocol.record_route, dict(id='between', adapter=transport.ADAPTER,
            endpoint='https://between.example/responses', model='fixture-model',
            configuration=dict(max_output_tokens=1000, timeout_seconds=5)))
        def respond(route, request, credential):
            supplied = json.loads(request['input'][0]['content'][len(transport.PACKET_LABEL):])
            response = fixture.provider_response(dict(allowed_control=supplied['allowed_control'],
                evidence_control=supplied['evidence_control'], excluded_controls=[]))
            response['usage']['input_tokens'] = limit + 1000
            return response
        with patch.object(transport, 'send_request', side_effect=respond):
            fixture.mutate(protocol.probe_route, dict(id='between-probe', route_id='between'))
        records = fixture.store.snapshot()['records']
        route = records['review_route']['between']
        request = capacity._build_serving_repair(records, fixture.artifacts, assignment, parent, route)
        self.assertGreater(capacity.measure_request(route, request)['tokens_upper'], limit)
        before = set(fixture.root.rglob('*'))
        serving = capacity.find_serving_routes(records, 'bar', packet, artifacts=fixture.artifacts,
            assignment=assignment, prompt=prompt)
        self.assertNotIn('between', [row['route_id'] for row in serving])
        self.assertEqual(set(fixture.root.rglob('*')), before)

    def test_a_staged_focused_parent_cannot_borrow_the_ordinary_packet_as_a_serving_repair(self):
        self.route()
        fixtures.ReviewProtocolTests.run_probe(self)
        records = self.records()
        parent = dict(id='focused-parent', role='adjudicator', route_id='route', dossier_id=None,
            reviewer_id='reviewer', author_id='author', context={}, staged_review=dict(kind='controller'))
        records['review_assignment'] = {parent['id']: parent}
        assignment = dict(parent, id='refused', context=dict(prior_assignment_id=parent['id']))
        serving = capacity.find_serving_routes(records, 'adjudicator', dict(evidence=[]),
            artifacts=self.artifacts, assignment=assignment, prompt=protocol.canonical_prompt('adjudicator'))
        self.assertEqual(serving, [])

    def prepare_native_capacity_route(self, output_tokens):
        import codex_route_fixtures
        from research_harness import review_excerpts
        codex_route_fixtures.install_codex_runtime(self)
        self.mutate(protocol.record_route, dict(id='route', adapter='codex_cli_v1', model='gpt-6-astra',
            endpoint='codex://local', configuration=dict(max_output_tokens=output_tokens, timeout_seconds=5)))
        def send(route, request, credential):
            packet = review_excerpts.read_sent_packet(request)
            response = codex_route_fixtures.build_codex_response(route, request,
                dict(allowed_control=packet['allowed_control'], evidence_control=packet['evidence_control'], excluded_controls=[]))
            response['usage']['input_tokens'] = capacity.measure_request(route, request)['tokens_upper']
            return response
        with patch.object(transport, 'send_request', side_effect=send):
            self.mutate(protocol.probe_route, dict(id='first-capacity', route_id='route', capacity=dict(input_tokens=10000)))
        return send

    def test_the_prescribed_probe_uses_utf8_to_fit_then_records_the_same_assignment(self):
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 20000):
            send = self.prepare_native_capacity_route(8000)
            payload = self.build_assignment('x'*12000)
            details = self.capture_stop(payload)
            steps = details['then'][0]['steps']
            size = steps[0]['payload']['capacity']['input_tokens']
            self.assertEqual(size, details['measure']['tokens_upper']+8000)
            self.assertGreater(size, 10000)
            with patch.object(transport, 'send_request', side_effect=send):
                probe = self.mutate(cli.OPERATIONS[steps[0]['command']], dict(steps[0]['payload'], id='larger-capacity'))['result']
            self.assertTrue(probe['capacity_verified'])
            self.assertTrue(any(ord(char)>127 for char in probe['positive_controls']['evidence']))
            self.assertEqual(len(steps), 1)
            accepted = self.mutate(protocol.record_assignment, payload)['result']
        self.assertEqual(accepted['id'], payload['id'])
        self.assertEqual(accepted['context'], payload['context'])

    def test_an_unrepresentable_probe_retains_the_prescribed_size_without_an_invalid_step(self):
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 20000):
            self.prepare_native_capacity_route(10000000)
            details = self.capture_stop(self.build_assignment('x'*12000))
        self.assertEqual(details['probe_unavailable']['input_tokens'], details['measure']['tokens_upper']+10000000)
        self.assertEqual(details['probe_unavailable']['code'], 'invalid_review_capacity_probe')
        self.assertEqual(details['probe_unavailable']['details']['character_limit'], 20000)
        self.assertEqual(details['then'], [])
        self.assertEqual(details['serving_routes'], [])
        self.assertIn('outside', details['coverage'])

    def test_route_change_uses_the_preserved_slots_repair_steps(self):
        from research_harness.review_plan_steps import describe_capacity_steps
        route = dict(id='old', adapter=transport.ADAPTER, model='unlisted', endpoint='https://old.example/responses',
                     configuration=dict(max_output_tokens=1000, timeout_seconds=5))
        serving = dict(route, id='serving', endpoint='https://serving.example/responses')
        parent = dict(id='bar', role='bar', route_id='old', slot_id='slot', dossier_id=None,
                      reviewer_id='reviewer', author_id='author', context={},
                      context_state=dict(context_status='verified_for_route'))
        records = dict(review_route={'old': route, 'serving': serving}, review_assignment={'bar': parent})
        assignment = dict(parent, id='stopped-result', role='result')
        steps = describe_capacity_steps(records, self.artifacts, assignment, [dict(route_id='serving')],
                                       packet=dict(own_bars=[dict(assignment_id='bar')]))[0]['steps']
        self.assertEqual([step['command'] for step in steps], ['review-context', 'review-probe'])
        self.assertEqual(steps[0]['payload']['assignment_id'], 'bar')
        self.assertEqual(steps[1]['payload']['route_id'], 'serving')
