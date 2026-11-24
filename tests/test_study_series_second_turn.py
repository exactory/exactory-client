"""Readiness parts on routes below the session v4 floor."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_review_parts_study as study_fixtures
from codex_route_fixtures import limit_codex_input, record_codex_route, read_sent_packet
from development_fixtures import DevelopmentCase
from test_review_parts_v3 import make_packet, make_turn_output
from test_review_route_capacity import build_route
from research_harness import bar_sources, research_decisions, review_excerpts
from research_harness import review_parts as parts, review_parts_v3 as parts_v3
from research_harness import review_protocol as protocol, review_route_capacity as capacity
from research_harness import review_session_v4 as session
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.operations import prepared_mutation


class StudySeriesSecondTurnTests(unittest.TestCase):
    def build_readiness(self, tokens):
        case = study_fixtures.ReadinessPartsTests()
        self.addCleanup(case.doCleanups)
        with patch.object(DevelopmentCase, 'capacity_probe_tokens', 128000 + tokens, create=True):
            case.setUp()
        return case

    def test_study_series_below_v4_floor_reaches_a_recorded_final(self):
        """A combined result review records the exact bindings after evidence overflows the frame."""
        # The admission of the parts protocol v3 charges the prompt and the target table of each turn, so the route
        # needs a final room above 129,896 characters; release 0.92.1 observed 127,000 here.
        case = self.build_readiness(140000)
        reviewer = 'r' * 520
        case.logical['assessor']['id'] = reviewer
        case.logical['support_contract']['evidence'] = [dict(reference='r' * 4100)]
        bars = ['bar-first', 'bar-second']
        case.logical['bar_ids'] = bars
        case.logical['bars'] = [dict(id=bar, stage='bar') for bar in bars]
        def prepare_fixture(store, value, **identity):
            def prepare(records, payload):
                dossier = records['strategy_dossier'][case.dossier_id]
                dossier = dict(dossier, payload=dict(dossier['payload'], phase='result'))
                return [('strategy_dossier', case.dossier_id, dict(dossier, digest=digest(dossier)))] + [
                    ('value_review', bar, dict(id=bar, dossier_id=case.dossier_id, assignment_id=bar,
                                             reviewer_id=bar, payload=dict(stage='bar', objections=[])))
                    for bar in bars], payload
            return prepared_mutation(store, 'test.study_series_second_turn', value, prepare, **identity)
        case.case.mutate(prepare_fixture, {})
        readiness = case.logical['readiness']
        inputs = readiness['inputs']
        readiness['inputs'] = dict(candidate=inputs['candidate'],
            synthesis=dict(digest=inputs['synthesis']['digest']),
            branches={'support': dict(id='support', evidence=case.references)})
        readiness['execution_observations'] = {}
        case.logical['intent'] = dict(id='intent')
        case.logical['evidence'] = protocol._evidence(case.case.artifacts, case.logical, records=case.records())
        assign = case.assign
        with patch.object(protocol, 'NEW_SESSION_RULES', (session.PROTOCOL, bar_sources.PROTOCOL_V3)), \
                patch.object(parts, 'NEW_SERIES_PROTOCOLS', (parts_v3.PROTOCOL, parts.PROTOCOL)), \
                patch.object(case, 'assign', side_effect=lambda *args: assign(*args, reviewer_id=reviewer)), \
                patch.object(study_fixtures, 'make_output', side_effect=make_turn_output):
            first = case.assign()
            self.assertEqual(first['session_admission']['reason'], 'below_floor')
            self.assertEqual(first[parts.KEY]['protocol'], parts_v3.PROTOCOL)
            self.assertEqual(first[parts.KEY]['kind'], 'read')
            turns, final = case.finish(first)
            self.assertIn(session.STUDY_FORMS_PROMPT.lstrip(), final['prompt']['text'])
            output = case.final_output(final, all_citations=True)
            frame = case.packet(final)
            output['support']['candidate_digest'] = frame['support_contract'].get(
                'candidate_digest', output['support']['candidate_digest'])
            if 'id' in frame['assessor']:
                output['support']['assessor'] = frame['assessor']
            output['value']['bar_ids'] = bars
            receipt = case.invoke(final, output)
            self.assertEqual(receipt['result']['status'], 'completed', receipt['result'].get('error'))
            self.assertIsNone(receipt.get('output_refusal'))
            records = case.records()
            verified = parts.verify_series(records, case.case.artifacts, final['id'])
            self.assertTrue(verified['complete'], verified['issues'])
            _, observed = protocol.read_assignment_output(records, case.case.artifacts, final['id'])
            support = observed['support']
            payload = dict(observed, id='combined', assignment_id=final['id'], dossier_id=case.dossier_id)
            case.case.mutate(research_decisions.record_value_review, payload)
            self.assertEqual(case.records()['value_review']['combined']['payload'], payload)
            self.assertEqual(case.records()['readiness_review'][support['id']]['payload'], support)

        self.assertGreaterEqual(len(turns) + 1, 2)
        for assignment in turns + [final]:
            request, packet = read_sent_packet(case.case, 'attempt-' + assignment['id'])
            self.assertEqual(packet[parts.KEY]['series_assignment_id'], first['id'])
            for field in ('protocol', 'role', 'dossier_id', 'assessor'):
                self.assertEqual(packet[field], case.logical[field])
            self.assertEqual(packet['reviewer_id'], reviewer)
            self.assertEqual(packet['support_contract']['candidate_digest'], support['candidate_digest'])
            self.assertNotEqual(packet['support_contract']['evidence'], case.logical['support_contract']['evidence'])
            measure = capacity.measure_request(records['review_route']['route'], request,
                                               records=records, artifacts=case.case.artifacts)
            self.assertTrue(measure['fits'], measure)
            self.assertLessEqual(measure['text_characters'],
                                 packet[parts.KEY]['capacity']['request_bound']['text_characters'])

    def test_parts_frames_retain_repair_continuation_and_verification_identities(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(Path(directory))
            route = build_route()
            repair = dict(assignment_id='predecessor', slot_id='slot', probe_id='probe',
                          event_ids=['event-%d' % i for i in range(300)])
            continuation = dict(assignment_id='source-turn', packet_digest='c' * 64, scientific_context='x' * 3000)
            result = dict(make_packet(1, 25), context_repair=repair, continuation=continuation,
                          reviewer_id='reviewer', dossier_id=None, scientific_history=[])
            verification = dict(protocol=protocol.PROTOCOL, role='verification', dossier_id=None,
                                reviewer_id='reviewer', evidence=[], paper=dict(kind='verification',
                                    task_digest='d' * 64, target=dict(id='target'), standards='x' * 210000))
            for packet, adapter, required, unit_paths in (
                    (result, 'context_repair', {'context_repair': ('assignment_id', 'slot_id', 'probe_id'),
                        'continuation': ('assignment_id', 'packet_digest')},
                     {('context_repair', 'event_ids'), ('continuation', 'scientific_context')}),
                    (verification, 'study_verification', {'paper': ('kind', 'task_digest', 'target')},
                     {('paper', 'standards')})):
                with self.subTest(adapter=adapter):
                    admission = parts_v3.admit_packet({}, artifacts, route, packet, adapter)
                    self.assertEqual(admission['state'], 'accept', admission.get('details'))
                    for kind in ('read', 'final'):
                        frame = parts_v3._build_frame(route, admission['inventory'], admission['plan'], kind)
                        for member, keys in required.items():
                            self.assertEqual({key: frame[member].get(key) for key in keys},
                                             {key: packet[member][key] for key in keys})
                    paths = {tuple(row['frame_path']) for row in admission['inventory']['units'] if 'frame_path' in row}
                    self.assertTrue(unit_paths <= paths, paths)

    def refuse_base_assignment(self, serving_probe_tokens):
        """The capacity stop of a first assignment on the route below its floor, beside a route record 'serving' whose
        probe observed serving_probe_tokens. Returns the refusal, the fixture and the revision before the call."""
        case = self.build_readiness(60000)
        case.logical[review_excerpts.KEY] = review_excerpts.carry_session(case.records(), case.case.artifacts, {}, {})
        limit_codex_input(case, case.measure(case.logical) // 2)
        with patch.object(case.case, 'capacity_probe_tokens', serving_probe_tokens, create=True):
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
        return raised.exception, case, revision

    def test_base_capacity_stop_lists_a_serving_route(self):
        """The excerpt-bearing readiness packet forces one measured search for a serving route."""
        # The charged admission of the parts protocol v3 needs a probe of more than 255,000 tokens (release 0.92.1).
        error, case, revision = self.refuse_base_assignment(280000)
        self.assertEqual(error.code, 'review_route_capacity_below_floor')
        self.assertEqual(error.details['reason'], 'no_parts_delivery')
        self.assertEqual([(row['route_id'], row['delivery']) for row in error.details['serving_routes']],
                         [('serving', 'parts')])
        self.assertEqual(case.case.store.revision, revision)

    def test_base_capacity_stop_lists_no_route_whose_probe_holds_no_charged_series(self):
        """At the probe of release 0.92.1 (255,000 tokens) the series of the parts protocol v3 with its charged prompt
        and target table does not fit the route 'serving', so the stop lists no serving route."""
        error, case, revision = self.refuse_base_assignment(255000)
        self.assertEqual((error.code, error.details['reason']),
                         ('review_route_capacity_below_floor', 'no_parts_delivery'))
        self.assertEqual(error.details['serving_routes'], [])
        self.assertEqual(case.case.store.revision, revision)
