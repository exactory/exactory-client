"""A post closes the review chains that it follows, also for a context repair that would start a session of the
rules v4 (finding S-C5 of the confirmation runs of release 0.93.0)."""

import unittest
from unittest.mock import patch

from codex_route_fixtures import invoke_codex, probe_codex_route, record_codex_route
from research_harness import bar_sources, research_decisions as decisions, review_protocol as protocol
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.review_continuation import plan_next
import test_review_session_v4_entry as entry_fixtures


class PostedRepairTests(unittest.TestCase):
    setUp = entry_fixtures.SessionEntryTests.setUp
    record_legacy_bars = entry_fixtures.SessionEntryTests.record_legacy_bars
    build_repair_payload = entry_fixtures.SessionEntryTests.build_repair_payload

    def post(self):
        statement = self.case.statement
        payload = dict(id='post', problem_id='656c336e-e892-4c47-80d2-a71d022f4116',
                       statement_digest=digest({key: statement[key] for key in ('statement', 'resolution_criteria')}),
                       reason='The owner published the synthetic statement.', evidence='Synthetic server receipt.')
        record = dict(payload, gate='passed', statement_id=statement['id'], posted_at='2026-11-05T02:53:34.915Z')
        self.case.store.mutate('problem.posted', payload,
            lambda transaction: (transaction.put('problem_post', 'post', record), record)[1],
            expected_revision=self.case.store.revision, request_id='post')

    def test_repair_of_a_recorded_slate_review_after_the_post_is_refused_with_the_code_of_its_run(self):
        self.record_legacy_bars()
        with patch.object(protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,)):
            head = self.case.assign('slate', 'first', identifier='slate-recorded')
            output = self.case.build_review_output('slate')
            output['value']['rubric_potential'] = {}
            for row in output['value']['assurances']:
                row['status'] = 'not_assessed'
            invoke_codex(self.case, head['id'], output)
            self.case.mutate(decisions.record_value_review, dict(output, id='review-slate', dossier_id=None,
                                                                 assignment_id=head['id']))
        self.post()
        self.case.mutate(protocol.record_context_event, dict(id='retirement', assignment_id=head['id'],
            kind='route_retired', source='owner', reason='The route is retired.', evidence=[self.case.supporting]))
        record_codex_route(self.case, 'repair-route', model='gpt-6-astra', output_tokens=128000)
        probe_codex_route(self.case, 'repair-probe', 'repair-route')
        plan = plan_next(self.case.read_records(), self.case.artifacts, head['id'])
        self.assertEqual(plan['state'], 'done', plan)
        self.assertNotIn('step', plan.get('details', {}))
        before = self.case.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            self.case.mutate(protocol.record_assignment, self.build_repair_payload(head, 'repair-route'))
        self.assertEqual((caught.exception.code, caught.exception.details.get('closure_root_id')),
                         ('problem_already_posted', head['id']))
        self.assertEqual(self.case.store.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
