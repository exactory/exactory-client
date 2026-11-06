"""A second fresh visual repair authenticates a flat scientific ancestry."""

import copy
import json
import unittest
from unittest.mock import patch

from research_harness import review_context_repair as context
from research_harness import review_protocol as protocol
from research_harness import review_source_complete as complete
from research_harness import review_transport as transport
from research_harness import review_visual_context_repair as repair
from research_harness import review_codex_visual_transport as visual
from research_harness.errors import ResearchError
from research_harness.evidence import digest
import test_visual_context_repair as fixtures


class NestedVisualContextRepairTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.VisualContextRepairTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.case = self.fixture.case
        self.artifacts = self.fixture.artifacts

    def invoke(self, assignment, identifier, output, *, contaminated=False):
        def respond(route, request, credential):
            response = self.fixture.fixture.response(route, request, output)
            if contaminated:
                response['context_evidence']['input_verified'] = False
                response['context_evidence']['issues'] = ['session_identity_or_instructions']
            return response
        with patch.object(transport, 'send_request', side_effect=respond):
            self.case.mutate(protocol.invoke_assignment, {
                'id': identifier, 'assignment_id': assignment['id']}, credential='test-only')

    def prepare_second_repair(self, *, legacy=False, duplicate_inherited=False, conflicting_inherited=False):
        if legacy:
            prepare = repair.prepare
            with patch.object(repair, 'prepare', side_effect=lambda *args:
                              prepare(*args, version=repair.PROTOCOL)):
                first = self.fixture.assign()
        else:
            first = self.fixture.assign()
        output = copy.deepcopy(self.fixture.output)
        output['value']['status'] = 'unresolved'
        output['limitations'] = ['The retained negative finding has no decisive supplied proof.']
        output['source_requests'] = [{'id': 'repair-inventory', 'kind': 'inventory_page', 'page': 0}]
        self.invoke(first, 'first-repair-source-attempt', output)
        first_packet = json.loads(self.artifacts.read(first['packet']))
        build_packet = protocol.build_packet
        def build_with_historical_duplicate(*args, **kwargs):
            packet = build_packet(*args, **kwargs)
            if duplicate_inherited:
                history = copy.deepcopy(first_packet['scientific_history'])
                duplicate = copy.deepcopy(history[0])
                if conflicting_inherited:
                    duplicate['observed_output']['limitations'] = [
                        'A conflicting replacement for the inherited negative finding.']
                history.append(duplicate)
                packet['scientific_history'] = history
                packet.pop('digest', None)
                packet['digest'] = digest(packet)
            return packet
        with patch.object(protocol, 'build_packet', side_effect=build_with_historical_duplicate):
            continued = self.case.mutate(protocol.record_assignment, dict(
                self.fixture.value, id='continued-first-repair',
                context={'prior_assignment_id': first['id']}))['result']
        next_output = copy.deepcopy(output)
        next_output['source_requests'] = [{'id': 'second-repair-inventory', 'kind': 'inventory_page', 'page': 0}]
        self.invoke(continued, 'second-contaminated-attempt', next_output, contaminated=True)
        self.case.mutate(protocol.record_context_event, {
            'id': 'second-contamination', 'assignment_id': continued['id'],
            'kind': 'contamination', 'source': 'runtime_instruction_transformation',
            'reason': 'The next native visual instruction bytes differ.',
            'evidence': [self.fixture.event_evidence]})
        self.fixture.probe('second-repair-probe')
        value = copy.deepcopy(self.fixture.value)
        value['id'] = 'second-fresh-visual-repair'
        value['context']['context_repair'].update(
            assignment_id=continued['id'], event_ids=['input-defect', 'second-contamination'],
            probe_id='second-repair-probe')
        return first, continued, value, output, next_output

    def test_repair_source_continuation_second_contamination_and_fresh_repair(self):
        first, continued, value, first_output, next_output = self.prepare_second_repair()
        second = self.case.mutate(protocol.record_assignment, value)['result']
        records = self.case.read_records()
        packet = json.loads(self.artifacts.read(second['packet']))
        chain = context._exposure_lineage(records, continued)
        self.assertEqual([row['assignment_id'] for row in packet['scientific_history']],
                         [ancestor['id'] for ancestor in chain])
        self.assertTrue(all('scientific_history' not in row['packet']
                            for row in packet['scientific_history']))
        observed = {row['assignment_id']: row['observed_output'] for row in packet['scientific_history']}
        self.assertEqual(observed[first['id']], first_output)
        self.assertEqual(observed[continued['id']], next_output)
        self.assertEqual(packet['historical_reviews'][-1]['id'], 'failed-visual-final')
        self.assertEqual(second['history'], [])
        self.assertEqual(second['session_id'], second['id'])
        self.assertEqual(context.slot_id(records, second), context.slot_id(records, first))
        self.assertEqual(packet[complete.MARKER]['source_requests'],
                         json.loads(self.artifacts.read(continued['packet']))[complete.MARKER]['source_requests'])
        complete.validate(records, self.artifacts, second, packet)
        self.assertIsInstance(protocol.build_assignment_request(records, self.artifacts, second), dict)
        response = copy.deepcopy(next_output)
        response['source_requests'] = [{'id': 'fresh-second-inventory', 'kind': 'inventory_page', 'page': 0}]
        self.invoke(second, 'fresh-second-repair-attempt', response)
        records = self.case.read_records()
        self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, second,
                         records['review_attempt']['fresh-second-repair-attempt']))
        self.assertEqual(protocol.assignment_state(records, self.artifacts, second['id'])['output'], response)

    def test_ancestry_authenticates_each_native_assignment_attempt_and_output(self):
        first, continued, value, _, _ = self.prepare_second_repair()
        second = self.case.mutate(protocol.record_assignment, value)['result']
        records = self.case.read_records()
        packet = json.loads(self.artifacts.read(second['packet']))
        ancestors = packet[repair.MARKER]['ancestry']
        self.assertEqual(len(ancestors), len(packet['scientific_history']))
        for ancestor in ancestors:
            assignment = records['review_assignment'][ancestor['assignment_id']]
            attempt = records['review_attempt'][ancestor['attempt_id']]
            self.assertEqual(ancestor['assignment_digest'], digest(assignment))
            self.assertEqual(ancestor['attempt_digest'], digest(attempt))
            self.assertEqual(ancestor['request'], attempt['request'])
            self.assertEqual(ancestor['output'], attempt['output'])
        for defect in ('assignment', 'attempt', 'output', 'scientific-packet'):
            changed, altered = copy.deepcopy(records), copy.deepcopy(packet)
            target = ancestors[0]
            if defect == 'assignment':
                changed['review_assignment'][target['assignment_id']]['author_id'] = 'altered-author'
            elif defect == 'attempt':
                changed['review_attempt'][target['attempt_id']]['origin'] = 'imported'
            elif defect == 'output':
                altered[repair.MARKER]['ancestry'][0]['output'] = self.fixture.event_evidence
            else:
                altered['scientific_history'][0]['packet']['reviewer_id'] = 'altered-reviewer'
            with self.subTest(defect=defect), self.assertRaises(ResearchError):
                complete.validate(changed, self.artifacts, second, altered)

    def test_duplicate_or_conflicting_ancestry_and_science_are_refused(self):
        _, _, value, _, _ = self.prepare_second_repair()
        second = self.case.mutate(protocol.record_assignment, value)['result']
        records = self.case.read_records()
        packet = json.loads(self.artifacts.read(second['packet']))
        for location in ('ancestry', 'scientific_history'):
            for conflicting in (False, True):
                changed = copy.deepcopy(packet)
                rows = changed[repair.MARKER]['ancestry'] if location == 'ancestry' else changed[location]
                duplicate = copy.deepcopy(rows[0])
                if conflicting:
                    duplicate['attempt_id' if location == 'ancestry' else 'observed_output'] = 'conflict'
                rows.append(duplicate)
                with self.subTest(location=location, conflicting=conflicting), self.assertRaises(ResearchError):
                    complete.validate(records, self.artifacts, second, changed)

    def test_conflicting_inherited_outputs_in_an_authenticated_predecessor_are_refused(self):
        _, continued, value, _, _ = self.prepare_second_repair(
            duplicate_inherited=True, conflicting_inherited=True)
        records = self.case.read_records()
        attempt = records['review_attempt']['second-contaminated-attempt']
        self.assertTrue(protocol.authenticate_provenance(records, self.artifacts, continued, attempt))
        with self.assertRaises(ResearchError) as conflict:
            self.case.mutate(protocol.record_assignment, value)
        self.assertEqual(conflict.exception.code, 'review_visual_repair_mismatch')

    def test_identical_inherited_outputs_collapse_to_one_authenticated_assignment(self):
        _, continued, value, _, _ = self.prepare_second_repair(duplicate_inherited=True)
        second = self.case.mutate(protocol.record_assignment, value)['result']
        records = self.case.read_records()
        packet = json.loads(self.artifacts.read(second['packet']))
        self.assertEqual([row['assignment_id'] for row in packet['scientific_history']],
                         [row['id'] for row in context._exposure_lineage(records, continued)])
        complete.validate(records, self.artifacts, second, packet)

    def test_second_repair_retains_required_sources_and_exact_slot(self):
        _, _, value, _, _ = self.prepare_second_repair()
        second = self.case.mutate(protocol.record_assignment, value)['result']
        records = self.case.read_records()
        packet = json.loads(self.artifacts.read(second['packet']))
        for defect in ('slot', 'source', 'source-request', 'negative-finding'):
            changed, assignment = copy.deepcopy(packet), copy.deepcopy(second)
            if defect == 'slot':
                assignment['slot_id'] = 'another-fixed-slot'
            elif defect == 'source':
                selected = changed[complete.MARKER]['sources'][0]['selected_text_sha256']
                for entry in changed['evidence']:
                    if entry['artifact']['sha256'] == selected:
                        entry.update(content='', encoding='omitted')
            elif defect == 'source-request':
                changed[complete.MARKER]['source_requests'] = []
            else:
                changed['scientific_history'][-1]['observed_output']['limitations'] = []
            with self.subTest(defect=defect), self.assertRaises(ResearchError):
                complete.validate(records, self.artifacts, assignment, changed)

    def test_v1_request_bytes_and_native_authentication_survive_a_v2_successor(self):
        first, _, value, _, _ = self.prepare_second_repair(legacy=True)
        records = self.case.read_records()
        attempt = records['review_attempt']['first-repair-source-attempt']
        original_packet = self.artifacts.read(first['packet'])
        original_request = self.artifacts.read(attempt['request'])
        packet = json.loads(original_packet)
        self.assertEqual(packet[repair.MARKER]['protocol'], repair.PROTOCOL)
        self.assertNotIn('ancestry', packet[repair.MARKER])
        complete.validate(records, self.artifacts, first, packet)
        self.assertEqual(protocol.build_assignment_request(records, self.artifacts, first),
                         json.loads(original_request))
        self.case.mutate(protocol.record_assignment, value)
        records = self.case.read_records()
        self.assertEqual(self.artifacts.read(first['packet']), original_packet)
        self.assertEqual(self.artifacts.read(attempt['request']), original_request)
        self.assertEqual(protocol.build_assignment_request(records, self.artifacts, first),
                         json.loads(original_request))
        self.assertTrue(protocol.authenticate_provenance(records, self.artifacts, first, attempt))

    def test_second_repair_overflow_is_refused_before_native_dispatch(self):
        _, _, value, _, _ = self.prepare_second_repair()
        second = self.case.mutate(protocol.record_assignment, value)['result']
        records = self.case.read_records()
        request = protocol.build_assignment_request(records, self.artifacts, second)
        before = self.case.store.revision
        dispatched = []
        def send(*args):
            dispatched.append(args)
            raise AssertionError('An oversized visual repair reached native dispatch')
        with patch.object(visual, 'MAX_USER_TEXT', len(visual.packet_text(request)) - 1), \
                patch.object(transport, 'send_request', side_effect=send):
            with self.assertRaises(ResearchError) as overflow:
                self.case.mutate(protocol.invoke_assignment, {
                    'id': 'overflow-second-repair', 'assignment_id': second['id']}, credential='test-only')
        self.assertEqual(overflow.exception.code, 'review_input_too_large')
        self.assertEqual(overflow.exception.details['phase'], 'dispatch_preflight')
        self.assertEqual(dispatched, [])
        self.assertEqual(self.case.store.revision, before)
        self.assertNotIn('overflow-second-repair', self.case.read_records().get('review_invocation', {}))


if __name__ == '__main__':
    unittest.main()
