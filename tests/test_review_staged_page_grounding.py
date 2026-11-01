"""Acceptance and composition require delivered, independently resolved grounds."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_harness import review_composition as composition, review_protocol as protocol
from research_harness import review_staged as staged, review_staged_protocol as producer
from research_harness import review_transport as transport
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from test_review_staged_fixtures import epoch, full_card, observation, task
from test_review_staged_text_contracts import incomplete_output


class StagedPageGroundingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.artifacts = ArtifactStore(Path(temporary.name))
        self.epoch = epoch()
        self.card = full_card(self.epoch)
        self.reader = task(self.epoch)
        self.reading = observation(self.epoch, self.reader, {'cards': [self.card]})
        bindings = producer.build_composition_bindings(self.epoch, [self.reading], tasks=[self.reader])
        self.plan = composition.plan_incomplete_composition(self.epoch, outcome=incomplete_output(),
            known_conclusions=self.card['conclusions'], findings=[], page_characters=10000,
            **{key: bindings[key] for key in ('source_root', 'dependency_root',
                'selected_observations_root', 'integration_root')})
        self.plan['evidence_obligations'] = bindings['evidence_obligations']
        self.answer = task(self.epoch, 'answer', dependencies=[self.reader['id']],
            composition_role='incomplete', composition_plan_root=digest(self.plan),
            answer_ranges=self.plan['ownership'])
        self.tasks = [self.reader, self.answer]
        self.packet = producer.build_packet(self.epoch, self.answer, observations=[self.reading],
            composition_plan=self.plan, tasks=self.tasks)
        self.request = {'input': [{'role': 'user',
            'content': transport.PACKET_LABEL + json.dumps(self.packet)}]}
        self.answer = staged.freeze_task(self.answer, digest(self.request), staged.SERIALIZATION)
        self.tasks = [self.reader, self.answer]
        source = self.packet['staged_review']['answer']
        self.page = {key: copy.deepcopy(source[key]) for key in ('protocol', 'epoch_id', 'role',
            'task_id', 'page_id', 'revision', 'source_root', 'dependency_root', 'ownership', 'replaces')}
        ground = self.card['conclusions'][0]
        self.page.update(grounding=[ground], findings=[], dispositions=[],
            fragments=[dict(fragment, grounds=[ground]) for fragment in source['source_fragments']])
        self.records = dict(review_epoch={self.epoch['id']: self.epoch},
            review_task={row['id']: dict(epoch_id=self.epoch['id'], task=row) for row in self.tasks},
            review_composition_plan={digest(self.plan): self.plan},
            review_task_acceptance={'reading': dict(task_id=self.reader['id'],
                receipt=dict(staged_observation=self.reading))})

    def replace_ground(self, ground):
        self.page['grounding'] = [ground]
        for fragment in self.page['fragments']:
            fragment['grounds'] = [ground]

    def accept_page(self):
        assignment = dict(id='answer-assignment', role='result', staged_task=self.answer,
            staged_review=dict(kind='task', epoch_id=self.epoch['id']), composition_plan_id=digest(self.plan),
            packet_digest=digest(self.packet), prompt_hash=digest('prompt'))
        captured = dict(reservation_id='answer-reservation', recorded_revision=1,
            observation=dict(request=protocol._put_json(self.artifacts, self.request),
                             output=protocol._put_json(self.artifacts, {'pages': [self.page]})))
        with patch.object(producer, 'authenticate_observation', return_value=True), \
                patch.object(producer, 'matches_currency', return_value=True):
            return producer.accept_capture(self.records, self.artifacts, assignment, captured, 'answer-attempt')

    def compose_page(self):
        observed = observation(self.epoch, self.answer, {'pages': [self.page]},
            dependencies={self.reader['id']: self.reading['raw_output_digest']})
        return producer.build_composition(self.epoch, self.plan, [self.reading, observed], None, tasks=self.tasks)

    def test_acceptance_rejects_self_certified_and_invented_grounds(self):
        for ground in ({'invented': True},
                       {'kind': 'reviewer_observation', 'reason': 'This page certifies itself.'},
                       dict(self.card['conclusions'][0], text='An invented conclusion.')):
            with self.subTest(ground=ground):
                self.replace_ground(ground)
                with self.assertRaises(ResearchError) as raised:
                    self.accept_page()
                self.assertEqual(raised.exception.code, 'review_composition_conflict')

    def test_composition_rejects_self_certified_and_invented_grounds(self):
        for ground in ({'invented': True},
                       {'kind': 'reviewer_observation', 'reason': 'This page certifies itself.'},
                       dict(self.card['conclusions'][0], text='An invented conclusion.')):
            with self.subTest(ground=ground):
                self.replace_ground(ground)
                with self.assertRaises(ResearchError) as raised:
                    self.compose_page()
                self.assertEqual(raised.exception.code, 'review_composition_conflict')

    def test_delivered_resolved_conclusion_can_be_accepted_and_composed(self):
        receipt = self.accept_page()
        self.assertEqual(receipt['staged_observation']['output']['pages'], [self.page])
        self.assertEqual(len(self.compose_page()['observations']), 1)

    def test_delivered_original_can_be_accepted_and_composed(self):
        self.replace_ground(self.card['critical_spans'][0])
        self.assertEqual(self.accept_page()['staged_observation']['output']['pages'], [self.page])
        self.assertEqual(len(self.compose_page()['observations']), 1)
