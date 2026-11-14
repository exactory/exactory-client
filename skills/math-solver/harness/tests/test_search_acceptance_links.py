"""Accepted results retain the exact observed review that accepted their proof."""

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from research_harness.errors import ResearchError
from research_harness.record_check import check_authentication
from research_harness.storage import Store as CommonStore
from search_controller.errors import SearchError
from tests.native_reviewer_support import observe_review
from tests.search_fixtures import digest
from tests.test_search_cli import SearchCLIWorkspace
from tests import test_search_deliver_attempt as deliveries


class AcceptanceLinkTests(SearchCLIWorkspace, unittest.TestCase):
    artifact = deliveries.DeliverAttemptTests.artifact
    external_checkpoint = deliveries.DeliverAttemptTests.external_checkpoint
    result_review = deliveries.DeliverAttemptTests.result_review
    unresolved_objective = deliveries.DeliverAttemptTests.unresolved_objective

    def read_links(self):
        return CommonStore(self.root).snapshot()['records'].get('native_acceptance_review', {})

    def test_two_approvals_link_each_acceptance_to_its_own_review(self):
        controller = self.unresolved_objective()
        checkpoint = controller.status()['checkpoints']['checkpoint-000001']
        other = self.result_review(checkpoint, checkpoint['claim'])
        other['reviewer']['actor_id'] = 'another-result-reviewer'
        observed = observe_review(controller, other, checkpoint)
        self.search('accept', dict(obligation_id='obligation-000002', outcome='reduction',
                                  dependency_ids=[], route_bindings=[], review=observed, inputs=[]),
                    target=checkpoint['id'], revision=7)
        state = controller.status()
        before = controller.store.read()
        controller.command('deliver-attempt', {}, state['revision'], 'deliver-two-approvals')
        links = self.read_links()
        self.assertEqual(len(links), 2)
        for acceptance in state['acceptances'].values():
            key = dict(attack_root=str(self.root.resolve()), objective_id=state['objective_id'],
                       acceptance_digest=digest(acceptance))
            assignment_id = acceptance['review']['reviewer']['attestation_id'].split(':', 1)[1]
            self.assertEqual(links[digest(key)], dict(key, acceptance_id=acceptance['id'],
                checkpoint_id=checkpoint['id'], evidence_digests=acceptance['audit']['evidence_digests'],
                review=dict(assignment_id=assignment_id, final_assignment_id=assignment_id,
                            attempt_id=assignment_id + '-attempt', output_digest=digest(acceptance['review'])),
                provenance=controller.provenance('deliver-attempt')))
        self.assertEqual(controller.store.read(), before)
        snapshot = CommonStore(self.root).snapshot()
        controller.command('deliver-attempt', {}, state['revision'], 'repeat-two-approvals')
        self.assertEqual(CommonStore(self.root).snapshot(), snapshot)
        # An old review without an observed assignment remains accepted history, with no invented link.
        from search_controller.integration import internal_operation
        legacy = {name: copy.deepcopy(value) for name, value in state['acceptances']['acceptance-000001'].items()
                  if name not in ('id', 'status', 'route_content_digests', 'progress_key', 'progress_eligible')}
        legacy['review']['reviewer']['attestation_id'] = 'historical-review'

        def record_historical_acceptance(current, content):
            content.put_blob(legacy['review'])
            return [dict(kind='result_accepted', payload=dict(acceptance=legacy, digest=content.put_blob(legacy)))]

        internal_operation(controller, 'accept', 'legacy-acceptance', record_historical_acceptance)
        current = controller.status()
        self.assertEqual(current['acceptances']['acceptance-000003']['status'], 'accepted')
        controller.command('deliver-attempt', {}, current['revision'], 'deliver-historical-acceptance')
        self.assertEqual(self.read_links(), links)
        artifact = self.root / '.search/artifacts' / controller.store.put_artifact((self.root / 'proof.md').read_bytes())
        artifact.chmod(0o644)
        artifact.write_text('Invalidate the accepted proof')
        controller.command('audit', {}, current['revision'], 'invalidate-acceptances')
        current = controller.status()
        self.assertTrue(all(item['status'] == 'invalidated' for item in current['acceptances'].values()))
        controller.command('deliver-attempt', {}, current['revision'], 'deliver-invalidated-acceptances')
        self.assertEqual(self.read_links(), links)

    def test_stored_attempt_digest_survives_link_recovery_without_moving_its_pointer(self):
        controller = self.unresolved_objective()
        common = CommonStore(self.root)
        fixture = Path(__file__).resolve().parents[4] / 'tests/fixtures/native_attempt_before_acceptance_links.json'
        summary = json.loads(fixture.read_text(encoding='utf-8'))['summary']
        self.assertEqual(digest(summary), 'e3d273fec1ded59b7b876ff1ced31cb2a42a46fa0ee3fab50d1bf9086c44e542')
        summary['attack_root'] = str(self.root.resolve())
        identity = digest(summary)
        attempt = dict(summary, provenance=controller.provenance('deliver-attempt'),
                       recorded_revision=common.revision + 1)
        key = dict(attack_root=str(self.root.resolve()), objective_id='objective-000001')
        pointer = dict(key, native_attempt_digest=identity, recorded_revision=common.revision + 1)

        def store_legacy_attempt(transaction):
            transaction.put('native_attempt', identity, attempt)
            transaction.put('native_attempt_current', digest(key), pointer)
            return {}

        common.mutate('native_math.deliver_attempt', dict(summary, provenance=controller.provenance('deliver-attempt')),
                      store_legacy_attempt, expected_revision=common.revision, request_id='native-attempt:' + identity)
        before = common.snapshot()
        result = controller.command('deliver-attempt', {}, 7, 'recover-links')
        after = common.snapshot()
        self.assertEqual(result['native_attempt_digest'], identity)
        for kind in ('native_attempt', 'native_attempt_current'):
            self.assertEqual(after['records'][kind], before['records'][kind])
        self.assertEqual(len(self.read_links()), 1)
        self.assertEqual(after['revision'], before['revision'] + 1)
        controller.command('deliver-attempt', {}, 7, 'repeat-recovery')
        self.assertEqual(common.snapshot(), after)

    def test_corrupt_proof_blocks_link_recovery_and_preserves_the_stored_attempt(self):
        controller = self.unresolved_objective()
        # Simulate the store before this release by leaving the delivered attempt without its link.
        from search_controller.attempt_delivery import build_attempt_summary
        summary = build_attempt_summary(controller, controller.status())
        common = CommonStore(self.root)

        def store_attempt(transaction):
            transaction.put('native_attempt', digest(summary),
                            dict(summary, provenance=controller.provenance('deliver-attempt')))
            return {}

        common.mutate('fixture.old_attempt', {}, store_attempt, expected_revision=common.revision,
                      request_id='old-attempt')
        artifact = self.root / '.search/artifacts' / controller.store.put_artifact((self.root / 'proof.md').read_bytes())
        original = artifact.read_bytes()
        artifact.chmod(0o644)
        artifact.write_text('Changed after acceptance')
        before = common.snapshot()
        with self.assertRaises(SearchError) as caught:
            controller.command('deliver-attempt', {}, 7, 'refuse-recovery')
        self.assertEqual(caught.exception.code, 'audit_failed')
        self.assertEqual(common.snapshot(), before)
        artifact.write_bytes(original)
        controller.command('deliver-attempt', {}, 7, 'recover-after-restoring-proof')
        self.assertEqual(len(self.read_links()), 1)

    def test_conflicting_link_refuses_the_whole_delivery(self):
        controller = self.unresolved_objective()
        acceptance = controller.status()['acceptances']['acceptance-000001']
        key = dict(attack_root=str(self.root.resolve()), objective_id='objective-000001',
                   acceptance_digest=digest(acceptance))
        common = CommonStore(self.root)

        def store_conflicting_link(transaction):
            transaction.put('native_acceptance_review', digest(key), dict(key, acceptance_id='wrong'))
            return {}

        common.mutate('fixture.conflicting_link', {}, store_conflicting_link,
                      expected_revision=common.revision, request_id='conflict')
        before = common.snapshot()
        with self.assertRaises(SearchError) as caught:
            controller.command('deliver-attempt', {}, 7, 'conflicting-delivery')
        self.assertEqual(caught.exception.code, 'record_conflict')
        self.assertEqual(common.snapshot(), before)

    def test_qualification_authenticates_the_link_and_refuses_changed_references(self):
        controller = self.unresolved_objective()
        controller.command('deliver-attempt', {}, 7, 'deliver-for-check')
        common = CommonStore(self.root)
        result = check_authentication(common)
        links = self.read_links()
        self.assertEqual(result['acceptance_reviews'], {key: link['review'] for key, link in links.items()})
        key = next(iter(links))
        snapshot = copy.deepcopy(common.snapshot())
        snapshot['records']['native_acceptance_review']['0' * 64] = snapshot['records']['native_acceptance_review'].pop(key)
        with patch.object(common, 'snapshot', return_value=snapshot):
            with self.assertRaises(ResearchError) as caught:
                check_authentication(common)
        self.assertEqual(caught.exception.code, 'record_acceptance_review_changed')
        for field, changed in (('assignment_id', 'absent'), ('final_assignment_id', 'absent'),
                               ('attempt_id', 'absent'), ('output_digest', '0' * 64)):
            with self.subTest(field=field):
                snapshot = copy.deepcopy(common.snapshot())
                snapshot['records']['native_acceptance_review'][key]['review'][field] = changed
                with patch.object(common, 'snapshot', return_value=snapshot):
                    with self.assertRaises(ResearchError) as caught:
                        check_authentication(common)
                self.assertEqual(caught.exception.code, 'record_acceptance_review_changed')


if __name__ == '__main__':
    unittest.main()
