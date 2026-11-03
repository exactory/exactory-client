"""Study adapters exercise actual fresh requests and the scientific gate readers."""

import copy
import json
import unittest
from unittest.mock import patch

from codex_route_fixtures import invoke_codex, limit_codex_input, record_codex_route
from development_fixtures import DevelopmentCase
from review_parts_fixtures import make_output
from research_harness import development, direction_reviews, research_decisions
from research_harness import review_parts as parts, review_protocol as protocol
from research_harness import review_parts_prompts as prompts, verification
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from research_harness.review_problem_parts import _measure
from research_harness.review_bodies import read_history
from strategy_fixtures import StrategyCase
import test_research_verification as verification_fixtures


class StudyAdapterTests(unittest.TestCase):
    def test_omitted_original_stays_in_frame_while_its_extraction_is_delivered(self):
        original = dict(path='original', sha256='a' * 64, size=1, media_type='application/pdf')
        extraction = dict(path='extraction', sha256='b' * 64, size=1, media_type='text/plain')
        omitted = dict(artifact=original, content='', encoding='omitted')
        packet = dict(role='verification', paper=dict(kind='verification', sources=dict(
            work=dict(work=dict(original=original, text=extraction)))), evidence=[
                omitted, dict(artifact=extraction, content='x', encoding='utf-8')])
        units = parts.enumerate_units(packet, 'study_verification')
        self.assertEqual([body['artifact'] for body in units[0]['bodies']], [extraction])
        self.assertEqual(parts.build_frame(packet, units)['evidence'], [omitted])
        readiness = dict(readiness=dict(inputs=dict(sources=packet['paper']['sources'])),
                         evidence=packet['evidence'])
        units = parts.enumerate_units(readiness, 'study_readiness')
        self.assertEqual(units[0]['citations'], [extraction])

    def test_persisted_inventory_keeps_citations_bodies_and_occurrences(self):
        from research_harness import review_parts_study as study
        artifact = dict(path='body', sha256='a' * 64, size=1, media_type='text/plain')
        reference = dict(kind='result', execution_id='run', output_id='out', artifact=artifact,
                         locator=dict(kind='json', pointer='/value', value=1))
        plan = dict(id='plan', digest='b' * 64, payload=dict(evidence=[reference],
                    evidence_requirements=[dict(id='needed', kind='result')]))
        packet = dict(role='result', dossier={}, readiness=dict(kind='readiness',
            inputs=dict(candidate=dict(digest='candidate'),
                        synthesis=dict(sections=dict(standards='fixed')),
                        plan=plan, checkpoint=dict(cycle_id='plan', plan_digest='b' * 64),
                        branches=[dict(id='branch', evidence=[reference])]),
            execution_observations=dict(run=dict(artifact=artifact))),
            evidence=[dict(artifact=artifact, content='x', encoding='utf-8')])
        inventory = parts.build_inventory(packet, 'study_readiness')
        persisted = json.loads(json.dumps(packet, sort_keys=True))
        self.assertEqual(parts.build_inventory(persisted, 'study_readiness', inventory), inventory)
        frame = parts.build_frame(packet, parts.enumerate_units(packet, 'study_readiness'))
        self.assertEqual(frame['readiness']['inputs']['candidate'],
                         packet['readiness']['inputs']['candidate'])
        self.assertEqual(frame['readiness']['inputs']['synthesis'],
                         packet['readiness']['inputs']['synthesis'])
        self.assertEqual(frame['readiness']['inputs']['branches'], [])
        citations = [ref for row in inventory['units'] for ref in row['citations']]
        self.assertIn(reference, citations)
        self.assertNotIn(dict(id='needed', kind='result'), citations)
        self.assertNotEqual(study.compute_readiness_key(packet), study.compute_readiness_key(
            dict(packet, readiness=dict(packet['readiness'], inputs=dict(
                packet['readiness']['inputs'],
                candidate=dict(digest='changed'))))))

    def test_bearing_credit_requires_the_exact_reference_and_its_digest_token(self):
        from research_harness import review_parts_study as study
        reference = dict(kind='source', link=dict(version_id='v', source_id='s'))
        name = digest(reference)
        card = dict(citations=[reference], bearing=[dict(target='validity', reason=name)])
        final = {parts.KEY: dict(targets=[dict(id='validity')]),
                 parts.STATE_KEY: dict(cards=[card])}
        series = dict(packets=[final])
        self.assertEqual(study.list_bearing_references(series, [reference]), [reference])
        for mutate in (lambda: card.update(citations=[]),
                       lambda: card['bearing'][0].update(reason=name + 'a'),
                       lambda: card['bearing'][0].update(target='unknown')):
            changed = copy.deepcopy(series)
            mutate()
            self.assertEqual(study.list_bearing_references(series, [reference]), [])
            card.clear()
            card.update(changed['packets'][0][parts.STATE_KEY]['cards'][0])

    def test_actual_reader_recognizes_only_bound_verification_parts(self):
        from research_harness.review_input_projection import _require_review_packet
        header = dict(protocol=parts.PROTOCOL, adapter='study_verification', kind='read',
                      series_assignment_id='a', turn=1, inventory_digest='b' * 64)
        packet = dict(protocol=protocol.PROTOCOL, role='verification', reviewer_id='verifier',
                      dossier_id=None, paper=dict(kind='verification', task_digest='a' * 64),
                      evidence=[], review_parts=header)
        packet['digest'] = digest(packet)
        assignment = dict(id='a', role='verification', reviewer_id='verifier', dossier_id=None,
                          packet_digest=packet['digest'], review_parts=copy.deepcopy(header),
                          context=dict(verification_task_id='a' * 64))
        _require_review_packet(packet, [assignment])
        for change in (dict(role='manuscript'), dict(dossier_id='dossier'),
                       dict(review_parts=dict(header, adapter='problem_result')),
                       dict(review_parts=dict(header, kind='combine')),
                       dict(review_parts=dict(header, protocol='forged')),
                       dict(review_parts=dict(header, series_assignment_id='another-series')),
                       dict(paper=dict(kind='verification', task_digest='c' * 64))):
            with self.subTest(change=change), self.assertRaises(ResearchError):
                changed = dict(packet, **change)
                changed.pop('digest')
                changed['digest'] = digest(changed)
                _require_review_packet(changed, [dict(assignment,
                                                     packet_digest=changed['digest'])])
        ordinary = {key: value for key, value in packet.items() if key != parts.KEY}
        with self.assertRaises(ResearchError):
            _require_review_packet(ordinary)


class StudyPartsCase(unittest.TestCase):
    role = None
    adapter = None

    def records(self):
        return self.case.store.snapshot()['records']

    def packet(self, assignment):
        return json.loads(self.case.artifacts.read(assignment['packet']))

    def assign(self, identifier='first', prior=None, **changes):
        context = copy.deepcopy(self.context)
        if prior is not None:
            context['prior_assignment_id'] = prior
        value = dict(id=identifier, route_id='route',
                     dossier_id=getattr(self, 'dossier_id', None), role=self.role,
                     reviewer_id='independent-verifier', author_id='external-author',
                     context=context)
        value.update(changes)
        return self.case.mutate(protocol.record_assignment, value)['result']

    def measure(self, packet, kind=None):
        route = self.records()['review_route']['route']
        prompt = protocol.canonical_prompt(self.role, excerpts=self.role == 'result')['text']
        if kind:
            prompt += '\n\n' + prompts.PARTS_COMMON + '\n\n' + prompts.PROMPTS[kind]
            if kind == 'final':
                prompt += '\n\n' + (prompts.FINAL_VERIFICATION if self.role == 'verification'
                                       else prompts.FINAL_READINESS)
        request = protocol.transport.request_body(route, prompt, packet, [], order=())
        return _measure(route, request)

    def enlarge(self, collection):
        # Size every fixture from the rendered prompts, including the final adapter text.
        prompt_size = max(self.measure(self.logical, kind) for kind in parts.KINDS)
        for index in range(8):
            collection['extra-' + str(index)] = dict(id='extra-' + str(index),
                evidence=copy.deepcopy(self.references),
                reason='λ evidence. ' * (prompt_size // 24))
        self.logical.pop('digest', None)
        self.logical['digest'] = digest(self.logical)
        self.limit = self.measure(self.logical) // 2
        limit_codex_input(self, self.limit)

    def install_packet(self):
        original = protocol.build_packet
        self.original_build_packet = original

        def build_packet(records, artifacts, dossier_id, role, reviewer_id, **context):
            if role == self.role and 'prior_assignment_id' not in context:
                packet = copy.deepcopy(self.logical)
                packet.update(reviewer_id=reviewer_id, dossier_id=dossier_id)
                packet.pop('digest', None)
                packet['digest'] = digest(packet)
                return packet
            return original(records, artifacts, dossier_id, role, reviewer_id, **context)

        patcher = patch.object(protocol, 'build_packet', side_effect=build_packet)
        patcher.start()
        self.addCleanup(patcher.stop)

    def invoke(self, assignment, output=None, attempt_id=None):
        packet = self.packet(assignment)
        output = make_output(packet) if output is None else output
        return invoke_codex(self.case, assignment['id'], output, attempt_id)

    def finish(self, first=None, *, bearings=False):
        assignment = first or self.assign()
        series_id = self.packet(assignment).get(parts.KEY, {}).get('series_assignment_id')
        turns = []
        while parts.find_kind(self.packet(assignment)) != 'final':
            packet = self.packet(assignment)
            self.assertIsNotNone(parts.find_kind(packet), 'Oversized study did not enter parts v2')
            self.assertEqual(packet[parts.KEY]['series_assignment_id'], series_id)
            output = make_output(packet)
            if bearings and parts.find_kind(packet) == 'read':
                rows = {row['handle']: row for row in packet[parts.KEY]['units']}
                for card in output['cards']:
                    names = [digest(ref) for ref in rows[card['unit']]['citations']]
                    if names:
                        card['bearing'] = [dict(target='validity', relation='context',
                                                reason='Exact references: ' + ' '.join(names))]
                if any(card['bearing'] for card in output['cards']):
                    output['unborne'] = [entry for entry in output['unborne']
                                         if entry['target'] != 'validity']
            self.invoke(assignment, output)
            turns.append(assignment)
            self.assertLess(len(turns), 40)
            assignment = self.assign('turn-' + str(len(turns) + 1), assignment['id'])
        return turns, assignment

    def assert_series_requests(self, turns):
        for assignment in turns:
            request = protocol.build_assignment_request(self.records(), self.case.artifacts,
                                                        assignment)
            self.assertEqual(read_history(self.case.artifacts, assignment), [])
            self.assertEqual(len(protocol.transport.logical_messages(request)), 1)
            self.assertLessEqual(_measure(self.records()['review_route']['route'], request),
                                 self.limit)


class VerificationPartsTests(StudyPartsCase):
    role = 'verification'
    adapter = 'study_verification'

    def setUp(self):
        self.case = verification_fixtures.ResearchVerificationTests()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        record_codex_route(self.case, output_tokens=128000)
        self.task = self.case.mutate(verification.record_task, {'task': self.case.task})['result']
        self.context = dict(verification_task_id=self.task['digest'])
        self.logical = protocol.build_packet(self.records(), self.case.artifacts, None, self.role,
                                            'independent-verifier', **self.context)
        self.references = [self.case.linked]
        patcher = patch.object(verification, 'review_packet', side_effect=lambda *args:
                               copy.deepcopy(self.logical['paper']))
        patcher.start()
        self.addCleanup(patcher.stop)

    def enlarge_verification(self):
        self.enlarge(self.logical['paper']['sources']['work'])

    def bind_value(self, assignment, output):
        return dict(task_digest=self.task['digest'], assessment=dict(
            assignment_id=assignment['id'], assessor='independent-verifier',
            checks=output['checks']))

    def final_output(self, final):
        inventory = parts.read_inventory(self.case.artifacts,
                                         self.packet(final)[parts.KEY]['inventory'])
        citations = {digest(ref): ref for row in inventory['units'] for ref in row['citations']}
        self.assertIn(digest(self.case.linked), citations)
        return dict(verdict=self.case.verdict(), checks=[dict(
            dimension=kind, reason='Exact full-read evidence.',
            evidence=list(citations.values())) for kind in ('soundness', 'novelty', 'impact')])

    def test_oversized_keyed_sources_complete_and_bind_only_the_final(self):
        self.enlarge_verification()
        turns, final = self.finish()
        output = self.final_output(final)
        self.invoke(final, output)
        result = parts.verify_series(self.records(), self.case.artifacts, final['id'])
        self.assertTrue(result['complete'], result['issues'])
        self.assert_series_requests(turns + [final])
        self.assertEqual({row['id'] for row in self.packet(turns[0])[parts.KEY]['targets']},
                         {'soundness', 'novelty', 'impact'})
        for key, expected in self.logical['paper']['sources'].items():
            self.assertTrue(parts.covers_items(result['packets'], {key: expected}, key,
                result['delivery']['inventory'], path=['paper', 'sources', key]))
        manifests = [row for packet in result['packets']
                     for row in packet[parts.KEY]['delivered_items']]
        self.assertTrue(all('key' in row and 'index' not in row for row in manifests))
        value = self.bind_value(final, output)
        self.assertTrue(verification.require_verdict_assignment(
            self.records(), self.case.artifacts, value, output['verdict'])['ready'])
        records = copy.deepcopy(self.records())
        records['review_attempt'].pop('attempt-' + turns[0]['id'])
        with self.assertRaises(ResearchError):
            verification.require_verdict_assignment(records, self.case.artifacts,
                                                    value, output['verdict'])
        self.assertEqual(self.packet(final)['paper']['task_digest'], self.task['digest'])

    def test_fitting_packet_remains_ordinary_and_cannot_gain_a_continuation(self):
        limit_codex_input(self, self.measure(self.logical) * 2)
        assignment = self.assign()
        self.assertIsNone(parts.find_kind(self.packet(assignment)))
        output = dict(verdict=self.case.verdict(), checks=[dict(
            dimension=kind, reason='Read the target.',
            evidence=self.references) for kind in ('soundness', 'novelty', 'impact')])
        self.invoke(assignment, output)
        self.assertTrue(verification.require_verdict_assignment(
            self.records(), self.case.artifacts,
            self.bind_value(assignment, output), output['verdict'])['ready'])
        with self.assertRaises(ResearchError):
            self.assign('ordinary-continuation', assignment['id'])

    def test_continuation_binds_reviewer_route_author_task_and_latest_turn(self):
        self.enlarge_verification()
        first = self.assign()
        self.invoke(first)
        for changes in (dict(reviewer_id='another-verifier'), dict(author_id='another-author'),
                        dict(context=dict(verification_task_id='wrong',
                                          prior_assignment_id=first['id']))):
            with self.subTest(changes=changes), self.assertRaises(ResearchError):
                self.assign('wrong-parent', first['id'], **changes)
        second = self.assign('second', first['id'])
        self.assertEqual(second['session_id'], first['session_id'])
        self.assertEqual(second['history_assignment_id'], first['id'])
        with self.assertRaises(ResearchError):
            self.assign('fork', first['id'])

    def test_verification_refuses_malformed_read_and_final_outputs(self):
        self.enlarge_verification()
        first = self.assign()
        packet = self.packet(first)
        self.assertEqual(parts.find_kind(packet), 'read')
        invalid = make_output(packet)
        invalid['cards'] = []
        response = self.invoke(first, invalid, 'invalid-read')
        self.assertEqual(response['output_refusal']['code'], 'invalid_value_review')
        self.invoke(first, make_output(packet), 'valid-read')
        turns, final = self.finish(self.assign('second', first['id']))
        response = self.invoke(final, dict(verdict=self.case.verdict()), 'invalid-final')
        self.assertIn('output_refusal', response)
        self.invoke(final, self.final_output(final), 'valid-final')
        self.assertTrue(parts.verify_series(self.records(), self.case.artifacts,
                                            final['id'])['complete'])

    def test_verification_refuses_an_unbindable_prediction_shape(self):
        self.enlarge_verification()
        turns, final = self.finish()
        output = self.final_output(final)
        output['verdict']['prediction'] = []
        response = self.invoke(final, output, 'invalid-prediction')
        self.assertEqual(response['output_refusal']['code'], 'prediction_required')
        self.assertFalse(protocol.assignment_state(self.records(), self.case.artifacts,
                                                   final['id'])['ready'])


class ReadinessPartsTests(StudyPartsCase):
    role = 'result'
    adapter = 'study_readiness'

    def setUp(self):
        self.case = DevelopmentCase()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.case.prepared_study()
        plan, self.execution = self.case.run_cycle()
        self.case.mutate(development.assess_cycle, self.case.assessment(plan, self.execution))
        self.case.save_checkpoint()
        record_codex_route(self.case, output_tokens=128000)
        self.context = {}
        from research_harness import bar_sources
        from research_harness.operations import prepared_mutation
        self.dossier_id = 'study-dossier'
        intent = dict(id='intent', payload=dict(authors=['external-author']))
        dossier = dict(id=self.dossier_id, payload=dict(intent_id='intent'))
        def prepare_fixture(store, value, **identity):
            return prepared_mutation(store, 'test.study_parts', value, lambda records, payload:
                ([('research_intent', 'intent', intent),
                  ('strategy_dossier', self.dossier_id, dossier)], payload), **identity)
        self.case.mutate(prepare_fixture, {})
        from research_harness.execution_evidence import author_readiness_state
        from research_harness.review_packets import readiness_packet
        report = author_readiness_state(self.records(), self.case.artifacts)
        self.candidate = report['candidate']
        self.references = copy.deepcopy(self.candidate['evidence'])
        self.logical = dict(protocol=bar_sources.PROTOCOL, role='result',
            reviewer_id='independent-verifier',
            dossier_id=self.dossier_id, dossier=dict(objective=self.case.objective),
            readiness=readiness_packet(report),
            support_contract=dict(candidate_digest=self.candidate['digest']),
            assessor=self.case.review(self.execution)['assessor'],
            source_inventory=bar_sources.inventory(self.records(), self.case.artifacts),
            source_deliveries=[], pending_source_requests=[],
            direction_evidence=dict(claim=dict(id='claim'), search=dict(id='search'),
                                    standards=dict(id='standards'),
                                    grand_challenge=dict(id='challenge'), readings=[]),
            evidence=[])
        self.logical['assessor']['id'] = 'independent-verifier'
        self.logical['evidence'] = protocol._evidence(self.case.artifacts, self.logical,
                                                     records=self.records())
        self.install_packet()

    def enlarge_readiness(self):
        self.enlarge(self.logical['readiness']['inputs']['branches'])

    def final_output(self, final, *, all_citations=False):
        support = self.case.review(self.execution)
        support['assessor'] = self.logical['assessor']
        if all_citations:
            inventory = parts.read_inventory(self.case.artifacts,
                                             self.packet(final)[parts.KEY]['inventory'])
            citations = {digest(ref): ref for row in inventory['units']
                         for ref in row['citations']}
            support_refs = [ref for ref in citations.values()
                            if ref.get('kind') in ('source', 'result')]
            self.assertTrue({digest(ref) for ref in self.references} <= set(citations))
            for check in support['checks']:
                check['evidence'] = support_refs
        evidence = next(ref['artifact'] for ref in self.references if ref['kind'] == 'result')
        response = StrategyCase.review_response(type('Fixture', (), {'evidence': evidence})(),
                                                'result')
        response['value']['bar_ids'] = []
        if all_citations:
            response['value']['evidence'] = [ref for ref in citations.values()
                                              if ref.get('kind') != 'result']
        response['support'] = support
        return response

    def test_real_continuation_rebuilds_readiness_and_dossier(self):
        self.enlarge_readiness()
        first = self.assign()
        self.invoke(first)
        def add_direction(records, artifacts, packet, dossier_id):
            packet['direction_evidence'] = copy.deepcopy(self.logical['direction_evidence'])
        with patch.object(protocol, 'add_direction_evidence', side_effect=add_direction):
            packet = self.original_build_packet(
                self.records(), self.case.artifacts, self.dossier_id,
                'result', 'independent-verifier', prior_assignment_id=first['id'])
        self.assertEqual(packet.get('readiness'), self.logical['readiness'])
        self.assertEqual(packet.get('dossier'), self.logical['dossier'])
        self.assertEqual(packet['digest'], digest({key: value for key, value in packet.items()
                                                  if key != 'digest'}))
        from research_harness.review_parts_study import compute_readiness_key
        self.assertEqual(compute_readiness_key(packet), compute_readiness_key(self.logical))

    def test_changed_candidate_uses_the_fresh_support_contract(self):
        self.enlarge_readiness()
        first = self.assign()
        self.invoke(first)
        self.logical['readiness']['inputs']['candidate']['digest'] = 'f' * 64
        self.logical['support_contract']['candidate_digest'] = 'f' * 64
        packet = self.original_build_packet(self.records(), self.case.artifacts, self.dossier_id,
            'result', 'independent-verifier', prior_assignment_id=first['id'])
        self.assertEqual(packet['support_contract']['candidate_digest'], 'f' * 64)

    def test_final_source_request_keeps_its_series_and_carried_cards(self):
        self.enlarge_readiness()
        turns, final = self.finish()
        self.invoke(final, dict(kind='requests', unit_requests=[], source_requests=[
            dict(id='page', kind='inventory_page', page=0)]))
        answer = self.assign('answer', final['id'])
        packet = self.packet(answer)
        self.assertEqual(parts.find_kind(packet), 'final')
        self.assertEqual(packet[parts.KEY]['series_assignment_id'], turns[0]['id'])
        self.assertEqual(packet[parts.STATE_KEY]['cards'],
                         self.packet(final)[parts.STATE_KEY]['cards'])
        self.assertEqual(packet['source_deliveries'][0]['status'], 'delivered')
        self.invoke(answer, self.final_output(answer))
        self.assertTrue(parts.verify_series(self.records(), self.case.artifacts,
                                            answer['id'])['complete'])

    def test_complete_readiness_series_cites_inventory_and_retains_frame(self):
        self.enlarge_readiness()
        turns, final = self.finish()
        self.invoke(final, self.final_output(final, all_citations=True))
        result = parts.verify_series(self.records(), self.case.artifacts, final['id'])
        self.assertTrue(result['complete'], result['issues'])
        self.assert_series_requests(turns + [final])
        for assignment in turns + [final]:
            packet = self.packet(assignment)
            self.assertEqual(packet['readiness']['inputs']['candidate'],
                             self.logical['readiness']['inputs']['candidate'])
            self.assertEqual(packet['readiness']['inputs']['synthesis'],
                             self.logical['readiness']['inputs']['synthesis'])
        self.assertEqual([row['id'] for row in self.packet(final)[parts.KEY]['targets']],
                         list(development._REVIEW_CHECKS))
        support = self.final_output(final)['support']
        report = development._review(development._Context(self.records(), self.case.artifacts),
                                     support, self.candidate, assignment_id=final['id'])
        self.assertTrue(report['ready'])
        records = copy.deepcopy(self.records())
        records['review_attempt'].pop('attempt-' + turns[0]['id'])
        with self.assertRaises(ResearchError):
            development._review(development._Context(records, self.case.artifacts), support,
                                self.candidate, assignment_id=final['id'])

    def test_card_bearing_covers_only_an_exact_named_candidate_reference(self):
        self.enlarge_readiness()
        turns, final = self.finish(bearings=True)
        output = self.final_output(final)
        omitted = self.references[-1]
        for check in output['support']['checks']:
            check['evidence'] = [ref for ref in self.references if ref != omitted]
        self.invoke(final, output)
        context = development._Context(self.records(), self.case.artifacts)
        self.assertTrue(development._review(context, output['support'], self.candidate,
                                            assignment_id=final['id'])['ready'])
        # Archived support keeps the assignment so later readiness sees the card credit.
        changes, report = development.prepare_combined_support(self.records(), self.case.artifacts,
            output['support'], revision=self.case.store.revision + 1, request_id='parts-support',
            assignment_id=final['id'])
        self.assertEqual(changes[0][2]['assignment_id'], final['id'])
        records = self.records()
        records.setdefault('readiness_review', {})[output['support']['id']] = changes[0][2]
        records['development_selection']['review'] = dict(review_id=output['support']['id'])
        self.assertTrue(development.assess_readiness(records, self.case.artifacts)['ready'])
        decision = dict(id='decision', phase='result',
                        support_candidate_digest=self.candidate['digest'])
        reviews = [dict(assignment_id=final['id'], payload=dict(support=output['support']))]
        self.assertEqual(research_decisions._support_obligations(records, self.case.artifacts,
                                                                decision, reviews), [])
        from research_harness import publication_scope
        contract = dict(id='scope', scientific_target_digest='s' * 64,
                        required_corrections=[], payload=dict(scientific_preparers=[]))
        scoped = dict(output['support'], target=dict(kind='source_limited_manuscript',
                      contract_id='scope', scientific_target_digest='s' * 64))
        scoped['checks'] = scoped['checks'] + [dict(kind='source_limits', status='passed',
            reason='The exact source scope is retained.', evidence=self.references[:-1])]
        publication_scope._validate_scoped_review(
            development._Context(records, self.case.artifacts),
            scoped, self.candidate, contract, self.candidate['digest'], assignment_id=final['id'])
        incomplete = copy.deepcopy(records)
        incomplete['review_attempt'].pop('attempt-' + turns[0]['id'])
        with self.assertRaises(ResearchError):
            publication_scope._validate_scoped_review(
                development._Context(incomplete, self.case.artifacts), scoped, self.candidate,
                contract, self.candidate['digest'], assignment_id=final['id'])

    def test_empty_bearings_do_not_cover_omitted_references(self):
        self.enlarge_readiness()
        turns, final = self.finish()
        output = self.final_output(final)
        for check in output['support']['checks']:
            check['evidence'] = self.references[:-1]
        self.invoke(final, output)
        with self.assertRaises(ResearchError) as raised:
            development._review(development._Context(self.records(), self.case.artifacts),
                                output['support'], self.candidate, assignment_id=final['id'])
        self.assertEqual(raised.exception.code, 'review_evidence_incomplete')

    def test_readiness_refuses_a_candidate_body_omitted_from_the_series(self):
        original = protocol._evidence
        unavailable = next(ref['link']['artifact']['sha256'] for ref in self.references
                           if ref['kind'] == 'source')

        def omit_body(*args, **kwargs):
            return [dict(entry, content='', encoding='omitted')
                    if entry['artifact']['sha256'] == unavailable else entry
                    for entry in original(*args, **kwargs)]

        with patch.object(protocol, '_evidence', side_effect=omit_body):
            self.logical['evidence'] = omit_body(self.case.artifacts, self.logical,
                                                  records=self.records())
            self.enlarge_readiness()
            turns, final = self.finish()
            output = self.final_output(final)
            response = self.invoke(final, output)
            self.assertEqual(response['output_refusal']['code'], 'review_evidence_undelivered')
            self.assertFalse(parts.verify_series(self.records(), self.case.artifacts,
                                                 final['id'])['complete'])
            with self.assertRaises(ResearchError) as raised:
                development._review(development._Context(self.records(), self.case.artifacts),
                                    output['support'], self.candidate, assignment_id=final['id'])
            self.assertEqual(raised.exception.code, 'review_parts_pending')

    def test_direction_gate_requires_a_complete_series(self):
        self.enlarge_readiness()
        turns, final = self.finish()
        self.invoke(final, self.final_output(final))
        expected = dict(self.logical['direction_evidence'], passages=[])
        reviews = [dict(id='review', assignment_id=final['id'])]
        with patch.object(direction_reviews, 'describe_direction_evidence',
                          return_value=(expected, {})):
            self.assertEqual(direction_reviews.assess_direction_reviews(
                self.records(), self.case.artifacts, {}, reviews, {}), [])
            records = copy.deepcopy(self.records())
            records['review_attempt'].pop('attempt-' + turns[0]['id'])
            self.assertTrue(direction_reviews.assess_direction_reviews(
                records, self.case.artifacts, {}, reviews, {}))

    def test_direction_reading_credits_a_source_delivered_in_segments(self):
        from research_harness.literature import import_bundle
        from research_harness.reading import record_reading
        work = self.case.metadata(91)
        body = 'A finite supported source.\n' * (self.measure(self.logical) // 8)
        capture = self.case.capture(work, pdf_text=body + '\nReferences: none.')
        bundle = self.case.bundle(work, capture, bundle_id='large-direction-bundle')
        for unit in bundle['units']:
            link = unit['link']
            link['locator'] = self.case.span(link['artifact'], link['locator']['quote'],
                                             kind='span')
        bundle['inventory']['links'] = [bundle['units'][0]['link']]
        self.case.mutate(import_bundle, bundle)
        self.case.mutate(record_reading,
            self.case.full_note(bundle, note_id='large-direction-reading'))
        reading = self.records()['reading']['large-direction-reading']
        sources = self.logical['readiness']['inputs']['sources']
        sources['reading'][reading['id']] = reading
        self.logical['evidence'] = protocol._evidence(self.case.artifacts, self.logical,
                                                     records=self.records())
        self.limit = self.measure(self.logical) // 2
        limit_codex_input(self, self.limit)
        turns, final = self.finish()
        self.invoke(final, self.final_output(final))
        self.assertTrue(any(entry.get('segment') and not entry.get('item')
                            for turn in turns for entry in self.packet(turn)['evidence']))
        expected = dict(self.logical['direction_evidence'], readings=[reading], passages=[])
        with patch.object(direction_reviews, 'describe_direction_evidence',
                          return_value=(expected, {})):
            obligations = direction_reviews.assess_direction_reviews(
                self.records(), self.case.artifacts,
                {}, [dict(id='review', assignment_id=final['id'])], {})
        self.assertEqual(obligations, [])

    def test_fitting_readiness_remains_a_single_assignment(self):
        limit_codex_input(self, self.measure(self.logical) * 2)
        assignment = self.assign()
        self.assertIsNone(parts.find_kind(self.packet(assignment)))
        self.invoke(assignment, self.final_output(assignment))
        self.assertTrue(protocol.assignment_state(self.records(), self.case.artifacts,
                                                  assignment['id'])['ready'])


if __name__ == '__main__':
    unittest.main()
