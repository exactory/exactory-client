"""Admission, prompt and read-answer rules for the opt-in v3 parts protocol."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_harness import bar_sources, review_preflight
from research_harness import review_condensing, review_field_classes as fields
from research_harness import review_parts as legacy, review_parts_v3 as parts
from research_harness import review_parts_prompts as texts, review_prompt_paragraphs as paragraphs
from research_harness import review_protocol as protocol, review_refusal_notice as notice
from research_harness import review_route_capacity as capacity
from research_harness.artifacts import ArtifactStore
from research_harness.evidence import digest
from research_harness.errors import ResearchError
import test_review_parts_v3 as fixtures
from review_parts_fixtures import PartsResultCase
from test_review_route_capacity import build_route


FORMS = dict(read=(None,), combine=(None, 'combine'), check=(None,), final=(None, 'page', 'closing'))
FORM_TEXT = dict(page=texts.PARTS_V3_PAGE, closing=texts.PARTS_V3_CLOSING)


def read_case(target_count=1):
    targets = [dict(id='target-%d' % n) for n in range(target_count)]
    group = dict(handle='g1', characters=10000)
    packet = {legacy.KEY: dict(kind='read', turn=1, targets=targets, capacity=dict(answer_bound=80000)),
              parts.GROUPS_KEY: [group]}
    plan = dict(task=dict(combine=dict(levels=[dict(material_allowance=60000)])))
    return packet, fixtures.make_turn_output(packet), plan


class ReadAnswerTests(unittest.TestCase):
    def test_ta7_distinct_bearings_and_partial_unborne(self):
        packet, base, plan = read_case(18)
        route = build_route()
        targets = packet[legacy.KEY]['targets']
        bearings = [dict(target=row['id'], relation='context', reason='Relevant context.') for row in targets[:11]]
        for name, accepted, entries, unborne in (
                ('eleven', True, bearings, base['unborne'][11:]),
                ('duplicate', False, [bearings[0], bearings[0]], base['unborne'][1:]),
                ('partial', True, bearings[:1], base['unborne'][1:2]),
                ('borne', False, bearings[:1], base['unborne'][:1]),
                ('duplicate unborne', False, [], base['unborne'][:1] * 2)):
            with self.subTest(name=name):
                output = copy.deepcopy(base)
                output['cards'][0]['bearing'] = entries
                output['unborne'] = unborne
                if accepted:
                    parts._validate_read(packet, output, {}, route, plan)
                else:
                    with self.assertRaises(ResearchError) as refused:
                        parts._validate_read(packet, output, {}, route, plan)
                    self.assertEqual(refused.exception.code, 'invalid_value_review')

        with self.subTest(name='a target may bear on two different cards'):
            packet[parts.GROUPS_KEY].append(dict(packet[parts.GROUPS_KEY][0], handle='g2'))
            output = fixtures.make_turn_output(packet)
            for card in output['cards']:
                card['bearing'] = bearings[:1]
            output['unborne'] = base['unborne'][1:]
            parts._validate_read(packet, output, {}, route, plan)

    def test_ta8_malformed_members_are_refused(self):
        packet, base, plan = read_case()
        route = build_route()
        for name, mutate in (
                ('bearing target', lambda o: o['cards'][0].update(bearing=[
                    dict(target=[], relation='context', reason='Relevant context.')])),
                ('unborne target', lambda o: o['unborne'][0].update(target=[])),
                ('group handle', lambda o: o['cards'][0]['group'].update(handle=[])),
                ('group number', lambda o: o['cards'][0]['group'].update(characters=10000.0)),
                ('statement unit', lambda o: o['cards'][0].update(statements=[
                    dict(unit=[], text='A claim.', why='Relevant context.')])),
                ('card', lambda o: o['cards'].__setitem__(0, 'card')),
                ('bearing entry', lambda o: o['cards'][0].update(bearing=['bearing'])),
                ('statement', lambda o: o['cards'][0].update(statements=['statement']))):
            with self.subTest(name=name):
                output = copy.deepcopy(base)
                mutate(output)
                with self.assertRaises(ResearchError) as refused:
                    parts._validate_read(packet, output, dict(units=[]), route, plan)
                self.assertEqual(refused.exception.code, 'invalid_value_review')

    def test_ta9_size_refusals_explain_their_own_rule(self):
        route = build_route()
        unit = legacy.enumerate_units(fixtures.make_packet(1, 11), 'problem_result')[0]
        unit['record_identity'] = dict(id='x' * parts.UNIT_ROW_CHARACTERS)
        with self.subTest(rule='unit row'):
            with self.assertRaises(ResearchError) as refused:
                parts._describe_inventory_unit(route, unit)
            error = refused.exception
            self.assertEqual(error.code, 'review_parts_inventory_too_large')
            self.assertEqual(error.details['unit'], unit['handle'])
            self.assertEqual(error.details['limit'], parts.UNIT_ROW_CHARACTERS)
            self.assertGreater(error.details['characters'], error.details['limit'])
            self.assertNotEqual(error.message, parts.DELIVERY_INVALID_MESSAGE)
        with self.subTest(rule='group row'):
            packet, output, plan = read_case()
            packet[parts.GROUPS_KEY][0]['unit'] = 'x' * parts.CARD_GROUP_FIELDS_CHARACTERS
            output['cards'][0]['group'] = copy.deepcopy(packet[parts.GROUPS_KEY][0])
            with self.assertRaises(ResearchError) as refused:
                parts._validate_read(packet, output, {}, route, plan)
            self.assertEqual(refused.exception.code, 'invalid_value_review')
            self.assertEqual(refused.exception.message, parts.GROUP_ROW_LIMIT_MESSAGE)
        with self.subTest(rule='forecast'):
            with self.assertRaises(ResearchError) as refused:
                review_condensing.forecast_condensing(1, 100, 0)
            self.assertEqual(refused.exception.code, 'invalid_input')
            self.assertIn('whose largest entry has zero characters', refused.exception.message)
            self.assertIn('a positive integer of at most the room of a block', refused.exception.message)


class TurnPromptTests(unittest.TestCase):
    def test_ta5_each_v3_final_form_puts_its_turn_instructions_last(self):
        from research_harness import review_session_v4

        cases = (
            ('problem_result', 'result', 'problem_creation', 'Give each prior material objection an objection finding'),
            ('problem_slate', 'slate', 'problem_creation', 'request the original unit when a card cannot support a grade'),
            ('problem_adjudicator', 'adjudicator', 'problem_creation', 'A rubric objection cannot be not_binding.'),
            ('study_verification', 'verification', None, 'The soundness evidence includes a full-read link'),
            ('study_readiness', 'result', None, 'validity, scope, novelty, contribution, development and branches'),
            ('native_math', 'native_math', None, 'A proposal and a result approval also bind claim_digest'),
            ('context_repair', 'bar', 'problem_creation', 'gives a finding for each inherited review under its review_id'),
        )
        for adapter, role, orchestration, instruction in cases:
            base = dict(orchestration=orchestration, repair=adapter == 'context_repair')
            for form in FORMS['final']:
                with self.subTest(adapter=adapter, form=form):
                    header = dict(adapter=adapter, kind='final', protocol=parts.PROTOCOL, capacity={})
                    if form:
                        header['final_form'] = form
                    prompt = legacy.build_packet_prompt(role, base, {legacy.KEY: header})
                    descriptor = prompt['parameters']['base_prompt']
                    canonical = protocol.canonical_prompt(role, **descriptor['parameters'])
                    self.assertEqual(descriptor['rendered_prompt_hash'], canonical['rendered_prompt_hash'])
                    text = prompt['text']
                    self.assertNotIn(bar_sources.PROMPT, text)
                    self.assertLess(text.index(texts.PARTS_V3_COMMON), text.index(instruction))
                    turn_text = FORM_TEXT.get(form, texts.PARTS_V3_FINAL)
                    self.assertLess(text.index(instruction), text.index(turn_text))
                    self.assertTrue(text.endswith(turn_text))
                    if adapter == 'study_readiness':
                        self.assertLess(text.index(instruction), text.index(review_session_v4.STUDY_FORMS_PROMPT.lstrip()))
                        self.assertLess(text.index(review_session_v4.STUDY_FORMS_PROMPT.lstrip()), text.index(turn_text))

    def test_ta5_v2_final_keeps_its_excerpt_capacity_and_adapter_text(self):
        from review_parts_fixtures import make_packet, observe, plan

        packet = make_packet(2, 1800)
        capacity_values = dict(protocol=paragraphs.CAPACITY_NOTICE_PROTOCOL, kind='single',
                               output_tokens=128000, answer_bound=21500)
        base = dict(orchestration='problem_creation', excerpts=True, capacity=capacity_values,
                    problem_paragraphs=[paragraphs.CAPACITY_NOTICE_PROTOCOL])
        history, kinds = [], []
        wire = hashlib.sha256()
        for _ in range(12):
            turn = plan(packet, history)
            delivered = {key: value for key, value in turn.items() if key != '_test_inventory'}
            header = turn[legacy.KEY]
            self.assertEqual(header['protocol'], legacy.PROTOCOL)
            kinds.append(header['kind'])
            prompt = legacy.build_packet_prompt('result', base, delivered)
            request = protocol.transport.request_body(build_route(), prompt['text'], delivered, [], order=())
            wire.update(legacy.render_json([delivered, prompt, request]).encode())
            if header['kind'] == 'final':
                break
            history.append(observe(turn))
        self.assertEqual(kinds[-1], 'final')
        self.assertEqual(wire.hexdigest(), '7cafa1643853019be3428b3b1d60ad3625c36b671771da1e53b79ae1861ce63b')


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.artifacts = ArtifactStore(Path(directory.name))
        self.route = build_route()

    def test_ta10_admission_takes_composition_targets(self):
        history = [dict(id='earlier-review', review=dict(objections=[dict(id='prior-objection')]))]
        cases = (
            (dict(role='bar', protocol=bar_sources.PROTOCOL),
             ['prior-objection', 'earlier-review']),
            (dict(role='slate', protocol=protocol._PROBLEM_PROTOCOL, subject=dict(kind='problem'),
                  candidates=[dict(id='candidate-a')]),
             ['problem_statement', 'problem_openness', 'problem_value', 'problem_citations', 'stop',
              'candidate-a', 'prior-objection', 'earlier-review']),
        )
        for packet, expected_ids in cases:
            with self.subTest(role=packet['role']):
                packet.update(context_repair=dict(assignment_id='predecessor'), historical_reviews=history, evidence=[])
                admission = parts.admit_packet({}, self.artifacts, self.route, packet, 'context_repair')
                self.assertEqual(admission['state'], 'accept', admission)
                self.assertEqual([row['id'] for row in admission['targets']], expected_ids)

    def prepare_turn(self, logical, parameters):
        role = logical['role']
        value = dict(id='turn', role=role, context={})
        ordinary = protocol.canonical_prompt(role, **parameters)
        return parts.prepare_packet({}, self.artifacts, value, self.route, logical, ordinary, [],
                                    entry='output', base_prompt_parameters=parameters)

    def finalize_turn(self, role, packet, parameters):
        declaration = packet.pop(capacity.CAPACITY_REQUEST_KEY)
        packet.pop('digest')
        packet['digest'] = digest(packet)
        plan = capacity.read_chain_capacity_plan(self.artifacts, dict(capacity_plan=declaration['plan']))
        prompt = legacy.build_packet_prompt(role, parameters, packet)
        return plan, prompt

    def check_preflight(self, role, packet, plan, prompt):
        request = protocol.transport.request_body(self.route, prompt['text'], packet, [], order=())
        report = review_preflight.build_report({}, self.artifacts, self.route, role, prompt,
            packet, request, capacity_plan=plan, turn_kind=packet[legacy.KEY]['kind'])
        return review_preflight.check_request(report, request, packet=packet, artifacts=self.artifacts)

    def test_adjudication_reserves_target_labels_in_the_request(self):
        logical = fixtures.make_packet(0)
        logical.pop('material_objections')
        logical.update(role='adjudicator', protocol='exactory-focused-review-evidence-v2',
                       objection=dict(id='O1', claim='x' * 32000))
        base = dict(orchestration='problem_creation')
        packet = self.prepare_turn(logical, base)
        self.assertIsNotNone(packet)
        plan, prompt = self.finalize_turn('adjudicator', packet, base)
        self.check_preflight('adjudicator', packet, plan, prompt)
        header = packet[legacy.KEY]
        inventory = parts._read_value(self.artifacts, header['inventory'])
        sizes = parts.measure_turn_prompts('adjudicator', base, 'problem_adjudicator')
        target_size = capacity.measure_capacity_value(self.route, {'targets': header['targets']})
        for kind, frame in plan['frame'].items():
            bare = fields.describe_role_frame(self.route, 'adjudicator', inventory['frame_source'], [kind],
                                               unit_fields=frame['unit_fields'])[kind]
            self.assertEqual(frame['fields'][fields.FRAME_DESCRIPTOR_FIELD],
                             bare[fields.FRAME_DESCRIPTOR_FIELD] + sizes[kind] + target_size)

    def test_adjudication_oversized_objection_target_refuses_admission(self):
        logical = fixtures.make_packet(0)
        logical.pop('material_objections')
        logical.update(role='adjudicator', protocol='exactory-focused-review-evidence-v2',
                       objection=dict(id='O1', claim='x' * 900000))
        packet = self.prepare_turn(logical, dict(orchestration='problem_creation'))
        self.assertTrue(packet is None, 'Admission accepted a 900,000-character target label.')

    def test_adjudication_statement_target_refuses_a_route_below_the_floor(self):
        logical = fixtures.make_packet(0)
        logical.pop('material_objections')
        logical.update(role='adjudicator', protocol='exactory-focused-review-evidence-v2',
                       objection=dict(id='O', claim='A material objection.'))
        logical['problem_evidence']['claim']['statement'] = 'a' * 240000
        logical['problem_evidence_digest'] = digest(logical['problem_evidence'])
        describe = capacity.describe_route_capacity

        def tight(*args, **kwargs):
            limits = describe(*args, **kwargs)
            limits['input_text_characters']['value'] = 180000
            return limits

        with patch.object(capacity, 'describe_route_capacity', side_effect=tight):
            packet = self.prepare_turn(logical, dict(orchestration='problem_creation'))
            self.assertTrue(packet is None, 'Admission exceeded the tight route with a 240,000-character label.')

    def test_the_reserve_counts_the_paragraph_versions_that_a_turn_of_its_kind_selects(self):
        """Row P11 of the second writer pass: a read, combine or check turn of a slate series selects no version of the
        slate places paragraph, and a final, page or closing turn its version 2."""
        base = dict(orchestration='problem_creation')
        sizes = parts.measure_turn_prompts('slate', base, 'problem_slate')
        header = dict(protocol=parts.PROTOCOL, adapter='problem_slate', capacity={})
        reserve = {kind: sizes[kind] - max(len(legacy.build_packet_prompt('slate', base, {legacy.KEY: dict(
            header, kind=kind, **(dict(final_form=form) if form else {}))})['text']) for form in FORMS[kind])
            for kind in FORMS}
        self.assertEqual(reserve['read'], reserve['combine'])
        self.assertEqual(reserve['read'], reserve['check'])
        self.assertEqual(reserve['final'] - reserve['read'], len(paragraphs.SLATE_PLACES_PROMPT_V2))

    def admit_result(self, objections):
        packet = fixtures.make_packet(2, 9000)
        packet['material_objections'] = [dict(id='objection-%d' % index, claim='%d ' % index + 'x' * 180,
            version_evidence=[dict(id='evidence-%d' % index, reason='A recorded material concern.')])
            for index in range(objections)]
        prompts = parts.measure_turn_prompts('result', dict(orchestration='problem_creation'), 'problem_result')
        return prompts, parts.admit_packet({}, self.artifacts, self.route, packet, 'problem_result',
                                           prompt_characters=prompts)

    def test_result_target_table_larger_than_a_frame_is_refused_on_every_route(self):
        prompts, admitted = self.admit_result(200)
        self.assertEqual(admitted['state'], 'accept', admitted)
        table_characters = capacity.measure_capacity_value(self.route, {'targets': admitted['targets']})
        for kind, frame in admitted['plan']['frame'].items():
            self.assertGreaterEqual(frame['bounded_fields'][fields.FRAME_DESCRIPTOR_FIELD],
                                    table_characters + prompts[kind])
        describe = capacity.describe_route_capacity

        def widen(*args, **kwargs):
            limits = describe(*args, **kwargs)
            for name in ('input_text_characters', 'input_tokens', 'input_characters'):
                if limits[name]['value'] is not None:
                    limits[name]['value'] = 50000000
            return limits

        # 900 objections and the 18 fixed targets: the table alone passes the frame maximum, so no route admits it.
        for wide in (False, True):
            with self.subTest(wide=wide):
                with patch.object(capacity, 'describe_route_capacity', side_effect=widen if wide else describe):
                    refused = self.admit_result(900)[1]
                self.assertEqual((refused['state'], refused['reason']), ('refuse', 'review_route_capacity_below_floor'))
                details = refused['details']
                self.assertEqual((details['kind'], details['frame_maximum_characters'], details['target_table']['targets']),
                                 ('read', capacity.FRAME_MAXIMUM_CHARACTERS, 918))
                self.assertGreater(details['target_table']['characters'], capacity.FRAME_MAXIMUM_CHARACTERS)
                self.assertLess(details['room'], details['minimum'])

    def test_refusal_for_a_short_read_reserve_names_the_reserve_and_the_target_table(self):
        accepted = self.admit_result(2)[1]
        table_characters = capacity.measure_capacity_value(self.route, {'targets': accepted['targets']})
        plan_chain = capacity.plan_chain_capacity

        def shorten(*args, **kwargs):
            plan = plan_chain(*args, **kwargs)
            plan['frame']['read']['bounded_fields']['$seam'] -= 1
            return plan

        with patch.object(capacity, 'plan_chain_capacity', side_effect=shorten):
            refused = self.admit_result(2)[1]
        self.assertEqual((refused['state'], refused['reason']), ('refuse', 'review_route_capacity_below_floor'))
        self.assertEqual(refused['details'], dict(kind='read', field='$seam', room=parts.UNIT_ROW_CHARACTERS - 1,
            minimum=parts.UNIT_ROW_CHARACTERS, target_table=dict(targets=20, characters=table_characters),
            frame_maximum_characters=capacity.FRAME_MAXIMUM_CHARACTERS))

    def test_equal_frame_values_resolve_to_the_same_requestable_unit(self):
        base = dict(orchestration='problem_creation')
        for from_record in (False, True):
            with self.subTest(from_record=from_record):
                logical = fixtures.make_packet(1, 100)
                evidence = logical['problem_evidence']
                value = evidence['openness_searches'][0] if from_record else {'schema_version': 2}
                evidence['openness'] = {'minimum': value}
                evidence['openness_minimum'] = value
                packet = self.prepare_turn(logical, base)
                header = packet[legacy.KEY]
                inventory = parts._read_value(self.artifacts, header['inventory'])
                first = packet['problem_evidence']['openness']['minimum']
                second = packet['problem_evidence']['openness_minimum']
                self.assertEqual(first, second)
                if from_record:
                    self.assertEqual(first['handle'], inventory['units'][0]['handle'])
                    self.assertEqual(inventory['units'][0]['kind'], 'record')
                for descriptor in (first, second):
                    handle = descriptor['handle']
                    scopes = parts._resolve_requests(inventory, dict(unit_requests=[dict(unit=handle)]))
                    delivery = parts._deliver_requested_units(self.route, inventory, scopes, 15000)
                    self.assertEqual(delivery[0]['unit'], handle)
                    self.assertEqual(delivery[0]['text'], legacy.render_json(value))

    def test_tight_frame_never_delivers_single_task_capacity_as_a_unit(self):
        logical = fixtures.make_packet(1, 16000)
        logical['route_capacity'] = dict(protocol=paragraphs.CAPACITY_NOTICE_PROTOCOL, kind='single',
                                         output_tokens=128000, answer_bound=21500)
        describe = capacity.describe_route_capacity

        def tight(*args, **kwargs):
            limits = describe(*args, **kwargs)
            limits['input_text_characters']['value'] = 180000
            return limits

        base = dict(orchestration='problem_creation', **paragraphs.select_prompt_parameters('result', logical))
        with patch.object(capacity, 'describe_route_capacity', side_effect=tight):
            packet = self.prepare_turn(logical, base)
            self.assertIsNotNone(packet)
            plan, prompt = self.finalize_turn('result', packet, base)
            self.check_preflight('result', packet, plan, prompt)
        header = packet[legacy.KEY]
        self.assertEqual(header['kind'], 'read')
        inventory = parts._read_value(self.artifacts, header['inventory'])
        self.assertNotIn('route_capacity', packet)
        self.assertNotIn('route_capacity', inventory['frame_source'])
        self.assertTrue(all(row.get('frame_path') != ['route_capacity'] and
                            paragraphs.CAPACITY_NOTICE_PROTOCOL not in row['text'] for row in inventory['units']))
        with self.assertRaises(ResearchError):
            parts._resolve_requests(inventory, dict(unit_requests=[dict(unit='route_capacity')]))

    def test_readiness_repair_final_uses_the_bound_adapter_and_exhaustion_cause(self):
        for exhaustion in (False, True):
            with self.subTest(exhaustion=exhaustion):
                repair = dict(assignment_id='predecessor')
                if exhaustion:
                    repair['exhaustion'] = dict(protocol='exactory-review-context-repair-exhaustion-v1')
                # A readiness packet carries the support contract and the assessor that a v3 final binds.
                logical = dict(role='result', protocol=bar_sources.PROTOCOL,
                               dossier={}, readiness=dict(kind='readiness'),
                               support_contract=dict(candidate_digest='c' * 64),
                               assessor=dict(id='independent-reader', kind='agent'),
                               context_repair=repair, historical_reviews=[], evidence=[])
                base = {'repair': True}
                packet = self.prepare_turn(logical, base)
                plan, prompt = self.finalize_turn('result', packet, base)
                header = packet[legacy.KEY]
                self.assertEqual(header['kind'], 'final')
                self.assertIn('validity, scope, novelty, contribution, development and branches', prompt['text'])
                self.assertIn('Current packet fields can supply units as well', prompt['text'])
                self.assertNotIn('exhausted output capacity', prompt['text'])
                self.assertEqual('The recorded cause is context_repair.exhaustion.' in prompt['text'], exhaustion)
                assignment = dict(role='result', reassessment_required=True, prompt=prompt, review_parts=header)
                self.assertTrue(protocol._prompt_matches(assignment, packet=packet))
                if exhaustion:
                    changed = copy.deepcopy(packet)
                    changed[legacy.KEY]['repair_binding']['exhaustion'] = False
                    self.assertFalse(protocol._prompt_matches(assignment, packet=changed))
                self.check_preflight('result', packet, plan, prompt)


class RecordedTurnTests(PartsResultCase):
    output_tokens = 128000
    start_grouped_series = fixtures.RecordedSeriesTests.start_grouped_series
    cap_attempt = fixtures.RecordedSeriesTests.cap_attempt
    record_final = fixtures.RecordedSeriesTests.record_final
    output = fixtures.RecordedSeriesTests.output

    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()
        selected = patch.object(legacy, 'NEW_SERIES_PROTOCOLS', (parts.PROTOCOL, legacy.PROTOCOL))
        selected.start()
        self.addCleanup(selected.stop)
        original = capacity.plan_chain_capacity

        def build_request_plan(*args, **kwargs):
            kwargs['final_pages'] = False
            return original(*args, **kwargs)

        pinned = patch.object(capacity, 'plan_chain_capacity', side_effect=build_request_plan)
        pinned.start()
        self.addCleanup(pinned.stop)

    def assert_packet_paragraphs(self, assignment):
        packet = self.packet(assignment)
        prompt = assignment['prompt']
        self.assertNotIn(paragraphs.CAPACITY_NOTICE_KEY, set(packet))
        self.assertNotIn(paragraphs.CAPACITY_PARAMETER, prompt['parameters'])
        self.assertFalse(paragraphs.has_capacity_paragraph(prompt['parameters'].get(paragraphs.PARAMETER, [])))
        self.assertTrue(paragraphs.matches_packet_paragraphs(prompt, assignment['role'], packet))

    def test_ta1_ta4_stored_prompt_reserves_and_second_read(self):
        """Reserve all seven forms and drive the authenticated late read and its replacement."""
        from research_harness import review_single_request

        self.oversized(count=2, characters=9000, limit=1300000)
        levels = capacity.describe_material_levels

        def short_read(kind, **kwargs):
            return (15000,) if kind == 'read' else levels(kind, **kwargs)

        with patch.object(capacity, 'describe_material_levels', side_effect=short_read), \
                patch.object(review_single_request, 'describe_task_entry', return_value='task'):
            first = self.assign()
            packet = self.packet(first)
            plan = capacity.read_chain_capacity_plan(self.case.artifacts, first)
            inventory = parts._read_value(self.case.artifacts, packet[legacy.KEY]['inventory'])
            logical = legacy.read_logical_packet(self.case.artifacts, packet[legacy.KEY])
            self.assertIn(logical['own_bars'][0], logical['bars'])
            for name in ('bar_ids', 'bars', 'own_bars'):
                self.assertTrue(logical[name])
                self.assertEqual(packet[name], [])
            records = self.case.read_records()
            route = records['review_route'][first['route_id']]
            base = first['prompt']['parameters']['base_prompt']['parameters']
            target_charge = capacity.measure_capacity_value(route, {'targets': packet[legacy.KEY]['targets']})
            eligible = ('objection-carry', 'accounts', 'account-probes', 'changes-if-resolved',
                        'replacement', 'delivery-notice')
            paragraph_bound = notice.NOTICE_PARAGRAPH_CHARACTERS + sum(
                max(len(text) for _, text in paragraph.versions) for paragraph in paragraphs.PARAGRAPHS
                if paragraph.name in eligible)

            first_result = self.invoke(first)
            self.assertNotIn('output_refusal', first_result)
            late = dict(id='search-late', queries=['Independent new observation'], reason='A new observation')
            self.case.put('literature_search', late['id'], late)
            self.case.expected['openness_searches'].append(dict(search=late, sources=[]))
            self.case.expected['value']['accounts'] = {'answer': 'none'}
            second = self.assign('late-read', first['id'])
            second_packet = self.packet(second)
            self.assertEqual(second_packet[legacy.KEY]['kind'], 'read')
            self.assertEqual(second['capacity_plan'], first['capacity_plan'])
            self.assertGreater(len(second['prompt']['text']), len(first['prompt']['text']) + 10000)
            self.assertIs(second['prompt']['parameters']['base_prompt']['parameters']['excerpts'], False)
            self.assertNotIn('Excerpt rules of this session', second['prompt']['text'])

            for kind, forms in FORMS.items():
                frame = plan['frame'][kind]
                bare_frame = fields.describe_role_frame(route, first['role'], inventory['frame_source'],
                                                     [kind], unit_fields=frame['unit_fields'])[kind]
                reserve = frame['fields'][fields.FRAME_DESCRIPTOR_FIELD] - bare_frame[fields.FRAME_DESCRIPTOR_FIELD]
                clean, delivered = [], []
                for form in forms:
                    header = dict(packet[legacy.KEY], kind=kind)
                    if form:
                        header['final_form'] = form
                    clean.append(len(legacy.build_packet_prompt('result', base, {legacy.KEY: header})['text']))
                    for refusal in (None, 'whole', 'bounded'):
                        candidate = dict(second_packet, review_parts=header)
                        if refusal:
                            candidate['output_refusal'] = {'form': refusal}
                        delivered.append(len(legacy.build_packet_prompt('result', base, candidate)['text']))
                with self.subTest(kind=kind):
                    self.assertGreaterEqual(reserve - target_charge, max(delivered))
                    self.assertLessEqual(reserve, target_charge + max(clean) + paragraph_bound)

            bad = self.output(second)
            bad['cards'].clear()
            result = self.invoke(second, bad)
            self.assertEqual(result['output_refusal']['code'], 'invalid_value_review')
            payload = {key: second[key] for key in ('role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id')}
            payload.update(id='late-read-replacement', context=dict(second['context'],
                output_refusal=dict(assignment_id=second['id'])))
            build_notice = notice.build_refusal_notice

            def require_bounded(*args, **kwargs):
                return build_notice(*args, **dict(kwargs, form='bounded'))

            with patch.object(notice, 'build_refusal_notice', side_effect=require_bounded):
                replacement = self.case.mutate(protocol.record_assignment, payload)['result']
            replacement_packet = self.packet(replacement)
            self.assertEqual(replacement_packet['output_refusal']['form'], 'bounded')
            self.assertEqual(replacement['capacity_plan'], first['capacity_plan'])
            self.assertEqual(replacement['prompt'], legacy.build_packet_prompt('result', base, replacement_packet))
            self.assert_packet_paragraphs(replacement)
            records = self.case.read_records()
            request = protocol.build_assignment_request(records, self.case.artifacts, replacement)
            report = review_preflight.build_report(records, self.case.artifacts, route, 'result',
                replacement['prompt'], replacement_packet, request, capacity_plan=plan, turn_kind='read')
            review_preflight.check_request(report, request, packet=replacement_packet, artifacts=self.case.artifacts)
            self.assertNotIn('output_refusal', self.invoke(replacement))
            self.assertEqual(capacity.read_chain_capacity_plan(self.case.artifacts, first), plan)

    def test_ta3_single_output_replacement_uses_its_own_packet_paragraphs(self):
        """Drive matching first-read replacements on ordinary and problem-area packets."""
        self.oversized(count=1, characters=2000, limit=1300000)
        for with_areas in (False, True):
            with self.subTest(with_areas=with_areas):
                if with_areas:
                    original = protocol.build_packet

                    def build(*args, **kwargs):
                        packet = original(*args, **kwargs)
                        if args[3] == 'result':
                            packet['problem_areas'] = [dict(area='math.CO', reason='The problem counts finite objects.')]
                        return packet

                    with patch.object(protocol, 'build_packet', side_effect=build):
                        first = self.assign('single-with-areas', reviewer='second')
                else:
                    first = self.assign('single')
                self.assertNotIn(legacy.KEY, self.packet(first))
                self.cap_attempt(first, first['id'] + '-at-cap')
                recovery = capacity.plan_capacity_recovery(self.case.read_records(), self.case.artifacts,
                                                          first, 'output_over_cap')
                self.assertEqual(recovery['kind'], 'replace', recovery)
                replacement = self.case.mutate(protocol.record_assignment, recovery['payload'])['result']
                self.assertEqual(self.packet(replacement)[legacy.KEY]['protocol'], parts.PROTOCOL)
                with self.subTest(check='review-run'):
                    self.assertNotIn('output_refusal', self.invoke(replacement))
                with self.subTest(check='paragraphs'):
                    self.assert_packet_paragraphs(replacement)

    def test_ta3_ta6_driven_turns_refusal_and_frame_unit(self):
        """The request and citation run after the read, combine, check and refusal replacement."""
        first = self.start_grouped_series()
        packet = self.packet(first)
        handle = packet['problem_evidence']['openness']['minimum']['handle']
        bad = self.output(first)
        bad['cards'].pop()
        refusal = self.invoke(first, bad)
        self.assertEqual(refusal['output_refusal']['code'], 'invalid_value_review')
        payload = dict(id='corrected-read', role=first['role'], route_id=first['route_id'], dossier_id=None,
                       reviewer_id=first['reviewer_id'], author_id=first['author_id'],
                       context=dict(output_refusal=dict(assignment_id=first['id'])))
        assignment = self.case.mutate(protocol.record_assignment, payload)['result']
        self.assertIn('output_refusal', self.packet(assignment))
        seen = set()
        for index in range(12):
            packet = self.packet(assignment)
            header = packet[legacy.KEY]
            kind = header['kind']
            seen.add(kind)
            with self.subTest(rule='TA3', kind=kind, turn=index):
                self.assert_packet_paragraphs(assignment)
            if kind == 'final':
                break
            output = self.output(assignment)
            if kind == 'combine' and header.get('mode') == 'scientific' and 'check' not in seen:
                unit = self.packet(first)[parts.GROUPS_KEY][0]['first']['unit']
                output['combinations'] = [dict(units=[unit], question='Is this evidence relevant?',
                    view='The evidence bears on the stated domain.', read=[dict(unit=unit, start=0, end=10)])]
            elif kind == 'check':
                output['findings'] = [dict(combination=name, finding='The supplied evidence is relevant.',
                    effect='supports', statements=[]) for name in header['check_combinations']]
            receipt = self.invoke(assignment, output)
            self.assertNotIn('output_refusal', receipt, receipt)
            assignment = self.assign('driven-turn-%d' % index, assignment['id'])
        self.assertEqual(seen, set(legacy.KINDS))
        with self.subTest(rule='TA6', check='frame'):
            self.assertRegex(handle, r'^u[0-9]+$')
        receipt = self.invoke(assignment, dict(kind='requests', unit_requests=[dict(unit=handle)], source_requests=[]))
        with self.subTest(rule='TA6', check='request'):
            self.assertNotIn('output_refusal', receipt, receipt.get('output_refusal'))
        if 'output_refusal' in receipt:
            return
        answered = self.assign('requested-frame-unit', assignment['id'])
        self.assertEqual(self.packet(answered)['review_parts_requests'][0]['unit'], handle)
        output = self.final_output()
        output['value']['evidence'].append(dict(ref=handle))
        self.record_final(answered, output)


class PromptFloorTests(unittest.TestCase):
    def test_ta2_one_character_below_the_read_floor_refuses(self):
        from test_review_output_profile import build_row

        logical = fixtures.make_packet(1, 16000)
        logical['problem_areas'] = [dict(area='math.CO', reason='The problem counts finite objects.')]
        logical['route_capacity'] = dict(protocol=paragraphs.CAPACITY_NOTICE_PROTOCOL, kind='single',
                                         output_tokens=128000, answer_bound=21500)
        clean = dict(orchestration='problem_creation', excerpts=False)
        decorated = dict(clean, **paragraphs.select_prompt_parameters('result', logical))
        self.assertIn(paragraphs.CAPACITY_PARAMETER, decorated)
        self.assertIn('areas', [row.name for row in paragraphs.PARAGRAPHS
                                if any(version in decorated[paragraphs.PARAMETER] for version, _ in row.versions)])
        describe = capacity.describe_route_capacity
        route = build_route()

        def prepare_at(artifacts, room, parameters):
            def limited(*args, **kwargs):
                limits = describe(*args, **kwargs)
                limits['input_text_characters']['value'] = room
                return limits

            with patch.object(capacity, 'describe_route_capacity', side_effect=limited):
                ordinary = protocol.canonical_prompt('result', **parameters)
                return parts.prepare_packet({}, artifacts, dict(id='tight-route', role='result', context={}),
                    route, logical, ordinary, [], entry='output', base_prompt_parameters=parameters)

        row = dict(build_row(level=None, kind='read', thinking=1), largest_output_tokens=12)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(capacity, 'read_profile_priors', return_value=[row]):
            artifacts = ArtifactStore(Path(directory))
            low, high = 100000, 250000
            self.assertIsNone(prepare_at(artifacts, low, clean))
            self.assertIsNotNone(prepare_at(artifacts, high, clean))
            while low + 1 < high:
                middle = (low + high) // 2
                if prepare_at(artifacts, middle, clean) is None:
                    low = middle
                else:
                    high = middle
            self.assertIsNone(prepare_at(artifacts, high - 1, decorated))
            packet = prepare_at(artifacts, high, decorated)
            self.assertIsNotNone(packet)
            self.assertEqual(packet['problem_areas'], [])
            self.assertNotIn(paragraphs.CAPACITY_NOTICE_KEY, packet)
            prompt = legacy.build_packet_prompt('result', decorated, packet)
            self.assertEqual(prompt, legacy.build_packet_prompt('result', clean, packet))

    def test_serving_route_estimate_uses_the_finalized_delivery(self):
        from research_harness import review_single_request

        logical = fixtures.make_packet(1, 16000)
        logical['problem_areas'] = [dict(area='math.CO', reason='The problem counts finite objects.')]
        role = 'result'
        base = dict(orchestration='problem_creation', excerpts=True,
                    **paragraphs.select_prompt_parameters(role, logical))
        ordinary = protocol.canonical_prompt(role, **base)
        route = build_route()
        value = dict(id='serving-assignment', role=role, context={})
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(review_single_request, 'describe_task_entry', return_value='task'), \
                patch.object(legacy, 'NEW_SERIES_PROTOCOLS', (parts.PROTOCOL, legacy.PROTOCOL)):
            artifacts = ArtifactStore(Path(directory))
            actual = capacity._build_serving_parts({}, artifacts, value, route, logical, ordinary)
            packet = parts.prepare_packet({}, artifacts, value, route, logical, ordinary, [],
                                         base_prompt_parameters=base)
            packet.pop(capacity.CAPACITY_REQUEST_KEY)
            packet.pop('digest')
            packet['digest'] = digest(packet)
            prompt = legacy.build_packet_prompt(role, base, packet)
            expected = protocol.transport.request_body(route, prompt['text'], packet, [])
            self.assertEqual(actual, expected)
