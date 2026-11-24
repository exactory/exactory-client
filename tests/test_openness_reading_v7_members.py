"""A new v7 chain states the count of the notes bound and the lists that hold rows; a stored chain keeps its bytes."""

import copy
import json
import re
import unittest
from unittest.mock import patch

from research_harness import openness_final_pages as final_pages, openness_reading as reading
from research_harness import openness_reading_v7 as v7, review_final_pages as pages
from research_harness import review_prompt_paragraphs as paragraphs, review_protocol as protocol
from research_harness import review_route_capacity as capacity
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.review_transport import request_body
from test_openness_reading_v7_turns import MemoryArtifacts, build_inventory, build_native
from v7_capacity_fixtures import build_route

MEMBER_VERSIONS = ['exactory-openness-notes-cost-v1', 'exactory-openness-list-rows-v1']


class MemberCase(unittest.TestCase):
    targets = 3

    def setUp(self):
        self.artifacts = MemoryArtifacts()
        self.route = build_route('codex_cli_v1', 'gpt-6-astra')
        self.native = self.build_native()
        self.built = self.build_inventory()
        self.value = dict(id='chain', role='native_math', reviewer_id='fixture-reviewer',
                          dossier_id=None, route_id='route', author_id='author', context={})
        self.logical = dict(protocol='fixture-native', role='native_math', reviewer_id='fixture-reviewer',
                            dossier_id=None, native=self.native, evidence=[], digest='unused')
        with patch.object(v7, 'build_inventory', return_value=self.built):
            self.packet, self.chain = v7.prepare_first_assignment({}, self.artifacts, self.value, self.route,
                                                                  self.logical, [])
        self.plan = capacity.read_chain_capacity_plan(self.artifacts, self.chain)
        self.inventory = json.loads(self.artifacts.read(self.chain['inventory']))
        self.state = json.loads(self.artifacts.read(self.chain['state']))
        self.assignment = dict(reading_chain=self.chain, capacity_plan=self.chain['capacity_plan'])

    def build_native(self):
        return build_native(self.targets)

    def build_inventory(self):
        return build_inventory(self.artifacts, 1000)

    def render(self, packet):
        binding = final_pages.read_binding(packet)
        return protocol.canonical_prompt('native_math', reading_stage=reading.find_stage(packet),
            reading_protocol=reading.find_protocol(packet), capacity=binding['capacity'],
            **paragraphs.select_prompt_parameters('native_math', packet))

    def assert_members(self, packet):
        binding = final_pages.read_binding(packet)
        self.assertIn('notes_cost', binding)
        self.assertIn('lists', binding)
        return binding

    def assert_lists(self, packet):
        binding = final_pages.read_binding(packet)
        self.assertIn('lists', binding)
        return binding['lists']

    def measure(self, text):
        return capacity.measure_capacity_value(self.route, text) - capacity.measure_capacity_value(self.route, '')

    def build(self, state=None, plan=None, kind='reading', requests=(), number=1):
        plan = plan or self.plan
        return v7.build_turn_packet(self.artifacts, self.route, self.logical, 'chain', number, self.inventory,
            state or self.state, plan, capacity.derive_plan_task_limits(plan, kind), kind, requests=requests)

    def answer(self, packet, **changes):
        binding = packet.get('reading_turn', packet.get('reading_final'))
        value = dict(schema_version=2, stage='reading', notes='A retained note.', excerpts=[], gaps=[],
                     passage_requests=[], **{key: binding[key] for key in
                                             ('subject_digest', 'claim_digest', 'reviewer')})
        value.update(changes)
        return value

    def with_unread_rows(self, count):
        value = dict(rows=[dict(id='u1:1:%d' % index, unit='u1', label='x' * 120, part=1, start=index,
                               end=index + 1, characters=1, cause='not_delivered') for index in range(count)],
                     attempts=[dict(id='0', row=None, outcome='provider_refused')])
        return dict(self.state, unread=dict(artifact=self.artifacts.put_json(value), digest=digest(value)))

    def advance(self, packet, output):
        projection = v7.project_own_turn(self.artifacts, self.route, packet, output, 'chain', 'attempt',
                                         state=self.state, inventory=self.inventory)
        return v7.build_chain_state(self.inventory, [projection], route=self.route, plan=self.plan,
                                    initial_state=self.state)['state']


class NotesCostTests(MemberCase):
    def test_the_first_turn_states_the_cost_of_one_escaped_character_and_no_use(self):
        self.assert_members(self.packet)
        self.assertEqual(self.packet['reading_turn']['notes_cost'], dict(
            used=0, quote=self.measure('"'), backslash=self.measure('\\'), line_break=self.measure('\n')))
        self.assertEqual(self.packet['reading_turn']['notes_cost'], dict(used=0, quote=4, backslash=4, line_break=3))

    def test_a_later_turn_states_what_its_notes_take_of_the_bound(self):
        notes = 'First line of the notes.\nA "quoted" word and a back\\slash.\n'
        state = self.advance(self.packet, self.answer(self.packet, notes=notes))
        following = self.build(state=state, number=2)
        self.assertEqual(following['reading_turn']['notes'], notes)
        cost = self.assert_members(following)['notes_cost']
        self.assertEqual(cost['used'], self.measure(notes))
        self.assertEqual(cost['used'], len(notes) + notes.count('\n') * (cost['line_break'] - 1)
                         + notes.count('"') * (cost['quote'] - 1) + notes.count('\\') * (cost['backslash'] - 1))
        final = self.build(state=state, kind='final', number=2)
        self.assertEqual(self.assert_members(final)['notes_cost'], cost)

    def test_the_stated_use_is_the_number_that_the_check_compares_with_the_bound(self):
        bound = self.packet['reading_turn']['allowances']['notes']
        notes = ('line of notes\n' * (bound // 14))[:bound - 100]
        with self.assertRaises(ResearchError) as caught:
            v7.validate_turn_output(self.packet, self.answer(self.packet, notes=notes), artifacts=self.artifacts,
                                   assignment=self.assignment)
        following = self.build(state=dict(self.state, latest_notes=notes))
        self.assertEqual(caught.exception.details['notes_cost'], self.assert_members(following)['notes_cost']['used'])


class ListRowsTests(MemberCase):
    def test_a_reading_turn_names_only_the_lists_that_hold_rows(self):
        self.assertEqual(self.assert_lists(self.packet), [dict(list='allowances', rows=3, after=None)])

    def test_a_list_leaves_the_member_with_its_last_page(self):
        current = self.build(requests=[dict(id='page', kind='list_page', list='allowances', after=None)])
        self.assertIsNone(current['reading_turn']['plan']['now'][0]['page']['next'])
        self.assertEqual(self.assert_lists(current), [])

    def test_the_answer_check_accepts_exactly_the_requests_that_the_member_offers(self):
        offered = {row['list']: row['after'] for row in self.assert_lists(self.packet)}
        for name in v7.LIST_PAGE_SUPPLIERS:
            request = dict(id='page', kind='list_page', list=name, after=offered.get(name))
            output = self.answer(self.packet, passage_requests=[request])
            with self.subTest(list=name):
                if name in offered:
                    v7.validate_turn_output(self.packet, output, artifacts=self.artifacts, assignment=self.assignment)
                else:
                    with self.assertRaises(ResearchError):
                        v7.validate_turn_output(self.packet, output, artifacts=self.artifacts,
                                               assignment=self.assignment)

    def test_a_final_turn_holds_the_member(self):
        final = self.build(kind='final')
        self.assertEqual(self.assert_lists(final), [dict(list='allowances', rows=3, after=None)])

    def test_a_final_over_unread_ranges_names_the_unread_lists_with_their_cursors(self):
        state = self.with_unread_rows(21)
        final = self.build(state=state, kind='final', number=2)
        self.assertEqual(self.assert_lists(final), [
            dict(list='unread', rows=21, after=None), dict(list='unread_attempts', rows=1, after=None),
            dict(list='allowances', rows=3, after=None)])
        plan = copy.deepcopy(self.plan)
        plan['task']['final']['notice_characters'] = 0
        limits = dict(capacity.derive_plan_task_limits(plan, 'final'), material_allowance=8200)
        request = dict(id='page', kind='list_page', list='unread', after=None)
        paged = v7.build_turn_packet(self.artifacts, self.route, self.logical, 'chain', 2, self.inventory,
                                     state, plan, limits, 'final', requests=[request])
        page = paged['reading_final']['plan']['now'][0]['page']
        self.assertIsNotNone(page['next'])
        self.assertEqual(self.assert_lists(paged)[0], dict(list='unread', rows=21, after=page['next']))
        assignment = dict(self.assignment, capacity_plan=dict(self.assignment['capacity_plan'],
            artifact=self.artifacts.put_json(plan), digest=digest(plan)), reading_chain=dict(
                self.chain, state=self.artifacts.put_json(state)))
        next_request = dict(request, id='next-page', after=page['next'])
        v7.validate_turn_output(paged, self.answer(paged, passage_requests=[next_request]),
                                artifacts=self.artifacts, assignment=assignment)
        with self.assertRaises(ResearchError) as stale:
            v7.validate_turn_output(paged, self.answer(paged, passage_requests=[request]),
                                    artifacts=self.artifacts, assignment=assignment)
        self.assertEqual(stale.exception.details['expected_after'], page['next'])

    def test_an_unread_final_reads_its_artifact_once_for_the_summary_and_list_counts(self):
        state = self.with_unread_rows(2)
        reference = state['unread']['artifact']
        with patch.object(self.artifacts, 'read', wraps=self.artifacts.read) as read:
            packet = self.build(state=state, kind='final')
        self.assertEqual(packet['reading_final']['unread']['count'], 2)
        self.assertEqual(self.assert_lists(packet)[:2], [
            dict(list='unread', rows=2, after=None), dict(list='unread_attempts', rows=1, after=None)])
        self.assertEqual(sum(call.args[0] == reference for call in read.call_args_list), 1)


class PromptAndFrameTests(MemberCase):
    def test_a_turn_with_the_members_carries_their_two_paragraphs(self):
        for packet in (self.packet, self.build(kind='final')):
            prompt = self.render(packet)
            self.assertEqual(prompt['parameters'].get(paragraphs.PARAMETER), MEMBER_VERSIONS)
            self.assertTrue(prompt['text'].endswith(paragraphs.NOTES_COST_PROMPT + paragraphs.LIST_ROWS_PROMPT))
            self.assertTrue(v7.matches_turn_prompt(dict(role='native_math', prompt=prompt), packet))
            bare = protocol.canonical_prompt('native_math', reading_stage=reading.find_stage(packet),
                reading_protocol=v7.PROTOCOL, capacity=packet.get('reading_turn', packet.get('reading_final'))['capacity'])
            self.assertFalse(v7.matches_turn_prompt(dict(role='native_math', prompt=bare), packet))

    def test_the_plan_reserves_the_members_and_their_paragraphs_and_the_request_fits(self):
        widest_notes = dict(used=999999999, quote=9, backslash=9, line_break=9)
        widest_lists = [dict(list=name, rows=999999999, after='x' * reading.RESERVED_IDENTIFIER_CHARACTERS)
                        for name in ('unread', 'unread_attempts', 'allowances', 'affected_notes')]
        required = dict(notes_cost=len(paragraphs.NOTES_COST_PROMPT)
                        + capacity.measure_capacity_value(self.route, widest_notes),
                        lists=len(paragraphs.LIST_ROWS_PROMPT)
                        + capacity.measure_capacity_value(self.route, widest_lists))
        for kind in ('reading', 'final'):
            fields = self.plan['frame'][kind]['fields']
            for field, characters in required.items():
                self.assertIn(field, fields)
                self.assertGreaterEqual(fields[field], characters, (kind, field))
            self.assertEqual(self.plan['frame'][kind]['unit_fields'], [])
        request = request_body(self.route, self.render(self.packet)['text'], self.packet)
        report = capacity.describe_plan_preflight({}, self.route, request, self.plan, 'reading')
        self.assertTrue(report['plan_comparison']['fits'], report['plan_comparison'])

    def test_shared_paragraphs_show_an_accepted_list_request_in_a_reading_answer(self):
        prompt = self.render(self.packet)
        self.assertIn(paragraphs.LIST_ROWS_PROMPT, prompt['text'])
        sample = re.search(r'\{\s*"id"\s*:[^{}]+\}', paragraphs.LIST_ROWS_PROMPT)
        self.assertIsNotNone(sample, 'The shared list paragraph supplies a complete JSON request.')
        request = json.loads(sample.group())
        self.assertEqual(set(request), {'id', 'kind', 'list', 'after'})
        self.assertEqual(request['kind'], 'list_page')
        self.assertIn('stage="reading"', paragraphs.LIST_ROWS_PROMPT)
        self.assertIn('stage="reading"', paragraphs.NOTES_COST_PROMPT)
        v7.validate_turn_output(self.packet, self.answer(self.packet, passage_requests=[request]),
                                artifacts=self.artifacts, assignment=self.assignment)

    def test_a_chain_whose_plan_reserves_no_member_keeps_its_packet_and_its_prompt(self):
        stored = copy.deepcopy(self.plan)
        for kind in ('reading', 'final'):
            for field in ('notes_cost', 'lists'):
                stored['frame'][kind]['fields'].pop(field, None)
        for kind in ('reading', 'final'):
            packet = self.build(plan=stored, kind=kind)
            binding = packet.get('reading_turn', packet.get('reading_final'))
            self.assertNotIn('notes_cost', binding)
            self.assertNotIn('lists', binding)
            self.assertEqual(paragraphs.select_prompt_parameters('native_math', packet), {})
            prompt = protocol.canonical_prompt('native_math', reading_stage=kind, reading_protocol=v7.PROTOCOL,
                                               capacity=binding['capacity'])
            self.assertTrue(v7.matches_turn_prompt(dict(role='native_math', prompt=prompt), packet))


class PagedMembersTests(MemberCase):
    targets = 9

    def setUp(self):
        switch = patch.object(v7, 'V7_FINAL_FORMS', ('request', 'pages'))
        switch.start()
        self.addCleanup(switch.stop)
        with patch.object(capacity, 'read_profile_priors', return_value=[]):
            super().setUp()
        self.assertEqual(self.plan['allocation']['final_form'], 'pages')

    def build_native(self):
        return dict(super().build_native(), kind='result')

    def build_inventory(self):
        gaps = self.artifacts.put_json([])
        return v7.plan_inventory([dict(kind='record', record_kind='minimum_gaps',
                                       record_id='minimum_gaps', artifact=gaps)], {gaps['sha256']: b'[]'})

    def build_page(self, kind, *, notes='Previous notes.', room=None, requests=(), plan=None):
        plan = plan or self.plan
        state = dict(self.state, latest_notes=notes, pending_requests=requests,
                     notes=[dict(turn=1, classification='current', text=notes)])
        limits = capacity.derive_plan_task_limits(plan, 'final')
        room = room or min(plan['allocation']['page_room'], limits['material_allowance'])
        inputs, catalog, full = final_pages.build_final_inputs(self.artifacts, self.route, self.logical, 'chain', 2,
            self.inventory, state, plan, limits, room)
        turns = pages.plan_final_turns(**inputs)
        page_plan = pages.build_page_plan('chain', digest(plan), digest(state),
            final_pages.read_final_currency(self.native, self.inventory), turns)
        turn = next(row for row in turns if row['kind'] == kind)
        combinations = {row['block']['handle']: dict(block=row['block']['handle'], summary='Combined notes.',
            refs=[row['entry_sizes'][0]['handle']]) for row in turns if row['kind'] == 'combine'}
        if kind != 'combine' and combinations:
            state['combinations'] = combinations
            _, catalog, full = final_pages.build_final_inputs(self.artifacts, self.route, self.logical, 'chain', 2,
                self.inventory, state, plan, limits, room)
        packet = final_pages.build_final_packet({}, self.artifacts, self.route, self.logical, 'chain', 2,
            self.inventory, state, plan, limits, page_plan, turn, catalog=catalog, full=full)
        assignment = dict(self.assignment, capacity_plan=dict(self.assignment['capacity_plan'],
            artifact=self.artifacts.put_json(plan), digest=digest(plan)), reading_chain=dict(
                self.chain, state=self.artifacts.put_json(state)))
        return packet, assignment

    def test_a_page_delivering_the_last_page_drops_that_list(self):
        request = dict(id='ask', kind='list_page', list='allowances', after=None)
        packet, assignment = self.build_page('page', requests=[request])
        binding = packet['reading_final']
        self.assertIsNone(binding['plan']['now'][0]['page']['next'])
        self.assertEqual(self.assert_lists(packet), [])
        with self.assertRaises(ResearchError) as refused:
            v7.validate_turn_output(packet, self.answer(packet, passage_requests=[request]),
                                    artifacts=self.artifacts, assignment=assignment)
        self.assertTrue(refused.exception.details['ended'])

    def test_a_page_carrying_only_segments_states_zero_notes_used(self):
        packet, _ = self.build_page('page', notes='n' * 18000, room=15000)
        self.assertIsNone(packet['reading_final']['notes'])
        self.assertEqual(self.assert_members(packet)['notes_cost']['used'], 0)

    def test_closing_keeps_both_members_and_the_same_notes_refusal_bound(self):
        packet, assignment = self.build_page('closing')
        binding = self.assert_members(packet)
        self.assertEqual(binding['notes_cost']['used'], 0)
        self.assertEqual(binding['lists'], [dict(list='allowances', rows=9, after=None)])
        self.assertEqual(self.render(packet)['parameters'].get(paragraphs.PARAMETER), MEMBER_VERSIONS)
        bound = self.plan['allocation']['notes']
        self.assertEqual(binding['allowances']['notes'], bound)
        request = dict(id='ask', kind='list_page', list='allowances', after=None)
        # A final can return a reading response with a request.
        accepted = self.answer(packet, notes='n' * (bound - 4) + '\nx', passage_requests=[request])
        v7.validate_turn_output(packet, accepted, artifacts=self.artifacts, assignment=assignment)
        with self.assertRaises(ResearchError) as refused:
            v7.validate_turn_output(packet, dict(accepted, notes=accepted['notes'] + 'x'),
                                    artifacts=self.artifacts, assignment=assignment)
        self.assertEqual(refused.exception.details['notes_cost'], bound + 1)

    def test_a_closing_over_unread_rows_with_a_delivery_and_a_full_notice_fits_its_bound(self):
        from test_openness_reading_v7_refusal import NoticeRoomTests
        from research_harness import review_refusal_notice as notices
        # Two thousand rows fill the inline unread prefix and the requested page of the same list.
        self.state = self.with_unread_rows(2000)
        packet, _ = self.build_page('closing', requests=[dict(id='ask', kind='list_page', list='unread', after=None)])
        binding = packet['reading_final']
        used = (sum(capacity.measure_capacity_value(self.route, value) for value in
                    (binding['closing'], binding['material'], binding['plan']['now'], packet['evidence']))
                + v7.compute_unread_rows_charge(self.route, binding['unread']))
        self.assertLessEqual(used, binding['capacity']['material_allowance'])
        self.assertTrue(binding['unread']['rows'])
        self.assertTrue(binding['plan']['now'])
        notice = NoticeRoomTests().build_bounded_notice(MemoryArtifacts(), route=self.route)
        self.assertEqual(capacity.measure_capacity_value(self.route, notice), 45000)
        packet = notices.attach_refusal_notice(packet, notice)
        prompt = protocol.canonical_prompt('native_math', reading_stage='closing', reading_protocol=v7.PROTOCOL,
            capacity=binding['capacity'], finding_version=2 if 'finding_index' in packet else 1,
            **paragraphs.select_prompt_parameters('native_math', packet))
        self.assertTrue(v7.matches_turn_prompt(dict(role='native_math', prompt=prompt), packet))
        request = request_body(self.route, prompt['text'], packet)
        compared = capacity.describe_plan_preflight({}, self.route, request, self.plan, 'final')['plan_comparison']
        self.assertTrue(compared['fits'], compared)

    def test_an_earlier_paged_plan_keeps_its_page_combine_and_closing_forms(self):
        stored = copy.deepcopy(self.plan)
        for field in ('notes_cost', 'lists'):
            stored['frame']['final']['fields'].pop(field, None)
        for kind in ('combine', 'page', 'closing'):
            with self.subTest(kind=kind):
                packet, _ = self.build_page(kind, notes='n' * 18000, room=15000, plan=stored)
                binding = final_pages.read_binding(packet)
                self.assertNotIn('notes_cost', binding)
                self.assertNotIn('lists', binding)
                prompt = self.render(packet)
                self.assertNotIn(paragraphs.PARAMETER, prompt['parameters'])
                self.assertTrue(v7.matches_turn_prompt(dict(role='native_math', prompt=prompt), packet))


class MathMembersTests(MemberCase):
    def build_native(self):
        native = dict(super().build_native(), kind='result', contract={}, evidence=[])
        native['scientific_context']['openness_minimum']['schema_version'] = 1
        return native

    def build_inventory(self):
        return v7.build_inventory({}, self.artifacts, self.native)

    def test_math_v2_reading_uses_the_same_members_and_paragraphs(self):
        self.assertEqual(reading.find_protocol(self.packet), reading.MATH_PROTOCOL_V2)
        self.assert_members(self.packet)
        self.assertEqual(self.render(self.packet)['parameters'].get(paragraphs.PARAMETER), MEMBER_VERSIONS)


if __name__ == '__main__':
    unittest.main()
