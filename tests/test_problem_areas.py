"""Further areas extend blind bars without changing the record's subject class."""

import copy
import json
import lzma
from pathlib import Path
from unittest.mock import patch

from research_harness import cli, output_views, problem_claims, problem_evidence, research_decisions, review_protocol
from research_harness import review_prompt_paragraphs
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from test_research_problem_claims import ProblemCase
from test_research_problem_delivery import ProblemDeliveryCase


class AreaMutationCase(ProblemCase):
    def add_area(self, **changes):
        self.assertIn("problem-area", cli.OPERATIONS)
        return self.mutate(cli.OPERATIONS["problem-area"], dict(
            {"id": "number-theory", "area": "math.NT",
             "reason": "The proposed relation also concerns arithmetic bounds."}, **changes))["result"]


class ProblemAreaMutationTests(AreaMutationCase):
    def test_area_is_an_immutable_record_and_leaves_the_marker_bytes(self):
        marker = self.initialize_problem()
        before = digest(marker)
        saved = self.add_area()
        self.assertEqual(saved, {"id": "number-theory", "area": "math.NT",
            "reason": "The proposed relation also concerns arithmetic bounds.", "recorded_revision": 2})
        records = self.store.snapshot()["records"]
        self.assertEqual(records["problem_area"], {"number-theory": saved})
        self.assertEqual(records["workspace"]["problem"], marker)
        self.assertEqual(digest(records["workspace"]["problem"]), before)
        self.assert_error("record_conflict", lambda: self.add_area(reason="A different reason."))

    def test_area_requires_a_problem_record(self):
        self.assert_error("orchestration_conflict", self.add_area)
        self.assertEqual(self.store.revision, 0)

    def test_area_requires_exact_fields_and_nonempty_text(self):
        self.initialize_problem()
        operation = cli.OPERATIONS.get("problem-area")
        self.assertIsNotNone(operation)
        good = {"id": "number-theory", "area": "math.NT", "reason": "An arithmetic relation."}
        invalid = [dict(good, extra="unrecognized")]
        for key in good:
            invalid.append({name: value for name, value in good.items() if name != key})
            invalid.extend(dict(good, **{key: value}) for value in (None, "", " \n\t", 3, True, [], {}, "bad\x00text"))
        invalid.extend(dict(good, area=value) for value in ("NT", "cs.DM", ["math.NT", "math.AG"]))
        before = self.store.snapshot()
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assert_error("invalid_input", lambda: self.mutate(operation, payload))
                self.assertEqual(self.store.snapshot(), before)

    def test_own_area_and_duplicate_further_area_are_refused(self):
        self.initialize_problem()
        self.assert_error("problem_area_duplicate", lambda: self.add_area(area="math.CO"))
        self.add_area()
        before = self.store.snapshot()
        self.assert_error("problem_area_duplicate", lambda: self.add_area(id="another-id"))
        self.assertEqual(self.store.snapshot(), before)

    def test_several_areas_can_be_recorded_before_candidate_delivery(self):
        self.initialize_problem()
        self.select_statement()
        self.add_area()
        second = self.add_area(id="geometry", area="math.AG")
        self.assertEqual(second["area"], "math.AG")
        self.assertEqual(len(self.store.snapshot()["records"]["problem_area"]), 2)

    def test_every_further_area_renders_as_a_current_problem_record(self):
        self.initialize_problem()
        areas = [self.add_area(), self.add_area(id="geometry", area="math.AG")]
        output_views.render_all(self.store)
        for area in areas:
            path = self.root / "final/problem/areas" / (area["id"] + ".json")
            self.assertTrue(path.is_file(), str(path))
            rendered = json.loads(path.read_text())
            self.assertEqual(rendered, area)

    def test_cli_example_check_and_mutation_use_the_common_identity_contract(self):
        self.initialize_problem()
        parser = cli.build_parser()
        self.assertIn("problem-area", cli.OPERATIONS)
        payload = cli.run(parser.parse_args(["example", "problem-area"]))
        self.assertEqual(set(payload), {"id", "area", "reason"})
        path = self.root / "area.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        args = ["problem-area", "--workspace", str(self.root), "--file", str(path),
                "--expected-revision", "1", "--request-id", "add-area"]
        before = self.store.snapshot()
        cli.run(parser.parse_args(args + ["--check"]))
        self.assertEqual(self.store.snapshot(), before)
        saved = cli.run(parser.parse_args(args))
        self.assertEqual(saved["result"]["area"], payload["area"])
        self.assertEqual(cli.run(parser.parse_args(args)), saved)


class ProblemAreaReviewTests(ProblemDeliveryCase, AreaMutationCase):
    def read_packet(self, assignment):
        return json.loads(self.artifacts.read(assignment["packet"]))

    def continue_bar(self, reviewer, suffix):
        output = self.build_review_output("bar")
        output["value"]["reason"] = ("math.CO: extremal bounds at levels 2 and 3. "
            "math.NT: arithmetic bounds at levels 2 and 3. math.AG: geometric bounds at levels 2 and 3.")
        return self.observe("bar", reviewer, output, identifier="bar-" + reviewer + suffix)

    def test_first_bars_receive_further_areas_and_the_registered_paragraph(self):
        self.initialize_problem()
        area = self.add_area()
        self.observe_bars()
        records = self.store.snapshot()["records"]
        bars = []
        for reviewer in ("first", "second"):
            assignment = records["review_assignment"]["bar-" + reviewer]
            packet = self.read_packet(assignment)
            self.assertEqual(packet["problem_areas"], [area])
            self.assertEqual(packet["area"], "math.CO")
            self.assertIn("exactory-problem-areas-v1", assignment["prompt"]["parameters"]["problem_paragraphs"])
            self.assertTrue(review_protocol._prompt_matches(assignment, packet=packet))
            self.assertNotIn("problem_refresh", packet)
            bars.append(records["value_review"]["review-bar-" + reviewer])
        review_protocol.validate_problem_bars(records, self.artifacts, bars)

    def test_area_after_bars_requires_both_continuations_before_slate(self):
        self.initialize_problem()
        self.record_candidate()
        self.observe_bars()
        marker = copy.deepcopy(self.store.snapshot()["records"]["workspace"]["problem"])
        self.add_area()
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            self.record_slate()
        self.assertEqual(caught.exception.code, "problem_bar_calibration_required")
        self.assertEqual(caught.exception.details,
                         {"review_id": "review-bar-first", "area_ids": ["number-theory"]})
        self.assert_error("problem_bar_calibration_required", lambda: self.assign("slate", "first"))
        self.assertEqual(self.store.snapshot(), before)
        first = self.continue_bar("first", "-areas")
        self.assert_error("problem_bar_calibration_required", self.record_slate)
        second = self.continue_bar("second", "-areas")
        bar_ids = [first["id"], second["id"]]
        self.assert_error("problem_bar_calibration_required", self.record_slate)
        self.assertEqual(self.record_slate(bar_review_ids=bar_ids)["bar_review_ids"], bar_ids)
        slate = self.assign("slate", "first")
        self.assertEqual(self.read_packet(slate)["bar_ids"], bar_ids)
        records = self.store.snapshot()["records"]
        self.assertEqual(records["workspace"]["problem"], marker)
        self.assertEqual(review_protocol.find_current_problem_bar_ids(records, self.artifacts), bar_ids)
        for reviewer in ("first", "second"):
            prior = records["review_assignment"]["bar-" + reviewer]
            current = records["review_assignment"]["bar-" + reviewer + "-areas"]
            packet = self.read_packet(current)
            self.assertEqual((current["context"], current["history_assignment_id"], current["slot_id"]),
                ({"prior_assignment_id": prior["id"]}, prior["id"], prior["slot_id"]))
            self.assertEqual(packet["problem_refresh"]["assignment_id"], prior["id"])
            self.assertEqual(packet["problem_areas"][0]["area"], "math.NT")
            self.assertIn("exactory-problem-areas-v1", current["prompt"]["parameters"]["problem_paragraphs"])
        self.assert_error("review_resampling_forbidden", lambda: self.assign("bar", "first", identifier="third-bar"))

    def test_later_area_requires_another_continuation_with_all_areas(self):
        self.initialize_problem()
        self.observe_bars()
        first_area = self.add_area()
        self.continue_bar("first", "-nt")
        second_area = self.add_area(id="geometry", area="math.AG")
        final = self.continue_bar("first", "-ag")
        records = self.store.snapshot()["records"]
        assignment = records["review_assignment"][final["assignment_id"]]
        self.assertEqual(assignment["history_assignment_id"], "bar-first-nt")
        self.assertEqual(assignment["slot_id"], "bar-first")
        self.assertCountEqual(self.read_packet(assignment)["problem_areas"], [first_area, second_area])
        review_protocol.validate_problem_bars(records, self.artifacts, [final])

    def test_area_added_while_a_bar_is_pending_reaches_its_continuation(self):
        self.initialize_problem()
        self.supporting = self.artifacts.put(b"The field standard and its sources.", "text/plain")
        self.prepare_review_route()
        pending = self.assign("bar", "first", **self.deliver_supporting("first"))
        area = self.add_area()
        output = self.build_review_output("bar")
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(review_protocol.invoke_assignment,
                {"id": "pending-bar-attempt", "assignment_id": pending["id"]}, credential="test-only")
        self.mutate(research_decisions.record_value_review, dict(output, id="review-bar-first",
            dossier_id=None, assignment_id=pending["id"]))
        final = self.continue_bar("first", "-areas")
        records = self.store.snapshot()["records"]
        assignment = records["review_assignment"][final["assignment_id"]]
        self.assertEqual(assignment["history_assignment_id"], "bar-first")
        self.assertEqual(self.read_packet(assignment)["problem_areas"], [area])

    def test_legacy_bar_calibration_and_added_areas_share_one_continuation(self):
        self.initialize_problem()
        self.record_candidate()
        canonical_prompt = review_protocol.canonical_prompt

        def render_legacy_bar(role, **options):
            if role == "bar" and options.get("orchestration") == "problem_creation":
                options["version"] = 1
            return canonical_prompt(role, **options)

        with patch.object(review_protocol, "canonical_prompt", side_effect=render_legacy_bar):
            self.observe_bars()
        area = self.add_area()
        self.assert_error("problem_bar_calibration_required", self.record_slate)
        bar_ids = [self.continue_bar(reviewer, "-current")["id"] for reviewer in ("first", "second")]
        self.assertEqual(self.record_slate(bar_review_ids=bar_ids)["bar_review_ids"], bar_ids)
        records = self.store.snapshot()["records"]
        for reviewer in ("first", "second"):
            assignment = records["review_assignment"]["bar-" + reviewer + "-current"]
            packet = self.read_packet(assignment)
            self.assertEqual(assignment["history_assignment_id"], "bar-" + reviewer)
            self.assertEqual(packet["problem_areas"], [area])
            self.assertEqual(packet["problem_refresh"]["previous_template_id"], "exactory-problem-review-v1/bar")

    def test_result_requires_a_slate_that_cites_the_continued_bars(self):
        self.initialize_problem()
        self.record_candidate()
        self.observe_bars()
        self.record_slate()
        self.select_statement(candidate_id="candidate-1")
        self.add_area()
        bar_ids = [self.continue_bar(reviewer, "-areas")["id"] for reviewer in ("first", "second")]
        before = self.store.snapshot()
        self.assert_error("problem_bar_calibration_required", lambda: self.assign("result", "first"))
        self.assertEqual(self.store.snapshot(), before)
        self.record_slate("slate-areas", bar_review_ids=bar_ids)
        result = self.assign("result", "first")
        self.assertEqual(self.read_packet(result)["bar_ids"], bar_ids)

    def test_source_continuation_receives_an_area_added_between_turns(self):
        self.initialize_problem()
        self.supporting = self.artifacts.put(b"The field standard and its sources.", "text/plain")
        self.prepare_review_route()
        request = self.build_review_output("bar")
        request["value"]["status"] = "unresolved"
        request["source_requests"] = [{"id": "page", "kind": "inventory_page", "page": 0}]
        self.observe("bar", "first", request, record=False)
        area = self.add_area()
        continued = self.assign("bar", "first", identifier="bar-sources", prior_assignment_id="bar-first")
        packet = self.read_packet(continued)
        self.assertEqual(packet["problem_areas"], [area])
        self.assertEqual(continued["slot_id"], "bar-first")
        self.assertIn("exactory-problem-areas-v1", continued["prompt"]["parameters"]["problem_paragraphs"])
        self.assertEqual(packet["source_deliveries"][0]["status"], "delivered")
        output = self.build_review_output("bar")
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(review_protocol.invoke_assignment,
                {"id": "continued-attempt", "assignment_id": continued["id"]}, credential="test-only")
        review = self.mutate(research_decisions.record_value_review, dict(output, id="continued-bar",
            dossier_id=None, assignment_id=continued["id"]))["result"]
        review_protocol.validate_problem_bars(self.store.snapshot()["records"], self.artifacts, [review])

    def test_pending_slate_assignment_closes_the_area_window_without_a_review(self):
        self.initialize_problem()
        self.record_candidate()
        self.observe_bars()
        self.assign("slate", "first")
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            self.add_area()
        self.assertEqual(caught.exception.code, "problem_area_new_record_required")
        for repair in ("new record", "carry", "forming-import"):
            self.assertIn(repair, caught.exception.message)
        self.assertEqual(self.store.snapshot(), before)

    def test_pending_and_failed_result_assignments_close_the_area_window(self):
        observe = self.observe

        def leave_results_pending(stage, reviewer, *args, **options):
            if stage == "result":
                return self.assign(stage, reviewer)
            return observe(stage, reviewer, *args, **options)

        with patch.object(self, "observe", side_effect=leave_results_pending):
            self.prepare_ready()
        before = self.store.snapshot()
        self.assert_error("problem_area_new_record_required", self.add_area)
        self.assertEqual(self.store.snapshot(), before)
        with patch("research_harness.review_transport.send_request", side_effect=ResearchError(
                "review_provider_error", "The provider did not complete the review.")):
            self.mutate(review_protocol.invoke_assignment,
                {"id": "failed-result", "assignment_id": "result-first"}, credential="test-only")
        self.assert_error("problem_area_new_record_required", self.add_area)

    def test_completed_result_assignments_close_the_area_window(self):
        self.prepare_ready()
        before = self.store.snapshot()
        self.assert_error("problem_area_new_record_required", self.add_area)
        self.assertEqual(self.store.snapshot(), before)

    def test_result_packet_delivers_areas_and_keeps_the_primary_area_rubric(self):
        self.initialize_problem()
        area = self.add_area()
        # prepare_ready opens the record; retain its real evidence and review setup here.
        with patch.object(self, "initialize_problem", return_value=None):
            self.prepare_ready()
        records = self.store.snapshot()["records"]
        for reviewer in ("first", "second"):
            assignment = records["review_assignment"]["result-" + reviewer]
            packet = self.read_packet(assignment)
            self.assertEqual(packet["problem_areas"], [area])
            self.assertEqual(packet["area"], "math.CO")
            self.assertIn("exactory-problem-areas-v1", assignment["prompt"]["parameters"]["problem_paragraphs"])
            self.assertTrue(review_protocol.has_problem_result_rubric(assignment))
        self.assertTrue(research_decisions.assess_problem_reviews(records, self.artifacts)["ready"])

    def test_low_primary_area_grade_still_fails_the_unchanged_threshold(self):
        self.initialize_problem()
        self.add_area()
        build_output = self.build_review_output

        def grade_primary_area(stage):
            output = build_output(stage)
            if stage == "result":
                output["value"]["rubric"]["priority"] = 1
                output["value"]["rubric_evidence"]["priority"] = (
                    "math.CO priority is 1; the arithmetic consequence does not raise this grade.")
            return output

        with patch.object(self, "initialize_problem", return_value=None), patch.object(
                self, "build_review_output", side_effect=grade_primary_area):
            self.prepare_ready()
        report = research_decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)
        self.assertFalse(report["ready"])
        self.assertIn("problem_rubric_below_threshold", {item["code"] for item in report["obligations"]})


class StoredProblemAreaCompatibilityTests(ProblemDeliveryCase):
    def test_stored_value_and_bars_keep_projection_digests_and_frozen_prompt_bytes(self):
        directory = Path(__file__).parent / "fixtures"
        stored = json.loads((directory / "problem_result_0820.json").read_text())
        frozen = json.loads(lzma.decompress((directory / "review-prompts-0.82.0.json.xz").read_bytes()))
        self.initialize_problem()
        self.observe_bars()
        records = self.store.snapshot()["records"]
        records.update(copy.deepcopy(stored["records"]))
        before = copy.deepcopy(records)
        bars = [records["value_review"]["review-bar-" + reviewer] for reviewer in ("first", "second")]
        review_protocol.validate_problem_bars(records, self.artifacts, bars)
        for key, project in (("binding_bytes", problem_evidence.describe_binding),
                             ("evidence_bytes", problem_evidence.describe_evidence)):
            actual = json.dumps(project(records, self.artifacts), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            self.assertEqual(actual, stored[key])
        self.assertEqual(digest(records["problem_value"]["value-1"]), stored["value_digest"])
        self.assertEqual(problem_claims.compute_problem_evidence_digest(records, self.artifacts), stored["evidence_digest"])
        for role in ("bar", "slate", "result"):
            with self.subTest(role=role):
                packet = review_protocol.build_packet(records, self.artifacts, None, role, "first")
                self.assertNotIn("problem_areas", packet)
                parameters = review_prompt_paragraphs.select_prompt_parameters(role, packet)
                self.assertEqual(parameters, {})
                options = dict(orchestration="problem_creation", version=5, common_version=3)
                expected = next(case["prompt"] for case in frozen["cases"]
                                if case["role"] == role and case["options"] == options)
                prompt = review_protocol.canonical_prompt(role, **options, **parameters)
                self.assertEqual(json.dumps(prompt, sort_keys=True).encode(),
                                 json.dumps(expected, sort_keys=True).encode())
        self.assertEqual(records, before)
