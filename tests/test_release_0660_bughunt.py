"""Regression evidence for defects found while auditing releases 0.66.0 and 0.66.2."""

import json
import unittest

import test_research_publication as publication_fixtures
from research_harness.artifacts import describe_artifact
from test_research_check_mode import CheckCommandMixin


class ReleaseCheckCoverageTests(CheckCommandMixin, unittest.TestCase):
    def test_strategy_packet_check_handles_a_command_that_publishes_claim_derivatives(self):
        fixture = publication_fixtures.ResearchPublicationTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.root, self.store = fixture.root, fixture.store
        claim_path = self.root / "evidence/claims.json"
        claims = json.loads(claim_path.read_text(encoding="utf-8"))
        claims[0]["revised"] = {"previous": "The maximum is at most 10.",
                                "reason": "The finite enumeration establishes equality at 9."}
        claim_path.write_text(json.dumps(claims), encoding="utf-8")
        bundle = fixture.mutate(fixture.publication().prepare_publication, fixture.bundle_payload())["result"]
        context_path = self.root.parent / "packet-context.json"
        context_path.write_text(json.dumps({"bundle": bundle}), encoding="utf-8")
        arguments = ["strategy-packet", "--workspace", str(self.root), "--role", "standalone",
                     "--reviewer-id", "independent-reviewer", "--context-file", str(context_path)]

        before = self.read_workspace()
        checked = self.invoke_arguments([*arguments, "--check"])
        self.assertEqual(self.read_workspace(), before)

        # The same packet preparation without --check really publishes bytes.
        current = [{key: value for key, value in claims[0].items() if key != "revised"}]
        data = (json.dumps(current, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        reference = describe_artifact(data, "application/json")
        derived_path = self.root / reference["path"]
        self.assertFalse(derived_path.exists())
        revision = self.store.revision
        written = self.invoke_arguments(arguments)
        self.assertEqual(written[0], 0, written[2])
        self.assertEqual(derived_path.read_bytes(), data)
        self.assertEqual(self.store.revision, revision)
        self.assertEqual(json.loads(written[1])["paper"]["files"]["claims"]["artifact"], reference)

        # File-mutating commands must accept the flag and refuse unsupported preparation explicitly.
        self.assertEqual(checked[0], 1, checked[2])
        self.assertEqual(json.loads(checked[2])["error"]["code"], "check_unsupported")


if __name__ == "__main__":
    unittest.main()
