"""Drive the shared decision 31 observations through the role-specific commands."""

import unittest
from copy import deepcopy
from unittest.mock import patch

import property_stub_a as stub
import test_review_parts_adjudication_journey as adjudication_fixtures
import test_review_parts_study as study_fixtures
import test_review_parts_slate as slate_fixtures
import test_property_drivers_a_parts as driver
from research_harness import review_parts, review_parts_v3 as parts, review_protocol as protocol
from research_harness import bar_sources, development, research_decisions, verification
from research_harness import review_route_capacity as capacity
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from test_review_output_profile import build_row


class RoleProperties(driver.PartsDriver, unittest.TestCase):
    def use_fixture(self, fixture):
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.select_parts_v3()
        return fixture

    def pin_read_profile(self):
        self.enter_patch(patch.object(capacity, 'read_profile_priors', return_value=[
            dict(build_row(level=None, kind='read', thinking=1), largest_output_tokens=12)]))
        self.enter_patch(patch.object(protocol.transport, 'CODEX_INPUT_LIMIT_CHARS', 1300000))

    def observe_groups(self, case, first, adapter, answer):
        assignment = first
        cross_group = None
        middle = None
        for _ in range(30):
            records = case.store.snapshot()['records']
            packet = protocol._read_json(case.artifacts, assignment['packet'])
            header = packet['review_parts']
            self.assertEqual((header['protocol'], header['adapter']), (parts.PROTOCOL, adapter))
            if header['kind'] == 'final':
                break
            groups = packet['review_parts_groups']
            material = list(stub.read_group_material(packet))
            if cross_group is None and len(groups) > 1 and groups[0]['last']['unit'] in material:
                # Pick a later unit, so repeating text within one unit cannot
                # give the cross-group quotation genuine credit.
                first_end = material.index(groups[0]['last']['unit'])
                if first_end < len(material) - 1:
                    request = protocol.build_assignment_request(records, case.artifacts, assignment)
                    prompt = stub.read_prompt(request)
                    for variant in ('missing', 'duplicate', 'first', 'last', 'unit_count'):
                        with self.subTest(variant=variant):
                            with self.assertRaises(ResearchError) as caught:
                                parts.validate_turn_output(records, case.artifacts, assignment, prompt,
                                                           stub.build_broken_cards(prompt, variant))
                            self.assertEqual(caught.exception.code, 'invalid_value_review')
                    cross_group = assignment['id']
            if middle is None:
                for group in groups:
                    if group['unit_count'] >= 3 and group['first']['unit'] in material:
                        index = material.index(group['first']['unit']) + 1
                        middle = (assignment['id'], material[index])
                        break
            if assignment['id'] == cross_group:
                def cross_answer(sent):
                    result = answer(sent)
                    wrong = stub.build_cross_group_answer(sent)
                    result['cards'][0]['statements'] = wrong['cards'][0]['statements']
                    return result
                stub.invoke_prompt(self, case, assignment, cross_answer)
            else:
                stub.invoke_prompt(self, case, assignment, answer)
            assignment = self.continue_series(case, assignment)
        else:
            self.fail('The series never reached its scientific final')
        return assignment, cross_group, middle

    def check_observations(self, case, final, cross_group, middle):
        records = case.store.snapshot()['records']
        verified = review_parts.verify_series(records, case.artifacts, final['id'])
        self.assertTrue(verified['complete'], verified['issues'])
        self.assertIsNotNone(cross_group, 'No read held separately credited groups')
        self.assertIsNotNone(middle, 'No card covered a middle unit')
        observations = parts.observe_series(records, case.artifacts, final['id'])
        crossed = next(row for row in observations if row['receipt']['assignment_id'] == cross_group)
        card = crossed['delta']['cards'][0]
        self.assertEqual(card['statements'], [])
        self.assertEqual(card['unverified_statements'], 1)
        self.assertFalse(any(row['statements'] for turn in observations for row in turn['delta']['cards']))

        identifier, handle = middle
        assignment = records['review_assignment'][identifier]
        packet = protocol._read_json(case.artifacts, assignment['packet'])
        changed = deepcopy(packet)
        for index, item in enumerate(changed['review_parts_material']):
            if isinstance(item, dict) and item['handle'] == handle:
                text = changed['review_parts_material'][index + 1]
                changed['review_parts_material'][index + 1] = ('z' if text[0] != 'z' else 'y') + text[1:]
                break
        else:
            self.fail('The middle unit has no text in this request')
        changed['digest'] = digest({key: value for key, value in changed.items() if key != 'digest'})
        self.assertEqual(changed['review_parts_groups'], packet['review_parts_groups'])
        with self.assertRaises(ResearchError) as caught:
            parts.validate_turn_output(records, case.artifacts, assignment, changed, stub.build_series_turn(changed))
        self.assertEqual(caught.exception.code, 'review_parts_delivery_invalid')
        assignment['packet'] = parts._put_value(case.artifacts, changed)
        assignment['packet_digest'] = changed['digest']
        self.assertFalse(review_parts.verify_series(records, case.artifacts, final['id'])['complete'])


class AdjudicationProperties(RoleProperties):
    def test_problem_adjudicator(self):
        """A focused dispute needs several originals to exercise grouped credit and coverage."""
        fixture = adjudication_fixtures.AdjudicationPartsCase()
        fixture.output_tokens = 128000
        self.use_fixture(fixture)
        fixture.oversized(count=3, paragraphs=2400)
        self.pin_read_profile()
        case = fixture.case
        final, cross, middle = self.observe_groups(case, fixture.assign(), 'problem_adjudicator',
                                                   stub.build_series_turn)
        stub.invoke_prompt(self, case, final, stub.request_first_original)
        final = self.continue_series(case, final)
        stub.invoke_prompt(self, case, final, stub.build_adjudication_final)
        records = case.store.snapshot()['records']
        _, output = protocol.read_assignment_output(records, case.artifacts, final['id'])
        self.assertIsNotNone(output, records.get('review_output_refusal'))
        saved = case.mutate(protocol.record_adjudication,
            dict(output, id='disposition', assignment_id=final['id']))['result']
        self.assertEqual(saved['assignment_id'], final['id'])
        self.check_observations(case, final, cross, middle)


class VerificationProperties(RoleProperties):
    def test_study_verification(self):
        """A keyed collection needs several originals to exercise group credit and middle-unit integrity."""
        fixture = self.use_fixture(study_fixtures.VerificationPartsTests())
        fixture.enlarge_verification()
        self.pin_read_profile()
        case = fixture.case
        final, cross, middle = self.observe_groups(case, fixture.assign(), 'study_verification',
                                                   stub.carry_source_citations)
        stub.invoke_prompt(self, case, final, stub.build_verification_final)
        records = case.store.snapshot()['records']
        _, output = protocol.read_assignment_output(records, case.artifacts, final['id'])
        self.assertIsNotNone(output, records.get('review_output_refusal'))
        payload = case.build_bind_payload(fixture.task['digest'], output['verdict'], observed=False)
        payload['assessment'].update(assignment_id=final['id'], checks=output['checks'])
        saved = case.mutate(verification.bind_verdict, payload)['result']
        self.assertEqual(saved['assessment']['assignment_id'], final['id'])
        self.check_observations(case, final, cross, middle)


class StrategicDriver(RoleProperties):
    def finish_value(self, case, first, adapter, command=research_decisions.record_value_review):
        turn_answer = stub.carry_source_citations if adapter == 'study_readiness' else stub.build_series_turn
        final, cross, middle = self.observe_groups(case, first, adapter, turn_answer)
        stub.invoke_prompt(self, case, final, stub.request_first_original)
        final = self.continue_series(case, final)
        answer = stub.build_readiness_final if adapter == 'study_readiness' else stub.build_problem_final
        stub.invoke_prompt(self, case, final, answer)
        records = case.store.snapshot()['records']
        _, output = protocol.read_assignment_output(records, case.artifacts, final['id'])
        diagnostics = records.get('review_output_refusal', {}).get('attempt-' + final['id'], {})
        self.assertIsNotNone(output, (diagnostics.get('code'), diagnostics.get('check'), diagnostics.get('details', {}).get('path'),
                                    diagnostics.get('details', {}).get('message')))
        if adapter == 'study_readiness':
            case.mutate(command, output['support'])
            recorded = case.store.snapshot()['records']['readiness_review'][output['support']['id']]
            self.assertEqual(recorded['payload'], output['support'])
        else:
            result = case.mutate(command, dict(output, id='strategic-review', dossier_id=None,
                                               assignment_id=final['id']))['result']
            self.assertEqual(result['assignment_id'], final['id'])
        self.check_observations(case, final, cross, middle)

    def open_slate(self):
        """The slate needs enough candidate-bound original material to enter a grouped read."""
        fixture = self.use_fixture(slate_fixtures.SlateSeriesTests())
        case = fixture.make_case()
        fixture.make_imported_packet(case, body_characters=360000, record_characters=1000)
        self.enter_patch(patch.object(protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,)))
        self.enter_patch(patch.object(bar_sources, 'SESSION_PROTOCOL', bar_sources.PROTOCOL))
        self.enter_patch(patch.object(protocol.transport, 'CODEX_INPUT_LIMIT_CHARS', 300000))
        return case, fixture.assign(case)



class StrategicProperties(StrategicDriver):
    def test_study_readiness(self):
        """Keyed branch originals exercise grouped delivery before binding the native readiness review."""
        fixture = self.use_fixture(study_fixtures.ReadinessPartsTests())
        # Use the review builder's typed template form in the synthetic packet.
        fixture.logical['support_contract']['evidence'] = protocol._reference_templates(fixture.references)
        for index in range(4):
            fixture.logical['readiness']['inputs']['branches']['branch-property-%d' % index] = dict(
                id='branch-property-%d' % index, evidence=deepcopy(fixture.references),
                reason='The branch retains its exact evidence. ' + str(index) * 14000)
        self.pin_read_profile()
        self.finish_value(fixture.case, fixture.assign(), 'study_readiness', development.record_readiness_review)
