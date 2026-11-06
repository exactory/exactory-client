"""Reuse successful manifest audits without weakening incoming checks."""

from collections import Counter
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from search_controller import evidence, schema as s
from search_controller.errors import SearchError
from search_controller.storage import Store


class EvidenceScalingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.content = Store(self.root / ".search")
        self.content.initialize({}, "objective-scaling")
        self.claim = {"statement": "The exact scaling fixture claim"}
        self.source = self.content.put_artifact(b"Pinned original source")
        self.study = self.content.put_artifact(b"Exact scope comparison")
        self.state = {"checkpoints": {}, "acceptances": {}, "runs": {}, "nodes": {},
                      "service": {}, "control": {"closure": None}, "proof_status": "open"}
        self.proofs = {}
        self.origins = self.start_patch(patch("search_controller.provenance.collect_probe_origins", return_value=[]))
        self.reviews = self.start_patch(patch("search_controller.reviewer.require_review"))

    def start_patch(self, manager):
        value = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)
        return value

    def make_manifest(self, name, dependencies=(), *, kind="analytical", external=()):
        proof = self.content.put_artifact(("Proof artifact for " + name).encode())
        roles = ["proof"] if kind == "analytical" else ["certificate", "checker"] if kind == "certificate" else ["theorem", "toolchain"]
        manifest = {"schema_version": 1, "kind": kind, "claim_digest": s.digest(self.claim),
                    "conclusion": {"outcome": "proof", "dependency_ids": [], "route_bindings": []},
                    "artifacts": [{"path": name + "-" + role, "digest": proof, "role": role} for role in roles],
                    "dependencies": list(dependencies), "external_dependencies": list(external),
                    "verification": None if kind == "analytical" else {
                        "run_id": "missing-run", "result_digest": "a" * 64, "policy_review": {},
                        "requested_declaration": None, "requested_type_digest": None}}
        digest = self.content.put_blob(manifest)
        self.proofs[digest] = proof
        return digest

    def make_checkpoint(self, *digests, claim=None):
        checkpoint = {"claim": self.claim if claim is None else claim,
                      "origin": {"kind": "external_result", "source_digest": self.source, "study_digest": self.study},
                      "evidence_digests": list(digests)}
        identity = "checkpoint-{:06d}".format(len(self.state["checkpoints"]) + 1)
        self.state["checkpoints"][identity] = checkpoint
        return identity

    def add_acceptance(self, checkpoint_id):
        review = {"fixture_review": len(self.state["acceptances"])}
        self.content.put_blob(review)
        identity = "acceptance-{:06d}".format(len(self.state["acceptances"]) + 1)
        self.state["acceptances"][identity] = {"status": "accepted", "checkpoint_id": checkpoint_id,
                                                "review": review, "dependency_ids": []}
        return identity

    def count_proofs(self, reads):
        counts = Counter(call.args[0] for call in reads.call_args_list)
        return {digest: counts[proof] for digest, proof in self.proofs.items()}

    def audit_modes(self, digest, modes):
        for _ in modes:
            identity = self.make_checkpoint(digest)
            self.add_acceptance(identity)
        original = evidence.audit_checkpoint
        selected = iter(modes)

        def audit_selected_mode(*args, **kwargs):
            verify, fresh = next(selected)
            kwargs.update(verify=verify, fresh=fresh)
            return original(*args, **kwargs)

        with patch.object(evidence, "audit_checkpoint", side_effect=audit_selected_mode), \
                patch("search_controller.provenance.audit_probe_credit"):
            return evidence.audit_state(self.root, self.state, self.content)

    def test_layered_graph_validates_each_of_25_manifests_once(self):
        roots = [self.make_manifest("leaf-" + str(side)) for side in range(2)]
        for level in range(11):
            roots = [self.make_manifest("level-{}-{}".format(level, side), roots) for side in range(2)]
        root = self.make_manifest("root", roots)
        with patch.object(self.content, "get_blob", wraps=self.content.get_blob) as blobs, \
                patch.object(self.content, "get_artifact", wraps=self.content.get_artifact) as artifacts:
            self.assertEqual(evidence.audit_manifest(self.root, self.state, root, self.content), ("analytical", "reviewed"))
        counts = Counter(call.args[0] for call in blobs.call_args_list)
        self.assertEqual(set(counts), set(self.proofs))
        self.assertEqual(len(counts), 25)
        self.assertEqual(set(counts.values()), {1})
        self.assertEqual(artifacts.call_count, len(counts))

    def test_checkpoint_reuses_shared_descendants_across_roots(self):
        leaf = self.make_manifest("leaf")
        roots = [self.make_manifest("root-" + str(index), [leaf]) for index in range(4)]
        checkpoint = self.state["checkpoints"][self.make_checkpoint(*roots)]
        with patch.object(self.content, "get_artifact", wraps=self.content.get_artifact) as reads:
            self.assertEqual(evidence.audit_checkpoint(self.root, self.state, checkpoint, self.content), ("analytical", "reviewed"))
        self.assertEqual(set(self.count_proofs(reads).values()), {1})

    def test_dependencies_reuse_across_checkpoints_explicit_roots_and_acceptances(self):
        leaf = self.make_manifest("leaf")
        roots = [self.make_manifest("root-" + str(index), [leaf]) for index in range(3)]
        checkpoints = [self.make_checkpoint(root) for root in roots]
        for identity in checkpoints:
            self.add_acceptance(identity)
        with patch.object(self.content, "get_artifact", wraps=self.content.get_artifact) as reads:
            evidence.audit_dependencies(self.root, self.state, self.content, checkpoints, manifest_digests=roots)
        self.assertEqual(set(self.count_proofs(reads).values()), {1})
        self.assertEqual(self.reviews.call_count, 3)

    def test_state_reuses_roots_but_next_invocation_detects_changed_external_bytes(self):
        external = self.root / "dependency.txt"
        external.write_bytes(b"Pinned external dependency")
        leaf = self.make_manifest("leaf", external=[{"path": str(external), "digest": evidence.file_digest(external)}])
        for index in range(3):
            root = self.make_manifest("root-" + str(index), [leaf])
            self.add_acceptance(self.make_checkpoint(root))
        with patch.object(self.content, "get_artifact", wraps=self.content.get_artifact) as reads:
            self.assertEqual(evidence.audit_state(self.root, self.state, self.content), {})
        self.assertEqual(set(self.count_proofs(reads).values()), {1})
        external.write_bytes(b"Changed before the next audit invocation")
        failures = evidence.audit_state(self.root, self.state, self.content)
        self.assertEqual(set(failures), set(self.state["acceptances"]))
        self.assertEqual({row["code"] for row in failures.values()}, {"digest_mismatch"})

    def test_completed_root_still_checks_each_incoming_claim(self):
        root = self.make_manifest("root")
        checkpoint = self.make_checkpoint(root)
        self.add_acceptance(checkpoint)
        self.add_acceptance(checkpoint)
        wrong = self.make_checkpoint(root, claim={"statement": "A different claim"})
        failed = self.add_acceptance(wrong)
        with patch.object(self.content, "get_artifact", wraps=self.content.get_artifact) as reads:
            failures = evidence.audit_state(self.root, self.state, self.content)
        self.assertEqual({key: value["code"] for key, value in failures.items()}, {failed: "claim_mismatch"})
        self.assertEqual(self.count_proofs(reads)[root], 1)

    def test_stronger_verification_rechecks_a_completed_weaker_mode(self):
        root = self.make_manifest("certificate", kind="certificate")
        with patch.object(self.content, "get_blob", wraps=self.content.get_blob) as reads:
            failures = self.audit_modes(root, [(False, False), (False, False), (True, False)])
        self.assertEqual({key: row["code"] for key, row in failures.items()}, {"acceptance-000003": "verification_required"})
        self.assertEqual(Counter(call.args[0] for call in reads.call_args_list)[root], 2)

    def test_fresh_mode_rechecks_origins_after_completed_historical_mode(self):
        root = self.make_manifest("root")
        self.origins.return_value = [{"probe": "exploratory"}]
        with patch.object(self.content, "get_blob", wraps=self.content.get_blob) as reads:
            failures = self.audit_modes(root, [(True, False), (True, False), (True, True)])
        self.assertEqual({key: row["code"] for key, row in failures.items()}, {"acceptance-000003": "exploratory_evidence"})
        self.assertEqual(Counter(call.args[0] for call in reads.call_args_list)[root], 2)

    def test_verify_and_fresh_are_independent_validation_modes(self):
        root = self.make_manifest("certificate", kind="certificate")
        with patch.object(self.content, "get_blob", wraps=self.content.get_blob) as reads:
            failures = self.audit_modes(root, [(False, True), (False, True), (True, False)])
        self.assertEqual({key: row["code"] for key, row in failures.items()}, {"acceptance-000003": "verification_required"})
        self.assertEqual(Counter(call.args[0] for call in reads.call_args_list)[root], 2)

    def test_completed_root_checks_cycle_before_reuse(self):
        root = self.make_manifest("root")
        checkpoint = self.make_checkpoint(root)
        for _ in range(3):
            self.add_acceptance(checkpoint)
        original = evidence.audit_checkpoint
        calls = []

        def audit_incoming_path(*args, **kwargs):
            calls.append(None)
            if len(calls) < 3:
                return original(*args, **kwargs)
            kwargs.pop("verify")
            return evidence.audit_manifest(self.root, self.state, root, self.content, visiting={root}, **kwargs)

        with patch.object(evidence, "audit_checkpoint", side_effect=audit_incoming_path), \
                patch.object(self.content, "get_blob", wraps=self.content.get_blob) as reads:
            failures = evidence.audit_state(self.root, self.state, self.content)
        self.assertEqual({key: row["code"] for key, row in failures.items()}, {"acceptance-000003": "dependency_cycle"})
        self.assertEqual(Counter(call.args[0] for call in reads.call_args_list)[root], 1)

    def test_completed_subtree_retains_depth_for_a_longer_incoming_path(self):
        leaf = self.make_manifest("leaf")
        subtree = self.make_manifest("subtree", [leaf])
        checkpoint = self.make_checkpoint(subtree)
        self.add_acceptance(checkpoint)
        self.add_acceptance(checkpoint)
        root = subtree
        for index in range(255):
            root = self.make_manifest("deep-" + str(index), [root])
        failed = self.add_acceptance(self.make_checkpoint(root))
        with patch.object(self.content, "get_blob", wraps=self.content.get_blob) as reads:
            failures = evidence.audit_state(self.root, self.state, self.content)
        self.assertEqual({key: row["code"] for key, row in failures.items()}, {failed: "dependency_cycle"})
        self.assertEqual(Counter(call.args[0] for call in reads.call_args_list)[subtree], 1)

    def test_completed_claim_mismatch_precedes_descendant_depth_failure(self):
        leaf = self.make_manifest("leaf")
        root = self.make_manifest("root", [leaf])
        checkpoint = self.make_checkpoint(root)
        for _ in range(3):
            self.add_acceptance(checkpoint)
        original = evidence.audit_checkpoint
        calls = []

        def audit_incoming_claim(*args, **kwargs):
            calls.append(None)
            if len(calls) < 3:
                return original(*args, **kwargs)
            kwargs.pop("verify")
            return evidence.audit_manifest(self.root, self.state, root, self.content,
                                           claim_digest="b" * 64, visiting=set(range(255)), **kwargs)

        with patch.object(evidence, "audit_checkpoint", side_effect=audit_incoming_claim), \
                patch.object(self.content, "get_blob", wraps=self.content.get_blob) as reads:
            failures = evidence.audit_state(self.root, self.state, self.content)
        self.assertEqual({key: row["code"] for key, row in failures.items()}, {"acceptance-000003": "claim_mismatch"})
        self.assertEqual(Counter(call.args[0] for call in reads.call_args_list)[root], 1)

    def test_completed_subtree_accepts_exactly_256_manifest_path(self):
        leaf = self.make_manifest("leaf")
        self.add_acceptance(self.make_checkpoint(leaf))
        root = leaf
        for index in range(255):
            root = self.make_manifest("deep-" + str(index), [root])
        self.add_acceptance(self.make_checkpoint(root))
        with patch.object(self.content, "get_artifact", wraps=self.content.get_artifact) as reads:
            self.assertEqual(evidence.audit_state(self.root, self.state, self.content), {})
        self.assertEqual(set(self.count_proofs(reads).values()), {1})

    def test_failed_validation_does_not_become_a_completed_result(self):
        root = self.make_manifest("root")
        checkpoint = self.make_checkpoint(root)
        failed = self.add_acceptance(checkpoint)
        self.add_acceptance(checkpoint)
        original = self.content.get_artifact
        attempted = []

        def read_repaired_artifact(digest):
            if digest == self.proofs[root]:
                attempted.append(digest)
                if len(attempted) == 1:
                    return b""
            return original(digest)

        with patch.object(self.content, "get_artifact", side_effect=read_repaired_artifact):
            failures = evidence.audit_state(self.root, self.state, self.content)
        self.assertEqual({key: row["code"] for key, row in failures.items()}, {failed: "missing_evidence"})
        self.assertEqual(len(attempted), 2)

    def test_failed_parent_is_retried_after_a_successful_descendant(self):
        leaf = self.make_manifest("leaf")
        root = self.make_manifest("root", [leaf])
        checkpoint = self.make_checkpoint(root)
        failed = self.add_acceptance(checkpoint)
        self.add_acceptance(checkpoint)
        original = self.content.get_blob
        attempted = []

        def read_repaired_manifest(digest):
            manifest = original(digest)
            if digest == root:
                attempted.append(digest)
                if len(attempted) == 1:
                    return dict(manifest, verification={})
            return manifest

        with patch.object(self.content, "get_blob", side_effect=read_repaired_manifest), \
                patch.object(self.content, "get_artifact", wraps=self.content.get_artifact) as reads:
            failures = evidence.audit_state(self.root, self.state, self.content)
        self.assertEqual({key: row["code"] for key, row in failures.items()}, {failed: "invalid_record"})
        self.assertEqual(len(attempted), 2)
        self.assertEqual(self.count_proofs(reads)[leaf], 1)

    def test_checkpoint_specific_sources_are_checked_on_completed_manifest_hits(self):
        root = self.make_manifest("root")
        checkpoint = self.make_checkpoint(root)
        self.add_acceptance(checkpoint)
        self.add_acceptance(checkpoint)
        original = self.content.get_artifact
        sources = []

        def read_changed_source(digest):
            if digest == self.source:
                sources.append(digest)
                if len(sources) == 2:
                    raise SearchError("corrupt_artifact", "Source changed between checkpoint audits")
            return original(digest)

        with patch.object(self.content, "get_artifact", side_effect=read_changed_source):
            failures = evidence.audit_state(self.root, self.state, self.content)
        self.assertEqual({key: row["code"] for key, row in failures.items()}, {"acceptance-000002": "corrupt_artifact"})
        self.assertEqual(len(sources), 2)

    def test_separate_manifest_invocations_detect_corrupt_immutable_bytes(self):
        root = self.make_manifest("root")
        self.assertEqual(evidence.audit_manifest(self.root, self.state, root, self.content), ("analytical", "reviewed"))
        (self.content.root / "artifacts" / self.proofs[root]).chmod(0o644)
        (self.content.root / "artifacts" / self.proofs[root]).write_bytes(b"Corrupt after preparation")
        with self.assertRaises(SearchError) as caught:
            evidence.audit_manifest(self.root, self.state, root, self.content)
        self.assertEqual(caught.exception.code, "corrupt_artifact")


if __name__ == "__main__":
    unittest.main()
