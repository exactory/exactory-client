"""A new value record names the places of its statement and what a resolution would change (0.91.6).

A value record that an earlier release recorded holds neither part, and it keeps its validation and its projections.
"""

import copy
import json
import unittest
from unittest.mock import patch

from problem_accounts_fixtures import CHANGES_IF_RESOLVED_TEXT, build_account_value, build_place, build_relation
from research_harness import problem_accounts, problem_claims, problem_delivery, problem_evidence, research_decisions
from research_harness import review_parts, review_parts_adjudicator, review_prompt_paragraphs, stop_classes
from research_harness.errors import ResearchError
from research_harness.review_prompt_paragraphs import CHANGES_IF_RESOLVED_PROMPT
from test_research_problem_claims import ProblemCase

PLACE = {"kind": "account", "id": "place"}
# The refusal messages of 0.91.6, as the texts lane wrote them.
CHANGES_REQUIRED_MESSAGE = (
    "A new value record holds changes_if_resolved, the paragraphs of the section What a resolution would change. Add "
    "changes_if_resolved to the payload as a list of paragraphs {text, evidence}. exactory-research example "
    "problem-value prints the shape")
PLACES_REQUIRED_MESSAGE = (
    "The accounts of a new value record hold places, with at least one entry. Add accounts.places with one entry for "
    "each work in which the statement arises. If no work read states the statement, needs it or stops at it, enter one "
    "place with the role continues")
PLACE_EVIDENCE_MESSAGE = (
    "This place cites no full reading. A place is a fact about a work. Cite a full reading of that work in evidence, or "
    "cite in evidence_ids an entry that holds one. A computation or an accepted result alone shows no place")
PLACE_UNCITED_MESSAGE = (
    "No paragraph of this section cites a place. At least one paragraph of motivation and at least one of "
    "changes_if_resolved cite an entry of accounts.places, as {kind: account, id}. In motivation, add the citation to "
    "the paragraph that states the line of results that leads to the statement. In changes_if_resolved, add it to the "
    "paragraph that says what an answer decides or supplies for that work")
LENGTH_MESSAGE = (
    "The text of changes_if_resolved is outside the length that the post accepts. The post sends the texts of the "
    "paragraphs with one empty line between them, after a trim. characters gives the length of that text, and minimum "
    "and maximum give the limits. Make the paragraphs shorter or longer. Then record the value again")
RELATION_IDS_MESSAGE = (
    "relation_ids of this place names an entry that is no relations entry. Name the ID of a relations entry that ties "
    "the statement to the item of the place, or leave relation_ids empty")
ROLE_MESSAGE = (
    "The role of a place is an open value {kind, description}: how the statement stands to the item of the place. The "
    "described kinds are stated, needed, decides, stops and continues")


class ChangesIfResolvedCase(ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.statement = self.select_statement()
        self.reading = self.record_full_reading()
        self.value = build_account_value([self.reading])

    def record(self, value=None):
        return self.mutate(problem_claims.record_value, self.value if value is None else value)["result"]

    def refuse(self, change):
        value = copy.deepcopy(self.value)
        change(value)
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            self.record(value)
        self.assertEqual(self.store.snapshot(), before)
        return caught.exception

    def put_stored_value(self, saved, identifier="stored-value"):
        """The value record as 0.91.2 to 0.91.5 recorded it: accounts of version 2, without places and the section."""
        stored = {key: item for key, item in saved.items() if key != "changes_if_resolved"}
        stored["accounts"] = {key: item for key, item in saved["accounts"].items() if key != "places"}
        stored["motivation"] = [dict(paragraph, evidence=[{"kind": "account", "id": "use"}])
                                for paragraph in saved["motivation"]]
        stored.update(id=identifier, recorded_revision=self.store.revision + 1)
        self.put("problem_value", identifier, stored)
        return stored


class ValueRefusalTests(ChangesIfResolvedCase):
    def test_a_new_value_record_holds_the_section(self):
        error = self.refuse(lambda value: value.pop("changes_if_resolved"))
        self.assertEqual((error.code, error.message, error.details),
                         ("problem_changes_if_resolved_required", CHANGES_REQUIRED_MESSAGE, None))

    def test_the_accounts_of_a_new_value_record_hold_at_least_one_place(self):
        for change in (lambda value: value["accounts"].pop("places"), lambda value: value["accounts"].update(places=[])):
            with self.subTest(change=change):
                error = self.refuse(change)
                self.assertEqual((error.code, error.message, error.details),
                                 ("problem_places_required", PLACES_REQUIRED_MESSAGE, None))

    def test_a_place_rests_on_a_full_reading_in_evidence_or_through_evidence_ids(self):
        error = self.refuse(lambda value: value["accounts"]["places"][0].update(evidence=[]))
        self.assertEqual((error.code, error.message, error.details),
                         ("problem_place_evidence_missing", PLACE_EVIDENCE_MESSAGE, {"id": "place"}))
        self.value["accounts"]["places"][0].update(evidence=[], evidence_ids=["use"])
        saved = self.record()
        self.assertEqual(problem_accounts.collect_entry_evidence(
            {entry["id"]: entry for entry in problem_accounts.collect_entries(saved["accounts"])}, "place"), [self.reading])

    def test_relation_ids_name_relations_entries_and_add_no_evidence(self):
        relation = build_relation(standing="established", evidence_ids=["use"])
        for identifier in ("use", "absent"):
            with self.subTest(identifier=identifier):
                error = self.refuse(lambda value: (value["accounts"]["relations"].append(copy.deepcopy(relation)),
                                                   value["accounts"]["places"][0].update(relation_ids=[identifier])))
                self.assertEqual((error.code, error.message, error.details),
                                 ("problem_account_reference_missing", RELATION_IDS_MESSAGE, {"id": "place"}))
        error = self.refuse(lambda value: (value["accounts"]["relations"].append(copy.deepcopy(relation)),
                                           value["accounts"]["places"][0].update(evidence=[], relation_ids=["relation"])))
        self.assertEqual((error.code, error.details), ("problem_place_evidence_missing", {"id": "place"}))
        self.value["accounts"]["relations"].append(relation)
        self.value["accounts"]["places"][0]["relation_ids"] = ["relation"]
        self.assertEqual(self.record()["accounts"]["places"][0]["relation_ids"], ["relation"])

    def test_the_role_is_an_open_value_of_two_texts(self):
        # "weaker" is a role of an alternative; as the role of a place it gets the refusal of a place.
        for role in ("stated", "weaker", {"kind": "stated"}, {"kind": " ", "description": "A described role."},
                     {"kind": "stated", "description": "A described role.", "extra": "member"}, None):
            with self.subTest(role=role):
                error = self.refuse(lambda value: value["accounts"]["places"][0].update(role=role))
                self.assertEqual((error.code, error.message), ("invalid_input", ROLE_MESSAGE))
        self.value["accounts"]["places"][0]["role"] = {"kind": "a kind of its own", "description": "No described kind fits."}
        self.assertEqual(self.record()["accounts"]["places"][0]["role"]["kind"], "a kind of its own")

    def test_a_place_is_a_fact_with_a_scope_and_a_date(self):
        # establishing_ids and consequence are members of other entries; a place that holds one is refused, with no walk of its references.
        for change in (lambda place: place.pop("scope"), lambda place: place.update(fact=" "),
                       lambda place: place.update(date="not a date"), lambda place: place.update(unknown="member"),
                       lambda place: place.update(establishing_ids=[]), lambda place: place.update(consequence="x")):
            with self.subTest(change=change):
                self.assertEqual(self.refuse(lambda value: change(value["accounts"]["places"][0])).code, "invalid_input")
        self.value["accounts"]["places"][0].pop("date")
        self.assertEqual(self.record()["accounts"]["places"][0]["date"], "2026-02-01")

    def test_place_ids_share_the_registry_of_the_entries(self):
        error = self.refuse(lambda value: value["accounts"]["places"][0].update(id="use"))
        self.assertEqual(error.code, "problem_account_id_conflict")

    def test_the_motivation_and_the_section_each_cite_a_place(self):
        use = {"kind": "account", "id": "use"}
        for section in ("motivation", "changes_if_resolved"):
            with self.subTest(section=section):
                error = self.refuse(lambda value: value[section][0].update(evidence=[use]))
                self.assertEqual((error.code, error.message, error.details),
                                 ("problem_value_place_uncited", PLACE_UNCITED_MESSAGE, {"section": section}))
        self.value["changes_if_resolved"].append({"text": "A second paragraph names no place.", "evidence": [use]})
        self.assertEqual(len(self.record()["changes_if_resolved"]), 2)

    def test_the_section_paragraphs_are_validated_as_the_motivation_paragraphs(self):
        for change, code in ((lambda section: section.clear(), "invalid_input"),
                             (lambda section: section[0].update(text=" "), "invalid_input"),
                             (lambda section: section[0].update(evidence=[]), "invalid_input"),
                             (lambda section: section[0].update(evidence=[PLACE, {"kind": "account", "id": "absent"}]),
                              "problem_account_reference_missing"),
                             (lambda section: section[0].update(evidence=[PLACE, self.reading]),
                              "problem_value_account_required")):
            with self.subTest(code=code):
                self.assertEqual(self.refuse(lambda value: change(value["changes_if_resolved"])).code, code)

    def test_the_joined_text_is_counted_as_the_post_counts_it(self):
        emoji = chr(0x1F600)
        for texts, characters in ((["x" * 99], 99), (["x" * 49, "y" * 49], 100), (["x" * 49 + "\r\n" + "y" * 49], 99),
                                  (["  " + "x" * 98 + "\n"], 98), (["x" * 4998 + emoji], 5000),
                                  (["x" * 4999 + emoji], 5001), (["x" * 2500, "y" * 2499], 5001)):
            with self.subTest(characters=characters, paragraphs=len(texts)):
                value = copy.deepcopy(self.value)
                value["changes_if_resolved"] = [{"text": text, "evidence": [PLACE]} for text in texts]
                if 100 <= characters <= 5000:
                    value["id"] = "accepted-" + str(len(texts)) + "-" + str(characters)
                    self.assertEqual(self.record(value)["changes_if_resolved"], value["changes_if_resolved"])
                    continue
                before = self.store.snapshot()
                with self.assertRaises(ResearchError) as caught:
                    self.record(value)
                self.assertEqual((caught.exception.code, caught.exception.message, caught.exception.details),
                                 ("problem_changes_if_resolved_length", LENGTH_MESSAGE,
                                  {"characters": characters, "minimum": 100, "maximum": 5000}))
                self.assertEqual(self.store.snapshot(), before)

    def test_an_imported_full_reading_counts_and_no_other_record_does(self):
        imported = {"kind": "record", "origin": "source-record", "record_kind": "reading", "id": "r", "digest": "d"}
        self.assertTrue(problem_accounts.is_full_reading(self.reading))
        self.assertTrue(problem_accounts.is_full_reading(imported))
        for reference in (dict(imported, record_kind="execution"), {"kind": "computation"}, {"kind": "result"},
                          {"kind": "record", "record_kind": "reading", "id": "r", "digest": "d"}):
            with self.subTest(reference=reference):
                self.assertFalse(problem_accounts.is_full_reading(reference))


class ValueProjectionTests(ChangesIfResolvedCase):
    def test_the_value_part_carries_the_section_with_delivered_evidence_and_the_places(self):
        saved = self.record()
        evidence = problem_evidence.describe_evidence(self.store.snapshot()["records"], self.artifacts)
        value = evidence["value"]
        self.assertEqual(list(value)[:4], ["id", "motivation", "changes_if_resolved", "current_state"])
        self.assertEqual(value["changes_if_resolved"], [dict(saved["changes_if_resolved"][0], delivered_evidence=[self.reading])])
        self.assertEqual(value["accounts"]["places"], saved["accounts"]["places"])
        self.assertIn(self.reading, problem_evidence.collect_value_items(saved))

    def test_a_stored_value_without_the_parts_keeps_its_validation_and_projections(self):
        stored = self.put_stored_value(self.record())
        records = self.store.snapshot()["records"]
        self.assertEqual(problem_evidence.validate_value(records, self.artifacts, stored)["accounts"], stored["accounts"])
        self.assertEqual(problem_evidence.list_value_sections(stored), ("motivation", "current_state"))
        value = problem_evidence.describe_evidence(records, self.artifacts)["value"]
        self.assertEqual(value["id"], "stored-value")
        self.assertNotIn("changes_if_resolved", value)
        self.assertNotIn("places", value["accounts"])
        self.assertEqual(list(value)[:3], ["id", "motivation", "current_state"])
        self.assertEqual(problem_evidence.describe_binding(records, self.artifacts)["value"], stored)
        files = problem_delivery.build_post_files(records, self.artifacts)
        self.assertEqual(set(files), {"title.txt", "statement.md", "criteria.md", "motivation.md", "state.md",
                                      "citations.json"})

    def test_the_export_writes_the_section_only_for_a_value_record_that_holds_it(self):
        saved = self.record()
        files = problem_delivery.build_post_files(self.store.snapshot()["records"], self.artifacts)
        self.assertEqual(files["changes-if-resolved.md"], CHANGES_IF_RESOLVED_TEXT)
        saved["changes_if_resolved"].append({"text": "A second paragraph.", "evidence": [PLACE]})
        self.put("problem_value", "two-paragraphs", dict(saved, id="two-paragraphs", recorded_revision=self.store.revision + 1))
        files = problem_delivery.build_post_files(self.store.snapshot()["records"], self.artifacts)
        self.assertEqual(files["changes-if-resolved.md"], CHANGES_IF_RESOLVED_TEXT + "\n\nA second paragraph.")

    def test_adjudicator_targets_list_each_paragraph_of_the_section(self):
        saved = self.record()
        packet = {"objection": {"id": "objection", "claim": "A material objection."},
                  "problem_evidence": problem_evidence.describe_evidence(self.store.snapshot()["records"], self.artifacts)}
        targets = [row["id"] for row in review_parts_adjudicator.list_targets(packet)]
        self.assertEqual(targets, ["objection", "statement", "resolution_criteria", "value:motivation:0",
                                   "value:changes_if_resolved:0", "value:current_state:0"])
        self.assertEqual(review_parts_adjudicator.list_targets(packet)[4]["label"], CHANGES_IF_RESOLVED_TEXT)
        self.put_stored_value(saved)
        packet["problem_evidence"] = problem_evidence.describe_evidence(self.store.snapshot()["records"], self.artifacts)
        self.assertNotIn("value:changes_if_resolved:0", [row["id"] for row in review_parts_adjudicator.list_targets(packet)])

    def test_the_handoff_step_of_problem_value_names_the_section(self):
        for code in ("problem_value_stale", "problem_result_superseded"):
            step = stop_classes.STOP_REGISTRY[code]["next"]["then"][0]["steps"][0]
            self.assertEqual(step["command"], "problem-value")
            self.assertEqual(step["chooses"], ["id", "statement_id", "accounts", "motivation", "changes_if_resolved",
                                               "current_state"])

    def test_a_result_frame_at_the_maximum_of_both_parts_grows_by_at_most_25000_characters(self):
        # Design assumption 5: the section at its maximum and six places of 1,500 characters each.
        self.value["accounts"]["places"] = [build_place([self.reading], "place-" + str(index), fact="f" * 1200)
                                            for index in range(6)]
        self.value["motivation"][0]["evidence"] = [{"kind": "account", "id": "place-0"}]
        cited = [{"kind": "account", "id": "place-" + str(index)} for index in range(6)]
        self.value["changes_if_resolved"] = [{"text": "c" * length, "evidence": cited} for length in (1000, 998, 998, 998, 998)]
        saved = self.record()
        joined = "\n\n".join(paragraph["text"] for paragraph in saved["changes_if_resolved"])
        self.assertEqual(len(joined), 5000)
        self.assertTrue(all(1450 <= len(json.dumps(place, sort_keys=True)) <= 1550 for place in saved["accounts"]["places"]))

        def measure_frame():
            evidence = problem_evidence.describe_evidence(self.store.snapshot()["records"], self.artifacts)
            packet = {"subject": {"kind": "problem"}, "problem_evidence": evidence, "evidence": []}
            frame = review_parts.build_frame(packet, review_parts.enumerate_units(packet, "problem_result"))
            return len(json.dumps(frame, sort_keys=True, ensure_ascii=False))

        largest = measure_frame()
        self.put_stored_value(saved)
        growth = largest - measure_frame() + len(CHANGES_IF_RESOLVED_PROMPT)
        self.assertLessEqual(growth, 25000)


class ResultReviewDeliveryTests(unittest.TestCase):
    def setUp(self):
        import test_research_problem_delivery as delivery_fixtures
        self.case = delivery_fixtures.ProblemDeliveryCase()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()

    def read_result(self, identifier):
        records = self.case.store.snapshot()["records"]
        assignment = records["review_assignment"][identifier]
        return assignment, json.loads(self.case.artifacts.read(assignment["packet"]))

    def test_a_result_review_of_a_stored_value_is_refreshed_in_its_slot_with_the_paragraph(self):
        def put_stored_value(store, payload, *, expected_revision, request_id):
            # The value record as 0.91.2 to 0.91.5 recorded it: accounts of version 2, without places and the section.
            saved = {key: copy.deepcopy(item) for key, item in payload.items() if key != "changes_if_resolved"}
            saved["accounts"].pop("places")
            saved["motivation"][0]["evidence"] = [{"kind": "account", "id": "use"}]
            saved.update(accounts_version=2, recorded_revision=expected_revision + 1)
            self.case.put("problem_value", saved["id"], saved)
            return {"result": saved}
        with patch.object(problem_claims, "record_value", side_effect=put_stored_value):
            self.case.prepare_ready()
        stored, packet = self.read_result("result-first")
        self.assertNotIn("changes_if_resolved", packet["problem_evidence"]["value"])
        # The paragraphs that 0.91.5 selects for this packet, on the base template that this release leaves as it is.
        versions = ["exactory-problem-accounts-v1", "exactory-route-capacity-notice-v1"]
        self.assertEqual(stored["prompt"]["parameters"]["problem_paragraphs"], versions)
        base = review_prompt_paragraphs.strip_prompt_paragraphs(stored["prompt"], "result")
        self.assertEqual(review_prompt_paragraphs.append_prompt_paragraphs(
            base, "result", None, versions, stored["prompt"]["parameters"]["capacity"]), stored["prompt"])
        records = self.case.store.snapshot()["records"]
        self.assertTrue(research_decisions.assess_problem_reviews(records, self.case.artifacts)["ready"])
        value = copy.deepcopy(records["problem_value"]["value-1"])
        self.case.mutate(problem_claims.record_value, build_account_value(
            value["accounts"]["explanatory_use"][0]["evidence"], "value-with-section"))
        pending = research_decisions.assess_problem_reviews(self.case.store.snapshot()["records"], self.case.artifacts)
        self.assertEqual([row["code"] for row in pending["obligations"]].count("problem_evidence_unassessed"), 2)
        for reviewer in ("first", "second"):
            self.case.observe("result", reviewer, identifier="refreshed-" + reviewer)
            refreshed, packet = self.read_result("refreshed-" + reviewer)
            original, _ = self.read_result("result-" + reviewer)
            self.assertEqual((refreshed["history_assignment_id"], refreshed["slot_id"]), (original["id"], original["slot_id"]))
            self.assertEqual(packet["problem_evidence"]["value"]["changes_if_resolved"][0]["text"], CHANGES_IF_RESOLVED_TEXT)
            self.assertEqual(refreshed["prompt"]["parameters"]["problem_paragraphs"], ["exactory-problem-accounts-v1",
                             "exactory-problem-changes-if-resolved-v1", "exactory-route-capacity-notice-v1"])
            self.assertIn(CHANGES_IF_RESOLVED_PROMPT, refreshed["prompt"]["text"])
        pending = research_decisions.assess_problem_reviews(self.case.store.snapshot()["records"], self.case.artifacts)
        self.assertTrue(pending["ready"], pending["obligations"])
