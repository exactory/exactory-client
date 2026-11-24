"""Per-place conditions and executable inspection decisions for the conditions lane."""

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from ci import series_code_reach as reach
from ci.series_code_groups import read_group_entries
from research_harness import cli, review_series, stop_classes
from research_harness.errors import ResearchError
import test_review_continuation as continuation_fixtures
from test_series_code_reach import record_reachable_errors


ROOT = Path(__file__).resolve().parents[1]
MEMBERS = ROOT / 'tests/fixtures/series_code_groups/d2-rows-close-conditions.json'


class ConditionsRowTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def prepare_case(self):
        fixture = continuation_fixtures.ContinuationTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        return fixture

    def run_refused_series(self, fixture, code):
        def refuse_operation(store, payload, **identity):
            raise ResearchError(code, 'The operation names its refused condition.', {'condition': 'synthetic'})

        lines = []
        with record_reachable_errors('run_series') as observed, patch.dict(
                cli.OPERATIONS, {'review-run': refuse_operation}):
            result = review_series.run_series(fixture.case.store, fixture.assignment['id'], emit=lines.append)
        self.assertEqual(result, 3, lines)
        self.assertIn(code, observed)
        terminal = lines[-1]
        self.assertEqual((terminal['state'], terminal['code'], terminal['stop_class']),
                         ('stop', code, 'decision'))
        self.assertEqual(terminal['error']['details'], {'condition': 'synthetic'})
        self.assertNotIn('unlisted', terminal['details'])
        return terminal

    def require_group_decisions(self, group, codes):
        self.assertTrue(codes)
        members = json.loads(MEMBERS.read_text())[group]['members']
        self.assertEqual(set(codes), set(members))
        fixture = self.prepare_case()
        before = copy.deepcopy(fixture.case.read_records())
        packet = fixture.case.artifacts.read(fixture.assignment['packet'])
        expected = copy.deepcopy(stop_classes.STOP_GROUPS[group]['next'])
        expected.pop('kind')
        for index, answer in enumerate(expected['then']):
            for step in answer['steps']:
                step['arguments'] = {'--workspace': str(fixture.case.store.root)}
                if index == 0:
                    step.update(payload={}, chooses=[], prerequisites=[])
        for code in codes:
            with self.subTest(code=code):
                self.assertEqual(stop_classes.STOP_REGISTRY[code]['group'], group)
                terminal = self.run_refused_series(fixture, code)
                self.assertEqual(terminal['details']['decision'], expected)
        after = fixture.case.read_records()
        for kind, records in before.items():
            for key, record in records.items():
                self.assertEqual(after[kind][key], record)
        self.assertEqual(fixture.case.artifacts.read(fixture.assignment['packet']), packet)

    def test_every_named_cause_member_ends_with_the_group_decision(self):
        self.require_group_decisions('named_cause', stop_classes._NAMED_CAUSE_CODES_CONDITIONS)

    def test_every_stored_state_member_ends_with_the_group_decision(self):
        self.require_group_decisions('stored_state', stop_classes._STORED_STATE_CODES_CONDITIONS)

    def require_executable_answers(self, group, codes, commands):
        self.assertTrue(codes)
        fixture = self.prepare_case()
        self.assertEqual(stop_classes.STOP_REGISTRY[codes[0]]['group'], group)
        terminal = self.run_refused_series(fixture, codes[0])
        answers = terminal['details']['decision']['then']
        self.assertEqual([step['command'] for answer in answers for step in answer['steps']], commands)
        for answer in answers:
            for step in answer['steps']:
                arguments = [step['command']]
                for key, value in step['arguments'].items():
                    arguments.extend((key, str(value)))
                self.assertEqual((step.get('payload', {}), step.get('chooses', []),
                                  step.get('prerequisites', [])), ({}, [], []))
                with record_reachable_errors(step['command']):
                    report = cli.run(cli.build_parser().parse_args(arguments))
                self.assertIsInstance(report, dict)
                if step['command'] == 'status':
                    self.assertIn('obligations', report)
                    self.assertIn('next', report)
                else:
                    self.assertIn('series_stops', report)

    def test_named_cause_answers_execute_the_printed_inspections(self):
        self.require_executable_answers('named_cause', stop_classes._NAMED_CAUSE_CODES_CONDITIONS,
                                        ['record-check', 'status'])

    def test_stored_state_answer_executes_the_printed_inspection(self):
        self.require_executable_answers('stored_state', stop_classes._STORED_STATE_CODES_CONDITIONS,
                                        ['record-check'])

    def test_guard_accepts_every_recorded_place_and_derives_its_group(self):
        self.assertTrue(MEMBERS.is_file())
        evidence = json.loads(MEMBERS.read_text())
        report = reach.analyze_repository(ROOT)
        groups = read_group_entries(ROOT, report, stop_classes.STOP_REGISTRY, stop_classes.STOP_GROUPS)
        for group, entry in evidence.items():
            self.assertTrue(entry['members'])
            for code, sites in entry['members'].items():
                with self.subTest(code=code):
                    self.assertEqual(groups[group]['members'][code], sites)
                    self.assertEqual(set(report['code_sites'][code]), set(sites))
                    conditions = {condition for values in sites.values() for condition in values}
                    stored = set(stop_classes.STOP_GROUPS['stored_state']['conditions'])
                    self.assertEqual(group, 'stored_state' if conditions <= stored else 'named_cause')


if __name__ == '__main__':
    unittest.main()
