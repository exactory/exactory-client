"""Readiness parts on routes below the session v4 floor."""

import unittest
from unittest.mock import patch

import test_review_parts_study as study_fixtures
from codex_route_fixtures import limit_codex_input, record_codex_route, read_sent_packet
from development_fixtures import DevelopmentCase
from test_review_parts_v3 import make_turn_output
from test_review_route_capacity import build_route
from research_harness import bar_sources, development, review_excerpts, review_field_classes
from research_harness import review_parts as parts, review_parts_v3 as parts_v3
from research_harness import review_protocol as protocol, review_route_capacity as capacity
from research_harness import review_session_v4 as session
from research_harness.errors import ResearchError
from research_harness.evidence import digest


class StudySeriesSecondTurnTests(unittest.TestCase):
    def build_readiness(self, tokens):
        case = study_fixtures.ReadinessPartsTests()
        self.addCleanup(case.doCleanups)
        with patch.object(DevelopmentCase, 'capacity_probe_tokens', 128000 + tokens, create=True):
            case.setUp()
        return case

    def test_study_series_below_v4_floor_reaches_a_recorded_final(self):
        """A readiness candidate and its exact source and result take a read and a final below the v4 floor."""
        case = self.build_readiness(127000)
        readiness = case.logical['readiness']
        inputs = readiness['inputs']
        readiness['inputs'] = dict(candidate=inputs['candidate'],
            synthesis=dict(digest=inputs['synthesis']['digest']),
            branches={'support': dict(id='support', evidence=case.references)})
        readiness['execution_observations'] = {}
        case.logical['intent'] = dict(id='intent')
        case.logical['evidence'] = protocol._evidence(case.case.artifacts, case.logical, records=case.records())
        with patch.object(protocol, 'NEW_SESSION_RULES', (session.PROTOCOL, bar_sources.PROTOCOL_V3)), \
                patch.object(parts, 'NEW_SERIES_PROTOCOLS', (parts_v3.PROTOCOL, parts.PROTOCOL)), \
                patch.object(study_fixtures, 'make_output', side_effect=make_turn_output):
            first = case.assign()
            self.assertEqual(first['session_admission']['reason'], 'below_floor')
            self.assertEqual(first[parts.KEY]['protocol'], parts_v3.PROTOCOL)
            self.assertEqual(first[parts.KEY]['kind'], 'read')
            turns, final = case.finish(first)
            self.assertIn(session.STUDY_FORMS_PROMPT.lstrip(), final['prompt']['text'])
            output = case.final_output(final, all_citations=True)
            case.invoke(final, output)
            records = case.records()
            verified = parts.verify_series(records, case.case.artifacts, final['id'])
            self.assertTrue(verified['complete'], verified['issues'])
            _, observed = protocol.read_assignment_output(records, case.case.artifacts, final['id'])
            support = observed['support']
            case.case.mutate(development.record_readiness_review, support)
            self.assertEqual(case.records()['readiness_review'][support['id']]['payload'], support)

        self.assertGreaterEqual(len(turns) + 1, 2)
        for assignment in turns + [final]:
            request, packet = read_sent_packet(case.case, 'attempt-' + assignment['id'])
            self.assertEqual(packet[parts.KEY]['series_assignment_id'], first['id'])
            for field in ('protocol', 'role', 'reviewer_id', 'dossier_id'):
                self.assertEqual(packet[field], case.logical[field])
            measure = capacity.measure_request(records['review_route']['route'], request,
                                               records=records, artifacts=case.case.artifacts)
            self.assertTrue(measure['fits'], measure)
            self.assertLessEqual(measure['text_characters'],
                                 packet[parts.KEY]['capacity']['request_bound']['text_characters'])

    def test_base_capacity_stop_lists_a_serving_route(self):
        """The excerpt-bearing readiness packet forces one measured search for a serving route."""
        case = self.build_readiness(60000)
        case.logical[review_excerpts.KEY] = review_excerpts.carry_session(case.records(), case.case.artifacts, {}, {})
        limit_codex_input(case, case.measure(case.logical) // 2)
        with patch.object(case.case, 'capacity_probe_tokens', 255000, create=True):
            record_codex_route(case.case, 'serving', output_tokens=128000, model='serving-model')
        records, artifacts = case.records(), case.case.artifacts
        packet = dict(case.logical)
        packet['digest'] = digest(packet)
        assignment = dict(id='first', route_id='route', role='result', dossier_id=case.dossier_id,
                          reviewer_id=packet['reviewer_id'], author_id='external-author', context={})
        prompt = protocol.canonical_prompt('result', excerpts=True)
        request = protocol.transport.request_body(records['review_route']['route'], prompt['text'], packet, [])
        revision = case.case.store.revision
        with patch.object(protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,)), \
                patch.object(parts, 'NEW_SERIES_PROTOCOLS', (parts_v3.PROTOCOL,)), \
                self.assertRaises(ResearchError) as raised:
            capacity.require_covered_assignment(records, artifacts, assignment, packet, prompt, request)
        error = raised.exception
        self.assertEqual(error.code, 'review_route_capacity_below_floor')
        self.assertEqual(error.details['reason'], 'no_parts_delivery')
        self.assertEqual([(row['route_id'], row['delivery']) for row in error.details['serving_routes']],
                         [('serving', 'parts')])
        self.assertEqual(case.case.store.revision, revision)

    def test_unknown_packet_field_refuses_with_a_code(self):
        packet = dict(protocol=protocol.PROTOCOL, role='verification', undeclared_field=True)
        with self.assertRaises(ResearchError) as raised:
            review_field_classes.classify_packet(build_route(), 'verification', packet)
        self.assertEqual(raised.exception.code, 'review_input_unreadable')
        self.assertEqual(raised.exception.details['field'], 'undeclared_field')
