"""Adversarial checks of scope boundaries and bounded planning."""

import copy
import json
import unittest

from research_harness import review_staged as staged
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from tests.test_review_staged_fixtures import epoch, profile, full_card, selection, task, observation


class ContractValidationTests(unittest.TestCase):
    def test_frozen_source_identities_are_validated_before_completion_coverage(self):
        for corruption in ('missing_id', 'conflicting_id'):
            value = epoch()
            source = value['manifest']['sources'][0]
            if corruption == 'missing_id':
                del source['id']
            else:
                value['manifest']['sources'].append(dict(source, text='Conflicting source bytes.'))
            for record in (value['manifest'], value):
                record['digest'] = digest({key: item for key, item in record.items() if key != 'digest'})
            with self.subTest(corruption=corruption):
                with self.assertRaises(ResearchError) as raised:
                    staged.verify_completion(value, {'observations': []})
                self.assertEqual(raised.exception.code, 'invalid_review_staged')

    def test_source_identity_requires_canonical_json_object_keys(self):
        with self.assertRaises(ResearchError) as raised:
            staged.make_source('source', 'record', 'text', identity={1: 'invalid key'})
        self.assertEqual(raised.exception.code, 'invalid_review_staged')

    def test_shared_validation_keeps_staged_error_codes(self):
        for value in (None, {}, {'source_id': 's', 'start': 0, 'end': 1, 'sha256': 'x', 'extra': True}):
            with self.subTest(value=value), self.assertRaises(ResearchError) as raised:
                staged.materialize_quotation(staged.make_source('s', 'record', 'x', identity={'id': 's'}), value)
            self.assertEqual(raised.exception.code, 'invalid_review_staged')
        for source_id in ('', '   ', None):
            with self.subTest(source_id=source_id), self.assertRaises(ResearchError) as raised:
                staged.make_source(source_id, 'record', 'x', identity={'id': 's'})
            self.assertEqual(raised.exception.code, 'invalid_review_staged')

    def test_unmeasured_reconciliation_is_not_executable(self):
        value = epoch()
        draft = staged.make_task(value, 'integrate', scope='segmented_card')
        self.assertEqual(staged.task_state(value, draft)['state'], 'blocked')
        with self.assertRaises(ResearchError):
            staged.freeze_task(draft, 'rendered-request', staged.SERIALIZATION)
        fit = staged.joint_fit([10], 100, 100, profile())
        bound = staged.freeze_task(draft, 'rendered-request', staged.SERIALIZATION, measurement=fit)
        self.assertEqual(staged.task_state(value, bound)['state'], 'ready')

    def test_incomplete_completion_root_retains_fragment_conclusions(self):
        value = epoch()
        card = full_card(value)
        card['kind'] = 'fragment'
        assigned = task(value)
        captured = observation(value, assigned, dict(cards=[card], cells=card['judgments']))
        first = staged.verify_completion(value, {'observations': [captured]}, tasks=[assigned])
        captured['output']['cards'][0]['conclusions'][0]['text'] = 'A different retained fragment conclusion.'
        captured['raw_output_digest'] = digest(captured['output'])
        second = staged.verify_completion(value, {'observations': [captured]}, tasks=[assigned])
        self.assertFalse(first['complete'])
        self.assertNotEqual(first['known_conclusions_root'], second['known_conclusions_root'])

    def test_new_context_version_can_complete_within_the_same_epoch(self):
        value = epoch()
        claims = copy.deepcopy(value['manifest']['claims'])
        claims[0]['dependencies'] = [claims[1]['id']]
        contexts = staged.build_contexts(claims, value['manifest']['sources'])
        card = full_card(value)
        for cell in card['judgments']:
            cell['context_root'] = contexts[cell['claim_id']]['root']
        assigned, selected = selection(value, [card], contexts=contexts)
        output = selected['observations'][0]['output']
        output['new_dependencies'] = [dict(claim_id=claims[0]['id'], depends_on=claims[1]['id'])]
        selected['observations'][0]['raw_output_digest'] = digest(output)
        report = staged.verify_completion(value, selected, tasks=assigned, contexts=contexts)
        self.assertTrue(report['complete'], report['issues'])

    def test_compact_critical_statements_keep_original_characters(self):
        value = epoch()
        card = full_card(value)
        compact = staged.compact_card(card, manifest=value['manifest'])
        original = card['critical_spans'][0]
        source = next(row for row in value['manifest']['sources'] if row['id'] == original['source_id'])
        self.assertEqual(compact['critical_quotations'][0]['text'], source['text'][original['start']:original['end']])

    def test_task_objection_alias_cannot_resolve_against_an_earlier_packet(self):
        full = dict(cite={'id': 'source'}, content='original')
        objections = [dict(id='O1', version_evidence=[full]), dict(id='O2', version_evidence=[dict(
                     delivery='repeated-in-material-objections', objection_id='O1', field='version_evidence',
                     index=0, digest=digest(full))])]
        value = epoch(material_objections=objections)
        assigned = staged.make_task(value, 'answer', objection_entries=value['material_objection_entries'])
        assigned = staged.freeze_task(assigned, 'rendered-answer-request', staged.SERIALIZATION,
                                       measurement=staged.joint_fit([10], 100, 100, profile()))
        captured = observation(value, assigned, {'pages': [{'id': 'page'}]})
        captured['actual_input']['material_objections'] = objections[1:]
        report = staged.verify_completion(value, {'observations': [captured]}, tasks=[assigned])
        self.assertIn('review_objection_evidence_invalid', [row['code'] for row in report['issues']])

    def test_focused_adjudication_uses_its_bound_disposition(self):
        value = epoch(role='adjudicator', focused_scope=dict(review_pair_roots=['review-A', 'review-B'],
                       objection_id='O7', objection_version='version-7'))
        terminal = dict(objection_id='O7', disposition='upheld', correction_admissible=None, visible_conditions=[])
        assigned, selected = selection(value, terminal=terminal)
        report = staged.verify_completion(value, selected, tasks=assigned)
        self.assertTrue(report['complete'], report['issues'])
        selected['observations'][0]['output']['terminal']['objection_id'] = 'O8'
        selected['observations'][0]['raw_output_digest'] = digest(selected['observations'][0]['output'])
        self.assertFalse(staged.verify_completion(value, selected, tasks=assigned)['complete'])

    def test_rubric_choices_honor_integration_constraints(self):
        value = epoch()
        assigned, selected = selection(value, integration_changes={
            'decision_constraints': {'visible_conditions': [], 'permissible_decisions': {'/value/rubric/calibration': ['fail']}}})
        output = selected['observations'][0]['output']
        output['terminal']['decisions'] = {'/value/rubric/calibration': 'pass'}
        selected['observations'][0]['raw_output_digest'] = digest(output)
        self.assertFalse(staged.verify_completion(value, selected, tasks=assigned)['complete'])

    def test_successful_segmented_card_accounting_and_missing_segment(self):
        value = epoch(count=2)
        cards = [full_card(value, index=i) for i in range(2)]
        sizes = [dict(id=card['id'], characters=len(json.dumps(staged.compact_card(card),
                     sort_keys=True, ensure_ascii=False, separators=(',', ':')))) for card in cards]
        plan = staged.plan_tasks(value, route_limit=max(row['characters'] for row in sizes) + 40,
                                 output_tokens=100, profiles={'combine': profile()}, cards=sizes)
        reading = task(value)
        read_output = dict(cards=cards, cells=[cell for card in cards for cell in card['judgments']])
        observed_read = observation(value, reading, read_output)
        assigned, observed = [reading], [observed_read]
        for segment in [row for row in plan['tasks'] if row['scope'] == 'card_segment']:
            frozen = staged.freeze_task(segment, digest(segment), staged.SERIALIZATION)
            output = {'segment_readings': [dict(id='reading-' + frozen['id'], card_id=span['card_id'], start=span['start'], end=span['end'], reading='Read all assigned critical text.') for span in frozen['card_segments']]}
            captured = observation(value, frozen, output)
            captured['actual_input']['card_segments'] = [staged.card_span(
                next(card for card in cards if card['id'] == span['card_id']), span['start'], span['end'])
                for span in frozen['card_segments']]
            assigned.append(frozen)
            observed.append(captured)
        segments = [row['id'] for row in assigned if row['scope'] == 'card_segment']
        combine = staged.make_task(value, 'combine', scope='segmented_card', card_ids=[c['id'] for c in cards],
                                   dependencies=[row['id'] for row in assigned], segment_task_ids=segments)
        combine = staged.freeze_task(combine, digest(combine), staged.SERIALIZATION,
                                      measurement=staged.joint_fit([10], 100, 100, profile()))
        output = {'combinations': [dict(id='segmented-pair', task_id=combine['id'], card_ids=combine['card_ids'],
                   scope='segmented_card', synthesis='The conditions have been reconciled across both cards.',
                   selected_groups=[], segment_task_ids=segments, reconciliation='All segments and conditions reconciled.')]}
        captured = observation(value, combine, output,
                               dependencies={row['task_id']: row['raw_output_digest'] for row in observed})
        assigned.append(combine)
        observed.append(captured)
        report = staged.verify_completion(value, {'observations': observed}, tasks=assigned)
        self.assertEqual(report['progress']['combination_assessments'], 1, report['issues'])
        self.assertEqual(report['progress']['card_pairs'], 1)
        observed.pop(1)
        report = staged.verify_completion(value, {'observations': observed}, tasks=assigned)
        self.assertEqual(report['progress']['combination_assessments'], 0)

    def test_three_item_original_check_closes_exact_requested_group(self):
        value = epoch(count=3)
        group = dict(id='triple', occurrence_ids=[row['id'] for row in value['manifest']['occurrences']],
                     claim_ids=[value['manifest']['claims'][0]['id']],
                     question='Does the three-item interaction invalidate the claim?', requested_by=['integration'])
        plan = staged.plan_tasks(value, route_limit=10000, output_tokens=100,
                                 profiles={'original_check': profile()}, selected_groups=[group])
        assigned = staged.freeze_task(plan['tasks'][0], 'rendered-triple-request', staged.SERIALIZATION)
        check = dict(id='check-triple', task_id=assigned['id'], group_ids=['triple'], scope='joint_original',
                     status='resolved', conclusions=[dict(id='checked-conclusion', text='The three-item interaction was examined.',
                     grounds=assigned['inputs'][:1])], qualifications=[],
                     conflicts=[], unresolved=[], segment_ids=[], reconciliation=None)
        captured = observation(value, assigned, dict(original_checks=[check], selected_groups=[group]))
        report = staged.verify_completion(value, {'observations': [captured]}, tasks=[assigned])
        self.assertEqual(report['progress']['original_groups'], 1, report['issues'])
        captured['actual_input']['intervals'].pop()
        report = staged.verify_completion(value, {'observations': [captured]}, tasks=[assigned])
        self.assertEqual(report['progress']['original_groups'], 0)

    def test_subdivision_policy_bounds_planning(self):
        budgets = dict(max_calls=20, max_retries=1, max_rereads=2, max_subdivisions=1,
                       max_epochs=2, max_elapsed_seconds=1000)
        value = epoch(budgets=budgets)
        plan = staged.plan_tasks(value, route_limit=22, output_tokens=100,
                                 profiles={'read': profile()})
        self.assertTrue(plan['unresolved_task_ids'])
        self.assertLessEqual(len(plan['tasks']), 4)

    def test_invalid_reading_output_receives_no_source_progress(self):
        value = epoch()
        assigned = task(value)
        captured = observation(value, assigned, {'cards': [{'id': 'bad'}], 'cells': [{'id': 'bad'}]})
        report = staged.verify_completion(value, {'observations': [captured]}, tasks=[assigned])
        self.assertEqual(report['progress']['source_characters'], 0)

    def test_duplicate_descriptor_aliases_share_one_reading_scope(self):
        descriptor = {'sha256': 'abc', 'path': 'artifact', 'media_type': 'text/plain'}
        sources = [dict(id=key, kind='artifact', descriptor=descriptor, text='abc', identity={'id': key})
                   for key in ('first', 'second')]
        occurrences = [dict(key='readings', index=i, item={'id': i}, artifacts=[descriptor], source_ids=[key])
                       for i, key in enumerate(('first', 'second'))]
        manifest = staged.build_manifest({}, occurrences, sources)
        self.assertEqual(len([row for row in manifest['sources'] if row['kind'] == 'artifact']), 1)
        self.assertEqual(manifest['source_aliases']['second'], 'first')
        self.assertEqual(manifest['occurrences'][0]['source_ids'][-1],
                         manifest['occurrences'][1]['source_ids'][-1])

    def test_owned_body_cannot_be_certified_from_a_partial_interval(self):
        descriptor = {'sha256': 'abc', 'path': 'artifact'}
        sources = [dict(id='body', kind='artifact', descriptor=descriptor, text='abc',
                        identity={'id': 'body'}, required_intervals=[[0, 1]])]
        occurrences = [dict(key='readings', index=0, item={}, artifacts=[descriptor], source_ids=['body'])]
        with self.assertRaises(ResearchError):
            staged.build_manifest({}, occurrences, sources)

    def test_native_binary_requires_its_own_delivery(self):
        descriptor = {'sha256': 'pdf', 'path': 'source.pdf', 'media_type': 'application/pdf'}
        binary = staged.make_source('native', 'binary', identity={'version': 1}, descriptor=descriptor)
        with self.assertRaises(ResearchError):
            staged.source_span(binary, 0, 1)
        with self.assertRaises(ResearchError):
            staged.make_source('native', 'binary', 'decoded text', identity={'version': 1}, descriptor=descriptor)

    def test_no_bearing_requires_source_scope_and_grounded_reason(self):
        value = epoch()
        cell = full_card(value)['judgments'][0]
        cell['status'] = 'no_bearing'
        with self.assertRaises(ResearchError):
            staged.validate_cell(cell, value['manifest'])
        cell['source_subject'] = 'Only composite inputs are considered by this item.'
        self.assertEqual(staged.validate_cell(cell, value['manifest'])['status'], 'no_bearing')
        cell['reason'] = ''
        with self.assertRaises(ResearchError):
            staged.validate_cell(cell, value['manifest'])

    def test_empty_critical_selection_requires_grounded_explanation(self):
        value = epoch()
        card = full_card(value)
        card['critical_spans'] = []
        card['critical_quotations'] = []
        with self.assertRaises(ResearchError):
            staged.validate_card(card, value['manifest'])
        card['no_statement_reason'] = 'The examined item reports only a bibliographic identifier.'
        self.assertEqual(staged.validate_card(card, value['manifest'])['critical_spans'], [])

    def test_integration_cycle_is_not_a_completion_tree(self):
        value = epoch()
        tasks, selected = selection(value)
        receipt = selected['observations'][0]['output']['integrations'][0]
        receipt['child_ids'] = [receipt['id']]
        receipt['child_union'] = receipt['obligation_ids'][:]
        selected['observations'][0]['raw_output_digest'] = digest(selected['observations'][0]['output'])
        self.assertFalse(staged.verify_completion(value, selected, tasks=tasks)['complete'])

    def test_segmented_card_needs_actual_segments(self):
        value = epoch()
        assigned = staged.make_task(value, 'combine', scope='card_segment', card_ids=['card'],
                                   card_segments=[dict(card_id='card', start=0, end=10, characters=10)])
        assigned = staged.freeze_task(assigned, 'rendered-request-digest', staged.SERIALIZATION,
                                       measurement=staged.joint_fit([10], 100, 100, profile()))
        observed = observation(value, assigned, {})
        report = staged.verify_completion(value, {'observations': [observed]}, tasks=[assigned])
        self.assertIn('invalid_review_staged', [row['code'] for row in report['issues']])

    def test_impossible_output_does_not_subdivide_every_character(self):
        value = epoch()
        plan = staged.plan_tasks(value, route_limit=100, output_tokens=1,
                                 profiles={'read': profile(output_base_tokens=100)})
        self.assertLessEqual(len(plan['tasks']), 4)
        self.assertTrue(any(t['scope'] == 'unresolved' for t in plan['tasks']))

    def test_material_objection_aliases_need_the_full_entry_in_the_same_packet(self):
        full = {'cite': {'id': 'source'}, 'content': 'exact original'}
        packet = {'material_objections': [dict(id='O1', version_evidence=[full]),
                   dict(id='O2', version_evidence=[dict(delivery='repeated-in-material-objections',
                        objection_id='O1', field='version_evidence', index=0, digest=digest(full))])]}
        reference = packet['material_objections'][1]['version_evidence'][0]
        self.assertEqual(staged.resolve_objection_entry(packet, reference), full)
        with self.assertRaises(ResearchError):
            staged.resolve_objection_entry({'material_objections': packet['material_objections'][1:]}, reference)
        changed = copy.deepcopy(packet)
        changed['material_objections'][0]['version_evidence'][0]['content'] = 'changed'
        with self.assertRaises(ResearchError):
            staged.resolve_objection_entry(changed, reference)

    def test_current_complete_slots_still_need_science_and_qualification(self):
        heads = [dict(slot_id='A', epoch_id='epoch', state='complete', outcome={'id': 'negative'})]
        self.assertFalse(staged.collect_slot_outcomes(heads, qualified=True)['ready'])
        self.assertTrue(staged.collect_slot_outcomes(heads, qualified=True, scientific_ready=True)['ready'])

    def test_successor_and_stale_transitions_preserve_history(self):
        initial = dict(slot_id='A', epoch_id='old', state='complete', outcome={'id': 'old-result'},
                       inherited_adverse_findings=['finding'])
        successor = staged.transition_outcome(initial, 'successor', epoch_id='new')
        self.assertEqual(successor['state'], 'pending')
        self.assertIsNone(successor['outcome'])
        self.assertEqual(successor['history'][0]['outcome'], initial['outcome'])
        self.assertEqual(successor['inherited_adverse_findings'], ['finding'])
        stale = staged.transition_outcome(successor, 'evidence_changed')
        self.assertFalse(staged.collect_slot_outcomes([stale])['assessment_complete'])


if __name__ == '__main__':
    unittest.main()
