"""A source records a candidate's transfer without changing its destination."""

import json

from research_harness import cli, problem_reports
from test_research_check_mode import CheckCommandMixin
from test_research_problem_candidates import build_candidate
from test_research_problem_claims import ProblemCase


class ProblemCandidateTransferTests(CheckCommandMixin, ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem(start=dict(kind="phenomenon", description="A recurring bound."))
        self.mutate(cli.OPERATIONS["problem-candidate"], build_candidate())
        self.put_slate("slate-1", selected=["candidate-1"])
        (self.root.parent / "destination").mkdir()
        self.payload = dict(id="transfer-1", candidate_id="candidate-1",
                            destination_workspace=str((self.root.parent / "destination").resolve()),
                            reason="The candidate belongs to the destination's subject class.")

    def put_slate(self, identifier, *, selected=(), deferred=(), rejected=()):
        self.put("problem_slate", identifier, dict(id=identifier, selected=list(selected),
            deferred=[dict(candidate_id=value, evidence_needed="A proof.") for value in deferred],
            rejected=[dict(candidate_id=value, reason="Already known.") for value in rejected],
            candidate_ids=sorted(list(selected) + list(deferred) + list(rejected)),
            recorded_revision=self.store.revision + 1))

    def transfer_candidate(self, **changes):
        return self.mutate(cli.OPERATIONS["problem-candidate-transfer"], dict(self.payload, **changes))["result"]

    def test_transfer_records_destination_and_reason_and_makes_selected_candidate_due(self):
        self.assertFalse(problem_reports.is_report_due(self.store.snapshot()["records"]))
        cutoff = self.store.revision
        saved = self.transfer_candidate()
        self.assertEqual(saved, dict(self.payload, recorded_revision=cutoff + 1))
        records = self.store.snapshot()["records"]
        self.assertEqual(records["forming_transfer"], {saved["id"]: saved})
        self.assertTrue(problem_reports.is_report_due(records))
        report = self.mutate(cli.OPERATIONS["problem-report"],
            dict(id="report", reopen_when="A new construction."))["result"]
        candidate = report["candidates"][0]
        self.assertEqual(candidate["transfers"], [saved])
        self.assertEqual(candidate["ending"], "taken up in another record")
        self.assertEqual(report["outcome"], "No statement was formed")
        self.assertEqual(list((self.root.parent / "destination").iterdir()), [])

    def test_transfer_can_name_a_deferred_or_rejected_candidate_on_latest_slate(self):
        for status in ("deferred", "rejected"):
            with self.subTest(status=status):
                self.put_slate("slate-" + status, **{status: ["candidate-1"]})
                saved = self.transfer_candidate(id="transfer-" + status)
                self.assertEqual(saved["candidate_id"], "candidate-1")

    def test_due_waits_for_every_selected_candidate_to_be_taken_up(self):
        self.mutate(cli.OPERATIONS["problem-candidate"], build_candidate("candidate-2"))
        self.put_slate("slate-2", selected=["candidate-1", "candidate-2"])
        self.transfer_candidate()
        self.assertFalse(problem_reports.is_report_due(self.store.snapshot()["records"]))
        self.transfer_candidate(id="transfer-2", candidate_id="candidate-2")
        self.assertTrue(problem_reports.is_report_due(self.store.snapshot()["records"]))

    def test_transfer_before_a_later_slate_that_reselects_the_candidate_does_not_end_it(self):
        self.transfer_candidate()
        self.put_slate("slate-2", selected=["candidate-1"])
        records = self.store.snapshot()["records"]
        candidate = problem_reports.describe_candidate_endings(records)[0]
        self.assertEqual((candidate["slate_id"], candidate["transfers"]), ("slate-2", []))
        self.assertEqual((candidate["ended"], candidate["ending"]), (False, None))
        self.assertFalse(problem_reports.is_report_due(records))
        saved = self.transfer_candidate(id="transfer-2")
        candidate = problem_reports.describe_candidate_endings(self.store.snapshot()["records"])[0]
        self.assertEqual((candidate["transfers"], candidate["ending"]), ([saved], "taken up in another record"))

    def test_transfer_makes_a_report_due_but_open_forms_still_refuse_it(self):
        from test_research_problem_forming import build_forming
        self.mutate(cli.OPERATIONS["forming"], build_forming())
        self.transfer_candidate()
        self.assertTrue(problem_reports.is_report_due(self.store.snapshot()["records"]))
        self.assert_error("problem_report_forms_open", lambda: self.mutate(cli.OPERATIONS["problem-report"],
            dict(id="report", reopen_when="A new construction.")))

    def test_refuses_missing_slate_and_candidates_absent_from_latest_slate_without_writes(self):
        self.mutate(cli.OPERATIONS["problem-candidate"], build_candidate("unslated"))
        for identifier in ("missing", "unslated"):
            with self.subTest(candidate=identifier):
                self.assert_refusal_parity("problem-candidate-transfer", dict(self.payload, candidate_id=identifier),
                                          "problem_candidate_transfer_not_on_slate")
        self.put_slate("slate-2")
        self.assert_refusal_parity("problem-candidate-transfer", self.payload, "problem_candidate_transfer_not_on_slate")
        # The command also refuses before any slate has been recorded.
        other = ProblemCase()
        other.setUp()
        self.addCleanup(other.doCleanups)
        other.initialize_problem()
        other.mutate(cli.OPERATIONS["problem-candidate"], build_candidate())
        other.assert_error("problem_candidate_transfer_not_on_slate", lambda: other.mutate(
            cli.OPERATIONS["problem-candidate-transfer"], self.payload))
        self.assertNotIn("forming_transfer", self.store.snapshot()["records"])

    def test_payload_requires_all_fields_nonblank_text_and_an_absolute_different_destination(self):
        for key in self.payload:
            missing = dict(self.payload)
            del missing[key]
            self.assert_refusal_parity("problem-candidate-transfer", missing, "invalid_input")
            for value in ("", "  ", None, [], True):
                with self.subTest(field=key, value=value):
                    self.assert_refusal_parity("problem-candidate-transfer", dict(self.payload, **{key: value}), "invalid_input")
        self.assert_refusal_parity("problem-candidate-transfer", dict(self.payload, extra="unexpected"), "invalid_input")
        self.assert_refusal_parity("problem-candidate-transfer", dict(self.payload, destination_workspace="relative"), "invalid_input")
        self.assert_refusal_parity("problem-candidate-transfer", dict(self.payload, destination_workspace=str(self.root)),
                                  "problem_candidate_transfer_self")

    def test_destination_is_resolved_and_a_source_alias_is_refused(self):
        alias = self.root.parent / "source-alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.assert_refusal_parity("problem-candidate-transfer", dict(self.payload, destination_workspace=str(alias)),
                                  "problem_candidate_transfer_self")
        saved = self.transfer_candidate(destination_workspace=str(self.root / ".." / "destination"))
        self.assertEqual(saved["destination_workspace"], self.payload["destination_workspace"])

    def test_unresolvable_destination_is_refused_without_a_traceback_or_writes(self):
        loop = self.root.parent / "loop"
        loop.symlink_to(loop)
        for destination, message in ((loop, "destination_workspace cannot be resolved."),
                                     (self.root.parent / "missing", "destination_workspace does not exist."),
                                     (self.root.parent / "invalid\x00path",
                                      "Candidate transfer destination_workspace must be a nonempty string")):
            with self.subTest(destination=str(destination)):
                payload = dict(self.payload, destination_workspace=str(destination))
                self.assert_refusal_parity("problem-candidate-transfer", payload, "invalid_input")
                error = json.loads(self.invoke_command("problem-candidate-transfer", payload)[2])["error"]
                self.assertTrue(error["message"].startswith(message), error["message"])

    def test_replay_returns_original_transfer_and_reusing_id_refuses_replacement(self):
        operation = cli.OPERATIONS["problem-candidate-transfer"]
        revision = self.store.revision
        first = operation(self.store, self.payload, expected_revision=revision, request_id="transfer-request")
        self.put_slate("slate-2")
        self.assertEqual(operation(self.store, self.payload, expected_revision=revision, request_id="transfer-request"), first)
        self.put_slate("slate-3", selected=["candidate-1"])
        self.assert_error("record_conflict", lambda: self.transfer_candidate(reason="A different reason."))

    def test_transfer_stales_a_report_without_changing_gate_or_next(self):
        self.mutate(cli.OPERATIONS["problem-report"], dict(id="report", reopen_when="A new construction."))
        before = cli.status_report(self.store)
        self.transfer_candidate()
        after = cli.status_report(self.store)
        self.assertIn("problem_report_stale", [row["code"] for row in after["advisories"]])
        for key in ("ready", "obligations", "next", "milestone"):
            self.assertEqual(after[key], before[key])

    def test_check_and_example_use_public_payload_without_writes(self):
        example = cli.run(cli.build_parser().parse_args(["example", "problem-candidate-transfer"]))
        self.assertEqual(set(example), set(self.payload))
        example.update(candidate_id="candidate-1", destination_workspace=self.payload["destination_workspace"])
        before = self.read_workspace()
        diagnostic = self.assert_accepted_check(self.invoke_command("problem-candidate-transfer", example), self.store.revision)
        self.assertEqual(diagnostic["operation"], "problem.candidate_transfer")
        self.assertEqual(self.read_workspace(), before)
        response = self.invoke_command("problem-candidate-transfer", example, check=False)
        self.assertEqual(response[0], 0, response[2])
        self.assertEqual(json.loads(response[1])["result"]["reason"], example["reason"])


class ProblemCandidateTransferScopeTests(ProblemCase):
    def test_transfer_outside_problem_record_is_refused(self):
        self.assert_error("orchestration_conflict", lambda: self.mutate(cli.OPERATIONS["problem-candidate-transfer"],
            dict(id="transfer", candidate_id="candidate", destination_workspace=str(self.root.parent / "destination"),
                 reason="A different subject class.")))
