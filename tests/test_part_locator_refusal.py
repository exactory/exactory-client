"""Synthetic content reproduces the incident shape for part offsets and predecessor repair bindings."""

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from research_harness.artifacts import ArtifactStore, describe_artifact
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.review_context_repair import prepare_repair
from research_harness.review_refusal import validate_evidence_contract
from research_harness.source_links import span_locator


FIXTURE = Path(__file__).parent / 'fixtures' / 'part_locator_refusal'


class RefusalCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        shutil.copytree(FIXTURE / 'research', self.root / 'research')
        self.artifacts = ArtifactStore(self.root)
        self.fixture = json.loads((FIXTURE / 'refusal.json').read_text(encoding='utf-8'))
        self.evidence = self.fixture['evidence']
        self.packet = self.fixture['packet']


class PartLocatorRefusalTests(RefusalCase):
    def check_evidence(self, evidence=None, packet=None):
        return validate_evidence_contract({}, self.artifacts, [evidence or self.evidence],
                                          packets=(packet or self.packet,))

    def assert_part_refusal(self, evidence=None):
        with self.assertRaises(ResearchError) as caught:
            self.check_evidence(evidence)
        error = caught.exception
        self.assertEqual(error.code, 'invalid_locator')
        self.assertEqual(error.contract_check, 'evidence.locator')
        self.assertIn('[118926, 157555)', error.message)
        self.assertIn('counts from the start of the part', error.message)
        return error

    def test_refused_span_names_part_range_and_relative_offsets_without_reinterpreting(self):
        before = copy.deepcopy(self.fixture)
        before_digest = digest(self.fixture)
        error = self.assert_part_refusal()
        self.assertIn('start 0 and end 38629', error.message)
        self.assertEqual(error.details['part_relative_span'], {'start': 0, 'end': 38629})
        content = self.artifacts.read(self.evidence['link']['artifact']).decode('utf-8')
        self.assertEqual(len(content), 38629)  # UTF-8 bytes and character offsets differ in this synthetic part.
        corrected = copy.deepcopy(self.evidence)
        corrected['link']['locator'].update(start=0, end=38629, excerpt=content[:200])
        self.check_evidence(corrected)
        for change, message in (({'excerpt': content[1:20]}, 'excerpt'), ({'sha256': '0' * 64}, 'hash')):
            with self.subTest(change=change), self.assertRaises(ResearchError) as caught:
                changed = copy.deepcopy(corrected)
                changed['link']['locator'].update(change)
                self.check_evidence(changed)
            self.assertIn(message, caught.exception.message)
        overlapping = copy.deepcopy(self.packet)
        overlapping['source_deliveries'][0]['part'].update(start=10, end=38639)
        local = copy.deepcopy(self.evidence)
        local['link']['locator'] = span_locator(content, 15, 25)
        # When both ranges contain a span, its saved part-relative meaning wins.
        self.check_evidence(local, overlapping)
        self.assertEqual(self.fixture, before)
        self.assertEqual(digest(self.fixture), before_digest)

    def test_whole_text_subspan_names_offsets_within_the_part(self):
        evidence = copy.deepcopy(self.evidence)
        content = self.artifacts.read(evidence['link']['artifact']).decode('utf-8')
        evidence['link']['locator'] = dict(span_locator(content, 5, 25), start=118931, end=118951)
        error = self.assert_part_refusal(evidence)
        self.assertIn('start 5 and end 25', error.message)
        self.assertEqual(error.details['part_relative_span'], {'start': 5, 'end': 25})

    def test_repeated_part_bytes_use_the_range_containing_the_supplied_span(self):
        packet = copy.deepcopy(self.packet)
        later = packet['source_deliveries'][0]
        content = self.artifacts.read(self.evidence['link']['artifact'])
        whole = describe_artifact(content + content, self.evidence['link']['artifact']['media_type'])
        later['origin']['artifact_descriptor'] = dict(
            location=whole['path'], **{key: whole[key] for key in ('sha256', 'size', 'media_type')})
        later['part'].update(start=38629, end=77258)
        later['origin']['locator'].update(start=38629, end=77258)
        earlier = copy.deepcopy(later)
        earlier['part'].update(start=0, end=38629, part=1)
        earlier['origin']['locator'].update(start=0, end=38629)
        packet['source_deliveries'].insert(0, earlier)
        evidence = copy.deepcopy(self.evidence)
        evidence['link']['locator'].update(start=38629, end=77258)
        with self.assertRaises(ResearchError) as caught:
            self.check_evidence(evidence, packet)
        error = caught.exception
        self.assertEqual(error.code, 'invalid_locator')
        self.assertIn('[38629, 77258)', error.message)
        self.assertEqual(error.details['part_relative_span'], {'start': 0, 'end': 38629})

    def test_only_matching_delivered_part_provides_whole_text_offset_guidance(self):
        self.assert_part_refusal()
        for field, value in (('status', 'deferred_reading_budget'), ('version_id', 'arxiv:0001.00000v2'),
                             ('excerpt', {}), ('source_id', 'another-source')):
            packet = copy.deepcopy(self.packet)
            delivery = packet['source_deliveries'][0]
            if field == 'source_id':
                delivery['origin'][field] = value
            else:
                delivery[field] = value
            with self.subTest(field=field), self.assertRaises(ResearchError) as caught:
                self.check_evidence(packet=packet)
            self.assertEqual(caught.exception.message, 'The span must lie within the saved text')
        for start, end in ((118925, 157555), (118926, 157556), (-1, 10), (True, 157555), (157555, 118926)):
            evidence = copy.deepcopy(self.evidence)
            evidence['link']['locator'].update(start=start, end=end)
            with self.subTest(start=start, end=end), self.assertRaises(ResearchError) as caught:
                self.check_evidence(evidence)
            self.assertEqual(caught.exception.message, 'The span must lie within the saved text')


class RepairBindingRefusalTests(RefusalCase):
    def build_repair_payload(self):
        payload = copy.deepcopy(self.fixture['repair_payload'])
        # record_assignment supplies the problem subject before calling prepare_repair.
        payload['subject'] = {'kind': 'problem', 'statement_id': None}
        return payload

    def assert_binding_refusal(self, payload, missing):
        # Insertion order deliberately differs from recorded order.
        records = copy.deepcopy(self.fixture['repair_records'])
        records['review_context_event'] = dict(reversed(list(records['review_context_event'].items())))
        with self.assertRaises(ResearchError) as caught:
            prepare_repair(records, self.artifacts, payload)
        error = caught.exception
        self.assertEqual(error.code, 'review_context_repair_mismatch')
        self.assertIsInstance(error.details, dict)
        expected_ids = ['retire-fixture-route-0', 'retire-fixture-route-1']
        self.assertEqual(error.details['expected_event_ids'], expected_ids)
        self.assertEqual(error.details['missing_event_ids'], missing)
        expected_evidence = self.fixture['repair_payload']['context']['context_repair']['evidence']
        self.assertEqual(error.details['expected_evidence'], expected_evidence)
        self.assertIn(json.dumps(expected_ids), error.message)
        self.assertIn(json.dumps(expected_evidence, sort_keys=True), error.message)
        return error

    def test_second_retirement_alone_names_missing_predecessor_and_expected_evidence_order(self):
        payload = self.build_repair_payload()
        repair = payload['context']['context_repair']
        repair['event_ids'] = ['retire-fixture-route-1']
        repair['evidence'] = repair['evidence'][1:]
        self.assert_binding_refusal(payload, ['retire-fixture-route-0'])

    def test_missing_events_are_named_in_recorded_order(self):
        payload = self.build_repair_payload()
        payload['context']['context_repair']['event_ids'] = []
        self.assert_binding_refusal(payload, ['retire-fixture-route-0', 'retire-fixture-route-1'])

    def test_evidence_mismatch_names_all_expected_event_ids_and_evidence_in_recorded_order(self):
        payload = self.build_repair_payload()
        payload['context']['context_repair']['evidence'].reverse()
        payload['context']['context_repair']['evidence'].pop()
        self.assert_binding_refusal(payload, [])
