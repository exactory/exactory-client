"""Bounded advisory report views: size, omission accounting, source exclusion, immutability."""

import copy
import json
import unittest

from research_harness.errors import ResearchError
from research_harness.report_views import next_summary, obligations_page, order_obligations, status_summary


def encoded_size(value):
    return len(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"))


def report_fixture():
    obligations = [{"code": "reading_missing", "explanation": "Read the exact source."} for _ in range(20)]
    return {"revision": 27, "profile": "research", "ready": False,
            "study": {"stage": "literature", "status": "pending", "waiting": None},
            "obligations": obligations, "preparation": {"ready": False, "obligations": obligations},
            "next": {"code": "reading_missing", "version_id": "arxiv:2601.00001v1"},
            "readings": {"large": {"notes": "PRIVATE_SOURCE_SENTINEL" * 10000}},
            "runtime": {"plugin_version": "0.39.0"}, "counts": {"obligations": 20}}


def grand_challenge_view(challenges=100, criteria=100):
    """A Grand Challenge record at its widest: many challenges and criteria, and ids of the costliest character.

    The store refuses only NUL in text, and JSON writes another control character as six bytes."""
    return {"id": "\x01" * 10000, "reason": "PRIVATE_REASON_SENTINEL" * 1000, "challenges": [
        {"id": "c%03d" % number + "\x01" * 10000, "horizon": "ultimate" if number % 2 == 0 else "near_term",
         "statement": "PRIVATE_STATEMENT_SENTINEL" * 1000, "state": "s" * 10000, "evidence": [],
         "criteria": [{"id": "r%03d-%03d" % (number, item) + "\x01" * 10000, "statement": "t" * 10000}
                      for item in range(criteria)]}
        for number in range(challenges)]}


def round_view():
    """A round summary at its widest: every purpose fresh, every count and measure at a large value."""
    measure = {"median": 10 ** 9 + 0.5, "spread": [10 ** 9, 10 ** 9]}
    units = ("network_requests", "source_bytes", "readings", "screenings", "model_input_tokens", "model_output_tokens",
             "wall_seconds", "rounds")
    amounts = {unit: 10 ** 9 for unit in units}
    return {"number": 10 ** 9, "active": True, "assessed": False, "decision": "continue", "obligations": 10 ** 9,
            "progress": {"fresh_purposes": ["changes", "downstream", "exemplars", "next_step"], "exemplar": True,
                         "cycles": 10 ** 9, "new_claims": 10 ** 9, "dropped_claims": 10 ** 9, "readings": 10 ** 9},
            "measurement": {"reviews": {"count": 10 ** 9, "soundness": measure, "presentation": measure, "contribution": measure,
                                        "overall": measure},
                            "predictions": {"count": 10 ** 9, "percentile": measure}},
            "limits": {"literature": dict(amounts), "experiment": dict(amounts)},
            "budget": {unit: {"limit": 10 ** 9, "charged": 10 ** 9, "reserved": 10 ** 9, "unknown": 10 ** 9} for unit in units},
            "usage": {"literature": dict(amounts), "experiment": dict(amounts)}}


class ReportViewTests(unittest.TestCase):
    def test_recovery_precedes_incomplete_research_and_missing_review_pair(self):
        codes = ["problem_review_pair_required", "review_quality_measurement_pending", "assessment_incomplete",
                 "review_task_pending", "review_observation_acceptance_pending", "review_worker_reconciliation_required"]
        ordered = order_obligations([dict(code=code, **({"next": [{"command": "review-run"}]} if code == "review_task_pending" else {}))
                                     for code in codes])
        self.assertEqual([row["code"] for row in ordered], list(reversed(codes)))

    def test_dependency_blocked_tasks_do_not_hide_exhausted_scientific_work(self):
        blocked = {"code": "review_task_pending", "state": "blocked", "next": []}
        incomplete = {"code": "assessment_incomplete", "next": [{"workflow": "problem-value", "scope": {"claim_id": "c"}}]}
        self.assertEqual(order_obligations([blocked, incomplete])[0], incomplete)

    def test_aggregate_pending_does_not_hide_a_concrete_ready_task(self):
        aggregate = {"code": "review_task_pending", "occupied": 2, "pending": [{"slot_id": "A"}]}
        concrete = {"code": "review_task_pending", "state": "ready", "task_id": "read", "next": [
            {"command": "review-run", "payload": {"assignment_id": "controller", "task_id": "read", "epoch_id": "epoch"}}]}
        for number in range(32):
            concrete["task_id"] = "read-" + str(number)
            self.assertEqual(order_obligations([aggregate, concrete])[0], concrete)

    def test_completed_scientific_defects_precede_measurement_qualification(self):
        rows = [{"code": "review_quality_measurement_pending"}, {"code": "problem_assurance_pending"},
                {"code": "research_objection_unresolved"}]
        self.assertEqual(order_obligations(rows)[-1]["code"], "review_quality_measurement_pending")

    def test_status_summary_counts_occupied_complete_and_incomplete_slots_separately(self):
        report = report_fixture()
        report["review"] = {"slot_outcomes": {"occupied": 2, "complete": [{"id": "R"}],
                                               "incomplete": [{"id": "I", "unresolved_scope": "PRIVATE"}], "pending": []}}
        summary = status_summary(report)
        self.assertEqual(summary["review_slots"], {"occupied": 2, "complete": 1, "incomplete": 1, "pending": 0})
        self.assertNotIn("PRIVATE", json.dumps(summary))

    def test_next_summary_keeps_a_small_exact_recovery_payload(self):
        report = report_fixture()
        action = {"command": "review-run", "payload": {"assignment_id": "controller", "epoch_id": "epoch",
                                                       "task_id": "task", "action": "accept"}}
        report["next"] = {"code": "review_observation_acceptance_pending", "assignment_id": "controller", "next": [action]}
        before = copy.deepcopy(report)
        hint = next_summary(report)["next_hint"]
        self.assertEqual(hint["next_step"], action)
        self.assertEqual(report, before)

    def test_large_recovery_payload_is_omitted_whole_and_available_on_the_obligations_page(self):
        report = report_fixture()
        action = {"command": "review-assignment", "payload": {"scope": "x" * 10000}}
        item = {"code": "assessment_incomplete", "next": [action]}
        report.update(next=item, obligations=[item], preparation={})
        hint = next_summary(report)["next_hint"]
        self.assertIsNone(hint["next_step"])
        self.assertTrue(hint["details_required"])
        self.assertEqual(obligations_page(report, "assessment_incomplete", limit=1, cursor=None)["obligations"][0]["next"], [action])
        self.assertLessEqual(encoded_size(next_summary(report)), 4096)

    def test_exact_action_respects_the_total_next_summary_budget(self):
        report = report_fixture()
        report["next"] = {key: "\x01" * 10000 for key in ("code", "version_id", "work_id", "collection_id", "unit_id", "explanation")}
        report["next"]["next"] = [{"command": "review-run", "payload": {"id": "x" * 900}}]
        result = next_summary(report)
        self.assertLessEqual(encoded_size(result), 4096)
        if result["next_hint"]["next_step"] is not None:
            self.assertEqual(result["next_hint"]["next_step"], report["next"]["next"][0])

    def test_depth_plan_ids_remain_bounded_without_changing_full_status(self):
        report = report_fixture()
        values = {"loop_abstracts": 100, "round_loop_abstracts": 40, "hits_per_query": 10,
                  "innovation_candidates": 10, "innovation_cases": 5, "sample_size": 100,
                  "search_readings": 20, "core_fulltexts": 10}
        report["limits"] = {"depth": {"plan_id": "\x01" * 10000, "values": values}}
        before = copy.deepcopy(report)
        view = status_summary(report)
        self.assertEqual(view["limits"]["depth"]["plan_id"], "\x01" * 64)
        self.assertEqual(view["limits"]["depth"]["values"], values)
        self.assertLessEqual(encoded_size(view), 16 * 1024)
        self.assertEqual(report, before)

    def test_views_preserve_state_and_exclude_source_bodies(self):
        report = report_fixture()
        before = copy.deepcopy(report)
        for view in (status_summary(report), next_summary(report)):
            self.assertEqual(view["revision"], 27)
            self.assertFalse(view["ready"])
            self.assertFalse(view["preparation_ready"])
            self.assertTrue(view["advisory_only"])
            self.assertNotIn("PRIVATE_SOURCE_SENTINEL", json.dumps(view))
        self.assertEqual(report, before)

    def test_obligation_counts_include_undisplayed_codes(self):
        report = report_fixture()
        report["obligations"] = [{"code": "code_%02d" % i} for i in range(20)]
        summary = status_summary(report)["obligations"]
        self.assertEqual(summary["total"], 20)
        self.assertEqual(summary["shown_obligations"], 6)
        self.assertEqual(summary["omitted_obligations"], 14)
        self.assertEqual(summary["omitted_codes"], 14)

    def test_worst_case_strings_stay_bounded_and_are_marked(self):
        report = report_fixture()
        long_text = "\x00" * 10000
        report["profile"] = long_text
        report["study"] = {"stage": long_text, "status": long_text, "waiting": long_text}
        report["next"] = {key: long_text for key in ("code", "version_id", "work_id", "collection_id", "unit_id", "explanation")}
        report["obligations"] = [{"code": long_text + str(i)} for i in range(100)]
        report["preparation"]["obligations"] = report["obligations"]
        report["runtime"] = {"plugin_version": "0.39.0", "source_commit": "a" * 40, "dirty": True,
                             "executable": "/x" * 100, "package_digest": "b" * 64, "schema_version": 1,
                             "constitution": {"version": "3", "sha256": "c" * 64}}
        report["evaluation"] = {"reads": 10 ** 9, "artifacts_verified": 10 ** 9, "bytes_verified": 10 ** 12, "computed": 10 ** 6,
                                "readings_assessed": 10 ** 6, "links_validated": 10 ** 6, "graph_builds": 3, "cohort_reports": 1,
                                "elapsed_seconds": 123456.789}
        report["round"] = round_view()
        report["grand_challenge"] = grand_challenge_view()
        report["limits"] = {"policy": "lineage-v1", "loop": {"readings": 1000, "limit": 1000,
            "covered": ["direct", "originals", "theory", "adjacent", "recent"]}, "sample": None,
            "innovation_candidates": {"families": 100, "required": 100}, "search_readings": None, "core_papers": None,
            "depth": {"plan_id": "\x01" * 10000, "values": {"loop_abstracts": 1000, "round_loop_abstracts": 400,
                "hits_per_query": 100, "innovation_candidates": 100, "innovation_cases": 50, "sample_size": 1000,
                "search_readings": 200, "core_fulltexts": 100}}}
        report["probes"] = {"count": 10 ** 9, "elapsed_seconds": 10 ** 9 + 0.5}
        status, upcoming = status_summary(report), next_summary(report)
        self.assertLessEqual(encoded_size(status), 16 * 1024)
        self.assertLessEqual(encoded_size(upcoming), 4 * 1024)
        challenge = status["grand_challenge"]
        self.assertEqual(len(challenge["criterion_ids"]) + challenge["omitted_criterion_ids"], 100 * 100)
        self.assertEqual(status["limits"]["depth"]["values"], report["limits"]["depth"]["values"])
        self.assertTrue(upcoming["next_hint"]["truncated_fields"])
        self.assertTrue(upcoming["next_hint"]["details_required"])

    def test_absent_next_and_non_ascii_content_are_supported(self):
        report = report_fixture()
        report["next"] = None
        self.assertIsNone(next_summary(report)["next_hint"])
        report["next"] = {"explanation": "\U0001f52c" * 10000}
        self.assertLessEqual(encoded_size(next_summary(report)), 4 * 1024)

    def test_ready_state_is_preserved_without_granting_authority(self):
        report = report_fixture()
        report.update(ready=True, obligations=[], next=None)
        report["preparation"] = {"ready": True, "obligations": []}
        view = status_summary(report)
        self.assertTrue(view["ready"])
        self.assertTrue(view["preparation_ready"])
        self.assertTrue(view["advisory_only"])
        self.assertEqual(view["obligations"]["total"], 0)
        self.assertEqual(view["obligations"]["omitted_obligations"], 0)

    def test_the_round_object_is_copied_into_the_status_summary(self):
        report = report_fixture()
        self.assertIsNone(status_summary(report)["round"])
        report["round"] = round_view()
        view = status_summary(report)
        self.assertEqual(view["round"], round_view())
        self.assertEqual(view["round"]["number"], 10 ** 9)
        self.assertLessEqual(encoded_size(view), 16 * 1024)
        self.assertNotIn("round", next_summary(report))

    def test_the_grand_challenge_view_names_criterion_ids_and_counts_what_it_omits(self):
        report = report_fixture()
        self.assertIsNone(status_summary(report)["grand_challenge"])
        report["grand_challenge"] = {"id": "grand-challenge", "reason": "PRIVATE_REASON_SENTINEL", "challenges": [
            {"id": "general", "horizon": "ultimate", "criteria": [{"id": "rc-general", "statement": "PRIVATE_STATEMENT_SENTINEL"}]},
            {"id": "finite", "horizon": "near_term", "criteria": [{"id": "rc-finite", "statement": "PRIVATE_STATEMENT_SENTINEL"}]}]}
        self.assertEqual(status_summary(report)["grand_challenge"],
                         {"id": "grand-challenge", "ultimate": 1, "near_term": 1, "criterion_ids": ["rc-general", "rc-finite"],
                          "omitted_criterion_ids": 0})
        report["grand_challenge"] = grand_challenge_view()
        view = status_summary(report)["grand_challenge"]
        self.assertEqual((view["ultimate"], view["near_term"]), (50, 50))
        self.assertEqual(len(view["criterion_ids"]) + view["omitted_criterion_ids"], 100 * 100)
        self.assertGreater(view["omitted_criterion_ids"], 0)
        encoded = json.dumps(status_summary(report))
        self.assertNotIn("PRIVATE_STATEMENT_SENTINEL", encoded)
        self.assertNotIn("PRIVATE_REASON_SENTINEL", encoded)

    def test_missing_preparation_is_unknown_not_ready(self):
        report = report_fixture()
        del report["preparation"]
        self.assertIsNone(next_summary(report)["preparation_ready"])

    def test_many_instances_of_one_code_preserve_the_total(self):
        report = report_fixture()
        report["obligations"] = [{"code": "reading_missing"}] * 10000
        group = status_summary(report)["obligations"]
        self.assertEqual(group["total"], 10000)
        self.assertEqual(group["shown_obligations"], 10000)
        self.assertEqual(group["omitted_obligations"], 0)
        self.assertEqual(group["by_code"][0]["count"], 10000)

    def test_cohort_inventory_hint_does_not_need_a_code(self):
        report = report_fixture()
        report["next"] = {"version_id": "arxiv:2601.00001v1", "paths": ["source"]}
        hint = next_summary(report)["next_hint"]
        self.assertIsNone(hint["code"])
        self.assertEqual(hint["version_id"], "arxiv:2601.00001v1")
        self.assertTrue(hint["details_required"])
        self.assertNotIn("paths", hint)

    def test_the_grand_challenge_record_is_named_after_the_searches_and_before_the_objective(self):
        # The record needs a source read in full, so it never hides the roots, the readings or the searches. The
        # objective is written against the record and cannot be replaced once set, so the record comes first.
        ordered = order_obligations([{"code": "standards_missing"}, {"code": "objective_missing"},
                                     {"code": "grand_challenge_missing"}, {"code": "search_purpose_missing"},
                                     {"code": "fulltext_reading_missing"}, {"code": "roots_missing"}])
        self.assertEqual([item["code"] for item in ordered],
                         ["roots_missing", "fulltext_reading_missing", "search_purpose_missing", "grand_challenge_missing",
                          "objective_missing", "standards_missing"])

    def test_obligations_page_is_bound_to_the_revision(self):
        report = report_fixture()
        report["preparation"]["obligations"] = []
        report["obligations"] = [{"code": "abstract_reading_missing", "version_id": "arxiv:2601.%05dv1" % i} for i in range(30)]
        page = obligations_page(report, "abstract_reading_missing", limit=10, cursor=None)
        self.assertEqual((page["total"], page["offset"], page["returned"]), (30, 0, 10))
        self.assertEqual(page["obligations"][0]["version_id"], "arxiv:2601.00000v1")
        second = obligations_page(report, "abstract_reading_missing", limit=10, cursor=page["next_cursor"])
        self.assertEqual(second["offset"], 10)
        last = obligations_page(report, "abstract_reading_missing", limit=10, cursor=second["next_cursor"])
        self.assertIsNone(last["next_cursor"])
        with self.assertRaises(ResearchError) as raised:
            obligations_page(dict(report, revision=28), "abstract_reading_missing", limit=10, cursor=page["next_cursor"])
        self.assertEqual(raised.exception.code, "stale_cursor")
        with self.assertRaises(ResearchError):
            obligations_page(report, "abstract_reading_missing", limit=0, cursor=None)

    def test_obligations_page_lists_the_preparation_stage_while_it_has_obligations(self):
        report = report_fixture()
        report["preparation"]["obligations"] = [{"code": "cohort_abstract_reading_missing", "version_id": "arxiv:2601.%05dv1" % i} for i in range(3)]
        report["obligations"] = [{"code": "roots_missing"}]
        self.assertEqual(obligations_page(report, "cohort_abstract_reading_missing", limit=10, cursor=None)["total"], 3)
        self.assertEqual(obligations_page(report, "roots_missing", limit=10, cursor=None)["total"], 0)
        report["preparation"]["obligations"] = []
        self.assertEqual(obligations_page(report, "roots_missing", limit=10, cursor=None)["total"], 1)

    def test_next_prefers_earlier_preparation_stages(self):
        items = [{"code": "synthesis_dependencies_stale"}, {"code": "abstract_reading_missing", "version_id": "b"},
                 {"code": "abstract_reading_missing", "version_id": "a"}, {"code": "collection_pending"},
                 {"code": "objective_missing"}, {"code": "never_seen_code"}]
        ordered = order_obligations(items)
        self.assertEqual([o["code"] for o in ordered][:4],
                         ["collection_pending", "abstract_reading_missing", "abstract_reading_missing", "objective_missing"])
        self.assertEqual(ordered[1]["version_id"], "a")
        self.assertEqual(ordered[-1]["code"], "never_seen_code")


if __name__ == "__main__":
    unittest.main()
