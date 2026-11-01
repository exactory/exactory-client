"""Reviewer v2 records, quotation binding and unfinished task reconciliation."""

import copy
import unittest

from research_harness import review_staged as staged
from research_harness import review_composition as composition
from research_harness.artifacts import describe_artifact
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from test_review_staged_fixtures import epoch, profile, task, observation, full_card, selection
from test_review_composition import make_fixture, worked_adjudication, observe


def incomplete_output():
    return dict(unresolved_scope=[dict(id='unfinished', scope={'claim_ids': ['claim']},
                cause='The smallest reading did not settle the question.', affected_decisions=['/value/status'],
                exhausted_tasks=['old-task'], scope_kind='unresolved',
                next_action={'workflow': 'review-assignment', 'scope': {'evidence': 'new source'},
                             'prerequisite': 'new evidence'})],
                recovery_work=[dict(workflow='review-assignment', command='review-assignment',
                                   payload={'scope': 'new evidence'}, text='Supply the missing evidence.')])


class ReviewerTextContracts(unittest.TestCase):
    def test_quotations_bind_nested_normalized_short_and_unknown_sources_without_mutation(self):
        value = epoch()
        source = staged.make_source('quote', 'record', 'α  ﬁ\nβ🙂', identity={'id': 'quote'})
        spec = {k: copy.deepcopy(v) for k, v in value.items()
                if k not in ('digest', 'material_objection_entries')}
        spec['manifest'] = staged.build_manifest({}, [], [source])
        value = staged.freeze_epoch(spec)
        original = {'records': [{'grounds': [{'source_id': 'quote', 'text': 'fi β🙂'}]},
                                {'grounds': [{'source_id': 'quote', 'text': 'absent'}]},
                                {'grounds': [{'source_id': 'missing', 'text': 'x'}]},
                                {'grounds': [{'source_id': 'quote', 'text': 'f'}]}]}
        before = copy.deepcopy(original)
        bound, unmatched = staged.bind_quotations(original, epoch=value, sources=[source])
        self.assertEqual(original, before)
        span = bound['records'][0]['grounds'][0]
        self.assertEqual(staged.materialize_quotation(source, span)['text'], 'ﬁ\nβ🙂')
        self.assertEqual(unmatched, [original['records'][1]['grounds'][0], original['records'][2]['grounds'][0], original['records'][3]['grounds'][0]])
        self.assertEqual(staged.bind_quotations(bound, epoch=value, sources={'quote': source})[0], bound)

    def test_binding_happens_after_raw_digest_authentication_and_before_validation(self):
        value = epoch()
        card = full_card(value)
        card.pop('critical_quotations')
        source = next(s for s in value['manifest']['sources'] if s['id'] == card['critical_spans'][0]['source_id'])
        quote = dict(source_id=source['id'], text=source['text'])
        card['critical_spans'] = [quote]
        card['conclusions'][0]['grounds'] = [quote]
        for cell in card['judgments']:
            cell['grounds'] = [quote]
        assigned = task(value)
        captured = observation(value, assigned, {'cards': [card]})
        report = staged.verify_completion(value, {'observations': [captured]}, tasks=[assigned])
        self.assertEqual(report['progress']['full_cards'], 1, report['issues'])
        self.assertEqual(report['progress']['claim_cells'], 2, report['issues'])
        captured['raw_output_digest'] = digest({})
        self.assertEqual(staged.verify_completion(value, {'observations': [captured]}, tasks=[assigned])['progress']['full_cards'], 0)

    def test_bad_quotation_invalidates_its_record_and_keeps_other_units(self):
        value = epoch(count=2)
        cards = [full_card(value, i) for i in range(2)]
        cards[1].pop('critical_quotations')
        cards[1]['critical_spans'] = [{'source_id': cards[1]['critical_spans'][0]['source_id'], 'text': 'absent quotation'}]
        assigned = task(value)
        report = staged.verify_completion(value, {'observations': [observation(value, assigned, {'cards': cards})]}, tasks=[assigned])
        self.assertEqual(report['progress']['full_cards'], 1, report['issues'])
        self.assertIn('review_card_pending', [r['code'] for r in report['issues']])

    def test_ordinary_projection_result_and_adjudication_are_isolated(self):
        for role in ('result', 'adjudicator'):
            _, _, _, expected = make_fixture(output=worked_adjudication() if role == 'adjudicator' else None, role=role)
            raw = dict(expected, terminal={'composition_root': None}, answers=[])
            scientific, records = staged.project_ordinary_final(raw)
            self.assertEqual(scientific, expected)
            self.assertEqual(records, {'terminal': raw['terminal'], 'answers': []})
            selection_record = dict(controller_id='controller', kind='ordinary', output=raw,
                                    output_digest=digest(expected), terminal_attempt_id='attempt',
                                    terminal_raw_output_digest=digest(raw))
            self.assertEqual(composition.effective_review_output('controller', selection_record)['effective_output'], expected)
            scientific.clear()
            self.assertIn('terminal', raw)

    def test_completion_terminal_derives_the_exact_composition_choices(self):
        for role in ('result', 'adjudicator'):
            plan, observations, choices, expected = make_fixture(output=worked_adjudication() if role == 'adjudicator' else None, role=role)
            root = composition.build_root(plan, observations)
            terminal = dict(composition_root=root['digest'], integration_id='integration', visible_conditions=[], decisions=choices)
            terminal.update({'assurances': {kind: choices['/value/assurances/' + kind + '/status'] for kind in staged.ASSURANCES}}
                            if role == 'result' else {key: choices['/' + key] for key in ('objection_id', 'disposition', 'correction_admissible')})
            scope = dict(id=plan['epoch_id'], role=role)
            derived = staged.derive_composition_terminal(scope, terminal)
            self.assertEqual(derived, dict(protocol=composition.PROTOCOL, epoch_id=plan['epoch_id'], role=role,
                                          composition_root=root['digest'], choices=choices))
            self.assertEqual(composition.assemble_result(plan, observations, root, terminal), expected)
            effective = composition.effective_review_output('controller', dict(controller_id='controller', kind='paged',
                plan=plan, observations=observations, root=root, terminal=observe({'terminal': terminal}, 'terminal-attempt'), completion_root=digest('completion')))
            self.assertEqual(effective['effective_output'], expected)

    def test_claim_range_reads_have_one_planned_reconciliation_id(self):
        value = epoch()
        plan = staged.plan_tasks(value, route_limit=10000, output_tokens=17,
                                 profiles={'read': profile(output_tokens_per_claim=10)})
        reads = [r for r in plan['tasks'] if r['type'] == 'read']
        reconciliations = [r for r in plan['tasks'] if r['type'] == 'integrate']
        self.assertEqual(len(reads), 2)
        self.assertEqual(len(reconciliations), 1)
        reconcile = reconciliations[0]
        self.assertEqual(set(reconcile['segment_task_ids']), {r['id'] for r in reads})
        self.assertEqual(reconcile['claim_ids'], list(value['manifest']['contexts']))
        self.assertTrue(reconcile['reconciliation_id'])
        self.assertTrue(all(r['reconciliation_id'] == reconcile['reconciliation_id'] for r in reads))

    def test_accepted_partial_read_replans_only_unreached_units_and_claims(self):
        value = epoch(count=2)
        assigned = task(value)
        card = full_card(value)
        card['kind'] = 'fragment'
        card['judgments'] = card['judgments'][:1]
        captured = observation(value, assigned, {'cards': [card], 'cells': card['judgments']})
        plan = staged.plan_tasks(value, route_limit=10000, output_tokens=100,
                                 profiles={'read': profile()}, tasks=[assigned], selection={'observations': [captured]})
        reads = [r for r in plan['tasks'] if r['type'] == 'read']
        first, second = [r['id'] for r in value['manifest']['occurrences']]
        self.assertEqual([r['claim_ids'] for r in reads if first in r['occurrence_ids']], [[value['manifest']['claims'][1]['id']]])
        self.assertEqual([r['claim_ids'] for r in reads if second in r['occurrence_ids']], [list(value['manifest']['contexts'])])
        self.assertNotIn(assigned['id'], [r['id'] for r in reads])

    def test_compact_cards_retain_negative_scope_subject_and_finding_bodies(self):
        value = epoch()
        card = full_card(value)
        card.update(evidence_type='reviewer_observation', negative_observation=True,
                    examined_scope='The complete item.', no_statement_reason='It gives no decisive theorem.')
        card['judgments'][0].update(status='no_bearing', source_subject='Bibliographic data only.')
        finding = dict(id='finding', text='The source does not establish the scope.', adverse=True,
                       classification={'decisions': ['/value/status'], 'reason': 'The scope is material.'},
                       claim_ids=[card['judgments'][0]['claim_id']], grounds=card['critical_spans'])
        card['findings'] = [finding['id']]
        compact = staged.compact_card(card, manifest=value['manifest'], findings=[finding])
        self.assertEqual(compact['finding_bodies'], [finding])
        self.assertEqual(compact['judgments'][0]['source_subject'], card['judgments'][0]['source_subject'])
        for field in ('negative_observation', 'examined_scope', 'no_statement_reason'):
            self.assertEqual(compact[field], card[field])
        self.assertEqual(compact['conclusions'][0]['grounds'], card['conclusions'][0]['grounds'])

    def test_findings_are_typed_and_answered_requires_grounds(self):
        value = epoch()
        card = full_card(value)
        finding = dict(id='finding', text='The parameter restriction matters.', adverse=True,
                       classification={'decisions': ['/value/status'], 'reason': 'It restricts the claim.'},
                       claim_ids=[card['judgments'][0]['claim_id']], grounds=card['critical_spans'])
        self.assertEqual(staged.validate_finding(finding, value['manifest']), finding)
        for field, malformed in [('adverse', 'true'), ('classification', {'decisions': ['made-up'], 'reason': 'x'}),
                                 ('claim_ids', ['missing']), ('text', '')]:
            with self.subTest(field=field), self.assertRaises(ResearchError):
                staged.validate_finding(dict(finding, **{field: malformed}), value['manifest'])
        tasks, selected = selection(value)
        out = selected['observations'][0]['output']
        out['findings'] = [finding]
        out['answers'] = [dict(id='answer', finding_id='finding', disposition='answered', reason='Addressed.', grounds=[])]
        out['integrations'][0].update(findings=['finding'], answers=['answer'])
        selected['observations'][0]['raw_output_digest'] = digest(out)
        report = staged.verify_completion(value, selected, tasks=tasks)
        self.assertEqual(report['progress']['answered_findings'], 0)

    def test_final_incomplete_shape_checks_the_same_scope_and_recovery_contract(self):
        assigned = task(epoch(), 'final')
        valid = {'assessment_incomplete': incomplete_output()}
        staged.validate_task_output(assigned, valid)
        variants = [None, {}, dict(incomplete_output(), unresolved_scope=[])]
        bad = incomplete_output()
        bad['unresolved_scope'][0]['next_action'] = {'workflow': 'review-run'}
        variants.append(bad)
        for invalid in variants:
            with self.subTest(invalid=invalid), self.assertRaises(ResearchError):
                staged.validate_task_output(assigned, {'assessment_incomplete': invalid})

    def test_segment_readings_cover_the_assigned_ranges(self):
        value = epoch()
        assigned = staged.make_task(value, 'combine', scope='card_segment', card_ids=['card'],
                                   card_segments=[dict(card_id='card', start=2, end=7, characters=10)])
        reading = dict(id='reading', card_id='card', start=2, end=7, reading='The segment states the condition.')
        staged.validate_task_output(assigned, {'segment_readings': [reading]})
        for changed in (dict(reading, reading=''), dict(reading, end=8), dict(reading, card_id='other'), {'id': 'reading'}):
            with self.subTest(changed=changed), self.assertRaises(ResearchError):
                staged.validate_task_output(assigned, {'segment_readings': [changed]})

    def test_frame_entries_cover_all_assigned_fields_and_validate_kinds(self):
        value = epoch()
        assigned = task(value, 'frame')
        claims = [dict(c, kind='claim') for c in value['manifest']['claims']]
        output = dict(claims=claims, conditions=[], dependencies=[], unresolved=[])
        staged.validate_task_output(assigned, output, manifest=value['manifest'])
        for mutate in ('kind', 'span', 'dependency', 'unresolved'):
            changed = copy.deepcopy(output)
            if mutate == 'kind':
                changed['claims'][0]['kind'] = 'anything'
            elif mutate == 'span':
                changed['claims'].pop()
            elif mutate == 'dependency':
                changed['dependencies'] = [{'claim_id': claims[0]['id']}]
            else:
                changed['unresolved'] = [{'text': 'No definition.'}]
            with self.subTest(mutate=mutate), self.assertRaises(ResearchError):
                staged.validate_task_output(assigned, changed, manifest=value['manifest'])
        sources = value['manifest']['sources']
        with self.assertRaises(ResearchError):
            staged.build_manifest(value['manifest']['frame'], [], [], claims=[dict(c, kind='invented') for c in claims])

    def test_integration_id_lists_refer_to_real_findings_and_top_level_answers(self):
        value = epoch()
        for field, invalid in [('findings', 'finding'), ('answers', [{}]), ('answers', ['missing-answer'])]:
            tasks, selected = selection(value)
            selected['observations'][0]['output']['integrations'][0][field] = invalid
            selected['observations'][0]['raw_output_digest'] = digest(selected['observations'][0]['output'])
            with self.subTest(field=field):
                report = staged.verify_completion(value, selected, tasks=tasks)
                self.assertEqual(report['progress']['integrated_obligations'], 0)

    def test_record_roots_bind_long_receipt_lists_and_terminal_conditions(self):
        value = epoch()
        card = full_card(value)
        card['conditions'] = [dict(id='condition', text='Prime inputs only.',
                                   grounds=card['critical_spans'], satisfied=True)]
        tasks, selected = selection(value, [card])
        out = selected['observations'][0]['output']
        receipt = out['integrations'][0]
        for key in ('selected_observations', 'input_roots', 'child_union', 'obligation_ids'):
            receipt.pop(key)
        bound = staged.bind_integration_lists(receipt, epoch=value, task=tasks[1], cards=[card], children=[],
                                               observations=[selected['observations'][1]])
        receipt['record_root'] = bound['record_root']
        terminal = out['terminal']
        terminal.pop('visible_conditions')
        terminal['record_root'] = staged.bind_terminal_conditions(value, terminal, cards=[card], integration=bound)['record_root']
        selected['observations'][0]['raw_output_digest'] = digest(out)
        report = staged.verify_completion(value, selected, tasks=tasks)
        self.assertTrue(report['complete'], report['issues'])
        terminal['record_root'] = digest('wrong root')
        selected['observations'][0]['raw_output_digest'] = digest(out)
        self.assertFalse(staged.verify_completion(value, selected, tasks=tasks)['complete'])

    def test_pending_retrieval_is_superseded_only_by_the_bound_answer(self):
        value = epoch()
        assigned = task(value, 'final')
        request = dict(id='retrieval', question='Does the original state the hypothesis?', inputs=assigned['inputs'],
                       claim_ids=assigned['claim_ids'], context_roots=assigned['context_roots'], status='pending',
                       answer=None, observation_ids=[], requested_by=assigned['id'])
        first = observation(value, assigned, {'retrievals': [request]})
        answer_task = task(value, 'final', dependencies=[assigned['id']])
        second = observation(value, answer_task, {'retrievals': [dict(request, status='resolved', answer='It does.',
                             observation_ids=['observation-' + answer_task['id']])]},
                             dependencies={assigned['id']: first['raw_output_digest']})
        report = staged.verify_completion(value, {'observations': [second, first]}, tasks=[assigned, answer_task])
        self.assertNotIn('review_retrieval_pending', [r['code'] for r in report['issues']])
        self.assertNotIn('Conflicting selected scientific records.', str(report['issues']))

    def test_incomplete_composition_planner_assigns_pages_and_reviewer_origin_terminal(self):
        value = epoch()
        plan = composition.plan_incomplete_composition(value, outcome=incomplete_output(), known_conclusions=[], findings=[],
               source_root=value['manifest']['digest'], dependency_root=digest('dependencies'),
               selected_observations_root=digest('observations'), integration_root=digest('integration'), page_characters=1000)
        self.assertEqual(plan['role'], 'incomplete')
        self.assertEqual(plan['terminal_choices'], {'/origin': ['reviewer']})
        tasks = staged.plan_tasks(value, route_limit=10000, output_tokens=100, profiles={}, composition_plan=plan)['tasks']
        self.assertEqual(len([t for t in tasks if t['type'] == 'answer']), len(plan['required_pages']))
        terminal = next(t for t in tasks if t['type'] == 'final')
        self.assertEqual(terminal['composition_role'], 'incomplete')
        self.assertEqual(set(terminal['dependencies']), {t['id'] for t in tasks if t['type'] == 'answer'})

    def test_claim_range_fragments_and_reconciliation_receive_completion_credit(self):
        value = epoch()
        plan = staged.plan_tasks(value, route_limit=10000, output_tokens=17,
                                 profiles={'read': profile(output_tokens_per_claim=10)})
        tasks, observed, fragments = [], [], []
        for index, draft in enumerate(t for t in plan['tasks'] if t['type'] == 'read'):
            frozen = staged.freeze_task(draft, digest(draft), staged.SERIALIZATION)
            fragment = full_card(value)
            fragment.update(id='fragment-' + str(index), kind='fragment')
            fragment['conclusions'][0]['id'] = 'fragment-conclusion-' + str(index)
            fragment['judgments'] = [dict(c, id='fragment-cell-' + str(index)) for c in fragment['judgments']
                                     if c['claim_id'] in frozen['claim_ids']]
            fragments.append(fragment)
            tasks.append(frozen)
            observed.append(observation(value, frozen, {'cards': [fragment]}))
        draft = next(t for t in plan['tasks'] if t['type'] == 'integrate')
        frozen = staged.freeze_task(draft, digest(draft), staged.SERIALIZATION,
                                   measurement=staged.joint_fit([10], 10000, 100, profile()))
        card = full_card(value)
        card.update(fragment_ids=[c['id'] for c in fragments], reconciliation_id=frozen['reconciliation_id'])
        for cell in card['judgments']:
            cell.update(scope='segmented_original', reconciliation_id=frozen['reconciliation_id'])
        check = dict(id=frozen['reconciliation_id'], task_id=frozen['id'], group_ids=[], scope='segmented_original',
                     status='resolved', conclusions=[dict(id='reconciled', text='The ranges have consistent scope.',
                     grounds=card['critical_spans'])], qualifications=[], conflicts=[], unresolved=[],
                     segment_ids=frozen['segment_task_ids'], reconciliation='Each claim has one complete original reading.')
        captured = observation(value, frozen, {'cards': [card], 'original_checks': [check]},
                               dependencies={o['task_id']: o['raw_output_digest'] for o in observed})
        report = staged.verify_completion(value, {'observations': observed + [captured]}, tasks=tasks + [frozen])
        self.assertEqual(report['progress']['full_cards'], 1, report['issues'])
        self.assertEqual(report['progress']['claim_cells'], 2, report['issues'])
        self.assertFalse(any(r['code'] in ('invalid_review_staged', 'review_card_pending') for r in report['issues']), report['issues'])

    def test_original_segments_validate_grounded_provisional_conclusions_and_qualifications(self):
        value = epoch()
        group = dict(id='group', occurrence_ids=[value['manifest']['occurrences'][0]['id']],
                     claim_ids=list(value['manifest']['contexts']), question='Does the condition hold?', requested_by=['combine'])
        assigned = task(value, 'original_check', group_ids=['group'], scope='original_segment')
        check = dict(id=assigned['id'], task_id=assigned['id'], group_ids=['group'], scope='original_segment',
                     status='unresolved', conclusions=[dict(id='provisional', text='This segment states the hypothesis.',
                     grounds=assigned['inputs'][:1])], qualifications=[dict(occurrence_id=group['occurrence_ids'][0],
                     text='The source restricts the parameter.', grounds=assigned['inputs'][:1])], conflicts=[],
                     unresolved=[], segment_ids=[], reconciliation=None)
        captured = observation(value, assigned, {'original_checks': [check]})
        staged.validate_original_check(check, epoch=value, task=assigned, observations=[captured], groups=[group])
        check['qualifications'][0]['grounds'][0] = dict(assigned['inputs'][0], sha256=digest('invented'))
        with self.assertRaises(ResearchError):
            staged.validate_original_check(check, epoch=value, task=assigned, observations=[captured], groups=[group])

    def test_correction_admissibility_rejects_current_evidence_even_when_mislabeled(self):
        for evidence_class in ('original_context', 'current'):
            value = epoch(role='adjudicator', focused_scope=dict(review_pair_roots=['a', 'b'],
                          objection_id='O7', objection_version='version'))
            spec = {k: copy.deepcopy(v) for k, v in value.items() if k not in ('digest', 'material_objection_entries')}
            spec['manifest'] = staged.build_manifest(value['manifest']['frame'], [dict(key='readings', index=0,
                item={'text': 'The correction is an original assessment error.'}, artifacts=[], source_ids=[],
                evidence_class=evidence_class)], [])
            value = staged.freeze_epoch(spec)
            card = full_card(value)
            card['evidence_class'] = evidence_class
            for cell in card['judgments']:
                cell['evidence_class'] = evidence_class
            terminal = dict(objection_id='O7', disposition='upheld', correction_admissible=True, visible_conditions=[])
            tasks, selected = selection(value, [card], terminal=terminal)
            output = selected['observations'][0]['output']
            output['decision_checks'].append(dict(id='correction-check', decision='/correction_admissible', value=True,
                claim_ids=list(value['manifest']['contexts']), reason='The supplied original assessment contains the error.',
                grounds=[card['judgments'][0]['id']], dependencies=[]))
            selected['observations'][0]['raw_output_digest'] = digest(output)
            report = staged.verify_completion(value, selected, tasks=tasks)
            self.assertEqual(report['complete'], evidence_class == 'original_context', report['issues'])
            card['evidence_class'] = 'current' if evidence_class == 'original_context' else 'original_context'
            with self.assertRaises(ResearchError):
                staged.validate_card(card, value['manifest'])

    def test_shared_body_supports_correction_only_through_its_original_context(self):
        descriptor = describe_artifact(b'The exact original evidence.', 'text/plain')
        source = staged.make_source('shared-body', 'artifact', 'The exact original evidence.',
                                    identity={'artifact': descriptor}, descriptor=descriptor)
        occurrences = [dict(key=evidence_class, index=0, item={'context': evidence_class},
                            artifacts=[descriptor], source_ids=[], evidence_class=evidence_class)
                       for evidence_class in ('original_context', 'current')]
        manifest = staged.build_manifest({'statement': 'The correction concerns the supplied evidence.'},
                                         occurrences, [source])
        value = epoch(role='adjudicator', manifest=manifest, focused_scope=dict(
            review_pair_roots=['a', 'b'], objection_id='O7', objection_version='version'))
        cards = [full_card(value, index) for index in range(2)]
        for card, evidence_class in zip(cards, ('original_context', 'current')):
            card['evidence_class'] = evidence_class
            for cell in card['judgments']:
                cell['evidence_class'] = evidence_class
        terminal = dict(objection_id='O7', disposition='upheld', correction_admissible=True, visible_conditions=[])
        original_span = staged.source_span(source, 0, source['characters'])
        current_item = next(row for row in manifest['sources']
                            if row['id'] == manifest['occurrences'][1]['item_source_id'])
        current_span = staged.source_span(current_item, 0, current_item['characters'])
        cases = [('original conclusion', [cards[0]['judgments'][0]['id']], True),
                 ('original body', [original_span], True),
                 ('current conclusion', [cards[1]['judgments'][0]['id']], False),
                 ('current-only item', [current_span], False)]
        for label, grounds, expected in cases:
            with self.subTest(grounds=label):
                tasks, selected = selection(value, cards, terminal=terminal)
                output = selected['observations'][0]['output']
                output['decision_checks'].append(dict(id='correction-check', decision='/correction_admissible',
                    value=True, claim_ids=list(value['manifest']['contexts']),
                    reason='The original evidence establishes the omitted condition.', grounds=grounds, dependencies=[]))
                selected['observations'][0]['raw_output_digest'] = digest(output)
                report = staged.verify_completion(value, selected, tasks=tasks)
                self.assertEqual(report['complete'], expected, report['issues'])

    def test_incomplete_pages_assemble_with_the_same_terminal_shape(self):
        value = epoch()
        outcome = incomplete_output()
        plan = composition.plan_incomplete_composition(value, outcome=outcome, known_conclusions=[], findings=[],
               source_root=value['manifest']['digest'], dependency_root=digest('dependencies'),
               selected_observations_root=digest('observations'), integration_root=digest('integration'), page_characters=1000)
        content = dict(outcome, known_conclusions=[], findings=[])
        observed = []
        source = value['manifest']['sources'][0]
        ground = staged.source_span(source, 0, source['characters'])
        for page_id in plan['required_pages']:
            owners = [o for o in plan['ownership'] if o['page_id'] == page_id]
            fragments = [dict(o, content=([] if o['type'] == 'empty_list' else content[o['path'][1:]][o['ordinal']]),
                              grounds=[ground]) for o in owners]
            page = dict(protocol=composition.PROTOCOL, epoch_id=value['id'], role='incomplete', task_id=page_id,
                        page_id=page_id, revision=0, source_root=plan['source_root'], dependency_root=plan['dependency_root'],
                        ownership=owners, fragments=fragments, grounding=[ground], findings=[], dispositions=[], replaces=None)
            observed.append(observe(page))
        root = composition.build_root(plan, observed)
        terminal = dict(origin='reviewer', composition_root=root['digest'], decisions={'/origin': 'reviewer'})
        expected = dict(content, origin='reviewer')
        self.assertEqual(composition.assemble_result(plan, observed, root, terminal), expected)

    def test_constraint_keys_are_scientific_scalar_paths(self):
        with self.assertRaises(ResearchError):
            epoch(completion_requirements={'permissible_decisions': {'calibration': ['pass']}})
        value = epoch(completion_requirements={'permissible_decisions': {'/value/rubric/calibration': ['pass']}})
        self.assertIn('/value/rubric/calibration', value['completion_requirements']['permissible_decisions'])

    def test_segment_followup_keeps_its_reconciliation_identity_and_skips_read_scope(self):
        value = epoch()
        kwargs = dict(route_limit=10000, output_tokens=100, profiles={'read': profile(input_character_target=20)})
        initial = staged.plan_tasks(value, **kwargs)
        draft = next(t for t in initial['tasks'] if t['type'] == 'read' and t['scope'] == 'original_segment')
        frozen = staged.freeze_task(draft, digest(draft), staged.SERIALIZATION)
        fragment = full_card(value)
        fragment.pop('critical_quotations')
        fragment.update(id='segment-fragment', kind='fragment', critical_spans=frozen['inputs'][:1])
        fragment['conclusions'][0]['grounds'] = frozen['inputs'][:1]
        fragment['judgments'] = [dict(c, scope='segmented_original', reconciliation_id=frozen['reconciliation_id'],
                                     grounds=frozen['inputs'][:1]) for c in fragment['judgments'] if c['claim_id'] in frozen['claim_ids']]
        captured = observation(value, frozen, {'cards': [fragment]})
        followup = staged.plan_tasks(value, tasks=[frozen], selection={'observations': [captured]}, **kwargs)
        self.assertTrue(all(t['reconciliation_id'] == frozen['reconciliation_id'] for t in followup['tasks']))
        for read in (t for t in followup['tasks'] if t['type'] == 'read'):
            self.assertNotEqual((read['inputs'], read['claim_ids']), (frozen['inputs'], frozen['claim_ids']))
        self.assertIn(frozen['id'], next(t for t in followup['tasks'] if t['type'] == 'integrate')['dependencies'])

    def test_quote_from_an_undelivered_original_does_not_complete_a_card(self):
        value = epoch(count=2)
        draft = staged.plan_tasks(value, route_limit=10000, output_tokens=100, profiles={'read': profile()})['tasks'][0]
        frozen = staged.freeze_task(draft, digest(draft), staged.SERIALIZATION)
        card = full_card(value)
        other = full_card(value, 1)
        card.pop('critical_quotations')
        card['critical_spans'] = other['critical_spans']
        captured = observation(value, frozen, {'cards': [card]})
        report = staged.verify_completion(value, {'observations': [captured]}, tasks=[frozen])
        self.assertEqual(report['progress']['full_cards'], 0, report['issues'])

    def test_retrieval_cannot_change_the_original_question_when_resolving(self):
        value = epoch()
        assigned = task(value, 'final')
        request = dict(id='retrieval', question='Does the original state the hypothesis?', inputs=assigned['inputs'],
                       claim_ids=assigned['claim_ids'], context_roots=assigned['context_roots'], status='pending',
                       answer=None, observation_ids=[], requested_by=assigned['id'])
        first = observation(value, assigned, {'retrievals': [request]})
        answering = task(value, 'final', dependencies=[assigned['id']])
        second = observation(value, answering, {'retrievals': [dict(request, question='A different question.', status='resolved',
                             answer='It does.', observation_ids=['observation-' + answering['id']])]},
                             dependencies={assigned['id']: first['raw_output_digest']})
        report = staged.verify_completion(value, {'observations': [first, second]}, tasks=[assigned, answering])
        self.assertIn('Conflicting retrieval lifecycle records.', str(report['issues']))

    def test_partial_card_earns_no_card_credit_while_a_complete_sibling_stands(self):
        value = epoch(count=2)
        assigned = task(value)
        cards = [full_card(value, index) for index in range(2)]
        cards[1]['judgments'].pop()
        captured = observation(value, assigned, {'cards': cards})
        report = staged.verify_completion(value, {'observations': [captured]}, tasks=[assigned])
        self.assertEqual(report['progress']['full_cards'], 1, report['issues'])
        self.assertEqual(report['progress']['claim_cells'], len(cards[0]['judgments']), report['issues'])

    def test_completed_work_with_an_assessed_absence_can_finish_not_assessed(self):
        value = epoch()
        terminal = dict(assurances={kind: 'not_assessed' for kind in staged.ASSURANCES}, visible_conditions=[])
        tasks, selected = selection(value, terminal=terminal)
        for check in selected['observations'][0]['output']['assurance_checks']:
            check['reason'] = 'All assigned work is complete; the record supplies no decisive assessment for this criterion.'
        selected['observations'][0]['raw_output_digest'] = digest(selected['observations'][0]['output'])
        report = staged.verify_completion(value, selected, tasks=tasks)
        self.assertTrue(report['complete'], report['issues'])


if __name__ == '__main__':
    unittest.main()
