"""Remote finishing writes retain confirmed replies without repeating the remote action."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from research_harness import remote
from research_harness.errors import ResearchError
from research_harness.storage import Store


class RemoteFinishingTests(unittest.TestCase):
    def test_checked_absence_survives_busy_and_stale_discard_writes(self):
        from research_harness import zenodo
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory), create=True)
            remote.begin_intent(store, 'publication', {'base_url': 'https://example.org', 'new_version': False},
                                expected_revision=0, request_id='remote')
            def lose_response():
                raise ResearchError('fixture_failed', 'Lost response')
            with self.assertRaises(ResearchError):
                remote.remote_step(store, 'remote', 'create', {}, lose_response)
            reads = []
            def read_remote(method, url):
                reads.append((method, url))
                return []
            mutate, refusals = store.mutate, []
            def refuse(operation, *args, **kwargs):
                if operation == 'remote.discard' and len(refusals) < 2:
                    code = 'store_busy' if not refusals else 'stale_revision'
                    refusals.append(code)
                    if code == 'stale_revision':
                        mutate('fixture.writer', {}, lambda tx: tx.put('fixture', 'writer', {}),
                               expected_revision=store.revision, request_id='writer')
                    raise ResearchError(code, 'Concurrent writer')
                return mutate(operation, *args, **kwargs)
            with patch.object(store, 'mutate', side_effect=refuse), \
                    patch('research_harness.storage._BUSY_TIMEOUT_SECONDS', 0.001):
                intent = zenodo.reconcile_pending(store, 'remote', read_remote)
            self.assertIsNone(intent['pending'])
            self.assertEqual(intent['status'], 'prepared')
            self.assertEqual(intent['discarded'], [{'name': 'create', 'observation': {
                'kind': 'remote_read', 'listing': 'complete', 'notes': 'Exactory publication intent remote'}}])
            self.assertEqual(store.snapshot()['records']['fixture']['writer'], {})
            self.assertEqual(reads, [('GET', 'https://example.org/deposit/depositions?page=1&size=100')])

    def test_submission_and_publication_readback_keep_observations_during_busy_write(self):
        from research_harness import submission, zenodo
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory), create=True)
            mutate, refused = store.mutate, set()
            operations = {'submission.observe', 'publication.read_back'}
            def refuse(operation, *args, **kwargs):
                if operation in operations and operation not in refused:
                    refused.add(operation)
                    raise ResearchError('store_busy', 'Concurrent writer')
                return mutate(operation, *args, **kwargs)
            receipt = {'environment': 'sandbox', 'deposition_id': 123, 'draft_url': 'https://example.org/123'}
            with patch.object(store, 'mutate', side_effect=refuse), \
                    patch('research_harness.storage._BUSY_TIMEOUT_SECONDS', 0.001):
                observed = submission._observation(store, 'submit', 'matched', {'verificationId': 'confirmed'})
                published = zenodo._record_read_back_publication(store, 'publish', receipt, {'doi': '10.1234/example'})
            self.assertEqual(observed['result']['response'], {'verificationId': 'confirmed'})
            self.assertEqual(published['doi'], '10.1234/example')
            self.assertEqual(refused, operations)

    def test_confirmed_remote_response_survives_busy_and_stale_finishing_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory), create=True)
            remote.begin_intent(store, 'fixture', {'id': 'item'}, expected_revision=0, request_id='remote')
            mutate, refusals = store.mutate, []
            def refuse(operation, *args, **kwargs):
                if operation in ('remote.resolve', 'remote.finish') and operation not in refusals:
                    refusals.append(operation)
                    code = 'store_busy' if operation == 'remote.resolve' else 'stale_revision'
                    if code == 'stale_revision':
                        mutate('fixture.writer', {}, lambda tx: tx.put('fixture', 'writer', {}),
                               expected_revision=store.revision, request_id='writer')
                    raise ResearchError(code, 'Concurrent writer')
                return mutate(operation, *args, **kwargs)
            perform = Mock(return_value={'id': 'confirmed'})
            with patch.object(store, 'mutate', side_effect=refuse), \
                    patch('research_harness.storage._BUSY_TIMEOUT_SECONDS', 0.001):
                response = remote.remote_step(store, 'remote', 'create', {}, perform)
                receipt = remote.finish_intent(store, 'remote', 'fixture_receipt', response)
            self.assertEqual(receipt, {'id': 'confirmed'})
            self.assertEqual(perform.call_count, 1)
            self.assertEqual(refusals, ['remote.resolve', 'remote.finish'])
            self.assertEqual(remote.get_intent(store, 'remote')['status'], 'complete')
            self.assertEqual(remote.remote_step(store, 'remote', 'create', {}, perform), receipt)
            self.assertEqual(perform.call_count, 1)
