"""Record prerequisites retain their reviewed group decisions at a series ending."""

from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from ci import series_code_reach as reach
from ci.series_code_groups import read_group_entries
from research_harness import cli, review_series, stop_classes
from research_harness.errors import ResearchError
from test_series_code_reach import FIXTURE, record_reachable_errors
import test_review_continuation as fixtures


ROOT = Path(__file__).resolve().parents[1]
MEMBERS = ROOT / 'tests/fixtures/series_code_groups/d2-rows-close-record.json'


class RecordGroupTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ContinuationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.case = self.fixture.case

    def run_refusal(self, code, **kwargs):
        def refuse(store, payload, **identity):
            raise ResearchError(code, 'The retained prerequisite is unavailable.', {'reference': 'retained'})

        with patch.dict(cli.OPERATIONS, {'review-run': refuse}), record_reachable_errors('run_series') as observed:
            result = review_series.run_series(self.case.store, self.fixture.assignment['id'], **kwargs)
        self.assertEqual(result, 3)
        self.assertIn(code, observed)

    def require_group_decisions(self, group, codes, count):
        self.assertEqual(len(codes), count)
        members = json.loads(MEMBERS.read_text())[group]['members']
        self.assertEqual(set(codes), set(members))
        expected = copy.deepcopy(stop_classes.STOP_GROUPS[group]['next'])
        expected.pop('kind')
        for index, answer in enumerate(expected['then']):
            for step in answer['steps']:
                step['arguments'] = {'--workspace': str(self.case.store.root)}
                if index == 0:
                    step.update(payload={}, chooses=[], prerequisites=[])
        for code in codes:
            with self.subTest(code=code):
                rows = []
                self.run_refusal(code, emit=rows.append)
                last = rows[-1]
                self.assertEqual((last['state'], last['code'], last['stop_class']), ('stop', code, 'decision'))
                self.assertEqual(stop_classes.STOP_REGISTRY[code]['group'], group)
                self.assertNotIn('unlisted', last['details'])
                self.assertEqual(last['details']['decision'], expected)
                self.assertEqual(last['error']['code'], code)
                self.assertEqual(last['error']['details'], {'reference': 'retained'})
        self.assertEqual(self.case.read_records().get('review_attempt', {}), {})

    def test_each_named_cause_member_ends_with_the_group_decision(self):
        self.require_group_decisions('named_cause', stop_classes._NAMED_CAUSE_CODES_RECORD, 58)

    def test_each_stored_state_member_ends_with_the_group_decision(self):
        self.require_group_decisions('stored_state', stop_classes._STORED_STATE_CODES_RECORD, 1)

    def test_printed_named_cause_answers_execute_record_check_and_status(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.run_refusal('candidate_checkpoint_missing')
        last = json.loads(output.getvalue().splitlines()[-1])
        answers = last['details']['decision']['then']
        self.assertEqual([answer['steps'][0]['command'] for answer in answers], ['record-check', 'status'])
        for answer in answers:
            with self.subTest(answer=answer['answer']):
                self.assertEqual(len(answer['steps']), 1)
                step = answer['steps'][0]
                self.assertEqual((step.get('payload', {}), step.get('chooses', []),
                                  step.get('prerequisites', [])), ({}, [], []))
                argv = step['command'].split()
                for option, value in step['arguments'].items():
                    argv.extend((option, str(value)))
                report = cli.run(cli.build_parser().parse_args(argv))
                if step['command'] == 'record-check':
                    self.assertIsInstance(report['steps'], list)
                    stop = next(row for row in report['series_stops'] if row['id'] == last['stop_id'])
                    self.assertEqual(stop['code'], last['code'])
                    self.assertTrue(stop['listed'])
                    self.assertNotIn('finding', stop)
                else:
                    self.assertIsInstance(report['obligations'], list)
                    self.assertIsInstance(report['next'], dict)


class RecordGroupGuardTests(unittest.TestCase):
    def test_member_places_and_conditions_pass_rr5_and_determine_the_group(self):
        members = json.loads(MEMBERS.read_text())
        report = reach.analyze_repository(ROOT)
        merged = read_group_entries(ROOT, report, stop_classes.STOP_REGISTRY, stop_classes.STOP_GROUPS)
        fixture = json.loads(FIXTURE.read_text())
        self.assertEqual(merged, fixture['groups'])
        pending = {code: dict(row, sites=[fixture['site_vocabulary'][index] for index in row['sites']])
                   for code, row in fixture['unreviewed_rows'].items()}
        self.assertEqual(reach.build_unreviewed_rows(report, stop_classes.STOP_REGISTRY), pending)
        self.assertEqual(report['reachable'], fixture['reachable'])
        total = 0
        for group, entry in members.items():
            for code, sites in entry['members'].items():
                with self.subTest(code=code):
                    self.assertEqual(sites, merged[group]['members'][code])
                    self.assertEqual(set(sites), set(report['code_sites'][code]))
                    conditions = {condition for values in sites.values() for condition in values}
                    expected = ('stored_state' if conditions <= {'store', 'content', 'payload'} else 'named_cause')
                    self.assertEqual(group, expected)
                    total += len(sites)
        self.assertEqual(total, 172)


if __name__ == '__main__':
    unittest.main()
