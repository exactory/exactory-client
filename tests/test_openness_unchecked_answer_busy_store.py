"""An answer that fails its output check while the store refuses its refusal record: the joint behaviour of two lanes.

Lane finishing-writes-2: the refusal record of a run waits for a busy store; after the wait the run is refused with
store_busy and OUTPUT_REFUSAL_AFTER_RUN_STORE_BUSY_MSG, whose details name the request of the run, and review-series
runs that request again by itself. Lane v7-checked-answers: the plan of a head whose answer fails its check without a
refusal record is resume, the recorded review-run request, for a turn of protocol v7 and for a single review under the
finding contract (exactory-review-v7/native_math). Another command holds a lock of the store; synthetic scientific
content."""

import sqlite3
import unittest
from contextlib import contextmanager
from unittest import mock

from codex_route_fixtures import build_codex_response
from finishing_write_fixtures import OtherWriters, WaitlessTime
from openness_reading_fixtures import OpennessReadingCase
from research_harness import cli, finishing, openness_reading as reading, openness_reading_v7 as v7
from research_harness import review_protocol as protocol, review_refusal, review_series, storage
from research_harness.problem_openness import assess_openness
from research_harness.review_continuation import plan_next
from research_harness.review_task_execution import identify_call
import test_openness_finding_answer as finding_fixtures
import test_openness_reading_v7_turns as turn_fixtures
import test_openness_reading_v7_unchecked_answer as unchecked

# The message of the store's own refusal of a read or a write while another command holds its lock.
STORE_BUSY_MSG = 'Research store is busy; retry the same request after the writer finishes'


class RefusalWriteCase(OtherWriters, OpennessReadingCase):
    """The steps that a v7 turn and a single review share: a run whose refusal record the store refuses, and
    review-series beside another command."""

    def setUp(self):
        super().setUp()
        self.start_other_writers()

    def refuse_run_write(self, answer):
        """Run turn-1 with an answer that fails its check while another command holds the write lock of the store from
        the first commit of the refusal record until the run is refused (lane finishing-writes-2)."""
        error = self.refuse_beyond_the_wait('review.refusal', lambda: self.invoke('turn-1', answer))
        self.assertEqual((error.code, error.message),
                         ('store_busy', review_refusal.OUTPUT_REFUSAL_AFTER_RUN_STORE_BUSY_MSG))
        self.assertEqual((error.details['request_id'], error.details['attempt_id']),
                         ('invoke-attempt-turn-1', 'attempt-turn-1'))
        records = self.store.snapshot()['records']
        self.assertEqual(records.get('review_output_refusal', {}), {})
        self.assertTrue(protocol.assignment_state(records, self.artifacts, 'turn-1')['ready'])
        return error

    def assert_resume_of_the_run(self, refusal_code):
        """The plan of turn-1 is the recorded request of its run (lane v7-checked-answers)."""
        records = self.store.snapshot()['records']
        plan = plan_next(records, self.artifacts, 'turn-1')
        self.assertEqual((plan['state'], plan.get('command'), plan.get('request_id')),
                         ('resume', 'review-run', 'invoke-attempt-turn-1'), plan)
        self.assertEqual(plan['payload'], dict(id='attempt-turn-1', assignment_id='turn-1'))
        self.assertEqual(plan['details']['refusal'],
                         review_refusal.classify_observed_output(records, self.artifacts, 'attempt-turn-1'))
        self.assertEqual(plan['details']['refusal']['code'], refusal_code)
        return plan

    def take_step(self, plan):
        """Take a planned step through the operation table, as review-series does; a reviewer call fails the test."""
        with mock.patch.object(protocol.transport, 'send_request', side_effect=AssertionError('No reviewer call.')):
            return cli.OPERATIONS[plan['command']](self.store, plan['payload'], expected_revision=self.store.revision,
                                                   request_id=plan['request_id'])

    def run_series(self, respond):
        rows = []
        with mock.patch.object(protocol.transport, 'send_request', side_effect=respond):
            code = review_series.run_series(self.store, 'turn-1', emit=rows.append)
        return code, rows

    @contextmanager
    def keep_refusal_writes_busy(self):
        """Another command holds the write lock of the store from the first commit of a refusal record in a review-run
        command until that command ends, so that each refusal record is refused for the whole wait. The other writes
        of review-series commit."""
        held = []
        end = self.meet_commits(('review.refusal',), act=lambda: held or held.append(self.hold_write_lock()))
        run = cli.OPERATIONS['review-run']

        def run_beside_the_writer(store, payload, **identity):
            try:
                return run(store, payload, **identity)
            finally:
                while held:
                    held.pop().execute('ROLLBACK')
        try:
            with mock.patch.dict(cli.OPERATIONS, {'review-run': run_beside_the_writer}), \
                    mock.patch.object(storage, '_BUSY_TIMEOUT_SECONDS', 0.01), \
                    mock.patch.object(finishing, 'time', WaitlessTime(ticking=True)):
                yield
        finally:
            end()

    def assert_series_waits_for_the_store(self, code, rows):
        """review-series repeated the recorded request of turn-1 and ended with store_busy, a waiting stop, without
        the harness-defect stop review_series_no_progress and its step gate-auto-pass."""
        self.assertEqual((code, rows[-1]['state'], rows[-1].get('code'), rows[-1].get('stop_class')),
                         (3, 'wait', 'store_busy', 'waiting'), rows[-1])
        self.assertEqual(rows[-1]['error']['message'], review_refusal.OUTPUT_REFUSAL_AFTER_RUN_STORE_BUSY_MSG)
        # The step of a waiting stop: review-series on the same head again.
        self.assertEqual(rows[-1]['details']['step']['command'], 'review-series')
        steps = [(row['state'], row.get('command'), row.get('request_id')) for row in rows]
        # Each refusal but the last is a line of progress; the last ends the series.
        self.assertEqual(steps, [('resume', 'review-run', 'invoke-attempt-turn-1')]
                         * (review_series.MAX_CONSECUTIVE_REFUSALS - 1)
                         + [('wait', 'review-run', 'invoke-attempt-turn-1')])
        for row in rows:
            self.assertNotIn('review_series_no_progress', (row.get('code'), (row.get('error') or {}).get('code')), row)
            self.assertNotIn('gate-auto-pass', (row.get('command'), row['details'].get('step', {}).get('command')), row)
        records = self.store.snapshot()['records']
        self.assertEqual(records.get('review_output_refusal', {}), {})
        self.assertEqual(sorted(records['review_assignment']), ['turn-1'])
        self.assertEqual(records.get('problem_openness', {}), {})
        self.assertEqual([row['code'] for row in assess_openness(records, self.artifacts)],
                         ['problem_openness_missing'])


class V7TurnRefusalWriteTests(RefusalWriteCase):
    """A turn of an openness reading chain of protocol v7."""
    capacity_probe_tokens = 487900
    reading_protocol = None
    route_model = 'gpt-6-astra'
    route_output_tokens = 128000
    text_sizes = (50000, 50000, 50000)
    build_v7_answer = turn_fixtures.V7CommandTests.build_v7_answer
    build_v7_finding = turn_fixtures.V7CommandTests.build_v7_finding
    replace_head = unchecked.UncheckedAnswerTests.replace_head

    def setUp(self):
        super().setUp()
        enabled = mock.patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6))
        enabled.start()
        self.addCleanup(enabled.stop)
        self.assign('turn-1', self.export())
        packet = self.read_packet('turn-1')
        # One member more than the contract admits: the next assignment of 0.91.x read it without a check.
        refused = unchecked.build_refused_reading_answers(packet, self.build_v7_answer(packet))
        self.refused_answer = refused['extra_member']

    def answer_every_turn(self, calls, refuse_first=False):
        """The stand-in reviewer: a passing answer to every turn and the finding at the final turn; with refuse_first,
        the refused answer to the first run of turn 1."""
        def respond(route, request, credential):
            packet = reading.review_excerpts.read_sent_packet(request)
            binding = packet.get('reading_turn', packet.get('reading_final'))
            calls.append(binding['turn'])
            if 'reading_final' in packet:
                output = self.build_v7_finding(packet, binding['notes_handle'])
            elif refuse_first and calls == [1]:
                output = self.refused_answer
            else:
                output = self.build_v7_answer(packet)
            return build_codex_response(route, request, output)
        return respond

    def test_run_refused_beyond_the_wait_is_resumed_and_its_step_records_the_refusal(self):
        """Case (a). Lane finishing-writes-2: the run is refused with text 7 and keeps its request. Lane
        v7-checked-answers: the plan is resume with that request, and after its step the plan of a refused turn."""
        self.refuse_run_write(self.refused_answer)
        plan = self.assert_resume_of_the_run('invalid_reading_response')
        receipt = self.take_step(plan)
        self.assertEqual(receipt['output_refusal'],
                         {key: plan['details']['refusal'][key] for key in ('code', 'check', 'details')})
        records = self.store.snapshot()['records']
        self.assertEqual(sorted(records['review_output_refusal']), ['attempt-turn-1'])
        self.assertEqual(sorted(records['review_attempt']), ['attempt-turn-1'])
        self.assertEqual(self.store.committed_request('invoke-attempt-turn-1:refusal')['operation'], 'review.refusal')
        replacement = self.replace_head('turn-1')
        notice = self.read_packet(replacement['id'])['output_refusal']
        self.assertEqual([row['attempt_id'] for row in notice['refused']], ['attempt-turn-1'])

    def test_series_refused_again_at_resume_waits_and_a_later_series_goes_on(self):
        """Case (b). Lane finishing-writes-2: each step is refused with text 7 and the series runs the same request
        again until it ends with store_busy. Lane v7-checked-answers: the step is resume, so the series ends without
        review_series_no_progress and the gate keeps problem_openness_missing. A later series records the refusal,
        replaces the turn and reaches the finding."""
        self.refuse_run_write(self.refused_answer)
        calls = []
        with self.keep_refusal_writes_busy():
            code, rows = self.run_series(self.answer_every_turn(calls))
        self.assert_series_waits_for_the_store(code, rows)
        self.assertEqual(calls, [])
        code, rows = self.run_series(self.answer_every_turn(calls))
        self.assertEqual((code, rows[-1]['state']), (0, 'done'), rows[-1])
        steps = [(row['state'], row.get('command')) for row in rows]
        self.assertEqual(steps[:2], [('resume', 'review-run'), ('replace', 'review-assignment')], steps)
        self.assertEqual(rows[0]['request_id'], 'invoke-attempt-turn-1')
        records = self.store.snapshot()['records']
        self.assertEqual(sorted(records['review_output_refusal']), ['attempt-turn-1'])
        # The first reviewer call of the later series is the replacement of turn 1, which carries the refusal.
        self.assertEqual(calls[0], 1)
        self.assertEqual(calls.count(1), 1)
        self.assert_finding_rests_on_no_refused_answer(records, 'attempt-turn-1')

    def assert_finding_rests_on_no_refused_answer(self, records, refused_attempt):
        """The recorded finding verifies. No assignment continues from turn 1: the chain of the finding starts at the
        replacement of turn 1, whose packet carries the refusal of the refused answer."""
        assignments = records['review_assignment']
        head = next(iter(records['problem_openness']))
        reading.verify_chain(records, self.artifacts, assignments[head])
        self.assertEqual([row['code'] for row in assess_openness(records, self.artifacts)],
                         ['problem_openness_unresolved'])
        self.assertEqual([identifier for identifier, row in assignments.items()
                          if row['context'].get('prior_assignment_id') == 'turn-1'], [])
        first = head
        while assignments[first]['context'].get('prior_assignment_id') is not None:
            first = assignments[first]['context']['prior_assignment_id']
        self.assertEqual(assignments[first]['context']['output_refusal'], dict(assignment_id='turn-1'))
        notice = self.read_packet(first)['output_refusal']
        self.assertEqual([row['attempt_id'] for row in notice['refused']], [refused_attempt])

    def test_read_after_the_run_refused_for_the_whole_wait_is_resumed_without_using_the_answer(self):
        """Case (d). Another command holds the exclusive lock of the store from the end of the run until the run
        command ends, so the read of the store after the run, which waits for a busy store (lane finishing-writes-2),
        is refused with the store's own store_busy, whose details name no request; review-series then plans again in
        place of running that request again. Lane v7-checked-answers: the plan is resume with the request of the run;
        the step records the refusal and calls no reviewer, the turn is replaced, and no later turn is built on the
        refused answer."""
        invoke_once, run, held = protocol._invoke_once, cli.OPERATIONS['review-run'], []

        def run_then_lock(*args, **kwargs):
            result = invoke_once(*args, **kwargs)
            if not held:
                lock = sqlite3.connect(str(self.root / '.exactory' / 'research.sqlite3'), isolation_level=None)
                self.addCleanup(lock.close)
                lock.execute('BEGIN EXCLUSIVE')
                held.append(lock)
            return result

        def run_beside_the_lock(store, payload, **identity):
            try:
                return run(store, payload, **identity)
            finally:
                if held and held[0].in_transaction:
                    held[0].execute('ROLLBACK')
        calls = []
        with mock.patch.object(protocol, '_invoke_once', run_then_lock), \
                mock.patch.dict(cli.OPERATIONS, {'review-run': run_beside_the_lock}), \
                mock.patch.object(storage, '_BUSY_TIMEOUT_SECONDS', 0.01), \
                mock.patch.object(finishing, 'time', WaitlessTime(ticking=True)):
            code, rows = self.run_series(self.answer_every_turn(calls, refuse_first=True))
        self.assertEqual((code, rows[-1]['state']), (0, 'done'), rows[-1])
        first, second, third = rows[:3]
        self.assertEqual((first['state'], first['command'], first['error']['code'], first['error']['message']),
                         ('run', 'review-run', 'store_busy', STORE_BUSY_MSG), first)
        self.assertNotIn('request_id', first['error'].get('details') or {})
        self.assertEqual((second['state'], second['command'], second['request_id']),
                         ('resume', 'review-run', first['request_id']), second)
        self.assertNotIn('error', second)
        self.assertEqual(second['details']['refusal']['code'], 'invalid_reading_response')
        self.assertEqual((third['state'], third['command']), ('replace', 'review-assignment'), third)
        refused_attempt = second['payload']['id']
        records = self.store.snapshot()['records']
        self.assertEqual(sorted(records['review_output_refusal']), [refused_attempt])
        self.assertEqual(records['review_call_observation'][identify_call('review.invoke', refused_attempt)]
                         ['request_id'], first['request_id'])
        # The reviewer answered turn 1 twice: the refused run and the replacement. The resume step made no call.
        self.assertEqual(calls.count(1), 2)
        self.assert_finding_rests_on_no_refused_answer(records, refused_attempt)


class SingleReviewRefusalWriteTests(RefusalWriteCase):
    """Case (c): a single review under the finding contract (exactory-review-v7/native_math)."""
    reading_protocol = None
    export_finding_packet = finding_fixtures.FindingRecordingTests.export_finding_packet
    assert_refused_review_plan = unchecked.UncheckedSingleReviewTests.assert_refused_review_plan

    def setUp(self):
        super().setUp()
        self.assign('turn-1', self.export_finding_packet())
        records = self.store.snapshot()['records']
        self.assertEqual(records['review_assignment']['turn-1']['prompt']['template_id'],
                         'exactory-review-v7/native_math')
        # supported_open with a ground, which rule 2 of the finding contract refuses.
        self.refused_answer = finding_fixtures.build_answer(self.read_packet('turn-1'), 'supported_open',
                                                            [unchecked.OWN_ARGUMENT_GROUND])

    def test_run_refused_beyond_the_wait_is_resumed_and_its_step_records_the_refusal(self):
        """Case (c) after (a). Lane finishing-writes-2: the run is refused with text 7 and keeps its request. Lane
        v7-checked-answers: the plan is resume with that request, not the recording of the finding; after its step the
        plan is the handoff of a refused review."""
        self.refuse_run_write(self.refused_answer)
        plan = self.assert_resume_of_the_run('openness_finding_invalid')
        receipt = self.take_step(plan)
        self.assertEqual(receipt['output_refusal']['code'], 'openness_finding_invalid')
        records = self.store.snapshot()['records']
        self.assertEqual(sorted(records['review_output_refusal']), ['attempt-turn-1'])
        self.assertEqual(records.get('problem_openness', {}), {})
        self.assert_refused_review_plan()

    def test_series_refused_again_at_resume_waits_and_a_later_series_goes_on(self):
        """Case (c) after (b). Lane finishing-writes-2: each step is refused with text 7 and the series runs the same
        request again until it ends with store_busy. Lane v7-checked-answers: the step is resume, so no finding is
        recorded and the gate keeps problem_openness_missing. A later series records the refusal and ends with the
        handoff of a refused review."""
        self.refuse_run_write(self.refused_answer)
        calls = []

        def respond(route, request, credential):
            calls.append(request)
            raise AssertionError('No reviewer call.')
        with self.keep_refusal_writes_busy():
            code, rows = self.run_series(respond)
        self.assert_series_waits_for_the_store(code, rows)
        code, rows = self.run_series(respond)
        self.assertEqual([(row['state'], row.get('command'), row.get('request_id')) for row in rows[:1]],
                         [('resume', 'review-run', 'invoke-attempt-turn-1')], rows)
        self.assertEqual((code, rows[-1]['state'], rows[-1]['code']), (3, 'handoff', 'review_slot_exhausted'), rows[-1])
        records = self.store.snapshot()['records']
        self.assertEqual(sorted(records['review_output_refusal']), ['attempt-turn-1'])
        self.assertEqual(records.get('problem_openness', {}), {})
        self.assertEqual(calls, [])
        self.assert_refused_review_plan()


if __name__ == '__main__':
    unittest.main()
