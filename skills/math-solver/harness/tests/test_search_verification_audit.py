"""Read-only policy audit rejects misleading captured compiler output."""

from pathlib import Path
import tempfile
import unittest

from search_controller.evidence import audit_verification
from search_controller.errors import SearchError
from search_controller.storage import Store
from tests.search_fixtures import claim, digest, provenance


class VerificationAuditTests(unittest.TestCase):
    def test_inspection_must_match_the_whole_requested_declaration_and_exit_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary) / ".search")
            store.initialize({}, "objective-test")
            empty = store.put_artifact(b"")
            theorem_type = store.put_artifact(b"True")
            toolchain = store.put_artifact(b"leanprover/lean4:v4.22.0")
            inputs = {"schema_version": 1, "claim_digest": digest(claim()), "artifacts": [], "external_dependencies": []}
            input_digest = store.put_blob(inputs)
            for output, code, accepted in [("'root' depends on axioms: []", 0, True),
                                           ("'other.root' depends on axioms: []", 0, False),
                                           ("'root' depends on axioms: []", 1, False),
                                           ("'root' depends on axioms: [Lean.trustCompiler]", 0, False)]:
                result = {"schema_version": 1, "run_id": "run-000001", "claim_digest": digest(claim()),
                          "input_digest": input_digest, "declaration": "root", "theorem_type_digest": theorem_type,
                          "toolchain_digest": toolchain, "commands": [
                              {"argv": ["lake", "build"], "exit_code": 0, "stdout_digest": empty, "stderr_digest": empty},
                              {"argv": ["lake", "env", "lean", "Inspect.lean"], "exit_code": code,
                               "stdout_digest": store.put_artifact(output.encode()), "stderr_digest": empty}]}
                result_digest = store.put_blob(result)
                review = {"subject_digest": result_digest, "claim_digest": digest(claim()), "reviewer": provenance("policy-reviewer"),
                          "decision": "approve", "findings": {key: "Checked the exact declaration and captured inputs"
                          for key in ["statement", "assumptions", "scope", "dependencies", "policy"]}}
                manifest = dict(inputs, kind="lean", verification={"run_id": "run-000001", "result_digest": result_digest,
                                  "policy_review": review, "requested_declaration": "root", "requested_type_digest": theorem_type})
                state = {"runs": {"run-000001": {"status": "terminal", "input_digest": input_digest, "result_digest": result_digest}}}
                if accepted:
                    audit_verification(state, manifest, store)
                else:
                    with self.assertRaises(SearchError, msg=output):
                        audit_verification(state, manifest, store)
