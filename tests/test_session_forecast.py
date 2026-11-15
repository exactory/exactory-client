"""Session forecasts stop expensive pending delivery before transport dispatch."""

import copy
import json
import unittest
from unittest.mock import patch

from research_harness import cli, review_excerpts, review_route_capacity as capacity, review_protocol as protocol
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.review_continuation import plan_next
from research_harness.review_series import run_series
from research_harness.stop_classes import STOP_REGISTRY
import test_review_continuation as fixtures


FORECAST_CALL_ERROR = 'The forecast must refuse before sending another request.'


class SessionForecastTests(unittest.TestCase):
    def setUp(self):
        build_response = fixtures.fixtures.ProblemReviewTests.build_provider_response
        def build_bounded_response(case, output):
            response = build_response(case, output)
            if 'allowed_control' in output:
                # This backend verifies 151000 input tokens, leaving the original
                # 150000-token session boundary after its output reservation.
                response['usage']['input_tokens'] = 151000
            return response
        bounded = patch.object(fixtures.fixtures.ProblemReviewTests, 'build_provider_response',
                               build_bounded_response)
        bounded.start()
        self.addCleanup(bounded.stop)
        self.fixture = fixtures.ContinuationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.case = self.fixture.case
        self.assignment = self.fixture.assignment
        from visual_capacity_fixtures import install_bounded_binary_backend
        install_bounded_binary_backend(self, [self.case.read_records()['review_route']['route']], tokens_upper=512)

    def request_texts(self, sizes, *, html=False, escaped=False):
        requests = []
        for index, size in enumerate(sizes):
            version = self.case.metadata(81000 + index)
            # Different parts have different digests, as real extracted texts do.
            segment = (117000 if index == 0 and size == 2493595 else
                       review_excerpts.TURN_FULLTEXT_CHARACTERS)
            content = ''.join(str(part).ljust(segment - 1, ' ') + '\n'
                              for part in range((size + segment - 1) // segment))[:size]
            if escaped:
                content = ('\"\\' * ((size + 1) // 2))[:size]
            if html:
                content = str(index) + 'a' * (size - 1)
                self.case.capture(version, body=content, abstract=False)
            else:
                self.case.capture(version, pdf_text=content)
            requests.append(dict(id='source-' + str(index), kind='source', version_id=version, depth='fulltext'))
        # The initial assignment's source inventory predates these sources. A new
        # fixture packet is built before the observed request, with the same slot.
        packet = protocol._read_json(self.case.artifacts, self.assignment['packet'])
        from research_harness import bar_sources
        packet['source_inventory'] = bar_sources.inventory(self.case.read_records(), self.case.artifacts)
        packet.pop('digest', None)
        packet['digest'] = digest(packet)
        self.assignment = dict(self.assignment, packet=self.case.artifacts.put(
            json.dumps(packet, sort_keys=True, ensure_ascii=False).encode(), 'application/json'), packet_digest=packet['digest'])
        self.case.put('review_assignment', self.assignment['id'], self.assignment)
        self.fixture.assignment = self.assignment
        output = self.case.build_review_output('bar')
        output.update(source_requests=requests)
        output['value']['status'] = 'unresolved'
        self.fixture.invoke(output)
        return requests

    def plan(self):
        return plan_next(self.case.read_records(), self.case.artifacts, self.assignment['id'])

    def test_forecast_uses_keyed_input_evidence_instead_of_output_profile(self):
        records = self.case.read_records()
        forecast = capacity.describe_session_forecast(records, self.case.artifacts, self.assignment)
        self.assertTrue(forecast['request']['fits'])
        route = records['review_route'][self.assignment['route_id']]
        profile = capacity.read_output_profile(records, route)
        profile['verified_input_tokens'] = 1000000000
        route['runtime'] = dict(route['runtime'], implementation_sha256='b' * 64)
        with patch.object(capacity, 'read_output_profile', return_value=profile):
            forecast = capacity.describe_session_forecast(records, self.case.artifacts, self.assignment)
        self.assertEqual(forecast['request']['token_limit'], 0)
        self.assertFalse(forecast['request']['fits'])

    def test_four_padded_texts_stop_with_capture_details_before_another_call(self):
        requested = self.request_texts([2493595, 137153, 3947312, 19073281])
        before = self.case.store.revision
        lines = []
        with patch('research_harness.review_transport.send_request',
                   side_effect=AssertionError(FORECAST_CALL_ERROR)) as send:
            self.assertEqual(run_series(self.case.store, self.assignment['id'], emit=lines.append), 3)
        send.assert_not_called()
        stop = lines[-1]
        self.assertEqual(stop['code'], 'review_pending_delivery_over_plan')
        self.assertEqual(stop['stop_class'], 'decision')
        forecast = stop['capacity_forecast']
        self.assertEqual(forecast['pending_parts'], 216)
        self.assertEqual([row['version_id'] for row in forecast['pending_sources']],
                         [row['version_id'] for row in requested])
        self.assertEqual(forecast['pending_characters'], 25651341)
        self.assertEqual(forecast['pending_turn_limit'], capacity.SESSION_PENDING_TURN_LIMIT)
        capture = self.case.read_records()['work'][requested[0]['version_id']]['fulltexts'][0]
        self.assertEqual(forecast['pending_sources'][0]['whitespace_fraction'], capture['extraction']['whitespace_fraction'])
        self.assertEqual(forecast['pending_sources'][0]['expansion_ratio'], capture['extraction']['expansion_ratio'])
        self.assertEqual(stop['details']['decision']['then'][0]['steps'][0]['command'], 'select-main-capture')
        self.assertEqual(STOP_REGISTRY[stop['code']]['owner'], 'D1')
        self.assertEqual(len(stop['details']['decision']['then']), 1)
        self.assertGreaterEqual(self.case.store.revision, before)

    def test_short_session_continues_and_forecast_does_not_change_stored_bytes(self):
        self.request_texts([120100])
        records = self.case.read_records()
        packet = self.case.artifacts.read(self.assignment['packet'])
        request = protocol.build_assignment_request(records, self.case.artifacts, self.assignment)
        plan = self.plan()
        self.assertEqual(plan['state'], 'continue')
        forecast = plan['capacity_forecast']
        self.assertEqual((forecast['pending_parts'], forecast['pending_turns']), (2, 2))
        self.assertEqual(forecast['request'], capacity.measure_request(records['review_route']['route'], request,
            records=records, artifacts=self.case.artifacts))
        self.assertEqual(self.case.artifacts.read(self.assignment['packet']), packet)
        self.assertEqual(protocol.build_assignment_request(self.case.read_records(), self.case.artifacts, self.assignment), request)
        self.assertEqual(digest(self.case.read_records()['review_assignment'][self.assignment['id']]), digest(self.assignment))
        self.fixture.execute(plan)
        next_plan = self.plan()
        self.assertEqual(next_plan['state'], 'run')
        self.assertEqual(next_plan['capacity_forecast']['pending_parts'], 2)

    def test_projection_over_capacity_stops_even_below_turn_limit(self):
        self.request_texts([180000])
        plan = self.plan()
        self.assertEqual(plan['code'], 'review_pending_delivery_over_plan')
        self.assertEqual(plan['capacity_forecast']['reason'], 'projected_input')
        self.assertTrue(plan['capacity_forecast']['projected_final']['estimated'])

    def test_non_session_has_no_session_forecast(self):
        assignment = dict(self.assignment, role='native_math')
        self.assertIsNone(capacity.describe_session_forecast(self.case.read_records(), self.case.artifacts, assignment))
        unavailable = {key: value for key, value in self.assignment.items() if key != 'packet'}
        self.assertIsNone(capacity.describe_session_forecast(self.case.read_records(), self.case.artifacts, unavailable))

    def test_capture_steps_execute_to_a_recorded_review_without_an_extra_decision(self):
        for entry in ('plan', 'direct', 'delivered'):
            with self.subTest(entry=entry):
                scenario = self
                if entry != 'plan':
                    scenario = SessionForecastTests()
                    scenario.setUp()
                    self.addCleanup(scenario.doCleanups)
                if entry == 'delivered':
                    # Reproduce a paid partial delivery under an earlier, larger
                    # input allowance, then restore the current route capacity.
                    from capacity_evidence_fixtures import build_capacity_prior
                    from research_harness import review_capacity_evidence as evidence
                    route = scenario.case.read_records()['review_route']['route']
                    priors = tuple(evidence.read_capacity_priors()) + (
                        build_capacity_prior(route, 3001000, identifier='earlier-session-capacity'),)
                    with patch.object(evidence, 'CAPACITY_PRIORS', priors):
                        scenario.request_texts([2200000])
                        scenario.fixture.execute(scenario.plan())
                        old_head = scenario.plan()['head_id']
                        output = scenario.case.build_review_output('bar')
                        output['value']['status'] = 'unresolved'
                        scenario.case.mutate(protocol.invoke_assignment,
                            dict(id='earlier-part', assignment_id=old_head), credential='fixture')
                else:
                    scenario.request_texts([2200000])
                stopped = scenario.refuse_direct_run() if entry == 'direct' else scenario.plan()
                head_id = stopped['head_id']
                self.assertEqual(stopped['code'], 'review_pending_delivery_over_plan')
                steps = stopped['details']['decision']['then'][0]['steps']
                step = steps[0]
                version = step['payload']['version_id']
                from literature_fixtures import acquire_fulltext, client
                data = b'%PDF-1.4\n% Authored fixture for injected extraction.\n%%EOF'
                http, _, _ = client([(200, {'Content-Type': 'application/pdf'}, data)], max_retries=0)
                compact = acquire_fulltext(scenario.case.store, version, 'https://arxiv.org/pdf/' + version[6:], http=http,
                    expected_revision=scenario.case.store.revision, request_id='compact-capture',
                    extraction_options=dict(layout=False), extractor=lambda data: dict(status='extracted',
                        text='A compact source establishes the finite example. References: none.'))['capture']
                scenario.case.mutate(cli.OPERATIONS[step['command']],
                    dict(step['payload'], source_id=compact['source_id'], reason='Use the compact extraction.'))
                repair_target = None
                if entry != 'plan':
                    self.assertEqual([item['id'] for item in steps], [12, 8, 7, 1])
                    repair_target = head_id
                    head_id = scenario.repair_route(steps[1:])['id']
                else:
                    self.assertEqual(steps[-1], dict(command='review-series', assignment_id=head_id))
                lines = []
                def respond(route, request, credential):
                    output = scenario.case.build_review_output('bar')
                    if repair_target is not None:
                        output['reassessment'] = dict(assignment_id=repair_target, findings=[])
                    if review_excerpts.read_sent_packet(request).get('pending_source_requests'):
                        output['value']['status'] = 'unresolved'
                    return scenario.case.build_provider_response(output)
                with patch('research_harness.review_transport.send_request', side_effect=respond) as send:
                    self.assertEqual(run_series(scenario.case.store, head_id, emit=lines.append), 0, lines[-1])
                self.assertIn(send.call_count, (1, 2) if repair_target is not None else (1,))
                self.assertEqual(lines[-1]['state'], 'done')
                self.assertTrue(all('capacity_forecast' in row for row in lines))
                self.assertTrue(scenario.case.read_records()['value_review'])
                with patch.object(capacity, 'describe_session_forecast', side_effect=AssertionError('closed review was reopened')):
                    self.assertEqual(plan_next(scenario.case.read_records(), scenario.case.artifacts, head_id)['state'], 'done')

    def test_duplicate_requests_count_the_same_pending_text_once(self):
        requested = self.request_texts([240001])
        state = protocol.assignment_state(self.case.read_records(), self.case.artifacts, self.assignment['id'])
        output = copy.deepcopy(state['output'])
        output['source_requests'].append(dict(requested[0], id='another-request'))
        forecast = capacity.describe_session_forecast(self.case.read_records(), self.case.artifacts,
                                                       self.assignment, output=output)
        self.assertEqual((forecast['pending_parts'], forecast['pending_characters']), (3, 240001))
        self.assertEqual(len(forecast['pending_sources']), 1)

    def test_authenticated_delivery_reduces_pending_parts_and_keeps_legacy_request_bytes(self):
        self.request_texts([120100])
        self.fixture.execute(self.plan())
        head = self.plan()['head_id']
        records = self.case.read_records()
        request_before = protocol.build_assignment_request(records, self.case.artifacts, records['review_assignment'][head])
        output = self.case.build_review_output('bar')
        output['value']['status'] = 'unresolved'
        with patch('research_harness.review_transport.send_request', return_value=self.case.build_provider_response(output)):
            self.case.mutate(protocol.invoke_assignment, dict(id='part-observed', assignment_id=head), credential='fixture')
        forecast = self.plan()['capacity_forecast']
        self.assertEqual((forecast['pending_parts'], forecast['pending_characters']), (1, 100))
        self.assertEqual(protocol.build_assignment_request(self.case.read_records(), self.case.artifacts,
                                                          records['review_assignment'][head]), request_before)
        current = self.case.read_records()
        self.assertEqual(protocol._find_session_bodies(current, self.case.artifacts, current['review_assignment'][head]),
                         protocol._find_session_bodies(current, self.case.artifacts, current['review_assignment'][head],
                                                       evaluate_states=False))

    def test_versions_with_a_shared_body_fit_and_continue_to_a_recorded_review(self):
        requests = self.request_texts([100000, 100000])
        plan = self.plan()
        self.assertEqual(plan['state'], 'continue')
        forecast = plan['capacity_forecast']
        self.assertEqual(len(forecast['pending_sources']), 2)
        self.assertTrue(forecast['projected_final']['fits'])
        self.assertLess(forecast['projected_final']['text_characters'], 150000)
        def respond(route, request, credential):
            output = self.case.build_review_output('bar')
            if review_excerpts.read_sent_packet(request).get('pending_source_requests'):
                output['value']['status'] = 'unresolved'
                output['source_requests'] = requests
            return self.case.build_provider_response(output)
        lines = []
        with patch('research_harness.review_transport.send_request', side_effect=respond) as send:
            self.assertEqual(run_series(self.case.store, self.assignment['id'], emit=lines.append), 0, lines[-1])
        self.assertEqual(send.call_count, 2)
        self.assertTrue(self.case.read_records()['value_review'])

    def test_advisory_selection_keeps_a_stored_metadata_correction(self):
        import test_review_output_correction as corrections
        fixture = corrections.CorrectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.prepare_refused()
        fixture.correct(fixture.delta())
        case = fixture.fixture
        records = case.read_records()
        identifier = fixture.case.assignment['id']
        before = copy.deepcopy(records)
        state = protocol.assignment_state(records, case.artifacts, identifier)
        attempt, output = protocol.read_assignment_output(records, case.artifacts, identifier)
        self.assertEqual((attempt['id'], output), (state['attempt_id'], state['output']))
        self.assertEqual(output, fixture.corrected())
        self.assertEqual(records, before)

    def refuse_direct_run(self):
        # An assignment may already have been stored before the forecast unit shipped.
        payload = dict(id='legacy-next', route_id='route', role='bar', dossier_id=None, reviewer_id='first',
                       author_id='author', context=dict(prior_assignment_id=self.assignment['id']))
        self.case.mutate(protocol.record_assignment, payload)
        before = self.case.store.revision
        with patch('research_harness.review_transport.send_request',
                   side_effect=AssertionError(FORECAST_CALL_ERROR)) as send:
            with self.assertRaises(ResearchError) as raised:
                self.case.mutate(cli.OPERATIONS['review-run'], dict(id='refused-before-call', assignment_id='legacy-next'))
        self.assertEqual(raised.exception.code, 'review_pending_delivery_over_plan')
        send.assert_not_called()
        self.assertEqual(self.case.store.revision, before)
        self.assertNotIn('refused-before-call', self.case.read_records().get('review_invocation', {}))
        return dict(state='stop', code=raised.exception.code, head_id='legacy-next', details=raised.exception.details)

    def test_direct_dispatch_refuses_a_pending_forecast_before_recording_an_invocation(self):
        self.request_texts([240001])
        stopped = self.refuse_direct_run()
        self.assertEqual(stopped['details']['step']['command'], 'select-main-capture')
        answers = stopped['details']['decision']['then']
        self.assertEqual(answers[0]['steps'][0]['payload']['version_id'],
                         stopped['details']['pending_sources'][0]['version_id'])
        self.assertEqual([step['id'] for step in answers[0]['steps']], [12, 8, 7, 1])
        self.assertEqual([step['id'] for step in answers[1]['steps']], [8, 7, 1])

    def test_pending_turn_threshold_is_inclusive(self):
        self.request_texts([capacity.SESSION_PENDING_TURN_LIMIT * 120000])
        route = self.case.read_records()['review_route']['route']
        large = capacity.describe_route_capacity(route)
        large['input_tokens']['value'] = 10000000
        with patch.object(capacity, 'describe_route_capacity', return_value=large):
            forecast = self.plan()['capacity_forecast']
        self.assertEqual(forecast['pending_turns'], capacity.SESSION_PENDING_TURN_LIMIT)
        self.assertIsNone(forecast['reason'])
        self.assertLessEqual(forecast['pending_turns'], forecast['pending_turn_limit'])

    def test_assignment_receipt_and_route_report_keep_forecasts_outside_stored_receipts(self):
        from research_harness.cli import describe_review_receipt
        self.request_texts([120100])
        with patch.object(protocol, 'assignment_state', side_effect=AssertionError('Status evaluated assignment state')):
            pending_report = capacity.describe_open_route_reports(self.case.read_records(), self.case.artifacts)
        self.assertEqual(pending_report['route']['capacity_forecasts'][0]['pending_parts'], 2)
        self.fixture.execute(self.plan())
        head = self.plan()['head_id']
        receipt = dict(result=self.case.read_records()['review_assignment'][head])
        with patch.object(protocol, 'assignment_state', side_effect=AssertionError('A receipt evaluated assignment state')):
            described = describe_review_receipt(self.case.store, 'review-assignment', dict(id=head), receipt)
        self.assertEqual(described['capacity_forecast']['pending_parts'], 2)
        self.assertNotIn('capacity_forecast', receipt)
        with patch.object(protocol, 'assignment_state', side_effect=AssertionError('Status evaluated assignment state')):
            report = capacity.describe_open_route_reports(self.case.read_records(), self.case.artifacts)
        self.assertEqual(report['route']['capacity_forecasts'][0]['assignment_id'], head)

    def test_malformed_packet_does_not_replace_status_or_receipt_diagnostics(self):
        from research_harness.cli import describe_review_receipt
        records = self.case.read_records()
        assignment = records['review_assignment'][self.assignment['id']]
        assignment['packet'] = self.case.artifacts.put(b'{malformed', 'application/json')
        report = capacity.describe_open_route_reports(records, self.case.artifacts)
        self.assertNotIn('capacity_forecasts', report['route'])
        receipt = dict(result=assignment)
        with patch.object(self.case.store, 'snapshot', return_value=dict(records=records)):
            described = describe_review_receipt(self.case.store, 'review-assignment', dict(id=assignment['id']), receipt)
        self.assertNotIn('capacity_forecast', described)
        self.assertEqual(described['result'], assignment)

    def test_forecast_uses_matching_request_profile_and_observed_usage_medians(self):
        self.request_texts([120100])
        records = self.case.read_records()
        route = records['review_route']['route']
        request = protocol.build_assignment_request(records, self.case.artifacts, self.assignment)
        # Retained evidence can repeat while the canonical task counts its body once.
        body = 'x' * 60000
        reference = self.case.artifacts.put(body.encode('utf-8'), 'text/plain')
        packet = review_excerpts.read_sent_packet(request)
        entry = dict(artifact=reference, content=body, encoding='utf-8')
        packet.setdefault('evidence', []).extend([entry, entry])
        request = capacity._project_request(request, packet)
        measured = capacity.measure_request(route, request)
        task = capacity.describe_task(self.assignment['role'], packet, route=route, request=request)
        level = capacity._find_task_level(task['characters'])
        self.assertNotEqual(level, capacity._find_task_level(measured['text_characters']))
        profile = dict(route_class=capacity.describe_route_class(route), rows=[
            dict(kind='request', task_level=level, attempts=8, at_or_over_limit=3)])
        for index, (output, seconds) in enumerate(((700, 12), (900, 20))):
            records.setdefault('review_attempt', {})['usage-' + str(index)] = dict(id='usage-' + str(index),
                assignment_id=self.assignment['id'], recorded_revision=100 + index, completed_generation=True,
                task=dict(kind='request', characters=task['characters']),
                usage=dict(output_tokens=output, wall_seconds=seconds))
        # Usage samples are advisory, not candidates for authenticated source delivery.
        with patch.object(capacity, 'read_output_profile', return_value=profile), patch.object(
                protocol, '_find_session_bodies', return_value={}):
            state = protocol.assignment_state(self.case.read_records(), self.case.artifacts, self.assignment['id'])
            forecast = capacity.describe_session_forecast(records, self.case.artifacts, self.assignment,
                                                          request=request, output=state['output'])
        self.assertEqual(forecast['at_or_over_limit_share'], 3 / 8)
        self.assertEqual(forecast['expected_calls'], 3.2)
        self.assertEqual(forecast['expected_output_tokens'], 800)
        self.assertEqual(forecast['expected_duration_seconds'], 16)
        self.assertEqual(forecast['turns'], [dict(kind='request', count=2)])

    def test_repaired_session_has_the_same_forecast_stop_before_continuation(self):
        self.request_texts([capacity.SESSION_PENDING_TURN_LIMIT * 120000 + 1])
        with patch('research_harness.review_continuation._describe_chain', return_value='context_repair'):
            plan = self.plan()
        self.assertEqual(plan['chain'], 'context_repair')
        self.assertEqual(plan['code'], 'review_pending_delivery_over_plan')
        self.assertEqual(plan['capacity_forecast']['reason'], 'pending_turns')

    def test_complete_source_redelivers_a_whole_body_previously_received_as_partial(self):
        requested = self.request_texts([60000])
        records = self.case.read_records()
        capture = review_excerpts.find_fulltext_capture(
            records, requested[0]['version_id'])
        delivered = {capture['text']['sha256']: dict(is_complete_delivery=False)}
        with patch.object(protocol, '_find_session_bodies', return_value=delivered):
            forecast = capacity.describe_session_forecast(records, self.case.artifacts, self.assignment)
        self.assertEqual((forecast['pending_turns'], forecast['pending_characters']), (1, 60000))
        delivered[capture['text']['sha256']]['is_complete_delivery'] = True
        with patch.object(protocol, '_find_session_bodies', return_value=delivered):
            completed = capacity.describe_session_forecast(records, self.case.artifacts, self.assignment)
        self.assertEqual(completed['pending_parts'], 0)

    def repair_route(self, steps):
        retire, register, repair = steps
        event_evidence = self.case.artifacts.put(
            b'Synthetic decision to move this review to a serving route.', 'text/plain')
        self.case.mutate(cli.OPERATIONS[retire['command']], dict(retire['payload'], id='retire-forecast',
            reason='Use the serving route.', evidence=[event_evidence]))
        self.case.mutate(cli.OPERATIONS[register['command']], dict(register['payload'],
            id='serving-route', model='gpt-6.1-sol'))
        from visual_capacity_fixtures import install_bounded_binary_backend
        install_bounded_binary_backend(self, [self.case.read_records()['review_route']['serving-route']])
        def respond(route, request, credential):
            packet = json.loads(request['input'][0]['content'].removeprefix('The packet below is JSON.\n'))
            return self.case.build_provider_response(dict(allowed_control=packet['allowed_control'],
                evidence_control=packet['evidence_control'], excluded_controls=[]))
        with patch('research_harness.review_transport.send_request', side_effect=respond):
            self.case.mutate(protocol.probe_route, dict(id='serving-probe', route_id='serving-route'),
                             credential='fixture')
        self.assertIn('context.context_repair.event_ids', repair['chooses'])
        self.assertIn('context.context_repair.evidence', repair['chooses'])
        payload = copy.deepcopy(repair['payload'])
        payload.update(id='forecast-repair', route_id='serving-route', reviewer_id='fresh-reader')
        payload['context']['context_repair'].update(event_ids=['retire-forecast'], evidence=[event_evidence],
            probe_id='serving-probe', reason='Preserve and complete this review.')
        return self.case.mutate(cli.OPERATIONS[repair['command']], payload)['result']

    def test_route_answer_executes_retirement_probe_and_repair_to_a_recorded_review(self):
        for direct in (False, True):
            with self.subTest(direct=direct):
                scenario = self
                if direct:
                    scenario = SessionForecastTests()
                    scenario.setUp()
                    self.addCleanup(scenario.doCleanups)
                scenario.request_texts([180000])
                stopped = scenario.refuse_direct_run() if direct else scenario.plan()
                head_id = stopped['head_id']
                repair = scenario.repair_route(stopped['details']['decision']['then'][1]['steps'])
                output = scenario.case.build_review_output('bar')
                output['reassessment'] = dict(assignment_id=head_id, findings=[])
                lines = []
                def review_pending_sources(route, request, credential):
                    packet = review_excerpts.read_sent_packet(request)
                    selected = copy.deepcopy(output)
                    if packet.get('pending_source_requests'):
                        selected['value']['status'] = 'unresolved'
                    return scenario.case.build_provider_response(selected)
                with patch('research_harness.review_transport.send_request', side_effect=review_pending_sources):
                    self.assertEqual(run_series(scenario.case.store, repair['id'], emit=lines.append), 0, lines[-1])
                self.assertEqual(lines[-1]['state'], 'done')
                self.assertTrue(scenario.case.read_records()['value_review'])

    def test_native_exact_envelope_projection_keeps_the_request_unchanged(self):
        from research_harness import review_exact_text, review_exact_text_v2
        from test_research_codex_visual_transport import image_row
        route = dict(self.case.read_records()['review_route']['route'], adapter='codex_cli_visual_v1',
                     endpoint='codex://local')
        body = 'Synthetic visible source.\n' * 1000
        reference = self.case.artifacts.put(body.encode('utf-8'), 'text/plain')
        sources = {reference['sha256']: body}
        packet = dict(evidence=[dict(artifact=reference, content=body, encoding='utf-8')])
        messages = [dict(role='user', content=protocol.transport.PACKET_LABEL + json.dumps(packet))]
        for codec in (review_exact_text, review_exact_text_v2):
            with self.subTest(codec=codec.PROTOCOL):
                request = dict(instructions='', input=[dict(role='user', content=codec.encode(messages, sources))],
                    exactory_image_inputs=[image_row(40)])
                original = copy.deepcopy(request)
                current = capacity.measure_request(route, request)
                with patch.object(capacity, 'measure_request', wraps=capacity.measure_request) as measured:
                    projected = capacity._measure_added_text(route, request, 'Pending source.\n' * 500,
                                                             capacity.read_output_profile({}, route))
                projected_request = measured.call_args[0][1]
                self.assertEqual(capacity._read_encoded_sources(codec, projected_request['input'][0]['content']), sources)
                self.assertEqual(request, original)
                self.assertGreater(projected['text_characters'], current['text_characters'] + 2000)
                self.assertEqual(projected['native_images'], 1)

    def test_turn_packing_counts_the_original_text_of_a_whole_html_delivery(self):
        self.request_texts([50000, 50000], html=True)
        forecast = self.plan()['capacity_forecast']
        self.assertEqual(forecast['pending_parts'], 2)
        self.assertEqual(forecast['pending_turns'], 2)
        self.assertEqual(forecast['pending_characters'], 100000)

    def test_projected_final_counts_json_escaping_before_the_next_call(self):
        self.request_texts([90000], escaped=True)
        plan = self.plan()
        self.assertEqual(plan['code'], 'review_pending_delivery_over_plan')
        forecast = plan['capacity_forecast']
        self.assertEqual(forecast['pending_turns'], 1)
        self.assertEqual(forecast['pending_characters'], 90000)
        self.assertEqual(forecast['reason'], 'projected_input')
        self.assertGreater(forecast['projected_final']['tokens_upper'], forecast['projected_final']['token_limit'])

    def test_waiting_series_line_reports_pending_delivery_without_sending_a_request(self):
        from research_harness import review_series
        self.request_texts([120100])
        self.fixture.execute(self.plan())
        plan = self.plan()
        plan.pop('capacity_forecast')
        plan.update(state='wait', not_before='2100-01-01T00:00:00+00:00', code='review_capacity_wait')
        lines = []
        with patch.object(review_series, '_read_plan', return_value=(self.case.store.snapshot(), plan)), patch(
                'research_harness.review_transport.send_request') as send:
            self.assertEqual(run_series(self.case.store, self.assignment['id'], emit=lines.append), 4)
        send.assert_not_called()
        self.assertEqual(lines[-1]['capacity_forecast']['pending_parts'], 2)
        self.assertEqual(lines[-1]['capacity_forecast']['assignment_id'], plan['head_id'])

    def test_a_twenty_two_part_plan_inside_capacity_keeps_its_continuation(self):
        with patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 120):
            self.request_texts([22 * 120])
            plan = self.plan()
        self.assertEqual(plan['state'], 'continue')
        self.assertEqual(plan['capacity_forecast']['pending_parts'], 22)
        self.assertEqual(plan['capacity_forecast']['pending_turns'], 22)
        self.assertIsNone(plan['capacity_forecast']['reason'])
