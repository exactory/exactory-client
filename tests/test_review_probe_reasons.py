"""A probe record states why it does not verify its route record."""

import copy
import unittest
from unittest.mock import patch

import codex_route_fixtures
from literature_fixtures import LiteratureCase
from research_harness import review_codex_transport as codex, review_excerpts
from research_harness import review_protocol as protocol, review_transport as transport
from research_harness.errors import ResearchError
from research_harness.evidence import digest


class ProbeReasonTests(LiteratureCase):
    def setUp(self):
        super().setUp()
        codex_route_fixtures.install_codex_runtime(self)
        self.mutate(protocol.record_route, dict(id='route', adapter=codex.ADAPTER, model='unlisted-model',
            endpoint='codex://local', configuration=dict(max_output_tokens=1000, timeout_seconds=5)))

    def probe(self, identifier, *, change_rollout=None, change_output=None, failure=None, capacity=None):
        def send(route, request, credential):
            if failure is not None:
                raise failure
            packet = review_excerpts.read_sent_packet(request)
            output = dict(allowed_control=packet['allowed_control'], evidence_control=packet['evidence_control'],
                          excluded_controls=[])
            if change_output is not None:
                change_output(output)
            response = codex_route_fixtures.build_codex_response(route, request, output)
            response['usage']['input_tokens'] = 5000
            if change_rollout is not None:
                events = copy.deepcopy(codex._rollout.return_value)
                change_rollout(events)
                evidence = codex.inspect_rollout(events, request, response['id'], '/fixture', [])
                evidence.update(inspected=True, route_digest=digest(route), runtime=codex.runtime(),
                                event_digest=digest(events))
                response['context_evidence'] = evidence
                codex._rollout.return_value = events
            return response
        payload = dict(id=identifier, route_id='route')
        if capacity is not None:
            payload['capacity'] = dict(input_tokens=capacity)
        with patch.object(transport, 'send_request', side_effect=send):
            return self.mutate(protocol.probe_route, payload)['result']

    def test_a_verified_probe_records_no_reason(self):
        self.assertEqual(self.probe('verified')['unverified_reasons'], [])

    def test_an_unaudited_developer_input_is_named_and_verifies_no_capacity(self):
        unknown = {'type': 'response_item', 'payload': {'type': 'message', 'role': 'developer',
                   'content': [{'type': 'input_text', 'text': '<unaudited_role>synthetic</unaudited_role>'}]}}
        result = self.probe('unaudited', change_rollout=lambda events: events.insert(2, unknown), capacity=5000)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['unverified_reasons'], ['context:unknown_developer_input'])
        self.assertFalse(result['capacity_verified'])

    def test_a_failed_call_a_wrong_control_and_another_effort_are_named(self):
        cases = (('failed', dict(failure=ResearchError('review_provider_failure', 'The provider returned HTTP 400'))),
                 ('control', dict(change_output=lambda output: output.update(allowed_control='0' * 32))),
                 ('effort', dict(change_rollout=lambda events: events[1]['payload'].update(effort='xhigh'))))
        for (name, change), expected in zip(cases, (['call_incomplete'], ['control_mismatch'],
                                                    ['context:turn_effort'])):
            with self.subTest(name):
                self.assertEqual(self.probe(name, **change)['unverified_reasons'], expected)


if __name__ == '__main__':
    unittest.main()
