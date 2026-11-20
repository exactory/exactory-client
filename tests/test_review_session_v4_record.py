"""Pure v4 records keep authenticated history outside bounded request inputs.

Synthetic incident shapes: 20261112043216-bar-turn-over-output-cap-at-effort-max
and 20261111235418-bar-final-refused-for-text-delivered-in-reduced-history.
"""

import copy
import hashlib
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research_harness import bar_sources, review_route_capacity as capacity
from research_harness import review_transport as transport
from research_harness.artifacts import ArtifactStore
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from research_harness.problem_openness import serialize_record
from test_review_capacity_plan import build_plan
from v7_capacity_fixtures import build_route


def build_store(artifacts, count=1, characters=None):
    works = {}
    for index in range(count):
        version = '2601.%05dv1' % index
        content = ('Source %04d. A finite comparison. ' % index) * 5
        if characters is not None:
            content = (content * (characters // len(content) + 1))[:characters]
        reference = artifacts.put(content.encode(), 'text/plain; charset=utf-8')
        works[version] = dict(id=version, title='A source ' + str(index), abstracts=[], fulltexts=[
            dict(source_id='capture-%d' % index, availability='available',
                 original=reference, text=reference)])
    return dict(work=works)


def build_turn(unit, text, number=1, *, start=0, excerpts=(), requests=(), settled=(), passage=False):
    delivery = dict(unit=unit['handle'], part=1, start=start, end=start + len(text), text=text)
    if passage:
        delivery['request_kind'] = 'passage'
    return dict(number=number, assignment_id='assignment-%d' % number, attempt_id='attempt-%d' % number,
                deliveries=[delivery], output=dict(schema_version=1, stage='request', notes='Current notes.',
                    excerpts=list(excerpts), requests=list(requests), settled_requests=list(settled)))


class SessionRecordTests(unittest.TestCase):
    def setUp(self):
        self.session = importlib.import_module('research_harness.review_session_v4')
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.artifacts = ArtifactStore(Path(self.directory.name))
        self.route = build_route(effort='xhigh')

    def build_inventory(self, count=1, characters=None):
        records = build_store(self.artifacts, count, characters)
        return self.session.build_session_inventory(records, self.artifacts, {})

    def build_plan(self, inventory, targets=('t1', 't2')):
        frames = {kind: dict(instructions=2000, bindings=1000) for kind in ('request', 'final')}
        declaration = self.session.describe_session_plan(self.route, frames, inventory['units'], list(targets))
        binding = capacity.prepare_assignment_capacity({}, self.artifacts, self.route, declaration)
        return capacity.read_chain_capacity_plan(self.artifacts, binding)

    def build_observations(self, inventory, count):
        return [build_turn(unit, self.artifacts.read(unit['artifact']).decode(), number)
                for number, unit in enumerate((row for row in inventory['units'] if row['kind'] == 'fulltext'), 1)
                if number <= count]

    def test_inventory_binds_all_captures_record_artifacts_and_frame_units_without_writes(self):
        records = build_store(self.artifacts)
        work = next(iter(records['work'].values()))
        abstract = self.artifacts.put(b'A captured abstract.', 'text/plain')
        work['abstracts'] = [dict(source_id='abstract', artifact=abstract, completeness='complete')]
        record_body = self.artifacts.put(b'An artifact referenced by a delivered record.', 'text/plain')
        packet = dict(dossier=dict(artifact=record_body))
        frame_body = self.artifacts.put(serialize_record(dict(target='A large role field.')), 'application/json')
        frame_units = [dict(kind='fixed_field', identity=dict(field='role_field'), artifact=frame_body,
                            label='A long field label. ' * 40)]
        before = copy.deepcopy((records, packet, frame_units))
        files = sorted(str(path.relative_to(self.artifacts.root)) for path in self.artifacts.root.rglob('*'))
        result = self.session.build_session_inventory(records, self.artifacts, packet, frame_units)
        again = self.session.build_session_inventory(records, self.artifacts, packet, frame_units)
        inventory = result['inventory']
        self.assertEqual(result, again)
        self.assertEqual(result['artifact']['sha256'], digest(inventory))
        self.assertEqual((records, packet, frame_units), before)
        self.assertEqual(sorted(str(path.relative_to(self.artifacts.root))
                                for path in self.artifacts.root.rglob('*')), files)
        self.assertEqual([row['kind'] for row in inventory['units']],
                         ['source', 'abstract', 'fulltext', 'artifact', 'fixed_field'])
        self.assertEqual([row['handle'] for row in inventory['units']], ['u1', 'u2', 'u3', 'u4', 'u5'])
        self.assertTrue(all(len(row['label']) <= 120 for row in inventory['units']))
        self.assertEqual(inventory['units'][2]['identity']['source_id'], 'capture-0')
        self.assertEqual(inventory['units'][2]['sha256'], work['fulltexts'][0]['text']['sha256'])
        self.assertEqual(inventory['source_inventory_digest'], digest(json.loads(
            self.artifacts.read(bar_sources._reference(bar_sources.inventory(records, self.artifacts)[
                'artifact_descriptor'])))))
        self.assertEqual(inventory['units'][-1]['identity'], dict(field='role_field'))

    def test_projection_uses_authenticated_delivery_offsets_and_keeps_turn_bindings(self):
        delivered = 'The quoted comparison is exact.'
        body = 'p' * 400 + delivered
        reference = self.artifacts.put(body.encode(), 'text/plain')
        unit = dict(handle='u1', kind='fulltext', identity=dict(version_id='2601.00000v1'),
                    sha256=reference['sha256'], artifact=reference, characters=len(body), label='A comparator.')
        inventory = dict(protocol=self.session.PROTOCOL, units=[unit])
        excerpt = dict(unit=unit['handle'], text='quoted comparison', purpose='The comparator.', targets=['t1'])
        observation = build_turn(unit, delivered, 3, start=400, excerpts=[excerpt])
        before = copy.deepcopy((inventory, observation))
        projected = self.session.project_session_turn(inventory, observation)
        self.assertEqual((inventory, observation), before)
        self.assertEqual(projected['turn'], dict(number=3, assignment_id='assignment-3',
                                                 attempt_id='attempt-3', kind='request'))
        entry = projected['excerpts'][0]
        self.assertTrue(entry['handle'].startswith('e'))
        self.assertEqual((entry['start'], entry['end']), (404, 421))
        self.assertEqual(entry['sha256'], hashlib.sha256(b'quoted comparison').hexdigest())
        self.assertEqual(entry['status'], 'verified')
        self.assertNotIn('text', projected['deliveries'][0])

    def test_same_authenticated_turns_rebuild_the_same_state_digest(self):
        inventory = self.build_inventory(4)['inventory']
        plan = self.build_plan(inventory)
        turns = self.build_observations(inventory, 4)
        before = copy.deepcopy((inventory, turns, self.route, plan))
        first = self.session.build_session_state(inventory, turns, self.route, plan)
        second = self.session.build_session_state(inventory, turns, self.route, plan)
        self.assertEqual(first, second)
        self.assertEqual(first['artifact']['sha256'], digest(first['state']))
        self.assertEqual(first['state']['inventory_digest'], digest(inventory))
        self.assertEqual(first['state']['latest_notes'], 'Current notes.')
        self.assertEqual(len(first['state']['notes']), 4)
        self.assertEqual((inventory, turns, self.route, plan), before)

    def test_forty_source_turns_carry_no_history_rows(self):
        inventory = self.build_inventory(40)['inventory']
        plan = self.build_plan(inventory)
        turns = self.build_observations(inventory, 40)
        for turn in turns:
            turn['output']['excerpts'] = [dict(unit=turn['deliveries'][0]['unit'], text='A finite comparison.',
                purpose='A comparison for the field.', targets=['t1'])]
        views, usage = [], []
        for count in (2, 40):
            state = self.session.build_session_state(inventory, turns[:count], self.route, plan)['state']
            view = self.session.describe_session_allowances(self.route, state, plan, plan['task']['request'])
            self.assertEqual(view['delivered_sources'], count)
            # Counter digit growth and allowance values are the only changing scalars.
            view['delivered_sources'] = 0
            usage.append(view['targets'][0]['used'])
            for row in view['targets']:
                row.update(used=0, remaining=row['characters'])
            views.append(serialize_record(view))
            for key in ('turns', 'deliveries', 'excerpts_history', 'sources', 'notes_history'):
                self.assertNotIn(key, view)
        self.assertEqual(views[0], views[1])
        self.assertGreater(usage[1], usage[0])

    def test_three_large_source_turns_on_max_keep_only_current_notes_and_bounded_rows(self):
        # State-only shape of the 300,000-character, effort-max incident.
        self.route = build_route()
        inventory = self.build_inventory(3, characters=100000)['inventory']
        plan = self.build_plan(inventory)
        turns = self.build_observations(inventory, 3)
        for turn in turns:
            turn['output']['notes'] = 'n' * 5996
            turn['output']['excerpts'] = [dict(unit=turn['deliveries'][0]['unit'], text='A finite comparison.',
                purpose='A retained comparator.', targets=['t1'])]
        state = self.session.build_session_state(inventory, turns, self.route, plan)['state']
        view = self.session.describe_session_allowances(self.route, state, plan, plan['task']['request'])
        self.assertEqual(sum(row['end'] - row['start'] for row in state['deliveries']), 300000)
        self.assertEqual(view['delivered_sources'], 3)
        self.assertEqual(view['notes'], 6000)
        self.assertLess(capacity.measure_capacity_value(self.route, view), 7000)
        self.assertEqual(len(self.session.describe_session_final_entries(state, ['t1'])), 3)

    def test_thousand_delivered_sources_are_one_count_and_final_still_retains_early_excerpt(self):
        inventory = self.build_inventory(1000)['inventory']
        plan = self.build_plan(inventory)
        turns = self.build_observations(inventory, 1000)
        unit = turns[0]['deliveries'][0]['unit']
        turns[0]['output']['excerpts'] = [dict(unit=unit, text='A finite comparison.',
                                              purpose='The earliest comparator.', targets=['t1'])]
        state = self.session.build_session_state(inventory, turns, self.route, plan)['state']
        view = self.session.describe_session_allowances(self.route, state, plan, plan['task']['request'])
        self.assertEqual(view['delivered_sources'], 1000)
        self.assertLess(len(serialize_record(view)), 1000)
        self.assertEqual(len(self.session.describe_session_final_entries(state, ['t1'])), 1)
        self.assertNotIn('2601.', json.dumps(view))

    def test_response_and_pending_request_caps_record_overflow_and_settlement_frees_room(self):
        inventory = self.build_inventory()['inventory']
        plan = self.build_plan(inventory)
        unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
        turns = []
        for number, count in ((1, 11), (2, 10), (3, 1)):
            requests = [dict(id='r%d-%d' % (number, index), kind='unit', unit=unit['handle'])
                        for index in range(count)]
            turns.append(build_turn(unit, 'A delivered source.', number, requests=requests))
        projected = self.session.project_session_turn(inventory, turns[0])
        self.assertEqual(projected['requests'][-1]['status'], 'over_allowance')
        state = self.session.build_session_state(inventory, turns, self.route, plan)['state']
        self.assertEqual(len(state['pending_requests']), 20)
        self.assertEqual([row['id'] for row in state['requests'] if row['status'] == 'over_allowance'],
                         ['r1-10', 'r3-0'])
        turns.append(build_turn(unit, 'A delivered source.', 4, settled=['r1-0'],
                                requests=[dict(id='r4-0', kind='inventory_page', page=1)]))
        state = self.session.build_session_state(inventory, turns, self.route, plan)['state']
        self.assertEqual(len(state['pending_requests']), 20)
        self.assertNotIn('r1-0', [row['id'] for row in state['pending_requests']])
        self.assertEqual(state['settled_requests'][0]['id'], 'r1-0')
        view = self.session.describe_session_allowances(self.route, state, plan, plan['task']['request'])
        self.assertEqual([row['id'] for row in view['pending_requests']],
                         [row['id'] for row in state['pending_requests']])

    def test_completed_requests_leave_pending_but_partial_deliveries_keep_their_slot(self):
        inventory = self.build_inventory()['inventory']
        plan = self.build_plan(inventory)
        unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
        turns = [build_turn(unit, 'First delivery.', requests=[
            dict(id='read', kind='unit', unit=unit['handle']),
            dict(id='page', kind='inventory_page', page=1)])]
        partial = build_turn(unit, 'A partial delivery.', 2)
        partial['deliveries'][0].update(request_id='read', request_complete=False)
        page = dict(partial['deliveries'][0], request_id='page', request_complete=True)
        partial['deliveries'].append(page)
        turns.append(partial)
        state = self.session.build_session_state(inventory, turns, self.route, plan)['state']
        self.assertEqual([row['id'] for row in state['pending_requests']], ['read'])
        self.assertEqual(state['requests'][1]['status'], 'delivered')
        complete = build_turn(unit, 'The remaining delivery.', 3)
        complete['deliveries'][0].update(request_id='read', request_complete=True)
        turns.append(complete)
        state = self.session.build_session_state(inventory, turns, self.route, plan)['state']
        self.assertEqual(state['pending_requests'], [])
        self.assertEqual(state['settled_requests'], [])
        self.assertEqual([row['status'] for row in state['requests']], ['delivered', 'delivered'])

    def test_pending_request_rows_fit_the_carried_reserve_and_oversized_rows_stay_diagnostic(self):
        inventory = self.build_inventory()['inventory']
        plan = self.build_plan(inventory)
        unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
        turns = [build_turn(unit, 'A delivered source.', number, requests=[
            dict(id='r%d-%d' % (number, index), kind='inventory_query', page=0, query='q' * 1800)
            for index in range(10)]) for number in (1, 2)]
        turns[-1]['output']['notes'] = 'n' * (plan['allocation']['notes'] - 4)
        state = self.session.build_session_state(inventory, turns, self.route, plan)['state']
        view = self.session.describe_session_allowances(self.route, state, plan, plan['task']['request'])
        self.assertEqual(len(view['pending_requests']), 20)
        self.assertLessEqual(capacity.measure_capacity_value(self.route, view),
                             plan['task']['request']['carried_characters'])
        large = build_turn(unit, 'A delivered source.', requests=[
            dict(id='large', kind='inventory_query', page=0, query='q' * 3000)])
        state = self.session.build_session_state(inventory, [large], self.route, plan)['state']
        self.assertEqual(state['requests'][0]['status'], 'over_allowance')
        self.assertEqual(state['pending_requests'], [])

    def test_exact_serialized_excerpt_cost_is_charged_to_both_targets_and_overflow_is_not_carried(self):
        inventory = self.build_inventory()['inventory']
        plan = self.build_plan(inventory)
        unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
        text = 'A quote: "\\\\" and a comparison.'
        excerpt = dict(unit=unit['handle'], text=text, purpose='Escaped purpose: "\\".', targets=['t1', 't2'])
        turn = build_turn(unit, text, excerpts=[excerpt, excerpt])
        projected = self.session.project_session_turn(inventory, turn)
        charge = capacity.measure_capacity_value(self.route, projected['excerpts'][0])
        self.assertGreater(charge, len(text))
        for target in plan['allocation']['targets']:
            target['excerpts'] = charge
        state = self.session.build_session_state(inventory, [turn], self.route, plan)['state']
        self.assertEqual([row['used'] for row in state['allowances']['targets']], [charge, charge])
        self.assertEqual([row['status'] for row in state['excerpts']], ['verified', 'over_allowance'])
        for targets in (['t1'], ['t2'], ['t1', 't2']):
            entries = self.session.describe_session_final_entries(state, targets)
            self.assertEqual(entries, [state['excerpts'][0]])
        self.assertNotIn(state['excerpts'][1]['handle'], json.dumps(
            self.session.describe_session_allowances(self.route, state, plan, plan['task']['request'])))

    def test_passage_deliveries_charge_once_and_do_not_travel_again(self):
        inventory = self.build_inventory()['inventory']
        plan = self.build_plan(inventory)
        unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
        turn = build_turn(unit, 'A passage.', passage=True)
        state = self.session.build_session_state(inventory, [turn], self.route, plan)['state']
        self.assertEqual(state['allowances']['passages']['used'], 10)
        view = self.session.describe_session_allowances(self.route, state, plan, plan['task']['request'])
        self.assertEqual(view['passages']['used'], 10)
        self.assertNotIn('A passage.', json.dumps(view))

    def test_pending_row_budget_counts_native_escaping_and_accepts_its_exact_boundary(self):
        inventory = self.build_inventory()['inventory']
        unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
        for route in (build_route(effort='xhigh'), build_route('codex_cli_v1', 'gpt-6-astra'),
                      build_route('codex_cli_framed_v1', 'gpt-6-astra')):
            with self.subTest(adapter=route['adapter']):
                frames = {kind: dict(instructions=2000) for kind in ('request', 'final')}
                declaration = self.session.describe_session_plan(route, frames, inventory['units'], ['t1'])
                binding = capacity.prepare_assignment_capacity({}, self.artifacts, route, declaration)
                plan = capacity.read_chain_capacity_plan(self.artifacts, binding)
                request = dict(id='edge', kind='inventory_query', page=0, query='"\\' * 20)
                request['query'] += 'q' * (2000 - capacity.measure_capacity_value(route, request))
                self.assertEqual(capacity.measure_capacity_value(route, request), 2000)
                large = dict(request, query=request['query'] + 'q')
                accepted = build_turn(unit, 'Delivered text.', requests=[request])
                rejected = build_turn(unit, 'Delivered text.', 2, requests=[dict(large, id='over')])
                state = self.session.build_session_state(inventory, [accepted, rejected], route, plan)['state']
                self.assertEqual([row['status'] for row in state['requests']], ['pending', 'over_allowance'])
                view = self.session.describe_session_allowances(route, state, plan, plan['task']['request'])
                self.assertEqual(view['pending_requests'], [request])

    def test_unmatched_excerpt_has_no_charge_and_is_never_a_final_entry(self):
        inventory = self.build_inventory()['inventory']
        plan = self.build_plan(inventory)
        unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
        turn = build_turn(unit, 'The actual delivered source.', excerpts=[dict(unit=unit['handle'],
            text='An invented comparison.', purpose='An unverified claim.', targets=['t1'])])
        state = self.session.build_session_state(inventory, [turn], self.route, plan)['state']
        self.assertEqual(state['excerpts'][0]['status'], 'unmatched')
        self.assertEqual([row['used'] for row in state['allowances']['targets']], [0, 0])
        self.assertEqual(self.session.describe_session_final_entries(state, ['t1']), [])

    def test_carried_view_at_full_notes_and_target_rows_stays_within_the_declared_shares(self):
        inventory = self.build_inventory()['inventory']
        plan = self.build_plan(inventory)
        state = self.session.build_session_state(inventory, [], self.route, plan)['state']
        state['latest_notes'] = 'n' * (plan['allocation']['notes'] - 4)
        state['allowances']['targets'] = [dict(handle='t%d' % index, characters=30000, used=index)
                                           for index in range(1, 20001)]
        state['pending_requests'] = [dict(id='r%02d' % index, kind='artifact', sha256='a' * 64,
                                         turn=999999, status='pending') for index in range(20)]
        view = self.session.describe_session_allowances(self.route, state, plan, plan['task']['request'])
        core = {key: value for key, value in view.items()
                if key not in ('latest_notes', 'delivered_sources', 'pending_requests')}
        self.assertLessEqual(capacity.measure_capacity_value(self.route, core), 30000)
        self.assertLessEqual(capacity.measure_capacity_value(self.route, state['latest_notes']),
                             plan['allocation']['notes'])
        self.assertLessEqual(capacity.measure_capacity_value(self.route, view), 53000)
        self.assertEqual(len(view['pending_requests']), 20)
        self.assertEqual(view['targets'][0]['handle'], 't20000')
        self.assertGreater(view['omitted'], 19000)
        self.assertFalse(any('turn' in row or 'status' in row for row in view['pending_requests']))

    def test_large_target_allowances_use_the_f121_summary_with_most_used_rows_first(self):
        inventory = self.build_inventory()['inventory']
        plan = self.build_plan(inventory)
        state = self.session.build_session_state(inventory, [], self.route, plan)['state']
        state['allowances']['targets'] = [dict(handle='t%d' % index, characters=30000,
            used=100 if index == 19999 else 0) for index in range(20000)]
        view = self.session.describe_session_allowances(self.route, state, plan, plan['task']['request'])
        self.assertEqual(view['target_count'], 20000)
        self.assertEqual(view['unused_target_allowance'], 30000)
        self.assertEqual(view['omitted'], 19999)
        self.assertEqual(view['targets'][0]['handle'], 't19999')
        allowances = {key: value for key, value in view.items()
                      if key not in ('latest_notes', 'delivered_sources', 'pending_requests')}
        self.assertLessEqual(capacity.measure_capacity_value(self.route, allowances), 30000)


class SessionPlanTests(unittest.TestCase):
    def test_declaration_is_admitted_for_each_route_class_and_request_counts_carried_state(self):
        session = importlib.import_module('research_harness.review_session_v4')
        from test_openness_reading_v7_turns import MemoryArtifacts
        artifacts = MemoryArtifacts()
        routes = [build_route(effort='xhigh'), build_route(),
                  build_route(model='unlisted-model', effort='xhigh', output=1000)]
        frames = {kind: dict(instructions=2000, bindings=1000) for kind in ('request', 'final')}
        units = [dict(handle='u1', kind='fulltext', identity=dict(version_id='fixture'),
                      sha256='a' * 64, characters=120000, label='A source.')]
        for route in routes:
            with self.subTest(route=capacity.describe_route_class(route)):
                declaration = session.describe_session_plan(route, frames, units, ['t1'])
                before = copy.deepcopy((declaration, route))
                binding = capacity.prepare_assignment_capacity({}, artifacts, route, declaration)
                plan = capacity.read_chain_capacity_plan(artifacts, binding)
                self.assertEqual(set(plan['task']), {'request', 'final'})
                self.assertEqual(plan['task']['request']['carried_characters'],
                                 plan['allocation']['notes'] + declaration['allowance_rows_bound'] +
                                 declaration['pending_requests_bound'])
                self.assertEqual(plan['task']['request']['carried_components']['allowances'], 30000)
                self.assertEqual(plan['task']['request']['task_characters'], plan['frame']['request']['bound'] +
                    plan['task']['request']['material_allowance'] + plan['task']['request']['carried_characters'] +
                    plan['task']['request']['notice_characters'])
                self.assertEqual((declaration, route), before)

    def test_each_request_level_keeps_notes_excerpts_and_request_reserve_within_its_answer_bound(self):
        session = importlib.import_module('research_harness.review_session_v4')
        from test_openness_reading_v7_turns import MemoryArtifacts
        artifacts = MemoryArtifacts()
        frames = {kind: dict(instructions=2000) for kind in ('request', 'final')}
        for route in (build_route(effort='xhigh'), build_route(),
                      build_route(model='unlisted-model', effort='xhigh', output=1000)):
            declaration = session.describe_session_plan(route, frames, [], ['t1'])
            binding = capacity.prepare_assignment_capacity({}, artifacts, route, declaration)
            plan = capacity.read_chain_capacity_plan(artifacts, binding)
            state = session.build_session_state(dict(units=[]), [], route, plan)['state']
            for count in range(4):
                limits = capacity.derive_plan_task_limits(plan, 'request',
                    [dict(outcome='output_over_cap')] * count)
                with self.subTest(route=capacity.describe_route_class(route), level=limits['material_allowance']):
                    view = session.describe_session_allowances(route, state, plan, limits)
                    self.assertLessEqual(view['notes'] + view['excerpts'] + 4000, limits['answer_bound'])
                    self.assertLessEqual(view['notes'], plan['allocation']['notes'])

    def test_base_v7_plan_digest_and_stored_plan_reuse_do_not_recompute(self):
        session = importlib.import_module('research_harness.review_session_v4')
        from test_openness_reading_v7_turns import MemoryArtifacts
        artifacts = MemoryArtifacts()
        frames = {kind: dict(instructions=2000) for kind in ('request', 'final')}
        declaration = session.describe_session_plan(build_route(effort='xhigh'), frames, [], ['t1'])
        capacity.prepare_assignment_capacity({}, artifacts, build_route(effort='xhigh'), declaration)
        plan = build_plan()
        self.assertEqual(digest(plan), '417944b4ab4a2436886cf0586d2329a4207262f90a285814e1532251e94b506a')
        reference = dict(protocol=capacity.CAPACITY_PLAN_PROTOCOL, digest=digest(plan),
                         artifact=artifacts.put(serialize_record(plan)))
        with patch.object(capacity, 'plan_chain_capacity', side_effect=AssertionError('Reuse the stored plan.')):
            reused = capacity.prepare_assignment_capacity({}, artifacts, build_route(effort='xhigh'),
                                                          dict(kind='reading', plan=reference))
            self.assertEqual(reused['capacity_plan'], reference)


class SessionAllowancePlanTests(unittest.TestCase):
    def setUp(self):
        from capacity_evidence_fixtures import build_capacity_runtime
        from test_openness_reading_v7_turns import MemoryArtifacts
        self.session = importlib.import_module('research_harness.review_session_v4')
        self.artifacts = MemoryArtifacts()
        self.route = dict(build_route('openai_responses_v1', 'fixture-model'),
                          runtime=build_capacity_runtime('openai_responses_v1'))
        # The common target minimums need room independently of the bounded
        # request projection. This is a synthetic, keyed input-capacity witness.
        self.records = dict(review_route=dict(route=self.route), review_route_probe=dict(probe=dict(
            id='probe', route_id='route', capacity=dict(input_tokens=4128000), usage={},
            capacity_evidence=dict(key=capacity.describe_capacity_key(self.route), input_tokens=4128000))))
        self.frames = {kind: dict(instructions=2000, bindings=1000, envelope=1000)
                       for kind in ('request', 'final')}
        text = 'A finite comparison.'
        reference = self.artifacts.put(text.encode(), 'text/plain')
        self.unit = dict(handle='u1', kind='fulltext', identity=dict(version_id='2601.00000v1'),
                         artifact=reference, sha256=reference['sha256'], characters=len(text), label='A source.')
        self.inventory = dict(protocol=self.session.PROTOCOL, units=[self.unit])

    def admit_plan(self, count=500):
        declaration = self.session.describe_session_plan(self.route, self.frames,
            self.inventory['units'], ['t%d' % index for index in range(count)])
        binding = capacity.prepare_assignment_capacity(self.records, self.artifacts, self.route, declaration)
        plan = capacity.read_chain_capacity_plan(self.artifacts, binding)
        return declaration, binding, plan

    def build_request(self, plan, state, material):
        limits = capacity.derive_plan_task_limits(plan, 'request')
        packet = dict(source_rules=self.session.PROTOCOL, inventory_digest=digest(self.inventory),
            state_digest=digest(state), plan_digest=digest(plan), session_turn=dict(
                allowances=self.session.describe_session_allowances(self.route, state, plan, limits)),
            material=material)
        return transport.request_body(self.route, 'Read the delivered material and return a request answer.', packet)

    def test_finding_10_many_targets_share_admission_bound_and_page_every_allowance_row_once(self):
        declaration, binding, plan = self.admit_plan()
        self.assertEqual(declaration['allowance_rows_bound'], 30000)
        self.assertEqual(set(plan['task']), {'request', 'final'})
        components = plan['task']['request']['carried_components']
        self.assertEqual(components, dict(notes=plan['allocation']['notes'], allowances=30000,
            pending_requests=self.session.PENDING_REQUESTS_CHARACTERS + self.session.STATE_FIELDS_CHARACTERS))
        self.assertEqual(plan['task']['request']['carried_characters'], sum(components.values()))
        plan_bytes = self.artifacts.read(binding['capacity_plan']['artifact'])
        turns, delivered, page_ids, request_count = [], [], [], 0

        def check_request(material):
            nonlocal request_count
            # Re-read the plan artifact and rebuild state, rather than continuing
            # from a mutated display object or a current output profile.
            frozen = capacity.read_chain_capacity_plan(self.artifacts, binding)
            state = self.session.build_session_state(self.inventory, turns, self.route, frozen)['state']
            request = self.build_request(frozen, state, material)
            report = capacity.describe_plan_preflight(self.records, self.route, request, frozen, 'request')
            self.assertTrue(report['plan_comparison']['fits'], report['plan_comparison'])
            self.assertLessEqual(report['request_measure']['text_characters'], frozen['task']['request'][
                'request_bound']['text_characters'])
            with patch.object(capacity, 'read_profile_priors', return_value=[]):
                again = json.loads(serialize_record(state))
                self.assertEqual(self.build_request(frozen, again, material), request)
            self.assertEqual(self.artifacts.read(binding['capacity_plan']['artifact']), plan_bytes)
            request_count += 1
            return state

        for number in (1, 2):
            check_request(dict(text='A finite comparison.'))
            turn = build_turn(self.unit, 'A finite comparison.', number, excerpts=[dict(unit='u1',
                text='A finite comparison.', purpose='The last target is used first.',
                targets=plan['targets']['handles'] if number == 1 else ['t499'])],
                requests=[dict(id='r%d-%d' % (number, index), kind='unit', unit='u1')
                          for index in range(10)])
            turn['output']['notes'] = 'n' * (plan['allocation']['notes'] - 4)
            self.assertLessEqual(capacity.measure_capacity_value(self.route, turn['output']),
                                 plan['task']['request']['answer_bound'])
            turns.append(turn)
        state = self.session.build_session_state(self.inventory, turns, self.route, plan)['state']
        view = self.session.describe_session_allowances(self.route, state, plan, plan['task']['request'])
        complete_rows = [dict(row, remaining=row['characters'] - row['used'])
                         for row in state['allowances']['targets']]
        self.assertGreater(capacity.measure_capacity_value(self.route, complete_rows), 30000)
        self.assertEqual(view['target_count'], 500)
        self.assertGreater(view['omitted'], 0)
        self.assertEqual(view['omitted'] + len(view['targets']), 500)
        core = {key: value for key, value in view.items()
                if key not in ('latest_notes', 'delivered_sources', 'pending_requests')}
        self.assertGreater(capacity.measure_capacity_value(self.route, core), 29000)
        self.assertLessEqual(capacity.measure_capacity_value(self.route, core), 30000)
        self.assertEqual(view['targets'][0]['handle'], 't499')
        self.assertEqual(len(view['pending_requests']), 20)
        omitted = set(plan['targets']['handles']) - {row['handle'] for row in view['targets']}
        after = None
        while True:
            page = self.session.build_session_allowance_page(self.route, state, plan, after, 5000)
            self.assertLessEqual(capacity.measure_capacity_value(self.route, serialize_record(page).decode()), 5000)
            delivered.extend(page['rows'])
            page_ids.append(page['next'])
            check_request(dict(allowance_page=page))
            turn = build_turn(self.unit, '', len(turns) + 1)
            turn['deliveries'] = []
            turn['output']['notes'] = 'n' * (plan['allocation']['notes'] - 4)
            turn['list_pages'] = [dict(list=page['list'], after=page['after'], next=page['next'])]
            turns.append(turn)
            state = self.session.build_session_state(self.inventory, turns, self.route, plan)['state']
            after = page['next']
            if after is None:
                break
        handles = [row['handle'] for row in delivered]
        self.assertEqual(handles, plan['targets']['handles'])
        self.assertEqual(len(handles), len(set(handles)))
        self.assertTrue(omitted <= set(handles))
        self.assertGreater(len(page_ids), 1)
        self.assertEqual(request_count, 2 + len(page_ids))
        self.assertEqual(state['list_pages']['allowances'], dict(next=None, ended=True))
        self.assertEqual(digest(self.session.build_session_state(
            self.inventory, turns, self.route, plan)['state']), digest(state))

    def test_pending_rows_are_separate_from_the_single_allowance_projection_bound(self):
        small, _, first = self.admit_plan(1)
        large, _, second = self.admit_plan(500)
        self.assertEqual(small['allowance_rows_bound'], 30000)
        self.assertEqual(large['allowance_rows_bound'], 30000)
        self.assertEqual(small['pending_requests_bound'], large['pending_requests_bound'])
        for plan in (first, second):
            components = plan['task']['request']['carried_components']
            self.assertEqual(components['allowances'], 30000)
            self.assertGreater(components['pending_requests'], 40000)
            self.assertEqual(plan['task']['final']['carried_characters'], 0)

    def test_pending_request_bound_is_validated_before_any_plan_publication(self):
        declaration = self.session.describe_session_plan(self.route, self.frames, [], ['t1'])
        capacity.prepare_assignment_capacity(self.records, self.artifacts, self.route,
            dict(declaration, pending_requests_bound=0))
        for bound in (-1, True, '40042'):
            before = copy.deepcopy(self.artifacts.bodies)
            with self.subTest(bound=bound), self.assertRaises(ResearchError) as caught:
                capacity.prepare_assignment_capacity(self.records, self.artifacts, self.route,
                    dict(declaration, pending_requests_bound=bound))
            self.assertEqual(caught.exception.code, 'invalid_input')
            self.assertEqual(self.artifacts.bodies, before)
        before = copy.deepcopy(self.artifacts.bodies)
        with self.assertRaises(ResearchError) as caught:
            capacity.prepare_assignment_capacity(self.records, self.artifacts, self.route,
                dict(declaration, kind='final', frames=dict(final=self.frames['final']), pending_requests_bound=1))
        self.assertEqual(caught.exception.code, 'invalid_input')
        self.assertEqual(self.artifacts.bodies, before)

    def test_allowance_page_cursor_is_rebuilt_from_the_authenticated_turn(self):
        _, _, plan = self.admit_plan()
        state = self.session.build_session_state(self.inventory, [], self.route, plan)['state']
        page = self.session.build_session_allowance_page(self.route, state, plan, None, 1000)
        turn = build_turn(self.unit, 'A finite comparison.')
        turn['list_pages'] = [dict(list='allowances', after=None, next=page['next'])]
        state = self.session.build_session_state(self.inventory, [turn], self.route, plan)['state']
        self.assertEqual(state['list_pages']['allowances'], dict(next=page['next'], ended=False))
        with self.assertRaises(ResearchError) as caught:
            self.session.build_session_allowance_page(self.route, state, plan, None, 1000)
        self.assertEqual(caught.exception.code, 'invalid_reading_response')
        following = self.session.build_session_allowance_page(self.route, state, plan, page['next'], 1000)
        self.assertEqual(following['rows'][0]['handle'], 't%d' % len(page['rows']))

    def test_v7_fixture_admission_plan_artifacts_keep_their_base_digests(self):
        from research_harness import openness_reading_v7 as v7
        from test_openness_reading_v7_turns import MemoryArtifacts, build_native
        # Pin the admission frames of 0.92.0, including the unread-final reserve. They differ from 0.91.1 only by
        # finding version 2: the plan and the final frame gain finding_index, and the final instructions grow. G8
        # adds the sentences on finding_index.units and the descriptor form to the rules of version 2 (563
        # characters), which every final frame, room and task size of these plans follows. G8.11 adds the reserve
        # finding_units (1,500 characters) to the reading frame of every chain with a finding index, and the reading
        # room, task sizes and task levels follow it: with one target the reading task passes 240,000 characters, so
        # its level becomes 480,000, which the Codex profile has not measured.
        cases = [
            ('claude_cli_v1', 'claude-opus-5-5', 1,
             'f5b9d14870cdb0763f9682ee0674547b43d954e4570ee9380086d89f2168a3d2',
             '32633c3f172f84b6544116a4b5c9f0e5e89431dd4f2af59a9fbae096df5914bb'),
            ('claude_cli_v1', 'claude-opus-5-5', 2,
             'df5eb05213dce286d53a2a4fee8e209da1e52eaa5d49e2468ebc84940255f9be',
             '6451b8c7337b7b0ec6682628db05a94698634146636a049ef1c01fe186a0f063'),
            ('codex_cli_v1', 'gpt-6-astra', 1,
             '117ba5b03d1fea6336a581dfd2e08dac13cb37063cf442686b5c20cf27c76da1',
             '2667eb349c1254633cf4359587ad6bb006841dd89e3f55107cdeb1e88848e6e0'),
            ('codex_cli_v1', 'gpt-6-astra', 8,
             'b3926f0a01cd3d0b0ba3cddb877aef0388c7411176cd3c0866611b4f654fcf86',
             '0aa762d1375594df1fe33ba970a231fb6dce5ea6a1d6e5e906d213af89fefdf3')]
        for adapter, model, targets, expected, expected_artifact in cases:
            with self.subTest(adapter=adapter, targets=targets), patch.object(v7, 'build_inventory',
                    return_value=v7.plan_inventory([], {})):
                result = v7.check_v7_admission({}, MemoryArtifacts(), build_route(adapter, model, effort='xhigh'),
                                                build_native(targets))
                self.assertEqual(result['state'], 'record')
                self.assertEqual(digest(result['capacity_plan']), expected)
                self.assertEqual(result['capacity_members']['capacity_plan']['digest'], expected)
                self.assertEqual(result['capacity_members']['capacity_plan']['artifact']['sha256'], expected_artifact)


if __name__ == '__main__':
    unittest.main()
