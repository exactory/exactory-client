"""Evidence-bound rubric grades have independent, non-additive thresholds."""

import copy
import unittest
from unittest.mock import patch

from research_harness import problem_rubric as rubric
from research_harness.errors import ResearchError


def install_rubric_anchors(case):
    """Stand in for problem_rubric.ANCHORS until the controller's rubric texts are merged; a merged constant is kept."""
    if hasattr(rubric, "ANCHORS"):
        return
    items = rubric.ADMISSION_ITEMS + ("significance", "priority", "attempt", "accessibility", "calibration")
    anchors = patch.object(rubric, "ANCHORS", {item: {"definition": "The rubric definition of " + item + ".",
                                                     "threshold": "The posting threshold of " + item + "."}
                                              for item in items}, create=True)
    anchors.start()
    case.addCleanup(anchors.stop)


def passing_grades():
    return {"admission": {"A" + str(index): "pass" for index in range(1, 8)},
            "significance": {"B1": 2, "B2": 0}, "priority": 2, "attempt": "pass",
            "accessibility": "E-F", "calibration": "pass"}


class ProblemRubricTests(unittest.TestCase):
    def test_problem_assurance_vocabulary_is_shared_without_import_cycles(self):
        from research_harness import research_decisions, review_composition, review_staged
        self.assertTrue(hasattr(rubric, 'PROBLEM_CHECKS'))
        self.assertEqual(rubric.PROBLEM_CHECKS,
                         ('problem_statement', 'problem_openness', 'problem_value', 'problem_citations', 'stop'))
        self.assertIs(research_decisions.PROBLEM_CHECKS, rubric.PROBLEM_CHECKS)
        self.assertIs(review_composition.ASSURANCES, rubric.PROBLEM_CHECKS)
        self.assertIs(review_staged.ASSURANCES, rubric.PROBLEM_CHECKS)

    def test_full_grades_validate_and_reject_each_bad_value(self):
        grades = passing_grades()
        self.assertEqual(rubric.validate_grades(grades), grades)
        cases = []
        for path, bad in ((('significance', 'B1'), 4), (('admission', 'A1'), 'yes'),
                          (('accessibility',), 'E-X'), (('priority',), True),
                          (('priority',), 2.0), (('attempt',), 'passed'),
                          (('calibration',), None)):
            changed = copy.deepcopy(grades)
            parent = changed if len(path) == 1 else changed[path[0]]
            parent[path[-1]] = bad
            cases.append(changed)
        missing = copy.deepcopy(grades)
        del missing['admission']['A6']
        cases.extend([missing, dict(grades, extra=0), None, [], {}])
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises(ResearchError) as caught:
                rubric.validate_grades(bad)
            self.assertEqual(caught.exception.code, 'invalid_rubric_grades')

    def test_thresholds_pass_at_the_minimum(self):
        self.assertEqual(rubric.assess_grades(passing_grades()), [])

    def test_no_footholds_requires_level_three(self):
        for accessibility in ('E-A', 'E-N'):
            grades = dict(passing_grades(), accessibility=accessibility)
            self.assertEqual(rubric.assess_grades(grades), [
                {'item': 'significance', 'required': 'max(B1, B2) >= 3',
                 'graded': {'B1': 2, 'B2': 0}}])
            grades['significance']['B1'] = 3
            self.assertEqual(rubric.assess_grades(grades), [])

    def test_grounds_never_add(self):
        grades = dict(passing_grades(), significance={'B1': 1, 'B2': 1})
        self.assertEqual(rubric.assess_grades(grades), [
            {'item': 'significance', 'required': 'max(B1, B2) >= 2',
             'graded': {'B1': 1, 'B2': 1}}])
        grades['significance'] = {'B1': 0, 'B2': 2}
        self.assertEqual(rubric.assess_grades(grades), [])

    def test_each_admission_failure_is_a_shortfall(self):
        grades = passing_grades()
        grades['admission'] = dict.fromkeys(rubric.ADMISSION_ITEMS, 'fail')
        grades.update(priority=1, attempt='fail', calibration='fail')
        self.assertEqual(rubric.assess_grades(grades), [
            {'item': item, 'required': 'pass', 'graded': 'fail'} for item in rubric.ADMISSION_ITEMS
        ] + [{'item': 'priority', 'required': '>= 2', 'graded': 1},
             {'item': 'attempt', 'required': 'pass', 'graded': 'fail'},
             {'item': 'calibration', 'required': 'pass', 'graded': 'fail'}])

    def test_potential_grades(self):
        grades = {'significance': {'B1': 2, 'B2': 0}, 'priority': 0}
        self.assertEqual(rubric.validate_grades(grades, potential=True), grades)
        self.assertEqual(rubric.assess_potential(grades, require_readiness=False), [])
        self.assertEqual(rubric.assess_potential(grades), [
            {'item': 'readiness', 'required': 'pass', 'graded': None}])
        grades['readiness'] = 'pass'
        self.assertEqual(rubric.assess_potential(grades), [])
        grades['significance'] = {'B1': 1, 'B2': 1}
        self.assertEqual([item['item'] for item in rubric.assess_potential(grades)], ['significance'])
        for bad in (dict(grades, readiness='yes'), dict(grades, attempt='pass'),
                    {'significance': {'B1': 2}, 'priority': 2},
                    dict(grades, priority=4)):
            with self.subTest(bad=bad), self.assertRaises(ResearchError) as caught:
                rubric.validate_grades(bad, potential=True)
            self.assertEqual(caught.exception.code, 'invalid_rubric_grades')

    def test_rubric_objection_ids_round_trip(self):
        for item in rubric.ADMISSION_ITEMS + ('significance', 'priority', 'attempt', 'calibration'):
            identifier = rubric.rubric_objection_id('review:one', item)
            self.assertEqual(identifier, 'rubric:review:one:' + item)
            self.assertEqual(rubric.parse_rubric_objection_id(identifier), ('review:one', item))
        for bad in (None, '', 'ordinary', 'rubric::priority', 'rubric:r:B1', 'rubric:r:extra'):
            self.assertIsNone(rubric.parse_rubric_objection_id(bad))
