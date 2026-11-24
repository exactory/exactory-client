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
    def setUp(self):
        from research_harness import openness_reading
        released = patch.object(openness_reading, 'NEW_MATH_CHAIN_PROTOCOLS', (openness_reading.MATH_PROTOCOL,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

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

    def test_stored_unbound_chosen_capture_chain_verifies_and_accepts_after_another_choice(self):
        from research_harness.evidence import digest
        from research_harness.literature import import_bundle
        from research_harness.reading import record_reading, fulltext_coverage
        case = self.open_case()
        self.choose_capture(case, self.compact)
        bundle = case.case.bundle(self.version, capture=self.compact, bundle_id='compact-bundle')
        for unit in bundle['units']:
            unit['link'] = case.case.link(self.version, self.compact['source_id'], self.compact['text'],
                                          unit['link']['locator']['quote'], kind='span')
        bundle['inventory']['links'] = [bundle['units'][0]['link']]
        case.case.mutate(import_bundle, bundle)
        case.case.mutate(record_reading, case.case.full_note(bundle, 'compact-reading'))
        records = case.store.snapshot()['records']
        ids = fulltext_coverage(records, case.artifacts, self.version)['readings']
        case.review['findings']['openness']['minimum']['reading_digests'] = [digest(records['reading'][key]) for key in ids]
        checkpoint = case.controller.status()['checkpoints']['checkpoint-000001']
        namespace = dict(vars(reviewer))
        exec(compile(capture_fixtures.BUILDERS_PATH.read_bytes(), str(capture_fixtures.BUILDERS_PATH), 'exec'), namespace)
        with capture_fixtures.build_before_capture_binding(), patch.object(reviewer, 'packet', namespace['packet']):
            case.export_fixture(checkpoint)
            native = case.exported['native_packet']
            packet = json.loads(case.artifacts.read(native))
            case.review['findings']['openness']['evidence'] = [row['digest'] for row in packet['evidence']]
            turns = case.run_chain()
            receipts = case.read_receipts(turns[-1])
        before = case.store.snapshot()
        self.assertEqual(case.read_receipts(turns[-1]), receipts)
        self.assertEqual(case.store.snapshot(), before)
        self.choose_capture(case, self.first)
        self.assertEqual(case.read_receipts(turns[-1]), receipts)
        case.check_review(checkpoint)
        case.accept_result()
        audited = case.controller.status(full_audit=True)
        self.assertEqual(audited['freshness']['status'], 'fresh', audited['freshness'])
        self.assertEqual(audited['acceptances']['acceptance-000001']['reading_turns'], receipts)
        self.assertEqual(case.artifacts.read(native), json.dumps(packet, sort_keys=True, ensure_ascii=False,
                                                               separators=(',', ':')).encode())
        rows = openness_reading.list_math_text_rows(case.store.snapshot()['records'], packet, artifacts=case.artifacts,
            review=case.store.snapshot()['records']['review_assignment'][turns[-1]])
        self.assertEqual([row['artifact'] for row in rows], [self.compact['text']])

    def test_unbound_math_prefix_continues_through_preflight_with_the_capture_it_delivered(self):
        from research_harness.bar_sources import descriptor
        from research_harness.evidence import digest
        from research_harness.literature import import_bundle
        from research_harness.reading import record_reading, fulltext_coverage
        from tests.openness_chain_support import build_filler
        case = self.open_case()
        self.compact = case.case.capture(self.version, pdf_text='Paper 1 opening line.\n' +
            build_filler('compact-', 160000) + '\nReferences: none.')
        self.choose_capture(case, self.compact)
        bundle = case.case.bundle(self.version, capture=self.compact, bundle_id='compact-bundle')
        for unit in bundle['units']:
            unit['link'] = case.case.link(self.version, self.compact['source_id'], self.compact['text'],
                                          unit['link']['locator']['quote'], kind='span')
        bundle['inventory']['links'] = [bundle['units'][0]['link']]
        case.case.mutate(import_bundle, bundle)
        case.case.mutate(record_reading, case.case.full_note(bundle, 'compact-reading'))
        records = case.store.snapshot()['records']
        ids = fulltext_coverage(records, case.artifacts, self.version)['readings']
        case.review['findings']['openness']['minimum']['reading_digests'] = [digest(records['reading'][key]) for key in ids]
        checkpoint = case.controller.status()['checkpoints']['checkpoint-000001']
        namespace = dict(vars(reviewer))
        exec(compile(capture_fixtures.BUILDERS_PATH.read_bytes(), str(capture_fixtures.BUILDERS_PATH), 'exec'), namespace)
        turns = ['turn-1']
        with capture_fixtures.build_before_capture_binding(), patch.object(reviewer, 'packet', namespace['packet']):
            case.export_fixture(checkpoint)
            packet = json.loads(case.artifacts.read(case.exported['native_packet']))
            case.review['findings']['openness']['evidence'] = [row['digest'] for row in packet['evidence']]
            case.assign_turn(turns[0])
            while not any(row.get('text_sha256') == self.compact['text']['sha256']
                          for row in case.read_turn(turns[-1])['reading_turn']['reading_plan']['delivered_now']):
                case.invoke(turns[-1], case.build_reading_response(case.read_turn(turns[-1])))
                turns.append('turn-%d' % (len(turns) + 1))
                case.assign_turn(turns[-1], turns[-2])
                self.assertLess(len(turns), 8)
            case.invoke(turns[-1], case.build_reading_response(case.read_turn(turns[-1])))
        pending_compact = [row for row in case.read_turn(turns[-1])['reading_turn']['reading_plan']['remaining']
                           if row.get('text_sha256') == self.compact['text']['sha256']]
        self.assertTrue(pending_compact)
        delivered_compact = []
        self.choose_capture(case, self.first)
        while True:
            prior = turns[-1]
            turns.append('turn-%d' % (len(turns) + 1))
            case.assign_turn(turns[-1], prior)
            packet = case.read_turn(turns[-1])
            if openness_reading.find_stage(packet) == 'final':
                case.invoke(turns[-1], case.review)
                break
            delivered_compact.extend(
                dict({key: value for key, value in row.items() if key != 'artifact'},
                     artifact_descriptor=descriptor(row['artifact']))
                for row in packet['reading_turn']['reading_plan']['delivered_now']
                if row.get('text_sha256') == self.compact['text']['sha256'])
            case.invoke(turns[-1], case.build_reading_response(packet))
            self.assertLess(len(turns), 12)
        self.assertEqual(delivered_compact, pending_compact)
        case.check_review(checkpoint)
        case.accept_result()
        self.assertEqual(case.controller.status(full_audit=True)['freshness']['status'], 'fresh')
