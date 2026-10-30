"""The reviewer prompts carry a versioned common text, and every prompt that 0.55.0 to 0.66.3 recorded stays
verifiable with its recorded bytes."""

import json
from pathlib import Path
import unittest

from research_harness import bar_sources, openness_reading
from research_harness import review_context_repair as context_repair
from research_harness import review_protocol as protocol
from research_harness.errors import ResearchError
from research_harness.evidence import digest


FROZEN_PATH = Path(__file__).resolve().parent / "fixtures" / "review-prompts-0.66.3.json"
SUFFIXES = dict({"earlier-" + str(index): text for index, text in enumerate(context_repair.EARLIER_PROMPTS)},
                current=context_repair.PROMPT)
ADJUDICATOR_BODIES = {"before-not-binding": protocol._PROBLEM_ADJUDICATOR_PROMPT_BEFORE_NOT_BINDING}
PROBLEM = "problem_creation"
CONTINUATION = {"prior_assignment_id": "assignment-prior"}
REPAIR = {"context_repair": {"assignment_id": "assignment-prior"}}


def render_case(case, **common):
    options = dict(case["options"], **common)
    if "repair_prompt" in options:
        options["repair_prompt"] = SUFFIXES[options["repair_prompt"]]
    if "adjudicator_prompt" in options:
        options["adjudicator_prompt"] = ADJUDICATOR_BODIES[options["adjudicator_prompt"]]
    if case["renderer"] == "previous_problem_result_prompt":
        return protocol.previous_problem_result_prompt(**options)
    return protocol.canonical_prompt(case["role"], **options)


def build_assignment(role, prompt, *, problem=False, repair=False, context=None):
    return {"role": role, "subject": {"kind": "problem" if problem else "dossier"}, "reassessment_required": repair,
            "context": context or {}, "prompt": prompt}


def build_frozen_assignment(case, prompt):
    return build_assignment(case["role"], prompt, problem=case["problem"], repair=case["reassessment_required"],
                            context=case["context"])


def list_current_cases():
    """The renderings that a new assignment receives: (role, options, problem, repair, context)."""
    cases = [(role, {}, False, False, {}) for role in protocol.ROLES]
    for role in bar_sources.ROLES:
        cases += [(role, {"repair": True}, False, True, {}),
                  (role, {"excerpts": True}, False, False, CONTINUATION),
                  (role, {"repair": True, "excerpts": True}, False, True, REPAIR)]
    for role in protocol._PROBLEM_PROMPTS:
        cases += [(role, {"orchestration": PROBLEM}, True, False, {}),
                  (role, {"orchestration": PROBLEM, "repair": True}, True, True, {}),
                  (role, {"orchestration": PROBLEM, "excerpts": True}, True, False, CONTINUATION),
                  (role, {"orchestration": PROBLEM, "repair": True, "excerpts": True}, True, True, REPAIR)]
    cases.append(("adjudicator", {"orchestration": PROBLEM}, True, False, {}))
    cases += [("native_math", {"reading_stage": stage}, False, False, {}) for stage in openness_reading.STAGES]
    return cases


def rehash(prompt, text):
    return dict(prompt, text=text, template_hash=digest(text), rendered_prompt_hash=digest(text))


class CommonPromptVersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frozen = json.loads(FROZEN_PATH.read_text(encoding="utf-8"))["cases"]

    def test_every_prompt_recorded_by_0663_still_verifies(self):
        self.assertEqual(len(self.frozen), 126)
        for case in self.frozen:
            with self.subTest(template=case["envelope"]["template_id"], options=case["options"]):
                prompt = render_case(case, common_version=1)
                self.assertEqual({key: prompt[key] for key in case["envelope"]}, case["envelope"])
                assignment = build_frozen_assignment(case, prompt)
                self.assertTrue(protocol._prompt_matches(assignment))
                self.assertEqual(protocol.is_previous_prompt(assignment), case["is_previous_prompt"])
                self.assertEqual(protocol._uses_previous_repair_suffix(assignment),
                                 case["uses_previous_repair_suffix"])

    def test_new_assignments_bind_common_version_two(self):
        self.assertEqual(protocol.COMMON_VERSION, 2)
        earlier, current = protocol._COMMON_PROMPTS[1], protocol._COMMON_PROMPTS[2]
        self.assertIn("RETRACTED", current)
        self.assertIn("Call no tool", current)
        for role, options, problem, repair, context in list_current_cases():
            with self.subTest(role=role, options=options):
                new, recorded = protocol.canonical_prompt(role, **options), protocol.canonical_prompt(
                    role, common_version=1, **options)
                self.assertEqual(new["parameters"], dict(recorded["parameters"], common_version=2))
                self.assertNotIn("common_version", recorded["parameters"])
                self.assertEqual(new["template_id"], recorded["template_id"])
                self.assertTrue(recorded["text"].startswith(earlier))
                self.assertEqual(new["text"], current + recorded["text"][len(earlier):])
                self.assertEqual((new["template_hash"], new["rendered_prompt_hash"]),
                                 (digest(new["text"]), digest(new["text"])))
                assignments = [build_assignment(role, prompt, problem=problem, repair=repair, context=context)
                               for prompt in (new, recorded)]
                self.assertEqual([protocol._prompt_matches(assignment) for assignment in assignments], [True, True])
                # The common text does not decide the scientific contract.
                self.assertEqual(protocol.is_previous_prompt(assignments[0]),
                                 protocol.is_previous_prompt(assignments[1]))

    def test_recorded_prompt_changes_are_refused(self):
        for case in self.frozen:
            prompt = render_case(case, common_version=1)
            parameters = prompt["parameters"]
            changed = [rehash(prompt, "X" + prompt["text"]),
                       rehash(prompt, protocol._COMMON_PROMPTS[2] + prompt["text"][len(protocol._COMMON_PROMPTS[1]):])]
            changed += [dict(prompt, parameters=dict(parameters, common_version=value)) for value in (1, 3, True, 2.0)]
            for altered in changed:
                with self.subTest(template=case["envelope"]["template_id"], options=case["options"],
                                  parameters=altered["parameters"]):
                    self.assertFalse(protocol._prompt_matches(build_frozen_assignment(case, altered)))
        for role, options, problem, repair, context in list_current_cases():
            new = protocol.canonical_prompt(role, **options)
            unmarked = dict(new, parameters={key: value for key, value in new["parameters"].items()
                                             if key != "common_version"})
            for altered in (unmarked, rehash(new, "X" + new["text"])):
                with self.subTest(role=role, options=options, parameters=altered["parameters"]):
                    assignment = build_assignment(role, altered, problem=problem, repair=repair, context=context)
                    self.assertFalse(protocol._prompt_matches(assignment))

    def test_an_unknown_common_version_is_refused_at_rendering(self):
        for value in (0, 3, True, "2"):
            with self.subTest(value=value):
                with self.assertRaises(ResearchError) as refused:
                    protocol.canonical_prompt("bar", common_version=value)
                self.assertEqual(refused.exception.code, "invalid_review_prompt_version")


if __name__ == "__main__":
    unittest.main()
