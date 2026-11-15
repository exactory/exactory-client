"""Reviewed Review A boundaries retain refusals and execute their named next step."""

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from research_harness import cli, review_continuation, review_series, stop_classes
from research_harness import review_protocol as protocol
from research_harness.errors import ResearchError
import test_review_continuation as continuation_fixtures


GROUPS = Path(__file__).parent / 'fixtures/series_code_groups/d2-rows-review-a.json'


class ReviewARowTests(unittest.TestCase):
    def prepare_fixture(self, cls=continuation_fixtures.ContinuationTests):
        fixture = cls()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        return fixture

    def test_every_stored_content_code_stops_at_the_shared_record_check_row(self):
        fixture = self.prepare_fixture()
        self.assertEqual(len(stop_classes._STORED_CONTENT_CODES_REVIEW_A), 13)
        self.assertEqual(set(stop_classes._STORED_CONTENT_CODES_REVIEW_A),
                         set(json.loads(GROUPS.read_text())['stored_state']['members']))
        for code in stop_classes._STORED_CONTENT_CODES_REVIEW_A:
            with self.subTest(code=code):
                def refuse(store, payload, **identity):
                    raise ResearchError(code, 'The retained binding differs.', {'binding': 'synthetic'})
                rows = []
                with patch.dict(cli.OPERATIONS, {'review-run': refuse}):
                    result = review_series.run_series(fixture.case.store, 'bar-first', emit=rows.append)
                self.assertEqual(result, 3)
                terminal = rows[-1]
                self.assertEqual((terminal['state'], terminal['code'], terminal['stop_class']),
                                 ('stop', code, 'decision'))
                self.assertNotIn('unlisted', terminal['details'])
                self.assertEqual(stop_classes.STOP_REGISTRY[code]['group'], 'stored_state')
                self.assertEqual(terminal['error']['details'], {'binding': 'synthetic'})
                decision = stop_classes.describe_terminal('review_history_invalid')['details']['decision']
                decision['then'][0]['steps'] = [dict(command='record-check',
                    arguments={'--workspace': str(fixture.case.store.root)}, payload={}, chooses=[], prerequisites=[])]
                self.assertEqual(terminal['details']['decision'], decision)

    def test_output_contract_codes_keep_the_scientific_refusal_outcome_and_retry(self):
        codes = (
            'review_adjudication_mismatch', 'review_bar_mismatch', 'review_bar_pending',
            'review_composition_conflict', 'review_composition_pending', 'review_objection_history_incomplete',
            'review_parts_request_no_progress', 'review_reassessment_incomplete', 'review_reassessment_mismatch',
            'review_reassessment_missing', 'review_work_items_incomplete')
        for code in codes:
            with self.subTest(code=code):
                self.assertEqual(stop_classes.STOP_REGISTRY[code]['next'],
                                 dict(kind='outcome', outcome='scientific_refused'))
                fixture = self.prepare_fixture()
                fixture.invoke(fixture.case.build_review_output('bar'))
                before = fixture.plan()
                refusal = ResearchError(code, 'The observed output fails its frozen contract.')
                refusal.contract_check = 'review.value'
                with patch('research_harness.research_decisions.validate_review_contract', side_effect=refusal):
                    with self.assertRaises(ResearchError):
                        fixture.execute(before, request='refused-output')
                    saved = fixture.case.read_records()['review_output_refusal']['observed']
                    self.assertEqual(saved['code'], code)
                    after = fixture.plan()
                    self.assertEqual((after['state'], after['command']), ('run', 'review-run'))
                    self.assertNotEqual(after, before)
                    with patch('research_harness.review_transport.send_request', return_value=
                               fixture.case.build_provider_response(fixture.case.build_review_output('bar'))):
                        fixture.execute(after, request='retry-output')
                    self.assertGreater(len(fixture.case.read_records()['review_attempt']), 1)
                fixture.doCleanups()

    def test_mixed_output_codes_keep_pre_call_record_failures_at_record_check(self):
        from research_harness import review_plan_steps
        fixture = self.prepare_fixture()
        records = fixture.case.read_records()
        changed = copy.deepcopy(records)
        changed['workspace']['problem']['field'] = 'A different field'
        checks = (
            ('review_bar_pending', lambda: protocol.build_packet(
                records, fixture.case.artifacts, None, 'slate', 'first')),
            ('review_bar_mismatch', lambda: protocol.validate_problem_bars(
                changed, fixture.case.artifacts, [dict(id='bar', assignment_id='bar-first')])))
        for code, check in checks:
            with self.subTest(code=code), self.assertRaises(ResearchError) as raised:
                check()
            self.assertEqual(raised.exception.code, code)
            self.assertIsNone(getattr(raised.exception, 'contract_check', None))
            plan = review_plan_steps.complete_plan(records, fixture.case.artifacts,
                fixture.assignment, stop_classes.describe_terminal(code))
            self.assertEqual(plan['state'], 'stop')
            self.assertNotIn('unlisted', plan['details'])
            self.assertEqual(plan['details']['decision']['then'][0]['steps'], [{'command': 'record-check'}])
        self.assertEqual(fixture.case.read_records().get('review_output_refusal', {}), {})
        self.assertEqual(fixture.case.read_records().get('review_attempt', {}), {})

    def test_provider_failure_uses_the_provider_unavailable_outcome(self):
        fixture = self.prepare_fixture()
        self.assertEqual(stop_classes.STOP_REGISTRY['review_provider_failure']['next'],
                         dict(kind='outcome', outcome='provider_unavailable'))
        with patch('research_harness.review_transport.send_request', side_effect=ResearchError(
                'review_provider_failure', 'Synthetic non-success provider response.')):
            fixture.execute(fixture.plan())
        attempt = next(iter(fixture.case.read_records()['review_attempt'].values()))
        self.assertEqual(attempt['error']['code'], 'review_provider_failure')
        after = fixture.plan()
        self.assertEqual((after['state'], after['code']), ('wait', 'review_provider_unavailable'))
        self.assertNotIn('unlisted', after['details'])

    def test_pending_parts_hands_off_to_a_series_that_records_the_final(self):
        from codex_route_fixtures import build_codex_response
        from review_parts_fixtures import make_output
        from test_review_parts_adjudication_journey import AdjudicationPartsCase
        from research_harness import review_excerpts, review_plan_steps, review_parts
        fixture = self.prepare_fixture(AdjudicationPartsCase)
        fixture.oversized(count=1, paragraphs=2000)
        first = fixture.assign()
        self.assertNotEqual(fixture.packet(first)['review_parts']['kind'], 'final')
        fixture.invoke(first)
        with self.assertRaises(ResearchError) as raised:
            fixture.case.mutate(protocol.record_adjudication,
                dict(fixture.final_output(), id='premature', assignment_id=first['id']))
        self.assertEqual(raised.exception.code, 'review_parts_pending')
        before = review_plan_steps.complete_plan(fixture.case.read_records(), fixture.case.artifacts,
            first, stop_classes.describe_terminal(raised.exception.code))
        self.assertEqual((before['state'], before['command']), ('handoff', 'review-series'))
        def respond(route, request, credential):
            packet = review_excerpts.read_sent_packet(request)
            output = (fixture.final_output() if packet[review_parts.KEY]['kind'] == 'final'
                      else make_output(packet))
            return build_codex_response(route, request, output)
        with patch('research_harness.review_transport.send_request', side_effect=respond):
            result = review_series.run_series(fixture.case.store,
                before['details']['step']['payload']['assignment_id'], emit=lambda row: None)
        self.assertEqual(result, 0)
        after = review_continuation.plan_next(fixture.case.read_records(), fixture.case.artifacts, first['id'])
        self.assertEqual(after['state'], 'done')
        self.assertNotEqual(after, before)
        self.assertTrue(fixture.case.read_records().get('review_adjudication'))

    def test_group_fixture_names_every_member_site_and_condition(self):
        from ci import series_code_reach as reach
        group = json.loads(GROUPS.read_text())['stored_state']
        self.assertEqual(set(group['members']), set(stop_classes._STORED_CONTENT_CODES_REVIEW_A))
        sources = reach.read_sources(Path(__file__).resolve().parents[1])
        functions = reach.build_index(sources)['functions']
        for code, sites in group['members'].items():
            with self.subTest(code=code):
                self.assertEqual(stop_classes.STOP_REGISTRY[code]['group'], 'stored_state')
                self.assertTrue(sites)
                for site, condition in sites.items():
                    self.assertIn(site.rsplit('#', 1)[0], functions)
                    self.assertTrue(condition)
                    self.assertLessEqual(set(condition), {'store', 'content', 'payload'})
        self.assertEqual(set(json.loads(GROUPS.read_text())['stored_state']), {'members'})
