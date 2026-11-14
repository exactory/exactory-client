"""C8 r4.2: stored plans freeze pending captures without giving delivery credit.

Source: 20261113110022-every-open-record-and-review-capacity, ruling 30.
"""

from contextlib import ExitStack
import json
import unittest
from unittest.mock import patch

from unbound_packet_fixtures import open_stored_case
from research_harness import openness_reading as reading, problem_openness as openness
from research_harness import review_excerpts, review_protocol as protocol, review_transport as transport
from research_harness.capture_selection import select_main_capture
from research_harness.errors import ResearchError


class UnboundStoppedPacketTests(unittest.TestCase):
    variants = ((4, 'chosen'), (4, 'both'), (5, 'chosen'), (5, 'both'), ('math', 'chosen'))

    def describe_units(self, rows):
        return [{key: value for key, value in row.items() if key not in ('artifact', 'artifact_descriptor')}
                for row in rows]

    def assert_stored_prefix(self, case, metadata, *, observed):
        before = case.store.snapshot()
        records = before['records']
        assignment = records['review_assignment']['turn-1']
        native = json.loads(case.artifacts.read(metadata['native']))
        rows = reading.list_reading_rows(records, case.artifacts, native, review=assignment)
        self.assertEqual([row['artifact'] for row in rows if row.get('version_id') == metadata['work']],
                         [metadata['compact']['text']])
        packet_bytes = case.artifacts.read(assignment['packet'])
        route = records['review_route']['route']
        with transport.text_only_delivery(True):
            ordinary = protocol.build_packet(records, case.artifacts, None, 'native_math', assignment['reviewer_id'],
                                             native_packet=metadata['native'])
            rebuilt, _ = reading.prepare_packet(records, case.artifacts, assignment, route, ordinary)
        self.assertEqual(json.dumps(rebuilt, sort_keys=True, ensure_ascii=False).encode(), packet_bytes)
        manifest = (reading._plan_search_delivery(case.artifacts, native)['manifest']
                    if metadata['protocol'] == 5 else None)
        delivery_rows = reading.list_delivery_rows(rows, manifest) if manifest is not None else rows
        spans = reading._compute_spans(case.artifacts, delivery_rows)
        if observed:
            observations = reading._observe_turns(records, case.artifacts, [assignment], delivery_rows, spans, manifest)
            self.assertEqual(observations[0]['turn']['delivered_units'], metadata['delivered_units'])
            self.assertFalse(any(row.get('version_id') == metadata['work']
                                 for row in observations[0]['turn']['delivered_units']))
            if metadata['protocol'] == 5:
                coverage = reading.verify_problem_coverage(records, case.artifacts, assignment)
                self.assertEqual(coverage['turns'], [observations[0]['turn']])
            first_receipt = observations[0]['turn']
        else:
            with self.assertRaises(ResearchError) as caught:
                reading._observe_turns(records, case.artifacts, [assignment], delivery_rows, spans, manifest)
            self.assertEqual(caught.exception.code, 'review_context_unverified')
            first_receipt = None
        self.assertEqual(case.store.snapshot(), before)
        return first_receipt

    def finish_stored_chain(self, case, metadata, first_receipt, *, expect_first=False):
        if first_receipt is None and not protocol.assignment_state(
                case.store.snapshot()['records'], case.artifacts, 'turn-1')['ready']:
            case.invoke('turn-1', case.build_reading_response(case.read_packet('turn-1'), excerpts=[]))
        prior_plan = case.read_packet('turn-1')['reading_turn']['reading_plan']
        identifiers = ['turn-1']
        case.assign('turn-2', metadata['native'], prior='turn-1')
        identifiers.append('turn-2')
        next_packet = case.read_packet('turn-2')
        plan = next_packet['reading_turn']['reading_plan']
        self.assertEqual(self.describe_units(plan['delivered_before']), self.describe_units(prior_plan['delivered_now']))
        if not expect_first:
            self.assertEqual(self.describe_units(plan['delivered_now'] + plan['remaining']),
                             self.describe_units(prior_plan['remaining']))
        expected = metadata['first'] if expect_first else metadata['compact']
        b_rows = [row for row in plan['delivered_now'] if row.get('version_id') == metadata['work']]
        self.assertTrue(b_rows)
        self.assertEqual(b_rows[0]['text_sha256'], expected['text']['sha256'])
        case.invoke('turn-2', case.build_reading_response(next_packet, excerpts=[]))
        _request, sent = case.read_sent_packet('turn-2')
        self.assertIn(b_rows[0]['artifact']['sha256'], review_excerpts.find_complete_bodies(sent))
        while True:
            identifier = 'turn-%d' % (len(identifiers) + 1)
            case.assign(identifier, metadata['native'], prior=identifiers[-1])
            identifiers.append(identifier)
            packet = case.read_packet(identifier)
            if reading.find_stage(packet) == 'final':
                case.invoke(identifier, case.build_native_result(packet))
                break
            case.invoke(identifier, case.build_reading_response(packet, excerpts=[]))
            self.assertLess(len(identifiers), 8)
        records = case.store.snapshot()['records']
        final = records['review_assignment'][identifiers[-1]]
        receipts = reading.verify_chain(records, case.artifacts, final)
        if first_receipt is not None:
            self.assertEqual(receipts[0], first_receipt)
        delivered = [row for turn in receipts[:-1] for row in turn['delivered_units']
                     if row.get('version_id') == metadata['work']]
        self.assertEqual([row['text_sha256'] for row in delivered], [expected['text']['sha256']])
        if metadata['protocol'] == 'math':
            state = protocol.assignment_state(records, case.artifacts, final['id'])
            self.assertTrue(state['ready'])
            self.assertEqual(state['output'], metadata['review'])
        else:
            saved = case.mutate(case.load_problem_module().record_openness, {'assignment_id': final['id']})['result']
            self.assertEqual(saved['reading_turns'], receipts)
            self.assertEqual(openness.assess_openness(case.store.snapshot()['records'], case.artifacts), [])
        return identifiers

    def test_observed_stopped_plans_keep_pending_capture_and_delivery_credit(self):
        for version, variant in self.variants:
            with self.subTest(protocol=version, variant=variant), ExitStack() as stack:
                case, metadata = open_stored_case(self, version, 'stopped-' + variant + '-observed')
                stack.callback(case.doCleanups)
                receipt = self.assert_stored_prefix(case, metadata, observed=True)
                self.finish_stored_chain(case, metadata, receipt)

    def test_later_choice_does_not_change_a_capture_still_named_only_in_remaining(self):
        for version, variant in self.variants:
            with self.subTest(protocol=version, variant=variant), ExitStack() as stack:
                case, metadata = open_stored_case(self, version, 'stopped-' + variant + '-observed')
                stack.callback(case.doCleanups)
                case.mutate(select_main_capture, dict(version_id=metadata['work'],
                    source_id=metadata['first']['source_id'], reason='A later choice cannot rewrite this chain.'))
                receipt = self.assert_stored_prefix(case, metadata, observed=True)
                self.finish_stored_chain(case, metadata, receipt)

    def test_an_uncalled_stored_turn_keeps_its_plan_and_then_continues(self):
        for version in (4, 5, 'math'):
            with self.subTest(protocol=version), ExitStack() as stack:
                case, metadata = open_stored_case(self, version, 'stopped-chosen-uncalled')
                stack.callback(case.doCleanups)
                self.assert_stored_prefix(case, metadata, observed=False)
                packet = case.artifacts.read(case.store.snapshot()['records']['review_assignment']['turn-1']['packet'])
                self.finish_stored_chain(case, metadata, None)
                self.assertEqual(case.artifacts.read(case.store.snapshot()['records']['review_assignment']['turn-1']['packet']),
                                 packet)

    def test_a_stopped_case_two_is_reexported_under_v5_and_reads_first_capture_under_v4(self):
        for version in (4, 5):
            with self.subTest(protocol=version), ExitStack() as stack:
                case, metadata = open_stored_case(self, version, 'stopped-choice-observed')
                stack.callback(case.doCleanups)
                if version == 5:
                    before = case.store.snapshot()
                    with patch.object(transport, 'send_request') as sender:
                        with self.assertRaises(ResearchError) as caught:
                            case.assign('turn-2', metadata['native'], prior='turn-1')
                        self.assertEqual(caught.exception.code, 'openness_reading_incomplete')
                        self.assertEqual(caught.exception.details['handoff'], 'export')
                        sender.assert_not_called()
                    self.assertEqual(case.store.snapshot(), before)
                else:
                    self.finish_stored_chain(case, metadata, None, expect_first=True)
