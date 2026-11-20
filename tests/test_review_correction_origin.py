"""An accepted output correction is authenticated against the records that admitted its call.

A record that is written later does not unselect the correction. It acts in the state of the assignment, as it acts
on an assignment whose accepted attempt needed no correction. Each test of LaterRecordTests builds the store of
test_review_output_correction twice, once with an accepted correction and once with an ordinary accepted attempt,
writes the same later record to both through its command, and compares the two states.
"""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_harness import record_check, research_decisions as decisions
from research_harness import review_output_correction as correction
from research_harness import review_failure_class as failures
from research_harness import review_protocol as protocol, review_transport as transport
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.storage import read_filtered_records
import test_review_output_correction as fixtures


class CorrectionOriginCase(unittest.TestCase):
    def build_refused(self):
        """The repair assignment of the fixture with two refused answers and no correction."""
        case = fixtures.CorrectionTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.prepare_refused()
        return case

    def build_corrected(self):
        """The same assignment with its accepted correction and the value review of the corrected output."""
        case = self.build_refused()
        self.assertEqual(case.correct(case.delta())['correction_status'], 'accepted')
        case.fixture.mutate(decisions.record_value_review, case.case.payload(case.corrected()))
        return case

    def build_ordinary(self):
        """The same assignment with one accepted attempt, no correction, and its value review."""
        case = fixtures.CorrectionTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.case.observe(case.case.output, 'ordinary-attempt')
        case.fixture.mutate(decisions.record_value_review, case.case.payload(case.case.output))
        return case

    def record_event(self, case, identifier, assignment_id, kind):
        fixture = case.fixture
        evidence = fixture.artifacts.put(('Evidence of ' + identifier + '.').encode(), 'text/plain')
        fixture.mutate(protocol.record_context_event, dict(
            id=identifier, assignment_id=assignment_id, kind=kind,
            source='owner' if kind == 'route_retired' else 'author_history',
            reason='A record that is written after the correction.', evidence=[evidence]))

    def probe(self, case, identifier, route_id='route', *, verified=True):
        fixture = case.fixture

        def respond(route, request, credential):
            packet = json.loads(request['input'][0]['content'].removeprefix('The packet below is JSON.\n'))
            return fixture.build_provider_response(dict(
                allowed_control=packet['allowed_control'] if verified else 'another value',
                evidence_control=packet['evidence_control'], excluded_controls=[]))
        with patch.object(transport, 'send_request', side_effect=respond):
            fixture.mutate(protocol.probe_route, dict(id=identifier, route_id=route_id), credential='test-only')

    def record_second_route(self, case):
        """A second route record with the identity of the first, and its verified probe."""
        route = case.fixture.read_records()['review_route']['route']
        case.fixture.mutate(protocol.record_route, dict(
            {key: copy.deepcopy(route[key]) for key in ('adapter', 'model', 'endpoint', 'configuration')},
            id='second-route'))
        self.probe(case, 'second-route-probe', 'second-route')

    def invoke_correction(self, case, route_id='route', **options):
        fixture = case.fixture
        with patch.object(transport, 'send_request', return_value=fixture.build_provider_response(case.delta())):
            return fixture.mutate(correction.invoke_correction, dict(
                id='correction', assignment_id=case.case.assignment['id'], route_id=route_id),
                credential='test-only', **options)['result']

    def read_state(self, case):
        fixture = case.fixture
        return protocol.assignment_state(fixture.read_records(), fixture.artifacts, case.case.assignment['id'])

    def assert_selected(self, case, state):
        """The stored correction is selected and authenticated, and the state holds the corrected output."""
        fixture, identifier = case.fixture, case.case.assignment['id']
        records = fixture.read_records()
        selected = correction.selected_correction(records, fixture.artifacts, identifier)
        self.assertIsNotNone(selected)
        self.assertEqual(selected['id'], 'correction')
        self.assertEqual(state['output'], case.corrected())
        self.assertEqual(state['output_correction_id'], 'correction')
        self.assertEqual(state['attempt_id'], 'refused-two')
        self.assertEqual(record_check.describe_correction_observation(
            records, fixture.artifacts, records['review_output_correction_invocation']['correction'],
            records['review_output_correction']['correction']), {'authenticated': True, 'status': 'completed'})
        self.assertFalse(protocol.has_exhausted_position(
            records, fixture.artifacts, records['review_assignment'][identifier]))


class LaterRecordTests(CorrectionOriginCase):
    def compare(self, write, expected_codes):
        """Write the same later record to both stores; the corrected assignment has the state of the ordinary one."""
        ordinary, corrected = self.build_ordinary(), self.build_corrected()
        write(ordinary)
        write(corrected)
        expected, actual = self.read_state(ordinary), self.read_state(corrected)
        self.assertEqual(sorted(row['code'] for row in expected['obligations']), expected_codes)
        self.assertEqual(expected['output'], ordinary.case.output)
        self.assert_selected(corrected, actual)
        self.assertEqual(sorted(row['code'] for row in actual['obligations']), expected_codes)
        for key in ('ready', 'context_status'):
            self.assertEqual(actual[key], expected[key], key)
        self.assertEqual(protocol.has_verified_history(actual), protocol.has_verified_history(expected))
        return ordinary, corrected

    def test_retirement_of_the_route_keeps_the_corrected_output_ready(self):
        self.compare(lambda case: self.record_event(
            case, 'retirement', case.case.assignment['id'], 'route_retired'), [])

    def test_retirement_in_the_lineage_of_the_repair_keeps_the_corrected_output(self):
        self.compare(lambda case: self.record_event(case, 'retirement', 'bar-first', 'route_retired'),
                     ['review_context_repair_stale'])

    def test_later_probe_that_does_not_verify_keeps_the_corrected_output(self):
        self.compare(lambda case: self.probe(case, 'later-probe', verified=False), ['review_context_unverified'])

    def test_contamination_of_the_assignment_keeps_the_corrected_output(self):
        self.compare(lambda case: self.record_event(
            case, 'later-exposure', case.case.assignment['id'], 'contamination'), ['review_context_contaminated'])

    def test_later_contamination_of_the_predecessor_keeps_the_corrected_output(self):
        self.compare(lambda case: self.record_event(case, 'later-exposure', 'result-first', 'contamination'),
                     ['review_context_repair_stale'])

    def test_context_repair_that_succeeds_the_assignment_takes_the_corrected_output_as_its_history(self):
        successors = []

        def succeed(case):
            fixture, assignment = case.fixture, case.case.assignment
            self.record_event(case, 'later-exposure', assignment['id'], 'contamination')
            self.probe(case, 'later-repair-probe')
            events = fixture.read_records()['review_context_event']
            successors.append(fixture.mutate(protocol.record_assignment, dict(
                id='later-repair', route_id='route', dossier_id=None, role='result', reviewer_id='first',
                author_id='author', context=dict(context_repair=dict(
                    assignment_id=assignment['id'], event_ids=['exposure', 'later-exposure'],
                    probe_id='later-repair-probe', reason='Reassess the retained result once more.',
                    evidence=[item for key in ('exposure', 'later-exposure')
                              for item in events[key]['evidence']]))))['result'])

        ordinary, corrected = self.compare(succeed, ['review_assignment_replaced', 'review_context_contaminated'])
        for case, output, successor in ((ordinary, ordinary.case.output, successors[0]),
                                        (corrected, corrected.corrected(), successors[1])):
            packet = json.loads(case.fixture.artifacts.read(successor['packet']))
            history = next(row for row in packet['scientific_history']
                           if row['assignment_id'] == case.case.assignment['id'])
            self.assertNotIn('raw_observations', history)
            finding, expected = (value['reassessment']['findings'][0]
                                 for value in (history['observed_output'], output))
            self.assertEqual([finding[key] for key in ('review_id', 'disposition', 'reason')],
                             [expected[key] for key in ('review_id', 'disposition', 'reason')])

    def test_later_record_plans_no_replacement_of_the_corrected_output(self):
        """The refusal planner and the refusal replacement read the correction that stays selected."""
        ordinary, corrected = self.build_ordinary(), self.build_corrected()
        plans = []
        for case in (ordinary, corrected):
            self.record_event(case, 'later-exposure', 'result-first', 'contamination')
            records = case.fixture.read_records()
            plans.append(failures.plan_refusal_recovery(
                records, case.fixture.artifacts, records['review_assignment'][case.case.assignment['id']]))
        self.assertEqual([(plan['state'], plan['code']) for plan in plans], [('stop', 'review_slot_exhausted')] * 2)
        # The request that plan_refusal_recovery built when a later record unselected the correction.
        fixture = corrected.fixture
        records = fixture.read_records()
        head = records['review_assignment'][corrected.case.assignment['id']]
        payload = {key: copy.deepcopy(head[key])
                   for key in ('role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id', 'context')}
        payload['id'] = head['id'] + '-refusal'
        payload['context']['output_refusal'] = {'assignment_id': head['id']}
        revision = fixture.store.revision
        with self.assertRaises(ResearchError) as caught:
            fixture.mutate(protocol.record_assignment, payload)
        self.assertEqual(caught.exception.code, 'review_refusal_replacement_ineligible')
        self.assertEqual(fixture.store.revision, revision)
        self.assertNotIn(payload['id'], fixture.read_records()['review_assignment'])


class CallOriginTests(CorrectionOriginCase):
    def test_answer_is_accepted_when_a_record_arrives_during_the_call(self):
        case = self.build_refused()
        identifier = case.case.assignment['id']
        observed = self.invoke_correction(case, after_start=lambda: self.record_event(
            case, 'retirement', identifier, 'route_retired'))
        self.assertEqual(observed['correction_status'], 'accepted', observed['correction_error'])
        records = case.fixture.read_records()
        invocation = records['review_output_correction_invocation']['correction']
        retirement = records['review_context_event']['retirement']
        self.assertLess(invocation['recorded_revision'], retirement['recorded_revision'])
        self.assertLess(retirement['recorded_revision'], observed['recorded_revision'])
        state = self.read_state(case)
        self.assertTrue(state['ready'], state['obligations'])
        self.assert_selected(case, state)

    def test_correction_on_another_route_record_follows_the_state_of_that_route(self):
        case = self.build_refused()
        self.record_second_route(case)
        self.assertEqual(self.invoke_correction(case, 'second-route')['correction_status'], 'accepted')
        state = self.read_state(case)
        self.assertTrue(state['ready'], state['obligations'])
        self.probe(case, 'second-route-later-probe', 'second-route', verified=False)
        records = case.fixture.read_records()
        self.assertEqual(protocol.route_state(records, case.fixture.artifacts, 'route')['context_status'],
                         'verified_for_route')
        state = self.read_state(case)
        self.assert_selected(case, state)
        self.assertEqual(state['context_status'], 'unverified')
        self.assertEqual([row['code'] for row in state['obligations']], ['review_context_unverified'])

    def test_new_correction_call_is_refused_after_each_later_record(self):
        """The conditions of a new call are read from the records of today, as before."""
        later_records = [
            ('retirement', 'review_correction_ineligible', lambda case: self.record_event(
                case, 'retirement', case.case.assignment['id'], 'route_retired')),
            ('probe', 'review_correction_ineligible', lambda case: self.probe(case, 'later-probe', verified=False)),
            ('contamination', 'review_correction_ineligible', lambda case: self.record_event(
                case, 'later-exposure', case.case.assignment['id'], 'contamination')),
            ('predecessor', 'review_context_repair_stale', lambda case: self.record_event(
                case, 'later-exposure', 'result-first', 'contamination'))]
        for label, code, write in later_records:
            with self.subTest(later_record=label):
                case = self.build_refused()
                write(case)
                with patch.object(transport, 'send_request') as send, self.assertRaises(ResearchError) as caught:
                    case.fixture.mutate(correction.invoke_correction, dict(
                        id='correction', assignment_id=case.case.assignment['id'], route_id='route'),
                        credential='test-only')
                self.assertEqual(caught.exception.code, code)
                send.assert_not_called()
                self.assertNotIn('correction',
                                 case.fixture.read_records().get('review_output_correction_invocation', {}))


class OriginViewTests(CorrectionOriginCase):
    def test_origin_of_scoped_records_lists_and_serves_the_rows_that_they_list_and_serve(self):
        case = self.build_refused()
        # A cause event outside the slot and the lineage of the repair, recorded before the correction call.
        self.record_event(case, 'outside-exposure', 'bar-second', 'contamination')
        self.assertEqual(self.invoke_correction(case)['correction_status'], 'accepted')
        fixture, identifier = case.fixture, case.case.assignment['id']
        plain = fixture.read_records()
        scoped = protocol._read_context_records(fixture.store._read_scoped_preparation().records,
                                                plain['review_assignment'][identifier])
        # The scoped view of a correction lists the slot and its lineage, and serves any assignment by its id.
        self.assertNotIn('bar-second', list(scoped['review_assignment']))
        self.assertIsNotNone(scoped['review_assignment'].get('bar-second'))
        invocation = plain['review_output_correction_invocation']['correction']
        origin = protocol.read_origin(scoped, fixture.artifacts, invocation['recorded_revision']).records
        self.assertEqual(sorted(origin['review_assignment']), sorted(scoped['review_assignment']))
        self.assertEqual(origin['review_assignment'].get('bar-second'), plain['review_assignment']['bar-second'])
        for kind in ('review_output_correction_invocation', 'review_output_correction'):
            self.assertIn('correction', scoped[kind])
            self.assertNotIn('correction', origin[kind])
            self.assertIsNone(origin[kind].get('correction'))
        # A filtered read of the origin reaches only the rows that the scoped records list.
        self.assertEqual(sorted(scoped['review_context_event']), ['exposure'])
        self.assertEqual(sorted(read_filtered_records(scoped, 'review_context_event', kind='contamination')),
                         ['exposure', 'outside-exposure'])
        self.assertEqual(sorted(read_filtered_records(origin, 'review_context_event', kind='contamination')),
                         ['exposure'])
        self.assertEqual(origin['review_context_event'].get('outside-exposure'),
                         plain['review_context_event']['outside-exposure'])

    def test_origin_leaves_out_the_review_rows_of_its_revision_and_later(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        rows = {'before': dict(id='before', recorded_revision=4), 'at': dict(id='at', recorded_revision=5),
                'after': dict(id='after', recorded_revision=6)}
        records = {kind: dict(rows) for kind in protocol.ORIGIN_KINDS}
        records['value_review'] = {'later': dict(id='later', recorded_revision=9)}
        records['review_execution_plan'] = {'plan': dict(id='plan')}
        origin = protocol.read_origin(records, ArtifactStore(Path(root.name)), 5)
        for kind in protocol.ORIGIN_KINDS:
            self.assertEqual(set(origin.records[kind]), {'before'}, kind)
            self.assertEqual(set(records[kind]), {'before', 'at', 'after'}, kind)
        self.assertEqual(origin.records['value_review'], records['value_review'])
        self.assertEqual(origin.records['review_execution_plan'], records['review_execution_plan'])


if __name__ == '__main__':
    unittest.main()
