"""Observed legacy and fresh series reach an evidenced context reassessment."""

import copy
import json
from unittest.mock import patch

from codex_route_fixtures import (build_codex_response, invoke_codex, limit_codex_input,
                                  probe_codex_route, record_codex_route)
from research_harness import review_parts as parts, review_problem_parts as legacy
from research_harness import research_decisions as decisions, review_protocol as protocol
from research_harness.evidence import digest
from review_parts_fixtures import PartsResultCase, make_output
from strategy_fixtures import StrategyCase
import test_research_context_repair_integration as repair_fixtures


class ResultRepairJourneyTests(PartsResultCase):
    def contaminate(self, predecessor):
        evidence = self.case.supporting
        self.case.mutate(protocol.record_context_event, dict(id='contamination', assignment_id=predecessor['id'],
            kind='contamination', source='author_history', reason='Observed author context.', evidence=[evidence]))
        record_codex_route(self.case, 'repair-route', output_tokens=128000)
        return dict(assignment_id=predecessor['id'], event_ids=['contamination'], probe_id='probe-repair-route',
                    reason='Reassess the retained scientific history.', evidence=[evidence])

    def repair_assignment(self, predecessor, binding, identifier='repair'):
        return self.case.mutate(protocol.record_assignment, dict(id=identifier, route_id='repair-route',
            dossier_id=None, role='result', reviewer_id='replacement', author_id=predecessor['author_id'],
            context=dict(context_repair=binding)))['result']

    def continue_repair(self, assignment, identifier):
        return self.case.mutate(protocol.record_assignment, dict(id=identifier, route_id='repair-route',
            dossier_id=None, role='result', reviewer_id='replacement', author_id=assignment['author_id'],
            context=dict(prior_assignment_id=assignment['id'])))['result']

    def finish_repair(self, first):
        assignment = first
        for number in range(40):
            packet = self.packet(assignment)
            if packet[parts.KEY]['kind'] == 'final':
                return assignment
            result = self.invoke(assignment, make_output(packet))
            self.assertNotIn('output_refusal', result)
            assignment = self.continue_repair(assignment, 'repair-turn-' + str(number))
        self.fail('The repair series did not finish')

    def test_three_legacy_parts_without_final_enter_fresh_repair(self):
        # Derive the limit from the rendered ordinary request, with enough room for the new frame.
        ordinary = protocol.build_packet(self.case.read_records(), self.case.artifacts, None, 'result', 'first')
        prompt = protocol.canonical_prompt('result', orchestration='problem_creation', excerpts=True,
            review_parts=dict(adapter='context_repair', kind='read'))
        rendered = protocol.transport.request_body(self.case.read_records()['review_route']['route'],
            prompt['text'], ordinary, [])
        measured = legacy._measure(self.case.read_records()['review_route']['route'], rendered)
        self.oversized(count=18, characters=measured * 3, limit=measured * 12)
        turns = []
        with patch.object(parts, 'prepare_packet', side_effect=lambda records, artifacts, value, route,
                          packet, prompt, history, **kwargs: packet), \
                patch.object(legacy, 'prepare_packet', side_effect=legacy._prepare_legacy_packet):
            prior = None
            for number in range(3):
                assignment = self.assign('legacy-' + str(number), prior)
                self.assertEqual(self.packet(assignment)[legacy.KEY]['stage'], 'part')
                output = super(PartsResultCase, self).output(assignment)
                self.invoke(assignment, output)
                turns.append(assignment)
                prior = assignment['id']
        binding = self.contaminate(turns[-1])
        repaired = self.repair_assignment(turns[-1], binding)
        packet = self.packet(repaired)
        self.assertEqual(packet[parts.KEY]['adapter'], 'context_repair')
        self.assertEqual(repaired['history'], [])
        logical = parts.read_logical_packet(self.case.artifacts, packet[parts.KEY])
        self.assertEqual(logical['context_repair']['assignment_id'], turns[-1]['id'])
        self.assertIn('predecessor_binding', logical['context_repair'])
        inventory = parts.read_inventory(self.case.artifacts, packet[parts.KEY]['inventory'])
        search = self.case.expected['openness_searches'][0]
        row = next(row for row in inventory['units'] if row['digest'] == digest(search))
        self.assertGreaterEqual(len(row['occurrences']), 2)
        units = parts.enumerate_units(logical, 'context_repair')
        texts = [body['content'] for unit in units for body in unit['bodies']]
        for ancestor in turns:
            state = protocol.assignment_state(self.case.read_records(), self.case.artifacts, ancestor['id'])
            self.assertIn(parts._dump(state['output']), texts)
        final = self.finish_repair(repaired)
        output = self.final_output()
        output['reassessment'] = dict(assignment_id=turns[-1]['id'], findings=[])
        review = self.record_final(final, output)
        self.assertEqual(self.coverage(review), [])
        self.assertTrue(parts.verify_series(self.case.read_records(), self.case.artifacts, final['id'])['complete'])

    def test_contaminated_v2_intermediate_turns_are_scientific_history(self):
        ordinary = protocol.build_packet(self.case.read_records(), self.case.artifacts, None, 'result', 'first')
        route = self.case.read_records()['review_route']['route']
        prompt = protocol.canonical_prompt('result', orchestration='problem_creation', excerpts=True,
            review_parts=dict(adapter='context_repair', kind='read'))
        measured = legacy._measure(route, protocol.transport.request_body(
            route, prompt['text'], ordinary, []))
        self.oversized(count=12, characters=measured * 3, limit=measured * 12)
        first = self.assign('fresh-original')
        self.invoke(first)
        second = self.assign('fresh-second', first['id'])
        self.invoke(second)
        binding = self.contaminate(second)
        repaired = self.repair_assignment(second, binding)
        packet = self.packet(repaired)
        self.assertEqual(packet[parts.KEY]['adapter'], 'context_repair')
        logical = parts.read_logical_packet(self.case.artifacts, packet[parts.KEY])
        self.assertEqual({turn['assignment_id'] for turn in logical['scientific_history']}
                         & {first['id'], second['id']}, {first['id'], second['id']})
        final = self.finish_repair(repaired)
        output = self.final_output()
        output['reassessment'] = dict(assignment_id=second['id'], findings=[])
        self.record_final(final, output)
        self.assertTrue(parts.verify_series(self.case.read_records(), self.case.artifacts, final['id'])['complete'])


class BarRepairJourneyTests(StrategyCase):
    expose = repair_fixtures.ContextRepairIntegrationTests.expose
    repair_payload = repair_fixtures.ContextRepairIntegrationTests.repair_payload
    final_response = repair_fixtures.ContextRepairIntegrationTests.final_response

    def setUp(self):
        super().setUp()
        self.setup_dossier()
        record_codex_route(self, output_tokens=128000)
        self.objection = dict(id='counterexample', claim='The broader inference is unsupported.',
            reason='The same observation fits both mechanisms.', evidence=[self.evidence],
            resolution_condition='Supply a deciding observation.')
        self.original = self.review_response('bar', status='insufficient', objections=[self.objection])
        assignment = self.mutate(protocol.record_assignment, dict(id='assignment-bar-a', route_id='route',
            dossier_id='dossier-1', role='bar', reviewer_id='reviewer-a', author_id='author', context={}))['result']
        invoke_codex(self, assignment['id'], self.original)
        self.mutate(decisions.record_value_review, dict(self.original, id='bar-a', dossier_id='dossier-1',
                                                       assignment_id=assignment['id']))
        self.expose()
        probe_codex_route(self, 'repair-probe')

    def assign_repair(self, identifier='repair-a'):
        payload = self.repair_payload()
        payload['id'] = identifier
        return self.mutate(protocol.record_assignment, payload)['result']

    def packet(self, assignment):
        return json.loads(self.artifacts.read(assignment['packet']))

    def test_fitting_bar_repair_stays_ordinary(self):
        assignment = self.assign_repair()
        self.assertNotIn(parts.KEY, self.packet(assignment))
        self.assertTrue(protocol._prompt_matches(assignment))

    def test_over_cap_attempt_does_not_release_a_pending_retry(self):
        first = self.assign_repair()
        def incomplete(route, request, credential):
            response = build_codex_response(route, request, {})
            response['status'] = 'incomplete'
            response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
            return response
        with patch.object(protocol.transport, 'send_request', side_effect=incomplete):
            protocol.invoke_assignment(self.store, dict(id='over-cap', assignment_id=first['id']),
                credential='fixture', expected_revision=self.store.revision, request_id='over-cap')
        with patch.object(protocol.transport, 'send_request', side_effect=SystemExit('Worker interrupted')):
            with self.assertRaises(SystemExit):
                protocol.invoke_assignment(self.store, dict(id='pending-retry', assignment_id=first['id']),
                    credential='fixture', expected_revision=self.store.revision, request_id='pending-retry')
        self.error('review_resampling_forbidden', lambda: self.assign_repair('forbidden-replacement'))

    def test_over_cap_ordinary_repair_continuation_enters_parts_with_its_history(self):
        self.continue_over_cap_repair(completed=False)

    def test_refused_capped_ordinary_repair_continuation_enters_parts_with_its_history(self):
        self.continue_over_cap_repair(completed=True)

    def continue_over_cap_repair(self, *, completed):
        first = self.assign_repair()
        request = self.review_response('bar', status='unresolved')
        request['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
        invoke_codex(self, first['id'], request)
        payload = self.repair_payload()
        payload.update(id='ordinary-child', context=dict(prior_assignment_id=first['id']))
        child = self.mutate(protocol.record_assignment, payload)['result']
        self.assertNotIn(parts.KEY, self.packet(child))
        def incomplete(route, request, credential):
            response = build_codex_response(route, request, dict(stage='bar') if completed else {})
            if not completed:
                response['status'] = 'incomplete'
            response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
            return response
        with patch.object(protocol.transport, 'send_request', side_effect=incomplete):
            protocol.invoke_assignment(self.store, dict(id='child-over-cap', assignment_id=child['id']),
                credential='fixture', expected_revision=self.store.revision, request_id='child-over-cap')
        if completed:
            from research_harness.errors import ResearchError
            with self.assertRaises(ResearchError) as refused:
                self.mutate(decisions.record_value_review, dict(stage='bar', id='capped-child-final',
                    dossier_id='dossier-1', assignment_id=child['id']))
            self.assertEqual(refused.exception.code, 'invalid_value_review')
        assignment = self.mutate(protocol.record_assignment, dict(payload, id='child-replacement'))['result']
        packet = self.packet(assignment)
        self.assertEqual(packet[parts.KEY]['adapter'], 'context_repair')
        logical = parts.read_logical_packet(self.artifacts, packet[parts.KEY])
        self.assertEqual({turn['assignment_id'] for turn in logical['scientific_history']},
                         {'assignment-bar-a', first['id']})
        self.assertEqual(logical['historical_reviews'], self.packet(first)['historical_reviews'])
        self.assertEqual(logical['context_repair']['predecessor_binding']['assignment_id'], 'assignment-bar-a')
        for number in range(30):
            packet = self.packet(assignment)
            if packet[parts.KEY]['kind'] == 'final':
                break
            invoke_codex(self, assignment['id'], make_output(packet))
            assignment = self.mutate(protocol.record_assignment, dict(payload,
                id='child-replacement-' + str(number), context=dict(prior_assignment_id=assignment['id'])))['result']
        self.assertEqual(packet[parts.KEY]['kind'], 'final')
        output = self.final_response()
        inventory = parts.read_inventory(self.artifacts, packet[parts.KEY]['inventory'])
        citations = {digest(citation): citation for row in inventory['units'] for citation in row['citations']}
        from research_harness.review_refusal import evidence_arrays
        for evidence in evidence_arrays(output):
            evidence[:] = list(citations.values())
        invoke_codex(self, assignment['id'], output)
        self.mutate(decisions.record_value_review, dict(output, id='continued-repair-final',
            dossier_id='dossier-1', assignment_id=assignment['id']))
        self.assertTrue(parts.verify_series(self.store.snapshot()['records'], self.artifacts, assignment['id'])['complete'])

    def test_reported_output_cap_replaces_one_refused_ordinary_attempt(self):
        first = self.assign_repair()
        def refused_over_cap(route, request, credential):
            response = build_codex_response(route, request, dict(stage='bar'))
            response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
            return response
        with patch.object(protocol.transport, 'send_request', side_effect=refused_over_cap):
            result = protocol.invoke_assignment(self.store, dict(id='refused-over-cap', assignment_id=first['id']),
                credential='fixture', expected_revision=self.store.revision, request_id='refused-over-cap')
        self.assertEqual(result['result']['status'], 'completed')
        from research_harness.errors import ResearchError
        with self.assertRaises(ResearchError):
            self.mutate(decisions.record_value_review, dict(stage='bar', id='refused-final',
                dossier_id='dossier-1', assignment_id=first['id']))
        self.assertIn('refused-over-cap', self.store.snapshot()['records']['review_output_refusal'])
        replacement = self.assign_repair('cap-replacement')
        self.assertEqual(self.packet(replacement)[parts.KEY]['adapter'], 'context_repair')

    def test_over_cap_repair_replacement_uses_parts_and_cites_all_units(self):
        first = self.assign_repair()
        self.assertNotIn(parts.KEY, self.packet(first))
        def incomplete(route, request, credential):
            response = build_codex_response(route, request, {})
            response['status'] = 'incomplete'
            response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
            return response
        with patch.object(protocol.transport, 'send_request', side_effect=incomplete):
            protocol.invoke_assignment(self.store, dict(id='over-cap', assignment_id=first['id']),
                credential='fixture', expected_revision=self.store.revision, request_id='over-cap')
        assignment = self.assign_repair('replacement')
        self.assertEqual(assignment['slot_id'], first['slot_id'])
        self.assertEqual(self.packet(assignment)[parts.KEY]['adapter'], 'context_repair')
        for number in range(30):
            packet = self.packet(assignment)
            self.assertTrue(protocol._prompt_matches(assignment))
            if packet[parts.KEY]['kind'] == 'final':
                break
            invoke_codex(self, assignment['id'], make_output(packet))
            payload = self.repair_payload()
            payload.update(id='replacement-' + str(number), context=dict(prior_assignment_id=assignment['id']))
            assignment = self.mutate(protocol.record_assignment, payload)['result']
        self.assertEqual(packet[parts.KEY]['kind'], 'final')
        series_id = packet[parts.KEY]['series_assignment_id']
        request = self.review_response('bar', status='unresolved')
        request['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
        response = invoke_codex(self, assignment['id'], request)
        self.assertNotIn('output_refusal', response)
        state = protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, assignment['id'])
        self.assertTrue(state['ready'], state['obligations'])
        payload = self.repair_payload()
        payload.update(id='answered-repair', context=dict(prior_assignment_id=assignment['id']))
        assignment = self.mutate(protocol.record_assignment, payload)['result']
        packet = self.packet(assignment)
        self.assertEqual(packet[parts.KEY]['kind'], 'final')
        self.assertEqual(packet[parts.KEY]['series_assignment_id'], series_id)
        self.assertEqual(packet['source_deliveries'][0]['status'], 'delivered')
        self.assertEqual(packet['historical_reviews'][0]['payload'], self.packet(first)['historical_reviews'][0]['payload'])
        output = self.final_response()
        invalid = copy.deepcopy(output)
        invalid['reassessment']['findings'] = []
        result = invoke_codex(self, assignment['id'], invalid, 'missing-disposition')
        self.assertEqual(result['output_refusal']['code'], 'review_reassessment_incomplete')
        inventory = parts.read_inventory(self.artifacts, packet[parts.KEY]['inventory'])
        citations = {digest(citation): citation for row in inventory['units'] for citation in row['citations']}
        self.assertTrue(citations)
        from research_harness.review_refusal import evidence_arrays
        for evidence in evidence_arrays(output):
            evidence[:] = list(citations.values())
        invoke_codex(self, assignment['id'], output, 'valid-final')
        self.mutate(decisions.record_value_review, dict(output, id='repaired-final', dossier_id='dossier-1',
                                                       assignment_id=assignment['id']))
        self.assertTrue(parts.verify_series(self.store.snapshot()['records'], self.artifacts, assignment['id'])['complete'])


class AdjudicatorRepairJourneyTests(StrategyCase):
    def test_registered_adjudicator_can_continue_a_repair_with_ordinary_base_prompt(self):
        import test_research_problem_adjudication_contract as fixtures
        from research_harness import review_parts_roles as roles, review_parts_problem_result as result
        from research_harness import review_parts_prompts as prompts
        case = fixtures.ProblemAdjudicationContractTests(methodName='runTest')
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.prepare_reviews(result_reviewer='first')
        record_codex_route(case, 'fresh-route', output_tokens=128000)
        context = dict(review_ids=['review-result-first', 'review-result-second'],
                       objection_id='unsupported-implication')
        packet = protocol.build_packet(case.read_records(), case.artifacts, None, 'adjudicator',
                                       'focused-third', **context)
        route = case.read_records()['review_route']['fresh-route']
        prompt = protocol.canonical_prompt('adjudicator', orchestration='problem_creation')
        read_prompt = protocol.canonical_prompt('adjudicator', orchestration='problem_creation',
            review_parts=dict(adapter='context_repair', kind='read'))
        measured = legacy._measure(route, protocol.transport.request_body(route, read_prompt['text'], packet, []))
        # The registry stand-in supplies only the sibling lane's interface; all
        # packets, observations, repairs and final validation use production code.
        adapter = dict(result.ADAPTER,
            applies=lambda value, packet: value.get('role') == 'adjudicator' and 'context_repair' not in packet,
            enumerate_units=lambda packet: [source for source in result.list_units(packet)
                if source['occurrences'][0]['path'] == ['problem_evidence', 'openness_searches']],
            citations=lambda source: [dict(citation, digest=digest(source['item']['search']))
                                      for citation in result.list_citations(source)],
            final_prompt=prompts.FINAL_ADJUDICATOR, source_requests=False)
        searches = []
        for number in range(6):
            search = dict(id='adjudication-search-' + str(number), queries=['exact implication'],
                          reason='x' * measured)
            case.put('literature_search', search['id'], search)
            searches.append(dict(search=search, sources=[]))
        # The fixture's description functions read this explicit scientific projection.
        original_describe = protocol.add_problem_evidence
        def add_evidence(records, artifacts, target):
            original_describe(records, artifacts, target)
            if target['role'] == 'adjudicator':
                target['problem_evidence']['openness_searches'].extend(copy.deepcopy(searches))
                target['problem_evidence_digest'] = digest(target['problem_evidence'])
        limit_codex_input(self, measured * 4)
        with patch.dict(roles.ADAPTERS, fixture_adjudicator=adapter), \
                patch.object(protocol, 'add_problem_evidence', side_effect=add_evidence):
            original = case.mutate(protocol.record_assignment, dict(id='original-focused', route_id='fresh-route',
                dossier_id=None, role='adjudicator', reviewer_id='focused-third',
                author_id=case.read_records()['workspace']['problem']['authors'][0], context=context))['result']
            packet = json.loads(case.artifacts.read(original['packet']))
            self.assertEqual(packet[parts.KEY]['kind'], 'read')
            invoke_codex(case, original['id'], make_output(packet))
            case.mutate(protocol.record_context_event, dict(id='focused-exposure', assignment_id=original['id'],
                kind='contamination', source='author_history', reason='Observed author input.', evidence=[case.supporting]))
            record_codex_route(case, 'new-focused-route', output_tokens=128000)
            value = dict(id='focused-repair', route_id='new-focused-route', dossier_id=None,
                role='adjudicator', reviewer_id='fresh-focused', author_id=original['author_id'],
                context=dict(context_repair=dict(assignment_id=original['id'], event_ids=['focused-exposure'],
                    probe_id='probe-new-focused-route', reason='Reassess exact focused evidence.', evidence=[case.supporting])))
            repaired = case.mutate(protocol.record_assignment, value)['result']
            for number in range(30):
                packet = json.loads(case.artifacts.read(repaired['packet']))
                self.assertTrue(repaired['prompt']['text'].startswith(prompt['text']))
                self.assertTrue(protocol._prompt_matches(repaired))
                if packet[parts.KEY]['kind'] == 'final':
                    break
                response = invoke_codex(case, repaired['id'], make_output(packet))
                self.assertNotIn('output_refusal', response)
                repaired = case.mutate(protocol.record_assignment, dict(value, id='focused-repair-' + str(number),
                    context=dict(prior_assignment_id=repaired['id'])))['result']
            self.assertEqual(packet[parts.KEY]['kind'], 'final')
            refused = invoke_codex(case, repaired['id'], dict(kind='requests', unit_requests=[],
                source_requests=[dict(id='page', kind='inventory_page', page=0)]), 'focused-source-refused')
            self.assertIn(refused['output_refusal']['code'],
                          ('invalid_value_review', 'invalid_review_source_request'))
            inventory = parts.read_inventory(case.artifacts, packet[parts.KEY]['inventory'])
            citations = {digest(citation): citation for row in inventory['units'] for citation in row['citations']}
            output = dict(objection_id='unsupported-implication', disposition='unresolved',
                reason='The supplied evidence leaves this exact implication unresolved.',
                evidence=list(citations.values()), correction_admissible=None,
                reassessment=dict(assignment_id=original['id'], findings=[]))
            response = invoke_codex(case, repaired['id'], output)
            self.assertNotIn('output_refusal', response)
            case.mutate(protocol.record_adjudication, dict(output, id='repaired-adjudication', assignment_id=repaired['id']))
            self.assertTrue(parts.verify_series(case.read_records(), case.artifacts, repaired['id'])['complete'])
