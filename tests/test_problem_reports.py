"""Reports derive their endings, posting-time classifications and currency."""

import copy
import json
from pathlib import Path

import test_research_problem_delivery as delivery
from problem_accounts_fixtures import build_accounts, build_relation
from research_harness import cli, output_views, problem_claims, problem_delivery, problem_evidence, problem_reports
from research_harness.evidence import digest
from research_harness.record_check import check_integrity
from test_research_check_mode import CheckCommandMixin
from test_research_problem_claims import ProblemCase
from test_research_problem_forming import build_form, build_forming, build_outcome


class ProblemReportTests(CheckCommandMixin, ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem(start=dict(kind="phenomenon", description="A recurring finite bound."))

    def record_report(self, identifier="report-1", **changes):
        return self.mutate(cli.OPERATIONS["problem-report"], dict(id=identifier,
            reopen_when="A proof overcomes the recorded obstruction.", **changes))["result"]

    def put_slate(self, selected=()):
        self.put("problem_slate", "slate", dict(id="slate", selected=list(selected), deferred=[], rejected=[],
            candidate_ids=sorted(self.store.snapshot()["records"].get("problem_candidate", {})),
            reason="The exploration ended.", recorded_revision=self.store.revision + 1))

    def put_candidate(self, identifier="candidate-1"):
        from test_research_problem_candidates import build_candidate
        self.put("problem_candidate", identifier, dict(build_candidate(identifier), recorded_revision=self.store.revision + 1))

    def put_value(self, identifier="value-1", accounts=True):
        statement = problem_claims.find_current_statement(self.store.snapshot()["records"])
        value = dict(id=identifier, statement_id=statement["id"], motivation=[], current_state=[],
                     recorded_revision=self.store.revision + 1)
        if accounts is not False:
            value["accounts"] = build_accounts() if accounts is True else accounts
        self.put("problem_value", identifier, value)
        return value

    def put_gated_post(self, value, *, staged=False, evidence_digest=None):
        identity = evidence_digest or digest(dict(value=value))
        frame = dict(problem_evidence_digest=identity,
                     problem_evidence=dict(value=dict(id=value["id"], digest=digest(value))))
        if staged:
            self.put("review_epoch", "epoch", dict(id="epoch", manifest=dict(frame=frame)))
        else:
            packet = self.artifacts.put(json.dumps(frame).encode(), "application/json")
            self.put("review_assignment", "review", dict(id="review", packet=packet, recorded_revision=self.store.revision + 1))
        statement = self.store.snapshot()["records"]["problem_statement"]["statement-1"]
        post = dict(id="post", statement_id=statement["id"], statement_digest=problem_claims.compute_statement_digest(statement),
                    problem_evidence_digest=identity, posted_at="2026-11-10T12:00:00Z", recorded_revision=self.store.revision + 1)
        self.put("problem_post", "post", post)
        return post

    def put_external_post(self):
        statement = self.store.snapshot()["records"]["problem_statement"]["statement-1"]
        return self.mutate(problem_claims.record_post_external, dict(id="post", problem_id="656c336e-e892-4c47-80d2-a71d022f4116",
            statement_digest=problem_claims.compute_statement_digest(statement), reason="The owner requested it.",
            evidence="The publication instruction."))["result"]

    def record_form(self, *, closed=True, **changes):
        self.mutate(cli.OPERATIONS["forming"], build_forming(**changes))
        if closed:
            self.mutate(cli.OPERATIONS["form-outcome"], build_outcome(disposition=dict(status="set_aside",
                reason="The available means did not decide it.", form_id=None, candidate_id=None)))

    def test_no_statement_outcome_cutoff_digest_and_author_field_are_computed(self):
        self.put_slate()
        cutoff = self.store.revision
        projection = problem_reports.project_dependencies(self.store.snapshot()["records"])
        report = self.record_report()
        self.assertEqual(report["outcome"], "No statement was formed")
        self.assertEqual(report["cutoff"], cutoff)
        self.assertEqual(report["dependency_digest"], digest(projection))
        self.assertEqual(report["recorded_revision"], cutoff + 1)
        self.assertEqual(report["start"]["kind"], "phenomenon")
        self.assertEqual(report["start"]["description"], "A recurring finite bound.")
        self.assertEqual(report["reopen_when"], "A proof overcomes the recorded obstruction.")
        self.assertEqual(report["forms"], [])
        self.assertEqual(problem_reports.describe_report_advisories(self.store.snapshot()["records"]), [])

    def test_formed_and_unposted_outcome_includes_every_form_and_latest_outcome(self):
        self.record_form(closed=False)
        earlier = self.mutate(cli.OPERATIONS["form-outcome"], build_outcome())["result"]
        latest = self.mutate(cli.OPERATIONS["form-outcome"], build_outcome("closed", standing="not_examined",
            lack_of_means="No solver for the required range.", disposition=dict(status="set_aside",
                reason="The required range exceeds the available means.", form_id=None, candidate_id=None)))["result"]
        self.put_slate()
        report = self.record_report()
        self.assertEqual(report["outcome"], "Statements were formed and none was posted")
        self.assertEqual(report["forms"][0]["outcome"], latest)
        self.assertNotEqual(report["forms"][0]["outcome"], earlier)
        self.assertEqual(report["forms"][0]["move"]["kind"], "expectation")
        self.assertEqual(report["counts"]["forms_not_examined"], [dict(form_id="form-1", lack_of_means=latest["lack_of_means"])])

    def test_incomplete_assessment_can_be_reported_before_due_with_open_forms(self):
        self.record_form(closed=False)
        self.put_candidate()
        self.put_slate(["candidate-1"])
        self.assertFalse(problem_reports.is_report_due(self.store.snapshot()["records"]))
        report = self.record_report()
        self.assertEqual(report["outcome"], "The assessment is incomplete")
        self.assertEqual(report["candidates"][0]["disposition"], dict(status="selected"))
        self.assertEqual(report["forms"][0]["disposition"]["status"], "open")

    def test_each_gated_classification_and_outcome_use_the_digest_covered_value(self):
        self.select_statement()
        ordinary = self.put_value()
        self.put_gated_post(ordinary)
        gain = build_accounts()
        gain["gain"] = dict(id="gain", claim="A shared construction.", separate_grounds=[])
        self.put_value("later-gain", gain)
        report = self.record_report()
        self.assertEqual(report["outcome"], "Only ordinary problems were posted")
        statement = report["statements"][0]
        self.assertEqual(statement["publication"], "posted through the gate")
        self.assertTrue(statement["posts"][0]["passed"])
        self.assertEqual(statement["posts"][0]["classification"], "ordinary")
        self.assertEqual(statement["posts"][0]["covered_value_id"], ordinary["id"])
        self.assertEqual(statement["posts"][0]["after_post"]["values"][0]["id"], "later-gain")
        self.assertEqual(report["counts"]["posts"], {"a gain was established": 0, "ordinary": 1, "kind unassessed": 0})

    def test_gain_outcome_keeps_its_classification_after_later_ordinary_value(self):
        self.select_statement()
        accounts = build_accounts()
        accounts["gain"] = dict(id="gain", claim="A shared construction.", separate_grounds=[])
        value = self.put_value(accounts=accounts)
        self.put_gated_post(value, staged=True)
        self.put_value("later-ordinary")
        report = self.record_report()
        self.assertEqual(report["outcome"], "A statement of the kind was posted")
        self.assertEqual(report["statements"][0]["posts"][0]["classification"], "a gain was established")

    def test_covered_value_without_accounts_is_unassessed(self):
        self.select_statement()
        self.put_gated_post(self.put_value(accounts=False))
        report = self.record_report()
        self.assertEqual(report["outcome"], "Statements were posted and the kind of at least one is unassessed")
        self.assertEqual(report["statements"][0]["posts"][0]["classification"], "kind unassessed")

    def test_external_post_is_unassessed_even_when_a_value_claims_a_gain(self):
        self.select_statement()
        accounts = build_accounts()
        accounts["gain"] = dict(id="gain", separate_grounds=[])
        self.put_value(accounts=accounts)
        self.put_external_post()
        report = self.record_report()
        self.assertEqual(report["outcome"], "Statements were posted and the kind of at least one is unassessed")
        self.assertEqual(report["statements"][0]["publication"], "posted outside the gate")
        self.assertFalse(report["statements"][0]["posts"][0]["passed"])
        self.assertEqual(report["counts"]["posts"]["ordinary"], 0)

    def test_due_requires_a_current_slate_and_every_selected_candidate_to_end(self):
        self.assertFalse(problem_reports.is_report_due(self.store.snapshot()["records"]))
        self.put_candidate()
        self.put_candidate("candidate-2")
        self.put_slate(["candidate-1", "candidate-2"])
        self.select_statement(candidate_id="candidate-1")
        self.put_external_post()
        self.assertFalse(problem_reports.is_report_due(self.store.snapshot()["records"]))
        self.select_statement(identifier="statement-2", candidate_id="candidate-2", statement="A different question.")
        self.mutate(problem_claims.record_decision, dict(id="withdraw", action="withdraw", review_ids=[], bar_review_ids=[], reason="The case is too weak."))
        self.assertTrue(problem_reports.is_report_due(self.store.snapshot()["records"]))
        report = self.record_report()
        self.assertTrue(all(row["ended"] for row in report["candidates"]))
        self.assertEqual(report["candidates"][1]["withdrawal_ids"], ["withdraw"])
        self.put_candidate("new-candidate")
        self.assertFalse(problem_reports.is_report_due(self.store.snapshot()["records"]))

    def test_due_scope_is_exactly_the_two_start_kinds_or_a_forming_call(self):
        records = self.store.snapshot()["records"]
        records["problem_slate"] = dict(slate=dict(id="slate", candidate_ids=[], selected=[], deferred=[], rejected=[], recorded_revision=2))
        for kind in ("phenomenon", "thought_experiment", "field_question", "other", None):
            with self.subTest(kind=kind):
                marker = records["workspace"]["problem"]
                marker.pop("start", None)
                if kind is not None:
                    marker["start"] = dict(kind=kind, description="An open kind.")
                self.assertEqual(problem_reports.is_report_due(records), kind in ("phenomenon", "thought_experiment"))
                records["forming_call"] = dict(call=dict(id="call", forms=[]))
                self.assertTrue(problem_reports.is_report_due(records))
                records.pop("forming_call")

    def test_transferred_candidate_ends_with_its_destination(self):
        self.put_candidate()
        self.put_slate(["candidate-1"])
        transfer = self.mutate(cli.OPERATIONS["problem-candidate-transfer"], dict(id="transfer", candidate_id="candidate-1",
            destination_workspace=str(self.root.parent / "destination"), reason="The subject class changed."))["result"]
        self.assertTrue(problem_reports.is_report_due(self.store.snapshot()["records"]))
        candidate = self.record_report()["candidates"][0]
        self.assertEqual(candidate["transfers"], [transfer])
        self.assertEqual(candidate["ending"], "taken up in another record")

    def test_withdrawal_of_a_reselected_earlier_statement_keeps_its_target(self):
        self.put_candidate()
        self.put_candidate("candidate-2")
        self.put_slate(["candidate-1", "candidate-2"])
        self.select_statement(candidate_id="candidate-1")
        self.select_statement(identifier="statement-2", candidate_id="candidate-2", statement="Another exact question.")
        self.select_statement(candidate_id="candidate-1")
        decision = self.mutate(problem_claims.record_decision, dict(id="withdraw", action="withdraw", review_ids=[],
            bar_review_ids=[], reason="The case is too weak."))["result"]
        self.assertEqual(decision["statement_id"], "statement-1")
        self.assertEqual(decision["candidate_ids"], ["candidate-1"])
        endings = self.record_report()["candidates"]
        self.assertEqual([row["ended"] for row in endings], [True, False])

    def test_stored_withdrawal_does_not_guess_between_two_earlier_candidates(self):
        self.put_candidate()
        self.put_candidate("candidate-2")
        self.put_slate(["candidate-1", "candidate-2"])
        self.select_statement(candidate_id="candidate-1")
        self.select_statement(identifier="statement-2", candidate_id="candidate-2", statement="Another exact question.")
        self.select_statement(candidate_id="candidate-1")
        decision = dict(id="old-withdraw", action="withdraw", review_ids=[], bar_review_ids=[],
                        reason="The case is too weak.", problem_evidence_digest=None,
                        recorded_revision=self.store.revision + 1)
        self.put("problem_decision", decision["id"], decision)
        report = self.record_report()
        self.assertFalse(any(row["ended"] for row in report["candidates"]))
        self.assertEqual(report["withdrawals"], [dict(decision=decision, candidate_ids=[], target_recorded=False)])
        self.assertEqual(report["outcome"], "The assessment is incomplete")

    def test_stored_withdrawal_can_end_the_only_possible_earlier_candidate(self):
        self.put_candidate()
        self.put_slate(["candidate-1"])
        self.select_statement(candidate_id="candidate-1")
        self.select_statement(identifier="second-version", candidate_id="candidate-1", neighbours=["Another comparison."])
        decision = dict(id="old-withdraw", action="withdraw", review_ids=[], bar_review_ids=[],
                        reason="The case is too weak.", problem_evidence_digest=None,
                        recorded_revision=self.store.revision + 1)
        self.put("problem_decision", decision["id"], decision)
        self.assertTrue(problem_reports.is_report_due(self.store.snapshot()["records"]))
        report = self.record_report()
        self.assertEqual(report["candidates"][0]["ending"], "withdrawn")
        self.assertEqual(report["withdrawals"], [dict(decision=decision, candidate_ids=["candidate-1"], target_recorded=False)])

    def test_withdrawal_before_formulation_can_end_the_selected_candidates(self):
        self.put_candidate()
        self.put_slate(["candidate-1"])
        decision = self.mutate(problem_claims.record_decision, dict(id="withdraw", action="withdraw", review_ids=[],
            bar_review_ids=[], reason="The work cannot establish the necessary evidence."))["result"]
        self.assertEqual(decision["candidate_ids"], ["candidate-1"])
        self.assertTrue(problem_reports.is_report_due(self.store.snapshot()["records"]))

    def test_reselection_after_a_withdrawal_reopens_the_assessment(self):
        self.put_candidate()
        self.put_slate(["candidate-1"])
        self.select_statement(candidate_id="candidate-1")
        self.mutate(problem_claims.record_decision, dict(id="withdraw", action="withdraw", review_ids=[],
            bar_review_ids=[], reason="The case is too weak."))
        self.assertTrue(problem_reports.is_report_due(self.store.snapshot()["records"]))
        self.put_slate(["candidate-1"])
        self.assertFalse(problem_reports.is_report_due(self.store.snapshot()["records"]))

    def test_due_report_refuses_open_or_unrecorded_form_outcomes_atomically(self):
        self.record_form(closed=False)
        self.put_slate()
        revision = self.store.revision
        self.assert_error("problem_report_forms_open", self.record_report)
        self.assertEqual(self.store.revision, revision)
        self.mutate(cli.OPERATIONS["form-outcome"], build_outcome())
        self.assert_error("problem_report_forms_open", self.record_report)
        self.assertNotIn("problem_report", self.store.snapshot()["records"])

    def test_refuses_a_gated_post_without_its_exact_evidence_snapshot(self):
        statement = self.select_statement()
        value = self.put_value()
        post = self.put_gated_post(value)
        self.put("problem_post", "post", dict(post, problem_evidence_digest="different-evidence"))
        self.assert_error("problem_report_evidence_missing", self.record_report)
        self.put("problem_post", "post", post)
        self.put("problem_value", value["id"], dict(value, current_state=[dict(text="Changed.", evidence=[])]))
        self.assert_error("problem_report_evidence_missing", self.record_report)
        self.assertEqual(statement["id"], post["statement_id"])

    def test_payload_refuses_missing_blank_and_author_entered_computed_fields(self):
        operation = cli.OPERATIONS["problem-report"]
        for payload in ({}, dict(id="report", reopen_when=""), dict(id="report", reopen_when=None),
                        dict(id="", reopen_when="New evidence."), dict(id="report", reopen_when="New evidence.", outcome="success")):
            with self.subTest(payload=payload):
                self.assert_error("invalid_input", lambda: self.mutate(operation, payload))

    def test_dependency_projection_stales_for_every_read_kind_but_not_unrelated_work(self):
        self.put_slate()
        self.record_report()
        records = self.store.snapshot()["records"]
        required_kinds = {
            "workspace", "problem_area", "forming_call", "form_outcome", "problem_forming_import", "forming_transfer",
            "problem_candidate", "problem_slate", "problem_statement", "problem_selection", "problem_value",
            "problem_decision", "problem_post", "problem_post_time", "problem_post_fact", "value_review",
            "review_adjudication", "review_assignment", "review_epoch", "problem_attempt", "native_attempt",
            "native_attempt_current", "cycle_plan", "execution", "cycle_assessment", "probe", "resource_account",
            "resource_budget", "strategy_account", "review_observation", "review_budget", "review_invocation",
            "review_attempt", "review_probe_invocation", "review_route_probe", "review_output_correction_invocation",
            "review_output_correction", "review_reservation",
        }
        self.assertLessEqual(required_kinds, set(problem_reports.project_dependencies(records)))
        for kind in required_kinds:
            with self.subTest(kind=kind):
                changed = copy.deepcopy(records)
                changed.setdefault(kind, {})["new"] = dict(id="new")
                self.assertEqual(problem_reports.describe_report_advisories(changed)[0]["code"], "problem_report_stale")
        self.put("literature_operation", "unrelated", dict(operation="unrelated"))
        self.assertEqual(problem_reports.describe_report_advisories(self.store.snapshot()["records"]), [])
        self.record_report("report-2")
        self.assertEqual(problem_reports.describe_report_advisories(self.store.snapshot()["records"]), [])

    def test_status_advisories_change_no_gate_obligation_or_next_action(self):
        self.put_slate()
        before = cli.status_report(self.store)
        self.assertIn("problem_report_due", [row["code"] for row in before["advisories"]])
        for summary in (cli.status_summary(before), cli.next_summary(before)):
            self.assertIn("problem_report_due", [row["code"] for row in summary["advisories"]])
        self.record_report()
        after = cli.status_report(self.store)
        for key in ("ready", "obligations", "next", "milestone"):
            self.assertEqual(after[key], before[key])
        self.put("resource_account", "research:literature", dict(charged=dict(readings=2), unknown=dict(readings=0)))
        stale = cli.status_report(self.store)
        self.assertIn("problem_report_stale", [row["code"] for row in stale["advisories"]])

    def test_report_views_keep_the_current_report_and_all_earlier_reports(self):
        self.put_slate()
        first = self.record_report()
        second = self.record_report("report-2")
        output_views.render_all(self.store)
        for path, expected in (("final/problem/reports/report-2.json", second),
                               ("final/history/problem/reports/report-1.json", first)):
            self.assertEqual(json.loads((self.root / path).read_text()), expected)

    def test_large_report_view_keeps_the_complete_computed_table(self):
        self.record_form(closed=False, forms=[build_form(text="A" * (output_views.COPY_LIMIT_BYTES + 1))])
        report = self.record_report()
        output_views.render_all(self.store)
        self.assertEqual(json.loads((self.root / "final/problem/reports/report-1.json").read_text()), report)

    def test_request_replay_returns_original_report_after_dependencies_change(self):
        payload = dict(id="report", reopen_when="A new proof method.")
        revision = self.store.revision
        operation = cli.OPERATIONS["problem-report"]
        first = operation(self.store, payload, expected_revision=revision, request_id="report-request")
        self.put_slate()
        self.assertEqual(operation(self.store, payload, expected_revision=revision, request_id="report-request"), first)
        self.assert_error("record_conflict", lambda: self.mutate(operation, payload))

    def test_check_mode_and_example_command_use_the_public_payload_without_writes(self):
        examples = json.loads((Path(__file__).resolve().parents[1] / "docs/research-cli-examples.json").read_text())
        response = self.invoke_command("problem-report", examples["problem-report"])
        self.assert_accepted_check(response, self.store.revision)
        self.assertNotIn("problem_report", self.store.snapshot()["records"])

    def test_form_counts_include_all_evidenced_standings_and_unexamined_forms(self):
        self.record_form(forms=[build_form("form-" + str(index)) for index in range(8)], closed=False)
        records = self.store.snapshot()["records"]
        for index, standing in enumerate(("refuted", "proved", "implied_by_known_results", "found_in_literature",
                                          "without_evidence", "not_examined", "supported", "unresolved")):
            reference = dict(kind="result", native_attempt_digest="delivered", acceptance_id="proof")
            evidence = [dict(standing="admissible", reference=reference)] if index < 4 else []
            outcome = build_outcome("outcome-" + str(index), form_id="form-" + str(index), standing=standing,
                evidence=evidence, disposition=dict(status="set_aside", reason="No further work.", form_id=None, candidate_id=None),
                lack_of_means="No effective bound." if standing == "not_examined" else None)
            records.setdefault("form_outcome", {})[outcome["id"]] = dict(outcome, recorded_revision=index + 2)
        report = problem_reports.compute_report(records, self.artifacts, self.store.revision)
        self.assertEqual(report["counts"]["forms_with_evidence"], 4)
        self.assertEqual(report["counts"]["forms_without_evidence"], 1)
        self.assertEqual(len(report["counts"]["forms_not_examined"]), 1)
        self.assertEqual(report["counts"]["forming"]["forms"], 8)
        self.assertEqual(report["imports"], [])
        self.assertEqual(report["forms"][0]["evidence"][0]["reference"], reference)

    def test_candidates_keep_latest_slate_dispositions_and_statements_without_posts(self):
        self.put_candidate()
        self.put_candidate("candidate-2")
        self.put_candidate("candidate-3")
        self.put_slate(["candidate-1"])
        slate = self.store.snapshot()["records"]["problem_slate"]["slate"]
        slate.update(deferred=[dict(candidate_id="candidate-2", evidence_needed="A finite check.")],
                     rejected=[dict(candidate_id="candidate-3", reason="Already known.")])
        self.put("problem_slate", "slate", slate)
        self.select_statement(candidate_id="candidate-1")
        report = self.record_report()
        self.assertEqual([row["disposition"]["status"] for row in report["candidates"]], ["selected", "deferred", "rejected"])
        self.assertEqual(report["statements"][0]["publication"], "not posted")
        self.assertEqual(report["statements"][0]["posts"], [])

    def test_post_dates_facts_later_reviews_consequences_and_measured_spend(self):
        statement = self.select_statement()
        post = self.put_external_post()
        accounts = build_accounts()
        reference = dict(kind="result", native_attempt_digest="delivered", acceptance_id="proof")
        relation = build_relation("consequence", standing="established", evidence=[reference],
            consequence=dict(assertion="The finite bound follows.", instances_or_translation="Restrict the domain.",
                             credited_to="statement", weaker_derivations=[]))
        accounts["relations"] = [relation, build_relation("unproved")]
        value = self.put_value(accounts=accounts)
        fact = dict(id="fact", post_id=post["id"], fact="An earlier theorem implies the bound.", date="unknown",
                    recorded_revision=self.store.revision + 1)
        self.put("problem_post_fact", "fact", fact)
        reviews = []
        for kind in ("value_review", "review_adjudication"):
            row = dict(id=kind, subject=dict(kind="problem", statement_id=statement["id"]), recorded_revision=self.store.revision + 1)
            self.put(kind, kind, row)
            reviews.append(dict(kind=kind, record=row))
        account = dict(key="research:literature", charged=dict(readings=3), unknown=dict(model_input_tokens=1))
        self.put("resource_account", "research:literature", account)
        self.put("review_attempt", "spent", dict(id="spent", status="completed", usage=dict(input_tokens=30, output_tokens=None)))
        report = self.record_report()
        shown = report["statements"][0]["posts"][0]
        self.assertEqual(shown["posted_at"], None)
        self.assertTrue(shown["has_post_fact"])
        self.assertEqual(shown["after_post"], dict(values=[value], facts=[fact], reviews=reviews))
        self.assertEqual(shown["consequences"], [dict(relation=relation, result_references=[reference], value_id=value["id"])])
        self.assertIn("does not hold whether", shown["proof_publication"])
        self.assertEqual(report["counts"]["relations"], dict(established=1, expected=1))
        self.assertEqual(report["spent"]["accounts"], {"research:literature": account})
        self.assertEqual(report["spent"]["reviews"]["observed"]["input_tokens"], 30)
        self.assertEqual(report["spent"]["reviews"]["unknown"]["output_tokens"], 1)

    def test_recorded_time_of_a_legacy_external_post_is_used(self):
        statement = self.select_statement()
        self.put("problem_post", "legacy", dict(id="legacy", gate="bypassed", statement_id=statement["id"],
            statement_digest=problem_claims.compute_statement_digest(statement), posted_at=None))
        time = self.mutate(problem_claims.record_post_time, dict(post_id="legacy", posted_at="1997-03-04T00:00:00Z",
            evidence="The original public timestamp."))["result"]
        shown = self.record_report()["statements"][0]["posts"][0]
        self.assertEqual(shown["posted_at"], time["posted_at"])
        self.assertFalse(shown["has_post_fact"])
        self.assertEqual(shown["time_statement"], time)

    def test_gain_takes_priority_over_an_unassessed_post_and_posts_are_counted_once(self):
        self.select_statement()
        accounts = build_accounts()
        accounts["gain"] = dict(id="gain", separate_grounds=[])
        self.put_gated_post(self.put_value(accounts=accounts))
        self.select_statement(identifier="same-text")
        statement = self.select_statement(identifier="other-text", statement="A different bound.")
        self.mutate(problem_claims.record_post_external, dict(id="outside", problem_id="656c336e-e892-4c47-80d2-a71d022f4116",
            statement_digest=problem_claims.compute_statement_digest(statement), reason="The owner requested it.", evidence="The request."))
        report = self.record_report()
        self.assertEqual(report["outcome"], "A statement of the kind was posted")
        self.assertEqual(report["counts"]["posts"], {"a gain was established": 1, "ordinary": 0, "kind unassessed": 1})

    def test_later_value_on_same_text_with_new_neighbours_is_shown_after_original_post(self):
        self.select_statement()
        value = self.put_value()
        self.put_gated_post(value)
        self.select_statement(identifier="same-text", neighbours=["A second comparison."])
        later = self.put_value("later-value")
        shown = self.record_report()["statements"][0]["posts"][0]
        self.assertEqual(shown["covered_value_id"], value["id"])
        self.assertEqual(shown["after_post"]["values"], [later])

    def test_test_counts_use_the_run_assessment_and_keep_the_frozen_plan(self):
        from test_problem_accounts_tests import AccountTestCreditTests
        case = AccountTestCreditTests(methodName="runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        self.addCleanup(case.tearDown)
        case.record()
        records = case.store.snapshot()["records"]
        for signal, expected in (("not_observed", "passed"), ("observed", "failed"), ("unresolved", "unresolved")):
            with self.subTest(signal=signal):
                records["cycle_assessment"]["assessment-1"]["payload"]["failures"][0]["status"] = signal
                report = problem_reports.compute_report(records, case.artifacts, case.store.revision)
                self.assertEqual(report["counts"]["tests"], dict(total=1, **{
                    key: int(key == expected) for key in ("passed", "failed", "unresolved")}))
                self.assertEqual(report["counts"]["runs"], 1)
                self.assertEqual(report["tests"][0]["plan"], case.planned)
                self.assertEqual(report["tests"][0]["execution_id"], case.observed["id"])


class ProblemReportScopeTests(ProblemCase):
    def test_outside_problem_record_is_refused(self):
        self.assert_error("orchestration_conflict", lambda: self.mutate(cli.OPERATIONS["problem-report"],
            dict(id="report", reopen_when="A new argument.")))

    def test_stored_0820_withdrawal_keeps_validation_digests_projection_rendering_and_status(self):
        fixtures = Path(__file__).parent / "fixtures"
        fixture = json.loads((fixtures / "problem_report_decision_0820.json").read_text())
        source = json.loads((fixtures / fixture["source_fixture"]).read_text())
        self.initialize_problem()

        def restore(transaction):
            for kind, rows in source["records"].items():
                for key, record in rows.items():
                    transaction.put(kind, key, record)

        self.store.mutate("test.restore", {}, restore, expected_revision=self.store.revision, request_id="restore")
        decision = fixture["decision"]
        self.put("problem_decision", decision["id"], decision)
        records = self.store.snapshot()["records"]
        self.assertNotIn("statement_id", decision)
        self.assertEqual(digest(records["problem_decision"][decision["id"]]), fixture["decision_digest"])
        self.assertEqual(problem_claims.compute_problem_evidence_digest(records, self.artifacts), fixture["evidence_digest"])
        self.assertEqual(problem_evidence.describe_binding(records, self.artifacts), fixture["binding"])
        self.assertEqual(problem_evidence.describe_evidence(records, self.artifacts), fixture["evidence"])
        report = cli.status_report(self.store)
        report.pop("runtime")
        self.assertEqual(report, fixture["status"])
        self.assertEqual(check_integrity(self.store), fixture["integrity"])
        model = output_views.compute_view_model(records, self.artifacts, output_views._ViewInputs({}, {}))
        self.assertEqual({path: dict(content=view.content.decode(), entry=view.entry) for path, view in model.items()
                          if "decisions" in path}, fixture["views"])
        self.assertFalse(problem_reports.is_report_due(records))
        self.assertEqual(problem_reports.describe_report_advisories(records), [])


class ProblemReportGateTests(delivery.ProblemDeliveryCase):
    def test_a_real_post_decision_passes_and_report_reads_its_covered_accounts(self):
        self.prepare_ready()
        self.record_post_decision()
        exported = problem_delivery.export_problem(self.store)
        post = self.mutate(problem_claims.record_posted, dict(problem_id="656c336e-e892-4c47-80d2-a71d022f4116",
            export_directory=exported["destination"]))["result"]
        before = cli.status_report(self.store)
        report = self.mutate(cli.OPERATIONS["problem-report"], dict(id="report", reopen_when="A new proof."))["result"]
        shown = report["statements"][0]["posts"][0]
        self.assertEqual(shown["problem_evidence_digest"], post["problem_evidence_digest"])
        self.assertEqual(shown["classification"], "ordinary")
        self.assertEqual(shown["posted_at"], post["posted_at"])
        after = cli.status_report(self.store)
        for key in ("ready", "obligations", "next", "milestone", "after_post"):
            self.assertEqual(after[key], before[key])
