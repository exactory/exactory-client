"""An accepted output correction is authenticated by the runtime that its route record pins.

The installed reviewer runtime and the installed reviewer program decide whether a new correction call starts. They
do not decide whether a stored correction is selected. The first class reads and writes the store of
test_review_output_correction, whose route is an HTTPS route. The second class builds the same position on a Claude
route with the fixture of test_review_route_renewal, because the Claude runtime reads no program.
"""

import copy
import unittest
from unittest.mock import patch

from research_harness import record_check, review_output_correction as correction
from research_harness import review_claude_transport as claude
from research_harness import review_protocol as protocol, review_transport as transport
from research_harness.errors import ResearchError
from research_harness.evidence import digest
import test_review_output_correction as fixtures
import test_review_route_renewal as renewal

MISSING_RUNTIME_ERROR = ResearchError('review_runtime_missing', 'The CLI is not installed')


class RecordedRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.CorrectionTests()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.case.prepare_refused()
        self.fixture = self.case.fixture
        self.assignment_id = self.case.case.assignment['id']
        self.pinned = copy.deepcopy(self.fixture.read_records()['review_route']['route']['runtime'])

    def install_runtime(self, runtime):
        """Replace the installed runtime inside a with block: a runtime, or an error that its reader raises."""
        reader = runtime if isinstance(runtime, ResearchError) else (lambda route=None: copy.deepcopy(runtime))
        return patch.object(transport, 'runtime', side_effect=reader)

    def read_state(self):
        return protocol.assignment_state(self.fixture.read_records(), self.fixture.artifacts, self.assignment_id)

    def invoke(self, identifier):
        return self.fixture.mutate(correction.invoke_correction,
            {'id': identifier, 'assignment_id': self.assignment_id, 'route_id': 'route'}, credential='test-only')

    def test_accepted_correction_stays_selected_under_every_other_installed_runtime(self):
        self.assertEqual(self.case.correct(self.case.delta())['correction_status'], 'accepted')
        records, artifacts = self.fixture.read_records(), self.fixture.artifacts
        expected = self.read_state()
        self.assertTrue(expected['ready'], expected['obligations'])
        self.assertEqual(expected['output_correction_id'], 'correction')
        installed = [dict(self.pinned, **{member: 'changed'}) for member in sorted(self.pinned) if member != 'adapter']
        self.assertGreaterEqual(len(installed), 4)
        for runtime in installed + [MISSING_RUNTIME_ERROR]:
            with self.subTest(installed=runtime), self.install_runtime(runtime):
                selected = correction.selected_correction(records, artifacts, self.assignment_id)
                self.assertIsNotNone(selected)
                self.assertEqual(selected['id'], 'correction')
                self.assertEqual(self.read_state(), expected)
                self.assertEqual(record_check.describe_correction_observation(
                    records, artifacts, records['review_output_correction_invocation']['correction'],
                    records['review_output_correction']['correction']), {'authenticated': True, 'status': 'completed'})
                self.assertFalse(protocol.has_exhausted_position(
                    records, artifacts, records['review_assignment'][self.assignment_id]))

    def test_correction_recorded_under_a_runtime_that_its_route_does_not_pin_is_refused(self):
        self.assertEqual(self.case.correct(self.case.delta())['correction_status'], 'accepted')
        records = copy.deepcopy(self.fixture.read_records())
        other = dict(self.pinned, implementation_sha256='0' * 64)
        records['review_output_correction']['correction']['runtime'] = other
        # The installed runtime equals the changed record, so only the pinned runtime can refuse it.
        with self.install_runtime(other):
            self.assertIsNone(correction.selected_correction(records, self.fixture.artifacts, self.assignment_id))

    def test_stale_route_refuses_the_correction_before_any_record_and_keeps_it_available(self):
        with self.install_runtime(dict(self.pinned, implementation_sha256='0' * 64)), \
                patch('research_harness.review_transport.send_request') as send:
            with self.assertRaises(ResearchError) as caught:
                self.invoke('on-stale-route')
        self.assertEqual(caught.exception.code, 'review_route_runtime_stale')
        send.assert_not_called()
        records = self.fixture.read_records()
        self.assertNotIn('on-stale-route', records.get('review_output_correction_invocation', {}))
        self.assertNotIn('on-stale-route', records.get('review_output_correction', {}))
        self.assertEqual(self.case.correct(self.case.delta(), 'after-renewal')['correction_status'], 'accepted')

    def test_missing_program_refuses_the_correction_before_any_record(self):
        with self.install_runtime(MISSING_RUNTIME_ERROR), patch('time.sleep'), \
                patch('research_harness.review_transport.send_request') as send:
            with self.assertRaises(ResearchError) as caught:
                self.invoke('without-program')
        self.assertEqual(caught.exception.code, 'review_runtime_missing')
        send.assert_not_called()
        self.assertNotIn('without-program', self.fixture.read_records().get('review_output_correction_invocation', {}))

    def test_answer_of_a_call_on_the_pinned_runtime_is_accepted_after_the_runtime_changes(self):
        sent = []

        def read_installed_runtime(route=None):
            return dict(self.pinned, implementation_sha256='0' * 64) if sent else copy.deepcopy(self.pinned)

        def send(route, request, credential):
            sent.append(request)
            return self.fixture.build_provider_response(self.case.delta())

        with patch.object(transport, 'runtime', side_effect=read_installed_runtime), \
                patch('research_harness.review_transport.send_request', side_effect=send):
            observed = self.invoke('correction')['result']
            state = self.read_state()
        self.assertEqual(len(sent), 1)
        self.assertEqual(observed['runtime'], self.pinned)
        self.assertEqual(observed['correction_status'], 'accepted', observed['correction_error'])
        self.assertEqual(state['output_correction_id'], 'correction')


class ClaudeRouteAdmissionTests(renewal.RouteRenewalFixture):
    def build_twice_refused_reassessment(self):
        """A result reassessment on the Claude route whose two answers label changed content as confirmed."""
        for reviewer in ('first', 'second'):
            bar = self.assign('bar', reviewer, 'bar-' + reviewer, **self.case.deliver_supporting(reviewer))
            answer = self.corrected()
            self.invoke(bar, answer)
            self.record(bar, answer)
        result = self.assign('result', 'first', 'result-first')
        accepted = self.corrected('result')
        accepted['value']['bar_ids'] = [row['id'] for row in
                                        protocol._read_json(self.case.artifacts, result['packet'])['bars']]
        self.invoke(result, accepted)
        self.record(result, accepted)
        self.case.mutate(protocol.record_context_event, dict(id='exposure', assignment_id=result['id'],
            kind='contamination', source='author_history', reason='Synthetic earlier exposure.',
            evidence=[self.case.supporting]))
        self.probe('route', 'repair-probe')
        repair = self.assign('result', 'first', 'repair-result', context_repair=dict(assignment_id=result['id'],
            event_ids=['exposure'], probe_id='repair-probe', reason='Reassess the retained result in a clean context.',
            evidence=[self.case.supporting]))
        packet = protocol._read_json(self.case.artifacts, repair['packet'])
        output = copy.deepcopy(accepted)
        output['value']['reason'] = 'This retained scientific wording differs from the predecessor.'
        output['reassessment'] = dict(assignment_id=result['id'], findings=[dict(
            review_id=row['id'], disposition='confirmed', reason='The scientific assessment remains unchanged.',
            evidence=copy.deepcopy(accepted['value']['evidence'][:1])) for row in packet['historical_reviews']])
        for identifier in ('refused-one', 'refused-two'):
            self.invoke(repair, output, identifier)
            with self.assertRaises(ResearchError) as caught:
                self.record(repair, output)
            self.assertEqual(caught.exception.code, 'review_reassessment_mismatch')
        delta = dict(source_output_digest=digest(output), findings=[dict(
            review_id=row['review_id'], disposition='revised',
            reason='The retained scientific text differs, so exact confirmation is not applicable.')
            for row in output['reassessment']['findings']])
        return repair, delta

    def invoke_correction(self, repair, identifier):
        return self.case.mutate(correction.invoke_correction,
                                dict(id=identifier, assignment_id=repair['id'], route_id='route'))['result']

    def test_claude_route_without_its_cli_refuses_the_correction_before_any_record(self):
        repair, delta = self.build_twice_refused_reassessment()
        self.assertNotIn('executable', self.records()['review_route']['route']['runtime'])
        calls = len(self.calls)
        with patch.object(claude, '_cli', side_effect=MISSING_RUNTIME_ERROR), patch('time.sleep'):
            with self.assertRaises(ResearchError) as caught:
                self.invoke_correction(repair, 'without-cli')
        self.assertEqual(caught.exception.code, 'review_runtime_missing')
        self.assertEqual(len(self.calls), calls)
        self.assertNotIn('without-cli', self.records().get('review_output_correction_invocation', {}))
        self.outputs.append(delta)
        with patch.object(claude, '_cli', return_value=('/usr/local/bin/claude', '2.1.322 (Claude Code)')):
            self.assertEqual(self.invoke_correction(repair, 'with-cli')['correction_status'], 'accepted')


if __name__ == '__main__':
    unittest.main()
