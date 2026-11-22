"""Command regressions for D2 G8, A4 and Q12, with synthetic scientific content."""

import copy
import functools
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from openness_reading_fixtures import OpennessReadingCase
from research_harness import openness_reading as reading, openness_reading_v7 as v7
from research_harness import review_protocol as protocol, review_route_capacity as capacity
from research_harness.check_mode import check_mutation
from research_harness.evidence import digest
from research_harness.problem_openness import serialize_record
from research_harness.errors import ResearchError


@contextmanager
def remove_artifact(case, reference):
    """Take one artifact out of the case's store for the block, and put its bytes back after it."""
    path = case.root / reference['path']
    content = path.read_bytes()
    path.unlink()
    try:
        yield
    finally:
        path.write_bytes(content)


def bind_statement_searches(case, identifier, neighbours):
    """Select a statement with these neighbours, record a search for each neighbour, and return the bindings."""
    from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
    case.selected = case.select_statement(identifier=identifier, candidate_id='candidate-1', neighbours=neighbours)
    bindings = [dict(formulation=case.selected['statement'], search_id=case.search['id'])]
    for formulation in case.selected['neighbours']:
        search = ProblemOpennessMinimumTests.search(case, formulation=formulation)
        bindings.append(dict(formulation=formulation, search_id=search['id']))
    return bindings


def export_bindings(case, identifier, bindings, reviewer_id='openness-reviewer'):
    """Export the openness packet of assignment `identifier` over these search bindings."""
    from research_harness.problem_openness import export_openness
    return export_openness(case.store, dict(assignment_id=identifier, reviewer_id=reviewer_id,
        minimum=dict(searches=bindings, settling_work_id=None, duplicate_resolutions=[])),
        case.root / (identifier + '-export'))['native_packet']


class CarriedIntegrationSupport:
    reading_protocol = None
    route_model = 'gpt-6-astra'
    route_output_tokens = 128000
    text_sizes = (50000, 50000, 50000)

    def setUp(self):
        super().setUp()
        self.limit_input(1300000)
        self.native = self.export()
        self.first = self.assign('turn-1', self.native)
        self.invoke('turn-1', self.build_reading_response(self.read_packet('turn-1')))
        self.old_records = copy.deepcopy(self.store.snapshot()['records'])
        self.historical_v5 = False
        self.enabled = patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6))
        self.enabled.start()
        self.addCleanup(self.enabled.stop)

    def answer_v7(self, packet):
        binding = packet.get('reading_turn', packet.get('reading_final'))
        return dict(schema_version=2, stage='reading', notes='The delivered material has been read.',
                    excerpts=[], gaps=[], passage_requests=[],
                    **{key: binding[key] for key in ('subject_digest', 'claim_digest', 'reviewer')})

    def start_chain(self, identifier='new'):
        native = self.export(name=identifier + '-export', assignment_id=identifier)
        receipt = self.assign(identifier, native)
        self.assertEqual(receipt['protocol_selection']['protocol'], v7.PROTOCOL)
        self.assertIn('carried_selection', receipt)
        return native, receipt

    @contextmanager
    def change_inventory(self, version=None, *, add=False):
        original = v7.build_inventory
        def build(records, artifacts, native, *, review=None):
            result = original(records, artifacts, native, review=review)
            inventory = result['inventory']
            if version is not None:
                unit = next(row for row in inventory['units'] if row['identity'].get('version_id') == version)
                body = next(row for row in inventory['material'] if unit['handle'] in row['units'])
                content = ('Changed result. ' + 'z' * 3000).encode('utf-8')
                reference = artifacts.put(content, 'text/plain; charset=utf-8')
                for row in (unit, body):
                    row.update(artifact=reference, sha256=reference['sha256'], characters=len(content))
                result['bodies'][reference['sha256']] = content
            if add:
                content = b'An added search record.'
                reference = artifacts.put(content, 'text/plain; charset=utf-8')
                unit = v7._append_unit(inventory, dict(kind='record', record_kind='fixture_added',
                    record_id='added', artifact=reference), content)
                v7._bind_body(inventory, {row['sha256']: row for row in inventory['material']},
                    unit, reference, content, 'record')
                result['bodies'][reference['sha256']] = content
            return result
        with patch.object(v7, 'build_inventory', side_effect=build):
            yield

    def assert_only_unread_text(self, packets, expected):
        delivered = [row for packet in packets if 'reading_turn' in packet
            for row in packet['reading_turn']['plan']['now'] if row['kind'] == 'fulltext']
        self.assertEqual(sum(row['characters'] for row in delivered), expected)

    def complete_earlier_readings(self):
        self.enabled.stop()
        current = 'turn-1'
        for number in range(2, 10):
            following = 'earlier-%d' % number
            self.assign(following, self.native, prior=current)
            packet = self.read_packet(following)
            if 'reading_turn' not in packet:
                break
            self.invoke(following, self.build_reading_response(packet, excerpts=[]))
            current = following
        self.enabled.start()

    def finish_chain(self, native, first='new', answer=None):
        current, packets = first, []
        for number in range(1, 150):
            packet = self.read_packet(current)
            packets.append(packet)
            records = self.store.snapshot()['records']
            assignment = records['review_assignment'][current]
            request = protocol.build_assignment_request(records, self.artifacts, assignment)
            plan = capacity.read_chain_capacity_plan(self.artifacts, assignment)
            kind = 'reading' if 'reading_turn' in packet else 'final'
            route = records['review_route']['route']
            self.assertTrue(capacity.describe_plan_preflight(records, route, request, plan, kind)['plan_comparison']['fits'])
            if kind == 'final':
                binding = packet['reading_final']
                output = dict(schema_version=3,
                    **{key: binding[key] for key in ('subject_digest', 'claim_digest', 'reviewer')},
                    findings=dict({key: 'Assessed the supplied subject.' for key in
                        ('statement', 'assumptions', 'scope', 'dependencies', 'policy')},
                        openness=dict(outcome='unresolved', coverage='Reviewed the required units.',
                            reason='A remaining gap.', grounds=[dict(kind='own_argument' if 'finding_index' in packet else 'reviewer_gap',
                                text='The reviewer argument requires further checking.' if 'finding_index' in packet
                                     else 'Further work is needed.')])) )
                self.invoke(current, output)
                records = self.store.snapshot()['records']
                observed = protocol.assignment_state(records, self.artifacts, current)
                self.assertTrue(observed['ready'], observed['obligations'])
                self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, records['review_assignment'][current],
                    records['review_attempt'][observed['attempt_id']]))
                for group in ('review_assignment', 'review_attempt', 'review_invocation'):
                    for key, value in self.old_records.get(group, {}).items():
                        self.assertEqual(digest(value), digest(records[group][key]))
                return packets
            self.invoke(current, answer(packet) if answer else self.answer_v7(packet))
            following = first + '-%d' % (number + 1)
            self.mutate(protocol.record_assignment, dict(id=following, route_id=assignment['route_id'], dossier_id=None,
                role='native_math', reviewer_id=assignment['reviewer_id'], author_id=assignment['author_id'],
                context=dict(native_packet=native, prior_assignment_id=current)))
            current = following
        self.fail('The admitted chain must reach its final.')


class CarriedIntegrationCases(CarriedIntegrationSupport):
    def test_a4_check_stores_selection_and_continuation_keeps_credited_ranges(self):
        native = self.export(name='new-export', assignment_id='new')
        payload = dict(id='new', route_id='route', dossier_id=None, role='native_math',
                       reviewer_id='openness-reviewer', author_id='problem-author', context=dict(native_packet=native))
        revision = self.store.revision
        checked = check_mutation(self.store, protocol.record_assignment, payload, expected_revision=revision,
                                 request_id='selection-check', bounded_payload=payload)
        self.assertEqual(self.store.revision, revision)
        receipt = self.assign('new', native)
        self.assertEqual(checked['carried_selection'], receipt['carried_selection'])
        selection = json.loads(self.artifacts.read(receipt['carried_selection']['artifact']))
        self.assertEqual(digest(selection), receipt['carried_selection']['digest'])
        self.assertEqual(self.artifacts.read(receipt['carried_selection']['artifact']), serialize_record(selection))
        self.assertEqual(receipt['carried_selection']['artifact']['sha256'], receipt['carried_selection']['digest'])
        self.assertEqual(len(selection['turns']), 1)
        self.assertTrue(receipt['carried_readings']['credited_characters'])
        packets = self.finish_chain(native)
        self.assertTrue(packets[0]['reading_turn']['notes'])
        delivered = [row for packet in packets if 'reading_turn' in packet for row in packet['reading_turn']['plan']['now']]
        self.assertEqual(sum(row['characters'] for row in delivered if row['kind'] == 'fulltext'), 50000)

    def test_g8_changed_unit_keeps_unchanged_range_and_drops_its_excerpt(self):
        with self.change_inventory(self.versions[1], add=True):
            native, receipt = self.start_chain()
            self.assertEqual(receipt['carried_readings']['notes']['affected'], 1)
            packet = self.read_packet('new')
            plan, inventory, state = v7.read_bound_state(self.artifacts, receipt, packet)
            self.assertTrue(state['turns'][0]['origin'])
            self.assertEqual(len(state['entries']), 1)
            self.assertIsNone(state['latest_notes'])
            self.assertEqual(packet['reading_turn']['plan']['now'][0]['kind'], 'carried_reading')
            self.assertIn('units', packet['evidence'][0]['content'])
            packets = self.finish_chain(native)
        self.assert_only_unread_text(packets, 3016 + 50000)
        self.assertNotIn(self.build_reading_response(self.read_packet('turn-1'))['notes'],
                         [row['text'] for row in packets[-1]['reading_final']['fill']])

    def test_g8_other_reviewer_carries_nothing_and_is_named(self):
        from research_harness.problem_openness import export_openness
        native = export_openness(self.store, dict(assignment_id='new', reviewer_id='another-reviewer',
            minimum=dict(searches=[dict(formulation=self.selected['statement'], search_id=self.search['id'])],
                         settling_work_id=None, duplicate_resolutions=[])), self.root / 'other-export')['native_packet']
        receipt = self.mutate(protocol.record_assignment, dict(id='new', route_id='route', dossier_id=None,
            role='native_math', reviewer_id='another-reviewer', author_id='problem-author', context=dict(native_packet=native)))['result']
        selection = json.loads(self.artifacts.read(receipt['carried_selection']['artifact']))
        self.assertEqual(selection['turns'], [])
        passed = receipt['carried_readings']['passed_over']
        self.assertEqual([row['chain'] for row in passed], ['turn-1'])
        self.assertIn('reviewer_id', passed[0]['message'])
        self.assert_only_unread_text(self.finish_chain(native), sum(self.text_sizes))

    def test_g8_model_effort_and_statement_mismatch_give_no_credit(self):
        original = v7._describe_carried_identity
        for index, field in enumerate(('model', 'effort', 'statement')):
            def identity(*args):
                value = original(*args)
                value = copy.deepcopy(value)
                if field == 'model':
                    value['route']['model'] = 'another-model'
                elif field == 'effort':
                    value['route']['configuration']['effort'] = 'max'
                else:
                    value['subject_digest'] = 'f' * 64
                return value
            with self.subTest(field=field), patch.object(v7, '_describe_carried_identity', side_effect=identity):
                native, receipt = self.start_chain('new%d' % index)
                selection = json.loads(self.artifacts.read(receipt['carried_selection']['artifact']))
                self.assertEqual(selection['turns'], [])
                self.assertEqual(receipt['carried_readings']['passed_over'], [])
                self.assert_only_unread_text(self.finish_chain(native, 'new%d' % index), sum(self.text_sizes))

    def test_g8_two_complementary_chains_and_later_record_do_not_change_selection(self):
        self.enabled.stop()
        native = self.export(priority=list(reversed(self.versions)), name='complement-export', assignment_id='complement')
        self.assign('complement', native)
        self.invoke('complement', self.build_reading_response(self.read_packet('complement'), excerpts=[]))
        self.enabled.start()
        native, receipt = self.start_chain()
        selection = json.loads(self.artifacts.read(receipt['carried_selection']['artifact']))
        self.assertEqual({row['origin']['chain'] for row in selection['turns']}, {'turn-1', 'complement'})
        # Later records cannot change the selection recomputed at its authenticated cutoff.
        self.enabled.stop()
        later = self.export(name='later-export', assignment_id='later')
        self.assign('later', later)
        self.invoke('later', self.build_reading_response(self.read_packet('later'), excerpts=[]))
        self.enabled.start()
        packets = self.finish_chain(native)
        self.assert_only_unread_text(packets, 0)
        self.assertEqual(digest(json.loads(self.artifacts.read(receipt['carried_selection']['artifact']))),
                         receipt['carried_selection']['digest'])

    def test_q12_pool_overflow_is_delivered_and_keep_preserves_handle_span_and_target_charge(self):
        self.enabled.stop()
        origin = self.export(name='pool-origin-export', assignment_id='pool-origin')
        self.assign('pool-origin', origin)
        origin_packet = self.read_packet('pool-origin')
        answer = self.build_reading_response(origin_packet, excerpts=[])
        for row in origin_packet['reading_turn']['reading_plan']['delivered_now']:
            if row.get('kind') != 'fulltext':
                continue
            body = next(item['content'] for item in origin_packet['evidence']
                        if item['artifact']['sha256'] == row['artifact']['sha256'])
            answer['excerpts'].extend(dict(version_id=row['version_id'], text=body[:100 + index],
                purpose='A distinct retained interval.') for index in range(100))
        observed = self.invoke('pool-origin', answer)
        self.assertNotIn('output_refusal', observed, observed.get('output_refusal'))
        exposure = self.artifacts.put(b'Synthetic exposure of the short earlier prefix.', 'text/plain')
        self.mutate(protocol.record_context_event, dict(id='short-prefix-exposure', assignment_id='turn-1',
            kind='contamination', source='author_history', reason='Use the independently reread large prefix.',
            evidence=[exposure]))
        self.enabled.start()
        native, receipt = self.start_chain()
        packet = self.read_packet('new')
        _, inventory, state = v7.read_bound_state(self.artifacts, receipt, packet)
        self.assertTrue(state['pool'])
        self.assertTrue(any(row['kind'] == 'carried_reading' for row in inventory['units']),
            dict(entries=len(state['entries']), pool=state['allowances']['pool']))
        self.assertEqual(packet['reading_turn']['plan']['now'][0]['kind'], 'carried_reading')
        available = v7._list_delivered_carried_entries(packet, self.artifacts, receipt)
        self.assertTrue(available)
        kept = next(iter(available.values()))
        def answer(packet):
            output = self.answer_v7(packet)
            if packet['reading_turn']['turn'] == 1:
                output['excerpts'] = [dict(keep=kept['handle'], targets=['t1'])]
            return output
        packets = self.finish_chain(native, answer=answer)
        final = packets[-1]['reading_final']
        self.assertCountEqual([row['handle'] for row in final['entries']], state['pool'] + [kept['handle']])
        retained = next(row for row in final['entries'] if row['handle'] == kept['handle'])
        for key in ('unit', 'start', 'end', 'sha256', 'text'):
            self.assertEqual(retained[key], kept[key])
        self.assertGreater(final['allowances']['targets'][0]['used'], len(kept['text']))
        recorded = self.mutate(self.load_problem_module().record_openness,
            dict(assignment_id=final['chain'] if final['turn'] == 1 else 'new-%d' % final['turn']))['result']
        self.assertEqual(recorded['outcome'], 'unresolved')

    def test_keep_rejects_a_handle_that_was_not_delivered(self):
        native, receipt = self.start_chain()
        packet = self.read_packet('new')
        output = self.answer_v7(packet)
        output['excerpts'] = [dict(keep='e1', targets=['t1'])]
        with self.assertRaises(ResearchError) as error:
            v7.validate_turn_output(packet, output, artifacts=self.artifacts, assignment=receipt)
        self.assertEqual(error.exception.code, reading.INVALID_RESPONSE)
        self.finish_chain(native)

    def test_keep_rejects_unknown_duplicate_and_malformed_entries(self):
        with patch.object(v7, 'describe_carried_pool', return_value=0):
            native, receipt = self.start_chain()
        packet = self.read_packet('new')
        for excerpts in ([dict(keep='e99999', targets=['t1'])],
                         [dict(keep='e1', targets=['t1'])] * 2,
                         [dict(keep='e1', targets=[])],
                         [dict(keep='e1', targets=['t999'])],
                         [dict(keep=[], targets=['t1'])],
                         [dict(keep='e1', targets=['t1'], extra=True)]):
            output = dict(self.answer_v7(packet), excerpts=excerpts)
            with self.subTest(excerpts=excerpts), self.assertRaises(ResearchError) as error:
                v7.validate_turn_output(packet, output, artifacts=self.artifacts, assignment=receipt)
            self.assertEqual(error.exception.code, reading.INVALID_RESPONSE)
        self.finish_chain(native)

    def test_current_legacy_notes_reserve_their_serialized_cost(self):
        self.enabled.stop()
        self.assign('old-2', self.native, prior='turn-1')
        output = self.build_reading_response(self.read_packet('old-2'), excerpts=[])
        output['notes'] = 'A current synthesis. ' + '\\"' * 9500
        self.invoke('old-2', output)
        self.enabled.start()
        native, receipt = self.start_chain()
        packet = self.read_packet('new')
        plan = capacity.read_chain_capacity_plan(self.artifacts, receipt)
        self.assertGreater(plan['allocation']['initial_notes_characters'], plan['allocation']['notes'])
        self.assertEqual(packet['reading_turn']['notes'], output['notes'])
        self.finish_chain(native)

    def test_pool_charge_is_included_in_a_denser_ratio_handoff(self):
        native, receipt = self.start_chain()
        self.invoke('new', self.answer_v7(self.read_packet('new')))
        records = copy.deepcopy(self.store.snapshot()['records'])
        records['review_attempt']['attempt-new']['usage'].update(sends=1, request_characters=100000, input_tokens=100000)
        plan = capacity.read_chain_capacity_plan(self.artifacts, receipt)
        without_pool = plan['allocation']['notes'] + sum(row['allowance'] for row in plan['allocation']['targets'])
        self.assertGreater(plan['allocation']['pool_used'], 0)
        with patch.object(capacity, 'derive_turn_task_limits', return_value=dict(material_allowance=without_pool)):
            result = v7.describe_ratio_handoff(records, self.artifacts, records['review_assignment']['new'])
        self.assertEqual(result['state'], 'handoff')
        self.assertEqual(result['details']['granted_characters'], without_pool + plan['allocation']['pool_used'])
        # The authentic low-usage stub chain itself remains finishable.
        self.assign('new-2', native, prior='new')
        self.finish_chain(native, 'new-2')

    def test_m3_third_chain_reads_changed_and_added_units_only(self):
        self.complete_earlier_readings()
        self.enabled.stop()
        native = self.export(name='second-export', assignment_id='second')
        self.assign('second', native)
        self.invoke('second', self.build_reading_response(self.read_packet('second'), excerpts=[]))
        self.enabled.start()
        with self.change_inventory(self.versions[1], add=True):
            native, receipt = self.start_chain()
            packets = self.finish_chain(native)
        self.assert_only_unread_text(packets, 3016)
        inventory = json.loads(self.artifacts.read(receipt['reading_chain']['inventory']))
        material = {row['handle']: row for row in inventory['material']}
        logical = {row['handle']: row for row in inventory['units']}
        remaining = [dict(row, kind=material[row['unit']]['kind'], identities=[logical[handle]['identity']
            for handle in material[row['unit']]['units']]) for row in receipt['carried_readings']['remaining_material']]
        print('M3_THIRD_CHAIN ' + json.dumps(dict(units=remaining,
            characters=receipt['carried_readings']['remaining_material_characters']), sort_keys=True))

    def test_credited_search_bodies_supply_receipts_to_an_added_search(self):
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        self.complete_earlier_readings()
        self.searches.append(ProblemOpennessMinimumTests.search(self, self.versions))
        native, receipt = self.start_chain()
        self.assertIn(digest(self.search), receipt['carried_readings']['searches'])
        packets = self.finish_chain(native)
        final_id = 'new-%d' % len(packets)
        assignment = self.store.snapshot()['records']['review_assignment'][final_id]
        _, _, state = v7.read_bound_state(self.artifacts, assignment, packets[-1])
        self.assertEqual(set(state['searches']), {digest(row) for row in self.searches})


class V6CarriedIntegrationTests(CarriedIntegrationCases, OpennessReadingCase):
    pass


class V5CarriedIntegrationTests(CarriedIntegrationCases, OpennessReadingCase):
    historical_v5 = True


class PartialRangeIntegrationTests(CarriedIntegrationSupport, OpennessReadingCase):
    text_sizes = (270000, 50000)

    def test_g8_partial_unit_resumes_at_its_first_missing_character(self):
        native, receipt = self.start_chain()
        packet = self.read_packet('new')
        _, inventory, state = v7.read_bound_state(self.artifacts, receipt, packet)
        unit = next(row for row in inventory['units'] if row['identity'].get('version_id') == self.versions[0])
        spans = [row for row in state['deliveries'] if row['unit'] == unit['handle']]
        end = max(row['end'] for row in spans)
        self.assertGreater(end, 0)
        self.assertLess(end, self.text_sizes[0])
        first = next(row for row in packet['reading_turn']['plan']['now'] if row['handle'] in unit['bodies'])
        self.assertEqual(first['start'], end)
        packets = self.finish_chain(native)
        self.assert_only_unread_text(packets, sum(self.text_sizes) - end)


class V5PartialRangeIntegrationTests(PartialRangeIntegrationTests):
    historical_v5 = True


class EmptySelectionIntegrationTests(OpennessReadingCase):
    reading_protocol = None
    route_model = 'gpt-6-astra'
    route_output_tokens = 128000
    text_sizes = (50000,) * 3
    answer_v7 = CarriedIntegrationSupport.answer_v7
    finish_chain = CarriedIntegrationSupport.finish_chain

    def test_no_earlier_chain_stores_an_empty_selection_and_reads_every_unit(self):
        self.old_records = copy.deepcopy(self.store.snapshot()['records'])
        native = self.export(name='new-export', assignment_id='new')
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
            receipt = self.assign('new', native)
            selection = json.loads(self.artifacts.read(receipt['carried_selection']['artifact']))
            self.assertEqual(selection['turns'], [])
            self.assertEqual(selection['affected_notes'], [])
            packets = self.finish_chain(native)
        CarriedIntegrationSupport.assert_only_unread_text(self, packets, sum(self.text_sizes))


class MultiTargetKeepIntegrationTests(CarriedIntegrationSupport, OpennessReadingCase):
    def select_statement(self, *args, **kwargs):
        kwargs.setdefault('neighbours', ['A neighbouring synthetic formulation.'])
        return super().select_statement(*args, **kwargs)

    def record_searches(self):
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        return [ProblemOpennessMinimumTests.search(self, self.versions, formulation=formulation)
                for formulation in [self.selected['statement']] + self.selected['neighbours']]

    def export(self, priority=(), name='openness-packet', assignment_id='turn-1'):
        from research_harness.problem_openness import export_openness
        minimum = dict(searches=[dict(formulation=row['question'], search_id=row['id']) for row in self.searches],
                       settling_work_id=None, duplicate_resolutions=[])
        if priority:
            minimum['text_priority'] = list(priority)
        return export_openness(self.store, dict(assignment_id=assignment_id, reviewer_id='openness-reviewer',
            minimum=minimum), self.root / name)['native_packet']

    def test_keep_charges_every_named_target(self):
        with patch.object(v7, 'describe_carried_pool', return_value=0):
            native, receipt = self.start_chain()
        def answer(packet):
            output = self.answer_v7(packet)
            if packet['reading_turn']['turn'] == 1:
                output['excerpts'] = [dict(keep='e1', targets=['t1', 't2'])]
            return output
        packets = self.finish_chain(native, answer=answer)
        rows = packets[-1]['reading_final']['allowances']['targets']
        self.assertEqual(rows[0]['used'], rows[1]['used'])
        self.assertGreater(rows[0]['used'], 0)


class AffectedNotesIntegrationTests(CarriedIntegrationSupport, OpennessReadingCase):
    text_sizes = (50000,) * 5

    def build_reading_response(self, packet, excerpts=None, passages=()):
        output = super().build_reading_response(packet, excerpts=excerpts, passages=passages)
        output['notes'] = 'Earlier synthesis. ' + 'n' * 2000
        return output

    def add_earlier_turns(self, count):
        self.enabled.stop()
        prior = 'turn-1'
        for number in range(2, count + 1):
            identifier = 'old-%d' % number
            self.assign(identifier, self.native, prior=prior)
            packet = self.read_packet(identifier)
            self.assertIn('reading_turn', packet)
            self.invoke(identifier, self.build_reading_response(packet, excerpts=[]))
            prior = identifier
        self.enabled.start()

    @contextmanager
    def use_floor_plan(self):
        original = v7.check_v7_admission
        def admit(*args):
            result = original(*args)
            self.assertEqual(result['state'], 'record')
            plan = result['capacity_plan']
            plan['task']['reading'] = capacity.derive_plan_task_limits(plan, 'reading', [dict(material_allowance=15000)])
            return result
        with patch.object(v7, 'check_v7_admission', side_effect=admit):
            yield

    def test_r71_two_affected_notes_fit_and_precede_other_units(self):
        self.add_earlier_turns(2)
        with self.change_inventory(self.versions[0]), self.use_floor_plan():
            native, receipt = self.start_chain()
            selection = json.loads(self.artifacts.read(receipt['carried_selection']['artifact']))
            plan = capacity.read_chain_capacity_plan(self.artifacts, receipt)
            groups = v7.list_required_affected_notes(plan, selection)
            self.assertEqual(len(groups['required']), 2, selection['affected_notes'])
            self.assertEqual(groups['on_request'], [])
            packets = self.finish_chain(native)
        rows = [row for packet in packets if 'reading_turn' in packet for row in packet['reading_turn']['plan']['now']]
        self.assertEqual([row['kind'] for row in rows[:2]], ['carried_reading', 'carried_reading'])

    def test_affected_selection_verifies_frozen_inventory_and_rejects_repriced_handle(self):
        from research_harness import openness_carried
        with self.change_inventory(self.versions[0]), self.use_floor_plan():
            native, receipt = self.start_chain()
            records = self.store.snapshot()['records']
            selection = json.loads(self.artifacts.read(receipt['carried_selection']['artifact']))
            inventory = v7.read_carried_inventory(self.artifacts, receipt)
            self.assertEqual(digest(inventory), selection['inventory_digest'])
            plan = capacity.read_chain_capacity_plan(self.artifacts, receipt)
            identity = v7._describe_carried_identity(records, receipt, records['review_route']['route'],
                json.loads(self.artifacts.read(native)))
            projections = openness_carried.verify_carried_selection(records, self.artifacts, inventory, identity, selection, plan=plan)
            self.assertEqual(projections, openness_carried.verify_carried_selection(records, self.artifacts, inventory,
                identity, receipt['carried_selection']['artifact'], plan=plan))
            self.assertEqual(openness_carried.describe_carried_selection(records, self.artifacts, inventory,
                identity, selection, plan=plan), receipt['carried_readings'])
            with self.assertRaises(ResearchError):
                openness_carried.verify_carried_selection(records, self.artifacts, inventory, identity, selection)
            malformed = dict(selection, affected_notes=None)
            with self.assertRaises(ResearchError):
                openness_carried.verify_carried_selection(records, self.artifacts, inventory, identity, malformed, plan=plan)
            changed = copy.deepcopy(selection)
            changed['affected_notes'][0] = v7.describe_affected_note_delivery(identity['route'],
                projections[0]['turn']['origin'], projections[0]['notes'], 'u999999')
            with self.assertRaises(ResearchError):
                openness_carried.verify_carried_selection(records, self.artifacts, inventory, identity, changed, plan=plan)
            self.finish_chain(native)

    def test_r71_required_sets_stay_fixed_after_a_real_level_replacement(self):
        from codex_route_fixtures import build_codex_response, bind_codex_response
        from research_harness.review_failure_class import plan_after_failure
        self.add_earlier_turns(3)
        with self.change_inventory(self.versions[0]):
            native, receipt = self.start_chain()
            selection = json.loads(self.artifacts.read(receipt['carried_selection']['artifact']))
            plan = capacity.read_chain_capacity_plan(self.artifacts, receipt)
            expected = v7.list_required_affected_notes(plan, selection)
            start = self.read_packet('new')['reading_turn']['capacity']['material_allowance']
            def respond(route, request, credential):
                response = build_codex_response(route, request, {})
                response.update(status='incomplete')
                response['usage']['output_tokens'] = 128001
                return bind_codex_response(route, request, response)
            with patch.object(protocol.transport, 'send_request', side_effect=respond):
                self.mutate(protocol.invoke_assignment, dict(id='failed-new', assignment_id='new'), credential='fixture')
            records = self.store.snapshot()['records']
            recovery = plan_after_failure(records, self.artifacts, 'new')
            self.assertEqual(recovery['state'], 'replace')
            payload = dict(recovery['details']['recovery']['payload'], id='replacement')
            assigned = self.mutate(protocol.record_assignment, payload)['result']
            lowered = self.read_packet('replacement')['reading_turn']['capacity']['material_allowance']
            self.assertLess(lowered, start)
            stored = capacity.read_chain_capacity_plan(self.artifacts, assigned)
            self.assertEqual(v7.list_required_affected_notes(stored, selection), expected)
            self.finish_chain(native, 'replacement')

    def test_r71_three_notes_two_required_optional_index_and_passage(self):
        self.add_earlier_turns(3)
        with self.change_inventory(self.versions[0]), self.use_floor_plan():
            native, receipt = self.start_chain()
            selection = json.loads(self.artifacts.read(receipt['carried_selection']['artifact']))
            plan = capacity.read_chain_capacity_plan(self.artifacts, receipt)
            groups = v7.list_required_affected_notes(plan, selection)
            self.assertEqual(len(groups['required']), 2)
            self.assertEqual(len(groups['on_request']), 1)
            optional = groups['on_request'][0]
            packet = self.read_packet('new')
            self.assertEqual(packet['reading_turn']['affected_notes']['notes'], 1)
            self.assertEqual(packet['reading_turn']['affected_notes']['index']['rows'][0]['handle'], optional['handle'])
            # Lower current levels have no input to the pure partition function.
            lower = capacity.derive_plan_task_limits(plan, 'reading', [dict(outcome='output_over_cap', material_allowance=15000)])
            self.assertEqual(v7.list_required_affected_notes(plan, selection), groups)
            def answer(packet):
                output = self.answer_v7(packet)
                if packet['reading_turn']['turn'] == 1:
                    output['passage_requests'] = [dict(id='optional-note', unit=optional['handle'], anchor='Earlier synthesis.', characters=300)]
                return output
            packets = self.finish_chain(native, answer=answer)
            self.assertTrue(any(row.get('request') == 'optional-note' for packet in packets if 'reading_turn' in packet
                for row in packet['reading_turn']['plan']['now']))
            # A second new chain may reach final without requesting the optional note.
            native, receipt = self.start_chain('without')
            packets = self.finish_chain(native, 'without')
            self.assertFalse(any(row.get('request') for packet in packets if 'reading_turn' in packet
                for row in packet['reading_turn']['plan']['now']))

    def test_r71_one_oversized_note_is_required_and_delivered_in_parts(self):
        # Keep the real shape and authentication of an earlier chain; escaped
        # notes cost more in the new unit than their legacy character allowance.
        self.enabled.stop()
        self.assign('old-2', self.native, prior='turn-1')
        output = self.build_reading_response(self.read_packet('old-2'), excerpts=[])
        output['notes'] = 'Earlier synthesis. ' + '\\"' * 3000
        self.invoke('old-2', output)
        self.enabled.start()
        with self.change_inventory(self.versions[0]), self.use_floor_plan():
            native, receipt = self.start_chain()
            selection = json.loads(self.artifacts.read(receipt['carried_selection']['artifact']))
            plan = capacity.read_chain_capacity_plan(self.artifacts, receipt)
            groups = v7.list_required_affected_notes(plan, selection)
            self.assertEqual(len(groups['required']), 1)
            self.assertGreater(groups['required'][0]['delivery_characters'], 15000)
            packets = self.finish_chain(native)
            handle = groups['required'][0]['handle']
            parts = [row for packet in packets if 'reading_turn' in packet
                for row in packet['reading_turn']['plan']['now'] if row['handle'] == handle]
            self.assertGreater(len(parts), 1)
            self.assertEqual(parts[0]['start'], 0)
            self.assertEqual(parts[-1]['end'], groups['required'][0]['characters'])


class ManyNotesIntegrationTests(CarriedIntegrationSupport, OpennessReadingCase):
    def test_q12_four_hundred_on_request_origins_have_bounded_index_and_complete_pages(self):
        def prepare(records, artifacts, value, route, logical, admission):
            # This capacity fixture supplies the authenticated-projection boundary;
            # G8 above separately exercises real v5/v6 observation at that boundary.
            projections = []
            for number in range(401):
                origin = dict(chain='origin-%03d-' % number + 'x' * 40, protocol=reading.PROTOCOL_V6,
                    assignment_id='old-%d' % number, attempt_id='attempt-%d' % number)
                projections.append(dict(turn=dict(origin=origin, assignment_id=origin['assignment_id'],
                    attempt_id=origin['attempt_id'], kind='reading'), deliveries=[], entries=[], searches=[],
                    notes=dict(classification='affected', units=[], text='Earlier note. ' + ('n' * 16000 if number == 400 else 'n' * 50)),
                    gaps=[], arguments=[], allowance_use=dict(targets={}, passages=0)))
            plan = admission['capacity_plan']
            plan['task']['reading'] = capacity.derive_plan_task_limits(plan, 'reading', [dict(material_allowance=15000)])
            return v7.prepare_first_assignment(records, artifacts, value, route, logical, projections, admission=admission)
        with patch.object(v7, 'prepare_carried_assignment', side_effect=prepare):
            native, receipt = self.start_chain()
        packet = self.read_packet('new')
        index = packet['reading_turn']['affected_notes']['index']
        self.assertLessEqual(len(serialize_record(index).decode('utf-8')), 10000)
        self.assertGreater(index['omitted'], 0)
        self.assertEqual(packet['reading_turn']['affected_notes']['notes'], 400)
        seen, started, ended = [], False, False
        def answer(packet):
            nonlocal started, ended
            output = self.answer_v7(packet)
            page = next((json.loads(body['content']) for row, body in zip(packet['reading_turn']['plan']['now'], packet['evidence'])
                         if row['kind'] == 'list_page'), None)
            if page is not None:
                seen.extend(row['chain'] for row in page['rows'])
                if page['next'] is None:
                    ended = True
                else:
                    output['passage_requests'] = [dict(id='page-%d' % packet['reading_turn']['turn'],
                        kind='list_page', list='affected_notes', after=page['next'])]
            elif not started:
                started = True
                output['passage_requests'] = [dict(id='page-first', kind='list_page', list='affected_notes', after=None)]
            return output
        packets = self.finish_chain(native, answer=answer)
        self.assertTrue(ended)
        self.assertEqual(seen, ['origin-%03d-' % n + 'x' * 40 for n in range(399, -1, -1)])
        self.assertEqual(len(seen), len(set(seen)))
        self.assertTrue(all(packet['reading_turn']['affected_notes']['notes'] == 400
                            for packet in packets if 'reading_turn' in packet))


class FallbackCarriedIntegrationTests(OpennessReadingCase):
    reading_protocol = None
    route_model = 'gpt-6-astra'
    route_output_tokens = 128000
    text_sizes = (50000,) * 3

    def test_a4_floor_final_with_one_extra_target_uses_v6_and_names_would_carry(self):
        from research_harness.problem_openness import export_openness
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        self.limit_input(1300000)
        self.selected = self.select_statement(identifier='nine-targets', candidate_id='candidate-1',
            neighbours=['Neighbour %d.' % n for n in range(8)])
        bindings = [dict(formulation=self.selected['statement'], search_id=self.search['id'])]
        for formulation in self.selected['neighbours']:
            search = ProblemOpennessMinimumTests.search(self, formulation=formulation)
            bindings.append(dict(formulation=formulation, search_id=search['id']))
        def export(identifier):
            return export_openness(self.store, dict(assignment_id=identifier, reviewer_id='openness-reviewer',
                minimum=dict(searches=bindings, settling_work_id=None, duplicate_resolutions=[])),
                self.root / (identifier + '-export'))['native_packet']
        old = export('old')
        first = self.assign('old', old)
        self.invoke('old', self.build_reading_response(self.read_packet('old'), excerpts=[]))
        old_digest = digest(self.store.snapshot()['records']['review_assignment']['old'])
        native = export('new')
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
            payload = dict(id='new', route_id='route', dossier_id=None, role='native_math', reviewer_id='openness-reviewer',
                author_id='problem-author', context=dict(native_packet=native))
            revision = self.store.revision
            checked = check_mutation(self.store, protocol.record_assignment, payload, expected_revision=revision,
                request_id='fallback-check', bounded_payload=payload)
            self.assertEqual(self.store.revision, revision)
            receipt = self.assign('new', native)
        self.assertEqual(checked['carried_readings'], receipt['carried_readings'])
        self.assertEqual(receipt['protocol_selection']['protocol'], reading.PROTOCOL_V6)
        self.assertNotIn('carried_selection', receipt)
        self.assertEqual(receipt['carried_readings']['chains'], [])
        self.assertEqual(receipt['carried_readings']['would_carry'], ['old'])
        self.assertEqual(receipt['protocol_selection']['admissions'][0]['largest_one_request_targets'], 8)
        current = 'new'
        for number in range(1, 10):
            packet = self.read_packet(current)
            if 'reading_turn' not in packet:
                break
            self.invoke(current, self.build_reading_response(packet, excerpts=[]))
            following = 'new-%d' % (number + 1)
            self.assign(following, native, prior=current)
            current = following
        else:
            self.fail('The fallback must reach final.')
        self.invoke(current, self.build_native_result(packet, outcome='unresolved'))
        records = self.store.snapshot()['records']
        state = protocol.assignment_state(records, self.artifacts, current)
        self.assertTrue(state['ready'], state['obligations'])
        self.assertEqual(digest(records['review_assignment']['old']), old_digest)
        request = protocol.build_assignment_request(records, self.artifacts, records['review_assignment'][current])
        self.assertLess(capacity.measure_request(records['review_route']['route'], request)['text_characters'], 1300000)


class Incident57IntegrationTests(OpennessReadingCase):
    reading_protocol = None
    historical_v5 = True
    route_model = 'gpt-6-astra'
    route_output_tokens = 128000
    text_sizes = (23000,) * 25 + (1000,) * 4
    answer_v7 = CarriedIntegrationSupport.answer_v7
    finish_chain = CarriedIntegrationSupport.finish_chain

    def record_searches(self):
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        return [ProblemOpennessMinimumTests.search(self, self.versions[index:index + 10])
                for index in range(0, len(self.versions), 10)]

    def test_q12_incident_57_v5_turns_carry_without_fulltext_redelivery(self):
        from research_harness import review_excerpts
        from test_review_output_profile import build_row
        self.limit_input(1300000)
        native = self.export(assignment_id='old-1')
        # Scale synthetic material and the legacy turn width together. Choose
        # the width from the actual v5 unit plan so all old records, as well as
        # all texts, finish in exactly 57 authenticated reading commands.
        records = self.store.snapshot()['records']
        binding = json.loads(self.artifacts.read(native))
        rows = reading.list_reading_rows(records, self.artifacts, binding)
        delivery = reading._plan_search_delivery(self.artifacts, binding)
        for content in delivery['bodies'].values():
            self.artifacts.put(content, 'application/json')
        physical = reading.list_delivery_rows(rows, delivery['manifest'])
        width = None
        for candidate in range(10000, 20001, 100):
            with patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', candidate):
                units = reading.list_reading_units(self.artifacts, physical)
            turns, used = 1, 0
            for unit in units:
                if used and used + unit['characters'] > candidate:
                    turns, used = turns + 1, 0
                used += unit['characters']
            if turns == 57:
                width = candidate
                break
        self.assertIsNotNone(width, 'The synthetic old inventory must have a 57-turn partition.')
        chunking = patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', width)
        chunking.start()
        self.addCleanup(chunking.stop)
        prior = None
        for number in range(1, 58):
            identifier = 'old-%d' % number
            self.assign(identifier, native, prior=prior)
            packet = self.read_packet(identifier)
            self.assertIn('reading_turn', packet)
            answer = self.build_reading_response(packet, excerpts=[])
            delivered = packet['reading_turn']['reading_plan']['delivered_now']
            for row in delivered:
                if row.get('kind') != 'fulltext':
                    continue
                body = next(body['content'] for body in packet['evidence'] if body['artifact']['sha256'] == row['artifact']['sha256'])
                answer['excerpts'].extend(dict(version_id=row['version_id'], text=body[:170],
                    purpose='Relevance to the exact statement. ' + 'p' * 90) for _ in range(19))
            self.invoke(identifier, answer)
            prior = identifier
        self.assertEqual(packet['reading_turn']['reading_plan']['remaining'], [])
        self.old_records = copy.deepcopy(self.store.snapshot()['records'])
        old_digests = {key: digest(row) for key, row in self.old_records['review_attempt'].items()}
        self.historical_v5 = False
        native = self.export(name='new-export', assignment_id='new')
        priors = [build_row(level=None, thinking=1000, count=8), build_row(kind='final', level=None, thinking=1000, count=8)]
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)), patch.object(
                capacity, 'read_profile_priors', return_value=priors):
            receipt = self.assign('new', native)
            self.assertEqual(receipt['protocol_selection']['protocol'], v7.PROTOCOL)
            selection = json.loads(self.artifacts.read(receipt['carried_selection']['artifact']))
            self.assertEqual(len(selection['turns']), 57)
            def answer(packet):
                value = self.answer_v7(packet)
                records = self.store.snapshot()['records']
                assigned = records['review_assignment'][packet['reading_turn']['chain'] if packet['reading_turn']['turn'] == 1
                    else 'new-%d' % packet['reading_turn']['turn']]
                available = v7._list_delivered_carried_entries(packet, self.artifacts, assigned)
                value['excerpts'] = [dict(keep=handle, targets=['t1']) for handle in list(available)[:1]]
                return value
            packets = self.finish_chain(native, answer=answer)
        self.assertFalse(any(row['kind'] == 'fulltext' for packet in packets if 'reading_turn' in packet
                             for row in packet['reading_turn']['plan']['now']))
        records = self.store.snapshot()['records']
        last = records['review_assignment']['new-%d' % len(packets)]
        verified = reading.verify_chain(records, self.artifacts, last)
        self.assertEqual(verified[-1]['assignment_id'], last['id'])
        recorded = self.mutate(self.load_problem_module().record_openness,
                              dict(assignment_id=last['id']))['result']
        self.assertEqual(recorded['outcome'], 'unresolved')
        _, _, state = v7.read_bound_state(self.artifacts, last, packets[-1])
        request = protocol.build_assignment_request(records, self.artifacts, last)
        route = records['review_route']['route']
        result = dict(pool_characters=state['allowances']['pool']['used'],
            carried_reading_turns=sum(any(row['kind'] == 'carried_reading' for row in packet['reading_turn']['plan']['now'])
                for packet in packets if 'reading_turn' in packet),
            final_start=capacity.read_chain_capacity_plan(self.artifacts, receipt)['task']['final']['material_allowance'],
            final_request=capacity.measure_request(route, request)['text_characters'],
            remaining_units=receipt['carried_readings']['remaining_material'],
            remaining_characters=receipt['carried_readings']['remaining_material_characters'], legacy_turn_width=width)
        print('CARRIED_57_MEASUREMENTS ' + json.dumps(result, sort_keys=True))
        self.assertEqual(old_digests, {key: digest(records['review_attempt'][key]) for key in old_digests})

class StoredSelectionIntegrationTests(CarriedIntegrationSupport, OpennessReadingCase):
    """A cutoff binds content records as well as the records of earlier reviews."""

    def read_selection_context(self, first='new', records=None):
        from research_harness.evaluation import Evaluation
        records = self.store.snapshot()['records'] if records is None else records
        assignment = records['review_assignment'][first]
        native = json.loads(self.artifacts.read(assignment['context']['native_packet']))
        selection = json.loads(self.artifacts.read(assignment['carried_selection']['artifact']))
        inventory = v7.read_carried_inventory(self.artifacts, assignment)
        identity = v7._describe_carried_identity(records, assignment,
            records['review_route'][assignment['route_id']], native)
        evaluation = Evaluation.of(records, self.artifacts)
        cutoff = evaluation.read_snapshot(assignment['recorded_revision'] - 1)
        return records, inventory, identity, selection, cutoff

    def verify_stored(self, selection=None, *, first='new', records=None):
        from research_harness.openness_carried import verify_stored_selection
        records, inventory, identity, stored, cutoff = self.read_selection_context(first, records)
        return verify_stored_selection(records, self.artifacts, inventory, identity,
            stored if selection is None else selection, cutoff=cutoff)

    def assert_mismatch(self, selection, *, first='new', records=None):
        with self.assertRaises(ResearchError) as caught:
            self.verify_stored(selection, first=first, records=records)
        self.assertEqual(caught.exception.code, 'openness_reading_incomplete')
        self.assertEqual(caught.exception.message, v7.SELECTION_MISMATCH_MESSAGE)

    def hide_extraction_in_bundle(self, number=1):
        """fulltext and bundle: a second body of work number, whose bundle names the delivered capture as a
        supplement, so that capture is no main capture any more."""
        from research_harness.literature import import_bundle
        version = self.versions[number - 1]
        records = self.store.snapshot()['records']
        earlier = records['source_bundle']['bundle-%d' % number]
        main = self.capture(version, pdf_text='An independent main capture. ' * 200 + 'References: none.')
        identifier = 'supplement-bundle-%d' % number
        bundle = self.bundle(version, capture=main, bundle_id=identifier)
        bundle['units'].append(dict(id='supplement', kind='supplement', required=True,
            link=self.link(version, earlier['source_id'], self.texts[version])))
        self.mutate(import_bundle, bundle)
        return self.store.snapshot()['records']['source_bundle'][identifier]

    def test_content_lost_after_the_cutoff_does_not_let_an_omitted_chain_pass(self):
        """The rule of version 2, on a chain that stores a selection of version 2."""
        from research_harness import openness_carried as carried
        with patch.object(carried, 'SELECTION_PROTOCOL', 'exactory-openness-carried-selection-v2'):
            native, receipt = self.start_chain()
        self.assertEqual(receipt['carried_selection']['protocol'], 'exactory-openness-carried-selection-v2')
        self.verify_stored()
        packets = self.finish_chain(native)
        self.hide_extraction_in_bundle()
        records, inventory, identity, selection, cutoff = self.read_selection_context()
        self.assertEqual(selection['turns'], carried.select_carried_readings(
            cutoff.records, cutoff, inventory, identity, protocol=selection['protocol']))
        self.assert_mismatch(dict(selection, turns=[]))
        # The complete stored selection passes the cutoff comparison but its
        # carried origin cannot authenticate on the changed current content.
        with self.assertRaises(ResearchError) as caught:
            self.verify_stored(selection)
        self.assertEqual(caught.exception.code, 'openness_reading_incomplete')
        last = records['review_assignment']['new-%d' % len(packets)]
        with self.assertRaises(ResearchError) as caught:
            reading.verify_chain(records, self.artifacts, last)
        self.assertEqual(caught.exception.code, 'openness_reading_incomplete')

    def test_content_recovered_after_the_cutoff_does_not_change_a_stored_selection(self):
        """The rule of version 2, on a chain that stores a selection of version 2."""
        from research_harness import openness_carried as carried
        hidden = self.hide_extraction_in_bundle()
        from research_harness.problem_openness import export_openness
        minimum = dict(searches=[dict(formulation=self.selected['statement'], search_id=search['id'])
            for search in self.searches], settling_work_id=None, duplicate_resolutions=[])
        native = export_openness(self.store, dict(assignment_id='new', reviewer_id='openness-reviewer',
            minimum=minimum), self.root / 'new-export', accept_gaps=True)['native_packet']
        with patch.object(carried, 'SELECTION_PROTOCOL', 'exactory-openness-carried-selection-v2'):
            receipt = self.assign('new', native)
        self.assertEqual(receipt['carried_selection']['protocol'], 'exactory-openness-carried-selection-v2')
        selection = json.loads(self.artifacts.read(receipt['carried_selection']['artifact']))
        self.assertEqual(selection['turns'], [])
        recovered = copy.deepcopy(hidden)
        recovered['units'] = []
        self.put('source_bundle', hidden['id'], recovered)
        self.verify_stored()
        records, inventory, identity, selection, cutoff = self.read_selection_context()
        present = carried.select_carried_readings(records, self.artifacts, inventory, identity,
                                                  protocol=selection['protocol'])
        self.assertTrue(present)
        self.assert_mismatch(dict(selection, turns=present))

    def prepare_successive_chains(self):
        native, receipt = self.start_chain('middle')
        self.assertEqual(receipt['carried_selection']['protocol'], 'exactory-openness-carried-selection-v3')
        self.finish_chain(native, first='middle')
        native, receipt = self.start_chain('new')
        self.assertEqual(receipt['carried_selection']['protocol'], 'exactory-openness-carried-selection-v3')
        packets = self.finish_chain(native)
        return receipt, packets

    def test_next_chain_carries_a_chain_with_a_selection_of_version_3(self):
        receipt, packets = self.prepare_successive_chains()
        records, inventory, identity, selection, cutoff = self.read_selection_context()
        self.assertIn('middle', {row['origin']['chain'] for row in selection['turns']})
        last = records['review_assignment']['new-%d' % len(packets)]
        self.assertTrue(reading.verify_chain(records, self.artifacts, last))
        self.assert_mismatch(dict(selection, turns=[row for row in selection['turns']
            if row['origin']['chain'] != 'middle']))

    def test_successive_chains_select_and_verify_on_prepared_and_scoped_views(self):
        from research_harness.openness_carried import select_carried_readings
        self.prepare_successive_chains()
        records, inventory, identity, selection, cutoff = self.read_selection_context()
        expected = select_carried_readings(records, self.artifacts, inventory, identity, protocol=selection['protocol'])
        for preparation in (self.store._read_preparation(), self.store._read_scoped_preparation()):
            with self.subTest(view=type(preparation).__name__):
                self.assertEqual(select_carried_readings(preparation.records, self.artifacts, inventory, identity,
                                                         protocol=selection['protocol']), expected)
                self.verify_stored(records=preparation.records)

    def test_artifact_lost_after_the_cutoff_refuses_the_verification(self):
        self.complete_earlier_readings()
        native, receipt = self.start_chain()
        self.assertEqual(receipt['carried_selection']['protocol'], 'exactory-openness-carried-selection-v3')
        packets = self.finish_chain(native)
        records, inventory, identity, selection, cutoff = self.read_selection_context()
        last = records['review_assignment']['new-%d' % len(packets)]
        old = [row for row in selection['turns'] if row['origin']['chain'] == 'turn-1']
        self.assertGreater(len(old), 1)
        altered = [selection, dict(selection, turns=[]), dict(selection, turns=selection['turns'][:1])]
        first_packet = records['review_assignment']['turn-1']['packet']
        inner_output = records['review_attempt'][old[1]['origin']['attempt_id']]['output']
        for reference, corrupt, code in ((first_packet, False, 'artifact_missing'),
                (inner_output, False, 'artifact_missing'), (first_packet, True, 'artifact_corrupt')):
            with self.subTest(artifact=reference['sha256'], corrupt=corrupt):
                path = self.root / reference['path']
                content = path.read_bytes()
                try:
                    if corrupt:
                        path.write_bytes(bytes([content[0] ^ 1]) + content[1:])
                    else:
                        path.unlink()
                    for changed in altered:
                        with self.assertRaises(ResearchError) as caught:
                            self.verify_stored(changed)
                        self.assertEqual(caught.exception.code, code)
                    with self.assertRaises(ResearchError) as caught:
                        reading.verify_chain(records, self.artifacts, last)
                    self.assertEqual(caught.exception.code, code)
                finally:
                    path.write_bytes(content)
                self.verify_stored()
                self.assert_mismatch(altered[1])
                self.assert_mismatch(altered[2])

    def test_artifact_missing_at_the_first_assignment_refuses_the_assignment(self):
        native = self.export(name='new-export', assignment_id='new')
        # The contract assertion comes before removing an artifact, so the base
        # fails for its protocol rather than an incidental refusal.
        from research_harness.openness_carried import build_carried_selection
        records = self.store.snapshot()['records']
        binding = json.loads(self.artifacts.read(native))
        inventory = v7.prepare_inventory(v7.build_inventory(records, self.artifacts, binding),
            binding, records['review_route']['route'])['inventory']
        identity = v7._describe_carried_identity(records, dict(id='new', reviewer_id='openness-reviewer',
            author_id='problem-author'), records['review_route']['route'], binding)
        built = build_carried_selection(records, self.artifacts, inventory, identity)
        self.assertEqual(built['selection']['protocol'], 'exactory-openness-carried-selection-v3')
        reference = records['review_assignment']['turn-1']['packet']
        path = self.root / reference['path']
        content = path.read_bytes()
        before = copy.deepcopy(records)
        try:
            path.unlink()
            with self.assertRaises(ResearchError) as caught:
                self.assign('new', native)
            self.assertEqual(caught.exception.code, 'artifact_missing')
            self.assertEqual(self.store.snapshot()['records'], before)
        finally:
            path.write_bytes(content)
        receipt = self.assign('new', native)
        self.assertIn('turn-1', receipt['carried_readings']['would_carry'] if
            'would_carry' in receipt['carried_readings'] else [row['chain'] for row in receipt['carried_readings']['chains']])
        # The nine-target packet exceeds the v7 per-request target floor and
        # exercises its real v6 fallback, including the diagnostic refusal: the
        # store cannot give the packet of an earlier chain on the same statement.
        bindings = bind_statement_searches(self, 'fallback-statement', ['Neighbour %d.' % number for number in range(8)])
        self.assign('old', export_bindings(self, 'old', bindings))
        self.invoke('old', self.build_reading_response(self.read_packet('old')))
        fallback = export_bindings(self, 'fallback', bindings)
        with remove_artifact(self, self.store.snapshot()['records']['review_assignment']['old']['packet']):
            fallback_receipt = self.assign('fallback', fallback)
        self.assertEqual(fallback_receipt['protocol_selection']['protocol'], reading.PROTOCOL_V6)
        self.assertEqual(fallback_receipt['carried_readings']['refusal']['code'], 'artifact_missing')


class StoredV1ChainTests(CarriedIntegrationSupport, OpennessReadingCase):
    text_sizes = (100000,) * 3
    read_selection_context = StoredSelectionIntegrationTests.read_selection_context
    hide_extraction_in_bundle = StoredSelectionIntegrationTests.hide_extraction_in_bundle

    def start_v1_chain(self):
        from research_harness import openness_carried as carried
        from openness_legacy_fixtures import select_as_0_91_1
        native = self.export(name='legacy-export', assignment_id='legacy')
        with patch.object(carried, 'SELECTION_PROTOCOL', 'exactory-openness-carried-selection-v1'), patch.object(
                carried, 'select_carried_readings', side_effect=lambda *inputs, protocol: select_as_0_91_1(*inputs)):
            receipt = self.assign('legacy', native)
        self.assertEqual(receipt['carried_selection']['protocol'], 'exactory-openness-carried-selection-v1')
        return native, receipt

    def test_v7_chain_with_a_v1_selection_continues_and_verifies(self):
        from ci.reviewer_program_guard import use_absent_programs
        from research_harness.record_check import check_record
        native, receipt = self.start_v1_chain()
        current = 'legacy'
        for number in range(1, 3):
            packet = self.read_packet(current)
            self.assertIn('reading_turn', packet)
            self.invoke(current, self.answer_v7(packet))
            following = 'legacy-%d' % (number + 1)
            self.assign(following, native, prior=current)
            current = following
        before = {kind: {key: digest(row) for key, row in group.items()}
                  for kind, group in self.store.snapshot()['records'].items()}
        packets = self.finish_chain(native, first=current)
        records = self.store.snapshot()['records']
        last = records['review_assignment'][current if len(packets) == 1 else current + '-%d' % len(packets)]
        self.assertTrue(reading.verify_chain(records, self.artifacts, last))
        finding = self.mutate(self.load_problem_module().record_openness, dict(assignment_id=last['id']))['result']
        self.assertEqual(finding['outcome'], 'unresolved')
        # The record check lists the path of the Codex program beside each runtime read. The test states that
        # the machine has none.
        use_absent_programs(self)
        checked = check_record(self.root, isolate=False)
        self.assertTrue(checked['ready'], checked)
        after = self.store.snapshot()['records']
        self.assertEqual(before, {kind: {key: digest(after[kind][key]) for key in group}
                                 for kind, group in before.items()})

    def test_store_with_a_selection_of_version_1_keeps_its_history_and_its_rule(self):
        """A store with a reading chain keeps its history (C21). A chain that stores a selection of version 1 verifies,
        and after a supplement bundle of work 1 its verification is refused, as before."""
        self.assertTrue(hasattr(self.store.snapshot()['records'], 'read_snapshot'))
        native, receipt = self.start_v1_chain()
        packets = self.finish_chain(native, first='legacy')
        records = self.store.snapshot()['records']
        self.assertTrue(hasattr(records, 'read_snapshot'))
        last = records['review_assignment']['legacy-%d' % len(packets)]
        self.assertTrue(reading.verify_chain(records, self.artifacts, last))
        self.hide_extraction_in_bundle()
        records = self.store.snapshot()['records']
        with self.assertRaises(ResearchError) as caught:
            reading.verify_chain(records, self.artifacts, last)
        self.assertEqual(caught.exception.code, 'openness_reading_incomplete')


def list_openness_obligations(report):
    return [(row['code'], row.get('cause')) for row in report['obligations'] if row['code'].startswith('problem_openness')]


class HistoryViewTests(CarriedIntegrationSupport, OpennessReadingCase):
    """A view of the records that a reader builds keeps the history that the verification of a carried selection
    reads. Source: review finding C1 of record 20261113110022-every-open-record-and-review-capacity, and test N9 of
    the design of its content change."""

    def finish_carrying_chain(self):
        """Chain A under v6 with two ready turns, then chain B under v7 that carries both and ends with its final."""
        self.complete_earlier_readings()
        native, receipt = self.start_chain()
        self.assertEqual([turn['origin']['assignment_id'] for row in receipt['carried_readings']['chains']
                          for turn in row['turns']], ['turn-1', 'earlier-2'])
        packets = self.finish_chain(native)
        return 'new' if len(packets) == 1 else 'new-%d' % len(packets)

    def test_status_with_a_damaged_context_keeps_a_current_carried_finding_current(self):
        from research_harness import cli, review_continuation
        final = self.finish_carrying_chain()
        self.mutate(self.load_problem_module().record_openness, dict(assignment_id=final))
        self.assertEqual(list_openness_obligations(cli.status_report(self.store)),
                         [('problem_openness_unresolved', None)])
        # Retained content can be malformed although the command validated it: the context of an unrelated chain.
        self.assign('unrelated', self.export(name='unrelated-export', assignment_id='unrelated'))
        unrelated = self.store.snapshot()['records']['review_assignment']['unrelated']
        self.put('review_assignment', 'unrelated', dict(unrelated, context=[]))
        annotate = review_continuation.annotate_review_obligations
        views = []

        def annotate_and_keep_view(records, artifacts, report, **options):
            views.append(records)
            return annotate(records, artifacts, report, **options)
        with patch.object(review_continuation, 'annotate_review_obligations', side_effect=annotate_and_keep_view):
            report = cli.status_report(self.store)
        self.assertEqual([(row['assignment_id'], row['code']) for row in report['review_context_failures']],
                         [('unrelated', 'invalid_review_context')])
        self.assertEqual(list_openness_obligations(report), [('problem_openness_unresolved', None)])
        self.assertTrue(views)
        self.assertEqual([hasattr(view, 'read_snapshot') for view in views], [True] * len(views))

    def test_capacity_trial_view_of_a_route_renewal_keeps_the_history(self):
        from research_harness import review_capacity_evidence, review_refusal_replacement as replacement
        self.start_chain()
        records = self.store.snapshot()['records']
        views = []

        def refuse_the_trial(view, artifacts, value, revision):
            views.append(view)
            raise ResearchError('review_input_too_large', 'A stand-in refusal of the test.')
        trial = dict(form='stand-in', request_measure=dict(tokens_upper=2000, token_limit=1000,
                                                           output_reservation_tokens=100))
        with patch.object(review_capacity_evidence, 'describe_capacity_key', return_value='stand-in-key'), \
                patch.object(replacement, 'prepare_replacement', side_effect=refuse_the_trial):
            result = replacement.select_renewal_probe(records, self.artifacts, dict(route_id='route'),
                                                      self.store.revision + 1, [trial])
        self.assertEqual(result, dict(reason='limit'))
        [view] = views
        self.assertIn('renewal-capacity-evidence', view['review_route_probe'])
        first = records['review_assignment']['new']
        self.assertTrue(hasattr(view, 'read_snapshot') and view.verify_record_version(
            'review_assignment', 'new', first['recorded_revision'], digest(first)))

    def test_capture_selection_in_the_store_keeps_the_turns_of_an_earlier_v7_chain(self):
        from research_harness.capture_selection import select_main_capture
        self.finish_carrying_chain()
        version = self.versions[0]
        capture = self.store.snapshot()['records']['work'][version]['fulltexts'][0]
        # The choice names the capture that is already the main capture, so no unit of a packet changes.
        self.mutate(select_main_capture, dict(version_id=version, source_id=capture['source_id'],
                                              reason='Keep the first extraction.'))
        receipt = self.assign('later', self.export(name='later-export', assignment_id='later'))
        report = receipt['carried_readings']
        self.assertEqual({row['chain']: [turn['origin']['assignment_id'] for turn in row['turns']]
                          for row in report['chains']}.get('new'), ['new'])
        self.assertEqual({row['chain']: row['reading_turns'] for row in report['outputs']}['new'], 1)

    def test_condition_of_a_gate_override_reads_a_current_carried_finding_as_current(self):
        from research_harness import overrides
        from test_override_instruction_parts import build_instruction_parts
        final = self.finish_carrying_chain()
        self.mutate(self.load_problem_module().record_openness, dict(assignment_id=final))
        (self.root / 'context').mkdir(exist_ok=True)
        (self.root / 'context/instruction.json').write_text(json.dumps(build_instruction_parts()), encoding='utf-8')
        receipt = self.mutate(overrides.record_override, dict(id='conditional', gate='post',
            instruction='context/instruction.json', reason='The owner asks for a release after an open finding.',
            condition=dict(text='Publish after an open finding.', part=1, check='openness_supported_open')))
        conditions = [row for authority in receipt['write_authorities'] for row in authority['conditions']]
        self.assertTrue(conditions)
        self.assertEqual([[(row['code'], row.get('cause')) for row in condition['obligations']]
                          for condition in conditions], [[('problem_openness_unresolved', None)]] * len(conditions))


def list_carried_turns(report):
    """{chain: [assignment_id of each carried turn]} of the carried_readings report of a first assignment."""
    return {row['chain']: [turn['origin']['assignment_id'] for turn in row['turns']] for row in report['chains']}


class ContentChangeSupport(CarriedIntegrationSupport):
    """Chain A (v6: turn-1, earlier-2) and chain B (v7: new, which carries A and ends with its final); then a command
    changes the content records, and a later chain starts on a new export."""

    read_selection_context = StoredSelectionIntegrationTests.read_selection_context
    hide_extraction_in_bundle = StoredSelectionIntegrationTests.hide_extraction_in_bundle
    finish_carrying_chain = HistoryViewTests.finish_carrying_chain

    def start_later_chain(self):
        """The first assignment of a later chain on a new export, which accepts the gaps of a changed bundle."""
        from research_harness.problem_openness import export_openness
        minimum = dict(searches=[dict(formulation=self.selected['statement'], search_id=search['id'])
                                 for search in self.searches], settling_work_id=None, duplicate_resolutions=[])
        native = export_openness(self.store, dict(assignment_id='later', reviewer_id='openness-reviewer',
            minimum=minimum), self.root / 'later-export', accept_gaps=True)['native_packet']
        return native, self.assign('later', native)

    def verify_final(self, final):
        records = self.store.snapshot()['records']
        return reading.verify_chain(records, self.artifacts, records['review_assignment'][final])

    def assert_stale(self, final):
        with self.assertRaises(ResearchError) as caught:
            self.verify_final(final)
        self.assertEqual((caught.exception.code, caught.exception.message),
                         ('openness_reading_incomplete', v7.STATE_VERIFICATION_MESSAGE))

    def finish_later_chain(self, native):
        """Run the later chain to its final, which reads no full text, and return the id of its final."""
        packets = self.finish_chain(native, first='later')
        self.assert_only_unread_text(packets, 0)
        return 'later' if len(packets) == 1 else 'later-%d' % len(packets)


class ContentChangeCarryTests(ContentChangeSupport, OpennessReadingCase):
    """C16 to C22, the selection of version 3: after a command changed the content records, a later chain carries the
    readings of each unchanged unit and reads the units that are new or changed. Source: record
    20261120002019-content-change-after-cutoff-discards-carried-readings, tests N1, N3 to N5, K1 and K2 of its
    design."""

    def test_supplement_bundle_keeps_the_readings_of_the_unchanged_units(self):
        """N1, with K1, N4 and N3 as its later steps on the same state. A bundle names the capture of work 1 that A and
        B read as a supplement. On the base the later chain carries no turn: the first assertion fails."""
        final = self.finish_carrying_chain()
        self.hide_extraction_in_bundle()
        native, receipt = self.start_later_chain()
        report = receipt['carried_readings']
        self.assertEqual(list_carried_turns(report), {'turn-1': ['turn-1', 'earlier-2'], 'new': ['new']})
        self.assertEqual(receipt['carried_selection']['protocol'], 'exactory-openness-carried-selection-v3')
        self.assertEqual({row['chain']: row['reading_turns'] for row in report['outputs']}, {'turn-1': 2, 'new': 1})
        self.assertGreaterEqual(report['credited_characters'], 2 * self.text_sizes[1])
        self.assertLess(report['remaining_characters'], self.text_sizes[0])
        self.assertEqual(report['notes'], dict(current=0, affected=3))
        # K1, a contract: the finding of B, whose own packet holds the changed unit, is stale.
        self.assert_stale(final)
        # N4: the first part of the verification refuses a stored selection without the chain whose content changed.
        from research_harness.openness_carried import verify_stored_selection
        records, inventory, identity, selection, cutoff = self.read_selection_context('later')
        plan = capacity.read_chain_capacity_plan(self.artifacts, records['review_assignment']['later'])
        verify_stored_selection(records, self.artifacts, inventory, identity, selection, cutoff=cutoff, plan=plan)
        without_a = [row for row in selection['turns'] if row['origin']['chain'] != 'turn-1']
        for altered in (dict(selection, turns=[]), dict(selection, turns=without_a)):
            with self.assertRaises(ResearchError) as caught:
                verify_stored_selection(records, self.artifacts, inventory, identity, altered, cutoff=cutoff, plan=plan)
            self.assertEqual(caught.exception.message, v7.SELECTION_MISMATCH_MESSAGE)
        later = self.finish_later_chain(native)
        self.assertTrue(self.verify_final(later))
        # N3: a later change of a unit of its own packet turns the finding of the later chain stale.
        self.hide_extraction_in_bundle(2)
        self.assert_stale(later)

    def test_stored_selection_of_version_2_keeps_its_verification(self):
        """N5 (C20): B stores a selection of version 2, which keeps its rule; a later chain of version 3 still carries
        B. On the base the later chain carries nothing: the last assertion fails, and the ones before it pass."""
        from research_harness import openness_carried as carried
        with patch.object(carried, 'SELECTION_PROTOCOL', 'exactory-openness-carried-selection-v2'):
            final = self.finish_carrying_chain()
        stored = self.store.snapshot()['records']['review_assignment']['new']['carried_selection']
        self.assertEqual(stored['protocol'], 'exactory-openness-carried-selection-v2')
        self.assertTrue(self.verify_final(final))
        self.hide_extraction_in_bundle()
        with self.assertRaises(ResearchError) as caught:
            self.verify_final(final)
        self.assertEqual(caught.exception.code, 'openness_reading_incomplete')
        self.assertEqual(digest(json.loads(self.artifacts.read(stored['artifact']))), stored['digest'])
        native, receipt = self.start_later_chain()
        self.assertEqual(list_carried_turns(receipt['carried_readings']),
                         {'turn-1': ['turn-1', 'earlier-2'], 'new': ['new']})

    def test_added_records_leave_the_carry_as_it_was(self):
        """K2, a contract test that passes on the base: a new version of work 1 with its capture, bundle and reading,
        and a search that finds it, add records and change no unit of an earlier packet."""
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        from research_harness.literature import import_bundle
        from research_harness.reading import record_reading
        self.finish_carrying_chain()
        version = self.metadata(1, version=2)
        capture = self.capture(version, pdf_text=self.build_text(1).replace('Supporting', 'Revised supporting'))
        bundle = self.bundle(version, capture=capture, bundle_id='bundle-1-v2')
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle, 'read-1-v2'))
        self.searches.append(ProblemOpennessMinimumTests.search(self, self.versions + [version]))
        native, receipt = self.start_later_chain()
        self.assertEqual(list_carried_turns(receipt['carried_readings']),
                         {'turn-1': ['turn-1', 'earlier-2'], 'new': ['new']})


class SuppliedTextContentChangeTests(ContentChangeSupport, OpennessReadingCase):
    """Work 1 is a DOI work whose full text was supplied with import-fulltext. Test N2 of the design of record
    20261120002019-content-change-after-cutoff-discards-carried-readings."""

    DOI = 'doi:10.1234/supplied-survey'

    def metadata(self, number=1, version=1, **kwargs):
        if number != 1 or getattr(self, 'supplied_done', False):
            return super().metadata(number, version, **kwargs)
        from research_harness.acquisition import import_response
        data = json.dumps(dict(id=self.DOI, title='A supplied source', authors=['A. Reader'], year=2026)).encode()
        self.sequence += 1
        import_response(self.store, 'mcp', data, source_url='https://sources.example/catalog',
            captured_at='2026-11-12T12:00:00Z', media_type='application/json',
            mappings=[dict(id='/id', title='/title')],
            expected_revision=self.store.revision, request_id='supplied-metadata-%d' % self.sequence)
        return self.DOI

    def capture(self, identifier, *args, pdf_text=None, **kwargs):
        if identifier != self.DOI or getattr(self, 'supplied_done', False):
            return super().capture(identifier, *args, pdf_text=pdf_text, **kwargs)
        from research_harness import acquisition
        self.supplied_done = True
        (self.root / 'survey.pdf').write_bytes(b'%PDF-1.4\n% Synthetic supplied survey.\n%%EOF\n')
        origin = dict(saved_by='A reader', obtained_from='https://publisher.example/survey.pdf',
                      obtained_at='2026-11-12T16:30:00-08:00', statement='Saved the complete text in a browser.')
        self.sequence += 1
        return acquisition.import_fulltext(self.store, identifier, 'survey.pdf', origin,
            request_id='supplied-%d' % self.sequence, expected_revision=self.store.revision,
            extractor=lambda data: {'status': 'extracted', 'text': pdf_text})['capture']

    def fetch_publisher_copy(self):
        """fulltext: the publisher's copy of the supplied work arrives through a fetch, with another extracted text."""
        from research_fixtures import client
        from research_harness.acquisition import acquire_fulltext
        http, _, _ = client([(200, {'Content-Type': 'application/pdf'},
                              b'%PDF-1.4\n% The fetched publisher copy.\n%%EOF')], max_retries=0)
        self.sequence += 1
        acquire_fulltext(self.store, self.DOI, 'https://publisher.example/survey.pdf', http=http,
            extractor=lambda data: {'status': 'extracted', 'text': self.build_text(1).replace('continues here',
                                                                                              'continues  here')},
            expected_revision=self.store.revision, request_id='fetched-%d' % self.sequence)

    def test_fetched_copy_after_a_supplied_text_keeps_the_readings_of_the_unchanged_units(self):
        """N2: the fetched copy replaces the supplied text as the main capture. On the base the later chain carries no
        turn: the first assertion fails."""
        self.finish_carrying_chain()
        self.fetch_publisher_copy()
        native, receipt = self.start_later_chain()
        report = receipt['carried_readings']
        self.assertEqual(list_carried_turns(report), {'turn-1': ['turn-1', 'earlier-2'], 'new': ['new']})
        self.assertGreaterEqual(report['credited_characters'], 2 * self.text_sizes[1])
        self.assertLess(report['remaining_characters'], self.text_sizes[0])
        self.assertTrue(self.verify_final(self.finish_later_chain(native)))


class ReadScopeIntegrationTests(CarriedIntegrationSupport, OpennessReadingCase):
    """C9 to C11: the selection reads an artifact of an earlier assignment only when its result can depend on it."""

    def build_payload(self, identifier='new', *, reviewer_id='openness-reviewer', route_id='route', bindings=None):
        bindings = bindings or [dict(formulation=self.selected['statement'], search_id=self.search['id'])]
        return dict(id=identifier, route_id=route_id, dossier_id=None, role='native_math', reviewer_id=reviewer_id,
                    author_id='problem-author',
                    context=dict(native_packet=export_bindings(self, identifier, bindings, reviewer_id)))

    def check_assignment(self, payload):
        return check_mutation(self.store, protocol.record_assignment, payload, expected_revision=self.store.revision,
                              request_id='read-scope-check', bounded_payload=payload)

    def read_earlier_first(self):
        return self.store.snapshot()['records']['review_assignment']['turn-1']

    def test_lost_packet_of_a_chain_under_another_reviewer_does_not_refuse_the_assignment(self):
        from research_harness import problem_openness
        packet = self.read_earlier_first()['packet']
        payload = self.build_payload(reviewer_id='another-reviewer')
        with remove_artifact(self, packet):
            report = self.mutate(protocol.record_assignment, payload)['result']['carried_readings']
            self.assertEqual(report['passed_over'], [])
            self.assertEqual(report['unavailable_chains'], [dict(chain='turn-1', code='artifact_missing',
                details=dict(path=packet['path']), message=v7.UNAVAILABLE_CHAIN_TEXT.format(chain='turn-1'))])
            packets = self.finish_chain(payload['context']['native_packet'])
            last = 'new' if len(packets) == 1 else 'new-%d' % len(packets)
            self.mutate(self.load_problem_module().record_openness, dict(assignment_id=last))
            records = self.store.snapshot()['records']
            self.assertTrue(reading.verify_chain(records, self.artifacts, records['review_assignment'][last]))
            self.assertNotIn('problem_openness_stale',
                             [row['code'] for row in problem_openness.assess_openness(records, self.artifacts)])
        report = self.check_assignment(self.build_payload('further', reviewer_id='another-reviewer'))['carried_readings']
        self.assertEqual([(row['chain'], row['reason']) for row in report['passed_over']], [('turn-1', 'reviewer_id')])
        self.assertNotIn('unavailable_chains', report)

    def test_lost_artifact_of_a_chain_on_another_route_identity_does_not_refuse_the_assignment(self):
        from codex_route_fixtures import record_codex_route
        record_codex_route(self, 'other-route', model=self.route_model, output_tokens=64000)
        payload = self.build_payload(route_id='other-route')
        first = self.read_earlier_first()
        for name, reference in (('packet', first['packet']), ('native_packet', first['context']['native_packet'])):
            with self.subTest(artifact=name), remove_artifact(self, reference):
                checked = self.check_assignment(payload)
                self.assertEqual(checked['protocol_selection']['protocol'], v7.PROTOCOL)
                self.assertEqual(checked['carried_readings']['chains'], [])
                self.assertNotIn('unavailable_chains', checked['carried_readings'])

    def test_lost_artifact_of_a_chain_on_another_statement_does_not_refuse_the_assignment(self):
        first = self.read_earlier_first()
        payload = self.build_payload(bindings=bind_statement_searches(self, 'other-statement', ['Neighbour 0.']))
        held = {row['artifact']['path'] for row in
                json.loads(self.artifacts.read(payload['context']['native_packet']))['evidence']}
        evidence = next(row['artifact'] for row in
                        json.loads(self.artifacts.read(first['context']['native_packet']))['evidence']
                        if row['artifact']['path'] not in held)
        for name, reference in (('packet', first['packet']), ('evidence', evidence)):
            with self.subTest(artifact=name), remove_artifact(self, reference):
                checked = self.check_assignment(payload)
                self.assertEqual(checked['protocol_selection']['protocol'], v7.PROTOCOL)
                self.assertEqual(checked['carried_readings']['chains'], [])
                self.assertNotIn('unavailable_chains', checked['carried_readings'])

    def assert_replacement_has_no_row(self, native, replacement):
        """The next chain names the replaced chain once, by its first assignment, with the turns of the replacement."""
        self.finish_chain(native, replacement)
        _, receipt = self.start_chain('following')
        rows = receipt['carried_readings']['outputs']
        self.assertEqual(rows[0]['chain'], 'new')
        self.assertEqual([row['chain'] for row in rows], ['new', 'turn-1'])
        self.assertEqual(rows[0]['carried_turns'], rows[0]['reading_turns'])
        selection = json.loads(self.artifacts.read(receipt['carried_selection']['artifact']))
        turns = [row['origin']['assignment_id'] for row in selection['turns'] if row['origin']['chain'] == 'new']
        self.assertEqual(turns, [replacement] + ['%s-%d' % (replacement, number) for number in range(2, len(turns) + 1)])

    def test_replacement_of_a_first_turn_for_capacity_has_no_row_of_its_own(self):
        from codex_route_fixtures import build_codex_response, bind_codex_response
        from research_harness.review_failure_class import plan_after_failure
        native, _ = self.start_chain()

        def respond(route, request, credential):
            response = build_codex_response(route, request, {})
            response.update(status='incomplete')
            response['usage']['output_tokens'] = 128001
            return bind_codex_response(route, request, response)
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            self.mutate(protocol.invoke_assignment, dict(id='failed-new', assignment_id='new'), credential='fixture')
        recovery = plan_after_failure(self.store.snapshot()['records'], self.artifacts, 'new')
        self.assertEqual(recovery['state'], 'replace')
        self.mutate(protocol.record_assignment, dict(recovery['details']['recovery']['payload'], id='replacement'))
        self.assert_replacement_has_no_row(native, 'replacement')

    def test_replacement_of_a_first_turn_after_a_refused_output_has_no_row_of_its_own(self):
        from research_harness.review_continuation import plan_next
        native, _ = self.start_chain()
        self.invoke('new', dict(stage='reading', notes=42))
        plan = plan_next(self.store.snapshot()['records'], self.artifacts, 'new')
        self.assertEqual(plan['state'], 'replace')
        replacement = self.mutate(protocol.record_assignment, plan['payload'])['result']
        self.assertIn('output_refusal', replacement['context'])
        self.assert_replacement_has_no_row(native, replacement['id'])

    def test_lost_packet_of_a_chain_on_another_statement_leaves_the_v6_report_whole(self):
        # The nine-target packet exceeds the v7 per-request target floor, so v6 starts the chain.
        fallback = export_bindings(self, 'fallback', bind_statement_searches(
            self, 'fallback-statement', ['Neighbour %d.' % number for number in range(8)]))
        with remove_artifact(self, self.read_earlier_first()['packet']):
            receipt = self.assign('fallback', fallback)
        report = receipt['carried_readings']
        self.assertNotIn('refusal', report)
        self.assertEqual(receipt['protocol_selection']['protocol'], reading.PROTOCOL_V6)
        self.assertNotIn('unavailable_chains', report)
        self.assertNotIn('turn-1', report['would_carry'] + [row['chain'] for row in report['passed_over'] + report['outputs']])


class SingleTurnReadScopeTests(OpennessReadingCase):
    """C10: a single-turn review starts no reading chain, so a new chain reads none of its artifacts."""
    reading_protocol = None
    route_model = 'gpt-6-astra'
    route_output_tokens = 128000

    def setUp(self):
        super().setUp()
        self.limit_input(1300000)
        self.assign('single', self.export(name='single-export', assignment_id='single'))
        self.invoke('single', self.build_native_result(self.read_packet('single'), outcome='unresolved'))

    def assign_without(self, name):
        single = self.store.snapshot()['records']['review_assignment']['single']
        self.assertNotIn('reading_chain', single)
        native = self.export(name='new-export', assignment_id='new')
        with remove_artifact(self, single['packet'] if name == 'packet' else single['context'][name]), patch.object(
                reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
            return self.assign('new', native)

    def test_lost_packet_of_a_single_turn_review_does_not_refuse_the_assignment(self):
        receipt = self.assign_without('packet')
        self.assertEqual(receipt['protocol_selection']['protocol'], v7.PROTOCOL)
        self.assertEqual(receipt['carried_readings']['outputs'], [])

    def test_lost_native_packet_of_a_single_turn_review_does_not_refuse_the_assignment(self):
        receipt = self.assign_without('native_packet')
        self.assertEqual(receipt['protocol_selection']['protocol'], v7.PROTOCOL)
        self.assertEqual(receipt['carried_readings']['outputs'], [])


class Record12ShapeTests(OpennessReadingCase):
    """The two earlier chains retain the cut's turns, units and output counts.

    Only synthetic material is used. Content and legacy delivery widths are
    scaled by one quarter; JSON envelopes retain their released representation.
    The partition helper fixes the fixture's paragraph boundaries and turn
    groups. The released continuity verifier still checks every delivery,
    request, response, excerpt, passage and original-search reconstruction.
    """
    reading_protocol = None
    historical_v5 = True
    route_model = 'gpt-6-astra'
    route_output_tokens = 128000
    shape_scale = 0.25
    # The members of a built cut that its users read; a copy of the store holds the rest.
    CUT_STATE = ('shape', 'text_sizes', 'versions', 'texts', 'selected', 'searches', 'search', 'shape_search_ids',
                 'shape_reading_names', 'shape_spans', 'shape_packets', 'shape_observations', 'old_records',
                 'older_native', 'stopped_native', 'native', 'sequence', 'shape_input_limit')
    answer_v7 = CarriedIntegrationSupport.answer_v7
    finish_chain = CarriedIntegrationSupport.finish_chain
    read_selection_context = StoredSelectionIntegrationTests.read_selection_context
    verify_stored = StoredSelectionIntegrationTests.verify_stored

    @staticmethod
    def read_shape():
        return json.loads((Path(__file__).parent / 'fixtures/record12_cut_shape.json').read_text())

    @classmethod
    def scale_size(cls, value):
        return max(1, round(value * cls.shape_scale))

    @staticmethod
    def build_filler(length):
        phrase = 'Synthetic material %06d. '
        return ''.join(phrase % number for number in range(length // len(phrase % 0) + 1))[:length]

    def build_text(self, number):
        from openness_reading_fixtures import build_theorem
        deliveries = self.read_shape()['chains'][0]['turns']
        length = sum(self.scale_size(row[3]) for turn in deliveries for row in turn['d']
                     if row[0] == 'T' and row[4] == 'text-%02d' % number)
        head = 'Paper %d opening line.\n' % number + build_theorem(number) + '\n\n'
        tail = 'References: none.'
        return head + self.build_filler(length - len(head) - len(tail)) + tail

    def record_searches(self):
        # The old chain knows the final ten searches and only the first 28
        # decisive works. The stopped chain later binds all twenty searches.
        self.selected = self.select_statement(identifier='shape-statement', candidate_id='candidate-1',
            neighbours=['Neighbouring bounded statement %d.' % number for number in range(1, 10)])
        self.shape_search_ids = {}
        return self.record_search_group(11)

    def pad_json_string(self, value, container, key, target):
        difference = target - len(serialize_record(value))
        self.assertGreaterEqual(difference, 0, 'shape: scaled record must hold its JSON envelope')
        container[key] += self.build_filler(difference)
        self.assertEqual(len(serialize_record(value)), target)

    def record_search_group(self, first):
        from research_harness.acquisition import import_response
        from research_harness.literature import record_search
        shape = self.read_shape()
        shared_turn = shape['chains'][0]['turns'][27 if first == 1 else 28]
        sizes = [self.scale_size(row[3]) for row in shared_turn['d']]
        query = ('Synthetic group %d query. ' % first).ljust(sizes[1] - 4, 'q')
        found = self.versions if first == 1 else self.versions[:28]
        data = dict(query=query, results=[dict(id=work, title='Synthetic mathematical source') for work in found])
        self.sequence += 1
        captured = import_response(self.store, 'mcp', json.dumps(data).encode(),
            source_url='https://example.org/shape-search', captured_at='2026-10-15T12:00:00Z',
            media_type='application/json', mappings=[dict(id='/results/%d/id' % index,
                title='/results/%d/title' % index) for index in range(len(found))],
            expected_revision=self.store.revision, request_id='shape-capture-%d' % self.sequence)
        identifier = 'shape-search-%02d' % first
        self.mutate(record_search, dict(id=identifier, profile='research', purpose='direct',
            question=self.selected['statement'], scope=self.selected['statement'], queries=[query],
            responses=[dict(source_id=captured['source_ids'][0], query=query,
                query_locator=dict(kind='json', pointer='/query', value=query), results_pointer='/results')],
            captured_at='2026-10-15T12:00:00Z', found_work_ids=list(found), verdict='nothing-new',
            cited_work_ids=[], dispositions=[dict(work_id=work, disposition='relevant',
                reason='The exact finite example is relevant.') for work in found], resolved=[],
            impact='Evidence for the declared bounded formulation.', gaps=[], hits_per_query=40))
        base = self.store.snapshot()['records']['literature_search'][identifier]
        self.pad_json_string(base['dispositions'], base['dispositions'][0], 'reason', sizes[0])
        self.pad_json_string(base['queries'], base['queries'], 0, sizes[1])
        base['responses'][0]['shape_padding'] = ''
        self.pad_json_string(base['responses'], base['responses'][0], 'shape_padding', sizes[2])
        scope = 'Synthetic search scope. '
        base['scope'] = scope + 's' * (sizes[3] - len(serialize_record(scope)))
        result = []
        formulations = [self.selected['statement']] + self.selected['neighbours']
        for offset, formulation in enumerate(formulations):
            ordinal = first + offset
            row = copy.deepcopy(base)
            row['id'] = base['id'] if offset == 0 else 'shape-search-%02d' % ordinal
            row['question'] = formulation
            row['impact'] = ('The finite examples do not settle this formulation. '
                'Supporting readings shape-support-1 and shape-support-2.' if first == 1 else
                'The first 28 finite examples do not settle this formulation.')
            # Both chains quote the same final ten residuals; count a part once.
            lengths = {}
            for chain in shape['chains']:
                for turn in chain['turns']:
                    for delivery in turn['d']:
                        if delivery[0] == 'R' and delivery[4] == 'search-%02d' % ordinal:
                            lengths[delivery[1]] = self.scale_size(delivery[3])
            target = sum(lengths.values())
            envelope = dict(record_id=row['id'], search_digest='0' * 64,
                fields={key: value for key, value in row.items()
                        if key not in ('dispositions', 'queries', 'responses', 'scope')},
                shared_fields={key: '0' * 64 for key in ('dispositions', 'queries', 'responses', 'scope')})
            difference = target - len(serialize_record(envelope))
            self.assertGreaterEqual(difference, 0, 'shape: residual envelope fits its scaled material')
            row['impact'] += self.build_filler(difference)
            self.put('literature_search', row['id'], row)
            self.shape_search_ids[row['id']] = ordinal
            result.append(row)
        return result

    @classmethod
    def build_fixture(cls):
        """Build a reusable authenticated cut; the caller releases doCleanups."""
        import os
        directory = os.environ.get('EXACTORY_RECORD12_SAVED_DIRECTORY')
        if directory:
            return cls.read_fixture_report(directory)
        fixture = cls('test_stopped_chain_carries_all_57_turns_and_every_output')
        fixture._building_shape = True
        try:
            fixture.setUp()
        except BaseException:
            fixture.doCleanups()
            raise
        return fixture

    @classmethod
    def read_fixture_report(cls, directory):
        """Reauthenticate a saved synthetic cut for another interpreter's checks."""
        fixture = cls('test_stopped_chain_carries_all_57_turns_and_every_output')
        try:
            state = json.loads((Path(directory) / 'state.json').read_text())
            fixture.assertEqual(state['shape'], cls.read_shape(), 'shape: saved cut uses Appendix A')
            fixture.install_cut(Path(directory) / 'store', state)
            fixture.shape_packets = {}
            for chain, count in (('older-1', 44), ('stopped', 57)):
                for number in range(1, count + 1):
                    identifier = chain if number == 1 else '%s-%d' % (chain, number)
                    fixture.shape_packets[chain, number] = fixture.read_packet(identifier)
            fixture.assert_cut_shape()
            return fixture
        except BaseException:
            fixture.doCleanups()
            raise

    @classmethod
    def load_cut(cls):
        """A case that holds a writable copy of the one build of this process (build_record12_cut), with the
        runtime and the context of the shape installed as the build left them. The caller releases doCleanups."""
        built = build_record12_cut()
        fixture = cls('test_stopped_chain_carries_all_57_turns_and_every_output')
        try:
            fixture.install_cut(built['store'], built['state'])
            return fixture
        except BaseException:
            fixture.doCleanups()
            raise

    def install_cut(self, store, state):
        """Copy a built store into this case's own root, take a copy of the built state, and install the runtime and
        the context of the shape."""
        from codex_route_fixtures import install_codex_runtime
        from literature_fixtures import LiteratureCase
        from research_harness.storage import Store
        LiteratureCase.setUp(self)
        shutil.rmtree(self.root)
        shutil.copytree(store, self.root)
        self.store = Store(self.root)
        self.artifacts = self.store.build_artifact_store()
        for name, value in state.items():
            setattr(self, name, copy.deepcopy(value))
        self.historical_v5 = False
        self.shape_quotes = {}
        install_codex_runtime(self)
        self.install_shape_runtime()
        self.install_shape_context()

    @classmethod
    def setUpClass(cls):
        # Each test mutates its own complete filesystem clone, including the store's event history, of a copy of the
        # one build of this process. The last cleanup checks that the build kept its bytes.
        import os
        import time
        started = time.monotonic()
        cls.shape_template = cls.load_cut()
        cls.addClassCleanup(check_record12_cut_unchanged)
        cls.addClassCleanup(cls.shape_template.doCleanups)
        directory = os.environ.get('EXACTORY_RECORD12_REPORT_DIRECTORY')
        if directory:
            cls.shape_template.write_fixture_report(directory)
            print('SHAPE_BUILD_SECONDS %.3f' % (time.monotonic() - started), flush=True)
            print('SHAPE_SCALE %s INPUT_LIMIT %s' % (cls.shape_template.shape_scale,
                cls.shape_template.shape_input_limit), flush=True)
            cls.addClassCleanup(lambda: print('SHAPE_CLASS_SECONDS %.3f' % (time.monotonic() - started), flush=True))

    def write_fixture_report(self, directory):
        """Preserve the synthetic cut only for an explicitly requested measurement."""
        destination = Path(directory).resolve()
        if getattr(self, '_fixture_report_directory', None) != str(destination):
            destination.mkdir(parents=True)
            shutil.copytree(self.root, destination / 'store')
            self._fixture_report_directory = str(destination)
        names = ('shape', 'text_sizes', 'versions', 'texts', 'selected', 'searches', 'search',
            'shape_search_ids', 'shape_reading_names', 'shape_spans', 'shape_observations',
            'old_records', 'older_native', 'stopped_native', 'native', 'sequence', 'shape_input_limit')
        (destination / 'state.json').write_text(json.dumps({name: getattr(self, name) for name in names if hasattr(self, name)}))

    def clone_shape_fixture(self):
        from literature_fixtures import LiteratureCase
        from research_harness.storage import Store
        LiteratureCase.setUp(self)
        shutil.rmtree(self.root)
        template = self.shape_template
        shutil.copytree(template.root, self.root)
        self.store = Store(self.root)
        self.artifacts = self.store.build_artifact_store()
        for name in self.CUT_STATE:
            setattr(self, name, copy.deepcopy(getattr(template, name)))
        self.historical_v5 = False

    def setUp(self):
        if not getattr(self, '_building_shape', False):
            self.clone_shape_fixture()
            return
        self.shape = self.read_shape()
        self.text_sizes = tuple(self.scale_size(row[2]) for row in self.shape['inventory'] if row[1] == 'fulltext')
        super().setUp()
        self.limit_input(1300000)
        self.shape_spans = {}
        self.shape_packets = {}
        self.shape_quotes = {}
        self.install_shape_runtime()
        self.pad_shape_records()
        self.older_native = self.export(name='shape-older-export', assignment_id='older-1')
        self.install_shape_spans(self.older_native)
        self.run_shape_chain('older-1', self.older_native, self.shape['chains'][1])
        new_searches = self.record_search_group(1)
        self.searches = new_searches + self.searches
        self.search = self.searches[0]
        self.stopped_native = self.export(name='shape-stopped-export', assignment_id='stopped')
        self.install_shape_spans(self.stopped_native)
        self.run_shape_chain('stopped', self.stopped_native, self.shape['chains'][0])
        self.old_records = copy.deepcopy(self.store.snapshot()['records'])
        self.calibrate_shape_capacity()
        self.historical_v5 = False
        self.install_shape_context()
        self.native = self.export(name='shape-new-export', assignment_id='new')
        # Preserve the authenticated cut before diagnostic assertions, so a
        # measurement run can inspect a refusal without rerunning101commands.
        import os
        directory = os.environ.get('EXACTORY_RECORD12_REPORT_DIRECTORY')
        if directory:
            self.write_fixture_report(directory)
        self.assert_cut_shape()

    def install_shape_context(self):
        """Restore the same route limit, measured profile and v7 activation."""
        from test_review_output_profile import build_row
        priors = [build_row(level=None, thinking=1000, count=8),
                  build_row(kind='final', level=None, thinking=1000, count=8)]
        for patcher in (patch.object(protocol.transport, 'CODEX_INPUT_LIMIT_CHARS', self.shape_input_limit),
                        patch.object(capacity, 'read_profile_priors', return_value=priors),
                        patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def calibrate_shape_capacity(self):
        """Set the current route one character below its actual mandatory final floor."""
        reports = []
        original = reading.preflight_final_input

        def capture_preflight(*args, **kwargs):
            result = original(*args, **kwargs)
            reports.append(result)
            return result

        payload = dict(id='stopped-58', route_id='route', dossier_id=None, role='native_math',
            reviewer_id='openness-reviewer', author_id='problem-author',
            context=dict(native_packet=self.stopped_native, prior_assignment_id='stopped-57'))
        with patch.object(protocol.transport, 'CODEX_INPUT_LIMIT_CHARS', 1300000), patch.object(
                reading, 'preflight_final_input', new=capture_preflight):
            check_mutation(self.store, protocol.record_assignment, payload, expected_revision=self.store.revision,
                request_id='shape-capacity-calibration', bounded_payload=payload)
        self.shape_input_limit = reports[-1]['input_characters'] - 1

    def install_shape_runtime(self):
        from functools import lru_cache
        from research_harness import review_excerpts
        normalize = lru_cache(maxsize=512)(review_excerpts.normalize_with_offsets)
        from research_harness import review_request_evaluation, review_request_fit
        measure_string = lru_cache(maxsize=8192)(review_request_fit._measure_string)
        original_request = review_request_evaluation.request_for_order
        requests = {}
        def build_request(evaluation, route, prompt, packet, history, order, *, policy=None):
            # Memoize a pure computation across these many command evaluations.
            # The full actual inputs, their object order and the route limit
            # are the key. Authentication and artifact reads are never cached.
            key = hashlib.sha256(json.dumps([route, prompt, packet, list(history), list(order), policy,
                protocol.transport.local_input_limit(route)], ensure_ascii=False, separators=(',', ':')).encode()).digest()
            if key not in requests:
                value = original_request(evaluation, route, prompt, packet, history, order, policy=policy)
                requests[key] = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
            return json.loads(requests[key])
        self.original_build_turn = reading._build_reading_turn
        original_spans = review_excerpts.compute_part_spans
        def compute_spans(content, segment_size=None):
            return self.shape_spans.get(hashlib.sha256(content.encode()).hexdigest(),
                original_spans(content, segment_size))
        for patcher in (patch.object(review_request_evaluation, 'request_for_order', new=build_request),
                        patch.object(review_request_fit, '_measure_string', new=measure_string),
                        patch.object(review_excerpts, 'normalize_with_offsets', new=normalize),
                        patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', self.scale_size(120000)),
                        patch.object(review_excerpts, 'compute_part_spans', new=compute_spans),
                        patch.object(reading, '_build_reading_turn', new=self.build_shape_turn)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def export(self, priority=(), name='openness-packet', assignment_id='turn-1'):
        from research_harness.problem_openness import export_openness
        if self.historical_v5:
            from openness_legacy_fixtures import export_as_0_85_3
            export_openness = export_as_0_85_3
        minimum = dict(searches=[dict(formulation=search['question'], search_id=search['id'])
            for search in self.searches], settling_work_id=None, duplicate_resolutions=[])
        if priority:
            minimum['text_priority'] = list(priority)
        return export_openness(self.store, dict(assignment_id=assignment_id,
            reviewer_id='openness-reviewer', minimum=minimum), self.root / name)['native_packet']

    def pad_shape_records(self):
        # Two nondecisive complete readings occur only as citations of searches.
        for number in (1, 2):
            self.record_full_reading(note_id='shape-support-%d' % number, number=100 + number)
        records = self.store.snapshot()['records']
        older_ids = sorted(row['id'] for row in records['reading'].values()
                           if row['id'].startswith('read-') and row['version_id'] != self.versions[28])
        older_slots = [row[4] for turn in self.shape['chains'][1]['turns'] for row in turn['d'] if row[0] == 'G']
        self.shape_reading_names = dict(zip(older_ids, older_slots))
        spare = [row[0] for row in self.shape['inventory'] if row[1] == 'reading' and row[0] not in older_slots]
        self.shape_reading_names.update(zip(['read-29', 'shape-support-1', 'shape-support-2'], spare))
        inventory_sizes = {row[0]: self.scale_size(row[2]) for row in self.shape['inventory']}
        for identifier, name in self.shape_reading_names.items():
            row = copy.deepcopy(records['reading'][identifier])
            self.pad_json_string(row, row['notes']['relevance'], 'text', inventory_sizes[name])
            self.put('reading', identifier, row)
        statement = copy.deepcopy(self.selected)
        self.pad_json_string(statement, statement, 'resolution_criteria', inventory_sizes['statement-01'])
        self.put('problem_statement', statement['id'], statement)
        self.selected = statement

    def assign_historical_head(self, identifier, native_packet):
        historical = protocol.openness_reading
        with patch.object(historical, '_build_reading_turn', new=self.build_shape_turn):
            return super().assign_historical_head(identifier, native_packet)

    def find_shape_name(self, row):
        if row['kind'] == 'fulltext':
            return 'T', 'text-%02d' % (self.versions.index(row['version_id']) + 1)
        kind = row['record_kind']
        if kind == 'search_shared_field':
            first = min(self.shape_search_ids[owner['record_id']] for owner in row['owners'])
            return 'F', ('search-01..10' if first == 1 else 'search-11..20')
        if kind in ('search_residual', 'literature_search'):
            return 'R', 'search-%02d' % self.shape_search_ids[row['record_id']]
        if kind == 'reading':
            return 'G', self.shape_reading_names[row['record_id']]
        return 'S', 'statement-01'

    def install_shape_spans(self, native):
        binding = json.loads(self.artifacts.read(native))
        rows = reading.list_reading_rows(self.store.snapshot()['records'], self.artifacts, binding)
        plan = reading._plan_search_delivery(self.artifacts, binding)
        for content in plan['bodies'].values():
            self.artifacts.put(content, 'application/json')
        for row in reading.list_delivery_rows(rows, plan['manifest']):
            code, name = self.find_shape_name(row)
            content = self.artifacts.read(row['artifact']).decode()
            if code == 'F':
                lengths = [len(content)]
            else:
                parts = {}
                for chain in self.shape['chains']:
                    for turn in chain['turns']:
                        for delivery in turn['d']:
                            if (delivery[0], delivery[4]) == (code, name):
                                parts[delivery[1]] = self.scale_size(delivery[3])
                lengths = [parts[key] for key in sorted(parts)] if parts else [len(content)]
            self.assertEqual(sum(lengths), len(content), 'shape: scaled sizes of ' + name)
            start, spans = 0, []
            for length in lengths:
                spans.append((start, start + length))
                start += length
            self.shape_spans[row['artifact']['sha256']] = spans

    def build_shape_turn(self, artifacts, route, packet, record, delivered, candidates, turn):
        # Shape rows describe groups, while normal continuity still requires
        # exactly the next prefix of the physical plan and the scaled width.
        shape_chain = self.shape['chains'][0 if len(packet['native']['scientific_context'][
            'openness_minimum']['reading_digests']) == 29 else 1]
        if turn > len(shape_chain['turns']):
            return self.original_build_turn(artifacts, route, packet, record, delivered, candidates, turn)
        count = len(shape_chain['turns'][turn - 1]['d'])
        value = reading._assemble_reading_turn(artifacts, packet, record, delivered,
            candidates[:count], candidates[count:], turn)
        prompt = protocol.canonical_prompt('native_math', reading_stage='reading', reading_protocol=record['protocol'])['text']
        return reading._fit_turn(route, prompt, value)[0]

    def build_shape_answer(self, packet, shape_turn):
        response = self.build_reading_response(packet, excerpts=[])
        chain = 'older' if self.active_shape_native == self.older_native else 'stopped'
        response['notes'] = ('Synthetic notes of turn %d. ' % shape_turn['n']).ljust(
            self.scale_size(shape_turn['notes']), 'n')
        records = self.store.snapshot()['records']
        binding = json.loads(self.artifacts.read(self.active_shape_native))
        rows = reading.list_reading_rows(records, self.artifacts, binding)
        sources = {}
        for row in rows:
            kind = 'text' if row['kind'] == 'fulltext' else {'literature_search': 'search',
                'reading': 'reading', 'problem_statement': 'statement'}[row['record_kind']]
            sources.setdefault(kind, row)
        for kind, count in shape_turn['x'].items():
            row = sources[kind]
            identity = ({'version_id': row['version_id']} if row['kind'] == 'fulltext' else
                        {key: row[key] for key in ('record_kind', 'record_id')})
            content = self.artifacts.read(row['artifact']).decode()
            key = (self.active_shape_native['sha256'], row['artifact']['sha256'])
            cursor, seen = self.shape_quotes.setdefault(key, (0, set()))
            from research_harness import review_excerpts
            for index in range(count):
                while cursor + self.scale_size(170) <= len(content):
                    quote = content[cursor:cursor + self.scale_size(170)]
                    cursor += 1
                    normalized = review_excerpts.normalize_with_offsets(quote)[0]
                    if normalized not in seen:
                        seen.add(normalized)
                        break
                else:
                    self.fail('shape: the synthetic source needs distinct retained quote spans')
                response['excerpts'].append(dict(identity, text=quote,
                    purpose=('%s turn %02d %s excerpt %02d.' % (chain, shape_turn['n'], kind, index)).ljust(40, 'p')))
            self.shape_quotes[key] = cursor, seen
        for kind, count in shape_turn['p'].items():
            row = sources[kind]
            identity = ({'version_id': row['version_id']} if row['kind'] == 'fulltext' else
                        {key: row[key] for key in ('record_kind', 'record_id')})
            anchor_start = 0 if chain == 'older' else 100
            anchor = self.artifacts.read(row['artifact']).decode()[anchor_start:anchor_start + 20]
            response['passage_requests'].extend(dict(identity, id='passage-%d-%d' % (shape_turn['n'], index),
                anchor=anchor, characters=20 + 5 * shape_turn['n'] + index) for index in range(count))
        return response

    def run_shape_chain(self, chain, native, shape_chain):
        self.active_shape_native = native
        prior = None
        for shape_turn in shape_chain['turns']:
            identifier = chain if prior is None else '%s-%d' % (chain, shape_turn['n'])
            self.assign(identifier, native, prior=prior)
            packet = self.read_packet(identifier)
            expected = sorted((row[0], row[1], row[2], self.scale_size(row[3]), row[4]) for row in shape_turn['d'])
            delivered = packet['reading_turn']['reading_plan']['delivered_now']
            actual = sorted((self.find_shape_name(row)[0], row['part'], row['parts'], row['characters'],
                             self.find_shape_name(row)[1]) for row in delivered)
            self.assertEqual(actual, expected, 'shape: deliveries of %s turn %d' % (chain, shape_turn['n']))
            self.invoke(identifier, self.build_shape_answer(packet, shape_turn))
            self.shape_packets[chain, shape_turn['n']] = packet
            prior = identifier
        return prior

    def assert_cut_shape(self):
        records = self.store.snapshot()['records']
        self.shape_observations = {}
        for chain, expected in (('older-1', 44), ('stopped', 57)):
            first = records['review_assignment'][chain]
            last = records['review_assignment'][chain + '-%d' % expected]
            observed = v7.observe_carried_chain(records, self.artifacts, last)
            self.assertEqual(len(observed), expected, 'shape: ready reading prefix of ' + chain)
            self.assertTrue(all(row['turn']['kind'] == 'reading' for row in observed), 'shape: no final')
            self.shape_observations[chain] = observed
            shape_chain = self.shape['chains'][0 if chain == 'stopped' else 1]
            for shape_turn, actual in zip(shape_chain['turns'], observed):
                packet = self.shape_packets[chain, shape_turn['n']]
                deliveries = packet['reading_turn']['reading_plan']['delivered_now']
                expected_delivery = sorted((row[0], row[1], row[2], self.scale_size(row[3]), row[4])
                                           for row in shape_turn['d'])
                actual_delivery = sorted((self.find_shape_name(row)[0], row['part'], row['parts'], row['characters'],
                    self.find_shape_name(row)[1]) for row in deliveries)
                self.assertEqual(actual_delivery, expected_delivery, 'shape: reauthenticated physical deliveries')
                for member, status, counts in (('excerpts', 'verified', 'x'), ('passages', 'delivered', 'p')):
                    sources = {}
                    for entry in actual[member]:
                        if entry['status'] != status:
                            continue
                        kind = ('text' if entry['identity']['kind'] == 'fulltext' else
                            {'literature_search': 'search', 'reading': 'reading',
                             'problem_statement': 'statement'}[entry['identity']['record_kind']])
                        sources[kind] = sources.get(kind, 0) + 1
                    self.assertEqual(sources, shape_turn[counts],
                        'shape: %s output counts in %s turn %d' % (member, chain, shape_turn['n']))
        stopped = self.shape_observations['stopped']
        next_payload = dict(id='stopped-58', route_id='route', dossier_id=None, role='native_math',
            reviewer_id='openness-reviewer', author_id='problem-author',
            context=dict(native_packet=self.stopped_native, prior_assignment_id='stopped-57'))
        with self.assertRaises(ResearchError, msg='shape: the stopped chain cannot fit its next request') as caught:
            check_mutation(self.store, protocol.record_assignment, next_payload, expected_revision=self.store.revision,
                request_id='shape-next-request', bounded_payload=next_payload)
        self.assertIn(caught.exception.code, ('review_input_too_large', reading.PROJECTION_STOP),
                      'shape: capacity ends the stopped prefix')
        no_credit = [28, 29] + list(range(30, 49, 2))
        for number in no_credit:
            packet = self.shape_packets['stopped', number]
            deliveries = packet['reading_turn']['reading_plan']['delivered_now']
            self.assertTrue(all(row['record_kind'] == ('search_shared_field' if number < 30 else 'search_residual')
                for row in deliveries), 'shape: shared fields or residual first parts')
            if number >= 30:
                self.assertEqual([(row['part'], row['parts']) for row in deliveries], [(1, 2)])
            self.assertTrue(any(row['status'] == 'verified' and row['identity'].get('record_kind') == 'literature_search'
                                for row in stopped[number - 1]['excerpts']), 'shape: excerpt of an original search')
        self.assertGreaterEqual(sum(any(row['identity']['kind'] == 'fulltext' for row in
            stopped[number - 1]['excerpts'] + stopped[number - 1]['passages']) for number in no_credit), 3,
            'shape: earlier search parts retain full-text outputs')
        stopped_searches = {sha for row in stopped for sha in row['searches']}
        self.assertEqual(len(stopped_searches), 19, 'shape: stopped chain completed 19 searches')
        self.assertFalse(any(row['identity'].get('record_kind') in ('reading', 'problem_statement')
                             for observed in stopped for row in observed['deliveries']),
                         'shape: stopped chain reached neither reading records nor statement')
        older = self.shape_observations['older-1']
        delivered = [row for observed in older for row in observed['deliveries']]
        self.assertEqual(len({row['identity']['version_id'] for row in delivered
                              if row['identity']['kind'] == 'fulltext'}), 28, 'shape: older full texts')
        self.assertEqual(len({sha for observed in older for sha in observed['searches']}), 10, 'shape: older searches')
        self.assertEqual(len({row['identity']['record_id'] for row in delivered
                              if row['identity'].get('record_kind') == 'reading'}), 28, 'shape: older reading records')
        self.assertTrue(any(row['identity'].get('record_kind') == 'problem_statement' for row in delivered),
                        'shape: older statement record')
        minimum = json.loads(self.artifacts.read(self.native))['scientific_context']['openness_minimum']
        self.assertEqual((minimum['schema_version'], len(minimum['searches']), len(minimum['reading_digests']),
                          len(minimum['supporting_reading_digests']), len(minimum['neighbours'])),
                         (2, 20, 29, 2, 9), 'shape: new minimum and cited supporting readings')

    def start_shape_successor(self):
        receipt = self.assign('new', self.native)
        return receipt, json.loads(self.artifacts.read(receipt['carried_selection']['artifact']))

    def test_stopped_chain_carries_all_57_turns_and_every_output(self):
        receipt, selection = self.start_shape_successor()
        stopped = [row for row in selection['turns'] if row['origin']['chain'] == 'stopped']
        self.assertEqual(len(stopped), 57)
        selected = {row['origin']['assignment_id']: row for row in stopped}
        for number in [28, 29] + list(range(30, 49, 2)):
            assignment = 'stopped-%d' % number
            self.assertIn(assignment, selected)
            observed = self.shape_observations['stopped'][number - 1]
            expected = [dict(kind=kind, index=index) for kind in ('excerpt', 'passage')
                for index, row in enumerate(observed[kind + 's'])
                if row['status'] == ('verified' if kind == 'excerpt' else 'delivered')]
            self.assertEqual(selected[assignment]['entries'], expected)
        self.assertEqual(self.read_packet('new')['reading_turn']['notes'],
            self.shape_observations['stopped'][-1]['notes'])

    def test_selection_of_the_real_shape_is_of_version_3_and_verifies(self):
        from openness_legacy_fixtures import select_as_0_91_1
        receipt, selection = self.start_shape_successor()
        self.assertEqual(receipt['carried_selection']['protocol'], 'exactory-openness-carried-selection-v3')
        self.verify_stored()
        report = next(row for row in receipt['carried_readings']['outputs'] if row['chain'] == 'stopped')
        zero = dict(excerpts=0, passages=0, characters=0)
        self.assertEqual(report['superseded'], zero)
        self.assertEqual(report['source_changed'], zero)
        records, inventory, identity, selection, cutoff = self.read_selection_context()
        previous = select_as_0_91_1(cutoff.records, cutoff, inventory, identity)
        def list_credits(turns):
            return {row['origin']['assignment_id']: (row['ranges'], row['searches']) for row in turns
                    if row['ranges'] or row['searches']}
        self.assertEqual(list_credits(selection['turns']), list_credits(previous))

    def test_v7_chain_reaches_a_verified_finding_with_the_carried_outputs(self):
        from codex_route_fixtures import build_codex_response
        from research_harness import review_excerpts
        from research_harness.review_series import run_series
        receipt, selection = self.start_shape_successor()
        packets = []
        def respond(route, request, credential):
            packet = review_excerpts.read_sent_packet(request)
            packets.append(packet)
            if 'reading_turn' in packet:
                output = self.answer_v7(packet)
            else:
                binding = packet['reading_final']
                # The final of a new chain takes finding version 2, where a reviewer_gap needs the fields of a
                # named gap; an own argument keeps the outcome unresolved, as in finish_chain.
                output = dict(schema_version=3,
                    **{key: binding[key] for key in ('subject_digest', 'claim_digest', 'reviewer')},
                    findings=dict({key: 'Assessed the supplied formulation.' for key in
                        ('statement', 'assumptions', 'scope', 'dependencies', 'policy')},
                        openness=dict(outcome='unresolved', coverage='Reviewed the bound material.',
                            reason='A remaining literature gap.', grounds=[dict(kind='own_argument',
                                text='The reviewer argument requires further checking.')])) )
            return build_codex_response(route, request, output)
        steps = []
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            self.assertEqual(run_series(self.store, 'new', emit=steps.append), 0, steps[-1] if steps else [])
        records = self.store.snapshot()['records']
        own = [row for row in records['review_assignment'].values()
               if row.get('reading_chain', {}).get('first_assignment_id') == 'new']
        last = max(own, key=lambda row: row['reading_chain']['turn'])
        final = self.read_packet(last['id'])
        final_entries = final['reading_final']['entries']
        delivered_entries = []
        for packet in packets:
            if 'reading_turn' not in packet:
                continue
            assignment = next(row for row in own if row['reading_chain']['turn'] == packet['reading_turn']['turn'])
            delivered_entries.extend(v7._list_delivered_carried_entries(packet, self.artifacts, assignment).values())
        present = final_entries + delivered_entries
        inventory = v7.read_carried_inventory(self.artifacts, records['review_assignment']['new'])
        sources = {(digest(row['identity']), row['sha256']): row['handle'] for row in inventory['units']}
        def build_entry_key(row):
            return tuple(row[key] for key in ('kind', 'unit', 'start', 'end', 'sha256', 'text', 'purpose', 'status'))
        present_keys = {build_entry_key(row) for row in present}
        missing = []
        for number in [28, 29] + list(range(30, 49, 2)):
            observation = self.shape_observations['stopped'][number - 1]
            for kind in ('excerpt', 'passage'):
                for index, entry in enumerate(observation[kind + 's']):
                    expected = dict(entry, kind=kind, unit=sources[digest(entry['identity']), entry['source_sha256']])
                    if build_entry_key(expected) not in present_keys:
                        missing.append(('stopped-%d' % number, kind, index))
        self.assertEqual(missing, [], 'Every output of the twelve turns reaches the final pool or a delivered unit')
        verified = reading.verify_chain(records, self.artifacts, last)
        finding = next(row for row in records['problem_openness'].values() if row['assignment_id'] == last['id'])
        self.assertEqual(verified, finding['reading_turns'])
        self.assertFalse(any(row['kind'] == 'fulltext' for packet in packets if 'reading_turn' in packet
                             for row in packet['reading_turn']['plan']['now']))
        for kind, group in self.old_records.items():
            self.assertEqual({key: digest(row) for key, row in group.items()},
                {key: digest(records[kind][key]) for key in group})


def digest_directory(path):
    """The digest of the relative path and the SHA-256 of the bytes of each file under a directory."""
    return digest([[item.relative_to(path).as_posix(), hashlib.sha256(item.read_bytes()).hexdigest()]
                   for item in sorted(path.rglob('*')) if item.is_file()])


@functools.lru_cache(maxsize=None)
def build_record12_cut():
    """Build the record-12 cut once in this process (principle 3) and keep it until the process ends: the built
    store in a directory of its own, the built state, and the digest of the store's bytes. Each user loads a writable
    copy (Record12ShapeTests.load_cut), so no user writes to this build; ci/unittest_shards.py keeps the modules of
    its users in one shard."""
    directory = tempfile.TemporaryDirectory(prefix='record12-cut-')
    template = Record12ShapeTests.build_fixture()
    try:
        store = Path(shutil.copytree(template.root, Path(directory.name) / 'store'))
        state = {name: copy.deepcopy(getattr(template, name)) for name in Record12ShapeTests.CUT_STATE}
    finally:
        template.doCleanups()
    return dict(directory=directory, store=store, state=state, sha256=digest_directory(store))


def check_record12_cut_unchanged():
    """Fail when the one build of this process no longer has the bytes that it had when it was built."""
    built = build_record12_cut()
    if digest_directory(built['store']) != built['sha256']:
        raise AssertionError('The one build of the record-12 cut changed while its users ran.')


class FullPoolCapacityTests(unittest.TestCase):
    def test_legacy_notes_below_the_reading_floor_refuse_before_delivery(self):
        from test_review_capacity_plan import build_plan
        from v7_capacity_fixtures import build_route
        plan = build_plan()
        plan['limits']['input_text_characters']['value'] = 1
        with self.assertRaises(ResearchError) as error:
            v7._reserve_initial_notes(build_route(effort='xhigh'), plan, 'A synthesis. ' + '\\"' * 9500)
        self.assertEqual(error.exception.code, 'review_route_capacity_below_floor')
        self.assertIn('initial_notes_characters', error.exception.details)

    def test_q12_pool_and_every_target_grant_share_one_bounded_final(self):
        from test_openness_reading_v7_turns import MemoryArtifacts, build_native
        from test_openness_reading_v7_state import build_source, build_observation, select_observation
        from v7_capacity_fixtures import build_route
        from test_review_output_profile import build_row
        artifacts, rows, bodies, observations = MemoryArtifacts(), [], {}, []
        route = build_route('codex_cli_v1', 'gpt-6-astra')
        from test_review_capacity_closure import build_capacity_records
        records = build_capacity_records(route, tokens=487900, capacity_probe=True)
        native = build_native(3)
        for number in range(1, 58):
            spans = [('Span %03d/%02d. ' % (number, index) + 'x' * 170)[:170] for index in range(19)]
            row, body = build_source(''.join(spans), 'work-%d' % number)
            rows.append(row)
            bodies[row['artifact']['sha256']] = body
            artifacts.put(body, row['artifact']['media_type'])
            observed = build_observation(row, body, number, excerpt=False)
            for index, text in enumerate(spans):
                observed['excerpts'].append(dict(identity=observed['deliveries'][0]['identity'],
                    source_sha256=row['artifact']['sha256'], start=index * 170, end=(index + 1) * 170,
                    sha256=hashlib.sha256(text.encode('utf-8')).hexdigest(), text=text, purpose='p' * 125, status='verified'))
            observations.append(observed)
        gaps = artifacts.put_json([])
        rows.append(dict(kind='record', record_kind='minimum_gaps', record_id='minimum_gaps', artifact=gaps))
        bodies[gaps['sha256']] = b'[]'
        built = v7.plan_inventory(rows, bodies)
        priors = [build_row(level=None, thinking=1000, count=8), build_row(kind='final', level=None, thinking=1000, count=8)]
        with patch.object(v7, 'build_inventory', return_value=built), patch.object(capacity, 'read_profile_priors', return_value=priors):
            admission = v7.check_v7_admission(records, artifacts, route, native)
        inventory = admission['inventory']['inventory']
        projections = [v7.project_carried_turn(inventory, observed, select_observation(inventory, observed))
                       for observed in observations]
        logical = dict(protocol='fixture', role='native_math', reviewer_id=native['reviewer'], dossier_id=None, native=native)
        packet, assignment = v7.prepare_first_assignment(records, artifacts, dict(id='chain'), route, logical, projections, admission=admission)
        plan = capacity.read_chain_capacity_plan(artifacts, assignment)
        state = json.loads(artifacts.read(assignment['state']))
        inventory = json.loads(artifacts.read(assignment['inventory']))
        self.assertTrue(state['carried_readings'])
        self.assertGreater(state['allowances']['pool']['used'], 400000)
        self.assertLessEqual(state['allowances']['pool']['used'] + state['allowances']['notes']
            + sum(row['characters'] for row in state['allowances']['targets']), plan['task']['final']['material_allowance'])
        # Consume the unused target grants alongside a full notes grant. These
        # represent retained own readings after the overflow units were read.
        entries, usage = [], {}
        for index, target in enumerate(state['allowances']['targets']):
            source = inventory['units'][index]
            text = artifacts.read(source['artifact']).decode('utf-8')
            entry = dict(kind='excerpt', unit=source['handle'], text=text, purpose='p', targets=[target['handle']],
                start=0, end=len(text), sha256=source['sha256'], status='verified')
            charge = capacity.measure_capacity_value(route, dict(entry, handle='e999999999'))
            self.assertLess(charge, target['characters'])
            entries.append(entry)
            usage[target['handle']] = charge
        own = dict(turn=dict(assignment_id='own', attempt_id='try-own', kind='reading', origin=None), deliveries=[],
            entries=entries, notes=dict(classification='current', units=[], text='n' * state['allowances']['notes']),
            gaps=[], arguments=[], searches=[], allowance_use=dict(targets=usage, passages=0))
        rebuilt = v7.build_chain_state(inventory, [own], route=route, plan=plan, initial_state=state)
        final = v7.build_turn_packet(artifacts, route, logical, 'chain', 9, inventory, rebuilt['state'], plan,
            capacity.derive_plan_task_limits(plan, 'final'), 'final')
        request = protocol.transport.request_body(route, protocol.canonical_prompt('native_math', reading_stage='final',
            reading_protocol=v7.PROTOCOL)['text'], final)
        self.assertTrue(capacity.describe_plan_preflight({}, route, request, plan, 'final')['plan_comparison']['fits'])


if __name__ == '__main__':
    unittest.main()
