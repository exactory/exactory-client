"""A finding index over its stated size names the bodies of its longest values (G8 of 0.92.0).

Synthetic shapes of the stores measured in record 20261113110022-every-open-record-and-review-capacity: two bound
searches that share one long dispositions value beside a third search with its own, the packet with 18 bound
searches and 13 long dispositions values, the packet with 29 bound searches, and an index that fits in no form with
unit references. The reviewer is the stub sender of the reading fixtures.
"""

from contextlib import ExitStack
import copy
import json
import unittest
from unittest.mock import patch

from openness_named_gap_fixtures import (FIXED_PACKET_NAMES, bind_fixture_basis, build_fixed_answer, build_ground,
                                         build_query, build_reason, fixture_case, index_case)
from research_harness import openness_reading as reading, openness_reading_v7 as v7
from research_harness import review_route_capacity as capacity, review_search_delivery as delivery
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.problem_openness import prepare_openness, serialize_record
from test_openness_reading_v7_turns import MemoryArtifacts, build_native
from v7_capacity_fixtures import build_route

# The stated size of a finding index in 0.92.0 (FINDING_INDEX_CHARACTERS), written out so that the base commit fails
# on the behaviour and not on a missing name.
STATED_CHARACTERS = 100000
# Bound searches of the store whose index fits in no form with unit references.
SEARCHES = 40


def measure_json(value):
    return len(serialize_record(value).decode('utf-8'))


def read_chain(case, identifier):
    """Return the assignment, plan, inventory and route of one turn of a chain."""
    records = case.store.snapshot()['records']
    assignment = records['review_assignment'][identifier]
    return dict(assignment=assignment, plan=capacity.read_chain_capacity_plan(case.artifacts, assignment),
                inventory=json.loads(case.artifacts.read(assignment['reading_chain']['inventory'])),
                route=records['review_route'][assignment['route_id']])


def find_body(inventory, sha256):
    return next(row['handle'] for row in inventory['material'] if row['sha256'] == sha256)


def find_index_unit(inventory):
    return next(row for row in inventory['units'] if row['identity'] == dict(kind='fixed_field', field='finding_index'))


def find_search_body(inventory, search):
    """Return the handle of the body that holds an unshared field of a bound search: its residual or its record."""
    entry = next(row for row in inventory['search_delivery']['searches'] if row['record_id'] == search['id'])
    return find_body(inventory, (entry['residual'] or entry['artifact'])['sha256'])


def continue_chain(case, reference, current, prefix):
    """Answer reading turns with the stub until the chain assigns a final; return its id and packet."""
    for number in range(40):
        packet = case.read_packet(current)
        if 'reading_turn' not in packet:
            return current, packet
        case.invoke(current, case.build_reading_response(packet))
        following = '%s-%d' % (prefix, number + 1)
        case.assign(following, reference, prior=current)
        current = following
    raise AssertionError('The synthetic chain must reach its final.')


def request_passages(case, reference, current, packet, requests, prefix):
    """Send the requests of a final as a reading answer; return the delivered text of each request id."""
    binding = packet['reading_final']
    case.invoke(current, dict(schema_version=2, stage='reading', notes='Read the bodies that the index names.',
        excerpts=[], gaps=[], passage_requests=requests,
        **{key: binding[key] for key in ('subject_digest', 'claim_digest', 'reviewer')}))
    following = prefix + '-0'
    case.assign(following, reference, prior=current)
    answered = case.read_packet(following)
    rows = answered.get('reading_turn', answered.get('reading_final'))['plan']['now']
    delivered = {}
    for row, body in zip(rows, answered['evidence']):
        if row.get('request'):
            delivered[row['request']] = delivered.get(row['request'], '') + body['content']
    return following, delivered


def replace_refused(case, current, identifier):
    """Record the replacement that review-series plans for a refused turn; return its id and packet."""
    from research_harness import review_protocol
    from research_harness.review_continuation import plan_next
    action = plan_next(case.store.snapshot()['records'], case.artifacts, current)
    if action['state'] != 'replace':
        raise AssertionError(action)
    case.mutate(review_protocol.record_assignment, dict(copy.deepcopy(action['payload']), id=identifier))
    return identifier, case.read_packet(identifier)


def describe_long_shapes(own_characters):
    shared = build_reason('shared', 290000)
    return [dict(formulation=0, reason=shared), dict(formulation=1, reason=shared),
            dict(formulation=0, reason=build_reason('own', own_characters))]


class LongValueChainTests(unittest.TestCase):
    def make_case(self, shapes, protocol='v7', name='variant-search'):
        case = index_case(shapes, protocol, name)
        self.addCleanup(case.doCleanups)
        return case

    def record_supported_open(self, case, reference, current):
        current, packet = continue_chain(case, reference, current, 'final-turn')
        accepted = case.invoke(current, build_fixed_answer(packet))
        self.assertNotIn('output_refusal', accepted, accepted)
        record = case.mutate(case.load_problem_module().record_openness, dict(assignment_id=current))['result']
        self.assertEqual(record['outcome'], 'supported_open')
        records = case.store.snapshot()['records']
        reading.verify_chain(records, case.artifacts, records['review_assignment'][current])
        return current

    def test_g8_shared_and_own_long_dispositions_leave_the_final_and_name_their_bodies(self):
        for own_characters in (120000, 50000):
            with self.subTest(own_characters=own_characters):
                case = self.make_case(describe_long_shapes(own_characters))
                reference = case.export()
                native = json.loads(case.artifacts.read(reference))
                records = case.store.snapshot()['records']
                route = records['review_route']['route']
                self.assertEqual(v7.check_v7_admission(records, case.artifacts, route, native)['state'], 'record')
                with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
                    selection = reading.choose_chain_protocol(records, case.artifacts, route, native)
                self.assertEqual(selection['protocol'], v7.PROTOCOL)
                current, packet = case.finish_reading(reference)
                chain = read_chain(case, current)
                index, inventory = packet['finding_index'], chain['inventory']
                shared, own = case.searches[0]['dispositions'], case.searches[2]['dispositions']
                self.assertEqual(case.searches[1]['dispositions'], shared)
                expected = {digest(shared): dict(unit=find_body(inventory, digest(shared)),
                                                 characters=measure_json(shared))}
                if own_characters == 120000:
                    expected[digest(own)] = dict(unit=find_search_body(inventory, case.searches[2]),
                                                 characters=measure_json(own))
                else:
                    self.assertEqual(index['fields'][digest(own)], own)
                self.assertEqual(index['units'], expected)
                self.assertFalse(set(expected) & set(index['fields']))
                for row in index['searches']:
                    self.assertIn(row['queries'], index['fields'])
                    self.assertIn(row['scope'], index['fields'])
                self.assertEqual(index['read_works'], chain['plan']['finding_index']['read_works'])
                self.assertLessEqual(capacity.measure_capacity_value(route, index), STATED_CHARACTERS)
                requests = [dict(id='shared-body', unit=expected[digest(shared)]['unit'], anchor=None, characters=3000)]
                texts = {'shared-body': serialize_record(shared).decode('utf-8')}
                if own_characters == 120000:
                    requests.append(dict(id='own-body', unit=expected[digest(own)]['unit'],
                                         anchor=own[0]['reason'][:80], characters=3000))
                    texts['own-body'] = serialize_record(own).decode('utf-8')
                current, delivered = request_passages(case, reference, current, packet, requests, 'passages')
                self.assertEqual(set(delivered), set(texts))
                for key, text in delivered.items():
                    self.assertGreaterEqual(len(text), 2000)
                    self.assertIn(text, texts[key])
                self.record_supported_open(case, reference, current)
                case.doCleanups()

    def assert_longest_values_left(self, index, complete):
        """Every value that stays and could leave is no longer than any value that left."""
        queries = {row['queries'] for row in complete['searches']}
        left = [index['units'][key]['characters'] for key in index['units']]
        stayed = [measure_json(value) for key, value in index['fields'].items() if key not in queries]
        self.assertTrue(left)
        self.assertLessEqual(max(stayed, default=0), min(left))
        self.assertEqual(set(index['fields']) | set(index['units']), set(complete['fields']))

    def test_g8_eighteen_searches_keep_their_shortest_values_within_the_stated_size(self):
        # Packet 467f9f33 of the measured record: 18 bound searches, 13 dispositions values of 20,000 to 83,000.
        shapes = [dict(formulation=number % 2, reason=build_reason('long %d' % number, 20000 + 5250 * number))
                  for number in range(13)]
        shapes += [dict(formulation=number % 2) for number in range(5)]
        case = self.make_case(shapes)
        reference = case.export()
        records = case.store.snapshot()['records']
        route = records['review_route']['route']
        native = json.loads(case.artifacts.read(reference))
        self.assertEqual(v7.check_v7_admission(records, case.artifacts, route, native)['state'], 'record')
        current, packet = case.finish_reading(reference)
        chain = read_chain(case, current)
        index = packet['finding_index']
        self.assertEqual(len(index['searches']), 18)
        self.assertLessEqual(capacity.measure_capacity_value(route, index), STATED_CHARACTERS)
        self.assert_longest_values_left(index, chain['plan']['finding_index'])
        self.record_supported_open(case, reference, current)

    def test_g8_twenty_nine_searches_keep_every_query_and_read_work_in_a_form_with_references(self):
        # Packet 156bb277 of the measured record: 29 bound searches, each with its own queries, 22 values leave.
        shapes = [dict(formulation=number % 2, queries=[build_query(str(number), 1000)],
                       reason=build_reason('long %d' % number, 18000 + 300 * number) if number < 22 else None)
                  for number in range(29)]
        case = self.make_case(shapes)
        reference = case.export()
        current, packet = case.finish_reading(reference)
        chain = read_chain(case, current)
        index, complete = packet['finding_index'], chain['plan']['finding_index']
        self.assertEqual(set(index), {'searches', 'fields', 'read_works', 'units'})
        self.assertEqual(index['searches'], complete['searches'])
        self.assertEqual(index['read_works'], complete['read_works'])
        self.assertEqual(len({row['queries'] for row in index['searches']}), 29)
        for row in index['searches']:
            self.assertEqual(index['fields'][row['queries']], complete['fields'][row['queries']])
        self.assertLessEqual(capacity.measure_capacity_value(chain['route'], index), STATED_CHARACTERS)
        self.assert_longest_values_left(index, complete)
        self.record_supported_open(case, reference, current)


class DescriptorFormTests(unittest.TestCase):
    """40 bound searches, each with its own long query: the last form is over the stated size by a margin."""

    def make_case(self, protocol):
        queries = load_queries()
        shapes = [dict(formulation=number % 2, queries=queries + [build_query(str(number), 2500)])
                  for number in range(SEARCHES)]
        case = index_case(shapes, protocol)
        self.addCleanup(case.doCleanups)
        return case

    def test_g8_an_index_without_a_fitting_form_is_one_unit_and_the_rules_still_read_it(self):
        case = self.make_case('v7')
        reference = case.export()
        current, packet = case.finish_reading(reference)
        chain = read_chain(case, current)
        inventory, complete = chain['inventory'], chain['plan']['finding_index']
        unit = find_index_unit(inventory)
        last = delivery.build_last_finding_form(complete, inventory)
        self.assertGreater(capacity.measure_capacity_value(chain['route'], last), STATED_CHARACTERS + 20000)
        self.assertEqual(json.loads(case.artifacts.read(unit['artifact'])), last)
        self.assertEqual(packet['finding_index'], dict(unit=unit['bodies'][0], characters=unit['characters'],
                                                       searches=SEARCHES, read_works=1))
        records = case.store.snapshot()['records']
        delivered = [row for assignment in records['review_assignment'].values()
                     for row in json.loads(case.artifacts.read(assignment['packet'])).get('reading_turn', {}).get(
                         'plan', {}).get('now', []) if row['handle'] == unit['bodies'][0]]
        self.assertEqual(sum(row['characters'] for row in delivered), unit['characters'])
        self.assertTrue(all(row['label'] == 'Finding index' for row in delivered))
        current, delivered = request_passages(case, reference, current, packet,
            [dict(id='index-unit', unit=unit['bodies'][0], anchor=None, characters=3000)], 'index-passage')
        self.assertEqual(delivered['index-unit'], serialize_record(last).decode('utf-8')[:3000])
        current, packet = continue_chain(case, reference, current, 'after-passage')
        for attempt, rule in (('bound-term', 'gap_term_bound'), ('read-work', 'gap_work_read')):
            with self.subTest(rule=rule):
                # The packet shows neither the bound query nor the read work; the rules read the complete index. A
                # refusal notice repeats the forecast of the refused answer, which names the queries.
                self.assertNotIn('spectral radius graph', json.dumps(dict(packet, output_refusal=None)))
                self.assertEqual(packet['finding_index'], dict(unit=unit['bodies'][0], characters=unit['characters'],
                                                               searches=SEARCHES, read_works=1))
                bound = bind_fixture_basis(case, packet)
                ground = (build_ground(bound, term='graph spectral radius') if rule == 'gap_term_bound'
                          else build_ground(bound, work=dict(title=case.descriptor['read_title'])))
                response = case.invoke(current, build_fixed_answer(bound, ground=ground), attempt=attempt)
                self.assertEqual(response['output_refusal']['code'], 'openness_finding_invalid')
                refusal = case.store.snapshot()['records']['review_output_refusal'][attempt]
                self.assertEqual(refusal['details']['rule'], rule)
                current, packet = replace_refused(case, current, 'replacement-' + attempt)
                self.assertIn('output_refusal', packet)
        accepted = case.invoke(current, build_fixed_answer(packet))
        self.assertNotIn('output_refusal', accepted, accepted)
        record = case.mutate(case.load_problem_module().record_openness, dict(assignment_id=current))['result']
        self.assertEqual(record['outcome'], 'supported_open')
        records = case.store.snapshot()['records']
        reading.verify_chain(records, case.artifacts, records['review_assignment'][current])

    def test_g8_a_page_holds_the_form_of_its_own_selection(self):
        case = self.make_case('page')
        reference = case.export()
        current, packet = case.finish_reading(reference)
        self.assertIn('page_plan_digest', packet['reading_final'])
        chain = read_chain(case, current)
        native = json.loads(case.artifacts.read(reference))
        labels = {row['handle']: row['formulation'] for row in v7.list_target_labels(native)}
        formulations = [labels[handle] for handle in packet['reading_final']['page']['targets']]
        expected = delivery.fit_finding_index(chain['plan']['finding_index'], chain['inventory'], STATED_CHARACTERS,
            lambda value: capacity.measure_capacity_value(chain['route'], value), formulations=formulations)
        self.assertEqual(packet['finding_index'], expected)
        self.assertEqual(len(expected['searches']), SEARCHES // 2)


class ChainVerificationTests(unittest.TestCase):
    def make_case(self, own_characters, protocol='v7'):
        case = index_case(describe_long_shapes(own_characters), protocol)
        self.addCleanup(case.doCleanups)
        return case

    def test_g8_a_packet_whose_form_differs_from_its_rebuilt_form_is_refused(self):
        case = self.make_case(120000)
        current, packet = case.finish_reading(case.export())
        accepted = case.invoke(current, build_fixed_answer(packet))
        self.assertNotIn('output_refusal', accepted, accepted)
        originals = case.store.snapshot()['records']
        reading.verify_chain(originals, case.artifacts, originals['review_assignment'][current])
        chain = read_chain(case, current)
        complete, index = chain['plan']['finding_index'], packet['finding_index']
        left = sorted(index['units'])
        scope = index['searches'][0]['scope']
        last = delivery.build_last_finding_form(complete, chain['inventory'])
        for change in ('handle', 'characters', 'returned', 'kept_out', 'descriptor'):
            changed = copy.deepcopy(packet)
            form = changed['finding_index']
            if change == 'handle':
                form['units'][left[0]]['unit'] = form['units'][left[1]]['unit'] if (
                    form['units'][left[0]]['unit'] != form['units'][left[1]]['unit']) else 'u1'
            elif change == 'characters':
                form['units'][left[0]]['characters'] += 1
            elif change == 'returned':
                form['fields'][left[0]] = copy.deepcopy(complete['fields'][left[0]])
                del form['units'][left[0]]
            elif change == 'kept_out':
                form['units'][scope] = dict(unit=find_search_body(chain['inventory'], case.searches[0]),
                                            characters=measure_json(form['fields'].pop(scope)))
            else:
                changed['finding_index'] = dict(unit=index['units'][left[0]]['unit'], characters=measure_json(last),
                    searches=len(complete['searches']), read_works=len(complete['read_works']))
            changed.pop('digest')
            changed['digest'] = digest(changed)
            records = copy.deepcopy(originals)
            assignment = records['review_assignment'][current]
            assignment.update(packet=case.artifacts.put(serialize_record(changed), 'application/json'),
                              packet_digest=changed['digest'])
            with self.subTest(change=change), self.assertRaises(ResearchError) as caught:
                reading.verify_chain(records, case.artifacts, assignment)
            self.assertEqual(caught.exception.code, 'openness_reading_incomplete')
            with self.subTest(change=change, boundary='record'), self.assertRaises(ResearchError) as caught:
                prepare_openness(records, case.artifacts, dict(assignment_id=current), case.store.revision + 1)
            self.assertEqual(caught.exception.code, 'openness_reading_incomplete')

    def test_g8_a_chain_keeps_the_stated_size_of_its_inventory(self):
        case = self.make_case(50000)
        reference = case.export()
        case.assign('turn-1', reference)
        with patch.object(v7, 'FINDING_INDEX_CHARACTERS', 50000):
            current, packet = continue_chain(case, reference, 'turn-1', 'read')
            chain = read_chain(case, current)
            self.assertEqual(chain['inventory']['fixed_bounds']['finding_index'], STATED_CHARACTERS)
            shared, own = case.searches[0]['dispositions'], case.searches[2]['dispositions']
            self.assertEqual(packet['finding_index']['fields'][digest(own)], own)
            self.assertEqual(set(packet['finding_index']['units']), {digest(shared)})
            accepted = case.invoke(current, build_fixed_answer(packet))
            self.assertNotIn('output_refusal', accepted, accepted)
            records = case.store.snapshot()['records']
            reading.verify_chain(records, case.artifacts, records['review_assignment'][current])
            record = case.mutate(case.load_problem_module().record_openness, dict(assignment_id=current))['result']
            self.assertEqual(record['outcome'], 'supported_open')


class UnchangedFormTests(unittest.TestCase):
    def assert_complete_form(self, case, current, packet):
        native = json.loads(case.artifacts.read(
            case.store.snapshot()['records']['review_assignment'][current]['context']['native_packet']))
        records = case.store.snapshot()['records']
        self.assertEqual(packet['finding_index'], delivery.build_finding_index(records, case.artifacts, native))
        self.assertNotIn('units', packet['finding_index'])
        for assignment in records['review_assignment'].values():
            turn = json.loads(case.artifacts.read(assignment['packet'])).get('reading_turn', {})
            self.assertNotIn('finding_units', turn)

    def test_g8_an_index_within_its_stated_size_keeps_the_complete_form(self):
        for name in FIXED_PACKET_NAMES:
            with self.subTest(packet=name):
                case = fixture_case(name, 'v7')
                self.addCleanup(case.doCleanups)
                current, packet = case.finish_reading(case.export())
                self.assert_complete_form(case, current, packet)
                case.doCleanups()
        with self.subTest(packet='two values of 36,000 among ten searches'):
            case = index_case([dict(formulation=number % 2, reason=build_reason('long %d' % number, 36000)
                                    if number < 2 else None) for number in range(10)])
            self.addCleanup(case.doCleanups)
            current, packet = case.finish_reading(case.export())
            self.assertGreater(measure_json(packet['finding_index']), 72000)
            self.assert_complete_form(case, current, packet)

    def test_g8_a_v6_final_and_a_review_of_one_request_hold_the_complete_index(self):
        # The store of the long-value chain test refuses v6 on this route (review_chain_projection_over_limit), so
        # this store holds one search whose own value puts the complete index over the stated size.
        for form in ('v6', 'single'):
            with self.subTest(form=form):
                case = index_case([dict(formulation=0, reason=build_reason('own', 105000)), dict(formulation=1)], form)
                self.addCleanup(case.doCleanups)
                current, packet = case.finish_reading(case.export())
                self.assertEqual(reading.find_stage(packet), None if form == 'single' else 'final')
                route = case.store.snapshot()['records']['review_route']['route']
                self.assertGreater(capacity.measure_capacity_value(route, packet['finding_index']), STATED_CHARACTERS)
                self.assert_complete_form(case, current, packet)
                case.doCleanups()


def build_admission_native(artifacts, searches):
    """Return a problem native that binds the given search records to its two formulations, and its inventory."""
    native = build_native(2)
    minimum = native['scientific_context']['openness_minimum']
    formulations = [minimum['statement']] + minimum['neighbours']
    native['evidence'] = []
    rows, bodies, bindings = [], {}, []
    for number, search in enumerate(searches):
        content = serialize_record(search)
        reference = artifacts.put(content)
        native['evidence'].append(dict(digest=reference['sha256'], artifact=reference))
        binding = dict(formulation=formulations[number % 2], search_digest=reference['sha256'])
        minimum['searches'].append(binding)
        bindings.append(dict(binding, search=search))
        rows.append(dict(kind='record', record_kind='literature_search', record_id=search['id'], artifact=reference))
        bodies[reference['sha256']] = content
    gaps = artifacts.put_json([])
    rows.append(dict(kind='record', record_kind='minimum_gaps', record_id='minimum_gaps', artifact=gaps))
    bodies[gaps['sha256']] = b'[]'
    return native, v7.plan_inventory(rows, bodies, search_bindings=bindings)


def build_search(number, reason, query_characters=40):
    dispositions = [dict(work_id='work-%d' % number, disposition='out_of_scope', reason=reason)] if reason else []
    return dict(id='search-%d' % number, queries=[build_query(str(number), query_characters)], scope='Finite graphs.',
                dispositions=dispositions, resolved=[], row_dispositions=[])


def admit(route, artifacts, native, built, *, finding_version=2, frame_bound=None):
    """Admit on the route, or on a route whose final frame can hold at most frame_bound characters."""
    from research_harness.review_refusal_notice import BOUNDED_NOTICE_CHARACTERS
    patches = [patch.object(v7, 'build_inventory', return_value=built),
               patch.object(capacity, 'read_profile_priors', return_value=[])]
    if frame_bound is not None:
        room = frame_bound + BOUNDED_NOTICE_CHARACTERS + capacity.describe_material_levels('final')[-1]
        tokens = capacity.describe_route_capacity(route)['input_tokens']['value']
        # The reading frame keeps its notes reserve at the minimum, so the bound isolates the final frame.
        patches += [patch.object(capacity, 'NOTES_MAXIMUM_CHARACTERS', 6000),
                    patch.object(v7, 'derive_inherited_ratio', return_value=dict(
                        characters_per_token=(room + .5) / tokens, source=dict(chain='old', attempt='measured')))]
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        return v7.check_v7_admission({}, artifacts, route, native, finding_version=finding_version)


class AdmissionLoopTests(unittest.TestCase):
    """G8.6 on admission alone: the steps of the loop, and the band in which version 2 refuses and version 1 admits."""

    def setUp(self):
        self.route = build_route('codex_cli_v1', 'gpt-6-astra')
        self.artifacts = MemoryArtifacts()

    def admit(self, native, built, **options):
        return admit(self.route, self.artifacts, native, built, **options)

    def measure_version_change(self):
        """Return the characters that a version 2 final adds to its frame at least: longer rules and a descriptor."""
        from research_harness.review_protocol import canonical_prompt
        texts = [canonical_prompt('native_math', reading_stage='final', reading_protocol=v7.PROTOCOL,
                                  finding_version=version)['text'] for version in (1, 2)]
        return len(texts[1]) - len(texts[0]) + v7.measure_finding_descriptor(self.route)

    def test_g8_a_final_frame_slightly_over_its_bound_lets_the_index_give_up_values_first(self):
        native, built = build_admission_native(self.artifacts, [build_search(number, build_reason(str(number), 30000))
                                                                for number in range(5)])
        native['subject']['statement'] = 'A synthetic exact statement. ' + 's' * 80000
        admission = self.admit(native, built)
        self.assertEqual(admission['state'], 'record')
        inventory, plan = admission['inventory']['inventory'], admission['capacity_plan']
        bounds = dict(statement=100000, formulations=100000, native_frame=100000, decisive_work_ids=10000,
                      finding_index=STATED_CHARACTERS)
        final = v7.measure_turn_frames(self.route, native, fixed_bounds=bounds, finding_index=plan['finding_index'],
                                       inventory=inventory)['final']
        self.assertGreater(sum(final.values()), plan['frame']['final']['bound'])
        self.assertLess(sum(final.values()), plan['frame']['final']['bound'] + 10000)
        room = plan['frame']['final']['bound'] - sum(final.values()) + final['finding_index']
        self.assertEqual(inventory['fixed_bounds'], dict(bounds, finding_index=room))
        self.assertFalse(any(row['kind'] == 'fixed_field' and row['identity']['field'] != 'search_delivery'
                             for row in inventory['units']))
        form = v7.fit_packet_finding_index(self.route, inventory, plan['finding_index'])
        self.assertTrue(form['units'])
        self.assertLessEqual(capacity.measure_capacity_value(self.route, form), room)

    def test_g8_a_route_with_less_room_moves_the_fixed_fields_and_then_reduces_the_index_to_its_descriptor(self):
        native, built = build_admission_native(self.artifacts, [build_search(number, 'Out of scope.', 5000)
                                                                for number in range(10)])
        native['subject']['statement'] = 'A synthetic exact statement. ' + 's' * 3000
        native['scientific_context']['openness_minimum']['neighbours'] = ['Neighbour. ' + 'n' * 3000]
        native['scientific_context']['decisive_work_ids'] = ['work-%d' % number for number in range(400)]
        native['contract'] = dict(area='math.CO', original_claim='c' * 3000)
        short = dict(statement=0, formulations=0, native_frame=0, decisive_work_ids=0)
        frame = sum(v7.measure_turn_frames(self.route, native, fixed_bounds=short)['final'].values())
        admission = self.admit(native, built, frame_bound=frame + self.measure_version_change() + 1000)
        self.assertEqual(admission['state'], 'record')
        inventory, plan = admission['inventory']['inventory'], admission['capacity_plan']
        self.assertEqual(inventory['fixed_bounds'], dict(short, finding_index=0))
        unit = find_index_unit(inventory)
        self.assertEqual(v7.fit_packet_finding_index(self.route, inventory, plan['finding_index']),
                         dict(unit=unit['bodies'][0], characters=unit['characters'], searches=10, read_works=0))

    def test_g8_outside_the_band_version_2_admits_wherever_version_1_does_and_inside_it_refuses(self):
        change = self.measure_version_change()
        for searches in (1, 5, 60):
            for characters in (0, 6000, 100000, 1000000):
                reason = build_reason('shared', characters)
                native, built = build_admission_native(self.artifacts, [build_search(number, reason)
                                                                        for number in range(searches)])
                frame = sum(v7.measure_turn_frames(self.route, native)['final'].values())
                for extra in (0, 3000, 120000):
                    with self.subTest(searches=searches, characters=characters, extra=extra):
                        bound = frame + change + extra
                        admitted = self.admit(native, built, finding_version=1, frame_bound=bound)
                        self.assertEqual(admitted['state'], 'record')
                        self.assertEqual(self.admit(native, built, frame_bound=bound)['state'], 'record')
        # The band is measured against the version 1 final frame after its loop has moved every fixed field that
        # it can move (over 1,000 characters) to its short form; no field of the native above is that long.
        for long_fields in (False, True):
            with self.subTest(band=True, long_fields=long_fields):
                native, built = build_admission_native(self.artifacts, [build_search(0, build_reason('one', 6000))])
                bounds = None
                if long_fields:
                    native['subject']['statement'] = 'A synthetic exact statement. ' + 's' * 3000
                    native['scientific_context']['openness_minimum']['neighbours'] = ['Neighbour. ' + 'n' * 3000]
                    native['scientific_context']['decisive_work_ids'] = ['work-%d' % number for number in range(400)]
                    native['contract'] = dict(area='math.CO', original_claim='c' * 3000)
                    bounds = dict(statement=0, formulations=0, native_frame=0, decisive_work_ids=0)
                frame = sum(v7.measure_turn_frames(self.route, native, fixed_bounds=bounds)['final'].values())
                bound = frame + change - 1
                self.assertEqual(self.admit(native, built, finding_version=1, frame_bound=bound)['state'], 'record')
                refused = self.admit(native, built, frame_bound=bound)
                self.assertEqual((refused['state'], refused['details']['reason']), ('stop', 'fixed_frame'))
                self.assertEqual(self.admit(native, built, frame_bound=bound + 1)['state'], 'record')


class RegistryTests(unittest.TestCase):
    """G8.10: every form of the member finding_index has a closed declaration in the packet registry."""

    def setUp(self):
        self.route = build_route('codex_cli_v1', 'gpt-6-astra')
        self.artifacts = MemoryArtifacts()

    def validate_index(self, registry, value):
        from ci.capacity_packet_inventory import validate_packet
        return validate_packet('annotation', value, dict(registry, roles=dict(annotation={'$ref': 'finding_index'})))

    def test_g8_every_form_of_the_index_has_a_closed_declaration(self):
        from ci.capacity_packet_inventory import read_packet_registry, validate_packet
        registry = read_packet_registry()
        forms = {}
        for reason, query_characters, frame_bound in ((None, 40, None), (build_reason('long', 30000), 40, None),
                                                      (None, 5000, 30000)):
            native, built = build_admission_native(self.artifacts, [build_search(number, reason, query_characters)
                                                                    for number in range(10)])
            if frame_bound is not None:
                frame_bound += sum(v7.measure_turn_frames(self.route, native)['final'].values())
            admission = admit(self.route, self.artifacts, native, built, frame_bound=frame_bound)
            form = v7.fit_packet_finding_index(self.route, admission['inventory']['inventory'],
                                               admission['capacity_plan']['finding_index'])
            forms['descriptor' if 'fields' not in form else 'references' if 'units' in form else 'complete'] = form
        self.assertEqual(set(forms), {'complete', 'references', 'descriptor'})
        for name, form in forms.items():
            with self.subTest(form=name):
                self.assertEqual(self.validate_index(registry, form), [])
        changed = copy.deepcopy(forms['references'])
        next(iter(changed['units'].values()))['undeclared'] = True
        self.assertTrue(self.validate_index(registry, changed))
        self.assertTrue(self.validate_index(registry, dict(forms['descriptor'], undeclared=True)))
        case = index_case(describe_long_shapes(120000))
        self.addCleanup(case.doCleanups)
        _, packet = case.finish_reading(case.export())
        self.assertIn('units', packet['finding_index'])
        self.assertEqual(validate_packet('native_math', packet, registry), [])


def load_queries():
    from openness_named_gap_fixtures import load_fixed_packet
    return list(load_fixed_packet('variant-search')['queries'])


if __name__ == '__main__':
    unittest.main()
