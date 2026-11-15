"""C8 r4.2: stored plans freeze pending captures without giving delivery credit.

Source: 20261113110022-every-open-record-and-review-capacity, ruling 30.
"""

from contextlib import ExitStack
import json
import unittest
from unittest.mock import patch

from unbound_packet_fixtures import open_current_stopped_case, open_stored_case
import test_openness_capture_binding as binding_fixtures
from research_harness.capture_selection import bind_packet_selections
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
            prepare = reading.prepare_packet
            if reading.is_openness_packet(native):
                # Rebuild a stored first turn with its released builder. R71 now
                # refuses this old packet when starting a new chain.
                records = bind_packet_selections(records, native['scientific_context'],
                                                 artifacts=case.artifacts, review=assignment)
                namespace = dict(vars(reading))
                exec(compile(binding_fixtures.BUILDERS_PATH.read_bytes(),
                             str(binding_fixtures.BUILDERS_PATH), 'exec'), namespace)
                prepare = namespace['prepare_packet']
            rebuilt, _ = prepare(records, case.artifacts, assignment, route, ordinary)
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

    def finish_current_chain(self, case, metadata, first_receipt, *, expect_first=False):
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
                expected_output = case.build_native_result(packet)
                case.invoke(identifier, expected_output)
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
            self.assertEqual(state['output'], expected_output)
        else:
            saved = case.mutate(case.load_problem_module().record_openness, {'assignment_id': final['id']})['result']
            self.assertEqual(saved['reading_turns'], receipts)
            self.assertEqual(openness.assess_openness(case.store.snapshot()['records'], case.artifacts), [])
        return identifiers

    def assert_obsolete_route_refuses_new_call(self, case, metadata, *, uncalled=False):
        from research_harness.review_capacity_evidence import describe_capacity_key
        before = case.store.snapshot()
        route = before['records']['review_route']['route']
        self.assertIsNone(describe_capacity_key(route))
        with patch.object(transport, 'send_request') as sender:
            with self.assertRaises(ResearchError) as caught:
                if uncalled:
                    protocol.invoke_assignment(case.store, dict(id='obsolete-runtime-call', assignment_id='turn-1'),
                        expected_revision=case.store.revision, request_id='obsolete-runtime-call', credential='test-only')
                else:
                    case.assign('turn-2', metadata['native'], prior='turn-1')
            sender.assert_not_called()
        self.assertEqual(caught.exception.code,
                         'review_route_runtime_stale' if uncalled else 'review_route_capacity_below_floor')
        if not uncalled:
            self.assertEqual(caught.exception.details['measure']['token_limit'], 0)
        self.assertEqual(case.store.snapshot(), before)

    def test_archived_stopped_plans_keep_pending_capture_and_delivery_credit(self):
        for version, variant in self.variants:
            with self.subTest(protocol=version, variant=variant), ExitStack() as stack:
                case, metadata = open_stored_case(self, version, 'stopped-' + variant + '-observed')
                stack.callback(case.doCleanups)
                self.assert_stored_prefix(case, metadata, observed=True)
                self.assert_obsolete_route_refuses_new_call(case, metadata)

    def test_later_choice_keeps_the_archived_capture_named_only_in_remaining(self):
        for version, variant in self.variants:
            with self.subTest(protocol=version, variant=variant), ExitStack() as stack:
                case, metadata = open_stored_case(self, version, 'stopped-' + variant + '-observed')
                stack.callback(case.doCleanups)
                case.mutate(select_main_capture, dict(version_id=metadata['work'],
                    source_id=metadata['first']['source_id'], reason='A later choice cannot rewrite this chain.'))
                self.assert_stored_prefix(case, metadata, observed=True)
                self.assert_obsolete_route_refuses_new_call(case, metadata)

    def test_an_uncalled_archived_turn_keeps_its_plan_and_refuses_an_obsolete_runtime(self):
        for version in (4, 5, 'math'):
            with self.subTest(protocol=version), ExitStack() as stack:
                case, metadata = open_stored_case(self, version, 'stopped-chosen-uncalled')
                stack.callback(case.doCleanups)
                self.assert_stored_prefix(case, metadata, observed=False)
                packet = case.artifacts.read(case.store.snapshot()['records']['review_assignment']['turn-1']['packet'])
                self.assert_obsolete_route_refuses_new_call(case, metadata, uncalled=True)
                self.assertEqual(case.artifacts.read(case.store.snapshot()['records']['review_assignment']['turn-1']['packet']),
                                 packet)

    def test_archived_case_two_keeps_v5_reexport_and_v4_obsolete_capacity_refusals(self):
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
                    self.assert_obsolete_route_refuses_new_call(case, metadata)

    def test_current_keyed_stopped_prefixes_continue_and_deliver_the_chosen_capture(self):
        for version, variant in self.variants:
            with self.subTest(protocol=version, variant=variant), ExitStack() as stack:
                case, metadata = open_current_stopped_case(self, version, both=variant == 'both')
                stack.callback(case.doCleanups)
                records = case.store.snapshot()['records']
                assignment = records['review_assignment']['turn-1']
                first = case.read_packet('turn-1')
                native = json.loads(case.artifacts.read(metadata['native']))
                rows = reading.list_reading_rows(records, case.artifacts, native, review=assignment)
                manifest = (reading._plan_search_delivery(case.artifacts, native)['manifest']
                            if version == 5 else None)
                delivery_rows = reading.list_delivery_rows(rows, manifest) if manifest is not None else rows
                receipt = reading._observe_turns(records, case.artifacts, [assignment], delivery_rows,
                    reading._compute_spans(case.artifacts, delivery_rows), manifest)[0]['turn']
                self.assertFalse(any(row.get('version_id') == metadata['work']
                                     for row in first['reading_turn']['reading_plan']['delivered_now']))
                self.finish_current_chain(case, metadata, receipt)

    def test_current_keyed_prefixes_keep_the_pending_capture_after_a_later_choice(self):
        for version, variant in self.variants:
            with self.subTest(protocol=version, variant=variant), ExitStack() as stack:
                case, metadata = open_current_stopped_case(self, version, both=variant == 'both')
                stack.callback(case.doCleanups)
                before = case.store.snapshot()['records']
                case.mutate(select_main_capture, dict(version_id=metadata['work'],
                    source_id=metadata['first']['source_id'], reason='A later choice cannot rewrite this chain.'))
                self.finish_current_chain(case, metadata, None)
                records = case.store.snapshot()['records']
                self.assertEqual(records['review_assignment']['turn-1'], before['review_assignment']['turn-1'])
                self.assertEqual(records['review_attempt']['attempt-turn-1'], before['review_attempt']['attempt-turn-1'])

    def test_current_keyed_uncalled_prefixes_keep_their_packets_and_continue(self):
        for version in (4, 5, 'math'):
            with self.subTest(protocol=version), ExitStack() as stack:
                case, metadata = open_current_stopped_case(self, version, observed=False)
                stack.callback(case.doCleanups)
                assignment = case.store.snapshot()['records']['review_assignment']['turn-1']
                packet = case.artifacts.read(assignment['packet'])
                self.finish_current_chain(case, metadata, None)
                self.assertEqual(case.artifacts.read(assignment['packet']), packet)
