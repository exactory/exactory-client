"""Property coverage for session v4 with a reviewer at the transport boundary."""

import unittest

import property_stub_a as stub
import test_review_session_v4_turns as fixtures
from research_harness import research_decisions, review_protocol as protocol, review_session_v4 as session
from research_harness.literature import import_bundle
from research_harness.reading import record_reading
from research_harness.review_composite import read_final_output
from research_harness.review_continuation import plan_next


class SessionProperties(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.SessionTurnsTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.fixture = fixture
        self.case = fixture.case

    def open_session(self):
        return self.case.assign('bar', 'first', identifier='s' * 60)

    def seed_fulltext(self):
        version = self.fixture.add_sources([80])[0]
        capture = self.case.read_records()['work'][version]['fulltexts'][0]
        bundle = self.case.bundle(version, capture=capture, bundle_id='one-bundle')
        self.case.mutate(import_bundle, bundle)
        self.case.mutate(record_reading, self.case.full_note(bundle, note_id='one-reading'))

    def drive_requests(self, answer, *, fill_request=False):
        assignment = self.open_session()
        for _ in range(4):
            packet = self.fixture.read_packet(assignment)
            if 'session_final' in packet:
                return assignment
            stub.invoke_prompt(self, self.case, assignment, answer, fill_request=fill_request)
            records = self.case.read_records()
            attempt = records['review_attempt']['attempt-' + assignment['id']]
            self.assertTrue(protocol.authenticate_attempt(records, self.case.artifacts, assignment, attempt))
            if fill_request:
                self.assertEqual(attempt['answer']['characters'], attempt['answer']['bound'])
            action = plan_next(records, self.case.artifacts, assignment['id'])
            self.assertEqual((action['state'], action['command']), ('continue', 'review-assignment'), action)
            assignment = self.case.mutate(protocol.record_assignment, action['payload'])['result']
        self.fail('The advertised inventory and passage requests did not reach a final')

    def record_bar(self, assignment):
        records = self.case.read_records()
        attempt = records['review_attempt']['attempt-' + assignment['id']]
        self.assertTrue(protocol.authenticate_attempt(records, self.case.artifacts, assignment, attempt))
        action = plan_next(records, self.case.artifacts, assignment['id'])
        self.assertEqual((action['state'], action.get('command')), ('record', 'value-review'),
                         (action['state'], action.get('code'), records.get('review_output_refusal')))
        final, _ = read_final_output(records, self.case.artifacts, assignment)
        saved = self.case.mutate(research_decisions.record_value_review,
            dict(final, id='bar-review', dossier_id=None, assignment_id=assignment['id']))['result']
        self.assertEqual(saved['assignment_id'], assignment['id'])
        self.assertEqual(len(saved['payload']['value']['evidence']), 1)
        return attempt

    def test_over_bound_final_is_recorded_and_authenticates(self):
        self.seed_fulltext()
        final = self.drive_requests(stub.build_session_request)

        def answer(prompt):
            output = stub.build_bar(prompt)
            return stub.size_answer(output, output['value'], 'reason', 2 * stub.read_answer_bound(prompt))

        stub.invoke_prompt(self, self.case, final, answer)
        attempt = self.record_bar(final)
        self.assertEqual(attempt['answer']['characters'], 2 * attempt['answer']['bound'])

    def test_failed_attempts_reach_the_position_stop(self):
        stub.exhaust_attempts(self, self.case, self.open_session())

    def test_q18_every_answer_is_at_its_bound(self):
        self.seed_fulltext()

        def answer_request(prompt):
            output = stub.build_session_request(prompt)
            output['notes'] = 'n' * prompt['session_turn']['allowances']['notes']
            return output

        final = self.drive_requests(answer_request, fill_request=True)

        def answer_final(prompt):
            output = stub.build_bar(prompt)
            return stub.size_answer(output, output['value'], 'reason', stub.read_answer_bound(prompt))

        stub.invoke_prompt(self, self.case, final, answer_final)
        attempt = self.record_bar(final)
        self.assertEqual(attempt['answer']['characters'], attempt['answer']['bound'])
        _, _, state = session.read_bound_session(self.case.artifacts, final)
        self.assertTrue(state['latest_notes'])
        self.assertEqual(len(state['excerpts']), 1)
