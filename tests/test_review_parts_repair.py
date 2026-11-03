"""Repair adapter contracts independent of transport observations."""

import base64
import copy
import json
import unittest
from unittest.mock import patch

from research_harness import review_parts as parts, review_parts_roles as roles
from research_harness import review_parts_prompts as prompts, review_protocol as protocol
from research_harness.evidence import digest
from review_parts_fixtures import make_packet
from strategy_fixtures import StrategyCase


class RepairAdapterTests(StrategyCase):
    def test_text_repair_keeps_binary_omissions_out_of_coverage(self):
        from research_harness import review_parts_repair as repair

        current_pdf = self.artifacts.put(b'%PDF-1.4\nCurrent original\n%%EOF', 'application/pdf')
        old_bytes = b'%PDF-1.4\nHistorical original\n%%EOF'
        old_pdf = self.artifacts.put(old_bytes, 'application/pdf')
        ancestor = dict(role='bar', original=old_pdf, evidence=[dict(artifact=old_pdf,
            content=base64.b64encode(old_bytes).decode(), encoding='base64')])
        logical = repair.prepare_history({}, self.artifacts,
            dict(role='bar', context_repair=dict(assignment_id='old'), original=current_pdf),
            [dict(assignment_id='old', packet=ancestor)])
        for reference in (current_pdf, old_pdf):
            entry = next(entry for entry in logical['evidence'] if entry['artifact'] == reference)
            self.assertEqual(entry['encoding'], 'omitted')
            self.assertEqual(entry['delivery'], 'bytes-omitted-on-text-route')
            self.assertEqual(entry.get('content'), '')
        inventory = parts.build_inventory(logical, 'context_repair')
        units = parts.enumerate_units(logical, 'context_repair', inventory)
        self.assertTrue(all(body['encoding'] == 'utf-8' for unit in units for body in unit['bodies']))
        packet = parts.project_packet(parts.build_frame(logical, units), units,
            [dict(unit=unit['handle']) for unit in units], dict(kind='read'),
            dict(cards=[], combinations=[], findings=[]))
        self.assertTrue(all(parts.is_unit_complete(unit, [packet]) for unit in units))
        from research_harness.review_delivered_evidence import project_delivery_view
        delivery = project_delivery_view({}, self.artifacts, [packet])
        self.assertNotIn(current_pdf, delivery['artifacts'].values())
        self.assertNotIn(old_pdf, delivery['artifacts'].values())

    def test_historical_source_parts_keep_their_exact_citation_scope(self):
        from research_harness import review_parts_repair as repair
        from research_harness.review_delivered_evidence import project_delivery_view
        from research_harness.bar_sources import descriptor

        text = 'A delivered source part.'
        excerpt = self.artifacts.put(text.encode(), 'text/plain')
        start = len('Before. ')
        original = self.artifacts.put(('Before. ' + text + ' After.').encode(), 'text/plain')
        mapping = dict(status='delivered', part=dict(start=start, end=start + len(text)),
            version_id='arxiv:2410.00001v1', excerpt=excerpt,
            origin=dict(artifact_descriptor=descriptor(original), source_id='source-1',
                        locator=dict(kind='span', start=start, end=start + len(text), sha256=excerpt['sha256'])))
        ancestor = dict(role='bar', source_deliveries=[mapping], prior_context=[],
            evidence=[dict(artifact=excerpt, content=text, encoding='utf-8')])
        expected = project_delivery_view({}, self.artifacts, [ancestor])['source_mappings']
        self.assertEqual(expected, [mapping])
        logical = repair.prepare_history({}, self.artifacts,
            dict(role='bar', context_repair=dict(assignment_id='old'), prior_context=[]),
            [dict(assignment_id='old', packet=ancestor)])
        inventory = parts.build_inventory(logical, 'context_repair')
        units = parts.enumerate_units(logical, 'context_repair', inventory)
        header = dict(protocol=parts.PROTOCOL, adapter='context_repair', kind='read',
            units=inventory['units'], logical_packet_digest=digest(logical),
            logical_packet=self.artifacts.put(parts.render_json(logical).encode(), 'application/json'))
        packet = parts.project_packet(parts.build_frame(logical, units), units,
            [dict(unit=unit['handle']) for unit in units], header,
            dict(cards=[], combinations=[], findings=[]))
        delivery = parts.read_series_delivery({}, self.artifacts, packets=[packet], inventory=inventory)
        self.assertIn(excerpt, delivery['artifacts'].values())
        self.assertEqual(delivery['source_mappings'], expected)
        from research_harness.review_supplied_evidence import cite_passage
        from research_harness.research_decisions import find_review_undelivered_references
        citation = dict(kind='source', link=cite_passage(self.artifacts, mapping))
        self.assertEqual(find_review_undelivered_references({}, self.artifacts, [citation], delivery), [])
        widened = copy.deepcopy(citation)
        widened['link']['locator']['end'] += 1
        self.assertTrue(find_review_undelivered_references({}, self.artifacts, [widened], delivery))

    def test_repair_wraps_role_units_and_deduplicates_ancestor_occurrences(self):
        from research_harness import review_parts_repair as repair
        packet = make_packet(2, 100)
        for item in packet['problem_evidence']['openness_searches']:
            item['search'] = dict(id=item['id'])
        packet['context_repair'] = dict(assignment_id='ancestor', event_ids=['event'], probe_id='probe')
        ancestor = copy.deepcopy(packet)
        ancestor.pop('context_repair')
        output = dict(stage='result', phase='evidence_part', notes=[])
        packet['scientific_history'] = [dict(assignment_id='ancestor', packet=ancestor,
            observed_output=None, intermediate_output=output, independence='no_longer_qualified')]
        logical = repair.prepare_history({}, self.artifacts, packet)
        self.assertEqual(roles.find_adapter(dict(role='result'), logical), 'context_repair')
        inventory = parts.build_inventory(logical, 'context_repair')
        shared = next(row for row in inventory['units']
                      if row['digest'] == digest(ancestor['problem_evidence']['openness_searches'][0]))
        self.assertEqual(len(shared['occurrences']), 2)
        self.assertTrue(all(row['citations'] for row in inventory['units']))
        units = parts.enumerate_units(logical, 'context_repair')
        texts = [body['content'] for unit in units for body in unit['bodies']]
        self.assertIn(parts.render_json(output), texts)
        self.assertEqual(logical['context_repair'], packet['context_repair'])
        self.assertEqual(json.loads(parts.render_json(logical)), logical)

    def test_adjudicator_wrapping_uses_registry_and_role_final_text(self):
        from research_harness import review_parts_repair as repair
        definition = dict(applies=lambda value, packet: value.get('role') == 'adjudicator'
                          and 'context_repair' not in packet,
                          enumerate_units=lambda packet: [], targets=lambda packet: [],
                          series_key=lambda packet: dict(objection='version'),
                          validate_final=lambda *args, **kwargs: 'role-validated',
                          final_prompt=prompts.FINAL_ADJUDICATOR, source_requests=False)
        packet = dict(role='adjudicator', subject=dict(kind='problem'),
                      context_repair=dict(assignment_id='old'), scientific_history=[])
        with patch.dict(roles.ADAPTERS, problem_adjudicator=definition):
            key = repair.compute_series_key(packet)
            self.assertEqual(key['role'], dict(objection='version'))
            assignment = dict(id='repair', role='adjudicator',
                packet=self.artifacts.put(parts.render_json(packet).encode(), 'application/json'))
            with patch.object(protocol, 'validate_adjudication_contract',
                              side_effect=AssertionError('Use the registered role validator')):
                self.assertEqual(repair.validate_final({}, self.artifacts, assignment, {}), 'role-validated')
            for kind in ('read', 'final'):
                prompt = protocol.canonical_prompt('adjudicator', orchestration='problem_creation',
                    review_parts=dict(adapter='context_repair', kind=kind))
                if kind == 'final':
                    self.assertTrue(prompt['text'].endswith(
                        prompts.FINAL_ADJUDICATOR + '\n\n' + prompts.FINAL_REPAIR))
                else:
                    self.assertNotIn(prompts.FINAL_REPAIR, prompt['text'])

    def test_repair_role_matrix_and_prompt_composition(self):
        for role in ('bar', 'slate', 'result', 'adjudicator', 'verification', 'native_math'):
            packet = dict(role=role, subject=dict(kind='problem'), context_repair={})
            name = roles.find_adapter(dict(role=role), packet)
            self.assertEqual(name == 'context_repair', role in ('bar', 'slate', 'result', 'adjudicator'))
        for role in ('bar', 'slate', 'result'):
            prompt = protocol.canonical_prompt(role, repair=True, orchestration='problem_creation',
                review_parts=dict(adapter='context_repair', kind='final'))
            suffix = ((prompts.FINAL_RESULT + '\n\n') if role == 'result' else '') + prompts.FINAL_REPAIR
            self.assertTrue(prompt['text'].endswith(suffix))

    def test_frame_keeps_exact_inherited_final_payloads_and_objection_identities(self):
        from research_harness import review_parts_repair as repair
        packet = make_packet(1, 100)
        packet['context_repair'] = dict(assignment_id='ancestor')
        packet['scientific_history'] = []
        packet['historical_reviews'] = [dict(id='final', payload=dict(stage='result',
            objections=[dict(id='qualified', stated_id='stated', claim='Exact claim.', version_evidence=[])],
            limitations=['Exact limitation.']))]
        logical = repair.prepare_history({}, self.artifacts, packet)
        frame = parts.build_frame(logical, parts.enumerate_units(logical, 'context_repair'))
        self.assertEqual(frame['historical_reviews'], packet['historical_reviews'])
        changed = copy.deepcopy(logical)
        changed['context_repair']['assignment_id'] = 'different'
        self.assertNotEqual(repair.compute_series_key(logical), repair.compute_series_key(changed))

    def test_reassessment_uses_all_frozen_finals_and_only_series_evidence(self):
        from research_harness import review_context_repair as repair
        from research_harness.errors import ResearchError
        logical = dict(role='bar', context_repair=dict(assignment_id='old'), scientific_history=[],
            historical_reviews=[dict(id='first', payload={}), dict(id='second', payload={})],
            evidence=[dict(artifact=self.evidence, content=self.artifacts.read(self.evidence).decode(),
                           encoding='utf-8')])
        inventory = parts.build_inventory(logical, 'context_repair')
        logical_reference = self.artifacts.put(parts.render_json(logical).encode(), 'application/json')
        header = dict(protocol=parts.PROTOCOL, adapter='context_repair', kind='final',
            series_assignment_id='repaired', inventory=parts.store_inventory(self.artifacts, inventory),
            inventory_digest=digest(inventory), logical_packet=logical_reference,
            logical_packet_digest=digest(logical))
        packet = dict(logical, review_parts=header, review_parts_state=dict(cards=[], combinations=[], findings=[]))
        assignment = dict(id='repaired', role='bar', review_parts=dict(adapter='context_repair'),
            context={}, packet=self.artifacts.put(parts.render_json(packet).encode(), 'application/json'))
        records = dict(review_assignment={'repaired': assignment})
        finding = dict(review_id='first', disposition='revised', reason='Rechecked the supplied originals.',
                       evidence=[self.evidence])
        output = dict(reassessment=dict(assignment_id='old', findings=[finding]))
        with self.assertRaises(ResearchError) as raised:
            repair.validate_reassessment(records, self.artifacts, assignment, output, packet=packet)
        self.assertEqual(raised.exception.code, 'review_reassessment_incomplete')
        output['reassessment']['findings'].append(dict(finding, review_id='second'))
        repair.validate_reassessment(records, self.artifacts, assignment, output, packet=packet)
        unavailable = self.artifacts.put(b'An existing artifact never delivered to this series.', 'text/plain')
        output['reassessment']['findings'][1]['evidence'] = [unavailable]
        with self.assertRaises(ResearchError) as raised:
            repair.validate_reassessment(records, self.artifacts, assignment, output, packet=packet)
        self.assertEqual(raised.exception.code, 'review_evidence_undelivered')

    def test_nonterminal_v2_outputs_are_intermediate_and_adjudicator_is_admitted(self):
        from research_harness import review_context_repair as repair
        evidence = self.evidence
        predecessor = dict(id='old', role='adjudicator', dossier_id=None, author_id='author',
            reviewer_id='old-reviewer', route_id='old-route', context={}, recorded_revision=1,
            subject=dict(kind='problem'), review_parts=dict(adapter='fixture_adjudicator'))
        event = dict(id='event', assignment_id='old', kind='contamination', source='author_history',
                     reason='Observed exposure.', evidence=[evidence], recorded_revision=2)
        probe = dict(id='probe', recorded_revision=3)
        records = dict(review_assignment={'old': predecessor}, review_context_event={'event': event},
            review_route_probe={'probe': probe}, review_route={}, review_attempt={})
        value = dict(predecessor, id='new', route_id='new-route', reviewer_id='new-reviewer',
            context=dict(context_repair=dict(assignment_id='old', event_ids=['event'], probe_id='probe',
                reason='Reassess observed history.', evidence=[evidence])))
        for kind in ('read', 'combine', 'check', 'requests'):
            output = dict(kind=kind)
            packet = dict(role='adjudicator', review_parts=dict(protocol=parts.PROTOCOL,
                          kind='final' if kind == 'requests' else kind))
            state = dict(ready=False, context_status='contaminated', output=output,
                obligations=[dict(code='review_context_contaminated')], raw_output=output)
            with self.subTest(kind=kind), \
                    patch.object(protocol, 'assignment_state', return_value=state), \
                    patch.object(protocol, 'source_delivery_packet', return_value=packet), \
                    patch.object(protocol, 'route_state', return_value=dict(context_status='verified_for_route',
                                                                           probe_id='probe')):
                prepared = repair.prepare_repair(records, self.artifacts, value)
            turn = prepared['scientific_history'][0]
            self.assertIsNone(turn['observed_output'])
            self.assertEqual(turn['intermediate_output'], output)

    def test_final_fill_prioritizes_inherited_support_before_other_flagged_originals(self):
        from research_harness import review_parts_repair as repair
        packet = make_packet(0)
        packet['role'] = 'bar'
        packet['context_repair'] = dict(assignment_id='old')
        packet['scientific_history'] = []
        frame_text = dict(artifact=self.evidence, content=self.artifacts.read(self.evidence).decode(), encoding='utf-8')
        packet['scientific_history'] = [dict(assignment_id='old', independence='no_longer_qualified',
            packet=dict(units=[], bindings=[], frames=[dict(artifact=self.evidence, bodies=[])]), outputs=[])]
        packet['evidence'] = [frame_text]
        other = self.artifacts.put(b'Another flagged original.', 'text/plain')
        packet['scientific_history'][0]['outputs'] = [dict(artifact=other, bodies=[])]
        packet['evidence'].append(dict(artifact=other, content=self.artifacts.read(other).decode(), encoding='utf-8'))
        packet['historical_reviews'] = [dict(id='final', payload=dict(evidence=[protocol._reference_templates(self.evidence)]))]
        inventory = parts.build_inventory(packet, 'context_repair')
        units = parts.enumerate_units(packet, 'context_repair', inventory)
        selections = [dict(unit=unit['handle']) for unit in reversed(units)]
        header = dict(protocol=parts.PROTOCOL, kind='final', fill=selections,
                      original_characters=sum(unit['characters'] for unit in units))
        planned = parts.project_packet(parts.build_frame(packet, units), units, selections, header,
                                       dict(cards=[], combinations=[], findings=[]))
        route = dict(adapter='codex_cli_v1', model='fixture', endpoint='codex://local',
                     configuration=dict(max_output_tokens=128000, timeout_seconds=5))
        prompt = protocol.canonical_prompt('bar', repair=True,
            review_parts=dict(adapter='context_repair', kind='final'))
        measure = lambda value: legacy_measure(route, prompt, value)
        from research_harness.review_problem_parts import _measure
        def legacy_measure(route, prompt, value):
            return _measure(route, protocol.transport.request_body(route, prompt['text'], value, [], order=()))
        final = repair.prioritize_fill(packet, inventory, planned, measure, measure(planned) * 2)
        self.assertEqual(final[parts.KEY]['fill'][0]['unit'], units[0]['handle'])

    def test_adjudicator_continuation_preserves_the_original_correction(self):
        from research_harness import review_parts_repair as repair
        correction = dict(kind='omission', claim='An exact supplied condition was omitted.',
                          evidence=[self.evidence])
        logical = dict(correction=protocol._reference_templates(correction),
            assessments=[dict(id='first'), dict(id='second')], objection=dict(id='objection'),
            context_repair=dict(assignment_id='original'), scientific_history=[], historical_reviews=[])
        reference = self.artifacts.put(parts.render_json(logical).encode(), 'application/json')
        frozen = dict(review_parts=dict(logical_packet=reference, logical_packet_digest=digest(logical)))
        original = dict(id='original', context=dict(review_ids=['first', 'second'],
                                                   objection_id='objection', correction=correction))
        parent = dict(id='repair', context=dict(context_repair=dict(assignment_id='original'),
                      review_ids=['first', 'second'], objection_id='objection'),
                      packet=self.artifacts.put(parts.render_json(frozen).encode(), 'application/json'))
        records = dict(review_assignment={row['id']: row for row in (original, parent)})
        value = dict(dossier_id=None, role='adjudicator', reviewer_id='replacement')
        def build(records, artifacts, dossier, role, reviewer, **context):
            return dict(role=role, correction=context.get('correction'))
        with patch.object(protocol, 'build_packet', side_effect=build):
            packet = repair.build_continuation_packet(records, self.artifacts, value, parent)
        self.assertEqual(packet['correction'], correction)

    def test_a_recorded_adjudicator_reassessment_is_repairable_again(self):
        from research_harness import review_context_repair as repair
        from research_harness.errors import ResearchError
        output = dict(objection_id='objection', disposition='unresolved', reason='The exact premise stays open.',
            evidence=[self.evidence], correction_admissible=None,
            reassessment=dict(assignment_id='earlier', findings=[]))
        predecessor = dict(id='old', role='adjudicator', dossier_id=None, author_id='author',
            reviewer_id='old-reviewer', route_id='old-route', context={}, recorded_revision=1,
            subject=dict(kind='problem'), review_parts=dict(adapter='context_repair'))
        event = dict(id='event', assignment_id='old', kind='contamination', source='author_history',
                     reason='Observed exposure.', evidence=[self.evidence], recorded_revision=2)
        saved = dict(output, id='prior-adjudication', assignment_id='old', recorded_revision=1)
        records = dict(review_assignment={'old': predecessor}, review_context_event={'event': event},
            review_route_probe={'probe': dict(id='probe', recorded_revision=3)}, review_route={}, review_attempt={},
            review_adjudication={saved['id']: saved})
        value = dict(predecessor, id='new', route_id='new-route', reviewer_id='new-reviewer',
            context=dict(context_repair=dict(assignment_id='old', event_ids=['event'], probe_id='probe',
                reason='Reassess the retained focused finding.', evidence=[self.evidence])))
        packet = dict(role='adjudicator', review_parts=dict(protocol=parts.PROTOCOL, kind='final'))
        state = dict(ready=False, context_status='contaminated', output=output,
            obligations=[dict(code='review_context_contaminated')], raw_output=output)
        with patch.object(protocol, 'assignment_state', return_value=state), \
                patch.object(protocol, 'source_delivery_packet', return_value=packet), \
                patch.object(protocol, 'route_state', return_value=dict(context_status='verified_for_route',
                                                                       probe_id='probe')):
            prepared = repair.prepare_repair(records, self.artifacts, value)
            self.assertEqual(prepared['historical_reviews'], [saved])
            self.assertEqual(prepared['scientific_history'][0]['observed_output'], output)
            state['output'] = dict(output, reassessment=dict(assignment_id='wrong', findings=[]))
            with self.assertRaises(ResearchError) as raised:
                repair.prepare_repair(records, self.artifacts, value)
            self.assertEqual(raised.exception.code, 'review_context_repair_history_unrecorded')

    def test_adjudicator_repair_final_keeps_current_prompt_rules(self):
        for kind in ('read', 'combine', 'check', 'final'):
            prompt = protocol.canonical_prompt('adjudicator', orchestration='problem_creation',
                review_parts=dict(adapter='context_repair', kind=kind))
            self.assertEqual(protocol._matches_current_problem_adjudicator_prompt(prompt), kind == 'final')

    def test_v2_history_keeps_actual_delivery_and_old_handle_bindings(self):
        from research_harness import review_parts_repair as repair
        logical = make_packet(2, 100)
        inventory = parts.build_inventory(logical, 'problem_result')
        logical_reference = self.artifacts.put(parts.render_json(logical).encode(), 'application/json')
        header = dict(protocol=parts.PROTOCOL, kind='read', adapter='problem_result',
            logical_packet=logical_reference, logical_packet_digest=digest(logical),
            inventory=parts.store_inventory(self.artifacts, inventory), inventory_digest=digest(inventory),
            delivered_units=['u1'], originals=[dict(unit='u1', item=True, start=0, end=10)])
        actual = dict(logical, review_parts=header,
                      review_parts_state=dict(cards=[], combinations=[], findings=[]))
        current = dict(logical, context_repair=dict(assignment_id='old'))
        histories = []
        for handle in ('u1', 'u2'):
            actual = copy.deepcopy(actual)
            actual[parts.KEY]['delivered_units'] = [handle]
            actual[parts.KEY]['originals'][0]['unit'] = handle
            ancestor = dict(assignment_id='old', packet=actual, observed_output=None,
                            intermediate_output=dict(kind='read', cards=[dict(unit=handle)]))
            # Keep the output identical to isolate the packet delivery binding.
            ancestor['intermediate_output'] = dict(kind='read', cards=[])
            normalized = repair.prepare_history({}, self.artifacts, current, [ancestor])
            reference = normalized['scientific_history'][0]['packet']['frames'][0]['artifact']
            histories.append(json.loads(self.artifacts.read(reference)))
        self.assertNotEqual(histories[0], histories[1])
        self.assertEqual(histories[1][parts.KEY], actual[parts.KEY])
        self.assertEqual(histories[1][parts.STATE_KEY], actual[parts.STATE_KEY])
        self.assertEqual(histories[1]['inventory'], inventory)
