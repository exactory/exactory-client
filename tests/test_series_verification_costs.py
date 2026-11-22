"""Work bounds for legacy parts verification on recorded turns and small delivered packets."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from weakref import ref

from research_harness import (bar_sources, review_excerpts, review_parts as parts,
                              review_protocol, workspace)
from research_harness.artifacts import ArtifactStore, describe_artifact
from research_harness.errors import ResearchError
from render_reference import use_reference_functions
from review_parts_fixtures import make_output, make_packet, observe, plan
import test_research_output_views as views
from test_render_bytes import render_with_list_counts


class SeriesListingCosts(unittest.TestCase):
    def test_listing_parses_each_packet_once(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(Path(directory))
            binding = dict(protocol=parts.PROTOCOL, kind='read', series_assignment_id='first',
                           series_key='fixture', adapter='problem_result')
            assignments = {}
            for turn, identifier in enumerate(('first', 'second'), 1):
                packet = {parts.KEY: dict(binding, turn=turn)}
                reference = review_protocol._put_json(artifacts, packet)
                assignments[identifier] = dict(id=identifier, packet=reference, role='result', route_id='route',
                                               session_id='fixture', reviewer_id='reviewer', author_id='author')
            assignments['second'].update(history_assignment_id='first', context=dict(prior_assignment_id='first'))
            with patch.object(workspace, 'strict_json', wraps=workspace.strict_json) as parse:
                chain = parts._list_series(dict(review_assignment=assignments), artifacts, 'second')
        self.assertEqual([assignment['id'] for assignment, _ in chain], ['first', 'second'])
        self.assertEqual(parse.call_count, len(chain))


class SeriesVerificationCosts(views.PartsViewCase):
    def setUp(self):
        for selection in (patch.object(parts, 'NEW_SERIES_PROTOCOLS', (parts.PROTOCOL,)),
                          patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))):
            selection.start()
            self.addCleanup(selection.stop)
        super().setUp()

    def test_render_lists_once_per_parts_verification(self):
        self.prepare_parts(count=2)
        first = self.assign()
        self.invoke(first)
        second = self.assign('next-turn', first['id'])
        summary, calls = render_with_list_counts(self.case.store)
        self.assertEqual(summary['errors'], 0)
        self.assertCountEqual(calls, [(first['id'], 1), (second['id'], 1)])

        records, artifacts = self.case.read_records(), self.case.artifacts
        with use_reference_functions():
            expected = parts.observe_series(records, artifacts, second['id'])
        self.assertTrue(expected)
        self.assertEqual(parts.observe_series(records, artifacts, second['id']), expected)
        chain = parts._list_series(records, artifacts, second['id'])
        with patch.object(parts, '_list_series', wraps=parts._list_series) as listing:
            actual = parts.observe_series(records, artifacts, second['id'], chain=chain)
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

    def test_read_refuses_an_invalid_card_before_rechecking_the_manifest(self):
        packet = plan(make_packet(2, 200))
        output = make_output(packet)
        output['cards'][0]['statements'] = {}
        with patch.object(parts, 'read_delivered_items', wraps=parts.read_delivered_items) as read:
            with self.assertRaisesRegex(ResearchError, 'Statements are an array'):
                parts.validate_output(packet, output, packet['_test_inventory'])
        read.assert_called_once_with(packet)

    def test_check_reads_populated_items_once_in_each_validation_stage(self):
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
        with use_reference_functions():
            expected = parts.validate_output(check, output, check['_test_inventory'])
        with patch.object(parts, 'read_delivered_items', wraps=parts.read_delivered_items) as read:
            result = parts.validate_output(check, output, check['_test_inventory'])
        self.assertEqual(result, expected)
        finding = result['findings'][0]
        self.assertEqual((len(finding['statements']), finding['unverified_statements']), (2, 1))
        self.assertEqual(read.call_count, 2)

    def test_statement_verification_accepts_previously_read_items(self):
        packet = plan(make_packet(1, 200))
        inventory = packet['_test_inventory']
        statements = [dict(text='xxxxxxxx', why='The record contains the quoted text.')]
        with use_reference_functions():
            expected = parts._verify_statements(packet, 'u1', statements, inventory, 0)
        self.assertEqual(len(expected[0]), 1)
        self.assertEqual(parts._verify_statements(packet, 'u1', statements, inventory, 0), expected)
        items = parts.read_delivered_items(packet)
        with patch.object(parts, 'read_delivered_items', wraps=parts.read_delivered_items) as read:
            supplied = parts._verify_statements(packet, 'u1', statements, inventory, 0, items)
            empty = parts._verify_statements(packet, 'u1', statements, inventory, 0, {})
        read.assert_not_called()
        self.assertEqual(supplied, expected)
        self.assertEqual(empty, ([], 1, len(statements[0]['text'])))

    def test_check_offsets_and_manifest_reads_stay_within_release_bounds(self):
        class TrackedOffsets(list):
            pass

        def validate(packet, output, inventory):
            normalize = review_excerpts.normalize_with_offsets
            source_texts = {entry['content'] for entry in packet['evidence']}
            offsets_seen = []
            peak = 0

            def track_offsets(text):
                nonlocal peak
                normalized, offsets = normalize(text)
                if text in source_texts:
                    offsets = TrackedOffsets(offsets)
                    offsets_seen.append(ref(offsets))
                    peak = max(peak, sum(table() is not None for table in offsets_seen))
                return normalized, offsets

            with patch.object(review_excerpts, 'normalize_with_offsets', new=track_offsets), \
                    patch.object(parts, 'read_delivered_items', wraps=parts.read_delivered_items) as read:
                result = parts.validate_output(packet, output, inventory)
            self.assertFalse(any(table() is not None for table in offsets_seen))
            return result, read.call_count, peak, len(offsets_seen)

        for shape, bodies, spans, release_peak in (
                ('shared body', ('Q1.Q2.', 'Q1.Q2.'), [(0, 0, 6)], 1),
                ('shared segments', ('Q1.Q1.', 'Q1.Q1.'), [(0, 0, 2), (0, 3, 5)], 2),
                ('distinct bodies', ('Q1.', 'Q2.'), [(0, 0, 3), (1, 0, 3)], 1),
                ('no statements', (), [], 0)):
            with self.subTest(shape=shape):
                if bodies:
                    references = {body: describe_artifact(body.encode(), 'text/plain') for body in bodies}
                    logical = dict(evidence=[dict(artifact=reference, content=body, encoding='utf-8')
                                             for body, reference in references.items()],
                                   original_evidence=[dict(page=page, artifact=references[body])
                                                      for page, body in enumerate(bodies, 1)])
                    adapter = 'problem_adjudicator'
                else:
                    logical = make_packet(2, 200)
                    adapter = 'problem_result'
                inventory = parts.build_inventory(logical, adapter)
                units = parts.enumerate_units(logical, adapter, inventory)
                self.assertEqual(len(units), 2)
                selections = [dict(unit=unit['handle']) for unit in units]
                if bodies:
                    selections = [dict(unit=units[owner]['handle'], body=references[bodies[owner]]['sha256'],
                                       start=start, end=end) for owner, start, end in spans]
                packet = parts.project_packet(parts.build_frame(logical, units), units, selections,
                    dict(protocol=parts.PROTOCOL, kind='check', turn=3, check_combinations=['c'],
                         output_allowance=1000, findings_budget_remaining=2000),
                    dict(cards=[], combinations=[], findings=[]))
                self.assertEqual(len(packet[parts.KEY]['delivered_items']), 0 if bodies else 2)
                statements = [dict(unit=unit['handle'], text='Q', why='Review.') for unit in units] if bodies else []
                output = dict(kind='check', turn=3, findings=[
                    dict(combination='c', finding='Read the sources.', effect='supports', statements=statements)])
                with use_reference_functions():
                    expected, release_reads, expected_peak, release_normalizations = validate(packet, output, inventory)
                actual, reads, peak, normalizations = validate(packet, output, inventory)
                self.assertEqual(actual, expected)
                self.assertEqual(len(actual['findings'][0]['statements']), len(statements))
                self.assertEqual(expected_peak, release_peak)
                self.assertLessEqual(peak, release_peak)
                self.assertEqual(normalizations, release_normalizations)
                self.assertEqual(release_reads, 1 + len(statements))
                self.assertEqual(reads, 2 if statements else 1)

    def test_equal_fragments_keep_distinct_owners_body_digests_and_spans(self):
        fragment = 'Ａ  ﬁnd'
        prefixes = ['x', 'long lead ']
        bodies = [prefix + fragment for prefix in prefixes]
        references = [describe_artifact(body.encode(), 'text/plain') for body in bodies]
        logical = dict(evidence=[dict(artifact=reference, content=body, encoding='utf-8')
                                 for reference, body in zip(references, bodies)],
                       original_evidence=[dict(page=page, artifact=reference)
                                          for page, reference in enumerate(references, 1)])
        inventory = parts.build_inventory(logical, 'problem_adjudicator')
        units = parts.enumerate_units(logical, 'problem_adjudicator', inventory)
        self.assertEqual(len(units), 2)
        selections = [dict(unit=unit['handle'], body=reference['sha256'],
                           start=len(prefix), end=len(prefix) + len(fragment))
                      for unit, reference, prefix in zip(units, references, prefixes)]
        packet = parts.project_packet(parts.build_frame(logical, units), units, selections,
            dict(protocol=parts.PROTOCOL, kind='check', turn=3, check_combinations=['c'],
                 output_allowance=1000, findings_budget_remaining=2000),
            dict(cards=[], combinations=[], findings=[]))
        statements = [dict(unit=unit['handle'], text='A find', why='Review.') for unit in units]
        output = dict(kind='check', turn=3, findings=[
            dict(combination='c', finding='Read both sources.', effect='supports', statements=statements)])
        with use_reference_functions():
            expected = parts.validate_output(packet, output, inventory)
        actual = parts.validate_output(packet, output, inventory)
        self.assertEqual(actual['findings'][0]['statements'], [
            dict(statement, text=fragment, at=selection) for statement, selection in zip(statements, selections)])
        self.assertEqual(actual, expected)
