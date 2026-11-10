"""Ideate's forming history is immutable, exploratory and separate from claims."""

import copy
import json
from pathlib import Path
from unittest.mock import patch

from research_harness import cli, output_views, problem_candidates, problem_claims, problem_evidence
from research_harness.evidence import digest
from test_research_problem_candidates import build_candidate
from test_research_problem_claims import ProblemCase
from test_research_probe_lifecycle import build_probe_intent, build_probe_outcome


def build_form(identifier="form-1", **changes):
    return dict({"id": identifier, "text": "For every n, B(n) <= n^2.", "exact": True,
        "purpose": {"kind": "expected_statement", "form_id": None}, "previous": None,
        "grounds": [{"description": "The finite constructions suggest the bound.", "evidence": []}],
        "nearest_alternatives": [], "expected_relations": [],
        "planned_checks": [{"id": "check-1", "description": "Compute B(3).", "contradiction": "B(3) > 9.",
                            "no_contradiction_reason": None}], "correction_checks": []}, **changes)


def build_forming(identifier="forming-1", forms=None, **changes):
    forms = [build_form()] if forms is None else forms
    return dict({"id": identifier, "question": {"kind": "thought_experiment", "description": "How large can B(n) be?",
        "places": []}, "move": {"kind": "expectation", "description": "State the expected size as a proposition."},
        "inputs": [], "amounts": {"forms": len(forms), "nearest_alternatives": {form["id"]: len(form["nearest_alternatives"]) for form in forms},
            "expected_relations": {form["id"]: len(form["expected_relations"]) for form in forms}, "pairs": None},
        "dictionary": None, "comparison_count": None, "forms": forms, "probes": [],
        "stopping_reason": "The proposed forms are ready for checks.",
        "performer": {"kind": "session", "session_id": "session-1"}, "usage": None}, **changes)


def build_outcome(identifier="outcome-1", **changes):
    return dict({"id": identifier, "form_id": "form-1", "target": {"kind": "form", "id": None},
        "standing": "unresolved", "disposition": {"status": "open", "reason": "Further work is needed.",
            "form_id": None, "candidate_id": None}, "lack_of_means": None,
        "outcome": "The bound remains unresolved.", "evidence": [], "reason": "No general argument is available."}, **changes)


class ProblemFormingTests(ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()

    def record_forming(self, payload=None):
        self.assertIn("forming", cli.OPERATIONS)
        return self.mutate(cli.OPERATIONS["forming"], build_forming() if payload is None else payload)["result"]

    def record_outcome(self, payload=None):
        self.assertIn("form-outcome", cli.OPERATIONS)
        return self.mutate(cli.OPERATIONS["form-outcome"], build_outcome() if payload is None else payload)["result"]

    def cite_record(self, kind, identifier):
        row = self.store.snapshot()["records"][kind][identifier]
        return {"kind": "record", "record_kind": kind, "id": identifier, "digest": digest(row)}

    def record_probe(self):
        self.mutate(cli.OPERATIONS["probe-intent"], build_probe_intent(self.artifacts))
        return self.mutate(cli.OPERATIONS["probe"], build_probe_outcome(self.artifacts))["result"]

    def build_probe_evidence(self, **changes):
        return dict({"kind": "counterexample", "description": "The finite example exceeds the bound.",
            "standing": "exploratory", "date": "2026-11-09T01:00:00Z",
            "reference": {"kind": "probe", "id": "probe-1"}, "counterexample": "n=3, B(3)=10 > 9."}, **changes)

    def test_records_all_forms_and_reported_amounts_without_an_intent_or_milestone(self):
        alternative = build_form("alternative", purpose={"kind": "alternative", "form_id": "form-1"})
        form = build_form(nearest_alternatives=["alternative"], expected_relations=[
            {"id": "relation-1", "description": "The first form implies the alternative.", "form_ids": ["alternative"]}])
        payload = build_forming(forms=[form, alternative], usage={"input_tokens": 100, "output_tokens": None})
        revision = self.store.revision
        saved = self.record_forming(payload)
        self.assertEqual({key: saved[key] for key in payload}, payload)
        self.assertEqual(saved["recorded_revision"], revision + 1)
        records = self.store.snapshot()["records"]
        self.assertEqual(records["forming_call"][saved["id"]], saved)
        self.assertFalse(records.get("research_intent"))
        self.assertFalse(records.get("resource_account"))
        self.assert_error("record_conflict", lambda: self.record_forming(dict(payload, stopping_reason="Changed.")))
        self.put("problem_post", "posted", {"id": "posted"})
        self.record_forming(build_forming("after-post", forms=[]))
        self.record_outcome()

    def test_records_no_form_and_replays_the_original_receipt(self):
        payload = build_forming(forms=[], stopping_reason="No promising exact statement emerged.")
        revision = self.store.revision
        first = cli.OPERATIONS["forming"](self.store, payload, expected_revision=revision, request_id="repeat")
        replay = cli.OPERATIONS["forming"](self.store, payload, expected_revision=revision, request_id="repeat")
        self.assertEqual(first, replay)
        self.assertEqual(first["result"]["forms"], [])

    def test_open_move_transfer_dictionary_comparison_and_model_artifacts(self):
        prompt = self.artifacts.put(b"State the transfer.", "text/plain")
        output = self.artifacts.put(b"A carried theorem.", "text/plain")
        self.record_forming(build_forming(move={"kind": "dualization", "description": "Exchange the two roles."},
            performer={"kind": "model_lane", "lane_id": "lane-1", "model": "reported-model", "effort": "reported-effort",
                       "prompt": prompt, "output": output}))
        dictionary = {"source_setting": "integers", "target_setting": "polynomials", "entries": [
            {"source": "log size", "target": "degree", "description": "Replace size by degree."}]}
        self.record_forming(build_forming("transfer", forms=[], move={"kind": "transfer", "description": "Carry the theorem."}, dictionary=dictionary))
        self.record_forming(build_forming("comparison", forms=[], move={"kind": "comparison", "description": "Compare sequences."},
            amounts={"forms": 0, "nearest_alternatives": {}, "expected_relations": {}, "pairs": 20}, comparison_count=20))

    def test_question_places_inputs_grounds_and_probes_validate_evidence(self):
        self.record_probe()
        evidence = [self.cite_record("probe", "probe-1")]
        payload = build_forming(question={"kind": "phenomenon", "description": "The same values occur in two settings.",
            "places": [{"description": "Finite sequences", "evidence": evidence}]},
            inputs=[{"description": "Observed values", "evidence": evidence}],
            forms=[build_form(grounds=[{"description": "Computed small cases", "evidence": evidence}])],
            probes=[{"probe_id": "probe-1", "form_id": "form-1", "role": "origin"}])
        self.record_forming(payload)
        bad = copy.deepcopy(payload)
        bad["id"] = "bad"
        bad["question"]["places"][0]["evidence"][0]["digest"] = "0" * 64
        self.assert_error("strategy_evidence_stale", lambda: self.record_forming(bad))

    def test_correction_accounts_for_every_earlier_check(self):
        self.record_forming()
        form = build_form("corrected", previous={"form_id": "form-1", "reason": "The first bound fails."}, correction_checks=[
            {"check_id": "check-1", "was_made": True, "keeps_prediction": False, "chosen_with_outcome": True}])
        payload = build_forming("correction", forms=[form], move={"kind": "correction", "description": "Allow the observed discrepancy."})
        saved = self.record_forming(payload)
        self.assertEqual(saved["forms"][0]["correction_checks"], form["correction_checks"])
        for checks in ([], [dict(form["correction_checks"][0], check_id="unknown")], form["correction_checks"] * 2):
            bad = copy.deepcopy(payload)
            bad["id"] = "bad-correction"
            bad["forms"][0]["id"] = "bad-corrected"
            bad["amounts"]["nearest_alternatives"] = {"bad-corrected": 0}
            bad["amounts"]["expected_relations"] = {"bad-corrected": 0}
            bad["forms"][0]["correction_checks"] = checks
            self.assert_error("problem_form_correction_incomplete", lambda: self.record_forming(bad))

    def test_forming_refuses_bad_fields_counts_and_shapes(self):
        changes = [lambda p: p.update(extra=True), lambda p: p.pop("question"), lambda p: p.update(id=""),
            lambda p: p["question"].update(kind=""),
            lambda p: p["question"].update(kind="phenomenon"),
            lambda p: p["move"].update(description=""), lambda p: p.update(stopping_reason=""),
            lambda p: p["amounts"].update(forms=True), lambda p: p["amounts"].update(pairs=-1),
            lambda p: p["amounts"].update(nearest_alternatives={}), lambda p: p.update(comparison_count=1.5),
            lambda p: p.update(dictionary={}), lambda p: p["move"].update(kind="transfer"),
            lambda p: p["move"].update(kind="comparison"), lambda p: p.update(usage={"tokens": -1}),
            lambda p: p.update(usage=[]), lambda p: p["performer"].update(kind="unknown"),
            lambda p: p["performer"].update(session_id=""),
            lambda p: p["forms"][0].update(exact=1), lambda p: p["forms"][0].update(text=""),
            lambda p: p["forms"][0]["purpose"].update(kind="candidate"),
            lambda p: p["forms"][0]["planned_checks"][0].update(contradiction=None),
            lambda p: p["forms"][0]["planned_checks"][0].update(no_contradiction_reason="Cannot fail."),
            lambda p: p["forms"][0].update(correction_checks=[{"check_id": "check-1"}]),
            lambda p: p["forms"][0].update(nearest_alternatives=["form-1"])]
        for change in changes:
            bad = build_forming()
            change(bad)
            with self.subTest(payload=bad):
                self.assert_error("invalid_input", lambda: self.record_forming(bad))
        self.assertFalse(self.store.snapshot()["records"].get("forming_call"))
        for key in ("places", "inputs"):
            bad = build_forming()
            parent = bad["question"] if key == "places" else bad
            parent[key] = [{"description": "A place or input", "evidence": []}]
            self.assert_error("invalid_strategy", lambda: self.record_forming(bad))

    def test_forming_refuses_duplicate_missing_and_nonhistorical_form_references(self):
        self.record_forming()
        self.assert_error("problem_form_id_conflict", lambda: self.record_forming(build_forming("again")))
        self.assert_error("problem_form_id_conflict", lambda: self.record_forming(build_forming("duplicate", forms=[build_form("x"), build_form("x")])))
        for form in (build_form("x", nearest_alternatives=["missing"]),
                     build_form("x", purpose={"kind": "alternative", "form_id": "missing"}),
                     build_form("x", previous={"form_id": "missing", "reason": "A correction."}),
                     build_form("x", expected_relations=[{"id": "r", "description": "Equivalent", "form_ids": ["missing"]}])):
            self.assert_error("problem_form_missing", lambda: self.record_forming(build_forming("missing", forms=[form])))
        self.assert_error("strategy_reference_missing", lambda: self.record_forming(build_forming("no-probe", forms=[],
            probes=[{"probe_id": "missing", "form_id": None, "role": "origin"}])))
        self.assert_error("invalid_input", lambda: self.record_forming(build_forming("test-credit", forms=[],
            probes=[{"probe_id": "probe-1", "form_id": None, "role": "test"}])))

    def test_outcomes_keep_standing_disposition_and_history_apart(self):
        self.record_forming()
        first = self.record_outcome()
        second = self.record_outcome(build_outcome("later", standing="not_examined", lack_of_means="No solver is available.",
            disposition={"status": "set_aside", "reason": "No means to continue.", "form_id": None, "candidate_id": None}))
        self.assertIsNone(first["previous"])
        self.assertEqual(second["previous"], first["id"])
        self.assertEqual(self.store.snapshot()["records"]["form_outcome"][first["id"]], first)
        self.assertEqual(second["standing"], "not_examined")
        self.assertEqual(second["disposition"]["status"], "set_aside")
        self.assert_error("record_conflict", lambda: self.record_outcome())

    def test_outcomes_for_relations_and_checks_supersede_only_the_same_item(self):
        self.record_forming(build_forming(forms=[build_form(expected_relations=[
            {"id": "relation-1", "description": "Equivalent to a known statement.", "form_ids": []}])]))
        for index, kind in enumerate(("expected_relation", "planned_check")):
            target = {"kind": kind, "id": "relation-1" if index == 0 else "check-1"}
            payload = build_outcome(kind, target=target, standing=None, disposition=None)
            first = self.record_outcome(payload)
            second = self.record_outcome(dict(payload, id=kind + "-later"))
            self.assertIsNone(first["previous"])
            self.assertEqual(second["previous"], first["id"])
        self.assertIsNone(self.record_outcome()["previous"])

    def test_exploratory_refutation_names_its_counterexample_and_standing(self):
        self.record_probe()
        self.record_forming()
        saved = self.record_outcome(build_outcome(standing="refuted", evidence=[self.build_probe_evidence()]))
        self.assertEqual(saved["refutation_basis"], "exploratory_computation")
        self.assertEqual(saved["evidence"][0]["standing"], "exploratory")
        self.assert_error("problem_form_counterexample_missing", lambda: self.record_outcome(build_outcome("bad", standing="refuted",
            evidence=[self.build_probe_evidence(counterexample=None)])))
        self.assert_error("problem_form_evidence_standing", lambda: self.record_outcome(build_outcome("bad", evidence=[
            self.build_probe_evidence(standing="admissible")])))

    def test_outcome_admissible_reading_uses_existing_validation(self):
        reading = self.record_full_reading()
        self.record_forming()
        evidence = {"kind": "a theorem read in full", "description": "The full text establishes this form.",
            "standing": "admissible", "date": "2026-11-09T01:00:00Z", "reference": reading, "counterexample": None}
        self.record_outcome(build_outcome(standing="found_in_literature", evidence=[evidence]))
        bad = copy.deepcopy(evidence)
        bad["reference"]["reading_id"] = "missing"
        self.assert_error("problem_value_reading_missing", lambda: self.record_outcome(build_outcome("bad", evidence=[bad])))
        bad = dict(evidence, standing="exploratory")
        self.assert_error("problem_form_evidence_standing", lambda: self.record_outcome(build_outcome("bad", evidence=[bad])))

    def test_outcome_refuses_bad_inputs_and_unsubstantiated_claims(self):
        self.record_forming()
        changes = [("problem_form_missing", lambda p: p.update(form_id="missing")),
            ("problem_form_item_missing", lambda p: p.update(target={"kind": "planned_check", "id": "missing"})),
            ("invalid_input", lambda p: p.update(standing="selected")),
            ("invalid_input", lambda p: p["disposition"].update(status="refuted")),
            ("invalid_input", lambda p: p.update(standing="not_examined")),
            ("invalid_input", lambda p: p.update(reason="")),
            ("invalid_input", lambda p: p.update(target={"kind": "form", "id": "check-1"})),
            ("invalid_input", lambda p: p["disposition"].update(status="superseded")),
            ("invalid_input", lambda p: p["disposition"].update(status="taken_up_as_candidate")),
            ("invalid_input", lambda p: p.update(target={"kind": "planned_check", "id": "check-1"})),
            ("problem_form_evidence_missing", lambda p: p.update(standing="proved")),
            ("problem_form_evidence_missing", lambda p: p.update(standing="refuted")),
            ("problem_form_evidence_missing", lambda p: p.update(standing="supported"))]
        for code, change in changes:
            bad = build_outcome()
            change(bad)
            with self.subTest(payload=bad):
                self.assert_error(code, lambda: self.record_outcome(bad))
        self.assertFalse(self.store.snapshot()["records"].get("form_outcome"))

    def test_candidates_cite_forming_calls_and_outcomes_with_exact_digests(self):
        self.record_forming()
        self.record_outcome()
        evidence = [self.cite_record("forming_call", "forming-1"), self.cite_record("form_outcome", "outcome-1")]
        candidate = build_candidate(source={"kind": "own_record", "reference": "The recorded expectation and its outcome.", "evidence": evidence})
        self.mutate(problem_candidates.record_candidate, candidate)
        # A3 owns the source-kind vocabulary; exercise A2's condition with that literal accepted.
        with patch.object(problem_candidates, "SOURCE_KINDS", problem_candidates.SOURCE_KINDS + ("phenomenon",)):
            candidate["source"]["kind"] = "phenomenon"
            candidate["id"] = "phenomenon"
            self.mutate(problem_candidates.record_candidate, candidate)
            candidate["id"] = "missing-provenance"
            candidate["source"]["evidence"] = evidence[1:]
            self.assert_error("problem_candidate_forming_missing", lambda: self.mutate(problem_candidates.record_candidate, candidate))
        candidate["source"]["kind"] = "own_record"
        candidate["source"]["evidence"][0]["digest"] = "0" * 64
        self.assert_error("strategy_evidence_stale", lambda: self.mutate(problem_candidates.record_candidate, candidate))

    def test_status_counts_and_intermediate_views_include_history(self):
        self.record_forming()
        self.record_outcome()
        self.record_outcome(build_outcome("later", standing="without_evidence", disposition={"status": "set_aside",
            "reason": "No evidence emerged.", "form_id": None, "candidate_id": None}))
        report = cli.status_report(self.store)
        self.assertEqual(report["forming"]["calls"], 1)
        self.assertEqual(report["forming"]["forms"], 1)
        self.assertEqual(report["forming"]["outcomes"], 2)
        self.assertEqual(report["forming"]["by_standing"]["without_evidence"], 1)
        self.assertEqual(report["forming"]["by_standing"]["unresolved"], 0)
        self.assertEqual(report["forming"]["by_disposition"]["set_aside"], 1)
        output_views.render_all(self.store)
        root = output_views.find_workspace_root(self.store.root)
        index = json.loads((root / "intermediate/index.json").read_text())
        entries = {entry["path"]: entry for entry in index["files"]}
        for path, kind in (("problem/forming/forming-1.json", "forming_call"), ("problem/form-outcomes/outcome-1.json", "form_outcome")):
            entry = entries[path]
            self.assertEqual(entry["kind"], kind)
            self.assertTrue((root / "intermediate" / path).is_file())
        self.assertEqual(entries["problem/form-outcomes/outcome-1.json"]["superseded_by"], "later")
        self.assertEqual(output_views.PLACEMENT_BY_KIND["forming_call"], "problem_forming")

    def test_unexamined_forms_are_counted_and_neither_command_changes_gates(self):
        before = cli.status_report(self.store)
        self.record_forming()
        after = cli.status_report(self.store)
        self.assertEqual(after["forming"]["without_outcome"], 1)
        self.assertEqual(sum(after["forming"]["by_standing"].values()), 0)
        self.assertEqual(sum(after["forming"]["by_disposition"].values()), 0)
        from research_harness.report_views import status_summary
        self.assertEqual(status_summary(after)["forming"], after["forming"])
        for key in ("ready", "milestone", "obligations", "next"):
            self.assertEqual(before[key], after[key])

    def test_a_call_can_keep_an_early_form_and_its_immediate_correction(self):
        first = build_form()
        corrected = build_form("corrected", previous={"form_id": "form-1", "reason": "Make the bound precise."},
            correction_checks=[{"check_id": "check-1", "was_made": False, "keeps_prediction": True, "chosen_with_outcome": False}])
        self.record_forming(build_forming(forms=[first, corrected]))
        saved = self.record_outcome(build_outcome(disposition={"status": "superseded", "reason": "Use the corrected text.",
            "form_id": "corrected", "candidate_id": None}))
        self.assertEqual(saved["disposition"]["form_id"], "corrected")

    def test_no_outcome_can_contradict_a_check_and_a_candidate_can_take_up_a_form(self):
        form = build_form(planned_checks=[{"id": "check-1", "description": "Inspect the asymptotic pattern.",
            "contradiction": None, "no_contradiction_reason": "Finite data cannot contradict this asymptotic statement."}])
        self.record_forming(build_forming(forms=[form]))
        self.mutate(problem_candidates.record_candidate, build_candidate())
        self.record_outcome(build_outcome(disposition={"status": "taken_up_as_candidate", "reason": "Compare it at the slate.",
            "form_id": None, "candidate_id": "candidate-1"}))

    def test_superseding_form_must_be_different_and_recorded_later(self):
        self.record_forming()
        self.record_forming(build_forming("second-call", forms=[build_form("second-form")]))
        for identifier in ("form-1", "second-form"):
            payload = build_outcome(form_id=identifier, disposition={"status": "superseded", "reason": "Use another form.",
                "form_id": "form-1", "candidate_id": None})
            with self.subTest(form_id=identifier):
                self.assert_error("invalid_input", lambda: self.record_outcome(payload))
        self.assertFalse(self.store.snapshot()["records"].get("form_outcome"))

    def test_malformed_evidence_and_reference_ids_are_plain_refusals(self):
        self.record_forming()
        reading = {"kind": "reading", "version_id": "missing", "reading_id": []}
        computation = {"kind": "computation", "execution_id": [], "output_id": "result", "formal_check": None}
        for reference in (reading, computation):
            evidence = self.build_probe_evidence(standing="admissible", reference=reference, counterexample=None)
            self.assert_error("invalid_input", lambda: self.record_outcome(build_outcome(evidence=[evidence])))
        for changes in ({"date": "2026-11-09"}, {"kind": ""}, {"description": ""}, {"counterexample": ""},
                        {"reference": {"kind": "artifact"}}, {"reference": []}):
            evidence = self.build_probe_evidence(**changes)
            self.assert_error("invalid_input", lambda: self.record_outcome(build_outcome(evidence=[evidence])))

    def test_a_later_probe_cannot_be_the_origin_of_an_earlier_form(self):
        self.record_forming()
        self.record_probe()
        payload = build_forming("later-call", forms=[], probes=[
            {"probe_id": "probe-1", "form_id": "form-1", "role": "origin"}])
        self.assert_error("problem_form_origin_late", lambda: self.record_forming(payload))
        payload["probes"][0]["role"] = "observation"
        self.record_forming(payload)

    def test_malformed_formal_policy_is_refused_before_computation_validation(self):
        self.record_forming()
        claim = {"statement": "The finite case holds.", "quantifiers": "n=3", "assumption_ids": [], "proof_policy": [],
                 "scope": {"kind": "named", "name": "one case"}}
        reference = {"kind": "computation", "execution_id": "execution", "output_id": "result",
                     "formal_check": {"claim": claim, "required_deliverables": []}}
        evidence = self.build_probe_evidence(standing="admissible", reference=reference, counterexample=None)
        self.assert_error("invalid_input", lambda: self.record_outcome(build_outcome(evidence=[evidence])))

    def test_additional_forming_refusals_preserve_the_revision(self):
        changes = [lambda p: p.update(forms=[None]), lambda p: p.update(forms={}),
            lambda p: p["forms"][0].update(id=[]), lambda p: p["question"].update(places=None),
            lambda p: p.update(inputs=None), lambda p: p.update(probes=None),
            lambda p: p["amounts"]["nearest_alternatives"].update({"form-1": -1}),
            lambda p: p["amounts"]["expected_relations"].update({"form-1": True}),
            lambda p: p.update(usage={"seconds": "unknown"}),
            lambda p: p["forms"][0]["purpose"].update(form_id="form-1"),
            lambda p: p["forms"][0].update(expected_relations=[{"id": "r", "description": "", "form_ids": []}]),
            lambda p: p["forms"][0].update(expected_relations=[{"id": "r", "description": "A relation", "form_ids": []}] * 2),
            lambda p: p["forms"][0]["planned_checks"].append(copy.deepcopy(p["forms"][0]["planned_checks"][0])),
            lambda p: p["forms"][0].update(previous={"form_id": "form-1", "reason": ""})]
        revision = self.store.revision
        for change in changes:
            bad = build_forming()
            change(bad)
            with self.subTest(payload=bad):
                self.assert_error("invalid_input", lambda: self.record_forming(bad))
        self.assertEqual(self.store.revision, revision)

    def test_both_cli_commands_have_examples_and_the_standard_check_contract(self):
        parser = cli.build_parser()
        for command in ("forming", "form-outcome"):
            args = parser.parse_args([command, "--file", "payload.json", "--expected-revision", "0", "--request-id", "one", "--check"])
            self.assertTrue(args.check)
            self.assertEqual(args.file, "payload.json")
            self.assertIn("id", cli.run(parser.parse_args(["example", command])))
        self.assert_error("orchestration_conflict", lambda: self.mutate(cli.OPERATIONS["intent"], {}))


class ProblemFormingOrchestrationTests(ProblemCase):
    def test_refuses_both_commands_outside_a_problem_before_checking_payload(self):
        for command in ("forming", "form-outcome"):
            self.assertIn(command, cli.OPERATIONS)
            self.assert_error("orchestration_conflict", lambda: self.mutate(cli.OPERATIONS[command], {}))


class ProblemFormingCompatibilityTests(ProblemCase):
    def test_0820_candidate_status_views_and_evidence_keep_their_bytes_and_meaning(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/problem_forming_0820.json").read_text())
        records = fixture["records"]
        snapshot = {"revision": fixture["revision"], "records": records}
        self.initialize_problem()
        with patch.object(self.store, "snapshot", return_value=snapshot), patch.object(self.store, "describe_runtime", return_value={"fixture": "0.82.0"}):
            report = cli.status_report(self.store)
            self.assertEqual(report, fixture["status"])
            from research_harness.report_views import status_summary
            self.assertEqual(status_summary(report), fixture["status_summary"])
        model = output_views.compute_view_model(records, self.artifacts, output_views._ViewInputs({}, {}))
        self.assertEqual({path: {"content": view.content.decode(), "entry": view.entry} for path, view in model.items()}, fixture["views"])
        self.assertEqual(problem_candidates.compute_candidate_set_digest(records), fixture["candidate_digest"])
        self.assertEqual(problem_claims.compute_problem_evidence_digest(records, self.artifacts), fixture["evidence_digest"])
        self.assertEqual(problem_evidence.describe_binding(records, self.artifacts), fixture["binding"])
        self.assertEqual(problem_evidence.describe_evidence(records, self.artifacts), fixture["evidence"])
        with patch.object(self.store, "read_kinds", return_value=(fixture["revision"], "a" * 64, records)):
            output_views.render_all(self.store)
        rendered = {str(path.relative_to(self.root)): path.read_text() for folder in ("final", "intermediate")
                    for path in (self.root / folder).rglob("*") if path.is_file()}
        self.assertEqual(rendered, fixture["rendered_views"])
        candidate = records["problem_candidate"]["candidate-1"]
        payload = {key: value for key, value in candidate.items() if key != "recorded_revision"}
        saved = self.mutate(problem_candidates.record_candidate, payload)["result"]
        self.assertEqual(saved, candidate)
