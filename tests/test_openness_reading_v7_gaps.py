"""The five v7 gaps, with synthetic command chains and immutable artifact requests."""

import json
import unittest
from unittest.mock import patch

import test_openness_reading_v7_turns as turns
import test_openness_carried_integration as carried
from openness_reading_fixtures import OpennessReadingCase
from research_harness import openness_reading as reading, openness_reading_v7 as v7
from research_harness import review_protocol as protocol, review_route_capacity as capacity
from research_harness.errors import ResearchError
from research_harness.openness_minimum import GAP_REFUSALS
from research_harness.problem_openness import serialize_record
from research_harness.review_continuation import plan_next
from v7_capacity_fixtures import build_route


class GapCommandTests(OpennessReadingCase):
    capacity_probe_tokens = 487900
    reading_protocol = None
    route_model = 'gpt-6-astra'
    route_output_tokens = 128000
    text_sizes = (50000, 50000, 50000)
    build_v7_answer = turns.V7CommandTests.build_v7_answer
    build_v7_finding = turns.V7CommandTests.build_v7_finding
    finish_reading = turns.V7CommandTests.finish_reading

    def setUp(self):
        super().setUp()
        enabled = patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6))
        enabled.start()
        self.addCleanup(enabled.stop)

    def assert_fits(self, head):
        records = self.store.snapshot()['records']
        assignment = records['review_assignment'][head]
        plan = capacity.read_chain_capacity_plan(self.artifacts, assignment)
        request = protocol.build_assignment_request(records, self.artifacts, assignment)
        report = capacity.describe_plan_preflight(records, records['review_route']['route'], request, plan,
                                                assignment['capacity_plan_kind'])
        self.assertTrue(report['plan_comparison']['fits'], report)

    def finish_from(self, native, head):
        for number in range(100):
            self.assert_fits(head)
            packet = self.read_packet(head)
            if 'reading_final' in packet:
                answer = self.build_v7_finding(packet, packet['reading_final']['notes_handle'])
                self.invoke(head, answer)
                records = self.store.snapshot()['records']
                self.assertTrue(v7.verify_chain(records, self.artifacts, records['review_assignment'][head]))
                return head
            self.invoke(head, self.build_v7_answer(packet))
            following = 'finish-%d' % number
            self.assign(following, native, prior=head)
            head = following
        self.fail('The chain must reach a verified finding.')

    def test_decisive_ids_are_visible_and_source_settles_checks_the_list(self):
        native = self.export()
        head = self.finish_reading(native)
        packet = self.read_packet(head)
        ids = json.loads(self.artifacts.read(native))['scientific_context']['decisive_work_ids']
        self.assertTrue(ids)
        self.assertEqual(packet['native']['scientific_context']['decisive_work_ids'], ids)
        assignment = self.store.snapshot()['records']['review_assignment'][head]
        answer = self.build_v7_finding(packet, packet['reading_final']['notes_handle'])
        answer['findings']['openness']['grounds'][0].pop('refs')
        answer['findings']['openness']['grounds'].append(dict(kind='source_settles', text='The decisive result.', work_id=ids[0]))
        v7.validate_turn_output(packet, answer, artifacts=self.artifacts, assignment=assignment)
        answer['findings']['openness']['grounds'][-1]['work_id'] = 'unlisted-work'
        with self.assertRaises(ResearchError) as error:
            v7.validate_turn_output(packet, answer, artifacts=self.artifacts, assignment=assignment)
        self.assertEqual(error.exception.details['rule'], 'decisive_work')
        answer['findings']['openness']['grounds'][-1]['work_id'] = ids[0]
        self.invoke(head, answer)
        records = self.store.snapshot()['records']
        self.assertTrue(v7.verify_chain(records, self.artifacts, records['review_assignment'][head]))

    def test_oversized_decisive_ids_are_delivered_and_requested_from_final(self):
        with patch.object(v7, 'DECISIVE_WORK_IDS_CHARACTERS', 1):
            native = self.export()
            head = self.finish_reading(native)
            packet = self.read_packet(head)
            compact = packet['native']['scientific_context']['decisive_work_ids']
            ids = json.loads(self.artifacts.read(native))['scientific_context']['decisive_work_ids']
            self.assertEqual(compact['count'], len(ids))
            inventory = json.loads(self.artifacts.read(self.store.snapshot()['records']['review_assignment'][head]['reading_chain']['inventory']))
            part = next(row for row in inventory['material'] if row['handle'] == compact['unit'])
            self.assertEqual(part['label'], 'decisive_work_ids')
            answer = self.build_v7_answer(packet)
            answer['passage_requests'] = [dict(id='decisive-list', unit=compact['unit'], anchor=None, characters=10000)]
            self.invoke(head, answer)
            self.assign('requested', native, prior=head)
            requested = self.read_packet('requested')
            row = next(row for row in requested['reading_turn']['plan']['now'] if row.get('request') == 'decisive-list')
            body = next(row for row in requested['evidence'] if row['handle'] == compact['unit'])
            self.assertEqual(json.loads(body['content']), ids)
            self.assertEqual(row['handle'], compact['unit'])
            self.finish_from(native, 'requested')

    def test_logical_source_request_and_null_anchor_survive_capacity_replacement(self):
        from codex_route_fixtures import build_codex_response, bind_codex_response
        from test_review_output_profile import build_row
        native = self.export()
        with patch.object(capacity, 'read_profile_priors', return_value=[build_row(level=None, thinking=1000, count=8)]):
            head = self.finish_reading(native)
            packet = self.read_packet(head)
            assignment = self.store.snapshot()['records']['review_assignment'][head]
            _, inventory, _ = v7.read_bound_state(self.artifacts, assignment, packet)
            logical = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
            answer = self.build_v7_answer(packet)
            answer['passage_requests'] = [dict(id='logical-start', unit=logical['handle'], anchor=None, characters=400)]
            self.invoke(head, answer)
            self.assign('passage', native, prior=head)
            expected = next(row for row in self.read_packet('passage')['reading_turn']['plan']['now'] if row.get('request'))
            self.assertIn(expected['handle'], logical['bodies'])
            self.assertEqual(expected['start'], 0)
            def respond(route, request, credential):
                response = build_codex_response(route, request, {})
                response['status'] = 'incomplete'
                response['usage']['output_tokens'] = 138371
                return bind_codex_response(route, request, response)
            with patch.object(protocol.transport, 'send_request', side_effect=respond):
                self.mutate(protocol.invoke_assignment, dict(id='failed-passage', assignment_id='passage'), credential='fixture')
            recovery = plan_next(self.store.snapshot()['records'], self.artifacts, 'passage')
            replacement = self.mutate(protocol.record_assignment, recovery['payload'])['result']['id']
            packet = self.read_packet(replacement)
            row = next(row for row in packet['reading_turn']['plan']['now'] if row.get('request'))
            self.assertEqual((row['handle'], row['start'], row['request']), (expected['handle'], 0, 'logical-start'))
            answer = self.build_v7_answer(packet)
            body = next(body for body in packet['evidence'] if body['handle'] == row['handle'])
            self.assertEqual(body['content'], self.artifacts.read(logical['artifact']).decode()[:400])
            answer['excerpts'] = [dict(unit=row['handle'], text=body['content'][:100], purpose='The opening.', targets=['t1'])]
            self.invoke(replacement, answer)
            self.assign('after-passage', native, prior=replacement)
            self.finish_from(native, 'after-passage')

    def test_minimum_gap_rows_name_targets_and_the_record_is_requestable(self):
        native = json.loads(self.artifacts.read(self.export()))
        minimum = native['scientific_context']['openness_minimum']
        minimum['neighbours'] = ['A neighbouring synthetic formulation.']
        native['scientific_context']['minimum_gaps'] = [dict(formulation=formulation, condition='reading_missing',
            outcomes=['supported_open', 'already_solved'], details='A synthetic missing reading.')
            for formulation in [minimum['statement']] + minimum['neighbours']]
        reference = protocol._put_json(self.artifacts, native)
        head = self.finish_reading(reference)
        packet = self.read_packet(head)
        self.assertEqual(packet['reading_final']['gaps'], [dict(handle='g%d' % index, condition=row['condition'],
            outcomes=row['outcomes'], target='t%d' % (index + 1)) for index, row in enumerate(native['scientific_context']['minimum_gaps'])])
        answer = self.build_v7_answer(packet)
        answer['passage_requests'] = [dict(id='gap-details', unit=packet['reading_final']['minimum']['gaps_unit'],
                                          anchor=None, characters=10000)]
        self.invoke(head, answer)
        self.assign('gap-record', reference, prior=head)
        body = next(body for row, body in zip(self.read_packet('gap-record')['reading_turn']['plan']['now'],
            self.read_packet('gap-record')['evidence']) if row.get('request') == 'gap-details')
        self.assertEqual(json.loads(body['content']), native['scientific_context']['minimum_gaps'])
        self.finish_from(reference, 'gap-record')

    def test_reading_material_refuses_without_credit_and_replacement_reads(self):
        native = self.export()
        self.assign('turn-1', native)
        before = self.read_packet('turn-1')
        result = self.invoke('turn-1', dict(stage='material', title='An argument', part=1, text='A proof outline.', more=True))
        refusal = result['output_refusal']
        self.assertEqual((refusal['code'], refusal['check'], refusal['details']['path']),
                         ('invalid_review_material', 'review.shape', '/stage'))
        records = self.store.snapshot()['records']
        assigned = records['review_assignment']['turn-1']
        state = json.loads(self.artifacts.read(assigned['reading_chain']['state']))
        self.assertEqual(state['deliveries'], [])
        self.assertEqual(state['entries'], [])
        self.assertFalse(protocol.assignment_state(records, self.artifacts, 'turn-1')['ready'])
        recovery = plan_next(records, self.artifacts, 'turn-1')
        replacement = self.mutate(protocol.record_assignment, recovery['payload'])['result']['id']
        packet = self.read_packet(replacement)
        self.assertNotIn('material_request', packet['reading_turn'])
        self.assertIn('final turn', packet['output_refusal']['refused'][0]['message'])
        self.assertEqual(packet['reading_turn']['plan']['now'][0]['handle'], before['reading_turn']['plan']['now'][0]['handle'])
        self.finish_from(native, replacement)


class GapBudgetTests(unittest.TestCase):
    def test_forecast_vocabulary_rows_fit_both_routes(self):
        from pathlib import Path
        declarations = json.loads((Path(__file__).parent / 'fixtures' / 'capacity_packet_schema.json').read_text())['definitions']
        self.assertEqual(declarations['v7_forecast_gap']['class'], 'fixed')
        self.assertEqual(declarations['v7_forecast_gap']['bound']['serialized_characters'], capacity.GAP_ROW_CHARACTERS)
        self.assertEqual(declarations['v7_reviewer_gap']['class'], 'material')
        for adapter, model in [('codex_cli_v1', 'gpt-6-astra'), ('claude_cli_v1', 'claude-opus-5-5')]:
            route = build_route(adapter, model)
            for condition in GAP_REFUSALS:
                row = dict(handle='g999999999', condition=condition, outcomes=['supported_open', 'already_solved'], target='t999999999')
                self.assertLessEqual(capacity.measure_capacity_value(route, row), capacity.GAP_ROW_CHARACTERS)
            fields = v7.measure_turn_frames(route, turns.build_native())['final']
            self.assertIn('minimum.gaps_unit', fields)

    def test_decisive_frame_measures_list_and_compact_forms_and_exact_bound_fits(self):
        for adapter, model in [('codex_cli_v1', 'gpt-6-astra'), ('claude_cli_v1', 'claude-opus-5-5')]:
            route = build_route(adapter, model)
            native = turns.build_native()
            ids = ['w' * (v7.DECISIVE_WORK_IDS_CHARACTERS - capacity.measure_capacity_value(route, ['']))]
            native['scientific_context']['decisive_work_ids'] = ids
            self.assertEqual(capacity.measure_capacity_value(route, ids), v7.DECISIVE_WORK_IDS_CHARACTERS)
            self.assertEqual(v7.measure_turn_frames(route, native)['final']['decisive_work_ids'], v7.DECISIVE_WORK_IDS_CHARACTERS)
            bounds = dict(statement=100000, formulations=100000, native_frame=100000, decisive_work_ids=0)
            self.assertEqual(v7.measure_turn_frames(route, native, fixed_bounds=bounds)['final']['decisive_work_ids'],
                             capacity.measure_capacity_value(route, dict(count=1, unit='u999999999')))
            fixture = turns.TurnPacketTests()
            fixture.setUp()
            fixture.route = route
            fixture.native = native
            fixture.packet['native'] = native
            from test_review_output_profile import build_row
            priors = [build_row(level=None, thinking=1000, count=8),
                      build_row(kind='final', level=None, thinking=1000, count=8)]
            with patch.object(capacity, 'read_profile_priors', return_value=priors):
                packet, chain = fixture.prepare()
            plan = capacity.read_chain_capacity_plan(fixture.artifacts, chain)
            inventory = json.loads(fixture.artifacts.read(chain['inventory']))
            state = json.loads(fixture.artifacts.read(chain['state']))
            limits = capacity.derive_plan_task_limits(plan, 'final')
            state['latest_notes'] = 'n' * limits['material_allowance']
            final = v7.build_turn_packet(fixture.artifacts, route, fixture.packet, 'chain', 2, inventory, state, plan, limits, 'final')
            request = protocol.transport.request_body(route, protocol.canonical_prompt('native_math', reading_stage='final',
                reading_protocol=v7.PROTOCOL)['text'], final)
            self.assertTrue(capacity.describe_plan_preflight({}, route, request, plan, 'final')['plan_comparison']['fits'])

    def test_own_gap_text_charges_wire_cost_and_excess_is_not_shown(self):
        fixture = turns.TurnPacketTests()
        fixture.setUp()
        packet, chain = fixture.prepare()
        plan = capacity.read_chain_capacity_plan(fixture.artifacts, chain)
        inventory = json.loads(fixture.artifacts.read(chain['inventory']))
        state = json.loads(fixture.artifacts.read(chain['state']))
        answer = fixture.answer(packet)
        answer['gaps'] = [dict(formulation='t1', text='"' * 600), dict(formulation='t2', text='g' * 600)]
        answer['gaps'] += [dict(formulation=target, text='g' * 600) for target in ('t1', 't2', 't3') for _ in range(100)]
        projected = v7.project_own_turn(fixture.artifacts, fixture.route, packet, answer, 'one', 'attempt', state=state)
        self.assertTrue(any(row['status'] == 'over_allowance' for row in projected['gaps']))
        for target in ('t1', 't2', 't3'):
            retained = [row for row in projected['gaps'] if row['formulation'] == target and row['status'] == 'verified']
            expected = sum(capacity.measure_capacity_value(fixture.route,
                dict(handle='h999999999', formulation=row['formulation'], text=row['text'])) for row in retained)
            self.assertEqual(projected['allowance_use']['targets'][target], expected)
        rebuilt = v7.build_chain_state(inventory, [projected], route=fixture.route, plan=plan)['state']
        final = v7.build_turn_packet(fixture.artifacts, fixture.route, fixture.packet, 'chain', 2, inventory, rebuilt, plan,
                                    capacity.derive_plan_task_limits(plan, 'final'), 'final')
        self.assertEqual(final['reading_final']['gaps'][:2], [dict(handle='h%d' % (index + 1), **row)
                                                            for index, row in enumerate(answer['gaps'][:2])])
        self.assertEqual(len(final['reading_final']['gaps']), sum(row['status'] == 'verified' for row in projected['gaps']))
        self.assertLessEqual(v7._measure_final_material(fixture.route,
            {key: final['reading_final'][key] for key in v7.FINAL_MATERIAL_FIELDS}), plan['task']['final']['material_allowance'])

    def test_factored_search_logical_handles_resolve_shared_and_residual_fields(self):
        artifacts = turns.MemoryArtifacts()
        values = [dict(id='search-%d' % n, shared='shared phrase', unique='residual phrase %d' % n) for n in range(2)]
        searches = [dict(kind='record', record_kind='literature_search', record_id=value['id'],
                        artifact=artifacts.put_json(value)) for value in values]
        bindings = [dict(formulation='A formulation.', search_digest=row['artifact']['sha256'], search=value)
                    for row, value in zip(searches, values)]
        built = v7.plan_inventory(searches, artifacts.bodies, search_bindings=bindings)
        prepared = v7.prepare_inventory(built, turns.build_native(), build_route('codex_cli_v1', 'gpt-6-astra'))
        inventory = prepared['inventory']
        for body in prepared['bodies'].values(): artifacts.put(body)
        unit = next(row for row in inventory['units'] if row['label'] == 'search-0')
        fixture = turns.TurnPacketTests()
        fixture.setUp()
        fixture.artifacts, fixture.built = artifacts, built
        packet, chain = fixture.prepare()
        assignment = dict(reading_chain=chain, capacity_plan=chain['capacity_plan'])
        parts = []
        for anchor in ('shared phrase', 'residual phrase 0'):
            row = v7.resolve_request_part(inventory, artifacts, dict(unit=unit['handle'], anchor=anchor))
            self.assertIsNotNone(row)
            self.assertIn(anchor, artifacts.read(row['artifact']).decode())
            parts.append(row['handle'])
            answer = fixture.answer(packet)
            answer['passage_requests'] = [dict(id='field', unit=unit['handle'], anchor=anchor, characters=10)]
            v7.validate_turn_output(packet, answer, artifacts=artifacts, assignment=assignment)
        self.assertNotEqual(*parts)
        self.assertIsNone(v7.resolve_request_part(inventory, artifacts, dict(unit=unit['handle'], anchor='shared phrase residual phrase')))
        answer['passage_requests'][0]['anchor'] = 'shared phrase residual phrase'
        with self.assertRaises(ResearchError) as error:
            v7.validate_turn_output(packet, answer, artifacts=artifacts, assignment=assignment)
        self.assertEqual(error.exception.code, 'invalid_reading_response')

    def test_invalid_anchors_and_unknown_handles_keep_the_reading_refusal(self):
        fixture = turns.TurnPacketTests()
        fixture.setUp()
        packet, chain = fixture.prepare()
        valid = fixture.answer(packet)
        valid['passage_requests'] = [dict(id='start', unit=packet['reading_turn']['plan']['now'][0]['handle'],
                                         anchor=None, characters=100)]
        v7.validate_turn_output(packet, valid, artifacts=fixture.artifacts,
            assignment=dict(reading_chain=chain, capacity_plan=chain['capacity_plan']))
        for unit, anchor in [('u999999999', None), (packet['reading_turn']['plan']['now'][0]['handle'], ''),
                             (packet['reading_turn']['plan']['now'][0]['handle'], 'absent phrase')]:
            answer = fixture.answer(packet)
            answer['passage_requests'] = [dict(id='bad', unit=unit, anchor=anchor, characters=100)]
            with self.assertRaises(ResearchError) as error:
                v7.validate_turn_output(packet, answer, artifacts=fixture.artifacts,
                    assignment=dict(reading_chain=chain, capacity_plan=chain['capacity_plan']))
            self.assertEqual(error.exception.code, 'invalid_reading_response')

    def test_four_hundred_notes_of_one_chain_have_individual_rows_and_complete_pages(self):
        plan = dict(task=dict(reading=dict(material_allowance=15000)))
        notes = [dict(handle='u%d' % n, origin=dict(chain='origin'), characters=20000 if n == 0 else 50,
                      delivery_characters=20000 if n == 0 else 100) for n in range(401)]
        state = dict(affected_notes=notes, list_pages={})
        index = v7._describe_affected_note_index(plan, state)
        self.assertLessEqual(len(serialize_record(index).decode()), 10000)
        self.assertGreater(index['omitted'], 0)
        self.assertEqual(len(index['rows']) + index['omitted'], 400)
        after, seen = None, []
        while True:
            page = v7.build_list_page('affected_notes', after, plan, state,
                                     build_route('codex_cli_v1', 'gpt-6-astra'), 5000)
            seen.extend(row['handle'] for row in page['rows'])
            self.assertTrue(all(row['id'] == row['handle'] for row in page['rows']))
            state['list_pages']['affected_notes'] = dict(next=page['next'], ended=page['next'] is None)
            if page['next'] is None:
                break
            after = page['next']
        self.assertEqual(seen, [row['handle'] for row in notes[1:]])
        self.assertEqual(v7.LIST_PAGE_SUPPLIERS['affected_notes']['row_characters'], 512)

    def test_pending_material_is_final_only_and_legacy_material_keeps_its_bytes(self):
        from research_harness import review_handles
        from research_harness.evidence import digest
        output = dict(stage='material', title='An argument', part=1, text='Part one.', more=True)
        for protocol_name in (reading.PROTOCOL_V5, reading.PROTOCOL_V6):
            legacy = dict(handle_table={}, reading_turn=dict(protocol=protocol_name), capacity=dict(answer_bound=18000))
            before = serialize_record(legacy)
            review_handles.validate_material(legacy, output)
            self.assertEqual(serialize_record(legacy), before)
        fixture = turns.TurnPacketTests()
        fixture.setUp()
        packet, chain = fixture.prepare()
        with self.assertRaises(ResearchError) as error:
            review_handles.validate_material(dict(packet, handle_table={}, capacity=dict(answer_bound=18000)), output)
        self.assertEqual(error.exception.code, 'invalid_review_material')
        plan = capacity.read_chain_capacity_plan(fixture.artifacts, chain)
        inventory = json.loads(fixture.artifacts.read(chain['inventory']))
        state = json.loads(fixture.artifacts.read(chain['state']))
        state['material_state'] = dict(arguments={}, pending=dict(ref='m1', part=2))
        state['material_request'] = dict(stage='material', title='An argument', ref='m1', part=2)
        before = digest(state)
        rendered = v7.build_turn_packet(fixture.artifacts, fixture.route, fixture.packet, 'chain', 2,
            inventory, state, plan, capacity.derive_plan_task_limits(plan, 'reading'), 'reading')
        self.assertNotIn('material_request', rendered['reading_turn'])
        self.assertEqual(digest(state), before)


class AffectedNoteGapTests(carried.CarriedIntegrationSupport, OpennessReadingCase):
    text_sizes = (50000,) * 8
    add_earlier_turns = carried.AffectedNotesIntegrationTests.add_earlier_turns
    use_floor_plan = carried.AffectedNotesIntegrationTests.use_floor_plan

    def build_reading_response(self, packet, excerpts=None, passages=()):
        output = super().build_reading_response(packet, excerpts=excerpts, passages=passages)
        output['notes'] = 'Earlier synthesis. ' + 'n' * 4000
        return output

    def test_four_notes_index_middle_null_request_charges_and_verifies(self):
        self.add_earlier_turns(4)
        with self.change_inventory(self.versions[0]), self.use_floor_plan():
            native, receipt = self.start_chain()
            packet = self.read_packet('new')
            rows = packet['reading_turn']['affected_notes']['index']['rows']
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(set(row) == {'handle', 'chain', 'characters'} for row in rows))
            self.assertEqual([row['chain'] for row in rows], ['turn-1'] * 3)
            desired = rows[1]
            requested = False
            observed = []
            def answer(packet):
                nonlocal requested
                output = self.answer_v7(packet)
                if not requested:
                    output['passage_requests'] = [dict(id='middle-note', unit=desired['handle'], anchor=None, characters=desired['characters'])]
                    requested = True
                for row, body in zip(packet['reading_turn']['plan']['now'], packet['evidence']):
                    if row.get('request') == 'middle-note':
                        observed.append(row)
                        value = json.loads(body['content'])
                        self.assertEqual(set(value), {'notes', 'origin'})
                        self.assertEqual(value['origin']['assignment_id'], 'old-2')
                        self.assertEqual(row['start'], 0)
                return output
            packets = self.finish_chain(native, answer=answer)
            self.assertEqual(len(observed), 1)
            self.assertEqual(observed[0]['characters'], desired['characters'])
            self.assertEqual(packets[-1]['reading_final']['allowances']['passages']['used'], desired['characters'])
            records = self.store.snapshot()['records']
            head = max((row for row in records['review_assignment'].values()
                        if row.get('reading_chain', {}).get('first_assignment_id') == 'new'),
                       key=lambda row: row['reading_chain']['turn'])
            self.assertTrue(v7.verify_chain(records, self.artifacts, head))

    def test_carried_entry_logical_source_can_be_requested_from_final(self):
        native, receipt = self.start_chain()
        head = 'new'
        for number in range(100):
            packet = self.read_packet(head)
            if 'reading_final' in packet:
                break
            self.invoke(head, self.answer_v7(packet))
            following = 'carried-%d' % number
            self.assign(following, native, prior=head)
            head = following
        source = next(row for row in packet['reading_final']['sources'] if row['kind'] == 'fulltext')
        entry = next(row for row in packet['reading_final']['entries'] if row['unit'] == source['handle'])
        answer = self.answer_v7(packet)
        answer['passage_requests'] = [dict(id='carried-phrase', unit=source['handle'], anchor=entry['text'][:40], characters=200)]
        self.invoke(head, answer)
        self.assign('carried-passage', native, prior=head)
        packet = self.read_packet('carried-passage')
        row = next(row for row in packet['reading_turn']['plan']['now'] if row.get('request') == 'carried-phrase')
        self.assertNotEqual(row['handle'], source['handle'])
        body = next(body for body in packet['evidence'] if body['handle'] == row['handle'])
        self.assertIn(entry['text'][:40], body['content'])
        answer = self.answer_v7(packet)
        answer['excerpts'] = [dict(unit=row['handle'], text=body['content'][:100], purpose='The carried source.', targets=['t1'])]
        self.invoke('carried-passage', answer)
        self.assign('after-carried', native, prior='carried-passage')
        self.finish_chain(native, first='after-carried')
        records = self.store.snapshot()['records']
        head = max((row for row in records['review_assignment'].values()
                    if row.get('reading_chain', {}).get('first_assignment_id') == 'new'),
                   key=lambda row: row['reading_chain']['turn'])
        self.assertTrue(v7.verify_chain(records, self.artifacts, head))
        finding = self.mutate(self.load_problem_module().record_openness, dict(assignment_id=head['id']))['result']
        self.assertEqual(finding['outcome'], 'unresolved')
