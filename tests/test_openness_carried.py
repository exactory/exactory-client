"""D2 G8: select authenticated own readings by unchanged unit, never by whole turn.

Synthetic scientific content; incident shape source:
20261112235500-openness-chain-final-turn-over-route-limit-after-57-turns.
"""

import copy
import hashlib
import importlib
import json
import unittest
from unittest.mock import patch
from pathlib import Path

from openness_reading_fixtures import OpennessReadingCase
from unbound_packet_fixtures import open_stored_case
from research_harness import openness_reading as reading, openness_reading_v7 as v7
from research_harness import review_context_repair
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.problem_openness import serialize_record


def compare_test_routes(first, second):
    """Supply the later D2 dependency on this base, which has no Ruling 2 comparator."""
    return all(first[key] == second[key] for key in ('adapter', 'model', 'endpoint', 'configuration'))


def build_identity(records, artifacts, assignment, revision=None):
    native = json.loads(artifacts.read(assignment['context']['native_packet']))
    minimum = native['scientific_context']['openness_minimum']
    return dict(reviewer_id=assignment['reviewer_id'], author_id=assignment['author_id'],
                route=records['review_route'][assignment['route_id']],
                subject_digest=native['subject_digest'], claim_digest=native['claim_digest'],
                formulations=dict(statement=minimum['statement'], neighbours=minimum['neighbours']),
                recorded_revision=revision if revision is not None else 1 + max(
                    row.get('recorded_revision', 0) for group in records.values() for row in group.values()))


SELECTION_V2 = 'exactory-openness-carried-selection-v2'


def read_cutoff(case, first):
    from research_harness.evaluation import Evaluation
    records = case.store.snapshot()['records']
    return Evaluation.of(records, case.artifacts).read_snapshot(first['recorded_revision'] - 1)


def start_v7(case, identifier='new', route='route'):
    from research_harness import review_protocol as protocol
    old = case.historical_v5
    case.historical_v5 = False
    try:
        native = case.export(name=identifier + '-export', assignment_id=identifier)
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
            first = case.mutate(protocol.record_assignment, dict(id=identifier, route_id=route, dossier_id=None,
                role='native_math', reviewer_id='openness-reviewer', author_id='problem-author',
                context=dict(native_packet=native)))['result']
    finally:
        case.historical_v5 = old
    records = case.store.snapshot()['records']
    identity = build_identity(records, case.artifacts, first, first['recorded_revision'])
    inventory = v7.read_carried_inventory(case.artifacts, first)
    selection = json.loads(case.artifacts.read(first['carried_selection']['artifact']))
    return first, inventory, identity, selection


def observe_v7(case, first):
    packet = case.read_packet(first['id'])
    return case.invoke(first['id'], dict(schema_version=2, stage='reading', notes='The new chain continues.',
        excerpts=[], gaps=[], passage_requests=[], **{key: packet['reading_turn'][key]
            for key in ('subject_digest', 'claim_digest', 'reviewer')}))


def finish_v7(case, first):
    from test_openness_carried_integration import CarriedIntegrationSupport
    case.old_records = copy.deepcopy(case.store.snapshot()['records'])
    CarriedIntegrationSupport.finish_chain(case, first['context']['native_packet'], first['id'],
        answer=lambda packet: dict(schema_version=2, stage='reading', notes='The new chain continues.',
            excerpts=[], gaps=[], passage_requests=[], **{key: packet['reading_turn'][key]
                for key in ('subject_digest', 'claim_digest', 'reviewer')}))
    records = case.store.snapshot()['records']
    return max((row for row in records['review_assignment'].values()
                if row.get('reading_chain', {}).get('first_assignment_id') == first['id']),
               key=lambda row: row['recorded_revision'])


class CarriedSelectionCases:
    reading_protocol = None
    route_model = 'gpt-6-astra'
    text_sizes = (50000, 50000, 50000)

    def setUp(self):
        super().setUp()
        self.carried = importlib.import_module('research_harness.openness_carried')
        comparator = patch.object(review_context_repair, 'is_same_route', compare_test_routes, create=True)
        comparator.start()
        self.addCleanup(comparator.stop)
        self.limit_input(1300000)
        self.native = self.export()
        self.first = self.assign('turn-1', self.native)
        packet = self.read_packet('turn-1')
        self.invoke('turn-1', self.build_reading_response(packet, passages=[dict(
            id='p1', version_id=self.versions[0], anchor='Paper 1 opening line.', characters=80)]))
        self.records = self.store.snapshot()['records']
        self.identity = build_identity(self.records, self.artifacts, self.first)
        self.planned = v7.build_inventory(self.records, self.artifacts,
                                         json.loads(self.artifacts.read(self.native)), review=self.first)
        self.inventory = self.planned['inventory']

    def select_readings(self, inventory=None, identity=None, records=None):
        return self.carried.select_carried_readings(records or self.records, self.artifacts,
            inventory or self.inventory, identity or self.identity)

    def change_unit(self, inventory, unit):
        unit['sha256'] = 'f' * 64
        unit['artifact'] = dict(unit['artifact'], sha256='f' * 64)
        for row in inventory['material']:
            if unit['handle'] in row['units']:
                row['sha256'] = 'f' * 64

    def verify_selection(self, selection, records=None, inventory=None):
        return self.carried.verify_carried_selection(records or self.records, self.artifacts,
            inventory or self.inventory, self.identity, selection)

    def test_g8_changed_neighbour_keeps_credit_and_filters_own_source_entries(self):
        observations = v7.observe_carried_chain(self.records, self.artifacts, self.first)
        delivered = [row for row in observations[0]['deliveries'] if row['identity']['kind'] == 'fulltext']
        self.assertEqual(len(delivered), 2)
        inventory = copy.deepcopy(self.inventory)
        changed = next(row for row in inventory['units'] if row['identity'] == delivered[1]['identity'])
        self.change_unit(inventory, changed)
        turns = self.select_readings(inventory)
        self.assertEqual(len(turns), 1)
        self.assertNotIn(changed['handle'], [row['unit'] for row in turns[0]['ranges']])
        self.assertTrue(turns[0]['ranges'])
        self.assertEqual(turns[0]['notes']['classification'], 'affected')
        self.assertIn(dict(identity=changed['identity'], sha256=delivered[1]['sha256'],
                           current_sha256='f' * 64), turns[0]['notes']['units'])
        self.assertNotIn(dict(kind='excerpt', index=1), turns[0]['entries'])
        selection = self.carried.build_carried_selection(self.records, self.artifacts, inventory, self.identity)
        self.verify_selection(selection['selection'], inventory=inventory)

    def test_entry_of_a_turn_whose_delivered_units_changed_is_carried(self):
        # The answer's source was not delivered by this chain. Its own bytes,
        # rather than the units delivered alongside the notes, decide its carry.
        native = self.export(name='entry-export', assignment_id='entry-first')
        self.assign('entry-first', native)
        self.invoke('entry-first', self.build_reading_response(self.read_packet('entry-first'), excerpts=[],
            passages=[dict(id='undelivered-source', version_id=self.versions[-1],
                           anchor='Paper 3 opening line.', characters=80)]))
        records = self.store.snapshot()['records']
        inventory = copy.deepcopy(self.inventory)
        for unit in inventory['units']:
            if unit['identity'].get('version_id') != self.versions[-1]:
                self.change_unit(inventory, unit)
        built = self.carried.build_carried_selection(records, self.artifacts, inventory,
                                                     build_identity(records, self.artifacts, self.first))
        turns = {row['origin']['assignment_id']: row for row in built['selection']['turns']}
        self.assertIn('entry-first', turns)
        self.assertIn(dict(kind='passage', index=0), turns['entry-first']['entries'])
        self.assertEqual(turns['entry-first']['ranges'], [])
        self.assertEqual(turns['entry-first']['searches'], [])

    def test_g8_identity_excludes_other_reviewer_model_effort_statement_and_author(self):
        changes = [dict(reviewer_id='another'), dict(author_id='another'),
                   dict(subject_digest='a' * 64), dict(claim_digest='b' * 64),
                   dict(formulations=dict(statement='Other statement', neighbours=[]))]
        for key in ('adapter', 'model', 'endpoint', 'configuration'):
            route = copy.deepcopy(self.identity['route'])
            route[key] = dict(route[key], effort='other') if key == 'configuration' else 'other'
            changes.append(dict(route=route))
        for change in changes:
            with self.subTest(change=change):
                identity = dict(self.identity, **change)
                self.assertEqual(self.select_readings(identity=identity), [])
                result = self.carried.list_passed_over_chains(self.records, self.artifacts, identity)
                self.assertEqual(bool(result['passed_over']), 'reviewer_id' in change)
                if result['passed_over']:
                    self.assertEqual(result['passed_over'][0]['reason'], 'reviewer_id')

    def test_normalized_reviewer_and_route_id_runtime_do_not_change_identity(self):
        identity = copy.deepcopy(self.identity)
        identity['reviewer_id'] = '  OPENNESS-REVIEWER  '
        identity['route'].update(id='new-route', runtime={'version': 'another'})
        self.assertTrue(self.select_readings(identity=identity))

    def test_g8_selection_builder_is_deterministic_and_keeps_its_input(self):
        before = copy.deepcopy(self.records)
        built = self.carried.build_carried_selection(self.records, self.artifacts, self.inventory, self.identity)
        self.assertEqual(built, self.carried.build_carried_selection(
            self.records, self.artifacts, self.inventory, self.identity))
        self.assertEqual(built['artifact']['sha256'], digest(built['selection']))
        self.assertEqual(built['content'], serialize_record(built['selection']))
        self.assertEqual(set(built['selection']), {'protocol', 'inventory_digest', 'turns'})
        self.verify_selection(built['selection'])
        self.assertEqual(self.records, before)

    def test_g8_two_complementary_chains_use_latest_first_but_record_order_in_result(self):
        # A later chain reads only work B first; work A keeps its earlier credit.
        native = self.export(priority=list(reversed(self.versions)), name='later-export', assignment_id='later')
        self.assign('later', native)
        self.invoke('later', self.build_reading_response(self.read_packet('later'), excerpts=[]))
        records = self.store.snapshot()['records']
        identity = build_identity(records, self.artifacts, self.first)
        turns = self.select_readings(records=records, identity=identity)
        fulltexts = {row['handle'] for row in self.inventory['units'] if row['kind'] == 'fulltext'}
        self.assertTrue(fulltexts <= {row['unit'] for turn in turns for row in turn['ranges']})
        owners = {turn['origin']['chain'] for turn in turns if any(r['unit'] in fulltexts for r in turn['ranges'])}
        self.assertEqual(owners, {'turn-1', 'later'})
        self.assertEqual([turn['origin']['chain'] for turn in turns], ['turn-1', 'later'])

    def test_g8_removed_dependency_affects_notes_and_added_unit_does_not(self):
        turns = self.select_readings()
        self.assertEqual(turns[0]['notes']['classification'], 'current')
        dependency = next(row for row in self.inventory['units'] if row['kind'] == 'fulltext')
        inventory = copy.deepcopy(self.inventory)
        inventory['units'] = [row for row in inventory['units'] if row['handle'] != dependency['handle']]
        turns = self.select_readings(inventory)
        self.assertEqual(turns[0]['notes']['classification'], 'affected')
        self.assertIn(dict(identity=dependency['identity'], sha256=dependency['sha256'], current_sha256=None),
                      turns[0]['notes']['units'])

    def test_search_credit_uses_completed_original_receipts_and_report_counts(self):
        native = self.native
        prior = 'turn-1'
        for number in range(2, 10):
            identifier = 'turn-%d' % number
            self.assign(identifier, native, prior=prior)
            packet = self.read_packet(identifier)
            if reading.find_stage(packet) == 'final':
                break
            self.invoke(identifier, self.build_reading_response(packet, excerpts=[]))
            prior = identifier
        records = self.store.snapshot()['records']
        identity = build_identity(records, self.artifacts, self.first)
        built = self.carried.build_carried_selection(records, self.artifacts, self.inventory, identity)
        credited = [sha for turn in built['selection']['turns'] for sha in turn['searches']]
        self.assertEqual(credited, [digest(self.search)])
        self.assertFalse(any(next(row for row in self.inventory['units'] if row['handle'] == span['unit'])[
            'identity'].get('record_kind') == 'literature_search'
            for turn in built['selection']['turns'] for span in turn['ranges']))
        report = self.carried.describe_carried_selection(records, self.artifacts, self.inventory, identity,
                                                        built['selection'])
        self.assertEqual(report['searches'], credited)
        self.assertEqual(report['credited_material_characters'] + report['remaining_material_characters'],
                         sum(row['characters'] for row in self.inventory['material']))
        self.assertEqual(report['notes']['current'], len(built['selection']['turns']))
        self.assertEqual(report['notes']['affected'], 0)
        self.assertEqual(report['credited_characters'] + report['remaining_characters'],
                         sum(row['characters'] for row in self.inventory['units']))

    def test_refuses_changed_range_digest_notes_classification_and_unknown_members(self):
        built = self.carried.build_carried_selection(self.records, self.artifacts, self.inventory, self.identity)
        for mutation in ('range', 'notes', 'member', 'origin', 'empty', 'inventory'):
            with self.subTest(mutation=mutation):
                selection = copy.deepcopy(built['selection'])
                turn = selection['turns'][0]
                if mutation == 'range':
                    turn['ranges'][0]['sha256'] = '0' * 64
                elif mutation == 'notes':
                    turn['notes']['classification'] = 'affected'
                elif mutation == 'member':
                    turn['entries'][0]['sha256'] = '0' * 64
                elif mutation == 'origin':
                    turn['origin']['attempt_id'] = 'other'
                elif mutation == 'empty':
                    turn['ranges'], turn['searches'] = [], []
                else:
                    selection['inventory_digest'] = '0' * 64
                with self.assertRaises(ResearchError) as caught:
                    if selection['protocol'] == SELECTION_V2:
                        from research_harness.evaluation import Evaluation
                        self.carried.verify_stored_selection(self.records, self.artifacts, self.inventory,
                            self.identity, selection, cutoff=Evaluation.of(self.records, self.artifacts))
                    else:
                        self.verify_selection(selection)
                self.assertEqual(caught.exception.code, 'openness_reading_incomplete')
                self.assertEqual(caught.exception.message, v7.SELECTION_MISMATCH_MESSAGE)

    def test_stored_selection_is_the_selection_of_the_cutoff(self):
        first, inventory, identity, selection = start_v7(self)
        self.assertEqual(first['carried_selection']['protocol'], SELECTION_V2)
        cutoff = read_cutoff(self, first)
        built = self.carried.build_carried_selection(cutoff.records, cutoff, inventory, identity)['selection']
        self.assertEqual(set(built), {'protocol', 'inventory_digest', 'turns'})
        self.assertEqual(selection['turns'], built['turns'])
        self.carried.verify_stored_selection(self.store.snapshot()['records'], self.artifacts,
            inventory, identity, selection, cutoff=cutoff)
        observe_v7(self, first)
        self.assign('old-next', self.native, prior='turn-1')
        self.invoke('old-next', self.build_reading_response(self.read_packet('old-next'), excerpts=[]))
        records = self.store.snapshot()['records']
        fresh_cutoff = read_cutoff(self, first)
        self.assertEqual(self.carried.select_carried_readings(fresh_cutoff.records, fresh_cutoff,
            inventory, identity), selection['turns'])
        self.carried.verify_stored_selection(records, self.artifacts, inventory, identity, selection,
                                               cutoff=fresh_cutoff)
        v7.verify_problem_coverage(records, self.artifacts, records['review_assignment']['new'])
        with self.assertRaises(ResearchError):
            v7.verify_problem_coverage(dict(records), self.artifacts, records['review_assignment']['new'])
        for stamp in (first['recorded_revision'] - 1, first['recorded_revision'] + 1):
            with self.subTest(spoofed_stamp=stamp):
                changed = copy.copy(records)
                spoofed = dict(first, recorded_revision=stamp)
                changed['review_assignment'] = dict(records['review_assignment'], new=spoofed)
                with self.assertRaises(ResearchError) as caught:
                    v7.verify_problem_coverage(changed, self.artifacts, spoofed)
                self.assertEqual(caught.exception.message, v7.ORIGIN_VERIFICATION_MESSAGE)

    def test_chain_recorded_later_does_not_change_a_stored_selection(self):
        first, inventory, identity, selection = start_v7(self)
        self.assertEqual(first['carried_selection']['protocol'], SELECTION_V2)
        later = self.export(name='later-export', assignment_id='later')
        self.assign('later', later)
        self.invoke('later', self.build_reading_response(self.read_packet('later'), excerpts=[]))
        records = self.store.snapshot()['records']
        self.assertNotIn('later', [row['chain'] for row in self.carried.list_qualifying_chains(
            records, self.artifacts, identity)])
        cutoff = read_cutoff(self, first)
        self.carried.verify_stored_selection(records, self.artifacts, inventory, identity, selection, cutoff=cutoff)
        self.assertEqual(self.carried.select_carried_readings(cutoff.records, cutoff, inventory, identity),
                         selection['turns'])

    def test_turn_that_becomes_ready_after_the_cutoff_does_not_change_a_stored_selection(self):
        from codex_route_fixtures import record_codex_route, probe_codex_route
        from research_harness import review_protocol as protocol, review_transport as transport
        record_codex_route(self, 'second-route', model=self.route_model)
        with patch.object(transport, 'send_request', side_effect=ResearchError(
                'fixture_probe_failed', 'The authored probe failed.')):
            protocol.probe_route(self.store, dict(id='failed-probe', route_id='route'), credential='test-only',
                expected_revision=self.store.revision, request_id='failed-probe')
        first, inventory, identity, selection = start_v7(self, route='second-route')
        self.assertEqual(first['carried_selection']['protocol'], SELECTION_V2)
        self.assertEqual(selection['turns'], [])
        probe_codex_route(self, 'recovered-probe')
        records = self.store.snapshot()['records']
        self.carried.verify_stored_selection(records, self.artifacts, inventory, identity, selection,
                                               cutoff=read_cutoff(self, first))
        second, _, _, following = start_v7(self, identifier='following', route='second-route')
        self.assertEqual(second['carried_selection']['protocol'], SELECTION_V2)
        self.assertIn('turn-1', [row['origin']['chain'] for row in following['turns']])
        final = finish_v7(self, first)
        reading.verify_chain(self.store.snapshot()['records'], self.artifacts, final)

    def test_contamination_after_the_cutoff_counts_for_carried_turns_alone(self):
        from research_harness import review_protocol as protocol
        later = self.export(name='rereading-export', assignment_id='rereading')
        self.assign('rereading', later)
        self.invoke('rereading', self.build_reading_response(self.read_packet('rereading'), excerpts=[]))
        first, inventory, identity, selection = start_v7(self)
        self.assertEqual(first['carried_selection']['protocol'], SELECTION_V2)
        self.assertNotIn('turn-1', [row['origin']['chain'] for row in selection['turns']])
        final = finish_v7(self, first)
        exposure = self.artifacts.put(b'Authored evidence of context exposure.', 'text/plain')
        for assignment in ('turn-1', 'rereading'):
            self.mutate(protocol.record_context_event, dict(id='exposure-' + assignment,
                assignment_id=assignment, kind='contamination', source='author_history',
                reason='A recorded context exposure after the cutoff.', evidence=[exposure]))
            records = self.store.snapshot()['records']
            if assignment == 'turn-1':
                self.carried.verify_stored_selection(records, self.artifacts, inventory, identity, selection,
                                                       cutoff=read_cutoff(self, first))
                reading.verify_chain(records, self.artifacts, final)
            else:
                with self.assertRaises(ResearchError) as caught:
                    self.carried.verify_stored_selection(records, self.artifacts, inventory, identity, selection,
                                                           cutoff=read_cutoff(self, first))
                self.assertEqual(caught.exception.code, 'openness_reading_incomplete')
                with self.assertRaises(ResearchError):
                    reading.verify_chain(records, self.artifacts, final)

    def test_superseded_reading_leaves_its_entries_with_the_earlier_chain(self):
        native = self.export(priority=[self.versions[0], self.versions[2], self.versions[1]],
                             name='reread-export', assignment_id='reread')
        self.assign('reread', native)
        self.invoke('reread', self.build_reading_response(self.read_packet('reread'), excerpts=[]))
        records = self.store.snapshot()['records']
        identity = build_identity(records, self.artifacts, self.first)
        built = self.carried.build_carried_selection(records, self.artifacts, self.inventory, identity)
        self.assertEqual(built['selection']['protocol'], SELECTION_V2)
        earlier = next(row for row in built['selection']['turns'] if row['origin']['chain'] == 'turn-1')
        self.assertIn(dict(kind='excerpt', index=1), earlier['entries'])
        self.assertNotIn(dict(kind='excerpt', index=0), earlier['entries'])
        units = {row['identity'].get('version_id'): row['handle'] for row in self.inventory['units']
                 if row['kind'] == 'fulltext'}
        self.assertIn(units[self.versions[1]], [span['unit'] for span in earlier['ranges']])
        self.assertNotIn(units[self.versions[0]], [span['unit'] for span in earlier['ranges']])
        self.assertEqual(records['review_attempt']['attempt-turn-1'], self.records['review_attempt']['attempt-turn-1'])

    def test_report_places_every_output_in_one_state(self):
        for identifier, excerpts in (('no-output', []), ('reread', None)):
            native = self.export(priority=[self.versions[0], self.versions[2], self.versions[1]],
                                 name=identifier + '-export', assignment_id=identifier)
            self.assign(identifier, native)
            self.invoke(identifier, self.build_reading_response(self.read_packet(identifier), excerpts=excerpts))
        inventory = copy.deepcopy(self.inventory)
        changed = next(row for row in inventory['units'] if row['identity'].get('version_id') == self.versions[2])
        self.change_unit(inventory, changed)
        records = self.store.snapshot()['records']
        identity = build_identity(records, self.artifacts, self.first)
        built = self.carried.build_carried_selection(records, self.artifacts, inventory, identity)
        report = self.carried.describe_carried_selection(records, self.artifacts, inventory, identity,
                                                         built['selection'])
        self.assertIn('outputs', report)
        self.assertEqual({row['chain'] for row in report['outputs']}, {'turn-1', 'no-output', 'reread'})
        for row in report['outputs']:
            chain = next(chain for chain in self.carried.list_qualifying_chains(records, self.artifacts,
                identity) if chain['chain'] == row['chain'])
            observed = self.carried.observe_qualifying_chain(records, self.artifacts, chain, identity)
            entries = [(kind, entry) for turn in observed for kind in ('excerpt', 'passage')
                       for entry in turn[kind + 's']
                       if entry['status'] == ('verified' if kind == 'excerpt' else 'delivered')]
            states = [row[key] for key in ('carried', 'superseded', 'source_changed')]
            self.assertEqual(sum(state['excerpts'] for state in states), sum(kind == 'excerpt' for kind, _ in entries))
            self.assertEqual(sum(state['passages'] for state in states), sum(kind == 'passage' for kind, _ in entries))
            self.assertEqual(sum(state['characters'] for state in states), sum(len(entry['text']) for _, entry in entries))
            self.assertEqual(row['reading_turns'], len(observed))
            self.assertEqual(row['carried_turns'], sum(turn['origin']['chain'] == row['chain']
                                                     for turn in built['selection']['turns']))
        empty = next(row for row in report['outputs'] if row['chain'] == 'no-output')
        self.assertEqual(empty['carried_turns'], 0)
        old = next(row for row in report['outputs'] if row['chain'] == 'turn-1')
        self.assertGreater(old['superseded']['excerpts'], 0)
        self.assertTrue(old['message'])
        latest = next(row for row in report['outputs'] if row['chain'] == 'reread')
        self.assertGreater(latest['source_changed']['excerpts'], 0)

    def test_g8_observation_changed_byte_and_entry_span_digest_fail_authentication(self):
        built = self.carried.build_carried_selection(self.records, self.artifacts, self.inventory, self.identity)
        for name in ('output', 'request'):
            with self.subTest(artifact=name):
                attempt = self.records['review_attempt']['attempt-turn-1']
                path = self.root / attempt[name]['path']
                original = path.read_bytes()
                path.chmod(0o600)
                try:
                    path.write_bytes(original + b' ')
                    with self.assertRaises(ResearchError) as caught:
                        self.verify_selection(built['selection'])
                    self.assertEqual(caught.exception.code, 'openness_reading_incomplete')
                finally:
                    path.write_bytes(original)
        records = copy.deepcopy(self.records)
        attempt = records['review_attempt']['attempt-turn-1']
        output = json.loads(self.artifacts.read(attempt['output']))
        output['excerpts'][0]['sha256'] = '0' * 64
        attempt['output'] = self.artifacts.put(serialize_record(output), 'application/json')
        attempt['output_digest'] = digest(output)
        with self.assertRaises(ResearchError):
            self.verify_selection(built['selection'], records=records)

    def test_notes_wait_for_the_requesting_turns_passage_to_reach_its_successor(self):
        native = self.export(name='passage-export', assignment_id='passage-first')
        self.assign('passage-first', native)
        packet = self.read_packet('passage-first')
        self.invoke('passage-first', self.build_reading_response(packet, passages=[dict(
            id='future-source', version_id=self.versions[-1], anchor='Paper 3 opening line.', characters=80)]))
        records = self.store.snapshot()['records']
        inventory = copy.deepcopy(self.inventory)
        changed = next(row for row in inventory['units'] if row['identity'].get('version_id') == self.versions[-1])
        self.change_unit(inventory, changed)
        identity = build_identity(records, self.artifacts, self.first)
        turns = self.select_readings(inventory, identity=identity, records=records)
        self.assertEqual([row['notes']['classification'] for row in turns], ['current'])
        built = self.carried.build_carried_selection(records, self.artifacts, inventory, identity)
        self.carried.verify_carried_selection(records, self.artifacts, inventory, identity, built['selection'])

    def test_q12_selection_projects_to_affected_notes_and_overflow_without_source_rereading(self):
        from test_review_capacity_plan import build_plan
        from test_review_route_capacity import build_route
        inventory = copy.deepcopy(self.inventory)
        changed = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
        self.change_unit(inventory, changed)
        built = self.carried.build_carried_selection(self.records, self.artifacts, inventory, self.identity)
        projections = self.verify_selection(built['selection'], inventory=inventory)
        plan = build_plan()
        self.assertEqual(plan['task']['final']['material_allowance'], 480000)
        state = v7.build_chain_state(inventory, projections, route=build_route(effort='xhigh'), plan=plan)
        affected = [row for row in state['inventory']['units'] if row['kind'] == 'carried_reading']
        self.assertEqual(len(affected), 1)
        self.assertEqual(affected[0]['identity']['content_kind'], 'affected_notes')
        self.assertTrue(state['state']['deliveries'])
        self.assertNotIn(changed['handle'], {row['unit'] for row in state['state']['deliveries']})

    def test_overlapping_ranges_are_taken_whole_only_when_they_add_characters(self):
        observation = v7.observe_carried_chain(self.records, self.artifacts, self.first)[0]
        original = observation['deliveries'][0]
        observation = copy.deepcopy(observation)
        pieces = []
        for part, (start, end) in enumerate(((0, 20000), (10000, 30000), (10000, 20000), (30000, 50000)), 1):
            text = original['text'][start:end]
            pieces.append(dict(original, start=start, end=end, part=part, parts=4,
                               text=text, span_sha256=hashlib.sha256(text.encode()).hexdigest()))
        observation['deliveries'] = pieces + observation['deliveries'][1:]
        protocol = observation['origin']['protocol']
        # Exercise the selection policy separately from authentication of the source observation.
        observers = dict(self.carried.CHAIN_OBSERVERS, **{protocol: lambda *args: [observation]})
        with patch.object(self.carried, 'CHAIN_OBSERVERS', observers):
            built = self.carried.build_carried_selection(self.records, self.artifacts, self.inventory, self.identity)
            self.verify_selection(built['selection'])
            ranges = built['selection']['turns'][0]['ranges']
            unit = next(row for row in self.inventory['units'] if row['identity'] == original['identity'])
            self.assertEqual([(row['start'], row['end']) for row in ranges if row['unit'] == unit['handle']],
                             [(0, 20000), (10000, 30000), (30000, 50000)])
            report = self.carried.describe_carried_selection(self.records, self.artifacts,
                self.inventory, self.identity, built['selection'])
            self.assertEqual(next(row for row in report['ranges'] if row['unit'] == unit['handle'])['characters'], 50000)

    def test_verifies_a_published_selection_artifact(self):
        built = self.carried.build_carried_selection(self.records, self.artifacts, self.inventory, self.identity)
        reference = self.artifacts.put(built['content'], 'application/json')
        self.assertEqual(reference, built['artifact'])
        self.assertEqual(self.verify_selection(reference), self.verify_selection(built['selection']))

    def test_not_ready_contaminated_or_recorded_after_cutoff_gives_no_credit(self):
        self.assertEqual(self.select_readings(identity=dict(self.identity,
            recorded_revision=self.records['review_attempt']['attempt-turn-1']['recorded_revision'])), [])
        records = copy.deepcopy(self.records)
        records.setdefault('review_context_event', {})['contamination'] = dict(
            id='contamination', assignment_id='turn-1', kind='contamination', source='owner',
            recorded_revision=self.identity['recorded_revision'] + 1)
        self.assertEqual(self.select_readings(records=records), [])


class V5CarriedSelectionTests(CarriedSelectionCases, OpennessReadingCase):
    historical_v5 = True


class V6CarriedSelectionTests(CarriedSelectionCases, OpennessReadingCase):
    def assign_first(self, identifier, bindings=None, *, reviewer_id='openness-reviewer', route_id='route'):
        from research_harness import review_protocol as protocol
        from test_openness_carried_integration import export_bindings
        bindings = bindings or [dict(formulation=self.selected['statement'], search_id=self.search['id'])]
        return self.mutate(protocol.record_assignment, dict(id=identifier, route_id=route_id, dossier_id=None,
            role='native_math', reviewer_id=reviewer_id, author_id='problem-author',
            context=dict(native_packet=export_bindings(self, identifier, bindings, reviewer_id))))['result']

    def test_selection_reads_the_artifacts_that_its_result_depends_on(self):
        from codex_route_fixtures import record_codex_route
        from research_harness import review_excerpts
        from test_openness_carried_integration import bind_statement_searches
        self.assign_first('other-reviewer', reviewer_id='another-reviewer')
        record_codex_route(self, 'other-route', model=self.route_model, output_tokens=64000)
        self.assign_first('other-route', route_id='other-route')
        # One request holds every text, so this review stays a single turn.
        with patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 2 * sum(self.text_sizes)):
            self.assign_first('single')
        self.assign_first('other-statement', bind_statement_searches(self, 'other-statement', ['Neighbour 0.']))
        records = self.store.snapshot()['records']
        assignments = records['review_assignment']
        self.assertNotIn('reading_chain', assignments['single'])
        identity = build_identity(records, self.artifacts, self.first)
        with patch.object(self.artifacts, 'read', wraps=self.artifacts.read) as read:
            turns = self.carried.select_carried_readings(records, self.artifacts, self.inventory, identity)
        paths = {call.args[0]['path'] for call in read.call_args_list}
        self.assertEqual({identifier: [name for name, reference in (
                              ('packet', assignments[identifier]['packet']),
                              ('native_packet', assignments[identifier]['context']['native_packet']))
                              if reference['path'] in paths]
                          for identifier in ('other-reviewer', 'other-route', 'single', 'other-statement', 'turn-1')},
                         {'other-reviewer': [], 'other-route': [], 'single': [], 'other-statement': ['native_packet'],
                          'turn-1': ['packet', 'native_packet']})
        self.assertIn(records['review_attempt']['attempt-turn-1']['output']['path'], paths)
        self.assertEqual([row['origin']['assignment_id'] for row in turns], ['turn-1'])

    def test_lost_native_packet_of_a_candidate_refuses_the_verification(self):
        from test_openness_carried_integration import bind_statement_searches, remove_artifact
        bindings = bind_statement_searches(self, 'other-statement', ['Neighbour 0.'])
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
            first = self.assign_first('new', bindings)
        self.assertEqual(first['carried_selection']['protocol'], SELECTION_V2)
        final = finish_v7(self, first)
        with remove_artifact(self, self.first['context']['native_packet']), self.assertRaises(ResearchError) as caught:
            reading.verify_chain(self.store.snapshot()['records'], self.artifacts, final)
        self.assertEqual(caught.exception.code, 'artifact_missing')
        self.assertTrue(reading.verify_chain(self.store.snapshot()['records'], self.artifacts, final))


class StoredCaptureSelectionTests(unittest.TestCase):
    def test_g8_c8_completed_chosen_capture_and_stopped_pending_capture(self):
        carried = importlib.import_module('research_harness.openness_carried')
        with patch.object(review_context_repair, 'is_same_route', compare_test_routes, create=True):
            for scenario in ('workaround', 'stopped-both-observed'):
                with self.subTest(scenario=scenario):
                    case, metadata = open_stored_case(self, 5, scenario)
                    records = case.store.snapshot()['records']
                    before = copy.deepcopy(records)
                    assignment = records['review_assignment'][metadata['turns'][-1]]
                    native = json.loads(case.artifacts.read(metadata['native']))
                    inventory = v7.build_inventory(records, case.artifacts, native, review=assignment)['inventory']
                    identity = build_identity(records, case.artifacts, assignment)
                    built = carried.build_carried_selection(records, case.artifacts, inventory, identity)
                    carried.verify_carried_selection(records, case.artifacts, inventory, identity, built['selection'])
                    handles = {span['unit'] for turn in built['selection']['turns'] for span in turn['ranges']}
                    chosen = next(row for row in inventory['units'] if row['identity'].get('version_id') == metadata['work'])
                    self.assertEqual(chosen['sha256'], metadata['compact']['text']['sha256'])
                    self.assertEqual(chosen['handle'] in handles, scenario == 'workaround')
                    self.assertTrue(handles)
                    self.assertEqual(records, before)


class PartialRangeCases:
    reading_protocol = None
    route_model = 'gpt-6-astra'
    text_sizes = (300000,)

    def test_g8_two_of_three_parts_keep_exact_ranges_and_read_only_the_remainder(self):
        carried = importlib.import_module('research_harness.openness_carried')
        self.limit_input(1300000)
        native = self.export(assignment_id='part-1')
        prior = None
        for number in (1, 2):
            identifier = 'part-%d' % number
            self.assign(identifier, native, prior=prior)
            self.invoke(identifier, self.build_reading_response(self.read_packet(identifier), excerpts=[]))
            prior = identifier
        records = self.store.snapshot()['records']
        first = records['review_assignment']['part-1']
        endpoint = records['review_assignment']['part-2']
        inventory = v7.build_inventory(records, self.artifacts, json.loads(self.artifacts.read(native)),
                                       review=endpoint)['inventory']
        identity = build_identity(records, self.artifacts, first)
        with patch.object(review_context_repair, 'is_same_route', compare_test_routes, create=True):
            built = carried.build_carried_selection(records, self.artifacts, inventory, identity)
            carried.verify_carried_selection(records, self.artifacts, inventory, identity, built['selection'])
            unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
            ranges = [span for turn in built['selection']['turns'] for span in turn['ranges']
                      if span['unit'] == unit['handle']]
            self.assertEqual([span['part'] for span in ranges], [1, 2])
            self.assertEqual([span['parts'] for span in ranges], [3, 3])
            report = carried.describe_carried_selection(records, self.artifacts, inventory, identity, built['selection'])
            missing = next(row for row in report['remaining'] if row['unit'] == unit['handle'])
            self.assertEqual(missing['ranges'], [dict(start=ranges[-1]['end'], end=unit['characters'],
                                                     characters=unit['characters'] - ranges[-1]['end'])])


class V5PartialRangeTests(PartialRangeCases, OpennessReadingCase):
    historical_v5 = True


class V6PartialRangeTests(PartialRangeCases, OpennessReadingCase):
    pass


class PartialSearchSupport:
    reading_protocol = None
    route_model = 'gpt-6-astra'
    route_output_tokens = 128000
    text_sizes = (1000,)

    def setUp(self):
        super().setUp()
        self.carried = importlib.import_module('research_harness.openness_carried')
        comparator = patch.object(review_context_repair, 'is_same_route', compare_test_routes, create=True)
        comparator.start()
        self.addCleanup(comparator.stop)
        from research_harness import review_excerpts
        chunking = patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 12000)
        chunking.start()
        self.addCleanup(chunking.stop)
        self.limit_input(1300000)

    def record_searches(self):
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        return [ProblemOpennessMinimumTests.search(self, self.versions,
            query='Partial search marker. ' + 'q' * 9000)]

    def build_partial_chain(self, stop_at_partial=None):
        self.native = self.export(assignment_id='old-1')
        prior, partials, notes = None, [], {}
        for number in range(1, 15):
            identifier = 'old-%d' % number
            first = self.assign(identifier, self.native, prior=prior)
            packet = self.read_packet(identifier)
            if 'reading_turn' not in packet:
                break
            now = packet['reading_turn']['reading_plan']['delivered_now']
            is_partial = any(row.get('record_kind') == 'literature_search'
                             and row['part'] < row['parts'] for row in now)
            if is_partial:
                partials.append(identifier)
            answer = self.build_reading_response(packet)
            answer['notes'] = 'Notes from ready reading %d.' % number
            notes[identifier] = answer['notes']
            if is_partial and len(partials) == 1:
                answer['excerpts'] = [dict(record_kind='literature_search', record_id=self.search['id'],
                    text='Partial search marker.', purpose='The earlier portion of the original search.')]
                answer['passage_requests'] = [dict(id='search-answer', record_kind='literature_search',
                    record_id=self.search['id'], anchor='Partial search marker.', characters=80)]
            observed = self.invoke(identifier, answer)
            self.assertNotIn('output_refusal', observed, observed.get('output_refusal'))
            prior = identifier
            if stop_at_partial is not None and len(partials) == stop_at_partial:
                break
        else:
            self.fail('The bounded partial-search fixture must finish.')
        records = self.store.snapshot()['records']
        first = records['review_assignment']['old-1']
        inventory = v7.build_inventory(records, self.artifacts, json.loads(self.artifacts.read(self.native)),
                                       review=first)['inventory']
        identity = build_identity(records, self.artifacts, first)
        self.assertTrue(partials, 'The fixture must deliver an earlier search part.')
        return records, inventory, identity, partials, notes

    def assert_selection_refused(self, records, inventory, identity, selection, cutoff):
        with self.assertRaises(ResearchError) as caught:
            self.carried.verify_stored_selection(records, self.artifacts, inventory, identity, selection,
                                                  cutoff=cutoff)
        self.assertEqual(caught.exception.code, 'openness_reading_incomplete')
        self.assertEqual(caught.exception.message, v7.SELECTION_MISMATCH_MESSAGE)

class PartialSearchCases(PartialSearchSupport):
    def test_turn_with_an_earlier_part_of_a_search_is_carried_with_its_entries(self):
        records, inventory, identity, partials, _ = self.build_partial_chain()
        built = self.carried.build_carried_selection(records, self.artifacts, inventory, identity)
        turns = {row['origin']['assignment_id']: row for row in built['selection']['turns']}
        self.assertIn(partials[0], turns)
        self.assertIn(dict(kind='excerpt', index=0), turns[partials[0]]['entries'])
        self.assertIn(dict(kind='passage', index=0), turns[partials[0]]['entries'])
        self.assertEqual((turns[partials[0]]['ranges'], turns[partials[0]]['searches']), ([], []))

    def test_last_ready_turn_without_a_credit_gives_the_starting_notes(self):
        _, _, _, partials, notes = self.build_partial_chain(stop_at_partial=1)
        start_v7(self)
        self.assertEqual(self.read_packet('new')['reading_turn']['notes'], notes[partials[-1]])

    def test_verifier_refuses_a_selection_that_lacks_a_turn_the_end_of_a_chain_or_a_chain(self):
        _, _, _, partials, _ = self.build_partial_chain(stop_at_partial=2)
        first, inventory, identity, selection = start_v7(self)
        self.assertEqual(first['carried_selection']['protocol'], SELECTION_V2)
        cutoff = read_cutoff(self, first)
        records = self.store.snapshot()['records']
        self.assertFalse(next(row for row in selection['turns']
                              if row['origin']['assignment_id'] == partials[-1])['searches'])
        for removed in ((partials[0],), (partials[-1],), tuple(partials[-2:]),
                        tuple(row['origin']['assignment_id'] for row in selection['turns'])):
            with self.subTest(removed=removed):
                changed = copy.deepcopy(selection)
                changed['turns'] = [row for row in changed['turns']
                                    if row['origin']['assignment_id'] not in removed]
                self.assert_selection_refused(records, inventory, identity, changed, cutoff)

    def test_verifier_refuses_a_changed_entry_or_credit(self):
        self.build_partial_chain()
        overlap = self.export(name='overlap-export', assignment_id='overlap')
        self.assign('overlap', overlap)
        self.invoke('overlap', self.build_reading_response(self.read_packet('overlap'), excerpts=[]))
        first, inventory, identity, selection = start_v7(self)
        self.assertEqual(first['carried_selection']['protocol'], SELECTION_V2)
        cutoff = read_cutoff(self, first)
        records = self.store.snapshot()['records']
        for mutation in ('entry', 'superseded-entry', 'search', 'move-credit'):
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(selection)
                if mutation == 'entry':
                    next(row for row in changed['turns'] if row['entries'])['entries'].pop()
                elif mutation == 'superseded-entry':
                    next(row for row in changed['turns'] if row['origin']['assignment_id'] == 'old-1')[
                        'entries'].append(dict(kind='excerpt', index=0))
                elif mutation == 'search':
                    next(row for row in changed['turns'] if row['searches'])['searches'].pop()
                else:
                    handle = next(row['handle'] for row in inventory['units'] if row['kind'] == 'fulltext')
                    source = next(row for row in changed['turns']
                                  if any(span['unit'] == handle for span in row['ranges']))
                    target = next(row for row in changed['turns'] if row['origin']['assignment_id'] == 'old-1')
                    span = next(span for span in source['ranges'] if span['unit'] == handle)
                    source['ranges'].remove(span)
                    target['ranges'].append(span)
                self.assert_selection_refused(records, inventory, identity, changed, cutoff)
        # The same old chain can run another authentic turn after this cutoff.
        self.assign('overlap-2', overlap, prior='overlap')
        self.invoke('overlap-2', self.build_reading_response(self.read_packet('overlap-2'), excerpts=[]))
        current = self.store.snapshot()['records']
        later_turns = self.carried.select_carried_readings(current, self.artifacts, inventory,
            build_identity(current, self.artifacts, first))
        extra = next(row for row in later_turns if row['origin']['assignment_id'] == 'overlap-2')
        changed = copy.deepcopy(selection)
        changed['turns'].append(extra)
        self.assert_selection_refused(current, inventory, identity, changed, cutoff)


class V5PartialSearchTests(PartialSearchCases, OpennessReadingCase):
    historical_v5 = True


class V6PartialSearchTests(PartialSearchCases, OpennessReadingCase):
    pass


class StoredV1SelectionTests(PartialSearchSupport, OpennessReadingCase):
    historical_v5 = True

    def test_selection_of_0_91_1_verifies_under_its_own_rule(self):
        from openness_legacy_fixtures import select_as_0_91_1
        records, inventory, identity, partials, _ = self.build_partial_chain()
        selection = dict(protocol='exactory-openness-carried-selection-v1', inventory_digest=digest(inventory),
                         turns=select_as_0_91_1(records, self.artifacts, inventory, identity))
        self.assertTrue(self.carried.verify_carried_selection(records, self.artifacts, inventory, identity, selection))
        self.assertNotIn(partials[0], [row['origin']['assignment_id'] for row in selection['turns']])
        changed = copy.deepcopy(selection)
        changed['turns'][0]['ranges'], changed['turns'][0]['searches'] = [], []
        with self.assertRaises(ResearchError) as caught:
            self.carried.verify_carried_selection(records, self.artifacts, inventory, identity, changed)
        self.assertEqual(caught.exception.code, 'openness_reading_incomplete')


def build_rule_fixture(protocol='exactory-openness-reading-v5', *, complete_search=True):
    """Fixed own-turn observations for search, foreign passage and span supersession."""
    from research_harness.artifacts import describe_artifact
    from test_openness_reading_v7_state import build_source, build_observation
    first, a = build_source('First half establishes the bound. Second half supplies the counterexample.', 'text-a')
    second, b = build_source('Only the older chain delivered this text; the later chain requested its passage.', 'text-b')
    search = dict(id='fixed-search', query='A fixed synthetic query.', verdict='No matching theorem.')
    content = serialize_record(search)
    record = dict(kind='record', record_kind='literature_search', record_id=search['id'],
                  artifact=describe_artifact(content, 'application/json'))
    inventory = v7.plan_inventory([first, second, record],
        {row['artifact']['sha256']: body for row, body in ((first, a), (second, b), (record, content))})['inventory']

    def observe(chain, number, row=first, body=a):
        observation = build_observation(row, body, number, excerpt=False)
        identifier = chain if number == 1 else '%s-%d' % (chain, number)
        observation['origin'] = dict(chain=chain, protocol=protocol, assignment_id=identifier,
                                     attempt_id='attempt-' + identifier)
        observation['turn'].update(assignment_id=identifier, attempt_id='attempt-' + identifier)
        observation['notes'] = '%s notes %d.' % (chain, number)
        return observation

    def entry(row, body, start, end, status='verified'):
        text = body.decode()[start:end]
        return dict(identity=v7._describe_identity(row), source_sha256=row['artifact']['sha256'],
                    start=start, end=end, sha256=hashlib.sha256(text.encode()).hexdigest(),
                    text=text, purpose='A fixed supporting span.', status=status)

    older = observe('older', 1)
    older['deliveries'] += build_observation(second, b, excerpt=False)['deliveries']
    older['excerpts'] = [entry(first, a, 0, 15), entry(first, a, 50, 65), entry(first, a, 30, 50)]
    partial = observe('older', 2)
    partial['deliveries'] = []
    partial['excerpts'] = [entry(record, content, 0, 20)]
    newer = observe('newer', 1)
    newer['deliveries'][0].update(end=40, text=a[:40].decode(),
                                span_sha256=hashlib.sha256(a[:40]).hexdigest())
    newer['passages'] = [entry(second, b, 0, 25, status='delivered')]
    completed = observe('newer', 2)
    completed['deliveries'] = []
    completed['searches'] = [record['artifact']['sha256']] if complete_search else []
    tail = observe('newer', 3)
    tail['deliveries'] = []
    chains = [('newer', [newer, completed, tail]), ('older', [older, partial])]
    records = dict(review_assignment={}, review_attempt={})
    for start, (chain, observations) in zip((30, 10), chains):
        for offset, observation in enumerate(observations):
            origin = observation['origin']
            records['review_assignment'][origin['assignment_id']] = dict(id=origin['assignment_id'],
                recorded_revision=start + offset * 2)
            records['review_attempt'][origin['attempt_id']] = dict(recorded_revision=start + offset * 2 + 1)
    return inventory, chains, records


def select_rule_fixture(carried, artifacts, inventory, chains, records):
    observations = dict(chains)
    candidates = [dict(chain=chain, protocol=turns[0]['origin']['protocol'],
        recorded_revision=records['review_assignment'][chain]['recorded_revision']) for chain, turns in chains]
    with patch.object(carried, 'list_qualifying_chains', return_value=candidates), \
            patch.object(carried, 'observe_qualifying_chain', side_effect=lambda r, a, chain, i: observations[chain['chain']]), \
            patch.object(carried, '_list_search_dependencies', return_value={}):
        return carried.build_carried_selection(records, artifacts, inventory, dict(recorded_revision=100))['selection']


class CarryRuleTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from research_harness.artifacts import ArtifactStore
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.artifacts = ArtifactStore(Path(self.temporary.name))
        self.carried = importlib.import_module('research_harness.openness_carried')

    def test_supersession_follows_the_credit_of_later_chains(self):
        inventory, chains, records = build_rule_fixture()
        selected = select_rule_fixture(self.carried, self.artifacts, inventory, chains, records)
        self.assertEqual(selected['protocol'], SELECTION_V2)
        for protocol in ('exactory-openness-reading-v5', 'exactory-openness-reading-v6'):
            for complete in (True, False):
                with self.subTest(protocol=protocol, complete_search=complete):
                    inventory, chains, records = build_rule_fixture(protocol, complete_search=complete)
                    selection = select_rule_fixture(self.carried, self.artifacts, inventory, chains, records)
                    turns = {turn['origin']['assignment_id']: turn for turn in selection['turns']}
                    self.assertEqual(dict(kind='excerpt', index=0) in turns['older-2']['entries'], not complete)
                    self.assertIn(dict(kind='passage', index=0), turns['newer']['entries'])
                    self.assertNotIn(dict(kind='excerpt', index=0), turns['older']['entries'])
                    self.assertIn(dict(kind='excerpt', index=1), turns['older']['entries'])
                    # The exception requires the whole span, including at a boundary.
                    self.assertIn(dict(kind='excerpt', index=2), turns['older']['entries'])
                    self.assertEqual(turns['newer-3']['entries'], [])
                    self.assertEqual(turns['newer-3']['ranges'], [])

    def test_selection_of_version_2_keeps_its_result(self):
        inventory, chains, records = build_rule_fixture()
        selection = select_rule_fixture(self.carried, self.artifacts, inventory, chains, records)
        expected = json.loads((Path(__file__).parent / 'fixtures/carried_selection_v2.json').read_text())
        self.assertEqual(selection, expected)
        inventory, chains, records = build_rule_fixture('exactory-openness-reading-v6')
        for turn in expected['turns']:
            turn['origin']['protocol'] = 'exactory-openness-reading-v6'
        self.assertEqual(select_rule_fixture(self.carried, self.artifacts, inventory, chains, records), expected)


class AcceptanceSelectionTests(OpennessReadingCase):
    reading_protocol = None
    historical_v5 = True
    route_model = 'gpt-6-astra'
    text_sizes = (5000,) * 29

    def record_searches(self):
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        return [ProblemOpennessMinimumTests.search(self, self.versions[number % 3::3], query='Bounded query %d' % number)
                for number in range(10)]

    def test_g8_incident_29_texts_ten_searches_then_ten_more_and_minimum_v2(self):
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        carried = importlib.import_module('research_harness.openness_carried')
        self.limit_input(1300000)
        native = self.export(assignment_id='old-1')
        prior, number = None, 1
        while True:
            identifier = 'old-%d' % number
            self.assign(identifier, native, prior=prior)
            packet = self.read_packet(identifier)
            if reading.find_stage(packet) == 'final':
                break
            self.invoke(identifier, self.build_reading_response(packet, excerpts=[]))
            prior, number = identifier, number + 1
            self.assertLess(number, 30)
        records = self.store.snapshot()['records']
        old_bytes = {attempt['id']: {key: self.artifacts.read(attempt[key]) for key in ('request', 'output')}
                     for attempt in records['review_attempt'].values()}
        # A genuine v2 export: unchanged searches stay bound alongside ten additions.
        self.searches += [ProblemOpennessMinimumTests.search(self, self.versions[number % 3::3],
                         query='Additional query %d' % number) for number in range(10)]
        self.historical_v5 = False
        fresh = self.export(name='new-export', assignment_id='new-chain')
        records = self.store.snapshot()['records']
        first = records['review_assignment']['old-1']
        identity = build_identity(records, self.artifacts, first)
        new_native = json.loads(self.artifacts.read(fresh))
        self.assertEqual(new_native['scientific_context']['openness_minimum']['schema_version'], 2)
        inventory = v7.build_inventory(records, self.artifacts, new_native)['inventory']
        with patch.object(review_context_repair, 'is_same_route', compare_test_routes, create=True):
            built = carried.build_carried_selection(records, self.artifacts, inventory, identity)
            carried.verify_carried_selection(records, self.artifacts, inventory, identity, built['selection'])
            credited = {r['unit'] for t in built['selection']['turns'] for r in t['ranges']}
            self.assertEqual(sum(row['kind'] == 'fulltext' and row['handle'] in credited
                                 for row in inventory['units']), 29)
            self.assertEqual({sha for t in built['selection']['turns'] for sha in t['searches']},
                             {digest(search) for search in self.searches[:10]})
            changed = copy.deepcopy(inventory)
            unit = next(row for row in changed['units'] if row['kind'] == 'fulltext')
            unit['sha256'] = 'f' * 64
            turns = carried.select_carried_readings(records, self.artifacts, changed, identity)
            self.assertNotIn(unit['handle'], {r['unit'] for t in turns for r in t['ranges']})
            for key in ('reviewer_id', 'subject_digest', 'route_model', 'route_effort'):
                other = copy.deepcopy(identity)
                if key.startswith('route_'):
                    if key == 'route_model':
                        other['route']['model'] = 'another-model'
                    else:
                        other['route']['configuration']['effort'] = 'another-effort'
                else:
                    other[key] = 'another'
                self.assertEqual(carried.select_carried_readings(records, self.artifacts, inventory, other), [])
        self.assertEqual({attempt_id: {key: self.artifacts.read(records['review_attempt'][attempt_id][key])
                         for key in ('request', 'output')} for attempt_id in old_bytes}, old_bytes)


class NotesDependencyTests(unittest.TestCase):
    def test_same_bytes_on_a_search_component_do_not_replace_another_units_dependency(self):
        from test_openness_reading_v7_state import build_source, build_observation
        carried = importlib.import_module('research_harness.openness_carried')
        row, content = build_source('Equal bytes can have distinct logical identities.')
        inventory = v7.plan_inventory([row], {row['artifact']['sha256']: content})['inventory']
        observation = build_observation(row, content)
        manifest = dict(searches=[dict(record_id='a-search', search_digest='a' * 64,
            residual=row['artifact'], artifact=dict(sha256='a' * 64), shared_fields={})], shared_fields=[])
        first = dict(context=dict(native_packet={}), reviewer_id='reviewer')
        with patch.object(carried, 'load_packet', return_value={}), patch.object(
                reading, '_plan_search_delivery', return_value=dict(manifest=manifest)):
            dependencies = carried._list_search_dependencies({}, None, first)
        classes = carried.classify_carried_notes(inventory, [observation], search_dependencies=dependencies)
        self.assertEqual(classes[0]['classification'], 'current')
        self.assertEqual(classes[0]['units'][0]['identity'], observation['deliveries'][0]['identity'])

    def test_passage_requested_in_a_turn_affects_notes_only_when_a_successor_receives_it(self):
        from test_openness_reading_v7_state import build_source, build_observation
        carried = importlib.import_module('research_harness.openness_carried')
        first, content = build_source('The first source stays unchanged.')
        requested, answer = build_source('A passage from a source not yet delivered.', 'work-2')
        inventory = v7.plan_inventory([first], {first['artifact']['sha256']: content})['inventory']
        first_turn = build_observation(first, content)
        passage = build_observation(requested, answer)['excerpts'][0]
        first_turn['passages'] = [dict(passage, status='delivered')]
        second_turn = build_observation(first, content, number=2)
        classes = carried.classify_carried_notes(inventory, [first_turn, second_turn])
        self.assertEqual([row['classification'] for row in classes], ['current', 'affected'])
        self.assertEqual(classes[1]['units'][-1]['current_sha256'], None)
