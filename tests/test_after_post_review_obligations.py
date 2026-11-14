"""Stopped review chains retain their evidence without reopening a posted claim."""

import json
from unittest.mock import patch

from test_posted_chain_closure import ClosureCase
from research_harness import cli, problem_delivery, review_protocol as protocol, review_task_execution
from research_harness.evidence import digest


class AfterPostReviewTests(ClosureCase):
    def stop_invocation(self):
        with patch.object(review_task_execution, 'start_call', return_value=None), \
                patch.object(protocol, '_observed', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.mutate(protocol.invoke_assignment,
                            dict(id='stopped-call', assignment_id=self.assignment['id']), credential='fixture')
        records = self.store.snapshot()['records']
        self.assertIn('stopped-call', records['review_invocation'])
        self.assertNotIn('stopped-call', records.get('review_attempt', {}))

    def assert_posted_views(self, code):
        before = self.store.snapshot()
        for command in ('next', 'status'):
            report = cli.run(cli.build_parser().parse_args([command, '--workspace', str(self.root)]))
            self.assertEqual([row['code'] for row in report['obligations']], ['problem_already_posted'])
            self.assertEqual(report['counts']['obligations'], 1)
            rows = [row for row in report['after_post']
                    if row.get('assignment_id') == self.assignment['id'] and row['code'] == code]
            self.assertEqual(len(rows), 1, report['after_post'])
            self.assertNotIn('next', rows[0])
            self.assertTrue(rows[0]['reason'])
        page = cli.run(cli.build_parser().parse_args(
            ['obligations', '--workspace', str(self.root), '--code', code]))
        self.assertEqual(page['total'], 0)
        self.assertEqual(self.store.snapshot(), before)

    def test_stopped_invocation_without_attempt_moves_after_external_post(self):
        self.limit_input()
        self.reference = self.export(name='reading-chain-packet', assignment_id='reading-root')
        self.assignment = self.assign('reading-root', self.reference)
        self.invoke(self.assignment['id'], self.build_reading_response(self.read_packet(self.assignment['id'])))
        self.assignment = self.assign('stopped-turn-2', self.reference, prior=self.assignment['id'])
        self.stop_invocation()
        self.post()
        self.assert_posted_views('review_output_pending')

    def test_stopped_invocation_without_attempt_moves_after_passed_post(self):
        self.stop_invocation()
        self.post(legacy=True)
        self.assert_posted_views('review_output_pending')

    def test_closed_review_stays_after_post_when_another_statement_is_selected(self):
        self.stop_invocation()
        self.post()
        self.selected = self.select_statement(identifier='different-statement',
                                              statement='Is the bound strict for every odd input?')
        self.searches = self.record_searches()
        self.assign('new-statement-review', self.export(name='new-claim-packet', assignment_id='new-statement-review'))
        report = cli.status_report(self.store)
        self.assertIsNone(report.get('post'))
        self.assertNotIn(self.assignment['id'], {row.get('assignment_id') for row in report['obligations']})
        self.assertIn('new-statement-review', {row.get('assignment_id') for row in report['obligations']})
        closed = [row for row in report['after_post'] if row.get('assignment_id') == self.assignment['id']]
        self.assertEqual([row['code'] for row in closed], ['review_output_pending'])
        self.assertNotIn('next', closed[0])

    def test_unverified_stopped_chain_moves_after_replacement_export_and_external_post(self):
        self.stop_invocation()
        packet = self.read_packet(self.assignment['id'])
        output = self.build_reading_response(packet)
        reference = self.artifacts.put(json.dumps(output).encode(), 'application/json')
        self.mutate(protocol.record_attempt, dict(id='imported-output', assignment_id=self.assignment['id'],
            status='completed', output=reference, reason='Retained output of the stopped reviewer.', usage={}))
        self.assign('replacement', self.export(name='replacement-packet', assignment_id='replacement'))
        before = cli.status_report(self.store)
        self.assertIn('review_invocation_unverified', {row['code'] for row in before['obligations']})
        records = self.store.snapshot()['records']
        assignment_digest = digest(records['review_assignment'][self.assignment['id']])
        self.post()
        self.assert_posted_views('review_invocation_unverified')
        self.assertEqual(digest(self.store.snapshot()['records']['review_assignment'][self.assignment['id']]),
                         assignment_digest)

    def test_closed_review_already_named_by_gate_moves_after_post_for_another_current_statement(self):
        self.stop_invocation()
        self.post()
        self.select_statement(identifier='different-statement', statement='Is the bound strict for every odd input?')
        pending = dict(code='review_output_pending', explanation='A complete observed reviewer response is required.',
                       assignment_id=self.assignment['id'])
        before = self.store.snapshot()
        with patch.object(problem_delivery, 'assess_source_requests', return_value=[pending]):
            report = cli.status_report(self.store)
        self.assertNotIn(pending['assignment_id'], {row.get('assignment_id') for row in report['obligations']})
        self.assertEqual([row for row in report['after_post'] if row.get('assignment_id') == pending['assignment_id']],
                         [dict(pending, reason=pending['explanation'])])
        self.assertEqual(self.store.snapshot(), before)

    def test_closing_an_old_review_preserves_a_current_no_post_decision(self):
        self.stop_invocation()
        self.post()
        self.select_statement(identifier='different-statement', statement='Is the bound strict for every odd input?')
        slate = self.store.snapshot()['records']['problem_slate']['slate-1']
        self.put('problem_slate', 'slate-1', dict(slate, selected=[], reason='No further post is planned.'))
        pending = dict(code='review_output_pending', explanation='A complete observed reviewer response is required.',
                       assignment_id=self.assignment['id'])
        with patch.object(problem_delivery, 'assess_source_requests', return_value=[pending]):
            report = cli.status_report(self.store)
        self.assertEqual(report['next']['code'], 'problem_no_post')
        self.assertNotIn(pending['assignment_id'], {row.get('assignment_id') for row in report['obligations']})

    def test_closing_the_last_old_review_preserves_the_export_decision(self):
        self.stop_invocation()
        self.post()
        self.select_statement(identifier='different-statement', statement='Is the bound strict for every odd input?')
        pending = dict(code='review_output_pending', explanation='A complete observed reviewer response is required.',
                       assignment_id=self.assignment['id'])
        records = self.store.snapshot()['records']
        gate = problem_delivery.assess_post(records, self.artifacts)
        gate = dict(gate, obligations=[pending], counts=dict(gate['counts'], obligations=1))
        before = self.store.snapshot()
        with patch.object(cli, 'gate_state', return_value=gate):
            report = cli.status_report(self.store)
        self.assertEqual(report['next']['code'], 'problem_export_required')
        self.assertEqual(report['obligations'], [])
        self.assertEqual(report['counts']['obligations'], 0)
        self.assertIn(dict(pending, reason=pending['explanation']), report['after_post'])
        self.assertEqual(self.store.snapshot(), before)
