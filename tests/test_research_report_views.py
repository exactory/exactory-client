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
            "runtime": {"plugin_version": "0.38.0"}, "counts": {"obligations": 20}}


class ReportViewTests(unittest.TestCase):
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
        report["runtime"] = {"plugin_version": "0.38.0", "source_commit": "a" * 40, "dirty": True,
                             "executable": "/x" * 100, "package_digest": "b" * 64, "schema_version": 1,
                             "constitution": {"version": "2", "sha256": "c" * 64}}
        report["evaluation"] = {"reads": 10 ** 9, "artifacts_verified": 10 ** 9, "bytes_verified": 10 ** 12, "computed": 10 ** 6,
                                "readings_assessed": 10 ** 6, "links_validated": 10 ** 6, "graph_builds": 3, "cohort_reports": 1,
                                "elapsed_seconds": 123456.789}
        status, upcoming = status_summary(report), next_summary(report)
        self.assertLessEqual(encoded_size(status), 16 * 1024)
        self.assertLessEqual(encoded_size(upcoming), 4 * 1024)
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

    def test_obligations_page_is_bound_to_the_revision(self):
        report = report_fixture()
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

    def test_next_prefers_earlier_preparation_stages(self):
        items = [{"code": "synthesis_dependencies_stale"}, {"code": "abstract_reading_missing", "version_id": "b"},
                 {"code": "abstract_reading_missing", "version_id": "a"}, {"code": "collection_pending"},
                 {"code": "objective_missing"}, {"code": "never_seen_code"}]
        ordered = order_obligations(items)
        self.assertEqual([o["code"] for o in ordered][:3], ["collection_pending", "objective_missing", "abstract_reading_missing"])
        self.assertEqual(ordered[2]["version_id"], "a")
        self.assertEqual(ordered[-1]["code"], "never_seen_code")


if __name__ == "__main__":
    unittest.main()
