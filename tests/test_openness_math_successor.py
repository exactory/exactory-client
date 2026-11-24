"""A25 and ruling 24.2: the three stops of a math v1 chain, each with the step of its admitted successor.

Every test sets openness_reading.NEW_MATH_CHAIN_PROTOCOLS to (math v2, math v1) for its duration.
"""

import json
import unittest
from unittest.mock import patch

import test_openness_carried_math as math_v1_fixtures
import test_reading_forecast as forecasts
import test_research_problem_openness_minimum as minimum_fixtures
from test_openness_reading_math_v2 import MathV2Support
from research_harness import openness_minimum, openness_reading as reading, openness_reading_v7 as v7
from research_harness import openness_successor as successor
from research_harness import review_protocol as protocol, review_route_capacity as capacity
from research_harness.check_mode import check_mutation
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.problem_openness import serialize_record
from research_harness.review_continuation import plan_next

MATH_V2 = reading.MATH_PROTOCOL_V2


def list_answers(decision):
    return [row['answer'] for row in decision['then']]


class MathSuccessorTests(MathV2Support):
    text_sizes = (80000, 80000, 80000)

    def run_v1_turns(self, native, count=3):
        with patch.object(reading, 'NEW_MATH_CHAIN_PROTOCOLS', (reading.MATH_PROTOCOL,)):
            for number in range(1, count + 1):
                self.run_turn(native, 'math-%d' % number, 'math-%d' % (number - 1) if number > 1 else None)

    def stop_v1_final(self, native):
        """Three v1 turns retain unmatched excerpts, which no rule bounds (AR18.2). On a route of 150,000 characters
        the exact measure of ruling 24.2 stops the final before its call, and v2 admits a chain over the packet."""
        with patch.object(reading, 'NEW_MATH_CHAIN_PROTOCOLS', (reading.MATH_PROTOCOL,)):
            for number in (1, 2, 3):
                identifier = 'math-%d' % number
                self.assign(identifier, native, 'math-%d' % (number - 1) if number > 1 else None)
                self.invoke(identifier, self.build_reading_response(self.read_packet(identifier),
                            excerpts=forecasts.MathForecastTests.build_diagnostics(self, number)))
        self.limit_input(150000)
        with self.assertRaises(ResearchError) as caught:
            self.assign('math-final', native, 'math-3')
        self.assertEqual(caught.exception.code, reading.PROJECTION_STOP)
        self.assertFalse(caught.exception.details.get('estimate', False))
        return caught.exception.details

    def test_a25_exact_final_stop_names_the_successor_and_its_steps_reach_a_recorded_review(self):
        native = self.build_math_packet()
        details = self.stop_v1_final(native)
        records = self.store.snapshot()['records']
        before = {kind: {key: digest(row) for key, row in group.items()} for kind, group in records.items()}
        decision = details['decision']
        self.assertEqual(decision['owner_decides'], successor.MATH_EXACT_DECISION)
        self.assertEqual(list_answers(decision), [successor.MATH_SUCCESSOR_ANSWER, reading.SMALLER_PACKET_ANSWER])
        self.assertEqual(details['successor']['protocol'], MATH_V2)
        self.assertEqual(len(details['successor']['carried_readings']['chains']), 1)
        [step] = decision['then'][0]['steps']
        packet = json.loads(self.artifacts.read(native))
        self.assertEqual((step['id'], step['command'], step['reviewer_id']), (5, 'search review-packet', 'openness-reviewer'))
        self.assertEqual(step['payload'], dict(kind='result', subject=packet['subject'], claim_digest=packet['claim_digest']))
        # This converted problem packet omits the math controller's digest-bound claim.
        self.assertIn('openness', step['chooses'])
        self.assertEqual([value.get('check', False) for value in step['then']], [True, False])
        # The controller's review-packet command builds the successor packet; this fixture attests it.
        packet['reviewer']['attestation_id'] = 'review-assignment:successor'
        successor_native = self.artifacts.put(serialize_record(packet), 'application/json')
        payloads = [dict(value['payload'], id='successor', context=dict(native_packet=successor_native))
                    for value in step['then']]
        checked = check_mutation(self.store, protocol.record_assignment, payloads[0],
            expected_revision=self.store.revision, request_id='successor-check', bounded_payload=payloads[0])
        result = self.mutate(protocol.record_assignment, payloads[1])['result']
        self.assertEqual(checked['carried_readings'], result['carried_readings'])
        final, packets = self.finish(successor_native, first='successor', assigned=True)
        assigned = self.store.snapshot()['records']['review_assignment']['successor']
        selection = json.loads(self.artifacts.read(assigned['carried_selection']['artifact']))
        carried = {span['unit'] for turn in selection['turns'] for span in turn['ranges']}
        self.assertEqual([row['origin']['assignment_id'] for row in selection['turns']], ['math-1', 'math-2', 'math-3'])
        for turn in packets:
            if 'reading_turn' in turn:
                self.assertTrue(carried.isdisjoint(row['handle'] for row in turn['reading_turn']['plan']['now']))
        self.assert_final(final)
        current = self.store.snapshot()['records']
        for kind, group in before.items():
            self.assertEqual({key: digest(current[kind][key]) for key in group}, group)

    def test_a25_exact_stop_of_a_reading_turn_has_no_continue_answer(self):
        native = self.build_math_packet()
        self.run_v1_turns(native, 1)
        self.limit_input(1000)
        with self.assertRaises(ResearchError) as caught:
            self.assign('math-2', native, 'math-1')
        self.assertFalse(caught.exception.details.get('estimate', False))
        answers = list_answers(caught.exception.details['decision'])
        self.assertEqual(caught.exception.details['successor']['protocol'], reading.MATH_PROTOCOL)
        self.assertEqual(answers, [reading.SMALLER_PACKET_ANSWER])

    def test_a25_capacity_decision_of_a_v1_turn_without_a_smaller_task_names_the_successor(self):
        native = self.build_math_packet()
        self.run_v1_turns(native, 1)
        records = self.store.snapshot()['records']
        decision = capacity.describe_capacity_decision(records, self.artifacts, records['review_assignment']['math-1'],
            'output_over_cap', dict(protocol=reading.MATH_PROTOCOL, kind='reading', handler='nothing'))
        self.assertEqual(decision['details']['successor']['protocol'], MATH_V2)
        self.assertEqual(list_answers(decision), [successor.MATH_SUCCESSOR_ANSWER, 'Move to another route'])
        self.assertEqual(decision['then'][0]['steps'][0]['id'], 5)
        with patch.object(reading, 'NEW_MATH_CHAIN_PROTOCOLS', (reading.MATH_PROTOCOL,)):
            decision = capacity.describe_capacity_decision(records, self.artifacts, records['review_assignment']['math-1'],
                'output_over_cap', dict(protocol=reading.MATH_PROTOCOL, kind='reading', handler='nothing'))
        self.assertEqual(list_answers(decision), ['Move to another route'])

    def test_a25_v2_refusal_gives_a_v1_successor_that_reads_every_unit_again(self):
        native = self.build_math_packet()
        self.run_v1_turns(native, 1)
        refusal = dict(state='stop', protocol=MATH_V2, reason='binary_required_evidence')
        with patch.object(v7, 'check_v7_admission', return_value=refusal):
            records = self.store.snapshot()['records']
            admitted = successor.admit_successor_chain(records, self.artifacts, records['review_assignment']['math-1'])
            handoff = successor.describe_successor_handoff(records, self.artifacts, records['review_assignment']['math-1'],
                                                           reading.PROJECTION_STOP)
        self.assertEqual(admitted['protocol'], reading.MATH_PROTOCOL)
        self.assertEqual(admitted['admissions'][0]['reason'], 'binary_required_evidence')
        self.assertTrue(admitted['reads_every_unit_again'])
        self.assertNotIn('carried_readings', admitted)
        self.assertEqual(handoff['state'], 'stop')
        self.assertEqual(handoff['details']['successor']['protocol'], reading.MATH_PROTOCOL)
        self.assertEqual(list_answers(handoff['details']['decision']), [reading.SMALLER_PACKET_ANSWER])

    def test_exact_stop_of_a_stored_v1_chain_with_a_long_head_plans_v2(self):
        native = self.build_math_packet()
        head = 'z' * 121
        identifiers = ('math-1', 'math-2', head)
        with patch.object(reading, 'NEW_MATH_CHAIN_PROTOCOLS', (reading.MATH_PROTOCOL,)):
            for index, identifier in enumerate(identifiers):
                self.assign(identifier, native, identifiers[index - 1] if index else None)
                self.invoke(identifier, self.build_reading_response(self.read_packet(identifier),
                    excerpts=forecasts.MathForecastTests.build_diagnostics(self, index + 1)))
        self.limit_input(150000)
        from research_harness.review_continuation import plan_next
        with patch.object(v7, 'MATH_V2_FINAL_FORMS', ('request',)):
            result = plan_next(self.store.snapshot()['records'], self.artifacts, head)
        self.assertEqual((result['state'], result['code']), ('stop', reading.PROJECTION_STOP))
        self.assertEqual(result['details']['successor']['protocol'], MATH_V2)
        self.assertEqual([turn['origin']['assignment_id']
                          for chain in result['details']['successor']['carried_readings']['chains']
                          for turn in chain['turns']], list(identifiers))
        with self.assertRaises(ResearchError) as caught:
            self.assign('math-final', native, head)
        self.assertEqual(caught.exception.code, reading.PROJECTION_STOP)
        self.assertEqual(caught.exception.details['successor']['protocol'], MATH_V2)

    def test_exact_stop_suppresses_the_same_route_v1_restart(self):
        native = self.build_covered_packet(9, 'math-1')
        self.limit_input(150000)
        prior = None
        with patch.object(v7, 'MATH_V2_FINAL_FORMS', ('request',)):
            with self.assertRaises(ResearchError) as caught:
                for number in range(1, 5):
                    identifier = 'math-%d' % number
                    self.assign(identifier, native, prior)
                    self.assertEqual(reading.find_protocol(self.read_packet(identifier)), reading.MATH_PROTOCOL)
                    self.invoke(identifier, self.build_reading_response(self.read_packet(identifier),
                        excerpts=forecasts.MathForecastTests.build_diagnostics(self, number)))
                    prior = identifier
        self.assertIsNotNone(prior)
        self.assertEqual(caught.exception.code, reading.PROJECTION_STOP)
        details = caught.exception.details
        self.assertFalse(details.get('estimate', False))
        self.assertEqual(details['successor']['protocol'], reading.MATH_PROTOCOL)
        self.assertEqual(list_answers(details['decision']), [reading.SMALLER_PACKET_ANSWER])

    def test_a25_v1_turn_keeps_the_character_admission_of_its_transport(self):
        native = self.build_math_packet()
        self.run_v1_turns(native, 1)
        records = self.store.snapshot()['records']
        first = records['review_assignment']['math-1']
        measure = capacity.measure_request

        def measure_over_tokens(*args, **kwargs):
            return dict(measure(*args, **kwargs), fits=False, tokens_upper=10 ** 9)
        with patch.object(capacity, 'measure_request', side_effect=measure_over_tokens):
            self.assertIsNone(successor.describe_input_handoff(records, self.artifacts, first))
        self.limit_input(1000)
        handoff = successor.describe_input_handoff(records, self.artifacts, first)
        self.assertEqual(handoff['state'], 'stop')
        self.assertEqual(handoff['details']['successor']['protocol'], reading.MATH_PROTOCOL)
        self.assertEqual(list_answers(handoff['details']['decision']), [reading.SMALLER_PACKET_ANSWER])

    def test_a25_route_assessment_lists_the_math_chain_as_served_once_v2_is_in_the_order(self):
        from research_harness import cli
        arguments = cli.build_parser().parse_args(['review-route-assess', '--workspace', str(self.root),
                                                  '--route-id', 'route'])
        with patch.object(reading, 'NEW_MATH_CHAIN_PROTOCOLS', (reading.MATH_PROTOCOL,)):
            report = cli.run(arguments)
        self.assertIn('math_reading_v1', {row['reason'] for row in report['outside_solved_part']['native_math']})
        report = cli.run(arguments)
        self.assertNotIn('math_reading_v1', {row['reason'] for row in report['outside_solved_part']['native_math']})


class MathEstimateStopTests(forecasts.FinalPreflightCase):
    reading_protocol = None
    historical_v5 = True
    text_sizes = (125000, 125000, 125000)
    input_limit = 260000
    route_output_tokens = 128000
    export_math = forecasts.MathForecastTests.export_math
    build_diagnostics = forecasts.MathForecastTests.build_diagnostics

    def assign_as_under_v1(self, identifier, native, prior):
        """Record a turn whose packet is the one that the released order (math v1) builds, and return it."""
        records = self.store.snapshot()['records']
        value = dict(id=identifier, role='native_math', route_id='route', dossier_id=None,
                     reviewer_id='openness-reviewer', author_id='problem-author',
                     context=dict(native_packet=native, prior_assignment_id=prior))
        logical = protocol.build_packet(records, self.artifacts, None, 'native_math', value['reviewer_id'], **value['context'])
        built = []
        for protocols in ((reading.MATH_PROTOCOL,), (MATH_V2, reading.MATH_PROTOCOL)):
            with patch.object(reading, 'NEW_MATH_CHAIN_PROTOCOLS', protocols):
                built.append(reading.prepare_packet(records, self.artifacts, value, records['review_route']['route'], logical))
        self.assertEqual(built[0], built[1])
        assignment = self.assign(identifier, native, prior)
        packet = json.loads(self.artifacts.read(assignment['packet']))
        self.assertEqual(reading.find_protocol(packet), reading.MATH_PROTOCOL)
        self.assertEqual(assignment['prompt_hash'], protocol.canonical_prompt('native_math',
            reading_stage=reading.find_stage(packet), reading_protocol=reading.MATH_PROTOCOL)['rendered_prompt_hash'])
        return packet

    def test_finding5_estimate_stop_keeps_continue_and_the_continued_chain_ends_under_v1(self):
        self.limit_input(self.input_limit)
        native = self.export_math()
        for number in (1, 2):
            identifier = 'turn-%d' % number
            self.assign(identifier, native, 'turn-1' if number == 2 else None)
            self.invoke(identifier, self.build_reading_response(self.read_packet(identifier),
                        excerpts=self.build_diagnostics(number)))
        stored = {key: digest(row) for key, row in self.store.snapshot()['records']['review_assignment'].items()}
        with self.assertRaises(ResearchError) as caught:
            self.assign('turn-3', native, 'turn-2')
        released = caught.exception.details['decision']
        self.assertEqual(list_answers(released), [reading.CONTINUE_MATH_ANSWER, reading.WAIT_MATH_ANSWER,
                                                  reading.SMALLER_PACKET_ANSWER])
        with patch.object(reading, 'NEW_MATH_CHAIN_PROTOCOLS', (MATH_V2, reading.MATH_PROTOCOL)):
            with self.assertRaises(ResearchError) as caught:
                self.assign('turn-3', native, 'turn-2')
            self.assertTrue(caught.exception.details['estimate'])
            decision = caught.exception.details['decision']
            self.assertEqual(decision['owner_decides'], successor.MATH_ESTIMATE_DECISION)
            self.assertEqual(list_answers(decision), [reading.CONTINUE_MATH_ANSWER, successor.MATH_SUCCESSOR_ANSWER,
                                                      reading.SMALLER_PACKET_ANSWER])
            self.assertEqual(decision['then'][0], released['then'][0])
            export = decision['then'][1]['steps'][0]
            self.assertEqual((export['id'], export['then'][0]['check']), (5, True))
            [step] = decision['then'][0]['steps']
            self.assertEqual((step['id'], step['payload']['assignment_id']), (9, 'turn-2'))
            evidence = self.artifacts.put(b'Continue the measured chain.', 'text/plain')
            self.mutate(protocol.record_context_event, dict(step['payload'], id='continue-chain', evidence=[evidence]))
            current = 'turn-2'
            for number in range(3, 15):
                identifier = 'turn-%d' % number
                packet = self.assign_as_under_v1(identifier, native, current)
                current = identifier
                if reading.find_stage(packet) == 'final':
                    self.invoke(identifier, self.build_native_result(packet))
                    break
                self.invoke(identifier, self.build_reading_response(packet, excerpts=[]))
            else:
                self.fail('The continued v1 chain did not reach its final.')
            records = self.store.snapshot()['records']
            self.assertTrue(reading.verify_chain(records, self.artifacts, records['review_assignment'][current]))
        self.assertEqual({key: digest(records['review_assignment'][key]) for key in stored}, stored)


class MathV1CallPreparationTests(math_v1_fixtures.MathCarriedCase):
    text_sizes = (135000,)

    def test_healthy_v1_preview_and_dispatch_skip_unused_minimum_validation(self):
        from continuity_forms_fixtures import prepare_unit_fixture
        from test_chain_stop_math_successor import MathSuccessorSubjectTests

        fixture = prepare_unit_fixture(self, MathSuccessorSubjectTests, 'runTest')
        controller, claim = fixture.start_controller(self)
        minimum = fixture.build_minimum(self, 'read-1')
        exported = controller.command('review-packet', dict(kind='result', subject=dict(claim=claim),
            claim_digest=digest(claim), inputs=[], reviewer=fixture.build_reviewer('first'),
            openness=minimum), None, None)
        with patch.object(openness_minimum, 'validate_minimum_bindings',
                          wraps=openness_minimum.validate_minimum_bindings) as validated, \
                patch.object(reading, 'list_reading_rows', wraps=reading.list_reading_rows) as read_rows:
            with patch.object(reading, 'NEW_MATH_CHAIN_PROTOCOLS', (reading.MATH_PROTOCOL,)):
                assignment = self.assign('first', exported['native_packet'])
            self.assertEqual(assignment['reading_chain_admission']['state'], 'stop')
            self.assertEqual(validated.call_count, 1)
            packet = self.read_packet('first')
            self.assertEqual(reading.find_protocol(packet), reading.MATH_PROTOCOL)

            planning_counts = []
            for extra_searches in (0, 32):
                for index in range(extra_searches):
                    minimum_fixtures.ProblemOpennessMinimumTests.search(
                        self, self.versions, formulation='Independent question ' + str(index))
                validated.reset_mock()
                read_rows.reset_mock()
                plan = plan_next(self.store.snapshot()['records'], self.artifacts, 'first')
                self.assertEqual((plan['state'], plan['command']), ('run', 'review-run'))
                planning_counts.append((validated.call_count, read_rows.call_count))

            validated.reset_mock()
            with patch.object(protocol.transport, 'CODEX_INPUT_LIMIT_CHARS', 1000), \
                    patch.object(protocol.transport, 'send_request') as send:
                blocked = plan_next(self.store.snapshot()['records'], self.artifacts, 'first')
                self.assertEqual((blocked['state'], blocked['code']), ('stop', reading.PROJECTION_STOP))
                with self.assertRaises(ResearchError) as stopped:
                    protocol.invoke_assignment(self.store, plan['payload'], expected_revision=self.store.revision,
                                               request_id='stopped-v1-call', credential='test-only')
                send.assert_not_called()
            self.assertEqual(stopped.exception.code, reading.PROJECTION_STOP)
            self.assertFalse(stopped.exception.details.get('estimate'))
            self.assertGreater(validated.call_count, 0)
            smaller = next(answer for answer in stopped.exception.details['decision']['then']
                           if answer['answer'] == reading.SMALLER_PACKET_ANSWER)
            [step] = smaller['steps']
            self.assertIn('openness', step['chooses'])
            self.assertNotIn('openness', step['payload'])

            validated.reset_mock()
            read_rows.reset_mock()
            self.invoke('first', self.build_reading_response(packet), plan['payload']['id'])
            # The plan's call preview runs the same v1 limit checks as dispatch, without the unused admission answer.
            self.assertEqual((planning_counts, validated.call_count, read_rows.call_count),
                             ([(0, 2), (0, 2)], 0, 2))


class MathRecoveryRowsTests(unittest.TestCase):
    def test_a25_math_v2_rows_take_the_v7_handlers_and_v1_rows_name_no_later_unit(self):
        rows = capacity.list_capacity_recovery_rows()
        for kind, handler in [('reading', 'reading_v7'), ('final', 'final_v7'), ('combine', 'final_v7')]:
            [row] = [row for row in rows if row['protocol'] == MATH_V2 and row['kind'] == kind]
            self.assertEqual(row['handler'], handler)
        for row in rows:
            if row['protocol'] == reading.MATH_PROTOCOL:
                self.assertEqual(row['handler'], 'nothing')
                self.assertNotIn('required_unit', row)


if __name__ == '__main__':
    unittest.main()
