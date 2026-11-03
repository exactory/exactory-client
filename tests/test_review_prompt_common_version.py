"""The reviewer prompts carry a versioned common text, and every prompt that 0.55.0 to 0.66.3 recorded stays
verifiable with its recorded bytes."""

import json
from pathlib import Path
import unittest

from research_harness import bar_sources, openness_reading
from research_harness import review_context_repair as context_repair
from research_harness import review_protocol as protocol
from research_harness import review_problem_parts, review_excerpts, review_staged_protocol
from research_harness.errors import ResearchError
from research_harness.evidence import digest


FROZEN_PATH = Path(__file__).resolve().parent / "fixtures" / "review-prompts-0.66.3.json"
FROZEN_0715_PATH = FROZEN_PATH.with_name("review-prompts-0.71.5.json")
SUFFIXES = dict({"earlier-" + str(index): text for index, text in enumerate(context_repair.EARLIER_PROMPTS)},
                current=context_repair.PROMPT)
ADJUDICATOR_BODIES = {"before-not-binding": protocol._PROBLEM_ADJUDICATOR_PROMPT_BEFORE_NOT_BINDING}
PART_TEXTS = {"part-0691": review_problem_parts.PART_PROMPT_0691}
PROBLEM = "problem_creation"
CONTINUATION = {"prior_assignment_id": "assignment-prior"}
REPAIR = {"context_repair": {"assignment_id": "assignment-prior"}}


def render_case(case, **common):
    options = dict(case["options"], **common)
    if "repair_prompt" in options:
        options["repair_prompt"] = SUFFIXES[options["repair_prompt"]]
    if "adjudicator_prompt" in options:
        options["adjudicator_prompt"] = ADJUDICATOR_BODIES[options["adjudicator_prompt"]]
    if options.get("part_suffix") is not None:
        options["part_suffix"] = PART_TEXTS[options["part_suffix"]]
    if case["renderer"] == "previous_problem_result_prompt":
        return protocol.previous_problem_result_prompt(**options)
    return protocol.canonical_prompt(case["role"], **options)


def build_assignment(role, prompt, *, problem=False, repair=False, context=None):
    return {"role": role, "subject": {"kind": "problem" if problem else "dossier"}, "reassessment_required": repair,
            "context": context or {}, "prompt": prompt}


def build_frozen_assignment(case, prompt):
    if "staged_task" in case:
        return dict(role=case["role"], subject={"kind": "problem"}, prompt=prompt,
                    staged_task=case["staged_task"])
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

    def test_every_prompt_recorded_by_0715_still_verifies(self):
        document = json.loads(FROZEN_0715_PATH.read_text(encoding="utf-8"))
        self.assertEqual(document["origin"], "ee36aa3 (Exactory 0.71.5)")
        self.assertEqual(len(document["cases"]), 182)
        self.assertEqual(len(document["cases"]), sum(document["family_counts"].values()))
        staged = [case for case in document["cases"] if case["family"] == "staged"]
        self.assertEqual(len(staged), 46)
        self.assertEqual(len({case["envelope"]["rendered_prompt_hash"] for case in staged}), 32)
        tasks_with_ids = {(case["role"], case["staged_task"]["type"]) for case in staged
                          if all(case["staged_task"][field] for field in ("claim_ids", "occurrence_ids", "group_ids"))}
        self.assertEqual(tasks_with_ids, {(role, kind) for role in ("result", "adjudicator")
                                         for kind in ("frame", "read", "combine", "original_check", "integrate",
                                                      "answer", "final")})
        for case in document["cases"]:
            with self.subTest(family=case["family"], role=case["role"], options=case["options"]):
                prompt = render_case(case, common_version=2)
                self.assertEqual({key: value for key, value in prompt.items() if key != "text"}, case["envelope"])
                assignment = build_frozen_assignment(case, prompt)
                self.assertEqual(protocol._prompt_matches(assignment), case["prompt_matches"])
                self.assertEqual(protocol.is_previous_prompt(assignment), case["is_previous_prompt"])
                self.assertEqual(protocol._uses_previous_repair_suffix(assignment), case["uses_previous_repair_suffix"])
                if "staged_matches_prompt" in case:
                    self.assertEqual(review_staged_protocol.matches_prompt(assignment, 2),
                                     case["staged_matches_prompt"])

    def test_new_assignments_bind_common_version_three(self):
        self.assertEqual(protocol.COMMON_VERSION, 3)
        earlier, current = protocol._COMMON_PROMPTS[2], protocol._COMMON_PROMPTS[3]
        self.assertIn("RETRACTED", current)
        self.assertIn("Call no tool", current)
        for role, options, problem, repair, context in list_current_cases():
            with self.subTest(role=role, options=options):
                new, recorded = protocol.canonical_prompt(role, **options), protocol.canonical_prompt(
                    role, common_version=2, **options)
                self.assertEqual(new["parameters"], dict(recorded["parameters"], common_version=3))
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

    def test_common_text_only_inserts_sentences_and_keeps_pinned_digests(self):
        self.assertEqual({version: digest(text) for version, text in protocol._COMMON_PROMPTS.items()}, {
            1: "959718bf7a487e3335e733fad01f2604d8f6d26ae91923b69fd4091757801ea3",
            2: "849b9ee2e5307f618fe5c7d65839fb2eeb77b03d73edbfa9c06fd0c82a39dc47",
            3: "af4a01085de573e1a9827041139ea85af85112a46130e3a05c29c54f2ba58f9a",
        })
        earlier, current = protocol._COMMON_PROMPTS[2], protocol._COMMON_PROMPTS[3]
        marker = "Start the object at the first column of its line. "
        before, after = earlier.split(marker)
        self.assertTrue(current.startswith(before + marker))
        self.assertTrue(current.endswith(after))
        inserted = current[len(before + marker):-len(after)]
        self.assertTrue(inserted)
        self.assertEqual(current, earlier.replace(marker, marker + inserted, 1))
        self.assertTrue(current.endswith(" "))
        self.assertFalse(current.endswith("  "))

    def test_every_new_prompt_family_uses_common_version_three(self):
        cases = list_current_cases()
        for role in protocol._PROBLEM_PROMPTS:
            for version in range(1, 7):
                if role == "slate" and version == 1:
                    continue
                cases.append((role, dict(orchestration=PROBLEM, version=version), True, False, {}))
        for problem in (False, True):
            for role in bar_sources.ROLES:
                for repair in (False, True):
                    for rules in review_excerpts.PHASE_PROMPTS:
                        options = dict(excerpts=True, excerpt_rules=rules, repair=repair)
                        if problem:
                            options["orchestration"] = PROBLEM
                        cases.append((role, options, problem, repair, REPAIR if repair else CONTINUATION))
        for stage in review_problem_parts.STAGES:
            cases.append(("result", dict(orchestration=PROBLEM, excerpts=True, evidence_parts=stage),
                          True, False, {}))
        for role in ("result", "adjudicator"):
            task = dict(type="final", scope="joint_original", claim_ids=[], occurrence_ids=[], group_ids=[])
            cases.append((role, dict(orchestration=PROBLEM, staged_task=task), True, False, {}))
        for role, options, problem, repair, context in cases:
            with self.subTest(role=role, options=options):
                prompt = protocol.canonical_prompt(role, **options)
                self.assertEqual(prompt["parameters"]["common_version"], 3)
                self.assertTrue(prompt["text"].startswith(protocol._COMMON_PROMPTS[3]))
                recorded = protocol.canonical_prompt(role, common_version=2, **options)
                self.assertEqual(prompt["template_id"], recorded["template_id"])
                self.assertEqual(prompt["text"], protocol._COMMON_PROMPTS[3]
                                 + recorded["text"][len(protocol._COMMON_PROMPTS[2]):])
                assignment = build_assignment(role, prompt, problem=problem, repair=repair, context=context)
                self.assertTrue(protocol._prompt_matches(assignment))

    def test_relabeling_common_versions_two_and_three_is_refused(self):
        self.assertEqual(protocol.COMMON_VERSION, 3)
        for role, options, problem, repair, context in list_current_cases():
            current = protocol.canonical_prompt(role, **options)
            recorded = protocol.canonical_prompt(role, common_version=2, **options)
            unmarked = {key: value for key, value in current["parameters"].items() if key != "common_version"}
            altered = [dict(current, parameters=unmarked),
                       dict(current, parameters=dict(current["parameters"], common_version=2)),
                       rehash(current, current["text"] + "changed"),
                       dict(recorded, parameters=dict(recorded["parameters"], common_version=3))]
            for prompt in altered:
                with self.subTest(role=role, options=options, parameters=prompt["parameters"]):
                    assignment = build_assignment(role, prompt, problem=problem, repair=repair, context=context)
                    self.assertFalse(protocol._prompt_matches(assignment))

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
        for value in (0, 4, True, "3"):
            with self.subTest(value=value):
                with self.assertRaises(ResearchError) as refused:
                    protocol.canonical_prompt("bar", common_version=value)
                self.assertEqual(refused.exception.code, "invalid_review_prompt_version")


if __name__ == "__main__":
    unittest.main()
