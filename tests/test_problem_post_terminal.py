"""A recorded delivery remains terminal when later rules find new post defects."""

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_research_problem_delivery as delivery
import test_research_problem_objections as objections
from research_harness import cli, problem_delivery, research_decisions, review_protocol
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from research_harness.gates import gate_state
from research_harness.report_views import next_summary


class PostedProblemTests(delivery.ProblemDeliveryCase):
    make_finding = objections.ProblemObjectionTests.make_finding
    prepare_problem_evidence = objections.ProblemObjectionTests.prepare_problem_evidence
    prepare_reviews = objections.ProblemObjectionTests.prepare_reviews
    observe_adjudication = objections.ProblemObjectionTests.observe_adjudication

    def make_objection(self):
        assessment = self.store.snapshot()["records"]["problem_assessment"]["assessment-1"]
        return dict(objections.ProblemObjectionTests.make_objection(self), evidence=[{
            "kind": "record", "record_kind": "problem_assessment", "id": "assessment-1",
            "digest": digest(assessment),
        }])

    def post_current_statement(self):
        self.record_post_decision()
        exported = problem_delivery.export_problem(self.store)
        return self.mutate(self.load_problem_module().record_posted, {
            "problem_id": "656c336e-e892-4c47-80d2-a71d022f4116",
            "export_directory": exported["destination"],
        })["result"]

    def assert_terminal(self, post, expected_codes):
        before = self.store.snapshot()
        report = cli.status_report(self.store)
        terminal = {
            "code": "problem_already_posted",
            "explanation": "This exact statement already has a recorded post.",
            "post_id": post["id"], "problem_id": post["problem_id"],
        }
        self.assertEqual(report["next"], terminal)
        self.assertEqual(report["obligations"], [terminal])
        self.assertEqual(report["milestone"], "post")
        self.assertFalse(report["ready"])
        self.assertEqual(report["counts"]["obligations"], 1)
        self.assertLessEqual(expected_codes, {row["code"] for row in report["after_post"]})
        for row in report["after_post"]:
            self.assertNotIn("next", row)
            self.assertTrue(row["reason"])
            self.assertTrue(row["explanation"])
        summary = next_summary(report)
        self.assertEqual(summary["next_hint"]["code"], "problem_already_posted")
        self.assertEqual(summary["after_post"], len(report["after_post"]))
        self.assertEqual(summary["post_obligations"]["total"], 1)
        self.assertEqual(self.store.snapshot(), before)
        gate = gate_state(before["records"], self.artifacts, "post")
        self.assertEqual(gate["obligations"], [terminal])
        self.assertEqual(gate["after_post"], report["after_post"])
        return report

    def test_changed_evidence_digest_after_post_is_advisory(self):
        self.prepare_ready()
        post = self.post_current_statement()
        old_digest = post["problem_evidence_digest"]
        records = self.store.snapshot()["records"]
        changed = copy.deepcopy(records["problem_value"]["value-1"])
        changed.pop("recorded_revision")
        changed["id"] = "value-after-post"
        changed["motivation"][0]["text"] = "The same bound also connects two finite constructions."
        self.mutate(self.load_problem_module().record_value, changed)
        report = self.assert_terminal(post, {"problem_evidence_unassessed", "problem_decision_stale"})
        self.assertNotEqual(report["problem_evidence_digest"], old_digest)
        self.assertNotIn("gate", post)
        self.assertEqual(report["post"], dict(post, gate="passed"))

    def test_legacy_adjudication_missing_later_version_evidence_is_advisory(self):
        self.prepare_reviews(result_reviewer="first")
        add_focused = review_protocol.add_focused_adjudication

        def build_legacy_packet(records, artifacts, packet, *args, **kwargs):
            add_focused(records, artifacts, packet, *args, **kwargs)
            packet["version_evidence"] = []

        # Historical rules accepted this adjudication and its post. Today's
        # delivery audit runs unmodified after those immutable records exist.
        with patch.object(review_protocol, "add_focused_adjudication", side_effect=build_legacy_packet), \
                patch.object(research_decisions, "find_undelivered_adjudication_keys", return_value=[]):
            adjudication = self.observe_adjudication()
            post = self.post_current_statement()
        records = self.store.snapshot()["records"]
        version = next(row for row in research_decisions.collect_problem_post_objections(
            records, self.artifacts, list(records["value_review"].values()))
            if row["id"] == "unsupported-implication")
        missing = research_decisions.find_undelivered_adjudication_keys(
            records, self.artifacts, adjudication["assignment_id"],
            version_evidence=review_protocol.describe_version_evidence(records, self.artifacts, version))
        self.assertEqual(missing, ["version_evidence:problem_assessment:assessment-1"])
        self.assert_terminal(post, {"research_objection_unresolved"})

    def test_new_unrelated_post_finding_is_advisory(self):
        self.prepare_ready()
        post = self.post_current_statement()
        finding = {"code": "future_post_check", "explanation": "A later rule requires another recorded check.",
                   "reason": "The inventory does not contain the named evidence unit.",
                   "assignment_id": "result-first"}
        with patch.object(problem_delivery, "assess_source_requests", return_value=[finding]):
            report = self.assert_terminal(post, {"future_post_check"})
        self.assertEqual(report["after_post"], [finding])

    def test_a_later_empty_slate_does_not_replace_the_terminal_next(self):
        self.prepare_ready()
        post = self.post_current_statement()
        records = self.store.snapshot()["records"]
        slate = dict(records["problem_slate"]["slate-1"], selected=[], reason="No further post is planned.")
        self.put("problem_slate", "slate-1", slate)
        self.assert_terminal(post, {"problem_candidate_unselected"})

    def test_post_matching_uses_the_exact_claim_digest_across_statement_ids(self):
        self.prepare_ready()
        post = self.post_current_statement()
        self.select_statement(identifier="same-claim", title="A new title for the same claim", candidate_id="candidate-1")
        self.assert_terminal(post, {"problem_value_missing"})
        self.select_statement(identifier="changed-claim", statement="Does the bound hold for every odd input?")
        report = cli.status_report(self.store)
        self.assertNotEqual(report["next"]["code"], "problem_already_posted")
        self.assertEqual(report.get("after_post", []), [])
        self.assertIsNone(report.get("post"))


class PostedCarryRaceTests(unittest.TestCase):
    def test_approved_external_post_is_recorded_after_source_changes(self):
        self.record_approved_post_after_source_change()

    def test_approved_external_post_is_recorded_when_source_disappears(self):
        self.record_approved_post_after_source_change(unavailable=True)

    def record_approved_post_after_source_change(self, *, unavailable=False):
        import test_problem_objection_carry as carry_fixtures
        import test_math_problems_cli as server_fixtures
        from test_research_check_mode import CheckCommandMixin

        class PostableCarryFixture(carry_fixtures.CarryFixture):
            def build_statement(self, identifier='statement-1', **changes):
                changes.setdefault('statement', 'For every positive integer n, does the finite construction satisfy the uniform bound B(n) <= n^2?')
                changes.setdefault('resolution_criteria', 'Give a proof of the bound for every positive integer, or a fully checked counterexample in the stated family.')
                return super().build_statement(identifier, **changes)

            def build_value(self, evidence, identifier='value-1'):
                value = super().build_value(evidence, identifier)
                for field in ('motivation', 'current_state'):
                    value[field][0]['text'] += ' The supplied reading establishes the exact finite construction and explains its scope across the full stated family.'
                return value

        fixtures = carry_fixtures.ProblemObjectionCarryTests()
        self.addCleanup(fixtures.doCleanups)
        source = fixtures.source()
        destination = PostableCarryFixture()
        destination.area = 'math.PR'
        destination.setUp()
        self.addCleanup(destination.doCleanups)
        destination.prepare_problem_evidence()
        destination.bars()
        destination.invoke_arguments = lambda arguments: CheckCommandMixin.invoke_arguments(destination, arguments)
        carry_fixtures.legacy_handoff_or_carry_adapter(destination, source)
        fixtures.imported_pair(destination)
        destination.record_post_decision()
        gate_response = destination.invoke_arguments(['gate', 'post', '--workspace', str(destination.root)])
        self.assertEqual(gate_response[0], 0, gate_response[2])
        self.assertTrue(json.loads(gate_response[1])['ready'])
        exported = problem_delivery.export_problem(destination.store)
        directory = Path(exported['destination'])
        approved_files = {path.name: path.read_bytes() for path in directory.iterdir()}

        # Send the actual CLI's approved export to a server double. Only the HTTP
        # boundary is replaced; an external post exists before recording locally.
        server = server_fixtures._MathCliTestCase()
        server.setUp()
        self.addCleanup(server.doCleanups)
        problem_id = '656c336e-e892-4c47-80d2-a71d022f4116'
        server.answers[('POST', '/api/v1/math-problems')] = (201, dict(id=problem_id))
        arguments = ['post-problem', '--title', (directory / 'title.txt').read_text().strip(),
                     '--area', destination.area, '--citations-file', str(directory / 'citations.json')]
        for field, filename in (('statement', 'statement.md'), ('motivation', 'motivation.md'),
                                ('current-state', 'state.md'), ('resolution-criteria', 'criteria.md')):
            arguments.extend(['--' + field + '-file', str(directory / filename)])
        external, _ = server.run_cli(arguments)
        self.assertEqual(json.loads(external)['id'], problem_id)
        sent = server.read_sent_body('POST', '/api/v1/math-problems')
        self.assertEqual(sent['statement'], (directory / 'statement.md').read_text().strip())
        self.assertFalse(destination.store.snapshot()['records'].get('problem_post'))

        source.assess('result', 'first')
        if unavailable:
            source.root.rename(source.root.with_name('source-unavailable-after-send'))
        refresh_code = 'problem_objection_source_unavailable' if unavailable else 'problem_objections_refresh_required'
        before = cli.status_report(destination.store)
        self.assertIn(refresh_code, {row['code'] for row in before['obligations']})
        destination.assert_error('readiness_required', lambda: problem_delivery.export_problem(destination.store))
        tampered = destination.root / 'tampered-export'
        tampered.mkdir()
        for name, content in approved_files.items():
            (tampered / name).write_bytes(content)
        (tampered / 'statement.md').write_text('A replacement that was not the approved external post.')
        destination.assert_error('problem_export_mismatch', lambda: destination.mutate(
            destination.load_problem_module().record_posted,
            dict(problem_id=problem_id, export_directory=str(tampered))))
        posted = CheckCommandMixin.invoke_command(destination, 'problem-posted',
            dict(problem_id=problem_id, export_directory=str(directory)), check=False, request='record-approved-external-post')
        self.assertEqual(posted[0], 0, posted[2])
        post = json.loads(posted[1])['result']
        self.assertEqual(post['problem_id'], problem_id)
        self.assertEqual(post['statement_digest'], exported['statement_digest'])
        self.assertEqual(post['export_digest'], problem_delivery.compute_export_digest(approved_files))
        self.assertNotEqual(post.get('gate'), 'bypassed')
        self.assertEqual(destination.store.snapshot()['records']['problem_post'][post['id']], post)
        self.assertEqual({path.name: path.read_bytes() for path in directory.iterdir()}, approved_files)
        for command in ('status', 'next'):
            response = destination.invoke_arguments([command, '--workspace', str(destination.root)])
            self.assertEqual(response[0], 0, response[2])
            report = json.loads(response[1])
            self.assertEqual(report['next']['code'], 'problem_already_posted')
            self.assertEqual({row['code'] for row in report['obligations']}, {'problem_already_posted'})
            owed = [row for row in report['after_post'] if row['code'] == refresh_code]
            self.assertEqual(len(owed), 1)
            self.assertEqual(owed[0]['source_workspace'], str(source.root.resolve()))
            self.assertTrue(owed[0]['reason'])
            self.assertNotIn('next', owed[0])
        destination.assert_error('problem_already_posted', lambda: destination.assign('result', 'first', identifier='call-after-post'))
        destination.assert_error('problem_already_posted', lambda: destination.assign('slate', 'first',
            identifier='slate-continuation-after-post', prior_assignment_id='slate-first'))

    def test_posted_carry_controller_refuses_child_and_frontier_before_source_reads(self):
        import sys
        import test_problem_objection_carry as carry_fixtures
        from problem_parts_fixtures import ResultPartsCase
        from research_harness import problem_claims as real_claims, review_staged_protocol
        from test_problem_post_review_terminal import record_external_post
        fixtures = carry_fixtures.ProblemObjectionCarryTests()
        self.addCleanup(fixtures.doCleanups)
        source = fixtures.source()
        staged = ResultPartsCase()
        staged.staged_entry = True
        staged.setUp()
        self.addCleanup(staged.doCleanups)
        sys.modules['research_harness.problem_claims'].require_problem = real_claims.require_problem
        home = staged.case
        carry_fixtures.legacy_handoff_or_carry_adapter(home, source)
        staged.oversized()
        controller = staged.assign_staged('posted-controller')
        record_external_post(home)
        source.root.rename(source.root.with_name('source-unavailable-after-controller-post'))
        child = {key: controller[key] for key in ('role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id')}
        child.update(id='child-after-post', context=dict(controller_assignment_id=controller['id'],
            task_id=controller['staged_review']['task_ids'][0]))
        for operation, payload in (
            (review_protocol.record_assignment, child),
            (review_staged_protocol.plan_frontier, dict(controller_assignment_id=controller['id']))):
            with self.subTest(operation=operation.__name__):
                revision = home.store.revision
                with self.assertRaises(ResearchError) as caught:
                    home.mutate(operation, payload)
                self.assertEqual(caught.exception.code, 'problem_already_posted')
                self.assertEqual(home.store.revision, revision)

    def test_fresh_carry_keeps_a_new_slate_root_available_after_another_slot_posted(self):
        import test_problem_objection_carry as carry_fixtures
        from test_research_problem_slate import ProblemSlateTests
        fixtures = carry_fixtures.ProblemObjectionCarryTests()
        self.addCleanup(fixtures.doCleanups)
        source = fixtures.source()
        case = ProblemSlateTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.record_candidate()
        case.observe_bars()
        old = case.assign('slate', 'first')
        with patch.object(review_protocol.transport, 'send_request', side_effect=OSError('Synthetic failed slate')):
            case.mutate(review_protocol.invoke_assignment, dict(id='failed-slate', assignment_id=old['id']), credential='fixture')
        case.record_slate()
        statement = case.select_statement(candidate_id='candidate-1')
        case.mutate(case.load_problem_module().record_post_external, dict(id='post',
            problem_id='656c336e-e892-4c47-80d2-a71d022f4116',
            statement_digest=case.load_problem_module().compute_statement_digest(statement),
            reason='Synthetic publication.', evidence='Synthetic server receipt.'))
        carry_fixtures.legacy_handoff_or_carry_adapter(case, source)
        new = case.assign('slate', 'second')
        self.assertEqual(old['slot_id'], 'bar-first')
        self.assertEqual(new['slot_id'], 'bar-second')
        self.assertIsNone(problem_delivery.find_review_post(case.store.snapshot()['records'], new, case.artifacts))
        self.assertEqual(problem_delivery.find_review_closure_root(case.store.snapshot()['records'], new)['id'], new['id'])
