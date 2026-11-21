"""The must-pass set of a release holds the fixed modules and the test modules that a diff reaches (ci/must_pass.py)."""

import unittest

from ci import must_pass


class MustPassSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = must_pass.describe_test_files(must_pass.ROOT)

    def find(self, *changed):
        return must_pass.find_touched_modules(self.files, changed)

    def test_the_fixed_modules_exist(self):
        for name in must_pass.ALWAYS + must_pass.HARNESS_PINS:
            with self.subTest(module=name):
                self.assertIn(name, self.files)

    def test_an_empty_diff_selects_the_fixed_modules_only(self):
        self.assertEqual(self.find(), set())
        self.assertEqual(must_pass.select_modules(must_pass.ROOT, []), sorted(must_pass.ALWAYS))

    def test_a_changed_fixture_builder_selects_the_test_that_reaches_it_through_other_test_modules(self):
        # 0.91.8 changed a default text of this file. The parity test reaches the file through the fixtures of other
        # test modules, and it failed in the CI because no list of the release held it.
        found = self.find("tests/test_research_problem_claims.py")
        self.assertIn("test_review_request_parity", found)
        self.assertIn("test_problem_accounts", found)
        self.assertNotIn("test_review_request_parity", self.find("tests/test_must_pass.py"))

    def test_a_changed_test_helper_selects_its_importers(self):
        self.assertEqual(self.find("tests/payload_changes.py"), {"test_problem_payload_changes"})

    def test_a_changed_harness_module_selects_the_test_modules_that_import_it(self):
        found = self.find("research_harness/problem_accounts.py")
        self.assertIn("test_problem_accounts", found)
        # The importers of the importers belong to the full suites, not to this set.
        self.assertNotIn("test_review_request_parity", found)

    def test_a_changed_command_and_a_changed_document_select_the_test_modules_that_name_them(self):
        self.assertIn("test_math_problems_cli", self.find("bin/exactory"))
        self.assertIn("test_research_guidance", self.find("docs/research-cli.md"))

    def test_a_change_of_the_harness_or_of_a_fixture_puts_the_slow_pins_first(self):
        for changed in ("research_harness/problem_accounts.py", "tests/fixtures/capacity_packet_schema.json"):
            with self.subTest(changed=changed):
                selected = must_pass.select_modules(must_pass.ROOT, [changed])
                self.assertEqual(tuple(selected[:len(must_pass.HARNESS_PINS)]), must_pass.HARNESS_PINS)
                self.assertTrue(set(must_pass.ALWAYS) <= set(selected))
                self.assertEqual(len(selected), len(set(selected)))
        self.assertFalse(set(must_pass.HARNESS_PINS) & set(must_pass.select_modules(must_pass.ROOT, ["bin/exactory"])))


if __name__ == "__main__":
    unittest.main()
