"""Synthetic selection incident, with user words distinct from session text."""

import copy
import hashlib
import json
from pathlib import Path

from research_harness import output_views, overrides, record_check
from research_harness.evidence import digest
from test_gate_override import OverrideCommandMixin
from test_research_problem_claims import ProblemCase


def build_instruction_parts():
    return {"schema": "exactory-instruction-v1", "parts": [
        {"kind": "message", "at": "2026-11-01T18:00:00-07:00", "text": "Please arrange a public release."},
        {"kind": "dialog", "asked_at": None, "answered_at": "2026-11-01T19:00:00-07:00",
         "question": "Which release path should run?", "options": [
             {"label": "Wait", "text": "Publish after an open finding."},
             {"label": "Review", "text": "Complete every review first."},
             {"label": "Hold", "text": "Keep the result private."}], "selected": [0], "typed": None},
        {"kind": "message", "at": None, "text": "Continue the requested work."},
        {"kind": "note", "at": None, "text": "The review ended without a finding.", "reports": None},
        {"kind": "message", "at": "2026-11-02T01:00:00Z", "text": "Proceed with the public release."}]}


class InstructionPartsTests(OverrideCommandMixin, ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.select_statement()
        self.write_instruction()

    def write_parts(self, value=None):
        value = build_instruction_parts() if value is None else value
        path = self.root / "context/instruction.json"
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return dict(self.build_override(gate="post"), instruction="context/instruction.json")

    def test_selection_keeps_three_messages_apart_from_all_options_in_reports_and_view(self):
        payload = self.write_parts()
        before = self.read_workspace()
        self.assert_accepted_check(self.invoke_command("gate-override", payload), self.store.revision)
        self.assertEqual(self.read_workspace(), before)
        record = self.record_override(payload)["result"]
        instruction = record["instruction"]
        self.assertEqual(instruction["form"], "parts")
        self.assertEqual(instruction["parts"], build_instruction_parts()["parts"])
        self.assertEqual(record["evidence"], "reported_by_agent")
        self.assertEqual(self.artifacts.read(instruction["artifact"]),
                         (self.root / payload["instruction"]).read_bytes())
        expected_words = [{"at": part["at"], "text": part["text"]}
                          for part in build_instruction_parts()["parts"] if part["kind"] == "message"]
        for command in ("status", "next"):
            row, = self.read_report(command)["overrides"]
            self.assertEqual(row["user_words"], expected_words)
            selection, = row["selections"]
            self.assertEqual(selection["selected"], [{"label": "Wait", "text": "Publish after an open finding."}])
            self.assertEqual(selection["unselected"], build_instruction_parts()["parts"][1]["options"][1:])
            self.assertEqual(selection["author"], "session")
        output_views.render_all(self.store)
        view = json.loads((self.root / "final/overrides/override-1.json").read_bytes())
        self.assertEqual(view["instruction"], instruction)
        self.assertEqual(view["user_words"], expected_words)
        self.assertEqual(view["selections"], row["selections"])

    def test_typed_dialog_words_are_user_words_and_multiple_choices_remain_selections(self):
        value = build_instruction_parts()
        value["parts"][1].update(typed="Use the first two choices.", selected=[0, 1])
        record = self.record_override(self.write_parts(value))["result"]
        row, = overrides.describe_overrides({"gate_override": {record["id"]: record}}, self.root)["overrides"]
        self.assertEqual(row["user_words"][1], {"at": "2026-11-01T19:00:00-07:00", "text": "Use the first two choices."})
        self.assertEqual(len(row["selections"][0]["selected"]), 2)
        value["parts"][1]["selected"] = []
        self.assert_accepted_check(self.invoke_command("gate-override", dict(self.write_parts(value), id="typed-only"), request="typed-only"),
                                   self.store.revision)

    def test_summaries_and_local_log_preserve_parts_attribution(self):
        payload = self.write_parts()
        overrides.append_unrecorded_override(self.root, payload, expected_revision=self.store.revision, request_id="local")
        local, = overrides.read_unrecorded_overrides(self.root)
        self.assertEqual(local["instruction"]["form"], "parts")
        self.assertEqual(local["instruction"]["parts"], build_instruction_parts()["parts"])
        for command in ("status", "next"):
            for summary in (False, True):
                row, = self.read_report(command, summary)["unrecorded_overrides"]
                self.assertEqual(len(row["user_words"]), 3)
                self.assertEqual(row["selections"][0]["author"], "session")

    def test_summaries_bound_many_parts_without_mixing_authors(self):
        value = build_instruction_parts()
        value["parts"] += [{"kind": "message", "at": None, "text": "m" * 600}] * 60
        self.record_override(self.write_parts(value))
        for command in ("status", "next"):
            report = self.read_report(command, True)
            row, = report["overrides"]
            self.assertTrue(row["user_words"])
            self.assertGreater(row["omitted_user_words"], 0)
            self.assertLess(len(json.dumps(report, ensure_ascii=False, indent=2).encode()), 24000)

    def test_wide_dialog_previews_keep_the_existing_summary_byte_limits(self):
        value = build_instruction_parts()
        dialog = value["parts"][1]
        dialog["question"] = "\x01" * 1000
        dialog["options"] = [dict(label="\x01" * 40, text="\x01" * 250) for _ in range(6)]
        dialog["selected"] = [0, 1, 2]
        value["parts"] = [copy.deepcopy(dialog) for _ in range(3)]
        self.record_override(self.write_parts(value))
        for command, limit in (("status", 16384), ("next", 4096)):
            report = self.read_report(command, True)
            self.assertLessEqual(len(json.dumps(report, ensure_ascii=False, indent=2).encode()), limit)
            row, = report["overrides"]
            self.assertGreater(row["omitted_selections"], 0)

    def test_large_override_view_keeps_recorded_parts_when_other_fields_are_summarized(self):
        value = {"schema": "exactory-instruction-v1", "parts": [
            {"kind": "message", "at": None, "text": "u" * 50000}]}
        payload = dict(self.write_parts(value), reason="r" * (1024 * 1024))
        record = self.mutate(overrides.record_override, payload)["result"]
        self.assertEqual(record["instruction"]["form"], "parts")
        output_views.render_all(self.store)
        view = json.loads((self.root / "final/overrides/override-1.json").read_bytes())
        self.assertTrue(view["summary"])
        self.assertEqual(view["fields"]["instruction"], record["instruction"])
        self.assertEqual(view["fields"]["user_words"], [{"at": None, "text": "u" * 50000}])
        self.assertEqual(view["fields"]["selections"], [])
        self.assertTrue(view["fields"]["reason"]["omitted"])

    def test_corrupt_new_local_shapes_are_reported_as_unreadable(self):
        overrides.append_unrecorded_override(self.root, self.write_parts(), expected_revision=self.store.revision,
                                              request_id="local")
        original = overrides.read_unrecorded_overrides(self.root)[0]
        invalid = []
        for key, value in (("parts", []), ("form", "unknown"), ("sha256", "0" * 64)):
            changed = copy.deepcopy(original)
            changed["instruction"][key] = value
            invalid.append(changed)
        invalid += [dict(original, condition={"text": "Anything", "check": None}), dict(original, lifts=[{}])]
        path = self.root / ".exactory/unrecorded-overrides.jsonl"
        path.chmod(0o600)
        for row in invalid:
            with self.subTest(row=row):
                path.write_text(json.dumps(row) + "\n")
                unreadable, = overrides.read_unrecorded_overrides(self.root)
                self.assertEqual(unreadable["id"], "unreadable-line-1")

    def test_invalid_parts_are_refused_in_check_and_record_without_store_changes(self):
        original = build_instruction_parts()
        invalid_values = [[], {}, {"schema": "other", "parts": original["parts"]},
                          dict(original, extra=True), dict(original, parts=[]),
                          dict(original, parts=[original["parts"][3]])]
        mutations = [
            (0, "kind", "agent"), (0, "at", "2026-11-01T00:00:00"), (0, "at", 12),
            (0, "text", ""), (0, "text", "word\x00word"), (0, "extra", True),
            (1, "options", []), (1, "options", [{"label": "Only", "text": "One"}]),
            (1, "options", [{"label": "A", "text": "One"}, {"label": "B"}]),
            (1, "question", None), (1, "asked_at", "yesterday"), (1, "answered_at", False),
            (1, "selected", []), (1, "selected", [True]), (1, "selected", [-1]),
            (1, "selected", [3]), (1, "selected", [0, 0]), (1, "selected", "0"),
            (1, "typed", ""), (1, "typed", 1), (3, "reports", 1)]
        for index, key, value in mutations:
            changed = copy.deepcopy(original)
            changed["parts"][index][key] = value
            invalid_values.append(changed)
        for index, key in ((0, "at"), (0, "text"), (1, "typed"), (3, "reports")):
            changed = copy.deepcopy(original)
            del changed["parts"][index][key]
            invalid_values.append(changed)
        for value in invalid_values:
            with self.subTest(value=value):
                self.assert_override_refused(self.write_parts(value), "gate_override_instruction")

    def test_json_suffix_requires_strict_json_and_parts_obey_utf8_byte_limit(self):
        payload = self.write_parts()
        path = self.root / payload["instruction"]
        for raw in ('null', 'ordinary user words', '{"schema":"exactory-instruction-v1","parts":[],"parts":[]}',
                    '{"schema":NaN,"parts":[]}', b'\xff'):
            path.write_bytes(raw.encode() if isinstance(raw, str) else raw)
            self.assert_override_refused(payload, "gate_override_instruction")
        self.write_parts()
        raw = path.read_bytes()
        path.write_bytes(raw + b" " * (64 * 1024 - len(raw)))
        self.assert_accepted_check(self.invoke_command("gate-override", payload), self.store.revision)
        path.write_bytes(path.read_bytes() + b" ")
        self.assert_override_refused(payload, "gate_override_instruction")

    def test_parts_replay_uses_pinned_record_after_instruction_file_is_removed(self):
        payload = self.write_parts()
        revision = self.store.revision
        receipt = self.record_override(payload, request="parts-original")
        self.assertEqual(receipt["result"]["instruction"]["form"], "parts")
        (self.root / payload["instruction"]).unlink()
        for check in (False, True):
            response = self.invoke_command("gate-override", payload, revision=revision, request="parts-original", check=check)
            self.assertEqual(response[0], 0, response)
            self.assertEqual(json.loads(response[1]), receipt)

    def test_new_text_form_and_stored_text_and_local_shapes_keep_their_meaning(self):
        record = self.record_override(self.build_override(gate="post"))["result"]
        self.assertEqual(record["instruction"]["form"], "text")
        fixture = json.loads((Path(__file__).parent / "fixtures/override_text_v0853.json").read_text())
        legacy = fixture["record"]
        self.artifacts.put(fixture["text"].encode(), "text/plain; charset=utf-8")
        self.put("gate_override", "override-1", legacy)
        local_path = self.root / ".exactory/unrecorded-overrides.jsonl"
        local_path.write_text(json.dumps(fixture["local"]) + "\n")
        self.assertEqual(digest(legacy), fixture["record_digest"])
        self.assertEqual(digest(overrides.read_unrecorded_overrides(self.root)[0]), fixture["local_digest"])
        self.assertEqual(overrides.describe_overrides(self.store.snapshot()["records"], self.root), fixture["description"])
        output_views.render_all(self.store)
        view = (self.root / "final/overrides/override-1.json").read_bytes()
        self.assertEqual(view.decode(), fixture["view"])
        self.assertEqual(hashlib.sha256(view).hexdigest(), fixture["view_sha256"])
        self.store.snapshot()  # Authenticate the legacy event history.
        self.assertNotIn("override_instructions", record_check.check_integrity(self.store))
