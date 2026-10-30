"""The math reading protocol sits beside the problem protocols, whose prompts and passage behavior stay as released."""

import unittest

from openness_reading_fixtures import OpennessReadingCase
from research_harness import openness_reading, native_review, review_protocol as protocol

# The v3 reading-stage prompts as 0.66.3 recorded them.
V3_PROMPT_HASHES = {'reading': 'f0046cd110be7b578a23db1a977fe1ca0d57d24d9a6d8cdf1ec8a2d1b7a7a9c2',
                    'final': '5c52fed6ef690922bc61fcb50cebc3c147db3d47f687d9fd7ee501dd5a19074a'}
UNIT_IDENTITY_FIELDS = ('source_id', 'version_id', 'record_kind', 'record_id', 'text_sha256')


class ReadingProtocolPromptTests(unittest.TestCase):
    def test_the_v3_prompts_keep_their_0_66_3_hashes(self):
        for stage, template_hash in V3_PROMPT_HASHES.items():
            with self.subTest(stage=stage):
                # The hashes pin the renderings of 0.66.3, which opened with the common text of version 1.
                for prompt in (protocol.canonical_prompt('native_math', reading_stage=stage, common_version=1),
                               protocol.canonical_prompt('native_math', reading_stage=stage,
                                                         reading_protocol=openness_reading.PROTOCOL, common_version=1)):
                    self.assertEqual((prompt['template_id'], prompt['template_hash'], prompt['rendered_prompt_hash']),
                                     (openness_reading.PROTOCOL + '/' + stage, template_hash, template_hash))

    def test_the_math_prompts_are_selected_by_their_protocol(self):
        texts = openness_reading.PROMPTS[openness_reading.MATH_PROTOCOL]
        earlier = {protocol.canonical_prompt('native_math', reading_stage=stage,
                                             reading_protocol=rules)['template_hash']
                   for rules in (openness_reading.PROTOCOL_V1, openness_reading.PROTOCOL_V2, openness_reading.PROTOCOL)
                   for stage in openness_reading.STAGES}
        reading, final = (protocol.canonical_prompt('native_math', reading_stage=stage,
                                                    reading_protocol=openness_reading.MATH_PROTOCOL)
                          for stage in openness_reading.STAGES)
        common = reading['text'][:len(reading['text']) - len(texts['reading'])]
        self.assertEqual(reading['text'], common + texts['reading'])
        self.assertEqual(final['text'], common + native_review.PROMPT + texts['final'])
        self.assertTrue(texts['final'].startswith(' ') and not texts['final'].startswith('  '))
        for stage, prompt in (('reading', reading), ('final', final)):
            with self.subTest(stage=stage):
                self.assertEqual(prompt['template_id'], openness_reading.MATH_PROTOCOL + '/' + stage)
                self.assertNotIn(prompt['template_hash'], earlier)
                self.assertNotIn('problem_statement', texts[stage])
                for name in UNIT_IDENTITY_FIELDS:
                    self.assertIn(name, texts[stage])
        self.assertIn('120,000', texts['reading'])


class ProblemPassageBehaviorTests(OpennessReadingCase):
    def test_a_problem_final_reading_response_whose_passage_finds_nothing_continues_as_before(self):
        self.limit_input()
        native_packet = self.export(priority=self.versions)
        final = self.run_reading_turns(native_packet)[-1]
        absent = {'id': 'absent', 'version_id': self.versions[0], 'characters': 90,
                  'anchor': 'This verbatim phrase is absent from every stored text.'}
        self.invoke(final, self.build_reading_response(self.read_packet(final), excerpts=[], passages=[absent]))
        records = self.store.snapshot()['records']
        self.assertNotIn('attempt-' + final, records.get('review_output_refusal', {}))
        self.assertTrue(protocol.assignment_state(records, self.artifacts, final)['ready'])
        self.assign('turn-further', native_packet, prior=final)
        [answer] = self.read_packet('turn-further')['reading_record']['passages']
        self.assertEqual((answer['request_id'], answer['status']), ('absent', 'not_found'))
        self.assertEqual(self.read_packet('turn-further')['reading_record']['protocol'], openness_reading.PROTOCOL)


if __name__ == '__main__':
    unittest.main()
