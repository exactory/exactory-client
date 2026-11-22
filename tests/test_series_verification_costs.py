"""Work bounds for legacy parts verification on recorded turns and small delivered packets."""

import unittest
from unittest.mock import patch

from research_harness import (bar_sources, output_views, review_excerpts, review_parts as parts,
                              review_protocol, workspace)
from review_parts_fixtures import make_output, make_packet, observe, plan
import test_research_output_views as views


class SeriesVerificationCosts(views.PartsViewCase):
    def setUp(self):
        for selection in (patch.object(parts, 'NEW_SERIES_PROTOCOLS', (parts.PROTOCOL,)),
                          patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))):
            selection.start()
            self.addCleanup(selection.stop)
        super().setUp()

    def start_two_turns(self):
        self.prepare_parts(count=3)
        first = self.assign()
        self.invoke(first)
        second = self.assign('next-turn', first['id'])
        return self.case.read_records(), second

    def test_verification_lists_once(self):
        records, second = self.start_two_turns()
        with patch.object(parts, '_list_series', wraps=parts._list_series) as listing:
            result = parts.verify_series(records, self.case.artifacts, second['id'])
        self.assertTrue(result['packets'])
        self.assertEqual(listing.call_count, 1)

    def test_listing_parses_each_packet_once(self):
        records, second = self.start_two_turns()
        with patch.object(workspace, 'strict_json', wraps=workspace.strict_json) as parse:
            chain = parts._list_series(records, self.case.artifacts, second['id'])
        self.assertEqual(len(chain), 2)
        self.assertEqual(parse.call_count, len(chain))

    def test_render_lists_once_per_parts_verification(self):
        records, _ = self.start_two_turns()
        parts_turns = {identifier for identifier, assignment in records['review_assignment'].items()
                       if parts.KEY in assignment}
        verify = parts.verify_series
        calls = {}

        def count_lists(records, artifacts, identifier):
            with patch.object(parts, '_list_series', wraps=parts._list_series) as listing:
                result = verify(records, artifacts, identifier)
            calls[identifier] = listing.call_count
            return result

        with patch.object(parts, 'verify_series', side_effect=count_lists):
            self.assertEqual(output_views.render_all(self.case.store)['errors'], 0)
        self.assertEqual(calls, dict.fromkeys(parts_turns, 1))

    def test_observation_reuses_the_listing_and_preserves_the_result(self):
        records, second = self.start_two_turns()
        chain = parts._list_series(records, self.case.artifacts, second['id'])
        expected = parts.observe_series(records, self.case.artifacts, second['id'])
        self.assertTrue(expected)
        with patch.object(parts, '_list_series', wraps=parts._list_series) as listing:
            actual = parts.observe_series(records, self.case.artifacts, second['id'], chain=chain)
        self.assertEqual(actual, expected)
        listing.assert_not_called()


class StatementVerificationCosts(unittest.TestCase):
    def test_read_checks_delivered_items_once_in_each_validation_stage(self):
        packet = plan(make_packet(2, 200))
        output = make_output(packet)
        self.assertEqual(len(output['cards']), 2)
        with patch.object(parts, 'read_delivered_items', wraps=parts.read_delivered_items) as read:
            parts.validate_output(packet, output, packet['_test_inventory'])
        self.assertEqual(read.call_count, 2)

    def test_check_normalizes_each_delivered_text_once(self):
        logical = make_packet(2, 200)
        read = plan(logical)
        observations = [observe(read)]
        combine = plan(logical, observations)
        combination = dict(units=['u1'], question='What does the record show?',
                           view='Read the original.', read=[dict(unit='u1')])
        observations.append(observe(combine, make_output(combine, [combination])))
        check = plan(logical, observations)
        self.assertEqual(check[parts.KEY]['kind'], 'check')
        output = make_output(check)
        output['findings'][0]['statements'] = [dict(unit='u1', text=text, why='Read the original.')
                                              for text in ('s0', 'xxxxxxxx', 'absent')]
        with patch.object(review_excerpts, 'normalize_with_offsets',
                          wraps=review_excerpts.normalize_with_offsets) as normalize:
            result = parts.validate_output(check, output, check['_test_inventory'])
        finding = result['findings'][0]
        self.assertEqual((len(finding['statements']), finding['unverified_statements']), (2, 1))
        # One delivered item and three statement texts.
        self.assertEqual(normalize.call_count, 4)

    def test_statement_verification_accepts_previously_read_items(self):
        packet = plan(make_packet(1, 200))
        inventory = packet['_test_inventory']
        statements = [dict(text='xxxxxxxx', why='The record contains the quoted text.')]
        expected = parts._verify_statements(packet, 'u1', statements, inventory, 0)
        self.assertEqual(len(expected[0]), 1)
        items = parts.read_delivered_items(packet)
        self.assertEqual(parts._verify_statements(packet, 'u1', statements, inventory, 0, items), expected)
