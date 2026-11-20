"""All D3 section 7.3 cases use synthetic instructions and real store shapes."""

import copy
import json
from pathlib import Path
from unittest import mock

from research_harness import automatic_overrides, overrides, record_check
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.problem_claims import compute_statement_digest
from test_gate_override import OverrideCommandMixin
from test_override_instruction_parts import build_instruction_parts
from test_research_problem_claims import ProblemCase
import test_research_problem_openness_minimum as openness_fixtures


POST_ACTION = "POST /api/v1/math-problems"
OPEN_CONDITION = {"text": "Publish after an open finding.", "part": 1, "check": "openness_supported_open"}


class OverrideAuthorityTests(OverrideCommandMixin, ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.selected = self.select_statement()
        self.write_instruction()
        self.write_parts(build_instruction_parts())

    def write_parts(self, value):
        (self.root / "context/instruction.json").write_text(json.dumps(value), encoding="utf-8")

    def build_payload(self, identifier="conditional", **changes):
        return dict(self.build_override(identifier, gate="post"), instruction="context/instruction.json", **changes)

    def record_user_override(self, identifier="conditional", **changes):
        return self.mutate(overrides.record_override, self.build_payload(identifier, **changes))["result"]

    def assess_authority(self, *, records=None, local=(), kind=None, gate="post", action=POST_ACTION):
        self.assertTrue(hasattr(overrides, "assess_write_authority"), "UA4 needs one authority assessment")
        write = dict(gate=gate, public_action=action)
        if kind is not None:
            write["kind"] = kind
        return overrides.assess_write_authority(self.store.snapshot()["records"] if records is None else records,
                                                local, write, artifacts=self.artifacts)

    def test_gate_condition_then_exact_action_without_lift_keeps_both_applicable(self):
        self.record_user_override(condition=OPEN_CONDITION)
        self.record_user_override("exact", public_action=POST_ACTION)
        result = self.assess_authority()
        self.assertTrue(result["covered"])
        self.assertEqual(result["override_ids"], ["conditional", "exact"])
        self.assertFalse(result["authorized"])
        self.assertEqual(result["conditions"][0]["state"], "unmet")

    def test_exact_action_then_gate_condition_keeps_both_applicable(self):
        self.record_user_override("exact", public_action=POST_ACTION)
        self.record_user_override(condition=OPEN_CONDITION)
        result = self.assess_authority()
        self.assertEqual(result["override_ids"], ["exact", "conditional"])
        self.assertFalse(result["authorized"])

    def test_condition_on_previous_statement_stands_after_new_statement_is_covered(self):
        self.record_user_override(condition=OPEN_CONDITION)
        self.selected = self.select_statement(identifier="statement-2", statement="Does a cubic bound hold for every index of the finite construction?")
        self.assertFalse(self.assess_authority(kind="problem-override")["covered"])
        self.record_user_override("new-text")
        result = self.assess_authority(kind="problem-override")
        self.assertTrue(result["covered"])
        self.assertEqual(result["override_ids"], ["conditional", "new-text"])
        self.assertFalse(result["authorized"])
        self.assertEqual(result["conditions"][0]["override_id"], "conditional")

    def test_named_lift_authorizes_export_and_post_and_is_shown_verbatim(self):
        self.record_user_override(condition=OPEN_CONDITION)
        value = build_instruction_parts()
        quote = "Release without waiting for an open finding."
        value["parts"].append(dict(kind="message", at=None, text=quote))
        self.write_parts(value)
        lift = dict(override_id="conditional", basis="named", part=5, quote=quote)
        self.record_user_override("lift", lifts=[lift])
        for kind in (None, "problem-override"):
            result = self.assess_authority(kind=kind)
            self.assertTrue(result["authorized"])
            condition, = result["conditions"]
            self.assertEqual(condition["state"], "lifted")
            self.assertEqual(condition["lifted_by"], ["lift"])
        row = overrides.describe_overrides(self.store.snapshot()["records"], self.root)["overrides"][1]
        self.assertEqual(row["lifts"], [lift])

    def test_stored_exact_action_with_another_gate_remains_covered_and_authorized(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/override_text_v0853.json").read_text())
        legacy = dict(fixture["record"], gate="readiness", public_action=POST_ACTION)
        self.put("gate_override", legacy["id"], legacy)
        result = self.assess_authority()
        self.assertTrue(result["covered"])
        self.assertTrue(result["authorized"])
        self.assertEqual(result["conditions"], [])
        self.assertTrue(automatic_overrides._covers_public_write([legacy], "post", POST_ACTION))

    def test_incident_step_one_selection_records_unmet_condition_and_its_obligation(self):
        value = build_instruction_parts()
        self.write_parts(dict(value, parts=value["parts"][:2]))
        record = self.record_user_override(condition=OPEN_CONDITION)
        self.assertEqual(record["condition"], OPEN_CONDITION)
        self.assertNotIn("state", record["condition"])
        condition, = self.assess_authority(kind="problem-override")["conditions"]
        self.assertEqual(condition["state"], "unmet")
        self.assertEqual(condition["obligations"][0]["code"], "problem_openness_missing")

    def test_incident_step_two_general_message_lifts_nothing_while_finding_is_pending(self):
        value = build_instruction_parts()
        self.write_parts(dict(value, parts=value["parts"][:2]))
        self.record_user_override(condition=OPEN_CONDITION)
        self.write_parts(dict(value, parts=value["parts"][:3]))
        self.record_user_override("continue")
        result = self.assess_authority(kind="problem-override")
        self.assertTrue(result["covered"])
        self.assertFalse(result["authorized"])
        self.assertEqual(result["conditions"][0]["lifted_by"], [])

    def test_incident_step_three_chain_without_finding_retains_unmet_condition_without_new_record(self):
        value = build_instruction_parts()
        self.write_parts(dict(value, parts=value["parts"][:2]))
        self.record_user_override(condition=OPEN_CONDITION)
        self.write_parts(dict(value, parts=value["parts"][:3]))
        self.record_user_override("continue")
        before = self.store.snapshot()
        self.assertFalse(self.assess_authority(kind="problem-override")["authorized"])
        self.assertEqual(self.store.snapshot(), before)

    def test_incident_step_four_after_report_lift_authorizes_export_and_post(self):
        value = build_instruction_parts()
        self.write_parts(dict(value, parts=value["parts"][:2]))
        self.record_user_override(condition=OPEN_CONDITION)
        self.write_parts(dict(value, parts=value["parts"][:3]))
        self.record_user_override("continue")
        value["parts"][3]["reports"] = "conditional"
        self.write_parts(value)
        self.record_user_override("after-report", lifts=[dict(override_id="conditional", basis="after_report", part=4,
                                                             quote=value["parts"][4]["text"])])
        for kind in (None, "problem-override"):
            result = self.assess_authority(kind=kind)
            self.assertTrue(result["authorized"])
            self.assertEqual(result["conditions"][0]["lifted_by"], ["after-report"])

    def test_condition_tracks_missing_unresolved_supported_and_stale_findings(self):
        self.record_user_override(condition=OPEN_CONDITION)
        self.assertEqual(self.assess_authority()["conditions"][0]["state"], "unmet")
        search = openness_fixtures.ProblemOpennessMinimumTests.search(self)
        for outcome, state in (("unresolved", "unmet"), ("supported_open", "met")):
            finding = openness_fixtures.ProblemOpennessMinimumTests.build_finding(self, [search], outcome=outcome)
            finding.update(id=outcome, statement_digest=compute_statement_digest(self.selected),
                           recorded_revision=self.store.revision + 1)
            self.put("problem_openness", outcome, finding)
            result = self.assess_authority()
            condition, = result["conditions"]
            self.assertEqual(condition["state"], state)
            self.assertEqual(result["authorized"], state == "met")
            if state == "met":
                self.assertEqual(condition["evidence"], [{"id": outcome, "digest": digest(finding)}])
            else:
                self.assertEqual(condition["obligations"][0]["code"], "problem_openness_unresolved")
        self.selected = self.select_statement(identifier="statement-2", statement="Does a cubic bound always hold for every finite construction?")
        condition, = self.assess_authority()["conditions"]
        self.assertEqual(condition["state"], "unmet")
        self.assertEqual(condition["obligations"][0]["code"], "problem_openness_stale")

    def test_text_typed_and_selected_labels_can_bind_conditions_but_session_words_cannot(self):
        value = build_instruction_parts()
        value["parts"][1]["typed"] = "Wait for explicit confirmation."
        self.write_parts(value)
        for index, (part, phrase) in enumerate(((0, value["parts"][0]["text"]), (1, "Wait"),
                                                (1, value["parts"][1]["typed"]))):
            self.record_user_override("binding-" + str(index), condition=dict(text=phrase, part=part, check=None))
        (self.root / "context/instruction.txt").write_text("Release after explicit confirmation.")
        record = self.mutate(overrides.record_override, dict(self.build_override("text", gate="post"),
            condition=dict(text="explicit confirmation", part=0, check=None)))["result"]
        self.assertEqual(record["condition"]["part"], 0)
        result = self.assess_authority()
        self.assertTrue(all(row["state"] == "user_decides" for row in result["conditions"]))
        self.assertFalse(result["authorized"])

    def test_text_form_named_lift_uses_part_zero_and_after_report_requires_parts(self):
        path = self.root / "context/instruction.txt"
        path.write_text("Wait for an open finding before release.")
        first = dict(self.build_override("text-condition", gate="post"),
                     condition=dict(text="an open finding", part=0, check="openness_supported_open"))
        self.mutate(overrides.record_override, first)
        quote = "Release without an open finding."
        path.write_text(quote)
        lift = dict(override_id="text-condition", basis="named", part=0, quote=quote)
        payload = dict(self.build_override("text-lift", gate="post"), lifts=[lift])
        self.mutate(overrides.record_override, payload)
        result = self.assess_authority()
        self.assertTrue(result["authorized"])
        self.assertEqual(result["conditions"][0]["state"], "lifted")
        self.assertEqual(record_check.check_integrity(self.store)["override_instructions"], 2)
        self.assert_override_refused(dict(payload, id="no-report", lifts=[dict(lift, basis="after_report")]),
                                     "gate_override_invalid")
        self.assert_override_refused(dict(payload, id="wrong-part", lifts=[dict(lift, part=1)]), "gate_override_invalid")

    def test_unreadable_records_make_local_named_conditions_unknown_and_lifts_skip_the_check(self):
        payload = self.build_payload(condition=OPEN_CONDITION)
        overrides.append_unrecorded_override(self.root, payload, expected_revision=self.store.revision, request_id="local")
        local = overrides.read_unrecorded_overrides(self.root)
        self.assertTrue(hasattr(overrides, "assess_write_authority"))
        result = overrides.assess_write_authority(None, local, dict(gate="post", public_action=POST_ACTION), artifacts=self.artifacts)
        self.assertTrue(result["covered"])
        self.assertFalse(result["authorized"])
        self.assertEqual(result["conditions"][0]["state"], "unknown")
        value = build_instruction_parts()
        value["parts"][3]["reports"] = "conditional"
        self.write_parts(value)
        lifting = self.build_payload("local-lift", lifts=[dict(override_id="conditional", basis="after_report", part=4,
                                                             quote=value["parts"][4]["text"])])
        overrides.append_unrecorded_override(self.root, lifting, expected_revision=self.store.revision, request_id="local-lift")
        with mock.patch("research_harness.problem_openness.assess_openness", side_effect=AssertionError("lifted check ran")):
            result = self.assess_authority(local=overrides.read_unrecorded_overrides(self.root))
        self.assertTrue(result["authorized"])
        self.assertEqual(result["conditions"][0]["state"], "lifted")

    def test_unrelated_lift_cannot_remove_a_condition_and_lifting_a_lift_does_not_restore_it(self):
        self.record_user_override(condition=OPEN_CONDITION)
        lift = dict(override_id="conditional", basis="named", part=4, quote="Proceed with the public release.")
        self.record_user_override("other-action", public_action="POST /api/v1/submissions", lifts=[lift])
        self.assertFalse(self.assess_authority()["authorized"])
        self.record_user_override("applies", lifts=[lift], condition=dict(text="Continue the requested work.", part=2, check=None))
        self.record_user_override("lift-applies", lifts=[dict(lift, override_id="applies")])
        result = self.assess_authority()
        self.assertTrue(result["authorized"])
        self.assertEqual([row["state"] for row in result["conditions"]], ["lifted", "lifted"])

    def test_malformed_conditions_are_refused_without_mutation_in_both_modes(self):
        self.assert_accepted_check(self.invoke_command("gate-override", self.build_payload(condition=OPEN_CONDITION)),
                                   self.store.revision)
        invalid = [None, {}, dict(OPEN_CONDITION, state="met"), dict(OPEN_CONDITION, part=True),
                   dict(OPEN_CONDITION, part=-1), dict(OPEN_CONDITION, part=99), dict(OPEN_CONDITION, check="invented"),
                   dict(OPEN_CONDITION, text="Keep the result private."), dict(OPEN_CONDITION, text="Wait", part=3),
                   dict(OPEN_CONDITION, text="Which release path should run?"), dict(OPEN_CONDITION, text="")]
        for condition in invalid:
            with self.subTest(condition=condition):
                self.assert_override_refused(self.build_payload(condition=condition), "gate_override_invalid")
        self.assert_override_refused(dict(self.build_payload(condition=OPEN_CONDITION), gate="readiness"), "gate_override_invalid")

    def test_malformed_lifts_need_prior_condition_verbatim_user_words_and_a_prior_report(self):
        self.record_user_override(condition=OPEN_CONDITION)
        lift = dict(override_id="conditional", basis="named", part=4, quote="Proceed with the public release.")
        invalid = [None, {}, [None], [dict(lift, override_id="absent")], [dict(lift, override_id="conditional", part=True)],
                   [dict(lift, basis="implicit")], [dict(lift, quote="not present")], [dict(lift, part=3)],
                   [dict(lift, basis="after_report")], [dict(lift, override_id="new")], [lift, lift]]
        self.record_user_override("unconditional")
        invalid.append([dict(lift, override_id="unconditional")])
        for lifts in invalid:
            with self.subTest(lifts=lifts):
                self.assert_override_refused(self.build_payload("new", lifts=lifts), "gate_override_invalid")
        value = build_instruction_parts()
        value["parts"][3]["reports"] = "conditional"
        # A report after the user's words cannot establish after_report.
        value["parts"][3], value["parts"][4] = value["parts"][4], value["parts"][3]
        self.write_parts(value)
        self.assert_override_refused(self.build_payload("new", lifts=[dict(lift, basis="after_report", part=3)]),
                                     "gate_override_invalid")

    def test_check_mode_validates_condition_and_lift_without_writing_and_replays_after_file_removal(self):
        payload = self.build_payload(condition=OPEN_CONDITION)
        before = self.read_workspace()
        self.assert_accepted_check(self.invoke_command("gate-override", payload), self.store.revision)
        self.assertEqual(self.read_workspace(), before)
        revision = self.store.revision
        receipt = self.record_override(payload, request="condition-original")
        (self.root / payload["instruction"]).unlink()
        for check in (False, True):
            response = self.invoke_command("gate-override", payload, revision=revision, request="condition-original", check=check)
            self.assertEqual(response[0], 0, response)
            self.assertEqual(json.loads(response[1]), receipt)

    def test_record_check_verifies_new_parts_condition_and_lift_against_pinned_instruction(self):
        record = self.record_user_override(condition=OPEN_CONDITION)
        checked = record_check.check_integrity(self.store)
        self.assertEqual(checked["override_instructions"], 1)
        changed = copy.deepcopy(record)
        changed["instruction"]["parts"][1]["selected"] = [2]
        self.put("gate_override", record["id"], changed)
        self.assert_error("gate_override_instruction", lambda: record_check.check_integrity(self.store))

    def test_local_condition_remains_a_valid_target_when_a_lift_is_recorded_in_the_store(self):
        overrides.append_unrecorded_override(self.root, self.build_payload(condition=OPEN_CONDITION),
                                              expected_revision=self.store.revision, request_id="local")
        value = build_instruction_parts()
        value["parts"][3]["reports"] = "conditional"
        self.write_parts(value)
        self.record_user_override("lift-local", lifts=[dict(override_id="conditional", basis="after_report", part=4,
                                                          quote=value["parts"][4]["text"])])
        self.assertTrue(self.assess_authority(local=overrides.read_unrecorded_overrides(self.root))["authorized"])
        self.assertEqual(record_check.check_integrity(self.store)["override_instructions"], 1)

    def test_record_check_uses_the_instruction_before_view_rendering_and_rejects_wrong_conditions(self):
        record = self.record_user_override(condition=OPEN_CONDITION)
        changed = copy.deepcopy(record)
        changed["condition"]["text"] = "Words that were never instructed."
        self.put("gate_override", record["id"], changed)
        self.assert_error("gate_override_invalid", lambda: record_check.check_integrity(self.store))

    def test_missing_artifact_reader_and_unreadable_evidence_leave_the_condition_unknown(self):
        self.record_user_override(condition=OPEN_CONDITION)
        self.assertTrue(hasattr(overrides, "assess_write_authority"))
        result = overrides.assess_write_authority(self.store.snapshot()["records"], [], dict(gate="post", public_action=POST_ACTION))
        self.assertEqual(result["conditions"][0]["state"], "unknown")
        with mock.patch("research_harness.problem_openness.assess_openness", side_effect=ResearchError("storage_io", "Unreadable evidence")):
            result = self.assess_authority()
        self.assertEqual(result["conditions"][0]["state"], "unknown")
        self.assertFalse(result["authorized"])

    def test_a_local_lift_of_an_unreadable_target_does_not_lift_a_condition_recorded_later(self):
        lift = dict(override_id="future", basis="named", part=4, quote="Proceed with the public release.")
        with mock.patch("research_harness.storage.Store.snapshot", side_effect=ResearchError("storage_io", "Unreadable store")):
            overrides.append_unrecorded_override(self.root, self.build_payload("earlier-lift", lifts=[lift]),
                                                  expected_revision=self.store.revision, request_id="local")
        self.record_user_override("future", condition=OPEN_CONDITION)
        local = overrides.read_unrecorded_overrides(self.root)
        result = self.assess_authority(local=local)
        self.assertFalse(result["authorized"])
        self.assertEqual(result["conditions"][0]["state"], "unmet")
        self.assertEqual(result["conditions"][0]["lifted_by"], [])

    def test_fallback_lift_still_lifts_an_earlier_recorded_condition(self):
        self.record_user_override(condition=OPEN_CONDITION)
        lift = dict(override_id="conditional", basis="named", part=4, quote="Proceed with the public release.")
        with mock.patch("research_harness.storage.Store.snapshot", side_effect=ResearchError("storage_io", "Unreadable store")):
            overrides.append_unrecorded_override(self.root, self.build_payload("later-lift", lifts=[lift]),
                                                  expected_revision=self.store.revision, request_id="local")
        result = self.assess_authority(local=overrides.read_unrecorded_overrides(self.root))
        self.assertTrue(result["authorized"])
        self.assertEqual(result["conditions"][0]["state"], "lifted")

    def test_local_append_order_keeps_a_later_local_condition_standing(self):
        lift = dict(override_id="local-condition", basis="named", part=4, quote="Proceed with the public release.")
        with mock.patch("research_harness.storage.Store.snapshot", side_effect=ResearchError("storage_io", "Unreadable store")):
            overrides.append_unrecorded_override(self.root, self.build_payload("local-lift", lifts=[lift]),
                                                  expected_revision=self.store.revision, request_id="local-lift")
        overrides.append_unrecorded_override(self.root, self.build_payload("local-condition", condition=OPEN_CONDITION),
                                              expected_revision=self.store.revision, request_id="local-condition")
        result = self.assess_authority(local=overrides.read_unrecorded_overrides(self.root))
        self.assertFalse(result["authorized"])
        self.assertEqual(result["conditions"][0]["state"], "unmet")

    def test_colliding_stored_and_local_ids_keep_each_condition_visible(self):
        self.record_user_override(condition=OPEN_CONDITION)
        search = openness_fixtures.ProblemOpennessMinimumTests.search(self)
        finding = openness_fixtures.ProblemOpennessMinimumTests.build_finding(self, [search])
        finding.update(id="open", statement_digest=compute_statement_digest(self.selected),
                       recorded_revision=self.store.revision + 1)
        self.put("problem_openness", "open", finding)
        local_condition = dict(text="Continue the requested work.", part=2, check=None)
        with mock.patch("research_harness.storage.Store.snapshot", side_effect=ResearchError("storage_io", "Unreadable store")):
            overrides.append_unrecorded_override(self.root, self.build_payload(condition=local_condition),
                                                  expected_revision=self.store.revision, request_id="collision")
        report = overrides.describe_overrides(self.store.snapshot()["records"], self.root, artifacts=self.artifacts)
        self.assertEqual(report["overrides"][0]["condition"]["state"], "met")
        self.assertEqual(report["unrecorded_overrides"][0]["condition"]["state"], "user_decides")
        self.assertEqual(report["unrecorded_overrides"][0]["condition"]["text"], local_condition["text"])
        self.assertFalse(self.assess_authority(local=overrides.read_unrecorded_overrides(self.root))["authorized"])

    def test_legacy_authority_equals_coverage_for_recorded_and_local_shapes_and_all_scopes(self):
        self.assertTrue(hasattr(overrides, "assess_write_authority"))
        fixture = json.loads((Path(__file__).parent / "fixtures/override_text_v0853.json").read_text())
        for row in (fixture["record"], fixture["local"]):
            for gate, action, authority, expected in (("post", None, "user", True),
                    ("readiness", POST_ACTION, "user", True), ("readiness", None, "user", False),
                    ("post", "POST /other", "user", False), ("post", None, "harness", False)):
                record = dict(row, gate=gate, authority=authority)
                if action is not None:
                    record["public_action"] = action
                records = dict(self.store.snapshot()["records"], gate_override={record["id"]: record})
                for entries, local in ((records, []), ({}, [record])):
                    with self.subTest(gate=gate, action=action, authority=authority, local=bool(local)):
                        result = overrides.assess_write_authority(entries, local, dict(gate="post", public_action=POST_ACTION))
                        self.assertEqual(result["covered"], expected)
                        self.assertEqual(result["authorized"], expected)
                        self.assertEqual(result["conditions"], [])
