"""Verbatim Fable v2 text and deterministic task suffix composition."""

import json
from pathlib import Path
import unittest

from research_harness import review_protocol as protocol
from research_harness import review_staged_prompts as prompts


class StagedPromptTests(unittest.TestCase):
    def test_every_text_matches_the_frozen_version_two_copy(self):
        expected = json.loads((Path(__file__).parent / 'fixtures/review-staged-prompts-v2.json').read_text())
        for name, text in expected.items():
            with self.subTest(name=name):
                self.assertEqual(getattr(prompts, name), text)

    def build_prompt(self, kind, scope='joint_original', role='result', **options):
        return protocol.canonical_prompt(role, orchestration='problem_creation',
            staged_task=dict(type=kind, scope=scope, claim_ids=['one'], occurrence_ids=['unit'], group_ids=[]),
            **options)

    def test_composition_order_and_ordinary_final(self):
        prompt = self.build_prompt('final', repair=True)
        texts = [protocol._COMMON_PROMPTS[2], protocol._PROBLEM_PROMPTS['result'],
                 prompts.STAGED_SERIES_PROMPT, prompts.STAGED_RESULT_ROLE_PROMPT,
                 prompts.STAGED_SHARED_RECORDS_PROMPT, prompts.STAGED_ORDINARY_FINAL_PROMPT,
                 prompts.RETRIEVAL_RULES_PROMPT, prompts.ASSESSMENT_INCOMPLETE_PROMPT,
                 prompts.CONTEXT_REPAIR_STAGED_PROMPT]
        positions = [prompt['text'].index(text) for text in texts]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn(protocol.review_excerpts.PHASE_PROMPT, prompt['text'])
        self.assertNotIn(protocol.context_repair.PROMPT, prompt['text'])

    def test_scope_variants_and_adjudication(self):
        cases = [('frame', 'joint_original', prompts.FRAME_TASK_PROMPT),
                 ('read', 'joint_original', prompts.READ_TASK_PROMPT),
                 ('combine', 'joint_card', prompts.COMBINE_TASK_PROMPT),
                 ('combine', 'segmented_card', prompts.COMBINE_SEGMENTED_PROMPT),
                 ('original_check', 'original_segment', prompts.ORIGINAL_CHECK_SEGMENT_ADDITION),
                 ('integrate', 'segmented_original', prompts.CARD_RECONCILIATION_PROMPT),
                 ('integrate', 'joint_original', prompts.INTEGRATE_TASK_PROMPT),
                 ('answer', 'joint_original', prompts.ANSWER_PAGE_TASK_PROMPT)]
        for kind, scope, text in cases:
            with self.subTest(kind=kind, scope=scope):
                self.assertIn(text, self.build_prompt(kind, scope)['text'])
        answer = self.build_prompt('answer', role='adjudicator', repair=True)['text']
        self.assertNotIn(prompts.STAGED_SHARED_RECORDS_PROMPT, answer)
        self.assertIn(prompts.STAGED_ADJUDICATION_ANSWER_ADDITION, answer)
        self.assertIn(prompts.CONTEXT_REPAIR_STAGED_PROMPT, answer)
        for composed, text in ((False, prompts.STAGED_ADJUDICATION_FINAL_PROMPT),
                               (True, prompts.STAGED_ADJUDICATION_TERMINAL_PROMPT)):
            self.assertIn(text, self.build_prompt('final', role='adjudicator', composed=composed)['text'])

    def test_v2_envelope_authentication_and_tampering(self):
        prompt = self.build_prompt('read', claim_range=True)
        assignment = dict(role='result', subject={'kind': 'problem'}, prompt=prompt,
                          staged_task=prompt['parameters']['staged_task'])
        self.assertTrue(protocol._prompt_matches(assignment))
        assignment['prompt'] = dict(prompt, text=prompt['text'] + 'changed')
        self.assertFalse(protocol._prompt_matches(assignment))


if __name__ == '__main__':
    unittest.main()
