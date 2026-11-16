"""Stored finding-v1 chains produced by the actual 0.90.0 and 0.91.0 releases.

Each archive contains the release's records and content-addressed artifacts, including
the prompts, requests, captured responses and final packet. Generation used the
release's OpennessReadingCase with two synthetic 80,000-character works and a stub
Codex sender. The native root was /fixture/openness-finding-<release> at export,
before any packet or request was hashed. Unneeded acquisition/literature operation
receipts were omitted. The manifest pins the generating release and source hashes;
these tests read no Git history and do not regenerate the historical assignments.
"""

import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from codex_route_fixtures import build_codex_response
from research_harness.artifacts import ArtifactStore
from research_harness.evidence import digest
from research_harness.native_review import load_packet
from research_harness.review_continuation import plan_next
from research_harness.review_search_delivery import build_finding_index
from research_harness.storage import Store
from research_harness import openness_reading as reading
from research_harness import openness_reading_v7 as v7
from research_harness import review_protocol as protocol
from research_harness import review_route_capacity as capacity
from research_harness import review_codex_transport as codex


RELEASE_FIXTURES = (
    ('0.90.0', 'b4a5c016f6f9a22274bc6971fe31ff2d1fabb9ac',
     'openness-finding-0900.tar.gz',
     '5e3886bb2a12fb088b60b00e68fca45d5a8f3f38c21e35c7bdbf5ce8cd91b5d5',
     reading.PROTOCOL_V6),
    ('0.91.0', 'b6b993e461808841e9fd69d44f4106a18637d381',
     'openness-finding-0910.tar.gz',
     '469dc8664d024305cf54312acaaf724280381abff537256205ad5ecbcca84b13',
     v7.PROTOCOL),
)


def rebuild_v6_final(records, artifacts, assignment, packet):
    """Rebuild the saved v6 final from the native original and observed prefix."""
    native = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
    route = records['review_route'][assignment['route_id']]
    with protocol.transport.text_only_delivery(route['adapter'] in protocol.transport.TEXT_ONLY_ADAPTERS):
        logical = protocol.build_packet(records, artifacts, None, 'native_math', assignment['reviewer_id'],
                                        **assignment['context'])
    rows = reading.list_reading_rows(records, artifacts, native, review=assignment)
    manifest = reading._plan_search_delivery(artifacts, native)['manifest']
    delivery_rows = reading.list_delivery_rows(rows, manifest)
    chain = reading._list_chain(records, assignment)
    observations = reading._observe_turns(records, artifacts, chain[:-1], delivery_rows,
        reading._compute_spans(artifacts, delivery_rows), manifest)
    eligible = reading._classify_final_texts(records, artifacts, native, rows, reading.PROTOCOL_V6)
    return reading._build_final_turn(route, logical,
        packet['reading_record'], rows, reading._compute_spans(artifacts, rows), observations, eligible)


def rebuild_v7_final(records, artifacts, assignment):
    """Rebuild the saved v7 final at its stored pre-final state and allowance."""
    native = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
    logical = dict(protocol=protocol.PROTOCOL, role=assignment['role'], reviewer_id=assignment['reviewer_id'],
                   dossier_id=assignment['dossier_id'], native=native)
    chain = assignment['reading_chain']
    inventory = json.loads(artifacts.read(chain['inventory']))
    state = json.loads(artifacts.read(chain['state']))
    plan = capacity.read_chain_capacity_plan(artifacts, assignment)
    historical = dict(records, review_attempt={key: row for key, row in records['review_attempt'].items()
        if row['recorded_revision'] < assignment['recorded_revision']})
    prior = records['review_assignment'][assignment['context']['prior_assignment_id']]
    limits = capacity.derive_turn_task_limits(historical, artifacts, prior, kind='final')
    return v7.build_turn_packet(artifacts, records['review_route'][assignment['route_id']], logical,
        chain['first_assignment_id'], chain['turn'], inventory, state, plan, limits, 'final',
        requests=state.get('pending_requests', []), finding_version=1)


def restore_legacy_records(fixture, artifacts, revision):
    """Restore a disposable prefix without changing the archived records or bytes."""
    records = {kind: {key: row for key, row in values.items()
                     if row.get('recorded_revision', 0) <= revision}
               for kind, values in fixture['records'].items()}
    store = Store(artifacts.root, create=True)
    for number in range(1, revision + 1):
        pending = [(kind, key, row) for kind, values in records.items() for key, row in values.items()
                   if max(1, row.get('recorded_revision', 1)) == number]
        def restore(tx):
            for kind, key, row in pending:
                tx.put(kind, key, row)
        store.mutate('test.restore_legacy', {'revision': number}, restore,
                     expected_revision=store.revision, request_id='restore-%d' % number)
    return store, records


class StoredFindingCompatibilityTests(unittest.TestCase):
    def setUp(self):
        # The release fixtures use the sender stub's empty CLI declarations.
        declarations = patch.object(codex, 'NATIVE_DECLARATION_SETS', {()})
        declarations.start()
        self.addCleanup(declarations.stop)
        # Continuation receipts do not inspect the checkout or installed Git.
        runtime = patch.object(Store, 'describe_runtime', return_value={'fixture': 'stored-finding-compatibility'})
        runtime.start()
        self.addCleanup(runtime.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fixtures = []
        for release, commit, filename, sha256, reading_protocol in RELEASE_FIXTURES:
            path = Path(__file__).parent / 'fixtures' / filename
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), sha256)
            root = Path(temporary.name) / release
            with tarfile.open(str(path), 'r:gz') as archive:
                manifest = json.load(archive.extractfile('manifest.json'))
                for member in archive.getmembers():
                    if member.name != 'manifest.json':
                        target = root / member.name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(archive.extractfile(member).read())
            self.assertEqual(manifest['release'], release)
            self.assertEqual(manifest['release_tag'], 'exactory--v' + release)
            self.assertEqual(manifest['release_commit'], commit)
            self.assertEqual(manifest['protocol'], reading_protocol)
            self.assertEqual(manifest['assignment_ids'], ['turn-1', 'turn-2', 'turn-3'])
            self.fixtures.append((release, manifest, ArtifactStore(root)))

    def test_saved_prompt_and_packet_digests_verify_for_both_releases(self):
        for release, fixture, artifacts in self.fixtures:
            records = fixture['records']
            for identifier in fixture['assignment_ids']:
                with self.subTest(release=release, assignment=identifier):
                    assignment = records['review_assignment'][identifier]
                    packet = json.loads(artifacts.read(assignment['packet']))
                    prompt = assignment['prompt']
                    self.assertNotIn('finding_version', prompt['parameters'])
                    self.assertNotIn('finding_index', packet)
                    self.assertEqual(prompt['template_hash'], digest(prompt['text']))
                    self.assertEqual(prompt['rendered_prompt_hash'], assignment['prompt_hash'])
                    self.assertEqual(assignment['packet_digest'], packet['digest'])
                    self.assertEqual(packet['digest'], digest({key: value for key, value in packet.items()
                                                              if key != 'digest'}))
                    self.assertTrue(protocol._prompt_matches(assignment, packet))

    def test_saved_authenticated_chains_verify_for_both_releases(self):
        for release, fixture, artifacts in self.fixtures:
            with self.subTest(release=release):
                records = fixture['records']
                final = records['review_assignment'][fixture['final_assignment_id']]
                for identifier in fixture['assignment_ids']:
                    observed = protocol.assignment_state(records, artifacts, identifier)
                    self.assertTrue(observed['ready'], observed['obligations'])
                    assignment = records['review_assignment'][identifier]
                    attempt = records['review_attempt'][observed['attempt_id']]
                    self.assertTrue(protocol.authenticate_attempt(records, artifacts, assignment, attempt))
                turns = reading.verify_chain(records, artifacts, final)
                self.assertEqual(len(turns), len(fixture['assignment_ids']))
                output = protocol.assignment_state(records, artifacts, final['id'])['output']
                self.assertEqual(output['findings']['openness']['grounds'][0]['kind'], 'reviewer_gap')
                self.assertIn('text', output['findings']['openness']['grounds'][0])

    def test_final_packet_rebuilds_with_the_saved_bytes_for_both_releases(self):
        for release, fixture, artifacts in self.fixtures:
            with self.subTest(release=release):
                records = fixture['records']
                assignment = records['review_assignment'][fixture['final_assignment_id']]
                saved = artifacts.read(assignment['packet'])
                packet = json.loads(saved)
                if fixture['protocol'] == reading.PROTOCOL_V6:
                    rebuilt = rebuild_v6_final(records, artifacts, assignment, packet)
                else:
                    rebuilt = rebuild_v7_final(records, artifacts, assignment)
                self.assertEqual(rebuilt, packet)
                encoded = json.dumps(rebuilt, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
                self.assertEqual(encoded, saved)
                self.assertEqual(hashlib.sha256(encoded).hexdigest(), assignment['packet']['sha256'])

    def assert_legacy_prefix_continues(self, release):
        """Continue a disposable copy before its saved final, preserving its prefix."""
        _, fixture, artifacts = next(row for row in self.fixtures if row[0] == release)
        original = fixture['records']
        old_final = original['review_assignment'][fixture['final_assignment_id']]
        revision = old_final['recorded_revision'] - 1
        store, records = restore_legacy_records(fixture, artifacts, revision)
        self.assertNotIn(old_final['id'], records['review_assignment'])
        self.assertFalse(any(row['assignment_id'] == old_final['id']
                             for row in records['review_attempt'].values()))
        before = store.snapshot()['records']['review_assignment']
        saved_packets = {identifier: artifacts.read(row['packet']) for identifier, row in before.items()}
        prior = before[old_final['context']['prior_assignment_id']]
        route = records['review_route'][prior['route_id']]
        payload = dict(id='continued-final', route_id=prior['route_id'], dossier_id=None,
            role=prior['role'], reviewer_id=prior['reviewer_id'], author_id=prior['author_id'],
            context=dict(native_packet=prior['context']['native_packet'], prior_assignment_id=prior['id']))
        with patch.object(codex, 'runtime', return_value=route['runtime']):
            protocol.record_assignment(store, payload, expected_revision=store.revision,
                                       request_id='continue-legacy-prefix')
        after = store.snapshot()['records']['review_assignment']
        continued = after[payload['id']]
        packet = json.loads(artifacts.read(continued['packet']))
        self.assertEqual(reading.find_stage(packet), 'final')
        self.assertEqual(continued['prompt']['parameters']['finding_version'], 2)
        self.assertIn('finding_index', packet)
        native = load_packet(artifacts, continued['context']['native_packet'], continued['reviewer_id'])
        self.assertEqual(packet['finding_index'], build_finding_index(store.snapshot()['records'], artifacts, native))
        self.assertTrue(protocol._prompt_matches(continued, packet))
        for identifier, saved in before.items():
            self.assertEqual(after[identifier], saved)
            self.assertEqual(artifacts.read(after[identifier]['packet']), saved_packets[identifier])
            self.assertNotIn('finding_version', saved['prompt']['parameters'])

    def test_0900_reading_prefix_continues_to_a_finding_v2_final(self):
        self.assert_legacy_prefix_continues('0.90.0')

    def test_0910_reading_prefix_continues_to_a_finding_v2_final(self):
        self.assert_legacy_prefix_continues('0.91.0')

    def test_0910_pending_final_refusal_replaces_with_a_finding_v2_final(self):
        _, fixture, artifacts = next(row for row in self.fixtures if row[0] == '0.91.0')
        original = fixture['records']
        final = original['review_assignment'][fixture['final_assignment_id']]
        store, records = restore_legacy_records(fixture, artifacts, final['recorded_revision'])
        for kind in ('review_attempt', 'review_invocation'):
            self.assertFalse(any(row['assignment_id'] == final['id'] for row in records[kind].values()))
        self.assertNotIn('finding_version', final['prompt']['parameters'])
        self.assertNotIn('finding_index', capacity.read_chain_capacity_plan(artifacts, final))
        pending = protocol.assignment_state(records, artifacts, final['id'])
        self.assertIsNone(pending['output'])
        saved_assignments = records['review_assignment']
        saved_packets = {identifier: artifacts.read(row['packet'])
                         for identifier, row in saved_assignments.items()}
        route = records['review_route'][final['route_id']]
        attempt_id = 'malformed-legacy-final'
        def send_malformed_answer(route, request, credential):
            return build_codex_response(route, request, {})
        with patch.object(codex, 'runtime', return_value=route['runtime']), patch.object(
                codex, '_rollout', return_value=[]), patch.object(
                protocol.transport, 'send_request', side_effect=send_malformed_answer) as sender:
            response = protocol.invoke_assignment(store, dict(id=attempt_id, assignment_id=final['id']),
                credential='fixture', expected_revision=store.revision, request_id='invoke-malformed-legacy-final')
            self.assertEqual(sender.call_count, 1)
            self.assertEqual(response['output_refusal']['code'], 'openness_finding_invalid')
            records = store.snapshot()['records']
            self.assertTrue(protocol.authenticate_attempt(records, artifacts, final,
                                                         records['review_attempt'][attempt_id]))
            self.assertEqual(records['review_output_refusal'][attempt_id]['check'], 'openness.finding')
            action = plan_next(records, artifacts, final['id'])
            self.assertEqual((action['state'], action.get('command')), ('replace', 'review-assignment'))
            payload = dict(action['payload'], id='replacement-final')
            protocol.record_assignment(store, payload, expected_revision=store.revision,
                                       request_id='replace-refused-legacy-final')
        records = store.snapshot()['records']
        replacement = records['review_assignment'][payload['id']]
        packet = json.loads(artifacts.read(replacement['packet']))
        self.assertEqual(reading.find_stage(packet), 'final')
        self.assertEqual(replacement['prompt']['parameters']['finding_version'], 2)
        native = load_packet(artifacts, replacement['context']['native_packet'], replacement['reviewer_id'])
        self.assertEqual(packet['finding_index'], build_finding_index(records, artifacts, native))
        self.assertTrue(protocol._prompt_matches(replacement, packet))
        self.assertIn('output_refusal', packet)
        chain = replacement['reading_chain']
        v7._verify_turn_binding(records, artifacts, records['review_assignment'][chain['first_assignment_id']],
            replacement, packet, native, route, json.loads(artifacts.read(chain['inventory'])),
            json.loads(artifacts.read(chain['state'])), capacity.read_chain_capacity_plan(artifacts, replacement),
            chain['turn'])
        for identifier, saved in saved_assignments.items():
            self.assertEqual(records['review_assignment'][identifier], saved)
            self.assertEqual(artifacts.read(saved['packet']), saved_packets[identifier])


if __name__ == '__main__':
    unittest.main()
