"""Bindings locate each page answer field in the complete review."""
import copy
import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from research_harness import review_composition as composition
from research_harness.errors import ResearchError
from test_review_composition import build_final_rule_cases


class PagePointerTests(unittest.TestCase):
    def build_result_pages(self, session=False):
        role, packet, output = build_final_rule_cases()[2]
        evidence = output['value']['evidence']
        for number in range(3):
            identity = 'objection-%d' % number
            packet['material_objections'].append(dict(id=identity))
            output['value']['objection_findings'].append(dict(
                id=identity, status='continuing', reason='A synthetic gap remains.', evidence=evidence))
        rule = composition.find_rule(role, packet)
        targets = composition.list_targets(rule, packet)
        if session:
            packet['session_targets'] = [dict(row, handle='t%d' % number)
                                         for number, row in enumerate(targets, 1)]
            targets = composition.list_targets(rule, packet)
        groups = [targets[:-2], targets[-2:]]
        pages, _ = composition.split_output(rule, packet, output, groups)
        return rule, packet, pages

    def check_refusal(self, rule, packet, page, *, missing=(), unexpected=()):
        with self.assertRaises(ResearchError) as caught:
            composition.validate_page_output(rule, packet, page['targets'], page['output'])
        error = caught.exception
        self.assertEqual(error.code, 'review_composition_conflict')
        self.assertEqual(error.contract_check, 'review.shape')
        self.assertEqual(error.details['path'], '/targets/0/fields')
        self.assertEqual(error.details['missing_fields'], sorted(missing))
        self.assertEqual(error.details['unexpected_fields'], sorted(unexpected))

    def test_restarted_indices_are_refused_in_both_families(self):
        for session in (False, True):
            with self.subTest(session=session):
                rule, packet, pages = self.build_result_pages(session)
                page = pages[1]
                composition.validate_page_output(rule, packet, page['targets'], page['output'])
                first = page['output']['targets'][0]['fields']
                for index, row in enumerate(page['output']['targets']):
                    row['fields'] = {path.replace('/%d/' % (index + 1), '/%d/' % index): value
                                     for path, value in row['fields'].items()}
                self.check_refusal(rule, packet, page, missing=first,
                                   unexpected=page['output']['targets'][0]['fields'])

    def test_refused_evidence_of_a_page_field_is_located_in_the_page_answer(self):
        """Repairs A17. A field of a page whose evidence the identifier check refuses is named by its pointer in the
        page answer, in a series of the parts protocol v3 and in a session of the rules v4."""
        from research_harness.artifacts import describe_artifact
        link = dict(kind='source', link=dict(version_id='https://[', source_id='capture', locator=dict(kind='artifact'),
                                             artifact=describe_artifact(b'A synthetic source.', 'text/plain')))
        for session in (False, True):
            with self.subTest(session=session):
                rule, packet, pages = self.build_result_pages(session)
                page = pages[1]
                page['output']['targets'][1]['fields']['/value/objection_findings/2/evidence'] = [link]
                with self.assertRaises(ResearchError) as caught:
                    composition.validate_page_output(rule, packet, page['targets'], page['output'])
                error = caught.exception
                self.assertEqual((error.code, error.contract_check, error.details['path']),
                                 ('invalid_identifier', 'evidence.shape',
                                  '/targets/1/fields/~1value~1objection_findings~12~1evidence'))

    def test_missing_empty_collection_and_descendant_pointer_are_refused(self):
        rule, packet, pages = self.build_result_pages()
        with self.subTest(case='missing collection'):
            pages[0]['output']['targets'][0]['fields'].pop('/value/work_items')
            self.check_refusal(rule, packet, pages[0], missing=['/value/work_items'])
        with self.subTest(case='descendant'):
            pointer = '/value/objection_findings/1/evidence'
            fields = pages[1]['output']['targets'][0]['fields']
            fields[pointer + '/0'] = fields.pop(pointer)[0]
            self.check_refusal(rule, packet, pages[1], missing=[pointer], unexpected=[pointer + '/0'])
        with self.subTest(case='fields is not an object'):
            pages[1]['output']['targets'][0]['fields'] = []
            self.check_refusal(rule, packet, pages[1], missing=[
                '/value/objection_findings/1/' + name for name in ('evidence', 'id', 'reason', 'status')])

    def test_foreign_field_and_nonempty_collection_have_shape_check(self):
        rule, packet, pages = self.build_result_pages()
        for pointer, value in (('/objections', []), ('/value/work_items', [{}])):
            with self.subTest(pointer=pointer):
                page = copy.deepcopy(pages[0])
                page['output']['targets'][0]['fields'][pointer] = value
                with self.assertRaises(ResearchError) as caught:
                    composition.validate_page_output(rule, packet, page['targets'], page['output'])
                self.assertEqual(caught.exception.code, 'review_composition_conflict')
                self.assertEqual(getattr(caught.exception, 'contract_check', None), 'review.shape')

    def test_pointers_match_split_output_for_every_rule_and_family(self):
        for role, packet, output in build_final_rule_cases():
            rule = composition.find_rule(role, packet)
            targets = composition.list_targets(rule, packet)
            if rule['id'].endswith(('/problem-openness', '/math-openness')):
                with self.subTest(rule=rule['id']):
                    self.assertEqual(composition.list_page_pointers(rule, packet, targets), {})
                continue
            for session in (False, True):
                with self.subTest(rule=rule['id'], session=session):
                    bound = self.bind_targets(packet, targets, session)
                    handles = ['t%d' % number for number in range(1, len(targets) + 1)]
                    pages, _ = composition.split_output(rule, bound, output, [handles])
                    pointers = composition.list_page_pointers(rule, bound, handles)
                    self.assertEqual(list(pointers), handles)
                    for target, handle, row in zip(targets, handles, pages[0]['output']['targets']):
                        self.assertEqual(row['target'], handle)
                        if target['id'] == 'openness':
                            self.assertEqual(row['fields'], {})
                            self.assertEqual(pointers[handle], [
                                '/findings/openness/' + name for name in
                                ('outcome', 'coverage', 'reason', 'evidence', 'searched_at', 'minimum')])
                        else:
                            self.assertCountEqual(pointers[handle], row['fields'])
                            for pointer, value in row['fields'].items():
                                self.assertEqual(composition._read_path(output, pointer), value)
                                member = pointer.rsplit('/', 1)[1]
                                if member in ('id', 'kind', 'dimension', 'review_id'):
                                    self.assertEqual(value, target['id'])
                                elif pointer.startswith(('/value/rubric/', '/value/rubric_evidence/', '/findings/')):
                                    self.assertEqual(member, target['id'])
                                elif pointer.startswith('/value/rubric_potential/'):
                                    self.assertEqual(member.replace('~1', '/').replace('~0', '~'), target['id'])

    def bind_targets(self, packet, targets, session):
        if session:
            return dict(packet, session_targets=[dict(row, handle='t%d' % number)
                                                  for number, row in enumerate(targets, 1)])
        return dict(packet, review_parts=dict(protocol='exactory-review-parts-v3',
                                              page_plan_digest='fixture', targets=targets))

    def test_empty_candidate_map_belongs_to_the_first_slate_target(self):
        role, packet, output = build_final_rule_cases()[1]
        packet['candidates'] = []
        output['value']['rubric_potential'] = {}
        rule = composition.find_rule(role, packet)
        first = composition.list_targets(rule, packet)[0]
        pages, _ = composition.split_output(rule, packet, output, [[first]])
        page = pages[0]
        composition.validate_page_output(rule, packet, page['targets'], page['output'])
        page['output']['targets'][0]['fields'].pop('/value/rubric_potential')
        self.check_refusal(rule, packet, page, missing=['/value/rubric_potential'])

    def test_candidate_id_is_escaped_in_its_pointer(self):
        packet = dict(role='slate', subject=dict(kind='problem'), candidates=[dict(id='candidate~/a')])
        rule = composition.find_rule('slate', packet)
        target = next(row for row in composition.list_targets(rule, packet) if row['id'] == 'candidate~/a')
        self.assertEqual(composition.list_page_pointers(rule, packet, [target]),
                         {'candidate~/a': ['/value/rubric_potential/candidate~0~1a']})

    def test_split_preserves_empty_evidence_on_its_entry(self):
        from test_research_problem_reviews import ProblemReviewTests
        reference = dict(kind='record', record_kind='reading', id='reading', digest='d' * 64)
        packet = dict(role='bar', subject=dict(kind='problem'), material_objections=[dict(id='a'), dict(id='b')])
        output = ProblemReviewTests.build_review_output(SimpleNamespace(supporting=reference), 'bar')
        output['value']['objection_findings'] = [
            dict(id=key, status='continuing', reason='A gap remains.', evidence=evidence)
            for key, evidence in [('a', [reference]), ('b', [])]]
        rule = composition.find_rule('bar', packet)
        pages, _ = composition.split_output(rule, packet, output, [['a'], ['b']])
        self.assertNotIn('/value/objection_findings/1/evidence', pages[0]['output']['targets'][0]['fields'])
        self.assertEqual(pages[1]['output']['targets'][0]['fields']['/value/objection_findings/1/evidence'], [])

    def test_missing_adjudication_evidence_is_a_shape_refusal(self):
        packet = dict(role='adjudicator', objection=dict(id='O7'))
        rule = composition.find_rule('adjudicator', packet)
        page = dict(targets=['objection'], output=dict(targets=[
            dict(target='objection', fields={'/reason': 'The objection remains.'}, sentence='Retain the objection.')]))
        self.check_refusal(rule, packet, page, missing=['/evidence'])

    def test_role_refusal_uses_original_index_of_compacted_entry(self):
        rule, packet, pages = self.build_result_pages()
        pointer = '/value/objection_findings/1/status'
        pages[1]['output']['targets'][0]['fields'][pointer] = 'invalid'
        with self.assertRaises(ResearchError) as caught:
            composition.validate_page_output(rule, packet, pages[1]['targets'], pages[1]['output'])
        self.assertEqual(caught.exception.contract_check, 'review.value')
        # Repairs A17: the refused field is named by its pointer in the page answer.
        self.assertEqual(caught.exception.details['path'], '/targets/0/fields/~1value~1objection_findings~11~1status')


    def test_bound_handles_and_explicit_ids_are_distinct_in_pointer_and_decision_rows(self):
        role, packet, output = build_final_rule_cases()[2]
        evidence = output['value']['evidence']
        for identifier in ('earlier', 't19', 't1'):
            packet['material_objections'].append(dict(id=identifier))
            output['value']['objection_findings'].append(dict(
                id=identifier, status='continuing', reason='A gap remains.', evidence=evidence))
        rule = composition.find_rule(role, packet)
        definitions = composition.list_targets(rule, packet)
        for session in (False, True):
            bound = self.bind_targets(packet, definitions, session)
            for index, (target, path) in enumerate([
                    (definitions[0], '/value/assurances/0/kind'),
                    (definitions[18], '/value/objection_findings/0/id'),
                    (definitions[19], '/value/objection_findings/1/id'),
                    (definitions[20], '/value/objection_findings/2/id')]):
                handle = ['t1', 't19', 't20', 't21'][index]
                with self.subTest(session=session, handle=handle):
                    pointers = composition.list_page_pointers(rule, bound, [handle])
                    self.assertIn(path, pointers[handle])
                    self.assertIn(path, composition.list_page_pointers(rule, bound, [target])[target['id']])
                    pages, _ = composition.split_output(rule, bound, output, [[target]])
                    self.assertEqual(pages[0]['output']['targets'][0]['target'], handle)
                    composition.validate_page_output(rule, bound, [handle], pages[0]['output'])
                    self.assertEqual(composition.build_decision_rows(rule, bound, pages)[0][0], handle)

    def test_study_slate_owns_historical_bar_objections(self):
        from strategy_fixtures import StrategyCase
        source = dict(kind='record', record_kind='reading', id='reading', digest='d' * 64)
        for identifier in ('historical-only', 'impact'):
            role = 'slate'
            packet = dict(role=role, bar_ids=['bar-a', 'bar-b'],
                historical_bars=[dict(review=dict(id='prior-bar', objections=[dict(id=identifier)]))])
            complete = StrategyCase.review_response(SimpleNamespace(evidence=source), role)
            complete['value']['objection_findings'] = [dict(
                id=identifier, status='continuing', reason='An inherited gap.', evidence=[source])]
            rule = composition.find_rule(role, packet)
            definitions = composition.list_targets(rule, packet)
            for session in (False, True):
                with self.subTest(identifier=identifier, session=session):
                    ids = [t['id'] for t in definitions]
                    self.assertEqual(ids.count(identifier), 1)
                    bound = self.bind_targets(packet, definitions, session)
                    groups = [[definition] for definition in definitions]
                    pages, closing = composition.split_output(rule, bound, complete, groups)
                    pointer = '/value/objection_findings/0/id'
                    handle = 't%d' % (1 + ids.index(identifier))
                    self.assertIn(pointer, composition.list_page_pointers(rule, bound, [handle])[handle])
                    for page in pages:
                        composition.validate_page_output(rule, bound, page['targets'], page['output'])
                    composition.validate_closing_output(rule, bound, [], closing)
                    self.assertEqual(composition.compose_output(rule, bound, pages, closing), complete)

    def test_study_bar_page_uses_inherited_objection_membership(self):
        from strategy_fixtures import StrategyCase
        reference = dict(kind='record', record_kind='reading', id='reading', digest='d' * 64)
        packet = dict(role='bar', context_repair=dict(assignment_id='parent'),
            historical_reviews=[dict(id='old-review', payload=dict(objections=[dict(id='prior')]))])
        output = StrategyCase.review_response(SimpleNamespace(evidence=reference), 'bar')
        output['value']['objection_findings'] = [dict(
            id='prior', status='continuing', reason='The objection remains.', evidence=[reference])]
        output['reassessment'] = dict(assignment_id='parent', findings=[dict(
            review_id='old-review', disposition='confirmed', reason='It persists.', evidence=[reference])])
        rule = composition.find_rule('bar', packet)
        targets = composition.list_targets(rule, packet)
        for session in (False, True):
            with self.subTest(session=session):
                bound = self.bind_targets(packet, targets, session)
                pages, closing = composition.split_output(rule, bound, output, [[row] for row in targets])
                for page in pages:
                    composition.validate_page_output(rule, bound, page['targets'], page['output'])
                composition.validate_closing_output(rule, bound, [], closing)
                self.assertEqual(composition.compose_output(rule, bound, pages, closing), output)

    def test_study_readiness_closing_uses_frozen_objection_membership(self):
        role, packet, output = build_final_rule_cases()[12]
        rule = composition.find_rule(role, packet)
        packet['historical_bars'] = [dict(review=dict(id='older', objections=[dict(id='expected')]))]
        output['value']['objection_findings'] = [dict(
            id='expected', status='continuing', reason='The objection remains.', evidence=output['value']['evidence'])]
        targets = composition.list_targets(rule, packet)
        pages, closing = composition.split_output(rule, packet, output, [targets])
        composition.validate_closing_output(rule, packet, [], closing)
        self.assertEqual(composition.compose_output(rule, packet, pages, closing), output)
        closing['fields']['/value/objection_findings/0/id'] = 'other'
        with self.assertRaises(ResearchError) as caught:
            composition.validate_closing_output(rule, packet, [], closing)
        self.assertEqual(caught.exception.code, 'review_objection_history_incomplete')

    def test_role_refusals_identify_single_bad_field(self):
        cases = build_final_rule_cases()
        for index, pointer, check in [
                (12, '/support/checks/1/status', 'review.support'),
                (11, '/checks/1/reason', 'review.shape'),
                (4, '/reassessment/findings/0/disposition', 'review.reassessment'),
                (5, '/reason', 'adjudication.shape'),
                (2, '/value/rubric/admission/A1', 'review.value')]:
            role, packet, output = cases[index]
            rule = composition.find_rule(role, packet)
            definitions = composition.list_targets(rule, packet)
            pages, _ = composition.split_output(rule, packet, output, [[row] for row in definitions])
            with self.subTest(pointer=pointer):
                page = next(page for page in pages if pointer in page['output']['targets'][0]['fields'])
                page['output']['targets'][0]['fields'][pointer] = None
                with self.assertRaises(ResearchError) as caught:
                    composition.validate_page_output(rule, packet, page['targets'], page['output'])
                self.assertEqual(caught.exception.contract_check, check)
                # Repairs A17: a refusal of the value check names the field by its pointer in the page answer.
                located = ('/targets/0/fields/' + pointer.replace('~', '~0').replace('/', '~1')
                           if check == 'review.value' else pointer)
                self.assertEqual((caught.exception.details or {}).get('path'), located)

    def test_native_assessment_uses_bound_target_handle(self):
        role, packet, _ = build_final_rule_cases()[7]
        rule = composition.find_rule(role, packet)
        definitions = composition.list_targets(rule, packet)
        for session in (False, True):
            bound = self.bind_targets(packet, definitions, session)
            for decision in ('revise', 'reject'):
                with self.subTest(session=session, decision=decision):
                    output = dict(decision=decision, reason='The result needs revision.')
                    pages, closing = composition.split_output(rule, bound, output, [['t2']])
                    self.assertEqual(pages[0]['output']['targets'][0]['target'], 't2')
                    composition.validate_page_output(rule, bound, ['t2'], pages[0]['output'])
                    composition.validate_closing_output(rule, bound, [], closing)
                    self.assertEqual(composition.compose_output(rule, bound, pages, closing), output)

    def test_native_page_prompt_describes_conditional_openness_fields(self):
        # Rows P7 and P10 of the second writer pass: the rules of a native page are in the text of the native adapter.
        from research_harness.review_parts_prompts import FINAL_NATIVE_V3
        for text in ('schema_version 1', 'schema_version 2', '/findings/openness', 'revise', 'reject'):
            self.assertIn(text, FINAL_NATIVE_V3)

    def test_decision_rows_build_one_lookup_per_batch(self):
        count = 3000
        targets = [dict(id='o%d' % i, handle='t%d' % (i + 1)) for i in range(count)]
        packet = dict(role='bar', session_targets=targets, material_objections=[dict(id=t['id']) for t in targets])
        rule = composition.find_rule('bar', packet)
        page = dict(output=dict(targets=[dict(target=t['handle'], fields={}, sentence='Assessed.')
                                       for t in targets[:32]]))
        with patch.object(composition, '_index_target_ids', wraps=composition._index_target_ids) as lookup:
            rows = composition.build_decision_rows(rule, packet, [page])
        self.assertEqual([row[0] for row in rows], [target['handle'] for target in targets[:32]])
        self.assertEqual(lookup.call_count, 1)

    def test_shared_resolver_refuses_unhashable_and_oversized_keys(self):
        """Handoff of lane parts-v3-requests: a position with more digits than the targets is a refused target."""
        targets = [dict(id='first'), dict(id='second')]
        lookup = composition._index_target_ids(targets)
        self.assertEqual(composition._resolve_target_id(targets, 't01', lookup), 'second')
        for value in ([], {'id': []}, 't' + '9' * 5000):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(ResearchError) as caught:
                    composition._resolve_target_id(targets, value, lookup)
                self.assertEqual(caught.exception.code, 'review_composition_conflict')

    def test_unhashable_page_target_is_a_shape_conflict(self):
        """Handoff of lane parts-v3-requests: a page target that is a list or an object is refused by its shape."""
        packet = dict(role='slate', subject=dict(kind='problem'), candidates=[dict(id='candidate')])
        rule = composition.find_rule('slate', packet)
        for target in ([], {'id': []}):
            with self.subTest(target=target):
                output = dict(targets=[dict(target=target, fields={}, sentence='Reviewed.')])
                with self.assertRaises(ResearchError) as caught:
                    composition.validate_page_output(rule, packet, ['candidate'], output)
                self.assertEqual(caught.exception.code, 'review_composition_conflict')
                self.assertEqual(caught.exception.contract_check, 'review.shape')


class PagePointerJourneyTests(unittest.TestCase):
    """Exercise replacement and recording with page answers made by reviewer stubs."""

    def set_up_fixture(self, fixture):
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        return fixture

    def pin_two_pages(self, first_page_targets, *, room_for_19_targets=False):
        from unittest.mock import patch
        from research_harness import review_route_capacity as capacity
        plan_capacity = capacity.plan_chain_capacity
        material_levels = capacity.describe_material_levels

        def plan(*args, **kwargs):
            result = plan_capacity(*args, **kwargs)
            final = result['task'].get('final')
            if final is not None:
                final['target_allowance'] = first_page_targets
                for level in final['levels']:
                    level['target_allowance'] = first_page_targets
            return result

        def levels(kind, **kwargs):
            # The result has eighteen fixed targets before either objection.
            return (240000,) if kind == 'final' else material_levels(kind, **kwargs)

        for patcher in (patch.object(capacity, 'plan_chain_capacity', side_effect=plan),
                        patch.object(capacity, 'describe_material_levels', side_effect=levels)
                        if room_for_19_targets else None):
            if patcher is not None:
                patcher.start()
                self.addCleanup(patcher.stop)

    def build_answers_by_label(self, logical, full):
        """Use the complete review only for fixture values, identified by label."""
        from research_harness.problem_rubric import ADMISSION_ITEMS, GROUNDS
        rule = composition.find_rule(full['stage'], logical)
        value = full['value']
        by_id = {row['id']: {} for row in composition.list_targets(rule, logical)}
        for collection, identity in (('assurances', 'kind'), ('work_items', 'id'), ('objection_findings', 'id')):
            prefix = '/value/' + collection
            for entry in value[collection]:
                by_id[entry[identity]].update({prefix + '/*/' + key: item for key, item in entry.items()})
        for identifier, grade in value.get('rubric_potential', {}).items():
            by_id[identifier]['/value/rubric_potential/' + identifier] = grade
        for identifier, reason in value.get('rubric_evidence', {}).items():
            section = 'admission' if identifier in ADMISSION_ITEMS else 'significance' if identifier in GROUNDS else ''
            rubric = value['rubric'][section] if section else value['rubric']
            grade_path = '/value/rubric/' + (section + '/' if section else '') + identifier
            by_id[identifier].update({grade_path: rubric[identifier], '/value/rubric_evidence/' + identifier: reason})
        definitions = composition.list_targets(rule, logical)
        first = by_id[definitions[0]['id']]
        for collection in ('assurances', 'work_items', 'objection_findings'):
            if not value[collection]:
                first['/value/' + collection] = []
        if 'rubric_potential' in value and not value['rubric_potential']:
            first['/value/rubric_potential'] = {}
        by_label = {row['label'][:120]: by_id[row['id']] for row in definitions}
        self.assertEqual(len(by_label), len(definitions))
        return rule, by_label

    def answer_page(self, page, labels, by_label, *, restart):
        import re
        counters = {}
        rows = []
        for handle in page['targets']:
            template = by_label[labels[handle]]
            if restart:
                fields = {}
                groups = set()
                for pointer, item in template.items():
                    if '/*/' in pointer:
                        collection = pointer.split('/*/')[0]
                        groups.add(collection)
                        pointer = pointer.replace('/*/', '/%d/' % counters.get(collection, 0))
                    fields[pointer] = item
                for collection in groups:
                    counters[collection] = counters.get(collection, 0) + 1
            else:
                fields = {pointer: template[re.sub(r'/[0-9]+/', '/*/', pointer, count=1)]
                          for pointer in page['fields'][handle]}
            rows.append(dict(target=handle, fields=fields, sentence='The supplied evidence was assessed.'))
        return dict(targets=rows), set(counters)

    def drive_pages(self, case, current, *, read_packet, read_binding, read_logical, read_labels, full_output,
                    advance, answer_nonpage):
        from research_harness import review_composite, review_protocol, research_decisions
        from research_harness.review_failure_class import plan_after_failure
        from codex_route_fixtures import invoke_codex

        page_sizes = []
        seen_lists = set()
        refused = False
        missing_bindings = []
        for _ in range(16):
            packet = read_packet(current)
            binding = read_binding(packet)
            if 'closing' in binding:
                self.assertEqual(missing_bindings, [], 'A page omitted its pointer binding.')
                logical = read_logical(current, packet)
                _, closing = composition.split_output(rule, logical, full, [])
                receipt = invoke_codex(case, current['id'], closing)
                self.assertNotIn('output_refusal', receipt, receipt.get('output_refusal'))
                output, record = review_composite.read_final_output(case.read_records(), case.artifacts, current)
                self.assertIsNotNone(record)
                saved = case.mutate(research_decisions.record_value_review, dict(output, id='pointer-review',
                    dossier_id=None, assignment_id=current['id']))['result']
                self.assertEqual(saved['composite']['id'], record['id'])
                self.assertEqual(saved['payload']['value'], full['value'])
                self.assertTrue(refused)
                self.assertEqual(len(record['pages']), 2)
                self.assertEqual(len({target for item in record['pages'] for target in item['targets']}), len(by_label))
                print('PAGE_POINTERS', current['role'], 'parts' if 'review_parts' in packet else 'session',
                      'targets=', len(by_label), 'pages=', len(record['pages']), 'sizes=', page_sizes, flush=True)
                return
            if 'page' not in binding:
                current = advance(current, answer_nonpage(current, packet))
                continue
            if not page_sizes:
                logical = read_logical(current, packet)
                full = full_output(logical)
                rule, by_label = self.build_answers_by_label(logical, full)
            page = binding['page']
            if ('fields' in page and binding.get('adapter') == 'problem_slate'
                    and page['targets'] == ['t5', 't6']):
                self.assertIn('/value/assurances/4/kind', page['fields']['t5'])
                self.assertEqual(page['fields']['t6'], ['/value/rubric_potential/t5'])
            labels = read_labels(binding)
            restarted, lists = self.answer_page(page, labels, by_label, restart=True)
            if not refused and seen_lists & lists:
                invoke_codex(case, current['id'], restarted)
                records = case.read_records()
                attempt_id = 'attempt-' + current['id']
                self.assertIn(attempt_id, records.get('review_output_refusal', {}),
                              'The page with restarted indices was accepted.')
                self.assertEqual(records['review_output_refusal'][attempt_id]['code'], 'review_composition_conflict')
                step = plan_after_failure(records, case.artifacts, current['id'])
                self.assertEqual(step['state'], 'replace', step)
                current = case.mutate(review_protocol.record_assignment, step['payload'])['result']
                replacement = read_packet(current)
                self.assertEqual(read_binding(replacement)['page'], page)
                self.assertEqual(read_binding(replacement)['capacity']['material_allowance'],
                                 binding['capacity']['material_allowance'])
                notice = replacement['output_refusal']['refused'][-1]
                self.assertEqual(notice['check'], 'review.shape')
                position = int(notice['path'].split('/')[2])
                row = restarted['targets'][position]
                self.assertEqual(notice['details']['missing_fields'],
                                 sorted(set(page['fields'][row['target']]) - row['fields'].keys()))
                self.assertTrue(notice['details']['missing_fields'])
                packet, binding = replacement, read_binding(replacement)
                refused = True
            if 'fields' in page:
                expected = composition.list_page_pointers(rule, read_logical(current, packet), page['targets'])
                self.assertEqual(page['fields'], expected)
                answer, _ = self.answer_page(page, read_labels(binding), by_label, restart=False)
            else:
                # Keep driving until the later-page refusal assertion on the original base.
                missing_bindings.append(page['number'])
                answer = restarted
            current_before = current
            current = advance(current, answer)
            attempt = case.read_records()['review_attempt']['attempt-' + current_before['id']]
            sizes = attempt['preflight']['plan_comparison']['sizes']['text_characters']
            self.assertLessEqual(sizes['value'], sizes['bound'])
            page_sizes.append((composition._measure_final_value(page.get('fields', {})),
                               len(json.dumps(page, ensure_ascii=False)), sizes['value'], sizes['bound']))
            seen_lists.update(lists)
        self.fail('The two-page journey did not record its review.')

    def drive_session(self, role, objections, first_page_targets, *, read_binding=None):
        from research_harness import review_session_v4 as session
        from test_review_session_v4_pages import SessionPagesTests
        fixture = self.set_up_fixture(SessionPagesTests())
        self.pin_two_pages(first_page_targets, room_for_19_targets=role == 'result')
        current = fixture.start(role, objections, distinct_objections=True)

        def full_output(logical):
            output = fixture.case.build_review_output(role)
            output['value'].update(status='unresolved', objection_findings=[
                dict(id=row['id'], status='continuing', reason='A gap remains.', evidence=[fixture.case.supporting])
                for row in logical['historical_reviews'][0]['objections']])
            if role == 'slate':
                output['value']['rubric_potential'] = {}
                for row in output['value']['assurances']:
                    row['status'] = 'not_assessed'
            return output

        self.drive_pages(fixture.case, current, read_packet=fixture.read_packet,
            read_binding=read_binding or session.read_session_binding,
            read_logical=lambda assignment, packet: session.build_composition_packet(
                fixture.case.artifacts, assignment, packet),
            read_labels=lambda binding: {row['handle']: row['label'] for row in binding['targets']},
            full_output=full_output, advance=fixture.advance, answer_nonpage=lambda assignment, packet: fixture.answer(assignment))

    def test_session_bar_replaces_restarted_page_and_records(self):
        """Drive a real bar and its replacement across the smallest split with two objections."""
        self.drive_session('bar', 2, 1)

    def test_session_slate_replaces_restarted_page_and_records(self):
        """Drive a real slate, splitting its required assurances with no additional targets."""
        self.drive_session('slate', 0, 4)

    def test_session_result_replaces_restarted_page_and_records(self):
        """Eighteen fixed result targets precede the two objections split across the pages."""
        self.drive_session('result', 2, 19)

    def test_parts_result_replaces_restarted_page_and_records(self):
        """A captured search enters v3 before the two result pages split the two objections."""
        from unittest.mock import patch
        from research_harness import review_parts, review_single_request
        from test_review_parts_v3_pages import PagedSeriesTests
        fixture = self.set_up_fixture(PagedSeriesTests())
        self.pin_two_pages(19, room_for_19_targets=True)
        with patch.object(review_single_request, 'describe_task_entry', return_value='task'):
            current = fixture.start(2, characters=1000)

        def full_output(logical):
            output = fixture.final_output()
            output['value']['objection_findings'] = [dict(id=row['id'], status='resolved',
                reason='The supplied record resolves this concern.', evidence=output['value']['evidence'])
                for row in logical['material_objections']]
            return output

        self.drive_pages(fixture.case, current, read_packet=fixture.packet, read_binding=lambda packet: packet[review_parts.KEY],
            read_logical=lambda assignment, packet: dict(review_parts.read_logical_packet(
                fixture.case.artifacts, packet[review_parts.KEY]), review_parts=packet[review_parts.KEY]),
            read_labels=self.read_parts_labels, full_output=full_output,
            advance=fixture.advance, answer_nonpage=lambda assignment, packet: fixture.answer(assignment))

    def read_parts_labels(self, binding):
        return {'t%d' % number: row['label'][:120] for number, row in enumerate(binding['targets'], 1)}

    def test_parts_slate_replaces_restarted_page_and_records(self):
        """Use one candidate and split the slate's five required assurances across its pages."""
        from unittest.mock import patch
        from codex_route_fixtures import invoke_codex, record_codex_route
        from research_harness import bar_sources, review_parts, review_protocol, review_single_request
        from test_review_parts_slate import SlateSeriesTests
        from test_research_problem_slate import ProblemSlateTests
        from test_review_parts_v3 import make_turn_output
        fixture = self.set_up_fixture(SlateSeriesTests())
        self.pin_two_pages(4)
        case = self.set_up_fixture(ProblemSlateTests())
        case.record_candidate('t5')
        case.observe_bars()
        record_codex_route(case, 'local', output_tokens=128000)

        def advance(assignment, output):
            invoke_codex(case, assignment['id'], output)
            records = case.read_records()
            self.assertNotIn('attempt-' + assignment['id'], records.get('review_output_refusal', {}))
            return fixture.assign(case, identifier='slate-next-%d' % case.store.revision,
                                  prior_assignment_id=assignment['id'])

        def full_output(logical):
            candidates = logical['candidates']
            output = case.build_slate_output([row['id'] for row in candidates])
            candidate = candidates[0]
            evidence = [dict(kind='record', record_kind='problem_candidate', id=candidate['id'],
                             digest=candidate['digest'])]
            output['value']['evidence'] = evidence
            for assurance in output['value']['assurances']:
                assurance['evidence'] = evidence
            return output

        with patch.object(bar_sources, 'SESSION_PROTOCOL', bar_sources.PROTOCOL), \
                patch('research_harness.review_problem_parts.preserves_ordinary_packet', return_value=False), \
                patch.object(review_single_request, 'describe_task_entry', return_value='task'):
            current = fixture.assign(case)
            self.drive_pages(case, current, read_packet=lambda assignment: review_protocol._read_json(case.artifacts, assignment['packet']),
                read_binding=lambda packet: packet[review_parts.KEY],
                read_logical=lambda assignment, packet: dict(review_parts.read_logical_packet(
                    case.artifacts, packet[review_parts.KEY]), review_parts=packet[review_parts.KEY]),
                read_labels=self.read_parts_labels, full_output=full_output, advance=advance,
                answer_nonpage=lambda assignment, packet: make_turn_output(packet))

    def test_journey_detects_an_absent_first_binding(self):
        from research_harness.review_session_v4 import read_session_binding

        def omit_first(packet):
            binding = read_session_binding(packet)
            if binding.get('page', {}).get('number') == 1:
                binding = copy.deepcopy(binding)
                binding['page'].pop('fields')
            return binding

        with self.assertRaisesRegex(AssertionError, 'omitted its pointer binding'):
            self.drive_session('slate', 0, 4, read_binding=omit_first)

    def test_nested_fixture_failure_restores_claims(self):
        from research_harness import problem_claims
        import test_research_problem_reviews as problem_fixtures
        from test_review_session_v4_pages import SessionPagesTests

        name = 'research_harness.problem_claims'
        fixture = SessionPagesTests()
        self.addCleanup(sys.modules.__setitem__, name, problem_claims)
        with patch.object(problem_fixtures, 'install_rubric_anchors', side_effect=RuntimeError('anchor failure')):
            with self.assertRaisesRegex(RuntimeError, 'anchor failure'):
                self.set_up_fixture(fixture)
            fixture.doCleanups()
            self.assertIs(sys.modules[name], problem_claims)

    def test_slate_fixture_setup_failure_runs_child_cleanup(self):
        from test_research_problem_slate import ProblemSlateTests
        from test_review_parts_slate import SlateSeriesTests

        active = []
        def fail_after_registering_cleanup(child):
            active.append(child)
            child.addCleanup(active.remove, child)
            raise RuntimeError('slate setup failure')

        fixture = self.set_up_fixture(SlateSeriesTests())
        with patch.object(ProblemSlateTests, 'setUp', fail_after_registering_cleanup):
            with self.assertRaisesRegex(RuntimeError, 'slate setup failure'):
                fixture.make_case()
        fixture.doCleanups()
        self.assertFalse(active)

    def test_bar_repair_in_pages_records_review(self):
        from codex_route_fixtures import invoke_codex
        from research_harness import bar_sources, research_decisions as decisions
        from research_harness import review_parts as legacy, review_parts_v3 as parts
        from research_harness import review_protocol as protocol
        from research_harness import review_parts_repair as repair, review_route_capacity as capacity
        from research_harness.review_refusal import evidence_arrays
        from test_review_parts_repair_journey import BarRepairJourneyTests
        from test_review_parts_v3_pages import AdapterPageTests

        case = self.set_up_fixture(BarRepairJourneyTests())
        helper = self.set_up_fixture(AdapterPageTests())
        helper.pin_read_profile()
        original = capacity.plan_chain_capacity

        def build_plan(*args, **kwargs):
            plan = original(*args, **kwargs)
            task = plan['task']['final']
            task['target_allowance'] = 1
            for level in task['levels']:
                level['target_allowance'] = 1
            return plan

        def assign(identifier, prior):
            payload = dict(case.repair_payload(), id=identifier, context=dict(prior_assignment_id=prior))
            return case.mutate(protocol.record_assignment, payload)['result']

        def answer(assignment):
            packet = case.packet(assignment)
            inventory = legacy.read_inventory(case.artifacts, packet[legacy.KEY]['inventory'])
            citations = [ref for row in inventory['units'] for ref in row['citations']]
            self.assertTrue(citations)
            output = case.final_response()
            for evidence in evidence_arrays(output):
                evidence[:] = citations[:1]
            return output

        with patch.object(repair, 'requires_parts', return_value=True), \
                patch.object(bar_sources, 'SESSION_PROTOCOL', bar_sources.PROTOCOL), \
                patch.object(capacity, 'plan_chain_capacity', side_effect=build_plan):
            first = case.assign_repair()
            self.assertEqual(case.packet(first)[legacy.KEY]['protocol'], parts.PROTOCOL)
            final, answer, composite = helper.drive(case, first, case.packet, answer, assign,
                lambda assignment, output: invoke_codex(case, assignment['id'], output),
                lambda: case.store.snapshot()['records'], case.artifacts)
        result = case.mutate(decisions.record_value_review, dict(answer, id='paged-bar-repair',
            dossier_id='dossier-1', assignment_id=final['id']))['result']
        self.assertEqual(result['composite']['id'], composite['id'])
