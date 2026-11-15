"""A refused turn receives one immutable, authenticated replacement input."""

import copy
import json
import unittest
from unittest.mock import patch

from research_harness import cli, research_decisions as decisions, review_protocol as protocol
from research_harness import review_refusal as refusal, review_refusal_notice as notices
from research_harness.errors import ResearchError
from research_harness.evidence import digest
import test_research_problem_reviews as problem_fixtures


class RefusalReplacementTests(unittest.TestCase):
    def setUp(self):
        self.case = problem_fixtures.ProblemReviewTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.supporting = self.case.artifacts.put(b'A synthetic field standard.', 'text/plain')
        self.case.prepare_review_route()

    def invoke(self, assignment, output, identifier=None):
        with patch.object(protocol.transport, 'send_request', return_value=self.case.build_provider_response(output)):
            return self.case.mutate(protocol.invoke_assignment,
                dict(id=identifier or 'attempt-' + assignment['id'], assignment_id=assignment['id']), credential='fixture')

    def record(self, assignment, output):
        return self.case.mutate(decisions.record_value_review,
            dict(output, id='review-' + assignment['id'], dossier_id=None, assignment_id=assignment['id']))

    def build_refused(self):
        context = self.case.deliver_supporting('first')
        assignment = self.case.assign('bar', 'first', identifier='initial', **context)
        output = self.case.build_review_output('bar')
        output['value']['status'] = 'invalid'
        self.invoke(assignment, output)
        with self.assertRaises(ResearchError):
            self.record(assignment, output)
        return assignment, output

    def replace(self, assignment, identifier='replacement', **changes):
        payload = {key: copy.deepcopy(assignment[key]) for key in
                   ('route_id', 'dossier_id', 'role', 'reviewer_id', 'author_id', 'context')}
        payload.update(id=identifier)
        payload['context']['output_refusal'] = {'assignment_id': assignment['id']}
        payload.update(changes)
        return self.case.mutate(protocol.record_assignment, payload)['result']

    def read_notice(self, assignment):
        return protocol._read_json(self.case.artifacts, assignment['packet'])['output_refusal']

    def test_replacement_freezes_one_fresh_diagnosis_and_records_corrected_answer(self):
        first, output = self.build_refused()
        before = copy.deepcopy(self.case.read_records())
        original = refusal.classify_observed_output
        with patch.object(refusal, 'classify_observed_output', wraps=original) as check:
            replacement = self.replace(first)
        self.assertEqual([call.args[2] for call in check.call_args_list], ['attempt-initial'])
        notice = self.read_notice(replacement)
        self.assertEqual(notice['refused'][0]['status'], 'refused')
        self.assertEqual(notice['output'], output)
        self.assertEqual(replacement['slot_id'], first['slot_id'])
        self.assertEqual(replacement['prompt']['template_id'], first['prompt']['template_id'])
        self.assertEqual(protocol.read_history(self.case.artifacts, replacement), [])
        request = protocol.build_assignment_request(self.case.read_records(), self.case.artifacts, replacement)
        self.assertEqual(len(protocol.transport.logical_messages(request)), 1)
        corrected = self.case.build_review_output('bar')
        self.invoke(replacement, corrected)
        records = self.case.read_records()
        self.assertTrue(protocol.authenticate_attempt(records, self.case.artifacts, replacement,
                                                      records['review_attempt']['attempt-replacement']))
        self.record(replacement, corrected)
        self.assertEqual(self.case.read_records()['review_output_refusal'], before['review_output_refusal'])
        self.assertEqual(self.case.read_records()['review_attempt']['attempt-initial'],
                         before['review_attempt']['attempt-initial'])

    def test_second_replacement_lists_revived_attempt_and_keeps_head_finality(self):
        first, output = self.build_refused()
        second = self.replace(first)
        bad = copy.deepcopy(output)
        bad['value']['reason'] = 'A second answer still has the same shape defect.'
        self.invoke(second, bad)
        with self.assertRaises(ResearchError):
            self.record(second, bad)
        before = copy.deepcopy(self.case.read_records())
        original = refusal.classify_observed_output
        def classify(records, artifacts, attempt_id):
            return None if attempt_id == 'attempt-initial' else original(records, artifacts, attempt_id)
        with patch.object(refusal, 'classify_observed_output', side_effect=classify) as check:
            third = self.replace(second, 'replacement-2')
            self.assertEqual([call.args[2] for call in check.call_args_list], ['attempt-initial', 'attempt-replacement'])
            entries = self.read_notice(third)['refused']
            self.assertEqual(entries[0]['status'], 'not_refused')
            self.assertEqual(set(entries[0]), {'assignment_id', 'attempt_id', 'output_digest', 'refusal_digest', 'status'})
            self.assertEqual(entries[1]['status'], 'refused')
            state = protocol.assignment_state(self.case.read_records(), self.case.artifacts, first['id'])
            self.assertFalse(state['ready'])
            self.assertEqual(state['not_selected']['assignment_id'], second['id'])
            for action in (lambda: self.record(first, output),
                           lambda: self.invoke(first, output, 'late'),
                           lambda: self.case.assign('bar', 'first', prior_assignment_id=first['id'])):
                with self.assertRaises(ResearchError) as caught:
                    action()
                self.assertIn(caught.exception.code, ('review_assignment_replaced', 'review_assignment_superseded'))
            corrected = self.case.build_review_output('bar')
            self.invoke(third, corrected)
            self.record(third, corrected)
        self.assertEqual(before['review_output_refusal'], self.case.read_records()['review_output_refusal'])
        self.assertEqual(before['review_attempt']['attempt-initial'], self.case.read_records()['review_attempt']['attempt-initial'])

    def test_third_replacement_is_refused(self):
        first, output = self.build_refused()
        for number in (1, 2):
            first = self.replace(first, 'replacement-' + str(number))
            self.invoke(first, output)
            with self.assertRaises(ResearchError):
                self.record(first, output)
            with self.assertRaises(ResearchError) as repeated:
                self.invoke(first, output, 'repeat-' + str(number))
            self.assertEqual(repeated.exception.code, 'review_slot_exhausted')
        with self.assertRaises(ResearchError) as caught:
            self.replace(first, 'replacement-3')
        self.assertEqual(caught.exception.code, 'review_slot_exhausted')

    def test_notice_uses_changed_check_and_path_in_both_forms(self):
        first, output = self.build_refused()
        failure = dict(code='invalid_value_review', check='review.value',
                       details=dict(path='/value/reason', message='Correct this reason.', minimum_characters=100))
        with patch.object(refusal, 'classify_observed_output', return_value=failure):
            for form in ('whole', 'bounded'):
                notice = notices.build_refusal_notice(self.case.read_records(), self.case.artifacts,
                                                     ('attempt-initial',), form=form)
                entry = notice['refused'][0]
                self.assertEqual(entry['path'], '/value/reason')
                self.assertNotIn('/value/status', json.dumps(notice))
                if form == 'bounded':
                    self.assertEqual(notice['refused_part']['value'], output['value']['reason'])
                    self.assertEqual(notice['refused_part']['path'], '/value/reason')

    def test_origin_is_frozen_when_checks_change_and_rejects_each_forged_entry(self):
        from research_harness.review_refusal_replacement import validate_replacement_origin
        first, _ = self.build_refused()
        replacement = self.replace(first)
        records = self.case.read_records()
        packet = protocol._read_json(self.case.artifacts, replacement['packet'])
        with patch.object(refusal, 'classify_observed_output', side_effect=AssertionError('No origin recheck.')):
            validate_replacement_origin(records, self.case.artifacts, replacement)
            for key, value in (('refusal_digest', '0' * 64), ('output_digest', '0' * 64),
                               ('assignment_id', 'other'), ('attempt_id', 'absent'), ('status', 'unknown')):
                forged = copy.deepcopy(packet)
                forged['output_refusal']['refused'][0][key] = value
                with self.subTest(key=key), self.assertRaises(ResearchError) as caught:
                    validate_replacement_origin(records, self.case.artifacts, replacement, forged)
                self.assertEqual(caught.exception.code, 'review_refusal_replacement_origin_invalid')
            forged = copy.deepcopy(packet)
            forged['output_refusal']['refused'] = []
            with self.assertRaises(ResearchError):
                validate_replacement_origin(records, self.case.artifacts, replacement, forged)

    def test_mismatched_context_or_identity_and_usable_output_are_refused(self):
        first, _ = self.build_refused()
        for key, value in (('reviewer_id', 'another'), ('author_id', 'coauthor'), ('dossier_id', None)):
            changes = {key: value}
            if key == 'dossier_id':
                changes = {'context': {'output_refusal': {'assignment_id': first['id']}}}
            with self.subTest(key=key), self.assertRaises(ResearchError) as caught:
                self.replace(first, **changes)
            self.assertEqual(caught.exception.code, 'review_refusal_replacement_mismatch')
        with patch.object(refusal, 'classify_observed_output', return_value=None):
            with self.assertRaises(ResearchError) as caught:
                self.replace(first)
            self.assertEqual(caught.exception.code, 'review_refusal_replacement_ineligible')

    def test_revived_head_without_successor_records_its_original_observation(self):
        first, output = self.build_refused()
        self.assertEqual(first['prompt']['parameters']['common_version'], 4)
        before = copy.deepcopy(self.case.read_records())
        with patch.object(decisions, 'validate_review_contract', return_value=None):
            state = protocol.assignment_state(self.case.read_records(), self.case.artifacts, first['id'])
            self.assertTrue(state['ready'], state['obligations'])
            self.assertEqual(state['attempt_id'], 'attempt-initial')
            self.record(first, output)
        after = self.case.read_records()
        self.assertEqual(before['review_output_refusal'], after['review_output_refusal'])
        self.assertEqual(before['review_attempt'], after['review_attempt'])

    def test_new_reviewer_in_slot_keeps_a_revived_output_unselected(self):
        first, output = self.build_refused()
        self.invoke(first, output, 'second-refusal')
        with self.assertRaises(ResearchError):
            self.record(first, output)
        for number in (1, 2):
            first = self.replace(first, identifier='refusal-' + str(number))
            self.invoke(first, output, 'replacement-refusal-' + str(number))
            with self.assertRaises(ResearchError):
                self.record(first, output)
        successor = self.case.assign('bar', 'another', identifier='new-reviewer')
        self.assertEqual(successor['slot_id'], first['slot_id'])
        with patch.object(decisions, 'validate_review_contract', return_value=None):
            state = protocol.assignment_state(self.case.read_records(), self.case.artifacts, first['id'])
            self.assertEqual(state['not_selected']['assignment_id'], successor['id'])
            with self.assertRaises(ResearchError) as caught:
                self.record(first, output)
            self.assertEqual(caught.exception.code, 'review_assignment_superseded')


    def test_both_admitted_forms_freeze_the_changed_path_and_authenticate_without_rechecking(self):
        from research_harness.review_refusal_replacement import validate_replacement_origin
        for form in ('whole', 'bounded'):
            with self.subTest(form=form):
                # Separate reviewers keep the turn positions independent for these two admissions.
                if form == 'whole':
                    first, output = self.build_refused()
                else:
                    context = self.case.deliver_supporting('second')
                    first = self.case.assign('bar', 'second', identifier='large', **context)
                    output = self.case.build_review_output('bar')
                    output['value'].update(status='invalid', reason='x' * 100000)
                    self.invoke(first, output)
                    with self.assertRaises(ResearchError):
                        self.record(first, output)
                diagnosis = dict(code='invalid_value_review', check='review.value',
                    details=dict(path='/value/consequence', message='Correct the consequence.', minimum_characters=100))
                with patch.object(refusal, 'classify_observed_output', return_value=diagnosis) as check:
                    replacement = self.replace(first, 'replacement-' + form)
                self.assertEqual(check.call_count, 1)
                notice = self.read_notice(replacement)
                self.assertEqual(notice['form'], form)
                self.assertEqual(notice['refused'][-1]['path'], '/value/consequence')
                self.assertNotIn('/value/status', json.dumps(notice))
                if form == 'bounded':
                    self.assertEqual(notice['refused_part']['path'], '/value/consequence')
                    self.assertEqual(notice['refused_part']['value'], output['value']['consequence'])
                    self.assertLessEqual(len(notices._serialize_value(notice)), 45000)
                corrected = self.case.build_review_output('bar')
                self.invoke(replacement, corrected)
                records = self.case.read_records()
                with patch.object(refusal, 'classify_observed_output', side_effect=AssertionError('Keep frozen diagnosis.')):
                    validate_replacement_origin(records, self.case.artifacts, replacement)
                    protocol.build_assignment_request(records, self.case.artifacts, replacement)
                    self.assertTrue(protocol.authenticate_provenance(records, self.case.artifacts, replacement,
                        records['review_attempt']['attempt-' + replacement['id']]))
                self.record(replacement, corrected)

    def test_pending_or_unsupported_turn_cannot_be_replaced(self):
        from research_harness.review_refusal_replacement import prepare_replacement
        first, _ = self.build_refused()
        records = self.case.read_records()
        payload = {key: copy.deepcopy(first[key]) for key in
                   ('route_id', 'dossier_id', 'role', 'reviewer_id', 'author_id', 'subject', 'context')}
        payload['id'] = 'replacement'
        payload['context']['output_refusal'] = {'assignment_id': first['id']}
        for marker in ('staged_review', 'reading_chain'):
            altered = copy.deepcopy(records)
            altered['review_assignment'][first['id']][marker] = {'protocol': 'frozen-unsupported-chain'}
            with self.subTest(marker=marker), self.assertRaises(ResearchError) as caught:
                prepare_replacement(altered, self.case.artifacts, payload, self.case.store.revision + 1)
            self.assertEqual(caught.exception.code, 'review_refusal_replacement_ineligible')
        records['review_invocation']['pending'] = dict(id='pending', assignment_id=first['id'])
        with self.assertRaises(ResearchError) as caught:
            prepare_replacement(records, self.case.artifacts, payload, self.case.store.revision + 1)
        self.assertEqual(caught.exception.code, 'review_invocation_pending')


    def test_status_names_the_successor_of_an_unselected_output(self):
        from research_harness.cli import _review_status
        first, _ = self.build_refused()
        successor = self.replace(first)
        status = _review_status(self.case.read_records(), self.case.artifacts)
        self.assertEqual(status['assignments'][first['id']]['not_selected']['assignment_id'], successor['id'])


    def test_normalized_identity_keeps_the_original_packet_identity_and_records(self):
        first, _ = self.build_refused()
        replacement = self.replace(first, reviewer_id=' FIRST ', author_id=' AUTHOR ')
        self.assertEqual(replacement['reviewer_id'], first['reviewer_id'])
        self.assertEqual(replacement['author_id'], first['author_id'])
        corrected = self.case.build_review_output('bar')
        self.invoke(replacement, corrected)
        self.record(replacement, corrected)



class CitationReplacementTests(unittest.TestCase):
    def build_case(self, count):
        import test_record_reading_handoff as fixtures
        harness = fixtures.ReadingHandoffTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        assignment, output = harness.build_final(count=count, abstract=True)
        if count == 0:
            # build_final includes the abstract alone when no fulltext was requested.
            self.assertEqual(len(output['value']['evidence']), 1)
        with self.assertRaises(ResearchError) as caught:
            harness.case.mutate(decisions.record_value_review,
                dict(output, id='refused', dossier_id=None, assignment_id=assignment['id']))
        self.assertEqual(caught.exception.code, 'source_link_not_fulltext')
        replacement = harness.case.assign('bar', 'first', identifier='replacement',
            prior_assignment_id='initial', output_refusal={'assignment_id': assignment['id']})
        return harness, replacement, output

    def test_abstract_link_replacement_delivers_two_accepted_forms_and_records(self):
        harness, replacement, output = self.build_case(0)
        packet = protocol._read_json(harness.case.artifacts, replacement['packet'])
        notice = packet['output_refusal']
        self.assertEqual(notice['refused'][0]['check'], 'evidence.fulltext')
        self.assertEqual(notice['refused'][0]['details']['delivered_depth'], 'abstract')
        self.assertEqual(notice['refused'][0]['details']['notice'], refusal.SOURCE_LINK_ACCEPTED_FORMS_NOTICE)
        output['value']['evidence'][-1] = harness.abstract_link['artifact']
        seen = []
        def respond(route, request, credential):
            seen.append(request)
            return harness.case.build_provider_response(output)
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            harness.case.mutate(protocol.invoke_assignment, dict(id='corrected', assignment_id=replacement['id']),
                                credential='fixture')
        self.assertEqual(len(seen), 1)
        harness.case.mutate(decisions.record_value_review,
            dict(output, id='recorded', dossier_id=None, assignment_id=replacement['id']))
        task = harness.case.read_records()['review_attempt']['corrected']['task']
        self.assertEqual(task['notice_characters'], len(notices._serialize_value(notice)))
        self.assertGreaterEqual(task['material_characters'], task['notice_characters'])
        from ci.route_capacity_profile import describe_refusal_replacements
        sample = dict(route_class={}, task=task, usage={}, completed_generation=True)
        self.assertEqual(describe_refusal_replacements([sample, dict(sample, task={})]), [sample])

    def test_abstract_beside_fulltext_replacement_keeps_one_reading_handoff(self):
        from research_harness.review_continuation import plan_next
        harness, replacement, output = self.build_case(1)
        output['value']['evidence'][-1] = harness.abstract_link['artifact']
        harness.invoke(replacement, output, 'corrected')
        payload = dict(output, id='recorded', dossier_id=None, assignment_id=replacement['id'])
        with self.assertRaises(ResearchError) as caught:
            harness.case.mutate(decisions.record_value_review, payload)
        self.assertEqual(caught.exception.code, 'reading_missing')
        plan = plan_next(harness.case.read_records(), harness.case.artifacts, replacement['id'])
        self.assertEqual(plan['command'], 'read')
        self.assertEqual(len(plan['details']['readings']), 1)
        from research_harness.reading import record_reading
        harness.case.mutate(record_reading, harness.case.full_note(harness.bundles[0]))
        harness.case.mutate(decisions.record_value_review, payload)


class EvidenceGrammarVersionTests(unittest.TestCase):
    def test_new_grammar_has_one_sentence_and_old_envelopes_keep_every_digest(self):
        self.assertEqual(protocol.COMMON_VERSION, 4)
        for role in ('bar', 'slate', 'result', 'adjudicator'):
            with self.subTest(role=role):
                old = protocol.canonical_prompt(role, orchestration='problem_creation', common_version=3)
                new = protocol.canonical_prompt(role, orchestration='problem_creation')
                self.assertNotIn(protocol.EVIDENCE_DEPTH_SENTENCE, old['text'])
                self.assertIn(protocol.EVIDENCE_DEPTH_SENTENCE, new['text'])
                self.assertEqual(new['text'], old['text'].replace(protocol._EVIDENCE_GRAMMAR_END,
                    protocol._EVIDENCE_GRAMMAR_END + protocol.EVIDENCE_DEPTH_SENTENCE))
                assignment = dict(role=role, prompt=old, subject={'kind': 'problem'}, context={})
                self.assertTrue(protocol._prompt_matches(assignment))
                self.assertEqual(digest(old['text']), old['rendered_prompt_hash'])
