"""Forecast the stored minimum before paying for an openness reading chain."""

import copy
import json
from pathlib import Path
from unittest.mock import patch

from research_harness import openness_minimum as minimums
from research_harness import problem_openness
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from test_research_problem_claims import ProblemCase
import test_research_problem_openness_minimum as minimum_fixtures
from test_search_receipts import SearchCaptureCase


class MinimumAssessmentTests(ProblemCase):
    search = minimum_fixtures.ProblemOpennessMinimumTests.search
    read = minimum_fixtures.ProblemOpennessMinimumTests.read
    build_finding = minimum_fixtures.ProblemOpennessMinimumTests.build_finding
    audit = minimum_fixtures.ProblemOpennessMinimumTests.audit
    capture_page = SearchCaptureCase.capture_page
    build_search_payload = SearchCaptureCase.build_search_payload

    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.selected = self.select_statement()

    def build_cases(self):
        work, other = self.metadata(), self.metadata(2)
        reading = self.read(work)
        clean = self.search([work, other], dispositions={other: "out_of_scope"})
        records = self.store.snapshot()["records"]
        cases = []

        def add_case(condition, change, *, settling=None):
            snapshot = copy.deepcopy(records)
            search = snapshot["literature_search"][clean["id"]]
            finding = self.build_finding([search], [reading], settling=settling)
            change(search, finding["minimum"], snapshot)
            finding["minimum"]["searches"][0]["search_digest"] = digest(search)
            finding["evidence"] = [digest(search), digest(reading)]
            cases.append((condition, finding, snapshot))

        add_case("settling_undeclared", lambda s, m, r: None)
        add_case("search_integrity", lambda s, m, r: s.update(integrity_errors=[{
            "code": "invalid_work", "query": s["queries"][0], "source_id": s["responses"][0]["source_id"],
            "index": 1, "record_type": "paratext"}]))
        add_case("search_declared_gap", lambda s, m, r: s.update(gaps=["Some returned sources remain unassessed."]))
        add_case("search_coverage_unspecified", lambda s, m, r: s.update(coverage=[]))
        add_case("search_coverage_unspecified", lambda s, m, r: s["coverage"][0].pop("inspected_count"))
        add_case("search_coverage_unspecified", lambda s, m, r: s["coverage"][0].update(kind="unspecified"))
        add_case("result_uncertain", lambda s, m, r: s["dispositions"][1].update(disposition="potentially_relevant"))
        add_case("duplicate_unbound", lambda s, m, r: s["dispositions"][1].update(disposition="duplicate"))
        add_case("duplicate_unbound", lambda s, m, r: m.update(duplicate_resolutions=[{
            "search_digest": digest(s), "work_id": other, "canonical_work_id": work}]))

        def bind_bad_duplicate(search, minimum, snapshot):
            search["dispositions"][0]["disposition"] = "out_of_scope"
            search["dispositions"][1]["disposition"] = "duplicate"
            minimum["duplicate_resolutions"] = [{"search_digest": digest(search),
                "work_id": other, "canonical_work_id": work}]

        add_case("duplicate_source_not_decisive", bind_bad_duplicate)
        add_case("reading_missing", lambda s, m, r: m.update(reading_digests=[]))
        add_case("settling_declared", lambda s, m, r: None, settling=work)
        add_case("settling_not_decisive", lambda s, m, r: None, settling=other)
        add_case("search_declared_gap", lambda s, m, r: s.update(gaps=["A gap beside a settling source."]), settling=work)
        return cases

    def test_g1_forecast_and_audit_share_every_record_condition_and_legacy_order(self):
        legacy = {"__name__": "research_harness.legacy_minimum", "__package__": "research_harness"}
        source = Path(__file__).with_name("fixtures") / "openness_minimum_0_85_3.py.txt"
        exec(compile(source.read_text(), str(source), "exec"), legacy)
        for condition, finding, records in self.build_cases():
            with self.subTest(condition=condition, settling=finding["minimum"]["settling_work_id"]):
                gaps = minimums.list_minimum_gaps(finding["minimum"], self.selected["statement"], records, self.artifacts)
                self.assertIn(condition, [gap["condition"] for gap in gaps])
                forecast = minimums.forecast_outcomes(gaps)
                for outcome in ("supported_open", "already_solved"):
                    finding["outcome"] = outcome
                    blocking = [gap for gap in gaps if outcome in gap["outcomes"]]
                    self.assertEqual(forecast[outcome], "blocked" if blocking else "possible")
                    results = []
                    for audit in (legacy["audit_minimum"], minimums.audit_minimum):
                        try:
                            audit(finding, self.selected["statement"], records, self.artifacts, set(finding["evidence"]))
                            results.append(None)
                        except ResearchError as error:
                            results.append(error.as_dict())
                    self.assertEqual(results[0], results[1])
                    self.assertEqual(None if not blocking else blocking[0]["code"],
                                     None if results[1] is None else results[1]["code"])

    def test_g1_audit_has_no_record_condition_outside_the_gap_list(self):
        for condition, finding, records in self.build_cases():
            with self.subTest(condition=condition):
                with patch.object(minimums, "list_minimum_gaps", return_value=[]):
                    for outcome in ("supported_open", "already_solved"):
                        finding["outcome"] = outcome
                        minimums.audit_minimum(finding, self.selected["statement"], records,
                                               self.artifacts, set(finding["evidence"]))

    def test_legacy_search_gap_precedes_an_unreadable_versionless_decisive_work(self):
        versionless = "arxiv:2601.00001"
        search = self.search([versionless], gaps=["The search is incomplete."])
        records = self.store.snapshot()["records"]
        search = records["literature_search"][search["id"]]
        finding = self.build_finding([search])
        self.assert_error("openness_search_gap", lambda: self.audit(finding, records=records))
        search["gaps"] = []
        finding = self.build_finding([search])
        self.assert_error("openness_evidence_mismatch", lambda: self.audit(finding, records=records, delivered=set()))
        finding["searched_at"] = "2026-10-14"
        self.assert_error("openness_search_date", lambda: self.audit(finding, records=records))
        finding["searched_at"] = "2026-10-17"
        self.assert_error("missing_version", lambda: self.audit(finding, records=records))
        gaps = minimums.list_minimum_gaps(finding["minimum"], self.selected["statement"], records, self.artifacts)
        self.assertEqual((gaps[0]["condition"], gaps[0]["code"]), ("reading_missing", "missing_version"))

    def test_legacy_date_check_precedes_a_gap_of_another_binding_of_the_same_search(self):
        neighbour = "An equivalent form of the same problem."
        old = self.search(gaps=["Coverage needs reassessment."])
        new = self.search(captured_at="2026-10-16T12:00:00Z")
        records = self.store.snapshot()["records"]
        old, new = (records["literature_search"][row["id"]] for row in (old, new))
        for search in (old, new):
            search["queries"] = [self.selected["statement"], neighbour]
            search["coverage"] = [dict(search["coverage"][0], query=query) for query in search["queries"]]
        finding = self.build_finding([old, new], neighbours=[neighbour])
        finding["minimum"]["searches"].insert(1, {"formulation": neighbour, "search_digest": digest(old)})
        finding["searched_at"] = "2026-10-14"
        self.assert_error("openness_search_date", lambda: self.audit(finding, records=records))
        gaps = minimums.list_minimum_gaps(finding["minimum"], self.selected["statement"], records, self.artifacts)
        self.assertEqual([gap["formulation"] for gap in gaps if gap["condition"] == "search_declared_gap"], [neighbour])

    def test_assessment_errors_keep_their_position_among_reading_checks(self):
        from research_harness.reading import record_availability, require_fulltext
        from research_harness.source_deferrals import defer_source
        work, other = self.metadata(), self.metadata(2)
        search = self.search([work, other])
        records = self.store.snapshot()["records"]
        versionless = other.rsplit("v", 1)[0]
        records["work"][versionless] = dict(records["work"][other], id=versionless)
        search = records["literature_search"][search["id"]]
        search["dispositions"][1]["work_id"] = versionless
        finding = self.build_finding([search])
        gaps = minimums.list_minimum_gaps(finding["minimum"], self.selected["statement"], records, self.artifacts)
        self.assertEqual([gap["code"] for gap in gaps],
                         ["openness_reading_gap", "missing_version", "openness_settling_source"])
        self.assert_error("openness_reading_gap", lambda: self.audit(finding, records=records))
        capture = self.capture(work, status=404)
        self.mutate(record_availability, {"id": "unavailable", "profile": "research", "version_id": work,
            "depth": "fulltext", "source_ids": [capture["source_id"]], "reason": "Original returned 404.",
            "policy": {"id": "origin-404", "minimum_attempts": 1, "allowed_statuses": [404],
                       "rationale": "The original is unavailable."}})
        self.mutate(require_fulltext, {"id": "required", "profile": "research", "version_id": work,
            "purpose": "validity", "reason": "Read the decisive source."})
        authorization = self.artifacts.put(b"Proceed with this recorded source gap.", "text/plain")
        self.mutate(defer_source, {"id": "deferred", "profile": "research", "version_id": work,
            "reason": "The original cannot be read.", "authorization": authorization,
            "acquisition_evidence": [self.artifacts.put(b"404 acquisition log.", "text/plain")],
            "dependent_claims": ["The source could settle the claim."], "continuing_work": ["Read other sources."]})
        (self.root / authorization["path"]).unlink()
        records = self.store.snapshot()["records"]
        finding = self.build_finding([records["literature_search"][search["id"]]])
        gaps = minimums.list_minimum_gaps(finding["minimum"], self.selected["statement"], records, self.artifacts)
        self.assertEqual(gaps[0]["details"]["assessment_error"]["code"], "artifact_missing")
        self.assert_error("artifact_missing", lambda: self.audit(finding, records=records))
        search = records["literature_search"][search["id"]]
        for row in search["dispositions"]:
            row["disposition"] = "out_of_scope"
        finding = self.build_finding([search])
        self.assert_error("artifact_missing", lambda: self.audit(finding, records=records))

    def test_gap_list_retains_fixed_order_and_all_work_query_and_repair_details(self):
        work = self.metadata()
        search = self.search([work], dispositions={work: "unresolved"}, gaps=["Read the remaining results."])
        finding = self.build_finding([search])
        gaps = minimums.list_minimum_gaps(finding["minimum"], self.selected["statement"],
                                          self.store.snapshot()["records"], self.artifacts)
        self.assertEqual([gap["condition"] for gap in gaps],
                         ["search_declared_gap", "result_uncertain", "settling_undeclared"])
        for gap in gaps[:2]:
            self.assertEqual((gap["formulation"], gap["search_id"], gap["search_digest"]),
                             (self.selected["statement"], search["id"], digest(search)))
            self.assertEqual(gap["details"]["command"], "search")
        self.assertEqual(gaps[1]["work_id"], work)
        self.assertEqual(gaps[0]["details"]["gaps"], ["Read the remaining results."])

    def test_reassessment_removes_gaps_but_keeps_incomplete_enumeration_notices(self):
        capture = self.capture_page({"meta": {"count": 4}, "results": []})
        payload = self.build_search_payload([capture])
        payload.update(question=self.selected["statement"], gaps=["A bounded capture needs reassessment."])
        from research_harness.literature import record_search
        self.mutate(record_search, payload)
        old = self.store.snapshot()["records"]["literature_search"][payload["id"]]
        new = self.search(query="bounded", captured_at="2026-10-16T12:00:00Z")
        finding = self.build_finding([old, new])
        records = self.store.snapshot()["records"]
        gaps = minimums.list_minimum_gaps(finding["minimum"], self.selected["statement"], records, self.artifacts)
        self.assertEqual([gap["condition"] for gap in gaps], ["settling_undeclared"])
        self.audit(finding)
        notices = minimums.list_minimum_notices(finding["minimum"], records, self.artifacts)
        self.assertEqual(notices[0]["incomplete_responses"][0]["reported_count"], 4)

    def test_w7_notices_report_excluded_rows_and_distant_page_capture_times(self):
        captures = [self.capture_page({"meta": {"count": 3}, "results": [
            {"id": "https://openalex.org/W123", "title": None, "type": "paratext"},
            {"id": "https://openalex.org/W124", "title": "An authored result", "type": "article"}]}),
            self.capture_page({"meta": {"count": 3}, "results": [
                {"id": "https://openalex.org/W125", "title": "Another result", "type": "article"}]},
                page=2, captured_at="2026-09-08T12:00:00Z")]
        payload = self.build_search_payload(captures)
        payload["question"] = self.selected["statement"]
        from research_harness.literature import record_search
        receipt = self.mutate(record_search, payload)["result"]
        search = self.store.snapshot()["records"]["literature_search"][payload["id"]]
        result = problem_openness.export_openness(self.store, self.build_spec([search]), self.root / "packet")
        notice = result["audit_forecast"]["notices"][0]
        self.assertEqual(notice["excluded_item_count"], 1)
        self.assertEqual(notice["excluded_items"], receipt["excluded_items"])
        self.assertEqual(notice["warnings"], receipt["warnings"])
        self.assertEqual(notice["warnings"][0]["query"], "bounded")
        self.assertEqual(result["audit_forecast"]["searched_through"], "2026-09-09")

    def test_decisive_settling_work_allows_a_resolved_export(self):
        work = self.metadata()
        self.read(work)
        search = self.search([work])
        result = problem_openness.export_openness(self.store, self.build_spec([search], settling=work))
        self.assertEqual(result["audit_forecast"]["outcomes"],
                         {"supported_open": "blocked", "already_solved": "possible"})
        self.assertNotIn("warnings", result)

    def test_export_forecasts_unreadable_decisive_work_and_an_unknown_settling_work(self):
        search = self.search(["arxiv:2601.00001"])
        for settling, expected in ((None, "reading_missing"), ("arxiv:2601.99999v1", "settling_not_decisive")):
            spec = self.build_spec([search], settling=settling)
            with self.assertRaises(ResearchError) as caught:
                problem_openness.export_openness(self.store, spec)
            self.assertEqual(caught.exception.code, "openness_minimum_gaps")
            self.assertIn(expected, [gap["condition"] for gap in caught.exception.details["gaps"]])
            accepted = problem_openness.export_openness(self.store, spec, accept_gaps=True)
            self.assertEqual(accepted["warnings"][0]["details"], caught.exception.details)
        self.assert_error("invalid_identifier", lambda: problem_openness.export_openness(
            self.store, self.build_spec([search], settling="an-invalid-identifier"), accept_gaps=True))

    def test_deferred_source_is_a_named_gap_until_resumed_even_with_a_full_reading(self):
        from research_harness.reading import record_availability, require_fulltext
        from research_harness.source_deferrals import defer_source
        work = self.metadata()
        search = self.search([work])
        capture = self.capture(work, status=404)
        self.mutate(record_availability, {"id": "missing", "profile": "research", "version_id": work,
            "depth": "fulltext", "source_ids": [capture["source_id"]], "reason": "The original returned 404.",
            "policy": {"id": "origin-404", "minimum_attempts": 1, "allowed_statuses": [404],
                       "rationale": "Unavailability leaves a reading gap."}})
        self.mutate(require_fulltext, {"id": "decisive", "profile": "research", "version_id": work,
            "purpose": "validity", "reason": "The source is decisive."})
        self.mutate(defer_source, {"id": "deferred", "profile": "research", "version_id": work,
            "reason": "The original is unavailable.", "authorization": self.artifacts.put(b"Continue with a gap.", "text/plain"),
            "acquisition_evidence": [self.artifacts.put(b"404 retrieval log.", "text/plain")],
            "dependent_claims": ["The result depends on this source."], "continuing_work": ["Read other sources."]})
        for status in ("active", "stale"):
            readings = [] if status == "active" else [self.read(work)]
            finding = self.build_finding([search], readings)
            records = self.store.snapshot()["records"]
            gaps = minimums.list_minimum_gaps(finding["minimum"], self.selected["statement"], records, self.artifacts)
            self.assertEqual(gaps[0]["condition"], "reading_deferred")
            self.assertEqual((gaps[0]["work_id"], gaps[0]["details"]["status"]), (work, status))
            self.assertEqual([gap["condition"] for gap in gaps],
                             ["reading_deferred", "reading_missing", "settling_undeclared"]
                             if status == "active" else ["reading_deferred", "settling_undeclared"])
            self.assert_error("openness_reading_gap", lambda: self.audit(finding))
            with patch.object(minimums, "list_minimum_gaps", return_value=[]):
                self.audit(finding)

    def build_spec(self, searches, *, settling=None):
        return {"assignment_id": "minimum-review", "reviewer_id": "openness-reviewer", "minimum": {
            "searches": [{"formulation": row["question"], "search_id": row["id"]} for row in searches],
            "settling_work_id": settling, "duplicate_resolutions": []}}

    def test_g2_incident_shape_refuses_without_writes_and_accepts_with_identical_warning(self):
        # Sources: 20261112135015-openness-reading-turn-stopped-by-provider-safeguards T5
        # and 20261112143000-openness-chain-ends-unresolved-on-search-integrity-gaps T1.
        work = self.metadata()
        first = self.capture_page({"meta": {"count": 4}, "results": [
            {"id": "https://openalex.org/W123", "title": None, "type": "article"}]}, query="bounded")
        payload = self.build_search_payload([first])
        payload.update(question=self.selected["statement"], gaps=["Some returned sources remain unread."])
        from research_harness.literature import record_search
        self.mutate(record_search, payload)
        uncertain = self.search([work], dispositions={work: "potentially_relevant"}, query="other query")
        records = self.store.snapshot()["records"]
        spec = self.build_spec([records["literature_search"][payload["id"]], uncertain])
        destination = self.root / "packet"
        before = sorted(self.root.rglob("*"))
        with self.assertRaises(ResearchError) as caught:
            problem_openness.export_openness(self.store, spec, destination)
        self.assertEqual(caught.exception.code, "openness_minimum_gaps")
        self.assertEqual(sorted(self.root.rglob("*")), before)
        forecast = caught.exception.details
        self.assertEqual(forecast["outcomes"], {"supported_open": "blocked", "already_solved": "blocked"})
        self.assertEqual([gap["condition"] for gap in forecast["gaps"]],
                         ["search_integrity", "search_declared_gap", "result_uncertain", "settling_undeclared"])
        invalid = forecast["gaps"][0]["details"]["integrity_errors"][0]
        self.assertEqual((invalid["code"], invalid["query"], invalid["count"]), ("invalid_work", "bounded", 1))
        result = problem_openness.export_openness(self.store, spec, destination, accept_gaps=True)
        self.assertEqual(result["audit_forecast"], forecast)
        self.assertEqual(result["warnings"][0]["details"], forecast)
        self.assertIn("v5", result["warnings"][0]["message"])
        packet = json.loads(self.artifacts.read(result["native_packet"]))
        self.assertNotIn("accept_gaps", json.dumps(packet))
        self.assertEqual(set(packet["scientific_context"]), {"openness_minimum"})
        self.assertTrue((destination / "inputs.json").is_file())

    def test_g2_bounded_enumeration_is_a_notice_and_keeps_legacy_packet_bytes(self):
        first = self.capture_page({"meta": {"count": 4}, "results": [
            {"id": "https://openalex.org/W123", "title": "Authored source", "type": "article"}]})
        payload = self.build_search_payload([first])
        payload["question"] = self.selected["statement"]
        from research_harness.literature import record_search
        self.mutate(record_search, payload)
        search = self.store.snapshot()["records"]["literature_search"][payload["id"]]
        spec = self.build_spec([search])
        legacy = dict(vars(problem_openness))
        source = Path(__file__).with_name("fixtures") / "openness_export_0_69_15.py.txt"
        exec(compile(source.read_text(), str(source), "exec"), legacy)
        old = legacy["export_openness"](self.store, spec, self.root / "old-packet")
        result = problem_openness.export_openness(self.store, spec, self.root / "packet")
        self.assertEqual(result["native_packet"], old["native_packet"])
        forecast = result["audit_forecast"]
        self.assertEqual(forecast["outcomes"], {"supported_open": "possible", "already_solved": "blocked"})
        self.assertEqual([gap["condition"] for gap in forecast["gaps"]], ["settling_undeclared"])
        notice = forecast["notices"][0]
        self.assertEqual((notice["search_id"], notice["coverage"][0]["kind"]), (search["id"], "bounded_unordered"))
        item = notice["incomplete_responses"][0]
        self.assertEqual((item["query"], item["returned_count"], item["reported_count"]), ("bounded", 1, 4))
        self.assertEqual(forecast["searched_through"], "2026-09-09")
        packet_minimum = json.loads(self.artifacts.read(result["native_packet"]))["scientific_context"]["openness_minimum"]
        finding = {"minimum": packet_minimum, "outcome": "supported_open",
                   "searched_at": forecast["searched_through"], "evidence": [digest(search)]}
        self.audit(finding)

    def test_unresolved_obligation_reports_current_minimum_gaps(self):
        work = self.metadata()
        search = self.search([work], dispositions={work: "potentially_relevant"})
        finding = self.build_finding([search], outcome="unresolved")
        self.put("problem_openness", "unresolved", dict(finding, id="unresolved",
            statement_digest=self.load_problem_module().compute_statement_digest(self.selected),
            recorded_revision=self.store.revision + 1))
        obligation = problem_openness.assess_openness(self.store.snapshot()["records"], self.artifacts)[0]
        self.assertEqual(obligation["code"], "problem_openness_unresolved")
        self.assertEqual([gap["condition"] for gap in obligation["gaps"]], ["result_uncertain", "settling_undeclared"])

    def test_unresolved_obligation_reports_a_versionless_reading_gap_without_failing_status(self):
        versionless = "arxiv:2601.00001"
        search = self.search([versionless])
        records = self.store.snapshot()["records"]
        search = records["literature_search"][search["id"]]
        finding = self.build_finding([search], outcome="unresolved")
        records["problem_openness"] = {"unresolved": dict(finding, id="unresolved",
            statement_digest=self.load_problem_module().compute_statement_digest(self.selected))}
        obligation = problem_openness.assess_openness(records, self.artifacts)[0]
        self.assertEqual(obligation["code"], "problem_openness_unresolved")
        self.assertEqual(obligation["gaps"][0]["work_id"], versionless)
        self.assertEqual(obligation["gaps"][0]["code"], "missing_version")

    def test_cli_accept_gaps_is_local_to_the_export_command(self):
        from research_harness import cli
        search = self.search(gaps=["The saved search leaves a gap."])
        spec = self.build_spec([search])
        path = self.root / "spec.json"
        path.write_text(json.dumps(spec))
        args = cli.build_parser().parse_args(["export", "--workspace", str(self.root), "--kind",
            "problem-openness", "--spec", str(path), "--accept-gaps"])
        result = cli.run(args)
        self.assertEqual(result["warnings"][0]["code"], "openness_minimum_gaps")
        spec["accept_gaps"] = True
        self.assert_error("invalid_input", lambda: problem_openness.export_openness(self.store, spec))

    def test_export_example_provides_the_spec_without_persisting_the_option(self):
        from research_harness import cli
        spec = cli.run(cli.build_parser().parse_args(["example", "problem-openness-export"]))
        binding = spec["minimum"]["searches"][0]
        search = self.search(gaps=["The example leaves a declared search gap."])
        self.select_statement(identifier="example-statement", statement=binding["formulation"])
        search = dict(search, id=binding["search_id"], question=binding["formulation"])
        self.put("literature_search", binding["search_id"], search)
        path = self.root / "export-spec.json"
        path.write_text(json.dumps(spec))
        args = cli.build_parser().parse_args(["export", "--workspace", str(self.root), "--kind", "problem-openness",
            "--spec", str(path), "--accept-gaps"])
        result = cli.run(args)
        self.assertEqual(result["warnings"][0]["code"], "openness_minimum_gaps")
        packet = json.loads(self.artifacts.read(result["native_packet"]))
        self.assertNotIn("accept_gaps", json.dumps(packet))
