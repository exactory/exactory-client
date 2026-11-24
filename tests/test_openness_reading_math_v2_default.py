"""The released value of NEW_MATH_CHAIN_PROTOCOLS, and the bytes of protocol v7 after its description table.

The fixture openness-v7-problem-chain-golden.json holds what the base of lane d1-reading-math-v2 (the head of the
0.92.0 branch before the lane) built for the chain below: the inventory, plan and state digests, the frames of the
plan, and the packet, prompt and request digest of a reading turn and of a final.
"""

from contextlib import nullcontext
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_property_drivers_b as drivers
from test_openness_carried_math import MathCarriedCase
from test_openness_reading_v7_turns import MemoryArtifacts, build_native
from openness_reading_fixtures import build_theorem
from openness_legacy_fixtures import load_0_87_0_openness_reading
from v7_capacity_fixtures import build_route
from research_harness import openness_reading as reading, openness_reading_v7 as v7
from research_harness import review_protocol as protocol, review_route_capacity as capacity
from research_harness.evidence import digest
from research_harness.problem_openness import serialize_record
from research_harness.openness_minimum import OpennessError, validate_minimum_bindings

GOLDEN_PATH = Path(__file__).parent / 'fixtures/openness-v7-problem-chain-golden.json'


def build_problem_chain(frames):
    """Rebuild the problem v7 chain with its historical frame fields."""
    artifacts = MemoryArtifacts()
    native = dict(build_native(targets=3), kind='result', contract=dict(orchestration='problem_creation'))
    native['scientific_context']['openness_minimum'].update(supporting_reading_digests=[], duplicate_resolutions=[],
                                                            family_readings=[], settling_work_id=None)
    native['subject'] = dict(id='statement-1', statement='A synthetic exact statement.')
    subject = artifacts.put(serialize_record(native['subject']), 'application/json')
    native.update(subject_digest=subject['sha256'], evidence=[dict(kind='blob', digest=subject['sha256'], artifact=subject)])
    native['scientific_context']['audit_forecast']['notices'] = [dict(kind='fixture', text='A forecast notice.')]
    native['scientific_context']['minimum_gaps'] = [dict(formulation='A synthetic exact statement.', condition='A gap.',
                                                         outcomes=dict(supported_open='blocked'))]
    route = build_route('codex_cli_v1', 'gpt-6-astra')
    logical = dict(protocol=protocol.PROTOCOL, role='native_math', reviewer_id=native['reviewer'], dossier_id=None,
                   native=native)
    with patch.object(v7, 'measure_turn_frames', return_value=frames):
        admitted = v7.check_v7_admission({}, artifacts, route, native, finding_version=1)
    packet, chain = v7.prepare_first_assignment({}, artifacts, dict(id='golden-chain'), route, logical, [],
                                                admission=admitted)
    inventory = json.loads(artifacts.read(chain['inventory']))
    state = json.loads(artifacts.read(chain['state']))
    plan = admitted['capacity_plan']
    values = dict(inventory=digest(inventory), plan=digest(plan), state=digest(state), frames=plan['frame'])
    state['deliveries'] = [dict(unit=row['handle'], start=0, end=row['characters']) for row in inventory['material']]
    final = v7.build_turn_packet(artifacts, route, logical, 'golden-chain', 2, inventory, state, plan,
                                 capacity.derive_plan_task_limits(plan, 'final'), 'final')
    for stage, value in [('reading', packet), ('final', final)]:
        prompt = protocol.canonical_prompt('native_math', reading_protocol=v7.PROTOCOL, reading_stage=stage,
                                           common_version=1)
        values[stage] = dict(packet=value, prompt=prompt,
                             request_digest=digest(protocol.transport.request_body(route, prompt['text'], value)))
    return values


class ProblemV7BytesTests(unittest.TestCase):
    def test_problem_v7_inventory_plan_packets_prompts_and_request_digests_equal_the_base(self):
        expected = json.loads(GOLDEN_PATH.read_text())
        frames = {kind: frame['fields'] for kind, frame in expected['frames'].items()}
        self.assertEqual(json.loads(json.dumps(build_problem_chain(frames))), expected)


class MathV1DefaultTests(MathCarriedCase):
    route_output_tokens = 128000

    def compare_with_released(self, reference, identifier, prior=None):
        records = self.store.snapshot()['records']
        value = dict(id=identifier, role='native_math', route_id='route', dossier_id=None,
            reviewer_id='openness-reviewer', author_id='problem-author', context=dict(native_packet=reference))
        if prior is not None:
            value['context']['prior_assignment_id'] = prior
        route = records['review_route']['route']
        logical = protocol.build_packet(records, self.artifacts, None, 'native_math', value['reviewer_id'], **value['context'])
        expected, _ = load_0_87_0_openness_reading().prepare_packet(records, self.artifacts, value, route, logical)
        current, _ = reading.prepare_packet(records, self.artifacts, value, route, logical)
        self.assertEqual(current, expected)
        actual = self.assign(identifier, reference, prior)
        self.assertEqual(actual['packet_digest'], expected['digest'])
        self.assertEqual(actual['prompt_hash'], protocol.canonical_prompt('native_math',
            reading_stage=reading.find_stage(expected), reading_protocol=reading.MATH_PROTOCOL)['rendered_prompt_hash'])
        self.assertEqual(json.loads(self.artifacts.read(actual['packet'])), expected)
        return actual

    def test_default_new_math_chain_is_the_released_v1_chain(self):
        self.assertEqual(reading.NEW_MATH_CHAIN_PROTOCOLS, (reading.MATH_PROTOCOL,))
        self.compare_with_released(self.build_math_packet(), 'math-1')

    def test_v1_chain_in_progress_continues_byte_for_byte_under_either_order(self):
        native = self.build_math_packet()
        self.assign('math-1', native)
        self.invoke('math-1', self.build_reading_response(self.read_packet('math-1')))
        records = self.store.snapshot()['records']
        value = dict(id='math-2', role='native_math', route_id='route', dossier_id=None,
            reviewer_id='openness-reviewer', author_id='problem-author',
            context=dict(native_packet=native, prior_assignment_id='math-1'))
        logical = protocol.build_packet(records, self.artifacts, None, 'native_math', value['reviewer_id'], **value['context'])
        built = []
        for protocols in ((reading.MATH_PROTOCOL,), (reading.MATH_PROTOCOL_V2, reading.MATH_PROTOCOL)):
            with patch.object(reading, 'NEW_MATH_CHAIN_PROTOCOLS', protocols):
                built.append(reading.prepare_packet(records, self.artifacts, value, records['review_route']['route'], logical))
        self.assertEqual(built[0], built[1])
        with patch.object(reading, 'NEW_MATH_CHAIN_PROTOCOLS', (reading.MATH_PROTOCOL_V2, reading.MATH_PROTOCOL)):
            self.compare_with_released(native, 'math-2', 'math-1')

    def test_math_v1_forecast_offers_only_available_ways(self):
        """Use a v1 chain long enough to be assigned a reading turn."""
        reference = self.build_math_packet()
        first = self.assign('math-1', reference)
        self.assertEqual(reading.find_protocol(self.read_packet('math-1')), reading.MATH_PROTOCOL)
        self.assertEqual(first['capacity_warning']['input']['ways'],
                         [reading.CONTINUE_MATH_ANSWER, reading.WAIT_MATH_ANSWER, reading.SMALLER_PACKET_ANSWER])
        self.invoke('math-1', self.build_reading_response(self.read_packet('math-1')))
        with patch.object(reading, 'NEW_MATH_CHAIN_PROTOCOLS', (reading.MATH_PROTOCOL_V2, reading.MATH_PROTOCOL)):
            second = self.assign('math-2', reference, 'math-1')
        self.assertEqual(reading.find_protocol(self.read_packet('math-2')), reading.MATH_PROTOCOL)
        self.assertEqual(second['capacity_warning']['input']['ways'],
                         [reading.CONTINUE_MATH_ANSWER, reading.SMALLER_PACKET_ANSWER])


class MathV2RequestTests(drivers.MathChain):
    def reach_request_final(self, head, native):
        self.assign(head, native)
        for _ in range(20):
            stage = reading.find_stage(self.read_packet(head))
            if stage != 'reading':
                self.assertEqual(stage, 'final')
                return head
            _, _, receipt = self.send_turn(head)
            self.assertNotIn('output_refusal', receipt, receipt)
            head = self.assign_next(self.plan(head))
        self.fail('The math chain must reach its final request.')

    def test_math_final_output_cap_retries_once_and_stops_on_its_own_floor(self):
        """Two targets distinguish a paged replacement from an identical retry."""
        from research_harness.review_series import run_series

        head = self.reach_request_final('math-1', self.build_math_packet(neighbours=1))
        _, initial, _ = self.send_turn(head, capped=True)
        retry = self.plan(head)
        self.assertEqual(retry['state'], 'run')
        self.assertEqual(retry['payload']['assignment_id'], head)
        _, repeated, _ = self.send_turn(head, capped=True, attempt_id=retry['payload']['id'])
        self.assertEqual(repeated['request'], initial['request'])
        stopped = self.plan(head)
        self.assertEqual((stopped['state'], stopped['code']), ('stop', 'review_output_over_cap'))
        # The fixture binds no native objective, so the export of the route answer cannot be checked.
        self.assertEqual(stopped['details']['export_refusal']['code'], 'native_review_objective_unavailable')
        self.assertEqual([[step['command'] for step in answer['steps']]
                          for answer in stopped['details']['decision']['then']], [['status']])
        self.assertTrue(all('final_page_plan' not in row.get('reading_chain', {})
                            for row in self.read_records()['review_assignment'].values()))
        self.assertEqual(stopped['details']['range_not_served'], v7.MATH_ONE_REQUEST_STATEMENT)
        with patch.object(v7, 'V7_FINAL_FORMS', ('request', 'pages')):
            self.assertEqual(self.plan(head)['details']['range_not_served'], v7.MATH_ONE_REQUEST_STATEMENT)
        rows = []
        with patch.object(protocol.transport, 'send_request') as send:
            self.assertEqual(run_series(self.store, head, emit=rows.append), 3)
        send.assert_not_called()
        self.assertEqual(rows[-1]['code'], 'review_output_over_cap')

    def test_math_final_checks_nonapproval_objections_before_recording(self):
        head = self.reach_request_final('math-1', self.build_math_packet())
        output = dict(decision='revise', reason='Further work is needed.', objections=['A missing argument.'])
        refusal = self.invoke(head, output)['output_refusal']
        self.assertEqual((refusal['code'], refusal['details']['path']), ('invalid_native_review', '/objections/0'))
        replacement = self.assign_next(self.plan(head))
        corrected = dict(output, objections=[dict(blocking=True, description='A missing argument.')])
        self.assertNotIn('output_refusal', self.invoke(replacement, corrected))
        self.assertEqual(self.assert_final(replacement, 'revise'), (corrected, None))

    def send_completed_at_cap(self, head, attempt_id=None):
        fixtures = drivers.codex_route_fixtures
        respond = fixtures.build_codex_response

        def capped(route, request, output):
            response = respond(route, request, output)
            response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
            return fixtures.bind_codex_response(route, request, response)

        with patch.object(fixtures, 'build_codex_response', side_effect=capped):
            return self.send_turn(head, attempt_id=attempt_id)[1]

    def test_completed_at_cap_retries_and_selects_the_below_cap_answer(self):
        head = self.reach_request_final('math-1', self.build_math_packet())
        initial = self.send_completed_at_cap(head)
        self.assertEqual(initial['status'], 'completed')
        self.assertIsNone(protocol.assignment_state(self.read_records(), self.artifacts, head)['output'])
        retry = self.plan(head)
        self.assertEqual((retry['state'], retry['command']), ('run', 'review-run'))
        self.assertEqual(retry['payload']['assignment_id'], head)
        _, corrected, receipt = self.send_turn(head, attempt_id=retry['payload']['id'])
        self.assertNotIn('output_refusal', receipt)
        self.assertEqual(corrected['request'], initial['request'])
        self.assertEqual(protocol.assignment_state(self.read_records(), self.artifacts, head)['attempt_id'],
                         corrected['id'])
        self.record_final(head, self.plan(head))

    def test_two_completed_attempts_at_cap_stop_before_another_call(self):
        head = self.reach_request_final('math-1', self.build_math_packet())
        initial = self.send_completed_at_cap(head)
        retry = self.plan(head)
        self.assertEqual((retry['state'], retry['command']), ('run', 'review-run'))
        self.assertEqual(retry['payload']['assignment_id'], head)
        repeated = self.send_completed_at_cap(head, retry['payload']['id'])
        self.assertEqual(repeated['request'], initial['request'])
        stopped = self.plan(head)
        self.assertEqual((stopped['state'], stopped['code']), ('stop', 'review_output_over_cap'))
        self.assertIsNone(protocol.assignment_state(self.read_records(), self.artifacts, head)['output'])
        with patch.object(protocol.transport, 'send_request') as send:
            self.assert_error('review_output_over_cap', lambda: self.mutate(protocol.invoke_assignment,
                dict(id='third', assignment_id=head), credential='fixture'))
        send.assert_not_called()

    def test_long_first_assignment_uses_released_math_selection(self):
        identifier = 'a' * 121
        native = self.build_math_packet(assignment_id=identifier)
        self.assign(identifier, native)
        self.assertNotEqual(reading.find_protocol(self.read_packet(identifier)), reading.MATH_PROTOCOL_V2)

    def math_citation_fixture(self, count):
        reference = self.build_math_packet()
        native = json.loads(self.artifacts.read(reference))
        minimum = native['scientific_context']['openness_minimum']
        rows = []
        for index in range(count - 2):
            search = dict(self.search, id='search-%04d-' % index + 'q' * 100)
            record = self.artifacts.put(serialize_record(search), 'application/json')
            rows.append(search)
            minimum['searches'].append(dict(formulation=minimum['statement'], search_digest=record['sha256']))
            native['evidence'].append(dict(kind='blob', digest=record['sha256'], artifact=record))
        return native, rows

    def test_required_citation_boundary_over_one_target(self):
        from research_harness.check_mode import check_mutation
        from research_harness import review_excerpts

        native, rows = self.math_citation_fixture(104)

        def seed(selected, label):
            def record(transaction):
                for row in selected:
                    transaction.put('literature_search', row['id'], row)
                return len(selected)
            self.store.mutate('fixture.searches', dict(count=len(selected)), record,
                              expected_revision=self.store.revision, request_id=label)

        smaller = copy.deepcopy(native)
        minimum = smaller['scientific_context']['openness_minimum']
        minimum['searches'].pop()
        smaller['evidence'].pop()
        seed(rows[:-1], 'seed-103')
        validate_minimum_bindings(minimum, minimum['statement'], self.read_records())
        value = dict(id='math-1', role='native_math', route_id='route', dossier_id=None,
                     reviewer_id='openness-reviewer', author_id='problem-author',
                     context=dict(native_packet=self.artifacts.put(serialize_record(smaller), 'application/json')))
        checked = check_mutation(self.store, protocol.record_assignment, value,
            expected_revision=self.store.revision, request_id='check-103', bounded_payload=value)
        admitted = self.mutate(protocol.record_assignment, value)['result']
        self.assertEqual((checked['check'], checked['recorded']), ('accepted', False))
        self.assertEqual(reading.find_protocol(self.read_packet(admitted['id'])), reading.MATH_PROTOCOL_V2)

        seed(rows[-1:], 'seed-104')
        records = self.read_records()
        with self.assertRaises(OpennessError) as stale:
            validate_minimum_bindings(minimum, minimum['statement'], records)
        self.assertEqual((stale.exception.code, stale.exception.details['search_ids']),
                         ('openness_search_stale', [rows[-1]['id']]))
        route = records['review_route']['route']
        minimum = native['scientific_context']['openness_minimum']
        validate_minimum_bindings(minimum, minimum['statement'], records)
        expected = dict(required=104, citable=104, request_characters=20065, request_bound=20000,
                        answer_characters=2107, answer_bound=7500)
        for identifier, budget, form in (('receipt-ordinary', 1300000, None),
                                         ('receipt-v1', 5000, reading.MATH_PROTOCOL)):
            with self.subTest(form=form), patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', budget):
                native['reviewer']['attestation_id'] = 'review-assignment:' + identifier
                value = dict(id=identifier, role='native_math', route_id='route', dossier_id=None,
                             reviewer_id='openness-reviewer', author_id='problem-author',
                             context=dict(native_packet=self.artifacts.put(serialize_record(native), 'application/json')))
                def check(request_id):
                    return check_mutation(self.store, protocol.record_assignment, value,
                        expected_revision=self.store.revision, request_id=request_id, bounded_payload=value)
                with patch.object(reading, 'NEW_MATH_CHAIN_PROTOCOLS', (reading.MATH_PROTOCOL,)):
                    legacy = check(identifier + '-legacy')
                    with protocol.transport.text_only_delivery(True):
                        logical = protocol.build_packet(records, self.artifacts, None, value['role'],
                            value['reviewer_id'], **value['context'])
                        legacy_packet, _ = reading.prepare_packet(records, self.artifacts, value, route, logical)
                checked = check(identifier + '-check')
                saved = self.mutate(protocol.record_assignment, value)['result']
                self.assertEqual(reading.find_protocol(self.read_packet(identifier)), form)
                self.assertEqual(checked['review_delivery'], legacy['review_delivery'])
                self.assertEqual(saved['packet_digest'], legacy_packet['digest'])
                for result in (checked, saved):
                    refusal = result['protocol_selection']['admissions'][0]
                    self.assertEqual(refusal['reason'], 'required_citations_over_bound')
                    self.assertEqual(refusal['details'], dict(expected,
                        answer_characters=expected['answer_characters'] + len(identifier) - len('math-1')))
                self.assertNotIn('protocol_selection', self.read_records()['review_assignment'][identifier])

    def test_required_citations_cannot_exceed_the_final_answer_grant(self):
        native, _ = self.math_citation_fixture(2)
        records = self.read_records()
        route = records['review_route']['route']
        # Canonical reviewer provenance is echoed in the answer. This input still fits the real route.
        native['reviewer']['actor_id'] = 'a' * 8000
        refusal = v7.check_v7_admission(records, self.artifacts, route, native)
        self.assertEqual((refusal['state'], refusal.get('reason')), ('stop', 'required_citations_over_bound'))
        details = refusal['details']
        self.assertLessEqual(details['request_characters'], details['request_bound'])
        self.assertGreater(details['answer_characters'], details['answer_bound'])

    def test_original_242_citation_request_is_refused(self):
        native, rows = self.math_citation_fixture(242)
        records = self.read_records()
        records['literature_search'].update({row['id']: row for row in rows})
        route = records['review_route']['route']
        refusal = v7.check_v7_admission(records, self.artifacts, route, native)
        self.assertEqual(refusal.get('reason'), 'required_citations_over_bound')
        self.assertEqual(refusal['details'], dict(required=242, citable=242, request_characters=46975,
            request_bound=20000, answer_characters=4177, answer_bound=7500))

    def test_cap_stop_applies_across_refusal_replacements(self):
        self.assert_cap_stop_across_refusal_replacements('incomplete')

    def test_invalid_completed_at_cap_counts_across_replacements(self):
        self.assert_cap_stop_across_refusal_replacements('invalid')

    def test_valid_completed_at_cap_allows_shape_replacement_then_stops(self):
        self.assert_cap_stop_across_refusal_replacements('valid')

    def assert_cap_stop_across_refusal_replacements(self, kind):
        final = self.reach_request_final('math-1', self.build_math_packet())
        invalid = dict(decision='revise', reason='Needs work.', objections=['invalid'])
        if kind == 'incomplete':
            self.send_turn(final, capped=True)
        else:
            output = patch.object(drivers.stub, 'build_answer', return_value=invalid) if kind == 'invalid' else nullcontext()
            with output:
                self.send_completed_at_cap(final)
        retry = self.plan(final)
        self.assertEqual(retry['state'], 'run')
        with patch.object(drivers.stub, 'build_answer', return_value=invalid):
            self.send_turn(final, attempt_id=retry['payload']['id'])
        replacement = self.plan(final)
        self.assertEqual((replacement['state'], replacement['command']), ('replace', 'review-assignment'))
        corrected = self.assign_next(replacement)
        attempt = self.send_completed_at_cap(corrected)
        self.assertEqual(self.plan(corrected)['code'], 'review_output_over_cap')
        with patch.object(protocol.transport, 'send_request',
                          side_effect=AssertionError('The stopped replacement reached transport.')) as send:
            self.assert_error('review_output_over_cap', lambda: self.mutate(protocol.invoke_assignment,
                dict(id='direct-after-stop', assignment_id=corrected), credential='fixture'))
            send.assert_not_called()
        self.assertEqual(attempt['status'], 'completed')

    def test_unadvertised_evidence_handle_cannot_approve_the_final(self):
        head = self.reach_request_final('math-1', self.build_math_packet())
        packet = self.read_packet(head)
        binding = packet['reading_final']
        assignment = self.read_records()['review_assignment'][head]
        inventory = json.loads(self.artifacts.read(assignment['reading_chain']['inventory']))
        shown = {row['handle'] for row in binding['evidence_units']['rows']}
        alias = next(body['handle'] for body in inventory['material']
                     if len(body['units']) == 1 and body['units'][0] in shown and body['handle'] not in shown)
        output = drivers.stub.build_math_final(packet, binding)
        output['findings']['openness']['evidence'] = [dict(ref=alias)]
        refusal = self.invoke(head, output).get('output_refusal', {})
        self.assertEqual(refusal.get('code'), 'invalid_reference_handle')
        self.assertEqual(refusal['details']['path'], '/findings/openness/evidence/0')


class MathV2DuplicateCitationTests(drivers.MathChain):
    text_sizes = (1000, 1000)

    def build_text(self, number):
        return build_theorem(1) + ' ' + build_theorem(2) + ' References: none.'

    def test_equal_fulltext_bytes_have_one_citation_handle(self):
        first = self.assign('math-1', self.build_math_packet())
        inventory = json.loads(self.artifacts.read(first['reading_chain']['inventory']))
        fulltexts = [row for row in inventory['units'] if row['kind'] == 'fulltext']
        self.assertEqual(len(fulltexts), 2)
        self.assertEqual(fulltexts[0]['sha256'], fulltexts[1]['sha256'])
        _, turns = self.drive('math-1')
        rows = turns[-1][2]['evidence_units']['rows']
        self.assertEqual([row['handle'] for row in rows if row['kind'] == 'fulltext'], [fulltexts[0]['handle']])


if __name__ == '__main__':
    unittest.main()
