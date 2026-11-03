import copy
import unittest
from research_harness import review_parts as parts
from research_harness.errors import ResearchError
from review_parts_fixtures import make_packet, make_output, plan, observe


class OutputTests(unittest.TestCase):
    def test_missing_extra_unknown_and_over_allowance_cards_refused(self):
        packet=plan(make_packet(1,200))
        for mutate in (lambda o:o['cards'].clear(),lambda o:o['cards'].append(copy.deepcopy(o['cards'][0])),lambda o:o['cards'][0].update(unit='u99'),lambda o:o['cards'][0].update(summary='x'*90000)):
            output=make_output(packet); mutate(output)
            with self.subTest(output=str(output)[:100]),self.assertRaises(ResearchError) as raised:
                parts.validate_output(packet,output,packet['_test_inventory'])
            self.assertEqual(raised.exception.code,'invalid_value_review')

    def test_statements_verified_only_in_delivered_text(self):
        packet=plan(make_packet(1,200)); output=make_output(packet)
        output['cards'][0]['statements']=[dict(text='x'*30,why='A quote.'),dict(text='This was never delivered.',why='A guess.')]
        delta=parts.validate_output(packet,output,packet['_test_inventory'])
        self.assertEqual(len(delta['cards'][0]['statements']),1)
        self.assertEqual(delta['cards'][0]['unverified_statements'],1)
        self.assertEqual(delta['cards'][0]['turn'],1)

    def test_bearing_and_statement_caps(self):
        packet=plan(make_packet(1,200)); output=make_output(packet)
        output['cards'][0]['bearing']=[dict(target='invented',relation='supports',reason='A reason.')]
        with self.assertRaises(ResearchError):parts.validate_output(packet,output,packet['_test_inventory'])
        output=make_output(packet); output['cards'][0]['statements']=[dict(text='x'*6001,why='Too long.')]
        with self.assertRaises(ResearchError):parts.validate_output(packet,output,packet['_test_inventory'])

    def test_combination_ids_spans_and_exact_findings(self):
        original=make_packet(2,200); read=plan(original); observations=[observe(read)]
        combine=plan(original,observations)
        combo=dict(units=['u1','u2'],question='Do they agree?',view='Joint reading.',read=[dict(unit='u1')])
        output=make_output(combine,[combo]); delta=parts.validate_output(combine,output,combine['_test_inventory'])
        self.assertEqual(delta['combinations'][0]['id'],'c2-1')
        output['combinations'][0]['read']=[dict(unit='u1',body='0'*64,start=0,end=30)]
        with self.assertRaises(ResearchError):parts.validate_output(combine,output,combine['_test_inventory'])
        observations.append(observe(combine,make_output(combine,[combo])))
        check=plan(original,observations)
        self.assertEqual(check['review_parts']['kind'],'check')
        output=make_output(check); output['findings']=[]
        with self.assertRaises(ResearchError):parts.validate_output(check,output,check['_test_inventory'])

    def test_final_request_form_and_unknown_units(self):
        packet=plan(make_packet(0)); inventory=packet['_test_inventory']
        for output in (dict(kind='requests',unit_requests=[],source_requests=[]),dict(kind='requests',unit_requests=[dict(unit='u9')],source_requests=[])):
            with self.assertRaises(ResearchError):parts.validate_output(packet,output,inventory)

    def test_final_source_requests_use_existing_source_contract(self):
        packet=plan(make_packet(0))
        output=dict(kind='requests',unit_requests=[],source_requests=[dict(id='page',kind='inventory_page',page=0)])
        self.assertEqual(parts.validate_output(packet,output,packet['_test_inventory']),dict(cards=[],combinations=[],findings=[]))

class RevisedOutputTests(unittest.TestCase):
    def test_unborne_covers_exactly_the_targets_without_a_bearing(self):
        packet = plan(make_packet(1, 200))
        output = make_output(packet)
        output['unborne'] = [dict(target=row['id'], reason='No delivered unit bears on this target.')
                             for row in packet['review_parts']['targets']]
        self.assertEqual(len(parts.validate_output(packet, output, packet['_test_inventory'])['cards']), 1)
        output['unborne'].pop()
        with self.assertRaises(ResearchError):
            parts.validate_output(packet, output, packet['_test_inventory'])

    def test_material_bearing_requires_a_statement(self):
        packet = plan(make_packet(1, 200))
        output = make_output(packet)
        target = packet['review_parts']['targets'][0]['id']
        output['cards'][0]['bearing'] = [dict(target=target, relation='supports', reason='A bearing.')]
        output['unborne'] = [dict(target=row['id'], reason='No bearing.')
                             for row in packet['review_parts']['targets'] if row['id'] != target]
        with self.assertRaises(ResearchError):
            parts.validate_output(packet, output, packet['_test_inventory'])

    def test_ordinary_final_has_no_parts_output_allowance(self):
        packet = plan(make_packet(0))
        output = dict(stage='result', limitations=['x' * 70000])
        self.assertEqual(parts.validate_output(packet, output, packet['_test_inventory']),
                         dict(cards=[], combinations=[], findings=[]))

    def test_request_why_and_item_spans_are_validated(self):
        packet = make_packet(1, 8000)
        read = plan(packet, limit=20000)
        combined = plan(packet, [observe(read)], limit=20000)
        combined['review_parts']['originals'] = []
        request = dict(kind='requests', unit_requests=[dict(unit='u1', item=True, start=0, end=40,
                                                           why='Read this item again.')], source_requests=[])
        self.assertEqual(parts.validate_output(combined, request, combined['_test_inventory']),
                         dict(cards=[], combinations=[], findings=[]))
        request['unit_requests'][0]['end'] = 100000
        with self.assertRaises(ResearchError):
            parts.validate_output(combined, request, combined['_test_inventory'])

    def test_combination_originals_must_fit_one_check(self):
        packet = make_packet(2, 200)
        read = plan(packet)
        combined = plan(packet, [observe(read)])
        combined['review_parts']['check_room'] = 10
        output = make_output(combined, [dict(units=['u1'], question='Question?', view='A view.',
                                             read=[dict(unit='u1')])])
        with self.assertRaises(ResearchError):
            parts.validate_output(combined, output, combined['_test_inventory'])

    def test_verified_statements_keep_canonical_item_offsets(self):
        packet = plan(make_packet(1, 200))
        output = make_output(packet)
        output['cards'][0]['statements'] = [dict(text='x' * 30, why='The exact item text.')]
        statement = parts.validate_output(packet, output, packet['_test_inventory'])['cards'][0]['statements'][0]
        item = packet['problem_evidence']['openness_searches'][0]
        start = parts._dump(item).index('x' * 30)
        self.assertEqual(statement['at'], dict(unit='u1', item=True, start=start, end=start + 30))

    def test_verified_statements_keep_whole_body_offsets_from_a_segment(self):
        packet = make_packet(0, body='prefix\n' + 'filler ' * 9000 + '\nA decisive theorem is here.\n')
        observations = []
        for _ in range(20):
            turn = plan(packet, observations, limit=35000)
            if turn['review_parts']['kind'] != 'read':
                self.fail('The requested text was never delivered')
            output = make_output(turn)
            if any('A decisive theorem is here.' in entry['content'] for entry in turn['evidence']):
                output['cards'][0]['statements'] = [dict(text='A decisive theorem is here.', why='The theorem.')]
                delta = parts.validate_output(turn, output, turn['_test_inventory'])
                statement = delta['cards'][0]['statements'][0]
                start = packet['evidence'][0]['content'].index('A decisive theorem is here.')
                self.assertEqual(statement['at'], dict(unit='u1', body=packet['evidence'][0]['artifact']['sha256'],
                                                       start=start, end=start + len(statement['text'])))
                break
            observations.append(observe(turn))
        else:
            self.fail('The statement search did not terminate')

    def test_renaming_a_source_request_does_not_create_progress(self):
        packet = plan(make_packet(0))
        source = dict(id='first', kind='source', version_id='arxiv:2401.00001v1', depth='abstract')
        packet['source_deliveries'] = [dict(request=source, status='delivered')]
        request = dict(kind='requests', unit_requests=[], source_requests=[dict(source, id='second')])
        with self.assertRaises(ResearchError) as raised:
            parts.validate_output(packet, request, packet['_test_inventory'])
        self.assertEqual(raised.exception.code, 'review_parts_request_no_progress')

    def test_segment_citation_is_confined_to_the_delivered_locator(self):
        import hashlib
        packet = make_packet(0, body='αβγ ' * 20000)
        turn = plan(packet, limit=40000)
        body = packet['evidence'][0]
        segment = next(entry for entry in turn['evidence'] if entry.get('segment') and not entry.get('item'))
        start, end = segment['segment']['start'], segment['segment']['end']
        link = dict(artifact=body['artifact'], locator=dict(kind='span', start=start, end=end,
                    sha256=hashlib.sha256(body['content'][start:end].encode()).hexdigest()))
        reference = dict(kind='source', link=link)
        self.assertTrue(parts.has_series_reference(None, [turn], reference))
        self.assertFalse(parts.has_series_reference(None, [turn], body['artifact']))
        link['locator']['end'] += 1
        self.assertFalse(parts.has_series_reference(None, [turn], reference))

    def test_a_complete_item_span_already_answers_a_whole_unit_request(self):
        original = make_packet(1, 1000)
        read = plan(original)
        final = plan(original, [observe(read)])
        row = final['_test_inventory']['units'][0]
        final['review_parts']['originals'] = [dict(unit='u1', item=True, start=0, end=row['item_characters'])]
        request = dict(kind='requests', unit_requests=[dict(unit='u1', why='Read the same unit.')], source_requests=[])
        with self.assertRaises(ResearchError) as raised:
            parts.validate_output(final, request, final['_test_inventory'])
        self.assertEqual(raised.exception.code, 'review_parts_request_no_progress')

    def test_parts_binding_is_checked_before_a_staged_base_prompt(self):
        from unittest.mock import patch
        from research_harness import review_protocol as protocol, review_staged_protocol
        prompt = protocol.canonical_prompt('adjudicator', orchestration='problem_creation',
            staged_task=dict(type='final'), review_parts=dict(adapter='problem_result', kind='read'))
        assignment = dict(role='adjudicator', subject=dict(kind='problem'), prompt=prompt, context={})
        with patch.object(review_staged_protocol, 'matches_prompt', return_value=True) as staged_match:
            self.assertFalse(protocol._prompt_matches(assignment))
        staged_match.assert_not_called()

    def test_combination_can_request_a_passage_carded_under_its_reading(self):
        packet = make_packet(0)
        packet['problem_evidence']['readings'] = [dict(id='reading', text='The reading.'),
                                                  dict(id='other', text='Another reading.')]
        packet['problem_evidence']['passages'] = [dict(reading_id='reading', text='The exact passage.')]
        read = plan(packet)
        observations = [observe(read)]
        combine = plan(packet, observations)
        combination = dict(units=['u2'], question='What does this passage establish?',
                           view='Read the source.', read=[dict(unit='u2')])
        observations.append(observe(combine, make_output(combine, [combination])))
        check = plan(packet, observations)
        self.assertEqual(check['review_parts']['kind'], 'check')
        self.assertEqual([card['unit'] for card in check['review_parts_state']['cards']], ['u1'])
        observations.append(observe(check))
        self.assertEqual(plan(packet, observations)['review_parts']['kind'], 'final')

    def test_read_manifest_cannot_hide_a_delivered_unit_without_its_card(self):
        packet = plan(make_packet(2, 200))
        packet['review_parts']['delivered_units'].pop()
        with self.assertRaises(ResearchError) as raised:
            parts.validate_output(packet, make_output(packet), packet['_test_inventory'])
        self.assertEqual(raised.exception.code, 'review_parts_delivery_invalid')
