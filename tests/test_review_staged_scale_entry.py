"""Automatic scale fallback at the public assignment-recording boundary."""

import copy
from pathlib import Path
import signal
import time
import types
import unittest
from unittest.mock import patch

from problem_parts_fixtures import KEY, ResultPartsCase
from research_harness import review_problem_parts as parts, review_protocol as protocol
from research_harness import review_staged as staged, review_staged_protocol as producer
from research_harness import review_transport as transport
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.storage import _canonical
from test_review_staged_scale import scale_packet


def load_legacy_parts():
    # Exact source from exactory--v0.69.18, also available in shallow CI checkouts.
    source = (Path(__file__).parent / 'fixtures/review_problem_parts_0_69_18.py.txt').read_text()
    module = types.ModuleType('research_harness._legacy_problem_parts_06918')
    exec(compile(source, 'review_problem_parts_06918.py', 'exec'), module.__dict__)
    return module


class StagedScaleEntryTests(ResultPartsCase):
    staged_entry = True
    timeout_seconds = 600

    def install_scale_packet(self, large=True):
        synthetic = scale_packet(self.case.artifacts, large=large)
        self.case.expected['computations'] = synthetic['problem_evidence']['computations']
        # The scale fixture's frame needs more than the default local input
        # allowance. At this budget the real v1 builder can deliver it, while
        # the complete ordinary packet and staged full-card output cannot fit.
        self.oversized(count=0, limit=2200000 if large else 200000)
        original = protocol.build_packet
        packets = []
        def build(*args, **kwargs):
            packet = original(*args, **kwargs)
            packet['research'] = copy.deepcopy(synthetic['research'])
            packet.pop('digest', None)
            packet['digest'] = digest(packet)
            packets.append(copy.deepcopy(packet))
            return packet
        patcher = patch.object(protocol, 'build_packet', side_effect=build)
        patcher.start()
        self.addCleanup(patcher.stop)
        return packets

    def test_automatic_large_entry_records_byte_exact_06918_part_within_30_seconds(self):
        packets = self.install_scale_packet()
        def timed_out(signum, frame):
            raise AssertionError('automatic scale fallback exceeded 30 seconds')
        previous = signal.signal(signal.SIGALRM, timed_out)
        signal.alarm(30)
        started = time.monotonic()
        try:
            with patch.object(producer, 'build_manifest', wraps=producer.build_manifest) as manifest, \
                    patch.object(producer, 'check_scale', wraps=producer.check_scale) as preflight, \
                    patch.object(staged, 'freeze_epoch', wraps=staged.freeze_epoch) as freeze:
                assignment = self.assign('large-automatic')
            self.assertEqual(manifest.call_count, 1)
            self.assertEqual(preflight.call_count, 1)
            self.assertEqual(freeze.call_count, 0)
            self.assertLess(time.monotonic() - started, 30)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)
        records = self.case.read_records()
        recorded = self.packet(assignment)
        self.assertNotIn('staged_review', recorded)
        self.assertNotIn('staged_review', assignment)
        self.assertEqual(recorded[KEY]['stage'], 'part')
        self.assertFalse(records.get('review_epoch'))
        self.assertFalse(records.get('review_task'))
        self.assertFalse(records.get('review_slot_head'))
        legacy = load_legacy_parts()
        expected = legacy.prepare_packet(records, self.case.artifacts,
            dict(id=assignment['id'], role='result', context={}), records['review_route']['route'], packets[-1],
            protocol.canonical_prompt('result', orchestration='problem_creation'), assignment['history'])
        self.assertEqual(_canonical(recorded[KEY]), _canonical(expected[KEY]))
        self.assertEqual(assignment['packet'], protocol._put_json(self.case.artifacts, expected))
        self.assertEqual(assignment['prompt'], protocol.canonical_prompt(
            'result', orchestration='problem_creation', excerpts=True, evidence_parts='part'))
        self.assertTrue(assignment['history'])

    def test_automatic_under_bound_entry_preflights_and_freezes_manifest_once(self):
        self.install_scale_packet(large=False)
        with patch.object(producer, 'build_manifest', wraps=producer.build_manifest) as manifest, \
                patch.object(producer, 'check_scale', wraps=producer.check_scale) as preflight:
            assignment = self.assign('small-automatic')
        self.assertEqual(assignment['staged_review']['kind'], 'controller')
        self.assertNotIn(KEY, self.packet(assignment))
        self.assertEqual(manifest.call_count, 1)
        self.assertEqual(preflight.call_count, 1)
        records = self.case.read_records()
        frozen = records['review_epoch'][assignment['staged_review']['epoch_id']]
        self.assertEqual(frozen['manifest'], preflight.call_args.args[3])
        self.assertEqual(len(frozen['manifest']['occurrences']), 32)

    def test_explicit_large_upgrade_refuses_without_record_or_artifact_writes(self):
        prior = self.assign('ordinary-predecessor')
        self.install_scale_packet()
        before = self.case.store.snapshot()
        objects = self.case.root / 'research/sources/objects'
        paths = sorted(path.relative_to(objects) for path in objects.rglob('*'))
        with self.assertRaises(ResearchError) as caught:
            self.case.assign('result', 'first', identifier='explicit-large',
                             staged_upgrade=dict(assignment_id=prior['id']))
        self.assertEqual(caught.exception.code, 'review_staged_scale_unsupported')
        self.assertEqual(self.case.store.snapshot(), before)
        self.assertEqual(sorted(path.relative_to(objects) for path in objects.rglob('*')), paths)

    def test_automatic_entry_propagates_other_preflight_errors(self):
        self.install_scale_packet(large=False)
        with patch.object(producer, 'check_scale', side_effect=ResearchError(
                'review_task_dependency_changed', 'The prepared input changed.')):
            with self.assertRaises(ResearchError) as caught:
                self.assign('changed-input')
        self.assertEqual(caught.exception.code, 'review_task_dependency_changed')

    def test_focused_scale_refusal_retains_the_06918_packet(self):
        self.oversized(count=0, limit=200000)
        records = self.case.read_records()
        route = records['review_route']['route']
        route = dict(route, configuration=dict(route['configuration'], max_output_tokens=1000))
        packet = dict(scale_packet(self.case.artifacts), subject=dict(kind='problem'), role='adjudicator')
        packet['digest'] = digest(packet)
        value = dict(id='focused', role='adjudicator', context={})
        prompt = protocol.canonical_prompt('adjudicator', orchestration='problem_creation')
        expected = load_legacy_parts().prepare_packet(records, self.case.artifacts, value, route, packet, prompt, [])
        prepared = parts.prepare_packet(records, self.case.artifacts, value, route, packet, prompt, [])
        self.assertEqual(_canonical(prepared), _canonical(expected))
        self.assertNotIn('staged_review', prepared)

    def test_explicit_under_bound_upgrade_publishes_prepared_packets_and_requests(self):
        prior = self.assign('ordinary-predecessor')
        self.install_scale_packet(large=False)
        successor = self.case.assign('result', 'first', identifier='explicit-small',
                                     staged_upgrade=dict(assignment_id=prior['id']))
        records = self.case.read_records()
        self.assertEqual(self.packet(successor)['staged_review']['kind'], 'controller')
        for task_id in successor['staged_review']['task_ids']:
            task = records['review_task'][task_id]
            self.assertTrue(self.case.artifacts.read(task['packet']))
            self.assertTrue(self.case.artifacts.read(task['request']))


class StagedVisualScaleEntryTests(unittest.TestCase):
    def test_explicit_visual_upgrade_refuses_without_new_pages_or_inventory(self):
        import test_visual_result_delivery as fixtures
        from test_research_binary_pdf import authored_pdf
        from research_fixtures import client
        from research_harness import literature, reading
        from research_harness.acquisition import acquire_fulltext
        from research_harness.direction_sources import build_inline_passages
        fixture = fixtures.VisualResultDeliveryTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        case = fixture.case
        version = case.metadata(3)
        body = authored_pdf().replace(b'acquisition', b'replacement')
        self.assertNotEqual(body, authored_pdf())
        http, _, _ = client([(200, {'Content-Type': 'application/pdf'}, body)])
        capture = acquire_fulltext(case.store, version, 'https://arxiv.org/pdf/' + version[6:], http=http,
            extractor=lambda _: dict(status='extracted', text='Additional authored page. References: none.\f'),
            expected_revision=case.store.revision, request_id='new-visual-pdf')['capture']
        bundle = case.bundle(version, capture=capture, bundle_id='new-visual-pdf-bundle')
        link = dict(version_id=version, source_id=capture['source_id'], artifact=capture['original'],
                    locator=dict(kind='pdf', page_index=0, printed_page='1', region=[0, 0, 1, 1]))
        bundle['units'].append(dict(id='new-figure', kind='figure', required=True, link=link))
        case.mutate(literature.import_bundle, bundle)
        case.mutate(reading.record_reading, case.full_note(bundle, note_id='new-visual-pdf-reading'))
        records = case.read_records()
        note = records['reading']['new-visual-pdf-reading']
        case.expected['readings'].append(dict(id=note['id'], version_id=version, digest=digest(note),
                                             depth='fulltext', reading=note))
        for passage, data in build_inline_passages(records, case.artifacts, [note]):
            if data is not None:
                self.assertEqual(case.artifacts.put(data, passage['excerpt']['media_type']), passage['excerpt'])
            case.expected['passages'].append(passage)
        before = case.store.snapshot()
        objects = case.root / 'research/sources/objects'
        paths = sorted(path.relative_to(objects) for path in objects.rglob('*'))
        prior = fixture.assignment
        value = {key: prior[key] for key in ('role', 'route_id', 'reviewer_id', 'author_id', 'dossier_id')}
        value.update(id='new-page-upgrade', context=dict(staged_upgrade=dict(assignment_id=prior['id'])))
        with self.assertRaises(ResearchError) as caught:
            case.mutate(protocol.record_assignment, value)
        self.assertEqual(caught.exception.code, 'review_staged_scale_unsupported')
        self.assertEqual(case.store.snapshot(), before)
        self.assertEqual(sorted(path.relative_to(objects) for path in objects.rglob('*')), paths)


class StagedOmittedBodyEntryTests(ResultPartsCase):
    staged_entry = True
    output_tokens = 1000

    def test_scale_fallback_keeps_the_existing_mandatory_body_guard(self):
        from research_harness.direction_sources import has_attachment
        from research_harness.review_excerpts import read_sent_packet
        self.oversized(count=0, limit=100000)
        self.case.expected['computations'] = [dict(id='computation-' + name,
            artifact=self.case.artifacts.put(('computation-' + name + '\n').encode() * 3000, 'text/plain'))
            for name in ('A', 'B')]
        records = self.case.read_records()
        with transport.text_only_delivery(True):
            packet = protocol.build_packet(records, self.case.artifacts, None, 'result', 'first')
        assignment = self.assign('omitted-body-fallback')
        route = records['review_route']['route']
        prompt = protocol.canonical_prompt('result', orchestration='problem_creation')
        ordinary = transport.request_body(route, prompt['text'], packet, assignment['history'])
        self.assertLessEqual(parts._measure(route, ordinary), transport.local_input_limit(route))
        self.assertFalse(parts.preserves_ordinary_packet(self.case.artifacts, packet, ordinary))
        legacy = load_legacy_parts().prepare_packet(records, self.case.artifacts,
            dict(id=assignment['id'], role='result', context={}), route, packet, prompt, assignment['history'])
        self.assertEqual(legacy, packet)
        prepared = self.packet(assignment)
        self.assertNotIn('staged_review', prepared)
        self.assertEqual(prepared[KEY]['stage'], 'part')
        sent = read_sent_packet(self.request(assignment))
        self.assertEqual(len(sent['problem_evidence']['computations']), 1)
        self.assertTrue(has_attachment(self.case.artifacts, sent,
                                      sent['problem_evidence']['computations'][0]['artifact']))
