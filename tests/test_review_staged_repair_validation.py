"""Repair contracts are enforced before accepting or crediting staged finals."""

import copy
from unittest.mock import patch

import codex_route_fixtures as codex
from problem_parts_fixtures import ResultPartsCase
from research_harness import review_context_repair as repair
from research_harness import review_protocol as protocol, review_refusal
from research_harness import review_staged as staged
from research_harness import review_staged_protocol as producer
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from test_review_staged_fixtures import epoch, selection, task
import test_review_staged_protocol as protocol_fixtures


class StagedRepairValidationTests(ResultPartsCase):
    staged_entry = True

    def build_adjudicator_capture(self):
        frame = dict(statement='For primes p the conclusion holds.', grade=2,
            context_repair={'assignment_id': 'predecessor'}, historical_reviews=[])
        manifest = staged.build_manifest(frame,
            [dict(key='readings', index=0, item={'text': 'Evidence 0.'}, artifacts=[], source_ids=[])], [])
        value = epoch(role='adjudicator', route_id='route', manifest=manifest,
            focused_scope=dict(objection_id='O7', objection_version=digest('O7'),
                               review_pair_roots=[digest('first'), digest('second')]))
        terminal = dict(objection_id='O7', disposition='upheld', correction_admissible=None, visible_conditions=[])
        tasks, selected = selection(value, terminal=terminal)
        combined = selected['observations'][0]
        combined['output'].pop('terminal')
        combined['raw_output_digest'] = digest(combined['output'])
        assigned = task(value, 'final', dependencies=[tasks[-1]['id']])
        packet = producer.build_packet(value, assigned, observations=selected['observations'],
                                       tasks=tasks)
        records = self.case.read_records()
        template = records['review_assignment']['bar-first']
        prompt = protocol.canonical_prompt('adjudicator', orchestration='problem_creation',
                                            staged_task=assigned, repair=True)
        request = producer.build_request(self.case.artifacts, records['review_route']['route'], prompt, packet)
        assigned = staged.freeze_task(assigned, digest(request), staged.SERIALIZATION)
        tasks.append(assigned)
        child = dict(template, id='final-child', role='adjudicator', staged_task=assigned,
            reassessment_required=True, packet=protocol._put_json(self.case.artifacts, packet),
            staged_review=dict(kind='task', epoch_id=value['id'],
                controller_assignment_id=value['controller_assignment_id'], task_id=assigned['id']))
        controller = dict(template, id=value['controller_assignment_id'], role='adjudicator',
            packet=protocol._put_json(self.case.artifacts, frame),
            staged_review=dict(kind='controller', epoch_id=value['id']))
        records['review_assignment'].update({row['id']: row for row in (child, controller)})
        records['review_epoch'] = {value['id']: value}
        records['review_task'] = {row['id']: dict(task=row, epoch_id=value['id']) for row in tasks}
        records['review_task_acceptance'] = {}
        for index, observed in enumerate(selected['observations']):
            assigned_task = next(row for row in tasks if row['id'] == observed['task_id'])
            reader = dict(template, id='reader-' + str(index), staged_task=assigned_task,
                staged_review=dict(kind='task', epoch_id=value['id']))
            records['review_assignment'][reader['id']] = reader
            records['review_task_acceptance'][reader['id']] = dict(task_id=assigned_task['id'], receipt=dict(
                staged_observation=observed, attempt=dict(id=observed['id'], assignment_id=reader['id'],
                                                         recorded_revision=1)))
        raw = dict(terminal=terminal, decision_checks=combined['output']['decision_checks'],
            objection_id='O7', disposition='upheld',
            correction_admissible=None, reason='The original retains the prime-domain restriction.',
            evidence=[self.case.supporting], reassessment=dict(assignment_id='predecessor', findings=[]))
        capture = dict(reservation_id='capture', recorded_revision=2, worker={}, observation=dict(status='completed',
            request=protocol._put_json(self.case.artifacts, request),
            output=protocol._put_json(self.case.artifacts, raw)))
        return records, value, child, capture, raw

    def test_accept_capture_refuses_wrong_adjudicator_reassessment_before_completion(self):
        records, value, child, capture, raw = self.build_adjudicator_capture()
        with patch.object(producer, 'authenticate_observation', return_value=True), \
                patch.object(producer, 'matches_currency', return_value=True):
            valid = producer.accept_capture(records, self.case.artifacts, child, capture, 'valid-final')
            observations, _, _ = producer.collect_observations(records, self.case.artifacts, value)
            completed = staged.verify_completion(value,
                {'observations': observations + [valid['staged_observation']]},
                tasks=producer.list_epoch_tasks(records, value))
            self.assertTrue(completed['complete'], completed['issues'])
            raw['reassessment']['assignment_id'] = 'WRONG'
            capture['observation']['output'] = protocol._put_json(self.case.artifacts, raw)
            with self.assertRaises(ResearchError) as rejected:
                producer.accept_capture(records, self.case.artifacts, child, capture, 'invalid-final')
        self.assertEqual(rejected.exception.code, 'review_reassessment_mismatch')
        self.assertEqual(rejected.exception.contract_check, 'review.reassessment')
        self.assertEqual(len(records['review_task_acceptance']), 2)

    def test_refusal_classifies_invalid_adjudicator_reassessment_against_frozen_frame(self):
        records, _, child, capture, raw = self.build_adjudicator_capture()
        cases = [(None, 'review_reassessment_missing'),
                 (dict(assignment_id='WRONG', findings=[]), 'review_reassessment_mismatch'),
                 (dict(assignment_id='predecessor', findings=[dict(review_id='unknown', disposition='revised',
                    reason='Rechecked the original.', evidence=[self.case.supporting])]),
                  'review_reassessment_incomplete')]
        for reassessment, code in cases:
            with self.subTest(code=code):
                output = dict(raw)
                output.pop('reassessment')
                if reassessment is not None:
                    output['reassessment'] = reassessment
                attempt = producer.build_capture_attempt(child, capture, 'invalid-final')
                attempt['output'] = protocol._put_json(self.case.artifacts, output)
                records['review_observation'] = {capture['reservation_id']: dict(capture,
                    origin='instrumented', observation=dict(capture['observation'], output=attempt['output']))}
                records['review_attempt'][attempt['id']] = attempt
                with patch.object(protocol, 'authenticate_attempt', return_value=True):
                    failure = review_refusal.classify_observed_output(records, self.case.artifacts, attempt['id'])
                self.assertIsNotNone(failure)
                self.assertEqual(failure['code'], code)
                self.assertIn(failure['check'], ('review.reassessment', 'review.staged_task'))

    def test_later_predecessor_exposure_invalidates_completed_staged_repair_and_retains_history(self):
        records, value, _, raw = protocol_fixtures.StagedEntryTests.build_terminal_records(self)
        controller = records['review_assignment'][value['controller_assignment_id']]
        predecessor = dict(controller, id='predecessor', context={})
        predecessor.pop('staged_review')
        records['review_assignment'][predecessor['id']] = predecessor
        codex.probe_codex_route(self.case, 'repair-probe')
        probe = self.case.read_records()['review_route_probe']['repair-probe']
        records['review_route_probe'][probe['id']] = probe
        exposure = dict(id='exposure', assignment_id=predecessor['id'], kind='contamination',
            source='foreign_input', reason='Author context reached the predecessor.',
            evidence=[self.case.supporting], recorded_revision=probe['recorded_revision'] - 1)
        records['review_context_event'] = {exposure['id']: exposure}
        controller.update(recorded_revision=probe['recorded_revision'] + 1, context=dict(context_repair=dict(
            assignment_id=predecessor['id'], event_ids=[exposure['id']], probe_id=probe['id'])))
        repair.validate_repair_binding(records, self.case.artifacts, controller)
        retained = copy.deepcopy(records['review_task_acceptance'])
        with patch.object(producer, 'authenticate_observation', return_value=True), \
                patch.object(producer, 'matches_currency', return_value=True):
            before = producer.build_state(records, self.case.artifacts, controller)
            self.assertTrue(before['ready'], before['obligations'])
            self.assertTrue(before['completion']['complete'])
            later = dict(exposure, id='later-exposure', recorded_revision=controller['recorded_revision'] + 1)
            records['review_context_event'][later['id']] = later
            with self.assertRaises(ResearchError) as stale:
                repair.validate_repair_binding(records, self.case.artifacts, controller)
            self.assertEqual(stale.exception.code, 'review_context_repair_stale')
            after = producer.build_state(records, self.case.artifacts, controller)
            child = producer.build_state(records, self.case.artifacts,
                                         records['review_assignment']['selected-child-0'])
            historical = producer.build_state(records, self.case.artifacts, controller, include_contaminated=True)
        self.assertFalse(after['ready'], after['obligations'])
        self.assertEqual(after['context_status'], 'contaminated')
        self.assertEqual(after['effective']['context_state'], 'contaminated')
        self.assertIn('review_context_repair_stale', [row['code'] for row in after['obligations']])
        self.assertFalse(after['completion']['complete'])
        self.assertIn('review_context_repair_stale', [row['code'] for row in after['completion']['issues']])
        self.assertEqual(child['context_status'], 'contaminated')
        self.assertFalse(child['completion']['complete'])
        self.assertIn('review_context_repair_stale', [row['code'] for row in child['obligations']])
        self.assertEqual(after['output'], before['output'])
        self.assertEqual(after['raw_output'], raw)
        self.assertEqual(historical['raw_output'], raw)
        self.assertEqual(records['review_task_acceptance'], retained)
