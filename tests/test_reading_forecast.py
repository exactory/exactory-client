"""Reading-chain admission bounds and advisory forecasts preserve scientific inputs."""

import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from research_harness import openness_reading as reading, review_protocol as protocol
from research_harness import review_route_capacity as capacity
from research_harness.errors import ResearchError
from test_openness_final_preflight import FinalPreflightCase


class ReadingProjectionTests(FinalPreflightCase):
    reading_protocol = None
    input_limit = 1300000

    def build_projection_inputs(self):
        self.limit_input(self.input_limit)
        native, _ = self.build_native()
        records, artifacts, value, route, packet = self.build_inputs(native)
        route = dict(route, adapter='claude_cli_v1', model='claude-opus-5-5', endpoint='claude://local',
                     configuration=dict(max_output_tokens=128000, timeout_seconds=60, effort='xhigh'))
        rows = reading.list_reading_rows(records, artifacts, packet['native'])
        plan = reading._plan_search_delivery(artifacts, packet['native'])
        for content in plan['bodies'].values():
            artifacts.put(content, 'application/json')
        record = reading._create_record(reading.PROTOCOL_V5, plan['manifest'],
                                        excerpt_protocol=reading.review_excerpts.SESSION_PROTOCOL)
        return records, artifacts, value, route, packet, rows, record

    def test_unbounded_protocols_name_every_missing_bound_and_admission_stop(self):
        records, artifacts, value, route, packet, rows, record = self.build_projection_inputs()
        for rules in (reading.PROTOCOL_V5, reading.MATH_PROTOCOL):
            with self.subTest(protocol=rules):
                record['protocol'] = rules
                projected = reading.project_reading_chain(route, packet, rows, record, artifacts=artifacts)
                self.assertFalse(projected['finite'])
                self.assertEqual(set(projected['unbounded_kinds']), {
                    'unmatched_excerpt', 'over_allowance_excerpt', 'delivered_passage', 'undelivered_passage'})
                stop = reading.admit_reading_chain(route, projected, records=records,
                                                   artifacts=artifacts, assignment=value)
                self.assertEqual(stop['code'], 'review_chain_projection_over_limit')
                self.assertEqual(stop['stop_class'], 'decision')
                self.assertEqual(stop['details']['reason'], 'no_enforceable_bound')
                self.assertIn('owner_decides', stop['details']['decision'])
                self.assertEqual(len(stop['details']['decision']['then']), 2)

    def test_v6_record_allowance_is_additive_and_below_floor_is_a_decision(self):
        records, artifacts, value, route, packet, rows, record = self.build_projection_inputs()
        record['protocol'] = 'exactory-openness-reading-v6'
        before = copy.deepcopy((packet, rows, record))
        projected = reading.project_reading_chain(route, packet, rows, record, artifacts=artifacts,
                                                   record_total=reading.RECORD_ALLOWANCE_FLOOR)
        self.assertTrue(projected['finite'])
        self.assertEqual(projected['characters'], max(projected['fixed_parts'].values())
                         + reading.RECORD_ALLOWANCE_FLOOR)
        self.assertEqual((packet, rows, record), before)
        self.assertGreater(projected['planned_reading_turns'], 0)
        admitted = reading.admit_reading_chain(route, projected)
        self.assertEqual(admitted['state'], 'record')
        projected['record_total'] = reading.RECORD_ALLOWANCE_FLOOR - 1
        stop = reading.admit_reading_chain(route, projected)
        self.assertEqual(stop['details']['reason'], 'below_floor')
        unallocated = copy.deepcopy(record)
        unallocated['allowances']['record'] = dict(characters=None, used=0, not_retained={})
        allocated = reading.project_reading_chain(route, packet, rows, unallocated, artifacts=artifacts)
        self.assertGreaterEqual(allocated['record_total'], reading.RECORD_ALLOWANCE_FLOOR)

    def test_serialized_retained_entries_count_escapes_and_uncharged_statuses(self):
        _, _, _, route, _, _, record = self.build_projection_inputs()
        record['excerpts'] = [dict(status='unmatched', text='"\\\n' * 40, purpose='x' * 500,
                                   version_id='v' * 2000), dict(status='over_allowance', version_id='v')]
        record['passages'] = [dict(status='not_found', request_id='p' * 2000)]
        counts = reading.measure_retained_entries(route, record)
        self.assertEqual(counts['unmatched_excerpt']['count'], 1)
        self.assertGreater(counts['unmatched_excerpt']['characters'], 2500)
        self.assertEqual(counts['over_allowance_excerpt']['count'], 1)
        self.assertEqual(counts['undelivered_passage']['count'], 1)

    def test_stored_retention_without_a_purpose_cap_reports_that_missing_bound(self):
        records, artifacts, value, route, packet, rows, record = self.build_projection_inputs()
        record.pop('excerpt_protocol')
        projected = reading.project_reading_chain(route, packet, rows, record, artifacts=artifacts)
        self.assertIn('verified_excerpt', projected['unbounded_kinds'])
        self.assertIsNone(projected['entry_bounds']['verified_excerpt']['serialized_characters'])

    def test_projection_reserves_original_sources_and_optional_identity_variants(self):
        from research_harness.bar_sources import descriptor
        records, artifacts, value, route, packet, rows, record = self.build_projection_inputs()
        expected = []
        for row in rows:
            identity = reading._describe_identity(row)
            reading._add_source(expected, descriptor(row['artifact']), identity)
            if 'text_sha256' in identity:
                reading._add_source(expected, descriptor(row['artifact']),
                                    {key: value for key, value in identity.items() if key != 'text_sha256'})
        record['protocol'] = reading.PROTOCOL_V6
        projected = reading.project_reading_chain(route, packet, rows, record, artifacts=artifacts, record_total=300000)
        self.assertGreaterEqual(projected['fixed_record_parts']['sources'],
                                reading.measure_record_entries(route, expected))

    def test_check_reports_nonfinite_projection_without_recording_an_assignment(self):
        from research_harness.check_mode import check_mutation
        records, artifacts, value, route, packet, rows, record = self.build_projection_inputs()
        before = self.store.snapshot()
        checked = check_mutation(self.store, protocol.record_assignment, value,
            expected_revision=self.store.revision, request_id='check-projection', bounded_payload=value)
        self.assertFalse(checked['reading_chain_admission']['projection']['finite'])
        self.assertEqual(checked['reading_chain_admission']['details']['reason'], 'no_enforceable_bound')
        self.assertEqual(self.store.snapshot(), before)

    def test_smaller_packet_answer_checks_before_recording_and_preserves_the_author_boundary(self):
        records, artifacts, value, route, packet, rows, record = self.build_projection_inputs()
        decision = reading.describe_projection_decision(reading.PROTOCOL_V5, records=records,
                                                        artifacts=artifacts, assignment=value)
        export = decision['then'][1]['steps'][0]
        self.assertEqual([step.get('check', False) for step in export['then']], [True, False])
        for step in export['then']:
            self.assertEqual(step['payload']['role'], value['role'])
            self.assertEqual(step['payload']['author_id'], value['author_id'])


class MathForecastTests(FinalPreflightCase):
    reading_protocol = None
    text_sizes = (125000, 125000, 125000)
    input_limit = 260000

    def export_math(self):
        native = json.loads(self.artifacts.read(self.export()))
        native['contract'].pop('orchestration')
        native['scientific_context'].pop('openness_delivery', None)
        records = self.store.snapshot()['records']
        for search in records['literature_search'].values():
            for response in search['responses']:
                reference = records['source'][response['source_id']]['response']
                if not any(row['digest'] == reference['sha256'] for row in native['evidence']):
                    native['evidence'].append(dict(kind='artifact', artifact=reference, digest=reference['sha256']))
        return self.pin_packet(native)

    def build_diagnostics(self, number, count=20):
        return [dict(version_id='unknown-' + str(number) + '-' + str(index) + 'v' * 2000,
                     text='Unmatched text %d %d. ' % (number, index) + 'x' * 200,
                     purpose='p' * 500) for index in range(count)]

    def test_first_and_middle_turn_report_uncharged_diagnostics_and_chain_token_ratio(self):
        self.limit_input(self.input_limit)
        native = self.export_math()
        first = self.assign('turn-1', native)
        self.assertEqual(first['capacity_forecast']['final_estimate']['status'], 'unavailable')
        self.assertEqual(reading.find_protocol(self.read_packet('turn-1')), reading.MATH_PROTOCOL)
        self.invoke('turn-1', self.build_reading_response(self.read_packet('turn-1'),
                    excerpts=self.build_diagnostics(1, 2)))
        second = self.assign('turn-2', native, 'turn-1')
        self.assertEqual(second['capacity_forecast']['retained_entries']['unmatched_excerpt']['count'], 2)
        self.assertGreater(second['capacity_forecast']['retained_entries']['unmatched_excerpt']['characters'], 4000)
        self.assertEqual(second['capacity_forecast']['remaining_allowances']['excerpts'], 200000)
        self.assertIsNotNone(second['capacity_warning']['input']['request']['chain_tokens_per_character'])
        self.invoke('turn-2', self.build_reading_response(self.read_packet('turn-2'), excerpts=[]))
        third = self.assign('turn-3', native, 'turn-2')
        self.assertTrue(third['capacity_forecast']['final_estimate']['estimate'])
        self.assertEqual(third['capacity_forecast']['final_estimate']['status'], 'available')

    def test_estimate_stops_before_call_and_owner_continue_reaches_fitting_final(self):
        self.limit_input(self.input_limit)
        native = self.export_math()
        for number in (1, 2):
            identifier = 'turn-%d' % number
            self.assign(identifier, native, 'turn-1' if number == 2 else None)
            self.invoke(identifier, self.build_reading_response(self.read_packet(identifier),
                        excerpts=self.build_diagnostics(number)))
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            self.assign('turn-3', native, 'turn-2')
        stop = caught.exception
        self.assertEqual(stop.code, reading.PROJECTION_STOP)
        self.assertTrue(stop.details['estimate'])
        self.assertEqual(self.store.snapshot(), before)
        decision = stop.details['decision']
        self.assertEqual(len(decision['then']), 3)
        step = decision['then'][0]['steps'][0]
        self.assertEqual((step['id'], step['payload']['assignment_id']), (9, 'turn-2'))
        evidence = self.artifacts.put(b'Continue the measured chain.', 'text/plain')
        self.mutate(protocol.record_context_event, dict(step['payload'], id='continue-chain',
            evidence=[evidence]))
        prior = 'turn-2'
        for number in range(3, 15):
            identifier = 'turn-%d' % number
            assignment = self.assign(identifier, native, prior)
            self.assertLessEqual(assignment['capacity_warning']['input']['request']['characters'], self.input_limit)
            packet = self.read_packet(identifier)
            if reading.find_stage(packet) == 'final':
                break
            self.invoke(identifier, self.build_reading_response(packet, excerpts=[]))
            prior = identifier
        else:
            self.fail('The continued math chain did not reach its final')

    def test_exact_oversize_math_turn_stops_before_any_invocation(self):
        self.limit_input(self.input_limit)
        native = self.export_math()
        self.assign('turn-1', native)
        self.invoke('turn-1', self.build_reading_response(self.read_packet('turn-1'),
                    excerpts=self.build_diagnostics(1, 110)))
        with self.assertRaises(ResearchError) as caught:
            self.assign('turn-2', native, 'turn-1')
        self.assertEqual(caught.exception.code, reading.PROJECTION_STOP)
        self.assertEqual(caught.exception.details['reason'], 'no_enforceable_bound')
        self.assertIn('reading-math-v2', json.dumps(caught.exception.details['decision']))
        self.assertEqual(len(self.store.snapshot()['records']['review_attempt']), 1)

    def test_math_with_no_reading_units_and_an_oversize_fixed_packet_stops_before_a_call(self):
        self.limit_input(self.input_limit)
        native = json.loads(self.artifacts.read(self.export_math()))
        native['scientific_context']['openness_minimum'].update(searches=[], reading_digests=[])
        reference = self.pin_packet(native)
        with self.assertRaises(ResearchError) as caught:
            self.assign('turn-1', reference)
        self.assertEqual(caught.exception.code, reading.PROJECTION_STOP)
        self.assertEqual(caught.exception.details['reason'], 'no_enforceable_bound')
        self.assertEqual(len(self.store.snapshot()['records'].get('review_attempt', {})), 0)

    def test_owner_continue_on_an_existing_math_head_authorizes_that_head(self):
        self.limit_input(self.input_limit)
        native = self.export_math()
        for number in (1, 2):
            identifier = 'turn-%d' % number
            self.assign(identifier, native, 'turn-1' if number == 2 else None)
            self.invoke(identifier, self.build_reading_response(self.read_packet(identifier),
                        excerpts=self.build_diagnostics(number)))
        describe = reading.describe_reading_receipt
        def describe_without_admission(*args, **kwargs):
            return describe(*args, **dict(kwargs, admit_turn=False))
        with patch.object(reading, 'describe_reading_receipt', side_effect=describe_without_admission):
            self.assign('turn-3', native, 'turn-2')
        output = self.build_reading_response(self.read_packet('turn-3'), excerpts=[])
        with self.assertRaises(ResearchError) as caught:
            self.invoke('turn-3', output)
        step = caught.exception.details['decision']['then'][0]['steps'][0]
        self.assertEqual(step['payload']['assignment_id'], 'turn-3')
        evidence = self.artifacts.put(b'Continue this existing head under the exact character check.', 'text/plain')
        self.mutate(protocol.record_context_event, dict(step['payload'], id='continue-existing-head', evidence=[evidence]))
        self.invoke('turn-3', output)
        state = protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, 'turn-3')
        self.assertTrue(state['ready'], state['obligations'])


class AliasedDeliveryProjectionTests(FinalPreflightCase):
    reading_protocol = None
    input_limit = 1300000
    text_sizes = (40000, 40000, 40000)

    def build_projection_inputs(self):
        return ReadingProjectionTests.build_projection_inputs(self)

    def build_text(self, number):
        from openness_reading_fixtures import build_sized_body
        return build_sized_body(1 if number == 1 else 2, 40000)

    def test_a_shorter_reading_prefix_with_an_aliased_body_stays_below_the_reading_projection(self):
        records, artifacts, value, route, packet, rows, record = self.build_projection_inputs()
        record['protocol'] = reading.PROTOCOL_V6
        delivery_rows = reading.list_delivery_rows(rows, record['search_delivery'])
        units = reading.list_reading_units(artifacts, delivery_rows)
        selected = {row['artifact']['sha256'] for row in rows if row['kind'] == 'fulltext'}
        projected = reading.project_reading_chain(route, packet, rows, record, artifacts=artifacts,
                                                   record_total=0, eligible_texts=selected)
        fixed = reading._build_projection_record(artifacts, delivery_rows, record, units,
                                                 reading.transport.local_input_limit(route), source_rows=rows)
        prefix = reading._assemble_reading_turn(artifacts, packet, fixed, [], units[:2], units[2:], len(units) + 1)
        prompt = protocol.canonical_prompt('native_math', reading_stage='reading',
                                           reading_protocol=reading.PROTOCOL_V5)['text']
        measured = capacity.measure_request(route, reading._render(route, prompt, prefix))
        self.assertLessEqual(measured['characters'], projected['fixed_parts']['reading'])


class AdversarialProjectionTests(FinalPreflightCase):
    reading_protocol = None
    text_sizes = (8000,) * 29
    input_limit = 1300000

    def build_projection_inputs(self):
        return ReadingProjectionTests.build_projection_inputs(self)

    def record_searches(self):
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        return [ProblemOpennessMinimumTests.search(self, self.versions[(index % 3) * 10:(index % 3 + 1) * 10])
                for index in range(20)]

    def test_adversarial_shapes_fit_the_projected_total_in_reading_and_final_builders(self):
        from research_harness import review_transport as transport, review_excerpts
        from research_harness.bar_sources import descriptor
        records, artifacts, value, route, packet, rows, empty = self.build_projection_inputs()
        self.assertEqual(len(empty['search_delivery']['searches']), 20)
        self.assertEqual(len([row for row in rows if row['kind'] == 'fulltext']), 29)
        delivery_rows = reading.list_delivery_rows(rows, empty['search_delivery'])
        units = reading.list_reading_units(artifacts, delivery_rows)
        spans = reading._compute_spans(artifacts, delivery_rows)
        observations = []
        reserved = 'identifier-' + 'x' * (reading.RESERVED_IDENTIFIER_CHARACTERS - 11)
        self.assertGreaterEqual(len(units), 57)
        cursor = 0
        for index in range(57):
            count = 2 if len(units) - cursor > 57 - index else 1
            group = units[cursor:cursor + count]
            cursor += count
            hashes = {unit['artifact_descriptor']['sha256'] for unit in group}
            whole = {review_excerpts.find_identity(row) for row in delivery_rows
                     if row['artifact']['sha256'] in hashes}
            parts = {(review_excerpts.find_identity(unit), unit['part']) for unit in group if unit['parts'] > 1}
            turn = dict(assignment_id=reserved, attempt_id=reserved,
                        delivered_units=[reading._describe_identity(row) for row in delivery_rows
                                         if review_excerpts.find_identity(row) in whole], notes='Read the unit.')
            if parts:
                turn['delivered_parts'] = [{key: item for key, item in unit.items() if key != 'artifact_descriptor'}
                                           for unit in group if unit['parts'] > 1]
            observations.append(dict(assignment_id=reserved, turn=turn, reading=True, whole=whole, parts=parts,
                output=dict(passage_requests=[]), delivered_now=[dict(reading._describe_identity(unit),
                    artifact=reading._reference(unit['artifact_descriptor'])) for unit in group],
                bodies={unit['artifact_descriptor']['sha256']: artifacts.read(reading._reference(
                    unit['artifact_descriptor'])) for unit in group}))
        self.assertEqual(cursor, len(units))
        self.assertEqual(len(observations), 57)
        for shape in ('short-excerpts-long-purposes', 'uncharged-diagnostics', 'passage-turns',
                      'long-identifiers', 'incident-57-turns'):
            for purpose_size in ((500, 5000) if shape == 'short-excerpts-long-purposes' else (500,)):
                with self.subTest(shape=shape, purpose_size=purpose_size):
                    record = copy.deepcopy(empty)
                    record['turns'] = [dict(item['turn']) for item in observations]
                    record['search_receipts'] = reading._verify_search_coverage(
                        artifacts, empty['search_delivery'], delivery_rows, spans, observations)
                    record['allowances']['record'] = dict(characters=50000, used=0, not_retained={})
                    for row in rows:
                        reading._add_source(record['sources'], descriptor(row['artifact']), reading._describe_identity(row))
                    base_turns = copy.deepcopy(record['turns'])
                    index = 0
                    while True:
                        if shape == 'uncharged-diagnostics':
                            if index % 3 == 0:
                                entry = dict(status='unmatched', version_id='v' * 2000, text='"\\\n' * 60,
                                             purpose='p' * purpose_size, text_sha256=('%064x' % index))
                                key = 'excerpts'
                            elif index % 3 == 1:
                                entry = dict(status='over_allowance', version_id=self.versions[0],
                                             purpose='p' * purpose_size, characters=20)
                                key = 'excerpts'
                            else:
                                entry = dict(status='not_found', version_id='unknown-' + 'v' * 2000,
                                             request_id='missing-' + str(index) + 'p' * 2000,
                                             assignment_id=reserved)
                                key = 'passages'
                        elif shape == 'passage-turns':
                            entry = dict(status='delivered', request_id='p-' + str(index), text='x',
                                         assignment_id=reserved, version_id=self.versions[0], start=0, end=1,
                                         sha256='a' * 64)
                            key = 'passages'
                        else:
                            entry = dict(status='verified', version_id=self.versions[0], text='x' * 20,
                                         purpose='p' * min(purpose_size, 500), start=index, end=index + 20,
                                         sha256='a' * 64, text_sha256='b' * 64)
                            if purpose_size > 500:
                                entry.update(purpose_characters=purpose_size, purpose_sha256='c' * 64)
                            key = 'excerpts'
                        trial = copy.deepcopy(record)
                        trial[key].append(entry)
                        if shape == 'passage-turns':
                            trial['turns'].append(dict(assignment_id=reserved, attempt_id=reserved,
                                delivered_units=[], notes_withheld=reading.NOTES_WITHHELD_NOTE.format(reserved)))
                        cost = sum(reading.measure_record_entries(route, trial[name]) for name in ('excerpts', 'passages'))
                        cost += reading.measure_record_entries(route, trial['turns']) - reading.measure_record_entries(route, base_turns)
                        if cost > record['allowances']['record']['characters']:
                            break
                        record = trial
                        record['allowances']['record']['used'] = cost
                        index += 1
                    if shape == 'uncharged-diagnostics':
                        self.assertEqual({entry['status'] for entry in record['excerpts']}, {'unmatched', 'over_allowance'})
                        self.assertTrue(record['passages'])
                    if shape == 'passage-turns':
                        self.assertEqual(len(record['turns']) - len(base_turns), len(record['passages']))
                    v6 = dict(record, protocol=reading.PROTOCOL_V6)
                    projected = reading.project_reading_chain(route, packet, rows, v6, artifacts=artifacts,
                        record_total=record['allowances']['record']['characters'])
                    self.assertTrue({'shared_search_body', 'search_residual'} <= {
                        row['kind'] for row in projected['units']})
                    # UB6 fits optional bodies and notes within its first projection; the legacy builders here are
                    # driven with that ceiling, without changing one byte of their packet construction.
                    ceiling = projected['characters']
                    with patch.object(transport, 'local_input_limit', return_value=ceiling):
                        final = reading._build_final_turn(route, packet, record, rows,
                            reading._compute_spans(artifacts, rows), observations)
                        turn = reading._build_reading_turn(artifacts, route, packet, record, [], units, 1)
                    for built in (final, turn):
                        prompt = protocol.canonical_prompt('native_math', reading_stage=reading.find_stage(built),
                                                          reading_protocol=reading.PROTOCOL_V5)['text']
                        request = reading._render(route, prompt, built)
                        self.assertLessEqual(capacity.measure_request(route, request)['characters'], ceiling)
                    for rules in (reading.PROTOCOL_V5, reading.MATH_PROTOCOL):
                        from research_harness.check_mode import check_mutation
                        legacy = dict(record, protocol=rules)
                        result = reading.project_reading_chain(route, packet, rows, legacy, artifacts=artifacts)
                        self.assertFalse(result['finite'])
                        self.assertIn('undelivered_passage', result['unbounded_kinds'])
                        built = reading._assemble_reading_turn(artifacts, packet, legacy, [], units[:1], units[1:], 1)
                        # This unit supplies a filled retained-record shape; UB6 owns the answering/retention engine.
                        # The public check still builds its prompt, request, receipt and admission report normally.
                        chain = dict(first_assignment_id=value['id'], turn=1)
                        with patch.object(reading, 'prepare_packet', return_value=(built, chain)):
                            checked = check_mutation(self.store, protocol.record_assignment, value,
                                expected_revision=self.store.revision,
                                request_id='check-' + shape + '-' + str(purpose_size) + '-' + rules,
                                bounded_payload=value)
                        self.assertFalse(checked['reading_chain_admission']['projection']['finite'])
                        self.assertEqual(checked['reading_chain_admission']['details']['reason'], 'no_enforceable_bound')
        # A factored original keeps its fixed delivery marker. It is never converted to an ordinary body omission.
        factored = sum(entry['residual'] is not None for entry in empty['search_delivery']['searches'])
        marker = 'F' * 100000
        with patch.object(reading, 'SEARCH_DELIVERY_NOTE', marker):
            measured = reading.measure_final_input(route, packet, rows, empty)
            projected = reading.project_reading_chain(route, packet, rows, dict(empty, protocol=reading.PROTOCOL_V6),
                                                       artifacts=artifacts, record_total=50000)
        self.assertGreaterEqual(measured['parts']['base_packet'], factored * len(marker))
        self.assertGreaterEqual(projected['fixed_parts']['final'], factored * len(marker))


class LegacyReadingForecastTests(FinalPreflightCase):
    reading_protocol = None

    def load_released_builders(self):
        from pathlib import Path
        namespace = dict(__name__='research_harness.reading_0_87_0', __package__='research_harness')
        path = Path(__file__).with_name('fixtures') / 'openness_reading_0_87_0.py.txt'
        source = path.read_bytes()
        self.assertEqual(hashlib.sha256(source).hexdigest(), '1b33b0fa64a5be8b74bae463c254346aa6d744981273321c60a87a6ffb3964e1')
        exec(compile(source, str(path), 'exec'), namespace)
        return namespace

    def test_chain_admitted_by_0870_keeps_packets_prompts_and_request_digests(self):
        released = self.load_released_builders()
        self.limit_input(160000)
        native = self.export()
        with patch.object(reading, 'prepare_packet', side_effect=released['prepare_packet']):
            first = self.assign('turn-1', native)
        prior = 'turn-1'
        for number in range(2, 10):
            old = self.read_packet(prior)
            self.invoke(prior, self.build_reading_response(old))
            args = self.build_inputs(native, prior)
            expected, _ = released['prepare_packet'](*args)
            actual, _ = reading.prepare_packet(*args)
            self.assertEqual(actual, expected)
            prompt = protocol.canonical_prompt('native_math', reading_stage=reading.find_stage(actual),
                                               reading_protocol=reading.PROTOCOL_V5)
            expected_request = reading.transport.request_body(args[3], prompt['text'], expected)
            identifier = 'turn-%d' % number
            current = self.assign(identifier, native, prior)
            self.assertEqual(self.read_packet(identifier), expected)
            self.assertEqual(current['prompt'], prompt)
            request = protocol.build_assignment_request(self.store.snapshot()['records'], self.artifacts, current)
            self.assertEqual(protocol.digest(request), protocol.digest(expected_request))
            self.assertIn('capacity_forecast', current)
            if reading.find_stage(actual) == 'final':
                break
            prior = identifier
        else:
            self.fail('The released chain did not reach a final')

    def test_reserved_identifiers_and_thirty_failed_attempts_keep_only_the_observed_attempt_in_the_record(self):
        self.limit_input(500000)
        identifier = 'head-' + 'x' * (reading.RESERVED_IDENTIFIER_CHARACTERS - 5)
        attempt = 'observed-' + 'x' * (reading.RESERVED_IDENTIFIER_CHARACTERS - 9)
        native = self.export(assignment_id=identifier)
        self.assign(identifier, native)
        self.invoke(identifier, self.build_reading_response(self.read_packet(identifier)), attempt=attempt)
        for number in range(30):
            prefix = 'failed-%02d-' % number
            self.mutate(protocol.record_attempt, dict(id=prefix + 'x' * (
                reading.RESERVED_IDENTIFIER_CHARACTERS - len(prefix)), assignment_id=identifier,
                status='failed', output=None, usage=None, reason='Retained synthetic historical failure.'))
        assignment = self.assign('turn-2', native, identifier)
        record = reading._find_record(self.read_packet('turn-2'))
        self.assertEqual(len(record['turns']), 1)
        self.assertEqual(record['turns'][0]['attempt_id'], attempt)
        self.assertNotIn('failed-00-', json.dumps(record))
        self.assertFalse(assignment['reading_chain_admission']['projection']['finite'])


class SeriesReadingForecastTests(FinalPreflightCase):
    reading_protocol = None
    text_sizes = (49000,)

    def test_a_forecast_read_failure_preserves_the_original_series_stop(self):
        from research_harness import cli, review_excerpts
        from research_harness.review_series import run_series
        self.limit_input(500000)
        native = self.export()
        with patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 1000):
            assignment = self.assign('turn-1', native)
            def lose_packet(store, payload, **identity):
                (self.root / assignment['packet']['path']).unlink()
                raise ResearchError('artifact_missing', 'The synthetic packet is unavailable.')
            rows = []
            with patch.dict(cli.OPERATIONS, {'review-run': lose_packet}), patch.object(
                    reading.transport, 'send_request') as send:
                code = run_series(self.store, 'turn-1', emit=rows.append)
            self.assertEqual(code, 3)
            send.assert_not_called()
            self.assertEqual(rows[-1]['code'], 'artifact_missing')
            self.assertEqual(rows[-1]['capacity_forecast']['status'], 'unavailable')

    def test_v5_turn_50_cannot_fit_and_series_stops_before_a_call(self):
        from research_harness.review_series import run_series
        from research_harness import review_excerpts
        self.limit_input(500000)
        native = self.export()
        prior = None
        with patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 1000):
            for number in range(1, 50):
                identifier = 'turn-%d' % number
                self.assign(identifier, native, prior)
                packet = self.read_packet(identifier)
                self.assertEqual(reading.find_stage(packet), 'reading')
                excerpts = [] if number < 49 else [dict(version_id='missing-' + str(index) + 'v' * 2000,
                    text='Unmatched synthetic passage %d. ' % index + 'x' * 200, purpose='p' * 500)
                    for index in range(200)]
                self.invoke(identifier, self.build_reading_response(packet, excerpts=excerpts))
                prior = identifier
            rows = []
            with patch.object(reading.transport, 'send_request') as send:
                code = run_series(self.store, prior, emit=rows.append)
            self.assertEqual(code, 3)
            send.assert_not_called()
            terminal = rows[-1]
            self.assertEqual(terminal['code'], reading.PROJECTION_STOP)
            self.assertEqual(terminal['stop_class'], 'decision')
            self.assertIn('decision', terminal['details'])
            self.assertIn('input', terminal['capacity_warning'])
            self.assertTrue(all('capacity_forecast' in row for row in rows))
            self.assertEqual(len(self.store.snapshot()['records']['review_attempt']), 49)

    def test_input_over_limit_attempt_keeps_d2_code_and_uses_chain_owner_answers(self):
        from research_harness.review_continuation import plan_next
        from research_harness import review_excerpts
        self.limit_input(500000)
        native = self.export()
        with patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 1000):
            self.assign('turn-1', native)
            with patch.object(reading.transport, 'send_request', side_effect=ResearchError('review_input_too_large',
                              'The provider rejected this synthetic input.')):
                self.mutate(protocol.invoke_assignment, dict(id='failed-input', assignment_id='turn-1'), credential='fixture')
            plan = plan_next(self.store.snapshot()['records'], self.artifacts, 'turn-1')
        self.assertEqual((plan['state'], plan['code']), ('stop', 'review_input_too_large'))
        self.assertEqual(plan['details']['decision']['owner_decides'], reading.PROBLEM_PROJECTION_DECISION)
        self.assertEqual(len(plan['details']['decision']['then']), 2)


if __name__ == '__main__':
    unittest.main()
