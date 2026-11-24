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
from research_harness import review_prompt_paragraphs as paragraphs, review_protocol as protocol
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
# The registered version of the paragraph of G8.11 that a chain with the frame members of 0.93.0 selects, written out
# for the same reason.
FINDING_UNITS_VERSION = 'exactory-openness-finding-units-v2'


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
        # The six fixed packets of T6; excluded-work-long holds a value that leaves (ExcludedWorkLongTests).
        for name in [name for name in FIXED_PACKET_NAMES if name != 'excluded-work-long']:
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


def admit(route, artifacts, native, built, *, finding_version=2, frame_bound=None, reading_bound=None):
    """Admit on the route, or on a route whose final frame can hold at most frame_bound characters.

    With reading_bound as well, the notes reserve of the reading frame is sized so that the reading frame can hold at
    most reading_bound characters on the same route.
    """
    from research_harness.review_refusal_notice import BOUNDED_NOTICE_CHARACTERS
    patches = [patch.object(v7, 'build_inventory', return_value=built),
               patch.object(capacity, 'read_profile_priors', return_value=[])]
    if frame_bound is not None:
        room = frame_bound + BOUNDED_NOTICE_CHARACTERS + capacity.describe_material_levels('final')[-1]
        tokens = capacity.describe_route_capacity(route)['input_tokens']['value']
        # The reading frame keeps its notes reserve at the minimum, so the bound isolates the final frame.
        notes = 6000
        if reading_bound is not None:
            rows = v7.compute_allowance_rows_bound(route, [row['handle'] for row in v7.list_target_labels(native)])
            notes = (room - BOUNDED_NOTICE_CHARACTERS - capacity.describe_material_levels('reading')[-1] - rows
                     - reading_bound)
        patches += [patch.object(capacity, 'NOTES_MAXIMUM_CHARACTERS', notes),
                    patch.object(v7, 'derive_inherited_ratio', return_value=dict(
                        characters_per_token=(room + .5) / tokens, source=dict(chain='old', attempt='measured')))]
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        return v7.check_v7_admission({}, artifacts, route, native, finding_version=finding_version)


def measure_version_change(route):
    """Return the characters that a version 2 final adds to its frame at least: longer rules and a descriptor."""
    from research_harness.review_protocol import canonical_prompt
    texts = [canonical_prompt('native_math', reading_stage='final', reading_protocol=v7.PROTOCOL,
                              finding_version=version)['text'] for version in (1, 2)]
    return len(texts[1]) - len(texts[0]) + v7.measure_finding_descriptor(route)


class AdmissionLoopTests(unittest.TestCase):
    """G8.6 on admission alone: the steps of the loop, and the band in which version 2 refuses and version 1 admits."""

    def setUp(self):
        self.route = build_route('codex_cli_v1', 'gpt-6-astra')
        self.artifacts = MemoryArtifacts()

    def admit(self, native, built, **options):
        return admit(self.route, self.artifacts, native, built, **options)

    def measure_version_change(self):
        return measure_version_change(self.route)

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


def list_chain_turns(case, first):
    """Return (assignment, packet) for every turn of the chain that starts with first, in recorded order."""
    records = case.store.snapshot()['records']
    rows = sorted((row for row in records['review_assignment'].values()
                   if row.get('reading_chain', {}).get('first_assignment_id') == first),
                  key=lambda row: row['recorded_revision'])
    return [(row, json.loads(case.artifacts.read(row['packet']))) for row in rows]


def list_body_deliveries(turns, handle):
    """Return the spans of one body that the reading turns delivered, as (start, end) in delivery order."""
    return [(row['start'], row['end']) for _, packet in turns
            for row in packet.get('reading_turn', {}).get('plan', {}).get('now', []) if row['handle'] == handle]


def merge_spans(spans):
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def read_body_text(case, inventory, handle):
    body = next(row for row in inventory['material'] if row['handle'] == handle)
    return case.artifacts.read(body['artifact']).decode('utf-8')


def assert_reading_turns_name_values(test, turns, count):
    """Every reading turn holds reading_turn.finding_units with count, and its prompt the registered paragraph."""
    reading_turns = [(assignment, packet) for assignment, packet in turns if 'reading_turn' in packet]
    test.assertTrue(reading_turns)
    for assignment, packet in reading_turns:
        with test.subTest(turn=assignment['id']):
            test.assertEqual(packet['reading_turn']['finding_units'], count)
            test.assertIn(FINDING_UNITS_VERSION, assignment['prompt']['parameters']['problem_paragraphs'])
            test.assertIn(paragraphs.FINDING_UNITS_PROMPT_V2, assignment['prompt']['text'])
            test.assertTrue(protocol._prompt_matches(assignment, packet))
    return reading_turns


class FindingUnitsTests(unittest.TestCase):
    """G8.11: the reading turns of a chain whose final names values by their bodies hold their count and paragraph."""

    def make_case(self):
        case = index_case(describe_long_shapes(120000))
        self.addCleanup(case.doCleanups)
        return case

    def test_g8_reading_turns_hold_the_count_and_the_paragraph_and_the_inventory_the_spans(self):
        case = self.make_case()
        reference = case.export()
        current, packet = case.finish_reading(reference)
        inventory = read_chain(case, current)['inventory']
        shared, own = case.searches[0]['dispositions'], case.searches[2]['dispositions']
        rows = inventory['finding_units']
        self.assertEqual([row['unit'] for row in rows],
                         [packet['finding_index']['units'][digest(value)]['unit'] for value in (shared, own)])
        self.assertEqual((rows[0]['start'], rows[0]['end']), (0, measure_json(shared)))
        self.assertEqual(rows[1]['unit'], find_search_body(inventory, case.searches[2]))
        for row, value in zip(rows, (shared, own)):
            text = read_body_text(case, inventory, row['unit'])
            self.assertEqual(text[row['start']:row['end']], serialize_record(value).decode('utf-8'))
        turns = assert_reading_turns_name_values(self, list_chain_turns(case, 'turn-1'), 2)
        from ci.capacity_packet_inventory import read_packet_registry, validate_packet
        registry = read_packet_registry()
        self.assertEqual(validate_packet('native_math', turns[0][1], registry), [])
        self.assertTrue(validate_packet('native_math', dict(turns[0][1], reading_turn=dict(
            turns[0][1]['reading_turn'], finding_units=dict(undeclared=True))), registry))
        for row in rows:
            self.assertTrue(any(start <= row['start'] and row['end'] <= end
                                for start, end in merge_spans(list_body_deliveries(turns, row['unit']))))
        self.assertNotIn('finding_units', packet['reading_final'])
        LongValueChainTests.record_supported_open(self, case, reference, current)

    def test_g8_a_span_is_located_by_the_key_of_its_value_and_not_by_its_text(self):
        value = [dict(disposition='out_of_scope', reason='The delivered row concerns infinite graphs only.',
                      work_id='work-1')]
        residual = dict(fields=dict(dispositions=value, resolved=value, scope='Finite graphs.'), record_id='search-1',
                        search_digest='a' * 64, shared_fields=dict(queries='b' * 64))
        text = serialize_record(residual).decode('utf-8')
        expected = serialize_record(value).decode('utf-8')
        for path in (('fields', 'dispositions'), ('fields', 'resolved'), ('fields', 'scope'), ('record_id',), ()):
            with self.subTest(path=path):
                start, end = delivery.locate_value_span(residual, path)
                found = residual
                for key in path:
                    found = found[key]
                self.assertEqual(text[start:end], serialize_record(found).decode('utf-8'))
        start, _ = delivery.locate_value_span(residual, ('fields', 'resolved'))
        self.assertNotEqual(start, text.find(expected))

    def test_g8_a_refused_reading_turn_keeps_the_paragraph_before_the_refusal_paragraph(self):
        texts = dict(whole=paragraphs.REFUSAL_WHOLE_PROMPT, bounded=paragraphs.REFUSAL_BOUNDED_PROMPT)
        # A refused answer whose escaped text does not fit the reading turn takes the bounded notice.
        pattern = '{"finding": ["quoted", "\\\\", null]}\n'
        for form, padding in (('whole', 0), ('bounded', 200000)):
            with self.subTest(form=form):
                case = self.make_case()
                reference = case.export()
                case.assign('turn-1', reference)
                output = dict(stage='reading', notes=42)
                if padding:
                    output['padding'] = (pattern * (padding // len(pattern) + 1))[:padding]
                response = case.invoke('turn-1', output)
                self.assertIn('output_refusal', response)
                current, packet = replace_refused(case, 'turn-1', 'replacement-' + form)
                self.assertEqual(packet['output_refusal']['form'], form)
                self.assertEqual(packet['reading_turn']['finding_units'], 2)
                assignment = case.store.snapshot()['records']['review_assignment'][current]
                prompt = assignment['prompt']
                self.assertEqual(prompt['parameters']['problem_paragraphs'],
                                 [FINDING_UNITS_VERSION, paragraphs.NOTES_COST_PARAGRAPH_VERSION,
                                  paragraphs.LIST_ROWS_PARAGRAPH_VERSION, paragraphs.REFUSAL_PARAGRAPH_VERSIONS[form]])
                self.assertLess(prompt['text'].index(paragraphs.FINDING_UNITS_PROMPT_V2), prompt['text'].index(texts[form]))
                self.assertTrue(protocol._prompt_matches(assignment, packet))
                current, packet = continue_chain(case, reference, current, 'after-' + form)
                LongValueChainTests.record_supported_open(self, case, reference, current)
                case.doCleanups()

    def test_g8_a_capacity_replacement_of_a_reading_turn_measures_the_paragraph(self):
        from codex_route_fixtures import bind_codex_response, build_codex_response
        from research_harness import review_preflight
        case = self.make_case()
        reference = case.export()
        case.assign('turn-1', reference)
        def respond(route, request, credential):
            response = build_codex_response(route, request, {})
            response.update(status='incomplete')
            response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
            return bind_codex_response(route, request, response)
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            case.mutate(protocol.invoke_assignment, dict(id='over-1', assignment_id='turn-1'), credential='fixture')
        records = case.store.snapshot()['records']
        recovery = capacity.plan_capacity_recovery(records, case.artifacts, records['review_assignment']['turn-1'],
                                                   'output_over_cap')
        self.assertEqual(recovery['kind'], 'replace')
        replacement = case.mutate(protocol.record_assignment, recovery['payload'])['result']
        packet = case.read_packet(replacement['id'])
        self.assertEqual(packet['reading_turn']['finding_units'], 2)
        self.assertLess(packet['reading_turn']['capacity']['material_allowance'],
                        case.read_packet('turn-1')['reading_turn']['capacity']['material_allowance'])
        self.assertIn(paragraphs.FINDING_UNITS_PROMPT_V2, replacement['prompt']['text'])
        records = case.store.snapshot()['records']
        report = review_preflight.build_assignment_report(records, case.artifacts,
                                                          records['review_assignment'][replacement['id']])
        self.assertEqual(recovery['preflight']['input_size'], report['input_size'])

    def test_g8_the_paragraph_and_its_member_fit_the_reserve_that_the_reading_frame_holds(self):
        from research_harness import review_transport as transport
        for adapter, model in (('codex_cli_v1', 'gpt-6-astra'), ('claude_cli_v1', 'claude-opus-5-5')):
            for text in (paragraphs.FINDING_UNITS_PROMPT, paragraphs.FINDING_UNITS_PROMPT_V2):
                with self.subTest(adapter=adapter, version=len(text)):
                    route = build_route(adapter, model)
                    base = capacity.measure_request(route, transport.request_body(route, '', {}))['text_characters']
                    request = transport.request_body(route, text, dict(finding_units=999999999))
                    self.assertLessEqual(capacity.measure_request(route, request)['text_characters'] - base,
                                         v7.FINDING_UNITS_CHARACTERS)
        route, artifacts = build_route('codex_cli_v1', 'gpt-6-astra'), MemoryArtifacts()
        for reason in (None, build_reason('long', 30000)):
            with self.subTest(reason=reason is not None):
                native, built = build_admission_native(artifacts, [build_search(number, reason) for number in range(5)])
                admission = admit(route, artifacts, native, built)
                inventory, plan = admission['inventory']['inventory'], admission['capacity_plan']
                self.assertEqual('finding_units' in inventory, reason is not None)
                first = v7.measure_turn_frames(route, native)['reading']
                second = v7.measure_turn_frames(route, native, fixed_bounds=inventory['fixed_bounds'],
                    finding_index=plan['finding_index'], inventory=inventory)['reading']
                # The frame counts the reserve for a chain whose final names a value. A chain whose final holds
                # the complete index keeps the reading frame of finding version 1.
                self.assertEqual(second, dict(first, finding_units=v7.FINDING_UNITS_CHARACTERS) if reason else first)
                self.assertEqual(plan['frame']['reading']['fields'], second)
                self.assertNotIn('finding_units', v7.measure_turn_frames(route, native, finding_index=None)['final'])

    def test_g8_a_descriptor_index_without_a_value_that_can_leave_names_the_unit_alone(self):
        route, artifacts = build_route('codex_cli_v1', 'gpt-6-astra'), MemoryArtifacts()
        native, built = build_admission_native(artifacts, [build_search(number, None, 5000) for number in range(10)])
        frame = sum(v7.measure_turn_frames(route, native)['final'].values())
        admission = admit(route, artifacts, native, built, frame_bound=frame + 30000)
        self.assertEqual(admission['state'], 'record')
        inventory, plan = admission['inventory']['inventory'], admission['capacity_plan']
        self.assertEqual(delivery.build_last_finding_form(plan['finding_index'], inventory)['units'], {})
        unit = find_index_unit(inventory)
        self.assertEqual(v7.fit_packet_finding_index(route, inventory, plan['finding_index']),
                         dict(unit=unit['bodies'][0], characters=unit['characters'], searches=10, read_works=0))
        self.assertEqual(inventory['finding_units'], [dict(unit=unit['bodies'][0], start=0, end=unit['characters'])])
        logical = dict(protocol='exactory-independent-review-v1', role='native_math', reviewer_id='openness-reviewer',
                       dossier_id=None, native=native)
        packet, _ = v7.prepare_first_assignment({}, artifacts, dict(id='turn-1'), route, logical, [],
                                                admission=admission)
        self.assertEqual(packet['reading_turn']['finding_units'], 1)
        self.assertEqual(paragraphs.select_prompt_parameters('native_math', packet),
                         dict(problem_paragraphs=[FINDING_UNITS_VERSION, paragraphs.NOTES_COST_PARAGRAPH_VERSION,
                                                 paragraphs.LIST_ROWS_PARAGRAPH_VERSION]))

    def test_g8_outside_the_reading_band_version_2_admits_wherever_version_1_does(self):
        route, artifacts = build_route('codex_cli_v1', 'gpt-6-astra'), MemoryArtifacts()
        change = measure_version_change(route)
        for searches in (1, 5):
            for characters in (0, 6000, 100000):
                native, built = build_admission_native(artifacts, [build_search(number, build_reason('shared', characters))
                                                                   for number in range(searches)])
                frames = v7.measure_turn_frames(route, native)
                reading_frame, final_frame = (sum(frames[kind].values()) for kind in ('reading', 'final'))
                for extra in (0, 3000):
                    with self.subTest(searches=searches, characters=characters, extra=extra):
                        bounds = dict(frame_bound=final_frame + change + 3000,
                                      reading_bound=reading_frame + v7.FINDING_UNITS_CHARACTERS + extra)
                        self.assertEqual(admit(route, artifacts, native, built, finding_version=1, **bounds)['state'],
                                         'record')
                        self.assertEqual(admit(route, artifacts, native, built, **bounds)['state'], 'record')
        for long_statement in (False, True):
            with self.subTest(band=True, long_statement=long_statement):
                native, built = build_admission_native(artifacts, [build_search(0, build_reason('one', 6000))])
                if long_statement:
                    native['subject']['statement'] = 'A synthetic exact statement. ' + 's' * 3000
                frames = v7.measure_turn_frames(route, native)
                reading_frame, final_frame = (sum(frames[kind].values()) for kind in ('reading', 'final'))
                bounds = dict(frame_bound=final_frame + change + 3000,
                              reading_bound=reading_frame + v7.FINDING_UNITS_CHARACTERS - 1)
                self.assertEqual(admit(route, artifacts, native, built, finding_version=1, **bounds)['state'], 'record')
                admission = admit(route, artifacts, native, built, **bounds)
                if long_statement:
                    # The reading frame moves its largest movable field to a unit, and the chain is admitted.
                    self.assertEqual(admission['state'], 'record')
                    fixed_bounds = admission['inventory']['inventory']['fixed_bounds']
                    self.assertIn(0, (fixed_bounds['statement'], fixed_bounds['formulations']))
                else:
                    self.assertEqual((admission['state'], admission['details']['reason']), ('stop', 'fixed_frame'))


class ReserveInTheLoopTests(unittest.TestCase):
    """G8.11: the reading frame counts the reserve in the pass of the admission loop whose inventory names a value."""

    def test_g8_the_reading_frame_counts_the_reserve_when_the_index_gives_up_values_in_the_loop(self):
        route, artifacts = build_route('codex_cli_v1', 'gpt-6-astra'), MemoryArtifacts()
        change = measure_version_change(route)
        native, built = build_admission_native(artifacts, [build_search(number, build_reason(str(number), 30000))
                                                           for number in range(2)])
        frames = v7.measure_turn_frames(route, native)
        reading_frame, final_frame = (sum(frames[kind].values()) for kind in ('reading', 'final'))
        # The complete index fits its stated size: no value is named and nothing is reserved, on the route as it is
        # and on a route whose reading frame can hold the reading frame of finding version 1 and no more.
        for bounds in ({}, dict(frame_bound=final_frame + change + 120000, reading_bound=reading_frame)):
            with self.subTest(bounds=sorted(bounds)):
                admission = admit(route, artifacts, native, built, **bounds)
                self.assertEqual(admission['state'], 'record')
                self.assertNotIn('finding_units', admission['inventory']['inventory'])
                self.assertEqual(admission['capacity_plan']['frame']['reading']['characters'], reading_frame)
        # A final frame over its bound lets the index give up values; the plan of that admission counts the reserve.
        final_bound = dict(frame_bound=final_frame + change + 20000)
        admission = admit(route, artifacts, native, built,
                          reading_bound=reading_frame + v7.FINDING_UNITS_CHARACTERS, **final_bound)
        self.assertEqual(admission['state'], 'record')
        inventory, plan = admission['inventory']['inventory'], admission['capacity_plan']
        self.assertLess(inventory['fixed_bounds']['finding_index'], STATED_CHARACTERS)
        self.assertTrue(inventory['finding_units'])
        self.assertEqual(plan['frame']['reading']['fields']['finding_units'], v7.FINDING_UNITS_CHARACTERS)
        self.assertEqual(plan['frame']['reading']['characters'], reading_frame + v7.FINDING_UNITS_CHARACTERS)
        # With one character less for the reading frame, the chain has no field to move and is refused.
        refused = admit(route, artifacts, native, built,
                        reading_bound=reading_frame + v7.FINDING_UNITS_CHARACTERS - 1, **final_bound)
        self.assertEqual((refused['state'], refused['details']['reason']), ('stop', 'fixed_frame'))


class ExcludedWorkLongTests(unittest.TestCase):
    """Test 12 without reviewer calls: the fixed acceptance packet excluded-work-long in a real v7 store."""

    def test_g8_the_long_excluded_work_judgment_is_read_kept_and_settles_the_final(self):
        from openness_named_gap_fixtures import load_fixed_packet
        descriptor = load_fixed_packet('excluded-work-long')
        self.assertGreater(sum(measure_json(row) for row in descriptor['dispositions'][2:]), 100000)
        case = fixture_case('excluded-work-long', 'v7')
        self.addCleanup(case.doCleanups)
        reference = case.export()
        current, packet = case.finish_reading(reference)
        inventory = read_chain(case, current)['inventory']
        dispositions = case.search['dispositions']
        self.assertEqual(case.searches[1]['dispositions'], dispositions)
        judgment = dispositions[1]
        self.assertEqual((judgment['disposition'], judgment['reason']),
                         ('out_of_scope', 'The delivered row concerns infinite graphs only.'))
        body = find_body(inventory, digest(dispositions))
        self.assertEqual(packet['finding_index']['units'],
                         {digest(dispositions): dict(unit=body, characters=measure_json(dispositions))})
        self.assertEqual(inventory['finding_units'], [dict(unit=body, start=0, end=measure_json(dispositions))])
        assert_reading_turns_name_values(self, list_chain_turns(case, 'turn-1'), 1)
        kept = [row for row in packet['reading_final']['entries']
                if row['text'] == serialize_record(judgment).decode('utf-8')]
        self.assertEqual([(row['unit'], row['targets']) for row in kept], [(body, ['t1', 't2'])])
        accepted = case.invoke(current, build_fixed_answer(packet))
        self.assertNotIn('output_refusal', accepted, accepted)
        record = case.mutate(case.load_problem_module().record_openness, dict(assignment_id=current))['result']
        self.assertEqual(record['outcome'], descriptor['expected_outcome'])
        records = case.store.snapshot()['records']
        reading.verify_chain(records, case.artifacts, records['review_assignment'][current])


# The deliveries of the new chain of the G8 journey, scenario stopped-both-observed, measured on the first change set
# (63ea8d29): its final holds the complete index, and the second change set leaves them as they are.
STOPPED_BOTH_OBSERVED_DELIVERIES = [
    [('u19', 0, 489), ('u3', 0, 12672)],
    [('u3', 12672, 41715)],
    [('u3', 41715, 50000), ('u5', 0, 1298), ('u7', 0, 2526), ('u9', 0, 3143), ('u11', 0, 298), ('u13', 0, 519),
     ('u15', 0, 210), ('u17', 0, 455)]]


def run_g8_journey(owner, scenario, *, every_value_leaves=False):
    """Run the journey of the G8 test on its stored store and return the case, receipt and turns of the new chain.

    The new chain carries the turns of the stored v5 chain. every_value_leaves gives the chain the stated size of the
    last form of its index, so that every value that can leave does.
    """
    from unbound_packet_fixtures import open_stored_case
    from codex_route_fixtures import limit_codex_input, record_codex_route
    from research_harness.problem_openness import export_openness
    import test_openness_carried_integration as integration
    import test_openness_reading_v7_refusal as refusal
    import test_openness_reading_v7_turns as turn
    case, _ = open_stored_case(owner, 5, scenario)
    limit_codex_input(case, 1300000)
    route = case.store.snapshot()['records']['review_route']['route']
    record_codex_route(case, 'current-route', model=route['model'],
                       output_tokens=route['configuration']['max_output_tokens'])
    def assign(identifier, native_packet, prior=None):
        context = dict(native_packet=native_packet)
        if prior is not None:
            context['prior_assignment_id'] = prior
        return case.mutate(protocol.record_assignment, dict(id=identifier, route_id='current-route', dossier_id=None,
            role='native_math', reviewer_id='openness-reviewer', author_id=case.author_id, context=context))['result']
    case.assign = assign
    minimum = dict(searches=[dict(formulation=row['question'], search_id=row['id']) for row in case.searches],
                   settling_work_id=None, duplicate_resolutions=[])
    with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
        native = export_openness(case.store, dict(assignment_id='c8-successor', reviewer_id='openness-reviewer',
            minimum=minimum), case.root / 'c8-new-export', accept_gaps=True)['native_packet']
        stated = v7.FINDING_INDEX_CHARACTERS
        if every_value_leaves:
            records = case.store.snapshot()['records']
            current = records['review_route']['current-route']
            admission = v7.check_v7_admission(records, case.artifacts, current, json.loads(case.artifacts.read(native)))
            stated = capacity.measure_capacity_value(current, delivery.build_last_finding_form(
                admission['capacity_plan']['finding_index'], admission['inventory']['inventory']))
        with patch.object(v7, 'FINDING_INDEX_CHARACTERS', stated):
            receipt = case.assign('c8-successor', native)
        case.build_v7_answer = lambda packet: integration.CarriedIntegrationSupport.answer_v7(case, packet)
        case.build_v7_finding = lambda packet, handle: turn.V7CommandTests.build_v7_finding(case, packet, handle)
        refusal.V7RefusalTests.finish_from(case, native, 'c8-successor')
    return case, receipt, list_chain_turns(case, 'c8-successor')


class CarriedChainTests(unittest.TestCase):
    """Test 15 (G8.12): a chain that carries earlier turns reads the values that its final names in its own turns."""
    def setUp(self):
        from research_harness import openness_reading_v7
        released = patch.object(openness_reading_v7, 'V7_FINAL_FORMS', ('request',))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def read_initial_state(self, case, receipt):
        inventory = json.loads(case.artifacts.read(receipt['reading_chain']['inventory']))
        return inventory, json.loads(case.artifacts.read(receipt['reading_chain']['state']))

    def test_g8_a_carried_chain_reads_the_value_that_its_final_names_in_its_own_turns(self):
        case, receipt, turns = run_g8_journey(self, 'workaround')
        self.assertEqual(receipt['protocol_selection']['protocol'], v7.PROTOCOL)
        inventory, initial = self.read_initial_state(case, receipt)
        searches = {row['id']: row for row in case.searches}
        shared = searches['shared-search-0']['dispositions']
        self.assertEqual(searches['shared-search-1']['dispositions'], shared)
        body = find_body(inventory, digest(shared))
        self.assertEqual(measure_json(shared), 290071)
        self.assertEqual(inventory['finding_units'], [dict(unit=body, start=0, end=290071)])
        final = turns[-1][1]
        self.assertEqual(final['finding_index']['units'], {digest(shared): dict(unit=body, characters=290071)})
        reading_turns = assert_reading_turns_name_values(self, turns, 1)
        # Each character of the named body arrives once, in the chain's own turns; no full text arrives (test 1).
        spans = list_body_deliveries(reading_turns, body)
        self.assertEqual(merge_spans(spans), [(0, 290071)])
        self.assertEqual(sum(end - start for start, end in spans), 290071)
        self.assertFalse(any(row['kind'] == 'fulltext' for _, packet in reading_turns
                             for row in packet['reading_turn']['plan']['now']))
        # The three searches stay credited to the carried turns.
        expected = sorted(row['search_digest'] for row in inventory['search_delivery']['searches'])
        self.assertEqual(sorted(initial['searches']), expected)
        self.assertTrue(all(turn['origin'] is not None for turn in initial['turns']))
        # The report of the carried selection lists the named body as remaining work, as the reading plan does.
        report = receipt['carried_readings']
        remaining = {row['unit']: row for row in report['remaining_material']}
        self.assertEqual(remaining[body]['ranges'], [dict(start=0, end=290071, characters=290071)])
        planned = {}
        for row, start, end in v7._find_remaining_ranges(inventory, initial):
            planned.setdefault(row['handle'], []).append((start, end))
        self.assertEqual(sum(end - start for start, end in planned[body]), remaining[body]['characters'])
        self.assertEqual(report['credited_material_characters'] + report['remaining_material_characters'],
                         sum(row['characters'] for row in v7.read_carried_inventory(case.artifacts, receipt)['material']))
        # Outside G8: a carried search credit covers the residual of search-9 only up to the length of its record, so
        # the reading plan also holds the tail of that residual, which the report counts as credited.
        tails = {handle: spans for handle, spans in planned.items() if handle not in remaining}
        self.assertEqual(tails, {find_search_body(inventory, searches['search-9']): [(1155, 1797)]})
        self.assertEqual({handle: [(row['start'], row['end']) for row in remaining[handle]['ranges']]
                          for handle in remaining}, {handle: spans for handle, spans in planned.items()
                                                     if handle in remaining})
        # Condition 4: the chain verifies, and the same final without the own deliveries of the body does not. The
        # receipts of the two searches that share the body wait for those deliveries.
        records = case.store.snapshot()['records']
        verified = v7.verify_problem_coverage(records, case.artifacts, turns[-1][0], final=True)
        plan = capacity.read_chain_capacity_plan(case.artifacts, receipt)
        selection = json.loads(case.artifacts.read(receipt['carried_selection']['artifact']))
        state = verified['state']
        self.assertEqual(sorted(v7._verify_required_coverage(case.artifacts, inventory, state, plan, selection)),
                         expected)
        without = dict(state, deliveries=[row for row in state['deliveries'] if row['unit'] != body])
        with self.assertRaises(ResearchError) as caught:
            v7._verify_required_coverage(case.artifacts, inventory, without, plan, selection)
        self.assertEqual(caught.exception.code, 'openness_reading_incomplete')
        self.assertIn(dict(unit=body, start=0, end=290071), caught.exception.details['ranges'])
        owners = {row['search_digest'] for row in inventory['search_delivery']['searches']
                  if 'dispositions' in row['shared_fields']}
        self.assertEqual(len(owners), 2)
        self.assertEqual(set(v7._collect_completed_searches(case.artifacts, inventory, without, [])),
                         set(expected) - owners)

    def test_g8_a_value_that_one_carried_search_holds_alone_is_read_as_its_span(self):
        case, receipt, turns = run_g8_journey(self, 'workaround', every_value_leaves=True)
        inventory, _ = self.read_initial_state(case, receipt)
        search = next(row for row in case.searches if row['id'] == 'search-9')
        residual = find_search_body(inventory, search)
        named = [row for row in inventory['finding_units'] if row['unit'] == residual]
        self.assertEqual(len(named), 1)
        text = read_body_text(case, inventory, residual)
        self.assertEqual(text[named[0]['start']:named[0]['end']],
                         serialize_record(search['dispositions']).decode('utf-8'))
        record = next(row for row in inventory['units'] if residual in row['bodies'])
        tail = (record['characters'], len(text))
        self.assertEqual(tail, (1155, 1797))
        reading_turns = assert_reading_turns_name_values(self, turns, len(inventory['finding_units']))
        self.assertEqual(merge_spans(list_body_deliveries(reading_turns, residual)),
                         [(named[0]['start'], named[0]['end']), tail])

    def test_g8_a_carried_chain_whose_final_holds_the_complete_index_reads_as_before(self):
        case, receipt, turns = run_g8_journey(self, 'stopped-both-observed')
        inventory, _ = self.read_initial_state(case, receipt)
        self.assertNotIn('finding_units', inventory)
        self.assertNotIn('units', turns[-1][1]['finding_index'])
        reading_turns = [(assignment, packet) for assignment, packet in turns if 'reading_turn' in packet]
        self.assertEqual([[(row['handle'], row['start'], row['end']) for row in packet['reading_turn']['plan']['now']]
                          for _, packet in reading_turns], STOPPED_BOTH_OBSERVED_DELIVERIES)
        for assignment, packet in reading_turns:
            self.assertNotIn('finding_units', packet['reading_turn'])
            self.assertEqual(assignment['prompt']['parameters']['problem_paragraphs'],
                             [paragraphs.NOTES_COST_PARAGRAPH_VERSION, paragraphs.LIST_ROWS_PARAGRAPH_VERSION])


def load_queries():
    from openness_named_gap_fixtures import load_fixed_packet
    return list(load_fixed_packet('variant-search')['queries'])


if __name__ == '__main__':
    unittest.main()
