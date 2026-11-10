"""Forming imports retain verified foreign history without installing records."""

import copy
import json
import unittest
from unittest.mock import patch

from research_harness import cli, problem_accounts, problem_claims, problem_evidence, problem_forming, strategy
from research_harness.evidence import digest
from test_research_problem_claims import ProblemCase
from test_research_problem_candidates import build_candidate
from test_research_problem_forming import build_forming, build_outcome


class FormingImportTests(unittest.TestCase):
    def fixture(self, case_class=ProblemCase):
        case = case_class()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.initialize_problem()
        return case

    def setUp(self):
        self.source = self.fixture()
        self.destination = self.fixture()
        self.call = self.source.mutate(problem_forming.record_forming, build_forming())["result"]
        candidate = build_candidate()
        candidate["source"]["evidence"] = [self.reference("forming_call", self.call)]
        self.source.mutate(cli.OPERATIONS["problem-candidate"], candidate)
        self.source.mutate(problem_forming.record_form_outcome, build_outcome())

    def reference(self, kind, record, origin=None):
        value = dict(kind="record", record_kind=kind, id=record["id"], digest=digest(record))
        if origin is not None:
            value["origin"] = origin
        return value

    def import_history(self, destination=None, source=None, identifier="import-1", **changes):
        destination = destination or self.destination
        source = source or self.source
        return destination.mutate(cli.OPERATIONS["forming-import"], dict(dict(id=identifier,
            source_workspace=str(source.root), source_revision=source.store.revision,
            source_candidate_ids=["candidate-1"]), **changes))["result"]

    def test_snapshot_manifest_and_candidate_citation(self):
        from research_harness.problem_forming_import import verify_imports
        imported = self.import_history()
        records = self.destination.store.snapshot()["records"]
        self.assertEqual(set(records["problem_forming_import"]), {"import-1"})
        self.assertNotIn("forming_call", records)
        self.assertEqual(verify_imports(records, self.destination.artifacts), 1)
        member = next(row for row in imported["members"] if row["record_kind"] == "forming_call")
        self.assertEqual(member["date"], self.call["recorded_at"])
        reference = self.reference("forming_call", self.call, imported["source_origin"])
        candidate = build_candidate()
        candidate["source"].update(kind="phenomenon", evidence=[reference])
        saved = self.destination.mutate(cli.OPERATIONS["problem-candidate"], candidate)["result"]
        self.assertEqual(saved["source"]["evidence"], [reference])
        self.assertNotIn("forming_call", self.destination.store.snapshot()["records"])

    def test_reports_name_both_ends_and_keep_inherited_history_and_counts_apart(self):
        from research_harness import problem_reports
        self.source.mutate(problem_forming.record_form_outcome, build_outcome("closed", disposition=dict(status="set_aside",
            reason="The candidate is taken up in the destination.", form_id=None, candidate_id=None)))
        self.source.put("problem_slate", "slate", dict(id="slate", candidate_ids=["candidate-1"], selected=["candidate-1"],
            deferred=[], rejected=[], recorded_revision=self.source.store.revision + 1))
        transfer = self.source.mutate(cli.OPERATIONS["problem-candidate-transfer"], dict(id="transfer", candidate_id="candidate-1",
            destination_workspace=str(self.destination.root), reason="The main weight belongs to this subject class."))["result"]
        before = self.source.store.snapshot()
        imported = self.import_history()
        self.assertEqual(self.source.store.snapshot(), before)
        payload = dict(id="report", reopen_when="A new construction.")
        source = self.source.mutate(cli.OPERATIONS["problem-report"], payload)["result"]
        destination = self.destination.mutate(cli.OPERATIONS["problem-report"], payload)["result"]
        self.assertTrue(problem_reports.is_report_due(self.source.store.snapshot()["records"]))
        self.assertEqual(source["candidates"][0]["ending"], "taken up in another record")
        self.assertEqual(source["candidates"][0]["transfers"], [transfer])
        self.assertEqual(destination["imports"], [imported])
        self.assertEqual(destination["imports"][0]["source_workspace"], str(self.source.root.resolve()))
        self.assertEqual(destination["imports"][0]["forming"]["forms"], 1)
        self.assertEqual(destination["imports"][0]["members"][0]["origin"], imported["source_origin"])
        self.assertEqual(destination["counts"]["forming"]["forms"], 0)
        self.assertEqual(destination["forms"], [])
        self.assertEqual(problem_reports.describe_report_advisories(self.destination.store.snapshot()["records"]), [])
        from research_harness.problem_forming_import import require_fresh_sources
        require_fresh_sources(self.destination.store.snapshot()["records"], self.destination.artifacts)

    def test_real_import_and_refresh_stale_the_destination_report(self):
        from research_harness import problem_reports
        self.destination.mutate(cli.OPERATIONS["problem-report"], dict(id="before", reopen_when="New evidence."))
        self.import_history()
        records = self.destination.store.snapshot()["records"]
        self.assertEqual(problem_reports.describe_report_advisories(records)[0]["code"], "problem_report_stale")
        self.destination.mutate(cli.OPERATIONS["problem-report"], dict(id="after", reopen_when="New evidence."))
        self.source.mutate(problem_forming.record_form_outcome, build_outcome("later"))
        self.import_history(identifier="refresh")
        self.assertEqual(problem_reports.describe_report_advisories(self.destination.store.snapshot()["records"])[0]["code"],
                         "problem_report_stale")

    def test_refuses_revision_self_missing_candidate_and_invalid_payload(self):
        destination = self.destination
        payload = dict(id="bad", source_workspace=str(self.source.root),
                       source_revision=self.source.store.revision, source_candidate_ids=["candidate-1"])
        for changes, code in ((dict(source_revision=0), "problem_forming_source_revision_mismatch"),
                              (dict(source_workspace=str(destination.root)), "problem_forming_self_import"),
                              (dict(source_candidate_ids=["missing"]), "problem_candidate_missing"),
                              (dict(source_candidate_ids=[]), "invalid_input"),
                              (dict(source_workspace="relative"), "invalid_input"),
                              (dict(source_revision=True), "invalid_input")):
            with self.subTest(changes=changes):
                destination.assert_error(code, lambda: destination.mutate(cli.OPERATIONS["forming-import"], dict(payload, **changes)))
        self.assertNotIn("problem_forming_import", destination.store.snapshot()["records"])

    def test_snapshot_is_usable_offline_and_source_changes_are_reported(self):
        from research_harness.problem_forming_import import require_fresh_sources, describe_imports
        imported = self.import_history()
        records = self.destination.store.snapshot()["records"]
        require_fresh_sources(records, self.destination.artifacts)
        self.source.put("unrelated", "x", {"text": "No forming material changed."})
        require_fresh_sources(records, self.destination.artifacts)
        self.source.mutate(problem_forming.record_form_outcome, build_outcome("later"))
        self.destination.assert_error("problem_forming_refresh_required", lambda: require_fresh_sources(records, self.destination.artifacts))
        moved = self.source.root.with_name(self.source.root.name + "-moved")
        self.source.root.rename(moved)
        self.addCleanup(lambda: moved.rename(self.source.root))
        self.destination.assert_error("problem_forming_source_unavailable", lambda: require_fresh_sources(records, self.destination.artifacts))
        delivered = describe_imports(records, self.destination.artifacts)
        self.assertEqual(delivered[0]["source_origin"], imported["source_origin"])
        self.assertEqual(next(row["record"] for row in delivered[0]["records"]
                              if row["cite"]["record_kind"] == "forming_call"), self.call)

    def test_tampered_manifest_or_snapshot_is_refused(self):
        from research_harness.problem_forming_import import verify_imports
        self.import_history()
        original = self.destination.store.snapshot()["records"]
        for field, replacement in (("members", []), ("source_origin", "false-origin"), ("source_revision", 0)):
            records = copy.deepcopy(original)
            records["problem_forming_import"]["import-1"][field] = replacement
            self.destination.assert_error("problem_forming_provenance_invalid", lambda: verify_imports(records, self.destination.artifacts))

    def test_import_example_is_registered(self):
        payload = cli.run(cli.build_parser().parse_args(["example", "forming-import"]))
        self.assertEqual(set(payload), {"id", "source_workspace", "source_revision", "source_candidate_ids"})

    def test_outcome_and_account_entry_cite_the_original_admissible_outcome(self):
        from test_problem_forming_evidence import build_evidence
        reference = self.source.record_full_reading()
        outcome = self.source.mutate(problem_forming.record_form_outcome, build_outcome('proved', standing='proved',
            evidence=[build_evidence(reference)]))["result"]
        imported = self.import_history()
        reference = self.reference('form_outcome', outcome, imported['source_origin'])
        self.destination.mutate(problem_forming.record_forming, build_forming())
        saved = self.destination.mutate(problem_forming.record_form_outcome, build_outcome(standing='proved',
            evidence=[build_evidence(reference)]))["result"]
        self.assertEqual(saved['evidence'][0]['reference'], reference)
        self.destination.select_statement()
        value = self.destination.build_value([reference])
        self.destination.mutate(problem_claims.record_value, value)
        records = self.destination.store.snapshot()['records']
        delivered = problem_evidence.describe_evidence(records, self.destination.artifacts)
        member = next(row for row in delivered['account_records'] if row['cite'] == reference)
        self.assertEqual(member['record'], outcome)
        self.assertNotIn('reading', records)
        self.assertNotIn('work', records)

    def test_wrong_origin_missing_id_and_changed_digest_are_refused(self):
        imported = self.import_history()
        reference = self.reference('forming_call', self.call, imported['source_origin'])
        records = self.destination.store.snapshot()['records']
        for origin in (None, '', [], True):
            self.destination.assert_error('invalid_strategy', lambda: strategy.evidence_fields(dict(reference, origin=origin), review_binding=True))
        for changes, code in ((dict(origin='missing'), 'problem_forming_reference_missing'),
                              (dict(id='missing'), 'problem_forming_reference_missing'),
                              (dict(digest='0' * 64), 'strategy_evidence_stale')):
            with self.subTest(changes=changes):
                self.destination.assert_error(code, lambda: strategy.evidence(records, self.destination.artifacts, [dict(reference, **changes)]))
        self.destination.assert_error('problem_forming_evidence_invalid', lambda: problem_accounts.validate_account_evidence(
            records, self.destination.artifacts, dict(answer_evidence=[], boundaries=[], explanatory_use=[], relations=[]),
            {'entry': dict(id='entry', evidence=[reference])}, self.destination.store.revision + 1))

    def test_probe_keeps_exploratory_standing_in_outcomes_and_accounts(self):
        from test_research_probe_lifecycle import build_probe_intent, build_probe_outcome
        from test_problem_forming_evidence import build_evidence
        self.source.mutate(cli.OPERATIONS['probe-intent'], build_probe_intent(self.source.artifacts))
        probe = self.source.mutate(cli.OPERATIONS['probe'], build_probe_outcome(self.source.artifacts))['result']
        source_outcome = self.source.mutate(problem_forming.record_form_outcome, build_outcome('exploratory-refutation', standing='refuted',
            evidence=[dict(build_evidence(dict(kind='probe', id=probe['id'])), standing='exploratory', counterexample='B(3)=10 > 9.')]))['result']
        imported = self.import_history()
        reference = self.reference('probe', probe, imported['source_origin'])
        self.destination.mutate(problem_forming.record_forming, build_forming())
        evidence = dict(build_evidence(reference), standing='exploratory', counterexample='B(3)=10 > 9.')
        self.destination.mutate(problem_forming.record_form_outcome, build_outcome(standing='refuted', evidence=[evidence]))
        outcome_reference = self.reference('form_outcome', source_outcome, imported['source_origin'])
        self.destination.mutate(problem_forming.record_form_outcome, build_outcome('imported-exploratory-outcome', standing='refuted',
            evidence=[dict(evidence, reference=outcome_reference)]))
        self.destination.assert_error('problem_form_evidence_standing', lambda: self.destination.mutate(problem_forming.record_form_outcome,
            build_outcome('wrong-standing', evidence=[dict(evidence, standing='admissible')])))
        records = self.destination.store.snapshot()['records']
        accounts = dict(answer_evidence=[], boundaries=[], explanatory_use=[], relations=[], checks=[dict(id='check')])
        entries = {'check': dict(id='check', standing='observation', evidence=[reference])}
        problem_accounts.validate_account_evidence(records, self.destination.artifacts, accounts, entries, self.destination.store.revision + 1)
        entries['check']['standing'] = 'test'
        self.destination.assert_error('problem_forming_evidence_invalid', lambda: problem_accounts.validate_account_evidence(
            records, self.destination.artifacts, accounts, entries, self.destination.store.revision + 1))

    def test_reviewers_resolve_foreign_and_native_ids_separately(self):
        from research_harness import review_protocol
        imported = self.import_history()
        native = self.destination.mutate(problem_forming.record_forming, build_forming())['result']
        reference = self.reference('forming_call', self.call, imported['source_origin'])
        records = self.destination.store.snapshot()['records']
        evidence = problem_evidence.describe_evidence(records, self.destination.artifacts)
        evidence['account_records'].append(dict(record=native, cite=self.reference('forming_call', native)))
        packet = dict(problem_evidence=evidence)
        carried = review_protocol.collect_carried_records(records, self.destination.artifacts, None, packets=[packet])
        for frozen in (False, True):
            found = review_protocol.find_carried_record(records, carried, reference, frozen=frozen)
            self.assertEqual(found[0]['record'], self.call)
            self.assertEqual(found[0]['origin'], imported['source_origin'])
            self.assertIsNone(review_protocol.find_carried_record(records, carried, dict(reference, origin='other'), frozen=frozen))
            found = review_protocol.find_carried_record(records, carried, self.reference('forming_call', native), frozen=frozen)
            self.assertEqual(found[0], native)

    def test_chains_keep_earlier_origins_and_do_not_double_count_repeated_imports(self):
        from research_harness.problem_forming_import import collect_inherited, describe_imports, require_fresh_sources
        from research_harness.problem_account_derivations import describe_account_derivations
        first = self.import_history()
        candidate = build_candidate()
        candidate['source']['evidence'] = [self.reference('forming_call', self.call, first['source_origin'])]
        self.destination.mutate(cli.OPERATIONS['problem-candidate'], candidate)
        third = self.fixture()
        self.import_history(third, self.destination)
        chained = third.store.snapshot()['records']
        self.assertEqual(len(describe_imports(chained, third.artifacts)), 2)
        strategy.evidence(chained, third.artifacts, [self.reference('forming_call', self.call, first['source_origin'])])
        self.import_history(third, self.source, identifier='direct')
        records = third.store.snapshot()['records']
        inherited = collect_inherited(records)
        self.assertEqual(len(inherited), 2)
        self.assertEqual(inherited[first['source_origin']]['counts']['exploration']['forms'], 1)
        self.assertEqual(len(describe_imports(records, third.artifacts)), 2)
        statement = third.select_statement()
        derived = describe_account_derivations(third.store.snapshot()['records'], dict(statement_id=statement['id'], accounts=dict(answer=dict(id='answer'), relations=[])))
        self.assertEqual(sum(row['counts']['exploration']['forms'] for row in derived['inherited']), 1)
        self.source.mutate(problem_forming.record_form_outcome, build_outcome('new'))
        third.assert_error('problem_forming_refresh_required', lambda: require_fresh_sources(records, third.artifacts))

    def test_refresh_preserves_the_old_snapshot_and_replays_the_receipt(self):
        from research_harness.problem_forming_import import require_fresh_sources
        first = self.import_history()
        self.source.mutate(problem_forming.record_form_outcome, build_outcome('later'))
        second = self.import_history(identifier='refresh')
        self.assertEqual(first['proof']['revision'] + 1, second['proof']['revision'])
        records = self.destination.store.snapshot()['records']
        require_fresh_sources(records, self.destination.artifacts)
        self.assertEqual(records['problem_forming_import']['import-1'], first)
        self.destination.assert_error('record_conflict', lambda: self.import_history())
        payload = dict(id='receipt', source_workspace=str(self.source.root), source_revision=self.source.store.revision, source_candidate_ids=['candidate-1'])
        revision = self.destination.store.revision
        operation = cli.OPERATIONS['forming-import']
        receipt = operation(self.destination.store, payload, expected_revision=revision, request_id='replay')
        self.source.mutate(problem_forming.record_form_outcome, build_outcome('after-receipt'))
        self.assertEqual(operation(self.destination.store, payload, expected_revision=revision, request_id='replay'), receipt)
        self.destination.assert_error('stale_revision', lambda: operation(self.destination.store, payload, expected_revision=revision, request_id='stale'))

    def test_refuses_outside_problem_aliases_cycles_and_too_many_sources(self):
        case = ProblemCase()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.assert_error('orchestration_conflict', lambda: case.mutate(cli.OPERATIONS['forming-import'], {}))
        alias = self.destination.root / 'alias'
        alias.symlink_to(self.destination.root, target_is_directory=True)
        self.destination.assert_error('problem_forming_self_import', lambda: self.import_history(source_workspace=str(alias)))
        loop = self.source.root / 'loop'
        loop.symlink_to(loop)
        self.destination.assert_error('problem_forming_source_unavailable', lambda: self.import_history(source_workspace=str(loop)))
        with patch('research_harness.problem_forming_import.MAX_SOURCES', 0):
            self.destination.assert_error('problem_forming_sources_exceeded', self.import_history)
        first = self.import_history()
        candidate = build_candidate()
        candidate['source']['evidence'] = [self.reference('forming_call', self.call, first['source_origin'])]
        self.destination.mutate(cli.OPERATIONS['problem-candidate'], candidate)
        self.source.assert_error('problem_forming_self_import', lambda: self.import_history(self.source, self.destination))

    def test_staleness_and_unavailability_reach_status_and_the_post_gate(self):
        from research_harness import problem_delivery
        self.import_history()
        self.source.mutate(problem_forming.record_form_outcome, build_outcome('later'))
        records = self.destination.store.snapshot()['records']
        pending = problem_delivery.assess_post(records, self.destination.artifacts)['obligations']
        self.assertIn('problem_forming_refresh_required', {row['code'] for row in pending})
        decision = dict(id='post', action='post', review_ids=[], bar_review_ids=[], reason='Ready.')
        self.destination.assert_error('problem_forming_refresh_required', lambda: self.destination.mutate(cli.OPERATIONS['problem-decision'], decision))
        report = cli.status_report(self.destination.store)
        self.assertIn('problem_forming_refresh_required', json.dumps(report))

    def test_missing_artifact_and_changed_snapshot_protocol_are_refused(self):
        from research_harness.problem_forming_import import verify_imports
        prompt = self.source.artifacts.put(b'Form a bound.', 'text/plain')
        output = self.source.artifacts.put(b'A form.', 'text/plain')
        self.source.mutate(problem_forming.record_forming, build_forming('lane', forms=[], performer=dict(
            kind='model_lane', lane_id='lane', model='model', effort='high', prompt=prompt, output=output)))
        imported = self.import_history()
        records = self.destination.store.snapshot()['records']
        broken = copy.deepcopy(records)
        broken['problem_forming_import']['import-1']['proof']['protocol'] = 'unknown'
        self.destination.assert_error('problem_forming_provenance_invalid', lambda: verify_imports(broken, self.destination.artifacts))
        (self.destination.root / prompt['path']).unlink()
        self.destination.assert_error('artifact_missing', lambda: verify_imports(records, self.destination.artifacts))

    def test_stored_records_without_imports_keep_their_projection(self):
        from pathlib import Path
        from research_harness import output_views
        frozen = json.loads((Path(__file__).parent / 'fixtures/problem_forming_0820.json').read_text())
        records = frozen['records']
        before = copy.deepcopy(records)
        self.assertEqual(problem_evidence.describe_binding(records, self.destination.artifacts), frozen['binding'])
        self.assertEqual(problem_evidence.describe_evidence(records, self.destination.artifacts), frozen['evidence'])
        self.assertEqual(problem_claims.compute_problem_evidence_digest(records, self.destination.artifacts), frozen['evidence_digest'])
        views = output_views.compute_view_model(records, self.destination.artifacts, output_views._ViewInputs({}, {}))
        self.assertEqual({path: dict(content=view.content.decode(), entry=view.entry) for path, view in views.items()}, frozen['views'])
        self.assertEqual(records, before)

    def test_runs_assessments_searches_counts_and_earlier_questions_are_inherited(self):
        import test_problem_accounts_tests as fixtures
        from research_harness.problem_account_derivations import describe_earlier_computations, describe_account_derivations
        from test_problem_forming_evidence import build_evidence
        source = fixtures.AccountTestCreditTests()
        source.setUp()
        self.addCleanup(source.doCleanups)
        source.record()
        source.mutate(problem_forming.record_form_outcome, build_outcome(standing='supported', evidence=[build_evidence(dict(
            kind='computation', execution_id=source.observed['id'], output_id='result', formal_check=None))]))
        imported = self.import_history(source=source, source_candidate_ids=['candidate-history'])
        kinds = {row['record_kind'] for row in imported['members']}
        self.assertTrue({'forming_call', 'form_outcome', 'cycle_plan', 'execution', 'cycle_assessment', 'literature_search'} <= kinds)
        run = next(row for row in imported['members'] if row['record_kind'] == 'execution')
        self.assertEqual(run['standing']['checks'], [dict(id='test', standing='test', passed=True)])
        execution = source.store.snapshot()['records']['execution'][source.observed['id']]
        reference = self.reference('execution', execution, imported['source_origin'])
        self.destination.select_statement()
        self.destination.mutate(problem_claims.record_value, self.destination.build_value([reference]))
        records = self.destination.store.snapshot()['records']
        earlier = describe_earlier_computations(records, dict(planned_revision=self.destination.store.revision + 1))
        self.assertEqual([(row['origin'], row['question']) for row in earlier], [(imported['source_origin'], source.planned['payload']['question'])])
        value = problem_claims.find_value(records)
        derived = describe_account_derivations(records, value)
        self.assertEqual(derived['counts']['exploration']['runs'], 0)
        self.assertEqual(derived['inherited'][0]['counts']['exploration']['runs'], 1)
        self.assertEqual(derived['inherited'][0]['checks']['current'][0]['test_ids'], ['test'])
        self.assertEqual(derived['inherited'][0]['checks']['current'][0]['failure_signals'][0]['status'], 'not_observed')
        delivered = problem_evidence.describe_evidence(records, self.destination.artifacts)
        self.assertTrue(kinds <= {row['cite']['record_kind'] for row in delivered['account_records']})

    def test_solver_snapshot_claim_and_original_result_are_delivered(self):
        from test_research_problem_objective_results import build_objective_native, build_objective_result
        from test_research_problem_attempt import put_native_attempt
        from test_problem_forming_evidence import build_evidence
        native = build_objective_native('counterexample')
        identifier = put_native_attempt(self.source, native)
        outcome = self.source.mutate(problem_forming.record_form_outcome, build_outcome('refuted', standing='refuted',
            evidence=[build_evidence(build_objective_result(identifier))]))['result']
        imported = self.import_history()
        stored = self.source.store.snapshot()['records']['native_attempt'][identifier]
        reference = dict(kind='record', record_kind='native_attempt', id=identifier, digest=digest(stored), origin=imported['source_origin'])
        from research_harness.problem_forming_import import describe_imports, validate_imported_evidence
        self.assertEqual(validate_imported_evidence(self.destination.store.snapshot()['records'], self.destination.artifacts, reference), 'admissible')
        rows = describe_imports(self.destination.store.snapshot()['records'], self.destination.artifacts)[0]['records']
        self.assertEqual(next(row['record'] for row in rows if row['cite']['record_kind'] == 'native_attempt')['contract']['original_claim'], native['contract']['original_claim'])
        self.assertEqual(next(row['record'] for row in rows if row['cite']['id'] == outcome['id']), outcome)
        later = copy.deepcopy(native)
        later['state']['last_reason'] = 'A later snapshot.'
        put_native_attempt(self.source, later)
        from research_harness.problem_forming_import import require_fresh_sources
        self.destination.assert_error('problem_forming_refresh_required', lambda: require_fresh_sources(self.destination.store.snapshot()['records'], self.destination.artifacts))

    def test_import_refuses_a_source_with_missing_performer_bytes(self):
        prompt = self.source.artifacts.put(b'Propose a form.', 'text/plain')
        output = self.source.artifacts.put(b'Form.', 'text/plain')
        self.source.mutate(problem_forming.record_forming, build_forming('model', forms=[], performer=dict(
            kind='model_lane', lane_id='lane', model='model', effort='high', prompt=prompt, output=output)))
        (self.source.root / prompt['path']).unlink()
        self.destination.assert_error('artifact_missing', self.import_history)

    def test_offline_record_check_authenticates_the_import(self):
        from research_harness.record_check import check_integrity
        self.import_history()
        records = self.destination.store.snapshot()['records']
        result = check_integrity(self.destination.store)
        self.assertEqual(result['problem_forming_imports'], 1)

    def test_refresh_keeps_each_computations_first_import_before_the_plan(self):
        from research_harness.problem_account_derivations import describe_earlier_computations
        from test_research_probe_lifecycle import build_probe_intent, build_probe_outcome
        self.source.mutate(cli.OPERATIONS['probe-intent'], build_probe_intent(self.source.artifacts))
        self.source.mutate(cli.OPERATIONS['probe'], build_probe_outcome(self.source.artifacts))
        self.import_history()
        plan = dict(planned_revision=self.destination.store.revision + 1)
        before = describe_earlier_computations(self.destination.store.snapshot()['records'], plan)
        self.source.mutate(cli.OPERATIONS['probe-intent'], build_probe_intent(self.source.artifacts, 'later-probe'))
        self.source.mutate(cli.OPERATIONS['probe'], build_probe_outcome(self.source.artifacts, 'later-probe'))
        self.import_history(identifier='refresh')
        after = describe_earlier_computations(self.destination.store.snapshot()['records'], plan)
        self.assertEqual(after, before)
        self.assertEqual([row['id'] for row in after], ['probe-1'])

    def test_empty_imported_forming_call_cannot_supply_the_required_history(self):
        empty = self.source.mutate(problem_forming.record_forming, build_forming('empty', forms=[]))['result']
        imported = self.import_history()
        candidate = build_candidate()
        candidate['source']['evidence'] = [self.reference('forming_call', empty, imported['source_origin'])]
        self.destination.mutate(cli.OPERATIONS['problem-candidate'], candidate)
        statement = self.destination.select_statement(candidate_id=candidate['id'])
        records = self.destination.store.snapshot()['records']
        self.destination.assert_error('problem_accounts_history_missing', lambda: problem_accounts.require_prior_form(
            records, statement, self.destination.store.revision + 1))

    def test_imported_gain_uses_original_form_and_result_ordering(self):
        from test_research_problem_objective_results import build_objective_native, build_objective_result
        from test_research_problem_attempt import put_native_attempt
        from test_problem_forming_evidence import build_evidence
        from problem_accounts_fixtures import build_relation
        identifier = put_native_attempt(self.source, build_objective_native())
        outcome = self.source.mutate(problem_forming.record_form_outcome, build_outcome('established', standing='proved',
            evidence=[build_evidence(build_objective_result(identifier))]))['result']
        imported = self.import_history()
        candidate = build_candidate()
        candidate['source']['evidence'] = [self.reference('forming_call', self.call, imported['source_origin'])]
        self.destination.mutate(cli.OPERATIONS['problem-candidate'], candidate)
        self.destination.select_statement(candidate_id=candidate['id'])
        reference = self.reference('form_outcome', outcome, imported['source_origin'])
        value = self.destination.build_value([reference])
        from research_harness import literature
        search = self.destination.capture_search()
        search['question'] = 'What earlier statements give this advantage?'
        self.destination.mutate(literature.record_search, search)
        value['accounts'].update(checks=[], search_coverage=[dict(id='coverage', purpose='earlier_statements', search_ids=[search['id']], unreached='Private notes.')], gain=dict(id='gain', form='proved_advantage',
            claim='The accepted reduction gives the stated advantage.', relation_ids=['gathered'], separate_grounds=[],
            conjunction=dict(status='not_known', description='No converse is claimed.', evidence_ids=[]),
            establishing_ids=['use'], new_object_justification=None))
        value['accounts']['relations'] = [build_relation('gathered', standing='established', evidence_ids=['use'],
            consequence=dict(assertion='The common reduction.', instances_or_translation='The stated family.',
                             credited_to='statement', weaker_derivations=[]))]
        saved = self.destination.mutate(problem_claims.record_value, value)['result']
        self.assertEqual(saved['accounts']['gain']['id'], 'gain')
        from test_research_problem_forming import build_form
        late = self.source.mutate(problem_forming.record_forming, build_forming('late', forms=[build_form('late-form')]))['result']
        refreshed = self.import_history(identifier='refresh')
        candidate = build_candidate('too-late')
        candidate['source']['evidence'] = [self.reference('forming_call', late, refreshed['source_origin'])]
        self.destination.mutate(cli.OPERATIONS['problem-candidate'], candidate)
        statement = self.destination.select_statement(identifier='later-statement', candidate_id=candidate['id'])
        value.update(id='late-gain', statement_id=statement['id'])
        self.destination.assert_error('problem_accounts_history_missing', lambda: self.destination.mutate(problem_claims.record_value, value))

    def test_real_slate_packet_carries_the_candidates_imported_provenance(self):
        import test_research_problem_slate as fixtures
        from research_harness import review_protocol
        destination = fixtures.ProblemSlateTests()
        destination.setUp()
        self.addCleanup(destination.doCleanups)
        imported = self.import_history(destination=destination)
        reference = self.reference('forming_call', self.call, imported['source_origin'])
        candidate = build_candidate()
        candidate['source'].update(kind='phenomenon', evidence=[reference])
        destination.mutate(cli.OPERATIONS['problem-candidate'], candidate)
        destination.observe_bars()
        assignment = destination.assign('slate', 'first')
        packet = json.loads(destination.artifacts.read(assignment['packet']))
        records = destination.store.snapshot()['records']
        carried = review_protocol.collect_carried_records(records, destination.artifacts, None, packets=[packet])
        found = review_protocol.find_carried_record(records, carried, reference, frozen=True)
        self.assertIsNotNone(found)
        self.assertEqual(found[0]['record'], self.call)

    def test_slate_import_delivers_the_probe_program_and_keeps_unread_outputs_sealed(self):
        import test_research_problem_slate as fixtures
        from test_research_program_probes import ProgramProbeCase

        class Source(ProgramProbeCase, ProblemCase):
            pass

        source = self.fixture(Source)
        call = source.mutate(problem_forming.record_forming, build_forming())['result']
        source.mutate(cli.OPERATIONS['problem-candidate'], build_candidate())
        intent, outcome, _ = source.record_program_probe()
        destination = fixtures.ProblemSlateTests()
        destination.setUp()
        self.addCleanup(destination.doCleanups)
        imported = self.import_history(destination=destination, source=source)
        candidate = build_candidate()
        candidate['source'].update(kind='phenomenon', evidence=[self.reference('forming_call', call, imported['source_origin'])])
        destination.mutate(cli.OPERATIONS['problem-candidate'], candidate)
        destination.observe_bars()
        packet = json.loads(destination.artifacts.read(destination.assign('slate', 'first')['packet']))
        delivered = [entry['artifact'] for entry in packet['evidence']]
        self.assertIn(intent['program'], delivered)
        for reference in outcome['outputs'] + [outcome['stdout'], outcome['stderr']]:
            self.assertNotIn(reference, delivered)
            destination.artifacts.verify_sealed(reference)
        rows = packet['problem_forming_imports'][0]['records']
        saved = next(row['record'] for row in rows if row['cite']['record_kind'] == 'probe')
        self.assertTrue(saved['program_run']['outputs_unread'])

    def test_parts_and_adjudicator_citation_inventories_keep_the_origin(self):
        from research_harness import review_parts_problem_result, review_parts_adjudicator
        imported = self.import_history()
        reference = self.reference('forming_call', self.call, imported['source_origin'])
        source = dict(item=dict(record=self.call, cite=reference), bodies=[],
                      occurrences=[dict(path=['problem_evidence', 'account_records'], index=0)])
        for adapter in (review_parts_problem_result, review_parts_adjudicator):
            with self.subTest(adapter=adapter.__name__):
                self.assertEqual(adapter.list_citations(source), [reference])

    def test_imported_projection_is_required_even_beside_a_stored_value(self):
        import problem_parts_fixtures as fixtures
        imported = self.import_history()
        reference = self.reference('forming_call', self.call, imported['source_origin'])
        case = fixtures.ResultPartsCase()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.case.expected['forming_imports'] = [dict(source_origin=imported['source_origin'], source_revision=imported['source_revision'])]
        case.case.expected['account_records'] = [dict(record=self.call, cite=reference)]
        search = dict(id='import-search', queries=['The source forming history.'])
        case.case.put('literature_search', search['id'], search)
        case.case.expected['openness_searches'] = [dict(search=search, sources=[])]
        assignment = case.assign()
        review = case.record_final(assignment)
        self.assertEqual(case.coverage(review), [])
        changed = copy.deepcopy(case.case.expected)
        changed['account_records'][0]['record']['stopping_reason'] = 'A different history.'
        pending = case.coverage(review, changed)
        self.assertEqual([row['code'] for row in pending], ['problem_evidence_unassessed'])

    def test_observed_result_review_can_cite_the_foreign_record(self):
        import test_research_problem_delivery as fixtures
        from research_harness import research_decisions
        destination = fixtures.ProblemDeliveryCase()
        destination.setUp()
        self.addCleanup(destination.doCleanups)
        destination.prepare_ready()
        imported = self.import_history(destination=destination)
        reference = self.reference('forming_call', self.call, imported['source_origin'])
        output = destination.build_review_output('result')
        output['value']['evidence'] = [reference]
        review = destination.observe('result', 'first', output=output, identifier='imported-result')
        records = destination.store.snapshot()['records']
        self.assertEqual(research_decisions.find_undelivered_review_references(records, destination.artifacts, review), [])
        expected = problem_evidence.describe_evidence(records, destination.artifacts)
        self.assertEqual(research_decisions._assess_problem_coverage(records, destination.artifacts, [review], expected,
            problem_claims.compute_problem_evidence_digest(records, destination.artifacts)), [])

    def test_check_mode_prepares_the_import_without_writing_either_store(self):
        from research_harness.check_mode import check_mutation
        payload = dict(id='checked', source_workspace=str(self.source.root), source_revision=self.source.store.revision,
                       source_candidate_ids=['candidate-1'])
        before_source = self.source.store.snapshot()
        before_destination = self.destination.store.snapshot()
        checked = check_mutation(self.destination.store, cli.OPERATIONS['forming-import'], payload,
            expected_revision=self.destination.store.revision, request_id='check', bounded_payload=payload)
        self.assertEqual(checked['check'], 'accepted')
        self.assertFalse(checked['recorded'])
        self.assertEqual(self.source.store.snapshot(), before_source)
        self.assertEqual(self.destination.store.snapshot(), before_destination)

    def test_source_lineage_counts_exclude_runs_of_an_unrelated_candidate(self):
        import test_problem_accounts_tests as fixtures
        source = fixtures.AccountTestCreditTests()
        source.setUp()
        self.addCleanup(source.doCleanups)
        source.record()
        source.mutate(cli.OPERATIONS['problem-candidate'], build_candidate('other'))
        other = source.select_statement(identifier='other-statement', candidate_id='other', statement='Does another bound hold?')
        records = source.store.snapshot()['records']
        plan = copy.deepcopy(records['cycle_plan']['cycle-1'])
        plan['id'] = 'unrelated-plan'
        plan['payload'].update(id='unrelated-plan', objective=dict(id=other['id'], statement=other['statement']))
        plan['payload'].pop('test_declaration')
        plan['planned_revision'] = source.store.revision + 1
        source.put('cycle_plan', 'unrelated-plan', plan)
        run = copy.deepcopy(records['execution'][source.observed['id']])
        run['id'] = 'unrelated-run'
        run['payload'].update(id=run['id'], cycle_id='unrelated-plan')
        run['recorded_revision'] = source.store.revision + 1
        source.put('execution', run['id'], run)
        imported = self.import_history(source=source, source_candidate_ids=['candidate-history'])
        self.assertEqual(imported['counts']['exploration']['runs'], 2)
        self.assertEqual(imported['counts']['lineage']['runs'], 1)

    def test_an_older_source_copy_cannot_hide_a_newer_retained_projection(self):
        import shutil
        from types import SimpleNamespace
        from research_harness.storage import Store
        from research_harness.problem_forming_import import describe_imports, require_fresh_sources
        clone = self.source.root.parent / 'older-source'
        shutil.copytree(self.source.root, clone)
        self.source.mutate(problem_forming.record_form_outcome, build_outcome('newer-outcome'))
        newer = self.import_history()
        self.import_history(source=SimpleNamespace(root=clone, store=Store(clone)), identifier='older')
        delivered = describe_imports(self.destination.store.snapshot()['records'], self.destination.artifacts)
        self.assertEqual(delivered[0]['source_revision'], newer['source_revision'])
        self.assertIn('newer-outcome', {row['cite']['id'] for row in delivered[0]['records']})
        self.source.mutate(problem_forming.record_form_outcome, build_outcome('after-copy'))
        self.destination.assert_error('problem_forming_refresh_required', lambda: require_fresh_sources(
            self.destination.store.snapshot()['records'], self.destination.artifacts))

    def test_an_older_import_chain_cannot_hide_a_delivered_newer_citation(self):
        import shutil
        from types import SimpleNamespace
        from research_harness.storage import Store
        from research_harness.problem_forming_import import describe_imports
        clone = self.source.root.parent / 'older-chain-source'
        shutil.copytree(self.source.root, clone)
        middle = self.fixture()
        self.import_history(destination=middle, source=SimpleNamespace(root=clone, store=Store(clone)))
        middle.mutate(cli.OPERATIONS['problem-candidate'], build_candidate())
        outcome = self.source.mutate(problem_forming.record_form_outcome, build_outcome('newer-outcome'))['result']
        newer = self.import_history()
        self.import_history(source=middle, identifier='older-chain')
        records = self.destination.store.snapshot()['records']
        reference = self.reference('form_outcome', outcome, newer['source_origin'])
        projections = describe_imports(records, self.destination.artifacts)
        self.assertTrue(any(row['cite'] == reference for projection in projections for row in projection['records']))
        self.assertEqual(strategy.evidence(records, self.destination.artifacts, [reference]), [reference])

    def test_source_copies_cannot_give_one_original_id_conflicting_bytes(self):
        import shutil
        from types import SimpleNamespace
        from research_harness.storage import Store
        clone = self.source.root.parent / 'forked-source'
        shutil.copytree(self.source.root, clone)
        clone_store = Store(clone)
        self.source.mutate(problem_forming.record_forming, build_forming('fork', forms=[], stopping_reason='The first branch stopped.'))
        problem_forming.record_forming(clone_store, build_forming('fork', forms=[], stopping_reason='The second branch stopped.'),
            expected_revision=clone_store.revision, request_id='fork')
        self.import_history()
        self.destination.assert_error('problem_forming_origin_conflict', lambda: self.import_history(
            source=SimpleNamespace(root=clone, store=clone_store), identifier='forked-import'))
        self.assertEqual(len(self.destination.store.snapshot()['records']['problem_forming_import']), 1)

    def test_source_history_cannot_backdate_the_creation_revision_of_a_form(self):
        backdated = dict(build_forming('backdated', forms=[]), recorded_revision=self.call['recorded_revision'],
                         recorded_at=self.call['recorded_at'])
        self.source.put('forming_call', backdated['id'], backdated)
        self.destination.assert_error('problem_forming_provenance_invalid', self.import_history)

    def test_an_older_solver_snapshot_keeps_unknown_creation_time(self):
        from test_research_problem_objective_results import build_objective_native, build_objective_result
        from test_problem_forming_evidence import build_evidence
        native = build_objective_native()
        identifier = digest({key: value for key, value in native.items() if key not in ('provenance', 'recorded_revision')})
        self.source.put('native_attempt', identifier, native)
        key = {name: native[name] for name in ('attack_root', 'objective_id')}
        self.source.put('native_attempt_current', digest(key), dict(key, native_attempt_digest=identifier,
            recorded_revision=self.source.store.revision + 1))
        self.source.mutate(problem_forming.record_form_outcome, build_outcome('legacy-result', standing='proved',
            evidence=[build_evidence(build_objective_result(identifier))]))
        imported = self.import_history()
        member = next(row for row in imported['members'] if row['record_kind'] == 'native_attempt')
        self.assertIsNone(member['availability'][0]['revision'])
        from research_harness.problem_forming_import import describe_imports
        delivered = describe_imports(self.destination.store.snapshot()['records'], self.destination.artifacts)
        self.assertEqual(next(row['record'] for row in delivered[0]['records'] if row['cite']['id'] == identifier), native)
