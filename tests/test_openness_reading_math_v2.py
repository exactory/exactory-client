"""A25 (reading-math-v2): a math-solver result review with an openness minimum reads as a bounded chain.

Every test sets openness_reading.NEW_MATH_CHAIN_PROTOCOLS to (math v2, math v1) for its duration.
"""

import copy
import json
from unittest.mock import patch

from test_openness_carried_math import MathCarriedCase
import test_openness_reading_v7_turns as turn_fixtures
from research_harness import native_review, openness_reading as reading, openness_reading_v7 as v7
from research_harness import review_composite, review_protocol as protocol
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.problem_openness import serialize_record

MATH_V2 = reading.MATH_PROTOCOL_V2
PROOF_TEXT = 'The proof artifact establishes the finite case.'
SUPPLEMENTARY_TEXT = 'Supplementary text outside the bound searches.'


def cite_evidence_bodies(artifacts, assignment, native, seen):
    """The delivered bodies, among the handles seen in reading turns, that belong to one unit the packet lists."""
    inventory = json.loads(artifacts.read(assignment['reading_chain']['inventory']))
    units = {row['handle']: row for row in inventory['units']}
    listed = {row['digest'] for row in native['evidence']}
    return [row['handle'] for row in inventory['material'] if row['handle'] in seen and len(row['units']) == 1
            and units[row['units'][0]]['sha256'] in listed]


class MathV2Support(MathCarriedCase):
    text_sizes = (1000,)
    route_output_tokens = 128000
    build_v7_answer = turn_fixtures.V7CommandTests.build_v7_answer

    def setUp(self):
        super().setUp()
        self.enable_protocols(MATH_V2, reading.MATH_PROTOCOL)

    def enable_protocols(self, *protocols):
        patcher = patch.object(reading, 'NEW_MATH_CHAIN_PROTOCOLS', protocols)
        patcher.start()
        self.addCleanup(patcher.stop)

    def build_math_packet(self, name='math-export', assignment_id='math-1', neighbours=0,
                          supplementary=(SUPPLEMENTARY_TEXT.encode(), 'text/plain; charset=utf-8')):
        """A math result packet with a version 1 minimum, a proof artifact and a supplementary input."""
        native = json.loads(self.artifacts.read(super().build_math_packet(name, assignment_id)))
        context = native['scientific_context']
        for key in ('audit_forecast', 'minimum_gaps'):
            context.pop(key, None)
        self.assertEqual(context['openness_minimum']['schema_version'], 1)
        context['openness_minimum']['neighbours'] = ['Synthetic neighbour %d' % index for index in range(neighbours)]
        self.proof = self.artifacts.put(PROOF_TEXT.encode(), 'text/markdown; charset=utf-8')
        self.supplementary = self.artifacts.put(*supplementary)
        native['evidence'] += [dict(kind='artifact', digest=row['sha256'], artifact=row)
                               for row in (self.proof, self.supplementary)]
        context['supplementary_inputs'] = dict(schema_version=1, inputs=[
            dict(kind='artifact', digest=self.supplementary['sha256'], artifact=self.supplementary)])
        return self.artifacts.put(serialize_record(native), 'application/json')

    def build_final_answer(self, packet, assignment, seen, decision='approve'):
        """The native result review of a final. Its evidence cites each delivered body that belongs to one unit that
        the native packet lists: a whole record or text, or the residual of a bound search."""
        if decision != 'approve':
            return dict(decision=decision, reason='The result requires further work.', objections=['A missing argument.'])
        native = json.loads(self.artifacts.read(assignment['context']['native_packet']))
        output = self.build_native_result(dict(native=native))
        output['findings']['openness']['evidence'] = [dict(ref=handle) for handle in
                                                      cite_evidence_bodies(self.artifacts, assignment, native, seen)]
        if reading.find_stage(packet) == 'closing':
            output = {key: output[key] for key in ('decision', 'findings')}
            for key in ('minimum', 'statement_digest', 'coverage'):
                output['findings']['openness'].pop(key)
        else:
            output['findings']['openness'].pop('minimum')
        return output

    def finish(self, native, first='math-1', decision='approve', assigned=False):
        """Answer every turn of a chain over its first assignment, which this method records unless assigned."""
        packet_native = json.loads(self.artifacts.read(native))
        packet_native['reviewer']['attestation_id'] = 'review-assignment:' + first
        native = self.artifacts.put(serialize_record(packet_native), 'application/json')
        current, packets, seen = first, [], {}
        if not assigned:
            self.assign(current, native)
        for _ in range(100):
            packet = self.read_packet(current)
            packets.append(packet)
            self.assertEqual(reading.find_protocol(packet), MATH_V2)
            if 'reading_turn' in packet:
                seen.update((row['handle'], row['sha256']) for row in packet['reading_turn']['plan']['now'])
                output = self.build_v7_answer(packet)
            elif 'reading_combine' in packet:
                binding = packet['reading_combine']
                output = dict(block=binding['block']['handle'], summary='Condensed supporting entries.',
                              refs=[binding['block']['entries'][0]['handle']])
            elif reading.find_stage(packet) == 'page':
                output = dict(targets=[dict(formulation=target, coverage='The sources cover this formulation.',
                    sentence='This formulation is covered.') for target in packet['reading_final']['page']['targets']])
            else:
                assignment = self.store.snapshot()['records']['review_assignment'][current]
                output = self.build_final_answer(packet, assignment, seen, decision)
            receipt = self.invoke(current, output)
            self.assertNotIn('output_refusal', receipt, receipt)
            if reading.find_stage(packet) in ('final', 'closing'):
                return current, packets
            following = 'math-next-%d' % len(self.store.snapshot()['records']['review_assignment'])
            self.assign(following, native, prior=current)
            current = following
        self.fail('A bounded math chain must reach its native review.')

    def assert_final(self, final, decision='approve'):
        records = self.store.snapshot()['records']
        assignment = records['review_assignment'][final]
        self.assertTrue(reading.verify_chain(records, self.artifacts, assignment))
        output, composite = review_composite.read_final_output(records, self.artifacts, assignment)
        native = json.loads(self.artifacts.read(assignment['context']['native_packet']))
        native_review.validate_output(native, output)
        self.assertEqual(output['decision'], decision)
        if decision == 'approve':
            openness = output['findings']['openness']
            self.assertEqual(openness['minimum'], native['scientific_context']['openness_minimum'])
            self.assertTrue({self.proof['sha256'], self.supplementary['sha256']} <= set(openness['evidence'])
                            <= {row['digest'] for row in native['evidence']})
        return output, composite


class MathV2ChainTests(MathV2Support):
    def test_a25_request_final_delivers_native_evidence_with_its_binding_and_records_the_review(self):
        native = self.build_math_packet()
        final, packets = self.finish(native)
        output, composite = self.assert_final(final)
        self.assertIsNone(composite)
        records = self.store.snapshot()['records']
        packet = json.loads(self.artifacts.read(native))
        inventory = json.loads(self.artifacts.read(records['review_assignment'][final]['reading_chain']['inventory']))
        self.assertEqual(inventory['protocol'], MATH_V2)
        self.assertFalse(any(row['identity'].get('record_kind') == 'problem_statement' for row in inventory['units']))
        for artifact in (self.proof, self.supplementary):
            [unit] = [row for row in inventory['units'] if row['sha256'] == artifact['sha256']]
            self.assertEqual(unit['identity'], dict(kind='record', record_kind='native_evidence',
                                                    record_id=artifact['sha256']))
            delivered = ''.join(row['content'] for turn in packets for row in turn['evidence']
                                if row['handle'] in unit['bodies'])
            self.assertEqual(delivered.encode(), self.artifacts.read(artifact))
        # An original that a bound reading pins is delivered by its extracted text.
        bundles = records['source_bundle']
        originals = {bundles[row['bundle_id']]['original_sha256'] for row in records['reading'].values()
                     if row.get('bundle_id') in bundles}
        self.assertTrue(originals)
        self.assertFalse(originals & {row['sha256'] for row in inventory['units']})
        frame = packets[-1]['native']
        self.assertEqual({key: frame[key] for key in ('kind', 'contract', 'objective_id', 'root')},
                         {key: packet[key] for key in ('kind', 'contract', 'objective_id', 'root')})
        context = frame['scientific_context']
        self.assertNotIn('audit_forecast', context)
        self.assertEqual(context['openness_minimum'], dict(schema_version=1, statement='t1',
            neighbours=dict(count=0), searches=dict(count=len(packet['scientific_context']['openness_minimum']['searches'])),
            reading_digests=dict(count=len(packet['scientific_context']['openness_minimum']['reading_digests']))))
        self.assertEqual(context['supplementary_inputs'], packet['scientific_context']['supplementary_inputs'])
        self.assertEqual(packets[-1]['reading_final']['minimum'], dict(
            digest=digest(packet['scientific_context']['openness_minimum']), schema_version=1,
            searches=context['openness_minimum']['searches']['count'],
            readings=context['openness_minimum']['reading_digests']['count']))

    def test_a25_forty_neighbours_reach_a_recorded_composite_at_the_final_floor(self):
        native = self.build_math_packet(neighbours=40)
        final, packets = self.finish(native)
        output, composite = self.assert_final(final)
        self.assertIsNotNone(composite)
        self.assertGreater(sum(reading.find_stage(packet) == 'page' for packet in packets), 1)
        self.assertEqual(len(output['findings']['openness']['coverage'].splitlines()), 41)
        assignment = self.store.snapshot()['records']['review_assignment'][final]
        plan = json.loads(self.artifacts.read(assignment['capacity_plan']['artifact']))
        self.assertNotIn('combine', plan['task'])
        self.assertEqual(plan['task']['final']['material_allowance'],
                         plan['task']['final']['levels'][-1]['material_allowance'])

    def test_a25_every_math_v2_packet_has_a_closed_schema(self):
        from ci.capacity_packet_inventory import read_packet_registry, validate_packet
        registry = read_packet_registry()
        for neighbours in (0, 40):
            native = self.build_math_packet(name='schema-%d' % neighbours, assignment_id='schema-%d' % neighbours,
                                            neighbours=neighbours)
            _, packets = self.finish(native, first='schema-%d' % neighbours)
            for packet in packets:
                with self.subTest(neighbours=neighbours, stage=reading.find_stage(packet)):
                    self.assertEqual(validate_packet('native_math', packet, registry), [])

    def test_a25_closing_revise_and_reject_are_recorded_without_approval_fields(self):
        for decision in ('revise', 'reject'):
            with self.subTest(decision=decision):
                native = self.build_math_packet(name=decision, assignment_id=decision, neighbours=40)
                final, _ = self.finish(native, first=decision, decision=decision)
                output, composite = self.assert_final(final, decision)
                self.assertIsNotNone(composite)
                self.assertEqual(set(output), {'decision', 'reason', 'objections'})

class MathV2CarriedTests(MathV2Support):
    text_sizes = (80000, 80000, 80000)

    def seed_stopped_v1(self):
        """Three verified v1 reading turns and a final that the exact measure of ruling 24.2 stops."""
        native = self.build_math_packet()
        with patch.object(reading, 'NEW_MATH_CHAIN_PROTOCOLS', (reading.MATH_PROTOCOL,)):
            self.run_turn(native)
            self.run_turn(native, 'math-2', 'math-1')
            self.run_turn(native, 'math-3', 'math-2')
            self.assign('math-final', native, 'math-3')
            self.assertEqual(reading.find_stage(self.read_packet('math-final')), 'final')
            self.limit_input(1000)
            with self.assertRaises(ResearchError) as caught:
                self.invoke('math-final', self.build_native_result(self.read_packet('math-final')))
            self.assertEqual(caught.exception.code, reading.PROJECTION_STOP)
        self.limit_input(1300000)
        return native

    def test_a25_packet_that_v2_refuses_starts_the_v1_chain_of_today(self):
        pdf = b'%PDF-1.4\n% A binary supplementary input that no text unit can deliver.\n'
        native = self.build_math_packet(supplementary=(pdf, 'application/pdf'))
        records = self.store.snapshot()['records']
        value = dict(id='math-1', role='native_math', route_id='route', dossier_id=None,
                     reviewer_id='openness-reviewer', author_id='problem-author', context=dict(native_packet=native))
        logical = protocol.build_packet(records, self.artifacts, None, 'native_math', value['reviewer_id'], **value['context'])
        route = records['review_route']['route']
        refusal = v7.check_v7_admission(records, self.artifacts, route, logical['native'])
        self.assertEqual((refusal['state'], refusal['reason']), ('stop', 'binary_required_evidence'))
        built = {}
        for protocols in ((reading.MATH_PROTOCOL,), (MATH_V2, reading.MATH_PROTOCOL)):
            with patch.object(reading, 'NEW_MATH_CHAIN_PROTOCOLS', protocols):
                built[protocols] = reading.prepare_packet(records, self.artifacts, value, route, logical)
        self.assertEqual(built[(reading.MATH_PROTOCOL,)], built[(MATH_V2, reading.MATH_PROTOCOL)])
        packet, chain = built[(MATH_V2, reading.MATH_PROTOCOL)]
        self.assertEqual((reading.find_protocol(packet), reading.find_stage(packet)), (reading.MATH_PROTOCOL, 'reading'))
        self.assertEqual(chain, dict(first_assignment_id='math-1', turn=1))

    def test_a25_stopped_v1_turns_are_carried_without_repeating_units_to_a_recorded_review(self):
        native = self.seed_stopped_v1()
        before = self.store.snapshot()['records']
        hashes = {kind: {key: digest(value) for key, value in rows.items()} for kind, rows in before.items()}
        completed = set()
        for identifier in ('math-1', 'math-2', 'math-3'):
            _, sent = self.read_sent_packet(identifier)
            completed.update(reading.review_excerpts.find_complete_bodies(sent))
        final, packets = self.finish(native, first='math-successor')
        self.assert_final(final)
        after = self.store.snapshot()['records']
        for kind, rows in hashes.items():
            self.assertEqual(rows, {key: digest(after[kind][key]) for key in rows})
        self.assertFalse(completed & {row['artifact']['sha256'] for packet in packets for row in packet['evidence']})
        first = after['review_assignment']['math-successor']
        selection = json.loads(self.artifacts.read(first['carried_selection']['artifact']))
        self.assertEqual([row['origin']['assignment_id'] for row in selection['turns']], ['math-1', 'math-2', 'math-3'])
        turns = reading.verify_chain(after, self.artifacts, after['review_assignment'][final])
        self.assertEqual([row['origin']['protocol'] for row in turns[:3]], [reading.MATH_PROTOCOL] * 3)
        self.assertEqual([row['origin']['assignment_id'] for row in turns[:3]], ['math-1', 'math-2', 'math-3'])

    def test_a25_guard_part_4_refuses_a_changed_observation_unit_and_projection(self):
        native = self.seed_stopped_v1()
        final, _ = self.finish(native, first='math-successor')
        records = self.store.snapshot()['records']
        assignment = records['review_assignment'][final]
        first = records['review_assignment']['math-successor']
        path = self.root / records['review_attempt']['attempt-math-1']['output']['path']
        original = path.read_bytes()
        path.chmod(0o600)
        try:
            path.write_bytes(original + b' ')
            with self.assertRaises(ResearchError):
                reading.verify_chain(records, self.artifacts, assignment)
        finally:
            path.write_bytes(original)
        selection = json.loads(self.artifacts.read(first['carried_selection']['artifact']))
        selection['turns'][0]['ranges'][0]['sha256'] = '0' * 64
        changed = copy.deepcopy(records)
        changed['review_assignment']['math-successor']['carried_selection'] = dict(
            protocol=first['carried_selection']['protocol'], digest=digest(selection),
            artifact=self.artifacts.put(serialize_record(selection), 'application/json'))
        with self.assertRaises(ResearchError):
            reading.verify_chain(changed, self.artifacts, changed['review_assignment'][final])
        project = v7.project_carried_turn

        def project_changed(*args, **kwargs):
            value = project(*args, **kwargs)
            value['notes']['text'] += ' Changed.'
            return value
        with patch.object(v7, 'project_carried_turn', side_effect=project_changed):
            with self.assertRaises(ResearchError):
                reading.verify_chain(records, self.artifacts, assignment)
        self.assertTrue(reading.verify_chain(records, self.artifacts, assignment))


class MathV2RecoveryTests(MathV2Support):
    from test_openness_page_replacement import PageReplacementTests
    replace = PageReplacementTests.replace
    invoke_usage = PageReplacementTests.invoke_usage

    def reach_final(self, native):
        head = 'math-1'
        self.assign(head, native)
        for number in range(100):
            packet = self.read_packet(head)
            if reading.find_stage(packet) != 'reading':
                return head, packet
            self.invoke(head, self.build_v7_answer(packet))
            following = 'math-read-%d' % number
            self.assign(following, native, prior=head)
            head = following
        self.fail('The fixture must finish reading.')

    def test_reading_at_output_limit_is_replaced_at_next_lower_level(self):
        from codex_route_fixtures import build_codex_response, bind_codex_response
        from test_review_output_profile import build_row
        from research_harness import review_route_capacity as capacity
        native = self.build_math_packet()
        with patch.object(capacity, 'read_profile_priors', return_value=[build_row(level=None, thinking=1000, count=8, over=1)]):
            self.assign('math-1', native)
            packet = self.read_packet('math-1')

            def respond(route, request, credential):
                response = build_codex_response(route, request, self.build_v7_answer(packet))
                response['status'] = 'incomplete'
                response['usage']['output_tokens'] = 138371
                return bind_codex_response(route, request, response)
            with patch.object(protocol.transport, 'send_request', side_effect=respond):
                self.mutate(protocol.invoke_assignment, dict(id='over-math-1', assignment_id='math-1'), credential='fixture')
            head = self.replace('math-1')
        after = self.read_packet(head)
        self.assertEqual(reading.find_protocol(after), MATH_V2)
        self.assertEqual((packet['reading_turn']['capacity']['material_allowance'],
                          after['reading_turn']['capacity']['material_allowance']), (120000, 60000))

    def test_refused_native_final_is_replaced_with_the_notice(self):
        native = self.build_math_packet()
        head, packet = self.reach_final(native)
        receipt = self.invoke(head, dict(decision='approve'))
        self.assertIn('output_refusal', receipt)
        replacement = self.replace(head)
        after = self.read_packet(replacement)
        self.assertEqual(after['output_refusal']['form'], 'whole')
        for key in v7.FINAL_MATERIAL_FIELDS:
            self.assertEqual(after['reading_final'][key], packet['reading_final'][key])
        seen = {row['handle']: row['sha256'] for name in self.store.snapshot()['records']['review_assignment']
                if name.startswith('math-1') or name.startswith('math-read')
                for row in self.read_packet(name).get('reading_turn', {}).get('plan', {}).get('now', [])}
        assignment = self.store.snapshot()['records']['review_assignment'][replacement]
        self.invoke(replacement, self.build_final_answer(after, assignment, seen))
        self.assert_final(replacement)

    def test_page_at_output_limit_is_split(self):
        native = self.build_math_packet(neighbours=40)
        head, packet = self.reach_final(native)
        self.assertEqual(reading.find_stage(packet), 'page')
        targets = packet['reading_final']['page']['targets']
        self.invoke_usage(head, dict(targets=[dict(formulation=target, coverage='Covered.', sentence='Covered.')
                                              for target in targets]), tokens=128000)
        replacement = self.replace(head)
        after = self.read_packet(replacement)
        self.assertEqual(reading.find_protocol(after), MATH_V2)
        self.assertLess(len(after['reading_final']['page']['targets']), len(targets))
        assignment = self.store.snapshot()['records']['review_assignment'][replacement]
        page_plan = json.loads(self.artifacts.read(assignment['reading_chain']['final_page_plan']['artifact']))
        self.assertEqual(page_plan['cause']['kind'], 'split')
        # The chain records its review, and the turn that the split took over is history for its terminal lookup.
        seen = {}
        for name in self.store.snapshot()['records']['review_assignment']:
            seen.update((row['handle'], row['sha256'])
                        for row in self.read_packet(name).get('reading_turn', {}).get('plan', {}).get('now', []))
        head = replacement
        for number in range(50):
            packet = self.read_packet(head)
            assignment = self.store.snapshot()['records']['review_assignment'][head]
            if reading.find_stage(packet) == 'page':
                output = dict(targets=[dict(formulation=target, coverage='Covered.', sentence='Covered.')
                                       for target in packet['reading_final']['page']['targets']])
            elif 'reading_combine' in packet:
                block = packet['reading_combine']['block']
                output = dict(block=block['handle'], summary='Condensed.', refs=[block['entries'][0]['handle']])
            else:
                output = self.build_final_answer(packet, assignment, seen)
            self.invoke(head, output)
            if reading.find_stage(packet) == 'closing':
                break
            self.assign('after-split-%d' % number, native, prior=head)
            head = 'after-split-%d' % number
        records = self.store.snapshot()['records']
        self.assertEqual(reading.find_final_assignment(records, self.artifacts, 'math-1')['id'], head)
        self.assert_final(head)


class MathV2UncheckedAnswerTests(MathV2Support):
    """A math v2 turn whose answer fails its check and has no refusal record (0.91.11 with the bounded chains of
    0.92.0): review-series plans the recorded run request again, and no next turn is assigned on the answer."""

    def test_reading_answer_that_failed_its_check_is_resumed_and_no_turn_follows_it(self):
        from research_harness.review_continuation import UNRECORDED_REFUSAL_MESSAGE, plan_next
        from test_openness_reading_v7_unchecked_answer import assert_unchecked, lose_refusal_record
        native = self.build_math_packet()
        self.assign('math-1', native)
        packet = self.read_packet('math-1')
        self.assertEqual(reading.find_protocol(packet), MATH_V2)
        with lose_refusal_record():
            receipt = self.invoke('math-1', dict(self.build_v7_answer(packet), notes=7))
        self.assertNotIn('output_refusal', receipt)
        plan = plan_next(self.store.snapshot()['records'], self.artifacts, 'math-1')
        self.assertEqual((plan['state'], plan['command'], plan['request_id']),
                         ('resume', 'review-run', 'invoke-attempt-math-1'), plan)
        self.assertEqual(plan['details']['message'], UNRECORDED_REFUSAL_MESSAGE)
        with self.assertRaises(ResearchError) as raised:
            self.assign('math-2', native, prior='math-1')
        assert_unchecked(self, raised.exception, 'review_source_continuation_unverified',
                         (v7, 'UNCHECKED_HEAD_MESSAGE'), 'math-1', 'attempt-math-1')
