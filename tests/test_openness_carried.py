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
                result = self.carried.list_qualifying_chains(self.records, self.artifacts, identity)
                self.assertEqual(bool(result['passed_over']), 'reviewer_id' in change)
                if result['passed_over']:
                    self.assertEqual(result['passed_over'][0]['reason'], 'reviewer_id')

    def test_normalized_reviewer_and_route_id_runtime_do_not_change_identity(self):
        identity = copy.deepcopy(self.identity)
        identity['reviewer_id'] = '  OPENNESS-REVIEWER  '
        identity['route'].update(id='new-route', runtime={'version': 'another'})
        self.assertTrue(self.select_readings(identity=identity))

    def test_g8_deterministic_stored_selection_verifies_without_selecting_again(self):
        before = copy.deepcopy(self.records)
        built = self.carried.build_carried_selection(self.records, self.artifacts, self.inventory, self.identity)
        self.assertEqual(built, self.carried.build_carried_selection(
            self.records, self.artifacts, self.inventory, self.identity))
        self.assertEqual(built['artifact']['sha256'], digest(built['selection']))
        self.assertEqual(built['content'], serialize_record(built['selection']))
        self.assertEqual(set(built['selection']), {'protocol', 'inventory_digest', 'turns'})
        # Record another authentic chain after the cutoff, with its own packet and observation.
        native = self.export(name='later-export', assignment_id='later')
        self.assign('later', native)
        self.invoke('later', self.build_reading_response(self.read_packet('later'), excerpts=[]))
        after = self.store.snapshot()['records']
        with patch.object(self.carried, 'select_carried_readings', side_effect=AssertionError('reselection')):
            self.verify_selection(built['selection'], records=after)
        self.assertEqual(self.select_readings(records=after), built['selection']['turns'])
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
                    self.verify_selection(selection)
                self.assertEqual(caught.exception.code, 'openness_reading_incomplete')
                self.assertEqual(caught.exception.message, v7.SELECTION_MISMATCH_MESSAGE)

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
    pass


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
