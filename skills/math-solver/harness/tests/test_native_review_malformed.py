"""Observed review packets still require the native subject's typed schema."""

import json
import unittest

from search_controller.errors import SearchError
from search_controller.service import Controller
from tests.native_reviewer_support import observe_review
from tests.search_fixtures import contract, digest, review
from tests.test_search_cli import SearchCLIWorkspace


class NativeReviewMalformedTests(SearchCLIWorkspace, unittest.TestCase):
    def test_observed_nonproposal_subject_is_rejected_without_a_raw_key_error(self):
        controller = Controller(self.root)
        controller.command("init", {"contract": contract()}, 0, "initialize")
        proposed = self.prepared_proposal()
        controller.command("propose", proposed, 1, "propose")
        malformed = {"malformed": "This is not a native proposal."}
        value = review(proposed["proposal"])
        value["subject_digest"] = digest(malformed)
        value = observe_review(controller, value, malformed)
        path = self.root / "malformed-proposal.json"
        path.write_text(json.dumps(malformed))
        spec = {"proposal_id": "proposal-000001", "review": value,
                "inputs": [{"path": path.name, "kind": "blob", "digest": digest(malformed)}]}
        before = controller.store.read()

        with self.assertRaises(SearchError) as caught:
            controller.command("review", spec, 2, "malformed-observed-review")

        self.assertEqual(caught.exception.code, "invalid_record")
        self.assertEqual(controller.store.read(), before)
