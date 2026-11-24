"""A context repair of an ordinary result head that the parts protocol v3 refuses is planned by the parts protocol v2
through the coverage check of the assignment (finding PL-F8 of the review of the merged tree, and finding F1 of the
review of the parts protocol v3)."""

import unittest
from unittest.mock import patch

from continuity_forms_fixtures import fork_store, prepare_unit_fixture
from research_harness import review_parts as legacy, review_parts_v3 as parts, review_protocol as protocol
from research_harness import review_single_request
from research_harness.errors import ResearchError
from test_review_capacity_steps import CapacityAnswers
import test_review_parts_v3_entry as entry_fixtures

SELECTIONS = ((legacy.PROTOCOL,), (parts.PROTOCOL, legacy.PROTOCOL))


class ResultRepairAfterV3RefusalTests(CapacityAnswers, unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        self.fixture = prepare_unit_fixture(self, entry_fixtures.StoredEntryTests,
                                            'test_ordinary_repair_conversion_stores_the_exact_planned_request')
        self.case = self.fixture.case
        for index in range(2):
            search = dict(id='s%d' % index, queries=['bounded sequence'], reason='a' * 3000)
            self.case.put('literature_search', search['id'], search)
        with patch.object(legacy, 'NEW_SERIES_PROTOCOLS', (legacy.PROTOCOL,)), \
                patch.object(review_single_request, 'describe_task_entry', return_value=None):
            self.head = self.fixture.assign('repair-parent')

    def assign_repair(self, tokens, selection, local_limit=None):
        """Record the repair route with tokens of input, then the fresh repair that needs parts under selection."""
        limit = patch.object(protocol.transport, 'CODEX_INPUT_LIMIT_CHARS',
                             local_limit or protocol.transport.CODEX_INPUT_LIMIT_CHARS)
        with patch.object(legacy, 'NEW_SERIES_PROTOCOLS', selection), limit, \
                patch.object(review_single_request, 'describe_task_entry', return_value='task'):
            binding = entry_fixtures.NaturalEntryTests._contaminate(None, self.fixture, self.head, tokens)
            return self.fixture.repair_assignment(self.head, binding)

    def test_a_repair_that_fits_v2_records_under_both_selections(self):
        """On a route of 65,000 tokens and 150,000 characters the v3 admission refuses the first read; the repair
        records a series of the parts protocol v2 under the release selection and with v3 selected."""
        admit = parts.admit_packet
        for selection in SELECTIONS:
            states = []

            def record_admission(*args, **kwargs):
                outcome = admit(*args, **kwargs)
                states.append(outcome['state'])
                return outcome

            with self.subTest(selection=selection[0]), fork_store(self), \
                    patch.object(parts, 'admit_packet', side_effect=record_admission):
                child = self.assign_repair(65000, selection, local_limit=150000)
                self.assertEqual((child[legacy.KEY]['protocol'], child[legacy.KEY]['kind']), (legacy.PROTOCOL, 'read'))
                self.assertEqual(states, [] if selection == SELECTIONS[0] else ['refuse'])

    def test_a_refusal_of_the_v2_planner_after_v3_is_the_capacity_stop_with_answers(self):
        """On a route of 15,000 tokens the planner of v2 has no room for a turn. With v3 selected the repair ends in
        the capacity stop of the assignment, and the store takes each of its answers; the release selection keeps the
        refusal of the planner of v2, as release 0.92.1 gives it."""
        with fork_store(self), self.assertRaises(ResearchError) as released:
            self.assign_repair(15000, SELECTIONS[0])
        self.assertEqual(released.exception.code, 'review_parts_frame_too_large')
        with patch.object(legacy, 'NEW_SERIES_PROTOCOLS', SELECTIONS[1]):
            with self.assertRaises(ResearchError) as stopped:
                self.assign_repair(15000, SELECTIONS[1])
            self.assertEqual(stopped.exception.code, 'review_route_capacity_below_floor')
            stop = stopped.exception.details
            self.assertEqual(stop['refusal'], 'review_parts_frame_too_large')
            self.assertTrue(stop['then'])
            self.take_answers(stop)


if __name__ == '__main__':
    unittest.main()
