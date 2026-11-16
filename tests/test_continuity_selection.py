"""G8: selected legacy readings reach recorded, authenticated v7 findings."""

import copy
import json
import unittest
from unittest.mock import patch

import test_openness_carried_integration as integration
from openness_reading_fixtures import OpennessReadingCase
from research_harness import openness_reading as reading, openness_reading_v7 as v7
from research_harness import review_protocol as protocol, review_route_capacity as capacity
from research_harness import openness_carried
from research_harness.evidence import digest
from research_harness.evaluation import Evaluation
import continuity_stage3_fixtures as stage3


class RecordedSelectionSupport:
    def finish_chain(self, native, first='new', answer=None):
        packets = integration.CarriedIntegrationSupport.finish_chain(self, native, first, answer)
        records = self.store.snapshot()['records']
        final = max((row for row in records['review_assignment'].values()
                     if row.get('reading_chain', {}).get('first_assignment_id') == first),
                    key=lambda row: row['recorded_revision'])
        before = copy.deepcopy(records)
        head = records['review_assignment'][first]
        selection = json.loads(self.artifacts.read(head['carried_selection']['artifact']))
        self.assertEqual(selection['protocol'], 'exactory-openness-carried-selection-v2')
        cutoff_revision = head['recorded_revision'] - 1
        cutoff = Evaluation.of(records, self.artifacts).read_snapshot(cutoff_revision)
        cutoff_digest = digest({kind: dict(rows) for kind, rows in cutoff.records.items()})
        inventory = v7.read_carried_inventory(self.artifacts, head)
        identity = v7._describe_carried_identity(records, head, records['review_route'][head['route_id']],
            json.loads(self.artifacts.read(head['context']['native_packet'])))
        self.assertEqual(openness_carried.select_carried_readings(cutoff.records, cutoff, inventory, identity),
                         selection['turns'])
        with patch.object(v7, 'check_v7_admission', side_effect=AssertionError('replanning')):
            result = stage3.record_finding(self, final['id'])
        self.assertTrue(result['reading_turns'])
        after = self.store.snapshot()['records']
        later_cutoff = Evaluation.of(after, self.artifacts).read_snapshot(cutoff_revision)
        self.assertEqual(digest({kind: dict(rows) for kind, rows in later_cutoff.records.items()}), cutoff_digest)
        self.assertEqual(openness_carried.select_carried_readings(later_cutoff.records, later_cutoff,
            inventory, identity), selection['turns'])
        for kind in ('review_assignment', 'review_attempt', 'review_invocation'):
            self.assertEqual(before[kind], after[kind])
        return packets


class SelectionCases(RecordedSelectionSupport, integration.CarriedIntegrationSupport):
    def test_g8_a4_changed_neighbour_and_changed_entry_source_record_and_verify(self):
        integration.CarriedIntegrationCases.test_g8_changed_unit_keeps_unchanged_range_and_drops_its_excerpt(self)

    def test_g8_other_reviewer_records_and_verifies_without_credit(self):
        integration.CarriedIntegrationCases.test_g8_other_reviewer_carries_nothing_and_is_named(self)

    def test_g8_other_model_effort_statement_record_and_verify_without_credit(self):
        integration.CarriedIntegrationCases.test_g8_model_effort_and_statement_mismatch_give_no_credit(self)

    def test_g8_complementary_origins_and_frozen_selection_record_and_verify(self):
        integration.CarriedIntegrationCases.test_g8_two_complementary_chains_and_later_record_do_not_change_selection(self)


class V5SelectionTests(SelectionCases, OpennessReadingCase):
    historical_v5 = True


class V6SelectionTests(SelectionCases, OpennessReadingCase):
    pass


class PartialSelectionCases(RecordedSelectionSupport, integration.CarriedIntegrationSupport):
    text_sizes = (270000, 50000)

    def test_g8_partial_unit_records_and_verifies_only_missing_range(self):
        integration.PartialRangeIntegrationTests.test_g8_partial_unit_resumes_at_its_first_missing_character(self)


class V5PartialSelectionTests(PartialSelectionCases, OpennessReadingCase):
    historical_v5 = True


class V6PartialSelectionTests(PartialSelectionCases, OpennessReadingCase):
    pass


class NotesSelectionCases(RecordedSelectionSupport, integration.CarriedIntegrationSupport):
    text_sizes = (50000,) * 5
    def build_reading_response(self, packet, excerpts=None, passages=()):
        output = super().build_reading_response(packet, excerpts=excerpts, passages=passages)
        output['notes'] = 'Earlier synthesis. ' + 'n' * 2000
        return output
    add_earlier_turns = integration.AffectedNotesIntegrationTests.add_earlier_turns
    use_floor_plan = integration.AffectedNotesIntegrationTests.use_floor_plan

    def test_g8_q12_affected_notes_deliver_required_and_fetch_optional_by_handle(self):
        self.add_earlier_turns(3)
        with self.change_inventory(self.versions[0]), self.use_floor_plan():
            native, first = self.start_chain()
            selection = json.loads(self.artifacts.read(first['carried_selection']['artifact']))
            plan = capacity.read_chain_capacity_plan(self.artifacts, first)
            groups = v7.list_required_affected_notes(plan, selection)
            self.assertEqual((len(groups['required']), len(groups['on_request'])), (2, 1))
            optional = groups['on_request'][0]['handle']
            def answer(packet):
                output = self.answer_v7(packet)
                if packet['reading_turn']['turn'] == 1:
                    output['passage_requests'] = [dict(id='optional-note', unit=optional,
                        anchor='Earlier synthesis.', characters=300)]
                return output
            packets = self.finish_chain(native, answer=answer)
            rows = [row for packet in packets if 'reading_turn' in packet
                    for row in packet['reading_turn']['plan']['now']]
            for required in groups['required']:
                self.assertTrue(any(row['handle'] == required['handle'] for row in rows))
            self.assertTrue(any(row['handle'] == optional and row.get('request') == 'optional-note' for row in rows))


class V5NotesSelectionTests(NotesSelectionCases, OpennessReadingCase):
    historical_v5 = True


class V6NotesSelectionTests(NotesSelectionCases, OpennessReadingCase):
    pass


class StoredCaptureJourneyTests(unittest.TestCase):
    def test_g8_c8_chosen_and_stopped_capture_new_export_records_and_verifies(self):
        from unbound_packet_fixtures import open_stored_case
        from codex_route_fixtures import limit_codex_input, record_codex_route
        from research_harness.problem_openness import export_openness
        import test_openness_reading_v7_refusal as refusal
        for scenario in ('workaround', 'stopped-both-observed'):
            with self.subTest(scenario=scenario):
                case, metadata = open_stored_case(self, 5, scenario)
                limit_codex_input(case, 1300000)
                route = case.store.snapshot()['records']['review_route']['route']
                record_codex_route(case, 'current-route', model=route['model'],
                    output_tokens=route['configuration']['max_output_tokens'])
                def assign(identifier, native_packet, prior=None):
                    context = dict(native_packet=native_packet)
                    if prior is not None:
                        context['prior_assignment_id'] = prior
                    return case.mutate(protocol.record_assignment, dict(id=identifier, route_id='current-route',
                        dossier_id=None, role='native_math', reviewer_id='openness-reviewer',
                        author_id=case.author_id, context=context))['result']
                case.assign = assign
                before = copy.deepcopy(case.store.snapshot()['records'])
                # Use the stored work/capture binding, but export the currently
                # derived minimum rather than replaying the old native packet.
                minimum = dict(searches=[dict(formulation=row['question'], search_id=row['id'])
                                        for row in case.searches],
                               settling_work_id=None, duplicate_resolutions=[])
                with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
                    native = export_openness(case.store, dict(assignment_id='c8-successor', reviewer_id='openness-reviewer',
                        minimum=minimum), case.root / 'c8-new-export', accept_gaps=True)['native_packet']
                    assigned = case.assign('c8-successor', native)
                    self.assertEqual(assigned['protocol_selection']['protocol'], v7.PROTOCOL)
                    delivered = []
                    original_invoke = case.invoke
                    def observe(identifier, output, attempt=None):
                        packet = case.read_packet(identifier)
                        delivered.extend(row for row in packet.get('reading_turn', {}).get('plan', {}).get('now', [])
                                         if row['kind'] == 'fulltext')
                        return original_invoke(identifier, output, attempt)
                    case.invoke = observe
                    case.build_v7_answer = lambda packet: integration.CarriedIntegrationSupport.answer_v7(case, packet)
                    import test_openness_reading_v7_turns as turn
                    case.build_v7_finding = lambda packet, handle: turn.V7CommandTests.build_v7_finding(case, packet, handle)
                    refusal.V7RefusalTests.finish_from(case, native, 'c8-successor')
                inventory = v7.read_carried_inventory(case.artifacts, assigned)
                work_units = {handle: row['identity'].get('version_id') for row in inventory['units']
                              if row['kind'] == 'fulltext' for handle in row['bodies']}
                if scenario == 'workaround':
                    self.assertEqual(delivered, [])
                else:
                    first_work = next(work for work in case.versions if work != metadata['work'])
                    self.assertFalse(any(work_units[row['handle']] == first_work for row in delivered))
                chosen = next(row for row in inventory['units'] if row['identity'].get('version_id') == metadata['work'])
                self.assertEqual(chosen['sha256'], metadata['compact']['text']['sha256'])
                self.assertEqual(any(row['handle'] in chosen['bodies'] for row in delivered), scenario != 'workaround')
                for kind in ('review_assignment', 'review_attempt', 'review_invocation'):
                    for key, row in before.get(kind, {}).items():
                        self.assertEqual(digest(row), digest(case.store.snapshot()['records'][kind][key]))
