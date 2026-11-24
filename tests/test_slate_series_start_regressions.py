"""Assignment regressions from the slate series confirmation review."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_review_parts_repair_journey as repair_fixtures
from continuity_forms_fixtures import fork_store
import test_review_parts_slate as slate_fixtures
from codex_route_fixtures import build_codex_response, invoke_codex, limit_codex_input, record_codex_route
from research_harness import bar_sources, problem_forming, problem_forming_import, research_decisions, review_failure_class, cli
from research_harness import review_parts as parts, review_parts_v3 as v3
from research_harness import review_protocol as protocol, review_route_capacity as capacity
from research_harness import review_session_v4, stop_classes
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.integration import pin_artifact
from research_harness.review_parts_slate import derive_text_capture_id
from review_parts_fixtures import PartsResultCase
from test_research_problem_forming import build_forming
from test_review_output_profile import build_row
from test_review_parts_v3 import make_turn_output


PROTOCOLS = (v3.PROTOCOL, parts.PROTOCOL)


def read_logical(case, assignment):
    packet = protocol._read_json(case.artifacts, assignment['packet'])
    return parts.read_logical_packet(case.artifacts, packet[parts.KEY])


def capture_step(error):
    return next(step for answer in error.details['then'] for step in answer['steps']
                if step['command'] == 'artifact')


def make_ascii_pdf():
    """A one-page vector PDF whose entire file can also be decoded as UTF-8."""
    data = bytearray(b'%PDF-1.4\n')
    stream = b'0 0 m 90 90 l S\n'
    offsets = []
    objects = (b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Count 1 /Kids [3 0 R] >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << >> /Contents 4 0 R >>',
               (b'<< /Length %d >>\nstream\n' % len(stream)) + stream + b'endstream')
    for index, value in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend((b'%d 0 obj\n' % index) + value + b'\nendobj\n')
    start = len(data)
    data.extend(b'xref\n0 5\n0000000000 65535 f \n')
    for offset in offsets:
        data.extend(b'%010d 00000 n \n' % offset)
    data.extend(b'trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % start)
    return bytes(data)


def record_asked_probe(case, payload):
    """Record a capacity probe whose provider reports exactly the input tokens that the payload asks for."""
    from research_harness.review_excerpts import read_sent_packet

    def respond(route, request, credential):
        packet = read_sent_packet(request)
        response = build_codex_response(route, request, dict(allowed_control=packet['allowed_control'],
            evidence_control=packet['evidence_control'], excluded_controls=[]))
        response['usage']['input_tokens'] = payload['capacity']['input_tokens']
        return response

    with patch.object(protocol.transport, 'send_request', side_effect=respond):
        return case.mutate(cli.OPERATIONS['review-probe'], payload)['result']


def take_capacity_answers(test, case, error, *, resume=None, check_assignment=None):
    """Execute every printed step in order, restoring the stopped store for each answer."""
    results = []
    for answer in error.details.get('then', ()):
        with test.subTest(answer=answer['answer'], route=answer.get('route_id')), fork_store(SimpleNamespace(case=case)):
            for step in answer['steps']:
                command = step['command']
                payload = dict(step['payload'])
                if command == 'review-probe':
                    record = record_asked_probe(case, dict(payload, id='answer-probe'))
                    continue
                if command == 'artifact':
                    payload['path'] = 'answer-text.txt'
                    (case.root / payload['path']).write_text('The bound synthetic original text.', encoding='utf-8')
                test.assertIn(command, ('artifact', 'review-assignment'))
                record = case.mutate(cli.OPERATIONS[command], payload)['result']
            if command == 'review-probe':
                test.assertEqual(record['id'], 'answer-probe')
                test.assertTrue(record['capacity_verified'])
                results.append(record)
                continue
            if command == 'artifact':
                test.assertEqual(record['text_form_of'], step['body'])
                if resume is None:
                    results.append(record)
                    continue
                record = resume()
            request = protocol.build_assignment_request(case.read_records(), case.artifacts, record)
            measured = capacity.measure_request(case.read_records()['review_route'][record['route_id']], request,
                                                records=case.read_records(), artifacts=case.artifacts)
            test.assertTrue(measured['fits'], measured)
            if check_assignment is not None:
                check_assignment(record)
            results.append(record)
    return results


class SlateStartRegressionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = slate_fixtures.SlateSeriesTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        if self._testMethodName != 'test_generic_pdf_and_raster_stop_before_the_first_read':
            self.case = self.fixture.make_case(input_tokens=160000, candidate=False)

    def assign(self, identifier='slate-series', **context):
        return self.fixture.assign(self.case, identifier, **context)

    def expect_capture(self):
        with patch.object(v3, 'admit_packet', wraps=v3.admit_packet) as planner:
            with self.assertRaises(ResearchError) as stopped:
                self.assign()
        planner.assert_not_called()
        self.assertEqual(stopped.exception.code, 'review_route_capacity_below_floor')
        self.assertEqual(stopped.exception.details['reason'], 'body_without_text_form')
        self.assertNotIn('slate-series', self.case.read_records()['review_assignment'])
        return stopped.exception

    def save_text(self, payload, content, path='capture.txt'):
        (self.case.store.root / path).write_text(content, encoding='utf-8')
        return self.case.mutate(pin_artifact, dict(payload, path=path))['result']

    def test_generic_pdf_and_raster_stop_before_the_first_read(self):
        for content in (b'%PDF-1.4\x80', b'\x89PNG\r\n\x1a\n'):
            with self.subTest(signature=content):
                self.case = self.fixture.make_case(input_tokens=160000, candidate=False)
                _, body = self.fixture.import_one_body(self.case, content, 'application/octet-stream')
                with patch.object(protocol, 'NEW_SESSION_RULES', (review_session_v4.PROTOCOL, bar_sources.PROTOCOL_V3)):
                    error = self.expect_capture()
                    take_capacity_answers(self, self.case, error)
                self.assertEqual(capture_step(error)['body'], body)

    def test_single_slate_refresh_can_start_parts(self):
        self.case.record_candidate()
        first = self.assign('single')
        self.assertNotIn(parts.KEY, first)
        output = self.case.build_slate_output(['candidate-1'])
        invoke_codex(self.case, first['id'], output)
        self.case.mutate(research_decisions.record_value_review,
                         dict(output, id='review', assignment_id=first['id'], dossier_id=None))
        self.fixture.import_one_body(self.case)
        with patch.object(v3, 'admit_packet', wraps=v3.admit_packet) as planner:
            refresh = self.assign('refresh')
        planner.assert_called_once()
        self.assertEqual(refresh[parts.KEY]['protocol'], v3.PROTOCOL)
        self.assertEqual(read_logical(self.case, refresh)['problem_refresh']['assignment_id'], first['id'])

    def test_reference_free_raw_head_retains_its_next_inventory(self):
        native = self.case.mutate(problem_forming.record_forming,
                                  build_forming('native', forms=[]))['result']
        reference = dict(kind='record', record_kind='forming_call', id='native', digest=digest(native))
        self.fixture.import_one_body(self.case, None, native=[reference])
        with patch.object(bar_sources, 'SESSION_PROTOCOL', bar_sources.PROTOCOL):
            first = self.assign()
        packet = protocol._read_json(self.case.artifacts, first['packet'])
        logical = read_logical(self.case, first)
        self.assertIn('problem_forming_imports', logical)
        self.assertNotIn('forming_records', logical)
        invoke_codex(self.case, first['id'], make_turn_output(packet))
        child = self.assign('next', prior_assignment_id=first['id'])
        following = protocol._read_json(self.case.artifacts, child['packet'])
        self.assertEqual(following[parts.KEY]['inventory'], packet[parts.KEY]['inventory'])
        self.assertNotIn('forming_records', read_logical(self.case, child))

    def test_generic_artifact_at_capture_id_cannot_claim_original(self):
        _, body = self.fixture.import_one_body(self.case, b'%PDF-1.4', 'application/pdf')
        self.save_text(dict(id=derive_text_capture_id(body), media_type='text/plain'), 'Unrelated text.')
        error = self.expect_capture()
        self.assertEqual(capture_step(error)['body'], body)
        take_capacity_answers(self, self.case, error)

    def test_capture_replaces_occupied_id_and_selected_text_stays_frozen(self):
        _, body = self.fixture.import_one_body(self.case, b'%PDF-1.4', 'application/pdf')
        self.save_text(dict(id=derive_text_capture_id(body), media_type='text/plain'), '  ')
        error = self.expect_capture()
        take_capacity_answers(self, self.case, error)
        step = capture_step(error)
        saved = self.save_text(step['payload'], 'First complete transcription.')
        self.assertEqual(saved['text_form_of'], body)
        first = self.assign()
        packet = protocol._read_json(self.case.artifacts, first['packet'])
        frozen = next(row for row in read_logical(self.case, first)['evidence'] if 'text_form_of' in row)
        self.assertEqual(frozen['artifact'], saved['artifact'])
        self.assertEqual(frozen['content'], 'First complete transcription.')
        replacement = self.save_text(step['payload'], 'Second declared transcription.', path='next.txt')
        another = self.case.mutate(protocol.record_assignment, dict(id='another-series', role='slate',
            route_id='local', reviewer_id='second', author_id='problem-author', dossier_id=None, context={}))['result']
        self.assertIn(replacement['artifact'], [row['artifact'] for row in read_logical(self.case, another)['evidence']])
        invoke_codex(self.case, first['id'], make_turn_output(packet))
        child = self.assign('next', prior_assignment_id=first['id'])
        self.assertIn(frozen, read_logical(self.case, child)['evidence'])

    def test_existing_extracted_text_is_resolved_outside_the_packet(self):
        version = self.case.metadata(4931)
        extracted = self.case.capture(version, pdf_text='Extracted text of the imported paper.')
        _, body = self.fixture.import_one_body(self.case, self.case.artifacts.read(extracted['original']),
                                              extracted['original']['media_type'])
        self.assertEqual(body, extracted['original'])
        packet = protocol.build_packet(self.case.read_records(), self.case.artifacts, None, 'slate', 'first')
        self.assertNotIn(extracted['text'], [row['artifact'] for row in packet['evidence']])
        first = self.assign()
        self.assertEqual(first[parts.KEY]['protocol'], v3.PROTOCOL)
        self.assertIn(extracted['text'], [row['artifact'] for row in read_logical(self.case, first)['evidence']])

    def test_raw_large_import_uses_capture_step_then_parts(self):
        with patch.object(bar_sources, 'SESSION_PROTOCOL', bar_sources.PROTOCOL):
            _, body = self.fixture.import_one_body(self.case, b'%PDF-1.4' + b' ' * 455000, 'application/pdf')
            error = self.expect_capture()
            take_capacity_answers(self, self.case, error)
            capture = capture_step(error)
            self.assertEqual(capture['payload']['id'], derive_text_capture_id(body))
            saved = self.save_text(capture['payload'], 'Complete synthetic transcription.')
            first = self.assign()
        self.assertEqual(first[parts.KEY]['protocol'], v3.PROTOCOL)
        self.assertIn(saved['artifact'], [row['artifact'] for row in read_logical(self.case, first)['evidence']])
        packet = protocol._read_json(self.case.artifacts, first['packet'])
        invoke_codex(self.case, first['id'], make_turn_output(packet))
        with patch.object(parts, 'NEW_SERIES_PROTOCOLS', (parts.PROTOCOL,)):
            child = self.assign('next', prior_assignment_id=first['id'])
        self.assertIn(saved['artifact'], [row['artifact'] for row in read_logical(self.case, child)['evidence']])

    def test_capture_stop_with_legacy_parent_never_offers_an_illegal_route(self):
        self.case.record_candidate()
        old = self.assign('old')
        output = self.case.build_slate_output(['candidate-1'])
        invoke_codex(self.case, old['id'], output)
        self.case.mutate(research_decisions.record_value_review,
                         dict(output, id='old-review', assignment_id=old['id'], dossier_id=None))
        self.fixture.import_one_body(self.case, b'%PDF-1.4', 'application/pdf')
        self.case.capacity_probe_tokens = 128000 + 400000
        record_codex_route(self.case, 'big', output_tokens=128000, model='big')
        error = self.expect_capture()
        self.assertEqual(error.details['serving_routes'], [])
        [resumed] = take_capacity_answers(self, self.case, error, resume=self.assign)
        # Design slate-reviews, decisions D3 and D5: the refresh of an ordinary slate review is a new chain in its slot.
        self.assertEqual(resumed['context'], {'session_refresh': {'assignment_id': old['id']}})
        self.assertEqual(resumed['route_id'], old['route_id'])

    def test_ascii_pdf_stops_even_when_v4_has_room(self):
        for media_type in ('application/octet-stream', 'text/plain; charset=us-ascii'):
            with self.subTest(media_type=media_type):
                self.case = self.fixture.make_case(input_tokens=400000, candidate=False)
                limit_codex_input(self.case, 1000000)
                _, body = self.fixture.import_one_body(self.case, make_ascii_pdf(), media_type, reason_characters=410000)
                with patch.object(protocol, 'NEW_SESSION_RULES', (review_session_v4.PROTOCOL, bar_sources.PROTOCOL_V3)):
                    error = self.expect_capture()
                    self.assertEqual(capture_step(error)['body'], body)
                    [resumed] = take_capacity_answers(self, self.case, error, resume=self.assign)
                    self.assertEqual(resumed[parts.KEY]['protocol'], v3.PROTOCOL)

    def test_fitting_single_still_requires_a_bound_original(self):
        self.case = self.fixture.make_case(input_tokens=400000, candidate=False)
        self.fixture.import_one_body(self.case, b'\xffopaque', 'application/octet-stream', reason_characters=1)
        error = self.expect_capture()
        [resumed] = take_capacity_answers(self, self.case, error, resume=self.assign)
        self.assertNotIn(parts.KEY, resumed)

    def test_missing_late_body_stops_both_forming_projections(self):
        for rules in (bar_sources.PROTOCOL, bar_sources.PROTOCOL_V3):
            with self.subTest(rules=rules), patch.object(bar_sources, 'SESSION_PROTOCOL', rules):
                self.case = self.fixture.make_case(input_tokens=160000, candidate=False)
                self.fixture.import_one_body(self.case)
                first = self.assign()
                packet = protocol._read_json(self.case.artifacts, first['packet'])
                origin = read_logical(self.case, first)['problem_forming_imports'][0]['source_origin']
                invoke_codex(self.case, first['id'], make_turn_output(packet))
                _, late = self.fixture.import_one_body(self.case, b'%PDF-1.4\x80' + b' ' * 460000,
                    'application/pdf', reason_characters=1, import_id='late', forming_id='late-call',
                    cite_candidate=False)
                describe_imports = problem_forming_import.describe_imports

                def describe_with_earlier_origin(records, artifacts):
                    return sorted(describe_imports(records, artifacts), key=lambda row: row['source_origin'] == origin)

                def resume():
                    return self.assign('next', prior_assignment_id=first['id'])

                def check_extension(child):
                    logical = read_logical(self.case, child)
                    self.assertEqual(logical['problem_forming_imports'][0]['source_origin'], origin)
                    self.assertIn(late['sha256'], bar_sources.find_bound_text_forms(logical))

                with patch.object(problem_forming_import, 'describe_imports', side_effect=describe_with_earlier_origin):
                    with self.assertRaises(ResearchError) as stopped:
                        resume()
                    self.assertEqual(stopped.exception.code, 'review_route_capacity_below_floor')
                    self.assertEqual(capture_step(stopped.exception)['body'], late)
                    [child] = take_capacity_answers(self, self.case, stopped.exception, resume=resume,
                                                    check_assignment=check_extension)
                self.assertEqual(child['context']['prior_assignment_id'], first['id'])

    def answer_with_source_request(self, assignment):
        output = self.case.build_slate_output(sorted(self.case.read_records()['problem_candidate']))
        output['value']['status'] = 'unresolved'
        output['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
        invoke_codex(self.case, assignment['id'], output)

    def read_packet(self, assignment):
        return protocol._read_json(self.case.artifacts, assignment['packet'])

    def test_stored_single_chain_continues_in_the_form_of_its_first_turn(self):
        """A single chain that release 0.92.0 started with an unbound PDF body takes its next turn as before."""
        self.fixture.import_one_body(self.case, b'%PDF-1.4\x80 stored', 'application/pdf', reason_characters=2000)
        with patch('research_harness.review_parts_slate.bind_assignment_bodies', create=True,
                   side_effect=lambda records, artifacts, value, packet, **kwargs: packet):
            first = self.assign()
        self.assertNotIn(parts.KEY, first)
        self.answer_with_source_request(first)
        child = self.assign('next', prior_assignment_id=first['id'])
        self.assertNotIn(parts.KEY, child)
        self.assertEqual(self.read_packet(child)['evidence'], self.read_packet(first)['evidence'])
        self.assertFalse(any('text_form_of' in row for row in self.read_packet(child)['evidence']))

    def test_single_chain_keeps_its_capture_and_binds_an_added_original(self):
        _, body = self.fixture.import_one_body(self.case, b'%PDF-1.4\x80 first', 'application/pdf',
                                               reason_characters=2000)
        error = self.expect_capture()
        self.assertEqual(capture_step(error)['body'], body)
        self.save_text(capture_step(error)['payload'], 'The text of the first original.')
        first = self.assign()
        self.assertNotIn(parts.KEY, first)
        captured = bar_sources.find_bound_text_forms(self.read_packet(first))
        self.assertEqual(set(captured), {body['sha256']})
        self.answer_with_source_request(first)
        _, late = self.fixture.import_one_body(self.case, b'%PDF-1.4\x80 late', 'application/pdf', reason_characters=1,
                                               import_id='late', forming_id='late-call', cite_candidate=False)

        def resume():
            return self.assign('next', prior_assignment_id=first['id'])

        def check_child(child):
            self.assertNotIn(parts.KEY, child)
            bound = bar_sources.find_bound_text_forms(self.read_packet(child))
            self.assertEqual(set(bound), {body['sha256'], late['sha256']})
            self.assertEqual(bound[body['sha256']], captured[body['sha256']])

        with self.assertRaises(ResearchError) as stopped:
            resume()
        self.assertEqual(stopped.exception.code, 'review_route_capacity_below_floor')
        self.assertEqual([step['body'] for answer in stopped.exception.details['then'] for step in answer['steps']],
                         [late])
        [child] = take_capacity_answers(self, self.case, stopped.exception, resume=resume, check_assignment=check_child)
        self.assertEqual(child['context']['prior_assignment_id'], first['id'])

    def test_capture_answer_accepts_each_step_with_two_uncaptured_bodies(self):
        self.fixture.import_one_body(self.case, b'%PDF-1.4 first', 'application/pdf')
        self.fixture.import_one_body(self.case, b'%PDF-1.4 second', 'application/pdf',
            import_id='second', forming_id='second-call', reason_characters=1, cite_candidate=False)
        error = self.expect_capture()
        self.assertEqual([step['command'] for answer in error.details['then'] for step in answer['steps']], ['artifact'])
        [capture] = take_capacity_answers(self, self.case, error)
        self.assertEqual(capture['text_form_of'], capture_step(error)['body'])

    def test_fresh_capture_does_not_offer_a_route_that_fails_the_session_floor(self):
        self.fixture.import_one_body(self.case, make_ascii_pdf(), 'application/octet-stream')
        self.case.capacity_probe_tokens = 128000 + 180000
        record_codex_route(self.case, 'border', output_tokens=128000, model='border')
        with patch.object(protocol, 'NEW_SESSION_RULES', (review_session_v4.PROTOCOL, bar_sources.PROTOCOL_V3)):
            error = self.expect_capture()
            self.assertEqual(error.details['serving_routes'], [])
            take_capacity_answers(self, self.case, error)

    def test_token_limited_slate_probe_is_an_executable_standalone_step(self):
        self.fixture.import_one_body(self.case)
        with patch.object(parts, 'NEW_SERIES_PROTOCOLS', (parts.PROTOCOL,)):
            with self.assertRaises(ResearchError) as stopped:
                self.assign()
            details = stopped.exception.details
            self.assertLessEqual(details['measure']['characters'], details['measure']['character_limit'])
            self.assertGreater(details['measure']['tokens_upper'], details['measure']['token_limit'])
            probe = next(row for row in details['then'] if row['answer'] == 'Probe this route')
            self.assertEqual([step['command'] for step in probe['steps']], ['review-probe'])
            take_capacity_answers(self, self.case, stopped.exception)

    def test_parts_floor_offers_the_fitting_single_from_exact_assignment(self):
        self.case = self.fixture.make_case(input_tokens=90000, candidate=False)
        self.fixture.import_one_body(self.case)
        self.case.capacity_probe_tokens = 128000 + 400000
        record_codex_route(self.case, 'big', output_tokens=128000, model='big')
        with self.assertRaises(ResearchError) as stopped:
            self.assign()
        self.assertGreater(stopped.exception.details['planner_floor']['minimum'],
                           stopped.exception.details['planner_floor']['room'])
        self.assertIn(('big', 'single'), [(row['route_id'], row['delivery'])
                                        for row in stopped.exception.details['serving_routes']])
        alternates = {assignment['route_id']: assignment for assignment in
                      take_capacity_answers(self, self.case, stopped.exception)}
        self.assertNotIn(parts.KEY, alternates['big'])

    def test_a_slate_above_a_fixed_character_limit_stops_without_a_probe_answer(self):
        """A capacity stop whose request is above a fixed character limit prints no probe answer, because a probe
        cannot raise a character limit. The state: a first slate with an imported body on a route of 400,000 input
        tokens and a character limit of 10,000; the series of the parts protocol v3 declines and the slate stops."""
        with patch.object(slate_fixtures, 'LIMITED_ROUTE_CHARACTERS', 10000):
            case = self.fixture.make_case(input_tokens=400000, candidate=False)
        self.fixture.import_one_body(case, b'Unique text of the imported forming record.', reason_characters=140000)
        with self.assertRaises(ResearchError) as stopped:
            self.fixture.assign(case)
        error = stopped.exception
        self.assertEqual(error.code, 'review_route_capacity_below_floor')
        self.assertGreater(error.details['measure']['characters'], error.details['measure']['character_limit'])
        self.assertLess(error.details['measure']['tokens_upper'], error.details['measure']['token_limit'])
        self.assertNotIn('review-probe', [step['command'] for answer in error.details['then'] for step in answer['steps']])
        take_capacity_answers(self, case, error)

    def test_a_slate_whose_route_cannot_hold_the_charged_read_stops_with_steps_the_store_takes(self):
        """The input of SlateSeriesTests.test_a_slate_over_the_token_limit_of_its_route_starts_a_series on its route of
        140,000 input tokens: the read frame cannot hold the charged prompt of a slate read beside its groups, so the
        slate stops with the capacity stop, and each step that the stop names is taken by the store once."""
        case = self.fixture.make_case(input_tokens=140000, candidate=False)
        self.fixture.import_one_body(case, b'Unique text of the imported forming record.', reason_characters=120000)
        with self.assertRaises(ResearchError) as stopped:
            self.fixture.assign(case)
        error = stopped.exception
        self.assertEqual(error.code, 'review_route_capacity_below_floor')
        self.assertNotIn('slate-series', case.read_records()['review_assignment'])
        steps = [(step['command'], repr(sorted(step['payload'].items())))
                 for answer in error.details['then'] for step in answer['steps']]
        self.assertTrue(steps)
        self.assertEqual(len(steps), len(set(steps)), steps)
        self.assertEqual(len(take_capacity_answers(self, case, error)), len(error.details['then']))

    def test_legacy_refresh_starts_parts_with_both_preferences(self):
        self.case = self.fixture.make_case(input_tokens=400000)
        limit_codex_input(self.case, 1000000)
        first = self.assign('old')
        output = self.case.build_slate_output(['candidate-1'])
        invoke_codex(self.case, first['id'], output)
        self.case.mutate(research_decisions.record_value_review,
                         dict(output, id='old-review', assignment_id=first['id'], dossier_id=None))
        self.fixture.import_one_body(self.case, None, reason_characters=410000)
        with patch.object(protocol, 'NEW_SESSION_RULES', (review_session_v4.PROTOCOL, bar_sources.PROTOCOL_V3)), \
                patch.object(review_session_v4, 'check_v4_admission', wraps=review_session_v4.check_v4_admission) as sessions:
            refresh = self.assign('refresh')
        # Design slate-reviews, decision D9: a refresh is a new chain with the context session_refresh, and with the
        # session rules v4 first their admission is tried (rule F1); here it admits the refresh as a v4 chain.
        sessions.assert_called()
        self.assertEqual(refresh['context'], {'session_refresh': {'assignment_id': first['id']}})
        self.assertIn('session_chain', refresh)
        self.assertNotIn(parts.KEY, refresh)


class ResultStartRegressionTests(PartsResultCase):
    output_tokens = 128000

    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        limit = {
            'test_lossy_refresh_single_stays_ineligible_after_v3_refusal': 65000,
            'test_route_discovery_does_not_replan_projected_result_read': 245000,
            'test_a_projected_read_that_fits_no_level_is_refused_by_the_v2_planner': 245000,
            'test_projected_read_at_a_fixed_character_limit_is_fitted_and_records': 245000,
        }.get(self._testMethodName, 165000)
        with patch('test_research_problem_reviews.ProblemReviewTests.capacity_probe_tokens',
                   self.output_tokens + limit, create=True):
            super().setUp()

    def test_lossy_refresh_single_stays_ineligible_after_v3_refusal(self):
        self.oversized(count=1, characters=1000, limit=500000)
        refusals = []
        candidates = []

        production = capacity.can_send_ordinary_packet

        def check_ordinary(records, artifacts, route, packet, request):
            result = production(records, artifacts, route, packet, request)
            measure = capacity.measure_request(route, request, records=records, artifacts=artifacts)
            candidates.append((measure['fits'], result))
            return result

        def admit(records, artifacts, route, packet, adapter, **options):
            result = original(records, artifacts, route, packet, adapter, **options)
            refusals.append((route['id'], result))
            return result

        original = v3.admit_packet
        with patch.object(parts, 'NEW_SERIES_PROTOCOLS', PROTOCOLS),                 patch.object(bar_sources, 'SESSION_PROTOCOL', bar_sources.PROTOCOL_V3):
            first = self.assign()
            self.record_final(first)
            limit_codex_input(self, 51000)
            evidence = self.case.artifacts.put(b'B' * 60000, 'text/plain')
            self.case.expected['assessment_evidence'] = [evidence]
            self.case.expected['value']['motivation'][0]['text'] += ' changed'
            with patch.object(v3, 'admit_packet', side_effect=admit), \
                    patch.object(capacity, 'can_send_ordinary_packet', side_effect=check_ordinary):
                with self.assertRaises(ResearchError) as stopped:
                    self.assign('refresh')
            take_capacity_answers(self, self.case, stopped.exception)
        self.assertEqual(candidates, [(True, False)])
        self.assertEqual(stopped.exception.code, 'review_route_capacity_below_floor')
        self.assertEqual([(route, row['state']) for route, row in refusals], [('route', 'refuse')])
        self.assertNotIn('refresh', self.case.read_records()['review_assignment'])

    def stop_ordinary_child_of_refresh(self):
        """A page request of an ordinary refresh that its route cannot carry: the stopped child and its stop."""
        self.oversized(count=1, characters=1000, limit=1000000)
        first = self.assign()
        self.record_final(first)
        self.case.expected['value']['motivation'][0]['text'] += ' changed'
        refresh = self.assign('refresh')
        self.assertNotIn(parts.KEY, refresh)
        output = self.case.build_review_output('result')
        output['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
        output['value'].update(status='unresolved', reason='R' * 160000)
        self.invoke(refresh, output)
        with patch.object(v3, 'admit_packet', wraps=v3.admit_packet) as planner:
            with self.assertRaises(ResearchError) as stopped:
                self.assign('page', refresh['id'])
        self.assertNotIn('problem_result', [call.args[4] for call in planner.call_args_list])
        return refresh, stopped.exception

    def test_ordinary_child_of_refresh_does_not_start_a_series(self):
        profiles = [dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
                    for kind in ('single', 'read', 'combine', 'check', 'final')]
        with patch.object(parts, 'NEW_SERIES_PROTOCOLS', PROTOCOLS), \
                patch.object(bar_sources, 'SESSION_PROTOCOL', bar_sources.PROTOCOL), \
                patch.object(capacity, 'read_profile_priors', return_value=profiles):
            _, stopped = self.stop_ordinary_child_of_refresh()
            take_capacity_answers(self, self.case, stopped)
        self.assertEqual(stopped.code, 'review_route_capacity_below_floor')
        self.assertEqual(stopped.details['reason'], 'no_parts_delivery')

    def test_the_plan_of_a_stopped_child_reaches_its_assignment_after_its_probes(self):
        """Follow the plan from the stop: each probe that an answer names, then the same assignment, until it records,
        after at most two probes (the prior, then the need) and without repeating a refused step."""
        profiles = [dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
                    for kind in ('single', 'read', 'combine', 'check', 'final')]
        with patch.object(parts, 'NEW_SERIES_PROTOCOLS', PROTOCOLS), \
                patch.object(bar_sources, 'SESSION_PROTOCOL', bar_sources.PROTOCOL), \
                patch.object(capacity, 'read_profile_priors', return_value=profiles):
            refresh, stopped = self.stop_ordinary_child_of_refresh()
            sizes = []
            for _ in range(3):
                [answer] = [row for row in stopped.details['then'] if row['answer'] == 'Probe this route']
                self.assertEqual([step['command'] for step in answer['steps']], ['review-probe'])
                payload = answer['steps'][0]['payload']
                self.assertNotIn(payload['capacity']['input_tokens'], sizes)
                sizes.append(payload['capacity']['input_tokens'])
                probe = record_asked_probe(self.case, dict(payload, id='plan-probe-' + str(len(sizes))))
                self.assertTrue(probe['capacity_verified'])
                try:
                    child = self.assign('page', refresh['id'])
                    break
                except ResearchError as error:
                    self.assertEqual(error.code, 'review_route_capacity_below_floor')
                    stopped = error
        self.assertLessEqual(len(sizes), 2)
        self.assertEqual(child['context']['prior_assignment_id'], refresh['id'])
        self.assertNotIn(parts.KEY, child)

    def test_route_discovery_does_not_replan_projected_result_read(self):
        """The discovery of the serving routes does not plan a projected read a second time: a route with room for the
        recorded read takes it as it is. No command of the merged tree reaches this rule (the first read is fitted to
        its route before any stop), so the discovery runs over the projected read that the first assignment records,
        with a second route of a larger fixed character limit."""
        self.oversized(count=1, characters=7000, limit=40000)
        self.case.expected['assessment_evidence'] = [self.case.artifacts.put(b'B' * 30000, 'text/plain')]
        self.case.capacity_probe_tokens = 128000 + 400000
        record_codex_route(self.case, 'big', output_tokens=128000, model='big')
        native_limit = protocol.transport.local_input_limit
        describe = capacity.describe_route_capacity

        def bounds(route, *args, **kwargs):
            result = describe(route, *args, **kwargs)
            if route['id'] == 'big':
                result['input_characters']['value'] = 200000
            return result

        profiles = [dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
                    for kind in ('single', 'read', 'combine', 'check', 'final')]
        with patch.object(parts, 'NEW_SERIES_PROTOCOLS', PROTOCOLS), \
                patch.object(bar_sources, 'SESSION_PROTOCOL', bar_sources.PROTOCOL), \
                patch.object(capacity, 'read_profile_priors', return_value=profiles), \
                patch.object(protocol.transport, 'local_input_limit',
                             side_effect=lambda route: 200000 if route['id'] == 'big' else native_limit(route)), \
                patch.object(capacity, 'describe_route_capacity', side_effect=bounds):
            head = self.assign()
            packet = self.packet(head)
            self.assertEqual(parts.find_kind(packet), 'read')
            with patch.object(capacity, '_build_serving_parts', wraps=capacity._build_serving_parts) as planner:
                rows = capacity.find_serving_routes(self.case.read_records(), 'result', packet,
                                                    artifacts=self.case.artifacts, assignment=head, prompt=head['prompt'])
        planner.assert_not_called()
        self.assertIn(('big', 'single'), [(row['route_id'], row['delivery']) for row in rows])

    def test_a_projected_read_that_fits_no_level_is_refused_by_the_v2_planner(self):
        """The input of test_projected_read_at_a_fixed_character_limit_is_fitted_and_records on a character limit of
        24,600: no read level of the parts protocol v3 fits, the series of v3 declines, and the planner of the parts
        protocol v2 refuses the assignment with its own code."""
        self.oversized(count=1, characters=7000, limit=24600)
        self.case.expected['assessment_evidence'] = [self.case.artifacts.put(b'B' * 30000, 'text/plain')]
        profiles = [dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
                    for kind in ('single', 'read', 'combine', 'check', 'final')]
        with patch.object(parts, 'NEW_SERIES_PROTOCOLS', PROTOCOLS), \
                patch.object(bar_sources, 'SESSION_PROTOCOL', bar_sources.PROTOCOL), \
                patch.object(capacity, 'read_profile_priors', return_value=profiles), \
                patch.object(parts, 'plan_turn', wraps=parts.plan_turn) as v2:
            with self.assertRaises(ResearchError) as refused:
                self.assign()
        self.assertEqual(refused.exception.code, 'review_parts_inventory_too_large')
        self.assertTrue(v2.called)
        self.assertNotIn('part-1', self.case.read_records()['review_assignment'])
        # The registry names the next step of this refusal: the record check or the status, not the refused assignment.
        then = stop_classes.STOP_REGISTRY[refused.exception.code]['next']['then']
        self.assertEqual([step['command'] for answer in then for step in answer['steps']], ['record-check', 'status'])

    def test_projected_read_at_a_fixed_character_limit_is_fitted_and_records(self):
        """A first result assignment with a read over a fixed character limit of 40,000, under the admission that charges
        the prompt of each turn: the read is fitted at a lower level of its plan, and its request fits both limits."""
        self.oversized(count=1, characters=7000, limit=40000)
        self.case.expected['assessment_evidence'] = [self.case.artifacts.put(b'B' * 30000, 'text/plain')]
        profiles = [dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
                    for kind in ('single', 'read', 'combine', 'check', 'final')]
        with patch.object(parts, 'NEW_SERIES_PROTOCOLS', PROTOCOLS), \
                patch.object(bar_sources, 'SESSION_PROTOCOL', bar_sources.PROTOCOL), \
                patch.object(capacity, 'read_profile_priors', return_value=profiles):
            assignment = self.assign()
        header = self.packet(assignment)[parts.KEY]
        self.assertEqual((header['protocol'], header['kind']), (v3.PROTOCOL, 'read'))
        plan = capacity.read_chain_capacity_plan(self.case.artifacts, assignment)
        level = header['capacity'].get('level_material_allowance', header['capacity']['material_allowance'])
        self.assertLess(level, plan['task']['read']['levels'][0]['material_allowance'])
        records = self.case.read_records()
        request = protocol.build_assignment_request(records, self.case.artifacts, assignment)
        measured = capacity.measure_request(records['review_route'][assignment['route_id']], request,
                                            records=records, artifacts=self.case.artifacts)
        self.assertTrue(measured['fits'], measured)
        self.assertLessEqual(measured['characters'], measured['character_limit'])
        self.assertLessEqual(measured['tokens_upper'], measured['token_limit'])


class RepairStartRegressionTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def test_selected_source_request_retry_keeps_the_ordinary_repair(self):
        for limit in (65000, 165000):
            with self.subTest(limit=limit):
                fixture = repair_fixtures.BarRepairJourneyTests()
                self.addCleanup(fixture.doCleanups)
                fixture.capacity_probe_tokens = 128000 + limit
                fixture.setUp()
                first = fixture.assign_repair()
                self.assertNotIn(parts.KEY, first)

                def at_cap(route, request, credential):
                    response = build_codex_response(route, request, {})
                    response.update(status='incomplete')
                    response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
                    return response

                with patch.object(protocol.transport, 'send_request', side_effect=at_cap):
                    failed = protocol.invoke_assignment(fixture.store, dict(id='failed', assignment_id=first['id']),
                        credential='fixture', expected_revision=fixture.store.revision, request_id='failed')['result']
                self.assertEqual(review_failure_class.classify_attempt(
                    fixture.store.snapshot()['records'], fixture.artifacts, failed)['outcome'], 'output_over_cap')
                output = fixture.review_response('bar', status='unresolved')
                output['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
                output['value']['reason'] = 'short'
                retry = invoke_codex(fixture, first['id'], output)['result']
                attempt, _ = protocol.read_assignment_output(fixture.store.snapshot()['records'],
                                                               fixture.artifacts, first['id'])
                self.assertEqual(attempt['id'], retry['id'])
                payload = dict(fixture.repair_payload(), id='source-child', context=dict(prior_assignment_id=first['id']))
                with patch.object(parts, 'NEW_SERIES_PROTOCOLS', PROTOCOLS), \
                        patch.object(v3, 'admit_packet', wraps=v3.admit_packet) as v3_plans, \
                        patch.object(parts, 'plan_turn', wraps=parts.plan_turn) as v2_plans:
                    child = fixture.mutate(protocol.record_assignment, payload)['result']
                v3_plans.assert_not_called()
                v2_plans.assert_not_called()
                self.assertNotIn(parts.KEY, child)
                self.assertEqual(child['history_assignment_id'], first['id'])

    def test_reviewed_cap_retry_repair_reaches_v2_only_through_the_coverage_check(self):
        """A repair after a reviewed retry of a capped attempt is planned as a fresh repair: v3 refuses it, the parts
        protocol v2 plans it, and the coverage check of the assignment refuses the first read that the route cannot
        carry with the capacity stop and its answers; nothing is recorded (finding PL-F8 of the review of the merged
        tree; the decision of the table replaces the rule that only an outstanding output cap opens the v2 planner)."""
        fixture = repair_fixtures.FreshRepairStartTests(
            'test_a_fresh_repair_refused_by_v3_reaches_v2_only_through_the_coverage_check')
        self.addCleanup(fixture.doCleanups)

        def retry(case, assignment_id, output):
            def over_cap(route, request, credential):
                response = build_codex_response(route, request, {})
                response.update(status='incomplete',
                    usage=dict(input_tokens=37, output_tokens=route['configuration']['max_output_tokens']))
                return response
            with patch.object(protocol.transport, 'send_request', side_effect=over_cap):
                failed = protocol.invoke_assignment(case.store, dict(id='earlier-cap', assignment_id=assignment_id),
                    credential='fixture', expected_revision=case.store.revision, request_id='earlier-cap')['result']
            records = case.store.snapshot()['records']
            self.assertEqual(review_failure_class.classify_attempt(records, case.artifacts, failed)['outcome'],
                             'output_over_cap')
            return invoke_codex(case, assignment_id, output)

        with patch.object(repair_fixtures, 'invoke_codex', side_effect=retry):
            fixture.setUp()
        fixture.capacity_probe_tokens = 128000 + 15000
        record_codex_route(fixture, 'small', output_tokens=128000, model='small')
        payload = dict(fixture.repair_payload(), route_id='small')
        payload['context']['context_repair']['probe_id'] = 'probe-small'
        admissions = []
        original = v3.admit_packet

        def admit(records, artifacts, route, packet, adapter, **options):
            result = original(records, artifacts, route, packet, adapter, **options)
            admissions.append((route['id'], adapter, result))
            return result

        with patch.object(parts, 'NEW_SERIES_PROTOCOLS', PROTOCOLS),                 patch.object(v3, 'admit_packet', side_effect=admit),                 patch.object(parts, 'plan_turn', wraps=parts.plan_turn) as v2:
            with self.assertRaises(ResearchError) as stopped:
                fixture.mutate(protocol.record_assignment, payload)
        self.assertEqual([(route, adapter, result['state']) for route, adapter, result in admissions],
                         [('small', 'context_repair', 'refuse')])
        self.assertEqual(v2.call_count, 1)
        self.assertEqual(stopped.exception.code, 'review_route_capacity_below_floor')
        self.assertEqual([answer['answer'] for answer in stopped.exception.details['then']],
                         ['Probe this route', 'Reassess the retained review'])
        self.assertNotIn(payload['id'], fixture.store.snapshot()['records']['review_assignment'])


class ReleasedSlateChainTests(unittest.TestCase):
    """Three slate states that release 0.92.0 recorded with a PDF forming body and no text form. The next slate
    assignment stops for a capture first, the store takes the capture, and the assignment then records."""

    ARCHIVE = 'slate-chains-0.92.0.tar.gz'

    def open_store(self, scenario):
        import tarfile
        from pathlib import Path
        from research_harness.artifacts import ArtifactStore
        from research_harness.storage import Store
        from test_research_problem_slate import ProblemSlateTests
        from visual_capacity_fixtures import install_bounded_binary_backend
        case = ProblemSlateTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        priors = patch.object(capacity, 'read_profile_priors', return_value=[
            dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
            for kind in ('single', 'read', 'combine', 'check', 'final')])
        priors.start()
        self.addCleanup(priors.stop)
        root = Path(case.temporary.name) / 'released' / scenario
        with tarfile.open(Path(__file__).with_name('fixtures') / self.ARCHIVE) as archive:
            for member in archive.getmembers():
                if member.isfile() and member.name.startswith(scenario + '/'):
                    path = root / member.name[len(scenario) + 1:]
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(archive.extractfile(member).read())
        case.root, case.store, case.artifacts = root, Store(root), ArtifactStore(root)
        # The released store holds the request IDs of its own commands; the next ones follow them.
        case.sequence = case.store.revision
        route = case.read_records()['review_route']['route']
        runtime = protocol.transport.runtime

        def read_installed_runtime(*args, **kwargs):
            # The store pins the runtime of its build (a neutral host and platform); it stands for the installed one.
            selected = args[0] if args else kwargs.get('route')
            return dict(route['runtime']) if selected is None or selected['id'] == 'route' else runtime(*args, **kwargs)

        installed = patch.object(protocol.transport, 'runtime', side_effect=read_installed_runtime)
        installed.start()
        self.addCleanup(installed.stop)
        install_bounded_binary_backend(case, [route])
        return case

    def test_the_fixture_holds_no_local_path(self):
        """The released stores were built under a neutral root; no file names a home folder."""
        import tarfile
        from pathlib import Path
        with tarfile.open(Path(__file__).with_name('fixtures') / self.ARCHIVE) as archive:
            for member in archive.getmembers():
                if member.isfile():
                    data = archive.extractfile(member).read()
                    for marker in (b'/Users/', b'/home/'):
                        self.assertEqual(data.count(marker), 0, (member.name, marker))

    def capture_then_record(self, case, identifier, reviewer, context, recorded_context=None):
        payload = dict(id=identifier, route_id='route', dossier_id=None, role='slate', reviewer_id=reviewer,
                       author_id='problem-author', context=context)
        revision = case.store.revision
        with self.assertRaises(ResearchError) as stopped:
            case.mutate(protocol.record_assignment, payload)
        self.assertEqual(stopped.exception.details['reason'], 'body_without_text_form')
        self.assertEqual(case.store.revision, revision)
        [step] = [step for answer in stopped.exception.details['then'] for step in answer['steps']]
        self.assertEqual(step['command'], 'artifact')
        (case.root / 'capture.txt').write_text('The bound synthetic original text.', encoding='utf-8')
        captured = case.mutate(pin_artifact, dict(step['payload'], path='capture.txt'))['result']
        self.assertEqual(captured['text_form_of'], step['body'])
        record = case.mutate(protocol.record_assignment, payload)['result']
        self.assertNotIn(parts.KEY, record)
        self.assertEqual(record['context'], context if recorded_context is None else recorded_context)
        return record

    def test_late_pdf_in_a_released_single_chain_is_captured_before_the_next_turn(self):
        self.capture_then_record(self.open_store('late-pdf-continuation'), 'slate-next', 'first',
                                 dict(prior_assignment_id='slate-stored'))

    def test_refresh_of_a_released_recorded_slate_captures_its_pdf_first(self):
        # The store binds the refresh to the recorded slate of the same slot, as a new chain (design slate-reviews,
        # section 9: a stored ordinary slate review is refreshed by a new chain).
        self.capture_then_record(self.open_store('pdf-refresh-of-recorded-slate'), 'slate-next', 'first', {},
                                 recorded_context=dict(session_refresh=dict(assignment_id='slate-first')))

    def test_repair_of_a_released_recorded_slate_captures_its_pdf_first(self):
        case = self.open_store('pdf-repair-of-recorded-slate')
        evidence = case.read_records()['review_context_event']['exposure']['evidence']
        self.capture_then_record(case, 'repaired-slate', 'repaired-first', dict(
            context_repair=dict(assignment_id='slate-first', event_ids=['exposure'], probe_id='repair-probe',
                                reason='Reassess the slate in a verified independent context.', evidence=evidence)))


class FixtureCleanupTests(unittest.TestCase):
    def test_failed_child_setup_is_cleaned_by_the_parent(self):
        from test_problem_forming_import import FormingImportTests, ProblemCase

        fixture = slate_fixtures.SlateSeriesTests()
        fixture.setUp()
        imports = FormingImportTests()
        for parent, factory, case_type in ((fixture, fixture.make_case, slate_fixtures.slate_fixtures.ProblemSlateTests),
                                           (imports, imports.fixture, ProblemCase)):
            with self.subTest(fixture=type(parent).__name__):
                self.addCleanup(parent.doCleanups)
                roots = []

                original_setup = case_type.setUp

                def fail(case):
                    original_setup(case)
                    self.addCleanup(case.doCleanups)
                    roots.append(case.store.root)
                    raise RuntimeError('fixture initialization failed')

                with patch.object(case_type, 'setUp', fail):
                    with self.assertRaises(RuntimeError):
                        factory()
                parent.doCleanups()
                self.assertFalse(roots[0].exists())
