"""A recorded public delivery keeps the authority of the bytes that were sent."""

import copy
import json
from pathlib import Path

from research_harness import cli, output_views, overrides, problem_claims, problem_delivery, problem_reports
from research_harness.evidence import digest
from research_harness.report_views import next_summary, status_summary
from test_override_readers import OPEN_CONDITION, OverrideReaderCase


class PostOverrideAttributionTests(OverrideReaderCase):
    def read_post_report(self):
        statements = problem_reports.describe_statements(self.store.snapshot()["records"], self.artifacts)
        return statements[0]["posts"][0]

    def record_external_post(self, export_digest=None, identifier="external"):
        value = dict(id=identifier, problem_id="656c336e-e892-4c47-80d2-a71d022f4116",
            statement_digest=problem_claims.compute_statement_digest(self.selected),
            reason="The requested public release completed.", evidence="The service returned an accepted receipt.")
        if export_digest is not None:
            value["export_digest"] = export_digest
        return self.mutate(problem_claims.record_post_external, value)["result"]

    def test_g13_export_authority_survives_intervening_instruction_and_changed_finding(self):
        self.record_condition()
        finding = self.record_finding()
        exported = problem_delivery.export_problem_override(self.store)
        directory = Path(exported["destination"])
        original = {path.name: path.read_bytes() for path in directory.iterdir()}
        self.record_lift()
        self.record_finding("unresolved")
        output_views.render_all(self.store)
        self.assertFalse(directory.exists())
        post = self.record_external_post(directory.name)
        expected = dict(exported["authority"], source="export", export_digest=directory.name)
        expected.pop("evaluated_at_revision")
        self.assertEqual(post["authority"], expected)
        self.assertEqual(post["authority"]["conditions"][0]["evidence"], [{"id": finding["id"], "digest": digest(finding)}])
        self.assertEqual(post["authority"]["override_ids"], ["conditional"])
        report = cli.status_report(self.store)
        self.assertNotIn("problem_post_condition_unmet", [row["code"] for row in report["after_post"]])
        self.assertEqual(self.read_post_report()["after_post"]["conditions"], [])
        historical = self.root / "final/history/problem/exports" / directory.name
        self.assertEqual({path.name: path.read_bytes() for path in historical.iterdir()}, original)
        for summary in (next_summary(report), status_summary(report)):
            self.assertEqual(summary["post"]["authority"]["source"], "export")
            self.assertEqual(summary["post"]["export_digest"], directory.name)
        output_views.render_all(self.store)
        self.assertEqual(json.loads((self.root / "final/problem/posts/external.json").read_bytes())["authority"], expected)

    def test_g13_recording_observation_reports_unmet_condition_without_refusing_the_post(self):
        self.record_condition()
        post = self.record_external_post()
        self.assertEqual(post["authority"]["source"], "recording")
        self.assertEqual(post["authority"]["conditions"][0]["state"], "unmet")
        report = cli.status_report(self.store)
        row, = [row for row in report["after_post"] if row["code"] == "problem_post_condition_unmet"]
        self.assertIn("not established as met", row["explanation"])
        self.assertIn("no evidence", row["explanation"])
        self.assertEqual(self.read_post_report()["after_post"]["conditions"], [row])
        self.record_lift()
        self.record_finding()
        self.assertEqual([row for row in cli.status_report(self.store)["after_post"]
                          if row["code"] == "problem_post_condition_unmet"], [row])
        self.assertEqual(self.store.snapshot()["records"]["problem_post"]["external"], post)
        self.assertEqual(self.read_post_report()["after_post"]["conditions"], [row])

    def test_missing_export_digest_records_observation_and_never_invents_export_evidence(self):
        self.record_condition()
        post = self.record_external_post("b" * 64)
        self.assertEqual(post["authority"]["source"], "recording")
        self.assertNotIn("export_digest", post["authority"])
        self.assertEqual(post["export_digest"], "b" * 64)

    def test_recording_observation_keeps_unknown_and_owner_decided_conditions(self):
        from unittest import mock
        from research_harness.errors import ResearchError
        self.mutate(overrides.record_override, self.build_conditional("manual",
            condition=dict(OPEN_CONDITION, check=None)))
        self.record_condition()
        with mock.patch("research_harness.problem_openness.assess_openness",
                        side_effect=ResearchError("artifact_missing", "The finding is unavailable")):
            post = self.record_external_post()
        self.assertEqual({row["override_id"]: row["state"] for row in post["authority"]["conditions"]},
                         {"manual": "user_decides", "conditional": "unknown"})
        finding, = problem_reports.describe_post_condition_findings(post)
        self.assertEqual(finding["code"], "problem_post_condition_unmet")
        self.assertEqual(finding["conditions"], post["authority"]["conditions"])
        self.assertIn("not established as met", finding["explanation"])
        self.assertIn("no evidence", finding["explanation"])
        self.record_finding()
        self.assertEqual(self.read_post_report()["after_post"]["conditions"], [finding])

    def test_legacy_export_without_authority_uses_labeled_recording_observation(self):
        self.record_condition()
        self.record_lift()
        records = self.store.snapshot()["records"]
        override = problem_delivery.find_post_override(records)
        files, manifest = problem_delivery.build_override_projection(records, self.artifacts, override)
        directory = self.root / "final/problem/exports/legacy"
        directory.mkdir(parents=True)
        for name, data in files.items():
            (directory / name).write_bytes(data)
        post = self.record_external_post(problem_delivery.compute_export_digest(files))
        self.assertEqual(post["authority"]["source"], "recording")
        self.assertEqual(post["authority"]["conditions"][0]["state"], "lifted")

    def test_stored_post_has_no_new_inferred_authority_and_preserves_digest_and_view(self):
        legacy = dict(id="legacy", problem_id="656c336e-e892-4c47-80d2-a71d022f4116",
            statement_id=self.selected["id"], statement_digest=problem_claims.compute_statement_digest(self.selected),
            reason="The requested release completed.", evidence="The service accepted the release.",
            gate="bypassed", posted_at="2026-10-01T00:00:00Z", recorded_revision=self.store.revision + 1)
        self.put("problem_post", "legacy", copy.deepcopy(legacy))
        before = digest(legacy)
        self.record_condition()
        report = cli.status_report(self.store)
        self.assertFalse(report["override_export_authority"]["authorized"])
        self.assertNotIn("authority", report["post"])
        self.assertNotIn("problem_post_condition_unmet", [row["code"] for row in report["after_post"]])
        self.assertNotIn("conditions", self.read_post_report()["after_post"])
        output_views.render_all(self.store)
        self.assertEqual(json.loads((self.root / "final/problem/posts/legacy.json").read_bytes()), legacy)
        self.assertEqual(digest(self.store.snapshot()["records"]["problem_post"]["legacy"]), before)

    def test_large_post_view_retains_authority_when_its_other_fields_are_summarized(self):
        self.record_condition()
        post = self.record_external_post()
        large = dict(post, evidence="e" * (1024 * 1024))
        self.put("problem_post", post["id"], large)
        output_views.render_all(self.store)
        shown = json.loads((self.root / "final/problem/posts/external.json").read_bytes())
        self.assertTrue(shown["summary"])
        self.assertEqual(shown["fields"]["authority"], post["authority"])

    def test_damaged_authority_metadata_is_left_unowned_and_post_recording_uses_an_observation(self):
        from research_harness.problem_export_history import find_export_authority
        self.record_condition()
        self.record_lift()
        exported = problem_delivery.export_problem_override(self.store)
        directory = Path(exported["destination"])
        manifest_path = directory / "problem-export.json"
        manifest = json.loads(manifest_path.read_bytes())
        authority = manifest["authority"]
        condition = authority["conditions"][0]
        invalid_authorities = [None, {}, [], dict(conditions=[], override_ids=[]),
            dict(authority, evaluated_at_revision=True), dict(authority, evaluated_at_revision=-1),
            dict(authority, override_ids="conditional"), dict(authority, override_ids=[1]),
            dict(authority, conditions="unmet"), dict(authority, conditions=[{}])]
        for changes in (dict(state="invented"), dict(check=1), dict(text=None), dict(evidence=[{}]),
                        dict(obligations=[{}]), dict(lifted_by=[1]), dict(evidence=None)):
            invalid_authorities.append(dict(authority, conditions=[dict(condition, **changes)]))
        for invalid in invalid_authorities:
            changed = dict(manifest, authority=invalid)
            manifest_path.chmod(0o600)
            manifest_path.write_text(json.dumps(changed))
            files = {path.name: path.read_bytes() for path in directory.iterdir()}
            identity = problem_delivery.compute_export_digest(files)
            output_views.render_all(self.store)
            self.assertEqual({path.name: path.read_bytes() for path in directory.iterdir()}, files)
            self.assertIsNone(find_export_authority(self.root, self.store.snapshot()["records"], identity,
                                                    problem_claims.compute_statement_digest(self.selected)))
        post = self.record_external_post(identity)
        self.assertEqual(post["authority"]["source"], "recording")

    def test_unrelated_unreadable_export_cannot_prevent_attribution_of_an_existing_post(self):
        from unittest import mock
        from research_harness import problem_export_history
        self.record_condition()
        self.record_lift()
        exported = problem_delivery.export_problem_override(self.store)
        directory = Path(exported["destination"])
        unrelated = directory.parent / "000-unreadable"
        unrelated.mkdir()
        (unrelated / "problem-export.json").write_text("{}")
        original = problem_export_history.read_file
        def read(directory, name):
            if directory == unrelated:
                raise OSError("A generated folder became unreadable")
            return original(directory, name)
        with mock.patch.object(problem_export_history, "read_file", side_effect=read):
            post = self.record_external_post(directory.name)
        self.assertEqual(post["authority"]["source"], "export")

    def test_summary_bounds_a_long_post_identifier_and_marks_the_preview(self):
        self.record_condition()
        self.record_external_post(identifier="p" * 20000)
        report = cli.status_report(self.store)
        for summarize, limit in ((status_summary, 16384), (next_summary, 4096)):
            shown = summarize(report)
            self.assertLessEqual(len(json.dumps(shown, ensure_ascii=False, indent=2).encode()), limit)
            self.assertIn("id", shown["post"]["truncated_fields"])
