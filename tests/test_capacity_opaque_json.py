"""Refused JSON has an explicit serialized bound while packet siblings stay closed."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from ci.capacity_packet_inventory import read_packet_registry, validate_packet


def build_registry(limit=40):
    return {'roles': {'result': {'members': {
        'value': {'class': 'fixed', 'opaque_json': {
            'limit_characters': limit,
            'provenance': 'research_harness.review_refusal_notice.build_refusal_notice'}},
        'closed': {'members': {'known': {'class': 'fixed'}}},
    }}}}


class OpaqueJsonTests(unittest.TestCase):
    def test_fixed_bound_counts_actual_serialization_and_keeps_siblings_closed(self):
        registry = build_registry()
        value = {'arbitrary': [{'unknown': 'λ"'}]}
        size = len(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))
        registry['roles']['result']['members']['value']['opaque_json']['limit_characters'] = size
        self.assertEqual(validate_packet('result', {'value': value}, registry), [])
        smaller = copy.deepcopy(registry)
        smaller['roles']['result']['members']['value']['opaque_json']['limit_characters'] -= 1
        self.assertTrue(validate_packet('result', {'value': value}, smaller))
        self.assertTrue(validate_packet('result', {'value': value, 'closed': {'unknown': 1}}, registry))
        self.assertTrue(validate_packet('result', {'unknown': value}, registry))

    def test_request_bound_requires_positive_finite_context_when_member_is_present(self):
        registry = build_registry('request_limit')
        packet = {'value': {'unexpected': [{'nested': 'bounded refused output'}]}}
        self.assertEqual(validate_packet('result', {'closed': {'known': 1}}, registry), [])
        self.assertTrue(validate_packet('result', packet, registry))
        for limit in (None, 0, -1, True, float('inf'), float('nan'), 1.5, '100'):
            with self.subTest(limit=limit):
                self.assertTrue(validate_packet('result', packet, registry,
                                               request_limit_characters=limit))
        self.assertTrue(validate_packet('result', packet, registry, request_limit_characters=10))
        self.assertEqual(validate_packet('result', packet, registry,
                                        request_limit_characters=1000), [])

    def test_malformed_declarations_cannot_open_a_packet_member(self):
        invalid = ({}, {'limit_characters': 40}, {'provenance': 'reviewed'},
                   {'limit_characters': 0, 'provenance': 'reviewed'},
                   {'limit_characters': True, 'provenance': 'reviewed'},
                   {'limit_characters': float('inf'), 'provenance': 'reviewed'},
                   {'limit_characters': 'unbounded', 'provenance': 'reviewed'},
                   {'limit_characters': 40, 'provenance': ''},
                   {'limit_characters': 40, 'provenance': 'reviewed', 'extra': True})
        for declaration in invalid:
            registry = build_registry()
            registry['roles']['result']['members']['value']['opaque_json'] = declaration
            with self.subTest(declaration=declaration):
                self.assertTrue(validate_packet('result', {'value': 1}, registry))
        registry = build_registry()
        registry['roles']['result']['members']['value']['members'] = {'hidden': {}}
        self.assertTrue(validate_packet('result', {'value': {'hidden': 1}}, registry))

    def test_opaque_member_accepts_json_values_only(self):
        registry = build_registry(1000)
        circular = []
        circular.append(circular)
        for value in (float('nan'), float('inf'), b'bytes', {'set'}, (1, 2),
                      {1: 'nonstring key'}, circular):
            with self.subTest(value_type=type(value).__name__):
                self.assertTrue(validate_packet('result', {'value': value}, registry))

    def test_real_refusal_forms_keep_numeric_caps_and_use_keyed_request_allowance(self):
        from ci.capacity_guard import describe_packet_task
        from capacity_evidence_fixtures import build_capacity_runtime
        from research_harness import review_refusal_notice as notices, review_transport
        from research_harness.artifacts import ArtifactStore
        from research_harness.evidence import digest
        from research_harness.review_capacity_evidence import describe_capacity_key
        registry = read_packet_registry()
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(Path(directory))
            output = {'unrecognized': [{'arbitrary': 'λ"' * 15000}]}
            reference = artifacts.put(json.dumps(output).encode('utf-8'), 'application/json')
            records = {'review_attempt': {}, 'review_output_refusal': {}}
            diagnoses = {}
            for index in range(4):
                identifier = 'refused-' + str(index)
                records['review_attempt'][identifier] = dict(output=reference)
                records['review_output_refusal'][identifier] = dict(assignment_id='prior',
                    attempt_id=identifier, output_digest=digest(output), recorded_revision=index)
                diagnoses[identifier] = dict(code='invalid_value_review', check='review.shape',
                    details=dict(path='/unrecognized', message='Correct the mechanical shape.',
                                 unexpected_fields=['field-' + 'x' * 1000] * 20))
            identifiers = tuple(diagnoses)
            bounded = notices.build_refusal_notice(records, artifacts, identifiers,
                                                   form='bounded', diagnoses=diagnoses)
            self.assertLessEqual(len(notices._serialize_value(bounded)), notices.BOUNDED_NOTICE_CHARACTERS)
            packet = dict(protocol='exactory-problem-review-v2', role='result', output_refusal=bounded)
            self.assertEqual(validate_packet('result', packet, registry), [])
            for member, excessive in (('details', 'x' * 4001), ('refused_part', 'x' * 20001)):
                changed = copy.deepcopy(packet)
                if member == 'details':
                    changed['output_refusal']['refused'][0]['details'] = excessive
                else:
                    changed['output_refusal']['refused_part']['value'] = excessive
                self.assertTrue(validate_packet('result', changed, registry))
            whole = notices.build_refusal_notice(records, artifacts, identifiers,
                                                 form='whole', diagnoses=diagnoses)
            packet['output_refusal'] = whole
            self.assertTrue(validate_packet('result', packet, registry))
            route = dict(id='route', adapter='openai_responses_v1', model='unlisted',
                endpoint='https://capacity.test/responses',
                runtime=build_capacity_runtime('openai_responses_v1'),
                configuration=dict(max_output_tokens=1000, timeout_seconds=5))
            records.update(review_route={'route': route}, review_route_probe={'probe': dict(
                id='probe', route_id='route', capacity=dict(input_tokens=301000), usage={},
                capacity_evidence=dict(key=describe_capacity_key(route), input_tokens=301000))})
            request = review_transport.request_body(route, 'Return JSON.', packet)
            self.assertEqual(describe_packet_task('result', packet, route=route, request=request,
                registry=registry, records=records, artifacts=artifacts)['kind'], 'single')
            changed = copy.deepcopy(route)
            changed['endpoint'] = 'https://another-capacity.test/responses'
            with self.assertRaises(ValueError):
                describe_packet_task('result', packet, route=changed, request=request,
                                     registry=registry, records=records, artifacts=artifacts)


if __name__ == '__main__':
    unittest.main()
