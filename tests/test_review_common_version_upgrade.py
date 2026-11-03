"""Review sessions retain their contracts across a common text upgrade."""

import copy
import json
import sys
import unittest
from unittest.mock import patch

import openness_reading_fixtures as reading_fixtures
import problem_parts_fixtures as parts_fixtures
import test_research_claude_transport as claude_fixtures
import test_research_codex_controls as control_fixtures
import test_research_context_repair_integration as repair_fixtures
import test_research_problem_reviews as problem_fixtures
import test_review_objection_identity as objection_fixtures
import test_review_staged_protocol as staged_fixtures
import test_visual_result_delivery as visual_fixtures
from codex_route_fixtures import record_codex_route
from research_harness import openness_reading
from research_harness import research_decisions as decisions
from research_harness import review_claude_transport as claude
from research_harness import review_codex_framed_transport as framed
from research_harness import review_protocol as protocol
from research_harness import review_staged as staged
from research_harness import review_staged_protocol as staged_protocol
from research_harness import review_transport as transport
from research_harness import review_worker
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.review_bodies import read_history


def open_fixture(test, fixture_class):
    fixture = fixture_class()
    test.addCleanup(fixture.doCleanups)
    fixture.setUp()
    return fixture


def assert_prompt_version(test, assignment, version):
    prompt = assignment['prompt']
    test.assertEqual(prompt['parameters']['common_version'], version)
    test.assertTrue(prompt['text'].startswith(
        protocol._COMMON_PROMPTS[version]))
    test.assertTrue(protocol._prompt_matches(assignment))


def assert_verified(test, case, assignment):
    state = protocol.assignment_state(
        case.store.snapshot()['records'], case.artifacts, assignment['id'])
    test.assertTrue(state['ready'], state['obligations'])
    test.assertEqual(state['context_status'], 'verified_for_route')
    return state


class ProblemSessionUpgradeTests(unittest.TestCase):
    def test_a_source_session_replays_its_completed_turn_after_upgrade(self):
        case = open_fixture(self, problem_fixtures.ProblemReviewTests)
        with patch.object(protocol, 'COMMON_VERSION', 2):
            case.add_reading_and_computation()
            case.supporting = case.artifacts.put(
                b'A field standard.', 'text/plain')
            case.prepare_review_route()
            output = case.build_review_output('bar')
            output['value']['status'] = 'unresolved'
            output['source_requests'] = [{
                'id': 'decisive-source', 'kind': 'source', 'depth': 'fulltext',
                'version_id': case.expected['readings'][0]['version_id']}]
            case.observe('bar', 'first', output, record=False)
            records = case.read_records()
            previous = copy.deepcopy(
                records['review_assignment']['bar-first'])
            attempt = copy.deepcopy(
                records['review_attempt']['attempt-bar-first'])
            request_bytes = case.artifacts.read(attempt['request'])
            stored_request = json.loads(request_bytes)
            history = copy.deepcopy(read_history(case.artifacts, previous))
            before = assert_verified(self, case, previous)

        after = assert_verified(self, case, previous)
        self.assertEqual(after['context_status'], before['context_status'])
        self.assertEqual(protocol.build_assignment_request(
            case.read_records(), case.artifacts, previous), stored_request)
        self.assertEqual(case.artifacts.read(attempt['request']),
                         request_bytes)
        self.assertEqual(
            case.read_records()['review_assignment'][previous['id']], previous)
        self.assertEqual(read_history(case.artifacts, previous), history)

        continued = case.assign('bar', 'first', identifier='bar-after-upgrade',
                                prior_assignment_id=previous['id'])
        expected_history = transport.logical_messages(stored_request) + [{
            'role': 'assistant',
            'content': json.dumps(output, sort_keys=True)}]
        self.assertEqual(read_history(case.artifacts, continued),
                         expected_history)
        packet = json.loads(case.artifacts.read(continued['packet']))
        self.assertEqual(packet['source_deliveries'][0]['status'], 'delivered')
        final = case.build_review_output('bar')
        with patch.object(transport, 'send_request',
                          return_value=case.build_provider_response(final)):
            case.mutate(protocol.invoke_assignment, {
                'id': 'attempt-after-upgrade',
                'assignment_id': continued['id']},
                credential='test-only')
        case.mutate(decisions.record_value_review, dict(
            final, id='review-after-upgrade', dossier_id=None,
            assignment_id=continued['id']))
        assert_verified(self, case, previous)
        assert_verified(self, case, continued)
        assert_prompt_version(self, previous, 2)
        assert_prompt_version(self, continued, protocol.COMMON_VERSION)
        self.assertGreater(protocol.COMMON_VERSION, 2)
        records = case.read_records()
        self.assertEqual(records['review_assignment'][previous['id']],
                         previous)
        self.assertEqual(records['review_attempt'][attempt['id']], attempt)
        self.assertEqual(read_history(case.artifacts, previous), history)

    def test_a_repair_preserves_its_predecessor_across_the_upgrade(self):
        with patch.object(protocol, 'COMMON_VERSION', 2):
            case = open_fixture(
                self, repair_fixtures.ContextRepairIntegrationTests)
            previous = copy.deepcopy(case.store.snapshot()['records'][
                'review_assignment']['assignment-bar-a'])
        repaired = case.prepare_repair()
        output = case.final_response()
        case.invoke(output)
        case.record_final(output)
        assert_verified(self, case, repaired)
        assert_prompt_version(self, previous, 2)
        assert_prompt_version(self, repaired, protocol.COMMON_VERSION)
        self.assertGreater(protocol.COMMON_VERSION, 2)
        records = case.store.snapshot()['records']
        self.assertEqual(records['review_assignment'][previous['id']],
                         previous)
        state = protocol.assignment_state(
            records, case.artifacts, previous['id'])
        self.assertEqual(state['context_status'], 'contaminated')

    def test_the_common_text_change_keeps_the_focused_adjudicator(self):
        with patch.object(protocol, 'COMMON_VERSION', 2):
            case = open_fixture(
                self, objection_fixtures.BlindObjectionIdentityTests)
            case.prepare_colliding_bars()
            case.observe_results({
                objection_fixtures.SHARED_ID: 'continuing',
                objection_fixtures.QUALIFIED_ID: 'continuing'})
            case.adjudicate('focused', objection_fixtures.QUALIFIED_ID,
                            objection_fixtures.BARS, 'upheld')
            before = copy.deepcopy(case.store.snapshot()['records'])
        previous = before['review_assignment']['focused']
        assert_verified(self, case, previous)
        self.assertFalse(protocol.is_previous_prompt(previous))
        with self.assertRaises(ResearchError) as refused:
            case.assign('bar', 'first', identifier='bar-after-upgrade')
        self.assertEqual(refused.exception.code, 'review_resampling_forbidden')
        for reviewer in ('focused-third', 'replacement-adjudicator'):
            with self.subTest(reviewer=reviewer):
                with self.assertRaises(ResearchError) as refused:
                    case.assign('adjudicator', reviewer,
                                identifier='focused-after-upgrade',
                                review_ids=objection_fixtures.BARS,
                                objection_id=objection_fixtures.QUALIFIED_ID)
                self.assertEqual(refused.exception.code,
                                 'review_adjudicator_fixed')
        self.assertEqual(case.store.snapshot()['records'], before)
        assert_prompt_version(self, previous, 2)
        self.assertGreater(protocol.COMMON_VERSION, 2)


class PartsSessionUpgradeTests(parts_fixtures.ResultPartsCase):
    def setUp(self):
        with patch.object(protocol, 'COMMON_VERSION', 2):
            super().setUp()

    def test_a_parts_series_verifies_turns_under_their_recorded_versions(self):
        self.oversized()
        with patch.object(protocol, 'COMMON_VERSION', 2):
            first = self.assign()
            self.assertEqual(self.packet(first)[parts_fixtures.KEY]['stage'],
                             'part')
            self.invoke(first)
            assert_verified(self, self.case, first)
            frozen = copy.deepcopy(
                self.case.read_records()['review_assignment'][first['id']])
            request = self.request(first)
        second = self.assign('part-after-upgrade', first['id'])
        self.assertEqual(self.packet(second)[parts_fixtures.KEY]['turn'], 2)
        remaining, final = self.finish_parts(first=second)
        self.record_final(final)
        for assignment in [first] + remaining + [final]:
            assert_verified(self, self.case, assignment)
            assert_prompt_version(self, assignment,
                                  2 if assignment['id'] == first['id']
                                  else protocol.COMMON_VERSION)
        self.assertEqual(self.request(first), request)
        self.assertEqual(
            self.case.read_records()['review_assignment'][first['id']], frozen)
        self.assertGreater(protocol.COMMON_VERSION, 2)


class OpennessSessionUpgradeTests(unittest.TestCase):
    def test_an_openness_chain_verifies_turns_across_the_upgrade(self):
        case = open_fixture(self, reading_fixtures.OpennessReadingCase)
        case.limit_input()
        native_packet = case.export()
        with patch.object(protocol, 'COMMON_VERSION', 2):
            first = case.assign('turn-1', native_packet)
            case.invoke(first['id'], case.build_reading_response(
                case.read_packet(first['id'])))
            assert_verified(self, case, first)
            frozen = copy.deepcopy(case.store.snapshot()['records'])
        turns = [first]
        while 'reading_turn' in case.read_packet(turns[-1]['id']):
            self.assertLess(len(turns), len(case.versions) + 1)
            assignment = case.assign(
                'turn-%d' % (len(turns) + 1), native_packet,
                prior=turns[-1]['id'])
            turns.append(assignment)
            packet = case.read_packet(assignment['id'])
            output = (case.build_reading_response(packet)
                      if 'reading_turn' in packet
                      else case.build_native_result(packet))
            case.invoke(assignment['id'], output)
        records = case.store.snapshot()['records']
        receipts = openness_reading.verify_chain(
            records, case.artifacts, turns[-1])
        self.assertEqual([receipt['assignment_id'] for receipt in receipts],
                         [assignment['id'] for assignment in turns])
        for index, assignment in enumerate(turns):
            assert_verified(self, case, assignment)
            assert_prompt_version(
                self, assignment, 2 if index == 0 else protocol.COMMON_VERSION)
        for kind in ('review_assignment', 'review_attempt'):
            for identifier, saved in frozen[kind].items():
                self.assertEqual(records[kind][identifier], saved)
        self.assertGreater(protocol.COMMON_VERSION, 2)


class StagedEpochUpgradeTests(parts_fixtures.ResultPartsCase):
    staged_entry = True

    def setUp(self):
        with patch.object(protocol, 'COMMON_VERSION', 2):
            super().setUp()

    def test_a_staged_epoch_keeps_old_plans_and_renders_new_plans(self):
        self.oversized()
        with patch.object(protocol, 'COMMON_VERSION', 2):
            controller = self.assign_staged()
            records = self.case.read_records()
            task_id = controller['staged_review']['task_ids'][0]
            planned = copy.deepcopy(records['review_task'][task_id])
            request_bytes = self.case.artifacts.read(planned['request'])
        old_child = self.case.assign(
            'result', 'first', identifier='old-plan-child',
            controller_assignment_id=controller['id'], task_id=task_id)
        self.assertEqual(old_child['prompt'], planned['prompt'])
        self.assertEqual(old_child['planned_request_digest'],
                         planned['task']['planned_request_digest'])
        self.assertEqual(digest(self.request(old_child)),
                         digest(json.loads(request_bytes)))

        epoch_id = controller['staged_review']['epoch_id']
        epoch = records['review_epoch'][epoch_id]
        source = next(row for row in epoch['manifest']['sources']
                      if row['kind'] == 'frame')
        task = staged.make_task(epoch, 'frame', inputs=[
            staged.source_span(source, 0, len(source['text']))])
        self.assertNotIn(task['id'], records['review_task'])
        new_child = self.case.assign(
            'result', 'first', identifier='new-plan-child',
            controller_assignment_id=controller['id'],
            task_id=task['id'], task=task)
        self.assertEqual(new_child['staged_review']['epoch_id'],
                         old_child['staged_review']['epoch_id'])
        for child in (old_child, new_child):
            with patch.object(review_worker, '_entry_command', return_value=[
                    sys.executable, '-c', staged_fixtures._WORKER_SCRIPT]):
                self.case.mutate(protocol.invoke_assignment, {
                    'id': 'attempt-' + child['id'],
                    'assignment_id': child['id']})
            current = self.case.read_records()
            state = protocol.assignment_state(
                current, self.case.artifacts, child['id'])
            self.assertTrue(state['accepted'], state['obligations'])
            self.assertEqual(state['context_status'], 'verified_for_route')
            self.assertTrue(staged_protocol.matches_prompt(
                child, child['prompt']['parameters']['common_version']))
            self.assertEqual(digest(self.request(child)),
                             child['planned_request_digest'])
        assert_prompt_version(self, old_child, 2)
        assert_prompt_version(self, new_child, protocol.COMMON_VERSION)
        self.assertEqual(self.case.read_records()['review_task'][task_id],
                         planned)
        self.assertEqual(self.case.artifacts.read(planned['request']),
                         request_bytes)
        self.assertGreater(protocol.COMMON_VERSION, 2)


class AssignmentInstructionsUpgradeTests(unittest.TestCase):
    def check_request_instructions(self, adapter):
        case = open_fixture(self, problem_fixtures.ProblemReviewTests)
        case.supporting = case.artifacts.put(
            b'A field standard.', 'text/plain')
        if adapter == 'codex_cli_v1':
            record_codex_route(case)
        elif adapter == 'codex_cli_framed_v1':
            controls = control_fixtures.CodexControlTests()
            runtime = {'adapter': adapter, 'fixture': 'common-version-upgrade'}
            patcher = patch.object(framed, 'runtime', return_value=runtime)
            case.addCleanup(patcher.stop)
            patcher.start()
            case.mutate(protocol.record_route,
                        dict(controls.build_route(framed), id='route'))
        elif adapter == 'openai_responses_v1':
            case.prepare_review_route()
        else:
            case.mutate(protocol.record_route,
                        dict(claude_fixtures.ROUTE, id='route'))

            def respond_to_probe(route, request, credential):
                packet = json.loads(request['input'][0]['content'][
                    len(transport.PACKET_LABEL):])
                output = {'allowed_control': packet['allowed_control'],
                          'evidence_control': packet['evidence_control'],
                          'excluded_controls': []}
                events = claude_fixtures.clean_stream(
                    request, json.dumps(output), model=route['model'])
                with patch.object(claude, '_cli', return_value=(
                        '/fixture/claude', '2.1.310')), patch.object(
                        claude, '_run_owned',
                        return_value=claude_fixtures.completed(events)):
                    return claude.send_request(route, request)

            with patch.object(transport, 'send_request',
                              side_effect=respond_to_probe):
                case.mutate(protocol.probe_route,
                            {'id': 'probe', 'route_id': 'route'},
                            credential='test-only')
        assignment = case.assign(
            'bar', 'first', **case.deliver_supporting('first'))
        request = protocol.build_assignment_request(
            case.read_records(), case.artifacts, assignment)
        route = case.read_records()['review_route']['route']
        self.assertEqual(route['adapter'], adapter)
        self.assertEqual(request['instructions'], assignment['prompt']['text'])
        assert_prompt_version(self, assignment, protocol.COMMON_VERSION)
        self.assertGreater(protocol.COMMON_VERSION, 2)

    def test_codex_requests_carry_the_new_assignments_instructions(self):
        self.check_request_instructions('codex_cli_v1')

    def test_claude_requests_carry_the_new_assignments_instructions(self):
        self.check_request_instructions('claude_cli_v1')

    def test_https_requests_carry_the_new_assignments_instructions(self):
        self.check_request_instructions('openai_responses_v1')

    def test_framed_requests_carry_the_new_assignments_instructions(self):
        self.check_request_instructions('codex_cli_framed_v1')

    def test_visual_requests_carry_the_new_assignments_instructions(self):
        fixture = open_fixture(self, visual_fixtures.VisualResultDeliveryTests)
        assignment = fixture.assignment
        records = fixture.case.read_records()
        request = protocol.build_assignment_request(
            records, fixture.artifacts, assignment)
        self.assertEqual(
            records['review_route'][assignment['route_id']]['adapter'],
            'codex_cli_visual_v1')
        self.assertEqual(request['instructions'], assignment['prompt']['text'])
        assert_prompt_version(self, assignment, protocol.COMMON_VERSION)
        self.assertGreater(protocol.COMMON_VERSION, 2)


if __name__ == '__main__':
    unittest.main()
