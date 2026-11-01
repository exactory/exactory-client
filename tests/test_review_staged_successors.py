"""Successor epochs retain observed ancestry without crediting refused science."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research_harness.artifacts import ArtifactStore
from research_harness import review_protocol as protocol
from research_harness import review_staged as staged
from research_harness import review_staged_protocol as producer
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from test_review_staged_fixtures import epoch, full_card, observation, task


class StagedSuccessorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.artifacts = ArtifactStore(Path(temporary.name))
        self.previous = epoch(controller_assignment_id='previous-controller', route_id='route')
        self.controller = dict(id='previous-controller', role='result', subject=self.previous['subject'],
            reviewer_id='reviewer-A', author_id='author', route_id='route', dossier_id=None, slot_id='A', context={},
            staged_review=dict(kind='controller', epoch_id=self.previous['id']))
        assigned = task(self.previous)
        self.child = dict(self.controller, id='previous-reader', staged_task=assigned,
            staged_review=dict(kind='task', epoch_id=self.previous['id'],
                               controller_assignment_id=self.controller['id'], task_id=assigned['id']))
        self.finding = dict(id='accepted-adverse', text='The claimed implication fails for composite inputs.',
            adverse=True, classification=dict(decisions=['/value/status'], reason='The domain limits the claim.'),
            claim_ids=[self.previous['manifest']['claims'][0]['id']], grounds=full_card(self.previous)['critical_spans'])
        output = dict(cards=[full_card(self.previous)], findings=[self.finding])
        observed = observation(self.previous, assigned, output)
        reference = protocol._put_json(self.artifacts, output)
        self.capture = dict(id='accepted-capture', reservation_id='accepted-capture', task_id=assigned['id'],
            origin='instrumented', worker={'pid': 17}, recorded_revision=4,
            observation=dict(status='completed', output=reference))
        self.attempt = dict(id=observed['id'], assignment_id=self.child['id'], reservation_id=self.capture['id'],
                            origin='instrumented', status='completed', output=reference, recorded_revision=5)
        self.records = dict(review_assignment={self.controller['id']:self.controller, self.child['id']:self.child},
            review_epoch={self.previous['id']:self.previous},
            review_task={assigned['id']:dict(id=assigned['id'], epoch_id=self.previous['id'], task=assigned)},
            review_attempt={self.attempt['id']:self.attempt}, review_observation={self.capture['id']:self.capture},
            review_task_acceptance={'accepted':dict(task_id=assigned['id'], reservation_id=self.capture['id'],
                receipt=dict(attempt=self.attempt, staged_observation=observed))},
            review_slot_head={'A':dict(slot_id='A', epoch_id=self.previous['id'], state='pending', outcome=None)})
        authenticate = patch.object(producer, 'authenticate_observation', return_value=True)
        authenticate.start()
        self.addCleanup(authenticate.stop)

    def prepare_successor(self, identifier='successor', predecessor=None, currency='new-evidence'):
        predecessor = predecessor or self.controller['id']
        record = dict(self.controller, id=identifier, context={'staged_upgrade': {'assignment_id': predecessor}})
        record.pop('staged_review', None)
        packet = dict(role='result', subject=self.previous['subject'], problem_evidence_digest=currency,
                      statement='The current claim is assessed against newly supplied evidence.')
        with patch.object(producer, 'prepare_frame_tasks', return_value=[]):
            changes = producer.prepare_controller(self.records, self.artifacts, record, packet, {})
        frozen = next(value for kind, _, value in changes if kind == 'review_epoch')
        return record, frozen, changes

    def install(self, record, changes):
        self.records['review_assignment'][record['id']] = record
        for kind, identifier, value in changes:
            self.records.setdefault(kind, {})[identifier] = value

    def test_predecessor_binds_the_observed_child_attempt_and_capture(self):
        _, frozen, _ = self.prepare_successor()
        predecessor = frozen['predecessor']
        self.assertFalse(predecessor['no_output'])
        self.assertEqual(predecessor['attempt_id'], self.attempt['id'])
        self.assertEqual(predecessor['attempt_digest'], digest(self.attempt))
        self.assertEqual(predecessor['attempt_assignment_id'], self.child['id'])
        self.assertEqual(predecessor['observation_id'], self.capture['id'])
        self.assertEqual(predecessor['observation_digest'], digest(self.capture))

    def test_captured_but_not_accepted_output_is_not_bound_as_no_output(self):
        self.records['review_attempt'].clear()
        self.records['review_task_acceptance'].clear()
        _, frozen, _ = self.prepare_successor()
        predecessor = frozen['predecessor']
        self.assertFalse(predecessor['no_output'])
        self.assertIsNone(predecessor['attempt_id'])
        self.assertEqual(predecessor['observation_id'], self.capture['id'])
        self.assertEqual(predecessor['observation_digest'], digest(self.capture))

    def test_accepted_receipt_still_binds_its_attempt_before_attempt_recording_resumes(self):
        self.records['review_attempt'].clear()
        _, frozen, _ = self.prepare_successor()
        self.assertEqual(frozen['predecessor']['attempt_id'], self.attempt['id'])
        self.assertEqual(frozen['predecessor']['attempt_digest'], digest(self.attempt))
        self.assertEqual(frozen['predecessor']['observation_digest'], digest(self.capture))

    def test_accepted_adverse_finding_is_a_sealed_inherited_obligation_with_exact_packet_material(self):
        _, frozen, _ = self.prepare_successor()
        inherited = next(row for row in frozen['manifest']['inherited_findings']
                         if row.get('record') == self.finding)
        self.assertEqual(inherited['provenance']['attempt_id'], self.attempt['id'])
        self.assertEqual(inherited['provenance']['observation_digest'], digest(self.capture))
        self.assertTrue(inherited['accepted'])
        staged._verify_seal(frozen['manifest'])
        completion = staged.verify_completion(frozen, {'observations': []})
        self.assertTrue(any(row['code'] == 'review_finding_answer_pending' and row.get('finding_id') == inherited['id']
                            for row in completion['issues']))
        assigned = staged.make_task(frozen, 'frame', inputs=[row['span'] for row in frozen['manifest']['frame_fields']],
                                    measurement=task(self.previous)['measurement'])
        packet = producer.build_packet(frozen, assigned)
        self.assertIn(self.finding['text'], json.dumps(packet))
        entry = next(row for row in packet['staged_review']['inherited'] if row['id'] == inherited['id'])
        self.assertEqual(entry['provenance'], inherited['provenance'])
        self.assertEqual(entry['record_digest'], digest(inherited))
        self.assertTrue(entry['source_ids'])

    def test_unverified_accepted_finding_cannot_be_laundered_into_the_successor(self):
        record = dict(self.controller, id='unverified-successor',
                      context={'staged_upgrade': {'assignment_id': self.controller['id']}})
        with patch.object(producer, 'authenticate_observation', return_value=False), \
                patch.object(producer, 'prepare_frame_tasks', return_value=[]):
            with self.assertRaises(ResearchError) as failure:
                producer.prepare_controller(self.records, self.artifacts, record,
                    dict(role='result', subject=self.previous['subject']), {})
        self.assertEqual(failure.exception.code, 'review_invocation_unverified')

    def test_inherited_packet_index_stays_scoped_to_assigned_originals(self):
        _, frozen, _ = self.prepare_successor()
        fields = [row['span'] for row in frozen['manifest']['frame_fields']
                  if row['path'][0] != 'inherited_review_findings']
        assigned = staged.make_task(frozen, 'frame', inputs=fields,
                                    measurement=task(self.previous)['measurement'])
        packet = producer.build_packet(frozen, assigned)
        self.assertEqual(packet['staged_review']['inherited'], [])

    def test_pending_child_invocation_must_be_reconciled_before_successor(self):
        self.records['review_invocation'] = {'pending': dict(id='pending', assignment_id=self.child['id'])}
        with self.assertRaises(ResearchError) as failure:
            self.prepare_successor()
        self.assertEqual(failure.exception.code, 'review_worker_reconciliation_required')

    def test_refused_adverse_remains_visible_and_uncredited_through_successor_ancestry(self):
        refused = dict(id='refused-adverse', text='A necessary premise is missing.', adverse=True)
        unresolved = 'The theorem omits the boundary case.'
        raw = {'findings': [refused], 'unresolved': [unresolved]}
        capture = dict(self.capture, id='refused-capture', reservation_id='refused-capture', recorded_revision=2,
                       observation=dict(status='completed', output=protocol._put_json(self.artifacts, raw)))
        self.records['review_observation'][capture['id']] = capture
        self.records['review_task_refusal'] = {'refused': dict(id='refused', reservation_id=capture['id'])}
        record, frozen, changes = self.prepare_successor()
        self.install(record, changes)
        _, later, _ = self.prepare_successor('later', record['id'], currency='later-evidence')
        findings = producer.collect_refused_findings(self.records, self.artifacts, later)
        carried = next(row for row in findings if row['record'] == refused)
        self.assertFalse(carried['accepted'])
        self.assertEqual(carried['epoch_id'], self.previous['id'])
        self.assertEqual(carried['reservation_id'], capture['id'])
        self.assertEqual(carried['raw_output_digest'], digest(raw))
        unresolved_record = next(row for row in findings if row['record'] == unresolved)
        self.assertEqual(unresolved_record['family'], 'unresolved')
        self.assertFalse(unresolved_record['accepted'])
        self.assertFalse(any(row.get('record') == refused for row in later['manifest']['inherited_findings']))
        self.assertTrue(any(row.get('record') == self.finding for row in later['manifest']['inherited_findings']))

    def test_ordinary_predecessor_retains_its_exact_historical_attempt_binding(self):
        ordinary = dict(self.controller, id='ordinary')
        ordinary.pop('staged_review')
        ordinary_attempt = dict(self.attempt, id='ordinary-attempt', assignment_id=ordinary['id'])
        ordinary_attempt.pop('reservation_id')
        self.records['review_assignment'][ordinary['id']] = ordinary
        self.records['review_attempt'][ordinary_attempt['id']] = ordinary_attempt
        with patch.object(producer, 'authenticate_observation', side_effect=AssertionError('Not a staged predecessor.')):
            _, frozen, _ = self.prepare_successor(predecessor=ordinary['id'])
        self.assertEqual(frozen['predecessor']['attempt_id'], ordinary_attempt['id'])
        self.assertEqual(frozen['predecessor']['attempt_digest'], digest(ordinary_attempt))
        self.assertFalse(frozen['predecessor']['no_output'])


if __name__ == '__main__':
    unittest.main()
