"""External deliveries have a validated, replayable local receipt without an export."""

import copy
import json
from datetime import datetime, timezone
from unittest.mock import patch

import test_research_check_mode as checks
import test_research_problem_claims as fixtures
from codex_route_fixtures import record_codex_route
from research_harness import cli, operations, problem_claims, problem_delivery
from research_harness import review_continuation, review_protocol
from research_harness.output_views import render_all
from research_harness.report_views import next_summary


def record_legacy_post(case, payload):
    """Write the record and receipt with the 0.73.2 operation and payload."""
    revision = case.store.revision
    post = dict(payload, statement_id=case.statement['id'], gate='bypassed',
                posted_at='2026-11-05T08:31:11.654321+00:00',
                recorded_revision=revision + 1)
    receipt = operations.prepared_mutation(
        case.store, 'problem.post_external', payload,
        lambda records, value: ([('problem_post', post['id'], post)], post),
        expected_revision=revision, request_id='legacy-post-request')
    return revision, receipt


class ExternalPostTests(checks.CheckCommandMixin, fixtures.ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.statement = self.select_statement()
        self.payload = {
            "id": "external-post-1",
            "problem_id": "656c336e-e892-4c47-80d2-a71d022f4116",
            "statement_digest": problem_claims.compute_statement_digest(self.statement),
            "reason": "The owner requested direct publication before the harness gate was complete.",
            "evidence": "The owner instructed that this exact statement be posted.",
        }

    def record_external(self, payload=None, **kwargs):
        self.assertIn("problem-post-external", cli.OPERATIONS)
        return self.mutate(cli.OPERATIONS["problem-post-external"], payload or self.payload, **kwargs)

    def test_records_a_terminal_bypassed_post_without_an_export(self):
        before = cli.status_report(self.store)
        post = self.record_external()["result"]
        for key, value in self.payload.items():
            self.assertEqual(post[key], value)
        self.assertEqual(post["gate"], "bypassed")
        self.assertEqual(post["statement_id"], self.statement["id"])
        self.assertIsNone(post["posted_at"])
        self.assertIn("recorded_at", post)
        self.assertNotIn("export_digest", post)
        self.assertNotIn("export_directory", post)
        self.assertFalse((self.root / "final/problem/exports").exists())
        report = cli.status_report(self.store)
        self.assertEqual(report["next"]["code"], "problem_already_posted")
        self.assertEqual(len(report["obligations"]), 1)
        self.assertEqual(report["post"], post)
        self.assertEqual({row["code"] for row in report["after_post"]},
                         {row["code"] for row in before["obligations"]})
        self.assertTrue(all("next" not in row for row in report["after_post"]))
        self.assertEqual(next_summary(report)["after_post"], len(report["after_post"]))

    def test_graph_shaped_server_post_is_recorded_without_export_or_new_reviews(self):
        statement = self.select_statement(identifier='statement-1b',
            title='A synthetic graph realization bound',
            statement='Does every graph in the synthetic rigid family satisfy $R(G) \\leq 3^{|V(G)|}$?\n\n'
                      '---\n\nHere $R(G)$ counts labelled real realizations of generic edge lengths.',
            resolution_criteria='Prove the bound for the stated family or supply a certified counterexample.')
        record_codex_route(self)
        assignment_payload = dict(id='outstanding-bar', role='bar', route_id='route',
                                  dossier_id=None, reviewer_id='first', author_id='problem-author', context={})
        assignment = self.mutate(review_protocol.record_assignment, assignment_payload)['result']
        self.mutate(review_protocol.record_assignment,
                    dict(assignment_payload, id='outstanding-second-bar', reviewer_id='second'))
        server_response = dict(
            id='656c336e-e892-4c47-80d2-a71d022f4116', title=statement['title'], area='math.CO',
            statement=statement['statement'].split('---')[0].strip(),
            statementDetails=statement['statement'].split('---')[1].strip(),
            resolutionCriteria=statement['resolution_criteria'], createdAt='2026-11-05T02:53:34.915Z',
            status='open')
        response_path = self.root / 'server-response.json'
        response_bytes = json.dumps(server_response, sort_keys=True).encode()
        response_path.write_bytes(response_bytes)
        evidence = self.mutate(cli.OPERATIONS['artifact'], dict(id='server-response',
            path='server-response.json', media_type='application/json'))['result']['artifact']
        payload = dict(self.payload, id='external-graph-post', problem_id=server_response['id'],
                       statement_digest='b8d4383cb07b38b136eebfea8feda1fa08f7ff743ed47b85996fe6af68c42bcd',
                       evidence=evidence, posted_at=server_response['createdAt'])
        before_report = cli.status_report(self.store)
        self.assertTrue(before_report['obligations'])
        before_findings = problem_delivery.assess_post(self.store.snapshot()['records'], self.artifacts)['obligations']
        self.assertEqual(review_continuation.plan_next(self.store.snapshot()['records'], self.artifacts,
                                                     assignment['id'])['state'], 'run')
        before = self.read_workspace()
        revision = self.store.revision
        request_id = 'record-existing-server-post'
        checked = self.invoke_command('problem-post-external', payload, revision=revision, request=request_id)
        self.assert_accepted_check(checked, revision)
        self.assertEqual(self.read_workspace(), before)
        written = self.invoke_command('problem-post-external', payload, check=False,
                                      revision=revision, request=request_id)
        self.assertEqual(written[0], 0, written[2])
        receipt = json.loads(written[1])
        post = receipt['result']
        self.assertEqual(post['statement_id'], 'statement-1b')
        self.assertEqual(post['problem_id'], server_response['id'])
        self.assertEqual(post['statement_digest'], payload['statement_digest'])
        self.assertEqual(post['evidence'], evidence)
        self.assertEqual(self.artifacts.read(evidence), response_bytes)
        self.assertEqual(post['posted_at'], '2026-11-05T02:53:34.915Z')
        self.assertNotEqual(post['recorded_at'], post['posted_at'])
        self.assertEqual(post['gate'], 'bypassed')
        self.assertNotIn('export_digest', post)
        self.assertNotIn('export_directory', post)
        self.assertFalse((self.root / 'final/problem/exports').exists())
        report = cli.status_report(self.store)
        self.assertEqual(report['next']['code'], 'problem_already_posted')
        self.assertEqual([row['code'] for row in report['obligations']], ['problem_already_posted'])
        retained = [dict(
            {key: value for key, value in row.items() if key != 'next'},
            reason=row.get('reason', row['explanation'])) for row in before_findings]
        self.assertEqual(report['after_post'][:len(retained)], retained)
        pending = report['after_post'][len(retained):]
        self.assertEqual({row['assignment_id'] for row in pending},
                         {'outstanding-bar', 'outstanding-second-bar'})
        self.assertTrue(all(row['code'] == 'review_output_pending' and 'next' not in row for row in pending))
        self.assertEqual(review_continuation.plan_next(self.store.snapshot()['records'], self.artifacts,
                                                     assignment['id'])['state'], 'done')
        after = self.read_workspace()
        for check in (False, True):
            replay = self.invoke_command('problem-post-external', payload, check=check,
                                         revision=revision, request=request_id)
            self.assertEqual(replay[0], 0, replay[2])
            self.assertEqual(json.loads(replay[1]), receipt)
            self.assertEqual(self.read_workspace(), after)
        self.assert_refusal_parity('problem-post-external', dict(payload, id='duplicate'),
                                  'problem_already_posted', request='duplicate-server-post')
        self.assert_refusal_parity('review-assignment', dict(assignment_payload, id='new-review'),
                                  'problem_already_posted', request='review-after-server-post')
        before_call = self.store.snapshot()
        with patch.object(review_protocol.transport, 'send_request', side_effect=AssertionError('No new call')):
            written = self.invoke_command('review-run', dict(id='new-call', assignment_id=assignment['id']),
                                          check=False, request='call-after-server-post')
        self.assertEqual(written[0], 1, written[2])
        self.assertEqual(json.loads(written[2])['error']['code'], 'problem_already_posted')
        self.assertEqual(self.store.snapshot(), before_call)

    def test_wrong_statement_digest_is_refused_without_writing(self):
        self.assertIn("problem-post-external", cli.OPERATIONS)
        self.assert_refusal_parity("problem-post-external", dict(self.payload, statement_digest="0" * 64),
                                  "problem_statement_mismatch")

    def test_boundary_refuses_malformed_ids_empty_text_and_invalid_evidence(self):
        self.assertIn("problem-post-external", cli.OPERATIONS)
        before = self.store.snapshot()
        invalid_values = {
            "id": ("", " \n", None),
            "problem_id": ("not-a-uuid", "656c336ee8924c4780d2a71d022f4116", None, 7),
            "reason": ("", " \n", None, []),
            "posted_at": ("2026-11-04T14:49:22", "2026-11-04", 7, None, "bad time"),
            "evidence": ("", " \n", None, [], {}, {"kind": "record", "id": "instruction"}),
        }
        for field, values in invalid_values.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assert_refusal_parity("problem-post-external", dict(self.payload, **{field: value}),
                                              "invalid_input")
        for field in self.payload:
            payload = dict(self.payload)
            payload.pop(field)
            with self.subTest(missing=field):
                self.assert_refusal_parity("problem-post-external", payload, "invalid_input")
        self.assert_refusal_parity("problem-post-external", dict(self.payload, export_directory="unused"),
                                  "invalid_input")
        self.assertEqual(self.store.snapshot(), before)

    def test_evidence_can_be_a_verified_artifact_reference(self):
        evidence = self.artifacts.put(b"An authored instruction to post the exact statement.", "text/plain")
        post = self.record_external(dict(self.payload, evidence=evidence))["result"]
        self.assertEqual(post["evidence"], evidence)

    def test_a_missing_evidence_artifact_is_refused(self):
        self.assertIn("problem-post-external", cli.OPERATIONS)
        evidence = self.artifacts.put(b"An authored instruction.", "text/plain")
        (self.root / evidence["path"]).unlink()
        self.assert_refusal_parity("problem-post-external", dict(self.payload, evidence=evidence), "artifact_missing")

    def test_a_second_post_of_the_statement_is_refused(self):
        self.record_external()
        self.assert_refusal_parity("problem-post-external", dict(self.payload, id="second-post"),
                                  "problem_already_posted", request="second-request")
        self.assertEqual(len(self.store.snapshot()["records"]["problem_post"]), 1)

    def test_an_ordinary_post_receipt_also_prevents_an_external_duplicate(self):
        post = dict(id="ordinary-post", problem_id=self.payload["problem_id"],
                    statement_digest=self.payload["statement_digest"], export_digest="legacy-export")
        self.put("problem_post", post["id"], post)
        self.assertIn("problem-post-external", cli.OPERATIONS)
        self.assert_refusal_parity("problem-post-external", self.payload, "problem_already_posted")

    def test_missing_current_statement_is_refused(self):
        self.put("problem_selection", "current", {"statement_id": "absent"})
        self.assertIn("problem-post-external", cli.OPERATIONS)
        self.assert_refusal_parity("problem-post-external", self.payload, "problem_statement_missing")

    def test_replay_returns_the_original_receipt_after_statement_changes(self):
        self.assertIn("problem-post-external", cli.OPERATIONS)
        operation = cli.OPERATIONS["problem-post-external"]
        revision = self.store.revision
        receipt = operation(self.store, self.payload, expected_revision=revision, request_id="external-request")
        self.select_statement(identifier="later-statement", statement="A different exact mathematical statement.")
        before = self.store.snapshot()
        replay = operation(self.store, copy.deepcopy(self.payload), expected_revision=revision,
                           request_id="external-request")
        self.assertEqual(replay, receipt)
        self.assertEqual(self.store.snapshot(), before)

    def test_cli_check_is_read_only_and_commit_places_the_post_view(self):
        self.assertIn("problem-post-external", cli.OPERATIONS)
        render_all(self.store)
        before = self.read_workspace()
        checked = self.invoke_command("problem-post-external", self.payload)
        self.assert_accepted_check(checked, self.store.revision)
        self.assertEqual(self.read_workspace(), before)
        written = self.invoke_command("problem-post-external", self.payload, check=False)
        self.assertEqual(written[0], 0, written[2])
        report = cli.status_report(self.store)
        self.assertEqual(report["post"]["gate"], "bypassed")
        self.assertEqual(report["post"]["reason"], self.payload["reason"])
        views = list((self.root / "final/problem/posts").rglob("*.json"))
        self.assertTrue(views)
        self.assertTrue(any(json.loads(path.read_text()).get("gate") == "bypassed" for path in views))

    def test_the_cli_example_has_all_external_post_fields(self):
        self.assertIn("problem-post-external", cli.OPERATIONS)
        payload = cli.run(cli.build_parser().parse_args(["example", "problem-post-external"]))
        self.assertEqual(set(payload), set(self.payload) | {"posted_at"})
        self.assertEqual(payload["posted_at"], "2026-11-04T14:49:22.236Z")
        self.assertEqual(len(payload["statement_digest"]), 64)

    def test_server_time_is_verbatim_and_recording_clock_is_separate(self):
        now = datetime(2026, 11, 5, 8, 31, 11, 654321, tzinfo=timezone.utc)
        times = ('2026-11-04T14:49:22.236Z', '2026-11-04T14:49:22Z',
                 '2026-11-04T10:49:22.123456-04:00', '2099-01-01T00:00:00+09:00')
        for index, posted_at in enumerate(times):
            with self.subTest(posted_at=posted_at), patch.object(problem_claims, 'datetime') as clock:
                clock.now.return_value = now
                self.select_statement(identifier='timed-' + str(index), statement='Synthetic bound ' + str(index))
                statement = self.store.snapshot()['records']['problem_statement']['timed-' + str(index)]
                payload = dict(self.payload, id='post-' + str(index), posted_at=posted_at,
                               statement_digest=problem_claims.compute_statement_digest(statement))
                before = self.read_workspace()
                self.assert_accepted_check(self.invoke_command('problem-post-external', payload), self.store.revision)
                self.assertEqual(self.read_workspace(), before)
                post = self.record_external(payload)['result']
                self.assertEqual(post['posted_at'], posted_at)
                self.assertEqual(post['recorded_at'], now.isoformat())
                clock.now.assert_called_with(timezone.utc)

    def test_omitted_time_records_null_with_the_frozen_recording_clock(self):
        now = datetime(2026, 11, 5, 8, 31, 11, tzinfo=timezone.utc)
        with patch.object(problem_claims, 'datetime') as clock:
            clock.now.return_value = now
            post = self.record_external()['result']
        self.assertIsNone(post['posted_at'])
        self.assertEqual(post['recorded_at'], now.isoformat())

    def test_legacy_projection_preserves_snapshot_receipt_fingerprint_and_raw_output(self):
        revision, receipt = record_legacy_post(self, self.payload)
        render_all(self.store)
        raw = {str(path): path.read_bytes() for path in (self.root / 'final/problem/posts').rglob('*.json')}
        self.assertTrue(raw)
        before = self.read_workspace()
        snapshot = self.store.snapshot()
        fingerprint = self.store._read_preparation()._fingerprint
        original = self.store.committed_request('legacy-post-request')
        report = cli.status_report(self.store)
        self.assertEqual(report['post'], dict(receipt['result'], posted_at=None,
                                             recorded_at=receipt['result']['posted_at']))
        self.assertEqual(self.store.snapshot(), snapshot)
        self.assertEqual(self.store.committed_request('legacy-post-request'), original)
        self.assertEqual(self.store._read_preparation()._fingerprint, fingerprint)
        self.assertEqual(self.read_workspace(), before)
        render_all(self.store)
        self.assertEqual({str(path): path.read_bytes() for path in (self.root / 'final/problem/posts').rglob('*.json')}, raw)
        replay = cli.OPERATIONS['problem-post-external'](
            self.store, self.payload, expected_revision=revision, request_id='legacy-post-request')
        self.assertEqual(replay, receipt)
        checked = self.invoke_command('problem-post-external', self.payload, revision=revision,
                                      request='legacy-post-request')
        self.assertEqual(json.loads(checked[1]), receipt)
        self.assert_refusal_parity('problem-post-external', dict(self.payload, posted_at='2026-11-04T14:49:22.236Z'),
                                  'request_id_conflict', revision=revision, request='legacy-post-request')

    def test_internal_posts_with_or_without_gate_are_never_converted(self):
        for gate in (None, 'passed'):
            with self.subTest(gate=gate):
                post = dict(self.payload, statement_id=self.statement['id'], posted_at='2026-11-04T14:49:22Z')
                if gate is not None:
                    post['gate'] = gate
                self.put('problem_post', post['id'], post)
                self.assertEqual(cli.status_report(self.store)['post'], dict(post, gate='passed'))
