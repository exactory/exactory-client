"""Replay v4 requests built by c408fea, with native support, selected sources and retained text."""

import copy
import json
import unittest
from unittest.mock import patch

from codex_route_fixtures import invoke_codex, record_codex_route
from openness_legacy_fixtures import load_0_71_8_openness_reading
from research_harness import openness_reading as reading, review_excerpts, review_protocol as protocol
from research_harness import problem_openness
from research_harness.errors import ResearchError
import test_problem_openness_source_delivery as sources


class V4ReplayTests(unittest.TestCase):
    def setUp(self):
        self.fixture = sources.SourceDeliveryTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.case = self.fixture.case
        record_codex_route(self.case, 'codex-route')
        self.released = load_0_71_8_openness_reading()

    def assign(self, identifier, reference, prior=None):
        context = {'native_packet': reference}
        if prior:
            context['prior_assignment_id'] = prior
        return self.case.mutate(protocol.record_assignment, {'id': identifier, 'route_id': 'codex-route',
            'dossier_id': None, 'role': 'native_math', 'reviewer_id': 'openness-assessor',
            'author_id': 'problem-author', 'context': context})['result']

    def packet(self, identifier):
        assignment = self.case.store.snapshot()['records']['review_assignment'][identifier]
        return json.loads(self.case.artifacts.read(assignment['packet']))

    def response(self, packet):
        binding = packet.get('reading_turn', packet.get('native'))
        anchor = 'Complete authored source λ.'
        return dict({key: binding[key] for key in ('subject_digest', 'claim_digest', 'reviewer')},
            schema_version=1, stage='reading', notes='Read the original selected text and exact bound records.',
            excerpts=[{'version_id': self.fixture.work, 'text': anchor, 'purpose': 'The selected source.'}],
            passage_requests=[{'id': 'source-passage', 'version_id': self.fixture.work,
                               'anchor': anchor, 'characters': 60}])

    def start(self):
        native = self.fixture.export('v4-first', priority=True)
        reference = self.case.pin_json(native)
        with patch.object(self.released.review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 3000), \
                patch.object(protocol, 'openness_reading', self.released):
            self.assign('v4-first', reference)
        invoke_codex(self.case, 'v4-first', self.response(self.packet('v4-first')))
        return reference

    def finish(self, reference):
        turns = ['v4-first']
        for number in range(2, 15):
            identifier = 'v4-%d' % number
            self.assign(identifier, reference, turns[-1])
            turns.append(identifier)
            packet = self.packet(identifier)
            if reading.find_stage(packet) == 'final':
                invoke_codex(self.case, identifier, self.case.output(packet['native'], 'supported_open'))
                return turns
            invoke_codex(self.case, identifier, self.response(packet))
        self.fail('The v4 chain did not finish')

    def test_stored_v4_packets_requests_prompts_observations_and_finding_replay_unchanged(self):
        with patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 3000):
            # The frozen module has its own excerpt module and the same historical budget for this fixture.
            with patch.object(self.released.review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 3000), \
                    patch.object(protocol, 'openness_reading', self.released):
                reference = self.case.pin_json(self.fixture.export('v4-first', priority=True))
                self.assign('v4-first', reference)
                invoke_codex(self.case, 'v4-first', self.response(self.packet('v4-first')))
                turns = self.finish(reference)
                records = self.case.store.snapshot()['records']
                final = records['review_assignment'][turns[-1]]
                receipts = self.released.verify_chain(records, self.case.artifacts, final)
                saved = self.case.mutate(self.case.load_problem_module().record_openness,
                                        {'assignment_id': turns[-1]})['result']
            before = self.case.store.snapshot()
            blobs = {row['sha256']: self.case.artifacts.read(row)
                     for group in ('review_assignment', 'review_attempt')
                     for record in before['records'][group].values()
                     for key in ('packet', 'request', 'response', 'output')
                     for row in [record.get(key)] if row is not None}
            self.assertEqual(reading.verify_chain(before['records'], self.case.artifacts, final), receipts)
            self.assertEqual(saved['reading_turns'], receipts)
            self.assertEqual(problem_openness.assess_openness(before['records'], self.case.artifacts), [])
            self.assertEqual(self.case.store.snapshot(), before)
            for identifier in turns:
                assignment = before['records']['review_assignment'][identifier]
                stage = reading.find_stage(self.packet(identifier))
                self.assertEqual(assignment['prompt'], protocol.canonical_prompt('native_math', reading_stage=stage, reading_protocol=reading.PROTOCOL_V4))
                self.assertNotIn('excerpt_protocol', reading._find_record(self.packet(identifier)))
            self.assertTrue(self.packet(turns[-1])['reading_record']['excerpts'])
            self.assertTrue(self.packet(turns[-1])['reading_record']['passages'])
            for group in ('review_assignment', 'review_attempt'):
                for record in before['records'][group].values():
                    for key in ('packet', 'request', 'response', 'output'):
                        row = record.get(key)
                        if row:
                            self.assertEqual(self.case.artifacts.read(row), blobs[row['sha256']])
            changed = copy.deepcopy(before['records'])
            changed['review_attempt']['attempt-v4-first']['origin'] = 'imported'
            with self.assertRaises(ResearchError):
                reading.verify_chain(changed, self.case.artifacts, final)

    def test_unfinished_base_v4_chain_continues_under_v4_with_the_same_source_rules(self):
        with patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 3000):
            reference = self.start()
            turns = self.finish(reference)
            for identifier in turns:
                self.assertEqual(reading.find_protocol(self.packet(identifier)), reading.PROTOCOL_V4)
                self.assertNotIn('search_delivery', reading._find_record(self.packet(identifier)))
            records = self.case.store.snapshot()['records']
            reading.verify_chain(records, self.case.artifacts, records['review_assignment'][turns[-1]])
