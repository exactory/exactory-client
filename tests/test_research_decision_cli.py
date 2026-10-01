"""Public strategic command contracts, including observational assessment."""

import contextlib
import io
import unittest

from literature_fixtures import LiteratureCase
from research_harness import cli, integration


class DecisionParserTests(unittest.TestCase):
    def parse(self, arguments):
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                return cli.build_parser().parse_args(arguments)
            except SystemExit as error:
                self.fail("The documented strategic command was rejected: " + str(error))

    def test_strategic_mutations_require_the_existing_revision_identity_contract(self):
        for command in ("intent", "strategy", "work-item", "research-lead", "value-review",
                        "research-decision", "review-route", "review-assignment", "review-attempt",
                        "review-context", "review-adjudication", "review-probe", "review-run"):
            with self.subTest(command=command):
                args = self.parse([command, "--file", "payload.json", "--expected-revision", "3", "--request-id", "request-1"])
                self.assertEqual(args.command, command)
                self.assertEqual((args.expected_revision, args.request_id), (3, "request-1"))

    def test_role_packets_and_decision_assessment_have_explicit_read_only_arguments(self):
        decision = self.parse(["research-decision-assess", "--boundary", "write", "--decision-id", "result-1"])
        self.assertEqual((decision.boundary, decision.decision_id), ("write", "result-1"))
        packet = self.parse(["strategy-packet", "--dossier-id", "slate-1", "--role", "bar", "--reviewer-id", "assessor-1"])
        self.assertEqual((packet.dossier_id, packet.role, packet.reviewer_id), ("slate-1", "bar", "assessor-1"))


class DecisionReadOnlyTests(LiteratureCase):
    def test_missing_intent_is_reported_without_initializing_or_mutating_research(self):
        self.mutate(integration.initialize_workspace, {"kind": "study", "state": {
            "name": "observational-fixture", "stage": "initiate", "status": "pending"}})
        before = self.store.snapshot()
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                args = cli.build_parser().parse_args([
                    "research-decision-assess", "--workspace", str(self.root), "--boundary", "target"])
            except SystemExit as error:
                self.fail("The assessment command was rejected: " + str(error))
        result = cli.run(args)
        self.assertFalse(result["ready"])
        self.assertIn("research_intent_missing", {item["code"] for item in result["obligations"]})
        self.assertEqual(self.store.snapshot(), before)
