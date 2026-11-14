"""V7 state contracts, with synthetic stored v5 and authenticated v6 observations.

Sources: 20261112235500-openness-chain-final-turn-over-route-limit-after-57-turns;
20261113110022-every-open-record-and-review-capacity (D1 9, D2 R66-R71, closure Q1/Q2/Q12).
"""

import copy
import hashlib
import importlib
import json
import unittest

from research_harness.artifacts import describe_artifact
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from research_harness.problem_openness import serialize_record
from test_review_capacity_plan import build_plan
from test_review_route_capacity import build_route
from unbound_packet_fixtures import open_stored_case
from openness_reading_fixtures import OpennessReadingCase


def build_source(text, version='work-1'):
    content = text.encode('utf-8')
    reference = describe_artifact(content, 'text/plain; charset=utf-8')
    return dict(kind='fulltext', version_id=version, text_sha256=reference['sha256'], artifact=reference), content


def build_observation(row, content, number=1, *, excerpt=True):
    text = content.decode('utf-8')
    identity = {key: row[key] for key in ('kind', 'version_id', 'text_sha256')}
    origin = dict(chain='earlier', protocol='exactory-openness-reading-v5',
                  assignment_id='old-%d' % number, attempt_id='try-%d' % number)
    span = text[:30]
    return dict(origin=origin, turn=dict(number=number, assignment_id=origin['assignment_id'],
        attempt_id=origin['attempt_id'], kind='reading'), notes='Latest notes %d.' % number,
        deliveries=[dict(identity=identity, sha256=row['artifact']['sha256'], part=1, parts=1,
            start=0, end=len(text), span_sha256=row['artifact']['sha256'], text=text)],
        excerpts=[dict(identity=identity, source_sha256=row['artifact']['sha256'], start=0, end=len(span),
            sha256=hashlib.sha256(span.encode()).hexdigest(), text=span, purpose='Relevance of this result.',
            status='verified')] if excerpt else [], passages=[], searches=[])


def select_observation(inventory, observation, *, notes_class='current'):
    matches = {(json.dumps(row['identity'], sort_keys=True), row['sha256']): row
               for row in inventory['units']}
    ranges = []
    for entry in observation['deliveries']:
        row = matches.get((json.dumps(entry['identity'], sort_keys=True), entry['sha256']))
        if row is not None:
            ranges.append(dict(unit=row['handle'], start=entry['start'], end=entry['end'],
                               sha256=entry['span_sha256'], part=entry['part'], parts=entry['parts']))
    entries = [dict(kind=kind, index=index) for kind in ('excerpt', 'passage')
               for index, entry in enumerate(observation[kind + 's'])
               if (json.dumps(entry['identity'], sort_keys=True), entry['source_sha256']) in matches]
    return dict(origin=observation['origin'], ranges=ranges,
                searches=[sha for sha in observation['searches'] if any(
                    row['sha256'] == sha for row in inventory['units'])],
                entries=entries, notes=dict(classification=notes_class, units=[]))


class V7InventoryTests(unittest.TestCase):
    def setUp(self):
        self.v7 = importlib.import_module('research_harness.openness_reading_v7')

    def test_q2_ten_thousand_units_keep_only_one_body_per_sha256(self):
        rows, bodies = [], {}
        for index in range(10000):
            row, content = build_source(str(index // 2).zfill(5) + 'x' * 195,
                                        'long-' + 'x' * 500 if index == 0 else 'work-%d' % index)
            rows.append(row)
            bodies[row['artifact']['sha256']] = content
        planned = self.v7.plan_inventory(rows, bodies)
        inventory = planned['inventory']
        self.assertEqual(len(inventory['units']), 10000)
        self.assertEqual(len(inventory['material']), 5000)
        self.assertEqual(sum(row['characters'] for row in inventory['material']), 1000000)
        self.assertEqual(inventory['material'][0]['units'], ['u1', 'u2'])
        self.assertEqual(planned['artifact']['sha256'], digest(inventory))
        self.assertTrue(all(len(row['label']) <= 120 for row in inventory['units']))
        self.assertEqual(self.v7.plan_inventory(rows, bodies), planned)

    def test_inventory_keeps_accepted_result_and_result_text_identities(self):
        result = serialize_record(dict(artifacts=[], manifests=[], acceptance_id='accepted'))
        text = b'A checked result body.'
        rows = [dict(kind='record', record_kind='accepted_result', record_id='attempt:accepted',
                     artifact=describe_artifact(result, 'application/json')),
                dict(kind='result_text', native_attempt_digest='1' * 64, acceptance_id='accepted',
                     text_sha256=hashlib.sha256(text).hexdigest(),
                     artifact=describe_artifact(text, 'text/plain'))]
        inventory = self.v7.plan_inventory(rows, {row['artifact']['sha256']: body
                                                for row, body in zip(rows, (result, text))})['inventory']
        self.assertEqual([row['kind'] for row in inventory['units']], ['record', 'result_text'])
        self.assertEqual(inventory['units'][0]['identity']['record_kind'], 'accepted_result')
        self.assertEqual(inventory['units'][1]['identity']['acceptance_id'], 'accepted')

    def test_search_material_rebuilds_originals_and_deduplicates_equal_field_bodies(self):
        searches = [dict(id=name, question='Q', profile='Q', extra={'all': True}, different=index)
                    for index, name in enumerate(('first', 'second'))]
        rows = [dict(kind='record', record_kind='literature_search', record_id=search['id'],
                     artifact=describe_artifact(serialize_record(search), 'application/json')) for search in searches]
        bindings = [dict(formulation='Q', search_digest=digest(search), search=search) for search in searches]
        planned = self.v7.plan_inventory(rows, {digest(search): serialize_record(search) for search in searches},
                                         search_bindings=bindings)
        inventory = planned['inventory']
        self.assertEqual(len(inventory['material']), 4)  # two values (Q and object) and two residuals
        from research_harness.review_search_delivery import verify_search_deliveries
        delivered = {row['sha256']: dict(content=planned['bodies'][row['sha256']], deliveries=[{'part': 1}])
                     for row in inventory['material']}
        receipts = verify_search_deliveries(inventory['search_delivery'], delivered,
                                            {digest(search): serialize_record(search) for search in searches})
        self.assertEqual(len(receipts), 2)
        for unit in inventory['units']:
            self.assertEqual(len(unit['bodies']), 3)
        self.assertNotIn(digest(searches[0]), {row['sha256'] for row in inventory['material']})


class V7ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.v7 = importlib.import_module('research_harness.openness_reading_v7')
        self.row, self.content = build_source('Every selected range keeps its authenticated source. ' * 4)
        self.inventory = self.v7.plan_inventory([self.row], {self.row['artifact']['sha256']: self.content})['inventory']
        self.observation = build_observation(self.row, self.content)

    def test_projection_credits_selected_subrange_and_entries_only(self):
        observation = copy.deepcopy(self.observation)
        changed, body = build_source('An unrelated changed body.', 'work-2')
        observation['deliveries'] += build_observation(changed, body)['deliveries']
        selection = select_observation(self.inventory, observation)
        selection['ranges'][0].update(start=10, end=70,
            sha256=hashlib.sha256(self.content.decode()[10:70].encode()).hexdigest())
        before = copy.deepcopy((self.inventory, observation, selection))
        projected = self.v7.project_carried_turn(self.inventory, observation, selection)
        self.assertEqual(projected['deliveries'], selection['ranges'])
        self.assertEqual(projected['turn']['origin'], observation['origin'])
        self.assertEqual(projected['entries'][0]['unit'], 'u1')
        self.assertEqual(projected['entries'][0]['targets'], [])
        self.assertEqual(projected['notes']['classification'], 'current')
        self.assertEqual((self.inventory, observation, selection), before)

    def test_selected_entry_uses_its_own_source_even_without_a_selected_delivery(self):
        row, body = build_source('An earlier source supplies this separately checked passage. ' * 2, 'work-2')
        inventory = self.v7.plan_inventory([self.row, row],
            {self.row['artifact']['sha256']: self.content, row['artifact']['sha256']: body})['inventory']
        observation = copy.deepcopy(self.observation)
        observation['excerpts'] += build_observation(row, body)['excerpts']
        selection = select_observation(inventory, observation)
        selection['entries'] = [dict(kind='excerpt', index=1)]
        projected = self.v7.project_carried_turn(inventory, observation, selection)
        self.assertEqual([item['unit'] for item in projected['deliveries']], ['u1'])
        self.assertEqual([item['unit'] for item in projected['entries']], ['u2'])
        changed = copy.deepcopy(inventory)
        changed['units'][1]['sha256'] = 'f' * 64
        with self.assertRaises(ResearchError):
            self.v7.project_carried_turn(changed, observation, selection)

    def test_first_assignment_state_has_no_prior_notes_or_deliveries(self):
        inventory = self.v7.plan_inventory([], {})['inventory']
        result = self.v7.build_chain_state(inventory, [], route=build_route(effort='xhigh'), plan=build_plan())
        self.assertIsNone(result['state']['latest_notes'])
        for field in ('turns', 'deliveries', 'entries', 'notes', 'pool', 'carried_readings', 'searches'):
            self.assertEqual(result['state'][field], [])
        self.assertEqual(result['state']['inventory_digest'], digest(inventory))

    def test_projection_rejects_unobserved_or_changed_selected_items(self):
        for change in ('origin', 'range', 'digest', 'entry', 'notes', 'part',
                       'unit', 'entry_kind', 'entry_index_type', 'entry_status'):
            observation = copy.deepcopy(self.observation)
            selection = select_observation(self.inventory, observation)
            if change == 'origin':
                selection['origin'] = dict(selection['origin'], attempt_id='other')
            elif change == 'range':
                selection['ranges'][0]['end'] += 1
            elif change == 'digest':
                selection['ranges'][0]['sha256'] = '0' * 64
            elif change == 'entry':
                selection['entries'][0]['index'] = 99
            elif change == 'notes':
                selection['notes']['classification'] = 'unknown'
            elif change == 'part':
                selection['ranges'][0].update(part=-1, parts=0)
            elif change == 'unit':
                selection['ranges'][0]['unit'] = 'u-missing'
            elif change == 'entry_kind':
                selection['entries'][0]['kind'] = 'unknown'
            elif change == 'entry_index_type':
                selection['entries'][0]['index'] = False
            else:
                observation['excerpts'][0]['status'] = 'unmatched'
            with self.subTest(change=change):
                with self.assertRaises(ResearchError) as caught:
                    self.v7.project_carried_turn(self.inventory, observation, selection)
                self.assertEqual(caught.exception.code, 'openness_reading_incomplete')
        changed = copy.deepcopy(self.inventory)
        changed['units'][0]['sha256'] = 'f' * 64
        with self.assertRaises(ResearchError):
            self.v7.project_carried_turn(changed, self.observation,
                                         select_observation(self.inventory, self.observation))

    def test_q12_pool_uses_serialized_cost_spills_entries_and_renews_affected_notes(self):
        route, plan = build_route(effort='xhigh'), build_plan()
        plan['allocation']['page_room'] = 13000
        selection = select_observation(self.inventory, self.observation)
        projection = self.v7.project_carried_turn(self.inventory, self.observation, selection)
        # Purpose escaping and metadata cost more than the retained text.
        projection['entries'][0]['purpose'] = '"\\\n' * 150
        result = self.v7.build_chain_state(self.inventory, [projection], route=route, plan=plan)
        state = result['state']
        self.assertEqual(state['pool'], [])
        self.assertEqual(len(state['carried_readings']), 1)
        self.assertEqual(len(state['deliveries']), 1)
        self.assertEqual(state['latest_notes'], self.observation['notes'])
        spill = result['inventory']['units'][-1]
        self.assertEqual(spill['kind'], 'carried_reading')
        self.assertEqual(json.loads(result['bodies'][spill['sha256']])['entries'][0]['text'],
                         projection['entries'][0]['text'])
        affected = copy.deepcopy(projection)
        affected['notes']['classification'] = 'affected'
        affected['notes']['units'] = [dict(identity=dict(kind='fulltext', version_id='work-removed', text_sha256='0' * 64),
                                                   sha256='0' * 64, current_sha256=None)]
        result = self.v7.build_chain_state(self.inventory, [affected], route=route, plan=plan)
        self.assertIsNone(result['state']['latest_notes'])
        self.assertEqual(len(result['state']['carried_readings']), 2)
        self.assertEqual(result['inventory']['units'][-1]['identity']['content_kind'], 'affected_notes')
        # Rebuilding from the returned inventory must not append the same carried units again.
        self.assertEqual(self.v7.build_chain_state(result['inventory'], [affected], route=route, plan=plan), result)
        corrupted = copy.deepcopy(result['inventory'])
        corrupted['units'][-1]['sha256'] = '0' * 64
        with self.assertRaises(ResearchError):
            self.v7.build_chain_state(corrupted, [affected], route=route, plan=plan)

    def test_q1_state_digest_rebuilds_sixty_turns_and_latest_current_notes(self):
        projections = []
        for number in range(1, 61):
            observation = build_observation(self.row, self.content, number)
            observation['origin'].update(chain='c' * 120, assignment_id=str(number).zfill(120),
                                         attempt_id=('try-%d' % number).ljust(120, 'x'))
            observation['turn'].update(assignment_id=observation['origin']['assignment_id'],
                                      attempt_id=observation['origin']['attempt_id'])
            selection = select_observation(self.inventory, observation,
                                           notes_class='affected' if number == 60 else 'current')
            projections.append(self.v7.project_carried_turn(self.inventory, observation, selection))
        route, plan = build_route(effort='xhigh'), build_plan()
        result = self.v7.build_chain_state(self.inventory, projections, route=route, plan=plan)
        again = self.v7.build_chain_state(json.loads(serialize_record(self.inventory)),
            json.loads(serialize_record(projections)), route=route, plan=json.loads(serialize_record(plan)))
        self.assertEqual(result, again)
        self.assertEqual(result['artifact']['sha256'], digest(result['state']))
        self.assertEqual(len(result['state']['turns']), 60)
        self.assertEqual(result['state']['latest_notes'], 'Latest notes 59.')
        self.assertEqual(result['state']['turns'][-1]['number'], 60)
        self.assertLessEqual(result['state']['allowances']['pool']['used'],
                             result['state']['allowances']['pool']['characters'])

    def test_search_credit_requires_completed_original_receipt(self):
        search = dict(id='search', question='A complete bound original.')
        body = serialize_record(search)
        row = dict(kind='record', record_kind='literature_search', record_id='search',
                   artifact=describe_artifact(body, 'application/json'))
        inventory = self.v7.plan_inventory([row], {digest(search): body})['inventory']
        observation = copy.deepcopy(self.observation)
        observation['deliveries'] = [dict(identity=inventory['units'][0]['identity'],
            sha256=digest(search), part=1, parts=2, start=0, end=10,
            span_sha256=hashlib.sha256(body[:10]).hexdigest(), text=body[:10].decode())]
        observation['excerpts'] = []
        selection = select_observation(inventory, observation)
        with self.assertRaises(ResearchError):
            self.v7.project_carried_turn(inventory, observation, selection)
        selection['ranges'] = []
        selection['searches'] = [digest(search)]
        with self.assertRaises(ResearchError):
            self.v7.project_carried_turn(inventory, observation, selection)
        observation['searches'] = [digest(search)]
        projection = self.v7.project_carried_turn(inventory, observation, selection)
        self.assertEqual(projection['deliveries'], [dict(unit='u1', part=1, parts=1, start=0,
                                                       end=len(body.decode()), sha256=digest(search))])

    def test_affected_notes_are_scheduled_newest_first_before_entry_units(self):
        plan = build_plan()
        plan['allocation']['page_room'] = 13000
        projections = []
        for number in (1, 2):
            observation = build_observation(self.row, self.content, number)
            selection = select_observation(self.inventory, observation, notes_class='affected')
            projection = self.v7.project_carried_turn(self.inventory, observation, selection)
            projection['entries'][0]['purpose'] = '"\\' * 200
            projections.append(projection)
        result = self.v7.build_chain_state(self.inventory, projections,
                                           route=build_route(effort='xhigh'), plan=plan)
        units = {row['handle']: row for row in result['inventory']['units']}
        schedule = [units[handle]['identity'] for handle in result['state']['carried_readings']]
        self.assertEqual([(row['content_kind'], row['origin']['assignment_id']) for row in schedule],
                         [('affected_notes', 'old-2'), ('affected_notes', 'old-1'),
                          ('entries', 'old-1'), ('entries', 'old-2')])

    def test_pool_boundary_matches_exact_route_serialization(self):
        from research_harness import openness_reading
        projection = self.v7.project_carried_turn(self.inventory, self.observation,
                                                 select_observation(self.inventory, self.observation))
        projection['entries'][0]['purpose'] = 'Quotes " and a backslash \\ and Unicode λ.'
        for adapter in ('claude_cli_v1', 'codex_cli_v1'):
            route = build_route(adapter=adapter, effort='xhigh')
            charge = openness_reading.measure_record_entries(route, [dict(projection['entries'][0], handle='e1')])
            for offset in (-1, 0):
                plan = build_plan()
                plan['allocation']['page_room'] = 12700 + charge + offset
                result = self.v7.build_chain_state(self.inventory, [projection], route=route, plan=plan)
                self.assertEqual(result['state']['pool'], [] if offset == -1 else ['e1'])
                self.assertEqual(result['state']['allowances']['pool']['used'], 0 if offset == -1 else charge)

    def test_q12_incident_57_turns_spill_only_carried_entries_at_starting_final(self):
        rows, bodies, observations = [], {}, []
        index = 0
        for number in range(1, 58):
            texts, purposes = [], []
            for _ in range(19 if number <= 37 else 18):
                size = 170 if index < 522 else 169
                texts.append(('Span %04d. Synthetic verified content. ' % index + 'x' * size)[:size])
                purposes.append(('Purpose %04d. ' % index + 'p' * 125)[:125 if index < 115 else 124])
                index += 1
            row, body = build_source(''.join(texts), 'work-%d' % number)
            rows.append(row)
            bodies[row['artifact']['sha256']] = body
            observation = build_observation(row, body, number, excerpt=False)
            start = 0
            for text, purpose in zip(texts, purposes):
                observation['excerpts'].append(dict(identity=observation['deliveries'][0]['identity'],
                    source_sha256=row['artifact']['sha256'], start=start, end=start+len(text),
                    sha256=hashlib.sha256(text.encode()).hexdigest(), text=text, purpose=purpose, status='verified'))
                start += len(text)
            observations.append(observation)
        inventory = self.v7.plan_inventory(rows, bodies)['inventory']
        projections = [self.v7.project_carried_turn(inventory, observation,
                        select_observation(inventory, observation)) for observation in observations]
        plan, route = build_plan(), build_route(effort='xhigh')
        result = self.v7.build_chain_state(inventory, projections, route=route, plan=plan)
        state = result['state']
        self.assertEqual(len(state['turns']), 57)
        self.assertEqual(len(state['entries']), 1063)
        self.assertEqual(sum(len(entry['text']) for entry in state['entries']), 180169)
        self.assertEqual(sum(len(entry['purpose']) for entry in state['entries']), 131927)
        self.assertEqual(plan['task']['final']['material_allowance'], 480000)
        self.assertTrue(state['carried_readings'])
        units = {row['handle']: row for row in result['inventory']['units']}
        carried = [units[handle] for handle in state['carried_readings']]
        spilled = [entry['handle'] for row in carried
                   for entry in json.loads(result['bodies'][row['sha256']])['entries']]
        self.assertEqual(set(state['pool']).intersection(spilled), set())
        self.assertEqual(set(state['pool'] + spilled), {entry['handle'] for entry in state['entries']})
        self.assertTrue(all(row['kind'] == 'carried_reading' for row in carried))
        from research_harness import review_route_capacity as capacity
        material = [row for row in result['inventory']['material'] if row['kind'] == 'carried_reading']
        forecast = capacity.describe_capacity_forecast(plan, route, plan['profile'], remaining_inventory=material)
        self.assertEqual(forecast['by_kind']['reading']['turns'], 1)

    def test_state_retains_own_gaps_arguments_and_allowance_use(self):
        projection = self.v7.project_carried_turn(self.inventory, self.observation,
                                                 select_observation(self.inventory, self.observation))
        own = copy.deepcopy(projection)
        own['turn'].update(assignment_id='new-1', attempt_id='new-try', origin=None)
        own['entries'][0]['targets'] = ['t0']
        own.update(gaps=[dict(formulation='t0', text='An explicit remaining gap.')],
                   arguments=[dict(handle='a1', title='Argument retained separately.', artifact=self.row['artifact'])],
                   allowance_use=dict(targets={'t0': 400}, passages=31))
        result = self.v7.build_chain_state(self.inventory, [projection, own],
                                           route=build_route(effort='xhigh'), plan=build_plan())
        state = result['state']
        self.assertEqual(state['gaps'], own['gaps'])
        self.assertEqual(state['arguments'], own['arguments'])
        self.assertEqual(state['allowances']['targets'][0]['used'], 400)
        self.assertEqual(state['allowances']['passages']['used'], 31)
        self.assertEqual(state['latest_notes'], own['notes']['text'])


class StoredV5ProjectionTests(unittest.TestCase):
    def test_real_shape_v5_stopped_chain_uses_its_pending_capture_binding(self):
        v7 = importlib.import_module('research_harness.openness_reading_v7')
        case, metadata = open_stored_case(self, 5, 'stopped-both-observed')
        records = case.store.snapshot()['records']
        before = copy.deepcopy(records)
        assignment = records['review_assignment']['turn-1']
        native = json.loads(case.artifacts.read(metadata['native']))
        planned = v7.build_inventory(records, case.artifacts, native, review=assignment)
        observations = v7.observe_carried_chain(records, case.artifacts, assignment)
        self.assertEqual(len(observations), 1)
        projection = v7.project_carried_turn(planned['inventory'], observations[0],
                                            select_observation(planned['inventory'], observations[0]))
        self.assertTrue(projection['deliveries'])
        chosen = [row for row in planned['inventory']['units']
                  if row['identity'].get('version_id') == metadata['work']]
        self.assertEqual([row['sha256'] for row in chosen], [metadata['compact']['text']['sha256']])
        self.assertNotIn(chosen[0]['handle'], [row['unit'] for row in projection['deliveries']])
        self.assertEqual(records, before)
        self.assertEqual(observations, v7.observe_carried_chain(records, case.artifacts, assignment))


class V6ProjectionTests(OpennessReadingCase):
    reading_protocol = None
    route_model = 'gpt-6-astra'
    text_sizes = (80000, 80000)

    def test_real_shape_v6_observation_projects_checked_excerpts_and_passages(self):
        v7 = importlib.import_module('research_harness.openness_reading_v7')
        self.limit_input(1300000)
        reference = self.export()
        assignment = self.assign('turn-1', reference)
        packet = self.read_packet('turn-1')
        self.invoke('turn-1', self.build_reading_response(packet, passages=[dict(
            id='p1', version_id=self.versions[0], text_sha256=self.texts[self.versions[0]]['sha256'],
            anchor='Paper 1 opening line.', characters=100)]))
        records = self.store.snapshot()['records']
        before = copy.deepcopy(records)
        planned = v7.build_inventory(records, self.artifacts, json.loads(self.artifacts.read(reference)))
        context = json.loads(self.artifacts.read(reference))['scientific_context']
        units = {row['identity'].get('record_kind'): row for row in planned['inventory']['units']}
        for field, expected in (('openness_minimum', context['openness_minimum']),
                                ('minimum_gaps', context['minimum_gaps']),
                                ('audit_notices', context['audit_forecast']['notices'])):
            self.assertEqual(planned['bodies'][units[field]['sha256']], serialize_record(expected))
        observations = v7.observe_carried_chain(records, self.artifacts, assignment)
        self.assertEqual(observations[0]['origin']['protocol'], 'exactory-openness-reading-v6')
        self.assertTrue(observations[0]['excerpts'])
        self.assertTrue(observations[0]['passages'])
        projection = v7.project_carried_turn(planned['inventory'], observations[0],
                                            select_observation(planned['inventory'], observations[0]))
        self.assertEqual([entry['kind'] for entry in projection['entries']], ['excerpt', 'passage'])
        self.assertEqual(records, before)
        self.assertEqual(assignment['prompt']['template_id'], 'exactory-openness-reading-v6/reading')


class LegacyV5ProjectionTests(OpennessReadingCase):
    reading_protocol = None
    historical_v5 = True

    def test_v5_checked_spans_project_with_original_request_bytes_unchanged(self):
        v7 = importlib.import_module('research_harness.openness_reading_v7')
        self.limit_input()
        reference = self.export()
        assignment = self.assign('turn-1', reference)
        packet = self.read_packet('turn-1')
        self.invoke('turn-1', self.build_reading_response(packet, passages=[dict(
            id='old-p1', version_id=self.versions[0], anchor='Paper 1 opening line.', characters=100)]))
        records = self.store.snapshot()['records']
        before = {name: self.artifacts.read(records['review_attempt']['attempt-turn-1'][name])
                  for name in ('request', 'response')}
        observations = v7.observe_carried_chain(records, self.artifacts, assignment)
        self.assertEqual(observations[0]['origin']['protocol'], 'exactory-openness-reading-v5')
        self.assertTrue(observations[0]['excerpts'])
        self.assertEqual(len(observations[0]['passages']), 1)
        inventory = v7.build_inventory(records, self.artifacts,
                                       json.loads(self.artifacts.read(reference)), review=assignment)['inventory']
        projection = v7.project_carried_turn(inventory, observations[0],
                                             select_observation(inventory, observations[0]))
        self.assertEqual(len(projection['entries']), len(observations[0]['excerpts']) + 1)
        self.assertEqual(projection['entries'][-1]['kind'], 'passage')
        for entry in projection['entries']:
            self.assertEqual(hashlib.sha256(entry['text'].encode()).hexdigest(), entry['sha256'])
        self.assertEqual({name: self.artifacts.read(records['review_attempt']['attempt-turn-1'][name])
                          for name in before}, before)
