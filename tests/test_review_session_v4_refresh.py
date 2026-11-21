"""The refresh of a problem review that a chain of the session rules v4 recorded (design session-v4-refresh, rules F1
to F8). The refresh is a new chain in the slot of the earlier chain, which its context names in session_refresh."""

import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from codex_route_fixtures import invoke_codex, record_codex_route
import test_review_session_v4_pages as pages_fixtures
import test_review_session_v4_turns as turn_fixtures
from research_harness import bar_sources, output_views, review_protocol as protocol
from research_harness import review_continuation as continuation, review_session_v4 as session
from research_harness import research_decisions as decisions
from research_harness.errors import ResearchError
from research_harness.review_bodies import read_history


class SessionRefreshTests(unittest.TestCase):
    setUp = turn_fixtures.SessionTurnsTests.setUp
    read_packet = turn_fixtures.SessionTurnsTests.read_packet
    add_sources = turn_fixtures.SessionTurnsTests.add_sources
    respond = turn_fixtures.SessionTurnsTests.respond
    finish = turn_fixtures.SessionTurnsTests.finish
    build_final = turn_fixtures.SessionTurnsTests.build_final
    record_legacy_bars = turn_fixtures.SessionTurnsTests.record_legacy_bars
    install_measured_profile = turn_fixtures.SessionTurnsTests.install_measured_profile

    def record_bar(self):
        """A bar that a chain of the rules v4 recorded: a request turn, a delivery turn and the final."""
        final, unit = self.build_final()
        self.finish(final, [dict(ref=unit['handle'])])
        return final

    def add_area(self):
        self.case.put('problem_area', 'aux', dict(id='aux', area='math.AG', reason='A further area.'))

    def plan(self, head_id):
        return continuation.plan_next(self.case.read_records(), self.case.artifacts, head_id)

    def record(self, payload):
        return self.case.mutate(protocol.record_assignment, payload)['result']

    def read_logical(self, assignment):
        return protocol._read_json(self.case.artifacts, assignment['session_chain']['logical'])

    def assign_refresh(self, final):
        """Record the payload that the plan of the head gives after a change of the record."""
        plan = self.plan(final['id'])
        self.assertEqual((plan['state'], plan.get('command')), ('continue', 'review-assignment'), plan)
        self.assertEqual(plan['payload']['context'], {'session_refresh': {'assignment_id': final['id']}})
        return self.record(plan['payload'])

    def drive(self, head, requests=True):
        """Answer the request turns of a v4 chain with the stub until its final turn; the first turn requests the full
        text of the first source when requests is true. Returns the final turn, not yet answered."""
        current = head
        _, inventory, _ = session.read_bound_session(self.case.artifacts, head)
        source = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
        wanted = [dict(id='source', kind='source', version_id=source['identity']['version_id'], depth='fulltext')]
        for number in range(20):
            if 'session_final' in self.read_packet(current):
                return current
            self.respond(current, wanted if requests and current is head else ())
            current = self.case.assign(head['role'], head['reviewer_id'], identifier=head['id'] + '-n%d' % number,
                                       prior_assignment_id=current['id'])
        self.fail('No final was reached')

    def make_stale(self, route_id):
        """The route record pins another reviewer runtime than the installed one."""
        from research_harness import review_transport as transport
        installed = transport.runtime

        def read_runtime(route):
            runtime = installed(route)
            return dict(runtime, implementation_sha256='0' * 64) if route['id'] == route_id else runtime
        stale = patch.object(transport, 'runtime', side_effect=read_runtime)
        stale.start()
        self.addCleanup(stale.stop)

    def assert_new_chain_in_slot(self, first, final):
        records = self.case.read_records()
        self.assertEqual(first['session_chain']['first_assignment_id'], first['id'])
        self.assertEqual(first['session_chain']['turn'], 1)
        self.assertEqual((first['session_id'], first['history_assignment_id']), (first['id'], None))
        self.assertEqual(first['slot_id'], protocol.context_repair.slot_id(records, final))

    def test_f1_bar_refresh_after_an_added_area_reaches_its_recorded_review(self):
        """Driven: the refresh of a v4 bar runs as a new v4 chain of three turns to its recorded review (2 to 3 s)."""
        final = self.record_bar()
        self.add_area()
        first = self.assign_refresh(final)
        self.assert_new_chain_in_slot(first, final)
        logical = self.read_logical(first)
        self.assertEqual(logical['problem_refresh'], {'assignment_id': final['id']})
        self.assertIn('math.AG', [row['area'] for row in logical['problem_areas']])
        self.assertEqual([entry['id'] for entry in logical['historical_reviews']], ['review-' + final['id']])
        self.assertNotIn('continuation', logical)
        self.assertIs(first['prompt']['parameters'][session.REFRESH_PARAMETER], True)
        self.assertIn(session.SESSION_REFRESH_PROMPT, first['prompt']['text'])
        current = self.drive(first)
        saved = self.finish(current)
        self.assertEqual(self.plan(current['id'])['state'], 'done')
        records = self.case.read_records()
        adoptions = output_views._find_adoptions(records, SimpleNamespace(adoptions={}))
        self.assertEqual(output_views._name_link(records, records['review_assignment'][first['id']], [], adoptions),
                         'problem_refresh')
        current_reviews, _ = output_views._find_current_value_reviews(records)
        self.assertEqual(sorted(current_reviews), [saved['id']])
        turns = [row for row in records['review_assignment'].values()
                 if row.get('session_chain', {}).get('first_assignment_id') == first['id']]
        self.assertEqual(len(turns), 3)
        for turn in turns:
            attempt, _ = protocol.read_assignment_output(records, self.case.artifacts, turn['id'])
            self.assertTrue(protocol.authenticate_attempt(records, self.case.artifacts, turn, attempt), turn['id'])

    def test_f2_slate_refresh_after_an_added_candidate_states_both_candidate_sets(self):
        self.record_legacy_bars()
        self.add_sources([2000])
        head = self.case.assign('slate', 'first', identifier='slate')
        final = self.drive(head)
        self.finish(final)
        before = self.read_logical(final)
        self.case.put('problem_candidate', 'candidate-extra', dict(id='candidate-extra', previous=None,
            statement='A further candidate statement.', source=dict(kind='native', reference='notes', evidence=[]),
            observation='An observation.', neighbours=[], potential='A potential.', notes='', evidence=[]))
        first = self.assign_refresh(final)
        self.assert_new_chain_in_slot(first, final)
        refresh = self.read_logical(first)['problem_refresh']
        self.assertEqual(refresh['previous_candidate_set_digest'], before['candidate_set_digest'])
        self.assertNotEqual(refresh['candidate_set_digest'], before['candidate_set_digest'])
        self.assertEqual((refresh['previous_bar_ids'], refresh['bar_ids']), (before['bar_ids'], before['bar_ids']))
        self.assertEqual(refresh['review_ids'], ['review-' + final['id']])
        logical = self.read_logical(first)
        self.assertEqual([row['assignment_id'] for row in logical['own_bars']], ['bar-first'])
        self.assertEqual(len(logical['bars']), 2)

    def test_f3_result_refresh_after_a_changed_currency_states_both_currencies(self):
        self.record_legacy_bars()
        self.install_measured_profile()
        self.add_sources([2000])
        head = self.case.assign('result', 'first', identifier='result')
        final = self.drive(head)
        self.finish(final)
        before = self.read_logical(final)
        self.case.expected['value']['motivation'].append(dict(text='A later motivation.', evidence=[]))
        first = self.assign_refresh(final)
        self.assert_new_chain_in_slot(first, final)
        refresh = self.read_logical(first)['problem_refresh']
        self.assertEqual(refresh['previous_problem_review_currency'], before['problem_review_currency'])
        self.assertEqual(refresh['previous_problem_evidence_digest'], before['problem_evidence_digest'])
        self.assertNotEqual(refresh['problem_review_currency'], before['problem_review_currency'])
        self.assertNotEqual(refresh['problem_evidence_digest'], before['problem_evidence_digest'])
        self.assertEqual(refresh['review_ids'], ['review-' + final['id']])

    def test_f4_below_the_floor_the_stop_gives_one_new_chain_step_on_each_serving_route(self):
        final = self.record_bar()
        self.add_area()
        record_codex_route(self.case, 'small', model='gpt-6-astra', output_tokens=4000)
        payload = dict(self.plan(final['id'])['payload'], route_id='small')
        with self.assertRaises(ResearchError) as raised:
            self.record(payload)
        error = raised.exception
        self.assertEqual(error.code, 'review_route_capacity_below_floor')
        self.assertEqual(error.details['reason'], session.SESSION_BELOW_FLOOR_REASON)
        self.assertEqual([row['route_id'] for row in error.details['serving_routes']], ['route'])
        steps = [(answer.get('route_id'), [(step['id'], step['command']) for step in answer['steps']])
                 for answer in error.details['then']]
        self.assertEqual(steps, [('route', [(14, 'review-assignment')])])
        step = error.details['then'][0]['steps'][0]
        self.assertEqual(step['payload'], dict(payload, route_id='route'))
        first = self.record(step['payload'])
        self.assert_new_chain_in_slot(first, final)

    def test_f4_an_empty_context_takes_the_refresh_link_and_one_review_takes_one_refresh(self):
        final = self.record_bar()
        self.add_area()
        payload = dict(id='again', route_id='route', dossier_id=None, role='bar', reviewer_id='first',
                       author_id='author', context={})
        first = self.record(payload)
        self.assertEqual(first['context'], {'session_refresh': {'assignment_id': final['id']}})
        self.assert_new_chain_in_slot(first, final)
        refresh = dict(payload, id='second', context={'session_refresh': {'assignment_id': final['id']}})
        self.case.assert_error('review_source_continuation_fork', lambda: self.record(refresh))
        other = dict(refresh, id='other', author_id='coauthor')
        self.case.assert_error('review_source_continuation_mismatch', lambda: self.record(other))

    def test_f5_refresh_of_a_bar_of_the_rules_v3_keeps_its_bytes(self):
        legacy = patch.object(protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        legacy.start()
        self.addCleanup(legacy.stop)
        self.add_sources([2000])
        context = self.case.deliver_supporting('first')
        bar = self.case.assign('bar', 'first', identifier='v3-bar', **context)
        output = self.case.build_review_output('bar')
        invoke_codex(self.case, bar['id'], output)
        self.case.mutate(decisions.record_value_review, dict(output, id='review-v3-bar', dossier_id=None,
                                                            assignment_id=bar['id']))
        self.add_area()
        plan = self.plan(bar['id'])
        self.assertEqual(plan['payload']['context'], {'prior_assignment_id': bar['id']})
        saved = self.record(plan['payload'])
        self.assertIn('continuation', self.read_packet(saved))
        self.assertEqual(len(read_history(self.case.artifacts, saved)), 2)
        # The packet digest and the prompt hash that the base d3d6aab7 gives for this fixture.
        self.assertEqual((saved['packet_digest'], saved['prompt_hash']),
                         ('cda972e27a0ba934bd62875d09a70b93f7d9029df7ad90afcc4b19f4ab7f94b6',
                          '401b85c3c6ee50d7267e61c0264cfcf5674fd86f55062ba518ee2473a9cce0d8'))

    def test_f6_a_turn_of_the_refresh_chain_authenticates_only_with_its_own_logical_packet(self):
        final = self.record_bar()
        self.add_area()
        first = self.assign_refresh(final)
        self.respond(first)
        records = self.case.read_records()
        assignment = records['review_assignment'][first['id']]
        attempt, _ = protocol.read_assignment_output(records, self.case.artifacts, first['id'])
        self.assertTrue(protocol.authenticate_attempt(records, self.case.artifacts, assignment, attempt))
        logical = copy.deepcopy(self.read_logical(assignment))
        logical['problem_refresh']['assignment_id'] = 'another'
        forged = copy.deepcopy(assignment)
        forged['session_chain']['logical'] = protocol._put_json(self.case.artifacts, logical)
        forged_records = dict(records, review_assignment=dict(records['review_assignment'], **{first['id']: forged}))
        self.assertFalse(self._authenticates(forged_records, forged, attempt))

    def _authenticates(self, records, assignment, attempt):
        try:
            return protocol.authenticate_attempt(records, self.case.artifacts, assignment, attempt)
        except ResearchError as error:
            self.assertEqual(error.code, 'review_invocation_unverified')
            return False

    def test_f7_a_final_of_the_refresh_chain_cannot_cite_a_text_that_only_the_earlier_chain_delivered(self):
        from research_harness.review_failure_class import plan_after_failure
        versions = self.add_sources([2000, 2000])
        current = self.case.assign('bar', 'first', identifier='two')
        self.respond(current, [dict(id='a', kind='source', version_id=versions[0], depth='fulltext'),
                               dict(id='b', kind='source', version_id=versions[1], depth='fulltext')])
        for number in range(10):
            current = self.case.assign('bar', 'first', identifier='two-n%d' % number, prior_assignment_id=current['id'])
            if 'session_final' in self.read_packet(current):
                break
            self.respond(current)
        _, inventory, state = session.read_bound_session(self.case.artifacts, current)
        first_text, second_text = [row for row in inventory['units'] if row['kind'] == 'fulltext']
        self.assertEqual({first_text['handle'], second_text['handle']} - {row['unit'] for row in state['deliveries']},
                         set())
        output = self.case.build_review_output('bar')
        output['unexpected'] = 'refused'
        invoke_codex(self.case, current['id'], output)
        step = plan_after_failure(self.case.read_records(), self.case.artifacts, current['id'])
        replacement = self.record(step['payload'])
        self.finish(replacement, [dict(ref=first_text['handle'])])
        self.add_area()
        final = self.drive(self.assign_refresh(replacement), requests=False)
        output = self.case.build_review_output('bar')
        output['value'].update(status='unresolved', evidence=[second_text['artifact']])
        invoke_codex(self.case, final['id'], output)
        state = protocol.assignment_state(self.case.read_records(), self.case.artifacts, final['id'])
        self.assertFalse(state['ready'])
        refusal = self.case.read_records()['review_output_refusal']['attempt-' + final['id']]
        self.assertEqual((refusal['code'], refusal['check']),
                         ('review_evidence_undelivered', 'review.evidence_delivery'))

    def test_f8_on_a_stale_route_record_the_plan_names_the_current_record_of_the_same_identity(self):
        final = self.record_bar()
        self.add_area()
        record_codex_route(self.case, 'route-2', model='gpt-6-astra', output_tokens=128000)
        self.make_stale('route')
        plan = self.plan(final['id'])
        self.assertEqual((plan['state'], plan['payload']['route_id']), ('continue', 'route-2'), plan)
        first = self.record(plan['payload'])
        self.assert_new_chain_in_slot(first, final)
        self.assertEqual(protocol.retired_routes(self.case.read_records()), set())

    def test_f8_without_a_current_record_the_plan_is_the_renewal_step_then_the_series(self):
        final = self.record_bar()
        self.add_area()
        self.make_stale('route')
        plan = self.plan(final['id'])
        self.assertEqual((plan['state'], plan['code']), ('handoff', 'review_route_runtime_stale'), plan)
        self.assertEqual(plan['details']['renewal'], dict(admitted=True, route_id=None, lacks='record'))
        step = plan['details']['step']
        self.assertEqual((step['id'], step['command']), (7, 'review-route'))
        self.assertEqual(step['then'][-1], dict(command='review-series', assignment_id=final['id']))
        self.assertNotIn('steps', plan['details'])
        self.assertNotIn('context_repair', json.dumps(plan))

    def test_f9_under_the_next_rule_set_the_refresh_is_a_new_request_without_history(self):
        final = self.record_bar()
        self.add_area()
        payload = self.plan(final['id'])['payload']
        stop = dict(state='stop', protocol=session.PROTOCOL, reason='binary_required_evidence')
        with patch.object(session, 'check_v4_admission', return_value=stop):
            saved = self.record(payload)
        self.assertNotIn('session_chain', saved)
        self.assertEqual((saved['session_id'], saved['history_assignment_id']), (saved['id'], None))
        self.assertEqual(read_history(self.case.artifacts, saved), [])
        self.assertEqual(saved['slot_id'], protocol.context_repair.slot_id(self.case.read_records(), final))
        packet = self.read_packet(saved)
        self.assertEqual(packet['problem_refresh'], {'assignment_id': final['id']})
        self.assertNotIn('continuation', packet)

    def test_f10_a_turn_after_a_recorded_v4_final_stays_refused(self):
        final = self.record_bar()
        self.add_area()
        payload = dict(id='after-final', route_id='route', dossier_id=None, role='bar', reviewer_id='first',
                       author_id='author', context={'prior_assignment_id': final['id']})
        self.case.assert_error('review_resampling_forbidden', lambda: self.record(payload))


class PagedFinalRefreshTests(unittest.TestCase):
    setUp = turn_fixtures.SessionTurnsTests.setUp
    read_packet = turn_fixtures.SessionTurnsTests.read_packet
    respond = turn_fixtures.SessionTurnsTests.respond
    record_legacy_bars = turn_fixtures.SessionTurnsTests.record_legacy_bars
    start = pages_fixtures.SessionPagesTests.start
    answer = pages_fixtures.SessionPagesTests.answer
    finish_pages = pages_fixtures.SessionPagesTests.finish_pages
    add_area = SessionRefreshTests.add_area
    plan = SessionRefreshTests.plan
    record = SessionRefreshTests.record
    read_logical = SessionRefreshTests.read_logical
    assign_refresh = SessionRefreshTests.assign_refresh
    assert_new_chain_in_slot = SessionRefreshTests.assert_new_chain_in_slot

    def test_f1_f10_after_a_final_in_pages_a_turn_is_refused_and_the_refresh_is_a_new_chain(self):
        """A bar recorded in final pages (nine targets force the pages); its refresh ends at its first assignment."""
        final, _, _ = self.finish_pages(self.start())
        self.assertIn('final_page_plan', final['session_chain'])
        self.add_area()
        payload = dict(id='after-pages', route_id='route', dossier_id=None, role='bar', reviewer_id='first',
                       author_id='author', context={'prior_assignment_id': final['id']})
        self.case.assert_error('review_resampling_forbidden', lambda: self.record(payload))
        first = self.assign_refresh(final)
        self.assert_new_chain_in_slot(first, final)
        self.assertEqual(self.read_logical(first)['problem_refresh'], {'assignment_id': final['id']})


if __name__ == '__main__':
    unittest.main()
