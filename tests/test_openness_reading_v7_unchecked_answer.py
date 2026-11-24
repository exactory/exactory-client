"""No later step builds on an answer that fails its output check, also without its refusal record.

The run of a turn records a refusal for an answer that fails its check. In these tests that write does not commit
(the run command ends before it), so the store holds a completed, authenticated answer that fails its check and no
refusal record. The answers belong to a v7 chain, to a v6 final and to a single review under the finding contract.
Synthetic scientific content."""

import copy
import json
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from openness_reading_fixtures import OpennessReadingCase
from research_harness import openness_reading as reading, openness_reading_v7 as v7
from research_harness import problem_openness, review_call_capture, review_continuation
from research_harness import review_protocol as protocol, review_refusal
from research_harness.errors import ResearchError
from research_harness.problem_openness import assess_openness
from research_harness.review_continuation import plan_next
from research_harness.review_task_execution import identify_call
import test_openness_finding_answer as finding_fixtures
import test_openness_reading_v7_refusal as refusal_fixtures
import test_openness_reading_v7_turns as turn_fixtures


@contextmanager
def lose_refusal_record():
    """End the run before it records the refusal of its answer."""
    with patch.object(review_refusal, 'record_native_refusal', lambda *args, **kwargs: None):
        yield


def assert_unchecked(case, error, code, message, assignment_id, attempt_id, refusal_code='invalid_reading_response',
                     check='native.output'):
    """Compare the code before the message, so that a tree without the rule fails on the cause of its refusal. message
    names the module and the constant of the text."""
    case.assertEqual(error.code, code, error.message)
    module, name = message
    case.assertEqual(error.message, getattr(module, name))
    case.assertEqual((error.details['assignment_id'], error.details['attempt_id']), (assignment_id, attempt_id))
    case.assertEqual((error.details['refusal']['code'], error.details['refusal']['check']), (refusal_code, check))


def run_recorded_request(case, payload, request_id):
    """Run a recorded review-run request again; a reviewer call fails the test."""
    with patch.object(protocol.transport, 'send_request', side_effect=AssertionError('No reviewer call.')):
        return protocol.invoke_assignment(case.store, payload, credential='test-only',
                                          expected_revision=case.store.revision, request_id=request_id)


def list_targets(packet):
    binding = packet['reading_turn']
    rows = binding.get('formulations')
    if isinstance(rows, list):
        return sorted(row['handle'] for row in rows)
    count = rows.get('count') if isinstance(rows, dict) else None
    return ['t%d' % number for number in range(1, count + 1)] if count else sorted(binding['page']['targets'])


def build_refused_reading_answers(packet, valid):
    """Reading answers that fail the output check of the turn, each in one way."""
    unit = packet['reading_turn']['plan']['now'][0]['handle']
    return dict(
        number_notes=dict(stage='reading', notes=42),
        extra_member=dict(valid, comment='One member more.'),
        long_notes=dict(valid, notes='n' * (packet['reading_turn']['allowances']['notes'] + 1)),
        excerpt_not_in_the_text=dict(valid, excerpts=[dict(unit=unit, text='This sentence is not in the unit.',
                                                           purpose='A quote.', targets=list_targets(packet)[:1])]),
        gap_of_an_unknown_formulation=dict(valid, gaps=[dict(formulation='t999', text='A gap.')]),
        wrong_reviewer=dict(valid, reviewer='another-reviewer'),
        eleven_requests=dict(valid, passage_requests=[dict(id='r%d' % number, unit=unit, anchor=None, characters=10)
                                                       for number in range(11)]),
        no_stage={key: value for key, value in valid.items() if key != 'stage'},
        part_of_an_argument=dict(stage='material', title='An argument', part=1, text='Part one.', more=False))


def build_refused_findings(valid):
    """Final answers that fail the output check of the final turn, each in one way."""
    unknown_outcome, open_with_a_gap, unknown_handle = (copy.deepcopy(valid) for _ in range(3))
    unknown_outcome['findings']['openness']['outcome'] = 'maybe'
    open_with_a_gap['findings']['openness']['outcome'] = 'supported_open'
    unknown_handle['findings']['openness']['grounds'][0]['refs'] = [dict(ref='m999999')]
    return dict(number_findings=dict(valid, findings=42), extra_member=dict(valid, comment='One member more.'),
                unknown_outcome=unknown_outcome, open_with_a_gap=open_with_a_gap, unknown_handle=unknown_handle,
                part_out_of_order=dict(stage='material', title='An argument', part=2, text='Part two.', more=False))


REFUSED_READING_ANSWER_NAMES = ('number_notes', 'extra_member', 'long_notes', 'excerpt_not_in_the_text',
                                'gap_of_an_unknown_formulation', 'wrong_reviewer', 'eleven_requests', 'no_stage',
                                'part_of_an_argument')
REFUSED_FINDING_NAMES = ('number_findings', 'extra_member', 'unknown_outcome', 'open_with_a_gap', 'unknown_handle',
                         'part_out_of_order')
# The finding contract admits supported_open only without grounds (rule 2 of native_review.FINDING_RULES).
OWN_ARGUMENT_GROUND = dict(kind='own_argument', text='An argument of the reviewer.')


class UncheckedAnswerTests(OpennessReadingCase):
    capacity_probe_tokens = 487900
    reading_protocol = None
    route_model = 'gpt-6-astra'
    route_output_tokens = 128000
    text_sizes = (50000, 50000, 50000)
    build_v7_answer = turn_fixtures.V7CommandTests.build_v7_answer
    build_v7_finding = turn_fixtures.V7CommandTests.build_v7_finding
    finish_reading = turn_fixtures.V7CommandTests.finish_reading
    replace_head = refusal_fixtures.V7RefusalTests.replace_head
    finish_from = refusal_fixtures.V7RefusalTests.finish_from

    def setUp(self):
        super().setUp()
        enabled = patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6))
        enabled.start()
        self.addCleanup(enabled.stop)

    def refuse_turn_1(self, case='number_notes'):
        native = self.export()
        self.assign('turn-1', native)
        packet = self.read_packet('turn-1')
        answer = build_refused_reading_answers(packet, self.build_v7_answer(packet))[case]
        with lose_refusal_record():
            receipt = self.invoke('turn-1', answer)
        records = self.store.snapshot()['records']
        self.assertNotIn('output_refusal', receipt)
        self.assertEqual(records.get('review_output_refusal', {}), {})
        self.assertTrue(protocol.assignment_state(records, self.artifacts, 'turn-1')['ready'])
        return native

    def test_next_turn_refuses_an_answer_that_failed_its_check(self):
        """The case of the record: the notes of the refused answer are a number."""
        native = self.refuse_turn_1()
        with self.assertRaises(ResearchError) as raised:
            self.assign('turn-2', native, prior='turn-1')
        assert_unchecked(self, raised.exception, 'review_source_continuation_unverified',
                         (v7, 'UNCHECKED_HEAD_MESSAGE'), 'turn-1', 'attempt-turn-1')
        self.assertNotIn('turn-2', self.store.snapshot()['records']['review_assignment'])

    def test_every_refused_reading_answer_is_refused_at_each_reader(self):
        for number, name in enumerate(REFUSED_READING_ANSWER_NAMES):
            with self.subTest(name):
                if number:
                    self.doCleanups()
                    self.setUp()
                native = self.refuse_turn_1(name)
                records = self.store.snapshot()['records']
                head = records['review_assignment']['turn-1']
                failure = review_refusal.classify_observed_output(records, self.artifacts, 'attempt-turn-1')
                self.assertIsNotNone(failure)
                with self.assertRaises(ResearchError) as raised:
                    self.assign('turn-2', native, prior='turn-1')
                assert_unchecked(self, raised.exception, 'review_source_continuation_unverified',
                                 (v7, 'UNCHECKED_HEAD_MESSAGE'), 'turn-1', 'attempt-turn-1',
                                 failure['code'], failure['check'])
                with self.assertRaises(ResearchError) as raised:
                    v7.verify_problem_coverage(records, self.artifacts, head)
                assert_unchecked(self, raised.exception, 'openness_reading_incomplete',
                                 (v7, 'ANSWER_VERIFICATION_MESSAGE'), 'turn-1', 'attempt-turn-1',
                                 failure['code'], failure['check'])
                with self.assertRaises(ResearchError) as raised:
                    v7.observe_v7_chain(records, self.artifacts, head)
                self.assertEqual(raised.exception.code, 'openness_reading_incomplete')
                plan = plan_next(records, self.artifacts, 'turn-1')
                self.assertEqual((plan['state'], plan.get('command'), plan.get('request_id')),
                                 ('resume', 'review-run', 'invoke-attempt-turn-1'), plan)

    def test_run_writes_the_answer_only_after_its_invocation_and_capture(self):
        """The resume step reads the invocation and the capture of the selected answer directly. A run that writes an
        answer wrote both records before it, and a run whose capture did not commit writes no answer."""
        self.refuse_turn_1('extra_member')
        records = self.store.snapshot()['records']
        capture = records['review_call_observation'][identify_call('review.invoke', 'attempt-turn-1')]
        self.assertEqual(capture['request_id'], 'invoke-attempt-turn-1')
        self.assertLess(records['review_invocation']['attempt-turn-1']['recorded_revision'],
                        capture['recorded_revision'])
        self.assertLess(capture['recorded_revision'], records['review_attempt']['attempt-turn-1']['recorded_revision'])
        self.assign('later', self.export(name='later-export', assignment_id='later'))
        busy = ResearchError('store_busy', 'The capture did not commit.')
        with patch.object(review_call_capture, 'capture_call', side_effect=busy), lose_refusal_record():
            with self.assertRaises(ResearchError) as raised:
                self.invoke('later', dict(stage='reading', notes=42))
        self.assertEqual(raised.exception.code, 'store_busy')
        records = self.store.snapshot()['records']
        self.assertIn('attempt-later', records['review_invocation'])
        self.assertNotIn(identify_call('review.invoke', 'attempt-later'), records['review_call_observation'])
        self.assertNotIn('attempt-later', records['review_attempt'])

    def test_later_chain_carries_no_answer_that_failed_its_check(self):
        self.refuse_turn_1('extra_member')
        later = self.export(name='later-export', assignment_id='later')
        receipt = self.assign('later', later)
        self.assertEqual(receipt['protocol_selection']['protocol'], v7.PROTOCOL)
        selection = json.loads(self.artifacts.read(receipt['carried_selection']['artifact']))
        self.assertEqual([turn['origin']['assignment_id'] for turn in selection['turns']], [])

    def test_later_chain_carries_the_turns_before_such_an_answer_and_verifies(self):
        native = self.export()
        self.assign('turn-1', native)
        self.invoke('turn-1', self.build_v7_answer(self.read_packet('turn-1')))
        self.assign('turn-2', native, prior='turn-1')
        packet = self.read_packet('turn-2')
        self.assertEqual(reading.find_stage(packet), 'reading')
        with lose_refusal_record():
            self.invoke('turn-2', dict(self.build_v7_answer(packet), comment='One member more.'))
        later = self.export(name='later-export', assignment_id='later')
        receipt = self.assign('later', later)
        selection = json.loads(self.artifacts.read(receipt['carried_selection']['artifact']))
        self.assertEqual([turn['origin']['assignment_id'] for turn in selection['turns']], ['turn-1'])
        # The later chain, recorded under the rule, reaches its finding and verifies with the carried turn.
        turns = self.finish_from(later, 'later')
        self.assertEqual([row['origin']['assignment_id'] for row in turns if row['origin'] is not None], ['turn-1'])
        records = self.store.snapshot()['records']
        head = next(iter(records['problem_openness']))
        self.assertEqual(reading.verify_chain(records, self.artifacts, records['review_assignment'][head]), turns)
        self.assertEqual([row['code'] for row in assess_openness(records, self.artifacts)],
                         ['problem_openness_unresolved'])

    def test_plan_runs_the_recorded_request_again_and_then_replaces_the_turn(self):
        native = self.refuse_turn_1()
        records = self.store.snapshot()['records']
        plan = plan_next(records, self.artifacts, 'turn-1')
        self.assertEqual((plan['state'], plan.get('command')), ('resume', 'review-run'), plan)
        self.assertEqual(plan['request_id'], 'invoke-attempt-turn-1')
        self.assertEqual(plan['payload'], dict(id='attempt-turn-1', assignment_id='turn-1'))
        self.assertEqual(plan['details']['message'], review_continuation.UNRECORDED_REFUSAL_MESSAGE)
        self.assertEqual((plan['details']['refusal']['code'], plan['details']['refusal']['check']),
                         ('invalid_reading_response', 'native.output'))
        receipt = run_recorded_request(self, plan['payload'], plan['request_id'])
        self.assertEqual(receipt['output_refusal']['code'], 'invalid_reading_response')
        records = self.store.snapshot()['records']
        self.assertEqual(sorted(records['review_output_refusal']), ['attempt-turn-1'])
        self.assertEqual(sorted(records['review_attempt']), ['attempt-turn-1'])
        replacement = self.replace_head('turn-1')
        notice = self.read_packet(replacement['id'])['output_refusal']
        self.assertEqual([row['attempt_id'] for row in notice['refused']], ['attempt-turn-1'])
        self.finish_from(native, replacement['id'])

    def test_series_records_the_refusal_and_reaches_the_finding(self):
        """The driver on a chain whose first answer fails its check and loses its refusal record."""
        from research_harness.review_series import run_series
        from codex_route_fixtures import build_codex_response
        self.assign('turn-1', self.export())
        calls = []

        def respond(route, request, credential):
            packet = reading.review_excerpts.read_sent_packet(request)
            calls.append(packet.get('reading_turn', packet.get('reading_final'))['turn'])
            if 'reading_turn' in packet:
                output = (self.build_v7_answer(packet) if packet.get('output_refusal')
                          or packet['reading_turn']['turn'] > 1 else dict(stage='reading', notes=42))
            else:
                output = self.build_v7_finding(packet, packet['reading_final']['notes_handle'])
            return build_codex_response(route, request, output)
        record = review_refusal.record_native_refusal
        lost = []

        def lose_first(*args, **kwargs):
            if not lost:
                lost.append(kwargs['request_id'])
                return None
            return record(*args, **kwargs)
        rows = []
        with patch.object(protocol.transport, 'send_request', side_effect=respond), \
                patch.object(review_refusal, 'record_native_refusal', lose_first):
            self.assertEqual(run_series(self.store, 'turn-1', emit=rows.append), 0, rows[-1] if rows else None)
        self.assertEqual(rows[-1]['state'], 'done')
        states = [(row['state'], row.get('command')) for row in rows]
        first = states.index(('resume', 'review-run'))
        self.assertEqual(states[first + 1], ('replace', 'review-assignment'), states)
        self.assertEqual(rows[first]['request_id'], lost[0])
        records = self.store.snapshot()['records']
        self.assertEqual(calls.count(1), 2)
        self.assertEqual(len(records['review_output_refusal']), 1)
        self.assertTrue(records.get('problem_openness'))

    def test_final_answer_that_failed_its_check_gives_no_finding(self):
        for number, name in enumerate(REFUSED_FINDING_NAMES):
            with self.subTest(name):
                if number:
                    self.doCleanups()
                    self.setUp()
                native = self.export()
                head = self.finish_reading(native)
                packet = self.read_packet(head)
                answer = build_refused_findings(self.build_v7_finding(
                    packet, packet['reading_final']['notes_handle']))[name]
                with lose_refusal_record():
                    self.invoke(head, answer)
                records = self.store.snapshot()['records']
                attempt = 'attempt-' + head
                failure = review_refusal.classify_observed_output(records, self.artifacts, attempt)
                self.assertIsNotNone(failure)
                self.assertEqual(records.get('review_output_refusal', {}), {})
                with self.assertRaises(ResearchError) as raised:
                    self.mutate(self.load_problem_module().record_openness, dict(assignment_id=head))
                assert_unchecked(self, raised.exception, 'review_context_unverified',
                                 (problem_openness, 'UNCHECKED_FINDING_MESSAGE'), head, attempt,
                                 failure['code'], failure['check'])
                with self.assertRaises(ResearchError) as raised:
                    reading.verify_chain(records, self.artifacts, records['review_assignment'][head])
                assert_unchecked(self, raised.exception, 'openness_reading_incomplete',
                                 (v7, 'ANSWER_VERIFICATION_MESSAGE'), head, attempt, failure['code'], failure['check'])
                records = self.store.snapshot()['records']
                self.assertEqual(records.get('problem_openness', {}), {})
                self.assertEqual([row['code'] for row in assess_openness(records, self.artifacts)],
                                 ['problem_openness_missing'])
                plan = plan_next(records, self.artifacts, head)
                self.assertEqual((plan['state'], plan.get('command'), plan.get('request_id')),
                                 ('resume', 'review-run', 'invoke-' + attempt), plan)

    def test_finding_recorded_from_such_a_final_becomes_stale(self):
        """A finding as an earlier release recorded it: supported_open with a ground, which the contract refuses."""
        head = self.finish_reading(self.export())
        packet = self.read_packet(head)
        answer = build_refused_findings(self.build_v7_finding(
            packet, packet['reading_final']['notes_handle']))['open_with_a_gap']
        with lose_refusal_record():
            self.invoke(head, answer)
        with patch.object(review_refusal, 'classify_observed_output', return_value=None):
            finding = self.mutate(self.load_problem_module().record_openness, dict(assignment_id=head))['result']
            self.assertEqual((finding['outcome'], len(finding['grounds'])), ('supported_open', 1))
            self.assertEqual(assess_openness(self.store.snapshot()['records'], self.artifacts), [])
        stale = assess_openness(self.store.snapshot()['records'], self.artifacts)
        self.assertEqual([(row['code'], row.get('cause')) for row in stale],
                         [('problem_openness_stale', 'openness_reading_incomplete')])

    def test_chain_that_went_on_over_such_an_answer_gives_no_finding(self):
        """A chain as an earlier release recorded it: the next turn was built on the answer of turn 1."""
        with turn_fixtures.use_earlier_v7_frames():
            native = self.refuse_turn_1('extra_member')
        with patch.object(review_refusal, 'classify_observed_output', return_value=None):
            self.assign('turn-2', native, prior='turn-1')
            self.invoke('turn-2', self.build_v7_answer(self.read_packet('turn-2')))
            self.assign('turn-3', native, prior='turn-2')
            packet = self.read_packet('turn-3')
            self.assertEqual(reading.find_stage(packet), 'final')
            self.invoke('turn-3', self.build_v7_finding(packet, packet['reading_final']['notes_handle']))
            self.mutate(self.load_problem_module().record_openness, dict(assignment_id='turn-3'))
            records = self.store.snapshot()['records']
            self.assertEqual(assess_openness(records, self.artifacts)[0]['code'], 'problem_openness_unresolved')
        records = self.store.snapshot()['records']
        with self.assertRaises(ResearchError) as raised:
            reading.verify_chain(records, self.artifacts, records['review_assignment']['turn-3'])
        assert_unchecked(self, raised.exception, 'openness_reading_incomplete', (v7, 'ANSWER_VERIFICATION_MESSAGE'),
                         'turn-1', 'attempt-turn-1')
        stale = assess_openness(records, self.artifacts)
        self.assertEqual([(row['code'], row.get('cause')) for row in stale],
                         [('problem_openness_stale', 'openness_reading_incomplete')])
        later = self.export(name='later-export', assignment_id='later')
        receipt = self.assign('later', later)
        selection = json.loads(self.artifacts.read(receipt['carried_selection']['artifact']))
        self.assertEqual([turn['origin']['assignment_id'] for turn in selection['turns']], [])

    def test_later_chain_that_carried_such_an_answer_gives_no_finding(self):
        """A later chain as an earlier release recorded it: its selection carries the answer of turn 1."""
        self.refuse_turn_1('extra_member')
        with patch.object(review_refusal, 'classify_observed_output', return_value=None):
            later = self.export(name='later-export', assignment_id='later')
            receipt = self.assign('later', later)
            selection = json.loads(self.artifacts.read(receipt['carried_selection']['artifact']))
            self.assertEqual([turn['origin']['assignment_id'] for turn in selection['turns']], ['turn-1'])
            self.finish_from(later, 'later')
            records = self.store.snapshot()['records']
            self.assertEqual(assess_openness(records, self.artifacts)[0]['code'], 'problem_openness_unresolved')
        records = self.store.snapshot()['records']
        head = next(iter(records['problem_openness']))
        with self.assertRaises(ResearchError) as raised:
            reading.verify_chain(records, self.artifacts, records['review_assignment'][head])
        self.assertEqual(raised.exception.code, 'openness_reading_incomplete')
        self.assertEqual(raised.exception.message, v7.SELECTION_MISMATCH_MESSAGE)
        self.assertEqual([(row['code'], row.get('cause')) for row in assess_openness(records, self.artifacts)],
                         [('problem_openness_stale', 'openness_reading_incomplete')])

    def test_next_turn_names_an_earlier_answer_that_failed_its_check(self):
        native = self.refuse_turn_1('extra_member')
        with patch.object(review_refusal, 'classify_observed_output', return_value=None):
            self.assign('turn-2', native, prior='turn-1')
        packet = self.read_packet('turn-2')
        self.assertEqual(reading.find_stage(packet), 'reading', 'The fixture must give two reading turns.')
        self.invoke('turn-2', self.build_v7_answer(packet))
        with self.assertRaises(ResearchError) as raised:
            self.assign('turn-3', native, prior='turn-2')
        assert_unchecked(self, raised.exception, 'review_source_continuation_unverified',
                         (v7, 'UNCHECKED_EARLIER_MESSAGE'), 'turn-1', 'attempt-turn-1')

    def test_part_of_an_argument_at_a_final_turn_is_no_finding(self):
        """A valid part of an argument: problem-openness refuses it as it refuses a reading response."""
        head = self.finish_reading(self.export())
        self.invoke(head, dict(stage='material', title='An argument', part=1, text='Part one.', more=True))
        with self.assertRaises(ResearchError) as raised:
            self.mutate(self.load_problem_module().record_openness, dict(assignment_id=head))
        self.assertEqual(raised.exception.code, 'openness_reading_incomplete')
        plan = plan_next(self.store.snapshot()['records'], self.artifacts, head)
        self.assertEqual((plan['state'], plan.get('command')), ('continue', 'review-assignment'))

    def test_checked_answers_keep_their_chain(self):
        native = self.export()
        self.assign('turn-1', native)
        turns = self.finish_from(native, 'turn-1')
        records = self.store.snapshot()['records']
        self.assertEqual(records.get('review_output_refusal', {}), {})
        self.assertGreater(len(turns), 1)
        head = next(iter(records['problem_openness']))
        plan = plan_next(records, self.artifacts, head)
        self.assertEqual(plan['state'], 'done')


class UncheckedV6FinalTests(OpennessReadingCase):
    """A final under the finding contract in a v6 chain: the recording reads its finding after the check."""
    capacity_probe_tokens = 487900
    reading_protocol = None
    route_model = 'gpt-6-astra'
    route_output_tokens = 128000
    text_sizes = (50000, 50000, 50000)

    def setUp(self):
        super().setUp()
        self.limit_input(1300000)

    def finish_reading(self, native):
        self.assign('turn-1', native)
        head = 'turn-1'
        for number in range(2, 100):
            packet = self.read_packet(head)
            if reading.find_stage(packet) == 'final':
                return head
            self.invoke(head, self.build_reading_response(packet, excerpts=[]))
            self.assign('turn-%d' % number, native, prior=head)
            head = 'turn-%d' % number
        self.fail('The chain must reach its final turn.')

    def test_v6_final_answer_that_failed_its_check_gives_no_finding(self):
        head = self.finish_reading(self.export())
        packet = self.read_packet(head)
        self.assertEqual(reading.find_protocol(packet), reading.PROTOCOL_V6)
        with lose_refusal_record():
            self.invoke(head, dict(self.build_native_result(packet, outcome='unresolved'), findings=42))
        records = self.store.snapshot()['records']
        self.assertEqual(records.get('review_output_refusal', {}), {})
        failure = review_refusal.classify_observed_output(records, self.artifacts, 'attempt-' + head)
        self.assertEqual(failure['code'], 'openness_finding_invalid')
        plan = plan_next(records, self.artifacts, head)
        self.assertEqual((plan['state'], plan.get('command'), plan.get('request_id')),
                         ('resume', 'review-run', 'invoke-attempt-' + head), plan)
        with self.assertRaises(ResearchError) as raised:
            self.mutate(self.load_problem_module().record_openness, dict(assignment_id=head))
        assert_unchecked(self, raised.exception, 'review_context_unverified',
                         (problem_openness, 'UNCHECKED_FINDING_MESSAGE'), head, 'attempt-' + head,
                         failure['code'], failure['check'])
        receipt = run_recorded_request(self, plan['payload'], plan['request_id'])
        self.assertEqual(receipt['output_refusal']['code'], 'openness_finding_invalid')
        plan = plan_next(self.store.snapshot()['records'], self.artifacts, head)
        self.assertEqual((plan['state'], plan.get('command')), ('run', 'review-run'), plan)


class UncheckedSingleReviewTests(OpennessReadingCase):
    """A single review under the finding contract (exactory-review-v7/native_math), recorded since 0.90.0."""
    reading_protocol = None
    export_finding_packet = finding_fixtures.FindingRecordingTests.export_finding_packet
    record_answer = finding_fixtures.FindingRecordingTests.record_answer

    def refuse_review(self):
        """Assign one review and give it supported_open with a ground, whose refusal record is lost."""
        self.assign('turn-1', self.export_finding_packet())
        records = self.store.snapshot()['records']
        self.assertEqual(records['review_assignment']['turn-1']['prompt']['template_id'],
                         'exactory-review-v7/native_math')
        packet = self.read_packet('turn-1')
        self.assertIsNone(reading.find_stage(packet))
        with lose_refusal_record():
            self.invoke('turn-1', finding_fixtures.build_answer(packet, 'supported_open', [OWN_ARGUMENT_GROUND]))
        records = self.store.snapshot()['records']
        self.assertEqual(records.get('review_output_refusal', {}), {})
        self.assertTrue(protocol.assignment_state(records, self.artifacts, 'turn-1')['ready'])
        return packet

    def assert_refused_review_plan(self):
        """The plan of a single review whose only answer is refused with its refusal record: the handoff to a new
        export, with the code review_slot_exhausted."""
        plan = plan_next(self.store.snapshot()['records'], self.artifacts, 'turn-1')
        self.assertEqual((plan['state'], plan.get('command'), plan.get('code')),
                         ('handoff', 'export', 'review_slot_exhausted'), plan)
        self.assertEqual(plan['details']['refused_attempts'],
                         [dict(attempt_id='attempt-turn-1', code='openness_finding_invalid')])

    def test_single_review_answer_that_failed_its_check_gives_no_finding(self):
        self.refuse_review()
        records = self.store.snapshot()['records']
        failure = review_refusal.classify_observed_output(records, self.artifacts, 'attempt-turn-1')
        self.assertEqual((failure['code'], failure['details']['rule']), ('openness_finding_invalid', 2))
        plan = plan_next(records, self.artifacts, 'turn-1')
        self.assertEqual((plan['state'], plan.get('command'), plan.get('request_id')),
                         ('resume', 'review-run', 'invoke-attempt-turn-1'), plan)
        self.assertEqual(plan['details']['refusal'], failure)
        with self.assertRaises(ResearchError) as raised:
            self.record_answer('turn-1')
        assert_unchecked(self, raised.exception, 'review_context_unverified',
                         (problem_openness, 'UNCHECKED_FINDING_MESSAGE'), 'turn-1', 'attempt-turn-1',
                         failure['code'], failure['check'])
        records = self.store.snapshot()['records']
        self.assertEqual(records.get('problem_openness', {}), {})
        self.assertEqual([row['code'] for row in assess_openness(records, self.artifacts)],
                         ['problem_openness_missing'])
        receipt = run_recorded_request(self, plan['payload'], plan['request_id'])
        self.assertEqual(receipt['output_refusal']['code'], 'openness_finding_invalid')
        self.assertEqual(sorted(self.store.snapshot()['records']['review_output_refusal']), ['attempt-turn-1'])
        self.assert_refused_review_plan()

    def test_single_review_answer_with_its_refusal_record_keeps_its_plan(self):
        """The control: the run records the refusal, and the plan is the plan that the resume step leads to."""
        self.assign('turn-1', self.export_finding_packet())
        packet = self.read_packet('turn-1')
        receipt = self.invoke('turn-1', finding_fixtures.build_answer(packet, 'supported_open', [OWN_ARGUMENT_GROUND]))
        self.assertEqual(receipt['output_refusal']['code'], 'openness_finding_invalid')
        self.assert_refused_review_plan()
        with self.assertRaises(ResearchError) as raised:
            self.record_answer('turn-1')
        self.assertEqual(raised.exception.code, 'review_context_unverified')
        self.assertNotIn('refusal', raised.exception.details or {})

    def test_single_review_finding_recorded_from_such_an_answer_becomes_stale(self):
        """A finding as 0.90.0 to 0.91.10 recorded it, read by the gate before and after its refusal is recorded."""
        packet = self.refuse_review()
        with patch.object(review_refusal, 'classify_observed_output', return_value=None):
            finding = self.record_answer('turn-1')
            self.assertEqual((finding['outcome'], len(finding['grounds'])), ('supported_open', 1))
            self.assertNotIn('reading_turns', finding)
            self.assertEqual(assess_openness(self.store.snapshot()['records'], self.artifacts), [])
        stale = [('problem_openness_stale', 'openness_native_review_unverified')]
        records = self.store.snapshot()['records']
        self.assertEqual([(row['code'], row.get('cause')) for row in assess_openness(records, self.artifacts)], stale)
        # The recorded request records the refusal; the finding stays stale.
        receipt = run_recorded_request(self, dict(id='attempt-turn-1', assignment_id='turn-1'), 'invoke-attempt-turn-1')
        self.assertEqual(receipt['output_refusal']['code'], 'openness_finding_invalid')
        records = self.store.snapshot()['records']
        self.assertEqual([(row['code'], row.get('cause')) for row in assess_openness(records, self.artifacts)], stale)
        # A second answer that passes becomes the selected answer; the finding still rests on the refused one.
        self.invoke('turn-1', finding_fixtures.build_answer(packet, 'supported_open'), attempt='second-answer')
        records = self.store.snapshot()['records']
        self.assertEqual(protocol.assignment_state(records, self.artifacts, 'turn-1')['attempt_id'], 'second-answer')
        self.assertEqual([(row['code'], row.get('cause')) for row in assess_openness(records, self.artifacts)], stale)

    def test_single_review_finding_whose_answer_the_store_cannot_give_is_stale(self):
        """The gate and status report the finding stale when the store refuses a read of the check, and when the
        output artifact of the recorded answer is missing."""
        self.assign('turn-1', self.export_finding_packet())
        self.invoke('turn-1', finding_fixtures.build_answer(self.read_packet('turn-1'), 'supported_open'))
        self.record_answer('turn-1')
        refused = ResearchError('storage_io', 'The store could not read an artifact.')
        with patch.object(review_refusal, 'classify_observed_output', side_effect=refused):
            self.assert_openness_obligations([('problem_openness_stale', 'storage_io')])
        # The state of the review then holds no selected answer, so the recorded answer is not the selected one.
        output = self.store.snapshot()['records']['review_attempt']['attempt-turn-1']['output']
        (self.root / 'research/sources/objects' / output['sha256']).unlink()
        self.assert_openness_obligations([('problem_openness_stale', 'openness_native_review_unverified')])

    def assert_openness_obligations(self, expected):
        """The gate and the obligations of status, which run the gate without raising."""
        from research_harness import cli
        records = self.store.snapshot()['records']
        self.assertEqual([(row['code'], row.get('cause')) for row in assess_openness(records, self.artifacts)],
                         expected)
        report = cli.run(cli.build_parser().parse_args(['status', '--workspace', str(self.root)]))
        self.assertEqual([(row['code'], row.get('cause')) for row in report['obligations']
                          if row['code'].startswith('problem_openness')], expected)

    def test_single_review_finding_with_a_passing_answer_keeps_no_obligation(self):
        self.assign('turn-1', self.export_finding_packet())
        self.invoke('turn-1', finding_fixtures.build_answer(self.read_packet('turn-1'), 'supported_open'))
        finding = self.record_answer('turn-1')
        self.assertEqual((finding['outcome'], finding['grounds']), ('supported_open', []))
        self.assertEqual(assess_openness(self.store.snapshot()['records'], self.artifacts), [])


if __name__ == '__main__':
    unittest.main()
