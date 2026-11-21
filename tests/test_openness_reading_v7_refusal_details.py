"""Every refusal of a v7 reading response names the member that failed and the values that the rule compared,
and the refusal notice shows those values to the reviewer. The shape is the paid verification of record 12
(20261120072937-v7-reading-refusals-without-bounds-and-an-empty-unread-list): turn 11 returned notes longer than
allowances.notes, turn 14 notes whose cost in the next request passed it, and turn 3 a page request for the empty
list unread; no notice gave the reviewer a value."""

import ast
import inspect
import json
import unittest
from unittest.mock import patch

from research_harness import openness_reading as reading, openness_reading_v7 as v7
from research_harness import review_handles, review_refusal, review_refusal_notice as notices
from research_harness import review_route_capacity as capacity
from research_harness.errors import ResearchError
from test_openness_reading_v7_turns import MemoryArtifacts, build_inventory, build_native
from v7_capacity_fixtures import build_route


class ReadingRefusalCase(unittest.TestCase):
    def setUp(self):
        self.artifacts = MemoryArtifacts()
        self.route = build_route('codex_cli_v1', 'gpt-6-astra')
        self.native = build_native(3)
        self.built = build_inventory(self.artifacts, 100000)
        value = dict(id='chain', role='native_math', reviewer_id='fixture-reviewer',
                     dossier_id=None, route_id='route', author_id='author', context={})
        self.logical = dict(protocol='fixture-native', role='native_math', reviewer_id='fixture-reviewer',
                            dossier_id=None, native=self.native, evidence=[], digest='unused')
        with patch.object(v7, 'build_inventory', return_value=self.built):
            self.packet, self.chain = v7.prepare_first_assignment({}, self.artifacts, value, self.route, self.logical,
                                                                  [])
        self.assignment = dict(reading_chain=self.chain, capacity_plan=self.chain['capacity_plan'])
        self.binding = self.packet['reading_turn']
        self.bound = self.binding['allowances']['notes']
        self.row = self.binding['plan']['now'][0]
        self.content = self.packet['evidence'][0]['content']

    def answer(self, **changes):
        value = dict(schema_version=2, stage='reading', notes='A retained note.', excerpts=[], gaps=[],
                     passage_requests=[], **{key: self.binding[key] for key in
                                             ('subject_digest', 'claim_digest', 'reviewer')})
        value.update(changes)
        return value

    def refuse(self, output, *, packet=None, assignment=None):
        with self.assertRaises(ResearchError) as caught:
            v7.validate_turn_output(packet or self.packet, output, artifacts=self.artifacts,
                                    assignment=assignment or self.assignment)
        self.assertEqual(caught.exception.code, reading.INVALID_RESPONSE)
        return dict(caught.exception.details or {})

    def build_turn(self, kind, state=None, requests=()):
        """A packet of the chain over its stored state, or over state stored in its place, with its assignment."""
        plan = capacity.read_chain_capacity_plan(self.artifacts, self.chain)
        inventory = json.loads(self.artifacts.read(self.chain['inventory']))
        chain = self.chain
        if state is None:
            state = json.loads(self.artifacts.read(self.chain['state']))
        else:
            chain = dict(self.chain, state=self.artifacts.put_json(state))
        packet = v7.build_turn_packet(self.artifacts, self.route, self.logical, 'chain', 1, inventory, state, plan,
                                      capacity.derive_plan_task_limits(plan, kind), kind, requests=list(requests))
        return packet, dict(reading_chain=chain, capacity_plan=chain['capacity_plan'])


class NotesRefusalTests(ReadingRefusalCase):
    def test_notes_longer_than_the_bound_give_their_length_their_cost_and_the_bound(self):
        details = self.refuse(self.answer(notes='n' * (self.bound + 996)))
        self.assertEqual(details, dict(path='/notes', characters=self.bound + 996, notes_cost=self.bound + 996,
                                       bound=self.bound))

    def test_notes_within_the_length_whose_cost_passes_the_bound_give_the_same_three_numbers(self):
        notes = ('line of notes\n' * (self.bound // 14))[:self.bound - 100]
        cost = capacity.measure_capacity_value(self.route, notes) - capacity.measure_capacity_value(self.route, '')
        self.assertLess(len(notes), self.bound)
        self.assertGreater(cost, self.bound)
        details = self.refuse(self.answer(notes=notes))
        self.assertEqual(details, dict(path='/notes', characters=len(notes), notes_cost=cost, bound=self.bound))

    def test_notes_at_the_bound_are_accepted_and_one_character_more_is_refused(self):
        v7.validate_turn_output(self.packet, self.answer(notes='n' * self.bound), artifacts=self.artifacts,
                                assignment=self.assignment)
        details = self.refuse(self.answer(notes='n' * (self.bound + 1)))
        self.assertEqual((details.get('path'), details.get('characters')), ('/notes', self.bound + 1))

    def test_notes_checked_without_the_store_give_their_length_and_the_bound(self):
        with self.assertRaises(ResearchError) as caught:
            v7.validate_turn_output(self.packet, self.answer(notes='n' * (self.bound + 1)))
        self.assertEqual(caught.exception.details, dict(path='/notes', characters=self.bound + 1, bound=self.bound))

    def test_notes_of_another_type_and_blank_notes_name_the_member(self):
        details = self.refuse(self.answer(notes=7))
        self.assertEqual(details, dict(path='/notes', expected_type='string', received_type='number'))
        self.assertEqual(self.refuse(self.answer(notes='   ')).get('path'), '/notes')


class ListRefusalTests(ReadingRefusalCase):
    def request(self, name, after=None):
        return self.answer(passage_requests=[dict(id='page', kind='list_page', list=name, after=after)])

    def test_a_request_for_a_list_without_rows_names_the_list_and_its_rows(self):
        for name in ('unread', 'unread_attempts', 'affected_notes'):
            with self.subTest(list=name):
                self.assertEqual(self.refuse(self.request(name)), dict(
                    path='/passage_requests/0/list', list=name, after=None, rows=0))

    def test_a_request_with_another_cursor_names_the_cursor_that_the_list_expects(self):
        self.assertEqual(self.refuse(self.request('allowances', 't2')), dict(
            path='/passage_requests/0/after', list='allowances', after='t2', expected_after=None))

    def test_a_request_for_a_list_that_has_ended_says_so(self):
        current, _ = self.build_turn('reading', requests=[dict(id='page', kind='list_page', list='allowances',
                                                               after=None)])
        self.assertIsNone(current['reading_turn']['plan']['now'][0]['page']['next'])
        details = self.refuse(self.request('allowances'), packet=current)
        self.assertEqual(details, dict(path='/passage_requests/0/after', list='allowances', after=None, ended=True))

    def test_an_unknown_and_a_repeated_list_name_the_list(self):
        self.assertEqual(self.refuse(self.request('entries')), dict(path='/passage_requests/0/list', list='entries'))
        twice = self.answer(passage_requests=[dict(id='a', kind='list_page', list='allowances', after=None),
                                              dict(id='b', kind='list_page', list='allowances', after=None)])
        self.assertEqual(self.refuse(twice), dict(path='/passage_requests/1/list', list='allowances'))

    def test_a_list_request_without_a_member_or_with_a_list_of_another_type_names_the_member(self):
        self.assertEqual(self.refuse(self.answer(passage_requests=[dict(id='page', kind='list_page',
                                                                        list='allowances')])),
                         dict(path='/passage_requests/0', expected_type='object', received_type='object',
                              missing_fields=['after'], unexpected_fields=[]))
        self.assertEqual(self.refuse(self.request(3)), dict(path='/passage_requests/0/list', expected_type='string',
                                                            received_type='number'))
        self.assertEqual(self.refuse(self.request('allowances', 3)), dict(
            path='/passage_requests/0/after', expected_type='string', received_type='number'))


class EveryRuleTests(ReadingRefusalCase):
    def test_every_refusal_names_the_member_that_failed(self):
        excerpt = dict(unit=self.row['handle'], text=self.content[:40], purpose='A range.', targets=['t1'])
        passage = dict(id='p', unit=self.row['handle'], anchor=None, characters=100)
        cases = [
            ('', ['not', 'an', 'object']),
            ('', {key: value for key, value in self.answer().items() if key != 'gaps'}),
            ('', self.answer(extra=1)),
            ('/schema_version', self.answer(schema_version=1)),
            ('/schema_version', self.answer(schema_version=True)),
            ('/stage', self.answer(stage='final')),
            ('/subject_digest', self.answer(subject_digest='0' * 64)),
            ('/claim_digest', self.answer(claim_digest='0' * 64)),
            ('/reviewer', self.answer(reviewer='another')),
            ('/notes', self.answer(notes=None)),
            ('/notes', self.answer(notes='')),
            ('/excerpts', self.answer(excerpts={})),
            ('/gaps', self.answer(gaps='none')),
            ('/excerpts/0', self.answer(excerpts=['text'])),
            ('/excerpts/0', self.answer(excerpts=[dict(excerpt, note='extra')])),
            ('/excerpts/0/unit', self.answer(excerpts=[dict(excerpt, unit=3)])),
            ('/excerpts/0/unit', self.answer(excerpts=[dict(excerpt, unit='u999')])),
            ('/excerpts/0/text', self.answer(excerpts=[dict(excerpt, text='')])),
            ('/excerpts/0/text', self.answer(excerpts=[dict(excerpt, text='text that the unit does not hold')])),
            ('/excerpts/0/purpose', self.answer(excerpts=[dict(excerpt, purpose=3)])),
            ('/excerpts/0/purpose', self.answer(excerpts=[dict(excerpt, purpose='p' * 201)])),
            ('/excerpts/0/targets', self.answer(excerpts=[dict(excerpt, targets=[])])),
            ('/excerpts/0/targets/0', self.answer(excerpts=[dict(excerpt, targets=['t9'])])),
            ('/excerpts/0/targets/1', self.answer(excerpts=[dict(excerpt, targets=['t1', 't1'])])),
            ('/excerpts/0', self.answer(excerpts=[dict(keep='e1', targets=['t1'], note='extra')])),
            ('/excerpts/0/keep', self.answer(excerpts=[dict(keep=3, targets=['t1'])])),
            ('/excerpts/1/keep', self.answer(excerpts=[excerpt, dict(keep='e1', targets=['t1'])])),
            ('/gaps/0', self.answer(gaps=[dict(formulation='t1')])),
            ('/gaps/0/formulation', self.answer(gaps=[dict(formulation='t9', text='A gap.')])),
            ('/gaps/0/text', self.answer(gaps=[dict(formulation='t1', text='g' * 601)])),
            ('/passage_requests', self.answer(passage_requests={})),
            ('/passage_requests', self.answer(passage_requests=[dict(passage, id='p%d' % n) for n in range(11)])),
            ('/passage_requests/0', self.answer(passage_requests=['p'])),
            ('/passage_requests/0/id', self.answer(passage_requests=[dict(passage, id='')])),
            ('/passage_requests/1/id', self.answer(passage_requests=[passage, dict(passage)])),
            ('/passage_requests/0', self.answer(passage_requests=[dict(passage, kind='passage')])),
            ('/passage_requests/0/unit', self.answer(passage_requests=[dict(passage, unit='u999')])),
            ('/passage_requests/0/anchor', self.answer(passage_requests=[dict(passage, anchor='an absent anchor')])),
            ('/passage_requests/0/anchor', self.answer(passage_requests=[dict(passage, anchor='')])),
            ('/passage_requests/0/characters', self.answer(passage_requests=[dict(passage, characters=0)])),
            ('/passage_requests/0/characters', self.answer(passage_requests=[dict(passage, characters='100')])),
            ('/passage_requests/0/after', self.answer(passage_requests=[
                dict(id='page', kind='list_page', list='allowances', after=3)])),
        ]
        for path, output in cases:
            with self.subTest(path=path, output=str(output)[:80]):
                self.assertEqual(self.refuse(output).get('path'), path)

    def test_a_count_of_characters_that_is_no_integer_names_the_integer_type(self):
        passage = dict(id='p', unit=self.row['handle'], anchor=None, characters=100.5)
        self.assertEqual(self.refuse(self.answer(passage_requests=[passage])), dict(
            path='/passage_requests/0/characters', expected_type='integer', received_type='number'))

    def test_a_passage_request_over_the_allowance_gives_what_it_takes_and_what_is_left(self):
        requests = [dict(id='p%d' % n, unit=self.row['handle'], anchor=None, characters=100000) for n in range(2)]
        details = self.refuse(self.answer(passage_requests=requests))
        requested = min(len(self.content), 100000)
        self.assertEqual(details, dict(path='/passage_requests/1/characters', requested=requested,
                                       remaining=100000 - requested))

    def test_a_request_with_the_id_of_a_request_still_delivered_names_the_id(self):
        state = json.loads(self.artifacts.read(self.chain['state']))
        state['pending_requests'] = [dict(id='p', unit=self.row['handle'], anchor=None, characters=100)]
        packet, assignment = self.build_turn('reading', state=state)
        output = self.answer(passage_requests=[dict(id='p', unit=self.row['handle'], anchor=None, characters=10)])
        output.update({key: packet['reading_turn'][key] for key in ('subject_digest', 'claim_digest', 'reviewer')})
        self.assertEqual(self.refuse(output, packet=packet, assignment=assignment),
                         dict(path='/passage_requests/0/id', request_id='p'))

    def test_a_carried_entry_kept_twice_names_the_second_one(self):
        rows = [dict(keep='e1', targets=['t1']), dict(keep='e1', targets=['t2'])]
        with patch.object(v7, '_list_delivered_carried_entries', return_value={'e1'}):
            details = self.refuse(self.answer(excerpts=rows))
        self.assertEqual(details, dict(path='/excerpts/1/keep', keep='e1'))

    def test_a_reading_response_of_a_final_turn_without_a_request_names_the_requests(self):
        final, assignment = self.build_turn('final')
        output = self.answer()
        output.update({key: final['reading_final'][key] for key in ('subject_digest', 'claim_digest', 'reviewer')})
        self.assertEqual(self.refuse(output, packet=final, assignment=assignment),
                         dict(path='/passage_requests', items=0, minimum_items=1))


class NoticeValueTests(ReadingRefusalCase):
    certificate = dict(assignment_id='chain', attempt_id='chain-run-1', output_digest='0' * 64)

    def build_entry(self, error):
        diagnosis = dict(code=error.code, check=error.contract_check,
                         details=dict(error.details, message=error.message))
        return notices._build_refused_entry(self.certificate, diagnosis, notices._measure_serialized_value)

    def build_reading_entry(self, output):
        with self.assertRaises(ResearchError) as caught, review_refusal.contract_check('native.output'):
            v7.validate_turn_output(self.packet, output, artifacts=self.artifacts, assignment=self.assignment)
        return self.build_entry(caught.exception)

    def test_the_notice_shows_the_numbers_of_refused_notes(self):
        entry = self.build_reading_entry(self.answer(notes='n' * (self.bound + 996)))
        self.assertEqual((entry['path'], entry['details']), ('/notes', dict(
            characters=self.bound + 996, notes_cost=self.bound + 996, bound=self.bound)))

    def test_the_notice_shows_the_list_of_a_refused_page_request(self):
        entry = self.build_reading_entry(self.answer(passage_requests=[
            dict(id='page', kind='list_page', list='unread', after=None)]))
        self.assertEqual((entry['path'], entry['details']), ('/passage_requests/0/list', dict(
            list='unread', after=None, rows=0)))

    def test_the_notice_shows_the_values_of_a_refused_part_of_an_argument(self):
        packet = dict(reading_final=dict(protocol=v7.PROTOCOL), capacity=dict(answer_bound=67000), handle_table={},
                      material_state=dict(arguments={'m1': dict(title='An argument')}, pending=dict(ref='m1', part=2)))
        output = dict(stage='material', title='An argument', part=5, text='Text.', more=False)
        with self.assertRaises(ResearchError) as caught:
            review_handles.validate_material(packet, output)
        entry = self.build_entry(caught.exception)
        self.assertEqual((entry['check'], entry['details']), ('review.shape', dict(
            expected_part=2, maximum_parts=review_handles.MATERIAL_PART_LIMIT, answer_bound=67000)))

    def test_the_notice_shows_the_place_and_the_handle_of_a_refused_citation(self):
        packet = dict(reading_final=dict(protocol=v7.PROTOCOL), handle_table={})
        grounds = [dict(kind='reviewer_gap', text='A gap.', refs=[dict(ref='n1')]),
                   dict(kind='reviewer_gap', text='A gap.', refs=[dict(ref='g1'), dict(ref='m1'), dict(ref='u1')])]
        output = dict(findings=dict(openness=dict(grounds=grounds)))
        with self.assertRaises(ResearchError) as caught:
            review_handles.expand_handles(packet, output, raw=True)
        entry = self.build_entry(caught.exception)
        self.assertEqual((entry['check'], entry['path'], entry['details']),
                         ('review.shape', '/findings/openness/grounds/1/refs/2', dict(ref='u1')))
        value, found = notices._read_refused_part(output, entry['path'])
        self.assertEqual((value, found), (dict(ref='u1'), True))

    def test_every_detail_of_the_reading_check_is_a_registered_notice_field(self):
        registered = set(review_refusal.CONTRACT_CHECKS['native.output']['notice_fields'])
        used = set()
        for node in ast.walk(ast.parse(inspect.getsource(v7))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == '_require_reading'):
                used.update(keyword.arg for keyword in node.keywords if keyword.arg not in (None, 'path'))
        self.assertTrue(used)
        self.assertEqual(used - registered, set())


if __name__ == '__main__':
    unittest.main()
