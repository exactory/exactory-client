"""A recorded delivery remains terminal when later rules find new post defects."""

import copy
from unittest.mock import patch

import test_research_problem_delivery as delivery
import test_research_problem_objections as objections
from research_harness import cli, problem_delivery, research_decisions, review_protocol
from research_harness.evidence import digest
from research_harness.gates import gate_state
from research_harness.report_views import next_summary


class PostedProblemTests(delivery.ProblemDeliveryCase):
    make_finding = objections.ProblemObjectionTests.make_finding
    prepare_problem_evidence = objections.ProblemObjectionTests.prepare_problem_evidence
    prepare_reviews = objections.ProblemObjectionTests.prepare_reviews
    observe_adjudication = objections.ProblemObjectionTests.observe_adjudication

    def make_objection(self):
        assessment = self.store.snapshot()["records"]["problem_assessment"]["assessment-1"]
        return dict(objections.ProblemObjectionTests.make_objection(self), evidence=[{
            "kind": "record", "record_kind": "problem_assessment", "id": "assessment-1",
            "digest": digest(assessment),
        }])

    def post_current_statement(self):
        self.record_post_decision()
        exported = problem_delivery.export_problem(self.store)
        return self.mutate(self.load_problem_module().record_posted, {
            "problem_id": "656c336e-e892-4c47-80d2-a71d022f4116",
            "export_directory": exported["destination"],
        })["result"]

    def assert_terminal(self, post, expected_codes):
        before = self.store.snapshot()
        report = cli.status_report(self.store)
        terminal = {
            "code": "problem_already_posted",
            "explanation": "This exact statement already has a recorded post.",
            "post_id": post["id"], "problem_id": post["problem_id"],
        }
        self.assertEqual(report["next"], terminal)
        self.assertEqual(report["obligations"], [terminal])
        self.assertEqual(report["milestone"], "post")
        self.assertFalse(report["ready"])
        self.assertEqual(report["counts"]["obligations"], 1)
        self.assertLessEqual(expected_codes, {row["code"] for row in report["after_post"]})
        for row in report["after_post"]:
            self.assertTrue(row["reason"])
            self.assertTrue(row["explanation"])
        summary = next_summary(report)
        self.assertEqual(summary["next_hint"]["code"], "problem_already_posted")
        self.assertEqual(summary["after_post"], len(report["after_post"]))
        self.assertEqual(summary["post_obligations"]["total"], 1)
        self.assertEqual(self.store.snapshot(), before)
        gate = gate_state(before["records"], self.artifacts, "post")
        self.assertEqual(gate["obligations"], [terminal])
        self.assertEqual(gate["after_post"], report["after_post"])
        return report

    def test_changed_evidence_digest_after_post_is_advisory(self):
        self.prepare_ready()
        post = self.post_current_statement()
        old_digest = post["problem_evidence_digest"]
        records = self.store.snapshot()["records"]
        changed = copy.deepcopy(records["problem_value"]["value-1"])
        changed.pop("recorded_revision")
        changed["id"] = "value-after-post"
        changed["motivation"][0]["text"] = "The same bound also connects two finite constructions."
        self.mutate(self.load_problem_module().record_value, changed)
        report = self.assert_terminal(post, {"problem_evidence_unassessed", "problem_decision_stale"})
        self.assertNotEqual(report["problem_evidence_digest"], old_digest)
        self.assertNotIn("gate", post)
        self.assertEqual(report["post"], dict(post, gate="passed"))

    def test_legacy_adjudication_missing_later_version_evidence_is_advisory(self):
        self.prepare_reviews(result_reviewer="first")
        add_focused = review_protocol.add_focused_adjudication

        def build_legacy_packet(records, artifacts, packet, *args, **kwargs):
            add_focused(records, artifacts, packet, *args, **kwargs)
            packet["version_evidence"] = []

        # Historical rules accepted this adjudication and its post. Today's
        # delivery audit runs unmodified after those immutable records exist.
        with patch.object(review_protocol, "add_focused_adjudication", side_effect=build_legacy_packet), \
                patch.object(research_decisions, "find_undelivered_adjudication_keys", return_value=[]):
            adjudication = self.observe_adjudication()
            post = self.post_current_statement()
        records = self.store.snapshot()["records"]
        version = next(row for row in research_decisions.collect_problem_post_objections(
            records, self.artifacts, list(records["value_review"].values()))
            if row["id"] == "unsupported-implication")
        missing = research_decisions.find_undelivered_adjudication_keys(
            records, self.artifacts, adjudication["assignment_id"],
            version_evidence=review_protocol.describe_version_evidence(records, self.artifacts, version))
        self.assertEqual(missing, ["version_evidence:problem_assessment:assessment-1"])
        self.assert_terminal(post, {"research_objection_unresolved"})

    def test_new_unrelated_post_finding_is_advisory(self):
        self.prepare_ready()
        post = self.post_current_statement()
        finding = {"code": "future_post_check", "explanation": "A later rule requires another recorded check.",
                   "reason": "The inventory does not contain the named evidence unit.",
                   "assignment_id": "result-first"}
        with patch.object(problem_delivery, "assess_source_requests", return_value=[finding]):
            report = self.assert_terminal(post, {"future_post_check"})
        self.assertEqual(report["after_post"], [finding])

    def test_a_later_empty_slate_does_not_replace_the_terminal_next(self):
        self.prepare_ready()
        post = self.post_current_statement()
        records = self.store.snapshot()["records"]
        slate = dict(records["problem_slate"]["slate-1"], selected=[], reason="No further post is planned.")
        self.put("problem_slate", "slate-1", slate)
        self.assert_terminal(post, {"problem_candidate_unselected"})

    def test_post_matching_uses_the_exact_claim_digest_across_statement_ids(self):
        self.prepare_ready()
        post = self.post_current_statement()
        self.select_statement(identifier="same-claim", title="A new title for the same claim", candidate_id="candidate-1")
        self.assert_terminal(post, {"problem_value_missing"})
        self.select_statement(identifier="changed-claim", statement="Does the bound hold for every odd input?")
        report = cli.status_report(self.store)
        self.assertNotEqual(report["next"]["code"], "problem_already_posted")
        self.assertEqual(report.get("after_post", []), [])
        self.assertIsNone(report.get("post"))
