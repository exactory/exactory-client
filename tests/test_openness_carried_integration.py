"""Command regressions for D2 G8, A4 and Q12, with synthetic scientific content."""

import copy
import hashlib
import json
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
                            reason='A remaining gap.', grounds=[dict(kind='reviewer_gap', text='Further work is needed.')])) )
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
        # An authentic later chain, even after the stored cutoff, cannot cause reselection.
        self.enabled.stop()
        later = self.export(name='later-export', assignment_id='later')
        self.assign('later', later)
        self.invoke('later', self.build_reading_response(self.read_packet('later'), excerpts=[]))
        self.enabled.start()
        from research_harness import openness_carried
        with patch.object(openness_carried, 'select_carried_readings', side_effect=AssertionError('reselection')):
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
