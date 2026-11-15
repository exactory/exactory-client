"""Entry codes have reviewed named-cause endings and executable inspections."""

import json
from pathlib import Path
import unittest
from unittest.mock import patch

from ci import series_code_reach as reach
from ci.series_code_groups import read_group_entries
from research_harness import cli, review_series, stop_classes
from research_harness.errors import ResearchError
import test_review_continuation as fixtures
from test_series_code_reach import record_reachable_errors


ROOT = Path(__file__).resolve().parents[1]
GROUPS = ROOT / 'tests/fixtures/series_code_groups/d2-rows-close-entry.json'


class EntryRowTests(unittest.TestCase):
    def prepare_fixture(self):
        fixture = fixtures.ContinuationTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        return fixture

    def run_refusal(self, fixture, code):
        def refuse(store, payload, **identity):
            raise ResearchError(code, 'The entry names its cause.', {'entry': 'synthetic'})

        rows = []
        with patch.dict(cli.OPERATIONS, {'review-run': refuse}):
            result = review_series.run_series(fixture.case.store, fixture.assignment['id'], emit=rows.append)
        self.assertEqual(result, 3)
        return rows[-1]

    def test_every_entry_member_ends_with_the_named_cause_decision_inside_the_recorder(self):
        codes = stop_classes._NAMED_CAUSE_CODES_ENTRY
        self.assertEqual(len(codes), 145)
        self.assertEqual(len(set(codes)), len(codes))
        fixture = self.prepare_fixture()
        workspace = str(fixture.case.store.root)
        expected = dict(owner_decides='How should the cause that the refusal names be answered?', then=[
            dict(answer='Check the retained record', steps=[dict(command='record-check',
                arguments={'--workspace': workspace}, payload={}, chooses=[], prerequisites=[])]),
            dict(answer='List what the workspace owes', steps=[dict(command='status',
                arguments={'--workspace': workspace})])])
        with record_reachable_errors('run_series') as observed:
            for code in codes:
                with self.subTest(code=code):
                    line = self.run_refusal(fixture, code)
                    self.assertEqual((line['state'], line['code'], line['stop_class']),
                                     ('stop', code, 'decision'))
                    self.assertEqual(stop_classes.STOP_REGISTRY[code]['group'], 'named_cause')
                    self.assertNotIn('unlisted', line['details'])
                    self.assertEqual(line['details']['decision'], expected)
                    self.assertEqual(line['error'], dict(code=code, message='The entry names its cause.',
                                                        details={'entry': 'synthetic'}))
        self.assertTrue(set(codes) <= set(observed))
        self.assertEqual(fixture.case.read_records().get('review_attempt', {}), {})

    def test_both_printed_answers_run_on_a_supported_retained_state(self):
        fixture = self.prepare_fixture()
        with record_reachable_errors('run_series'):
            line = self.run_refusal(fixture, 'review_task_exhausted')
        answers = line['details']['decision']['then']
        self.assertEqual([answer['steps'][0]['command'] for answer in answers], ['record-check', 'status'])
        before = fixture.case.store.snapshot()
        for answer in answers:
            self.assertEqual(len(answer['steps']), 1)
            step = answer['steps'][0]
            with self.subTest(command=step['command']):
                self.assertEqual(step.get('payload', {}), {})
                self.assertEqual(step.get('chooses', []), [])
                argv = [step['command']]
                for option, value in step['arguments'].items():
                    argv.extend([option, value])
                with record_reachable_errors(step['command']):
                    report = cli.run(cli.build_parser().parse_args(argv))
                if step['command'] == 'record-check':
                    self.assertIn('steps', report)
                    self.assertIn('ready', report)
                else:
                    self.assertIn('obligations', report)
                    self.assertIn('next', report)
        self.assertEqual(fixture.case.store.snapshot(), before)

    def test_entry_evidence_passes_writing_place_and_condition_group_guards(self):
        self.assertTrue(GROUPS.is_file(), 'Entry membership needs its writing-place evidence.')
        members = json.loads(GROUPS.read_text())['named_cause']['members']
        self.assertEqual(set(members), set(stop_classes._NAMED_CAUSE_CODES_ENTRY))
        self.assertEqual(len(members), 145)
        self.assertEqual(sum(len(sites) for sites in members.values()), 202)
        for code, sites in members.items():
            for site, conditions in sites.items():
                with self.subTest(code=code, site=site):
                    self.assertEqual(conditions, ['entry'])
        report = reach.analyze_repository(ROOT)
        groups = read_group_entries(ROOT, report, stop_classes.STOP_REGISTRY, stop_classes.STOP_GROUPS)
        self.assertEqual({code: groups['named_cause']['members'][code] for code in members}, members)
        self.assertFalse(set(members) & set(reach.build_unreviewed_rows(report, stop_classes.STOP_REGISTRY)))


if __name__ == '__main__':
    unittest.main()
