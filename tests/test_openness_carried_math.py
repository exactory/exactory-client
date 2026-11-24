"""D2 G8 over math-v1 result-review chains and their authenticated reading prefixes."""

import copy
import hashlib
import json
from unittest.mock import patch

import test_research_problem_openness_minimum as minimum_fixtures
from openness_reading_fixtures import OpennessReadingCase
from research_harness import openness_carried as carried, openness_reading as reading
from research_harness import openness_reading_v7 as v7, review_protocol as protocol
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.problem_openness import serialize_record
from test_openness_carried import NO_STATE_REFUSAL_CODE, STAND_IN_REFUSAL_MESSAGE, build_identity


class MathCarriedCase(OpennessReadingCase):
    reading_protocol = None
    historical_v5 = True
    route_model = 'gpt-6-astra'
    text_sizes = (50000, 50000, 50000)

    def setUp(self):
        super().setUp()
        self.limit_input(1300000)
        released = patch.object(reading, 'NEW_MATH_CHAIN_PROTOCOLS', (reading.MATH_PROTOCOL,))
        released.start()
        self.addCleanup(released.stop)

    def build_math_packet(self, name='math-export', assignment_id='math-1', exported=None):
        """The math form of an openness export of this record: exported, or the export of the fixture."""
        native = json.loads(self.artifacts.read(exported or self.export(name=name, assignment_id=assignment_id)))
        native['contract'].pop('orchestration')
        native['scientific_context'].pop('openness_delivery', None)
        records = self.store.snapshot()['records']
        for search in records['literature_search'].values():
            for response in search['responses']:
                reference = records['source'][response['source_id']]['response']
                if not any(row['digest'] == reference['sha256'] for row in native['evidence']):
                    native['evidence'].append(dict(kind='artifact', artifact=reference, digest=reference['sha256']))
        return self.artifacts.put(serialize_record(native), 'application/json')

    def run_turn(self, native, identifier='math-1', prior=None, passages=()):
        assignment = self.assign(identifier, native, prior)
        self.assertEqual(reading.find_stage(self.read_packet(identifier)), 'reading')
        self.invoke(identifier, self.build_reading_response(self.read_packet(identifier), passages=passages))
        return assignment

    def build_selection_inputs(self, native, first):
        records = self.store.snapshot()['records']
        packet = json.loads(self.artifacts.read(native))
        rows = reading.list_reading_rows(records, self.artifacts, packet, review=first)
        inventory = v7.plan_inventory(rows, {row['artifact']['sha256']: self.artifacts.read(row['artifact'])
                                            for row in rows})['inventory']
        identity = dict(build_identity(records, self.artifacts, first), packet_kind='math')
        return records, inventory, identity

    def build_selection(self, inputs):
        return carried.build_carried_selection(inputs[0], self.artifacts, inputs[1], inputs[2])['selection']

    def verify_selection(self, inputs, selection):
        return carried.verify_carried_selection(inputs[0], self.artifacts, inputs[1], inputs[2], selection)

    def change_unit(self, inventory, unit):
        unit['sha256'] = 'f' * 64
        unit['artifact'] = dict(unit['artifact'], sha256='f' * 64)
        for body in inventory['material']:
            if unit['handle'] in body['units']:
                body['sha256'] = 'f' * 64


class MathCarriedSelectionTests(MathCarriedCase):
    def test_g8_changed_neighbour_keeps_credit_and_filters_entries_and_notes(self):
        native = self.build_math_packet()
        first = self.run_turn(native)
        records, inventory, identity = self.build_selection_inputs(native, first)
        units = [row for row in inventory['units'] if row['kind'] == 'fulltext']
        self.change_unit(inventory, units[1])
        inputs = records, inventory, identity
        selection = self.build_selection(inputs)
        self.assertEqual(len(selection['turns']), 1)
        turn = selection['turns'][0]
        self.assertIn(units[0]['handle'], [span['unit'] for span in turn['ranges']])
        self.assertNotIn(units[1]['handle'], [span['unit'] for span in turn['ranges']])
        self.assertEqual(turn['entries'], [dict(kind='excerpt', index=0)])
        self.assertEqual(turn['notes']['classification'], 'affected')
        dependency = next(row for row in turn['notes']['units'] if row['identity'] == units[1]['identity'])
        self.assertEqual((dependency['sha256'], dependency['current_sha256']),
                         (self.texts[self.versions[1]]['sha256'], 'f' * 64))
        [projection] = self.verify_selection(inputs, selection)
        self.assertEqual(len(projection['entries']), 1)

    def test_g8_complementary_chains_prefer_latest_then_emit_record_order(self):
        native = self.build_math_packet()
        first = self.run_turn(native)
        # The same result and minimum, with the captured responses and text A removed
        # from this packet's bound reading set, make the later chain start at text B.
        packet = json.loads(self.artifacts.read(native))
        packet['reviewer']['attestation_id'] = 'review-assignment:later'
        minimum = packet['scientific_context']['openness_minimum']
        records = self.store.snapshot()['records']
        minimum['searches'] = []
        minimum['reading_digests'] = [digest(row) for row in records['reading'].values()
                                      if row['version_id'] != self.versions[0]]
        later_native = self.artifacts.put(serialize_record(packet), 'application/json')
        self.limit_input(90000)
        self.run_turn(later_native, 'later')
        self.limit_input(1300000)
        inputs = self.build_selection_inputs(native, first)
        selection = self.build_selection(inputs)
        self.assertEqual([turn['origin']['chain'] for turn in selection['turns']], ['math-1', 'later'])
        units = [row for row in inputs[1]['units'] if row['kind'] == 'fulltext']
        owners = {span['unit']: turn['origin']['chain'] for turn in selection['turns'] for span in turn['ranges']}
        self.assertEqual([owners[row['handle']] for row in units[:2]], ['math-1', 'later'])
        self.assertNotIn(units[2]['handle'], owners)
        self.verify_selection(inputs, selection)

    def test_g8_identity_exclusions_and_passed_over_reviewer(self):
        native = self.build_math_packet()
        first = self.run_turn(native)
        inputs = self.build_selection_inputs(native, first)
        self.assertTrue(self.build_selection(inputs)['turns'])
        changes = [dict(reviewer_id='another-reviewer'), dict(author_id='another-author'),
                   dict(subject_digest='a' * 64), dict(claim_digest='b' * 64),
                   dict(formulations=dict(statement='Another formulation', neighbours=[]))]
        for key in ('adapter', 'model', 'endpoint', 'configuration'):
            route = copy.deepcopy(inputs[2]['route'])
            route[key] = dict(route[key], effort='other') if key == 'configuration' else 'other'
            changes.append(dict(route=route))
        for change in changes:
            with self.subTest(change=change):
                identity = dict(inputs[2], **change)
                self.assertEqual(self.build_selection((inputs[0], inputs[1], identity))['turns'], [])
                passed = carried.list_passed_over_chains(inputs[0], self.artifacts, identity)['passed_over']
                self.assertEqual(passed, [dict(chain='math-1', protocol=reading.MATH_PROTOCOL,
                    recorded_revision=first['recorded_revision'], reason='reviewer_id')]
                    if 'reviewer_id' in change else [])
        identity = copy.deepcopy(inputs[2])
        identity['reviewer_id'] = '  OPENNESS-REVIEWER  '
        identity['route'].update(id='other-route', runtime={'version': 'other'})
        self.assertTrue(self.build_selection((inputs[0], inputs[1], identity))['turns'])

    def test_g8_math_and_problem_chains_give_each_other_nothing(self):
        native = self.build_math_packet()
        first = self.run_turn(native)
        problem_native = self.export(name='problem-export', assignment_id='problem')
        self.run_turn(problem_native, 'problem')
        inputs = self.build_selection_inputs(native, first)
        selection = self.build_selection(inputs)
        self.assertEqual([row['origin']['chain'] for row in selection['turns']], ['math-1'])
        # Omitted packet_kind preserves the existing problem identity interface.
        identity = dict(inputs[2])
        identity.pop('packet_kind')
        selection = self.build_selection((inputs[0], inputs[1], identity))
        self.assertEqual([row['origin']['chain'] for row in selection['turns']], ['problem'])

    def test_g8_later_chain_does_not_reselect_stored_artifact(self):
        native = self.build_math_packet()
        first = self.run_turn(native)
        inputs = self.build_selection_inputs(native, first)
        built = carried.build_carried_selection(inputs[0], self.artifacts, inputs[1], inputs[2])
        self.assertTrue(built['selection']['turns'])
        self.assertEqual(built, carried.build_carried_selection(inputs[0], self.artifacts, inputs[1], inputs[2]))
        reference = self.artifacts.put(built['content'], 'application/json')
        self.run_turn(self.build_math_packet('later-export', 'later'), 'later')
        after = self.store.snapshot()['records'], inputs[1], inputs[2]
        with patch.object(carried, 'select_carried_readings', side_effect=AssertionError('reselection')):
            projected = self.verify_selection(after, reference)
        self.assertEqual(len(projected), 1)
        self.assertEqual(self.build_selection(after), built['selection'])

    def test_g8_observation_range_and_entry_digest_tampering_fail_verification(self):
        native = self.build_math_packet()
        first = self.run_turn(native)
        inputs = self.build_selection_inputs(native, first)
        selection = self.build_selection(inputs)
        self.assertTrue(selection['turns'])
        for name in ('output', 'request'):
            attempt = inputs[0]['review_attempt']['attempt-math-1']
            path = self.root / attempt[name]['path']
            original = path.read_bytes()
            path.chmod(0o600)
            try:
                path.write_bytes(original + b' ')
                with self.assertRaises(ResearchError) as caught:
                    self.verify_selection(inputs, selection)
                self.assertEqual(caught.exception.code, 'openness_reading_incomplete')
            finally:
                path.write_bytes(original)
        changed = copy.deepcopy(selection)
        changed['turns'][0]['ranges'][0]['sha256'] = '0' * 64
        with self.assertRaises(ResearchError):
            self.verify_selection(inputs, changed)
        observer = carried.CHAIN_OBSERVERS[reading.MATH_PROTOCOL]

        def observe_changed_span(*args):
            observations = observer(*args)
            observations[0]['excerpts'][0]['sha256'] = '0' * 64
            return observations

        with patch.object(carried, 'CHAIN_OBSERVERS', dict(carried.CHAIN_OBSERVERS,
                          **{reading.MATH_PROTOCOL: observe_changed_span})):
            with self.assertRaises(ResearchError):
                self.verify_selection(inputs, selection)


class MathCarriedRefusalTests(MathCarriedCase):
    """Only the two refusals that state that a prefix is not ready send the selection of a math chain to the turn
    before (0.91.9, with the math observers of 0.92.0). A stand-in observer under the key of the math protocol gives
    the refusal for the second turn and observes the first."""

    def refuse_second_turn(self, code):
        observe = carried.CHAIN_OBSERVERS[reading.MATH_PROTOCOL]

        def refuse(records, artifacts, assignment):
            if assignment['id'] == 'math-2':
                raise ResearchError(code, STAND_IN_REFUSAL_MESSAGE)
            return observe(records, artifacts, assignment)
        return patch.object(carried, 'CHAIN_OBSERVERS', dict(carried.CHAIN_OBSERVERS, **{reading.MATH_PROTOCOL: refuse}))

    def build_two_turns(self):
        native = self.build_math_packet()
        first = self.run_turn(native)
        self.run_turn(native, 'math-2', 'math-1')
        inputs = self.build_selection_inputs(native, first)
        selection = self.build_selection(inputs)
        self.assertEqual([row['origin']['assignment_id'] for row in selection['turns']], ['math-1', 'math-2'])
        return inputs, selection

    def test_math_selection_ends_with_a_refusal_that_is_no_state_and_goes_back_for_the_two(self):
        inputs, _ = self.build_two_turns()
        with self.refuse_second_turn(NO_STATE_REFUSAL_CODE), self.assertRaises(ResearchError) as refused:
            self.build_selection(inputs)
        self.assertEqual((refused.exception.code, refused.exception.message),
                         (NO_STATE_REFUSAL_CODE, STAND_IN_REFUSAL_MESSAGE))
        for code in carried.PREFIX_STATE_CODES:
            with self.subTest(code=code), self.refuse_second_turn(code):
                self.assertEqual([row['origin']['assignment_id'] for row in self.build_selection(inputs)['turns']],
                                 ['math-1'])

    def test_math_verification_passes_a_refusal_that_is_no_state_through(self):
        inputs, selection = self.build_two_turns()
        with self.refuse_second_turn(NO_STATE_REFUSAL_CODE), self.assertRaises(ResearchError) as refused:
            self.verify_selection(inputs, selection)
        self.assertEqual(refused.exception.code, NO_STATE_REFUSAL_CODE)
        for code in carried.PREFIX_STATE_CODES + ('artifact_missing',):
            with self.subTest(code=code), self.refuse_second_turn(code), self.assertRaises(ResearchError) as refused:
                self.verify_selection(inputs, selection)
            self.assertEqual((refused.exception.code, refused.exception.message),
                             ('openness_reading_incomplete', v7.SELECTION_MISMATCH_MESSAGE))


class MathPassageDependencyTests(MathCarriedCase):
    text_sizes = (80000, 80000, 80000)

    def test_g8_passage_dependencies_begin_when_the_successor_receives_them(self):
        native = self.build_math_packet()
        first = self.run_turn(native, passages=[dict(id='p1', version_id=self.versions[2],
                                                   anchor='Paper 3 opening line.', characters=80)])
        self.run_turn(native, 'math-2', 'math-1')
        records, inventory, identity = self.build_selection_inputs(native, first)
        changed = next(row for row in inventory['units'] if row['identity'].get('version_id') == self.versions[2])
        self.change_unit(inventory, changed)
        selection = self.build_selection((records, inventory, identity))
        self.assertTrue(selection['turns'])
        # The requesting turn has not received the answer yet; the successor has.
        self.assertEqual(selection['turns'][0]['notes']['classification'], 'current')
        observations = carried.observe_math_chain(records, self.artifacts,
                                                  records['review_assignment']['math-2'])
        classes = carried.classify_carried_notes(inventory, observations)
        self.assertEqual([row['classification'] for row in classes], ['current', 'affected'])
        self.assertEqual(observations[0]['passages'][0]['status'], 'delivered')
        self.assertFalse(any(row['identity'] == changed['identity']
                             for turn in observations for row in turn['deliveries']))
        self.assertNotIn(dict(kind='passage', index=0), selection['turns'][0]['entries'])
        self.verify_selection((records, inventory, identity), selection)

    def test_g8_unready_middle_and_cutoff_keep_only_the_ready_prefix(self):
        native = self.build_math_packet()
        first = self.run_turn(native)
        self.run_turn(native, 'math-2', 'math-1')
        inputs = self.build_selection_inputs(native, first)
        selection = self.build_selection(inputs)
        self.assertEqual(len(selection['turns']), 2)
        for cutoff in (first['recorded_revision'], inputs[0]['review_attempt']['attempt-math-1']['recorded_revision']):
            with self.subTest(cutoff=cutoff):
                identity = dict(inputs[2], recorded_revision=cutoff)
                self.assertEqual(self.build_selection((inputs[0], inputs[1], identity))['turns'], [])
        records = copy.deepcopy(inputs[0])
        second = records['review_assignment']['math-2']
        identity = dict(inputs[2], recorded_revision=second['recorded_revision'])
        self.assertEqual([row['origin']['assignment_id'] for row in self.build_selection(
            (records, inputs[1], identity))['turns']], ['math-1'])
        records.setdefault('review_context_event', {})['contamination'] = dict(
            id='contamination', assignment_id='math-2', kind='contamination', source='owner',
            recorded_revision=inputs[2]['recorded_revision'] + 1)
        self.assertEqual([row['origin']['assignment_id'] for row in self.build_selection(
            (records, inputs[1], inputs[2]))['turns']], ['math-1'])
        records['review_context_event']['contamination']['assignment_id'] = 'math-1'
        self.assertEqual(self.build_selection((records, inputs[1], inputs[2]))['turns'], [])
        with self.assertRaises(ResearchError):
            self.verify_selection((records, inputs[1], inputs[2]), selection)

    def test_registered_math_successor_protocol_qualifies_in_its_own_family(self):
        native = self.build_math_packet()
        first = self.run_turn(native)
        records, _, identity = self.build_selection_inputs(native, first)
        self.assertTrue(carried.list_qualifying_chains(records, self.artifacts, identity))
        self.assertIn(reading.MATH_PROTOCOL_V2, carried.CHAIN_OBSERVERS)
        # A later math protocol qualifies once its row is registered, as math v2 is.
        successor = 'exactory-openness-reading-math-v3'
        records = copy.deepcopy(records)
        packet = self.read_packet('math-1')
        reading._find_record(packet)['protocol'] = successor
        packet['reading_turn']['protocol'] = successor
        records['review_assignment']['math-1']['packet'] = self.artifacts.put(serialize_record(packet), 'application/json')
        self.assertEqual(carried.list_qualifying_chains(records, self.artifacts, identity), [])
        with patch.object(carried, 'CHAIN_OBSERVERS', dict(carried.CHAIN_OBSERVERS,
                          **{successor: carried.CHAIN_OBSERVERS[reading.MATH_PROTOCOL]})):
            self.assertEqual(carried.list_qualifying_chains(records, self.artifacts, identity),
                             [dict(chain='math-1', protocol=successor, recorded_revision=first['recorded_revision'])])


class MathPartialRangeTests(MathCarriedCase):
    text_sizes = (300000,)

    def test_g8_two_of_three_parts_credit_exact_ranges_and_report_remainder(self):
        native = self.build_math_packet()
        first = self.run_turn(native)
        self.run_turn(native, 'math-2', 'math-1')
        inputs = self.build_selection_inputs(native, first)
        selection = self.build_selection(inputs)
        self.assertEqual(len(selection['turns']), 2)
        unit = next(row for row in inputs[1]['units'] if row['kind'] == 'fulltext')
        ranges = [span for turn in selection['turns'] for span in turn['ranges'] if span['unit'] == unit['handle']]
        self.assertEqual([(row['part'], row['parts']) for row in ranges], [(1, 3), (2, 3)])
        for span in ranges:
            body = self.artifacts.read(unit['artifact']).decode('utf-8')[span['start']:span['end']]
            self.assertEqual(span['sha256'], hashlib.sha256(body.encode('utf-8')).hexdigest())
        report = carried.describe_carried_selection(inputs[0], self.artifacts, inputs[1], inputs[2], selection)
        missing = next(row for row in report['remaining'] if row['unit'] == unit['handle'])
        self.assertEqual(missing['ranges'], [dict(start=ranges[-1]['end'], end=unit['characters'],
                                                 characters=unit['characters'] - ranges[-1]['end'])])
        self.assertEqual(report['notes'], dict(current=2, affected=0))


class MathStoppedChainTests(MathCarriedCase):
    text_sizes = (80000, 80000, 80000)

    def test_g8_three_readings_survive_ruling_24_2_final_stop_and_keep_record_digests(self):
        native = self.build_math_packet()
        first = self.run_turn(native)
        self.run_turn(native, 'math-2', 'math-1')
        self.run_turn(native, 'math-3', 'math-2')
        final = self.assign('math-final', native, 'math-3')
        self.assertEqual(reading.find_stage(self.read_packet('math-final')), 'final')
        # The final's actual serialized input cannot fit this capacity. The stop is
        # produced by the same admission path used by the reviewer command.
        self.limit_input(1000)
        with self.assertRaises(ResearchError) as caught:
            self.invoke('math-final', self.build_native_result(self.read_packet('math-final')))
        self.assertEqual(caught.exception.code, 'review_chain_projection_over_limit')
        self.limit_input(1300000)
        inputs = self.build_selection_inputs(native, first)
        before = {kind: {key: digest(row) for key, row in group.items()} for kind, group in inputs[0].items()}
        selection = self.build_selection(inputs)
        self.assertEqual(len(selection['turns']), 3)
        projections = self.verify_selection(inputs, selection)
        observations = carried.observe_math_chain(inputs[0], self.artifacts,
                                                  inputs[0]['review_assignment']['math-3'])
        self.assertEqual(len(observations), 3)
        final_records = copy.deepcopy(inputs[0])
        reduced = json.loads(self.artifacts.read(native))
        reduced['scientific_context']['openness_minimum']['reading_digests'] = [
            digest(row) for row in final_records['reading'].values() if row['version_id'] != self.versions[2]]
        final_records['review_assignment'][final['id']]['context']['native_packet'] = self.artifacts.put(
            serialize_record(reduced), 'application/json')
        self.assertEqual(carried.observe_math_chain(final_records, self.artifacts,
            final_records['review_assignment'][final['id']]), observations)
        units = {row['handle']: row for row in inputs[1]['units']}
        complete = set()
        for identifier in ('math-1', 'math-2', 'math-3'):
            _, sent = self.read_sent_packet(identifier)
            complete.update(reading.review_excerpts.find_complete_bodies(sent))
        delivered = {(digest(row['identity']), row['sha256']) for row in units.values()
                     if row['sha256'] in complete}
        credited = {(digest(units[span['unit']]['identity']), units[span['unit']]['sha256'])
                    for turn in selection['turns'] for span in turn['ranges']}
        searches = {sha for turn in selection['turns'] for sha in turn['searches']}
        credited.update((digest(row['identity']), row['sha256']) for row in units.values()
                        if row['sha256'] in searches)
        self.assertEqual(credited, delivered)
        self.assertEqual(sum(len(row['entries']) for row in projections), 3)
        self.assertEqual([entry['text'] for row in projections for entry in row['entries']],
                         [entry['text'] for row in observations for entry in row['excerpts']])
        self.assertTrue(searches)
        self.assertFalse(any(row['identity'].get('record_kind') == 'problem_statement' for row in units.values()))
        blocked = copy.deepcopy(inputs[0])
        blocked.setdefault('review_context_event', {})['contamination'] = dict(
            id='contamination', assignment_id='math-2', kind='contamination', source='owner',
            recorded_revision=inputs[2]['recorded_revision'] + 1)
        self.assertEqual([row['origin']['assignment_id'] for row in self.build_selection(
            (blocked, inputs[1], inputs[2]))['turns']], ['math-1'])
        with self.assertRaises(ResearchError):
            self.verify_selection((blocked, inputs[1], inputs[2]), selection)
        self.assertEqual(before, {kind: {key: digest(row) for key, row in group.items()}
                                  for kind, group in self.store.snapshot()['records'].items()})


class MathSearchPartsTests(MathCarriedCase):
    text_sizes = (1000,)

    def record_searches(self):
        return [minimum_fixtures.ProblemOpennessMinimumTests.search(self, self.versions, query='term ' * 15000)]

    def test_g8_search_credit_waits_for_every_original_record_part(self):
        native = self.build_math_packet()
        first = self.run_turn(native)
        inputs = self.build_selection_inputs(native, first)
        self.assertTrue(self.build_selection(inputs)['turns'])
        search = next(row for row in inputs[1]['units'] if row['identity'].get('record_kind') == 'literature_search')
        self.assertEqual(len(reading.review_excerpts.compute_part_spans(
            self.artifacts.read(search['artifact']).decode('utf-8'))), 3)
        prior, saw_partial, completions = 'math-1', False, []
        for number in range(1, 8):
            records = self.store.snapshot()['records']
            observed = carried.observe_math_chain(records, self.artifacts, records['review_assignment'][prior])
            parts = [row for turn in observed for row in turn['deliveries'] if row['identity'] == search['identity']]
            inputs = self.build_selection_inputs(native, first)
            selection = self.build_selection(inputs)
            searches = [sha for turn in selection['turns'] for sha in turn['searches']]
            if len(parts) < 3:
                self.assertEqual(searches, [])
                saw_partial = saw_partial or bool(parts)
            else:
                self.assertEqual(searches, [search['sha256']])
                completions = [sha for turn in observed for sha in turn['searches']]
                self.verify_selection(inputs, selection)
                break
            identifier = 'math-%d' % (number + 1)
            self.run_turn(native, identifier, prior)
            prior = identifier
        self.assertTrue(saw_partial)
        self.assertEqual(completions, [search['sha256']])


class MathReadScopeTests(MathCarriedCase):
    """V19 and R6 to R9 of the carried read scope for packet_kind math: the selection reads an artifact of an earlier
    assignment only when its result can depend on it, and a math chain has the math prefix in step 3 and a math
    openness packet in step 4."""

    def assign_math(self, identifier, *, reviewer_id='openness-reviewer', route_id='route', bindings=None):
        """Record the first turn of a math chain over an export of this record for the reviewer and the bindings."""
        from test_openness_carried_integration import export_bindings
        bindings = bindings or [dict(formulation=self.selected['statement'], search_id=self.search['id'])]
        native = self.build_math_packet(exported=export_bindings(self, identifier, bindings, reviewer_id))
        return self.mutate(protocol.record_assignment, dict(id=identifier, route_id=route_id, dossier_id=None,
            role='native_math', reviewer_id=reviewer_id, author_id='problem-author',
            context=dict(native_packet=native)))['result']

    def start_math_chain(self):
        native = self.build_math_packet()
        first = self.run_turn(native)
        return (first,) + self.build_selection_inputs(native, first)

    def describe_selection(self, records, inventory, identity):
        selection = self.build_selection((records, inventory, identity))
        return carried.describe_carried_selection(records, self.artifacts, inventory, identity, selection)

    def test_selection_reads_the_artifacts_that_its_result_depends_on(self):
        from codex_route_fixtures import record_codex_route
        from research_harness import review_excerpts
        from test_openness_carried_integration import bind_statement_searches
        native = self.build_math_packet()
        first = self.run_turn(native)
        self.assign_math('other-reviewer', reviewer_id='another-reviewer')
        record_codex_route(self, 'other-route', model=self.route_model, output_tokens=64000)
        self.assign_math('other-route', route_id='other-route')
        # One request holds every text, so this review stays a single turn.
        with patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 2 * sum(self.text_sizes)):
            self.assign('single', self.build_math_packet('single-export', 'single'))
        self.run_turn(self.export(name='problem-export', assignment_id='problem'), 'problem')
        self.assign_math('other-statement', bindings=bind_statement_searches(self, 'other-statement', ['Neighbour 0.']))
        records, inventory, identity = self.build_selection_inputs(native, first)
        assignments = records['review_assignment']
        self.assertNotIn('reading_chain', assignments['single'])
        with patch.object(self.artifacts, 'read', wraps=self.artifacts.read) as read:
            with patch.object(carried, 'load_packet', wraps=carried.load_packet) as loaded:
                turns = carried.select_carried_readings(records, self.artifacts, inventory, identity,
                                                        protocol=carried.SELECTION_PROTOCOL)
        paths = {call.args[0]['path'] for call in read.call_args_list}
        names = ('other-reviewer', 'other-route', 'single', 'problem', 'other-statement', 'math-1')
        self.assertEqual({identifier: [name for name, reference in (
                              ('packet', assignments[identifier]['packet']),
                              ('native_packet', assignments[identifier]['context']['native_packet']))
                              if reference['path'] in paths]
                          for identifier in names},
                         {'other-reviewer': [], 'other-route': [], 'single': [], 'problem': ['packet', 'native_packet'],
                          'other-statement': ['native_packet'], 'math-1': ['packet', 'native_packet']})
        # The problem chain ends at the math prefix of step 3, so only the math chain loads its envelope.
        self.assertEqual({call.args[1]['path'] for call in loaded.call_args_list},
                         {first['context']['native_packet']['path']})
        self.assertIn(records['review_attempt']['attempt-math-1']['output']['path'], paths)
        self.assertEqual([row['origin']['assignment_id'] for row in turns], ['math-1'])
        # The commands record no math chain over a problem openness packet, so this record is built in memory: the
        # packet of math-1 over the native packet of the problem chain passes steps 1 to 3 and fails the kind in step 4.
        swapped = copy.deepcopy(records)
        problem_native = assignments['problem']['context']['native_packet']
        swapped['review_assignment']['math-1']['context']['native_packet'] = problem_native
        with patch.object(carried, 'load_packet', wraps=carried.load_packet) as loaded:
            self.assertIsNone(carried._describe_chain(swapped, self.artifacts, swapped['review_assignment']['math-1'],
                                                      identity))
        self.assertEqual([call.args[1] for call in loaded.call_args_list], [problem_native])

    def test_lost_packet_of_a_chain_under_another_reviewer_does_not_refuse_the_selection(self):
        from test_openness_carried_integration import remove_artifact
        first, records, inventory, identity = self.start_math_chain()
        other = dict(identity, reviewer_id='another-reviewer')
        with remove_artifact(self, first['packet']):
            self.assertEqual(carried.select_carried_readings(records, self.artifacts, inventory, other,
                                                             protocol=carried.SELECTION_PROTOCOL), [])
            report = self.describe_selection(records, inventory, other)
            self.assertEqual((report['chains'], report['passed_over'], report['outputs']), ([], [], []))
            self.assertEqual(report['unavailable_chains'], [dict(chain='math-1', code='artifact_missing',
                details=dict(path=first['packet']['path']), message=v7.UNAVAILABLE_CHAIN_TEXT.format(chain='math-1'))])
        report = self.describe_selection(records, inventory, other)
        self.assertEqual(report['passed_over'], [dict(chain='math-1', protocol=reading.MATH_PROTOCOL,
            recorded_revision=first['recorded_revision'], reason='reviewer_id')])
        self.assertNotIn('unavailable_chains', report)

    def test_lost_artifact_of_a_chain_on_another_route_identity_does_not_refuse_the_selection(self):
        from codex_route_fixtures import record_codex_route
        from test_openness_carried_integration import remove_artifact
        first, records, inventory, identity = self.start_math_chain()
        record_codex_route(self, 'other-route', model=self.route_model, output_tokens=64000)
        other = dict(identity, route=self.store.snapshot()['records']['review_route']['other-route'])
        for name, reference in (('packet', first['packet']), ('native_packet', first['context']['native_packet'])):
            with self.subTest(artifact=name), remove_artifact(self, reference):
                report = self.describe_selection(records, inventory, other)
                self.assertEqual((report['chains'], report['outputs']), ([], []))
                self.assertNotIn('unavailable_chains', report)
                self.assertEqual(carried.list_passed_over_chains(records, self.artifacts,
                                                                 dict(other, reviewer_id='another-reviewer')),
                                 dict(passed_over=[], unavailable_chains=[]))

    def test_lost_artifact_of_a_chain_on_another_statement_does_not_refuse_the_selection(self):
        from test_openness_carried_integration import remove_artifact
        first, records, inventory, identity = self.start_math_chain()
        other = dict(identity, formulations=dict(statement='Another statement.', neighbours=['Neighbour 0.']))
        held = {row['artifact']['path'] for row in inventory['units'] + inventory['material']}
        evidence = next(row['artifact'] for row in
                        json.loads(self.artifacts.read(first['context']['native_packet']))['evidence']
                        if row['artifact']['path'] not in held)
        for name, reference in (('packet', first['packet']), ('evidence', evidence)):
            with self.subTest(artifact=name), remove_artifact(self, reference):
                report = self.describe_selection(records, inventory, other)
                self.assertEqual((report['chains'], report['outputs']), ([], []))
                self.assertNotIn('unavailable_chains', report)
                # The chain of the statement reads the same artifact, and the store refuses it.
                with self.assertRaises(ResearchError) as caught:
                    carried.select_carried_readings(records, self.artifacts, inventory, identity,
                                                    protocol=carried.SELECTION_PROTOCOL)
                self.assertEqual(caught.exception.code, 'artifact_missing')

    def test_replacement_of_a_first_turn_has_no_row_of_its_own(self):
        first, records, inventory, identity = self.start_math_chain()
        expected = self.build_selection((records, inventory, identity))
        self.assertEqual([row['origin']['chain'] for row in expected['turns']], ['math-1'])
        # A math v1 chain reruns a refused first turn and stops a first turn over the output limit, so the commands
        # record no replacement of a first turn here. Each replacement is built in memory in the form that a v7
        # chain records: it names math-1 as the first assignment of its reading chain.
        for replacement in (dict(capacity_replacement_of='math-1'),
                            dict(context=dict(first['context'], output_refusal=dict(assignment_id='math-1')))):
            with self.subTest(replacement=sorted(replacement)):
                changed = copy.deepcopy(records)
                member = dict(copy.deepcopy(first), id='replacement',
                              recorded_revision=identity['recorded_revision'] - 1,
                              reading_chain=dict(first_assignment_id='math-1', turn=1), **replacement)
                changed['review_assignment']['replacement'] = member
                self.assertIsNone(carried._describe_chain(changed, self.artifacts, member, identity))
                self.assertEqual(carried.list_qualifying_chains(changed, self.artifacts, identity),
                                 [dict(chain='math-1', protocol=reading.MATH_PROTOCOL,
                                       recorded_revision=first['recorded_revision'])])
                passed = carried.list_passed_over_chains(changed, self.artifacts,
                                                         dict(identity, reviewer_id='another-reviewer'))
                self.assertEqual([row['chain'] for row in passed['passed_over']], ['math-1'])
                report = self.describe_selection(changed, inventory, identity)
                self.assertEqual([row['chain'] for row in report['outputs']], ['math-1'])
