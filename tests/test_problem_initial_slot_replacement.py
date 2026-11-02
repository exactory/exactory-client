"""Only a pre-dispatch size failure replaces an initial problem result reviewer."""

import copy
from unittest.mock import patch

from problem_parts_fixtures import ResultPartsCase
from research_harness import review_protocol as protocol, review_transport as transport
from research_harness.errors import ResearchError


class ProblemInitialSlotReplacementTests(ResultPartsCase):
    def failed_initial(self, code='review_input_too_large'):
        assignment = self.assign('failed-initial')
        with patch.object(transport, 'send_request', side_effect=ResearchError(code, 'Synthetic pre-dispatch failure')):
            self.case.mutate(protocol.invoke_assignment, {'id': 'failed-attempt', 'assignment_id': assignment['id']},
                             credential='fixture-only')
        return assignment

    def test_same_reviewer_route_author_replaces_size_failure_in_own_bar_slot(self):
        failed = self.failed_initial()
        self.oversized()
        replacement = self.assign('replacement')
        self.assertEqual(replacement['slot_id'], failed['slot_id'])
        self.assertEqual(replacement['slot_id'], 'bar-first')
        self.assertEqual(self.packet(replacement)['problem_evidence_parts']['turn'], 1)
        self.assert_code('review_assignment_superseded', lambda: self.invoke(failed, {}, attempt='old-retry'))

    def test_other_failure_route_author_pending_or_completed_still_refuse(self):
        failed = self.failed_initial()
        original = self.case.read_records()
        value = {'id': 'replacement', 'route_id': failed['route_id'], 'role': 'result', 'dossier_id': None,
                 'reviewer_id': failed['reviewer_id'], 'author_id': failed['author_id'], 'subject': failed['subject'],
                 'context': {}}
        for change in ('other_failure', 'other_route', 'other_author', 'pending', 'completed', 'response', 'continuation', 'imported'):
            records, candidate = copy.deepcopy(original), copy.deepcopy(value)
            attempt = records['review_attempt']['failed-attempt']
            if change == 'other_failure':
                attempt['error']['code'] = 'review_transport_failure'
            elif change == 'other_route':
                candidate['route_id'] = 'another-route'
            elif change == 'other_author':
                candidate['author_id'] = 'coauthor'
            elif change == 'pending':
                records['review_invocation']['pending'] = {'id': 'pending', 'assignment_id': failed['id']}
            elif change == 'completed':
                attempt['status'] = 'completed'
            elif change == 'response':
                attempt['response'] = {'observed': True}
            elif change == 'continuation':
                records['review_assignment'][failed['id']]['context'] = {'prior_assignment_id': 'bar-first'}
            elif change == 'imported':
                attempt['origin'] = 'imported'
            with self.subTest(change=change):
                self.assert_code('review_resampling_forbidden', lambda: protocol._initial_slot(
                    records, self.case.artifacts, candidate))

    def test_a_run_refused_before_its_invocation_frees_the_slot(self):
        from codex_route_fixtures import limit_codex_input
        # The frozen ordinary request fits when it is assigned and exceeds the route after the limit falls, as a
        # visual-route packet or a packet frozen under an earlier limit does. Since 0.71.0 its run records nothing.
        self.oversized(limit=10 ** 6)
        refused = self.assign('refused-initial')
        self.assertNotIn('problem_evidence_parts', self.packet(refused))
        limit_codex_input(self, 110000)
        with patch.object(transport, 'send_request', side_effect=AssertionError('No reviewer call is permitted')):
            self.assert_code('review_input_too_large', lambda: self.case.mutate(
                protocol.invoke_assignment, {'id': 'refused-attempt', 'assignment_id': refused['id']},
                credential='fixture-only'))
        records = self.case.read_records()
        self.assertFalse([attempt for attempt in records.get('review_attempt', {}).values()
                          if attempt['assignment_id'] == refused['id']])
        replacement = self.assign('replacement')
        self.assertEqual(replacement['slot_id'], refused['slot_id'])
        self.assertEqual(self.packet(replacement)['problem_evidence_parts']['turn'], 1)

    def test_an_initial_assignment_that_never_ran_and_fits_holds_its_slot(self):
        self.assign('held-initial')
        self.assert_code('review_resampling_forbidden', lambda: self.assign('replacement'))

    def test_bar_and_nonproblem_roles_keep_the_existing_rule(self):
        failed = self.failed_initial()
        records = self.case.read_records()
        candidate = dict(failed, id='replacement')
        for role, subject in (('bar', failed['subject']), ('result', {'kind': 'dossier'})):
            altered = copy.deepcopy(records)
            altered['review_assignment'] = {'failed-initial': dict(failed, role=role, subject=subject)}
            value = dict(candidate, role=role, subject=subject)
            self.assert_code('review_resampling_forbidden', lambda: protocol._initial_slot(
                altered, self.case.artifacts, value))
