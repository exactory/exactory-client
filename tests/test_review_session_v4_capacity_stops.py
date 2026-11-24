"""The plans and stops of the session rules v4 at the capacity floor lead on: each answer is a step that the store
takes (findings S-F3 and S-F4 of the review of the merged tree of release 0.93.0)."""

import unittest
from unittest.mock import patch

from codex_route_fixtures import build_codex_response, record_codex_route
import test_research_problem_reviews as problem_fixtures
import test_review_session_v4_pages as pages
import test_review_session_v4_turns as turn_fixtures
from test_review_capacity_steps import CapacityAnswers
from research_harness import bar_sources, review_protocol as protocol, review_session_v4 as session
from research_harness.review_continuation import plan_next
from research_harness.review_failure_class import plan_after_failure


class SessionBelowFloorAnswersTests(CapacityAnswers, unittest.TestCase):
    """Rule V5 on the only route of a record: a new chain below the floor of the rules v4 stops before its first
    call, and the answers of the stop are the steps that raise the capacity of that route or add a route."""

    def setUp(self):
        self.case = problem_fixtures.ProblemReviewTests()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.case.supporting = self.case.artifacts.put(b'A synthetic field standard.', 'text/plain')
        rules = patch.object(protocol, 'NEW_SESSION_RULES', (session.PROTOCOL, bar_sources.PROTOCOL_V3))
        rules.start()
        self.addCleanup(rules.stop)
        self.payload = dict(id='new-bar', route_id='route', dossier_id=None, role='bar', reviewer_id='first',
                            author_id='author', context={})

    def record_sole_route(self, output_tokens, probe_tokens):
        self.case.capacity_probe_tokens = probe_tokens
        with patch.object(protocol, 'datetime', turn_fixtures.ProbeClock):
            record_codex_route(self.case, model='gpt-6-astra', output_tokens=output_tokens)

    def test_input_floor_stop_on_the_only_route_answers_with_a_probe_that_the_store_takes(self):
        # 150,000 observed input tokens leave 22,000 after the output reserve: the rules v3 record this bar, and the
        # request turn of the rules v4 needs more.
        self.record_sole_route(128000, 150000)
        stop = self.capture_stop(self.payload)
        self.assertEqual((stop['reason'], stop['serving_routes']), (session.SESSION_BELOW_FLOOR_REASON, []))
        [answer] = stop['then']
        [step] = answer['steps']
        self.assertEqual((answer['answer'], step['command'], step['payload']['route_id']),
                         ('Probe this route', 'review-probe', 'route'))
        self.assertIsNone(self.execute_repair(answer['steps'], 'route'))
        # The condition of the answer: the stopped command runs again, and the probed route now serves it.
        saved = self.case.mutate(protocol.record_assignment, self.payload)['result']
        self.assertEqual(saved['session_chain']['first_assignment_id'], 'new-bar')

    def test_output_floor_stop_on_the_only_route_answers_with_another_route_that_the_store_takes(self):
        self.record_sole_route(4000, 300000)
        stop = self.capture_stop(self.payload)
        self.assertEqual(stop['admission']['details']['reason'], 'output_floor')
        [answer] = stop['then']
        self.assertEqual([step['command'] for step in answer['steps']], ['review-route', 'review-assignment'])
        [(saved, records)] = self.take_answers(stop).values()
        self.assertEqual(saved['route_id'], 'handoff-route')
        self.assertIn('session_chain', saved)


class SessionFloorDecisionTests(CapacityAnswers, unittest.TestCase):
    """A turn of the rules v4 capped at its floor ends with the decision of its capacity recovery: the run of the same
    turn again, which the failure planner names, is refused by the store while the capped answer is whole."""
    setUp = pages.SessionPagesTests.setUp
    read_packet = pages.SessionPagesTests.read_packet
    respond = pages.SessionPagesTests.respond
    start = pages.SessionPagesTests.start
    answer = pages.SessionPagesTests.answer

    def answer_at_the_output_limit(self, current):
        """The page answer is whole and valid, and its usage reaches the output limit of the route."""
        def respond(route, request, credential):
            response = build_codex_response(route, request, self.answer(current))
            response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
            return response
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            self.case.mutate(protocol.invoke_assignment, dict(id='cap-%d' % self.case.store.revision,
                assignment_id=current['id']), credential='test-only')

    def test_page_floor_through_plan_next_ends_with_the_decision_of_its_recovery(self):
        current = self.start()
        for number in range(20):
            self.answer_at_the_output_limit(current)
            plan = plan_next(self.case.read_records(), self.case.artifacts, current['id'])
            if plan['state'] == 'replace':
                current = self.case.mutate(protocol.record_assignment, plan['payload'])['result']
            elif plan['state'] != 'run':
                break
        else:
            self.fail('The floor never stopped')
        self.assertEqual((plan['state'], plan['code']), ('stop', 'review_output_over_cap'), plan)
        recovery = plan_after_failure(self.case.read_records(), self.case.artifacts, current['id'])['details']['recovery']
        self.assertEqual(recovery['kind'], 'decision')
        self.assertEqual(plan['details']['decision'], {key: recovery[key] for key in ('owner_decides', 'then')})
        self.take_answers(plan['details'])


if __name__ == '__main__':
    unittest.main()
