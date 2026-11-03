"""Native and verification parts share the actual-input authentication boundary."""

import copy
import json
import unittest

import test_review_parts_native as native_fixtures
import test_review_parts_study as study_fixtures
from codex_route_fixtures import invoke_codex
from research_harness import review_parts as parts, review_protocol as protocol
from research_harness import review_transport as transport
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.review_input_projection import packets_from_request
from review_parts_fixtures import make_output


class PartsInputProjectionTests(unittest.TestCase):
    def prepare_case(self, role):
        fixture = (native_fixtures.NativePartsTests if role == 'native_math'
                   else study_fixtures.VerificationPartsTests)
        case = fixture()
        self.addCleanup(case.doCleanups)
        case.setUp()
        if role == 'native_math':
            case.limit_input()
        else:
            case.enlarge_verification()
        return case

    def read_packet(self, case, assignment):
        artifacts = case.artifacts if hasattr(case, 'artifacts') else case.case.artifacts
        return json.loads(artifacts.read(assignment['packet']))

    def build_request(self, packet):
        return dict(input=[dict(role='user', content=transport.PACKET_LABEL + json.dumps(packet))])

    def assert_refused(self, packet, assignment=None):
        packet.pop('digest', None)
        packet['digest'] = digest(packet)
        assignments = (None if assignment is None
                       else [dict(assignment, packet_digest=packet['digest'])])
        with self.assertRaises(ResearchError) as caught:
            packets_from_request(self.build_request(packet), assignments=assignments)
        self.assertEqual(caught.exception.code, 'review_input_unreadable')

    def assert_authentic_turns(self, role):
        case = self.prepare_case(role)
        assignment = case.assign()
        kinds = set()
        for number in range(40):
            packet = self.read_packet(case, assignment)
            kind = parts.find_kind(packet)
            kinds.add(kind)
            owner = case if role == 'native_math' else case.case
            records = owner.store.snapshot()['records']
            request = protocol.build_assignment_request(records, owner.artifacts, assignment)
            self.assertEqual(packets_from_request(request), [packet])
            self.assertEqual(packets_from_request(request, assignments=[assignment]), [packet])
            if kind == 'final':
                self.assertEqual(kinds, set(parts.KINDS))
            combinations = None
            if kind == 'combine':
                handle = packet[parts.STATE_KEY]['cards'][0]['unit']
                combinations = [dict(units=[handle], question='Does the exact original agree?',
                    view='Check the card against its original.',
                    read=[dict(unit=handle, item=True, start=0, end=1)])]
            output = make_output(packet, combinations=combinations)
            if kind == 'final':
                if role == 'native_math':
                    citations = [entry['digest'] for entry in case.native['evidence']]
                    output = case.final_output(citations)
                else:
                    output = case.final_output(assignment)
            response = invoke_codex(owner, assignment['id'], output)
            self.assertNotIn('output_refusal', response)
            records = owner.store.snapshot()['records']
            attempt = records['review_attempt']['attempt-' + assignment['id']]
            actual_request = json.loads(owner.artifacts.read(attempt['request']))
            self.assertEqual(packets_from_request(actual_request, assignments=[assignment]), [packet])
            if kind == 'final':
                return
            assignment = case.assign('turn-' + str(number + 2), assignment['id'])
        self.fail('The authentic parts series did not reach its final turn')

    def test_every_native_turn_is_read_from_its_authentic_request(self):
        self.assert_authentic_turns('native_math')

    def test_every_verification_turn_is_read_from_its_authentic_request(self):
        self.assert_authentic_turns('verification')

    def test_both_roles_require_the_same_complete_parts_header(self):
        for role in ('native_math', 'verification'):
            case = self.prepare_case(role)
            packet = self.read_packet(case, case.assign())
            for field in ('missing_header', 'minimal_header', 'protocol', 'adapter', 'turn',
                          'series_assignment_id', 'series_key', 'inventory', 'logical_packet',
                          'inventory_digest', 'logical_packet_digest', 'receipts', 'units',
                          'state', 'state_digest'):
                changed = copy.deepcopy(packet)
                header = changed[parts.KEY]
                if field == 'missing_header':
                    changed.pop(parts.KEY)
                elif field == 'minimal_header':
                    changed[parts.KEY] = {key: header[key] for key in
                        ('protocol', 'adapter', 'kind', 'series_assignment_id', 'turn',
                         'inventory_digest')}
                elif field in ('inventory', 'logical_packet'):
                    header[field]['path'] = 'forged-path'
                elif field in ('protocol', 'adapter', 'series_assignment_id'):
                    header[field] = ''
                elif field in ('inventory_digest', 'logical_packet_digest', 'state_digest'):
                    header[field] = '0' * 64
                elif field == 'series_key':
                    header[field] = None
                elif field == 'turn':
                    header[field] = True
                elif field == 'state':
                    changed[parts.STATE_KEY] = []
                else:
                    header[field] = {}
                with self.subTest(role=role, field=field):
                    self.assert_refused(changed)

    def test_native_subject_and_reviewer_changes_are_refused(self):
        case = self.prepare_case('native_math')
        assignment = case.assign()
        packet = self.read_packet(case, assignment)
        for field in ('subject_digest', 'actor_id', 'attestation_id'):
            changed = copy.deepcopy(packet)
            if field == 'subject_digest':
                changed['native'][field] = '0' * 64
            else:
                changed['native']['reviewer'][field] = 'different-reviewer'
            with self.subTest(field=field):
                self.assert_refused(changed, assignment)

    def test_verification_task_changes_are_refused(self):
        case = self.prepare_case('verification')
        assignment = case.assign()
        changed = self.read_packet(case, assignment)
        changed['paper']['task_digest'] = '0' * 64
        self.assert_refused(changed, assignment)

    def test_both_roles_refuse_another_series_or_turn(self):
        for role in ('native_math', 'verification'):
            case = self.prepare_case(role)
            assignment = case.assign()
            packet = self.read_packet(case, assignment)
            for field, value in (('series_assignment_id', 'another-series'), ('turn', 2),
                                 ('kind', 'check')):
                changed = copy.deepcopy(packet)
                changed[parts.KEY][field] = value
                if role == 'native_math' and field == 'series_assignment_id':
                    changed['native']['reviewer']['attestation_id'] = 'review-assignment:' + value
                with self.subTest(role=role, field=field):
                    self.assert_refused(changed, assignment)

    def test_both_roles_refuse_a_packet_of_another_authentic_series(self):
        for role in ('native_math', 'verification'):
            case = self.prepare_case(role)
            assignment = case.assign()
            other = self.prepare_case(role)
            if role == 'native_math':
                other.native['reviewer']['attestation_id'] = 'review-assignment:another-series'
                other.reference = other.artifacts.put(json.dumps(other.native).encode(),
                                                       'application/json')
            other_assignment = other.assign('another-series')
            packet = self.read_packet(other, other_assignment)
            with self.subTest(role=role):
                self.assertEqual(packets_from_request(self.build_request(packet)), [packet])
                self.assert_refused(packet, assignment)


if __name__ == '__main__':
    unittest.main()
