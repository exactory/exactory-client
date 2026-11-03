import json
import unittest
from research_harness.errors import ResearchError
from research_harness import review_parts as parts
from review_parts_fixtures import make_packet, make_output, plan, observe, measure_packet, measure_input_limit


class PlanningTests(unittest.TestCase):
    def test_read_then_combine_then_final_without_history(self):
        packet, observations = make_packet(5), []
        input_limit = measure_input_limit(110000)
        for _ in range(15):
            turn = plan(packet, observations)
            self.assertLessEqual(measure_packet({k:v for k,v in turn.items() if k != '_test_inventory'}), input_limit)
            if turn['review_parts']['kind'] == 'final':
                break
            observations.append(observe(turn))
        self.assertEqual([row['receipt']['kind'] for row in observations], ['read', 'read', 'combine'])
        self.assertEqual(turn['review_parts']['plan']['units_remaining'], 0)

    def test_inventory_contains_metadata_and_objection_entries(self):
        packet = make_packet(0)
        packet['material_objections'] = [dict(id='o1', version_evidence=[dict(reason='x'*20000)])]
        packet['historical_reviews'] = [dict(objections=[dict(id='o2', version_evidence=[dict(reason='y'*20000)])])]
        inventory = parts.build_inventory(packet, 'problem_result')
        self.assertEqual(len(inventory['units']), 2)
        self.assertLess(len(json.dumps(inventory)), 2500)
        self.assertEqual([r['handle'] for r in inventory['units']], ['u1','u2'])

    def test_segments_cover_exact_unicode_body_and_repeat_card(self):
        packet = make_packet(0, body='αβγ\n\n' * 35000)
        observations, spans = [], []
        for _ in range(30):
            turn = plan(packet, observations, room=55000)
            if turn['review_parts']['kind'] != 'read':
                break
            self.assertEqual(turn['review_parts']['delivered_units'], ['u1'])
            for entry in turn['evidence']:
                if 'segment' in entry and not entry.get('item'):
                    span=entry['segment']; spans.append((span['start'], span['end']))
                    self.assertEqual(entry['content'], ('αβγ\n\n'*35000)[span['start']:span['end']])
            observations.append(observe(turn))
        self.assertGreater(len(spans), 1)
        self.assertEqual(spans[0][0], 0)
        self.assertEqual(spans[-1][1], 175000)
        self.assertTrue(all(a[1] == b[0] for a,b in zip(spans, spans[1:])))
        self.assertTrue(parts.has_series_attachment(None, [o['packet'] for o in observations], packet['problem_evidence']['assessment_evidence'][0]))

    def test_read_and_card_allowances_adapt_and_replacement_halves(self):
        packet = make_packet(8, 18000)
        turn=plan(packet)
        observed=observe(turn, output_tokens=100000)
        following=plan(packet,[observed])
        self.assertEqual(following['review_parts']['plan']['unit_allowance'], turn['review_parts']['unit_characters']//2)
        replaced=plan(packet, replacement=dict(unit_characters=40000, over_cap=True))
        self.assertEqual(replaced['review_parts']['plan']['unit_allowance'], 20000)
        h=turn['review_parts']; inv=turn['_test_inventory']
        expected=min(80000,max(4000,int(h['card_budget_remaining']*h['unit_characters']/h['unit_characters_remaining'])))
        self.assertEqual(h['output_allowance'],expected)

    def test_no_progress_and_final_frame_too_large(self):
        with self.assertRaises(ResearchError) as raised:
            plan(make_packet(1, 200000), room=20000)
        self.assertEqual(raised.exception.code,'review_parts_inventory_too_large')
        packet=make_packet(0); packet['large_frame']='x'*200000
        with self.assertRaises(ResearchError) as raised:
            plan(packet,room=20000)
        self.assertEqual(raised.exception.code,'review_parts_frame_too_large')

    def test_check_carries_whole_findings_without_an_original_traffic_cap(self):
        packet = make_packet(2, 1000)
        read = plan(packet, room=60000)
        observations = [observe(read)]
        combine = plan(packet, observations, room=60000)
        combinations = [dict(units=['u1', 'u2'], question='Joint question ' + str(index),
                             view='A joint check.', read=[dict(unit='u1'), dict(unit='u2')])
                        for index in range(4)]
        # Different spans make the combinations distinct after deduplication.
        for index, combination in enumerate(combinations):
            combination['read'][0] = dict(unit='u1', item=True, start=index, end=800)
        observations.append(observe(combine, make_output(combine, combinations)))
        check = plan(packet, observations, room=60000)
        self.assertEqual(check['review_parts']['kind'], 'check')
        finding = observe(check)
        self.assertEqual(len(finding['delta']['findings']), 4)
        self.assertFalse(any('complete' in row for row in finding['delta']['findings']))
        observations.append(finding)
        final = plan(packet, observations, room=60000)
        self.assertEqual(final['review_parts']['kind'], 'final')
        self.assertNotIn('unchecked_combinations', final['review_parts'])

    def test_final_request_can_return_to_original_and_must_progress(self):
        packet=make_packet(4,15000); observations=[]
        while True:
            turn=plan(packet,observations,room=40000)
            if turn['review_parts']['kind']=='final':break
            observations.append(observe(turn))
        filled={r['unit'] for r in turn['review_parts']['originals']}
        requested=next(u['handle'] for u in turn['_test_inventory']['units'] if u['handle'] not in filled)
        request=dict(kind='requests',unit_requests=[dict(unit=requested,why='Read the missing original.')],source_requests=[])
        observations.append(observe(turn,request))
        answer=plan(packet,observations,room=40000)
        self.assertEqual(answer['review_parts']['originals'][0]['unit'],requested)
        with self.assertRaises(ResearchError) as raised:
            observe(answer,request)
        self.assertEqual(raised.exception.code,'review_parts_request_no_progress')

    def test_final_fill_prioritizes_bearings_then_combination_originals(self):
        packet = make_packet(3, 10000)
        read = plan(packet, room=60000)
        output = make_output(read)
        target = output['unborne'].pop(0)['target']
        card = next(card for card in output['cards'] if card['unit'] == 'u3')
        card['bearing'] = [dict(target=target, relation='qualifies', reason='The exact condition matters.')]
        card['statements'] = [dict(text='x' * 30, why='This is the exact condition.')]
        observations = [observe(read, output)]
        combine = plan(packet, observations, room=60000)
        combination = dict(units=['u2'], question='Does this condition hold?',
                           view='Check the original.', read=[dict(unit='u2')])
        observations.append(observe(combine, make_output(combine, [combination])))
        check = plan(packet, observations, room=60000)
        observations.append(observe(check))
        final = plan(packet, observations, room=60000)
        self.assertEqual([entry['unit'] for entry in final['review_parts']['fill']], ['u3', 'u2', 'u1'])

    def test_final_body_span_uses_canonical_size_when_it_needs_multiple_turns(self):
        packet = make_packet(0, body='\\' * 50000)
        observations = []
        for _ in range(20):
            final = plan(packet, observations, room=60000)
            if final['review_parts']['kind'] == 'final':
                break
            observations.append(observe(final))
        body = packet['evidence'][0]['artifact']['sha256']
        request = dict(kind='requests', unit_requests=[dict(unit='u1', body=body, start=0, end=40000,
                                                           why='Read this exact span.')], source_requests=[])
        observations.append(observe(final, request))
        answer = plan(packet, observations, room=60000)
        self.assertGreater(answer['review_parts']['answered'][0]['start'], 0)

    def test_combine_blocks_cover_every_pair_and_state_stays_fixed(self):
        packet = make_packet(12, 100)
        read = plan(packet, room=100000)
        output = make_output(read)
        for card in output['cards']:
            card['summary'] = 'A supported record. ' * 100
        observation = observe(read, output)
        input_limit = measure_input_limit(100000)
        context = parts.prepare_plan(packet, read['_test_inventory'], [observation], series_id='a1',
                                     input_limit=input_limit, measure=measure_packet, output_tokens=128000)
        empty = context.render('combine', state=dict(cards=[], combinations=[], findings=[]))
        cards = observation['delta']['cards']
        pair_room = max(4 * max(len(parts.render_json(card)) for card in cards),
                        3 * len(parts.render_json(cards)) // 4)
        combine_overhead = input_limit - measure_packet(empty) - pair_room
        def measure(candidate):
            extra = combine_overhead if candidate['review_parts']['kind'] == 'combine' else 0
            return measure_packet(candidate) + extra
        observations = [observation]
        pairs = []
        blocks = None
        for _ in range(100):
            turn = plan(packet, observations, input_limit=input_limit, measure=measure)
            if turn['review_parts']['kind'] != 'combine':
                break
            pairs.append(tuple(turn['review_parts']['combine_pair']))
            if blocks is None:
                blocks = turn['review_parts']['block_plan']
            self.assertEqual(turn['review_parts']['block_plan'], blocks)
            observations.append(observe(turn))
        else:
            self.fail('Block combinations did not terminate')
        self.assertGreater(len(blocks), 1)
        expected = {(left, right) for left in range(len(blocks))
                    for right in range(left + 1, len(blocks))}
        self.assertEqual(set(pairs), expected)
        self.assertEqual(len(pairs), len(expected))

    def test_final_whole_unit_requests_continue_a_segment_after_previous_final(self):
        packet=make_packet(2,100,body='q'*90000);observations=[]
        for _ in range(30):
            turn=plan(packet,observations,room=40000)
            if turn['review_parts']['kind']=='final':break
            observations.append(observe(turn))
        request=dict(kind='requests',unit_requests=[dict(unit='u3',why='Read the remaining body.')],source_requests=[])
        observations.append(observe(turn,request))
        first=plan(packet,observations,room=40000)
        observations.append(observe(first,request))
        second=plan(packet,observations,room=40000)
        first_spans=[e['segment'] for e in first['evidence'] if 'segment' in e]
        second_spans=[e['segment'] for e in second['evidence'] if 'segment' in e]
        self.assertTrue(first_spans and second_spans)
        self.assertGreaterEqual(second_spans[0]['start'],first_spans[-1]['end'])

    def test_omitted_binary_body_refuses_before_recording_a_read(self):
        packet = make_packet(0, body='binary bytes')
        packet['evidence'][0].update(content='', encoding='omitted', delivery='bytes-omitted-on-text-route')
        with self.assertRaises(ResearchError) as raised:
            plan(packet, room=50000)
        self.assertEqual(raised.exception.code, 'review_parts_inventory_too_large')

    def test_missing_output_usage_preserves_the_previous_read_allowance(self):
        packet=make_packet(8,18000);first=plan(packet)
        observation=observe(first,output_tokens=None)
        following=plan(packet,[observation])
        self.assertEqual(following['review_parts']['plan']['unit_allowance'],first['review_parts']['unit_characters'])

class RevisedPlanningTests(unittest.TestCase):
    def test_oversized_item_is_delivered_as_exact_canonical_json_segments(self):
        packet = make_packet(1, 120000)
        observations = []
        fragments = []
        for _ in range(30):
            turn = plan(packet, observations, room=60000)
            if turn['review_parts']['kind'] != 'read':
                break
            fragments.extend(entry for entry in turn['evidence'] if entry.get('item'))
            observations.append(observe(turn))
        self.assertGreater(len(fragments), 1)
        content = ''.join(entry['content'] for entry in fragments)
        self.assertEqual(content, json.dumps(packet['problem_evidence']['openness_searches'][0],
                                            ensure_ascii=False, sort_keys=True, separators=(',', ':')))

    def test_inventory_appends_late_units_without_changing_previous_rows(self):
        packet = make_packet(2, 200)
        inventory = parts.build_inventory(packet, 'problem_result')
        packet['material_objections'] = [dict(id='late', version_evidence=[dict(reason='New evidence.')])]
        extended = parts.build_inventory(packet, 'problem_result', previous=inventory)
        self.assertEqual(extended['units'][:2], inventory['units'])
        self.assertEqual(extended['units'][-1]['handle'], 'u3')

    def test_late_unit_is_read_after_combine_then_combined_with_earlier_cards(self):
        packet = make_packet(2, 200)
        read = plan(packet)
        combine = plan(packet, [observe(read)])
        observations = [observe(read), observe(combine)]
        packet['material_objections'] = [dict(id='late', version_evidence=[dict(reason='New evidence.')])]
        next_turn = plan(packet, observations)
        self.assertEqual(next_turn['review_parts']['kind'], 'read')
        self.assertEqual(next_turn['review_parts']['delivered_units'], ['u3'])
        observations.append(observe(next_turn))
        recombine = plan(packet, observations)
        self.assertEqual(recombine['review_parts']['kind'], 'combine')
        self.assertEqual({card['unit'] for card in recombine['review_parts_state']['cards']}, {'u1', 'u2', 'u3'})

    def test_read_turn_carries_only_earlier_cards_of_repeated_units(self):
        packet = make_packet(6, 10000)
        first = plan(packet, room=40000)
        second = plan(packet, [observe(first)], room=40000)
        repeated = set(first['review_parts']['delivered_units']) & set(second['review_parts']['delivered_units'])
        self.assertEqual({card['unit'] for card in second['review_parts_state']['cards']}, repeated)
        self.assertEqual(second['review_parts_state']['combinations'], [])
        self.assertEqual(second['review_parts_state']['findings'], [])

    def test_non_text_body_completes_whole_and_oversized_body_refuses_early(self):
        import base64
        import hashlib
        packet = make_packet(0)
        data = b'\x00\xff' * 100
        reference = dict(path='binary', sha256=hashlib.sha256(data).hexdigest(), size=len(data),
                         media_type='application/octet-stream')
        packet['problem_evidence']['computations'] = [dict(artifact=reference)]
        packet['evidence'] = [dict(artifact=reference, encoding='base64', content=base64.b64encode(data).decode())]
        first = plan(packet, room=30000)
        final = plan(packet, [observe(first)], room=30000)
        self.assertEqual(final['review_parts']['kind'], 'final')
        packet['evidence'][0]['content'] = 'a' * 90000
        with self.assertRaises(ResearchError) as raised:
            plan(packet, room=30000)
        self.assertEqual(raised.exception.code, 'review_parts_inventory_too_large')

    def test_canonical_unicode_size_is_not_ascii_escape_size(self):
        packet = make_packet(1, 1)
        item = dict(id='math', reason='λ≠α' * 1000)
        packet['problem_evidence']['openness_searches'] = [item]
        inventory = parts.build_inventory(packet, 'problem_result')
        self.assertEqual(inventory['units'][0]['item_characters'],
                         len(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(',', ':'))))

    def test_keyed_collection_projects_and_validates_by_key(self):
        from unittest.mock import patch
        from research_harness.review_parts_roles import ADAPTERS
        packet = make_packet(0)
        packet['items'] = {'key-a': dict(text='An object entry.')}
        adapter = dict(applies=lambda value, value_packet: True,
                       enumerate_units=lambda value: [dict(item=value['items']['key-a'],
                           occurrences=[dict(path=['items'], key='key-a')], bodies=[])],
                       targets=lambda value: [], series_key=lambda value: 'keyed', final_prompt='')
        with patch.dict(ADAPTERS, keyed=adapter):
            inventory = parts.build_inventory(packet, 'keyed')
            planned = parts.plan_turn(packet, inventory, [], series_id='keyed-first',
                                      input_limit=measure_input_limit(40000),
                                      measure=measure_packet, output_tokens=128000)
            output = make_output(planned)
            self.assertEqual(planned['items'], packet['items'])
            self.assertEqual(len(parts.validate_output(planned, output, inventory)['cards']), 1)

    def test_wrapped_historical_reviews_are_units_and_targets(self):
        packet = make_packet(0)
        packet['historical_reviews'] = [dict(review=dict(payload=dict(objections=[
            dict(id='wrapped-objection', version_evidence=[dict(reason='Historical evidence.')])])))]
        inventory = parts.build_inventory(packet, 'problem_result')
        self.assertEqual(len(inventory['units']), 1)
        turn = plan(packet)
        self.assertIn('wrapped-objection', [target['id'] for target in turn['review_parts']['targets']])

    def test_inventory_keeps_exact_citations_on_carried_cards(self):
        packet = make_packet(1, 100)
        citation = dict(kind='record', record_kind='literature_search', id='s0')
        packet['problem_evidence']['openness_searches'][0]['cite'] = citation
        turn = plan(packet)
        self.assertEqual(turn['_test_inventory']['units'][0]['citations'], [citation])
        self.assertEqual(observe(turn)['delta']['cards'][0]['citations'], [citation])

    def test_large_item_with_small_binary_body_is_not_refused(self):
        import base64
        from research_harness.artifacts import describe_artifact
        packet = make_packet(0)
        data = b'\x00\xff' * 100
        reference = describe_artifact(data, 'application/octet-stream')
        packet['problem_evidence']['computations'] = [dict(artifact=reference, description='x' * 50000)]
        packet['evidence'] = [dict(artifact=reference, encoding='base64', content=base64.b64encode(data).decode())]
        observations = []
        for _ in range(20):
            turn = plan(packet, observations, room=30000)
            if turn['review_parts']['kind'] == 'final':
                break
            observations.append(observe(turn))
        else:
            self.fail('A feasible binary unit never completed')
        self.assertTrue(parts.has_series_attachment(None, [row['packet'] for row in observations], reference))

    def test_weighted_partition_uses_the_fewest_contiguous_blocks(self):
        weights = [1, 1, 1, 7, 2]
        blocks = parts.partition_card_weights(weights, 10)
        self.assertEqual(len(blocks), 3)
        self.assertEqual([index for block in blocks for index in block], list(range(len(weights))))
        weights = [1, 1, 7, 1, 1]
        blocks = parts.partition_card_weights(weights, 10)
        self.assertEqual(len(blocks), 3)
        self.assertTrue(all(sum(weights[index] for index in left + right) <= 10
                            for position, left in enumerate(blocks) for right in blocks[position + 1:]))

    def test_final_request_survives_intervening_late_read_and_combine(self):
        packet = make_packet(4, 24000)
        observations = []
        for _ in range(20):
            final = plan(packet, observations, room=50000)
            if final['review_parts']['kind'] == 'final':
                break
            observations.append(observe(final))
        request = dict(kind='requests', unit_requests=[dict(unit='u4', why='Read its original.')], source_requests=[])
        observations.append(observe(final, request))
        packet['material_objections'] = [dict(id='late', version_evidence=[dict(reason='Late evidence.')])]
        for _ in range(20):
            turn = plan(packet, observations, room=50000)
            if turn['review_parts']['kind'] == 'final':
                break
            observations.append(observe(turn))
        self.assertEqual(turn['review_parts']['request'], request)
        self.assertEqual(turn['review_parts']['answered'][0]['unit'], 'u4')

    def test_segmented_item_keeps_its_occurrence_binding_in_the_header(self):
        turn = plan(make_packet(1, 120000), room=60000)
        self.assertEqual(turn['problem_evidence']['openness_searches'], [])
        self.assertEqual(turn['review_parts']['units'][0]['occurrences'],
                         [dict(path=['problem_evidence', 'openness_searches'], index=0)])

    def test_empty_body_is_delivered_with_a_segmented_item(self):
        packet = make_packet(0, body='')
        reference = packet['problem_evidence']['assessment_evidence'].pop()
        packet['problem_evidence']['computations'] = [dict(artifact=reference, description='x' * 50000)]
        observations = []
        for _ in range(20):
            turn = plan(packet, observations, room=30000)
            if turn['review_parts']['kind'] == 'final':
                break
            observations.append(observe(turn))
        reference = packet['evidence'][0]['artifact']
        self.assertTrue(parts.has_series_attachment(None, [row['packet'] for row in observations], reference))

    def test_passage_can_be_carded_under_a_reading_with_a_segmented_item(self):
        packet = make_packet(0)
        packet['problem_evidence']['readings'] = [dict(id='reading', text='r' * 70000)]
        packet['problem_evidence']['passages'] = [dict(reading_id='reading', text='p' * 100000)]
        observations = []
        for _ in range(20):
            turn = plan(packet, observations, room=60000)
            if turn['review_parts']['kind'] == 'final':
                break
            output = make_output(turn)
            if turn['review_parts']['kind'] == 'read':
                self.assertEqual([card['unit'] for card in output['cards']], ['u1'])
            observations.append(observe(turn, output))
        else:
            self.fail('The passage never reached a final')
        self.assertEqual(turn['review_parts']['plan']['units_remaining'], 0)
        request = dict(kind='requests', unit_requests=[dict(unit='u2', why='Read the passage again.')], source_requests=[])
        observations.append(observe(turn, request))
        answer = plan(packet, observations, room=60000)
        self.assertEqual(answer['review_parts']['answered'][0]['unit'], 'u2')

    def test_target_labels_select_the_id_from_the_text_constant(self):
        from unittest.mock import patch
        from research_harness import review_parts_prompts, review_parts_problem_result
        lines = [line for line in review_parts_prompts.TARGET_LABELS.splitlines()
                 if not line.startswith(('problem_statement: ', 'A1: '))]
        labels_text = '\n'.join(lines + ['problem_statement: Statement label.', 'A1: Precision label.'])
        with patch.object(review_parts_prompts, 'TARGET_LABELS', labels_text):
            targets = review_parts_problem_result.list_targets(make_packet(0))
        labels = {row['id']: row['label'] for row in targets}
        self.assertEqual(labels['problem_statement'], 'Statement label.')
        self.assertEqual(labels['A1'], 'Precision label.')

    def test_every_problem_check_and_rubric_item_has_a_target_label(self):
        from research_harness import review_parts_prompts, review_parts_problem_result
        from research_harness.problem_rubric import PROBLEM_CHECKS, ITEMS
        labels = dict(line.split(': ', 1) for line in review_parts_prompts.TARGET_LABELS.splitlines()
                      if ': ' in line)
        for identifier in PROBLEM_CHECKS + ITEMS:
            with self.subTest(target=identifier):
                self.assertTrue(labels[identifier].strip())
        self.assertEqual({target['id'] for target in review_parts_problem_result.list_targets(make_packet(0))},
                         set(PROBLEM_CHECKS + ITEMS))

    def test_missing_target_label_is_a_typed_error_naming_the_target(self):
        from unittest.mock import patch
        from research_harness import review_parts_prompts, review_parts_problem_result
        labels_text = '\n'.join(line for line in review_parts_prompts.TARGET_LABELS.splitlines()
                                if not line.startswith('A1: '))
        with patch.object(review_parts_prompts, 'TARGET_LABELS', labels_text):
            with self.assertRaises(ResearchError) as raised:
                review_parts_problem_result.list_targets(make_packet(0))
        self.assertEqual(raised.exception.code, 'review_parts_target_label_missing')
        self.assertEqual(raised.exception.details['target'], 'A1')

    def test_replacement_uses_slot_series_key_and_normalized_identity(self):
        from unittest.mock import Mock
        value = dict(role='result', route_id='route', reviewer_id='reviewer', author_id='author')
        failed = dict(value, id='failed', packet={}, slot_id='slot-A', recorded_revision=1,
                      reviewer_id=' REVIEWER ', context={})
        series_key = dict(binding='unchanged')
        packet = {'review_parts': dict(protocol=parts.PROTOCOL, kind='read', adapter='problem_result',
                                       series_key=series_key, unit_characters=30000)}
        artifacts = Mock()
        artifacts.read.return_value = json.dumps(packet).encode()
        attempt = dict(assignment_id='failed', output=None, usage={}, error=dict(code='review_incomplete'))
        records = dict(review_assignment={'failed': failed}, review_attempt={'attempt': attempt})
        result = parts.find_failed_replacement(records, artifacts, value, 'problem_result', series_key,
                                         'slot-A', None, 128000)
        self.assertEqual(result, dict(unit_characters=30000, over_cap=True))
        self.assertIsNone(parts.find_failed_replacement(records, artifacts, value, 'problem_result', series_key,
                                                 'slot-B', None, 128000))
        self.assertIsNone(parts.find_failed_replacement(records, artifacts, value, 'problem_result', dict(binding='changed'),
                                                 'slot-A', None, 128000))
        attempt['error'] = dict(code='unrelated', message='review_incomplete')
        self.assertIsNone(parts.find_failed_replacement(records, artifacts, value, 'problem_result', series_key,
                                                 'slot-A', None, 128000))

    def test_frame_with_less_than_one_card_floor_refuses_as_final_too_large(self):
        with self.assertRaises(ResearchError) as raised:
            plan(make_packet(1, 100), room=7000)
        self.assertEqual(raised.exception.code, 'review_parts_final_too_large')
