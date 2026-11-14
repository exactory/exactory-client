"""Stage the v2 minimum while exported packets retain their released contract."""

import copy
import hashlib
import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from research_harness import openness_minimum as minimums, problem_openness
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from test_research_problem_claims import ProblemCase
import test_research_problem_openness_minimum as fixtures


def build_v2_minimum():
    return {"schema_version": 2, "statement": "Bound every finite sequence.", "neighbours": [],
            "searches": [{"formulation": "Bound every finite sequence.", "search_digest": "a" * 64}],
            "reading_digests": ["b" * 64], "settling_work_id": None, "duplicate_resolutions": [],
            "family_readings": [{"search_digest": "a" * 64, "work_id": "arxiv:2601.00001",
                                 "version_id": "arxiv:2601.00001v1"}],
            "supporting_reading_digests": ["c" * 64],
            "results": [{"native_attempt_digest": "d" * 64, "acceptance_id": "acceptance-1",
                         "cited_as": "Lemma A", "link_digest": "e" * 64, "result_digest": "f" * 64}]}


class MinimumV2SchemaTests(unittest.TestCase):
    def test_v2_round_trips_all_three_binding_kinds_without_changing_bytes_or_digest(self):
        minimum = build_v2_minimum()
        original = problem_openness.serialize_record(minimum)
        restored = json.loads(original)
        minimums.validate_minimum(restored)
        self.assertEqual(restored, minimum)
        self.assertEqual(problem_openness.serialize_record(restored), original)
        self.assertEqual(digest(restored), digest(minimum))

    def test_v1_validation_and_refusals_match_the_frozen_validator_without_adding_fields(self):
        legacy = {"__name__": "research_harness.legacy_minimum", "__package__": "research_harness"}
        path = Path(__file__).with_name("fixtures") / "openness_minimum_0_85_3.py.txt"
        exec(compile(path.read_text(), str(path), "exec"), legacy)
        minimum = build_v2_minimum()
        for field in ("family_readings", "supporting_reading_digests", "results"):
            minimum.pop(field)
        minimum["schema_version"] = 1
        cases = [minimum, {key: value for key, value in minimum.items() if key != "duplicate_resolutions"}]
        for field, value in (("schema_version", True), ("reading_digests", ["invalid"]),
                             ("family_readings", []), ("supporting_reading_digests", []), ("results", []),
                             ("neighbours", [minimum["statement"]]), ("unexpected", "field")):
            cases.append(dict(minimum, **{field: value}))
        for case in cases:
            with self.subTest(case=case):
                original = problem_openness.serialize_record(case)
                results = []
                for validate in (legacy["validate_minimum"], minimums.validate_minimum):
                    try:
                        validate(case)
                        results.append(None)
                    except ResearchError as error:
                        results.append(error.as_dict())
                self.assertEqual(results[0], results[1])
                self.assertEqual(problem_openness.serialize_record(case), original)
        minimums.validate_minimum(build_v2_minimum())

    def test_v2_requires_all_lists_and_closed_top_level_fields(self):
        minimums.validate_minimum(build_v2_minimum())
        for field in build_v2_minimum():
            minimum = build_v2_minimum()
            minimum.pop(field)
            with self.subTest(missing=field), self.assertRaises(ResearchError) as caught:
                minimums.validate_minimum(minimum)
            self.assertEqual(caught.exception.code, "invalid_record")
        for value in (None, [], dict(build_v2_minimum(), extra=[])):
            with self.subTest(value=value), self.assertRaises(ResearchError) as caught:
                minimums.validate_minimum(value)
            self.assertEqual(caught.exception.code, "invalid_record")

    def test_v2_rejects_invalid_versions_and_list_types(self):
        minimums.validate_minimum(build_v2_minimum())
        for version in (True, False, 0, 1, 3, "2", 2.0, None):
            with self.subTest(version=version), self.assertRaises(ResearchError) as caught:
                minimums.validate_minimum(dict(build_v2_minimum(), schema_version=version))
            self.assertEqual(caught.exception.code, "invalid_record")
        for field in ("family_readings", "supporting_reading_digests", "results", "duplicate_resolutions"):
            for invalid in (None, {}, "not a list", True):
                with self.subTest(field=field, invalid=invalid), self.assertRaises(ResearchError) as caught:
                    minimums.validate_minimum(dict(build_v2_minimum(), **{field: invalid}))
                self.assertEqual(caught.exception.code, "invalid_record")

    def test_v2_binding_rows_require_exact_fields_and_typed_digests_and_text(self):
        minimums.validate_minimum(build_v2_minimum())
        for field in ("family_readings", "results"):
            row = build_v2_minimum()[field][0]
            cases = [None, [], dict(row, extra="field")]
            for key in row:
                cases.append({name: value for name, value in row.items() if name != key})
                for invalid in (None, "", " ", [], 3, True):
                    cases.append(dict(row, **{key: invalid}))
                if key.endswith("digest"):
                    for invalid in ("a" * 63, "A" * 64, "g" * 64):
                        cases.append(dict(row, **{key: invalid}))
            for invalid in cases:
                with self.subTest(field=field, row=invalid), self.assertRaises(ResearchError) as caught:
                    minimums.validate_minimum(dict(build_v2_minimum(), **{field: [invalid]}))
                self.assertEqual(caught.exception.code, "invalid_record")
        for invalid in (["g" * 64], ["c" * 63], ["C" * 64], [None], [3], [""]):
            with self.subTest(readings=invalid), self.assertRaises(ResearchError) as caught:
                minimums.validate_minimum(dict(build_v2_minimum(), supporting_reading_digests=invalid))
            self.assertEqual(caught.exception.code, "invalid_record")

    def test_v2_refuses_duplicate_binding_identities_but_keeps_distinct_bindings(self):
        minimums.validate_minimum(build_v2_minimum())
        for field, changes in (("family_readings", ({}, {"version_id": "arxiv:2601.00001v2"})),
                               ("results", ({}, {"cited_as": "Another name", "result_digest": "0" * 64})),
                               ("supporting_reading_digests", ({},))):
            for change in changes:
                minimum = build_v2_minimum()
                first = minimum[field][0]
                minimum[field].append(dict(first, **change) if isinstance(first, dict) else first)
                with self.subTest(field=field, change=change), self.assertRaises(ResearchError) as caught:
                    minimums.validate_minimum(minimum)
                self.assertEqual(caught.exception.code, "invalid_record")
        minimum = build_v2_minimum()
        minimum["family_readings"].append(dict(minimum["family_readings"][0], search_digest="0" * 64))
        minimum["results"].append(dict(minimum["results"][0], acceptance_id="acceptance-2"))
        minimum["supporting_reading_digests"].append("0" * 64)
        minimums.validate_minimum(minimum)


class MinimumV2StoreTests(ProblemCase):
    search = fixtures.ProblemOpennessMinimumTests.search
    read = fixtures.ProblemOpennessMinimumTests.read
    build_finding = fixtures.ProblemOpennessMinimumTests.build_finding

    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.selected = self.select_statement()

    def build_spec(self, searches):
        return {"assignment_id": "minimum-review", "reviewer_id": "openness-reviewer", "minimum": {
            "searches": [{"formulation": row["question"], "search_id": row["id"]} for row in searches],
            "settling_work_id": None, "duplicate_resolutions": []}}

    def test_current_exports_activate_v2_and_leave_the_native_packet_immutable(self):
        work = self.metadata()
        reading = self.read(work)
        search = self.search([work])
        spec = self.build_spec([search])
        result = problem_openness.export_openness(self.store, spec, self.root / "packet")
        packet = json.loads(self.artifacts.read(result["native_packet"]))
        original = self.artifacts.read(result["native_packet"])
        self.assertEqual(packet["scientific_context"]["openness_minimum"]["schema_version"], 2)
        bound = [(self.selected["statement"], search)]
        v2 = problem_openness.build_minimum(self.selected, bound, {reading["id"]: reading}, spec["minimum"],
            records=self.store.snapshot()["records"], artifacts=self.artifacts)
        expected = {"schema_version": 2, "statement": self.selected["statement"], "neighbours": [],
                    "searches": [{"formulation": self.selected["statement"], "search_digest": digest(search)}],
                    "reading_digests": [digest(reading)], "settling_work_id": None, "duplicate_resolutions": [],
                    "family_readings": [], "supporting_reading_digests": [], "results": []}
        self.assertEqual(v2, expected)
        minimums.validate_minimum(json.loads(problem_openness.serialize_record(v2)))
        minimums.validate_minimum_bindings(v2, self.selected["statement"], self.store.snapshot()["records"])
        self.assertEqual(self.artifacts.read(result["native_packet"]), original)
        for field in ("family_readings", "supporting_reading_ids", "results"):
            spec["minimum"][field] = []
        again = problem_openness.export_openness(self.store, spec, self.root / "packet")
        self.assertEqual(again["native_packet"], result["native_packet"])
        self.assertEqual(self.artifacts.read(again["native_packet"]), original)

    def test_invalid_or_unshipped_v2_spec_bindings_are_refused_before_writes(self):
        search = self.search()
        # UB6 delivers v2 bindings. Invalid or unshipped evidence must refuse before writing.
        spec = self.build_spec([search])
        for field in ("family_readings", "supporting_reading_ids", "results"):
            spec["minimum"][field] = []
        problem_openness.export_openness(self.store, spec, self.root / "packet")
        for field, row in (("family_readings", build_v2_minimum()["family_readings"][0]),):
            for value in ([row], None, {}, "invalid", False):
                candidate = copy.deepcopy(spec)
                candidate["minimum"][field] = value
                before = sorted(self.root.rglob("*"))
                with self.subTest(field=field, value=value), self.assertRaises(ResearchError) as caught:
                    problem_openness.export_openness(self.store, candidate, self.root / "refused")
                expected = "openness_reading_gap" if field == "family_readings" and value == [row] else "invalid_input"
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual(sorted(self.root.rglob("*")), before)

    def test_v2_search_date_uses_latest_instant_in_utc_while_v1_keeps_local_calendar_dates(self):
        earlier = self.search(captured_at="2026-10-16T04:00:00+02:00")
        latest = self.search(captured_at="2026-10-15T23:30:00-04:00", query="another query")
        records = self.store.snapshot()["records"]
        finding = self.build_finding([earlier, latest])
        self.assertEqual(minimums.find_searched_through(finding["minimum"], records), "2026-10-15")
        v2 = dict(finding["minimum"], schema_version=2, duplicate_resolutions=[],
                  family_readings=[], supporting_reading_digests=[], results=[])
        self.assertEqual(minimums.find_searched_through(v2, records), "2026-10-16")
        finding.update(minimum=v2, searched_at="2026-10-16")
        minimums.audit_minimum(finding, self.selected["statement"], records, self.artifacts, set(finding["evidence"]))
        finding["searched_at"] = "2026-10-15"
        self.assert_error("openness_search_date", lambda: minimums.audit_minimum(
            finding, self.selected["statement"], records, self.artifacts, set(finding["evidence"])))

    def test_v2_audit_accepts_utc_date_before_capture_local_date_and_preserves_v1_refusal(self):
        search = self.search(captured_at="2026-10-17T00:30:00+02:00")
        records = self.store.snapshot()["records"]
        finding = self.build_finding([search])
        finding["searched_at"] = "2026-10-16"
        self.assert_error("openness_search_date", lambda: minimums.audit_minimum(
            finding, self.selected["statement"], records, self.artifacts, set(finding["evidence"])))
        finding["minimum"].update(schema_version=2, duplicate_resolutions=[], family_readings=[],
                                  supporting_reading_digests=[], results=[])
        minimums.audit_minimum(finding, self.selected["statement"], records, self.artifacts, set(finding["evidence"]))
        self.assertEqual(minimums.find_searched_through(finding["minimum"], records), "2026-10-16")

    def test_controller_wrapper_and_stored_finding_readers_keep_v1_results_with_empty_v2_lists(self):
        harness = Path(__file__).resolve().parents[1] / "skills" / "math-solver" / "harness"
        with patch.object(sys, "path", [str(harness)] + sys.path):
            wrapper = importlib.import_module("search_controller.openness")
        search = self.search()
        finding = self.build_finding([search])
        finding.update(id="stored-finding", statement_digest=self.load_problem_module().compute_statement_digest(self.selected),
                       recorded_revision=self.store.revision + 1)
        records = self.store.snapshot()["records"]
        records["problem_openness"] = {finding["id"]: finding}
        original = problem_openness.serialize_record(finding)
        outcomes = []
        for version in (1, 2):
            if version == 2:
                finding["minimum"].update(schema_version=2, duplicate_resolutions=[], family_readings=[],
                                          supporting_reading_digests=[], results=[])
            wrapper.validate_minimum(finding["minimum"])
            with self.assertRaises(wrapper.SearchError) as caught:
                wrapper.validate_minimum(dict(finding["minimum"], reading_digests=["invalid"]))
            self.assertEqual(caught.exception.code, "invalid_record")
            wrapper.validate_minimum_bindings(finding["minimum"], self.selected, records)
            wrapper.audit_minimum(finding, self.selected, records, self.artifacts, set(finding["evidence"]))
            self.assertEqual(problem_openness.assess_openness(records, self.artifacts), [])
            finding["outcome"] = "unresolved"
            outcomes.append(problem_openness.assess_openness(records, self.artifacts))
            finding["outcome"] = "supported_open"
            if version == 1:
                self.assertEqual(problem_openness.serialize_record(finding), original)
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(outcomes[0][0]["code"], "problem_openness_unresolved")

    def test_math_solver_exports_v1_and_refuses_v2_before_writing(self):
        harness = Path(__file__).resolve().parents[1] / "skills" / "math-solver" / "harness"
        with patch.object(sys, "path", [str(harness), str(harness / "tests")] + sys.path):
            native_fixtures = importlib.import_module("search_fixtures")
            service = importlib.import_module("search_controller.service")
            errors = importlib.import_module("search_controller.errors")
        contract = native_fixtures.contract()
        contract["original_claim"]["statement"] = self.selected["statement"]
        controller = service.Controller(self.root)
        controller.command("init", {"contract": contract}, 0, "initialize-native")
        search = self.search()
        spec = {"kind": "result", "subject": {"claim": contract["original_claim"], "evidence_digests": []},
                "claim_digest": digest(contract["original_claim"]), "inputs": [],
                "reviewer": {"source": "host", "actor_id": "native-reviewer",
                             "attestation_id": "review-assignment:native-openness"},
                "openness": self.build_finding([search])["minimum"]}
        result = controller.command("review-packet", spec, None, None)
        packet = json.loads(self.artifacts.read(result["native_packet"]))
        self.assertEqual(packet["scientific_context"]["openness_minimum"], spec["openness"])
        for bindings in ({"family_readings": [], "supporting_reading_digests": [], "results": []},
                         {key: build_v2_minimum()[key] for key in ("family_readings", "supporting_reading_digests", "results")}):
            candidate = dict(spec, openness=dict(spec["openness"], schema_version=2, duplicate_resolutions=[], **bindings))
            source = self.root / "v2-only-evidence.txt"
            source.write_text("New evidence that a refused packet must not import.\n")
            candidate["inputs"] = [{"path": source.name, "kind": "artifact",
                                    "digest": hashlib.sha256(source.read_bytes()).hexdigest()}]
            minimums.validate_minimum(candidate["openness"])
            before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
            with self.subTest(bindings=bindings):
                with self.assertRaises(errors.SearchError) as caught:
                    controller.command("review-packet", candidate, None, None)
                self.assertEqual(caught.exception.code, "invalid_record")
                self.assertEqual({path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()},
                                 before)


if __name__ == "__main__":
    unittest.main()
