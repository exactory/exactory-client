"""Math openness exports and native acceptance use the main capture bound by the exported packet."""

import copy
import json
import unittest
from unittest.mock import patch

from tests import test_openness_reading_chain as chain_fixtures
import test_openness_capture_binding as capture_fixtures
from research_harness import openness_reading, review_protocol as protocol
from research_harness.capture_selection import PACKET_KEY, describe_capture_selections, select_main_capture
from search_controller import reviewer


class MathOpennessCaptureSelectionTests(unittest.TestCase):
    def open_case(self):
        case = chain_fixtures.MathOpennessReadingTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.build_fixture()
        self.version = case.versions[0]
        self.first = case.store.snapshot()['records']['work'][self.version]['fulltexts'][0]
        self.compact = case.case.capture(self.version, pdf_text=capture_fixtures.COMPACT_TEXT)
        return case

    def choose_capture(self, case, capture):
        case.case.mutate(select_main_capture, dict(version_id=self.version, source_id=capture['source_id'],
                                                  reason='Select the complete compact extraction.'))

    def test_native_export_binds_the_current_capture_and_acceptance_keeps_that_selection(self):
        case = self.open_case()
        self.choose_capture(case, self.compact)
        unrelated = case.case.metadata(2)
        other = case.case.capture(unrelated, pdf_text='An unrelated complete source. References: none.')
        case.case.mutate(select_main_capture, dict(version_id=unrelated, source_id=other['source_id'],
                                                 reason='Keep this independent source choice.'))
        checkpoint = case.controller.status()['checkpoints']['checkpoint-000001']
        case.export_fixture(checkpoint)
        packet = json.loads(case.artifacts.read(case.exported['native_packet']))
        records = case.store.snapshot()['records']
        self.assertEqual(packet['scientific_context'][PACKET_KEY], describe_capture_selections(records)[PACKET_KEY])
        self.assertNotIn(other['text']['sha256'], {row['digest'] for row in packet['evidence']})
        self.choose_capture(case, self.first)
        turns = case.run_chain()
        rows = openness_reading.list_math_text_rows(case.store.snapshot()['records'], packet)
        self.assertEqual([row['artifact'] for row in rows], [self.compact['text']])
        delivered = [row for identifier in turns[:-1]
                     for row in case.read_turn(identifier)['reading_turn']['reading_plan']['delivered_now']
                     if row['kind'] == 'fulltext']
        self.assertEqual([row['text_sha256'] for row in delivered], [self.compact['text']['sha256']])
        case.check_review(checkpoint)
        case.accept_result()
        audited = case.controller.status(full_audit=True)
        self.assertEqual(audited['acceptances']['acceptance-000001']['status'], 'accepted')
        self.assertEqual(audited['freshness']['status'], 'fresh', audited['freshness'])
        case.review = copy.deepcopy(case.review)
        case.review['reviewer']['attestation_id'] = 'review-assignment:next-1'
        case.export_fixture(checkpoint)
        fresh = json.loads(case.artifacts.read(case.exported['native_packet']))
        self.assertEqual(openness_reading.list_math_text_rows(case.store.snapshot()['records'], fresh)[0]['artifact'],
                         self.first['text'])

    def test_stored_unbound_native_packet_chain_and_acceptance_keep_the_first_capture(self):
        case = self.open_case()
        namespace = dict(vars(reviewer))
        source = capture_fixtures.BUILDERS_PATH.read_bytes()
        exec(compile(source, str(capture_fixtures.BUILDERS_PATH), 'exec'), namespace)
        checkpoint = case.controller.status()['checkpoints']['checkpoint-000001']
        with capture_fixtures.build_before_capture_binding(), patch.object(reviewer, 'packet', namespace['packet']):
            case.export_fixture(checkpoint)
            turns = case.run_chain()
        records = case.store.snapshot()['records']
        native = case.exported['native_packet']
        packet_bytes = case.artifacts.read(native)
        self.assertNotIn(PACKET_KEY, json.loads(packet_bytes)['scientific_context'])
        receipts = case.read_receipts(turns[-1])
        self.choose_capture(case, self.compact)
        case.check_review(checkpoint)
        case.accept_result()
        self.assertEqual(case.read_receipts(turns[-1]), receipts)
        self.assertEqual(case.artifacts.read(native), packet_bytes)
        for identifier in turns:
            assignment = records['review_assignment'][identifier]
            attempt = records['review_attempt']['attempt-' + identifier]
            self.assertTrue(protocol.authenticate_attempt(case.store.snapshot()['records'], case.artifacts,
                                                          assignment, attempt))
        audited = case.controller.status(full_audit=True)
        self.assertEqual(audited['acceptances']['acceptance-000001']['reading_turns'], receipts)
        self.assertEqual(audited['freshness']['status'], 'fresh', audited['freshness'])
