"""External deliveries have a validated, replayable local receipt without an export."""

import copy
import json

import test_research_check_mode as checks
import test_research_problem_claims as fixtures
from research_harness import cli, problem_claims
from research_harness.output_views import render_all
from research_harness.report_views import next_summary


class ExternalPostTests(checks.CheckCommandMixin, fixtures.ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.statement = self.select_statement()
        self.payload = {
            "id": "external-post-1",
            "problem_id": "656c336e-e892-4c47-80d2-a71d022f4116",
            "statement_digest": problem_claims.compute_statement_digest(self.statement),
            "reason": "The owner requested direct publication before the harness gate was complete.",
            "evidence": "The owner instructed that this exact statement be posted.",
        }

    def record_external(self, payload=None, **kwargs):
        self.assertIn("problem-post-external", cli.OPERATIONS)
        return self.mutate(cli.OPERATIONS["problem-post-external"], payload or self.payload, **kwargs)

    def test_records_a_terminal_bypassed_post_without_an_export(self):
        before = cli.status_report(self.store)
        post = self.record_external()["result"]
        for key, value in self.payload.items():
            self.assertEqual(post[key], value)
        self.assertEqual(post["gate"], "bypassed")
        self.assertEqual(post["statement_id"], self.statement["id"])
        self.assertIn("posted_at", post)
        self.assertNotIn("export_digest", post)
        self.assertNotIn("export_directory", post)
        self.assertFalse((self.root / "final/problem/exports").exists())
        report = cli.status_report(self.store)
        self.assertEqual(report["next"]["code"], "problem_already_posted")
        self.assertEqual(len(report["obligations"]), 1)
        self.assertEqual(report["post"], post)
        self.assertEqual({row["code"] for row in report["after_post"]},
                         {row["code"] for row in before["obligations"]})
        self.assertEqual(next_summary(report)["after_post"], len(report["after_post"]))

    def test_wrong_statement_digest_is_refused_without_writing(self):
        self.assertIn("problem-post-external", cli.OPERATIONS)
        self.assert_refusal_parity("problem-post-external", dict(self.payload, statement_digest="0" * 64),
                                  "problem_statement_mismatch")

    def test_boundary_refuses_malformed_ids_empty_text_and_invalid_evidence(self):
        self.assertIn("problem-post-external", cli.OPERATIONS)
        invalid_values = {
            "id": ("", " \n", None),
            "problem_id": ("not-a-uuid", "656c336ee8924c4780d2a71d022f4116", None, 7),
            "reason": ("", " \n", None, []),
            "evidence": ("", " \n", None, [], {}, {"kind": "record", "id": "instruction"}),
        }
        for field, values in invalid_values.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assert_refusal_parity("problem-post-external", dict(self.payload, **{field: value}),
                                              "invalid_input")
        for field in self.payload:
            payload = dict(self.payload)
            payload.pop(field)
            with self.subTest(missing=field):
                self.assert_refusal_parity("problem-post-external", payload, "invalid_input")
        self.assert_refusal_parity("problem-post-external", dict(self.payload, export_directory="unused"),
                                  "invalid_input")

    def test_evidence_can_be_a_verified_artifact_reference(self):
        evidence = self.artifacts.put(b"An authored instruction to post the exact statement.", "text/plain")
        post = self.record_external(dict(self.payload, evidence=evidence))["result"]
        self.assertEqual(post["evidence"], evidence)

    def test_a_missing_evidence_artifact_is_refused(self):
        self.assertIn("problem-post-external", cli.OPERATIONS)
        evidence = self.artifacts.put(b"An authored instruction.", "text/plain")
        (self.root / evidence["path"]).unlink()
        self.assert_refusal_parity("problem-post-external", dict(self.payload, evidence=evidence), "artifact_missing")

    def test_a_second_post_of_the_statement_is_refused(self):
        self.record_external()
        self.assert_refusal_parity("problem-post-external", dict(self.payload, id="second-post"),
                                  "problem_already_posted", request="second-request")
        self.assertEqual(len(self.store.snapshot()["records"]["problem_post"]), 1)

    def test_an_ordinary_post_receipt_also_prevents_an_external_duplicate(self):
        post = dict(id="ordinary-post", problem_id=self.payload["problem_id"],
                    statement_digest=self.payload["statement_digest"], export_digest="legacy-export")
        self.put("problem_post", post["id"], post)
        self.assertIn("problem-post-external", cli.OPERATIONS)
        self.assert_refusal_parity("problem-post-external", self.payload, "problem_already_posted")

    def test_missing_current_statement_is_refused(self):
        self.put("problem_selection", "current", {"statement_id": "absent"})
        self.assertIn("problem-post-external", cli.OPERATIONS)
        self.assert_refusal_parity("problem-post-external", self.payload, "problem_statement_missing")

    def test_replay_returns_the_original_receipt_after_statement_changes(self):
        self.assertIn("problem-post-external", cli.OPERATIONS)
        operation = cli.OPERATIONS["problem-post-external"]
        revision = self.store.revision
        receipt = operation(self.store, self.payload, expected_revision=revision, request_id="external-request")
        self.select_statement(identifier="later-statement", statement="A different exact mathematical statement.")
        before = self.store.snapshot()
        replay = operation(self.store, copy.deepcopy(self.payload), expected_revision=revision,
                           request_id="external-request")
        self.assertEqual(replay, receipt)
        self.assertEqual(self.store.snapshot(), before)

    def test_cli_check_is_read_only_and_commit_places_the_post_view(self):
        self.assertIn("problem-post-external", cli.OPERATIONS)
        render_all(self.store)
        before = self.read_workspace()
        checked = self.invoke_command("problem-post-external", self.payload)
        self.assert_accepted_check(checked, self.store.revision)
        self.assertEqual(self.read_workspace(), before)
        written = self.invoke_command("problem-post-external", self.payload, check=False)
        self.assertEqual(written[0], 0, written[2])
        report = cli.status_report(self.store)
        self.assertEqual(report["post"]["gate"], "bypassed")
        self.assertEqual(report["post"]["reason"], self.payload["reason"])
        views = list((self.root / "final/problem/posts").rglob("*.json"))
        self.assertTrue(views)
        self.assertTrue(any(json.loads(path.read_text()).get("gate") == "bypassed" for path in views))

    def test_the_cli_example_has_all_external_post_fields(self):
        self.assertIn("problem-post-external", cli.OPERATIONS)
        payload = cli.run(cli.build_parser().parse_args(["example", "problem-post-external"]))
        self.assertEqual(set(payload), set(self.payload))
        self.assertEqual(len(payload["statement_digest"]), 64)

