"""Payload files use one workspace boundary before command preparation."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from test_research_check_mode import CheckCommandMixin
from research_harness import cli, operations
from research_harness.artifacts import ArtifactStore
from research_harness.evidence import digest
from research_harness.storage import Store


ROOT = Path(__file__).resolve().parents[1]


class PayloadFilePathTests(CheckCommandMixin, unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'study'
        self.store = Store(self.root, create=True)
        (self.root / 'work').mkdir()
        (self.root / 'work/input.txt').write_bytes(b'Captured source bytes.\n')

    def build_artifact_payload(self, path):
        return dict(id='input', path=path, media_type='text/plain')

    def test_absolute_and_relative_artifact_paths_store_identical_payload_record_and_check_digest(self):
        relative = self.build_artifact_payload('work/input.txt')
        absolute = self.build_artifact_payload(str(self.root / 'work/input.txt'))
        first = self.assert_accepted_check(self.invoke_command('artifact', relative), 0)
        second = self.assert_accepted_check(self.invoke_command('artifact', absolute), 0)
        self.assertEqual(first['payload_digest'], second['payload_digest'])
        result = self.invoke_command('artifact', absolute, check=False)
        self.assertEqual(result[0], 0, result[2])
        self.assertEqual(self.store.committed_request('check-request')['payload'], relative)
        record = self.store.snapshot()['records']['local_artifact']['input']
        self.assertEqual(record['path'], 'work/input.txt')
        # The other spelling replays the exact committed receipt.
        replay = self.invoke_command('artifact', relative, check=False, revision=0)
        self.assertEqual(replay[0], result[0], replay[2])
        self.assertEqual(json.loads(replay[1]), json.loads(result[1]))

    def test_all_workspace_file_fields_normalize_to_the_same_payload_without_mutating_input(self):
        from research_harness.payload_paths import normalize_payload_paths
        absolute = str(self.root / 'work/input.txt')
        descriptor = dict(path=absolute, sha256='a' * 64, size=23, media_type='text/plain')
        cases = [
            ('artifact', {'path': absolute}, {'path': 'work/input.txt'}),
            ('adopt', {'files': [absolute]}, {'files': ['work/input.txt']}),
            ('import-response', {'response_file': absolute}, {'response_file': 'work/input.txt'}),
            ('import-oai-cohort', {'pages': [{'response_file': absolute}]},
             {'pages': [{'response_file': 'work/input.txt'}]}),
            ('manuscript', {'files': dict.fromkeys(('pdf', 'abstract', 'bibliography', 'claims', 'sources'), absolute)},
             {'files': dict.fromkeys(('pdf', 'abstract', 'bibliography', 'claims', 'sources'), 'work/input.txt')}),
            ('manuscript', {'files': {'sources': None}}, {'files': {'sources': None}}),
            ('problem-posted', {'export_directory': str(self.root / 'work')}, {'export_directory': 'work'}),
            ('gate-override', {'instruction': absolute}, {'instruction': 'work/input.txt'}),
            ('review-attempt', {'output': descriptor}, {'output': dict(descriptor, path='work/input.txt')}),
            ('check-claims', [{'evidence': [descriptor]}], [{'evidence': [dict(descriptor, path='work/input.txt')]}]),
        ]
        for command, payload, expected in cases:
            with self.subTest(command=command):
                before = copy.deepcopy(payload)
                self.assertEqual(normalize_payload_paths(self.root, command, payload), expected)
                self.assertEqual(normalize_payload_paths(self.root, command, expected), expected)
                self.assertEqual(payload, before)

    def test_absolute_input_keeps_the_workspace_parent_alias_in_both_modes(self):
        alias = self.root.parent / 'parent-alias'
        alias.symlink_to(self.root.parent, target_is_directory=True)
        self.root = alias / self.root.name
        absolute = self.build_artifact_payload(str(self.root / 'work/input.txt'))
        relative = self.build_artifact_payload('work/input.txt')
        first = self.assert_accepted_check(self.invoke_command('artifact', absolute), 0)
        second = self.assert_accepted_check(self.invoke_command('artifact', relative), 0)
        self.assertEqual(first['payload_digest'], second['payload_digest'])
        written = self.invoke_command('artifact', absolute, check=False)
        self.assertEqual(written[0], 0, written[2])
        self.assertEqual(self.store.committed_request('check-request')['payload'], relative)

    def test_repeated_paper_bundle_keeps_frozen_paths_outside_new_input_admission(self):
        from research_harness import review_protocol
        from research_harness.payload_paths import normalize_payload_paths
        nested = {}
        for _ in range(600):
            nested = {'child': nested}
        descriptor = dict(path=str(self.root / 'work/input.txt'), sha256='a' * 64,
                          size=23, media_type='text/plain')
        retained_path = str(self.root.parent / 'retained-location' / 'input.txt')
        bundle = dict(artifact=dict(descriptor, path=retained_path), nested=nested)
        payload = dict(role=sorted(review_protocol.PAPER_ROLES)[0], context=dict(bundle=bundle))
        before = self.store.snapshot()
        checked = self.invoke_command('review-assignment', payload)
        written = self.invoke_command('review-assignment', payload, check=False)
        self.assertEqual((checked[0], written[0]), (1, 1))
        self.assertEqual(json.loads(checked[2])['error'], json.loads(written[2])['error'])
        self.assertEqual(json.loads(checked[2])['error']['code'], 'invalid_input')
        self.assertEqual(self.store.snapshot(), before)
        payload['context']['source_links'] = [dict(artifact=descriptor)]
        normalized = normalize_payload_paths(self.root, 'review-assignment', payload)
        self.assertEqual(digest(normalized['context']['bundle']), digest(bundle))
        self.assertEqual(normalized['context']['source_links'][0]['artifact']['path'], 'work/input.txt')

    def test_sandbox_names_and_foreign_workspace_identities_keep_their_existing_meaning(self):
        from research_harness.payload_paths import normalize_payload_paths
        (self.root / 'program.py').symlink_to(self.root.parent / 'foreign-program.py')
        payload = dict(script='program.py', inputs=[dict(path='input.json')], outputs=[dict(path='stdout')])
        self.assertEqual(normalize_payload_paths(self.root, 'bind-run', payload), payload)
        for command, field in (('forming-import', 'source_workspace'),
                               ('problem-objections-carry', 'source_workspace'),
                               ('problem-candidate-transfer', 'destination_workspace')):
            payload = {field: str(self.root.parent / 'other-study')}
            self.assertEqual(normalize_payload_paths(self.root, command, payload), payload)

    def test_every_payload_file_field_refuses_escape_before_preparation_with_one_message(self):
        examples = json.loads((ROOT / 'docs/research-cli-examples.json').read_bytes())
        cases = [
            ('artifact', ('path',)),
            ('adopt', ('files', 0)),
            ('import-response', ('response_file',)),
            ('import-oai-cohort', ('pages', 0, 'response_file')),
            ('manuscript', ('files', 'pdf')),
            ('manuscript', ('files', 'abstract')),
            ('manuscript', ('files', 'bibliography')),
            ('manuscript', ('files', 'claims')),
            ('manuscript', ('files', 'sources')),
            ('problem-posted', ('export_directory',)),
            ('gate-override', ('instruction',)),
            ('review-attempt', ('output', 'path')),
        ]
        messages = set()
        for command, keys in cases:
            with self.subTest(command=command, field=keys):
                payload = copy.deepcopy(examples[command])
                if command == 'review-attempt':
                    payload['output'] = dict(path='work/input.txt', sha256='a' * 64, size=23, media_type='text/plain')
                target = payload
                for key in keys[:-1]:
                    target = target[key]
                target[keys[-1]] = str(self.root.parent / 'outside')
                result = self.invoke_command(command, payload, check=False)
                self.assertEqual(result[0], 1, result)
                error = json.loads(result[2])['error']
                self.assertEqual(error['code'], 'unsafe_path', error)
                messages.add(error['message'])
        self.assertEqual(len(messages), 1)
        message = messages.pop()
        self.assertIn('workspace-relative', message)
        self.assertIn('absolute', message)

    def test_noncanonical_and_symlink_paths_have_the_same_refusal_in_both_modes(self):
        (self.root / 'alias').symlink_to(self.root / 'work', target_is_directory=True)
        (self.root / 'work/link').symlink_to(self.root / 'work/input.txt')
        (self.root / 'outside-link').symlink_to(self.root.parent, target_is_directory=True)
        paths = ['', '.', '../outside', 'work/../work/input.txt', './work/input.txt',
                 'work//input.txt', 'work/input.txt/', 'work\\input.txt', 'work/\x00input',
                 'alias/input.txt', 'work/link', 'outside-link/outside',
                 str(self.root / 'alias/input.txt'), str(self.root) + '/work/../work/input.txt',
                 str(self.root) + '//work/input.txt']
        messages = set()
        before = self.store.snapshot()
        for path in paths:
            for check in (False, True):
                with self.subTest(path=path, check=check):
                    files_before = self.read_workspace() if check else None
                    response = self.invoke_command('artifact', self.build_artifact_payload(path), check=check)
                    self.assertEqual(response[0], 1, response)
                    error = json.loads(response[2])['error']
                    self.assertEqual(error['code'], 'unsafe_path')
                    messages.add(error['message'])
                    self.assertEqual(self.store.snapshot(), before)
                    if check:
                        self.assertEqual(self.read_workspace(), files_before)
        self.assertEqual(len(messages), 1)

    def test_import_response_is_workspace_relative_and_absolute_replay_keeps_stored_bytes(self):
        body = b'{"meta": {"count": 0}, "results": []}'
        (self.root / 'work/response.json').write_bytes(body)
        payload = dict(provider='openalex', response_file='work/response.json',
                       source_url='https://api.openalex.org/works?search=test',
                       captured_at='2026-11-13T12:00:00Z')
        result = self.invoke_command('import-response', payload, check=False, request='response')
        self.assertEqual(result[0], 0, result[2])
        before = self.store.snapshot()
        replay = self.invoke_command('import-response', dict(payload,
            response_file=str(self.root / 'work/response.json')), check=False, revision=0, request='response')
        self.assertEqual(replay[0], result[0], replay[2])
        self.assertEqual(json.loads(replay[1]), json.loads(result[1]))
        self.assertEqual(self.store.snapshot(), before)

    def test_deep_payload_keeps_the_structured_input_bound_refusal(self):
        nested = 'source'
        for _ in range(600):
            nested = [nested]
        response = self.invoke_command('artifact', dict(self.build_artifact_payload('work/input.txt'),
                                                       extra=nested), check=True)
        self.assertEqual(response[0], 1, response)
        self.assertEqual(json.loads(response[2])['error']['code'], 'invalid_input')

    def test_different_deep_retry_keeps_the_request_conflict_without_recursive_normalization(self):
        payload = self.build_artifact_payload('work/input.txt')
        response = self.invoke_command('artifact', payload, check=False)
        self.assertEqual(response[0], 0, response[2])
        nested = 'changed input'
        for _ in range(600):
            nested = [nested]
        for check in (True, False):
            response = self.invoke_command('artifact', dict(payload, extra=nested), check=check, revision=0)
            self.assertEqual(response[0], 1, response)
            self.assertEqual(json.loads(response[2])['error']['code'], 'request_id_conflict')

    def test_legacy_workspace_can_be_adopted_before_a_store_exists(self):
        legacy = self.root.parent / 'legacy-study'
        (legacy / '.exactory').mkdir(parents=True)
        marker = legacy / '.exactory/study.json'
        marker.write_text('{"version": 1}')
        source = legacy / 'notes.txt'
        source.write_text('Legacy assertions remain unverified.')
        payload = self.root.parent / 'adoption.json'
        payload.write_text(json.dumps(dict(id='adoption', profile='research', target=None,
            files=[str(source)], reason='Retain the original legacy notes.')))
        response = self.invoke_arguments(['adopt', '--workspace', str(legacy), '--file', str(payload),
            '--expected-revision', '0', '--request-id', 'adopt-legacy'])
        self.assertEqual(response[0], 0, response[2])
        adoption = json.loads(response[1])['result']
        self.assertEqual(adoption['files'], ['notes.txt'])
        self.assertEqual(adoption['snapshots'][0]['path'], 'notes.txt')
        self.assertFalse(adoption['scientific_validation'])

    def test_initial_import_reads_inside_the_new_workspace_before_store_creation(self):
        fresh = self.root.parent / 'fresh-store'
        fresh.mkdir()
        response = fresh / 'response.json'
        response.write_bytes(b'{"meta": {"count": 0}, "results": []}')
        payload = self.root / 'new-import.json'
        payload.write_text(json.dumps(dict(provider='openalex', response_file='response.json',
            source_url='https://api.openalex.org/works?search=test', captured_at='2026-11-13T12:00:00Z')))
        result = self.invoke_arguments(['import-response', '--workspace', str(fresh), '--file', str(payload),
            '--expected-revision', '0', '--request-id', 'initial-response'])
        self.assertEqual(result[0], 0, result[2])
        self.assertEqual(Store(fresh).revision, 2)

    def test_0853_artifact_receipt_replays_without_reopening_the_old_input(self):
        payload = self.build_artifact_payload('work/input.txt')
        # The 0.85.3 record shape is independent of today's artifact preparation.
        sha = '2255b51ae96111747a7351fbc11eb92724a0032d066380b81cc7b2707f40ee41'
        reference = dict(path='research/sources/objects/' + sha, sha256=sha, size=23, media_type='text/plain')
        self.assertEqual(ArtifactStore(self.root).put(b'Captured source bytes.\n', 'text/plain'), reference)
        record = dict(payload, artifact=reference, scientific_validation=False)
        operations.prepared_mutation(self.store, 'workspace.artifact', payload,
            lambda records, value: ([('local_artifact', 'input', record)], record),
            expected_revision=0, request_id='check-request')
        self.assertEqual(digest(self.store.snapshot()['records']['local_artifact']['input']),
                         'acb7c4c3bb371e12aec8121a0384526d21129f08612173820ca1898e51683ae5')
        result = self.invoke_command('artifact', dict(payload, path=str(self.root / payload['path'])),
                                     check=False, revision=0)
        self.assertEqual(result[0], 0, result[2])
        (self.root / 'work/input.txt').unlink()
        (self.root / 'work/input.txt').symlink_to(self.root.parent / 'gone')
        before = self.store.snapshot()
        replay = self.invoke_command('artifact', payload, check=False, revision=0)
        self.assertEqual(replay[0], result[0], replay[2])
        self.assertEqual(json.loads(replay[1]), json.loads(result[1]))
        self.assertEqual(self.store.snapshot(), before)
