"""The common openness auditor accepts a statement without a native controller claim."""

import unittest

from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.storage import Store
from search_controller.reviewer import common_root
from search_controller.service import Controller
from tests import test_search_openness_closure as closure_tests
from tests.test_search_cli import SearchCLIWorkspace


class SharedOpennessMinimumTests(SearchCLIWorkspace, unittest.TestCase):
    artifact = closure_tests.OpennessClosureTests.artifact
    external_checkpoint = closure_tests.OpennessClosureTests.external_checkpoint
    setup_external = closure_tests.OpennessClosureTests.setup_external
    set_up_accepted_proof = closure_tests.OpennessClosureTests.set_up_accepted_proof
    build_closure_spec = closure_tests.OpennessClosureTests.build_closure_spec

    def test_common_statement_audit_retains_the_native_statement_mismatch_code(self):
        self.set_up_accepted_proof("already_solved")
        controller = Controller(self.root)
        finding = self.build_closure_spec()["review"]["findings"]["openness"]
        common = common_root(self.root, controller.status())
        try:
            from research_harness.openness_minimum import audit_minimum
        except ModuleNotFoundError:
            self.fail("The shared record-level openness auditor is not available")
        records = Store(common).snapshot()["records"]
        artifacts = ArtifactStore(common)
        audit_minimum(finding, finding["minimum"]["statement"], records, artifacts, set(finding["evidence"]))
        with self.assertRaises(ResearchError) as caught:
            audit_minimum(finding, "A different exact statement", records, artifacts, set(finding["evidence"]))
        self.assertEqual(caught.exception.code, "openness_statement_mismatch")


if __name__ == "__main__":
    unittest.main()
