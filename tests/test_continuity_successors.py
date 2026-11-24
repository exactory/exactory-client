"""G1 / closure 8.3.6: execute each installed successor admission boundary."""

import unittest
from test_series_code_reach import record_reachable_errors
from unittest.mock import patch

import test_openness_reading_v7_refusal as refusal
import test_openness_unread_final as unread
from research_harness import openness_reading as reading, openness_reading_v7 as v7
from research_harness import review_failure_class as failures, stop_classes
from research_harness.review_continuation import plan_next
import continuity_stage3_fixtures as stage3


class SuccessorJourneyTests(unittest.TestCase):
    def setUp(self):
        from research_harness import openness_reading_v7
        released = patch.object(openness_reading_v7, 'V7_FINAL_FORMS', ('request',))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def prepare(self, cls):
        case = cls()
        self.addCleanup(case.doCleanups)
        case.setUp()
        return case

    @record_reachable_errors('plan_next')
    def test_g1_k7_v5_input_stop_exports_v7_carries_and_records(self):
        case = self.prepare(refusal.LegacySuccessorTests)
        case.test_k7_v5_input_limit_exports_to_v7_carries_and_records()

    @record_reachable_errors('plan_next')
    def test_g1_k7_v6_input_stop_exports_v7_carries_and_records(self):
        case = self.prepare(refusal.LegacySuccessorTests)
        case.test_k7_v6_input_limit_exports_to_v7_carries_and_records()

    @record_reachable_errors('plan_next')
    def test_g1_k7_v5_v6_reading_input_executes_all_successor_admissions(self):
        import continuity_guard_fixtures as matrix
        from research_harness import review_protocol as protocol
        for historical in (True, False):
            for end in ('v7', 'v6', 'neither'):
                with self.subTest(origin='v5' if historical else 'v6', successor=end):
                    case = self.prepare(refusal.LegacySuccessorTests)
                    case.historical_v5 = historical
                    if end == 'v6':
                        case.select_targets(9)
                    with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (reading.PROTOCOL_V6,)):
                        native = case.export()
                        case.assign('turn-1', native)
                        case.invoke('turn-1', case.build_reading_response(case.read_packet('turn-1'), excerpts=[]))
                        case.assign('blocked', native, prior='turn-1')
                    self.assertEqual(reading.find_stage(case.read_packet('blocked')), 'reading')
                    with patch.object(protocol.transport, 'send_request', side_effect=matrix.fail_size_request('input_over_limit', None)):
                        matrix.execute(case, 'review-run', dict(id='blocked-input', assignment_id='blocked'))
                    case.historical_v5 = False
                    with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
                        if end == 'neither':
                            case.fail_legacy_final = lambda: 'blocked'
                            case.test_k7_no_successor_stops_with_two_answers_and_an_executable_route()
                        else:
                            action = plan_next(case.store.snapshot()['records'], case.artifacts, 'blocked')
                            self.assertEqual((action['state'], action['command']), ('handoff', 'export'))
                            expected = v7.PROTOCOL if end == 'v7' else reading.PROTOCOL_V6
                            self.assertEqual(action['details']['successor']['protocol'], expected)
                            reference, assigned = case.execute_export(action)
                            self.assertEqual(assigned['protocol_selection']['protocol'], expected)
                            if end == 'v7':
                                self.assertTrue(assigned['carried_readings']['chains'])
                            self.assertEqual(plan_next(case.store.snapshot()['records'], case.artifacts, assigned['id'])['state'], 'run')
                            case.finish_from(reference, assigned['id'])
                    case.doCleanups()

    @record_reachable_errors('plan_next')
    def test_g1_k7_v5_v6_nine_targets_fall_back_to_v6_and_record(self):
        for historical in (True, False):
            with self.subTest(protocol='v5' if historical else 'v6'):
                case = self.prepare(refusal.LegacySuccessorTests)
                case.historical_v5 = historical
                case.select_targets(9)
                with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (reading.PROTOCOL_V6,)):
                    head = case.fail_legacy_final(expected_protocol=reading.PROTOCOL_V5 if historical else reading.PROTOCOL_V6)
                with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
                    action = plan_next(case.store.snapshot()['records'], case.artifacts, head)
                    self.assertEqual((action['state'], action.get('command')), ('handoff', 'export'), action)
                    self.assertEqual(action['details']['successor']['protocol'], reading.PROTOCOL_V6)
                    self.assertEqual(action['details']['successor']['admissions'][0]['state'], 'stop')
                    native, assignment = case.execute_export(action)
                    self.assertEqual(assignment['protocol_selection']['protocol'], reading.PROTOCOL_V6)
                    case.finish_from(native, assignment['id'])
                case.doCleanups()

    @record_reachable_errors('plan_next')
    def test_g1_k7_v5_v6_no_successor_executes_each_answer(self):
        for historical in (True, False):
            with self.subTest(protocol='v5' if historical else 'v6'):
                case = self.prepare(refusal.LegacySuccessorTests)
                case.historical_v5 = historical
                if not historical:
                    original = case.fail_legacy_final
                    case.fail_legacy_final = lambda fail=True: original(fail=fail, expected_protocol=reading.PROTOCOL_V6)
                case.test_k7_no_successor_stops_with_two_answers_and_an_executable_route()
                case.doCleanups()

    @record_reachable_errors('plan_next')
    def test_g1_k7_v7_ratio_export_carries_measure_and_records(self):
        case = self.prepare(refusal.V7RefusalTests)
        case.test_k7_dense_failed_final_carries_ratio_through_check_assignment_and_finding()

    @record_reachable_errors('plan_next')
    def test_g1_k7_v7_ratio_no_successor_executes_each_answer(self):
        from codex_route_fixtures import build_codex_response, bind_codex_response
        from research_harness import review_route_capacity as capacity, review_protocol as protocol
        case, native, head = stage3.prepare_turn(self, 'final')
        def respond(route, request, credential):
            response = build_codex_response(route, request, {})
            response['status'] = 'incomplete'
            response['usage']['input_tokens'] = capacity.measure_request(route, request)['text_characters'] * 5
            return bind_codex_response(route, request, response)
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            case.mutate(protocol.invoke_assignment, dict(id='dense-final', assignment_id=head), credential='fixture')
        case.fail_legacy_final = lambda: head
        refusal.LegacySuccessorTests.test_k7_no_successor_stops_with_two_answers_and_an_executable_route(case)

    @record_reachable_errors('plan_next')
    def test_g1_k7_v7_ratio_v6_fallback_terminates_with_recorded_finding(self):
        case = self.prepare(refusal.V7RefusalTests)
        case.test_k7_repeated_dense_ratios_end_after_two_successor_chains()

    @record_reachable_errors('plan_next')
    def test_g1_k7_v5_v6_last_provider_sequence_names_and_executes_row4(self):
        for historical in (True, False):
            with self.subTest(protocol='v5' if historical else 'v6'):
                case = self.prepare(refusal.LegacySuccessorTests)
                case.historical_v5 = historical
                with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (reading.PROTOCOL_V6,)):
                    native = case.export()
                    case.assign('turn-1', native)
                head = 'turn-1'
                for number in range(40):
                    action = plan_next(case.store.snapshot()['records'], case.artifacts, head)
                    if action['state'] == 'wait':
                        clock = failures.datetime
                        deadline = clock.fromisoformat(action['not_before'])
                        class DueClock(clock):
                            @classmethod
                            def now(cls, tz=None):
                                return deadline
                        with patch.object(failures, 'datetime', DueClock):
                            action = plan_next(case.store.snapshot()['records'], case.artifacts, head)
                    if action['state'] == 'stop':
                        self.assertEqual(action['stop_class'], 'decision')
                        self.assertIn(action['code'], stop_classes.STOP_REGISTRY)
                        steps = [step for answer in action['details']['decision']['then'] for step in answer['steps']]
                        self.assertTrue(any(step['id'] == 4 for step in steps))
                        case.historical_v5 = False
                        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
                            stage3.execute_stop_answers(self, case, head, action)
                        break
                    self.assertEqual(action['state'], 'run', action)
                    unread.UnreadFinalSupport.refuse_provider(case, action['payload']['assignment_id'], action['payload']['id'])
                else:
                    self.fail('The last legacy provider sequence must reach row 4.')
                case.doCleanups()
