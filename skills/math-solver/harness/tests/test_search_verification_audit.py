"""Read-only policy audit rejects misleading captured compiler output."""

from pathlib import Path
import json
import tempfile
import unittest

from search_controller.evidence import audit_verification
from search_controller.execution import lean_inspection_source
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
            runtime = Path(temporary) / "runtime"
            runtime.mkdir()
            (runtime / "library").write_bytes(b"pinned library")
            from search_controller.evidence import capture_toolchain
            inventory = store.put_blob(capture_toolchain(runtime))
            description = {"theorem": "root", "requested_type": "True"}
            step_digest = store.put_artifact(json.dumps(description).encode())
            source_digest = store.put_artifact(lean_inspection_source(description, "True").encode())
            task = {"kind": "proof", "purpose": "Verify True", "input_domain": "The exact declared fixture"}
            input_modes = [{"path": "sample/deterministic/proof/step.json", "mode": 0o644}]
            spec = {"kind": "lean", "requested_type": "True", "input_modes": input_modes}
            subject = {"node_id": "node-000001", "task": task, "spec_without_input_review": dict(spec)}
            spec["input_review"] = {"subject_digest": store.put_blob(subject), "claim_digest": digest(claim()),
                "reviewer": provenance("input-reviewer"), "decision": "approve",
                "findings": {key: "Exact fixture inputs reviewed" for key in ["statement", "assumptions", "scope", "dependencies", "policy"]}}
            store.put_blob(spec["input_review"])
            spec_digest = store.put_blob(spec)
            inputs = {"schema_version": 1, "claim_digest": digest(claim()),
                      "artifacts": [{"path": "sample/deterministic/proof/step.json", "digest": step_digest, "role": "input"}], "external_dependencies": []}
            input_digest = store.put_blob(inputs)
            for output, code, accepted in [("'root' depends on axioms: []", 0, True),
                                           ("'other.root' depends on axioms: []", 0, False),
                                           ("'root' depends on axioms: []", 1, False),
                                           ("'root' depends on axioms: [Lean.trustCompiler]", 0, False)]:
                output = "EXACTORY_TYPE_BEGIN\nroot : True\nEXACTORY_TYPE_END\n" + output + "\n'exactory_correspondence' depends on axioms: []\n"
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
                state = {"runs": {"run-000001": {"status": "terminal", "kind": "lean", "termination": "exit",
                    "node_id": "node-000001", "task": task, "input_modes": input_modes,
                    "commands": [["lake", "build"], ["lake", "env", "lean", "Inspect.lean"]],
                    "toolchain_inventory_digest": inventory, "inspection_source_digest": source_digest, "spec_digest": spec_digest,
                    "inspection": {"source_digest": source_digest, "printed_type_digest": store.put_artifact(b"root : True"),
                                   "declaration_axioms": [], "correspondence_axioms": []},
                    "input_digest": input_digest, "result_digest": result_digest}}}
                if accepted:
                    audit_verification(state, manifest, store)
                    state["runs"]["run-000001"]["input_modes"] = [{"path": "sample/deterministic/proof/step.json", "mode": 0o755}]
                    with self.assertRaises(SearchError):
                        audit_verification(state, manifest, store)
                else:
                    with self.assertRaises(SearchError, msg=output):
                        audit_verification(state, manifest, store)
