"""problem-statement and problem-value apply the rules of the post to the texts that the post sends (0.91.8).

The rules are those of exactory post-problem, in its order: the line breaks become line feeds, no control character
except tab and line feed and no half of a surrogate pair, then the bounds in UTF-16 code units after the trim. A record
that was stored before this release is not examined again.
"""

import copy

from research_harness import problem_claims
from research_harness.errors import ResearchError
from test_problem_changes_if_resolved import ChangesIfResolvedCase, PLACE
from test_research_problem_claims import ProblemCase

# The trim of the post removes these characters around a text: a space, a byte order mark and the line breaks.
TRIMMED = " ﻿\r\n"
VALUE_LENGTH_ERR_MSG = (
    "The post does not accept the text of this section at this length. The post sends the texts of the paragraphs of "
    "a section with one empty line between them, after a trim. section names the section, characters gives the length "
    "of that text in UTF-16 code units, and minimum and maximum give the limits. Make the paragraphs shorter or longer. "
    "Then record the value again")
VALUE_CHARACTER_ERR_MSG = (
    "The post does not accept a character in the text of this section. The post sends the texts of the paragraphs of "
    "a section with one empty line between them. section names the section, character gives the code point, and line "
    "and column give its place in that text, counted from 1. The post accepts no control character except tab, line "
    "feed and carriage return, and no half of a surrogate pair. Remove the character. Then record the value again")
STATEMENT_LENGTH_ERR_MSG = (
    "The post does not accept this text at this length. field names the text: title, statement (the text before a "
    "line that holds only ---), statement_details (the text after that line) or resolution_criteria. characters gives "
    "the length of the text in UTF-16 code units after a trim, and minimum and maximum give the limits. Make the text "
    "shorter or longer. Then record the statement again")
STATEMENT_CHARACTER_ERR_MSG = (
    "The post does not accept a character in this text. field names the text: title, statement (the text before a "
    "line that holds only ---), statement_details (the text after that line) or resolution_criteria. character gives "
    "the code point, and line and column give its place in that text, counted from 1. The post accepts no control "
    "character except tab, line feed and carriage return, and no half of a surrogate pair. Remove the character. Then "
    "record the statement again")
STATEMENT_SEPARATOR_ERR_MSG = (
    "The statement has more than one line that holds only ---. The post divides a statement at one such line: the "
    "text before it is the statement, and the text after it is the details. Keep at most one such line. Then record "
    "the statement again")


def wrap_in_trimmed(text):
    return TRIMMED + text + TRIMMED[::-1]


class StatementPostTextTests(ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.statement_count = 0

    def record(self, **changes):
        self.statement_count += 1
        return self.select_statement(identifier="statement-" + str(self.statement_count), **changes)

    def refuse(self, **changes):
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            self.record(**changes)
        self.assertEqual(self.store.snapshot(), before)
        return caught.exception

    def test_each_text_is_refused_one_unit_outside_its_bounds_after_the_trim(self):
        for field, minimum, maximum in (("title", 8, 200), ("statement", 50, 20000), ("statement_details", 1, 20000),
                                        ("resolution_criteria", 50, 5000)):
            for characters in (minimum - 1, minimum, maximum, maximum + 1):
                with self.subTest(field=field, characters=characters):
                    text = wrap_in_trimmed("x" * characters)
                    # The details are the text after the one line of the statement that holds only ---.
                    changes = ({"statement": "s" * 60 + "\n---\n" + text} if field == "statement_details"
                               else {field: text})
                    if minimum <= characters <= maximum:
                        saved = self.record(**changes)
                        self.assertEqual({key: saved[key] for key in changes}, changes)
                        continue
                    error = self.refuse(**changes)
                    self.assertEqual((error.code, error.message, error.details),
                                     ("problem_statement_post_text_invalid", STATEMENT_LENGTH_ERR_MSG,
                                      {"field": field, "rule": "length", "characters": characters,
                                       "minimum": minimum, "maximum": maximum}))

    def test_the_length_counts_utf16_code_units(self):
        emoji = chr(0x1F600)
        self.assertEqual(self.record(title="x" * 6 + emoji)["title"], "x" * 6 + emoji)
        error = self.refuse(title="x" * 199 + emoji)
        self.assertEqual(error.details, {"field": "title", "rule": "length", "characters": 201, "minimum": 8,
                                         "maximum": 200})

    def test_a_refused_character_is_named_with_its_line_and_its_column_in_its_text(self):
        # The text of the details starts with the line break that ends the --- line, as post-problem counts it.
        for field, changes, character, line, column in (
                ("title", {"title": "A finite" + chr(0x07) + " bound problem"}, "U+0007", 1, 9),
                ("statement", {"statement": "s" * 60 + "\n\nThe second paragraph." + chr(0x7F)}, "U+007F", 3, 22),
                ("statement_details", {"statement": "s" * 60 + "\n---\nThe details.\r\n\r\nab" + chr(0x0B) + "c"},
                 "U+000B", 4, 3),
                ("resolution_criteria", {"resolution_criteria": "Prove the bound for every n.\n\nOr give a counterexample "
                                                                + chr(0x1B) + "[1m with its certificate."},
                 "U+001B", 3, 26)):
            with self.subTest(field=field):
                error = self.refuse(**changes)
                self.assertEqual((error.code, error.message, error.details),
                                 ("problem_statement_post_text_invalid", STATEMENT_CHARACTER_ERR_MSG,
                                  {"field": field, "rule": "character", "character": character, "line": line,
                                   "column": column}))

    def test_a_carriage_return_and_a_tab_are_accepted(self):
        changes = {"title": "A finite\tbound problem", "statement": "s" * 30 + "\r\n\t" + "s" * 30 + "\r---\rThe\tdetails.",
                   "resolution_criteria": "r" * 30 + "\r" + "\t" + "r" * 30}
        saved = self.record(**changes)
        self.assertEqual({key: saved[key] for key in changes}, changes)

    def test_a_statement_with_two_lines_that_hold_only_the_separator_is_refused(self):
        error = self.refuse(statement="s" * 60 + "\n---\nThe details.\n  ---\t\nMore details.")
        self.assertEqual((error.code, error.message, error.details),
                         ("problem_statement_post_text_invalid", STATEMENT_SEPARATOR_ERR_MSG,
                          {"field": "statement", "rule": "separator", "lines": 2}))

    def test_a_stored_statement_is_selected_again_without_the_rules(self):
        stored = dict(self.build_statement("stored", title="Short", statement="A short statement."),
                      candidate_id=None, recorded_revision=self.store.revision + 1)
        self.put("problem_statement", "stored", stored)
        self.record()
        self.assertEqual(self.select_statement(identifier="stored", title="Short", statement="A short statement."),
                         stored)
        self.assertEqual(problem_claims.find_current_statement(self.store.snapshot()["records"]), stored)


class ValuePostTextTests(ChangesIfResolvedCase):
    def build_value(self, identifier, section, texts):
        value = copy.deepcopy(self.value)
        value["id"] = identifier
        evidence = value[section][0]["evidence"]
        value[section] = [{"text": text, "evidence": evidence} for text in texts]
        return value

    def refuse_value(self, value):
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            self.record(value)
        self.assertEqual(self.store.snapshot(), before)
        return caught.exception

    def test_each_section_is_refused_one_unit_outside_its_bounds_after_the_trim(self):
        for section, minimum, maximum in (("motivation", 100, 10000), ("changes_if_resolved", 100, 5000),
                                          ("current_state", 100, 10000)):
            for characters in (minimum - 1, minimum, maximum, maximum + 1):
                with self.subTest(section=section, characters=characters):
                    value = self.build_value("value-" + section + "-" + str(characters), section,
                                             [wrap_in_trimmed("x" * characters)])
                    if minimum <= characters <= maximum:
                        self.assertEqual(self.record(value)[section], value[section])
                        continue
                    error = self.refuse_value(value)
                    self.assertEqual((error.code, error.message, error.details),
                                     ("problem_value_post_text_invalid", VALUE_LENGTH_ERR_MSG,
                                      {"section": section, "rule": "length", "characters": characters,
                                       "minimum": minimum, "maximum": maximum}))

    def test_the_joined_text_is_counted_as_the_post_counts_it(self):
        emoji = chr(0x1F600)
        for texts, characters in ((["x" * 99], 99), (["x" * 49, "y" * 49], 100), (["x" * 49 + "\r\n" + "y" * 49], 99),
                                  (["  " + "x" * 98 + "\n"], 98), (["x" * 4998 + emoji], 5000),
                                  (["x" * 4999 + emoji], 5001), (["x" * 2500, "y" * 2499], 5001)):
            with self.subTest(characters=characters, paragraphs=len(texts)):
                value = self.build_value("accepted-" + str(len(texts)) + "-" + str(characters), "changes_if_resolved",
                                         texts)
                if 100 <= characters <= 5000:
                    self.assertEqual(self.record(value)["changes_if_resolved"], value["changes_if_resolved"])
                    continue
                error = self.refuse_value(value)
                self.assertEqual((error.code, error.message, error.details),
                                 ("problem_value_post_text_invalid", VALUE_LENGTH_ERR_MSG,
                                  {"section": "changes_if_resolved", "rule": "length", "characters": characters,
                                   "minimum": 100, "maximum": 5000}))

    def test_a_refused_character_is_named_with_its_line_and_its_column_in_the_joined_text(self):
        first = "The first paragraph states the line of results that leads to the statement, from the source onward."
        for section, texts, character, line, column in (
                ("motivation", [first, "The second" + chr(0x1B) + "[0m paragraph."], "U+001B", 3, 11),
                ("changes_if_resolved", [first + "\r\nIts second line." + chr(0x7F)], "U+007F", 2, 17),
                ("current_state", [first, "One line.\nAnother line.", "ab" + chr(0x01)], "U+0001", 6, 3)):
            with self.subTest(section=section):
                error = self.refuse_value(self.build_value("value-" + section, section, texts))
                self.assertEqual((error.code, error.message, error.details),
                                 ("problem_value_post_text_invalid", VALUE_CHARACTER_ERR_MSG,
                                  {"section": section, "rule": "character", "character": character, "line": line,
                                   "column": column}))

    def test_a_carriage_return_and_a_tab_are_accepted(self):
        text = "The first line of the paragraph.\r\n\tThe second line starts with a tab.\rThe third line follows a lone CR."
        for section in ("motivation", "changes_if_resolved", "current_state"):
            with self.subTest(section=section):
                value = self.build_value("value-" + section, section, [text])
                self.assertEqual(self.record(value)[section][0]["text"], text)

    def test_the_check_names_the_first_section_in_the_order_of_the_record(self):
        value = self.build_value("value-two", "current_state", ["x" * 99])
        value["motivation"][0]["text"] = "x" * 99
        self.assertEqual(self.refuse_value(value).details["section"], "motivation")
        value["changes_if_resolved"].append({"text": chr(0x1B), "evidence": [PLACE]})
        value["motivation"][0]["text"] = "m" * 100
        self.assertEqual(self.refuse_value(value).details["section"], "changes_if_resolved")
