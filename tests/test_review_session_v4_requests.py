"""Request validation and continuation under the session v4 rules."""

import ast
import copy
import importlib
import inspect
import json
import shutil
import sys
import unittest
from contextlib import ExitStack, contextmanager
from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter, process_time
from unittest.mock import patch

import test_review_session_v4_pages as pages
import test_review_session_v4_prompt as prompts
import test_review_session_v4_refresh as refresh
import test_review_session_v4_turns as turns
from codex_route_fixtures import build_codex_response, invoke_codex
from research_harness import (bar_sources, openness_final_pages, openness_reading_v7 as v7,
                              research_decisions as decisions, review_composition, review_continuation,
                              review_protocol as protocol, review_refusal, review_session_v4 as session)
from research_harness import review_route_capacity as capacity
from research_harness import cli, review_handles, review_refusal_notice, review_series
from research_harness.artifacts import ArtifactStore, describe_artifact
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.review_composite import read_final_output
from research_harness.review_excerpts import read_sent_packet
from research_harness.review_failure_class import plan_after_failure
from research_harness.storage import Store
from v7_capacity_fixtures import build_route

TURN_CODE = 'invalid_review_session_turn'
TURN_CHECK = 'review.session_turn'


def request_answer(*requests, **overrides):
    answer = dict(schema_version=1, stage='request', notes='Keep these notes.',
                  excerpts=[], requests=list(requests), settled_requests=[])
    return dict(answer, **overrides)


def request_source(version, depth):
    return dict(id='capture', kind='source', version_id=version, depth=depth)


def request_passage(unit, characters, anchor=None, identifier='passage'):
    return dict(id=identifier, kind='passage', unit=unit, characters=characters, anchor=anchor)


def next_bar(case, prior, identifier='next'):
    return case.case.assign('bar', 'first', identifier=identifier, prior_assignment_id=prior['id'])


class SessionRequestTests(unittest.TestCase):
    def setUp(self):
        preferred = patch.object(protocol, 'NEW_SESSION_RULES', (session.PROTOCOL, bar_sources.PROTOCOL_V3))
        preferred.start()
        self.addCleanup(preferred.stop)

    def make_case(self, fixture=turns.SessionTurnsTests):
        case = fixture()
        self.addCleanup(case.doCleanups)
        case.setUp()
        return case

    def test_failed_nested_setup_runs_its_registered_cleanup(self):
        cleaned = []

        def fail_after_setup_started(case):
            case.addCleanup(cleaned.append, True)
            raise RuntimeError('Fixture setup stopped.')

        with patch.object(turns.problem_fixtures.ProblemReviewTests, 'setUp', fail_after_setup_started):
            with self.assertRaisesRegex(RuntimeError, 'Fixture setup stopped'):
                self.make_case()
        self.doCleanups()
        self.assertEqual(cleaned, [True])

    def assert_bound(self, case, assignment, records):
        request = protocol.build_assignment_request(records, case.artifacts, assignment)
        route = records['review_route'][assignment['route_id']]
        packet = protocol._read_json(case.artifacts, assignment['packet'])
        bound = session.read_session_binding(packet)['capacity']['request_bound']
        self.assertLessEqual(capacity.measure_request(route, request)['text_characters'], bound)

    def assert_turn_refusal(self, error, path, **details):
        self.assertEqual((error.code, getattr(error, 'contract_check', None)), (TURN_CODE, TURN_CHECK))
        self.assertEqual(error.details, dict(path=path, **details))

    def next_action(self, case, assignment):
        return review_continuation.plan_next(case.case.read_records(), case.case.artifacts, assignment['id'])

    def write_action(self, case, action):
        self.assertEqual(action.get('command'), 'review-assignment', action)
        revision = case.case.store.revision
        written = case.case.mutate(protocol.record_assignment, action['payload'])['result']
        self.assertGreater(case.case.store.revision, revision)
        return written

    def run_action(self, case, assignment, answer, *, over_cap=False, output_tokens=None, action=None):
        action = action if action is not None else self.next_action(case, assignment)
        self.assertEqual((action['state'], action.get('command')), ('run', 'review-run'), action)
        revision = case.case.store.revision
        failed_stub = []

        def respond(route, request, credential):
            try:
                packet = read_sent_packet(request)
                form = session.find_session_form(packet)
                if form in ('final', 'request', 'page', 'closing'):
                    phrase = 'this turn is the final turn' if form == 'final' else 'request form'
                    self.assertIn(phrase, request['instructions'].lower())
                response = build_codex_response(route, request, {} if over_cap else answer(packet))
            except Exception as error:
                failed_stub.append(error)
                raise
            if over_cap:
                response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
            elif output_tokens is not None:
                response['usage']['output_tokens'] = output_tokens
            return response

        with patch.object(protocol.transport, 'send_request', side_effect=respond) as sender:
            case.case.mutate(protocol.invoke_assignment, action['payload'], credential='test-only')
            sender.assert_called_once()
        if failed_stub:
            raise failed_stub[0]
        self.assertGreater(case.case.store.revision, revision)
        return action

    @contextmanager
    def cloned_store(self, case, source):
        fixture = case.case
        original = fixture.root, fixture.store, fixture.artifacts, fixture.sequence
        with TemporaryDirectory() as directory:
            root = shutil.copytree(source, Path(directory) / 'study')
            fixture.root, fixture.store, fixture.artifacts = root, Store(root), ArtifactStore(root)
            try:
                yield
            finally:
                fixture.root, fixture.store, fixture.artifacts, fixture.sequence = original

    def stop_action(self, case, assignment, action):
        from research_harness.review_stop import stop_review
        from research_harness.stop_classes import STOP_REGISTRY
        self.assertIn(action.get('code'), STOP_REGISTRY)
        self.assertEqual((action['state'], action.get('command')), ('stop', 'review-stop'), action)
        self.assertEqual(action['details']['step'], dict(command='review-stop', payload=action['payload']))
        revision = case.case.store.revision
        stop = case.case.mutate(stop_review, action['payload'])['result']
        self.assertGreater(case.case.store.revision, revision)
        self.assertTrue(stop['boundary_stands'])
        self.assertEqual(stop['id'], action['payload']['id'])
        stopped = self.next_action(case, assignment)
        self.assertEqual((stopped.get('code'), stopped['details']['stop_id']), ('review_chain_stopped', stop['id']))
        return stop

    def record_action(self, case, assignment, action):
        self.assertEqual((action['state'], action.get('command')), ('record', 'value-review'), action)
        revision = case.case.store.revision
        prior = case.case.read_records()
        selected = next((row for row in prior.get('review_composite', {}).values()
                         if row['closing']['assignment_id'] == assignment['id']), None)
        saved = case.case.mutate(decisions.record_value_review, action['payload'])['result']
        self.assertGreater(case.case.store.revision, revision)
        self.assertEqual(saved['assignment_id'], assignment['id'])
        if selected is not None:
            self.assertEqual(saved['composite']['id'], selected['id'])
        self.assertEqual(self.next_action(case, assignment)['state'], 'done')
        return saved

    def visible_bar_output(self, packet, evidence, targets):
        objections = [dict(id=row['id'], status='continuing', reason='A gap remains.', evidence=[evidence])
                      for row in targets]
        output = dict(stage='bar', support=None, objections=[], limitations=['The supplied evidence is bounded.'],
                      value=dict(status='unresolved', consequence='The comparison remains open.', bar_ids=[],
                                 objective_adequacy='not_assessed', method_adequacy='not_assessed',
                                 reason='A gap remains.', evidence=[evidence], work_items=[], assurances=[],
                                 objection_findings=objections))
        form = session.find_session_form(packet)
        if form == 'final':
            return output
        binding = session.read_session_binding(packet)
        context = dict(subject=packet['subject'], session_targets=targets,
                       historical_reviews=[dict(objections=objections)])
        rule = review_composition.find_rule('bar', context)
        pages_output, closing = review_composition.split_output(
            rule, context, output, [binding.get('page', {}).get('targets', [])])
        return closing if form == 'closing' else pages_output[0]['output']

    def drive_to_review(self, case, current, request_answerer, scientific_answer, request_id, check_delivery):
        """Take each planned write and run, then the selected recording or stop.

        check_delivery gets the rows for request_id and their evidence entries in the packets that the reviewer
        received, with the authenticated state of the first turn after the request, and asserts that they serve
        it. A stop returns the planned action with the written stop."""
        commands = []
        requested = False
        received = []
        consumed = set()

        def progress(assignment):
            packet = case.read_packet(assignment)
            binding = session.read_session_binding(packet)
            _, _, state = session.read_bound_session(case.case.artifacts, assignment)
            return (session.find_session_form(packet), binding.get('final_turn'),
                    tuple(binding.get('page', {}).get('targets', [])),
                    {row['id'] for row in state['requests'] if row['status'] == 'delivered'},
                    {(row['unit'], row['start'], row['end']) for row in state['deliveries']},
                    [row['id'] for row in state['pending_requests']],
                    binding.get('plan', {}).get('now', []))

        def observe(answer):
            def respond(packet):
                for row in session.read_session_binding(packet).get('plan', {}).get('now', []):
                    if row.get('request_id') == request_id:
                        content = (session.serialize_record(row['page']).decode('utf-8') if 'page' in row else
                                   '{}' if row['kind'] == 'request_status' else None)
                        received.append((row, next(entry for entry in packet['evidence'] if (
                            entry['artifact']['sha256'] == row['sha256'] if content is None
                            else entry['content'] == content))))
                return answer(packet)
            return respond

        asked = None
        before = progress(current)
        for _ in range(24):
            action = self.next_action(case, current)
            command = action.get('command'), action.get('payload')
            self.assertNotIn(command, commands)
            commands.append(command)
            if action['state'] == 'stop' or action['state'] == 'handoff':
                return 'stop', (action, self.stop_action(case, current, action))
            if action['state'] == 'record':
                self.assertTrue(received and received[-1][0]['request_complete'], received)
                check_delivery(received, asked)
                return 'record', self.record_action(case, current, action)
            if action['command'] == 'review-assignment':
                current = self.write_action(case, action)
                if asked is None:
                    asked = session.read_bound_session(case.case.artifacts, current)[2]
                after = progress(current)
                self.assertNotEqual(before, after, 'a successor repeated the same semantic position and work')
                self.assertTrue(after[:3] != before[:3] or after[3] - before[3] or after[4] - before[4] or
                                any(digest(row) not in consumed for row in after[6]),
                                'the successor neither delivered material, closed work nor advanced the position')
                before = after
                continue
            self.assertEqual(action['command'], 'review-run')
            form, _, _, _, _, pending, now = before
            completed = {row['request_id'] for row in now if row.get('request_complete')}
            response = (request_answerer if not requested else
                        (lambda packet: request_answer(notes='n')) if form == 'request' or set(pending) - completed
                        else scientific_answer)
            consumed.update(map(digest, now))
            records = case.case.read_records()
            self.assertNotIn(action['payload']['id'], records.get('review_attempt', {}))
            self.run_action(case, current, observe(response), action=action)
            records = case.case.read_records()
            self.assertEqual(records['review_attempt'][action['payload']['id']]['status'], 'completed')
            self.assertNotIn(action['payload']['id'], records.get('review_output_refusal', {}))
            requested = True
        self.fail('The public commands did not record the review or take a terminal step')

    def drive_request(self, case, current, answer, request_id, *, resume=False, notes='n', output_tokens=None):
        """Follow the public steps from the answer through authenticated delivery."""
        actions = [self.run_action(case, current, answer, output_tokens=output_tokens)]
        original = case.read_packet(current)
        started_in = session.find_session_form(original)
        for _ in range(6):
            action = self.next_action(case, current)
            self.assertNotIn(action, actions)
            actions.append(action)
            if action['state'] in ('stop', 'handoff'):
                self.stop_action(case, current, action)
                return None
            current = self.write_action(case, action)
            packet = case.read_packet(current)
            form = session.find_session_form(packet)
            now = session.read_session_binding(packet).get('plan', {}).get('now', [])
            deliveries = [row for row in now if row.get('request_id') == request_id]
            self.assertTrue(deliveries or now or form != started_in,
                            (form, now, action['payload']))
            actions.append(self.run_action(case, current, lambda packet: request_answer(notes=notes),
                                           output_tokens=output_tokens))
            plan, inventory, _ = session.read_bound_session(case.case.artifacts, current)
            records = case.case.read_records()
            route = records['review_route'][current['route_id']]
            state = session.rebuild_session_state(records, case.case.artifacts, current, inventory, plan, route)
            if not any(row['id'] == request_id for row in state['pending_requests']):
                self.assertTrue(any(row['id'] == request_id and row['status'] == 'delivered'
                                    for row in state['requests']))
                if resume:
                    position = session.read_session_binding(original)
                    current = self.write_action(case, self.next_action(case, current))
                    self.assertEqual(session.find_session_form(case.read_packet(current)), started_in)
                    restored = session.read_session_binding(case.read_packet(current))
                    self.assertEqual((restored['final_turn'], restored['page']['targets']),
                                     (position['final_turn'], position['page']['targets']))
                    self.run_action(case, current, lambda packet: request_answer(notes=notes),
                                    output_tokens=output_tokens)
                return deliveries
        self.fail('The public series failed to close the request within six turns')

    def small_request_view(self, units=()):
        route = build_route('codex_cli_v1', 'gpt-6-astra')
        plan = dict(route_class=dict(adapter=route['adapter'], model=route['model'], effort='high'),
                    limits=dict(output_tokens=dict(value=route['configuration']['max_output_tokens'])),
                    targets=dict(handles=['t1']), allocation=dict(notes=32, targets=[], session_source_rows=True))
        inventory = dict(units=list(units))
        state = dict(requests=[], pending_requests=[], deliveries=[], turns=[], notes=[], excerpts=[],
                     settled_requests=[], allowances=dict(passages=dict(characters=100000, used=0), targets=[]))
        packet = dict(session_turn=dict(plan=dict(now=[]), allowances=dict(notes=32)), evidence=[])
        return route, plan, inventory, state, packet

    def test_request_form_refuses_at_the_member_with_compared_values(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        artifacts = ArtifactStore(Path(directory.name))
        capture = artifacts.put(b'A delivered abstract.', 'text/plain')
        version, unit = 'arxiv:2601.00001v1', 'u1'
        _, plan, inventory, state, packet = self.small_request_view([
            dict(handle=unit, kind='abstract', identity=dict(version_id=version), artifact=capture,
                 sha256=capture['sha256'])])
        excerpt = dict(unit=unit, text='undelivered', purpose='For reasoning.', targets=['t1'])
        notes_cap = packet['session_turn']['allowances']['notes']
        cases = [
            ({}, '', dict(expected_type='object', received_type='object', unexpected_fields=[],
                           missing_fields=sorted(request_answer()))),
            (request_answer(schema_version=True), '/schema_version', {}),
            (request_answer(stage='wrong'), '/stage', {}),
            (request_answer(notes=0), '/notes', dict(expected_type='string', received_type='number')),
            (request_answer(notes='x' * (notes_cap + 1)), '/notes', dict(notes_cost=notes_cap + 1, bound=notes_cap)),
            (request_answer(excerpts={}), '/excerpts', dict(expected_type='array', received_type='object')),
            (request_answer(requests=None), '/requests', dict(expected_type='array', received_type='null')),
            (request_answer(settled_requests=True), '/settled_requests',
             dict(expected_type='array', received_type='boolean')),
            (request_answer(excerpts=[{}]), '/excerpts/0',
             dict(expected_type='object', received_type='object', missing_fields=sorted(excerpt), unexpected_fields=[])),
            (request_answer(excerpts=[dict(excerpt, unit='unknown')]), '/excerpts/0/unit', dict(unit='unknown')),
            (request_answer(excerpts=[dict(excerpt, text='')]), '/excerpts/0/text',
             dict(characters=0, minimum_characters=1, maximum_characters=6000)),
            (request_answer(excerpts=[dict(excerpt, purpose='x' * 201)]), '/excerpts/0/purpose',
             dict(characters=201, maximum_characters=200)),
            (request_answer(excerpts=[dict(excerpt, targets=[])]), '/excerpts/0/targets',
             dict(items=0, minimum_items=1)),
            (request_answer(excerpts=[dict(excerpt, targets=['t1', 't1'])]), '/excerpts/0/targets/1',
             dict(target='t1')),
            (request_answer(excerpts=[excerpt]), '/excerpts/0/text', dict(unit=unit)),
            (request_answer(settled_requests=['missing']), '/settled_requests/0', dict(request_id='missing')),
            (request_answer({}), '/requests/0',
             dict(expected_type='object', received_type='object', missing_fields=['id', 'kind'], unexpected_fields=[])),
            (request_answer(dict(id='q' * 121, kind='unit', unit=unit)), '/requests/0/id',
             dict(characters=121, minimum_characters=1, maximum_characters=120)),
            (request_answer(dict(id='', kind='unit', unit=unit)), '/requests/0/id',
             dict(characters=0, minimum_characters=1, maximum_characters=120)),
            (request_answer(dict(id='x', kind='unit', unit=unit), dict(id='x', kind='unit', unit=unit)),
             '/requests/1/id', dict(request_id='x')),
            (request_answer(request_source(version, 'summary')), '/requests/0/depth', {}),
            (request_answer(request_source('unknown', 'abstract')), '/requests/0/version_id', {}),
            (request_answer(dict(id='x', kind='artifact', sha256='0' * 64)), '/requests/0/sha256', {}),
            (request_answer(dict(id='x', kind='unit', unit=[])), '/requests/0/unit', dict(unit=[])),
            (request_answer(request_passage(unit, 0)), '/requests/0/characters',
             dict(requested=0, remaining=100000, minimum_characters=1, maximum_characters=100000)),
            (request_answer(request_passage(unit, 1, [])), '/requests/0/anchor',
             dict(expected_type='string', received_type='array')),
            (request_answer(request_passage(unit, 1, '')), '/requests/0/anchor',
             dict(characters=0, minimum_characters=1, maximum_characters=600)),
            (request_answer(request_passage(unit, 1, 'not in the abstract')), '/requests/0/anchor',
             dict(unit=unit, predicate='occurs_in_unit')),
            (request_answer(dict(id='x', kind='inventory_page', page=True)), '/requests/0/page',
             dict(minimum_page=0, maximum_page=0)),
            (request_answer(dict(id='x', kind='list_page', list=[], after=None)), '/requests/0/list', dict(list=[])),
            (request_answer(dict(id='x', kind='list_page', list='allowances', after=[])),
             '/requests/0/after', dict(list='allowances')),
            (request_answer(dict(id='x', kind='unknown')), '/requests/0/kind', {}),
        ]
        for answer, path, details in cases:
            with self.subTest(path=path, details=details):
                with patch.object(session, 'read_bound_session', return_value=(plan, inventory, state)):
                    with self.assertRaises(ResearchError) as caught:
                        session.validate_request_output(artifacts, {}, packet, answer)
                self.assert_turn_refusal(caught.exception, path, **details)

    def test_turn_checks_supply_a_pointer_and_registered_literal_notice_fields(self):
        registered = review_refusal.CONTRACT_CHECKS.get(TURN_CHECK)
        self.assertIsNotNone(registered)
        self.assertIn(TURN_CHECK, inspect.getsource(session._require_turn))
        calls = []
        for module in (session, decisions):
            for node in ast.walk(ast.parse(inspect.getsource(module))):
                if isinstance(node, ast.Call) and (
                        getattr(node.func, 'id', None) == '_require_turn'
                        or getattr(node.func, 'attr', None) == '_require_turn'):
                    calls.append(node)
        self.assertTrue(calls)
        for node in calls:
            with self.subTest(line=node.lineno):
                names = {keyword.arg for keyword in node.keywords if keyword.arg is not None}
                self.assertIn('path', names)
                self.assertLessEqual(names - {'path'}, set(registered['notice_fields']))

    def test_reviewer_corrects_a_refused_request_from_the_replacement_notice(self):
        case = self.make_case()
        case.case.metadata(1)
        first = case.case.assign('bar', 'first', identifier='first')
        self.assert_bound(case.case, first, case.case.read_records())
        answer = request_answer(dict(id='unknown', kind='unit', unit='u999'))
        invoke_codex(case.case, first['id'], answer)
        step = plan_after_failure(case.case.read_records(), case.case.artifacts, first['id'])
        self.assertEqual(step['state'], 'replace')
        replacement = case.case.mutate(protocol.record_assignment, step['payload'])['result']
        self.assert_bound(case.case, replacement, case.case.read_records())

        def correct_from_notice(route, request, credential):
            text = request['input'][-1]['content']
            packet = json.loads(text[len(protocol.transport.PACKET_LABEL):])
            notice = packet['output_refusal']
            finding = notice['refused'][-1]
            self.assertEqual((finding['code'], finding['check'], finding['path']),
                             (TURN_CODE, TURN_CHECK, '/requests/0/unit'))
            part = notice['refused_part']
            self.assertEqual(part, dict(path=finding['path'], complete=True, value='u999'))
            self.assertEqual(finding['details'], dict(unit=part['value']))
            self.assertNotIn('output', notice)
            # This repair handles the unknown-unit finding using a visible inventory request.
            repairs = {'/requests/0/unit': dict(id='inventory', kind='inventory_page', page=0)}
            corrected = request_answer(repairs[finding['path']])
            return build_codex_response(route, request, corrected)

        with patch.object(protocol.transport, 'send_request', side_effect=correct_from_notice):
            case.case.mutate(protocol.invoke_assignment, dict(id='corrected-attempt', assignment_id=replacement['id']),
                             credential='test-only')
        index = next_bar(case, replacement, 'inventory')
        listing, = case.read_packet(index)['session_turn']['plan']['now']
        unit = next(item['handle'] for item in listing['page']['rows'] if item['kind'] == 'abstract')
        case.respond(index, [dict(id='capture', kind='unit', unit=unit)])
        delivery = next_bar(case, index, 'delivery')
        row, = case.read_packet(delivery)['session_turn']['plan']['now']
        self.assertEqual((row['kind'], row['request_id']), ('abstract', 'capture'))
        case.respond(delivery)
        final = next_bar(case, delivery, 'final')
        self.assertIn('session_final', case.read_packet(final))
        self.assert_bound(case.case, final, case.case.read_records())
        case.finish(final, [dict(ref=row['unit'])])

    def test_page_excerpt_creates_a_refusal_record(self):
        case = self.make_case(pages.SessionPagesTests)
        version = case.case.metadata(1)
        page = case.start()
        delivery = case.advance(page, request_answer(request_source(version, 'abstract')))
        packet = case.read_packet(delivery)
        binding = packet['session_final']
        excerpt = dict(unit=binding['plan']['now'][0]['unit'], text=packet['evidence'][0]['content'][:20],
                       purpose='Retain.', targets=binding['page']['targets'][:1])
        invoke_codex(case.case, delivery['id'], request_answer(excerpts=[excerpt]), 'page-excerpt')
        refusal = case.case.read_records().get('review_output_refusal', {}).get('page-excerpt')
        self.assertIsNotNone(refusal)
        details = refusal['details']
        self.assertEqual((refusal['code'], refusal['check'], details['path'], details['items'], details['bound']),
                         (TURN_CODE, TURN_CHECK, '/excerpts', 1, 0))

    def test_page_answer_while_a_request_is_open_names_the_pending_id(self):
        route = build_route()
        state = dict(pending_requests=[dict(id='capture')])
        packet = dict(session_final=dict(page=dict(targets=['t1']), plan=dict(now=[])))
        output = dict(targets=[])
        assignment = dict(id='page', route_id='route')
        with TemporaryDirectory() as directory:
            artifacts = ArtifactStore(Path(directory))
            with patch.object(session, 'read_bound_session', return_value=(None, None, state)):
                with self.assertRaises(ResearchError) as caught:
                    session.validate_final_turn_output(dict(review_route=dict(route=route)),
                                                       artifacts, assignment, packet, output, expanded=True)
            error = caught.exception
            self.assert_turn_refusal(error, '', pending_requests=['capture'])
            diagnosis = dict(code=error.code, check=error.contract_check,
                             details=dict(error.details, message=error.message))
            certificate = dict(assignment_id='page', attempt_id='attempt', output_digest=digest(output),
                               recorded_revision=1)
            records = dict(review_output_refusal=dict(attempt=certificate),
                           review_attempt=dict(attempt=dict(output=protocol._put_json(artifacts, output))))
            notice = review_refusal_notice.build_refusal_notice(
                records, artifacts, ('attempt',), form='bounded', diagnoses=dict(attempt=diagnosis))
        self.assertEqual(notice['refused'][0]['details'], dict(pending_requests=['capture']))
        self.assertEqual(notice['refused_part'], dict(path='', complete=True, value=output))

    def test_single_final_reports_unexpected_request_members(self):
        case = self.make_case()
        final, row, _ = self.deliver_abstract(case)
        self.assertNotIn('page_plan_digest', case.read_packet(final)['session_final'])
        answer = case.case.build_review_output('bar')
        answer['value'].update(status='unresolved', evidence=[dict(ref=row['unit'])])
        with self.assertRaises(ResearchError) as caught:
            decisions.validate_review_contract(case.case.read_records(), case.case.artifacts, final,
                                               dict(answer, schema_version=1))
        self.assert_turn_refusal(caught.exception, '', unexpected_fields=['schema_version'])
        mixed = dict(answer, schema_version=1, notes='Notes', requests=[dict(ref='u999')], excerpts=[],
                     settled_requests=[], source_requests=[])
        with patch.object(review_handles, '_expand_value', wraps=review_handles._expand_value) as expanded:
            invoke_codex(case.case, final['id'], mixed)
            self.assertFalse(any(call.args[1] == mixed for call in expanded.call_args_list))
        certificate = case.case.read_records()['review_output_refusal']['attempt-' + final['id']]
        self.assertEqual((certificate['code'], certificate['check'], certificate['details']['path']),
                         (TURN_CODE, TURN_CHECK, ''))
        step = plan_after_failure(case.case.read_records(), case.case.artifacts, final['id'])
        replacement = case.case.mutate(protocol.record_assignment, step['payload'])['result']
        notice = case.read_packet(replacement)['output_refusal']
        self.assertEqual(notice['refused'][0]['details']['unexpected_fields'],
                         ['excerpts', 'notes', 'requests', 'schema_version', 'settled_requests', 'source_requests'])
        self.assert_bound(case.case, replacement, case.case.read_records())
        case.finish(replacement, [dict(ref=row['unit'])])

    def test_list_cursor_uses_session_contract_and_preserves_cursor_details(self):
        case = self.make_case()
        first = case.case.assign('bar', 'first', identifier='first')
        case.respond(first, [dict(id='list-1', kind='list_page', list='allowances', after=None)])
        delivery = next_bar(case, first)
        packet = case.read_packet(delivery)
        page = packet['session_turn']['plan']['now'][0]['page']
        self.assertTrue(page['rows'])
        answer = request_answer(dict(id='list-2', kind='list_page', list='allowances', after=None))
        details = dict(ended=True) if page['next'] is None else dict(expected_after=page['next'])
        with self.assertRaises(ResearchError) as caught:
            session.validate_request_output(case.case.artifacts, delivery, packet, answer)
        self.assert_turn_refusal(caught.exception, '/requests/0/after', list='allowances', after=None, **details)

    def test_assignment_id_reports_its_length_as_invalid_input(self):
        with self.assertRaises(ResearchError) as caught:
            session.prepare_session_assignment({}, None, dict(id='x' * 121), {}, 1)
        self.assertEqual((caught.exception.code, caught.exception.details),
                         ('invalid_input', dict(characters=121, maximum_characters=120)))

    def test_capacity_replacement_refuses_a_different_binding(self):
        parent = dict(id='prior', role='bar', dossier_id=None, route_id='route', reviewer_id='a', author_id='b')
        for key in ('role', 'dossier_id', 'route_id', 'reviewer_id', 'author_id'):
            with self.subTest(key=key):
                with self.assertRaises(ResearchError) as caught:
                    session.prepare_capacity_assignment({}, None, dict(parent, **{key: 'different'}), 2, parent)
                self.assertEqual(caught.exception.code, 'review_source_continuation_mismatch')

    def test_capacity_replacement_refuses_a_turn_without_a_plan_to_replace_it(self):
        case = self.make_case()
        first = case.case.assign('bar', 'first', identifier='first')
        with self.assertRaises(ResearchError) as caught:
            session.prepare_capacity_assignment(case.case.read_records(), case.case.artifacts, first,
                                                case.case.store.revision + 1, first)
        self.assertEqual(caught.exception.code, 'invalid_review_context')

    def test_planned_successors_and_refresh_of_a_maximum_length_root_are_writable(self):
        case = self.make_case(refresh.SessionRefreshTests)
        version = case.case.metadata(1)
        current = case.case.assign('bar', 'first', identifier='a' * 120)
        case.respond(current, [request_source(version, 'abstract')])
        ids = {current['id']}
        for _ in range(2):
            step = case.plan(current['id'])
            self.assertEqual((step['state'], step['command']), ('continue', 'review-assignment'))
            self.assertLessEqual(len(step['payload']['id']), 120)
            current = case.record(step['payload'])
            self.assertNotIn(current['id'], ids)
            ids.add(current['id'])
            if session.find_session_kind(case.read_packet(current)) == 'request':
                delivery, = case.read_packet(current)['session_turn']['plan']['now']
                case.respond(current)
        self.assertEqual(session.find_session_kind(case.read_packet(current)), 'final')
        self.assert_bound(case.case, current, case.case.read_records())
        case.finish(current, [dict(ref=delivery['unit'])])
        case.add_area()
        step = case.plan(current['id'])
        self.assertEqual((step['state'], step['command']), ('continue', 'review-assignment'))
        self.assertLessEqual(len(step['payload']['id']), 120)
        restarted = case.record(dict(step['payload'], author_id='AUTHOR', reviewer_id='FIRST'))
        self.assertNotIn(restarted['id'], ids)
        case.assert_new_chain_in_slot(restarted, current)
        self.assertEqual((restarted['author_id'], restarted['reviewer_id']), ('author', 'first'))

    def test_scientific_refusal_requires_the_notice_path_and_a_bounded_id(self):
        for code in ('invalid_input', 'invalid_review_context'):
            with self.subTest(code=code):
                case = self.make_case()
                parent = case.case.assign('bar', 'first', identifier='root')
                self.assert_bound(case.case, parent, case.case.read_records())
                invoke_codex(case.case, parent['id'], request_answer(dict(id='bad', kind='unit', unit='u999')))
                step = plan_after_failure(case.case.read_records(), case.case.artifacts, parent['id'])
                payload = step['payload']
                self.assertEqual(payload['context'], dict(output_refusal=dict(assignment_id=parent['id'])))
                changed = (dict(payload, id='a' * 121) if code == 'invalid_input' else
                           dict(payload, context=dict(capacity_replacement=parent['id'])))
                revision = case.case.store.revision
                with self.assertRaises(ResearchError) as caught:
                    case.case.mutate(protocol.record_assignment, changed)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(case.case.store.revision, revision)
                replacement = case.case.mutate(protocol.record_assignment, payload)['result']
                packet = case.read_packet(replacement)
                notice = packet.pop('output_refusal')
                self.assertEqual(notice['refused'][-1]['assignment_id'], parent['id'])
                self.assertEqual(notice['refused'][-1]['path'], '/requests/0/unit')
                with self.assertRaises(ResearchError) as caught:
                    session.authenticate_session_binding(case.case.read_records(), case.case.artifacts, replacement, packet)
                self.assertEqual(caught.exception.code, 'review_invocation_unverified')

    def test_planned_children_share_parent_identity_and_only_one_capacity_child(self):
        case = self.make_case(pages.SessionPagesTests)
        turns.SessionTurnsTests.install_measured_profile(case)
        root = case.case.assign('bar', 'first', identifier='r' * 120)
        for child_kind in ('successor', 'capacity', 'scientific'):
            with self.subTest(child=child_kind), self.cloned_store(case, case.case.root):
                if child_kind == 'capacity':
                    for _ in range(2):
                        self.run_action(case, root, lambda packet: request_answer(), over_cap=True)
                        if self.next_action(case, root)['state'] == 'replace':
                            break
                else:
                    def answer(packet):
                        if child_kind == 'scientific':
                            return request_answer(dict(id='bad', kind='unit', unit='u999'))
                        return request_answer(dict(id='list', kind='list_page', list='allowances', after=None))
                    self.run_action(case, root, answer)
                action = self.next_action(case, root)
                self.assertEqual(action['state'], 'continue' if child_kind == 'successor' else 'replace')
                payload = action['payload']
                self.assertLessEqual(len(payload['id']), 120)
                child = self.write_action(case, dict(action, payload=dict(
                    payload, author_id=payload['author_id'].upper(), reviewer_id=payload['reviewer_id'].upper())))
                self.assert_bound(case.case, child, case.case.read_records())
                if child_kind == 'capacity':
                    revision = case.case.store.revision
                    with self.assertRaises(ResearchError) as caught:
                        case.case.mutate(protocol.record_assignment, dict(payload, id='sibling'))
                    self.assertEqual(caught.exception.code, 'review_assignment_replaced')
                    self.assertEqual(case.case.store.revision, revision)
                if child_kind == 'scientific':
                    self.assertIn('output_refusal', case.read_packet(child))
                self.run_action(case, child, lambda packet: request_answer())
                self.assertEqual((child['author_id'], child['reviewer_id']), (root['author_id'], root['reviewer_id']))

    def test_passage_of_an_empty_delivered_artifact_closes_with_a_status(self):
        case = self.make_case()
        empty = case.case.artifacts.put(b'', 'text/plain')
        build = protocol.build_packet

        def build_with_empty_artifact(*args, **kwargs):
            packet = build(*args, **kwargs)
            packet['historical_reviews'] = [dict(id='previous', objections=[
                dict(id='obj', claim='A synthetic gap.', severity='material', evidence=[empty])])]
            packet['digest'] = digest({k: v for k, v in packet.items() if k != 'digest'})
            return packet

        with patch.object(protocol, 'build_packet', side_effect=build_with_empty_artifact):
            root = case.case.assign('bar', 'first', identifier='root')
        _, inventory, _ = session.read_bound_session(case.case.artifacts, root)
        unit = next(row['handle'] for row in inventory['units'] if row['characters'] == 0 and row['kind'] == 'artifact')
        case.respond(root, [request_passage(unit, 1, identifier='capture')])
        next_turn = next_bar(case, root)
        self.assert_unavailable(case.read_packet(next_turn))
        case.respond(next_turn)
        self.assertEqual(session.find_session_kind(case.read_packet(next_bar(case, next_turn, 'final'))), 'final')

    def test_invalid_note_content_keeps_its_predicate_in_the_replacement_notice(self):
        for notes, predicate in (('   ', 'nonblank'), ('normal\x00suffix', 'no_nul')):
            with self.subTest(predicate=predicate):
                case = self.make_case()
                first = case.case.assign('bar', 'first', identifier='root')
                self.assert_bound(case.case, first, case.case.read_records())
                invoke_codex(case.case, first['id'], request_answer(notes=notes))
                step = plan_after_failure(case.case.read_records(), case.case.artifacts, first['id'])
                replacement = case.case.mutate(protocol.record_assignment, step['payload'])['result']
                notice = case.read_packet(replacement)['output_refusal']
                item, = notice['refused']
                self.assertEqual((item['code'], item['check'], item['path']), (TURN_CODE, TURN_CHECK, '/notes'))
                self.assertEqual(notice['refused_part']['value'], notes)
                self.assertEqual(item['details']['predicate'], predicate)

    def test_list_progress_and_successor_cursor_fit_the_delivery_pair(self):
        case = self.make_case(pages.SessionPagesTests)
        build = protocol.build_packet

        def with_objections(*args, **kwargs):
            packet = build(*args, **kwargs)
            objections = [dict(id='objection-%d' % number, claim='A gap.', severity='material',
                               evidence=[case.case.supporting]) for number in range(1150)]
            packet['historical_reviews'] = [dict(id='prior', objections=objections)]
            packet['digest'] = digest({key: value for key, value in packet.items() if key != 'digest'})
            return packet

        with patch.object(protocol, 'build_packet', side_effect=with_objections):
            current = case.case.assign('bar', 'first', identifier='root')

        first = dict(id='allow', kind='list_page', list='allowances', after=None)
        self.run_action(case, current, lambda packet: request_answer(first))
        current = self.write_action(case, self.next_action(case, current))
        packet = case.read_packet(current)
        binding = packet['session_turn']
        row = next(row for row in binding['plan']['now'] if row.get('request_id') == 'allow')
        page = row['page']
        cursor = page['next']
        self.assertTrue(cursor)
        self.assertTrue(row['request_complete'])
        plan, _, state = session.read_bound_session(case.case.artifacts, current)
        following = plan['targets']['handles'][len(page['rows'])]
        wanted = next(entry for entry in state['allowances']['targets'] if entry['handle'] == following)
        longer = dict(page, rows=page['rows'] + [dict(wanted, id=following)],
                      next=following if page['remaining'] > 1 else None, remaining=page['remaining'] - 1)
        content = session.serialize_record(longer)
        item = dict(artifact=describe_artifact(content, 'application/json'),
                    content=content.decode('utf-8'), encoding='utf-8')
        longer_row = dict(row, page=longer, characters=len(item['content']))
        route = case.case.read_records()['review_route']['route']
        self.assertGreater(capacity.measure_capacity_value(route, dict(row=longer_row, evidence=item)) + 2,
                           binding['capacity']['material_allowance'])
        follow = dict(first, id='follow', after=cursor)
        with self.assertRaises(ResearchError) as caught:
            session.validate_request_output(case.case.artifacts, current, packet,
                                            request_answer(dict(follow, after=None)))
        self.assert_turn_refusal(caught.exception, '/requests/0/after', list='allowances', after=None,
                                 expected_after=cursor)
        self.run_action(case, current, lambda packet: request_answer(follow))
        current = self.write_action(case, self.next_action(case, current))
        row = next(row for row in case.read_packet(current)['session_turn']['plan']['now']
                   if row.get('request_id') == 'follow')
        self.assertEqual((row['page']['after'], row['page']['next'], row['page']['remaining']), (cursor, None, 0))
        self.assertEqual([entry['id'] for entry in page['rows'] + row['page']['rows']], plan['targets']['handles'])
        for _ in range(5):
            self.run_action(case, current, lambda packet: request_answer(notes='n'))
            current = self.write_action(case, self.next_action(case, current))
            self.assert_bound(case.case, current, case.case.read_records())
            if session.find_session_form(case.read_packet(current)) != 'request':
                self.assertTrue(session.authenticate_session_binding(case.case.read_records(), case.case.artifacts,
                                                                    current, case.read_packet(current)))
                break
        else:
            self.fail('The public continuation never reached an authenticated final turn')
        _, _, state = session.read_bound_session(case.case.artifacts, current)
        self.assertFalse(state['pending_requests'])

    def test_page_list_progress_uses_the_residual_delivery_grant(self):
        case = self.make_case(pages.SessionPagesTests)
        page = case.start()
        self.assert_bound(case.case, page, case.case.read_records())
        with patch.object(session, '_pack_session_deliveries', wraps=session._pack_session_deliveries) as packed:
            self.run_action(case, page, lambda packet: request_answer(
                dict(id='allow', kind='list_page', list='allowances', after=None)))
            delivery = self.write_action(case, self.next_action(case, page))
            packet = case.read_packet(delivery)
            binding = packet['session_final']
            row, = binding['plan']['now']
            entry, = packet['evidence']
            route = case.case.read_records()['review_route']['route']
            grant = max(0, binding['capacity']['material_allowance'] - session.measure_final_material(route, binding) - 256)
            pending = [call.args[-1] for call in packed.call_args_list
                       if any(row['id'] == 'allow' for row in call.args[3]['pending_requests'])]
            self.assertTrue(pending)
            self.assertEqual(set(pending), {grant})
            self.assertLessEqual(capacity.measure_capacity_value(route, dict(row=row, evidence=entry)) + 2, grant)
            self.assertEqual(json.loads(entry['content']), row['page'])
            self.assertTrue(row['request_complete'])
            self.assert_bound(case.case, delivery, case.case.read_records())
            successor = case.advance(delivery)
        self.assertNotEqual(case.read_packet(successor)['session_final']['final_turn'],
                            packet['session_final']['final_turn'])

    def test_held_page_with_zero_or_tiny_residual_delivers_and_resumes(self):
        case = self.make_case(pages.SessionPagesTests)
        case.case.supporting = case.case.artifacts.put(b'', 'text/plain')
        case.add_sources([16000])
        case.limit_observed_input(3435400)
        quoted = []

        def quote_before_pages(number, assignment, packet):
            # A page takes no excerpts, so the request turns before the pages quote the unit three times. Their
            # measured ratio plans the first page at the room that these quotes nearly fill.
            _, inventory, _ = session.read_bound_session(case.case.artifacts, assignment)
            unit = next(row['handle'] for row in inventory['units'] if row['kind'] == 'fulltext')
            delivered = [row.get('unit') for row in packet['session_turn']['plan']['now']]
            if number == 0:
                case.respond(assignment, requests=[dict(kind='unit', id='unit', unit=unit)])
            elif unit in delivered and not quoted:
                quoted.append(unit)
                excerpts = [dict(unit=unit, text='x' * 5750, targets=[target], purpose='Keep.')
                            for target in ('t1', 't2', 't3')]
                case.invoke_usage(assignment, request_answer(notes='n', excerpts=excerpts), room=13875)
            else:
                case.respond(assignment)

        current = case.start(targets=9, request_answer=quote_before_pages)
        self.assertTrue(quoted)
        _, inventory, _ = session.read_bound_session(case.case.artifacts, current)
        unit = next(row['handle'] for row in inventory['units'] if row['kind'] == 'fulltext')
        empty = next(row['handle'] for row in inventory['units'] if row['kind'] == 'artifact' and not row['characters'])
        case.invoke_usage(current, request_answer(dict(kind='unit', id='unit', unit=unit), notes='n'), room=13875)
        current = self.write_action(case, self.next_action(case, current))
        route = case.case.read_records()['review_route']['route']
        binding = case.read_packet(current)['session_final']
        room = binding['capacity']['material_allowance'] - session.measure_final_material(route, binding)
        if not (room < 6800 and binding['request_allowances']['notes'] >= room - 290):
            self.fail('The physical page never reached the residual boundary')
        list_request = dict(id='need', kind='list_page', list='allowances', after=None)
        options = [(list_request, 0), (list_request, 450), (dict(id='need', kind='inventory_page', page=0), 215),
                   (request_passage(empty, 1, identifier='need'), 215)]
        for request, residual in options:
            with self.subTest(kind=request['kind'], residual=residual), self.cloned_store(case, case.case.root):
                notes = 'n' * max(1, room - 256 - residual + (2 if residual == 0 else 0))
                def answer(packet):
                    self.assertEqual(session.find_session_form(packet), 'page')
                    return request_answer(request, notes=notes)
                with patch.object(session, '_pack_session_deliveries', wraps=session._pack_session_deliveries) as packed:
                    deliveries = self.drive_request(case, current, answer, 'need', resume=True, notes=notes,
                                                    output_tokens=64000)
                self.assertTrue(deliveries)
                grants = [call.args[-1] for call in packed.call_args_list
                          if any(row['id'] == 'need' for row in call.args[3]['pending_requests'])]
                self.assertTrue(grants)
                if residual == 0:
                    self.assertEqual(grants[0], 0)
                elif residual == 450:
                    self.assertGreaterEqual(grants[0], 450)
                    self.assertLess(grants[0], 460)
                else:
                    self.assertGreater(grants[0], 0)
                    self.assertLess(grants[0], 256)
                if request['kind'] == 'list_page':
                    self.assertEqual(len(deliveries[0]['page']['rows']), 9)

    def test_public_request_progress_product(self):
        from research_fixtures import entry

        case = self.make_case(pages.SessionPagesTests)
        def version_label(identifier, **kw):
            return entry(identifier, **kw).replace('An authored example', 'arxiv:' + identifier)
        with patch('literature_fixtures.entry', side_effect=version_label):
            case.add_sources([600])

        def discovery(packet):
            return request_answer(dict(id='discovery-' + session.find_session_form(packet),
                                       kind='inventory_page', page=0), notes='n')

        snapshots = {}
        with TemporaryDirectory() as directory:
            def checkpoint(form, assignment):
                path = shutil.copytree(case.case.root, Path(directory) / form)
                snapshots[form] = path, assignment

            from research_harness.review_protocol import build_packet
            def with_objections(*args, **kwargs):
                packet = build_packet(*args, **kwargs)
                packet['historical_reviews'] = [dict(id='history', objections=[
                    dict(id='objection-%d' % index, claim='A synthetic material gap.',
                         evidence=[case.case.supporting], severity='material') for index in range(9)])]
                packet['digest'] = digest({key: value for key, value in packet.items() if key != 'digest'})
                return packet

            known = {}
            def remember(packet):
                binding = session.read_session_binding(packet)
                known['targets'] = [dict(row, id=row.get('id', row['label'])) for row in binding['targets']]
                known['evidence'] = next(row['artifact'] for row in packet['evidence']
                                         if row['content'] == 'A synthetic field standard.')
                return discovery(packet)

            def scientific(packet):
                return self.visible_bar_output(packet, known['evidence'], known['targets'])

            with patch.object(protocol, 'build_packet', side_effect=with_objections):
                current = case.case.assign('bar', 'first', identifier='start')
            self.run_action(case, current, remember)
            current = self.write_action(case, self.next_action(case, current))
            checkpoint('request', current)
            while session.find_session_form(case.read_packet(current)) == 'request':
                self.run_action(case, current, lambda packet: request_answer(notes='n'))
                current = self.write_action(case, self.next_action(case, current))
            self.assertEqual(session.find_session_form(case.read_packet(current)), 'page')
            self.run_action(case, current, discovery)
            page = self.write_action(case, self.next_action(case, current))
            checkpoint('page', page)
            closing = page
            while session.find_session_form(case.read_packet(closing)) != 'closing':
                self.run_action(case, closing, scientific)
                closing = self.write_action(case, self.next_action(case, closing))
            self.run_action(case, closing, discovery)
            closing = self.write_action(case, self.next_action(case, closing))
            checkpoint('closing', closing)

            kinds = ('source', 'artifact', 'unit', 'passage', 'inventory_page', 'list_page')
            forms = ('request', 'page', 'closing')
            self.assertEqual(set(snapshots), set(forms))
            cases = list(product(kinds, forms, (0, 215, None)))
            self.assertEqual((len(cases), len(set(cases))), (54, 54))
            for kind, form, room in cases:
                source, assignment = snapshots[form]
                with self.subTest(kind=kind, form=form, room=room), self.cloned_store(case, source):
                    def answer(packet):
                        self.assertEqual(session.find_session_form(packet), form)
                        listed = next(row['page']['rows'] for row in session.read_session_binding(packet)['plan']['now']
                                      if row.get('request_id') == 'discovery-' + form)
                        artifact = next(row for row in listed if row['kind'] == 'artifact')
                        fulltext = next(row for row in listed if row['kind'] == 'fulltext')
                        options = dict(source=dict(version_id=fulltext['label'], depth='fulltext'),
                            artifact=dict(sha256=artifact['label']), unit=dict(unit=fulltext['handle']),
                            passage=dict(unit=fulltext['handle'], anchor=None, characters=10),
                            inventory_page=dict(page=0), list_page=dict(list='allowances', after=None))
                        return request_answer(dict(id='matrix', kind=kind, **options[kind]), notes='n')

                    build_packet = session.build_session_packet
                    build_page = session.build_paged_packet
                    measure = session.measure_final_material

                    def matrix_pending(state):
                        return any(row['id'] == 'matrix' for row in state['pending_requests'])

                    def request_packet(artifacts, route, logical, chain, number, inventory, state,
                                       plan, limits, kind, **kwargs):
                        if room is not None and form == 'request' and kind == 'request' and matrix_pending(state):
                            limits = dict(limits, material_allowance=room)
                        return build_packet(artifacts, route, logical, chain, number, inventory, state,
                                            plan, limits, kind, **kwargs)

                    def page_packet(artifacts, route, logical, chain, number, inventory, state,
                                    plan, limits, page_plan, turn, catalog, full, **kwargs):
                        if room is None or form == 'request' or not matrix_pending(state):
                            return build_page(artifacts, route, logical, chain, number, inventory, state,
                                              plan, limits, page_plan, turn, catalog, full, **kwargs)
                        # The projection supplies occupied material at the page boundary. The wire packer
                        # and deduction of the 256-character reserve run as in the command.
                        def occupied(route, binding):
                            return max(measure(route, binding), binding['capacity']['material_allowance'] - room - 256)
                        with patch.object(session, 'measure_final_material', side_effect=occupied):
                            return build_page(artifacts, route, logical, chain, number, inventory, state,
                                              plan, limits, page_plan, turn, catalog, full, **kwargs)

                    _, inventory, _ = session.read_bound_session(case.case.artifacts, assignment)
                    fulltext = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
                    first_artifact = next(row for row in inventory['units'][:10] if row['kind'] == 'artifact')

                    def check_delivery(received, asked):
                        rows = [row for row, _ in received]
                        if kind == 'artifact':
                            # The frame delivered this artifact before the request.
                            self.assertEqual([(row['kind'], row['status']) for row in rows],
                                             [('request_status', 'already_delivered')])
                            self.assertEqual(session._find_covered_end(asked, first_artifact['handle']),
                                             first_artifact['characters'])
                        elif kind == 'inventory_page':
                            self.assertEqual([row['kind'] for row in rows], ['list_page'], rows)
                            row, = rows
                            self.assertEqual((row['page']['page'], row['page']['total']), (0, len(inventory['units'])))
                            def describe(items):
                                return [(item['handle'], item['kind'], item['characters']) for item in items]
                            self.assertEqual(describe(row['page']['rows']), describe(inventory['units'][:10]))
                        elif kind == 'list_page':
                            plan = capacity.read_chain_capacity_plan(case.case.artifacts, assignment)
                            listed = v7._validate_list_cursor('allowances', None, plan, asked,
                                                              artifacts=case.case.artifacts)
                            self.assertEqual({row['kind'] for row in rows}, {'list_page'}, rows)
                            cursors = [None] + [row['page']['next'] for row in rows]
                            self.assertEqual([(row['page']['list'], row['page']['after']) for row in rows],
                                             [('allowances', after) for after in cursors[:-1]])
                            self.assertIsNone(cursors[-1])
                            self.assertEqual([item for row in rows for item in row['page']['rows']], listed)
                        else:
                            text = case.case.artifacts.read(fulltext['artifact']).decode('utf-8')
                            start = 0 if kind == 'passage' else session._find_covered_end(asked, fulltext['handle'])
                            for row, entry in received:
                                self.assertEqual((row['unit'], row['kind'], row['start'], entry['content']),
                                                 (fulltext['handle'], 'fulltext', start, text[row['start']:row['end']]))
                                start = row['end']
                            self.assertEqual(start, 10 if kind == 'passage' else fulltext['characters'])

                    with patch.object(session, 'build_session_packet', side_effect=request_packet), patch.object(
                            session, 'build_paged_packet', side_effect=page_packet):
                        outcome, saved = self.drive_to_review(case, assignment, answer, scientific, 'matrix',
                                                              check_delivery)
                    if room is None or form != 'request':
                        self.assertEqual(outcome, 'record')
                        self.assertIn('composite', saved)
                    else:
                        self.assertEqual(outcome, 'stop')
                        action, stop = saved
                        records = case.case.read_records()
                        head = records['review_assignment'][action['payload']['assignment_id']]
                        plan, _, _ = session.read_bound_session(case.case.artifacts, head)
                        held = session.rebuild_session_state(records, case.case.artifacts, head, inventory, plan,
                                                             records['review_route'][head['route_id']])
                        self.assertEqual((action['code'], action['details']['material_allowance']),
                                         ('review_route_capacity_below_floor', room))
                        self.assertEqual([(row['id'], row['kind']) for row in held['pending_requests']],
                                         [('matrix', kind)])

    def test_list_with_no_packable_row_gets_a_terminal_status(self):
        case = self.make_case()
        root = case.case.assign('bar', 'first', identifier='root')
        plan, inventory, state = session.read_bound_session(case.case.artifacts, root)
        request = dict(id='capture', kind='list_page', list='allowances', after=None)
        state['pending_requests'] = [request]
        route = case.case.read_records()['review_route']['route']
        status = dict(kind='request_status', request_id='capture', status='unavailable', request_complete=True)
        placeholder = dict(artifact=describe_artifact(b'{}', 'application/json'), content='{}', encoding='utf-8')
        grant = capacity.measure_capacity_value(route, dict(row=status, evidence=placeholder)) + 2
        rows, evidence, pages_delivered = session._pack_session_deliveries(
            case.case.artifacts, route, inventory, state, plan, grant)
        row, = rows
        entry, = evidence
        self.assertEqual((row['status'], row['request_complete'], entry['content'], pages_delivered),
                         ('unavailable', True, '{}', []))
        self.assertEqual(session._pack_session_deliveries(case.case.artifacts, route, inventory, state, plan, 0),
                         ([], [], []))
        logical = protocol._read_json(case.case.artifacts, root['session_chain']['logical'])
        limits = dict(capacity.derive_plan_task_limits(plan, 'request'), material_allowance=0)
        with self.assertRaises(ResearchError) as caught:
            session.build_session_packet(case.case.artifacts, route, logical, 'root', 2, inventory, state,
                                         plan, limits, 'request')
        self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')

    def test_every_page_answer_previews_the_next_capacity_and_takes_the_stop(self):
        case = self.make_case(pages.SessionPagesTests)
        case.limit_observed_input(3435400)
        page = case.start()
        outputs = (
            request_answer(notes='n'),
            case.answer(page),
            dict(stage='material', title='Long argument', part=1, text='Start.', more=True),
            request_answer(dict(id='one', kind='list_page', list='allowances', after=None), notes='n'),
        )
        for output in outputs:
            with self.subTest(stage=output.get('stage'), requests=output.get('requests')), self.cloned_store(
                    case, case.case.root):
                case.invoke_usage(page, output, room=-50000)
                action = self.next_action(case, page)
                self.assertEqual(action.get('code'), 'review_route_capacity_below_floor', action)
                self.stop_action(case, page, action)

    def test_capped_page_answer_takes_the_same_writable_stop_as_its_ordinary_sibling(self):
        """A page answer at the output cap fails its turn, and its replacement has no room under the pinned plan."""
        case = self.make_case(pages.SessionPagesTests)
        case.limit_observed_input(3435400)
        page = case.start()
        cap = case.case.read_records()['review_route'][page['route_id']]['configuration']['max_output_tokens']
        actions = []
        for output_tokens in (64000, cap):
            with self.subTest(output_tokens=output_tokens), self.cloned_store(case, case.case.root):
                case.invoke_usage(page, case.answer(page), output_tokens=output_tokens, room=-50000)
                action = self.next_action(case, page)
                emitted = []
                with patch.object(protocol.transport, 'send_request', side_effect=AssertionError('no reviewer call')):
                    self.assertEqual(review_series.run_series(case.case.store, page['id'], emit=emitted.append), 3)
                step = emitted[-1]['details']['step']
                self.assertEqual((action['state'], emitted[-1]['code'], step['command'], step['payload']),
                                 ('stop', action.get('code'), 'review-stop', action.get('payload')))
                revision = case.case.store.revision
                stop = case.case.mutate(cli.OPERATIONS[step['command']], step['payload'])['result']
                self.assertGreater(case.case.store.revision, revision)
                stopped = self.next_action(case, page)
                self.assertEqual((stopped['code'], stopped['details']['stop_id']), ('review_chain_stopped', stop['id']))
                self.assertEqual(stop['reason'], action['code'] + ': ' + action['details']['message'])
                self.assertNotIn('review-route', stop['reason'])
                actions.append(action)
        self.assertEqual(actions[0], actions[1])

    def test_closing_request_records_the_exact_composite_plan(self):
        case = self.make_case(pages.SessionPagesTests)
        current = case.start()
        while session.find_session_form(case.read_packet(current)) != 'closing':
            self.run_action(case, current, lambda packet: case.answer(current))
            current = self.write_action(case, self.next_action(case, current))
        self.run_action(case, current, lambda packet: request_answer(
            dict(id='closing-list', kind='inventory_page', page=0), notes='n'))
        current = self.write_action(case, self.next_action(case, current))
        good = case.answer(current)
        bad = dict(good, bindings=[pointer for pointer in good['bindings'] if pointer != '/stage'])
        with self.cloned_store(case, case.case.root):
            self.run_action(case, current, lambda packet: bad)
            records = case.case.read_records()
            self.assertEqual([(row['code'], row['details'].get('composed_review'))
                              for row in records['review_output_refusal'].values()], [('invalid_value_review', True)])
            self.assertFalse(records.get('review_composite'))
            self.assertEqual(self.next_action(case, current)['state'], 'replace')
        self.run_action(case, current, lambda packet: good)
        action = self.next_action(case, current)
        with patch.object(decisions, 'validate_review_contract', wraps=decisions.validate_review_contract) as check:
            saved = case.case.mutate(decisions.record_value_review, action['payload'])['result']
        self.assertEqual([call.kwargs.get('composed') for call in check.call_args_list], [True])
        self.assertEqual(saved['composite']['id'], next(iter(case.case.read_records()['review_composite'])))

    def test_three_escape_heavy_inventory_pages_are_served_in_v3_and_v4(self):
        from research_fixtures import entry
        case = self.make_case(pages.SessionPagesTests)

        def escaped_title(identifier, **kwargs):
            return entry(identifier, **kwargs).replace('An authored example', chr(92) * 117 + identifier[-3:])

        with patch('literature_fixtures.entry', side_effect=escaped_title):
            case.add_sources([120] * 4)
        supporting = case.case.deliver_supporting('first')
        link = supporting['source_links'][0]
        for version in (bar_sources.PROTOCOL_V3, session.PROTOCOL):
            with self.subTest(version=version), self.cloned_store(case, case.case.root), patch.object(
                    protocol, 'NEW_SESSION_RULES', (version, bar_sources.PROTOCOL_V3)):
                current = case.case.assign('bar', 'first', identifier='r', **supporting)
                grants = []
                for index in range(3):
                    item = dict(id='inv%d' % index, kind='inventory_page', page=0)
                    if version == session.PROTOCOL:
                        output = request_answer(item, notes='n')
                    else:
                        output = case.case.build_review_output('bar')
                        output['value'].update(status='unresolved', evidence=[])
                        output['source_requests'] = [item]
                    self.run_action(case, current, lambda packet: output, output_tokens=120000)
                    current = self.write_action(case, self.next_action(case, current))
                    packet = case.read_packet(current)
                    self.assertEqual(packet['source_rules'], version)
                    if version == session.PROTOCOL:
                        binding = packet['session_turn']
                        delivery = next(row for row in binding['plan']['now'] if row.get('request_id') == item['id'])
                        grants.append(binding['capacity']['material_allowance'])
                        self.assertEqual((len(delivery['page']['rows']), delivery['request_complete']), (10, True))
                        _, inventory, _ = session.read_bound_session(case.case.artifacts, current)
                        self.assertEqual([(row['handle'], row['kind'], row['characters']) for row in delivery['page']['rows']],
                                         [(row['handle'], row['kind'], row['characters']) for row in inventory['units'][:10]])
                        titles = [row['label'] for row in delivery['page']['rows'] if row['kind'] == 'source']
                        self.assertEqual(len(set(titles)), len(titles), titles)
                        self.assert_bound(case.case, current, case.case.read_records())
                    else:
                        self.assertTrue(any(row['request'] == item and row['status'] == 'delivered' and row['page']['items']
                                            for row in packet['source_deliveries']))
                if version == session.PROTOCOL:
                    self.assertEqual(grants, [60000, 30000, 15000])
                    self.run_action(case, current, lambda packet: request_answer(request_source(link['version_id'], 'abstract')))
                    current = self.write_action(case, self.next_action(case, current))
                    self.run_action(case, current, lambda packet: request_answer())
                    current = self.write_action(case, self.next_action(case, current))
                    output = lambda packet: self.visible_bar_output(packet, link['artifact'], [])
                else:
                    output = lambda packet: case.case.build_review_output('bar')
                self.run_action(case, current, output)
                self.record_action(case, current, self.next_action(case, current))

    def test_planner_previews_reuse_authenticated_history(self):
        case = self.make_case(pages.SessionPagesTests)

        def measure(assignment, history, form):
            records, artifacts = case.case.read_records(), case.case.artifacts
            with patch.object(session, '_authenticate_session_binding', wraps=session._authenticate_session_binding) as auth:
                started, processor = perf_counter(), process_time()
                result = review_continuation.plan_next(records, artifacts, assignment['id'])
                elapsed, cpu = perf_counter() - started, process_time() - processor
            print('planner budget', form, history, 'bindings', auth.call_count,
                  'wall', round(elapsed, 4), 'cpu', round(cpu, 4), flush=True)
            self.assertEqual(result['state'], 'continue', result)
            self.assertGreater(auth.call_count, 0)
            self.assertLessEqual(auth.call_count, history)
            return result

        with self.cloned_store(case, case.case.root):
            current = case.case.assign('bar', 'first', identifier='root')
            for history in range(1, 13):
                item = dict(id='inventory-%d' % history, kind='inventory_page', page=0)
                self.run_action(case, current, lambda packet: request_answer(item, notes='n'))
                action = (measure(current, history, 'request') if history in (1, 12)
                          else self.next_action(case, current))
                if history != 12:
                    current = self.write_action(case, action)
        page = case.start()
        self.run_action(case, page, lambda packet: request_answer(
            dict(id='held', kind='list_page', list='allowances', after=None), notes='n'))
        history = len(case.case.read_records()['review_assignment'])
        measure(page, history, 'held page')

    def test_terminal_list_page_fits_at_its_exact_wire_cost(self):
        route, plan, inventory, state, _ = self.small_request_view()
        target = dict(handle='t1', characters=30000, used=0)
        state['allowances']['targets'] = [target]
        state['pending_requests'] = [dict(id='allow', kind='list_page', list='allowances', after=None)]
        page = dict(list='allowances', after=None, next=None, remaining=0, rows=[dict(target, id='t1')])
        content = session.serialize_record(page)
        row = dict(kind='list_page', request_id='allow', page=page, request_complete=True, characters=len(content))
        entry = dict(artifact=describe_artifact(content, 'application/json'), content=content.decode(), encoding='utf-8')
        exact = capacity.measure_capacity_value(route, dict(row=row, evidence=entry)) + 2
        for grant, kind in ((exact - 1, 'request_status'), (exact, 'list_page')):
            with self.subTest(grant=grant):
                deliveries, _, _ = session._pack_session_deliveries(None, route, inventory, state, plan, grant)
                self.assertEqual([delivery['kind'] for delivery in deliveries], [kind])
                if kind == 'list_page':
                    self.assertEqual(deliveries[0], row)

    def test_small_page_keeps_the_supplier_bound_for_the_first_excluded_row(self):
        route, plan, inventory, state, _ = self.small_request_view()
        state['pending_requests'] = [dict(id='allow', kind='list_page', list='allowances', after=None)]
        valid = dict(handle='t1', characters=30000, used=0)
        invalid = dict(handle='t2', characters=30000, used=0, extra='x' * 513)
        for targets, grant in (([invalid], 400), ([valid, invalid], 1000)):
            with self.subTest(grant=grant):
                state['allowances']['targets'] = targets
                plan['targets']['handles'] = [target['handle'] for target in targets]
                with self.assertRaises(ResearchError) as caught:
                    session._pack_session_deliveries(None, route, inventory, state, plan, grant)
                self.assertEqual(caught.exception.code, 'review_route_capacity_plan_violated')
                self.assertEqual(caught.exception.details['row'], 't2')
                self.assertGreater(caught.exception.details['characters'], caught.exception.details['bound'])

    def test_small_wire_grant_never_serializes_a_large_trial_prefix(self):
        route, plan, inventory, state, _ = self.small_request_view()
        state['pending_requests'] = [dict(id='allow', kind='list_page', list='allowances', after=None)]
        handles = ['t%d' % index for index in range(1, 19)]
        plan['targets']['handles'] = handles
        state['allowances']['targets'] = [dict(handle=handle, characters=30000, used=0) for handle in handles]
        with patch.object(capacity, 'measure_capacity_value', wraps=capacity.measure_capacity_value) as meter:
            rows, _, _ = session._pack_session_deliveries(None, route, inventory, state, plan, 1600)
        self.assertEqual(len(rows[0]['page']['rows']), 5)
        self.assertLessEqual(max(len(call.args[1].get('row', {}).get('page', {}).get('rows', []))
                                 for call in meter.call_args_list), 8)

    def test_empty_list_refusal_has_rows_zero_at_the_list_pointer(self):
        _, plan, inventory, state, packet = self.small_request_view()
        plan['targets']['handles'] = []
        answer = request_answer(dict(id='allow', kind='list_page', list='allowances', after=None))
        with patch.object(session, 'read_bound_session', return_value=(plan, inventory, state)):
            with self.assertRaises(ResearchError) as caught:
                session.validate_request_output(None, {}, packet, answer)
        self.assert_turn_refusal(caught.exception, '/requests/0/list', list='allowances', after=None, rows=0)

    def test_nested_fixture_cleanups_run_after_setup_fails(self):
        import codex_route_fixtures
        from codex_route_fixtures import codex, framed

        module = 'research_harness.problem_claims'
        claims = importlib.import_module(module)
        attributes = ('runtime', '_rollout', 'NATIVE_DECLARATIONS', 'NATIVE_DECLARATION_SETS')
        originals = [(adapter, name, getattr(adapter, name))
                     for adapter in (codex, framed) for name in attributes]
        problem_case = turns.problem_fixtures.ProblemReviewTests
        original_turn = turns.SessionTurnsTests.setUp
        original_problem = problem_case.setUp

        def assert_restored():
            for adapter, name, value in originals:
                self.assertIs(getattr(adapter, name), value)
            self.assertIs(sys.modules[module], claims)

        for failure in ('outer', 'inner', 'deliberately_late'):
            with self.subTest(failure=failure):
                outer = SessionRequestTests()
                inner = turns.SessionTurnsTests()
                created = []

                def track_turn(case):
                    created.append(case)
                    original_turn(case)

                def fail_problem(case):
                    created.append(case)
                    original_problem(case)
                    raise RuntimeError('fixture failure')

                def late_registration():
                    child = turns.SessionTurnsTests()
                    child.setUp()
                    outer.addCleanup(child.doCleanups)

                def clean_created():
                    for case in reversed(created):
                        case.doCleanups()

                with ExitStack() as fallback:
                    for adapter, name, value in originals:
                        fallback.callback(setattr, adapter, name, value)
                    fallback.callback(sys.modules.__setitem__, module, claims)
                    fallback.callback(clean_created)
                    with self.assertRaisesRegex(RuntimeError, 'fixture failure'):
                        if failure == 'inner':
                            with patch.object(problem_case, 'setUp', fail_problem):
                                try:
                                    inner.setUp()
                                finally:
                                    inner.doCleanups()
                                    assert_restored()
                        else:
                            with patch.object(turns.SessionTurnsTests, 'setUp', track_turn), patch.object(
                                    codex_route_fixtures, 'probe_codex_route', side_effect=RuntimeError('fixture failure')):
                                try:
                                    (late_registration if failure == 'deliberately_late' else outer.make_case)()
                                finally:
                                    outer.doCleanups()
                        # The normal checks below execute after the expected setup exception.
                    if failure == 'deliberately_late':
                        with self.assertRaises(AssertionError):
                            assert_restored()
                    else:
                        assert_restored()
                assert_restored()

    def test_page_plan_of_different_currency_is_unverified(self):
        case = self.make_case(pages.SessionPagesTests)
        page = case.start()
        chain = page['session_chain']
        page_plan = openness_final_pages.read_page_plan(case.case.artifacts, chain['final_page_plan'])
        for defect in ('wrong_currency', 'missing_currency', 'wrong_packet_digest', 'missing_packet_digest'):
            with self.subTest(defect=defect):
                changed_plan = copy.deepcopy(page_plan)
                if defect == 'wrong_currency':
                    changed_plan['currency']['packet_digest'] = '0' * 64
                elif defect == 'missing_currency':
                    del changed_plan['currency']
                reference = dict(artifact=protocol._put_json(case.case.artifacts, changed_plan),
                                 digest=digest(changed_plan))
                packet = case.read_packet(page)
                binding = session.read_session_binding(packet)
                binding['page_plan_digest'] = reference['digest']
                if defect == 'wrong_packet_digest':
                    binding['page_plan_digest'] = '0' * 64
                elif defect == 'missing_packet_digest':
                    del binding['page_plan_digest']
                packet['digest'] = digest({key: value for key, value in packet.items() if key != 'digest'})
                changed = dict(page, packet=protocol._put_json(case.case.artifacts, packet),
                               session_chain=dict(chain, final_page_plan=reference))
                with self.assertRaises(ResearchError) as caught:
                    session.read_composite_currency(case.case.read_records(), case.case.artifacts, changed)
                self.assertEqual((caught.exception.code, caught.exception.message),
                                 ('review_invocation_unverified', session.TURN_BINDING_MESSAGE))

    def test_missing_page_binding_operands_keep_the_stored_turn_diagnosis(self):
        case = self.make_case(pages.SessionPagesTests)
        page = case.start()
        records, artifacts = case.case.read_records(), case.case.artifacts
        for missing in ('packet', 'logical', 'inventory', 'state', 'final_page_plan', 'digest', 'artifact'):
            with self.subTest(missing=missing):
                changed = copy.deepcopy(page)
                chain = changed['session_chain']
                if missing == 'packet':
                    del changed[missing]
                elif missing in ('digest', 'artifact'):
                    del chain['final_page_plan'][missing]
                else:
                    del chain[missing]
                with self.assertRaises(ResearchError) as caught:
                    session.read_composite_currency(records, artifacts, changed)
                self.assertEqual((caught.exception.code, caught.exception.message),
                                 ('review_invocation_unverified', session.TURN_BINDING_MESSAGE))
        changed = copy.deepcopy(page)
        changed['session_chain']['final_page_plan']['digest'] = '0' * 64
        with self.assertRaises(ResearchError) as caught:
            session.read_composite_currency(records, artifacts, changed)
        self.assertEqual(caught.exception.code, 'artifact_corrupt')

    def test_public_page_binding_checks_assignments_routes_and_reference_members(self):
        case = self.make_case(pages.SessionPagesTests)
        page = case.start()
        records, artifacts = case.case.read_records(), case.case.artifacts
        packet = case.read_packet(page)
        for member in ('id', 'dossier_id', 'context', 'prompt', 'author_id', 'capacity_plan_kind'):
            with self.subTest(member=member):
                changed = {key: value for key, value in page.items() if key != member}
                with self.assertRaises(ResearchError) as caught:
                    session.authenticate_session_binding(records, artifacts, changed, packet)
                self.assertEqual(caught.exception.code, 'review_invocation_unverified')
        with self.assertRaises(ResearchError) as caught:
            session.authenticate_session_binding(records, artifacts, dict(page, capacity_plan_kind='request'), packet)
        self.assertEqual(caught.exception.code, 'review_invocation_unverified')
        for member in ('id', 'dossier_id', 'recorded_revision', 'author_id', 'capacity_plan_kind'):
            with self.subTest(predecessor=member):
                prior = page['history_assignment_id']
                assignments = dict(records['review_assignment'])
                assignments[prior] = {key: value for key, value in assignments[prior].items() if key != member}
                with self.assertRaises(ResearchError) as caught:
                    session.authenticate_final_binding(dict(records, review_assignment=assignments),
                                                       artifacts, page, packet)
                self.assertEqual(caught.exception.code, 'review_invocation_unverified')
        with self.assertRaises(ResearchError) as caught:
            session.authenticate_final_binding(dict(records, review_route={}), artifacts, page, packet)
        self.assertEqual(caught.exception.code, 'review_invocation_unverified')
        for member in ('logical', 'inventory', 'state'):
            with self.subTest(reference=member):
                changed = copy.deepcopy(page)
                del changed['session_chain'][member]['sha256']
                for check in (lambda: session.read_composite_currency(records, artifacts, changed),
                              lambda: session.authenticate_session_binding(records, artifacts, changed, packet)):
                    with self.assertRaises(ResearchError) as caught:
                        check()
                    self.assertEqual(caught.exception.code, 'review_invocation_unverified')
        changed = copy.deepcopy(page)
        changed['session_chain']['logical'] = describe_artifact(b'Missing stored logical packet.', 'application/json')
        with self.assertRaises(ResearchError) as caught:
            session.read_composite_currency(records, artifacts, changed)
        self.assertEqual(caught.exception.code, 'artifact_missing')
        with self.cloned_store(case, case.case.root):
            prior = copy.deepcopy(records['review_assignment'][page['history_assignment_id']])
            del prior['capacity_plan_kind']
            case.case.put('review_assignment', prior['id'], prior)
            action = self.next_action(case, page)
            # The plan checks the run that it would name (lane guard-findings-2) and stops with its refusal.
            self.assertEqual((action['state'], action['code']), ('stop', 'review_invocation_unverified'))
            revision = case.case.store.revision
            with patch.object(protocol.transport, 'send_request', side_effect=AssertionError('no reviewer call')):
                with self.assertRaises(ResearchError) as caught:
                    case.case.mutate(protocol.invoke_assignment, dict(id='unverified-run', assignment_id=page['id']),
                                     credential='test-only')
            self.assertEqual((caught.exception.code, case.case.store.revision),
                             ('review_invocation_unverified', revision))

    def test_missing_request_root_or_replacement_origin_is_unverified(self):
        case = self.make_case()
        root = case.case.assign('bar', 'first', identifier='root')
        self.run_action(case, root, lambda packet: request_answer(dict(id='bad', kind='unit', unit='u999')))
        child = self.write_action(case, self.next_action(case, root))
        records = case.case.read_records()
        packet = case.read_packet(child)
        for defect in ('missing-root', 'missing-bound-root', 'missing-replaced-origin'):
            with self.subTest(defect=defect):
                changed = copy.deepcopy(child)
                assignments = dict(records['review_assignment'])
                if defect == 'missing-root':
                    del assignments[root['id']]
                elif defect == 'missing-bound-root':
                    assignments[root['id']] = {key: value for key, value in root.items() if key != 'session_chain'}
                else:
                    changed['context']['output_refusal']['assignment_id'] = 'missing'
                with self.assertRaises(ResearchError) as caught:
                    session.authenticate_session_binding(dict(records, review_assignment=assignments),
                                                         case.case.artifacts, changed, packet)
                self.assertEqual((caught.exception.code, caught.exception.message),
                                 ('review_invocation_unverified', session.TURN_BINDING_MESSAGE))

    def test_final_page_binding_that_differs_from_the_rebuilt_turn_is_unverified(self):
        case = self.make_case(pages.SessionPagesTests)
        page = case.start()
        records = case.case.read_records()
        self.assert_bound(case.case, page, records)
        answer = case.answer(page)
        answer['targets'][0]['sentence'] = 'x' * 301
        invoke_codex(case.case, page['id'], answer)
        step = plan_after_failure(case.case.read_records(), case.case.artifacts, page['id'])
        self.assertEqual(step['state'], 'replace')
        replacement = case.case.mutate(protocol.record_assignment, step['payload'])['result']
        records = case.case.read_records()
        for assignment in (page, replacement):
            with self.subTest(assignment=assignment['id']):
                packet = case.read_packet(assignment)
                self.assertTrue(session.authenticate_final_binding(records, case.case.artifacts, assignment, packet))
                changed = dict(assignment, prompt=dict(assignment['prompt'], text='Different text.'))
                with self.assertRaises(ResearchError) as caught:
                    session.authenticate_final_binding(records, case.case.artifacts, changed, packet)
                self.assertEqual((caught.exception.code, caught.exception.message),
                                 ('review_invocation_unverified', session.TURN_BINDING_MESSAGE))
        packet = case.read_packet(replacement)
        del packet['output_refusal']
        with self.assertRaises(ResearchError) as caught:
            session.authenticate_final_binding(records, case.case.artifacts, replacement, packet)
        self.assertEqual((caught.exception.code, caught.exception.message),
                         ('review_invocation_unverified', session.TURN_BINDING_MESSAGE))

    def deliver_abstract(self, case, *, excerpt=False):
        version = case.case.metadata(1)
        first = case.case.assign('bar', 'first', identifier='first')
        case.respond(first, [request_source(version, 'abstract')])
        delivery = next_bar(case, first, 'delivery')
        packet = case.read_packet(delivery)
        row, = packet['session_turn']['plan']['now']
        self.assertEqual(row['kind'], 'abstract')
        excerpts = ([dict(unit=row['unit'], text=packet['evidence'][0]['content'][:20],
                          purpose='Keep the claim.', targets=['t1'])] if excerpt else [])
        case.respond(delivery, excerpts=excerpts)
        final = next_bar(case, delivery, 'final')
        self.assert_bound(case.case, final, case.case.read_records())
        return final, row, packet['evidence'][0]['artifact']

    def assert_abstract_review(self, case, final, reference, artifact):
        saved = case.finish(final, [reference])
        self.assertEqual(saved['payload']['value']['evidence'], [artifact])
        records = case.case.read_records()
        self.assertTrue(protocol.authenticate_attempt(records, case.case.artifacts, final,
                                                     records['review_attempt']['attempt-' + final['id']]))

    def test_problem_bar_records_an_abstract_by_its_unit_or_part_handle(self):
        case = self.make_case()
        final, row, artifact = self.deliver_abstract(case)
        evidence = [dict(ref=row['unit']), dict(ref=row['unit'], part=row['part'])]
        saved = case.finish(final, evidence)
        self.assertEqual(saved['payload']['value']['evidence'], [artifact, artifact])
        records = case.case.read_records()
        self.assertTrue(protocol.authenticate_attempt(records, case.case.artifacts, final,
                                                     records['review_attempt']['attempt-' + final['id']]))

    def test_problem_bar_records_an_abstract_by_its_excerpt_handle(self):
        case = self.make_case()
        final, _, artifact = self.deliver_abstract(case, excerpt=True)
        entry, = case.read_packet(final)['session_final']['entries']
        self.assertEqual(entry['status'], 'verified')
        self.assert_abstract_review(case, final, dict(ref=entry['handle']), artifact)

    def test_abstract_span_is_refused_as_an_invalid_handle(self):
        case = self.make_case()
        version = case.case.metadata(1)
        start = case.case.assign('bar', 'first', identifier='first')
        case.respond(start, [request_source(version, 'abstract')])
        delivery = next_bar(case, start, 'delivery')
        packet = case.read_packet(delivery)
        row, = packet['session_turn']['plan']['now']
        with self.assertRaises(ResearchError) as caught:
            review_handles.expand_handles(packet, dict(evidence=[dict(ref=row['unit'], start=0, end=20)]),
                                          artifacts=case.case.artifacts, assignment=delivery)
        self.assertEqual(caught.exception.code, 'invalid_reference_handle')

    def test_abstract_handle_citations_require_complete_delivery(self):
        case = self.make_case()
        version = case.case.metadata(1)
        alternative = case.case.metadata(2, abstract='A second relevant source establishes a bounded comparison.')
        start = case.case.assign('bar', 'first', identifier='first')
        _, inventory, _ = session.read_bound_session(case.case.artifacts, start)
        abstract, alternate = [row for row in inventory['units'] if row['kind'] == 'abstract']
        handle = abstract['handle']
        case.respond(start, [request_passage(handle, 15),
                             dict(request_source(alternative, 'abstract'), id='alternate')])
        partial = next_bar(case, start, 'partial')
        packet = case.read_packet(partial)
        content = {row['unit']: entry['content'] for row, entry in zip(
            packet['session_turn']['plan']['now'], packet['evidence']) if 'unit' in row}
        excerpts = [dict(unit=handle, text=content[handle], purpose='Keep.', targets=['t1']),
                    dict(unit=alternate['handle'], text=content[alternate['handle']],
                         purpose='Alternative support.', targets=['t1'])]
        for form in ('unit', 'part', 'excerpt', 'offset', 'complete'):
            with self.subTest(form=form), self.cloned_store(case, case.case.root):
                if form == 'complete':
                    case.respond(partial, [request_source(version, 'abstract')], excerpts=excerpts)
                    remainder = next_bar(case, partial, 'remainder')
                    row, = case.read_packet(remainder)['session_turn']['plan']['now']
                    self.assertEqual((row['start'], row['end']), (15, abstract['characters']))
                    case.respond(remainder)
                    current = remainder
                else:
                    case.respond(partial, excerpts=excerpts)
                    current = partial
                final = next_bar(case, current, 'final')
                self.assert_bound(case.case, final, case.case.read_records())
                entry = next(row for row in case.read_packet(final)['session_final']['entries']
                             if row['unit'] == handle)
                citations = dict(unit=dict(ref=handle), part=dict(ref=handle, part=1), excerpt=dict(ref=entry['handle']))
                if form == 'complete':
                    saved = case.finish(final, citations.values())
                    self.assertEqual(saved['payload']['value']['evidence'], [abstract['artifact']] * 3)
                elif form == 'offset':
                    answer = case.case.build_review_output('bar')
                    answer['value'].update(status='unresolved', evidence=[dict(ref=handle, start=0, end=10)])
                    invoke_codex(case.case, final['id'], answer, 'offset')
                    self.assertEqual(case.case.read_records()['review_output_refusal']['offset']['code'],
                                     'invalid_reference_handle')
                else:
                    answer = case.case.build_review_output('bar')
                    answer['value'].update(status='unresolved', evidence=[citations[form]])
                    invoke_codex(case.case, final['id'], answer)
                    refusal = case.case.read_records()['review_output_refusal']['attempt-final']
                    self.assertEqual(refusal['code'], 'review_evidence_undelivered')
                    self.assertIn('requires delivery of the entire abstract', refusal['details']['message'])
                    if form == 'unit':
                        replacement = self.write_action(case, self.next_action(case, final))

                        def use_delivered_support(packet):
                            finding = packet['output_refusal']['refused'][-1]
                            # The unchanged alternative evidence remains available to the replacement.
                            message = finding['message']
                            self.assertIn('only if the remaining answer meets the evidence requirements', message)
                            self.assertIn('another fully delivered, relevant item', message)
                            entry = next(row for row in packet['session_final']['entries']
                                         if row['purpose'] == 'Alternative support.')
                            repaired = case.case.build_review_output('bar')
                            repaired['value'].update(status='unresolved', evidence=[dict(ref=entry['handle'])])
                            return repaired

                        self.run_action(case, replacement, use_delivered_support)
                        step = self.next_action(case, replacement)
                        self.assertEqual((step['state'], step.get('command')), ('record', 'value-review'), step)
                        saved = case.case.mutate(decisions.record_value_review, step['payload'])['result']
                        self.assertEqual(saved['payload']['value']['evidence'], [alternate['artifact']])

    def test_study_bar_records_an_abstract_by_its_handle(self):
        case = self.make_case(prompts.StudyBarFormsTests)
        first = case.assign('first')
        self.assert_bound(case, first, case.records())
        invoke_codex(case, first['id'], request_answer(request_source(case.version, 'abstract')))
        handle = artifact = None
        current = first
        for index in range(1, 10):
            current = case.assign('study-%d' % index, prior_assignment_id=current['id'])
            self.assert_bound(case, current, case.records())
            packet = protocol._read_json(case.artifacts, current['packet'])
            if session.find_session_kind(packet) == 'final':
                break
            for row, entry in zip(packet['session_turn']['plan']['now'], packet['evidence']):
                if row.get('request_id') == 'capture':
                    handle, artifact = row['unit'], entry['artifact']
            invoke_codex(case, current['id'], request_answer())
        self.assertIsNotNone(handle)
        self.assertEqual(session.find_session_kind(packet), 'final')
        answer = case.review_response('bar')
        answer['value'].update(status='unresolved', evidence=[dict(ref=handle)])
        invoke_codex(case, current['id'], answer)
        records = case.records()
        self.assertTrue(protocol.assignment_state(records, case.artifacts, current['id'])['ready'],
                        records.get('review_output_refusal'))
        output, _ = read_final_output(records, case.artifacts, current)
        review = case.mutate(decisions.record_value_review,
            dict(output, id='review-study-bar', dossier_id='dossier-1', assignment_id=current['id']))['result']
        self.assertEqual(review['payload']['value']['evidence'], [artifact])
        records = case.records()
        self.assertTrue(protocol.authenticate_attempt(records, case.artifacts, current,
                                                     records['review_attempt']['attempt-' + current['id']]))

    def assert_unavailable(self, packet):
        from ci.capacity_packet_inventory import read_packet_registry, validate_packet

        self.assertEqual(session.read_session_binding(packet)['plan']['now'],
                         [dict(kind='request_status', request_id='capture', status='unavailable', request_complete=True)])
        evidence, = packet['evidence']
        self.assertEqual((evidence['content'], evidence['artifact']['media_type']), ('{}', 'application/json'))
        self.assertEqual(validate_packet(packet['role'], packet, read_packet_registry()), [])

    def test_unavailable_source_request_closes_before_recorded_final(self):
        case = self.make_case()
        version = case.case.metadata(1)
        first = case.case.assign('bar', 'first', identifier='first')
        case.respond(first, [dict(request_source(version, 'abstract'), id='abstract')])
        abstract = next_bar(case, first, 'abstract')
        row, = case.read_packet(abstract)['session_turn']['plan']['now']
        self.assertEqual((row['kind'], row['request_id']), ('abstract', 'abstract'))
        case.respond(abstract, [request_source(version, 'fulltext')])
        unavailable = next_bar(case, abstract, 'unavailable')
        self.assert_unavailable(case.read_packet(unavailable))
        case.respond(unavailable)
        final = next_bar(case, unavailable, 'final')
        self.assertEqual(session.find_session_kind(case.read_packet(final)), 'final')
        self.assert_bound(case.case, final, case.case.read_records())
        case.finish(final, [dict(ref=row['unit'])])

    def test_unavailable_source_request_leaves_the_same_page_ready_to_answer(self):
        case = self.make_case(pages.SessionPagesTests)
        version = case.case.metadata(1)
        page = case.start()
        self.assert_bound(case.case, page, case.case.read_records())
        delivery = case.advance(page, request_answer(request_source(version, 'fulltext')))
        packet = case.read_packet(delivery)
        self.assertEqual(packet['session_final']['page'], case.read_packet(page)['session_final']['page'])
        self.assert_unavailable(packet)
        self.assert_bound(case.case, delivery, case.case.read_records())
        successor = case.advance(delivery)
        self.assertNotEqual(case.read_packet(successor)['session_final']['final_turn'],
                            packet['session_final']['final_turn'])

    def test_route_measured_request_row_over_its_limit_is_refused(self):
        anchor = chr(92) * 600
        unit = 'u1'
        route, plan, inventory, state, packet = self.small_request_view([dict(handle=unit, kind='fulltext')])
        # JSON escaping charges the row even when a literal anchor is within its own length limit.
        row = request_passage(unit, 10, anchor, identifier='"' * 120)
        size = capacity.measure_capacity_value(route, row)
        self.assertGreater(size, session.REQUEST_ROW_CHARACTERS)
        with patch.object(session, 'read_bound_session', return_value=(plan, inventory, state)):
            with self.assertRaises(ResearchError) as caught:
                session.validate_request_output(None, {}, packet, request_answer(row))
        self.assert_turn_refusal(caught.exception, '/requests/0',
                                 characters=size, maximum_characters=session.REQUEST_ROW_CHARACTERS)
        observation = dict(number=1, assignment_id='first', attempt_id='attempt',
                           deliveries=[], output=request_answer(row))
        projected = session.build_session_state(inventory, [observation], route, plan, initial_state=state)['state']
        self.assertEqual(projected['requests'][0]['status'], 'over_allowance')
        self.assertEqual(projected['pending_requests'], [])

    def test_duplicate_list_requests_are_refused_before_they_can_queue(self):
        case = self.make_case()
        first = case.case.assign('bar', 'first', identifier='first')
        one = dict(id='list-1', kind='list_page', list='allowances', after=None)
        two = dict(one, id='list-2')
        packet = case.read_packet(first)
        with self.assertRaises(ResearchError) as caught:
            session.validate_request_output(case.case.artifacts, first, packet, request_answer(one, two))
        self.assert_turn_refusal(caught.exception, '/requests/1/list', request_id='list-1')
        plan, inventory, state = session.read_bound_session(case.case.artifacts, first)
        state['requests'] = [dict(one, status='pending', turn=1)]
        state['pending_requests'] = [one]
        # The synthetic validation view is built from its pending state.
        awaiting_delivery = dict(session_turn=dict(allowances=packet['session_turn']['allowances'], plan=dict(now=[])),
                                 evidence=[])
        with patch.object(session, 'read_bound_session', return_value=(plan, inventory, state)):
            with self.assertRaises(ResearchError) as caught:
                session.validate_request_output(case.case.artifacts, first, awaiting_delivery, request_answer(two))
        self.assert_turn_refusal(caught.exception, '/requests/0/list', request_id='list-1')

    def test_packet_passage_room_matches_the_form_checks_combined_limit(self):
        case = self.make_case()
        case.add_sources([4000])
        first = case.case.assign('bar', 'first', identifier='first')
        artifacts = case.case.artifacts
        plan, inventory, state = session.read_bound_session(artifacts, first)
        unit = next(row['handle'] for row in inventory['units'] if row['kind'] == 'fulltext')
        state['allowances']['passages']['used'] = 7
        # Separate starts let one passage complete while the second is split.
        state['pending_requests'] = [request_passage(unit, 20, identifier='short'),
                                     request_passage(unit, 3000, 'x', identifier='long')]
        state['requests'] = [dict(row, status='pending', turn=1) for row in state['pending_requests']]
        logical = protocol._read_json(artifacts, first['session_chain']['logical'])
        route = case.case.read_records()['review_route']['route']
        limits = dict(capacity.derive_plan_task_limits(plan, 'request'), material_allowance=3000)
        assignment = dict(first, session_chain=dict(first['session_chain'], state=protocol._put_json(artifacts, state)))
        packet = session.build_session_packet(artifacts, route, logical, 'first', 2, inventory, state, plan, limits, 'request')
        binding = packet['session_turn']
        passages_now = [row for row in binding['plan']['now'] if row.get('request_kind') == 'passage']
        self.assertEqual([(row['request_id'], row['request_complete']) for row in passages_now],
                         [('short', True), ('long', False)])
        expected = state['allowances']['passages']['characters'] - 7 - sum(
            row['characters'] for row in passages_now) - 3000
        self.assertEqual(binding['allowances']['passages']['available'], expected)
        admitted = request_passage(unit, expected - 1, identifier='admitted')
        edge = request_passage(unit, 1, identifier='edge')
        session.validate_request_output(artifacts, assignment, packet, request_answer(admitted, edge))
        with self.assertRaises(ResearchError) as caught:
            session.validate_request_output(artifacts, assignment, packet,
                                            request_answer(admitted, dict(edge, characters=2)))
        self.assert_turn_refusal(caught.exception, '/requests/1/characters', requested=2, remaining=1)

    def test_page_and_closing_passage_room_matches_the_form_check(self):
        case = self.make_case(pages.SessionPagesTests)
        case.case.metadata(1)
        current = case.start()
        artifacts = case.case.artifacts
        _, inventory, _ = session.read_bound_session(artifacts, current)
        unit = next(row for row in inventory['units'] if row['kind'] == 'abstract')
        for kind in ('page', 'closing'):
            while kind not in case.read_packet(current)['session_final']:
                current = case.advance(current)
            delivery = request_passage(unit['handle'], unit['characters'] + 1, identifier='deliver-' + kind)
            current = case.advance(current, request_answer(delivery))
            packet = case.read_packet(current)
            binding = packet['session_final']
            if kind == 'page':
                page_targets = binding['page']['targets'][:1]
            with self.subTest(turn=kind):
                self.assertIn(kind, binding)
                _, _, state = session.read_bound_session(artifacts, current)
                passages = state['allowances']['passages']
                row, = binding['plan']['now']
                self.assertTrue(row['request_complete'])
                self.assertEqual(row['characters'], unit['characters'])
                available = passages['characters'] - passages['used'] - unit['characters']
                self.assertEqual(binding['request_allowances']['passages'], dict(passages, available=available))
                self.assertEqual(binding['plan']['pending'], dict(requests=1))
                records = case.case.read_records()
                self.assert_bound(case.case, current, records)
                admitted = request_passage(unit['handle'], available - 1, identifier='admitted')
                edge = request_passage(unit['handle'], 1, identifier='edge')
                session.validate_final_turn_output(records, artifacts, current, packet, request_answer(admitted, edge))
                with self.assertRaises(ResearchError) as caught:
                    session.validate_final_turn_output(records, artifacts, current, packet,
                                                       request_answer(admitted, dict(edge, characters=2)))
                self.assert_turn_refusal(caught.exception, '/requests/1/characters', requested=2, remaining=1)
                if kind == 'closing':
                    excerpt = dict(unit=unit['handle'], text=packet['evidence'][0]['content'][:1],
                                   purpose='', targets=page_targets)
                    with self.assertRaises(ResearchError) as caught:
                        session.validate_final_turn_output(records, artifacts, current, packet,
                                                           request_answer(excerpts=[excerpt]))
                    self.assert_turn_refusal(caught.exception, '/excerpts', bound=0, items=1)
                    self.assertEqual(binding['request_allowances']['excerpts'], 0)

    def test_page_has_no_excerpt_room_without_an_eligible_delivery(self):
        case = self.make_case(pages.SessionPagesTests)
        current = case.start()
        with self.subTest(delivery='none'):
            self.assertEqual(case.read_packet(current)['session_final']['request_allowances']['excerpts'], 0)
        current = case.advance(current, request_answer(dict(id='inventory', kind='inventory_page', page=0)))
        binding = case.read_packet(current)['session_final']
        with self.subTest(delivery='inventory'):
            self.assertEqual(binding['plan']['now'][0]['kind'], 'list_page')
            self.assertEqual(binding['request_allowances']['excerpts'], 0)

    def start_page_with_small_material_room(self, case, room):
        case.limit_observed_input(100000)

        def answer(_number, current, _packet):
            case.invoke_usage(current, request_answer(), room=room)

        return case.start(request_answer=answer)

    def test_page_reports_one_pending_request_through_its_last_part(self):
        """Request a passage larger than one page's material room to observe multiple deliveries."""
        case = self.make_case(pages.SessionPagesTests)
        case.add_sources([7000])
        current = self.start_page_with_small_material_room(case, 6800)
        page = case.read_packet(current)['session_final']['page']
        _, inventory, _ = session.read_bound_session(case.case.artifacts, current)
        unit = next(row['handle'] for row in inventory['units'] if row['kind'] == 'fulltext')
        request = request_passage(unit, 7000)
        current = case.advance(current, request_answer(request))
        start = 0
        for part in range(1, 6):
            binding = case.read_packet(current)['session_final']
            self.assertEqual(binding['page'], page)
            self.assertEqual(binding['plan'].get('pending'), dict(requests=1))
            delivery, = binding['plan']['now']
            self.assertEqual((delivery['request_id'], delivery['start']), (request['id'], start))
            self.assertGreater(delivery['end'], start)
            start = delivery['end']
            self.assert_bound(case.case, current, case.case.read_records())
            if delivery['request_complete']:
                self.assertGreater(part, 1)
                self.assertEqual(start, request['characters'])
                break
            current = case.advance(current, request_answer())
        else:
            self.fail('The passage did not complete on its page')
        current = case.advance(current, request_answer())
        self.assertNotIn('plan', case.read_packet(current)['session_final'])

    def test_page_refuses_shared_new_excerpts_and_keeps_request_turn_material(self):
        case = self.make_case(pages.SessionPagesTests)
        case.add_sources([6000, 6001])
        artifacts = case.case.artifacts
        case.limit_observed_input(100000)

        def retain_prior(number, assignment, packet):
            plan, inventory, _ = session.read_bound_session(artifacts, assignment)
            first = next(row['handle'] for row in inventory['units'] if row['kind'] == 'fulltext')
            if number == 0:
                output = request_answer(request_passage(first, 4000))
            else:
                targets = plan['targets']['handles']
                excerpt = dict(unit=first, text=packet['evidence'][0]['content'][:3000],
                               purpose='Retain across pages.', targets=[targets[0], targets[-1]])
                output = request_answer(excerpts=[excerpt])
            case.invoke_usage(assignment, output, room=6800)

        page = case.start(request_answer=retain_prior)
        _, inventory, state = session.read_bound_session(artifacts, page)
        prior, = state['excerpts']
        self.assertEqual(prior['status'], 'verified')
        source = next(row for row in inventory['units'] if row['handle'] == prior['unit'])
        source = {key: source[key] for key in ('handle', 'kind', 'label')}
        second = next(row['handle'] for row in inventory['units']
                      if row['kind'] == 'fulltext' and row['handle'] != prior['unit'])
        current = case.advance(page, request_answer(request_passage(second, 4100, identifier='second')))
        packet = case.read_packet(current)
        binding = packet['session_final']
        delivery, = binding['plan']['now']
        self.assertFalse(delivery['request_complete'])
        self.assertEqual(binding['entries'], [prior])
        self.assertEqual(binding['sources'], [source])
        targets = binding['page']['targets']
        self.assertGreater(len(targets), 1)
        quote = dict(unit=second, text=packet['evidence'][0]['content'][:200], purpose='', targets=targets[:1])
        excerpts = [quote, dict(quote, targets=targets)]
        records = case.case.read_records()
        for settled in ([], ['second']):
            with self.subTest(settled=settled):
                with self.assertRaises(ResearchError) as caught:
                    session.validate_final_turn_output(records, artifacts, current, packet,
                        request_answer(excerpts=excerpts, settled_requests=settled))
                self.assert_turn_refusal(caught.exception, '/excerpts', bound=0, items=2)
        self.assertEqual(binding['request_allowances']['excerpts'], 0)

        current = case.advance(current, request_answer(notes='n' * binding['request_allowances']['notes']))
        successor = case.read_packet(current)['session_final']
        following, = successor['plan']['now']
        self.assertEqual((following['request_id'], following['start'], following['end']), ('second', delivery['end'], 4100))
        self.assertTrue(following['request_complete'])
        self.assertEqual(successor['page']['targets'], targets)
        self.assert_carried_page_material(case, current, prior, source)
        current = case.advance(current)
        next_targets = case.read_packet(current)['session_final']['page']['targets']
        self.assertNotEqual(next_targets, targets)
        self.assertIn(prior['targets'][-1], next_targets)
        self.assert_carried_page_material(case, current, prior, source)

    def assert_carried_page_material(self, case, current, prior, source):
        records = case.case.read_records()
        binding = case.read_packet(current)['session_final']
        self.assertEqual(binding['entries'], [prior])
        self.assertEqual(binding['sources'], [source])
        self.assertEqual(session.read_bound_session(case.case.artifacts, current)[2]['excerpts'], [prior])
        self.assertLessEqual(session.measure_final_material(records['review_route'][current['route_id']], binding),
                             binding['capacity']['material_allowance'])
        self.assert_bound(case.case, current, records)

    def test_held_page_and_closing_keep_maximum_notes_at_measured_room(self):
        """Keep a held page and closing while the planner reduces the room of future work."""
        for form, observed_input, reduced_room in (('page', 100000, 6800), ('closing', 90284, 6710)):
            with self.subTest(form=form):
                self.assert_held_notes(form, observed_input, reduced_room)

    def assert_held_notes(self, form, observed_input, reduced_room):
        from research_harness.review_continuation import plan_next

        case = self.make_case(pages.SessionPagesTests)
        case.add_sources([6000, 6001])
        case.limit_observed_input(observed_input)
        current = case.start(request_answer=lambda _, assignment, packet:
                             case.invoke_usage(assignment, request_answer(), room=24000))
        while form not in case.read_packet(current)['session_final']:
            current = case.advance(current)
        artifacts = case.case.artifacts
        plan, inventory, _ = session.read_bound_session(artifacts, current)
        unit = next(row['handle'] for row in inventory['units'] if row['kind'] == 'fulltext')
        case.invoke_usage(current, request_answer(request_passage(unit, 5000), notes='n'), room=reduced_room)

        def write_planned(parent):
            step = plan_next(case.case.read_records(), artifacts, parent['id'])
            self.assertEqual((step['state'], step['command']), ('continue', 'review-assignment'))
            return case.case.mutate(protocol.record_assignment, step['payload'])['result']

        current = write_planned(current)
        packet = case.read_packet(current)
        binding = packet['session_final']
        self.assertIn(form, binding)
        row, = binding['plan']['now']
        self.assertEqual((row['unit'], row['characters'], row['request_complete']), (unit, 5000, True))
        records = case.case.read_records()
        limits = session.derive_session_task_limits(records, artifacts, current, 'final')
        measured_room = openness_final_pages.measure_page_room(records, artifacts, current, plan, limits)
        self.assertLess(measured_room, binding['capacity']['material_allowance'])
        maximum = binding['request_allowances']['notes']
        route = records['review_route'][current['route_id']]
        planner_notes = capacity.measure_capacity_value(route, 'n' * maximum) - capacity.measure_capacity_value(route, None)
        with self.assertRaises(ResearchError) as caught:
            session.validate_final_turn_output(records, artifacts, current, packet, request_answer(notes='n' * (maximum + 1)))
        self.assert_turn_refusal(caught.exception, '/notes', notes_cost=maximum + 1, bound=maximum)
        for notes in ('n' * maximum, 'n'):
            case.invoke_usage(current, request_answer(notes=notes))
            current = write_planned(current)
            records = case.case.read_records()
            successor = case.read_packet(current)['session_final']
            self.assertIn(form, successor)
            self.assertEqual(successor['request_allowances']['excerpts'], 0)
            self.assertEqual(session.read_bound_session(artifacts, current)[2]['latest_notes'], notes)
            self.assertLessEqual(session.measure_final_material(records['review_route'][current['route_id']], successor),
                                 successor['capacity']['material_allowance'])
            self.assert_bound(case.case, current, records)
        self.assertLessEqual(planner_notes + capacity.EXCERPT_TEXT_CHARACTERS + capacity.EXCERPT_FIELDS_CHARACTERS,
                             measured_room)

    def test_full_notes_with_a_ratio_fall_of_the_same_answer_refuse_the_next_page_below_floor(self):
        """The answer that fills the notes room also measures a smaller room, which then holds no condensable notes."""
        case, current = self.answer_page_with_a_ratio_fall('n')
        successor = case.case.assign(current['role'], 'first', identifier='after-fall', prior_assignment_id=current['id'])
        self.assertIn('page', case.read_packet(successor)['session_final'])

        case, current = self.answer_page_with_a_ratio_fall(None)
        revision = case.case.store.revision
        with self.assertRaises(ResearchError) as caught:
            case.case.assign(current['role'], 'first', identifier='after-fall', prior_assignment_id=current['id'])
        self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')
        details = caught.exception.details
        self.assertLess(details['room'], details['minimum'])
        self.assertEqual(case.case.store.revision, revision)
        # The plan ends the chain with the stop that the store takes, not with the refused assignment.
        action = self.next_action(case, current)
        self.assertEqual(action.get('code'), 'review_route_capacity_below_floor', action)
        self.stop_action(case, current, action)

    def answer_page_with_a_ratio_fall(self, notes):
        """Answer a page with the given notes, or with its full notes room for None, while its usage lowers the room."""
        case = self.make_case(pages.SessionPagesTests)
        case.add_sources([6000, 6001])
        case.limit_observed_input(100000)
        current = case.start(request_answer=lambda _, assignment, packet:
                             case.invoke_usage(assignment, request_answer(), room=24000))
        _, inventory, _ = session.read_bound_session(case.case.artifacts, current)
        unit = next(row['handle'] for row in inventory['units'] if row['kind'] == 'fulltext')
        current = case.advance(current, request_answer(request_passage(unit, 5000)))
        if notes is None:
            notes = 'n' * case.read_packet(current)['session_final']['request_allowances']['notes']
        case.invoke_usage(current, request_answer(notes=notes), room=6800)
        return case, current

    def test_frame_delivery_names_the_escaped_field_pointer(self):
        with TemporaryDirectory() as directory:
            artifacts = ArtifactStore(Path(directory))
            artifact = artifacts.put(b'{}', 'application/json')
            unit = dict(artifact=artifact, handle='u1', kind='frame', characters=2,
                        identity=dict(field=['role~field', 'item/name', 0]))
            row, _ = session._build_delivery(artifacts, unit, 0, 2)
            archived, _ = session._build_delivery(artifacts, dict(unit, kind='final_entry', identity={}), 0, 2)
        self.assertEqual(row.get('field'), '/role~0field/item~1name/0')
        self.assertNotIn('field', archived)

    def test_result_page_target_names_the_objection_id(self):
        case = self.make_case(pages.SessionPagesTests)
        current = case.start('result', targets=3)
        for _ in range(10):
            packet = case.read_packet(current)
            binding = session.read_session_binding(packet)
            if 'page' in binding:
                rows = [row for row in binding['targets'] if row['label'] == 'A synthetic material gap.']
                if rows:
                    _, inventory, _ = session.read_bound_session(case.case.artifacts, current)
                    ids = {row['handle']: row['id'] for row in inventory['targets']}
                    self.assertEqual([row.get('id') for row in rows], [ids[row['handle']] for row in rows])
                    return
            self.assert_bound(case.case, current, case.case.read_records())
            current = case.advance(current)
        self.fail('No objection target reached a result page')

    def test_target_rows_with_ids_are_measured_against_their_bound(self):
        route = build_route()
        rows = [dict(handle='t%d' % i, label='Claim ' * 20, id='objection-%d' % i) for i in range(50)]
        bar = dict(handle='t51', label='bar', id='bar')
        labels = session.describe_target_labels(route, rows + [bar])
        self.assertIsInstance(labels, list)
        self.assertEqual([row.get('id') for row in labels[:-1]], [row['id'] for row in rows])
        self.assertEqual(labels[-1], dict(handle='t51', label='bar'))
        self.assertLessEqual(capacity.measure_capacity_value(route, labels), 6000)
        self.assertTrue(all(len(row['label']) < 120 for row in labels[:-1]))
