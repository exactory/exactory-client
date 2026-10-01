"""Locate malformed nested records without disclosing their payload values."""

import json
import unittest

from research_harness.errors import ResearchError
from research_harness.operations import fields


class FieldDiagnosticTests(unittest.TestCase):
    def test_existing_call_keeps_its_message_code_and_absent_details(self):
        with self.assertRaises(ResearchError) as caught:
            fields({"unexpected": "private-value"}, ("description", "exit_condition"),
                   ("end_condition",), code="invalid_prerequisite")
        self.assertEqual(caught.exception.as_dict(), {
            "code": "invalid_prerequisite",
            "message": "Expected fields: description, exit_condition; optional: end_condition"})

    def test_valid_record_has_the_same_acceptance_with_and_without_location(self):
        value = {"description": "A required source", "end_condition": "Read the source"}
        self.assertIsNone(fields(value, ("description",), ("end_condition",)))
        self.assertIsNone(fields(value, ("description",), ("end_condition",), path="/prerequisites/0"))

    def test_string_prerequisite_reports_its_exact_location_and_missing_shape(self):
        private = "A private source description which is not an object"
        with self.assertRaises(ResearchError) as caught:
            fields(private, ("description", "end_condition", "exit_condition"),
                   path="/candidates/2/next_test/prerequisites/1")
        self.assertEqual(caught.exception.code, "invalid_input")
        self.assertEqual(caught.exception.details, {
            "path": "/candidates/2/next_test/prerequisites/1", "expected_type": "object",
            "received_type": "string", "missing_fields": ["description", "end_condition", "exit_condition"],
            "unexpected_fields": []})
        self.assertNotIn(private, json.dumps(caught.exception.as_dict()))

    def test_object_reports_missing_and_unexpected_names_without_values(self):
        with self.assertRaises(ResearchError) as caught:
            fields({"description": "private-description", "extra": "private-extra-value"},
                   ("description", "exit_condition"), ("end_condition",),
                   code="invalid_prerequisite", path="/candidates/0/next_test/prerequisites/0")
        self.assertEqual(caught.exception.code, "invalid_prerequisite")
        self.assertEqual(caught.exception.details, {
            "path": "/candidates/0/next_test/prerequisites/0", "expected_type": "object",
            "received_type": "object", "missing_fields": ["exit_condition"],
            "unexpected_fields": ["extra"]})
        self.assertNotIn("private-", json.dumps(caught.exception.as_dict()))

    def test_root_pointer_and_json_types_remain_unambiguous(self):
        for value, received in [(None, "null"), ([], "array"), (False, "boolean"),
                                (3, "number"), (1.5, "number")]:
            with self.subTest(received=received, value_type=type(value).__name__):
                with self.assertRaises(ResearchError) as caught:
                    fields(value, ("description",), path="")
                self.assertEqual(caught.exception.details, {
                    "path": "", "expected_type": "object", "received_type": received,
                    "missing_fields": ["description"], "unexpected_fields": []})


if __name__ == "__main__":
    unittest.main()
