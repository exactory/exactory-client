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
        if 'reassessment' in output:
            values['/reassessment'] = output['reassessment']
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

    def test_repaired_adjudication_composes_and_projects_reassessment(self):
        from research_harness import review_staged as staged
        output = dict(worked_adjudication(), reassessment=dict(assignment_id='contaminated-adjudicator',
            findings=[dict(review_id='historical-finding', disposition='revised',
                reason='The original condition remains necessary.', evidence=[SOURCE])]))
        scientific, sidecar = staged.project_ordinary_final(dict(output, terminal={'visible_conditions': []}))
        self.assertEqual(scientific, output)
        self.assertEqual(sidecar, {'terminal': {'visible_conditions': []}})
        plan, observations, choices, expected = make_fixture(output, 'adjudicator')
        root = composition.build_root(plan, observations)
        self.assertEqual(composition.assemble_result(plan, observations, root,
            terminal_for(plan, root, choices)), expected)

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

    def test_self_certified_page_grounds_cannot_authorize_scientific_content(self):
        for observation in self.observations:
            page = observation['output']
            page['grounding'] = [{'invented': True}]
            for fragment in page['fragments']:
                fragment['grounds'] = page['grounding']
            for disposition in page['dispositions']:
                disposition['grounds'] = page['grounding']
            observation.update(observe(page))
        self.assertCode('review_composition_conflict', composition.build_root,
                        self.plan, self.observations)

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


class PageGroundingTests(unittest.TestCase):
    def setUp(self):
        from test_review_staged_fixtures import epoch, full_card, selection
        self.epoch = epoch()
        self.card = full_card(self.epoch)
        self.tasks, self.selection = selection(self.epoch, [self.card])
        self.plan, pages, _, _ = make_fixture()
        self.page = pages[0]['output']
        self.original = self.card['critical_spans'][0]
        self.delivered = {'originals': [], 'answer': {'conclusions': self.card['conclusions']}}
        self.actual_input = {'intervals': [self.original]}

    def validate(self, ground):
        page = copy.deepcopy(self.page)
        page['grounding'] = [ground]
        for fragment in page['fragments']:
            fragment['grounds'] = [ground]
        return composition.validate_page_grounding(page, epoch=self.epoch,
            selection=self.selection, tasks=self.tasks, actual_input=self.actual_input,
            delivered=self.delivered)

    def test_delivered_resolved_conclusion_is_a_valid_page_ground(self):
        self.validate(self.card['conclusions'][0])

    def test_fabricated_altered_and_undelivered_records_are_rejected(self):
        for ground in ({'invented': True}, dict(self.card['conclusions'][0], id='invented'),
                       dict(self.card['conclusions'][0], text='A different conclusion.')):
            with self.subTest(ground=ground), self.assertRaises(ResearchError) as raised:
                self.validate(ground)
            self.assertEqual(raised.exception.code, 'review_composition_conflict')
        self.delivered['answer']['conclusions'] = []
        with self.assertRaises(ResearchError) as raised:
            self.validate(self.card['conclusions'][0])
        self.assertEqual(raised.exception.code, 'review_composition_conflict')

    def test_unresolved_record_cannot_ground_a_page_even_when_delivered(self):
        card = self.card
        card['dependencies'] = ['missing-original-check']
        from test_review_staged_fixtures import selection
        self.tasks, self.selection = selection(self.epoch, [card])
        with self.assertRaises(ResearchError) as raised:
            self.validate(card['conclusions'][0])
        self.assertEqual(raised.exception.code, 'review_composition_conflict')

    def test_quotation_requires_exact_nonempty_delivered_original(self):
        self.validate(self.original)
        source = next(row for row in self.epoch['manifest']['sources']
                      if row['id'] == self.original['source_id'])
        from research_harness import review_staged as staged
        for ground in (dict(self.original, sha256=digest('invented')),
                       staged.source_span(source, 0, 0)):
            with self.subTest(ground=ground), self.assertRaises(ResearchError) as raised:
                self.validate(ground)
            self.assertEqual(raised.exception.code, 'review_composition_conflict')
        self.actual_input['intervals'] = []
        with self.assertRaises(ResearchError) as raised:
            self.validate(self.original)
        self.assertEqual(raised.exception.code, 'review_composition_conflict')


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




# Final pages use the ordinary output fixtures above and the native/study
# fixtures of the base. No source text or paths from a live record are copied.
def build_final_rule_cases():
    import hashlib
    from types import SimpleNamespace
    from strategy_fixtures import StrategyCase
    from test_openness_finding_answer import build_packet, build_answer
    from test_research_problem_reviews import ProblemReviewTests
    from test_research_verification import ResearchVerificationTests
    from research_harness import native_review, development

    source = dict(kind='record', record_kind='problem_reading', id='reading', digest='d' * 64)
    fixture = SimpleNamespace(evidence=source, supporting=source)
    cases = []
    for stage in ('bar', 'slate', 'result'):
        output = ProblemReviewTests.build_review_output(fixture, stage)
        packet = dict(role=stage, subject=dict(kind='problem'), problem_evidence={},
                      material_objections=[], historical_reviews=[], bar_ids=output['value']['bar_ids'])
        if stage == 'slate':
            packet['candidates'] = [dict(id='candidate-a'), dict(id='candidate-b'), dict(id='candidate-c')]
            output['value']['rubric_potential'] = {row['id']: dict(significance=dict(B1=2, B2=1), priority=2)
                                                    for row in packet['candidates']}
            for row in output['value']['assurances']:
                row['status'] = 'not_assessed'
        cases.append((stage, packet, output))
    # The generic value contract owns work items as well as assurances.
    output = StrategyCase.review_response(fixture, 'result')
    output['value']['work_items'] = [dict(id='work-a', classification='validity', status='accepted',
                                         reason='The exact work was checked.', evidence=[source])]
    packet = dict(role='result', dossier=dict(work_items=['work-a'], material_change=dict(prior_objections=[dict(id='prior-objection')])), bar_ids=output['value']['bar_ids'], historical_reviews=[dict(review=dict(id='earlier-review', objections=[dict(id='historical-objection')]))])
    output['value']['objection_findings'] = [dict(id=key, status='continuing', reason='The recorded objection remains.', evidence=[source]) for key in ('prior-objection', 'historical-objection')]
    cases.append(('result', packet, output))
    # A context repair inherits the role table and adds historical final targets.
    packet, output = copy.deepcopy(cases[2][1:])
    packet['context_repair'] = dict(assignment_id='predecessor')
    packet['historical_reviews'] = [dict(id='old-review', review=dict(objections=[]))]
    output['reassessment'] = dict(assignment_id='predecessor', findings=[dict(review_id='old-review',
        disposition='revised', reason='The retained finding was independently reassessed.', evidence=[source])])
    cases.append(('result', packet, output))
    packet = dict(role='adjudicator', subject=dict(kind='problem'), problem_evidence={},
                  objection=dict(id='O7', claim='The finite domain needs checking.'))
    output = dict(objection_id='O7', disposition='upheld', correction_admissible=None,
                  reason='The supplied evidence retains the objection.', evidence=[source])
    cases.append(('adjudicator', packet, output))
    # Native fixtures have the actual, versioned approval shape.
    for kind in ('proposal', 'result', 'recovery'):
        native = dict(kind=kind, subject_digest='a' * 64, claim_digest='b' * 64,
                      reviewer=dict(source='host', actor_id='reader', attestation_id='review-assignment:synthetic'),
                      contract={}, scientific_context={})
        keys = dict(proposal=native_review.PROPOSAL_FINDINGS, result=native_review.RESULT_FINDINGS,
                    recovery=native_review.RECOVERY_FINDINGS)[kind]
        output = dict(subject_digest=native['subject_digest'], reviewer=native['reviewer'], decision='approve',
                      findings={key: 'The exact supplied subject was checked.' for key in keys})
        if kind != 'recovery':
            output['claim_digest'] = native['claim_digest']
        if kind != 'result':
            output.update(schema_version=1, unresolved_objections=[])
        native_review.validate_output(native, output)
        cases.append(('native_math', dict(native=native), output))
    packet = build_packet()
    packet['native']['scientific_context']['openness_minimum']['neighbours'] = ['Neighbour one', 'Neighbour two']
    packet['reading_final'] = dict(protocol='exactory-openness-reading-v7', page=dict(targets=['t0', 't1', 't2']))
    output = build_answer(packet)
    output['findings']['openness']['coverage'] = [dict(formulation=formulation, assessment='The search is complete.')
        for formulation in ['The exact statement', 'Neighbour one', 'Neighbour two']]
    native_review.validate_finding(packet, output)
    cases.append(('native_math', packet, output))
    packet = copy.deepcopy(packet)
    packet['native']['scientific_context'].pop('audit_forecast')
    packet['native']['contract'] = {}
    packet['native']['scientific_context']['openness_minimum'].update(schema_version=1, searches=[], reading_digests=[], settling_work_id=None)
    output = copy.deepcopy(cases[7][2])
    output.update({key: packet['native'][key] for key in ('subject_digest', 'claim_digest', 'reviewer')})
    output['schema_version'] = 2
    output['findings']['openness'] = dict(outcome='unresolved', statement_digest='b' * 64,
        evidence=['e' * 64], searched_at='2026-11-14', coverage='The exact statement and both neighbours were searched.',
        reason='The delivered evidence leaves this mathematical question unresolved.',
        minimum=packet['native']['scientific_context']['openness_minimum'])
    native_review.validate_output(packet['native'], output)
    cases.append(('native_math', packet, output))
    # Delivered source bytes make source grammar and locator checks pure.
    body = 'A synthetic exact source for the finite claim.\n'
    artifact = dict(path='research/sources/objects/' + hashlib.sha256(body.encode()).hexdigest(), sha256=hashlib.sha256(body.encode()).hexdigest(),
                    size=len(body.encode()), media_type='text/plain')
    linked = dict(version_id='arxiv:2401.00001v1', source_id='capture', artifact=artifact,
                  locator=dict(kind='span', start=0, end=12, excerpt=body[:12], sha256=hashlib.sha256(body[:12].encode()).hexdigest()))
    evidence = [dict(artifact=artifact, content=body, encoding='utf-8')]
    packet = dict(role='verification', paper=dict(kind='verification', target=dict(id=linked['version_id'])), evidence=evidence)
    output = dict(verdict=ResearchVerificationTests.verdict(None), checks=[dict(dimension=key, reason='Checked exact delivered bytes.',
        evidence=[linked]) for key in ('soundness', 'novelty', 'impact')])
    cases.append(('verification', packet, output))
    output = StrategyCase.review_response(fixture, 'result')
    assessor = dict(id='independent-reader', kind='agent', provenance=artifact,
                    relationship='A separate reader.', independence_basis='A separate review context.')
    output['support'] = dict(id='readiness', candidate_digest='c' * 64, assessor=assessor, verdict='ready',
        limitations=['Only the supplied evidence was assessed.'], checks=[dict(kind=key, status='passed',
            reason='The exact candidate was checked.', evidence=[dict(kind='source', link=linked)])
            for key in development._REVIEW_CHECKS])
    packet = dict(role='result', dossier={}, readiness=dict(kind='readiness'),
        support_contract=dict(candidate_digest='c' * 64), assessor=assessor, evidence=evidence,
        bar_ids=output['value']['bar_ids'])
    cases.append(('result', packet, output))
    for index in (0, 1, 5, 12):
        role, packet, output = copy.deepcopy(cases[index])
        packet['context_repair'] = dict(assignment_id='predecessor')
        packet['historical_reviews'] = [dict(id='historical-review', review=dict(objections=[]))]
        output['reassessment'] = dict(assignment_id='predecessor', findings=[dict(review_id='historical-review',
            disposition='revised', reason='The earlier final was assessed again.', evidence=[source])])
        cases.append((role, packet, output))
    return cases


class FinalCompositionRuleTests(unittest.TestCase):
    def setUp(self):
        self.cases = build_final_rule_cases()

    def iter_cases(self):
        for role, packet, output in self.cases:
            rule = composition.find_rule(role, packet)
            yield rule, packet, output

    def build_partitions(self, targets):
        return ([targets], [[target] for target in targets], [targets[index:index + 3] for index in range(0, len(targets), 3)])

    def test_every_rule_round_trips_stored_real_shape_outputs_and_digests(self):
        import json
        import tempfile
        from research_harness.artifacts import ArtifactStore
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = ArtifactStore(temporary)
            for rule, packet, original in self.iter_cases():
                if rule['id'].endswith('/math-openness'):
                    continue
                reference = artifacts.put(json.dumps(original).encode(), 'application/json')
                output = json.loads(artifacts.read(reference))
                before = digest(dict(packet=packet, output=output, rule=rule))
                targets = composition.list_targets(rule, packet)
                for partition in self.build_partitions(targets):
                    with self.subTest(rule=rule['id'], pages=len(partition)):
                        pages, closing = composition.split_output(rule, packet, output, partition)
                        composed = composition.compose_output(rule, packet, pages, closing)
                        self.assertEqual(composed, output)
                        self.assertEqual(digest(composed), digest(output))
                self.assertEqual(before, digest(dict(packet=packet, output=output, rule=rule)))

    def test_guard2_every_field_of_each_complete_output_has_one_owner(self):
        for rule, packet, output in self.iter_cases():
            with self.subTest(rule=rule['id']):
                composition.validate_output_owners(rule, output)
                self.assertTrue(composition.describe_owners(rule))

    def test_guard2_removing_any_owner_fails(self):
        for rule, packet, output in self.iter_cases():
            for index, field in enumerate(rule['fields']):
                bad = copy.deepcopy(rule)
                bad['fields'].pop(index)
                with self.subTest(rule=rule['id'], field=field['path']), self.assertRaises(ResearchError):
                    composition.validate_output_owners(bad, output)

    def test_guard2_duplicating_any_owner_fails(self):
        for rule, packet, output in self.iter_cases():
            for field in rule['fields']:
                bad = copy.deepcopy(rule)
                bad['fields'].append(dict(field, owner='closing' if field['owner'] != 'closing' else 'page'))
                with self.subTest(rule=rule['id'], field=field['path']), self.assertRaises(ResearchError):
                    composition.validate_output_owners(bad, output)

    def test_guard2_unknown_fields_and_overlapping_subtrees_fail(self):
        for rule, packet, output in self.iter_cases():
            changed = dict(output, new_scientific_field=True)
            with self.subTest(rule=rule['id']), self.assertRaises(ResearchError):
                composition.validate_output_owners(rule, changed)
            bad = copy.deepcopy(rule)
            bad['fields'].append(dict(path='/**', owner='closing', target=None))
            with self.subTest(rule=rule['id']), self.assertRaises(ResearchError):
                composition.validate_output_owners(bad, output)

    def test_targets_follow_adapters_and_repairs_include_historical_finals(self):
        from research_harness.review_parts_roles import get_adapter
        for rule, packet, output in self.iter_cases():
            if rule['adapter']:
                if rule['adapter'] == 'context_repair' and rule.get('base_adapter') is None:
                    ordinary = dict(packet)
                    ordinary.pop('context_repair')
                    base_targets = composition.list_targets(composition.find_rule(rule['role'], ordinary), ordinary)
                    self.assertEqual(composition.list_targets(rule, packet), base_targets + ([dict(id='historical-review', label='historical-review')] if 'historical-review' not in {row['id'] for row in base_targets} else []))
                else:
                    self.assertEqual(composition.list_targets(rule, packet), get_adapter(rule['adapter'])['targets'](packet))
        self.assertIsNone(composition.find_rule('manuscript', {}))
        self.assertIsNone(composition.find_rule('standalone', {}))
        self.assertIsNone(composition.find_rule('unknown', {}))
        self.assertEqual(set(composition.ROLES_WITHOUT_RULES), {'manuscript', 'standalone'})

    def test_valid_pages_and_closings_pass_their_role_checks(self):
        for rule, packet, output in self.iter_cases():
            if rule['id'].endswith('/math-openness'):
                continue
            targets = composition.list_targets(rule, packet)
            partition = [[target] for target in targets]
            pages, closing = composition.split_output(rule, packet, output, partition)
            for assigned, page in zip(partition, pages):
                with self.subTest(rule=rule['id'], target=assigned):
                    composition.validate_page_output(rule, packet, assigned, page['output'])
            rows = composition.build_decision_rows(rule, packet, pages)
            with self.subTest(rule=rule['id'], closing=True):
                composition.validate_closing_output(rule, packet, rows, closing)

    def test_each_page_field_refuses_invalid_values_under_its_role_check(self):
        # The field paths of the stored outputs drive this test, so adding a
        # page-owned field requires a refusal witness here.
        checked = set()
        for rule, packet, output in self.iter_cases():
            if rule['id'].endswith('/math-openness'):
                continue
            targets = composition.list_targets(rule, packet)
            pages, closing = composition.split_output(rule, packet, output, [[target] for target in targets])
            for target, page in zip(targets, pages):
                row = page['output']['targets'][0]
                if 'fields' not in row:
                    continue
                for path, value in row['fields'].items():
                    if isinstance(value, (dict, list)) and not value:
                        continue
                    bad = copy.deepcopy(page)
                    bad['output']['targets'][0]['fields'][path] = 'invalid' if path == '/correction_admissible' else None
                    expected = ('review.reassessment' if path.startswith('/reassessment/') else
                                'review.support' if path.startswith('/support/') else
                                'evidence.shape' if path.endswith('/evidence') and rule['role'] != 'native_math' else rule['checks'][0])
                    # A value's malformed evidence list uses the shared evidence
                    # grammar check; verification owns the raw links-array check.
                    if path.startswith('/reassessment/') and path.endswith('/evidence'):
                        expected = 'evidence.shape'
                    if rule.get('base_adapter', rule['adapter']) == 'study_verification':
                        expected = 'review.shape'
                    with self.subTest(rule=rule['id'], path=path), self.assertRaises(ResearchError) as caught:
                        composition.validate_page_output(rule, packet, [target], bad['output'])
                    self.assertEqual(caught.exception.contract_check, expected, (rule['id'], path))
                    checked.add(rule['id'])
        self.assertEqual(checked, {rule['id'] for rule, _, _ in self.iter_cases()} - {composition.FINAL_RULE_PREFIX + 'bar', composition.FINAL_RULE_PREFIX + 'problem-openness', composition.FINAL_RULE_PREFIX + 'math-openness'})

    def test_page_identity_sentence_bound_and_ownership_are_checked(self):
        rule, packet, output = next(self.iter_cases())
        targets = composition.list_targets(rule, packet)
        pages, closing = composition.split_output(rule, packet, output, [targets])
        for change in ('sentence', 'target', 'target-object', 'closing-field', 'extra'):
            bad = copy.deepcopy(pages[0])
            if change == 'sentence':
                bad['output']['targets'][0]['sentence'] = 's' * 301
            elif change == 'target':
                bad['output']['targets'][0]['target'] = 'unknown'
            elif change == 'target-object':
                bad['output']['targets'][0]['target'] = {}
            elif change == 'closing-field':
                bad['output']['targets'][0]['fields']['/value/status'] = 'sufficient'
            else:
                bad['output']['extra'] = True
            with self.subTest(change=change), self.assertRaises(ResearchError):
                composition.validate_page_output(rule, packet, targets, bad['output'])

    def test_each_rule_page_field_limits_fit_all_final_bounds_below_eighty_thousand(self):
        from research_harness.review_route_capacity import _describe_answer_bounds
        for rule, packet, output in self.iter_cases():
            for count in range(1, 41):
                bound = _describe_answer_bounds('final', count, 4000)[0]
                self.assertLessEqual(bound, 80000)
                self.assertLessEqual(composition.describe_page_answer_bound(rule, count), bound)
                self.assertLessEqual(rule['closing_schema']['max_serialized_characters'], bound)
            self.assertEqual(_describe_answer_bounds('final', 40, 4000)[0], 80000)
            self.assertLessEqual(composition.describe_page_answer_bound(rule, 42), 80000)
            self.assertGreater(composition.describe_page_answer_bound(rule, 43), 80000)


class FindingFinalCompositionTests(unittest.TestCase):
    def setUp(self):
        self.role, self.packet, self.output = next(case for case in build_final_rule_cases()
            if composition.find_rule(case[0], case[1])['id'].endswith('/problem-openness'))
        self.rule = composition.find_rule(self.role, self.packet)
        self.targets = composition.list_targets(self.rule, self.packet)
        self.pages, self.closing = composition.split_output(self.rule, self.packet, self.output,
                                                           [[target] for target in self.targets])

    def test_resolved_outcome_with_one_page_ground_is_refused_by_validate_finding(self):
        from research_harness.native_review import validate_finding
        self.pages[0]['output']['targets'][0]['grounds'] = [dict(kind='reviewer_gap', text='A decisive source is missing.')]
        composition.validate_page_output(self.rule, self.packet, [self.targets[0]], self.pages[0]['output'])
        composed = composition.compose_output(self.rule, self.packet, self.pages, self.closing)
        with self.assertRaises(ResearchError) as caught:
            validate_finding(self.packet, composed)
        self.assertEqual(caught.exception.contract_check, 'openness.finding')
        self.assertEqual(caught.exception.details['rule'], 2)

    def test_unresolved_ground_order_and_recorded_refs_are_preserved(self):
        from research_harness.native_review import validate_finding
        reference = dict(path='research/sources/objects/' + 'f' * 64, sha256='f' * 64, size=23, media_type='text/plain')
        grounds = [dict(kind='reviewer_gap', text='The first formulation lacks a full reading.'),
                   dict(kind='own_argument', text='The next formulation has an independent argument.', refs=[reference]),
                   dict(kind='packet_defect', text='The overall packet has a distinct defect.')]
        self.pages[0]['output']['targets'][0]['grounds'] = [grounds[0]]
        self.pages[1]['output']['targets'][0]['grounds'] = [grounds[1]]
        self.closing.update(outcome='unresolved', grounds=[grounds[2]])
        for target, page in zip(self.targets, self.pages):
            composition.validate_page_output(self.rule, self.packet, [target], page['output'])
        composition.validate_closing_output(self.rule, self.packet, [], self.closing)
        composed = composition.compose_output(self.rule, self.packet, self.pages, self.closing)
        validate_finding(self.packet, composed)
        self.assertEqual(composed['findings']['openness']['grounds'], grounds)
        self.assertEqual(composed['findings']['openness']['grounds'][1]['refs'], [reference])
        self.assertNotIn('sentence', str(composed))

    def test_all_finding_page_fields_use_the_native_finding_check(self):
        for field, value in (('coverage', 'x' * 801), ('grounds', 'none'), ('grounds', [dict(kind='unknown', text='Ground.')]),
                             ('grounds', [dict(kind='own_argument', text='')]),
                             ('grounds', [dict(kind='source_settles', text='A source settles it.', work_id='absent')]),
                             ('grounds', [dict(kind='own_argument', text='s' * 601)])):
            page = copy.deepcopy(self.pages[0])
            page['output']['targets'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ResearchError) as caught:
                composition.validate_page_output(self.rule, self.packet, [self.targets[0]], page['output'])
            self.assertEqual(caught.exception.contract_check, 'openness.finding')

    def test_closing_findings_outcome_reason_and_grounds_use_native_checks(self):
        for key, value in (('statement', ''), ('assumptions', None), ('scope', []), ('dependencies', {}),
                           ('policy', False), ('outcome', 'approved'), ('reason', 'r' * 4001), ('grounds', 'none')):
            closing = dict(self.closing, **{key: value})
            with self.subTest(field=key), self.assertRaises(ResearchError) as caught:
                composition.validate_closing_output(self.rule, self.packet, [], closing)
            self.assertEqual(caught.exception.contract_check, 'openness.finding')

    def test_legacy_unstructured_finding_coverage_round_trips_without_digest_change(self):
        from research_harness.native_review import validate_finding
        packet = copy.deepcopy(self.packet)
        packet.pop('reading_final')
        output = copy.deepcopy(self.output)
        output['findings']['openness']['coverage'] = 'The complete coverage from an earlier final.'
        validate_finding(packet, output)
        for partition in ([self.targets], [[target] for target in self.targets]):
            pages, closing = composition.split_output(self.rule, packet, output, partition)
            self.assertEqual(digest(composition.compose_output(self.rule, packet, pages, closing)), digest(output))

    def test_incident_five_thousand_decision_rows_fit_serialized_policy_limit(self):
        # Source: 20261112235500-openness-chain-final-turn-over-route-limit-after-57-turns.
        # The source's unbounded final is represented by many synthetic targets,
        # without copying its mathematical text or research-record identity.
        from research_harness.review_route_capacity import DECISION_ROW_CHARACTERS
        packet = copy.deepcopy(self.packet)
        packet['native']['scientific_context']['openness_minimum']['neighbours'] = [
            'Synthetic formulation ' + str(index) for index in range(1, 5000)]
        from research_harness.artifacts import describe_artifact
        references = [describe_artifact(('Recorded detail ' + str(index)).encode(), 'text/plain') for index in range(6)]
        packet['handle_table'] = {'e' + str(index): dict(reference=reference) for index, reference in enumerate(references)}
        grounds = [dict(kind='reviewer_gap', text='The search needs a full reading.',
                        refs=references)]
        pages = [dict(targets=['t' + str(index)], output=dict(targets=[dict(formulation='t' + str(index), coverage='The exact formulation was checked.',
                                    grounds=grounds, sentence='s' * composition.SENTENCE_CHARACTERS)]))
                 for index in range(5000)]
        rows = composition.build_decision_rows(self.rule, packet, pages)
        targets = composition.list_targets(self.rule, packet)
        for index in (0, 4999):
            composition.validate_page_output(self.rule, packet, [targets[index]], pages[index]['output'])
        self.assertEqual(len(rows), 5000)
        for row in rows:
            self.assertEqual(len(row[2]), 300)
            self.assertLessEqual(composition._measure_final_value(row), DECISION_ROW_CHARACTERS)
            remaining = [handle for handle in composition._collect_reference_handles(packet, grounds) if handle not in row[3]]
            next_handle = remaining[0] if remaining else 'e6'
            self.assertGreater(composition._measure_final_value(row[:3] + [row[3] + [next_handle]]), DECISION_ROW_CHARACTERS)

    def test_decision_rows_count_escaping_and_all_ground_kinds(self):
        from research_harness.native_review import GROUND_RULES
        from research_harness.review_route_capacity import DECISION_ROW_CHARACTERS
        row = self.pages[0]['output']['targets'][0]
        row['sentence'] = '\\"\n' * 100
        row['grounds'] = [dict(kind=kind, text='An independent scientific ground.') for kind in GROUND_RULES]
        rows = composition.build_decision_rows(self.rule, self.packet, self.pages)
        self.assertLessEqual(composition._measure_final_value(rows[0]), DECISION_ROW_CHARACTERS)
        self.assertEqual(set(rows[0][1]), set(GROUND_RULES))
        self.assertEqual(row['sentence'], '\\"\n' * 100)


class OriginalValidatorParityTests(unittest.TestCase):
    def test_page_value_and_native_refusals_match_the_original_check_and_code(self):
        from research_harness import native_review, research_decisions
        from research_harness.review_refusal import contract_check
        witnessed = set()
        for role, packet, output in build_final_rule_cases():
            rule = composition.find_rule(role, packet)
            if rule['id'].endswith('/math-openness'):
                continue
            targets = composition.list_targets(rule, packet)
            pages, closing = composition.split_output(rule, packet, output, [[target] for target in targets])
            for target, page in zip(targets, pages):
                for path, value in page['output']['targets'][0].get('fields', {}).items():
                    if not (path.startswith('/value/') and not isinstance(value, (list, dict))
                            or role == 'native_math'):
                        continue
                    changed = copy.deepcopy(output)
                    composition._write_path(changed, path, [])
                    if role == 'native_math':
                        def validate():
                            with contract_check('native.output'):
                                native_review.validate_output(packet['native'], changed)
                    else:
                        def validate():
                            problem = packet.get('subject', {}).get('kind') == 'problem'
                            dossier = dict(payload=dict(work_items=packet.get('dossier', {}).get('work_items', []), material_change=None))
                            with contract_check('review.value'):
                                research_decisions._validate_value({}, None, changed['value'], role, dossier,
                                    expected_checks=CHECKS if problem else research_decisions.ALL_CHECKS,
                                    problem={} if problem else None, slate_packet=packet, require_two_bars=False,
                                    evidence_validator=lambda records, artifacts, values, **kwargs:
                                        composition._validate_packet_evidence(packet, values, **kwargs))
                    with self.subTest(rule=rule['id'], path=path):
                        with self.assertRaises(ResearchError) as original:
                            validate()
                        bad_page = copy.deepcopy(page)
                        bad_page['output']['targets'][0]['fields'][path] = []
                        with self.assertRaises(ResearchError) as paged:
                            composition.validate_page_output(rule, packet, [target], bad_page['output'])
                        self.assertEqual((paged.exception.code, paged.exception.contract_check),
                                         (original.exception.code, original.exception.contract_check))
                        witnessed.add(rule['id'])
        self.assertIn(composition.FINAL_RULE_PREFIX + 'native-result', witnessed)
        self.assertIn(composition.FINAL_RULE_PREFIX + 'slate', witnessed)

    def test_numeric_and_escaped_candidate_ids_keep_dictionary_shapes(self):
        role, packet, output = copy.deepcopy(build_final_rule_cases()[1])
        for index, key in enumerate(('10', 'a/b', 'x~y')):
            prior = packet['candidates'][index]['id']
            packet['candidates'][index]['id'] = key
            output['value']['rubric_potential'][key] = output['value']['rubric_potential'].pop(prior)
        rule = composition.find_rule(role, packet)
        targets = composition.list_targets(rule, packet)
        pages, closing = composition.split_output(rule, packet, output, [[target] for target in targets])
        self.assertEqual(composition.compose_output(rule, packet, pages, closing), output)

    def test_native_assessments_without_approval_bindings_round_trip(self):
        from research_harness.native_review import validate_output
        for role, packet, original in build_final_rule_cases():
            if role != 'native_math' or packet['native']['kind'] == 'proposal' or composition.find_rule(role, packet)['id'].endswith('/math-openness'):
                continue
            for decision in ('revise', 'reject'):
                output = dict(decision=decision, reason='The exact subject needs further work.', objections=[])
                validate_output(packet['native'], output)
                rule = composition.find_rule(role, packet)
                if rule['id'].endswith('/problem-openness'):
                    continue
                targets = composition.list_targets(rule, packet)
                pages, closing = composition.split_output(rule, packet, output, [targets])
                self.assertEqual(composition.compose_output(rule, packet, pages, closing), output)




class StoredFinalAcceptanceTests(unittest.TestCase):
    def assert_stored_final(self, role, packet, output):
        rule = composition.find_rule(role, packet)
        targets = composition.list_targets(rule, packet)
        partitions = ([targets], [[target] for target in targets],
                      [targets[index:index + 2] for index in range(0, len(targets), 2)])
        for partition in partitions:
            pages, closing = composition.split_output(rule, packet, output, partition)
            self.assertEqual(digest(composition.compose_output(rule, packet, pages, closing)), digest(output))

    def test_observed_bar_and_result_attempt_outputs_preserve_stored_digests(self):
        import json
        from test_research_problem_reviews import ProblemReviewTests
        fixture = ProblemReviewTests()
        fixture.setUp()
        try:
            fixture.observe_result_reviews()
            records = fixture.read_records()
            for attempt in records['review_attempt'].values():
                assignment = records['review_assignment'][attempt['assignment_id']]
                packet = json.loads(fixture.artifacts.read(assignment['packet']))
                output = json.loads(fixture.artifacts.read(attempt['output']))
                self.assert_stored_final(assignment['role'], packet, output)
        finally:
            fixture.doCleanups()

    def test_observed_native_verification_and_readiness_final_attempts_preserve_digests(self):
        import json
        from codex_route_fixtures import invoke_codex
        from test_review_parts_native import NativePartsTests
        from test_review_parts_study import VerificationPartsTests, ReadinessPartsTests
        from research_harness import review_parts
        native = NativePartsTests()
        native.setUp()
        try:
            native.limit_input()
            _, final = native.finish_series()
            packet = native.read_packet(final)
            inventory = review_parts.read_inventory(native.artifacts, packet['review_parts']['inventory'])
            citations = list(dict.fromkeys(ref for unit in inventory['units'] for ref in unit['citations']))
            invoke_codex(native, final['id'], native.final_output(citations), 'composition-final')
            attempt = native.store.snapshot()['records']['review_attempt']['composition-final']
            self.assert_stored_final('native_math', packet, json.loads(native.artifacts.read(attempt['output'])))
        finally:
            native.doCleanups()
        for fixture_type in (VerificationPartsTests, ReadinessPartsTests):
            fixture = fixture_type()
            fixture.setUp()
            try:
                if fixture.role == 'verification':
                    fixture.enlarge_verification()
                else:
                    fixture.enlarge_readiness()
                _, final = fixture.finish()
                fixture.invoke(final, fixture.final_output(final), 'composition-final')
                attempt = fixture.records()['review_attempt']['composition-final']
                self.assert_stored_final(fixture.role, fixture.logical,
                                         json.loads(fixture.case.artifacts.read(attempt['output'])))
            finally:
                fixture.doCleanups()




class FinalPageBoundaryTests(unittest.TestCase):
    def test_page_serialized_fields_limit_accepts_the_boundary_and_refuses_one_more(self):
        role, packet, output = build_final_rule_cases()[6]
        rule = composition.find_rule(role, packet)
        targets = composition.list_targets(rule, packet)
        pages, closing = composition.split_output(rule, packet, output, [[target] for target in targets])
        page = pages[0]
        row = page['output']['targets'][0]
        path = next(iter(row['fields']))
        row['fields'][path] = ''
        allowance = composition.PAGE_FIELDS_CHARACTERS - composition._measure_final_value(dict(fields=row['fields']))
        row['fields'][path] = 'x' * allowance
        row['sentence'] = 's' * composition.SENTENCE_CHARACTERS
        composition.validate_page_output(rule, packet, [targets[0]], page['output'])
        row['fields'][path] += 'x'
        with self.assertRaises(ResearchError) as caught:
            composition.validate_page_output(rule, packet, [targets[0]], page['output'])
        self.assertEqual(caught.exception.code, 'review_composition_conflict')
        self.assertEqual(caught.exception.contract_check, 'review.shape')

    def test_closing_limits_bindings_and_objection_checks_are_enforced(self):
        role, packet, output = build_final_rule_cases()[6]
        rule = composition.find_rule(role, packet)
        targets = composition.list_targets(rule, packet)
        pages, closing = composition.split_output(rule, packet, output, [targets])
        bad = copy.deepcopy(closing)
        bad['fields']['/unresolved_objections'] = [dict(blocking=True, description='x' * 16000)]
        with self.assertRaises(ResearchError) as caught:
            composition.validate_closing_output(rule, packet, [], bad)
        self.assertEqual(caught.exception.code, 'review_composition_conflict')
        for binding in ('/unexpected', closing['bindings'][0], {}):
            bad = copy.deepcopy(closing)
            bad['bindings'].append(binding)
            with self.subTest(binding=binding), self.assertRaises(ResearchError):
                composition.validate_closing_output(rule, packet, [], bad)
        role, packet, output = build_final_rule_cases()[2]
        rule = composition.find_rule(role, packet)
        pages, closing = composition.split_output(rule, packet, output, [composition.list_targets(rule, packet)])
        closing['fields']['/objections'] = [dict(id='new-objection', claim='A claim.', reason='',
            evidence=output['value']['evidence'], resolution_condition='Reassess it.')]
        with self.assertRaises(ResearchError) as caught:
            composition.validate_closing_output(rule, packet, [], closing)
        self.assertEqual(caught.exception.contract_check, 'review.objections')

    def test_slate_closing_objections_keep_candidate_membership_checks(self):
        role, packet, output = build_final_rule_cases()[1]
        rule = composition.find_rule(role, packet)
        pages, closing = composition.split_output(rule, packet, output, [composition.list_targets(rule, packet)])
        objection = dict(id='new-objection', claim='The potential is uncertain.', reason='The supplied premise lacks evidence.',
            evidence=output['value']['evidence'], resolution_condition='Supply the missing evidence.', candidate_ids=['candidate-a'])
        closing['fields']['/objections'] = [objection]
        composition.validate_closing_output(rule, packet, [], closing)
        objection['candidate_ids'] = ['unknown']
        with self.assertRaises(ResearchError) as caught:
            composition.validate_closing_output(rule, packet, [], closing)
        self.assertEqual(caught.exception.contract_check, 'review.objections')

    def test_finding_ground_list_shape_is_refused_before_composition(self):
        role, packet, output = build_final_rule_cases()[9]
        rule = composition.find_rule(role, packet)
        targets = composition.list_targets(rule, packet)
        pages, closing = composition.split_output(rule, packet, output, [targets])
        pages[0]['output']['targets'][0]['grounds'] = None
        with self.assertRaises(ResearchError) as caught:
            composition.validate_page_output(rule, packet, targets, pages[0]['output'])
        self.assertEqual(caught.exception.contract_check, 'openness.finding')
        self.assertEqual(caught.exception.details['rule'], 'grounds_list')




class MissingPageFieldTests(unittest.TestCase):
    def test_required_target_fields_cannot_be_filled_by_neutral_validation_siblings(self):
        selected = {composition.FINAL_RULE_PREFIX + name for name in
                    ('result', 'slate', 'native-proposal', 'native-result', 'native-recovery',
                     'study-verification', 'study-readiness')}
        seen = set()
        for role, packet, output in build_final_rule_cases():
            rule = composition.find_rule(role, packet)
            if rule['id'] not in selected:
                continue
            targets = composition.list_targets(rule, packet)
            pages, closing = composition.split_output(rule, packet, output, [[target] for target in targets])
            for target, page in zip(targets, pages):
                fields = page['output']['targets'][0]['fields']
                if not fields or all(isinstance(value, (list, dict)) and not value for value in fields.values()):
                    continue
                bad = copy.deepcopy(page)
                bad['output']['targets'][0]['fields'] = {}
                with self.subTest(rule=rule['id'], target=target), self.assertRaises(ResearchError):
                    composition.validate_page_output(rule, packet, [target], bad['output'])
                seen.add(rule['id'])
        self.assertEqual(seen, selected)




class CoverageCompatibilityTests(unittest.TestCase):
    def test_legacy_problem_native_result_keeps_structured_coverage_and_minimum(self):
        from research_harness.native_review import validate_output
        role, packet, output = copy.deepcopy(build_final_rule_cases()[10])
        packet['native']['contract']['orchestration'] = 'problem_creation'
        output['findings']['openness']['coverage'] = dict(statement='The exact prior search.', neighbours=['One', 'Two'])
        validate_output(packet['native'], output)
        rule = composition.find_rule(role, packet)
        self.assertTrue(rule['id'].endswith('/native-result'))
        self.assertEqual(composition.describe_owners(rule)['/findings/openness/minimum/**'], 'harness')
        self.assertEqual(composition.describe_owners(rule)['/findings/openness/statement_digest'], 'harness')
        targets = composition.list_targets(rule, packet)
        pages, closing = composition.split_output(rule, packet, output, [[target] for target in targets])
        for target, page in zip(targets, pages):
            composition.validate_page_output(rule, packet, [target], page['output'])
        composition.validate_closing_output(rule, packet, [], closing)
        self.assertEqual(digest(composition.compose_output(rule, packet, pages, closing)), digest(output))
        self.assertEqual(output['findings']['openness']['minimum'], packet['native']['scientific_context']['openness_minimum'])

    def test_final_rule_module_has_a_reviewed_source_certificate_and_pointer_declaration(self):
        import ast
        import json
        from pathlib import Path
        from ci.capacity_packet_inventory import compute_ast_digest
        root = Path(__file__).resolve().parents[1]
        registry = json.loads((root / 'tests/fixtures/capacity_packet_schema.json').read_text())
        certificate = registry['certificates']['research_harness.review_composition']
        self.assertIn('find_rule', certificate['functions'])
        self.assertIn('compose_output', certificate['functions'])
        self.assertIn('/findings/openness', registry['non_packet_names'])
        self.assertEqual(certificate['ast_sha256'], compute_ast_digest(ast.parse(
            (root / 'research_harness/review_composition.py').read_text())))




class AdjudicatorClosingTests(unittest.TestCase):
    def test_overall_disposition_and_correction_admissibility_belong_to_closing(self):
        role, packet, output = build_final_rule_cases()[5]
        rule = composition.find_rule(role, packet)
        owners = composition.describe_owners(rule)
        self.assertEqual(owners['/disposition'], 'closing')
        self.assertEqual(owners['/correction_admissible'], 'closing')
        targets = composition.list_targets(rule, packet)
        pages, closing = composition.split_output(rule, packet, output, [targets])
        for path in ('/disposition', '/correction_admissible'):
            bad = copy.deepcopy(closing)
            bad['fields'][path] = 'invalid'
            with self.subTest(path=path), self.assertRaises(ResearchError) as caught:
                composition.validate_closing_output(rule, packet, [], bad)
            self.assertEqual(caught.exception.contract_check, 'adjudication.shape')
        for restriction in (dict(subject=dict(kind='study')), dict(rubric_definition=dict(item='A1', definition='Check the grade.'))):
            restricted = dict(packet, **restriction)
            bad = copy.deepcopy(closing)
            bad['fields']['/disposition'] = 'not_binding'
            with self.subTest(restriction=restriction), self.assertRaises(ResearchError) as caught:
                composition.validate_closing_output(rule, restricted, [], bad)
            self.assertEqual(caught.exception.contract_check, 'adjudication.shape')
        self.assertEqual(composition.compose_output(rule, packet, pages, closing), output)




def build_math_page_case():
    role, packet, stored = copy.deepcopy(build_final_rule_cases()[10])
    minimum = packet['native']['scientific_context']['openness_minimum']
    minimum['neighbours'].append('Neighbour three')
    packet.pop('reading_final', None)
    rule = composition.find_rule(role, packet)
    targets = composition.list_targets(rule, packet)
    rows = [dict(formulation='t' + str(index), coverage='Searched formulation ' + str(index) + '.',
                 sentence='The delivered evidence was assessed.') for index in range(4)]
    pages = [dict(targets=targets[:2], output=dict(targets=rows[:2])),
             dict(targets=targets[2:], output=dict(targets=rows[2:]))]
    closing = dict(decision='approve', findings=copy.deepcopy(stored['findings']))
    for key in ('statement_digest', 'minimum', 'coverage'):
        closing['findings']['openness'].pop(key)
    return rule, packet, pages, closing


class RevisedCompositionInterfaceTests(unittest.TestCase):
    def test_finding11_uses_recorded_page_outputs_and_mapping_rule(self):
        from research_harness import native_review, review_handles
        role, packet, original = copy.deepcopy(build_final_rule_cases()[9])
        rule = composition.find_rule(role, packet)
        self.assertIsInstance(rule, dict)
        self.assertTrue(rule['id'].startswith(composition.FINAL_RULE_PREFIX))
        targets = composition.list_targets(rule, packet)
        pages, closing = composition.split_output(rule, packet, original, [targets[:1], targets[1:]])
        reference = dict(path='research/sources/objects/' + 'd' * 64, sha256='d' * 64, size=1, media_type='text/plain')
        packet['handle_table'] = dict(m1=dict(reference=reference))
        raw = copy.deepcopy(pages[0]['output'])
        raw['targets'][0]['grounds'] = [dict(kind='own_argument', text='An argument is recorded.', refs=[dict(ref='m1')])]
        pages[0]['output'] = review_handles.expand_handles(packet, raw)
        closing['outcome'] = 'unresolved'
        before = digest(dict(packet=packet, pages=pages, closing=closing))
        composed = composition.compose_output(rule, packet, pages, closing)
        native_review.validate_finding(packet, composed)
        self.assertEqual(composed['findings']['openness']['grounds'][0]['refs'], [reference])
        self.assertEqual(before, digest(dict(packet=packet, pages=pages, closing=closing)))
        self.assertTrue(all(set(page) == {'targets', 'output'} for page in pages))


class RevisedMathCompositionTests(unittest.TestCase):
    def test_finding1_two_pages_compose_labeled_approval_accepted_by_native_validator(self):
        from research_harness import native_review
        rule, packet, pages, closing = build_math_page_case()
        for page in pages:
            composition.validate_page_output(rule, packet, page['targets'], page['output'])
        rows = composition.build_decision_rows(rule, packet, pages)
        composition.validate_closing_output(rule, packet, rows, closing)
        output = composition.compose_output(rule, packet, pages, closing)
        native_review.validate_output(packet['native'], output)
        minimum = packet['native']['scientific_context']['openness_minimum']
        labels = [minimum['statement']] + minimum['neighbours']
        expected = '\n'.join(label + ': Searched formulation ' + str(index) + '.' for index, label in enumerate(labels))
        self.assertEqual(output['findings']['openness']['coverage'], expected)
        self.assertEqual(output['findings']['openness']['minimum'], minimum)
        self.assertEqual(output['findings']['openness']['statement_digest'], packet['native']['claim_digest'])
        self.assertEqual(composition.describe_owners(rule)['/schema_version'], 'harness')
        composition.validate_output_owners(rule, output)
        self.assertNotIn('sentence', str(output))

    def test_finding1_revise_and_reject_use_closing_alone(self):
        from research_harness import native_review
        rule, packet, pages, closing = build_math_page_case()
        for decision in ('revise', 'reject'):
            closing = dict(decision=decision, reason='The proposed result needs further work.', objections=[])
            composition.validate_closing_output(rule, packet, composition.build_decision_rows(rule, packet, pages), closing)
            output = composition.compose_output(rule, packet, pages, closing)
            self.assertEqual(output, closing)
            self.assertNotIn('findings', output)
            native_review.validate_output(packet['native'], output)
        self.assertEqual(len(composition.build_decision_rows(rule, packet, pages)), 4)

    def test_finding1_only_composed_coverage_has_an_inverse(self):
        rule, packet, pages, closing = build_math_page_case()
        output = composition.compose_output(rule, packet, pages, closing)
        targets = composition.list_targets(rule, packet)
        for partition in ([targets], [[target] for target in targets], [targets[:1], targets[1:3], targets[3:]]):
            split, finish = composition.split_output(rule, packet, output, partition)
            self.assertEqual(digest(composition.compose_output(rule, packet, split, finish)), digest(output))
        changed = copy.deepcopy(output)
        changed['findings']['openness']['coverage'] = 'An unstructured stored v1 assessment.'
        with self.assertRaises(ResearchError):
            composition.split_output(rule, packet, changed, [targets])

    def test_finding1_page_coverage_is_bounded_nonempty_and_single_line(self):
        rule, packet, pages, closing = build_math_page_case()
        for invalid in ('', 'x' * 801, 'First\nsecond', 'First\rsecond', 'First\u2028second', []):
            page = copy.deepcopy(pages[0])
            page['output']['targets'][0]['coverage'] = invalid
            with self.subTest(value=invalid), self.assertRaises(ResearchError) as caught:
                composition.validate_page_output(rule, packet, page['targets'], page['output'])
            self.assertEqual(caught.exception.contract_check, 'native.output')
        pages[0]['output']['targets'][0]['coverage'] = 'x' * 800
        composition.validate_page_output(rule, packet, pages[0]['targets'], pages[0]['output'])

    def test_finding1_malformed_closing_fields_use_the_native_output_check(self):
        rule, packet, pages, closing = build_math_page_case()
        missing_openness = copy.deepcopy(closing)
        missing_openness['findings'].pop('openness')
        invalid_openness = copy.deepcopy(closing)
        invalid_openness['findings']['openness'] = None
        for output in (None, dict(decision='approve'), dict(decision='approve', findings=[]),
                       missing_openness, invalid_openness):
            with self.subTest(output=output), self.assertRaises(ResearchError) as caught:
                composition.validate_closing_output(rule, packet, [], output)
            self.assertEqual(caught.exception.contract_check, 'native.output')

    def test_finding1_stored_v1_math_output_validates_without_rewriting_or_splitting(self):
        import json
        import tarfile
        from pathlib import Path
        from research_harness import native_review
        root = Path(__file__).parent / 'fixtures/openness_0870_choice'
        stores = json.loads((root / 'manifest.json').read_text())['stores']
        for name in ('math-stopped-chosen-observed', 'math-stopped-chosen-uncalled'):
            stored = stores[name]
            # This is the tag's saved authored final response; the stopped
            # store has no historical final observation to fabricate.
            output = stored['review']
            with tarfile.open(root / (name + '.tar.xz')) as archive:
                packet_bytes = archive.extractfile(stored['native']['path']).read()
            packet = json.loads(packet_bytes)
            before = digest(dict(packet=packet, output=output))
            native_review.validate_output(packet, output)
            rule = composition.find_rule('native_math', packet)
            self.assertEqual(rule['id'], composition.FINAL_RULE_PREFIX + 'math-openness')
            self.assertIn('page_schema', rule)
            composition.describe_owners(rule)
            self.assertEqual(before, digest(dict(packet=packet, output=output)))
            self.assertEqual(digest(json.loads(packet_bytes)), digest(packet))

    def test_finding1_labels_with_colons_unicode_and_line_breaks_round_trip(self):
        rule, packet, pages, closing = build_math_page_case()
        packet['native']['scientific_context']['openness_minimum']['statement'] = 'A λ: claim\nwith two lines'
        packet['native']['scientific_context']['openness_minimum']['neighbours'][0] = 'A "quoted" \\lambda: formulation'
        targets = composition.list_targets(rule, packet)
        for page, assigned in zip(pages, (targets[:2], targets[2:])):
            page['targets'] = assigned
        output = composition.compose_output(rule, packet, pages, closing)
        lines = output['findings']['openness']['coverage'].split('\n')
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[0].startswith('A λ: claim\\u000awith two lines: '))
        self.assertTrue(lines[1].startswith('A "quoted" \\lambda: formulation: '))
        split, finish = composition.split_output(rule, packet, output, [targets])
        self.assertEqual(composition.compose_output(rule, packet, split, finish), output)


class CompleteSchemaOwnershipTests(unittest.TestCase):
    def read_sources(self, rule):
        import importlib
        import inspect
        sources = {}
        for contract in rule['validator_contracts']:
            module, name = contract['validator'].split('.')
            sources[contract['validator']] = inspect.getsource(getattr(importlib.import_module('research_harness.' + module), name))
        return sources

    def test_finding9_every_validator_declaration_has_an_owner_without_fixture_witnesses(self):
        for role, packet, output in build_final_rule_cases():
            rule = composition.find_rule(role, packet)
            with self.subTest(rule=rule['id']):
                composition.validate_validator_ownership(rule, self.read_sources(rule))
                self.assertEqual(rule['page_schema']['id'], rule['id'] + '/page')
                self.assertEqual(rule['closing_schema']['id'], rule['id'] + '/closing')
                self.assertTrue(rule['output_variants'])

    def test_finding9_added_optional_validator_field_absent_from_fixtures_fails_ownership(self):
        import ast
        optional = 'optional_review_note'
        for role, packet, output in build_final_rule_cases():
            rule = composition.find_rule(role, packet)
            sources = self.read_sources(rule)
            contract = rule['validator_contracts'][0]
            tree = ast.parse(sources[contract['validator']])
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id in ('fields', '_fields', '_closed')
                        and composition._read_validator_subject(node.args[0]) == contract['subject']):
                    extra = ast.Tuple(elts=[ast.Constant(value=optional)], ctx=ast.Load())
                    if node.func.id == '_closed':
                        extra = ast.IfExp(test=ast.Compare(left=ast.Constant(value=optional), ops=[ast.In()],
                            comparators=[ast.Name(id='output', ctx=ast.Load())]), body=extra,
                            orelse=ast.Tuple(elts=[], ctx=ast.Load()))
                        node.args[1] = ast.BinOp(left=node.args[1], op=ast.Add(), right=extra)
                    elif len(node.args) > 2:
                        node.args[2] = ast.BinOp(left=node.args[2], op=ast.Add(), right=extra)
                    else:
                        node.args.append(extra)
                if isinstance(node, ast.Compare):
                    candidate = node.left
                    if (isinstance(candidate, ast.Call) and isinstance(candidate.func, ast.Name)
                            and candidate.func.id == 'set' and candidate.args
                            and composition._read_validator_subject(candidate.args[0]) == contract['subject']):
                        node.left = ast.BinOp(left=candidate, op=ast.Sub(), right=ast.Set(elts=[ast.Constant(value=optional)]))
            sources[contract['validator']] = ast.unparse(ast.fix_missing_locations(tree))
            self.assertNotIn(optional, str(output))
            if contract['validator'].startswith('native_review.'):
                from research_harness import native_review
                namespace = dict(vars(native_review))
                exec(compile(sources[contract['validator']], '<optional-validator-field>', 'exec'), namespace)
                copied = namespace[contract['validator'].split('.')[1]]
                copied(packet if contract['validator'].endswith('validate_finding') else packet['native'],
                       dict(output, **{optional: 'The copied validator accepts this optional field.'}))
            with self.subTest(rule=rule['id']), self.assertRaises(ResearchError):
                composition.validate_validator_ownership(rule, sources)

    def test_finding6_only_roles_without_a_chain_are_exempt(self):
        self.assertEqual(set(composition.ROLES_WITHOUT_RULES), {'manuscript', 'standalone'})
        for metadata in composition.ROLES_WITHOUT_RULES.values():
            self.assertEqual(metadata['unit'], 'paper-series')
            self.assertEqual(metadata['release'], '0.94.0')
            self.assertIn('No parts adapter or chain family', metadata['reason'])
        for role, packet, output in build_final_rule_cases():
            self.assertIsNotNone(composition.find_rule(role, packet))
        self.assertIsNotNone(composition.find_rule('verification', dict(role='verification')))




def build_serialized_sentence(cost):
    for controls in range(min(300, cost // 6), -1, -1):
        rest = cost - controls * 6
        quotes, plain = divmod(rest, 2)
        if controls + quotes + plain <= 300:
            return '\x00' * controls + '"' * quotes + 'x' * plain
    raise AssertionError('The requested serialized sentence cost is not representable')


class CompleteSchemaBoundaryTests(unittest.TestCase):
    def test_finding9_page_maxima_and_exact_eighty_thousand_boundary(self):
        rule, packet, pages, closing = build_math_page_case()
        minimum = packet['native']['scientific_context']['openness_minimum']
        minimum['neighbours'] = ['Formulation ' + str(index) for index in range(1, 43)]
        targets = composition.list_targets(rule, packet)
        entries = []
        for index in range(43):
            row = dict(formulation='t' + str(index), coverage='c' * 800, sentence='')
            row['sentence'] = build_serialized_sentence(composition.PAGE_ROW_CHARACTERS - composition._measure_final_value(row))
            self.assertEqual(composition._measure_final_value(row), composition.PAGE_ROW_CHARACTERS)
            entries.append(row)
        page = dict(targets=entries)
        excess = composition._measure_final_value(page) - 80000
        self.assertGreater(excess, 0)
        for row in reversed(entries):
            cost = composition._measure_final_value(row['sentence']) - 2
            reduction = min(excess, cost - 1)
            row['sentence'] = build_serialized_sentence(cost - reduction)
            excess -= reduction
            if not excess:
                break
        self.assertEqual(composition._measure_final_value(page), 80000)
        composition.validate_page_output(rule, packet, targets, page)
        entries[-1]['sentence'] += 'x'
        self.assertEqual(composition._measure_final_value(page), 80001)
        with self.assertRaises(ResearchError) as caught:
            composition.validate_page_output(rule, packet, targets, page)
        self.assertEqual(caught.exception.contract_check, 'review.shape')

    def test_finding9_closing_maximum_is_sixteen_thousand_serialized_characters(self):
        rule, packet, pages, closing = build_math_page_case()
        for name in ('statement', 'assumptions', 'scope', 'dependencies', 'policy'):
            closing['findings'][name] = 'f' * 2000
        openness = closing['findings']['openness']
        openness['reason'] = 'r' * 4000
        openness['evidence'] = [format(index, '064x') for index in range(32)]
        openness['reason'] = openness['reason'][:4000 - (composition._measure_final_value(closing) - 16000)]
        self.assertEqual(composition._measure_final_value(closing), 16000)
        composition.validate_closing_output(rule, packet, [], closing)
        openness['reason'] += 'r'
        with self.assertRaises(ResearchError):
            composition.validate_closing_output(rule, packet, [], closing)

    def test_finding9_every_schema_descendant_has_a_finite_serialized_bound(self):
        for role, packet, output in build_final_rule_cases():
            rule = composition.find_rule(role, packet)
            pending = [rule['page_schema'], rule['closing_schema']] + [field['schema'] for field in rule['fields']]
            while pending:
                schema = pending.pop()
                self.assertIs(type(schema['max_serialized_characters']), int)
                self.assertGreater(schema['max_serialized_characters'], 0)
                kind = schema['type']
                if kind == 'object':
                    self.assertFalse(schema['additional_properties'])
                    pending.extend(schema['properties'].values())
                elif kind == 'array':
                    self.assertIs(type(schema['max_items']), int)
                    pending.append(schema['items'])
                elif kind == 'json':
                    self.assertIs(type(schema['max_properties']), int)
                    self.assertIs(type(schema['max_items']), int)
                    self.assertIs(type(schema['max_depth']), int)
                elif kind in ('native_closing', 'native_math_closing'):
                    pending.extend((schema['approval'], schema['assessment']))

    def test_finding9_ground_counts_references_and_closing_text_have_bounds(self):
        role, packet, original = build_final_rule_cases()[9]
        rule = composition.find_rule(role, packet)
        targets = composition.list_targets(rule, packet)
        pages, closing = composition.split_output(rule, packet, original, [targets])
        closing['outcome'] = 'unresolved'
        for grounds in ([dict(kind='reviewer_gap', text='A gap.')] * 9,
                       [dict(kind='own_argument', text='See the argument.', refs=[dict(path='research/sources/objects/' + 'f' * 64,
                            sha256='f' * 64, size=1, media_type='text/plain')] * 9)]):
            closing['grounds'] = grounds
            with self.subTest(grounds=grounds), self.assertRaises(ResearchError):
                composition.validate_closing_output(rule, packet, [], closing)
        closing['grounds'] = [dict(kind='packet_defect', text='The packet has a defect.')]
        closing['statement'] = 's' * 2001
        with self.assertRaises(ResearchError):
            composition.validate_closing_output(rule, packet, [], closing)


class LegalOutputVariantTests(unittest.TestCase):
    def assert_round_trip(self, role, packet, output):
        rule = composition.find_rule(role, packet)
        composition.validate_output_owners(rule, output)
        self.assertEqual(rule['page_schema']['id'], rule['id'] + '/page')
        self.assertEqual(rule['closing_schema']['id'], rule['id'] + '/closing')
        targets = composition.list_targets(rule, packet)
        for partition in ([targets], [[target] for target in targets],
                          [targets[index:index + 2] for index in range(0, len(targets), 2)]):
            pages, closing = composition.split_output(rule, packet, output, partition)
            self.assertEqual(composition.compose_output(rule, packet, pages, closing), output)
            for page in pages:
                composition._validate_answer_schema(rule, rule['page_schema'], page['output'])
            composition.validate_closing_output(rule, packet, [], closing)

    def test_finding9_native_alternative_branches_and_unrestricted_assessment_members(self):
        from research_harness import native_review
        for role, packet, original in build_final_rule_cases()[6:9]:
            kind = packet['native']['kind']
            if kind == 'proposal':
                for decision in ('approve', 'revise', 'reject'):
                    output = copy.deepcopy(original)
                    output.update(decision=decision, unresolved_objections=[dict(blocking=True, description='A retained objection.')])
                    output['findings']['renewal_basis'] = ''
                    native_review.validate_output(packet['native'], output)
                    self.assert_round_trip(role, packet, output)
            else:
                for decision in ('revise', 'reject'):
                    for extras in ({}, dict(objections=[], optional_assessment=dict(**{'10': [True, None, 1]}),
                                            findings=['An operational assessment.'], reviewer='An extra value retained verbatim.')):
                        output = dict(decision=decision, reason='The proposed transition cannot be approved.', **extras)
                        native_review.validate_output(packet['native'], output)
                        self.assert_round_trip(role, packet, output)
        role, packet, original = build_final_rule_cases()[7]
        for version in (None, 1, 2):
            output = copy.deepcopy(original)
            if version is not None:
                output['schema_version'] = version
            if version == 2:
                output['findings']['openness'] = dict(outcome='unresolved', statement_digest=packet['native']['claim_digest'],
                    coverage='The exact statement was searched.', reason='The question remains unresolved.',
                    searched_at='2026-11-14', evidence=['d' * 64])
            native_review.validate_output(packet['native'], output)
            self.assert_round_trip(role, packet, output)

    def test_finding9_value_optional_fields_previous_rubrics_and_potential_readiness(self):
        from research_harness import bar_sources, problem_rubric, research_decisions
        for role, packet, original in build_final_rule_cases()[:3]:
            for option in ('source_requests', 'settled_requests', 'excerpts'):
                output = copy.deepcopy(original)
                output[option] = []
                if option == 'settled_requests':
                    packet = dict(packet, source_rules=bar_sources.PROTOCOL_V3)
                    bar_sources.validate_settled_requests(packet, output)
                self.assert_round_trip(role, packet, output)
        role, packet, output = copy.deepcopy(build_final_rule_cases()[2])
        output['value']['rubric']['admission'].pop('A7')
        output['value']['rubric_evidence'].pop('A7')
        problem_rubric.validate_grades(output['value']['rubric'], allow_previous=True)
        self.assert_round_trip(role, packet, output)
        role, packet, output = copy.deepcopy(build_final_rule_cases()[1])
        for value in output['value']['rubric_potential'].values():
            value['readiness'] = 'pass'
            problem_rubric.validate_grades(value, potential=True)
        self.assert_round_trip(role, packet, output)

    def test_finding9_verification_optional_fields_and_readiness_assessor_option(self):
        from research_harness.verification import validate_verdict_body
        role, packet, output = copy.deepcopy(build_final_rule_cases()[11])
        output['verdict'].update(rationaleSections=[], wouldChange='A decisive contrary source.', suggestions=[],
                                 findings=[], supersedesVerdictId='previous-verdict')
        validate_verdict_body(output['verdict'])
        self.assert_round_trip(role, packet, output)
        role, packet, output = copy.deepcopy(build_final_rule_cases()[12])
        packet['assessor']['author_memory_notes_present'] = False
        output['support']['assessor']['author_memory_notes_present'] = False
        self.assert_round_trip(role, packet, output)
class RemainingLegalVariantTests(unittest.TestCase):
    assert_round_trip = LegalOutputVariantTests.assert_round_trip

    def test_finding9_structured_native_coverage_and_optional_minimum(self):
        from research_harness import native_review
        role, packet, original = copy.deepcopy(build_final_rule_cases()[10])
        packet['native']['contract']['orchestration'] = 'problem_creation'
        for coverage in ('The exact formulation was searched.', ['The supplied coverage.'], dict(statement='A dated search.')):
            for supplied in (False, True):
                output = copy.deepcopy(original)
                output['findings']['openness']['coverage'] = coverage
                if not supplied:
                    output['findings']['openness'].pop('minimum')
                native_review.validate_output(packet['native'], output)
                self.assert_round_trip(role, packet, output)

    def test_finding9_all_adjudicator_scalar_branches(self):
        role, packet, original = copy.deepcopy(build_final_rule_cases()[5])
        for disposition in ('upheld', 'not_upheld', 'unresolved', 'not_binding'):
            for correction in (None, True, False):
                output = dict(original, disposition=disposition, correction_admissible=correction)
                self.assert_round_trip(role, packet, output)

    def test_finding9_finding_outcomes_legacy_grounds_and_coverage_forms(self):
        from research_harness import native_review
        role, packet, original = copy.deepcopy(build_final_rule_cases()[9])
        for outcome in ('supported_open', 'already_solved', 'unresolved'):
            current = copy.deepcopy(packet)
            current['native']['scientific_context']['audit_forecast']['outcomes'] = {
                name: 'possible' if name == outcome else 'blocked' for name in ('supported_open', 'already_solved')}
            output = copy.deepcopy(original)
            output['findings']['openness']['outcome'] = outcome
            native_review.validate_finding(current, output)
            self.assert_round_trip(role, current, output)
        packet.pop('reading_final')
        original['findings']['openness'].update(outcome='unresolved', coverage='The earlier collective coverage.',
            grounds=[dict(kind='own_argument', text='An earlier argument is retained.', turns=['reading-1'])])
        native_review.validate_finding(packet, original)
        self.assert_round_trip(role, packet, original)


if __name__ == '__main__':
    unittest.main()
