"""Staged reading metadata remains citable and part of exact original coverage."""

import copy
import json
import sys
from unittest.mock import patch

from research_harness import direction_reviews, research_decisions, review_protocol as protocol
from research_harness import review_staged as staged, review_staged_protocol as producer, review_worker
from research_harness.evidence import digest
from research_harness.problem_evidence import describe_evidence
from research_harness.review_refusal import evidence_arrays
from test_research_problem_citation_metadata import CitationMetadataCase
from test_review_staged_fixtures import epoch, task
import test_review_staged_frontier as frontier
from problem_parts_fixtures import ResultPartsCase


class StagedCitationMetadataTests(CitationMetadataCase):
    def make_packet(self, reading):
        manifest = staged.build_manifest({'statement': 'The bounded claim.', 'grade': 2},
            [dict(key='readings', index=0, item=reading, artifacts=[], source_ids=[])], [])
        value = epoch(manifest=manifest)
        assigned = task(value)
        return value, assigned, producer.build_packet(value, assigned)

    def test_reading_metadata_is_delivered_and_resolves_as_a_nested_record(self):
        record = self.record_metadata()
        records = self.store.snapshot()['records']
        reading = describe_evidence(records, self.artifacts)['readings'][0]
        _, _, packet = self.make_packet(reading)
        metadata = reading['citation_metadata']
        self.assertEqual(packet['staged_review']['units'][0].get('citation_metadata'), metadata)
        carried = list(protocol._list_packet_records(packet))
        for reference in (metadata['cite'], dict(metadata['cite'], digest=metadata['digest'])):
            for frozen in (False, True):
                self.assertEqual(protocol.find_carried_record(records, carried, reference, frozen=frozen),
                                 (metadata, []))
        self.assertIsNone(protocol.find_carried_record(records, carried,
            dict(metadata['cite'], digest=digest('other version')), frozen=True))
        self.assertEqual(metadata['digest'], digest(record))

    def test_metadata_does_not_expand_a_segment_beyond_its_original_input(self):
        self.record_metadata()
        reading = describe_evidence(self.store.snapshot()['records'], self.artifacts)['readings'][0]
        value, complete, _ = self.make_packet(reading)
        occurrence = value['manifest']['occurrences'][0]
        source = next(row for row in value['manifest']['sources'] if row['id'] == occurrence['item_source_id'])
        assigned = staged.make_task(value, 'read', inputs=[staged.source_span(source, 0, 10)],
                                    occurrence_ids=[occurrence['id']], claim_ids=[], measurement=complete['measurement'])
        packet = producer.build_packet(value, assigned)
        self.assertNotIn('citation_metadata', packet['staged_review']['units'][0])
        self.assertFalse(list(protocol._list_packet_records(packet)))

    def test_reading_without_metadata_keeps_the_recorded_packet_bytes(self):
        reading = describe_evidence(self.store.snapshot()['records'], self.artifacts)['readings'][0]
        self.assertNotIn('citation_metadata', reading)
        _, _, packet = self.make_packet(reading)
        # Recorded with the builder at 24b8fd3, before the optional projection.
        self.assertEqual(digest(packet), '40d5784db94336c65dd55b5a1a126f3ad0bbe96ca4e9aba1ac91413f1876180d')
        self.assertNotIn('citation_metadata', packet['staged_review']['units'][0])

    def test_result_series_cites_and_covers_the_reading_metadata(self):
        self.record_metadata()
        source_records = self.store.snapshot()['records']
        reading = describe_evidence(source_records, self.artifacts)['readings'][0]
        metadata = reading['citation_metadata']
        fixture = ResultPartsCase()
        fixture.staged_entry = True
        fixture.output_tokens = 4000
        fixture.timeout_seconds = 60
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        case = fixture.case
        case.expected['readings'] = [copy.deepcopy(reading)]
        for reference in protocol.collect_references(source_records):
            self.assertEqual(case.artifacts.put(self.artifacts.read(reference), reference['media_type']), reference)
        for kind in ('source', 'source_bundle', 'fulltext_capture', 'work'):
            for identifier, record in source_records.get(kind, {}).items():
                case.put(kind, identifier, record)
        for kind, identifier in (('reading', reading['id']), ('problem_citation_metadata', metadata['id'])):
            case.put(kind, identifier, source_records[kind][identifier])
        manifest = staged.build_manifest({'statement': 'The bounded claim.', 'grade': 2},
            [dict(key='readings', index=0, item=reading, artifacts=[], source_ids=[])], [])
        prior = fixture.assign('prior')
        with patch.object(producer, 'build_manifest', return_value=manifest):
            controller = case.assign('result', 'first', identifier='controller',
                                     staged_upgrade={'assignment_id': prior['id']})
        output = case.build_review_output('result')
        output['value'].update(objective_adequacy='not_assessed', method_adequacy='not_assessed')
        for references in evidence_arrays(output):
            references[:] = [copy.deepcopy(metadata['cite'])]
        (case.root / 'scientific.json').write_text(json.dumps(output))
        next_work = controller['next'][0]
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', frontier.WORKER]):
            for _ in range(20):
                result = frontier.FrontierTests.command(fixture, next_work['command'], next_work['payload'])
                if next_work['command'] == 'value-review':
                    saved = result
                    break
                self.assertNotIn('refusal', result, result)
                next_work = result['next'][0]
            else:
                self.fail('The metadata series did not reach scientific recording')
        records = case.read_records()
        graph = direction_reviews.collect_selected_task_graph(records, case.artifacts, controller['id'])
        self.assertTrue(graph['completion']['complete'], graph['completion']['issues'])
        item = graph['epoch']['manifest']['occurrences'][0]
        self.assertTrue(next(row for row in graph['completion']['coverage']
                             if row['source_id'] == item['item_source_id'])['complete'])
        packets = [packet for _, packet in graph['packets']]
        carried = protocol.collect_carried_records(records, case.artifacts, controller['id'], packets=packets)
        self.assertEqual(protocol.find_carried_record(records, carried, metadata['cite'], frozen=True), (metadata, []))
        self.assertEqual(research_decisions._assess_problem_coverage(records, case.artifacts, [saved],
                         case.expected, digest(case.expected)), [])
        omitted = copy.deepcopy(graph['selection'])
        for observed in omitted['observations']:
            observed['actual_input']['intervals'] = [span for span in observed['actual_input']['intervals']
                                                   if span['source_id'] != item['item_source_id']]
        completion = staged.verify_completion(graph['epoch'], omitted, tasks=graph['tasks'])
        self.assertFalse(completion['complete'])
        self.assertTrue(any(row['code'] == 'review_source_coverage_pending'
                            and row.get('source_id') == item['item_source_id'] for row in completion['issues']))
        with patch.object(direction_reviews, 'collect_selected_task_graph', return_value=dict(graph, completion=completion)):
            self.assertEqual(research_decisions._assess_problem_coverage(records, case.artifacts, [saved],
                case.expected, digest(case.expected))[0]['code'], 'problem_evidence_unassessed')
