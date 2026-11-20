"""G1/K7/K8: the carried-reading and unread-final continuity rows."""

import json
import unittest
from unittest.mock import patch

from ci import continuity_guard as guard
import continuity_stage3_fixtures as stage3
import continuity_guard_fixtures as matrix
import test_openness_unread_final as unread
import test_openness_reading_v7_refusal as refusal
from research_harness import openness_reading as reading, openness_reading_v7 as v7
from research_harness import review_failure_class as failures, review_route_capacity as capacity
from research_harness import review_protocol as protocol, stop_classes
from research_harness.review_continuation import plan_next
from test_series_code_reach import record_reachable_errors


class Stage3MatrixTests(unittest.TestCase):
    def test_g1_k8_v7_positions_execute_each_size_outcome_to_next_assignment(self):
        rows = [row for row in capacity.list_capacity_recovery_rows() if row['protocol'] == v7.PROTOCOL]
        self.assertTrue(rows)
        for row in rows:
            with self.subTest(kind=row['kind'], handler=row['handler']):
                self.execute_size_rows(row['kind'])

    def execute_size_rows(self, stage):
        for outcome, detail in guard.SIZE_OUTCOMES:
            with self.subTest(stage=stage, outcome=outcome, detail=detail):
                try:
                    with record_reachable_errors('plan_next'):
                        row = stage3.execute_size_journey(self, stage, outcome, detail)
                    # Final has one admitted level in 0.91.0. The response-body
                    # limit must use the reading ladder just as the other limits do.
                    # A condensing turn of five entries is split down to one entry.
                    expected = dict(reading=[120000, 60000, 30000, 15000], combine=[5, 3, 2, 1]).get(stage, [240000])
                    self.assertEqual(row['levels'], expected, row)
                    self.assertEqual(row['end'], 'stop', row)
                finally:
                    self.doCleanups()
        # The subtests retain each failed row independently; table additions
        # enter this loop without editing a second list of expected outcomes.

    @record_reachable_errors('plan_next')
    def test_g1_chain_kind_and_outcome_tables_include_the_stage3_rows(self):
        rows = {(row['kind'], row['protocol']) for row in capacity.list_capacity_recovery_rows()}
        self.assertTrue({('reading', v7.PROTOCOL), ('final', v7.PROTOCOL)} <= rows)
        self.assertTrue({name for name, detail in guard.SIZE_OUTCOMES} <= set(failures.OUTCOME_ROWS))
        self.assertTrue({'provider_refused', 'scientific_refused'} <= set(failures.OUTCOME_ROWS))

    @record_reachable_errors('plan_next')
    def test_g1_k7_provider_sequences_reading_reach_verified_unresolved(self):
        case = unread.UnreadFinalSupport()
        self.addCleanup(case.doCleanups)
        case.setUp()
        clock = failures.datetime
        waits = []
        original_plan = unread.plan_next
        def observe_plan(*args):
            with patch.object(failures, 'datetime', clock):
                actual = original_plan(*args)
            action = original_plan(*args)
            if actual['state'] == 'wait':
                self.assertEqual(actual['code'], 'review_sequence_wait')
                self.assertIn(actual['code'], stop_classes.STOP_REGISTRY)
                self.assertTrue(actual['not_before'])
                self.assertEqual(action['state'], 'run')
                waits.append(actual)
            return action
        with patch.object(unread, 'plan_next', side_effect=observe_plan):
            _, final, packet = case.reach_unread()
        self.assertEqual(len(waits), failures.SEQUENCE_LIMIT - 1)
        records = case.store.snapshot()['records']
        attempts = json.loads(case.artifacts.read(packet['reading_final']['unread']['artifact']))['attempts']
        self.assertEqual({row['sequence'] for row in attempts}, set(range(1, failures.SEQUENCE_LIMIT + 1)))
        self.assertTrue(all(row['outcome'] == 'provider_refused' for row in attempts))
        from collections import Counter
        counts = Counter(row['sequence'] for row in attempts)
        # The first sequence steps down once to every reading level below the one the turn starts at. Since G8.11
        # of 0.92.0 the reading frame of this chain reserves 1,500 characters for the finding-units paragraph, so on
        # this route the turn starts at 60,000 characters rather than 120,000.
        reading_task = capacity.read_chain_capacity_plan(case.artifacts, final)['task']['reading']
        lower = [row for row in reading_task['levels'] if row['material_allowance'] < reading_task['material_allowance']]
        self.assertEqual(reading_task['material_allowance'], 60000)
        self.assertEqual(counts[1], failures.PROVIDER_REFUSAL_ATTEMPT_LIMIT + len(lower))
        self.assertTrue(all(counts[sequence] == failures.PROVIDER_REFUSAL_ATTEMPT_LIMIT
                            for sequence in range(2, failures.SEQUENCE_LIMIT + 1)))
        result = case.record_final(final, packet)
        self.assertEqual(result['outcome'], 'unresolved')
        self.assertTrue(result['reading_turns'])

    @record_reachable_errors('plan_next')
    def test_g1_k7_unread_final_refusal_preserves_set_and_authenticates_origin(self):
        case = unread.UnreadFinalTests('test_a7_unread_final_survives_scientific_refusal_replacement_and_origin_tampering')
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.test_a7_unread_final_survives_scientific_refusal_replacement_and_origin_tampering()

    @record_reachable_errors('plan_next')
    def test_g1_k7_provider_final_runs_all_sequences_and_executes_stop_answers(self):
        case, native, head = stage3.prepare_turn(self, 'final')
        provider = unread.UnreadFinalSupport.refuse_provider
        clock = failures.datetime
        counts = {}
        for number in range(100):
            action = plan_next(case.store.snapshot()['records'], case.artifacts, head)
            if action['state'] == 'wait':
                self.assertIn(action['code'], stop_classes.STOP_REGISTRY)
                deadline = clock.fromisoformat(action['not_before'])
                class DueClock(clock):
                    @classmethod
                    def now(cls, tz=None):
                        return deadline
                with patch.object(failures, 'datetime', DueClock):
                    following = plan_next(case.store.snapshot()['records'], case.artifacts, head)
                self.assertEqual(following['state'], 'run', following)
                action = following
            if action['state'] == 'stop':
                self.assertEqual(action['stop_class'], 'decision')
                self.assertIn(action['code'], stop_classes.STOP_REGISTRY)
                self.assertEqual(action['details']['sequence'], failures.SEQUENCE_LIMIT)
                self.assertEqual(counts, {sequence: failures.PROVIDER_REFUSAL_ATTEMPT_LIMIT
                                         for sequence in range(1, failures.SEQUENCE_LIMIT + 1)})
                stage3.execute_stop_answers(self, case, head, action)
                return
            self.assertEqual(action['state'], 'run', action)
            provider(case, action['payload']['assignment_id'], action['payload']['id'])
            records = case.store.snapshot()['records']
            position = failures.count_position(records, case.artifacts, records['review_assignment'][head])
            sequence = position['sequence']
            counts[sequence] = counts.get(sequence, 0) + 1
            self.assertEqual(position['failures_by_outcome']['provider_refused'], counts[sequence])
        self.fail('The provider final row must reach its terminal decision.')

    @record_reachable_errors('plan_next')
    def test_g1_k7_scientific_reading_two_replacements_export_carries_to_finding(self):
        self.execute_refusal_exhaustion('reading')

    @record_reachable_errors('plan_next')
    def test_g1_k7_scientific_final_two_replacements_export_carries_to_finding(self):
        self.execute_refusal_exhaustion('final')

    def execute_refusal_exhaustion(self, stage):
        case, native, head = stage3.prepare_turn(self, stage)
        if stage == 'reading':
            case.invoke(head, case.build_v7_answer(case.read_packet(head)))
            following = case.assign('refused-position', native, prior=head)
            head = following['id']
        first = head
        for number in range(3):
            case.invoke(head, dict(stage='reading', notes=42))
            action = plan_next(case.store.snapshot()['records'], case.artifacts, first)
            if number < 2:
                self.assertEqual(action['state'], 'replace', action)
                assigned = matrix.execute(case, action['command'], action['payload'])
                packet = case.read_packet(assigned['id'])
                self.assertIn('output_refusal', packet)
                self.assertEqual(assigned['reading_chain']['turn'],
                    case.store.snapshot()['records']['review_assignment'][first]['reading_chain']['turn'])
                head = assigned['id']
            else:
                self.assertEqual((action['state'], action['command']), ('handoff', 'export'), action)
                self.assertIn(action['code'], stop_classes.STOP_REGISTRY)
                reference, assigned = refusal.LegacySuccessorTests.execute_export(case, action)
                self.assertEqual(assigned['protocol_selection']['protocol'], v7.PROTOCOL)
                self.assertTrue(assigned['carried_readings']['chains'])
                self.assertEqual(plan_next(case.store.snapshot()['records'], case.artifacts, assigned['id'])['state'], 'run')
                case.finish_from(reference, assigned['id'])


class Stage3EvidenceTests(unittest.TestCase):
    @record_reachable_errors('plan_next')
    def test_g1_r39_filled_final_replaces_at_same_level_and_records(self):
        identifiers = [
            'test_openness_reading_v7_refusal.FullGrantFinalRefusalTests.test_t1_short_refused_full_final_replaces_with_whole_notice_and_records',
            'test_openness_reading_v7_refusal.FullGrantFinalRefusalTests.test_t1_large_refused_full_final_replaces_with_bounded_notice_and_records',
        ]
        self.assertEqual(guard.run_evidence_tests(identifiers), {name: 'passed' for name in identifiers})

    def test_g2_current_reach_has_no_code_without_a_registry_row(self):
        from pathlib import Path
        from ci import series_code_reach
        root = Path(__file__).resolve().parents[1]
        actual = series_code_reach.build_fixture(root)
        expected = json.loads((root / 'tests/fixtures/series_code_reach.json').read_text())
        self.assertEqual(actual, expected)
        self.assertEqual(actual['unreviewed_rows'], {})
        series_code_reach.validate_registry_coverage(actual, stop_classes.STOP_REGISTRY)

    def test_g9_stage3_incidents_name_real_shape_unit_journeys(self):
        from pathlib import Path
        ledger = json.loads((Path(__file__).parent / 'incidents/ledger.json').read_text())
        entries = {row['id']: row for row in ledger['incidents']}
        for name, modules in (
            ('reading-chain-ended-by-provider-refusal',
             {'test_d2_outcomes', 'test_openness_carried', 'test_openness_unread_final', 'test_continuity_guard_stage3'}),
            ('new-reading-chain-reads-unchanged-units',
             {'test_openness_carried', 'test_openness_carried_integration', 'test_continuity_selection'}),
        ):
            with self.subTest(incident=name):
                row = entries[name]
                self.assertEqual(row['test_kind'], 'real_shape')
                self.assertTrue(modules <= {identifier.split('.')[0] for identifier in row['tests']})
