"""The released value of NEW_MATH_CHAIN_PROTOCOLS, and the bytes of protocol v7 after its description table.

The fixture openness-v7-problem-chain-golden.json holds what the base of lane d1-reading-math-v2 (the head of the
0.92.0 branch before the lane) built for the chain below: the inventory, plan and state digests, the frames of the
plan, and the packet, prompt and request digest of a reading turn and of a final.
"""

import json
from pathlib import Path
import unittest
from unittest.mock import patch

from test_openness_carried_math import MathCarriedCase
from test_openness_reading_v7_turns import MemoryArtifacts, build_native
from openness_legacy_fixtures import load_0_87_0_openness_reading
from v7_capacity_fixtures import build_route
from research_harness import openness_reading as reading, openness_reading_v7 as v7
from research_harness import review_protocol as protocol, review_route_capacity as capacity
from research_harness.evidence import digest
from research_harness.problem_openness import serialize_record

GOLDEN_PATH = Path(__file__).parent / 'fixtures/openness-v7-problem-chain-golden.json'


def build_problem_chain():
    """Build a problem v7 chain through the real inventory, admission and packet builders."""
    artifacts = MemoryArtifacts()
    native = dict(build_native(targets=3), kind='result', contract=dict(orchestration='problem_creation'))
    native['scientific_context']['openness_minimum'].update(supporting_reading_digests=[], duplicate_resolutions=[],
                                                            family_readings=[], settling_work_id=None)
    native['subject'] = dict(id='statement-1', statement='A synthetic exact statement.')
    subject = artifacts.put(serialize_record(native['subject']), 'application/json')
    native.update(subject_digest=subject['sha256'], evidence=[dict(kind='blob', digest=subject['sha256'], artifact=subject)])
    native['scientific_context']['audit_forecast']['notices'] = [dict(kind='fixture', text='A forecast notice.')]
    native['scientific_context']['minimum_gaps'] = [dict(formulation='A synthetic exact statement.', condition='A gap.',
                                                         outcomes=dict(supported_open='blocked'))]
    route = build_route('codex_cli_v1', 'gpt-6-astra')
    logical = dict(protocol=protocol.PROTOCOL, role='native_math', reviewer_id=native['reviewer'], dossier_id=None,
                   native=native)
    admitted = v7.check_v7_admission({}, artifacts, route, native, finding_version=1)
    packet, chain = v7.prepare_first_assignment({}, artifacts, dict(id='golden-chain'), route, logical, [],
                                                admission=admitted)
    inventory = json.loads(artifacts.read(chain['inventory']))
    state = json.loads(artifacts.read(chain['state']))
    plan = admitted['capacity_plan']
    values = dict(inventory=digest(inventory), plan=digest(plan), state=digest(state), frames=plan['frame'])
    state['deliveries'] = [dict(unit=row['handle'], start=0, end=row['characters']) for row in inventory['material']]
    final = v7.build_turn_packet(artifacts, route, logical, 'golden-chain', 2, inventory, state, plan,
                                 capacity.derive_plan_task_limits(plan, 'final'), 'final')
    for stage, value in [('reading', packet), ('final', final)]:
        prompt = protocol.canonical_prompt('native_math', reading_protocol=v7.PROTOCOL, reading_stage=stage,
                                           common_version=1)
        values[stage] = dict(packet=value, prompt=prompt,
                             request_digest=digest(protocol.transport.request_body(route, prompt['text'], value)))
    return values


class ProblemV7BytesTests(unittest.TestCase):
    def test_problem_v7_inventory_plan_packets_prompts_and_request_digests_equal_the_base(self):
        self.assertEqual(json.loads(json.dumps(build_problem_chain())), json.loads(GOLDEN_PATH.read_text()))


class MathV1DefaultTests(MathCarriedCase):
    route_output_tokens = 128000

    def compare_with_released(self, reference, identifier, prior=None):
        records = self.store.snapshot()['records']
        value = dict(id=identifier, role='native_math', route_id='route', dossier_id=None,
            reviewer_id='openness-reviewer', author_id='problem-author', context=dict(native_packet=reference))
        if prior is not None:
            value['context']['prior_assignment_id'] = prior
        route = records['review_route']['route']
        logical = protocol.build_packet(records, self.artifacts, None, 'native_math', value['reviewer_id'], **value['context'])
        expected, _ = load_0_87_0_openness_reading().prepare_packet(records, self.artifacts, value, route, logical)
        current, _ = reading.prepare_packet(records, self.artifacts, value, route, logical)
        self.assertEqual(current, expected)
        actual = self.assign(identifier, reference, prior)
        self.assertEqual(actual['packet_digest'], expected['digest'])
        self.assertEqual(actual['prompt_hash'], protocol.canonical_prompt('native_math',
            reading_stage=reading.find_stage(expected), reading_protocol=reading.MATH_PROTOCOL)['rendered_prompt_hash'])
        self.assertEqual(json.loads(self.artifacts.read(actual['packet'])), expected)
        return actual

    def test_default_new_math_chain_is_the_released_v1_chain(self):
        self.assertEqual(reading.NEW_MATH_CHAIN_PROTOCOLS, (reading.MATH_PROTOCOL,))
        self.compare_with_released(self.build_math_packet(), 'math-1')

    def test_v1_chain_in_progress_continues_byte_for_byte_under_either_order(self):
        native = self.build_math_packet()
        self.assign('math-1', native)
        self.invoke('math-1', self.build_reading_response(self.read_packet('math-1')))
        records = self.store.snapshot()['records']
        value = dict(id='math-2', role='native_math', route_id='route', dossier_id=None,
            reviewer_id='openness-reviewer', author_id='problem-author',
            context=dict(native_packet=native, prior_assignment_id='math-1'))
        logical = protocol.build_packet(records, self.artifacts, None, 'native_math', value['reviewer_id'], **value['context'])
        built = []
        for protocols in ((reading.MATH_PROTOCOL,), (reading.MATH_PROTOCOL_V2, reading.MATH_PROTOCOL)):
            with patch.object(reading, 'NEW_MATH_CHAIN_PROTOCOLS', protocols):
                built.append(reading.prepare_packet(records, self.artifacts, value, records['review_route']['route'], logical))
        self.assertEqual(built[0], built[1])
        with patch.object(reading, 'NEW_MATH_CHAIN_PROTOCOLS', (reading.MATH_PROTOCOL_V2, reading.MATH_PROTOCOL)):
            self.compare_with_released(native, 'math-2', 'math-1')


if __name__ == '__main__':
    unittest.main()
