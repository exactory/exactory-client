"""Sampled verification preparation: the draw, the record, and the prediction."""

import unittest

from literature_fixtures import FIELDS, LiteratureCase
from research_harness import sampling


def member(number, month):
    return {"work_id": "arxiv:2601.%05d" % number, "version_id": "arxiv:2601.%05dv1" % number, "month": month}


class DrawTests(LiteratureCase):
    def test_equal_allocation_by_month_and_determinism(self):
        members = [member(n, "2026-0" + str(1 + n % 3)) for n in range(1, 31)]
        first = sampling.draw(members, 9, "seed-a")
        again = sampling.draw(members, 9, "seed-a")
        other = sampling.draw(members, 9, "seed-b")
        self.assertEqual(first, again)
        self.assertNotEqual(first, other)
        self.assertEqual(len(first), 9)
        self.assertEqual({m["month"] for m in first}, {"2026-01", "2026-02", "2026-03"})
        self.assertEqual([sum(m["month"] == month for m in first) for month in ("2026-01", "2026-02", "2026-03")], [3, 3, 3])

    def test_a_short_month_gives_its_shortfall_to_the_others(self):
        members = [member(1, "2026-01")] + [member(n, "2026-02") for n in range(2, 12)]
        drawn = sampling.draw(members, 6, "seed")
        self.assertEqual(len(drawn), 6)
        self.assertEqual(sum(m["month"] == "2026-01" for m in drawn), 1)

    def test_a_population_within_the_size_is_taken_whole(self):
        members = [member(n, "2026-01") for n in range(1, 4)]
        self.assertEqual(len(sampling.draw(members, 100, "seed")), 3)


class SampleRecordTests(LiteratureCase):
    def setUp(self):
        super().setUp()
        from research_harness.principles import initialize_research
        self.mutate(initialize_research, {"profile": "verification", "target": self.verification_target(),
                                          "preparation_policy": "sampled-v1"})
        self.collection = self.cohort(tuple(range(1, 8)))

    def test_the_sample_is_recorded_with_its_seed_and_needs_the_policy(self):
        result = self.mutate(sampling.record_sample, {"id": "sample-1", "collection_id": self.collection, "size": 5, "seed": "abc"})["result"]
        self.assertEqual(result["size"], 5)
        self.assertEqual(len(result["members"]), 5)
        self.assertEqual(result["population"], 7)
        saved = sampling.current_sample(self.store.snapshot()["records"])
        self.assertEqual(saved["id"], "sample-1")
        self.assertEqual(saved["seed"], "abc")
        self.assert_error("invalid_input", lambda: self.mutate(sampling.record_sample,
            {"id": "sample-2", "collection_id": self.collection, "size": 101, "seed": "abc"}))
        self.assert_error("sample_exists", lambda: self.mutate(sampling.record_sample,
            {"id": "sample-3", "collection_id": self.collection, "size": 5, "seed": "xyz"}))

    def test_prediction_from_placements(self):
        from research_harness.reading import record_reading_batch
        self.mutate(sampling.record_sample, {"id": "sample-1", "collection_id": self.collection, "size": 3, "seed": "abc"})
        sample = sampling.current_sample(self.store.snapshot()["records"])
        versions = [m["version_id"] for m in sample["members"]]
        items = []
        for version, position in zip(versions, ("above", "below", "unplaced")):
            items.append({"version_id": version, "note": "Read the complete abstract.",
                          "notes": {f: {"text": "The abstract discusses " + f + ".", "status": "present"} for f in FIELDS},
                          "placement": {"position": position, "reason": "Authored placement."}})
        self.mutate(record_reading_batch, {"id": "batch-1", "depth": "abstract", "items": items})
        report = sampling.prediction(self.store.snapshot()["records"])
        self.assertEqual((report["n"], report["placed"], report["above"], report["below"], report["unplaced"]), (3, 2, 1, 1, 1))
        self.assertEqual(report["percentile"], 50)
        self.assertEqual(report["band"], {"best": 15, "worst": 85})
        self.assertFalse(report["widen_required"])


class PredictionTests(unittest.TestCase):
    """The prediction reads recorded placements; these author the records a batch reading leaves."""

    def make_sample_records(self, count):
        members = [{"version_id": "v%d" % index} for index in range(count)]
        return {"cohort_sample": {"sample-1": {"id": "sample-1", "members": members}},
                "cohort_sample_selection": {"current": {"id": "sample-1"}},
                "reading_batch": {}, "reading": {}}

    def place(self, records, version_id, position, *, reading_id, batch_id, revision):
        records["reading_batch"][batch_id] = {"id": batch_id, "revision": revision}
        records["reading"][reading_id] = {"id": reading_id, "version_id": version_id,
            "assessment": {"status": "complete"},
            "batch": {"batch_id": batch_id, "placement": {"position": position, "reason": "Authored placement."}}}

    def test_every_placed_member_below_predicts_the_top_of_the_range(self):
        records = self.make_sample_records(3)
        for index in range(3):
            self.place(records, "v%d" % index, "below", reading_id="reading-%d" % index, batch_id="batch-1", revision=1)
        report = sampling.prediction(records)
        self.assertEqual((report["size"], report["n"], report["placed"]), (3, 3, 3))
        self.assertEqual(report["percentile"], 100)
        self.assertEqual(report["band"], {"best": 100, "worst": 100})

    def test_every_placed_member_above_predicts_the_bottom_of_the_range(self):
        records = self.make_sample_records(2)
        for index in range(2):
            self.place(records, "v%d" % index, "above", reading_id="reading-%d" % index, batch_id="batch-1", revision=1)
        report = sampling.prediction(records)
        self.assertEqual(report["percentile"], 1)
        self.assertEqual(report["band"], {"best": 1, "worst": 1})

    def test_the_later_batch_overrides_an_earlier_placement_of_the_same_version(self):
        records = self.make_sample_records(1)
        self.place(records, "v0", "above", reading_id="reading-b", batch_id="batch-1", revision=1)
        self.place(records, "v0", "below", reading_id="reading-a", batch_id="batch-2", revision=2)
        report = sampling.prediction(records)
        self.assertEqual((report["above"], report["below"], report["percentile"]), (0, 1, 100))

    def test_a_sample_read_in_part_reports_its_size_beside_the_count_read(self):
        records = self.make_sample_records(5)
        self.place(records, "v0", "above", reading_id="reading-0", batch_id="batch-1", revision=1)
        self.place(records, "v1", "unplaced", reading_id="reading-1", batch_id="batch-1", revision=1)
        report = sampling.prediction(records)
        self.assertEqual((report["size"], report["n"], report["placed"], report["unplaced"]), (5, 2, 1, 1))


class CohortGateTests(LiteratureCase):
    def codes(self, collection):
        from research_harness.cohort_evidence import cohort_reading_report
        report = cohort_reading_report(self.store, [collection])
        return [o["code"] for o in report["obligations"]], [n["code"] for n in report.get("notices", [])]

    def test_lineage_reads_no_member_and_reports_pending_enumeration_as_a_notice(self):
        from research_harness.principles import initialize_research
        self.mutate(initialize_research, {"profile": "research", "target": None, "preparation_policy": "lineage-v1"})
        collection = self.cohort((1, 2, 3))
        obligations, notices = self.codes(collection)
        self.assertEqual(obligations, [])
        self.assertNotIn("cohort_abstract_reading_missing", obligations)

    def test_a_paused_enumeration_under_lineage_is_a_notice_not_an_obligation(self):
        from research_fixtures import client
        from research_harness.acquisition import collect_cohort
        from research_harness.principles import initialize_research
        self.mutate(initialize_research, {"profile": "research", "target": None, "preparation_policy": "lineage-v1"})
        http, _, _ = client([(429, {"Retry-After": "120"}, b"Limited")], max_retries=0)
        collection = self.mutate(collect_cohort, {"corpus": "arxiv", "primaryCategory": "cs.LG",
            "windowStart": "2026-01-01", "windowEnd": "2026-01-31"}, http=http, max_requests=1)["collection_id"]
        obligations, notices = self.codes(collection)
        self.assertEqual(obligations, [])
        self.assertIn("collection_pending", notices)

    def test_the_sample_reads_stale_once_the_population_grows_and_stays_current_when_a_work_repeats(self):
        from research_harness.cohort_evidence import cohort_reading_report
        from research_harness.principles import initialize_research
        self.mutate(initialize_research, {"profile": "verification", "target": self.verification_target(),
                                          "preparation_policy": "sampled-v1"})
        collection_id = self.cohort((1, 2, 3))
        self.mutate(sampling.record_sample, {"id": "s", "collection_id": collection_id, "size": 2, "seed": "x"})
        records = self.store.snapshot()["records"]
        collection = records["collection"][collection_id]
        report = cohort_reading_report(self.store, [collection_id])
        self.assertEqual(report["counts"]["sample"][collection_id], {"sampled": 2, "read": 0, "placed": 0})
        inventory = report["inventory"]
        repeated = inventory + [dict(inventory[0], version_id=inventory[0]["version_id"][:-1] + "2")]
        self.assertNotIn("sample_stale", [o["code"] for o in sampling.sample_obligations(records, collection, repeated)[0]])
        grown = inventory + [{"work_id": "arxiv:2601.00099", "version_id": "arxiv:2601.00099v1", "paths": [], "reading_id": None}]
        found, counts = sampling.sample_obligations(records, collection, grown)
        self.assertEqual([o["code"] for o in found], ["sample_stale"])
        self.assertEqual(counts, {"sampled": 0, "read": 0, "placed": 0})

    def test_sampled_needs_a_sample_and_every_sampled_abstract_with_a_placement(self):
        from research_harness.principles import initialize_research
        from research_harness.reading import record_reading_batch
        self.mutate(initialize_research, {"profile": "verification", "target": self.verification_target(),
                                          "preparation_policy": "sampled-v1"})
        collection = self.cohort((1, 2, 3))
        self.assertIn("sample_missing", self.codes(collection)[0])
        self.mutate(sampling.record_sample, {"id": "s", "collection_id": collection, "size": 2, "seed": "x"})
        obligations, _ = self.codes(collection)
        self.assertEqual(obligations.count("sample_reading_missing"), 2)
        members = sampling.current_sample(self.store.snapshot()["records"])["members"]
        plain = {"version_id": members[0]["version_id"], "note": "Read the complete abstract.",
                 "notes": {f: {"text": "The abstract discusses " + f + ".", "status": "present"} for f in FIELDS}}
        self.mutate(record_reading_batch, {"id": "b1", "depth": "abstract", "items": [plain]})
        obligations, _ = self.codes(collection)
        self.assertIn("placement_missing", obligations)
        placed = dict(plain, placement={"position": "above", "reason": "r"})
        self.mutate(record_reading_batch, {"id": "b2", "depth": "abstract", "items": [placed]})
        self.assertNotIn("placement_missing", self.codes(collection)[0])


class SampledExportTests(LiteratureCase):
    def test_default_export_under_sampled_lists_unread_sampled_members(self):
        from pathlib import Path
        import json
        from research_harness.batches import export_batches
        from research_harness.principles import initialize_research
        self.mutate(initialize_research, {"profile": "verification", "target": self.verification_target(),
                                          "preparation_policy": "sampled-v1"})
        collection = self.cohort((1, 2, 3, 4))
        self.mutate(sampling.record_sample, {"id": "s", "collection_id": collection, "size": 2, "seed": "x"})
        result = export_batches(self.store, destination=str(Path(self.temporary.name) / "sample"))
        listed = {item["version_id"] for item in json.loads(Path(result["files"][0]).read_text())["items"]}
        self.assertEqual(listed, {m["version_id"] for m in sampling.current_sample(self.store.snapshot()["records"])["members"]})
        template = json.loads((Path(result["files"][0]).parent / "README.json").read_text())["notes_shape"]["items"][0]
        self.assertEqual(template["placement"], {"position": "|".join(sampling.POSITIONS), "reason": "Why."})
