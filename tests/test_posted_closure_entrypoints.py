"""Public views and series execution preserve posted openness closure."""

from contextlib import contextmanager, redirect_stdout
import io
import json
import unittest
from unittest.mock import patch

from openness_reading_fixtures import OpennessReadingCase
from test_posted_chain_closure import ClosureCase
from research_harness import cli, native_review, problem_delivery
from research_harness import review_protocol as protocol
from research_harness import review_worker
from research_harness.evaluation import Evaluation
from research_harness.evidence import digest
from research_harness.storage import Store


class PostedClosureEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.case = ClosureCase()
        self.addCleanup(self.case.doCleanups)
        OpennessReadingCase.setUp(self.case)
        self.case.limit_input()
        self.case.reference = self.case.export(assignment_id='openness-root')
        self.root_assignment = self.case.assign('openness-root', self.case.reference)
        packet = self.case.read_packet(self.root_assignment['id'])
        self.assertIn('reading_turn', packet)
        self.case.invoke(self.root_assignment['id'], self.case.build_reading_response(packet))
        self.case.assignment = self.case.assign(
            'openness-head', self.case.reference, prior=self.root_assignment['id'])
        self.assertIn('reading_turn', self.case.read_packet(self.case.assignment['id']))
        self.assertNotIn('subject', self.case.assignment)

    @contextmanager
    def refuse_new_work(self):
        before = self.case.store.snapshot()
        with patch.object(protocol.transport, 'send_request',
                          side_effect=AssertionError('A posted chain starts no provider call')), \
                patch.object(review_worker, 'launch_worker',
                             side_effect=AssertionError('A posted chain launches no worker')), \
                patch.object(Store, 'mutate',
                             side_effect=AssertionError('A public view commits no mutation')), \
                patch.object(Store, 'mutate_scoped',
                             side_effect=AssertionError('A closed series commits no mutation')):
            yield
        self.assertEqual(self.case.store.snapshot(), before)

    def run_cli(self, command, *arguments, exit_code=None):
        output = io.StringIO()
        argv = [command, '--workspace', str(self.case.root)] + list(arguments)
        with redirect_stdout(output):
            if exit_code is None:
                cli.main(argv)
            else:
                with self.assertRaises(SystemExit) as exited:
                    cli.main(argv)
                self.assertEqual(exited.exception.code, exit_code)
        return json.loads(output.getvalue())

    def assert_closed_actions(self, report):
        self.assertEqual(report['next']['code'], 'problem_already_posted')
        self.assertEqual([row['code'] for row in report['obligations']], ['problem_already_posted'])
        self.assertEqual(report['counts']['obligations'], 1)
        self.assertTrue(any(row.get('assignment_id') == self.case.assignment['id']
                            and row['code'] == 'review_output_pending' for row in report['after_post']))
        for row in report['obligations'] + report['after_post']:
            self.assertNotIn('next', row)

    def test_status_closes_the_pending_openness_head_without_mutation(self):
        self.case.post(legacy=True)
        with self.refuse_new_work():
            report = self.run_cli('status')
        self.assert_closed_actions(report)
        self.assertEqual(set(report['review']['assignments']),
                         {self.root_assignment['id'], self.case.assignment['id']})
        self.assertEqual(report['review']['assignments'][self.case.assignment['id']]['refused_attempts'], [])

    def test_next_exposes_no_review_command_or_navigation(self):
        self.case.post(legacy=True)
        with self.refuse_new_work():
            report = self.run_cli('next')
        self.assert_closed_actions(report)

    def test_review_series_resolves_the_ancestor_to_the_closed_head(self):
        self.case.post(legacy=True)
        with self.refuse_new_work():
            plan = self.run_cli('review-series', '--assignment', self.root_assignment['id'], exit_code=0)
        self.assertEqual((plan['state'], plan['head_id']), ('done', self.case.assignment['id']))
        for key in ('command', 'payload', 'workflow', 'scope'):
            self.assertNotIn(key, plan)

    def test_output_export_keeps_the_openness_chain_and_observed_reading(self):
        self.case.post(legacy=True)
        before = self.case.store.snapshot()['records']
        attempt = next(iter(before['review_attempt'].values()))
        response = self.case.artifacts.read(attempt['output'])
        with self.refuse_new_work():
            summary = self.run_cli('export', '--kind', 'outputs')
        self.assertEqual(summary['errors'], 0)
        self.assertEqual(summary['revision'], self.case.store.revision)
        folder = self.case.root / 'intermediate/reviews/native_math/openness-root'
        series = json.loads((folder / 'series.json').read_text(encoding='utf-8'))
        self.assertEqual([turn['assignment_id'] for turn in series['turns']],
                         [self.root_assignment['id'], self.case.assignment['id']])
        self.assertEqual(series['turns'][0]['runs'][0]['status'], 'completed')
        self.assertEqual(series['turns'][1]['runs'], [])
        copied = folder / series['turns'][0]['runs'][0]['output_file']
        self.assertEqual(copied.read_bytes(), response)
        self.assertEqual(self.case.artifacts.read(attempt['output']), response)

    def add_solver_parts(self):
        self.case.limit_input(100000)
        evidence = []
        for number in range(3):
            body = ('Exact synthetic solver argument %d.\n' % number) * 2000
            reference = self.case.artifacts.put(body.encode(), 'text/plain')
            evidence.append(dict(digest=reference['sha256'], kind='artifact', artifact=reference))
        subject = dict(statement='Every synthetic solver objective has a finite witness.')
        packet = dict(
            protocol=native_review.PROTOCOL, kind='result', root=str(self.case.root),
            objective_id='solver-objective', contract=dict(proof_policy='analytical'),
            subject=subject, subject_digest=digest(subject), claim_digest=digest(subject),
            reviewer=dict(source='host', actor_id='solver-reviewer',
                          attestation_id='review-assignment:solver-parts'),
            scientific_context=dict(scope='objective'), evidence=evidence,
            execution_provenance=[], unmaterialized_digests=[])
        reference = self.case.artifacts.put(json.dumps(packet).encode(), 'application/json')
        assignment = self.case.mutate(protocol.record_assignment, dict(
            id='solver-parts', route_id='route', dossier_id=None, role='native_math',
            reviewer_id='solver-reviewer', author_id='problem-author',
            context=dict(native_packet=reference)))['result']
        self.assertEqual(assignment['review_parts']['adapter'], 'native_math')
        return assignment

    def test_status_and_output_parts_use_their_verified_artifact_evaluation(self):
        assignment = self.add_solver_parts()
        self.case.post(legacy=True)
        for command, arguments in (('status', ()), ('export', ('--kind', 'outputs'))):
            with self.subTest(command=command), self.refuse_new_work(), \
                    patch.object(problem_delivery, 'find_review_post',
                                 wraps=problem_delivery.find_review_post) as classify:
                report = self.run_cli(command, *arguments)
            calls = [call for call in classify.call_args_list
                     if call.args[1].get('id') == assignment['id']]
            self.assertTrue(calls)
            for call in calls:
                self.assertIsInstance(call.args[2], Evaluation)
                self.assertIs(call.args[2].records, call.args[0])
                self.assertEqual(json.loads(call.args[2].read(assignment['context']['native_packet']))['subject'],
                                 dict(statement='Every synthetic solver objective has a finite witness.'))
            if command == 'status':
                progress = report['review']['assignments'][assignment['id']]['review_parts']
            else:
                self.assertEqual(report['errors'], 0)
                path = self.case.root / 'intermediate/reviews/native_math/solver-parts/series.json'
                series = json.loads(path.read_text(encoding='utf-8'))
                progress = series['turns'][0]['review_parts']
            self.assertTrue(progress['next'])
            self.assertEqual(progress['next'][0]['scope']['assignment_id'], assignment['id'])


if __name__ == '__main__':
    unittest.main()
