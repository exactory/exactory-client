"""Prompt-only drivers for the released math v2 final form."""

import json
from unittest.mock import patch

import test_property_drivers_b as drivers


class MathFinalProperties(drivers.MathChain):
    def test_math_final_admission_at_the_one_request_boundary(self):
        """Use adjacent target counts to reach both sides of final admission."""
        admitted = self.build_covered_packet(8, 'math-1')
        native = self.build_covered_packet(9, 'math-9')
        records = self.read_records()
        reviewer = 'openness-reviewer'
        value = dict(id='math-9', role='native_math', route_id='route', dossier_id=None,
                     reviewer_id=reviewer, author_id='problem-author', context=dict(native_packet=native))
        logical = drivers.protocol.build_packet(records, self.artifacts, None, 'native_math', reviewer,
                                                native_packet=native)
        route = records['review_route']['route']
        refusal = drivers.v7.check_v7_admission(records, self.artifacts, route, logical['native'])
        self.assertEqual(refusal['state'], 'stop', refusal.get('final_form'))
        self.assertEqual((refusal['reason'], refusal['pages'], refusal['largest_one_request_targets']),
                         ('final_needs_pages', 2, 8))
        with patch.object(drivers.reading, 'NEW_MATH_CHAIN_PROTOCOLS', (drivers.reading.MATH_PROTOCOL,)):
            legacy = drivers.reading.prepare_packet(records, self.artifacts, value, route, logical)
        self.assertEqual(drivers.reading.prepare_packet(records, self.artifacts, value, route, logical), legacy)

        first = self.assign('math-1', admitted)
        self.assertEqual(drivers.reading.find_protocol(self.read_packet('math-1')), self.chain_protocol)
        self.assertNotIn('final_form', first['reading_chain_admission'])
        _, turns = self.drive('math-1')
        self.assertEqual(turns[-1][1], 'final')

    def test_math_carried_note_preserves_approval_evidence(self):
        """One new search changes the carried notes while preserving earlier evidence credit."""
        self.assign('math-1', self.build_math_packet())
        _, _, receipt = self.send_turn('math-1', fill=True)
        self.assertIsNone(receipt.get('output_refusal'))
        late = drivers.minimum_fixtures.ProblemOpennessMinimumTests.search(
            self, self.versions, query='A distinct search changes the manifest used by the notes.',
            captured_at='2026-11-21T15:00:00+00:00')
        self.searches.append(late)
        successor = self.build_math_packet(name='successor-export', assignment_id='successor')
        first = self.assign('successor', successor)
        self.assertTrue(first['carried_readings']['chains'])
        final, turns = self.drive('successor')
        self.assert_late_delivery(first, final, drivers.digest(late))
        self.assertEqual(turns[-1][2]['minimum']['searched_through'], '2026-11-21')
        self.assertTrue(any(row['kind'] == 'carried_reading' for _, stage, binding in turns
                            if stage == 'reading' for row in binding['plan']['now']))

    def test_math_k6_three_allowances(self):
        """Each segmenting kind needs two ranges, and the whole list must be requestable."""
        with patch.object(drivers.capacity, 'read_profile_priors', return_value=[]):
            pages, plan = self.run_k6(lambda: self.assign('math-1', self.build_math_packet()))
        self.assertEqual(len(pages), 1, 'The reading prompt must offer the allowances list request.')
        [page] = pages
        self.assertEqual((page['list'], page['after'], page['next']), ('allowances', None, None))
        self.assertEqual([row['id'] for row in page['rows']], plan['targets']['handles'])


class MathFinalEvidenceTests(drivers.MathChain):
    def record_searches(self):
        return [drivers.minimum_fixtures.ProblemOpennessMinimumTests.search(
            self, self.versions, captured_at='2026-10-15T23:30:00-07:00')]

    def test_math_final_lists_citable_units_and_the_search_date(self):
        reference = self.build_math_packet()
        native = json.loads(self.artifacts.read(reference))
        minimum = native['scientific_context']['openness_minimum']
        first = self.assign('math-1', reference)
        _, turns = self.drive('math-1')
        _, stage, binding = turns[-1]
        self.assertEqual(stage, 'final')
        inventory = json.loads(self.artifacts.read(first['reading_chain']['inventory']))
        units = inventory['units']
        offered = {row['digest'] for row in native['evidence']}
        required = {drivers.digest(self.search), *minimum['reading_digests']}
        rows = [dict(handle=row['handle'], kind=row['kind'], label=row['label'], required=row['sha256'] in required)
                for row in units if row['sha256'] in offered]
        rows.sort(key=lambda row: not row['required'])
        self.assertEqual(binding.get('evidence_units'), dict(count=len(rows), omitted=0, rows=rows))
        self.assertEqual(sum(row['required'] for row in rows), 2)
        [minimum_unit] = [row for row in units if row['identity'].get('record_kind') == 'openness_minimum']
        self.assertEqual(binding['minimum']['unit'], minimum_unit['handle'])
        self.assertEqual(binding['minimum']['searched_through'], '2026-10-15')
        version_two = dict(minimum, schema_version=2)
        self.assertEqual(drivers.v7.find_math_searched_through(self.artifacts, version_two, inventory), '2026-10-16')
