"""The after-turn capacity stop of a head of the session rules v4 (review_continuation._plan_session_capacity_stop)."""

import unittest
from unittest.mock import patch

import continuity_forms_fixtures as forms
from research_harness import review_session_v4
from research_harness.errors import ResearchError


class SessionCapacityStopTests(unittest.TestCase):
    def test_the_stop_step_is_taken_and_the_next_plan_names_its_release(self):
        """Repairs PL-F5 of the review of the merged tree. When the capacity plan pinned to a session v4 chain has no
        room for its next turn, the plan is a stop whose step is review-stop after the turn; the store takes it, and
        the next plan is the handoff review_chain_stopped whose step releases that stop. The admission of the next
        turn is replaced by its refusal: the subject is the path of the planner, not the size of the pinned plan."""
        form = forms.build_turn(self, 'session-v4', 'request')
        forms.run_head(form, forms.respond_with(form.answer()), plan=form.plan())
        self.assertEqual(form.plan()['state'], 'continue')
        refusal = ResearchError('review_route_capacity_below_floor', 'The pinned plan has no room.',
                                dict(kind='request', room=1000, minimum=2000))
        with patch.object(review_session_v4, 'require_writable_session_continuation', side_effect=refusal):
            stop = form.plan()
            self.assertEqual((stop['state'], stop['code'], stop['command']),
                             ('stop', 'review_route_capacity_below_floor', 'review-stop'))
            self.assertEqual(stop['details']['step'], dict(command='review-stop', payload=stop['payload']))
            self.assertEqual((stop['payload']['action'], stop['payload']['mode'], stop['payload']['assignment_id']),
                             ('stop', 'after_turn', form.head))
            revision = form.case.store.revision
            forms.matrix.execute(form.case, stop['command'], stop['payload'])
            self.assertGreater(form.case.store.revision, revision)
            following = form.plan()
        self.assertEqual((following['state'], following['code']), ('handoff', 'review_chain_stopped'))
        step = following['details']['step']
        self.assertEqual((step['command'], step['payload']),
                         ('review-stop', dict(action='release', stop_id=stop['payload']['id'])))


if __name__ == '__main__':
    unittest.main()
