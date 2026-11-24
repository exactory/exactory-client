"""C12 result and focused-role plans reach their registered exhaustion handoffs."""

from unittest.mock import patch
import copy
import unittest

from research_harness import literature, review_protocol as protocol, research_decisions as decisions
from research_harness.errors import ResearchError
from research_harness.review_continuation import plan_next
from research_harness.stop_classes import STOP_REGISTRY
import test_record_reading_handoff as fixtures


class CitationPlannerRoleTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def exhaust(self, case, assignment, output, invoke, record, step):
        for number in range(3):
            with self.assertRaises(ResearchError) as caught:
                record(assignment, output)
            self.assertEqual(caught.exception.code, 'source_link_not_fulltext')
            plan = plan_next(case.read_records(), case.artifacts, assignment['id'])
            if number < 2:
                self.assertEqual(plan['state'], 'replace', plan)
                assignment = case.mutate(protocol.record_assignment, plan['payload'])['result']
                invoke(assignment, output)
            else:
                self.assertEqual((plan['state'], plan['code']), ('handoff', 'review_slot_exhausted'))
                self.assertEqual(STOP_REGISTRY[plan['code']]['next']['kind'], 'role')
                self.assertEqual(plan['details']['step']['id'], step)
        return plan

    def test_c12_c_result_abstract_alone_and_beside_unread_fulltext_reach_repair(self):
        for count in (0, 1):
            with self.subTest(fulltexts=count):
                fixture = fixtures.ReadingHandoffTests()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                final, output = fixture.build_final(count=count, abstract=True, role='result')
                def record(assignment, answer):
                    return fixture.case.mutate(decisions.record_value_review,
                        dict(answer, id='unrecorded', dossier_id=None, assignment_id=assignment['id']))
                plan = self.exhaust(fixture.case, final, output, fixture.invoke, record, 2)
                self.assertEqual(plan['details']['step']['payload']['reviewer_id'], 'first')
                self.assertEqual(plan['details']['step']['payload']['context']['context_repair']['event_ids'], [])

    def test_c12_c_adjudication_abstract_alone_and_beside_unread_fulltext_reach_new_reviewer(self):
        for mixed in (False, True):
            with self.subTest(mixed=mixed):
                holder = fixtures.FocusedReadingHandoffTests()
                self.addCleanup(holder.doCleanups)
                fixture = holder.prepare()
                case = fixture.case
                evidence = [dict(kind='source', link=case.supporting_links[case.supporting['sha256']])]
                assignment = fixture.assign()
                if mixed:
                    old = case.expected['readings'][0]['reading']
                    link = old['inspections'][0]['link']
                    records = case.read_records()
                    bundle = {key: copy.deepcopy(value) for key, value in records['source_bundle'][old['bundle_id']].items()
                        if key in ('id', 'version_id', 'source_id', 'scope', 'completeness', 'units',
                                   'inventory', 'bibliography', 'resolutions')}
                    bundle['id'] = 'new-unread-bundle'
                    bundle['units'].append(dict(id='proof', kind='text', required=True, link=link))
                    case.mutate(literature.import_bundle, bundle)
                    evidence.append(dict(kind='source', link=link))
                output = dict(fixture.final_output(), evidence=evidence)
                fixture.invoke(assignment, output)
                def record(assignment, answer):
                    return case.mutate(protocol.record_adjudication,
                        dict(answer, id='unrecorded', assignment_id=assignment['id']))
                plan = self.exhaust(case, assignment, output, fixture.invoke, record, 3)
                step = plan['details']['step']
                self.assertEqual(step['payload']['context']['review_ids'], assignment['context']['review_ids'])
                self.assertIn('third', step['excluded_reviewer_ids'])
