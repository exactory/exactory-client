"""Typed pages preserve the ordinary scientific object and exact raw bindings."""

import copy
import unittest

from research_harness import review_composition as composition
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.problem_rubric import ADMISSION_ITEMS, ITEMS


CHECKS = ('problem_statement', 'problem_openness', 'problem_value', 'problem_citations', 'stop')
SOURCE = {'kind': 'record', 'record_kind': 'problem_reading', 'record_id': 'prime-domain-reading'}


class SharedCompositionValidationTests(unittest.TestCase):
    def test_invalid_unicode_json_uses_the_composition_error_code(self):
        plan, observations, _, _ = make_fixture()
        plan['subject'] = {'id': '\ud800'}
        with self.assertRaises(ResearchError) as raised:
            composition.build_root(plan, observations)
        self.assertEqual(raised.exception.code, 'review_composition_conflict')

    def test_existing_root_and_literal_nonempty_identifiers_stay_valid(self):
        plan, observations, _, _ = make_fixture()
        self.assertEqual(composition.build_root(plan, observations)['digest'],
                         'fd2237e1eb94132c6a084dbbd82d2b61544833e546b56b47c9d8ecd003a23848')
        self.assertEqual(composition.effective_review_output('   ')['kind'], 'pending')

    def test_shared_fields_keep_conflict_and_pending_distinct(self):
        plan, observations, _, _ = make_fixture()
        for changed, code in ((dict(plan, extra=True), 'review_composition_conflict'),
                              (plan, 'review_composition_pending')):
            with self.subTest(code=code), self.assertRaises(ResearchError) as raised:
                composition.build_root(changed, [] if code.endswith('pending') else observations)
            self.assertEqual(raised.exception.code, code)


def worked_objection():
    return {'id': 'O7', 'claim': 'The motivation supports the broad value claim.',
            'reason': 'The supplied argument applies only to prime p.', 'evidence': [SOURCE],
            'resolution_condition': 'Support the broad claim or address the domain restriction.'}


def worked_result():
    """Section 6.6 R, including the condition and the continuing exact O7."""
    return {'stage': 'result', 'support': None, 'value': {
        'status': 'insufficient', 'objective_adequacy': 'not_assessed', 'method_adequacy': 'not_assessed',
        'consequence': 'The motivation argument applies only to prime p.',
        'reason': 'The submitted value claim is broader than the prime-domain argument.',
        'bar_ids': ['bar-A', 'bar-B'], 'evidence': [SOURCE], 'work_items': [],
        'assurances': [{'kind': kind, 'status': 'failed' if kind == 'problem_value' else 'passed',
                        'reason': ('The motivation establishes only the prime-domain restriction.'
                                   if kind == 'problem_value' else 'This separately completed assessment passes.'),
                        'evidence': [SOURCE]} for kind in CHECKS],
        'rubric': {'admission': {key: 'pass' for key in ADMISSION_ITEMS},
                   'significance': {'B1': 1, 'B2': 1}, 'priority': 2, 'attempt': 'pass',
                   'accessibility': 'E-F', 'calibration': 'fail'},
        'rubric_evidence': {key: ('The prime-domain motivation does not establish the broader value claim.'
                                if key in ('B1', 'B2', 'calibration') else
                                'The supplied evidence supports this illustrative grade.') for key in ITEMS},
        'objection_findings': [{'id': 'O7', 'status': 'continuing',
                                'reason': 'O7 remains: only prime p is supported.', 'evidence': [SOURCE]}]},
        'objections': [], 'limitations': ['The prime-domain condition must remain visible.']}


def worked_adjudication():
    """Section 6.6 A, keeping original context distinct from current evidence."""
    return {'objection_id': 'O7', 'disposition': 'upheld', 'correction_admissible': None,
            'reason': 'The original-context evidence and the current evidence both retain the prime-domain restriction.',
            'evidence': [dict(SOURCE, record_id='original-context-reading'),
                         dict(SOURCE, record_id='current-evidence-reading')]}


def make_fixture(output=None, role='result'):
    output = copy.deepcopy(output if output is not None else worked_result())
    values, choices = {}, {}
    if role == 'result':
        choices.update({'/stage': output['stage'], '/support': output['support']})
        for key in ('status', 'objective_adequacy', 'method_adequacy'):
            choices['/value/' + key] = output['value'][key]
        for entry in output['value']['assurances']:
            prefix = '/value/assurances/' + entry['kind']
            choices[prefix + '/status'] = entry['status']
            values[prefix + '/reason'] = entry['reason']
            values[prefix + '/evidence'] = entry['evidence']
        for key in ('admission', 'significance'):
            for name, value in output['value']['rubric'][key].items():
                choices['/value/rubric/' + key + '/' + name] = value
        for key in ('priority', 'attempt', 'accessibility', 'calibration'):
            choices['/value/rubric/' + key] = output['value']['rubric'][key]
        for key in ('consequence', 'reason', 'bar_ids', 'evidence', 'objection_findings'):
            values['/value/' + key] = output['value'][key]
        for key, value in output['value']['rubric_evidence'].items():
            values['/value/rubric_evidence/' + key] = value
        for key in ('objections', 'limitations', 'excerpts', 'reassessment'):
            if key in output:
                values['/' + key] = output[key]
        page_ids = ['R1', 'R2', 'R3']
    else:
        choices = {('/' + key): output[key] for key in ('objection_id', 'disposition', 'correction_admissible')}
        values = {'/reason': output['reason'], '/evidence': output['evidence']}
        page_ids = ['A1', 'A2']
    plan = {'protocol': composition.PROTOCOL, 'schema': composition.SCHEMA,
            'epoch_id': 'epoch-' + role, 'role': role, 'slot_id': 'slot-' + role,
            'subject': {'kind': 'problem', 'id': 'prime-domain-problem'},
            'evidence_currency': digest('current-evidence'), 'source_root': digest('originals'),
            'dependency_root': digest('dependencies'), 'selected_observations_root': digest('selected'),
            'integration_root': digest('integration preserving prime p'),
            'findings': ['O7'] if role == 'result' else [],
            'evidence_obligations': [{'id': 'O7', 'version': digest(worked_objection())}],
            'required_pages': page_ids, 'ownership': [], 'revision_authorizations': [],
            'terminal_choices': {key: [value] for key, value in choices.items()}}
    if role == 'adjudicator':
        pair = worked_review_pair()
        plan['subject'].update(review_ids=[row['id'] for row in pair],
                               review_roots=[row['composition_root'] for row in pair],
                               objection=worked_objection(), objection_version=digest(worked_objection()))
    pages = {page_id: {'protocol': composition.PROTOCOL, 'epoch_id': plan['epoch_id'], 'role': role,
                      'task_id': 'answer-' + page_id, 'page_id': page_id, 'revision': 0,
                      'source_root': plan['source_root'], 'dependency_root': plan['dependency_root'],
                      'ownership': [], 'fragments': [], 'grounding': [SOURCE],
                      'findings': ['O7'] if page_id == 'R2' else [],
                      'dispositions': [{'finding_id': 'O7', 'status': 'retained',
                                        'reason': 'The exact objection continues.', 'grounds': [SOURCE]}]
                                      if page_id == 'R2' else [], 'replaces': None} for page_id in page_ids}
    for path, value in values.items():
        if role == 'result':
            owner = 'R2' if path == '/value/objection_findings' else (
                'R3' if path.startswith('/value/rubric_evidence/') or path == '/limitations' else 'R1')
        else:
            owner = 'A1' if path == '/reason' else 'A2'
        if isinstance(value, list):
            entries = [(None, None, 'empty_list', [], None)] if not value else [
                (str(item.get('id', owner + ':' + path + ':' + str(index))) if isinstance(item, dict)
                 else owner + ':' + path + ':' + str(index), index,
                 'object' if isinstance(item, dict) else 'string', item,
                 [0, len(item)] if isinstance(item, str) else None) for index, item in enumerate(value)]
        else:
            entries = [(None, None, 'string' if isinstance(value, str) else 'object', value,
                        [0, len(value)] if isinstance(value, str) else None)]
        for member_key, ordinal, value_type, content, span in entries:
            ownership = {'page_id': owner, 'path': path, 'member_key': member_key,
                         'ordinal': ordinal, 'type': value_type, 'range': span}
            plan['ownership'].append(ownership)
            pages[owner]['ownership'].append(copy.deepcopy(ownership))
            pages[owner]['fragments'].append(dict(ownership, content=copy.deepcopy(content), grounds=[SOURCE]))
    observations = [observe(pages[page_id]) for page_id in page_ids]
    return plan, observations, choices, output


def observe(page, attempt_id=None):
    return {'attempt_id': attempt_id or page['task_id'] + '-r' + str(page['revision']),
            'raw_output_digest': digest(page), 'output': copy.deepcopy(page)}


def terminal_for(plan, root, choices):
    return {'protocol': composition.PROTOCOL, 'epoch_id': plan['epoch_id'], 'role': plan['role'],
            'composition_root': root['digest'], 'choices': copy.deepcopy(choices)}


def worked_review_pair():
    """Recorded source fixtures with reassembled payloads and separate slot roots."""
    reviews = []
    for revision, identifier in enumerate(('RA', 'RB'), 1):
        plan, observations, choices, expected = make_fixture()
        plan.update(epoch_id='epoch-' + identifier, slot_id='slot-' + identifier)
        for observation in observations:
            observation['output']['epoch_id'] = plan['epoch_id']
            observation.update(observe(observation['output'], identifier + '-' + observation['attempt_id']))
        root = composition.build_root(plan, observations)
        terminal = terminal_for(plan, root, choices)
        payload = composition.assemble_result(plan, observations, root, terminal)
        reviews.append({'id': identifier, 'assignment_id': 'controller-' + identifier, 'payload': payload,
                        'output_digest': digest(payload), 'composition_root': root['digest'],
                        'reviewer_id': 'reviewer-' + identifier, 'author_id': 'author',
                        'recorded_revision': revision})
    return reviews


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.observations, self.choices, self.expected = make_fixture()

    def assertCode(self, code, function, *args):
        with self.assertRaises(ResearchError) as raised:
            function(*args)
        self.assertEqual(raised.exception.code, code)

    def assemble(self):
        root = composition.build_root(self.plan, self.observations)
        return composition.assemble_result(self.plan, self.observations, root,
                                            terminal_for(self.plan, root, self.choices))

    def test_worked_result_preserves_condition_objection_grades_and_ordinary_shape(self):
        self.assertEqual(self.assemble(), self.expected)
        self.assertEqual(self.expected['value']['rubric']['significance'], {'B1': 1, 'B2': 1})
        self.assertEqual(self.expected['value']['objection_findings'][0]['id'], 'O7')

    def test_worked_adjudication_preserves_exact_five_fields_and_frozen_scope(self):
        plan, observations, choices, expected = make_fixture(worked_adjudication(), 'adjudicator')
        root = composition.build_root(plan, observations)
        actual = composition.assemble_result(plan, observations, root, terminal_for(plan, root, choices))
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), 5)
        pair = worked_review_pair()
        self.assertEqual(plan['subject']['review_roots'], [row['composition_root'] for row in pair])
        self.assertEqual(plan['subject']['objection_version'], digest(worked_objection()))
        self.assertNotEqual(pair[0]['reviewer_id'], pair[1]['reviewer_id'])
        for review in pair:
            self.assertEqual(review['output_digest'], digest(review['payload']))
            self.assertEqual(review['payload']['value']['objection_findings'][0]['id'], 'O7')
        changed = copy.deepcopy(plan)
        changed['subject']['objection_version'] = digest('another version')
        self.assertCode('review_composition_conflict', composition.assemble_result,
                        changed, observations, root, terminal_for(plan, root, choices))

    def test_pages_cannot_own_terminal_scalars_or_arbitrary_patch_paths(self):
        for path in ('/stage', '/value/status', '/value/assurances/problem_value/status',
                     '/value/rubric/priority', '/value/work_items', '/value/unknown', '/source_requests'):
            plan, observations = copy.deepcopy((self.plan, self.observations))
            fragment = observations[0]['output']['fragments'][0]
            fragment['path'] = path
            observations[0]['output']['ownership'][0]['path'] = path
            plan['ownership'][0]['path'] = path
            with self.subTest(path=path):
                self.assertCode('review_composition_conflict', composition.validate_page,
                                observations[0]['output'], plan)

    def test_page_scope_and_grounding_are_bound(self):
        for key, value in (('epoch_id', 'another'), ('role', 'adjudicator'),
                           ('dependency_root', digest('changed')), ('source_root', digest('changed')),
                           ('grounding', [])):
            page = copy.deepcopy(self.observations[0]['output'])
            page[key] = value
            with self.subTest(key=key):
                self.assertCode('review_composition_conflict', composition.validate_page, page, self.plan)

    def test_list_order_comes_from_frozen_ordinals_not_input_order(self):
        self.observations.reverse()
        for observation in self.observations:
            observation['output']['fragments'].reverse()
            observation['raw_output_digest'] = digest(observation['output'])
        self.assertEqual(self.assemble(), self.expected)
        self.assertEqual([row['kind'] for row in self.assemble()['value']['assurances']], list(CHECKS))

    def test_explicit_empty_lists_required_and_never_invented(self):
        self.assertEqual(self.assemble()['objections'], [])
        plan, observations = copy.deepcopy((self.plan, self.observations))
        page = observations[0]['output']
        page['fragments'] = [entry for entry in page['fragments'] if entry['path'] != '/objections']
        observations[0] = observe(page)
        self.assertCode('review_composition_pending', composition.build_root, plan, observations)
        plan['ownership'] = [entry for entry in plan['ownership'] if entry['path'] != '/objections']
        page['ownership'] = [entry for entry in page['ownership'] if entry['path'] != '/objections']
        observations[0] = observe(page)
        self.assertCode('review_composition_pending', composition.build_root, plan, observations)

    def split_reason(self, segments):
        page = self.observations[0]['output']
        target = next(entry for entry in page['fragments'] if entry['path'] == '/value/reason')
        page['fragments'].remove(target)
        page['ownership'] = [entry for entry in page['ownership'] if entry['path'] != '/value/reason']
        self.plan['ownership'] = [entry for entry in self.plan['ownership'] if entry['path'] != '/value/reason']
        for start, end, content in segments:
            fragment = dict(target, range=[start, end], content=content)
            owner = {key: value for key, value in fragment.items() if key not in ('content', 'grounds')}
            self.plan['ownership'].append(owner)
            page['ownership'].append(copy.deepcopy(owner))
            page['fragments'].append(fragment)
        self.observations[0] = observe(page)

    def test_string_segments_join_exactly_without_normalization_or_separators(self):
        reason = 'prime p\n  e\u0301\tΩ '
        self.split_reason([(7, len(reason), reason[7:]), (0, 7, reason[:7])])
        self.expected['value']['reason'] = reason
        self.assertEqual(self.assemble(), self.expected)

    def test_gapped_overlapping_or_wrong_length_string_ranges_reject(self):
        for segments in ([(1, 3, 'ab')], [(0, 2, 'ab'), (1, 3, 'cd')], [(0, 3, 'ab')]):
            with self.subTest(segments=segments):
                self.setUp()
                self.split_reason(segments)
                self.assertCode('review_composition_conflict', composition.build_root,
                                self.plan, self.observations)

    def test_identical_observation_replay_is_idempotent(self):
        first = composition.build_root(self.plan, self.observations)
        self.observations.append(copy.deepcopy(self.observations[0]))
        self.assertEqual(composition.build_root(self.plan, self.observations), first)

    def test_distinct_list_occurrences_with_identical_content_remain_distinct(self):
        output = worked_result()
        output['limitations'] *= 2
        self.plan, self.observations, self.choices, self.expected = make_fixture(output)
        self.assertEqual(self.assemble()['limitations'], output['limitations'])

    def test_duplicate_field_or_member_ownership_rejects_even_when_identical(self):
        for different in (False, True):
            plan, observations = copy.deepcopy((self.plan, self.observations))
            fragment = copy.deepcopy(next(entry for entry in observations[0]['output']['fragments']
                                          if entry['path'] == '/value/bar_ids'))
            if different:
                fragment['content'] = 'other'
                fragment['range'] = [0, 5]
            owner = {key: value for key, value in fragment.items() if key not in ('content', 'grounds')}
            plan['ownership'].append(owner)
            observations[0]['output']['ownership'].append(owner)
            observations[0]['output']['fragments'].append(fragment)
            observations[0] = observe(observations[0]['output'])
            with self.subTest(different=different):
                self.assertCode('review_composition_conflict', composition.build_root, plan, observations)

    def test_missing_or_conflicting_list_member_ordinals_reject(self):
        for ordinal in (0, 2):
            plan, observations = copy.deepcopy((self.plan, self.observations))
            for entries in (plan['ownership'], observations[0]['output']['ownership'],
                            observations[0]['output']['fragments']):
                for entry in entries:
                    if entry['path'] == '/value/bar_ids' and entry['ordinal'] == 1:
                        entry['ordinal'] = ordinal
            observations[0] = observe(observations[0]['output'])
            with self.subTest(ordinal=ordinal):
                self.assertCode('review_composition_conflict', composition.build_root, plan, observations)

    def revise(self, cause='observed_reconciliation'):
        previous = next(row for row in reversed(self.observations) if row['output']['page_id'] == 'R2')
        page = copy.deepcopy(previous['output'])
        page['revision'] += 1
        page['replaces'] = {'page_id': page['page_id'], 'revision': previous['output']['revision'],
                            'attempt_id': previous['attempt_id'], 'raw_output_digest': previous['raw_output_digest'],
                            'cause': cause, 'authorization_root': digest('observed reconciliation')}
        revised = observe(page)
        self.plan['revision_authorizations'].append(dict(page['replaces'],
            replacement_raw_output_digest=revised['raw_output_digest']))
        self.observations.append(revised)
        return revised

    def test_revision_chain_uses_exact_prior_and_observed_authorization(self):
        revised = self.revise()
        self.assertEqual(self.assemble(), self.expected)
        root = composition.build_root(self.plan, self.observations)
        self.assertEqual(root['header']['counts']['pages'], 3)
        self.assertEqual(root['header']['counts']['observations'], 4)
        revised['output']['replaces']['raw_output_digest'] = digest('wrong predecessor')
        revised['raw_output_digest'] = digest(revised['output'])
        self.assertCode('review_composition_conflict', composition.build_root, self.plan, self.observations)

    def test_multiple_revisions_are_validated_in_chain_order(self):
        self.revise()
        self.revise()
        self.observations.reverse()
        self.assertEqual(self.assemble(), self.expected)
        root = composition.build_root(self.plan, self.observations)
        self.assertEqual(root['header']['counts']['observations'], 5)

    def test_public_results_do_not_share_mutable_inputs(self):
        before_plan, before_observations = copy.deepcopy((self.plan, self.observations))
        page = composition.validate_page(self.observations[0]['output'], self.plan)
        page['fragments'].clear()
        root = composition.build_root(self.plan, self.observations)
        root['nodes'][root['page_tree']]['pages'][0]['ownership'][0]['path'] = '/tampered'
        self.assertEqual(self.plan, before_plan)
        self.assertEqual(self.observations, before_observations)
        output = self.assemble()
        output['value']['evidence'][0]['record_id'] = 'tampered'
        self.assertEqual(self.plan, before_plan)
        self.assertEqual(self.observations, before_observations)

    def test_reviewer_incomplete_pages_retain_conclusions_and_recovery_scope(self):
        plan, observations, choices, expected = make_fixture(worked_adjudication(), 'adjudicator')
        plan.update(role='incomplete', required_pages=['I1'], ownership=[], findings=['O7'],
                    terminal_choices={'/origin': ['reviewer']})
        page = dict(observations[0]['output'], role='incomplete', page_id='I1', task_id='incomplete-answer',
                    fragments=[], ownership=[], findings=['O7'], dispositions=[{
                        'finding_id': 'O7', 'status': 'unresolved', 'reason': 'The broad value claim needs further work.',
                        'grounds': [SOURCE]}])
        partial = {'known_conclusions': [{'id': 'prime-only', 'text': 'The argument applies only to prime p.'}],
                   'unresolved_scope': [{'id': 'broad-value', 'cause': 'required_assessment_unavailable'}],
                   'findings': [{'id': 'O7', 'status': 'unresolved'}],
                   'recovery_work': [{'id': 'research', 'action': 'Assess the broader submitted value claim.'}]}
        for field, content in partial.items():
            owner = {'page_id': 'I1', 'path': '/' + field, 'member_key': content[0]['id'],
                     'ordinal': 0, 'type': 'object', 'range': None}
            plan['ownership'].append(owner)
            page['ownership'].append(copy.deepcopy(owner))
            page['fragments'].append(dict(owner, content=copy.deepcopy(content[0]), grounds=[SOURCE]))
        observations = [observe(page)]
        root = composition.build_root(plan, observations)
        authorization = terminal_for(plan, root, {'/origin': 'reviewer'})
        outcome = composition.assemble_result(plan, observations, root, authorization)
        self.assertEqual(outcome, dict(partial, origin='reviewer'))
        effective = composition.effective_review_output('controller', {'controller_id': 'controller',
            'kind': 'incomplete', 'outcome': outcome, 'selected_observations_root': plan['selected_observations_root'],
            'completion_root': digest('incomplete assessment')})
        self.assertNotIn('effective_output', effective)
        self.assertEqual(effective['incomplete_outcome'], outcome)

    def test_revision_without_prior_or_authorization_and_forks_reject(self):
        for mode in ('missing_prior', 'unauthorized', 'fork', 'gap'):
            self.setUp()
            revised = self.revise()
            if mode == 'missing_prior':
                self.observations.pop(1)
            elif mode == 'unauthorized':
                self.plan['revision_authorizations'] = []
            elif mode == 'gap':
                revised['output']['revision'] = 3
                revised['raw_output_digest'] = digest(revised['output'])
            else:
                fork = copy.deepcopy(revised)
                fork['attempt_id'] = 'fork-attempt'
                self.observations.append(fork)
            with self.subTest(mode=mode):
                self.assertCode('review_composition_conflict', composition.build_root, self.plan, self.observations)

    def test_metadata_correction_cannot_edit_scientific_content(self):
        revised = self.revise('metadata_correction')
        revised['output']['fragments'][0]['content']['status'] = 'resolved'
        revised['raw_output_digest'] = digest(revised['output'])
        self.plan['revision_authorizations'][0]['replacement_raw_output_digest'] = revised['raw_output_digest']
        self.assertCode('review_composition_conflict', composition.build_root, self.plan, self.observations)

    def test_observed_revision_can_change_string_length_and_requires_new_terminal(self):
        original_root = composition.build_root(self.plan, self.observations)
        original_terminal = terminal_for(self.plan, original_root, self.choices)
        prior = self.observations[0]
        page = copy.deepcopy(prior['output'])
        page['revision'] = 1
        reason = 'Observed original rereading still supports only prime p, not the broader value claim.'
        for entries in (page['fragments'], page['ownership'], self.plan['ownership']):
            for entry in entries:
                if entry['path'] == '/value/reason':
                    entry['range'] = [0, len(reason)]
                    if 'content' in entry:
                        entry['content'] = reason
        page['replaces'] = {'page_id': page['page_id'], 'revision': 0, 'attempt_id': prior['attempt_id'],
                            'raw_output_digest': prior['raw_output_digest'], 'cause': 'observed_reconciliation',
                            'authorization_root': digest('original rereading')}
        replacement = observe(page)
        self.plan['revision_authorizations'].append(dict(page['replaces'],
            replacement_raw_output_digest=replacement['raw_output_digest']))
        self.observations.append(replacement)
        root = composition.build_root(self.plan, self.observations)
        self.assertNotEqual(root['digest'], original_root['digest'])
        self.assertCode('review_composition_conflict', composition.assemble_result,
                        self.plan, self.observations, root, original_terminal)
        self.expected['value']['reason'] = reason
        self.assertEqual(self.assemble(), self.expected)

    def test_metadata_correction_is_directed_and_only_changes_reassessment_metadata(self):
        for before, after, accepted in (('confirmed', 'revised', True), ('revised', 'confirmed', False)):
            output = worked_result()
            output['reassessment'] = {'assignment_id': 'prior-controller', 'findings': [
                {'review_id': 'earlier-review', 'disposition': before,
                 'reason': 'Earlier metadata reason.', 'evidence': [SOURCE]}]}
            self.plan, self.observations, self.choices, self.expected = make_fixture(output)
            prior = self.observations[0]
            page = copy.deepcopy(prior['output'])
            page['revision'] = 1
            fragment = next(entry for entry in page['fragments'] if entry['path'] == '/reassessment')
            fragment['content']['findings'][0].update(disposition=after, reason='Observed metadata correction.')
            page['replaces'] = {'page_id': 'R1', 'revision': 0, 'attempt_id': prior['attempt_id'],
                                'raw_output_digest': prior['raw_output_digest'], 'cause': 'metadata_correction',
                                'authorization_root': digest('metadata correction receipt')}
            revised = observe(page)
            self.plan['revision_authorizations'].append(dict(page['replaces'],
                replacement_raw_output_digest=revised['raw_output_digest']))
            self.observations.append(revised)
            with self.subTest(before=before, after=after):
                if accepted:
                    actual = self.assemble()
                    self.assertEqual(actual['reassessment']['findings'][0]['disposition'], 'revised')
                    del actual['reassessment']
                    self.assertEqual(actual, worked_result())
                else:
                    self.assertCode('review_composition_conflict', composition.build_root,
                                    self.plan, self.observations)

    def test_malformed_records_raise_research_error_with_stable_code(self):
        for location, value in (('disposition_id', {}), ('terminal_level', True),
                                ('owner_ordinal', True), ('revision', True), ('json_number', float('nan'))):
            plan, observations = copy.deepcopy((self.plan, self.observations))
            if location == 'disposition_id':
                observations[1]['output']['dispositions'][0]['finding_id'] = value
            elif location == 'terminal_level':
                plan['terminal_choices']['/value/rubric/priority'] = [value]
            elif location == 'owner_ordinal':
                next(entry for entry in plan['ownership'] if entry['path'] == '/value/bar_ids')['ordinal'] = value
            elif location == 'revision':
                observations[0]['output']['revision'] = value
            else:
                plan['subject']['invalid_number'] = value
            if location != 'json_number':
                observations = [observe(row['output'], row['attempt_id']) for row in observations]
            with self.subTest(location=location):
                self.assertCode('review_composition_conflict', composition.build_root, plan, observations)

    def test_missing_required_or_omitted_adverse_page_cannot_form_root(self):
        self.assertCode('review_composition_pending', composition.build_root, self.plan,
                        [self.observations[0], self.observations[2]])
        plan = copy.deepcopy(self.plan)
        plan['required_pages'].remove('R2')
        plan['ownership'] = [entry for entry in plan['ownership'] if entry['page_id'] != 'R2']
        self.assertCode('review_composition_pending', composition.build_root, plan,
                        [self.observations[0], self.observations[2]])

    def test_extra_observation_and_raw_digest_tampering_are_rejected(self):
        observation = copy.deepcopy(self.observations[0])
        observation['output']['page_id'] = 'unplanned'
        observation['raw_output_digest'] = digest(observation['output'])
        self.assertCode('review_composition_conflict', composition.build_root, self.plan,
                        self.observations + [observation])
        self.observations[0]['output']['fragments'][0]['content'] += 'altered'
        self.assertCode('review_composition_conflict', composition.build_root, self.plan, self.observations)

    def test_root_is_deterministic_and_binds_every_header_and_page(self):
        root = composition.build_root(self.plan, self.observations)
        self.assertEqual(root, composition.build_root(self.plan, list(reversed(self.observations))))
        for mutate in ('header', 'tree', 'page', 'integration', 'selected', 'evidence'):
            plan, observations, altered = copy.deepcopy((self.plan, self.observations, root))
            if mutate == 'header':
                altered['header']['slot_id'] = 'other-slot'
            elif mutate == 'tree':
                altered['nodes'][altered['page_tree']]['pages'].pop()
            elif mutate == 'page':
                observations[0]['attempt_id'] = 'different-accepted-attempt'
            else:
                field = {'integration': 'integration_root', 'selected': 'selected_observations_root',
                         'evidence': 'evidence_obligations'}[mutate]
                plan[field] = digest('different') if mutate != 'evidence' else []
            with self.subTest(mutate=mutate):
                self.assertCode('review_composition_conflict', composition.assemble_result,
                                plan, observations, altered, terminal_for(self.plan, root, self.choices))

    def test_terminal_must_bind_exact_root_scope_and_permitted_choices(self):
        root = composition.build_root(self.plan, self.observations)
        for field, value in (('composition_root', digest('other')), ('epoch_id', 'other'),
                             ('role', 'adjudicator'), ('choices', dict(self.choices, **{'/value/status': 'sufficient'}))):
            terminal = terminal_for(self.plan, root, self.choices)
            terminal[field] = value
            with self.subTest(field=field):
                self.assertCode('review_composition_conflict', composition.validate_terminal_choices,
                                self.plan, terminal, root)
        terminal = terminal_for(self.plan, root, self.choices)
        terminal['choices']['/value/reason'] = 'Author patched reasoning.'
        self.assertCode('review_composition_conflict', composition.validate_terminal_choices,
                        self.plan, terminal, root)

    def test_root_tree_has_bounded_fanout_and_detects_deep_or_cyclic_references(self):
        # Many pages of one string exercise a real multi-level descriptor tree.
        reason = 'x' * (composition.TREE_FANOUT + 2)
        self.split_reason([(index, index + 1, 'x') for index in range(len(reason))])
        page = self.observations[0]['output']
        for index, fragment in enumerate(list(page['fragments'])):
            if fragment['path'] != '/value/reason':
                continue
            page_id = 'segment-' + str(index)
            self.plan['required_pages'].append(page_id)
            fragment['page_id'] = page_id
            for owner in self.plan['ownership']:
                if owner['path'] == fragment['path'] and owner['range'] == fragment['range']:
                    owner['page_id'] = page_id
            child = dict(copy.deepcopy(page), page_id=page_id, task_id='answer-' + page_id,
                         ownership=[{key: value for key, value in fragment.items()
                                     if key not in ('content', 'grounds')}], fragments=[fragment])
            self.observations.append(observe(child))
        page['fragments'] = [entry for entry in page['fragments'] if entry['path'] != '/value/reason']
        page['ownership'] = [entry for entry in page['ownership'] if entry['path'] != '/value/reason']
        self.observations[0] = observe(page)
        root = composition.build_root(self.plan, self.observations)
        self.assertGreater(root['header']['counts']['tree_depth'], 1)
        for node in root['nodes'].values():
            self.assertLessEqual(len(node.get('children', node.get('pages', []))), composition.TREE_FANOUT)
        terminal = terminal_for(self.plan, root, self.choices)
        self.assertEqual(composition.assemble_result(self.plan, self.observations, root, terminal)['value']['reason'], reason)
        altered = copy.deepcopy(root)
        altered['nodes'][altered['page_tree']] = {'kind': 'branch', 'children': [altered['page_tree']]}
        self.assertCode('review_composition_conflict', composition.assemble_result,
                        self.plan, self.observations, altered, terminal)
        altered = copy.deepcopy(root)
        reference = altered['page_tree']
        for index in range(composition.MAX_TREE_DEPTH + 1):
            node = {'kind': 'branch', 'children': [reference]}
            key = digest({'domain': composition.PROTOCOL + ':page-tree', 'value': node})
            altered['nodes'][key] = node
            reference = key
        altered['page_tree'] = reference
        altered['digest'] = digest({'domain': composition.PROTOCOL + ':root', 'value': {
            key: altered[key] for key in ('version', 'header', 'page_tree')}})
        self.assertCode('review_composition_conflict', composition.assemble_result,
                        self.plan, self.observations, altered, terminal)


class EffectiveOutputTests(unittest.TestCase):
    def test_ordinary_and_legacy_are_identity_cases_including_authorized_correction(self):
        for output in (worked_result(), worked_adjudication(), {'legacy': ['unmodified', None]}):
            selection = {'controller_id': 'controller', 'kind': 'ordinary', 'output': output,
                         'output_digest': digest(output), 'terminal_attempt_id': 'ordinary-attempt',
                         'terminal_raw_output_digest': digest('original before authorized correction'),
                         'currency_state': 'current', 'context_state': 'verified'}
            original = copy.deepcopy(selection)
            result = composition.effective_review_output('controller', selection)
            self.assertEqual(result['kind'], 'ordinary')
            self.assertEqual(result['effective_output'], output)
            self.assertEqual(result['effective_output_digest'], digest(output))
            self.assertIsNone(result['composition_root'])
            result['effective_output'].clear()
            self.assertEqual(selection, original)

    def test_paged_interface_keeps_raw_terminal_and_effective_result_separate(self):
        for role, output in (('result', worked_result()), ('adjudicator', worked_adjudication())):
            plan, observations, choices, expected = make_fixture(output, role)
            root = composition.build_root(plan, observations)
            terminal = terminal_for(plan, root, choices)
            selection = {'controller_id': 'controller', 'kind': 'paged', 'plan': plan,
                         'observations': observations, 'root': root, 'terminal': observe(terminal, 'TR'),
                         'completion_root': digest('complete'), 'currency_state': 'current', 'context_state': 'verified'}
            with self.subTest(role=role):
                actual = composition.effective_review_output('controller', selection)
                self.assertEqual(actual['effective_output'], expected)
                self.assertEqual(actual['effective_output_digest'], digest(expected))
                self.assertEqual(actual['terminal_raw_output_digest'], digest(terminal))
                self.assertNotEqual(actual['terminal_raw_output_digest'], actual['effective_output_digest'])
                self.assertEqual(actual['terminal_attempt_id'], 'TR')
                self.assertEqual(actual['composition_root'], root['digest'])
                self.assertEqual(actual['selected_observations_root'], plan['selected_observations_root'])

    def test_pending_and_incomplete_have_no_fabricated_scientific_output(self):
        self.assertEqual(composition.effective_review_output('controller')['kind'], 'pending')
        for kind in ('pending', 'incomplete'):
            selection = {'controller_id': 'controller', 'kind': kind, 'completion_root': digest('unfinished'),
                         'outcome': {'origin': 'execution', 'unresolved_scope_root': digest('O7')}}
            actual = composition.effective_review_output('controller', selection)
            self.assertEqual(actual['kind'], kind)
            self.assertNotIn('effective_output', actual)
            self.assertNotIn('effective_output_digest', actual)

    def test_wrong_controller_raw_or_effective_digest_rejects(self):
        selection = {'controller_id': 'controller', 'kind': 'ordinary', 'output': worked_result(),
                     'output_digest': digest('different'), 'terminal_attempt_id': 'attempt',
                     'terminal_raw_output_digest': digest('raw')}
        with self.assertRaises(ResearchError):
            composition.effective_review_output('controller', selection)
        selection['output_digest'] = digest(selection['output'])
        with self.assertRaises(ResearchError):
            composition.effective_review_output('other', selection)

    def test_paged_raw_terminal_tampering_rejects_without_using_effective_digest(self):
        plan, observations, choices, expected = make_fixture()
        root = composition.build_root(plan, observations)
        terminal = observe(terminal_for(plan, root, choices), 'terminal')
        terminal['raw_output_digest'] = digest(expected)
        with self.assertRaises(ResearchError) as raised:
            composition.effective_review_output('controller', {'controller_id': 'controller', 'kind': 'paged',
                'plan': plan, 'observations': observations, 'root': root, 'terminal': terminal,
                'completion_root': digest('complete')})
        self.assertEqual(raised.exception.code, 'review_composition_conflict')


if __name__ == '__main__':
    unittest.main()
