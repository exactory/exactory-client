"""Record 5: account-cited search records leave the frame and arrive once."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from research_harness import review_parts as parts, review_protocol as protocol
from research_harness.artifacts import ArtifactStore, describe_artifact
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from review_parts_fixtures import PartsResultCase, make_packet, observe, plan


RULE = 'exactory-result-record-units-v1'
FIXTURE = Path(__file__).parent / 'fixtures' / 'review-account-record-units-v2.json'


def make_search_packet(count=1, characters=400, body_characters=128):
    """Keep the captured-response/account/minimum shapes, with synthetic text."""
    packet = make_packet(0)
    evidence = packet['problem_evidence']
    evidence['account_records'] = []
    for index in range(count):
        content = str(index) + ' captured result. ' * (body_characters // 18 + 1)
        content = content[:body_characters]
        artifact = describe_artifact(content.encode('utf-8'), 'application/json')
        source = dict(id='capture-' + str(index), response=artifact, kind='search_response')
        search = dict(id='search-' + str(index), profile='research', purpose='openness',
                      queries=['synthetic finite structures'], cited_work_ids=[], found_work_ids=[],
                      responses=[dict(source_id=source['id'])], reason='x' * characters)
        evidence['openness_searches'].append(dict(
            formulation='synthetic formulation ' + str(index), search_digest=digest(search),
            search=search, sources=[source],
            cite=dict(kind='record', record_kind='literature_search', id=search['id'])))
        for kind, record in (('literature_search', search), ('source', source)):
            evidence['account_records'].append(dict(record=record, cite=dict(
                kind='record', record_kind=kind, id=record['id'], digest=digest(record))))
        packet['evidence'].append(dict(artifact=artifact, encoding='utf-8', content=content))
    evidence['value'] = dict(accounts=dict(search_coverage=[dict(
        id='coverage', search_ids=['search-' + str(index) for index in range(count)])]))
    packet['problem_evidence_digest'] = digest(evidence)
    return packet


class AccountRecordUnitTests(unittest.TestCase):
    def test_search_and_source_records_have_one_identity_and_exact_occurrences(self):
        packet = make_search_packet()
        inventory = parts.build_inventory(packet, 'problem_result')
        self.assertEqual(inventory.get('unit_rule'), RULE)
        units = parts.enumerate_units(packet, 'problem_result', inventory)
        self.assertEqual(len(units), 2)
        search, source = units
        self.assertEqual(search['item'], packet['problem_evidence']['openness_searches'][0]['search'])
        self.assertEqual(search['record_identity'], dict(
            record_kind='literature_search', id='search-0', digest=digest(search['item'])))
        self.assertEqual(len(search['occurrences']), 2)
        self.assertEqual(source['record_identity']['record_kind'], 'source')
        self.assertEqual(len(source['bodies']), 1)
        self.assertEqual(search['bodies'], [])
        turn = plan(packet, input_limit=1300000)
        parts.validate_deliveries(turn, inventory)
        for key in ('openness_searches', 'account_records'):
            self.assertTrue(parts.covers_items([turn], packet['problem_evidence'], key, inventory))
        altered = copy.deepcopy(packet['problem_evidence'])
        altered['openness_searches'][0]['formulation'] += ' changed'
        self.assertFalse(parts.covers_items([turn], altered, 'openness_searches', inventory))
        accounts_only = copy.deepcopy(packet)
        accounts_only['problem_evidence']['openness_searches'] = []
        account_turn = plan(accounts_only, input_limit=1300000)
        self.assertEqual(len(account_turn['_test_inventory']['units']), 2)
        self.assertTrue(parts.covers_items([account_turn], accounts_only['problem_evidence'],
                                          'account_records', account_turn['_test_inventory']))

    def test_ten_large_searches_and_captures_leave_a_small_frame_and_arrive_once(self):
        # Source: 20261112150500-result-review-frame-holds-cited-search-records, T1.
        packet = make_search_packet(10, characters=160000, body_characters=470000)
        inventory = parts.build_inventory(packet, 'problem_result')
        units = parts.enumerate_units(packet, 'problem_result', inventory)
        frame = parts.build_frame(packet, units)
        self.assertLess(len(parts.render_json(frame)), 1300000)
        self.assertEqual(frame['problem_evidence']['account_records'], [])
        self.assertEqual(frame['evidence'], [])
        observations = []
        for _ in range(30):
            turn = plan(packet, observations, input_limit=1300000)
            if parts.find_kind(turn) != 'read':
                break
            observations.append(observe(turn))
        self.assertEqual(len(units), 20)
        self.assertTrue(observations)
        reads = [row['packet'] for row in observations]
        for key in ('openness_searches', 'account_records'):
            self.assertTrue(parts.covers_items(reads, packet['problem_evidence'], key, inventory))
        for unit in units:
            self.assertTrue(parts.is_unit_complete(unit, reads))
            spans = [(entry['segment']['start'], entry['segment']['end'])
                     for turn in reads for entry in turn['evidence']
                     if entry.get('item') and entry.get('unit') == unit['handle']]
            self.assertEqual(sum(end - start for start, end in spans), unit['item_characters'])
        self.assertTrue(any(row['record_identity']['record_kind'] == 'literature_search'
                            for row in reads[0][parts.KEY]['units']))
        for body in packet['evidence']:
            entries = [entry for turn in reads for entry in turn['evidence']
                       if not entry.get('item') and entry.get('original_artifact', entry['artifact'])['sha256']
                       == body['artifact']['sha256']]
            self.assertEqual(sum(len(entry['content']) for entry in entries), len(body['content']))

    def test_shared_source_and_capture_have_one_owner(self):
        packet = make_search_packet(2)
        evidence = packet['problem_evidence']
        source = evidence['openness_searches'][0]['sources'][0]
        evidence['openness_searches'][1]['sources'] = [source]
        evidence['openness_searches'][1]['search']['responses'] = [dict(source_id=source['id'])]
        evidence['openness_searches'][1]['search_digest'] = digest(evidence['openness_searches'][1]['search'])
        evidence['account_records'] = [entry for entry in evidence['account_records']
                                      if entry['cite']['id'] != 'capture-1']
        for entry in evidence['account_records']:
            entry['cite']['digest'] = digest(entry['record'])
        evidence['assessment_evidence'] = [source['response']]
        units = parts.enumerate_units(packet, 'problem_result')
        owners = [unit for unit in units for body in unit['bodies']
                  if body['artifact']['sha256'] == source['response']['sha256']]
        self.assertEqual(len(owners), 1)
        self.assertEqual(owners[0]['record_identity']['record_kind'], 'source')

    def test_assessment_and_objection_occurrences_share_the_search_unit(self):
        packet = make_search_packet()
        search = packet['problem_evidence']['openness_searches'][0]['search']
        reference = dict(kind='record', record_kind='literature_search', id=search['id'],
                         digest=digest(search), record=search)
        packet['problem_evidence']['assessment_evidence'] = [reference]
        packet['material_objections'] = [dict(id='objection', version_evidence=[
            dict(reference, artifacts=[packet['evidence'][0]['artifact']])])]
        inventory = parts.build_inventory(packet, 'problem_result')
        units = parts.enumerate_units(packet, 'problem_result', inventory)
        self.assertEqual(len(units), 2)
        self.assertEqual(len(units[0]['occurrences']), 4)
        self.assertEqual(units[0]['bodies'], [])
        self.assertEqual(len(units[1]['bodies']), 1)
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(Path(directory))
            logical = artifacts.put(parts.render_json(packet).encode(), 'application/json')
            turn = plan(packet, input_limit=1300000, logical_reference=logical,
                        inventory_reference=parts.store_inventory(artifacts, inventory))
            for entry in turn['evidence']:
                artifacts.put(entry['content'].encode(), entry['artifact']['media_type'])
            delivery = parts.read_series_delivery({}, artifacts, packets=[turn], inventory=inventory)
        self.assertTrue(any(row[0] == 'literature_search' and row[1] == search['id']
                            and row[2] == digest(search) for row in delivery['carried_records']))
        for key in ('openness_searches', 'assessment_evidence', 'account_records'):
            self.assertTrue(parts.covers_items(delivery['read_packets'], packet['problem_evidence'], key, inventory))
        self.assertTrue(parts.covers_items(delivery['read_packets'], packet['material_objections'][0],
            'version_evidence', inventory, path=['material_objections', 0, 'version_evidence']))

    def test_canonical_local_artifact_record_keeps_its_owned_body(self):
        packet = make_packet(0)
        content = 'Synthetic artifact evidence.'
        artifact = describe_artifact(content.encode(), 'text/plain')
        record = dict(id='local-artifact', artifact=artifact)
        cite = dict(kind='record', record_kind='local_artifact', id=record['id'], digest=digest(record))
        evidence = packet['problem_evidence']
        evidence['assessment_evidence'] = [dict(cite, record=record)]
        evidence['account_records'] = [dict(record=record, cite=cite)]
        packet['evidence'] = [dict(artifact=artifact, encoding='utf-8', content=content)]
        units = parts.enumerate_units(packet, 'problem_result')
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]['item'], record)
        self.assertEqual([body['artifact'] for body in units[0]['bodies']], [artifact])
        turn = plan(packet, input_limit=1300000)
        for key in ('assessment_evidence', 'account_records'):
            self.assertTrue(parts.covers_items([turn], evidence, key, turn['_test_inventory']))

    def test_only_exact_local_record_wrappers_are_canonicalized(self):
        packet = make_packet(0)
        record = dict(verdict='checked', method='synthetic')
        exact = dict(kind='record', record_kind='native_closure', id='closure', digest=digest(record),
                     record=record, artifacts=[])
        projected = dict(exact, record=dict(verdict='preview'))
        imported = dict(exact, origin='synthetic-origin')
        packet['material_objections'] = [dict(id='objection', version_evidence=[exact, projected, imported])]
        units = parts.enumerate_units(packet, 'problem_result')
        self.assertEqual(len(units), 3)
        self.assertEqual(units[0].get('record_identity'), dict(
            record_kind='native_closure', id='closure', digest=digest(record)))
        self.assertEqual(units[0]['item'], record)
        self.assertEqual([unit['item'] for unit in units[1:]], [projected, imported])
        self.assertTrue(all('record_identity' not in unit for unit in units[1:]))

    def test_equal_json_in_different_record_kinds_stays_two_units(self):
        packet = make_search_packet(0)
        record = dict(id='same', text='One canonical JSON value.')
        packet['problem_evidence']['account_records'] = [dict(
            record=record, cite=dict(kind='record', record_kind=kind, id='same', digest=digest(record)))
            for kind in ('cycle_plan', 'forming_call')]
        units = parts.enumerate_units(packet, 'problem_result')
        self.assertEqual(len(units), 2)
        self.assertEqual({unit['record_identity']['record_kind'] for unit in units},
                         {'cycle_plan', 'forming_call'})

    def test_shared_capture_keeps_the_descriptors_of_both_source_records(self):
        packet = make_search_packet()
        source = packet['problem_evidence']['openness_searches'][0]['sources'][0]
        alias = dict(source, id='capture-alias', response=dict(source['response'], media_type='text/plain'))
        packet['problem_evidence']['account_records'].append(dict(record=alias, cite=dict(
            kind='record', record_kind='source', id=alias['id'], digest=digest(alias))))
        packet['evidence'].append(dict(packet['evidence'][0], artifact=alias['response']))
        units = parts.enumerate_units(packet, 'problem_result')
        bodies = [body for unit in units for body in unit['bodies']]
        self.assertEqual(len(bodies), 1)
        descriptors = [bodies[0]['artifact']] + bodies[0].get('aliases', [])
        self.assertIn(source['response'], descriptors)
        self.assertIn(alias['response'], descriptors)

    def test_unknown_record_unit_rule_refuses_to_reinterpret_a_stored_inventory(self):
        packet = make_search_packet()
        inventory = parts.build_inventory(packet, 'problem_result')
        inventory['unit_rule'] = 'unknown-record-rule'
        with self.assertRaises(ResearchError) as caught:
            parts.build_inventory(packet, 'problem_result', inventory)
        self.assertEqual(caught.exception.code, 'review_parts_inventory_changed')

    def test_delivery_projection_keeps_duplicate_collection_manifest_positions(self):
        packet = make_search_packet()
        first = describe_artifact(b'First assessment.', 'text/plain')
        second = describe_artifact(b'Second assessment.', 'text/plain')
        packet['problem_evidence']['assessment_evidence'] = [first, first, second]
        imported = [dict(record=dict(id=identifier), cite=dict(kind='record', record_kind='cycle_plan',
                    id=identifier, digest=digest(dict(id=identifier)), origin='synthetic-origin'))
                    for identifier in ('import-a', 'import-b')]
        accounts = packet['problem_evidence']['account_records']
        accounts[1:1] = [imported[0], imported[0], imported[1]]
        inventory = parts.build_inventory(packet, 'problem_result')
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(Path(directory))
            logical = artifacts.put(parts.render_json(packet).encode(), 'application/json')
            turn = plan(packet, input_limit=1300000, logical_reference=logical,
                        inventory_reference=parts.store_inventory(artifacts, inventory))
            for entry in turn['evidence']:
                artifacts.put(entry['content'].encode(), entry['artifact']['media_type'])
            delivery = parts.read_series_delivery({}, artifacts, packets=[turn], inventory=inventory)
        self.assertTrue(parts.covers_items(delivery['read_packets'], packet['problem_evidence'],
                                          'assessment_evidence', inventory))
        self.assertTrue(parts.covers_items(delivery['read_packets'], packet['problem_evidence'],
                                          'account_records', inventory))
        self.assertEqual(inventory.get('unit_rule'), RULE)

    def test_late_account_occurrence_keeps_the_original_unit_and_rejects_changed_binding(self):
        packet = make_search_packet()
        account = packet['problem_evidence'].pop('account_records')
        before = parts.build_inventory(packet, 'problem_result')
        packet['problem_evidence']['account_records'] = account
        after = parts.build_inventory(packet, 'problem_result', before)
        self.assertEqual(len(after['units']), 2)
        self.assertEqual(after['units'], before['units'])
        self.assertEqual(len(after['additional_occurrences']), 2)
        packet['problem_evidence']['openness_searches'][0]['formulation'] += ' changed'
        with self.assertRaises(ResearchError) as caught:
            parts.build_inventory(packet, 'problem_result', after)
        self.assertEqual(caught.exception.code, 'review_parts_inventory_changed')

    def test_imported_account_duplicates_rebuild_without_local_record_units(self):
        packet = make_packet(0)
        imported = [dict(record=dict(id=identifier), cite=dict(kind='record', record_kind='forming_call',
                    id=identifier, digest=digest(dict(id=identifier)), origin='synthetic-origin'))
                    for identifier in ('import-a', 'import-b')]
        expected = [imported[0], imported[0], imported[1]]
        packet['problem_evidence']['account_records'] = expected
        inventory = parts.build_inventory(packet, 'problem_result')
        self.assertEqual(len(inventory['units']), 2)
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(Path(directory))
            logical = artifacts.put(parts.render_json(packet).encode(), 'application/json')
            turn = plan(packet, input_limit=1300000, logical_reference=logical,
                        inventory_reference=parts.store_inventory(artifacts, inventory))
            delivery = parts.read_series_delivery({}, artifacts, packets=[turn], inventory=inventory)
        self.assertEqual(delivery['read_packets'][-1]['problem_evidence']['account_records'], expected)
        self.assertTrue(parts.covers_items(delivery['read_packets'], packet['problem_evidence'],
                                          'account_records', inventory))

    def test_partial_record_or_source_does_not_credit_the_frozen_occurrence(self):
        packet = make_search_packet(characters=160000)
        inventory = parts.build_inventory(packet, 'problem_result')
        turn = plan(packet, room=55000)
        self.assertEqual(inventory.get('unit_rule'), RULE)
        for key in ('openness_searches', 'account_records'):
            self.assertFalse(parts.covers_items([turn], packet['problem_evidence'], key, inventory))
        forged = copy.deepcopy(turn)
        entry = next(entry for entry in forged['evidence'] if entry.get('item'))
        entry['original_artifact']['sha256'] = '0' * 64
        with self.assertRaises(ResearchError) as caught:
            parts.validate_deliveries(forged, inventory)
        self.assertEqual(caught.exception.code, 'review_parts_delivery_invalid')

    def test_segmented_record_rebuilds_without_storing_a_full_record_artifact(self):
        packet = make_search_packet(characters=160000)
        inventory = parts.build_inventory(packet, 'problem_result')
        observations = []
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(Path(directory))
            logical = artifacts.put(parts.render_json(packet).encode(), 'application/json')
            reference = parts.store_inventory(artifacts, inventory)
            for _ in range(20):
                turn = plan(packet, observations, room=55000, logical_reference=logical,
                            inventory_reference=reference)
                if parts.find_kind(turn) != 'read':
                    break
                for entry in turn['evidence']:
                    artifacts.put(entry['content'].encode(), entry['artifact']['media_type'])
                observations.append(observe(turn))
            search = packet['problem_evidence']['openness_searches'][0]['search']
            full_record = describe_artifact(parts.render_json(search).encode(), 'application/json')
            self.assertFalse((artifacts.root / full_record['path']).exists())
            delivery = parts.read_series_delivery({}, artifacts,
                packets=[row['packet'] for row in observations], inventory=inventory)
            self.assertFalse((artifacts.root / full_record['path']).exists())
        for key in ('openness_searches', 'account_records'):
            self.assertTrue(parts.covers_items(delivery['read_packets'], packet['problem_evidence'], key, inventory))

    def test_recorded_inventory_turn_prompt_and_request_keep_their_hashes(self):
        frozen = json.loads(FIXTURE.read_text(encoding='utf-8'))
        packet = make_search_packet()
        old = frozen['inventory']
        self.assertNotIn('unit_rule', old)
        self.assertEqual(parts.build_inventory(packet, 'problem_result', old), old)
        units = parts.enumerate_units(packet, 'problem_result', old)
        self.assertEqual(digest(parts.build_frame(packet, units)), frozen['frame_digest'])
        turn = parts.plan_turn(packet, old, [], series_id='stored', input_limit=1300000,
                               measure=lambda value: len(parts.render_json(value)), output_tokens=128000)
        self.assertEqual(digest(turn), frozen['turn_digest'])
        prompt = parts.build_turn_prompt('result', dict(orchestration='problem_creation', excerpts=True), turn[parts.KEY])
        self.assertEqual(digest(prompt), frozen['prompt_digest'])
        request = protocol.transport.request_body(frozen['route'], prompt['text'], turn, [], order=())
        self.assertEqual(digest(request), frozen['request_digest'])
        observation = observe(dict(turn, _test_inventory=old))
        following = parts.plan_turn(packet, old, [observation], series_id='stored', input_limit=1300000,
                                    measure=lambda value: len(parts.render_json(value)), output_tokens=128000)
        self.assertNotIn('unit_rule', following[parts.KEY])
        self.assertEqual(digest(following), frozen['next_turn_digest'])
        prompt = parts.build_turn_prompt('result', dict(orchestration='problem_creation', excerpts=True),
                                         following[parts.KEY])
        self.assertEqual(digest(prompt), frozen['next_prompt_digest'])
        request = protocol.transport.request_body(frozen['route'], prompt['text'], following, [], order=())
        self.assertEqual(digest(request), frozen['next_request_digest'])
        self.assertEqual(parts.build_inventory(packet, 'problem_result').get('unit_rule'), RULE)


class AccountRecordJourneyTests(PartsResultCase):
    def test_authenticated_series_rebuilds_accounts_and_openness_for_coverage_and_citations(self):
        self.oversized(count=0, room=100000)
        packet = make_search_packet(2, characters=65000, body_characters=30000)
        search = packet['problem_evidence']['openness_searches'][0]['search']
        packet['problem_evidence']['assessment_evidence'] = [dict(kind='record',
            record_kind='literature_search', id=search['id'], digest=digest(search), record=search)]
        for entry in packet['evidence']:
            self.assertEqual(self.case.artifacts.put(entry['content'].encode(), 'application/json'), entry['artifact'])
        for row in packet['problem_evidence']['account_records']:
            self.case.put(row['cite']['record_kind'], row['record']['id'], row['record'])
        for key in ('openness_searches', 'assessment_evidence', 'account_records', 'value'):
            self.case.expected[key] = packet['problem_evidence'][key]
        turns, final = self.finish_series()
        self.assertEqual(self.packet(turns[0])[parts.KEY].get('unit_rule'), RULE)
        inventory = parts.read_inventory(self.case.artifacts, self.packet(final)[parts.KEY]['inventory'])
        self.assertEqual(len(inventory['units']), 4)
        review = self.record_final(final)
        self.assertEqual(self.coverage(review), [])
        verified = parts.verify_series(self.case.read_records(), self.case.artifacts, final['id'])
        self.assertTrue(verified['complete'], verified['issues'])
        carried = verified['delivery']['carried_records']
        for row in packet['problem_evidence']['account_records']:
            cite = row['cite']
            self.assertTrue(any(kind == cite['record_kind'] and identifier == cite['id']
                                and record_digest == cite['digest']
                                for kind, identifier, record_digest, record, artifacts in carried))
