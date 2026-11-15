"""Ruling 38 retains capacity and refusal replacements in one exhausted position."""

import copy
import unittest
from unittest.mock import patch

from codex_route_fixtures import build_codex_response, invoke_codex, limit_codex_input, probe_codex_route
from review_parts_fixtures import PartsResultCase, make_output
from research_harness import review_context_repair as repair, review_failure_class as failures
from research_harness import review_parts as parts, review_protocol as protocol, review_refusal as refusal
from research_harness import research_decisions as decisions
from research_harness.errors import ResearchError
from research_harness.evidence import digest


class MixedCapacityPositionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = PartsResultCase()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.oversized(count=6, characters=24000, room=110000)
        self.case, self.artifacts = self.fixture.case, self.fixture.case.artifacts

    def overflow(self, assignment):
        def respond(route, request, credential):
            response = build_codex_response(route, request, {})
            response['status'] = 'incomplete'
            response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
            return response
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            self.case.mutate(protocol.invoke_assignment,
                dict(id='overflow-' + assignment['id'], assignment_id=assignment['id']), credential='fixture')

    def retain_capacity_member(self, previous, identifier):
        # Reconstruct the retained capacity-record shape using the real parts reducer.
        # Admission of these stored rows is covered by the capacity unit's journeys.
        records = self.case.read_records()
        packet = self.fixture.packet(previous)
        header = packet[parts.KEY]
        logical = parts.read_logical_packet(self.artifacts, header)
        parameters = copy.deepcopy(previous['prompt']['parameters']['base_prompt']['parameters'])
        from research_harness import review_prompt_paragraphs as paragraphs
        parameters.update(paragraphs.read_paragraph_parameters(previous['prompt']))
        prompt = protocol.canonical_prompt(previous['role'], **parameters)
        value = {key: copy.deepcopy(previous[key]) for key in
                 ('role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id', 'context')}
        value['id'] = identifier
        candidate = parts.prepare_packet(records, self.artifacts, value, records['review_route']['route'],
            logical, prompt, [], base_prompt_parameters=parameters, slot_id=previous['slot_id'],
            capacity_replacement=dict(unit_characters=header['unit_characters'], over_cap=True),
            refusal_notice=packet.get('output_refusal'))
        self.assertLess(candidate[parts.KEY]['unit_characters'], header['unit_characters'])
        candidate = protocol.prepare_capacity_notice(records, records['review_route']['route'],
                                                    previous['role'], candidate)
        prompt = parts.build_turn_prompt(previous['role'], parameters, candidate[parts.KEY])
        selected = paragraphs.select_prompt_parameters(previous['role'], candidate)
        base_prompt = paragraphs.strip_prompt_paragraphs(prompt, previous['role'])
        recorded = paragraphs.read_paragraph_parameters(prompt)
        prompt = paragraphs.append_prompt_paragraphs(base_prompt, previous['role'],
            recorded.get(paragraphs.CARRY_PARAMETER), selected.get(paragraphs.PARAMETER),
            selected.get(paragraphs.CAPACITY_PARAMETER))
        saved = copy.deepcopy(previous)
        saved.update(id=identifier, capacity_replacement_of=previous['id'],
            packet=protocol._put_json(self.artifacts, candidate), packet_digest=candidate['digest'],
            prompt=prompt, prompt_hash=prompt['rendered_prompt_hash'],
            recorded_revision=self.case.store.revision + 1, session_id=identifier,
            review_parts_plan=parts.describe_plan(candidate))
        saved[parts.KEY] = {key: candidate[parts.KEY][key] for key in previous[parts.KEY]}
        self.case.put('review_assignment', identifier, saved)
        return saved

    def refuse(self, assignment, *, at_output_limit=False):
        output = make_output(self.fixture.packet(assignment))
        output.pop('kind')
        if at_output_limit:
            def respond(route, request, credential):
                response = build_codex_response(route, request, output)
                response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
                return response
            with patch.object(protocol.transport, 'send_request', side_effect=respond):
                response = self.case.mutate(protocol.invoke_assignment,
                    dict(id='attempt-' + assignment['id'], assignment_id=assignment['id']), credential='fixture')
        else:
            response = invoke_codex(self.case, assignment['id'], output)
        self.assertIn('output_refusal', response)
        return output

    def replace_refusal(self, previous, identifier):
        from research_harness.review_continuation import plan_next
        plan = plan_next(self.case.read_records(), self.artifacts, previous['id'])
        self.assertEqual(plan['state'], 'replace', plan)
        self.assertEqual(plan['payload']['context']['output_refusal'], dict(assignment_id=previous['id']))
        return self.case.mutate(protocol.record_assignment, dict(plan['payload'], id=identifier))['result']

    def test_capacity_replacement_of_a_refused_read_keeps_its_frozen_notice(self):
        self.check_refused_read_capacity_notice(continued=True)

    def test_capacity_replacement_of_a_refused_initial_read_keeps_its_frozen_notice(self):
        self.check_refused_read_capacity_notice(continued=False)

    def check_refused_read_capacity_notice(self, *, continued):
        from research_harness.review_continuation import plan_next
        from research_harness.review_problem_parts import _measure
        from research_harness.review_refusal_replacement import validate_replacement_origin
        from research_harness.review_route_capacity import measure_request

        for form in ('whole', 'bounded'):
            with self.subTest(form=form):
                fixture = PartsResultCase()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                fixture.oversized(count=5, characters=30000, room=65000)
                case, artifacts = fixture.case, fixture.case.artifacts
                seed = None
                if continued:
                    seed = fixture.assign('seed-' + form)
                    fixture.invoke(seed, make_output(fixture.packet(seed)))
                first = fixture.assign('first-' + form, seed['id'] if seed is not None else None)
                original_slot = repair.slot_id(case.read_records(), first)
                output = make_output(fixture.packet(first))
                output.pop('kind')
                if form == 'bounded':
                    output['synthetic_refusal_context'] = 'x' * 100000
                fixture.invoke(first, output)
                records = case.read_records()
                request = protocol.build_assignment_request(records, artifacts, first)
                extra_room = 50000 if form == 'bounded' else 10000
                limit_codex_input(fixture, _measure(records['review_route']['route'], request) + extra_room)
                refused_plan = plan_next(case.read_records(), artifacts, first['id'])
                self.assertEqual(refused_plan['state'], 'replace', refused_plan)
                refused = case.mutate(protocol.record_assignment,
                    dict(refused_plan['payload'], id='refusal-' + form))['result']
                refused_packet = fixture.packet(refused)
                notice = copy.deepcopy(refused_packet['output_refusal'])
                self.assertEqual(notice['form'], form)
                self.assertNotEqual(refused_packet[parts.KEY].get('entry'), 'output')
                frozen = artifacts.read(refused['packet'])
                before = protocol.build_assignment_request(case.read_records(), artifacts, refused)

                def overflow(route, request, credential):
                    response = build_codex_response(route, request, {})
                    response['status'] = 'incomplete'
                    response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
                    return response

                with patch.object(protocol.transport, 'send_request', side_effect=overflow):
                    case.mutate(protocol.invoke_assignment,
                        dict(id='overflow-' + form, assignment_id=refused['id']), credential='fixture')
                capacity_plan = plan_next(case.read_records(), artifacts, first['id'])
                self.assertEqual(capacity_plan['state'], 'replace', capacity_plan)
                self.assertEqual(capacity_plan['payload']['context'],
                                 dict(capacity_replacement=refused['id']))
                following = case.mutate(protocol.record_assignment,
                    dict(capacity_plan['payload'], id='capacity-' + form))['result']
                records = case.read_records()
                packet = fixture.packet(following)
                self.assertEqual(packet.get('output_refusal'), notice)
                self.assertEqual(following['capacity_replacement_of'], refused['id'])
                self.assertEqual(following['context'], refused['context'])
                self.assertEqual(repair.slot_id(records, following), original_slot)
                self.assertLess(packet[parts.KEY]['unit_characters'],
                                refused_packet[parts.KEY]['unit_characters'])
                validate_replacement_origin(records, artifacts, following)
                request = protocol.build_assignment_request(records, artifacts, following)
                self.assertTrue(measure_request(records['review_route']['route'], request,
                    records=records, artifacts=artifacts)['fits'])
                fixture.invoke(following, make_output(packet))
                self.assertTrue(protocol.assignment_state(case.read_records(), artifacts, following['id'])['ready'])
                self.assertEqual(artifacts.read(refused['packet']), frozen)
                self.assertEqual(protocol.build_assignment_request(case.read_records(), artifacts, refused), before)

    def test_capacity_replacement_of_a_refused_repair_read_keeps_its_frozen_notice(self):
        from research_harness.review_continuation import plan_next
        from research_harness.review_problem_parts import _measure
        from research_harness.review_refusal_replacement import validate_replacement_origin
        from research_harness.review_route_capacity import measure_request
        from test_review_parts_repair_journey import ResultRepairJourneyTests

        fixture = ResultRepairJourneyTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        case, artifacts = fixture.case, fixture.case.artifacts
        ordinary = protocol.build_packet(case.read_records(), artifacts, None, 'result', 'first')
        route = case.read_records()['review_route']['route']
        prompt = protocol.canonical_prompt('result', orchestration='problem_creation', excerpts=True,
            review_parts=dict(adapter='context_repair', kind='read'))
        measured = _measure(route, protocol.transport.request_body(route, prompt['text'], ordinary, []))
        fixture.oversized(count=12, characters=measured * 3, limit=measured * 12)
        first = fixture.assign('original')
        fixture.invoke(first)
        second = fixture.assign('second', first['id'])
        fixture.invoke(second)
        # The repair carries its ancestors' frames as well as their material.
        # Leave room for that frame, its refusal notice and a smaller read.
        limit_codex_input(fixture, measured * 48)
        binding = fixture.contaminate(second)
        repaired = fixture.repair_assignment(second, binding)
        original_slot = repair.slot_id(case.read_records(), repaired)
        repair_packet = fixture.packet(repaired)
        self.assertEqual((repair_packet[parts.KEY]['adapter'], parts.find_kind(repair_packet)),
                         ('context_repair', 'read'))
        bad = make_output(repair_packet)
        bad.pop('kind')
        fixture.invoke(repaired, bad)
        refused_plan = plan_next(case.read_records(), artifacts, repaired['id'])
        self.assertEqual(refused_plan['state'], 'replace', refused_plan)
        refused = case.mutate(protocol.record_assignment,
            dict(refused_plan['payload'], id='refused-repair'))['result']
        refused_packet = fixture.packet(refused)
        notice = copy.deepcopy(refused_packet['output_refusal'])
        self.assertEqual(notice['form'], 'whole')
        frozen = artifacts.read(refused['packet'])

        def overflow(route, request, credential):
            response = build_codex_response(route, request, {})
            response['status'] = 'incomplete'
            response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
            return response

        with patch.object(protocol.transport, 'send_request', side_effect=overflow):
            case.mutate(protocol.invoke_assignment,
                dict(id='overflow-repair', assignment_id=refused['id']), credential='fixture')
        capacity_plan = plan_next(case.read_records(), artifacts, repaired['id'])
        self.assertEqual(capacity_plan['state'], 'replace', capacity_plan)
        following = case.mutate(protocol.record_assignment,
            dict(capacity_plan['payload'], id='capacity-repair'))['result']
        records = case.read_records()
        packet = fixture.packet(following)
        self.assertEqual(packet.get('output_refusal'), notice)
        self.assertEqual(following['capacity_replacement_of'], refused['id'])
        self.assertEqual(following['context'], refused['context'])
        self.assertEqual(repair.slot_id(records, following), original_slot)
        self.assertLess(packet[parts.KEY]['unit_characters'], refused_packet[parts.KEY]['unit_characters'])
        validate_replacement_origin(records, artifacts, following)
        request = protocol.build_assignment_request(records, artifacts, following)
        self.assertTrue(measure_request(records['review_route'][following['route_id']], request,
            records=records, artifacts=artifacts)['fits'])
        fixture.invoke(following, make_output(packet))
        self.assertTrue(protocol.assignment_state(case.read_records(), artifacts, following['id'])['ready'])
        self.assertEqual(artifacts.read(refused['packet']), frozen)

    def build_mixed_position(self, *, extra_capacity=False, refused_capacity_source=False):
        first = self.fixture.assign('A')
        self.assertEqual(parts.find_kind(self.fixture.packet(first)), 'read')
        self.overflow(first)
        second = self.retain_capacity_member(first, 'B')
        self.refuse(second)
        third = self.replace_refusal(second, 'C')
        if extra_capacity:
            if refused_capacity_source:
                self.refuse(third, at_output_limit=True)
            else:
                self.overflow(third)
            fourth = self.retain_capacity_member(third, 'E')
            self.assertEqual(fourth['context']['output_refusal'], dict(assignment_id='B'))
            self.refuse(fourth)
        else:
            fourth = third
            self.refuse(third)
        final = self.replace_refusal(fourth, 'D')
        self.refuse(final)
        return final

    def build_repair_payload(self, head):
        from research_harness.review_continuation import plan_next
        plan = plan_next(self.case.read_records(), self.artifacts, head['id'])
        self.assertEqual((plan['state'], plan['command']), ('handoff', 'review-assignment'))
        self.assertEqual(plan['details']['step']['id'], 2)
        payload = copy.deepcopy(plan['details']['step']['payload'])
        payload.update(id='repair', route_id='route')
        payload['context']['context_repair'].update(probe_id='repair-probe',
            reason='Reassess the exhausted position with every retained observation.')
        return payload

    def check_mixed_repair(self, *, extra_capacity, refused_capacity_source=False):
        head = self.build_mixed_position(extra_capacity=extra_capacity,
                                         refused_capacity_source=refused_capacity_source)
        records = self.case.read_records()
        expected_position = ['A', 'B', 'C', 'E', 'D'] if extra_capacity else ['A', 'B', 'C', 'D']
        expected_refused = ['B', 'E', 'D'] if extra_capacity else ['B', 'C', 'D']
        if refused_capacity_source:
            expected_refused = ['B', 'C', 'E', 'D']
        self.assertEqual(failures.count_position(records, self.artifacts, head)['replacements'],
                         dict(capacity=2 if extra_capacity else 1, output_refused=2))
        frozen = {kind: {key: digest(row) for key, row in records[kind].items()} for kind in
                  ('review_assignment', 'review_attempt', 'review_output_refusal')}
        requests = {identifier: protocol.build_assignment_request(records, self.artifacts,
                    records['review_assignment'][identifier]) for identifier in expected_position}
        payload = self.build_repair_payload(head)
        exhaustion = payload['context']['context_repair']['exhaustion']
        self.assertEqual(exhaustion['position'], expected_position)
        self.assertEqual([row['assignment_id'] for row in exhaustion['refusals']], expected_refused)
        limit_codex_input(self.fixture, 2000000)
        probe_codex_route(self.case, 'repair-probe')
        validate = parts.validate_output
        refused_packet_digest = self.fixture.packet(records['review_assignment']['B'])['digest']
        def revised_validator(packet, output, inventory):
            if packet['digest'] == refused_packet_digest:
                output = dict(output, kind='read')
            return validate(packet, output, inventory)
        with patch.object(parts, 'validate_output', side_effect=revised_validator):
            self.assertIsNone(failures.classify_attempt(self.case.read_records(), self.artifacts,
                self.case.read_records()['review_attempt']['attempt-B']))
            saved = self.case.mutate(protocol.record_assignment, payload)['result']
            with patch.object(refusal, 'classify_observed_output',
                              side_effect=AssertionError('Origin must use stored records only.')):
                repair.validate_repair_origin(self.case.read_records(), self.artifacts, saved)
                repair.validate_repair_binding(self.case.read_records(), self.artifacts, saved)
        packet = self.fixture.packet(saved)
        self.assertNotIn(parts.KEY, packet)
        self.assertEqual(packet['context_repair']['exhaustion'], exhaustion)
        for identifier in expected_refused:
            turn = next(row for row in packet['scientific_history'] if row['assignment_id'] == identifier)
            self.assertIsNone(turn['observed_output'])
            self.assertEqual([row['attempt_id'] for row in turn['raw_observations']], ['attempt-' + identifier])
            raw = protocol.read_raw_observation(self.case.read_records(), self.artifacts, 'attempt-' + identifier)
            self.assertEqual(turn['raw_observations'][0]['raw_output_digest'], raw['raw_output_digest'])
        repair.validate_repair_origin(self.case.read_records(), self.artifacts, saved)
        output = self.case.build_review_output('result')
        output['reassessment'] = dict(assignment_id='D', findings=[])
        response = invoke_codex(self.case, saved['id'], output)
        self.assertNotIn('output_refusal', response)
        self.case.mutate(decisions.record_value_review, dict(output, id='review-repair',
            dossier_id=None, assignment_id=saved['id']))
        records = self.case.read_records()
        self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, saved,
                        records['review_attempt']['attempt-repair']))
        for kind, rows in frozen.items():
            self.assertEqual({key: digest(records[kind][key]) for key in rows}, rows)
        for identifier, request in requests.items():
            self.assertEqual(protocol.build_assignment_request(records, self.artifacts,
                             records['review_assignment'][identifier]), request)

    def test_mixed_capacity_and_refusal_position_repairs_without_rewriting_history(self):
        self.check_mixed_repair(extra_capacity=False)

    def test_capacity_replacement_of_a_refusal_keeps_the_second_refusal_replacement(self):
        self.check_mixed_repair(extra_capacity=True)

    def test_capacity_copy_keeps_the_notice_origin_before_its_own_refused_output(self):
        self.check_mixed_repair(extra_capacity=True, refused_capacity_source=True)

    def test_copied_refusal_context_counts_as_capacity(self):
        first = self.fixture.assign('A')
        self.overflow(first)
        second = self.retain_capacity_member(first, 'B')
        self.refuse(second)
        third = self.replace_refusal(second, 'C')
        self.overflow(third)
        fourth = self.retain_capacity_member(third, 'E')
        self.assertEqual(failures.count_position(self.case.read_records(), self.artifacts, fourth)['replacements'],
                         dict(capacity=2, output_refused=1))

    def test_capacity_copy_still_authenticates_its_notice_and_child_identity(self):
        from research_harness.review_refusal_replacement import validate_replacement_origin
        self.build_mixed_position(extra_capacity=True, refused_capacity_source=True)
        records = self.case.read_records()
        saved = records['review_assignment']['E']
        packet = self.fixture.packet(saved)
        for defect in ('parent-without-refusal-and-digest', 'parent-without-refusal-and-empty',
                       'author', 'reviewer', 'binding', 'digest'):
            child, changed = copy.deepcopy(saved), copy.deepcopy(packet)
            if defect.startswith('parent-without-refusal'):
                child['capacity_replacement_of'] = 'A'
            if defect.endswith('empty'):
                changed['output_refusal']['refused'] = []
            elif defect.endswith('digest'):
                changed['output_refusal']['refused'][0]['refusal_digest'] = '0' * 64
            elif defect in ('author', 'reviewer'):
                child[defect + '_id'] = 'different'
            else:
                child['context']['output_refusal']['assignment_id'] = 'A'
            with self.subTest(defect=defect):
                with self.assertRaises(ResearchError) as caught:
                    validate_replacement_origin(records, self.artifacts, child, changed)
                self.assertEqual(caught.exception.code, 'review_refusal_replacement_origin_invalid')

    def test_legacy_refusal_with_copied_capacity_marker_keeps_its_origin_checks(self):
        from research_harness.review_refusal_replacement import validate_replacement_origin
        self.build_mixed_position()
        records = self.case.read_records()
        saved = records['review_assignment']['C']
        legacy = dict(saved, capacity_replacement_of='A')
        packet = self.fixture.packet(saved)
        request = protocol.build_assignment_request(records, self.artifacts, saved)
        validate_replacement_origin(records, self.artifacts, legacy, packet)
        self.assertEqual(protocol.build_assignment_request(records, self.artifacts, legacy), request)
        for defect in ('digest', 'empty'):
            changed = copy.deepcopy(packet)
            if defect == 'digest':
                changed['output_refusal']['refused'][0]['refusal_digest'] = '0' * 64
            else:
                changed['output_refusal']['refused'] = []
            with self.subTest(defect=defect):
                with self.assertRaises(ResearchError) as caught:
                    validate_replacement_origin(records, self.artifacts, legacy, changed)
                self.assertEqual(caught.exception.code, 'review_refusal_replacement_origin_invalid')
        legacy_records = dict(records, review_assignment=dict(records['review_assignment'], C=legacy))
        self.assertEqual(failures.count_position(legacy_records, self.artifacts,
            records['review_assignment']['D'])['replacements'], dict(capacity=2, output_refused=1))

    def check_invalid_link(self, *, wrong_member):
        head = self.build_mixed_position()
        if wrong_member:
            member = copy.deepcopy(self.case.read_records()['review_assignment']['C'])
            member['context']['output_refusal']['assignment_id'] = 'A'
        else:
            member = copy.deepcopy(self.case.read_records()['review_assignment']['B'])
            member.pop('capacity_replacement_of')
        self.case.put('review_assignment', member['id'], member)
        exhaustion = repair.build_exhaustion_binding(self.case.read_records(), self.artifacts, head)
        before = self.case.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            repair._validate_exhaustion_records(before['records'], self.artifacts, head, exhaustion)
        self.assertEqual(caught.exception.code, 'review_context_repair_unverified')
        limit_codex_input(self.fixture, 2000000)
        probe_codex_route(self.case, 'repair-probe')
        payload = dict(id='repair', route_id='route', dossier_id=None, role='result', reviewer_id='first',
            author_id='author', context=dict(context_repair=dict(assignment_id='D', event_ids=[], evidence=[],
                exhaustion=exhaustion, probe_id='repair-probe', reason='Reassess the retained position.')))
        before = self.case.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            self.case.mutate(protocol.record_assignment, payload)
        self.assertEqual(caught.exception.code, 'review_context_repair_unverified')
        self.assertEqual(self.case.store.snapshot(), before)

    def test_missing_capacity_link_is_unverified_at_origin_and_admission_without_writes(self):
        self.check_invalid_link(wrong_member=False)

    def test_refusal_link_to_another_member_is_unverified_without_writes(self):
        self.check_invalid_link(wrong_member=True)
