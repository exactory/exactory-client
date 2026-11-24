"""A refusal for a set that the answer left incomplete names the missing members in its notice (finding S-C4 of the
confirmation runs of release 0.93.0): a final turn of a session shows such a set only by its size."""

import unittest

from codex_route_fixtures import invoke_codex
import test_review_session_v4_pages as pages
from research_harness import review_protocol as protocol
from research_harness.review_continuation import plan_next


class MissingObjectionNoticeTests(unittest.TestCase):
    setUp = pages.SessionPagesTests.setUp
    read_packet = pages.SessionPagesTests.read_packet
    respond = pages.SessionPagesTests.respond
    start = pages.SessionPagesTests.start
    answer = pages.SessionPagesTests.answer

    def test_final_without_a_predecessor_objection_is_refused_with_its_id_and_the_notice_answer_records(self):
        current = self.start(targets=3, distinct_objections=True)
        answer = self.answer(current)
        findings = answer['value']['objection_findings']
        omitted = findings.pop(0)
        invoke_codex(self.case, current['id'], answer)
        records = self.case.read_records()
        refusal = records['review_output_refusal']['attempt-' + current['id']]
        self.assertEqual((refusal['code'], refusal['details']['missing_ids']),
                         ('review_objection_history_incomplete', [omitted['id']]), refusal)
        plan = plan_next(records, self.case.artifacts, current['id'])
        self.assertEqual(plan['state'], 'replace', plan)
        replacement = self.case.mutate(protocol.record_assignment, plan['payload'])['result']
        [entry] = self.read_packet(replacement)['output_refusal']['refused']
        # The stub keeps its earlier answer and adds a finding for each id that the notice names.
        for identifier in entry['details']['missing_ids']:
            findings.append(dict(id=identifier, status='continuing', reason='A gap remains.',
                                 evidence=[self.case.supporting]))
        invoke_codex(self.case, replacement['id'], answer)
        state = protocol.assignment_state(self.case.read_records(), self.case.artifacts, replacement['id'])
        self.assertTrue(state['ready'], state['obligations'])


if __name__ == '__main__':
    unittest.main()
