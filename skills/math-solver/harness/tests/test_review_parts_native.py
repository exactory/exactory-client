"""Native acceptance consumes only an authenticated complete parts final."""

import copy
import json
import unittest
from unittest.mock import patch

from tests.openness_chain_support import MathOpennessChainCase
from codex_route_fixtures import limit_codex_input
from research_harness import review_parts as parts, review_protocol as protocol
from research_harness import review_transport as transport
from research_harness.artifacts import ArtifactStore
from research_harness.storage import Store
from review_parts_fixtures import make_output
from search_controller.errors import SearchError
from search_controller.reviewer import require_review
from search_controller.service import Controller
from search_controller.storage import ContentSink


class NativePartsAcceptanceTests(MathOpennessChainCase, unittest.TestCase):
    def test_single_output_replacement_is_consumed_and_acceptance_link_is_audited(self):
        from research_harness.review_continuation import plan_next
        from research_harness.record_check import check_authentication
        self.build_native_fixture()
        limit_codex_input(self, 2000000)
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 2000000), \
                patch.object(parts, 'prepare_packet', side_effect=lambda records, artifacts, value, route,
                             packet, prompt, history, **options: packet):
            original = self.assign_turn('turn-1')
            def over_cap(route, request, credential):
                response = self.respond(route, request, {})
                response['status'] = 'incomplete'
                response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
                return response
            with patch.object(transport, 'send_request', side_effect=over_cap):
                protocol.invoke_assignment(self.store, dict(id='over-cap', assignment_id=original['id']),
                    expected_revision=self.store.revision, request_id='over-cap')
        plan = plan_next(self.store.snapshot()['records'], self.artifacts, original['id'])
        self.assertEqual(plan['state'], 'replace', plan)
        assignment = protocol.record_assignment(self.store, plan['payload'],
            expected_revision=self.store.revision, request_id='replace-single')['result']
        for number in range(40):
            packet = self.read_turn(assignment['id'])
            self.invoke(assignment['id'], self.review if parts.find_kind(packet) == 'final' else make_output(packet))
            if parts.find_kind(packet) == 'final':
                break
            assignment = self.assign_turn('replacement-' + str(number), assignment['id'])
        else:
            self.fail('The native replacement series did not finish')
        checked = self.check_review()
        self.assertEqual(checked['attempt_id'], 'attempt-' + assignment['id'])
        self.accept_result()
        self.controller.command('deliver-attempt', {}, self.controller.status()['revision'], 'deliver-single-series')
        links = list(check_authentication(self.store)['acceptance_reviews'].values())
        self.assertEqual(links[0]['assignment_id'], original['id'])
        self.assertEqual(links[0]['final_assignment_id'], assignment['id'])

    def test_attempt_links_the_attested_parts_series_to_its_observed_final(self):
        from research_harness.evidence import digest
        from research_harness.record_check import check_authentication
        self.build_native_fixture()
        turns = self.finish_native_series()
        self.accept_result()
        state = self.controller.status()
        self.controller.command('deliver-attempt', {}, state['revision'], 'deliver-parts-result')
        links = list(self.store.snapshot()['records'].get('native_acceptance_review', {}).values())
        self.assertEqual(len(links), 1)
        expected = dict(assignment_id=turns[0], final_assignment_id=turns[-1],
                        attempt_id='attempt-' + turns[-1], output_digest=digest(self.review))
        self.assertEqual(links[0]['review'], expected)
        self.assertEqual(list(check_authentication(self.store)['acceptance_reviews'].values()), [expected])

    def build_native_fixture(self):
        self.initialize()
        specification = self.external_checkpoint()
        study = self.artifact('scope.md', 'The exact assumptions and endpoints. λ\n' * 5000)
        specification['checkpoint']['origin']['study_digest'] = study['digest']
        specification['inputs'][2] = study
        self.search('checkpoint', specification, revision=1)
        self.controller = Controller(self.root)
        self.subject = self.controller.status()['checkpoints']['checkpoint-000001']
        self.review = self.result_review(self.subject, self.subject['claim'])
        self.review['reviewer'] = dict(source='host', actor_id='openness-reviewer',
                                     attestation_id='review-assignment:turn-1')
        self.exported = self.controller.command('review-packet', dict(kind='result', subject=self.subject,
            claim_digest=self.review['claim_digest'], reviewer=self.review['reviewer'], inputs=[]), None, None)
        self.store = Store(self.exported['workspace'])
        self.artifacts = ArtifactStore(self.store.root)
        self.record_route('codex_cli_v1')
        ordinary = self.read_ordinary_packet()
        route = self.store.snapshot()['records']['review_route']['route']
        request = transport.request_body(route, protocol.canonical_prompt('native_math')['text'], ordinary)
        limit_codex_input(self, self.measure_request(request) // 2)

    def finish_native_series(self):
        assignment = self.assign_turn('turn-1')
        identifiers = []
        for number in range(40):
            identifiers.append(assignment['id'])
            packet = self.read_turn(assignment['id'])
            self.assertTrue('review_parts' in packet, 'An oversized native result needs a parts series')
            output = self.review if packet['review_parts']['kind'] == 'final' else make_output(packet)
            self.invoke(assignment['id'], output)
            if packet['review_parts']['kind'] == 'final':
                return identifiers
            assignment = self.assign_turn('turn-' + str(number + 2), assignment['id'])
        self.fail('Native series did not terminate')

    def check_review(self, **options):
        state = self.controller.status()
        return require_review(self.root, state, ContentSink(self.controller.store), self.review,
                              subject=self.subject, new=True, **options)

    def test_complete_native_series_accepts_and_records_receipts_for_audit(self):
        self.build_native_fixture()
        turns = self.finish_native_series()
        checked = self.check_review()
        self.assertEqual(checked['attempt_id'], 'attempt-' + turns[-1])
        receipts = checked['reading_turns']
        self.assertEqual([receipt['assignment_id'] for receipt in receipts], turns)
        self.assertTrue(all(set(receipt) == {'assignment_id', 'attempt_id', 'delivered_units'}
                            for receipt in receipts))
        self.assertTrue(all(receipt['delivered_units'] == [] for receipt in receipts))
        self.accept_result()
        state = self.controller.status(full_audit=True)
        accepted = state['acceptances']['acceptance-000001']
        self.assertEqual(accepted['reading_turns'], receipts)
        self.assertEqual(state['freshness']['status'], 'fresh')
        changed = copy.deepcopy(accepted)
        changed['reading_turns'][0]['attempt_id'] = 'different-attempt'
        with self.assertRaises(SearchError) as caught:
            self.check_review(record=changed)
        self.assertEqual(caught.exception.code, 'review_parts_incomplete')

    def test_missing_intermediate_attempt_refuses_the_native_final(self):
        self.build_native_fixture()
        turns = self.finish_native_series()
        records = self.store.snapshot()['records']
        records['review_attempt'] = {key: value for key, value in records['review_attempt'].items()
                                    if value['assignment_id'] != turns[1]}
        with patch.object(Store, 'snapshot', return_value=dict(records=records)):
            with self.assertRaises(SearchError) as caught:
                self.check_review()
        self.assertEqual(caught.exception.code, 'review_parts_incomplete')

    def test_pending_series_refuses_the_claimed_native_final(self):
        self.build_native_fixture()
        self.assign_turn('turn-1')
        packet = self.read_turn('turn-1')
        self.assertTrue('review_parts' in packet, 'The native read turn must use parts')
        self.invoke('turn-1', make_output(packet))
        with self.assertRaises(SearchError) as caught:
            self.check_review()
        self.assertEqual((caught.exception.code, caught.exception.details['cause']),
                         ('review_context_unverified', 'review_parts_incomplete'))

    def test_changed_export_or_reviewer_cannot_reuse_a_complete_series(self):
        self.build_native_fixture()
        turns = self.finish_native_series()
        original = self.store.snapshot()['records']
        for field in ('reviewer', 'native_packet'):
            records = copy.deepcopy(original)
            if field == 'reviewer':
                records['review_assignment'][turns[-1]]['reviewer_id'] = 'another-reviewer'
            else:
                assignment = records['review_assignment'][turns[-1]]
                native = json.loads(self.artifacts.read(assignment['context']['native_packet']))
                native['scientific_context']['scope'] = 'changed'
                assignment['context']['native_packet'] = self.artifacts.put(json.dumps(native).encode(),
                                                                          'application/json')
            with self.subTest(field=field), patch.object(Store, 'snapshot', return_value=dict(records=records)):
                with self.assertRaises(SearchError):
                    self.check_review()
