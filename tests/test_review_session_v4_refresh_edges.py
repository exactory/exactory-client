"""T-E1 to T-E9 for retired routes and context repair across a v4 review refresh."""

import copy
import importlib
import shutil
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from codex_route_fixtures import build_codex_response, invoke_codex, record_codex_route
import test_review_session_v4_refresh as refresh_fixtures
from test_review_session_v4_turns import ProbeClock

from research_harness import bar_sources, output_views, research_decisions as decisions
from research_harness import review_parts_repair, review_protocol as protocol, review_route_plan
from research_harness import review_session_v4 as session, review_transport
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.review_plan_steps import describe_step
from research_harness.review_series import run_series
from research_harness.stop_classes import describe_terminal
from research_harness.storage import Store


class EdgeFixture(unittest.TestCase):
    setUp = refresh_fixtures.SessionRefreshTests.setUp
    add_sources = refresh_fixtures.SessionRefreshTests.add_sources
    add_area = refresh_fixtures.SessionRefreshTests.add_area
    assign_refresh = refresh_fixtures.SessionRefreshTests.assign_refresh
    assert_new_chain_in_slot = refresh_fixtures.SessionRefreshTests.assert_new_chain_in_slot
    build_final = refresh_fixtures.SessionRefreshTests.build_final
    finish = refresh_fixtures.SessionRefreshTests.finish
    plan = refresh_fixtures.SessionRefreshTests.plan
    read_logical = refresh_fixtures.SessionRefreshTests.read_logical
    read_packet = refresh_fixtures.SessionRefreshTests.read_packet
    record = refresh_fixtures.SessionRefreshTests.record
    respond = refresh_fixtures.SessionRefreshTests.respond

    @contextmanager
    def fork(self, seed=None):
        case = self.case
        with tempfile.TemporaryDirectory(dir=case.root.parent) as directory:
            root = Path(directory) / 'record'
            shutil.copytree(seed or case.root, root)
            self.case = copy.copy(case)
            self.case.root, self.case.store, self.case.artifacts = root, Store(root), ArtifactStore(root)
            self.case.sequence += self.case.store.revision
            try:
                yield
            finally:
                self.case = case

    def event(self, assignment, identifier, kind='contamination'):
        self.case.mutate(protocol.record_context_event, dict(id=identifier, assignment_id=assignment['id'],
            source='owner', kind=kind, reason='Fixture observation.', evidence=[self.case.supporting]))

    def route(self, identifier='new', timeout=5):
        with patch.object(protocol, 'datetime', ProbeClock):
            record_codex_route(self.case, identifier, model='gpt-6-astra', output_tokens=128000,
                               timeout_seconds=timeout)

    def repair(self, predecessor, step=None, route='new', identifier='repair'):
        if step is None:
            step = describe_step(self.case.read_records(), self.case.artifacts, predecessor, 1)
        payload = copy.deepcopy(step['payload'])
        payload.update(id=identifier, reviewer_id=predecessor['reviewer_id'], route_id=route)
        payload['context']['context_repair'].update(probe_id='probe-' + route, reason='Reassess the bound evidence.')
        return self.record(payload)

    def run_answer(self, assignment, answer):
        plan = self.plan(assignment['id'])
        self.assertEqual((plan['state'], plan['command']), ('run', 'review-run'), plan)
        with patch.object(review_transport, 'send_request', side_effect=lambda route, request, credential:
                          build_codex_response(route, request, answer)):
            self.case.mutate(protocol.invoke_assignment, plan['payload'], credential='fixture-only')
        return plan['payload']['id']

    def drive(self, first):
        """Use the planned run and continuation payloads on each request turn."""
        _, inventory, _ = session.read_bound_session(self.case.artifacts, first)
        unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
        wanted = [dict(id='s', kind='source', version_id=unit['identity']['version_id'], depth='fulltext')]
        current = first
        for number in range(3):
            if 'session_final' in self.read_packet(current):
                return current, unit
            self.run_answer(current, dict(schema_version=1, stage='request', notes='Checked.',
                requests=wanted if number == 0 else [], excerpts=[], settled_requests=[]))
            plan = self.plan(current['id'])
            self.assertEqual((plan['state'], plan['command']), ('continue', 'review-assignment'), plan)
            current = self.record(plan['payload'])
        self.fail('No final was reached')

    def final_answer(self, unit, binding=None):
        answer = self.case.build_review_output('bar')
        evidence = [dict(ref=unit['handle'])]
        answer['value'].update(status='unresolved', evidence=evidence)
        if binding:
            answer['reassessment'] = dict(assignment_id=binding['assignment_id'], findings=[
                dict(review_id=identifier, disposition='revised', reason='I rechecked the field standard.',
                     evidence=evidence) for identifier in binding['historical_review_ids']])
            answer['limitations'] = ['The field standard was reassessed.']
        return answer

    def record_final(self, assignment, answer):
        attempt = self.run_answer(assignment, answer)
        refusal = self.case.read_records().get('review_output_refusal', {}).get(attempt)
        if refusal:
            raise ResearchError(refusal['code'], refusal.get('details', {}).get('message', refusal['code']),
                                {'check': refusal['check']})
        plan = self.plan(assignment['id'])
        self.assertEqual((plan['state'], plan['command']), ('record', 'value-review'), plan)
        return self.case.mutate(decisions.record_value_review, plan['payload'])['result']

    def record_legacy_final(self, assignment, output):
        invoke_codex(self.case, assignment['id'], output)
        return self.case.mutate(decisions.record_value_review, dict(output, id='review-' + assignment['id'],
            dossier_id=None, assignment_id=assignment['id']))['result']

    def assert_refusal(self, code, callback):
        with self.assertRaises(ResearchError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def save(self, root):
        shutil.copytree(self.case.root, root)
        return root


class SessionRefreshEdgeTests(EdgeFixture):
    @classmethod
    def setUpClass(cls):
        # The series driver imports the CLI. Load it before the claim fixture installs its narrower API.
        importlib.import_module('research_harness.cli')
        seeds = tempfile.TemporaryDirectory()
        cls.addClassCleanup(seeds.cleanup)
        root = Path(seeds.name)
        fixture = EdgeFixture()
        try:
            fixture.setUp()
            fixture.add_sources([100])
            links = fixture.case.deliver_supporting('first')
            first = fixture.case.assign('bar', 'first', identifier='request', **links)
            cls.original, unit = fixture.drive(first)
            cls.original_review = fixture.finish(cls.original, [dict(ref=unit['handle'])])
            cls.original_seed = fixture.save(root / 'original')
            fixture.event(cls.original, 'early')
            fixture.route()
            repair = fixture.repair(cls.original)
            cls.binding = fixture.read_logical(repair)['context_repair']
            cls.repair_final, unit = fixture.drive(repair)
            cls.repaired_review = fixture.record_final(cls.repair_final, fixture.final_answer(unit, cls.binding))
            fixture.add_area()
            cls.repair_seed = fixture.save(root / 'repair')
            cls.refresh_root = fixture.assign_refresh(cls.repair_final)
            cls.refresh_seed = fixture.save(root / 'refresh')
            cls.refresh_final, cls.unit = fixture.drive(cls.refresh_root)
            cls.final_seed = fixture.save(root / 'final')
            # Seed the already recorded ordinary refresh for E7 independently of E2's answer contract.
            # On the base only this fixture binding would otherwise prevent exercising the readers.
            frozen = fixture.read_logical(cls.refresh_final)
            with patch.object(protocol.context_repair, 'frozen_reassessment_packet', return_value=frozen):
                cls.refresh_review = fixture.record_final(cls.refresh_final, fixture.final_answer(cls.unit))
            cls.recorded_seed = fixture.save(root / 'recorded')
        finally:
            fixture.doCleanups()

    def test_e1_retirement_handoff_offers_only_the_planned_refresh(self):
        """Run the retirement handoff and its new review chain through the recorded bar."""
        with self.fork(self.original_seed):
            self.route('different', timeout=7)
            self.event(self.original, 'retired', 'route_retired')
            self.add_area()
            plan = self.plan(self.original['id'])
            self.assertEqual((plan['state'], plan['code']), ('handoff', 'review_route_retired'), plan)
            step = plan['details']['step']
            self.assertEqual((step['id'], step['command'], step['chooses']), (14, 'review-assignment', ['route_id']))
            self.assertEqual(step['payload']['context'], {'session_refresh': {'assignment_id': self.original['id']}})
            self.assertNotIn('steps', plan['details'])
            self.assertNotIn('route_id', step['payload'])
            self.assertNotIn('step', describe_terminal('review_route_retired')['details']['next'])
            records = self.case.read_records()
            self.assertEqual(protocol.find_current_problem_bar_ids(records, self.case.artifacts),
                             [self.original_review['id']])
            self.assertEqual(review_route_plan.list_route_uses(self.original, plan), [])
            self.assertEqual(review_route_plan.apply_route_rule(records, self.case.artifacts, self.original, plan), plan)
            series_end = []
            run_series(self.case.store, self.original['id'], emit=series_end.append)
            self.assertEqual(series_end[-1]['details']['step']['payload'], step['payload'])
            with patch.object(protocol, 'is_route_stale', side_effect=lambda artifacts, route: route['id'] == 'route'):
                self.assertEqual(self.plan(self.original['id'])['details']['step'], step)
            first = self.record(dict(step['payload'], route_id='different'))
            self.assert_new_chain_in_slot(first, self.original)
            self.assertIn('math.AG', [area['area'] for area in self.read_logical(first)['problem_areas']])
            final, unit = self.drive(first)
            saved = self.record_final(final, self.final_answer(unit))
            self.assertEqual(self.plan(final['id'])['state'], 'done')
            self.assertEqual(protocol.find_current_problem_bar_ids(self.case.read_records(), self.case.artifacts),
                             [saved['id']])
            self.event(self.original, 'late')
            plan = self.plan(final['id'])
            self.assertEqual((plan['state'], plan['code']), ('handoff', 'review_context_contaminated'))
            self.assertEqual(plan['details']['step']['payload']['context']['context_repair']['event_ids'],
                             ['retired', 'late'])

    def test_e2_refresh_after_repair_records_an_ordinary_final(self):
        with self.fork(self.final_seed):
            logical = self.read_logical(self.refresh_final)
            self.assertNotIn('context_repair', logical)
            history = {row['id']: row for row in logical['historical_reviews']}
            self.assertEqual(set(history), {self.original_review['id'], self.repaired_review['id']})
            disposition = history[self.repaired_review['id']]['reassessment']
            self.assertEqual(disposition['assignment_id'], self.original['id'])
            self.assertEqual([(row['review_id'], row['disposition']) for row in disposition['findings']],
                             [(self.original_review['id'], 'revised')])
            recorded = self.record_final(self.refresh_final, self.final_answer(self.unit))
            files = output_views._build_value_reviews(self.case.read_records(), self.case.artifacts, None)
            statuses = {file.entry['id']: file.entry['status'] for file in files.values()}
            self.assertEqual(statuses, {recorded['id']: 'current', self.repaired_review['id']: 'superseded',
                                        self.original_review['id']: 'historical'})
            self.assertEqual(self.plan(self.refresh_final['id'])['state'], 'done')

    def test_e3_refresh_refuses_the_still_valid_old_reassessment(self):
        with self.fork(self.final_seed):
            answer = self.final_answer(self.unit, self.binding)
            error = self.assert_refusal('review_reassessment_mismatch', lambda: self.record_final(self.refresh_final, answer))
            self.assertEqual(error.details['check'], 'review.reassessment')

    def test_e4_late_predecessor_event_after_refresh_start_keeps_the_stale_repair(self):
        with self.fork(self.refresh_seed):
            self.event(self.original, 'late')
            plan = self.plan(self.refresh_root['id'])
            self.assertEqual((plan['state'], plan['code']), ('handoff', 'review_context_repair_stale'), plan)
            self.assertEqual(plan['details']['step']['payload']['context']['context_repair']['event_ids'], ['early', 'late'])

    def test_e5_legacy_repaired_refresh_keeps_its_packet_prompt_and_reassessment(self):
        with patch.object(protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,)):
            original = self.case.assign('bar', 'first', identifier='v3', **self.case.deliver_supporting('first'))
            output = self.case.build_review_output('bar')
            prior_review = self.record_legacy_final(original, output)
            self.event(original, 'exp')
            self.route()
            repaired = self.repair(original)
            answer = dict(output, reassessment=dict(assignment_id=original['id'], findings=[
                dict(review_id=prior_review['id'], disposition='confirmed', reason='Same supported judgment.',
                     evidence=[self.case.supporting])]))
            self.record_legacy_final(repaired, answer)
            self.add_area()
            refresh = self.record(self.plan(repaired['id'])['payload'])
            self.assertEqual(refresh['context'], {'prior_assignment_id': repaired['id']})
            self.assertEqual((refresh['packet_digest'], refresh['prompt_hash']), LEGACY_REFRESH_DIGESTS)
            self.assertIn('context_repair', self.read_packet(refresh))
            with self.fork():
                self.assert_refusal('review_reassessment_missing', lambda: self.record_legacy_final(refresh, output))
            saved = self.record_legacy_final(refresh, answer)
            self.assertEqual(saved['payload']['reassessment'], answer['reassessment'])

    def test_e6_fallback_v3_and_its_later_turn_owe_no_reassessment(self):
        with self.fork(self.repair_seed):
            with patch.object(session, 'check_v4_admission', return_value=dict(state='stop', protocol=session.PROTOCOL,
                              reason='binary_required_evidence')):
                first = self.assign_refresh(self.repair_final)
            self.assertNotIn('session_chain', first)
            output = self.case.build_review_output('bar')
            saved = self.record_legacy_final(first, output)
            self.assertNotIn('reassessment', saved['payload'])
            self.case.put('problem_area', 'third', dict(id='third', area='math.NT', reason='A third area.'))
            later = self.record(self.plan(first['id'])['payload'])
            self.assertEqual(later['context'], {'prior_assignment_id': first['id']})
            saved = self.record_legacy_final(later, output)
            self.assertNotIn('reassessment', saved['payload'])

    def test_e7_current_readers_agree_for_each_late_event_and_the_clean_control(self):
        for assignment, code in ((self.original, 'review_context_repair_stale'),
                                 (self.repair_final, 'review_context_contaminated'),
                                 (self.refresh_root, 'review_context_contaminated'), (None, None)):
            with self.subTest(event=assignment and assignment['id']), self.fork(self.recorded_seed):
                if assignment is not None:
                    self.event(assignment, 'late')
                records, artifacts = self.case.read_records(), self.case.artifacts
                final, review = self.refresh_final, self.refresh_review
                state = protocol.assignment_state(records, artifacts, final['id'])
                plan = self.plan(final['id'])
                current, historical = output_views._find_current_value_reviews(records)
                bars = protocol.find_current_problem_bar_ids(records, artifacts)
                selected = decisions._collect_problem_reviews(records, 'bar', final['subject'])
                self.assertEqual(records['value_review'][review['id']], review)
                self.assertEqual(records['value_review'][self.repaired_review['id']], self.repaired_review)
                self.assertIsNotNone(state['raw_output'])
                if assignment is None:
                    self.assertEqual((state['ready'], plan['state'], state['obligations']), (True, 'done', []))
                    self.assertEqual((current, bars, selected), ({review['id']}, [review['id']], [review['id']]))
                else:
                    self.assertEqual((state['ready'], state['context_status']), (False, 'contaminated'))
                    self.assertIn(code, [row['code'] for row in state['obligations']])
                    self.assertEqual((plan['state'], plan['code']), ('handoff', code), plan)
                    self.assertEqual(plan['details']['step']['payload']['context']['context_repair']['event_ids'],
                                     ['early', 'late'])
                    self.assertIn(review['id'], historical)
                    self.assertEqual((current, bars, selected), (set(), [], []))

    def test_e8_new_repair_disposes_the_whole_slot_then_refreshes(self):
        """Record both a new repair of the three earlier finals and the ordinary final of its next refresh."""
        with self.fork(self.recorded_seed):
            self.event(self.refresh_final, 'after-refresh')
            plan = self.plan(self.refresh_final['id'])
            self.route('newer', timeout=8)
            first = self.repair(self.refresh_final, plan['details']['step'], route='newer', identifier='repair-again')
            binding = self.read_logical(first)['context_repair']
            previous = {review['id'] for review in (self.original_review, self.repaired_review, self.refresh_review)}
            self.assertEqual(set(binding['historical_review_ids']), previous)
            final, unit = self.drive(first)
            repaired = self.record_final(final, self.final_answer(unit, binding))
            self.assertEqual({row['review_id'] for row in repaired['payload']['reassessment']['findings']}, previous)
            self.case.put('problem_area', 'third', dict(id='third', area='math.NT', reason='A third area.'))
            current, unit = self.drive(self.assign_refresh(final))
            saved = self.record_final(current, self.final_answer(unit))
            self.assertNotIn('reassessment', saved['payload'])
            self.assertEqual(self.plan(current['id'])['state'], 'done')

    def test_e9_incomplete_refresh_attempt_does_not_exhaust_the_earlier_repair(self):
        with self.fork(self.final_seed):
            records = self.case.read_records()
            first = self.refresh_root
            attempt = next(row for row in records['review_attempt'].values() if row['assignment_id'] == first['id'])
            incomplete = dict(attempt, status='incomplete', error=dict(code='review_incomplete'))
            records = dict(records, review_attempt={attempt['id']: incomplete}, review_invocation={})
            self.assertEqual((
                review_parts_repair.has_over_cap_attempt(records, first, first['slot_id'], self.original['id']),
                review_parts_repair.has_exhausted_over_cap_repair(records, self.case.artifacts, first)), (False, False))


LEGACY_REFRESH_DIGESTS = (
    'ad20d30481e2145080958cdaf1b6c266c0a61620fa54f0750b40af7a0d3c8c62',
    'b61fb42dbe8fa2892fbd4a1b3aa949890368a3f845ccee5898c1d8abcaea649b',
)
