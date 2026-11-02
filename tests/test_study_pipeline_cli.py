"""Study pipeline commands preserve coverage, capacities and cumulative allowances."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
from unittest import mock

from literature_fixtures import LiteratureCase
from research_harness import batches, cli, lineage
from research_harness.principles import initialize_research
from research_harness.review_budget import record_budget


class StudyPipelineCliTests(LiteratureCase):
    def initialize(self, *, profile="research", policy="lineage-v1"):
        target = self.verification_target() if profile == "verification" else None
        self.mutate(initialize_research, {"profile": profile, "target": target, "preparation_policy": policy})

    def write_input(self, value):
        self.sequence += 1
        path = self.root.parent / ("input-%d.json" % self.sequence)
        path.write_text(json.dumps(value), encoding="utf-8")
        return str(path)

    def run_command(self, *arguments, exit_code=0):
        stdout, stderr = io.StringIO(), io.StringIO()
        code = 0
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                cli.main([*arguments, *([] if arguments[0] == "example" else ["--workspace", str(self.root)])])
            except SystemExit as error:
                code = error.code
        self.assertEqual(code, exit_code, stderr.getvalue())
        return json.loads(stdout.getvalue() if code == 0 else stderr.getvalue())

    def run_mutation(self, command, payload, *, check=False, exit_code=0, request=None, revision=None):
        path = self.write_input(payload)
        return self.run_command(command, "--file", path,
                                "--expected-revision", str(self.store.revision if revision is None else revision),
                                "--request-id", request or ("cli-%d" % self.sequence),
                                *(["--check"] if check else []), exit_code=exit_code)

    def read_files(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def read_manifest(self, result):
        path = Path(result["manifest"])
        self.assertTrue(path.is_file())
        return json.loads(path.read_bytes())

    def test_population_first_and_next_pages_expose_counts_without_writes(self):
        self.cohort(range(1, 32))
        before = self.read_files()
        first = self.run_command("population-query", "--terms", "bounded")
        self.assertEqual((first["total_matches"], first["returned"], first["offset"],
                          first["omitted"], first["remaining"], first["searchable"], first["unavailable"]),
                         (31, 30, 0, 1, 1, 31, 0))
        self.assertTrue(first["bound_reached"])
        second = self.run_command("population-query", "--terms", "bounded", "--cursor", first["next_cursor"])
        self.assertEqual((second["returned"], second["offset"], second["omitted"], second["remaining"]),
                         (1, 30, 30, 0))
        self.assertEqual(second["matches"][0]["version_id"], "arxiv:2601.00031v1")
        self.assertIsNone(second["next_cursor"])
        self.assertEqual(self.read_files(), before)

    def test_population_stale_and_wrong_query_cursors_name_restart_command(self):
        self.cohort((1, 2))
        page = lineage.population_query(self.store, ["bounded"], limit=1)
        for terms, code in (("other", "invalid_input"), ("bounded", "stale_cursor")):
            with self.subTest(code=code):
                if code == "stale_cursor":
                    self.metadata(3)
                before = self.read_files()
                error = self.run_command("population-query", "--terms", terms, "--cursor", page["next_cursor"],
                                         exit_code=1)["error"]
                self.assertEqual(error["code"], code)
                self.assertIn("population-query", error["details"]["recovery"])
                self.assertNotIn("--cursor", error["details"]["recovery"])
                self.assertEqual(self.read_files(), before)

    def test_candidate_page_coverage_survives_loading_and_batch_export(self):
        self.initialize()
        self.cohort((1, 2, 3))
        page = lineage.population_query(self.store, ["bounded"], limit=2)
        path = self.write_input(page)
        self.assertEqual(cli.load_candidates(path), ["arxiv:2601.00001v1", "arxiv:2601.00002v1"])
        before = self.store.snapshot()
        result = self.run_command("batches", "--loop", "--candidates", path,
                                  "--destination", str(self.root / "selected"))
        manifest = self.read_manifest(result)
        coverage = manifest["selection_coverage"]
        self.assertIn("query_coverage", coverage)
        self.assertEqual(coverage["query_coverage"], "partial")
        self.assertEqual(coverage["population_query"], {key: value for key, value in page.items() if key != "matches"})
        self.assertEqual(manifest["counts"]["exported"], 2)
        self.assertEqual(self.store.snapshot(), before)

    def test_complete_and_terminal_candidate_pages_report_whole_query_coverage(self):
        self.initialize()
        self.cohort((1, 2, 3))
        first = lineage.population_query(self.store, ["bounded"], limit=2)
        final = lineage.population_query(self.store, ["bounded"], cursor=first["next_cursor"])
        complete = lineage.population_query(self.store, ["bounded"])
        for name, page, status in (("final", final, "partial"), ("all", complete, "complete")):
            with self.subTest(name=name):
                result = self.run_command("batches", "--loop", "--candidates", self.write_input(page),
                                          "--destination", str(self.root / name))
                coverage = self.read_manifest(result)["selection_coverage"]
                self.assertIn("query_coverage", coverage)
                self.assertEqual(coverage["query_coverage"], status)

    def test_legacy_candidate_files_remain_explicit_selections_with_unknown_coverage(self):
        self.initialize()
        self.cohort((1, 2))
        for index, value in enumerate((["arxiv:2601.00001v1"],
                                       {"terms": ["bounded"], "matches": [{"version_id": "arxiv:2601.00002v1"}]})):
            with self.subTest(value=value):
                result = self.run_command("batches", "--loop", "--candidates", self.write_input(value),
                                          "--destination", str(self.root / ("legacy-%d" % index)))
                manifest = self.read_manifest(result)
                self.assertEqual(manifest["counts"]["exported"], 1)
                self.assertIn("query_coverage", manifest["selection_coverage"])
                self.assertEqual(manifest["selection_coverage"]["query_coverage"], "unknown")

    def test_refused_export_never_changes_a_manifest_created_by_another_exporter(self):
        self.initialize()
        self.cohort((1, 2))
        page = lineage.population_query(self.store, ["bounded"], limit=1)
        real_export = batches.export_batches
        for size, code in ((60, "review_destination_exists"), (0, "invalid_input"), (61, "input_io")):
            destination = self.root / ("competing-%d" % size)
            original = []

            def competing_export(store, **options):
                winner = dict(options, size=60, candidates=["arxiv:2601.00002v1"])
                real_export(store, **winner)
                original.append((destination / "manifest.json").read_bytes())
                if code == "input_io":
                    destination.mkdir()
                return real_export(store, **options)

            with self.subTest(size=size), mock.patch.object(batches, "export_batches", side_effect=competing_export):
                error = self.run_command("batches", "--loop", "--size", str(size), "--candidates", self.write_input(page),
                                         "--destination", str(destination), exit_code=1)["error"]
                self.assertEqual(error["code"], code)
                self.assertEqual((destination / "manifest.json").read_bytes(), original[0])

    def test_batch_output_capacity_options_reach_the_exporter(self):
        self.initialize(policy="exhaustive-v1")
        self.cohort((1, 2, 3))
        before = self.store.snapshot()
        result = self.run_command("batches", "--max-input-bytes", "8000", "--max-output-tokens", "500",
                                  "--estimated-output-tokens-per-item", "300",
                                  "--destination", str(self.root / "capacity"))
        manifest = self.read_manifest(result)
        self.assertEqual(manifest["capacity"], {"max_items": 60, "effective_max_items": 1,
            "max_input_bytes": 8000, "max_output_tokens": 500, "estimated_output_tokens_per_item": 300,
            "output_capacity": "estimated"})
        self.assertEqual(len(result["files"]), 3)
        self.assertTrue(all(Path(path).stat().st_size <= 8000 for path in result["files"]))
        self.assertEqual(self.store.snapshot(), before)

    def test_batch_byte_capacity_keeps_complete_unicode_abstracts(self):
        self.initialize()
        versions = [self.metadata(number, abstract="量" * 300) for number in (1, 2)]
        result = self.run_command("batches", "--loop", "--candidates", self.write_input(versions),
                                  "--max-input-bytes", "2800", "--destination", str(self.root / "bytes"))
        self.assertEqual(len(result["files"]), 2)
        for path in result["files"]:
            self.assertLessEqual(Path(path).stat().st_size, 2800)
            self.assertEqual(json.loads(Path(path).read_bytes())["items"][0]["text"], "量" * 300)

    def test_batch_output_options_must_be_paired_before_any_export(self):
        self.initialize(policy="exhaustive-v1")
        before = self.read_files()
        for option in ("--max-output-tokens", "--estimated-output-tokens-per-item"):
            with self.subTest(option=option):
                error = self.run_command("batches", option, "300", "--destination", str(self.root / "unpaired"),
                                         exit_code=1)["error"]
                self.assertEqual(error["code"], "invalid_input")
                self.assertIn("--max-output-tokens", error["message"])
                self.assertIn("--estimated-output-tokens-per-item", error["message"])
                self.assertEqual(self.read_files(), before)

    def test_batch_output_budget_refusal_names_larger_allowance(self):
        self.initialize(policy="exhaustive-v1")
        error = self.run_command("batches", "--max-output-tokens", "99",
                                 "--estimated-output-tokens-per-item", "100",
                                 "--destination", str(self.root / "small-output"), exit_code=1)["error"]
        self.assertEqual(error["code"], "batch_output_budget_too_small")
        recovery = error["details"]["recovery"]
        self.assertEqual(recovery[recovery.index("--max-output-tokens") + 1], "100")
        self.assertFalse((self.root / "small-output").exists())

    def test_oversized_candidate_keeps_incomplete_manifest_coverage_and_retry_bound(self):
        self.initialize()
        self.cohort((1, 2))
        page = lineage.population_query(self.store, ["bounded"], limit=1)
        destination = self.root / "too-small"
        error = self.run_command("batches", "--loop", "--candidates", self.write_input(page),
                                 "--max-input-bytes", "1", "--destination", str(destination), exit_code=1)["error"]
        self.assertEqual(error["code"], "batch_item_too_large")
        recovery = error["details"]["recovery"]
        self.assertEqual(int(recovery[recovery.index("--max-input-bytes") + 1]), error["details"]["input_bytes"])
        self.assertNotEqual(recovery[recovery.index("--destination") + 1], str(destination))
        manifest = json.loads((destination / "manifest.json").read_bytes())
        self.assertFalse(manifest["complete"])
        self.assertEqual(manifest["selection_coverage"]["query_coverage"], "partial")
        self.assertEqual(manifest["selection_coverage"]["population_query"]["next_cursor"], page["next_cursor"])

    def test_verification_depth_payload_and_profile_status_already_pass_through(self):
        self.initialize(profile="verification", policy="sampled-v1")
        payload = {"id": "verification-plan", "profile": "verification",
                   "values": {"hits_per_query": 12, "search_readings": 23, "core_fulltexts": 11},
                   "reason": "Inspect additional decisive literature.",
                   "stopping_criterion": "Resolve the remaining competing bounds."}
        saved = self.run_mutation("depth", payload)["result"]
        self.assertEqual(saved["profile"], "verification")
        self.assertEqual(saved["values"], payload["values"])
        for flags in ((), ("--summary",)):
            status = self.run_command("status", *flags)
            plan = status["limits"]["depth"]
            self.assertEqual(plan["profile"], "verification")
            self.assertEqual(plan["stopping_criterion"], payload["stopping_criterion"])
            self.assertEqual(plan["values"]["core_fulltexts"], 11)
            self.assertEqual(plan["values"]["sample_size"], 100)

    def test_review_budget_records_replays_and_renders_usage(self):
        payload = {"max_calls": 3, "reason": "Bound cumulative review calls."}
        before = self.store.revision
        result = self.run_mutation("review-budget", payload, request="budget", revision=before)
        self.assertEqual(result["result"]["max_calls"], 3)
        self.assertEqual(self.store.snapshot()["records"]["review_budget"]["store"], result["result"])
        usage = json.loads((self.root / "intermediate/reviews/usage.json").read_bytes())["review_budget"]
        self.assertEqual(usage, {"configured": True, "allowance": 3, "charged_calls": 0, "remaining_calls": 3})
        self.assertEqual(self.run_mutation("review-budget", payload, request="budget", revision=before), result)
        self.assertEqual(self.store.revision, before + 1)
        self.assertEqual(self.run_mutation("review-budget", dict(payload, max_calls=None))["result"]["max_calls"], None)

    def test_review_budget_below_charged_calls_refuses_with_recovery_in_both_modes(self):
        self.mutate(record_budget, {"max_calls": 2, "reason": "Initial allowance."})
        self.store.mutate("fixture.admission", {},
                          lambda tx: tx.put("review_invocation", "failed", {"id": "failed", "assignment_id": "assignment"}) or {},
                          expected_revision=self.store.revision, request_id="admission")
        for check in (True, False):
            before = self.store.snapshot()
            files_before = self.read_files()
            error = self.run_mutation("review-budget", {"max_calls": 0, "reason": "Too small."},
                                       check=check, exit_code=1)["error"]
            self.assertEqual(error["code"], "review_budget_below_charged")
            self.assertEqual(error["details"]["charged_calls"], 1)
            self.assertEqual(error["details"]["max_calls"], 0)
            self.assertEqual(error["details"]["minimum_max_calls"], 1)
            self.assertIn("review-budget", error["details"]["recovery"])
            self.assertEqual(self.store.snapshot(), before)
            if check:
                self.assertEqual(self.read_files(), files_before)

    def test_review_budget_check_accepts_without_writing_any_files(self):
        before = self.read_files()
        checked = self.run_mutation("review-budget", {"max_calls": 0, "reason": "Pause new review admissions."},
                                     check=True)
        self.assertEqual((checked["check"], checked["recorded"], checked["revision"]), ("accepted", False, 0))
        self.assertEqual(self.read_files(), before)

    def test_review_budget_example_is_registered(self):
        example = self.run_command("example", "review-budget")
        self.assertEqual(set(example), {"max_calls", "reason"})
        self.run_mutation("review-budget", example, check=True)
