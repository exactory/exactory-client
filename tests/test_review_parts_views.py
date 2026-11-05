"""Operator progress for fresh-input review series stays bounded and actionable."""

import copy
import json
import unittest
from unittest import mock

from codex_route_fixtures import limit_codex_input
from review_parts_fixtures import PartsResultCase
from research_harness import cli, report_views, review_parts as parts
from research_harness import review_protocol as protocol, review_transport as transport
from research_harness.review_problem_parts import _measure
from test_research_report_views import encoded_size, report_fixture


class PartsViewCase(PartsResultCase):
    def prepare_parts(self, count=6):
        self.oversized(count=0, limit=transport.CODEX_INPUT_LIMIT_CHARS)
        for index in range(count):
            search = dict(id='search-' + str(index), profile='research', purpose='openness',
                          queries=['bounded sequence'], cited_work_ids=[], found_work_ids=[], responses=[],
                          reason='x' * 24000)
            self.case.put('literature_search', search['id'], search)
            self.case.expected['openness_searches'].append(dict(search=search, sources=[]))
        records = self.case.read_records()
        packet = protocol.build_packet(records, self.case.artifacts, None, 'result', 'first')
        route = records['review_route']['route']

        def measure(candidate):
            prompt = protocol.canonical_prompt(
                'result', orchestration='problem_creation', excerpts=True,
                review_parts=candidate[parts.KEY])
            return _measure(route, transport.request_body(
                route, prompt['text'], candidate, [], order=()))

        inventory = parts.build_inventory(packet, 'problem_result')
        planned = parts.plan_turn(
            packet, inventory, [], series_id='part-1',
            input_limit=transport.CODEX_INPUT_LIMIT_CHARS, measure=measure,
            output_tokens=route['configuration']['max_output_tokens'])
        limit_codex_input(self, measure(planned) * 3 // 4)

    def read_progress(self, assignment):
        report = cli.status_report(self.case.store)
        row = report['review']['assignments'][assignment['id']]
        self.assertIn('review_parts', row)
        return report, row['review_parts']

    def request_original(self, assignment):
        header = self.packet(assignment)[parts.KEY]
        inventory = parts.read_inventory(self.case.artifacts, header['inventory'])
        for row in inventory['units']:
            original = dict(unit=row['handle'], item=True,
                            start=row['item_characters'] - 100, end=row['item_characters'])
            if not parts.covers_original(header['originals'], original, inventory):
                return dict(kind='requests', unit_requests=[
                    dict(original, why='Check the original.')], source_requests=[])
        self.fail('The final must leave an original to request')


class PartsStatusTests(PartsViewCase):
    def test_status_does_not_offer_to_rerun_an_observed_earlier_turn(self):
        self.prepare_parts()
        first = self.assign()
        self.invoke(first)
        second = self.assign('next-read', first['id'])
        self.invoke(second)
        pending = self.assign('pending-turn', second['id'])
        report = cli._review_status(self.case.read_records(), self.case.artifacts)
        earlier = report['assignments'][first['id']]['review_parts']
        self.assertEqual(earlier['next'][0]['workflow'], 'review-assignment')
        self.assertEqual(earlier['next'][0]['scope']['prior_assignment_id'], first['id'])
        latest = report['assignments'][pending['id']]['review_parts']
        self.assertEqual(latest['next'][0]['workflow'], 'review-run')
        self.assertEqual(latest['next'][0]['scope']['assignment_id'], pending['id'])

    def test_status_verifies_each_series_once_from_its_latest_turn(self):
        self.prepare_parts()
        turns, final = self.finish_series()
        self.record_final(final)
        records = self.case.read_records()
        with mock.patch.object(parts, 'verify_series', wraps=parts.verify_series) as verify, \
                mock.patch.object(report_views, 'describe_parts_turn',
                                  wraps=report_views.describe_parts_turn) as describe:
            report = cli._review_status(records, self.case.artifacts)
        self.assertEqual([call.args[2] for call in verify.call_args_list], [final['id']])
        descriptions = [call for call in describe.call_args_list
                        if call.args[0]['id'] in {turn['id'] for turn in turns + [final]}]
        self.assertEqual(len(descriptions), len(turns) + 1)
        self.assertTrue(all(call.args[2] is descriptions[0].args[2] for call in descriptions))
        self.assertTrue(report['assignments'][final['id']]['review_parts']['complete'])

    def test_status_and_next_follow_the_last_turn_then_run_its_successor(self):
        self.prepare_parts()
        first = self.assign()
        report, progress = self.read_progress(first)
        self.assertEqual((progress['adapter'], progress['kind'], progress['turn']),
                         ('problem_result', 'read', 1))
        self.assertEqual((progress['units_carded'], progress['units_remaining']), (0, 6))
        self.assertEqual(progress['next'][0]['workflow'], 'review-run')
        self.assertEqual(progress['next'][0]['scope']['assignment_id'], first['id'])
        self.invoke(first)
        report, progress = self.read_progress(first)
        delivered = len(self.packet(first)[parts.KEY]['delivered_items'])
        self.assertEqual((progress['units_carded'], progress['units_remaining']),
                         (delivered, 6 - delivered))
        self.assertEqual(progress['next'][0]['workflow'], 'review-assignment')
        self.assertEqual(progress['next'][0]['scope']['prior_assignment_id'], first['id'])
        self.assertEqual(report['next']['code'], 'review_parts_pending')
        hint = report_views.next_summary(report)['next_hint']
        self.assertEqual(hint['next_step']['scope']['prior_assignment_id'], first['id'])
        self.assertEqual(hint['review_parts']['units_carded'], delivered)
        self.assertEqual(hint['review_parts']['adapter'], 'problem_result')
        second = self.assign('next-read', first['id'])
        report, progress = self.read_progress(second)
        self.assertEqual(progress['next'][0]['workflow'], 'review-run')
        self.assertEqual(report['next']['assignment_id'], second['id'])
        summary = report_views.status_summary(report)['review_parts']
        self.assertEqual(summary['count'], 1)
        self.assertEqual(summary['examples'][0]['assignment_id'], second['id'])

    def test_final_request_stays_pending_and_ordinary_final_completes(self):
        self.prepare_parts()
        _, final = self.finish_series()
        self.invoke(final, self.request_original(final))
        _, progress = self.read_progress(final)
        self.assertFalse(progress['complete'])
        self.assertEqual(progress['next'][0]['scope']['prior_assignment_id'], final['id'])
        answered = self.assign('answered', final['id'])
        self.record_final(answered)
        _, progress = self.read_progress(answered)
        self.assertTrue(progress['complete'])
        self.assertEqual(progress['next'], [])
        self.assertEqual((progress['units_carded'], progress['units_remaining']), (6, 0))

    def test_failed_read_recommends_replacement_from_the_prior_turn(self):
        from codex_route_fixtures import build_codex_response
        self.prepare_parts()
        first = self.assign()
        self.invoke(first)
        second = self.assign('failed', first['id'])

        def incomplete(route, request, credential):
            response = build_codex_response(route, request, {})
            response['status'] = 'incomplete'
            return response

        with mock.patch.object(transport, 'send_request', side_effect=incomplete):
            protocol.invoke_assignment(
                self.case.store, dict(id='failed-run', assignment_id=second['id']),
                credential='fixture', expected_revision=self.case.store.revision,
                request_id='failed-run')
        with mock.patch('research_harness.review_failure_class.plan_after_failure',
                        return_value=dict(state='replace', code='fixture_failure', details={})):
            _, progress = self.read_progress(second)
        self.assertEqual(progress['next'][0]['workflow'], 'review-assignment')
        self.assertEqual(progress['next'][0]['scope']['prior_assignment_id'], first['id'])
        self.assertEqual(progress['next'][0]['state'], 'replace')
        self.assertEqual(progress['next'][0]['payload']['context'], dict(prior_assignment_id=first['id']))

    def test_unobserved_output_does_not_credit_units(self):
        self.prepare_parts()
        first = self.assign()
        self.invoke(first)
        records = copy.deepcopy(self.case.read_records())
        records['review_attempt']['attempt-' + first['id']]['origin'] = 'imported'
        progress = cli._review_status(records, self.case.artifacts)['assignments'][first['id']]
        self.assertIn('review_parts', progress)
        self.assertEqual(progress['review_parts']['units_carded'], 0)
        self.assertEqual(progress['review_parts'].get('blocked'), 'review_invocation_unverified')
        self.assertEqual(progress['review_parts']['next'], [])

    def test_a_broken_ancestor_does_not_credit_the_recorded_plan(self):
        self.prepare_parts()
        first = self.assign()
        self.invoke(first)
        second = self.assign('next-read', first['id'])
        self.assertGreater(second['review_parts_plan']['units_carded'], 0)
        records = copy.deepcopy(self.case.read_records())
        records['review_attempt']['attempt-' + first['id']]['origin'] = 'imported'
        progress = cli._review_status(records, self.case.artifacts)['assignments'][second['id']]['review_parts']
        self.assertEqual((progress['units_carded'], progress['units_remaining']), (0, 6))
        self.assertTrue(progress.get('blocked'))
        self.assertEqual(progress['next'], [])

    def test_missing_packet_is_reported_as_incomplete_progress(self):
        self.prepare_parts()
        assignment = self.assign()
        (self.case.root / assignment['packet']['path']).unlink()
        progress = cli._review_status(self.case.read_records(), self.case.artifacts)[
            'assignments'][assignment['id']]['review_parts']
        self.assertFalse(progress['complete'])
        self.assertEqual(progress['units_carded'], 0)
        self.assertGreater(progress['issues']['count'], 0)
        self.assertEqual(progress.get('blocked'), 'artifact_missing')
        self.assertEqual(progress['next'], [])

    def test_two_refusals_show_exhaustion_without_recommending_a_forbidden_run(self):
        self.prepare_parts()
        assignment = self.assign()
        output = self.output(assignment)
        output['cards'] = []
        for number in range(2):
            self.invoke(assignment, output, attempt='refused-' + str(number))
        report, progress = self.read_progress(assignment)
        self.assertEqual(progress.get('blocked'), 'review_slot_exhausted')
        self.assertEqual(progress['next'], [])
        self.assertEqual(report['next']['code'], 'review_parts_pending')
        action = report['next']['next'][0]
        self.assertEqual(action['state'], 'handoff')
        # A parts series is not a reassessment disposition, so its exhausted slot takes a new reviewer.
        self.assertEqual(action['command'], 'review-assignment')
        self.assertNotIn('payload', action)
        self.assertEqual(report_views.next_summary(report)['next_hint']['review_parts']['blocked'],
                         'review_slot_exhausted')
        self.assertEqual(report_views.status_summary(report)['review_parts']['examples'][0]['blocked'],
                         'review_slot_exhausted')

    def test_large_inventory_has_counts_without_unit_lists(self):
        self.prepare_parts(count=45)
        first = self.assign()
        report, progress = self.read_progress(first)
        self.assertEqual(progress['units_remaining'], 45)
        self.assertNotIn('units', progress)
        self.assertNotIn('u45', json.dumps(progress))
        for view in (report_views.status_summary(report), report_views.next_summary(report)):
            self.assertNotIn('search-44', json.dumps(view))
        self.assertLessEqual(len(json.dumps(report_views.status_summary(report)).encode()), 16 * 1024)

    def test_pending_parts_precede_the_missing_scientific_pair(self):
        rows = [dict(code='problem_review_pair_required'), dict(code='review_parts_pending')]
        self.assertEqual(report_views.order_obligations(rows)[0]['code'], 'review_parts_pending')

    def test_assignment_plan_reports_allowances_and_estimates(self):
        self.prepare_parts()
        first = self.assign()
        plan = first['review_parts_plan']
        self.assertEqual((plan['kind'], plan['turn']), ('read', 1))
        self.assertEqual((plan['units_carded'], plan['units_remaining']), (0, 6))
        self.assertGreater(plan['unit_allowance'], 0)
        self.assertGreater(plan['card_allowance'], 0)
        self.assertEqual(set(plan['remaining_turns']), set(parts.KINDS))
        self.assertTrue(all(set(value) == {'estimate'}
                            for value in plan['remaining_turns'].values()))


class PartsSummaryTests(unittest.TestCase):
    def test_next_parts_hint_stays_bounded_with_long_scope_ids(self):
        report = report_fixture()
        report['next'] = {key: '\x01' * 10000 for key in
                          ('version_id', 'work_id', 'collection_id', 'unit_id', 'explanation',
                           'adapter', 'series_assignment_id', 'blocked')}
        report['next'].update(code='review_parts_pending', kind='read', turn=1,
                              units_carded=0, units_remaining=10000, next=[])
        before = copy.deepcopy(report)
        self.assertLessEqual(encoded_size(report_views.next_summary(report)), 4096)
        self.assertEqual(report, before)

    def test_shared_body_is_credited_to_each_delivered_item(self):
        from review_parts_fixtures import make_packet
        packet = make_packet(0, body='One shared captured body.')
        reference = packet['evidence'][0]['artifact']
        packet['problem_evidence']['computations'] = [dict(id='computation', artifact=reference)]
        inventory = parts.build_inventory(packet, 'problem_result')
        units = parts.enumerate_units(packet, 'problem_result')
        plan = dict(units_carded=0, units_remaining=2, unit_allowance=1000,
                    card_allowance=4000, remaining_turns={})
        header = dict(protocol=parts.PROTOCOL, adapter='problem_result',
                      series_assignment_id='first', kind='read', turn=1, plan=plan)
        delivered = parts.project_packet(
            parts.build_frame(packet, units), units,
            [dict(unit='u1'), dict(unit='u2', item=True, start=0,
                                   end=units[1]['item_characters'], whole_item=True)], header,
            dict(cards=[], combinations=[], findings=[]))
        assignment = dict(id='first', review_parts=header, review_parts_plan=plan,
                          recorded_revision=1, role='result', route_id='route',
                          reviewer_id='reviewer', author_id='author', dossier_id=None)
        progress = report_views.describe_parts_turn(
            assignment, dict(ready=True), dict(complete=False, packets=[delivered],
                delivery=dict(inventory=inventory), issues=dict(count=1, examples=[])))
        self.assertEqual((progress['units_carded'], progress['units_remaining']), (2, 0))

    def test_many_series_have_bounded_examples_and_omission_counts(self):
        report = report_fixture()
        report['review'] = dict(assignments={
            str(index): dict(review_parts=dict(
                series_assignment_id='\x01' * 1000 + str(index), adapter='\x01' * 1000,
                kind='read', turn=1, units_carded=0, units_remaining=100000,
                complete=False, recorded_revision=index))
            for index in range(60)})
        before = copy.deepcopy(report)
        summary = report_views.status_summary(report)
        self.assertIn('review_parts', summary)
        progress = summary['review_parts']
        self.assertEqual(progress['count'], 60)
        self.assertLessEqual(len(progress['examples']), 20)
        self.assertEqual(len(progress['examples']) + progress['omitted'], 60)
        self.assertLessEqual(encoded_size(summary), 16 * 1024)
        self.assertEqual(report, before)
