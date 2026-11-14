"""Reviewed continuations and stored-content boundaries of the review B lane."""

import unittest
from unittest.mock import patch

from research_harness import cli, review_continuation, review_protocol, review_series, stop_classes
from research_harness.errors import ResearchError
import test_review_continuation as continuation_fixtures


SCIENTIFIC_REFUSAL_CODES = (
    'invalid_locator', 'invalid_native_review', 'invalid_reading_response',
    'invalid_record', 'invalid_review_adjudication', 'invalid_review_source_request',
    'invalid_review_staged', 'invalid_value_review', 'research_objection_identity_conflict',
    'source_mismatch', 'value_review_support_missing', 'openness_minimum_required', 'openness_search_date',
)


class ReviewBRowTests(unittest.TestCase):
    def prepare_fixture(self, fixture_type=continuation_fixtures.ContinuationTests):
        fixture = fixture_type()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_every_stored_content_code_keeps_the_reviewed_record_check_decision(self):
        fixture = self.prepare_fixture()
        codes = getattr(stop_classes, '_STORED_CONTENT_CODES_REVIEW_B', ())
        self.assertTrue(codes, 'The review B stored-content codes need a shared registry row.')
        for code in codes:
            with self.subTest(code=code):
                def refuse_operation(store, payload, **identity):
                    raise ResearchError(code, 'The retained review binding does not verify.', {'binding': 'saved'})
                rows = []
                with patch.dict(cli.OPERATIONS, {'review-run': refuse_operation}):
                    status = review_series.run_series(fixture.case.store, 'bar-first', emit=rows.append)
                terminal = rows[-1]
                self.assertEqual(status, 3)
                self.assertEqual((terminal['state'], terminal['code'], terminal['stop_class']),
                                 ('stop', code, 'decision'))
                self.assertNotIn('unlisted', terminal['details'])
                self.assertEqual(stop_classes.STOP_REGISTRY[code].get('group'), 'stored_state')
                self.assertEqual(terminal['details']['decision'],
                                 stop_classes.describe_terminal('review_history_invalid')['details']['decision'])
                self.assertEqual(terminal['details']['decision']['then'][0]['steps'], [{'command': 'record-check'}])
                self.assertEqual(terminal['error']['details'], {'binding': 'saved'})
                self.assertEqual(fixture.case.read_records().get('review_attempt', {}), {})

    def test_each_scientific_refusal_code_uses_the_shared_outcome(self):
        fixture = self.prepare_fixture()
        for code in SCIENTIFIC_REFUSAL_CODES:
            with self.subTest(code=code):
                def refuse_operation(store, payload, **identity):
                    raise ResearchError(code, 'The observed output fails its frozen contract.')
                rows = []
                with patch.dict(cli.OPERATIONS, {'review-run': refuse_operation}):
                    status = review_series.run_series(fixture.case.store, 'bar-first', emit=rows.append)
                self.assertEqual(status, 3)
                self.assertEqual(rows[-1]['code'], code)
                self.assertNotIn('unlisted', rows[-1]['details'])
                self.assertEqual(stop_classes.describe_terminal(code)['details']['next'],
                                 {'kind': 'outcome', 'outcome': 'scientific_refused'})

    def test_scientific_refusal_next_run_is_accepted_and_records_the_valid_output(self):
        fixture = self.prepare_fixture()
        self.assertIn('invalid_value_review', stop_classes.STOP_REGISTRY)
        invalid = fixture.case.build_review_output('bar')
        invalid['value']['status'] = 'invented'
        valid = fixture.case.build_review_output('bar')
        outputs = iter((invalid, valid))
        def respond(route, request, credential):
            return fixture.case.build_provider_response(next(outputs))
        rows = []
        with patch.object(review_protocol.transport, 'send_request', side_effect=respond):
            status = review_series.run_series(fixture.case.store, 'bar-first', emit=rows.append)
        self.assertEqual((status, rows[-1]['state']), (0, 'done'))
        records = fixture.case.read_records()
        refusals = list(records['review_output_refusal'].values())
        self.assertEqual([row['code'] for row in refusals], ['invalid_value_review'])
        self.assertEqual(len(records['review_attempt']), 2)
        self.assertEqual(records['value_review']['review:bar-first']['payload']['value'], valid['value'])
        self.assertTrue(any(row.get('details', {}).get('outcome') == 'scientific_refused' for row in rows))

    def test_duplicate_review_hands_off_to_an_accepted_eligible_bar(self):
        fixture = self.prepare_fixture()
        rows = []
        def refuse_operation(store, payload, **identity):
            raise ResearchError('value_review_duplicate', 'This output already supplies its immutable review.')
        with patch.dict(cli.OPERATIONS, {'review-run': refuse_operation}):
            review_series.run_series(fixture.case.store, 'bar-first', emit=rows.append)
        plan = rows[-1]
        self.assertEqual((plan['state'], plan['command']), ('handoff', 'review-assignment'))
        step = plan['details']['step']
        self.assertEqual(step['id'], 3)
        payload = dict(step['payload'], id='eligible-bar', route_id='route', reviewer_id='second')
        accepted = fixture.case.mutate(cli.OPERATIONS[step['command']], payload)['result']
        next_plan = fixture.plan(head=accepted['id'])
        self.assertEqual((next_plan['state'], next_plan['command']), ('run', 'review-run'))
        self.assertNotEqual(next_plan, plan)

    def test_stale_bar_handoff_refreshes_the_named_bar_in_its_original_slot(self):
        from test_problem_areas import ProblemAreaReviewTests
        fixture = self.prepare_fixture(ProblemAreaReviewTests)
        fixture.initialize_problem()
        fixture.record_candidate()
        fixture.observe_bars()
        fixture.add_area()
        with self.assertRaises(ResearchError) as caught:
            fixture.assign('slate', 'first')
        error = caught.exception
        plan = stop_classes.describe_terminal(error.code)
        plan['details'].update(error.details or {})
        self.assertEqual((plan['state'], plan['code']), ('stop', 'problem_bar_calibration_required'))
        step = plan['details']['decision']['then'][0]['steps'][0]
        self.assertEqual(step['command'], 'review-assignment')
        review = fixture.store.snapshot()['records']['value_review'][plan['details']['review_id']]
        prior = fixture.store.snapshot()['records']['review_assignment'][review['assignment_id']]
        payload = dict(step['payload'], id='refreshed-bar', dossier_id=prior['dossier_id'], route_id=prior['route_id'],
                       reviewer_id=prior['reviewer_id'], author_id=prior['author_id'])
        accepted = fixture.mutate(cli.OPERATIONS[step['command']], payload)['result']
        self.assertEqual(accepted['slot_id'], prior['slot_id'])
        self.assertEqual(accepted['history_assignment_id'], prior['id'])
        next_plan = review_continuation.plan_next(fixture.store.snapshot()['records'], fixture.artifacts, prior['id'])
        self.assertEqual(next_plan['head_id'], accepted['id'])
        self.assertEqual(next_plan['state'], 'run')
        self.assertNotEqual(next_plan, plan)

    def test_pending_evidence_part_handoff_accepts_the_series_continuation(self):
        from problem_parts_fixtures import ResultPartsCase
        fixture = self.prepare_fixture(ResultPartsCase)
        fixture.oversized()
        assignment = fixture.assign()
        fixture.invoke(assignment)
        terminal = stop_classes.describe_terminal('problem_evidence_part_pending')
        self.assertEqual((terminal['state'], terminal.get('command')), ('handoff', 'review-series'))
        before = review_continuation.plan_next(fixture.case.read_records(), fixture.case.artifacts, assignment['id'])
        self.assertEqual(before['state'], 'continue')
        from codex_route_fixtures import build_codex_response
        from problem_parts_fixtures import KEY, note_units
        from research_harness.review_excerpts import read_sent_packet
        def respond(route, request, credential):
            packet = read_sent_packet(request)
            if packet.get(KEY, {}).get('stage') == 'part':
                output = dict(stage='result', phase='evidence_part', part=packet[KEY]['turn'],
                    notes=[dict(unit=unit, note='The exact delivered search remains part of this assessment.')
                           for unit in note_units(packet)])
            else:
                output = fixture.final_output()
            return build_codex_response(route, request, output)
        rows = []
        with patch.object(review_protocol.transport, 'send_request', side_effect=respond):
            status = review_series.run_series(fixture.case.store, assignment['id'], emit=rows.append)
        self.assertEqual((status, rows[-1]['state']), (0, 'done'))
        self.assertTrue(any(row['state'] == 'continue' for row in rows))
        after = review_continuation.plan_next(fixture.case.read_records(), fixture.case.artifacts, assignment['id'])
        self.assertEqual(after['state'], 'done')
        self.assertNotEqual(before, after)


if __name__ == '__main__':
    unittest.main()
