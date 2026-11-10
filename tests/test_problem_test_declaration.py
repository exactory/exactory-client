"""Prospective problem tests are optional, immutable declarations of existing alternatives."""

import base64
import copy
import gzip
import json
from pathlib import Path

from research_harness import development, literature, output_views, problem_claims
from research_harness.evidence import digest
from research_harness.storage import _canonical
from test_research_problem_tools import ProblemToolsCase


class TestDeclarationTests(ProblemToolsCase):
    def record_outcome_search(self):
        search = self.capture_search()
        search["question"] = "Which maximum occurs in the finite square table?"
        self.mutate(literature.record_search, search)
        return search

    def prepare_declaration(self):
        self.prepare_problem()
        selected = self.select_statement(identifier="statement-with-neighbour", statement=self.objective["statement"],
            neighbours=["Every square in the finite range is strictly less than 9."])
        self.objective = {"id": selected["id"], "statement": selected["statement"]}
        search = self.record_outcome_search()
        return {"alternative": {"kind": "neighbour", "text": selected["neighbours"][0]},
                "excluded_outcome": "The maximum is exactly 9.",
                "prior_knowledge": [{"source": {"kind": "planning_model_recall",
                    "description": "The planning model's recollection of this quantity."},
                    "known": "Elementary square values are recalled; this declaration claims no independence."}],
                "search_id": search["id"]}

    def freeze_declaration(self, declaration):
        plan = self.plan()
        plan["test_declaration"] = declaration
        return self.mutate(development.plan_cycle, plan)["result"]

    def assert_declaration_refused(self, code, declaration):
        before = self.store.snapshot()
        self.assert_error(code, lambda: self.freeze_declaration(declaration))
        self.assertEqual(self.store.snapshot(), before)

    def test_neighbour_declaration_and_open_prior_sources_are_frozen_with_plan(self):
        declaration = self.prepare_declaration()
        declaration["prior_knowledge"] = [{"source": {"kind": kind, "description": description}, "known": known}
            for kind, description, known in (
                ("record_computations", "Earlier probes and runs in this record.", "No earlier runs."),
                ("inherited_computations", "Computations inherited from other problem records.", "None inherited."),
                ("external_computations", "Workspace /research/squares, model lane finite-check.", "The finite maximum was computed there."),
                ("literature_and_tables", "Printed tables of squares.", "Small square values occur in tables."),
                ("planning_model_recall", "What the planning model recalls about the quantity.", "Some individual values are familiar."),
                ("unlisted_source", "An open source kind, described in the performer's own words.", "No further knowledge reported."))]
        planned = self.freeze_declaration(declaration)
        self.assertEqual(planned["payload"]["test_declaration"], declaration)
        self.assertEqual(planned["digest"], digest({key: value for key, value in planned.items() if key != "digest"}))
        records = self.store.snapshot()["records"]
        self.assertTrue(development._Context(records, self.artifacts).verify_plan_dependencies(planned))
        self.assertEqual(records["cycle_plan"][planned["id"]], planned)
        self.assertEqual(planned["payload"]["failure_signals"], self.plan()["failure_signals"])

    def test_a_form_is_resolved_inside_the_named_forming_call(self):
        declaration = self.prepare_declaration()
        # Lane A2 supplies the forming command; this lane consumes its flat forms list.
        self.put("forming_call", "forming-1", {"id": "forming-1", "forms": [{"id": "strict-bound"}],
            "recorded_revision": self.store.revision + 1})
        declaration["alternative"] = {"kind": "form", "record_kind": "forming_call", "id": "forming-1",
                                      "form_id": "strict-bound"}
        planned = self.freeze_declaration(declaration)
        self.assertEqual(planned["payload"]["test_declaration"]["alternative"], declaration["alternative"])
        self.assertLess(self.store.snapshot()["records"]["forming_call"]["forming-1"]["recorded_revision"],
                        planned["planned_revision"])

    def test_unknown_call_form_and_wrong_call_are_refused(self):
        declaration = self.prepare_declaration()
        self.put("forming_call", "forming-1", {"id": "forming-1", "forms": [{"id": "strict-bound"}]})
        self.put("forming_call", "forming-2", {"id": "forming-2", "forms": []})
        for call_id, form_id in (("missing", "strict-bound"), ("forming-1", "missing"), ("forming-2", "strict-bound")):
            with self.subTest(call=call_id, form=form_id):
                declaration["alternative"] = {"kind": "form", "record_kind": "forming_call", "id": call_id,
                                              "form_id": form_id}
                self.assert_declaration_refused("problem_test_alternative_missing", declaration)

    def test_form_reference_requires_the_forming_call_kind(self):
        declaration = self.prepare_declaration()
        declaration["alternative"] = {"kind": "form", "record_kind": "problem_statement", "id": self.objective["id"],
                                      "form_id": "strict-bound"}
        self.assert_declaration_refused("invalid_development", declaration)

    def test_neighbour_must_match_the_current_statement_exactly(self):
        declaration = self.prepare_declaration()
        for neighbour in ("Unknown alternative.", declaration["alternative"]["text"] + " "):
            with self.subTest(neighbour=neighbour):
                changed = copy.deepcopy(declaration)
                changed["alternative"]["text"] = neighbour
                self.assert_declaration_refused("problem_test_alternative_missing", changed)
        selected = self.select_statement(identifier="no-neighbours", statement=self.objective["statement"])
        self.objective = {"id": selected["id"], "statement": selected["statement"]}
        self.assert_declaration_refused("problem_test_alternative_missing", declaration)

    def test_missing_search_is_refused(self):
        declaration = self.prepare_declaration()
        declaration["search_id"] = "not-recorded"
        self.assert_declaration_refused("problem_test_search_missing", declaration)

    def test_a_later_backdated_search_cannot_supply_a_plan_at_an_earlier_revision(self):
        declaration = self.prepare_declaration()
        later_search = self.capture_search(purpose="recent")
        declaration["search_id"] = later_search["id"]
        plan = self.plan()
        plan["test_declaration"] = declaration
        revision = self.store.revision
        self.assert_declaration_refused("problem_test_search_missing", declaration)
        # captured_at predates the plan, but the actual search judgment is recorded later.
        self.mutate(literature.record_search, later_search)
        before = self.store.snapshot()
        self.assert_error("stale_revision", lambda: development.plan_cycle(self.store, plan,
            expected_revision=revision, request_id="freeze-before-search"))
        self.assertEqual(self.store.snapshot(), before)
        planned = self.freeze_declaration(declaration)
        self.assertGreater(planned["planned_revision"], revision + 1)

    def test_a_declaration_requires_at_least_one_existing_failure_signal(self):
        declaration = self.prepare_declaration()
        plan = self.plan()
        plan.update(test_declaration=declaration, failure_signals=[])
        before = self.store.snapshot()
        self.assert_error("invalid_development", lambda: self.mutate(development.plan_cycle, plan))
        self.assertEqual(self.store.snapshot(), before)

    def test_a_search_recorded_after_a_frozen_plan_cannot_be_added_to_it(self):
        declaration = self.prepare_declaration()
        later_search = self.capture_search(purpose="recent")
        planned = self.mutate(development.plan_cycle, self.plan())["result"]
        self.mutate(literature.record_search, later_search)
        declaration["search_id"] = later_search["id"]
        changed = dict(planned["payload"], test_declaration=declaration)
        before = self.store.snapshot()
        self.assert_error("request_id_conflict", lambda: development.plan_cycle(self.store, changed,
            expected_revision=planned["planned_revision"] - 1, request_id=planned["request_id"]))
        self.assertEqual(self.store.snapshot(), before)
        self.assertNotIn("test_declaration", before["records"]["cycle_plan"][planned["id"]]["payload"])

    def test_declaration_shapes_and_descriptions_are_checked(self):
        declaration = self.prepare_declaration()
        changes = [None, {**declaration, "unexpected": True}, {**declaration, "excluded_outcome": ""},
                   {**declaration, "alternative": None}, {**declaration, "search_id": ""},
                   {**declaration, "prior_knowledge": "unknown"},
                   {**declaration, "prior_knowledge": [{"source": {"kind": "recall"}, "known": "Unknown."}]},
                   {**declaration, "prior_knowledge": [{"source": {"kind": "", "description": "Recall."}, "known": "Unknown."}]},
                   {**declaration, "prior_knowledge": [{"source": {"kind": "recall", "description": ""}, "known": "Unknown."}]},
                   {**declaration, "prior_knowledge": [{"source": {"kind": "recall", "description": "Recall."}, "known": ""}]},
                   {**declaration, "alternative": {"kind": "other"}}]
        changes.extend({key: value for key, value in declaration.items() if key != missing} for missing in declaration)
        for changed in changes:
            with self.subTest(declaration=changed):
                self.assert_declaration_refused("invalid_development", changed)

    def test_a_test_declaration_requires_at_least_one_prior_knowledge_entry(self):
        declaration = self.prepare_declaration()
        declaration["prior_knowledge"] = []
        self.assert_declaration_refused("invalid_development", declaration)

    def test_a_prior_knowledge_entry_can_state_that_no_outcome_was_known(self):
        declaration = self.prepare_declaration()
        declaration["prior_knowledge"][0]["known"] = "The performer knew nothing about the outcome from this source."
        self.assertEqual(self.freeze_declaration(declaration)["payload"]["test_declaration"], declaration)

    def test_declaration_is_refused_outside_a_problem_record(self):
        self.prepared_study()
        plan = self.plan()
        plan["test_declaration"] = {"alternative": {"kind": "neighbour", "text": "A strict bound."},
            "excluded_outcome": "Equality occurs.", "prior_knowledge": [], "search_id": "direct"}
        before = self.store.snapshot()
        self.assert_error("orchestration_conflict", lambda: self.mutate(development.plan_cycle, plan))
        self.assertEqual(self.store.snapshot(), before)

    def test_admission_and_assessment_preserve_the_frozen_declaration(self):
        declaration = self.prepare_declaration()
        planned = self.freeze_declaration(declaration)
        admission = self.admit()
        observed = self.execution(admission)
        self.mutate(development.record_execution, observed)
        self.mutate(development.assess_cycle, self.assessment(planned["payload"], observed))
        self.assertEqual(self.store.snapshot()["records"]["cycle_plan"][planned["id"]], planned)
        changed = copy.deepcopy(planned["payload"])
        changed["test_declaration"]["excluded_outcome"] = "A different predicted outcome."
        self.assert_error("request_id_conflict", lambda: development.plan_cycle(self.store, changed,
            expected_revision=planned["planned_revision"] - 1, request_id=planned["request_id"]))

    def test_plan_digest_uses_its_named_text_and_criteria_after_statement_revisions(self):
        declaration = self.prepare_declaration()
        planned = self.freeze_declaration(declaration)
        records = self.store.snapshot()["records"]
        expected = problem_claims.compute_statement_digest(problem_claims.find_current_statement(records))
        self.assertEqual(development.compute_plan_statement_digest(records, planned), expected)
        revised = self.select_statement(identifier="revised-neighbours", statement=self.objective["statement"],
            neighbours=["Another alternative."], revises_statement_id=self.objective["id"])
        records = self.store.snapshot()["records"]
        self.assertEqual(problem_claims.compute_statement_digest(revised), expected)
        self.assertEqual(development.compute_plan_statement_digest(records, planned), expected)
        self.select_statement(identifier="revised-criteria", statement=self.objective["statement"],
            resolution_criteria="Prove a strict bound instead.", revises_statement_id=revised["id"])
        records = self.store.snapshot()["records"]
        self.assertNotEqual(problem_claims.compute_statement_digest(problem_claims.find_current_statement(records)), expected)
        self.assertEqual(development.compute_plan_statement_digest(records, planned), expected)


class RevisedStatementTests(ProblemToolsCase):
    def test_a_revision_reference_can_name_any_existing_statement_and_selection_stays_current(self):
        self.initialize_problem()
        first = self.select_statement()
        self.select_statement(identifier="intermediate")
        revised = self.select_statement(identifier="revised", revises_statement_id=first["id"])
        records = self.store.snapshot()["records"]
        self.assertEqual(revised["revises_statement_id"], first["id"])
        self.assertEqual(problem_claims.find_current_statement(records), revised)
        self.assertEqual(records["problem_statement"][first["id"]], first)
        self.assertEqual(problem_claims.compute_statement_digest(first), problem_claims.compute_statement_digest(revised))

    def test_unknown_revision_reference_is_refused_without_selecting_it(self):
        self.initialize_problem()
        self.select_statement()
        before = self.store.snapshot()
        self.assert_error("problem_statement_missing", lambda: self.select_statement(identifier="revised",
            revises_statement_id="missing"))
        self.assertEqual(self.store.snapshot(), before)

    def test_a_revision_reference_must_be_a_statement_id(self):
        self.initialize_problem()
        for identifier in (None, "", {"id": "statement-1"}):
            with self.subTest(identifier=identifier):
                self.assert_error("invalid_input", lambda: self.select_statement(revises_statement_id=identifier))


class StoredPlanCompatibilityTests(ProblemToolsCase):
    def restore_fixture(self, name, *, before_plan=False):
        # Captured on unmodified 0.82.0 (645a791), before this lane changed code.
        path = Path(__file__).parent / "fixtures/problem_run_plans_0_82_0.json.gz"
        fixture = json.loads(gzip.decompress(path.read_bytes()))["cases"][name]
        for relative, encoded in fixture["objects"].items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(encoded))
        events = fixture["events"][:-1] if before_plan else fixture["events"]
        for event in events:
            def apply(transaction):
                for change in event["changes"]:
                    transaction.put(change["kind"], change["key"], change["value"])
                return event["result"]
            self.store.mutate(event["operation"], event["payload"], apply,
                expected_revision=event["revision"] - 1, request_id=event["request_id"])
        self.sequence = self.store.revision
        return fixture

    def assert_legacy_plan_unchanged(self, fixture):
        records = self.store.snapshot()["records"]
        planned = records["cycle_plan"][fixture["plan"]["id"]]
        self.assertNotIn("test_declaration", planned["payload"])
        self.assertEqual(_canonical(planned), fixture["plan_bytes"])
        self.assertEqual(planned["digest"], fixture["plan"]["digest"])
        context = development._Context(records, self.artifacts)
        self.assertTrue(context.verify_plan_dependencies(planned))
        self.assertEqual(context.project_dependencies(planned, "record"), fixture["projection"])
        model = output_views.compute_view_model(records, self.artifacts, output_views._ViewInputs({}, {}))
        views = {path: {"content": base64.b64encode(view.content).decode() if isinstance(view.content, bytes)
                                  else view.content, "entry": view.entry} for path, view in model.items()}
        self.assertEqual(views, fixture["views"])

    def test_stored_problem_plan_keeps_its_bytes_digest_validation_projection_and_views(self):
        fixture = self.restore_fixture("problem")
        self.assert_legacy_plan_unchanged(fixture)

    def test_replaying_a_stored_problem_plan_keeps_its_0820_contract_without_a_declaration(self):
        fixture = self.restore_fixture("problem")
        before = self.store.snapshot()
        planned = development.plan_cycle(self.store, fixture["plan"]["payload"],
            expected_revision=fixture["plan"]["planned_revision"] - 1, request_id=fixture["plan"]["request_id"])["result"]
        self.assertEqual(_canonical(planned), fixture["plan_bytes"])
        self.assertEqual(self.store.snapshot(), before)
        self.assert_legacy_plan_unchanged(fixture)

    def test_stored_ai_science_plan_keeps_its_bytes_digest_validation_projection_and_views(self):
        fixture = self.restore_fixture("study")
        self.assert_legacy_plan_unchanged(fixture)
        records = self.store.snapshot()["records"]
        self.assertTrue(development._Context(records, self.artifacts).managed)
        self.assertIsNone(development.compute_plan_statement_digest(records, fixture["plan"]))

    def test_a_managed_ai_science_plan_refuses_a_test_declaration(self):
        fixture = self.restore_fixture("study", before_plan=True)
        payload = copy.deepcopy(fixture["plan"]["payload"])
        payload["test_declaration"] = {"alternative": {"kind": "neighbour", "text": "A strict bound."},
            "excluded_outcome": "Equality occurs.", "prior_knowledge": [], "search_id": "direct"}
        before = self.store.snapshot()
        self.assert_error("orchestration_conflict", lambda: development.plan_cycle(self.store, payload,
            expected_revision=self.store.revision, request_id="study-declaration"))
        self.assertEqual(self.store.snapshot(), before)

    def test_recording_a_problem_plan_without_a_declaration_matches_0_82_0(self):
        fixture = self.restore_fixture("problem", before_plan=True)
        planned = development.plan_cycle(self.store, fixture["plan"]["payload"],
            expected_revision=self.store.revision, request_id=fixture["plan"]["request_id"])["result"]
        self.assertEqual(_canonical(planned), fixture["plan_bytes"])
        self.assert_legacy_plan_unchanged(fixture)

    def test_recording_an_ai_science_plan_without_a_declaration_matches_0_82_0(self):
        fixture = self.restore_fixture("study", before_plan=True)
        planned = development.plan_cycle(self.store, fixture["plan"]["payload"],
            expected_revision=self.store.revision, request_id=fixture["plan"]["request_id"])["result"]
        self.assertEqual(_canonical(planned), fixture["plan_bytes"])
        self.assert_legacy_plan_unchanged(fixture)

    def test_stored_statement_without_a_revision_reference_keeps_its_old_contract(self):
        fixture = self.restore_fixture("problem")
        payload = {key: value for key, value in fixture["statement"].items() if key != "recorded_revision"}
        payload.pop("formal_statement")
        saved = self.mutate(problem_claims.record_statement, payload)["result"]
        records = self.store.snapshot()["records"]
        self.assertNotIn("revises_statement_id", saved)
        self.assertEqual(_canonical(saved), fixture["statement_bytes"])
        self.assertEqual(problem_claims.find_current_statement(records), saved)
        self.assertEqual(problem_claims.compute_statement_digest(saved), fixture["statement_digest"])
        self.assertEqual(problem_claims.describe_problem_evidence(records, self.artifacts), fixture["problem_evidence"])
        self.assertEqual(problem_claims.compute_problem_evidence_digest(records, self.artifacts), fixture["problem_evidence_digest"])
        self.assert_legacy_plan_unchanged(fixture)
