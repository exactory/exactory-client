import json
from research_harness import review_protocol as protocol, review_parts as parts, problem_delivery
from research_harness.review_bodies import read_history
from research_harness.evidence import digest
from review_parts_fixtures import PartsResultCase


class ResultJourneyTests(PartsResultCase):
    def test_oversized_entry_records_inventory_and_empty_history(self):
        self.oversized(); assignment=self.assign(); packet=self.packet(assignment); header=packet['review_parts']
        self.assertEqual((header['protocol'],header['adapter'],header['kind'],header['turn']),('exactory-review-parts-v2','problem_result','read',1))
        self.assertEqual(header['series_assignment_id'],assignment['id'])
        self.assertEqual(assignment['history'],[])
        self.assertEqual(read_history(self.case.artifacts,assignment),[])
        self.assertEqual(assignment['session_id'],self.case.read_records()['review_assignment'][assignment['history_assignment_id']]['session_id'])
        self.assertEqual(digest(parts.read_inventory(self.case.artifacts,header['inventory'])),header['inventory_digest'])
        self.assertIn('review_parts_plan',assignment)

    def test_fitting_result_keeps_bar_history(self):
        assignment=self.assign(); self.assertNotIn('review_parts',self.packet(assignment))
        self.assertTrue(read_history(self.case.artifacts,assignment))

    def test_series_final_coverage_and_next_obligation(self):
        self.oversized(); first=self.assign(); self.invoke(first)
        pending=problem_delivery.assess_source_requests(self.case.read_records(),self.case.artifacts)
        item=next(row for row in pending if row['code']=='review_parts_pending')
        self.assertEqual(item['next'][0]['scope']['prior_assignment_id'],first['id'])
        second=self.assign('second-turn',first['id']); turns,final=self.finish_series(second)
        review=self.record_final(final)
        self.assertEqual(self.coverage(review),[])
        self.assertTrue(parts.verify_series(self.case.read_records(),self.case.artifacts,final['id'])['complete'])
        request=protocol.build_assignment_request(self.case.read_records(),self.case.artifacts,final)
        self.assertEqual(len(protocol.transport.logical_messages(request)),1)

    def test_new_prompt_hash_computed_and_ordinary_unchanged(self):
        old=protocol.canonical_prompt('result',orchestration='problem_creation',excerpts=True)
        new=protocol.canonical_prompt('result',orchestration='problem_creation',excerpts=True,review_parts=dict(adapter='problem_result',kind='read'))
        from research_harness import review_parts_prompts as prompts
        self.assertTrue(new['text'].startswith(old['text'] + '\n\n' + prompts.PARTS_COMMON + '\n\n'))
        self.assertNotEqual(new['rendered_prompt_hash'],old['rendered_prompt_hash'])
        self.assertEqual(new,protocol.canonical_prompt('result',orchestration='problem_creation',excerpts=True,review_parts=dict(adapter='problem_result',kind='read')))

    def test_initial_read_over_cap_replacement_keeps_slot_and_halves_allowance(self):
        from unittest.mock import patch
        from codex_route_fixtures import build_codex_response
        self.oversized(count=12,characters=10000,limit=80000)
        first=self.assign()
        def incomplete(route,request,credential):
            response=build_codex_response(route,request,{})
            response['status']='incomplete'
            response['usage']['output_tokens']=route['configuration']['max_output_tokens']
            return response
        with patch.object(protocol.transport,'send_request',side_effect=incomplete):
            protocol.invoke_assignment(self.case.store,dict(id='over-cap',assignment_id=first['id']),
                credential='fixture',expected_revision=self.case.store.revision,request_id='over-cap')
        replacement=self.assign('replacement')
        self.assertEqual(replacement['slot_id'],first['slot_id'])
        self.assertEqual(self.packet(replacement)['review_parts']['plan']['unit_allowance'],self.packet(first)['review_parts']['unit_characters']//2)

    def test_evidence_change_ends_series_and_carries_no_cards(self):
        self.oversized();first=self.assign();self.invoke(first)
        self.case.expected['openness_searches'][0]['search']['reason']+=' Changed.'
        next_turn=self.assign('new-evidence-series',first['id']);packet=self.packet(next_turn)
        self.assertEqual(packet['review_parts']['series_assignment_id'],next_turn['id'])
        self.assertEqual(packet['review_parts']['turn'],1)
        self.assertEqual(packet['review_parts_state']['cards'],[])
        self.assertEqual(next_turn['session_id'],first['session_id'])

    def test_v1_recorded_final_refresh_enters_v2(self):
        from unittest.mock import patch
        from problem_parts_fixtures import ResultPartsCase
        from research_harness import review_problem_parts as legacy
        self.oversized()
        with patch.object(parts,'prepare_packet',side_effect=lambda records,artifacts,value,route,packet,prompt,history,**kwargs:packet), \
                patch.object(legacy,'prepare_packet',side_effect=legacy._prepare_legacy_packet), \
                patch.object(self,'output',side_effect=lambda assignment:ResultPartsCase.output(self,assignment)):
            turns,final=self.finish_parts()
            self.record_final(final)
        self.case.expected['openness_searches'][0]['search']['reason']+=' Changed evidence.'
        assignment=self.assign('v2-after-v1',final['id'])
        self.assertEqual(self.packet(assignment)['review_parts']['series_assignment_id'],assignment['id'])
        self.assertNotIn(legacy.KEY,self.packet(assignment))

    def test_prompt_variant_requires_matching_parts_header_binding(self):
        self.oversized();assignment=self.assign()
        self.assertTrue(protocol._prompt_matches(assignment))
        for replacement in (None,dict(protocol=parts.PROTOCOL,adapter='problem_result',kind='final')):
            changed=dict(assignment)
            if replacement is None:changed.pop('review_parts')
            else:changed['review_parts']=replacement
            self.assertFalse(protocol._prompt_matches(changed))
    def test_final_source_request_is_answered_by_fresh_final(self):
        self.oversized();turns,final=self.finish_series()
        output=dict(kind='requests',unit_requests=[],source_requests=[dict(id='page',kind='inventory_page',page=0)])
        self.invoke(final,output)
        self.assertTrue(protocol.assignment_state(self.case.read_records(),self.case.artifacts,final['id'])['ready'])
        answered=self.assign('answered-final',final['id'])
        packet=self.packet(answered)
        self.assertEqual(packet['review_parts']['kind'],'final')
        self.assertEqual(packet['source_deliveries'][0]['request']['id'],'page')
        self.assertEqual(packet['source_deliveries'][0]['status'],'delivered')
        self.record_final(answered)
        self.assertTrue(parts.verify_series(self.case.read_records(), self.case.artifacts, answered['id'])['complete'])

    def test_pending_first_turn_recommends_running_its_existing_assignment(self):
        self.oversized();assignment=self.assign()
        pending=problem_delivery.assess_source_requests(self.case.read_records(),self.case.artifacts)
        item=next(row for row in pending if row['code']=='review_parts_pending')
        self.assertEqual(item['next'][0]['workflow'],'review-run')
        self.assertEqual(item['next'][0]['scope']['assignment_id'],assignment['id'])

    def test_final_cites_an_earlier_read_and_refuses_an_undelivered_record(self):
        import copy
        from research_harness.review_refusal import evidence_arrays
        from research_harness import research_decisions as decisions
        self.oversized();turns,final=self.finish_series()
        packet=self.packet(final)
        carried={entry['search']['id'] for entry in packet['problem_evidence']['openness_searches']}
        search=next(entry['search'] for entry in self.case.expected['openness_searches'] if entry['search']['id'] not in carried)
        scientific=self.final_output()
        for references in evidence_arrays(scientific):
            references[:]=[dict(kind='record',record_kind='literature_search',id=search['id'],digest=digest(search))]
        unavailable=dict(id='never-delivered-search',queries=['bounded sequence'],reason='Not in this review inventory.')
        self.case.put('literature_search',unavailable['id'],unavailable)
        invalid=copy.deepcopy(scientific)
        for references in evidence_arrays(invalid):
            references[:]=[dict(kind='record',record_kind='literature_search',id=unavailable['id'],digest=digest(unavailable))]
        result=self.invoke(final,invalid,attempt='refused-final')
        self.assertEqual(result['output_refusal']['code'],'review_evidence_undelivered')
        self.invoke(final,scientific,attempt='accepted-final')
        saved=self.case.mutate(decisions.record_value_review,dict(scientific,id='series-citation-review',dossier_id=None,assignment_id=final['id']))['result']
        self.assertEqual(self.coverage(saved),[])

    def test_parts_prompt_preserves_base_parameters_and_composes_only_its_kind(self):
        from research_harness import review_parts_prompts as prompts, review_excerpts
        options = dict(version=4, orchestration='problem_creation', excerpts=True,
                       excerpt_rules=review_excerpts.PROTOCOL_V1, common_version=1)
        base = protocol.canonical_prompt('result', **options)
        read = protocol.canonical_prompt('result', review_parts=dict(adapter='problem_result', kind='read'), **options)
        self.assertTrue(read['text'].startswith(base['text']))
        self.assertNotIn(prompts.FINAL_RESULT, read['text'])
        self.assertNotIn(prompts.FINAL_ANSWER, read['text'])
        final = protocol.canonical_prompt('result', review_parts=dict(adapter='problem_result', kind='final'), **options)
        answered = protocol.canonical_prompt('result', review_parts=dict(adapter='problem_result', kind='final', answer=True), **options)
        self.assertNotEqual(final['template_id'], answered['template_id'])
        self.assertTrue(answered['text'].endswith(prompts.FINAL_RESULT))
        self.assertIn(prompts.FINAL_ANSWER, answered['text'])
        self.assertEqual(read['parameters']['base_prompt']['parameters']['version'], 4)

    def test_final_accepts_excerpts_from_its_initialized_session(self):
        self.oversized()
        version = self.case.metadata(91)
        sentence = 'A captured theorem gives the exact bound for every finite graph.'
        self.case.capture(version, pdf_text=sentence)
        turns, final = self.finish_series()
        self.assertIn('reviewer_excerpts', self.packet(turns[0]))
        output = self.final_output()
        output['excerpts'] = [dict(version_id=version, text=sentence, purpose='The exact theorem.')]
        self.record_final(final, output)

    def test_final_passage_request_is_accepted_and_answered(self):
        self.oversized()
        version = self.case.metadata(92)
        sentence = 'A captured theorem gives the exact bound for every finite graph.'
        self.case.capture(version, pdf_text=sentence)
        turns, final = self.finish_series()
        request = dict(id='passage', kind='passage', version_id=version,
                       anchor=sentence[:30], characters=60)
        self.invoke(final, dict(kind='requests', unit_requests=[], source_requests=[request]))
        answered = self.assign('passage-answer', final['id'])
        packet = self.packet(answered)
        self.assertEqual(packet['source_deliveries'][0]['request'], request)
        self.assertEqual(packet['source_deliveries'][0]['status'], 'delivered')
        self.assertIn('reviewer_excerpts', packet)
        self.assertNotIn('scientific_context', packet['continuation'])
        self.record_final(answered)
        self.assertTrue(parts.verify_series(self.case.read_records(), self.case.artifacts, answered['id'])['complete'])

    def test_final_copies_every_units_citations_and_credits_segmented_body(self):
        from research_harness.review_refusal import evidence_arrays
        self.oversized()
        body = self.case.artifacts.put(('λ exact captured evidence.\n' * 6500).encode(), 'text/plain')
        self.case.expected['assessment_evidence'] = [body]
        turns, final = self.finish_series()
        inventory = parts.read_inventory(self.case.artifacts, self.packet(final)['review_parts']['inventory'])
        citations = {digest(citation): citation for row in inventory['units'] for citation in row['citations']}
        self.assertEqual(len(citations), 7)
        output = self.final_output()
        for references in evidence_arrays(output):
            references[:] = list(citations.values())
        review = self.record_final(final, output)
        self.assertEqual(self.coverage(review), [])
        self.assertTrue(parts.verify_series(self.case.read_records(), self.case.artifacts, final['id'])['complete'])
        self.assertTrue(any(entry.get('segment') and not entry.get('item')
                            for turn in turns for entry in self.packet(turn)['evidence']))

    def test_combination_checks_a_passage_with_its_reading_card(self):
        from review_parts_fixtures import make_output
        self.oversized()
        self.case.add_reading_and_computation()
        assignment = self.assign()
        for number in range(20):
            packet = self.packet(assignment)
            if packet['review_parts']['kind'] != 'read':
                break
            self.invoke(assignment)
            assignment = self.assign('passage-read-' + str(number), assignment['id'])
        self.assertEqual(packet['review_parts']['kind'], 'combine')
        passage = next(row for card in packet['review_parts_state']['cards']
                       for row in card.get('carded_units', []))
        combination = dict(units=[passage['handle']], question='What does this passage establish?',
                           view='Check the exact passage.', read=[dict(unit=passage['handle'])])
        self.invoke(assignment, make_output(packet, [combination]))
        check = self.assign('passage-check', assignment['id'])
        packet = self.packet(check)
        self.assertEqual(packet['review_parts']['kind'], 'check')
        self.assertIn(passage['carded_under'], [card['unit'] for card in packet['review_parts_state']['cards']])
        self.invoke(check)
        final = self.assign('passage-final', check['id'])
        review = self.record_final(final)
        self.assertEqual(self.coverage(review), [])
        self.assertTrue(parts.verify_series(self.case.read_records(), self.case.artifacts, final['id'])['complete'])
