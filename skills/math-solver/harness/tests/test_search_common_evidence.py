"""Fresh native commitments require common openness evidence and probe provenance."""

import copy
import json
import unittest
from unittest.mock import patch

from research_harness.artifacts import ArtifactStore
from research_harness.principles import initialize_research
from research_harness.probe import record_probe, record_probe_intent
from research_harness.storage import Store as CommonStore
from search_controller.errors import SearchError
from search_controller.service import Controller
from tests.native_reviewer_support import attest_spec, mutate
from tests.search_fixtures import digest
from tests.search_fixtures import review as proposal_review
from tests.test_search_cli import SearchCLIWorkspace
from tests import test_search_evidence as evidence_tests
from tests.openness_support import attach_minimum, open_common_case
from tests import test_search_openness_closure as closure_tests
from tests import test_search_execution_proof as certified_tests
from tests.support import OPENING, WorkspaceTest, run


class CommonEvidenceTests(SearchCLIWorkspace, unittest.TestCase):
    artifact = evidence_tests.EvidenceTests.artifact
    external_checkpoint = evidence_tests.EvidenceTests.external_checkpoint
    setup_external = evidence_tests.EvidenceTests.setup_external
    result_review = evidence_tests.EvidenceTests.result_review

    def build_acceptance(self, outcome="supported_open", **options):
        controller = Controller(self.root)
        checkpoint = controller.status()["checkpoints"]["checkpoint-000001"]
        review = self.result_review(checkpoint, checkpoint["claim"])
        review["findings"]["openness"]["outcome"] = outcome
        review = attach_minimum(controller, review, **options)
        return {"obligation_id": "obligation-000001", "outcome": "proof", "dependency_ids": [],
                "route_bindings": [], "review": review, "inputs": []}

    def assert_acceptance_refused(self, spec, code):
        controller = Controller(self.root)
        attest_spec(controller, spec)
        before = controller.store.read()
        result = self.search("accept", spec, target="checkpoint-000001", revision=2, success=False)
        self.assertEqual(result["error"]["code"], code)
        self.assertEqual(controller.store.read(), before)

    def export_packet(self, spec):
        """Export the result packet of the acceptance review directly, as observe_review does."""
        review = spec["review"]
        controller = Controller(self.root)
        reviewer = dict(review["reviewer"], source="host", attestation_id="review-assignment:direct-export")
        return controller.command("review-packet", {
            "kind": "result", "subject": controller.status()["checkpoints"]["checkpoint-000001"],
            "claim_digest": review["claim_digest"], "reviewer": reviewer, "inputs": [],
            "openness": review["findings"]["openness"]["minimum"]}, None, None)

    def assert_export_refused(self, spec, code, details):
        """review-packet refuses the minimum before it writes the packet or any common artifact."""
        controller = Controller(self.root)
        objects = self.root / "research/sources/objects"
        before = (controller.store.read(), sorted(objects.iterdir()))
        with self.assertRaises(SearchError) as caught:
            self.export_packet(spec)
        self.assertEqual((caught.exception.code, caught.exception.details), (code, details))
        self.assertEqual((controller.store.read(), sorted(objects.iterdir())), before)

    def find_search_id(self, search_digest):
        return next(row["id"] for row in CommonStore(self.root).snapshot()["records"]["literature_search"].values()
                    if digest(row) == search_digest)

    def test_statement_and_neighbour_searches_with_full_readings_allow_resolved_acceptance(self):
        self.setup_external()
        spec = self.build_acceptance(neighbours=["Every requested dimension satisfies the equivalent bound."], relevant=True)
        self.search("accept", spec, target="checkpoint-000001", revision=2)
        self.assertEqual(Controller(self.root).status(full_audit=True)["acceptances"]["acceptance-000001"]["status"], "accepted")

    def test_declared_neighbour_without_a_bound_search_is_a_gap(self):
        self.setup_external()
        neighbour = "The equivalent neighbouring formulation."
        spec = self.build_acceptance(neighbours=[neighbour])
        finding = spec["review"]["findings"]["openness"]
        removed = finding["minimum"]["searches"].pop()
        finding["evidence"].remove(removed["search_digest"])
        self.assert_export_refused(spec, "openness_search_missing", {"formulations": [neighbour]})

    def test_a_neighbour_bound_to_the_statement_search_is_refused_at_export(self):
        self.setup_external()
        neighbour = "The equivalent neighbouring formulation."
        spec = self.build_acceptance(neighbours=[neighbour])
        statement, bound = spec["review"]["findings"]["openness"]["minimum"]["searches"]
        bound["search_digest"] = statement["search_digest"]
        self.assert_export_refused(spec, "openness_statement_mismatch", {
            "formulation": neighbour, "search_id": self.find_search_id(statement["search_digest"]),
            "search_digest": statement["search_digest"]})

    def test_a_binding_to_a_search_digest_without_a_record_is_refused_at_export(self):
        self.setup_external()
        spec = self.build_acceptance()
        binding = spec["review"]["findings"]["openness"]["minimum"]["searches"][0]
        binding["search_digest"] = "a" * 64
        self.assert_export_refused(spec, "openness_search_missing",
                                   {"formulation": binding["formulation"], "search_digest": "a" * 64})

    def test_another_statement_an_unknown_reading_or_an_unknown_claim_is_refused_at_export(self):
        self.setup_external()
        spec = self.build_acceptance()
        minimum = spec["review"]["findings"]["openness"]["minimum"]
        minimum["statement"] = "Another exact statement."
        self.assert_export_refused(spec, "openness_statement_mismatch", None)
        spec = self.build_acceptance()
        spec["review"]["findings"]["openness"]["minimum"]["reading_digests"] = ["b" * 64]
        self.assert_export_refused(spec, "openness_evidence_mismatch", {"reading_digests": ["b" * 64]})
        spec = self.build_acceptance()
        spec["review"]["claim_digest"] = "c" * 64
        self.assert_export_refused(spec, "claim_mismatch", None)

    def test_relevant_source_without_full_reading_keeps_openness_unresolved(self):
        self.setup_external()
        spec = self.build_acceptance(relevant=True, read=False)
        self.assert_acceptance_refused(spec, "openness_reading_gap")
        spec["review"]["reviewer"]["attestation_id"] = "new-observation"
        spec["review"]["findings"]["openness"]["outcome"] = "unresolved"
        self.search("accept", spec, target="checkpoint-000001", revision=2)

    def test_finding_date_cannot_precede_recorded_search_capture(self):
        self.setup_external()
        spec = self.build_acceptance()
        spec["review"]["findings"]["openness"]["searched_at"] = "2026-10-14"
        self.assert_acceptance_refused(spec, "openness_search_date")

    def test_unavailable_source_does_not_waive_the_full_reading_minimum(self):
        from research_harness.reading import record_availability
        self.setup_external()
        spec = self.build_acceptance(relevant=True, read=False)
        case = open_common_case(Controller(self.root))
        work = next(iter(case.store.snapshot()["records"]["work"]))
        capture = case.capture(work, status=404)
        case.mutate(record_availability, {"id": "unavailable", "profile": "research", "version_id": work,
            "depth": "fulltext", "source_ids": [capture["source_id"]], "reason": "The exact original returned 404.",
            "policy": {"id": "recorded-terminal-attempt", "minimum_attempts": 1, "allowed_statuses": [404],
                       "rationale": "Captured origin unavailability is a gap, not reading credit."}})
        self.assert_acceptance_refused(spec, "openness_reading_gap")

    def test_authorized_source_deferral_does_not_resolve_openness(self):
        from research_harness.reading import require_fulltext
        from research_harness.source_deferrals import defer_source, assess_deferrals
        self.setup_external()
        spec = self.build_acceptance(relevant=True, read=False)
        case = open_common_case(Controller(self.root))
        work = next(iter(case.store.snapshot()["records"]["work"]))
        case.mutate(require_fulltext, {"id": "decisive-source", "profile": "research", "version_id": work,
            "purpose": "validity", "reason": "The exact openness conclusion relies on this prior source."})
        case.mutate(defer_source, {"id": "authorized-gap", "profile": "research", "version_id": work,
            "reason": "The original is unavailable.", "authorization": case.artifacts.put(b"Continue with this source gap.", "text/plain"),
            "acquisition_evidence": [case.artifacts.put(b"Authored retrieval log: no original was obtained.", "text/plain")],
            "dependent_claims": ["Openness cannot be settled without this source."],
            "continuing_work": ["Verify proof validity while openness stays pending."]})
        self.assertEqual(assess_deferrals(case.store.snapshot()["records"], case.artifacts)[0]["status"], "active")
        self.assert_acceptance_refused(spec, "openness_reading_gap")

    def test_already_solved_requires_a_bound_fully_read_settling_work(self):
        self.setup_external()
        spec = self.build_acceptance("already_solved")
        spec["review"]["findings"]["openness"]["minimum"]["settling_work_id"] = None
        self.assert_acceptance_refused(spec, "openness_settling_source")

    def test_fresh_root_closure_requires_the_common_minimum_even_after_proof_acceptance(self):
        self.setup_external()
        checkpoint = Controller(self.root).status()["checkpoints"]["checkpoint-000001"]
        spec = {"obligation_id": "obligation-000001", "outcome": "proof", "dependency_ids": [],
                "route_bindings": [], "review": self.result_review(checkpoint, checkpoint["claim"]), "inputs": []}
        self.search("accept", spec, target="checkpoint-000001", revision=2)
        closure = closure_tests.OpennessClosureTests.build_closure_spec(self)
        minimum = closure["review"]["findings"]["openness"].pop("minimum")
        removed = {row["search_digest"] for row in minimum["searches"]} | set(minimum["reading_digests"])
        closure["review"]["findings"]["openness"]["evidence"] = [identity for identity in closure["review"]["findings"]["openness"]["evidence"] if identity not in removed]
        attest_spec(Controller(self.root), closure)
        result = self.search("complete", closure, revision=3, success=False)
        self.assertEqual(result["error"]["code"], "openness_minimum_required")
        closure["review"]["reviewer"]["attestation_id"] = "new-final-observation"
        closure["review"] = attach_minimum(Controller(self.root), closure["review"], subject=closure["subject"])
        self.search("complete", closure, revision=3)
        self.assertEqual(Controller(self.root).status()["proof_status"], "proved")

    def test_manifest_only_supported_open_cannot_authorize_fresh_acceptance(self):
        self.setup_external()
        controller = Controller(self.root)
        checkpoint = controller.status()["checkpoints"]["checkpoint-000001"]
        review = self.result_review(checkpoint, checkpoint["claim"])
        review["findings"]["openness"].update(outcome="supported_open", evidence=checkpoint["evidence_digests"])
        spec = {"obligation_id": "obligation-000001", "outcome": "proof", "dependency_ids": [],
                "route_bindings": [], "review": review, "inputs": []}
        attest_spec(controller, spec)
        before = controller.store.read()
        result = self.search("accept", spec, target=checkpoint["id"], revision=2, success=False)
        self.assertEqual(result["error"]["code"], "openness_minimum_required")
        self.assertEqual(controller.store.read(), before)

    def record_probe_output(self, data):
        store = CommonStore(self.root, create=True)
        if "research" not in store.snapshot()["records"].get("configuration", {}):
            mutate(store, initialize_research, {"profile": "research", "target": None})
        artifacts = ArtifactStore(self.root)
        mutate(store, record_probe_intent, {"id": "explore", "question": "What does this finite example show?",
               "interpreter": "python", "program": artifacts.put(b"print('exploratory')\n", "text/x-python"),
               "inputs": [], "timeout_seconds": 2})
        output = artifacts.put(data, "text/plain")
        mutate(store, record_probe, {"id": "explore", "command": "python example.py", "outputs": [output],
               "stdout": output, "stderr": artifacts.put(b"", "text/plain"), "exit_status": 0,
               "elapsed_seconds": 0.1, "observation": "Exploratory output, without proof credit."})
        return output

    def build_probe_checkpoint(self):
        spec = self.external_checkpoint()
        output = self.record_probe_output(b"Unchanged exploratory output presented as an analytical proof.")
        spec["inputs"][0] = self.artifact("copied-proof.txt", ArtifactStore(self.root).read(output).decode())
        manifest = json.loads((self.root / "manifest.json").read_text())
        manifest["artifacts"][0].update(path="copied-proof.txt", digest=output["sha256"])
        (self.root / "manifest.json").write_text(json.dumps(manifest))
        spec["inputs"][-1]["digest"] = digest(manifest)
        spec["checkpoint"]["evidence_digests"] = [digest(manifest)]
        return spec

    def test_copied_probe_output_cannot_become_an_external_analytical_proof(self):
        self.initialize()
        spec = self.build_probe_checkpoint()
        controller = Controller(self.root)
        before = controller.store.read()
        result = self.search("checkpoint", spec, revision=1, success=False)
        self.assertEqual(result["error"]["code"], "exploratory_evidence")
        self.assertEqual(controller.store.read(), before)

    def test_probe_hypothesis_import_retains_origins_without_proof_credit(self):
        self.initialize()
        spec = self.build_probe_checkpoint()
        spec["checkpoint"]["kind"] = "hypothesis"
        self.search("checkpoint", spec, revision=1)
        state = Controller(self.root).status()
        identity = spec["inputs"][0]["digest"]
        self.assertEqual(state["service"].get("probe_origins", {}).get(identity), [
            {"probe_id": "explore", "sha256": identity, "roles": ["outputs", "stdout"]}])
        self.assertEqual(state["acceptances"], {})

    def test_probe_json_import_as_a_canonical_blob_cannot_erase_its_origin(self):
        self.initialize()
        spec = self.external_checkpoint()
        raw = (self.root / "manifest.json").read_bytes()
        self.record_probe_output(raw)
        # Blob imports canonicalize JSON, so the manifest identity differs from
        # the unchanged captured file's SHA-256. The import must retain both.
        controller = Controller(self.root)
        before = controller.store.read()
        result = self.search("checkpoint", spec, revision=1, success=False)
        self.assertEqual(result["error"]["code"], "exploratory_evidence")
        self.assertEqual(controller.store.read(), before)

    def test_late_probe_origin_blocks_fresh_reliance_but_preserves_historical_acceptance(self):
        self.setup_external()
        checkpoint = Controller(self.root).status()["checkpoints"]["checkpoint-000001"]
        spec = {"obligation_id": "obligation-000001", "outcome": "proof", "dependency_ids": [],
                "route_bindings": [], "review": self.result_review(checkpoint, checkpoint["claim"]), "inputs": []}
        self.search("accept", spec, target="checkpoint-000001", revision=2)
        controller = Controller(self.root)
        document = controller.store.read()
        self.record_probe_output((self.root / "proof.md").read_bytes())
        self.assertEqual(controller.store.read(), document)
        self.assertEqual(controller.status(full_audit=True)["acceptances"]["acceptance-000001"]["status"], "accepted")
        result = self.search("accept", spec, target="checkpoint-000001", revision=3, success=False)
        self.assertEqual(result["error"]["code"], "audit_failed")

    def test_recorded_strategy_cannot_rely_on_a_probe_only_hypothesis(self):
        from search_controller.errors import SearchError
        from tests.search_execution_support import admit_workspace
        from tests.strategy_refresh_support import reassess_fixture
        self.assertEqual(run(["init", "sample"], self.root)[0], 0)
        controller = admit_workspace(self.root)
        spec = self.build_probe_checkpoint()
        spec["checkpoint"]["kind"] = "hypothesis"
        self.search("checkpoint", spec, revision=controller.status()["revision"])
        before = controller.store.read()
        with self.assertRaises(SearchError) as caught:
            reassess_fixture(controller, [("node-000001", OPENING)])
        self.assertEqual(caught.exception.code, "exploratory_evidence")
        self.assertEqual(controller.store.read(), before)

    def test_fresh_move_rechecks_the_recorded_strategy_when_a_probe_origin_is_later_known(self):
        from search_controller.errors import SearchError
        from tests.search_execution_support import admit_workspace, begin_spec, invoke
        from tests.strategy_refresh_support import reassess_fixture
        self.assertEqual(run(["init", "sample"], self.root)[0], 0)
        controller = admit_workspace(self.root)
        spec = self.external_checkpoint()
        spec["checkpoint"]["kind"] = "hypothesis"
        self.search("checkpoint", spec, revision=controller.status()["revision"])
        reassess_fixture(controller, [("node-000001", OPENING)])
        self.record_probe_output((self.root / "proof.md").read_bytes())
        before = controller.store.read()
        with self.assertRaises(SearchError) as caught:
            invoke(controller, "begin", begin_spec())
        self.assertEqual(caught.exception.code, "exploratory_evidence")
        self.assertEqual(controller.store.read(), before)

    def test_admission_cannot_rely_on_probe_output_relabelled_as_a_study(self):
        self.initialize()
        prepared = self.prepared_proposal()
        self.record_probe_output((self.root / "problem.md").read_bytes())
        self.search("propose", prepared, revision=1)
        self.search("review", {"proposal_id": "proposal-000001", "review": proposal_review(prepared["proposal"]), "inputs": []}, revision=2)
        controller = Controller(self.root)
        before = controller.store.read()
        result = self.search("admit", {}, target="proposal-000001", revision=3, success=False)
        self.assertEqual(result["error"]["code"], "exploratory_evidence")
        self.assertEqual(controller.store.read(), before)

    def test_original_openness_prompt_keeps_its_observed_import_review_usable(self):
        from research_harness.native_review import OPENNESS_PROMPT
        self.setup_external()
        controller = Controller(self.root)
        checkpoint = controller.status()["checkpoints"]["checkpoint-000001"]
        spec = {"obligation_id": "obligation-000001", "outcome": "proof", "dependency_ids": [],
                "route_bindings": [], "review": self.result_review(checkpoint, checkpoint["claim"]), "inputs": []}
        with patch("research_harness.native_review.PROMPT", OPENNESS_PROMPT):
            attest_spec(controller, spec)
        self.search("accept", spec, target="checkpoint-000001", revision=2)
        self.assertEqual(controller.status(full_audit=True)["acceptances"]["acceptance-000001"]["status"], "accepted")

    def test_new_relevant_search_requires_reassessment_of_an_older_empty_search(self):
        self.setup_external()
        spec = self.build_acceptance()
        material = self.build_acceptance(relevant=True, read=False)
        searches = material["review"]["findings"]["openness"]["minimum"]["searches"]
        self.assert_export_refused(spec, "openness_search_stale",
                                   {"search_ids": [self.find_search_id(searches[0]["search_digest"])]})

    def test_a_relevant_search_recorded_after_the_export_requires_reassessment_at_acceptance(self):
        self.setup_external()
        spec = self.build_acceptance()
        attest_spec(Controller(self.root), spec)
        self.build_acceptance(relevant=True, read=False)
        self.assert_acceptance_refused(spec, "openness_search_stale")

    def test_later_full_reading_and_explicit_disposition_resolve_a_historical_search_gap(self):
        self.setup_external()
        old = self.build_acceptance(relevant=True, read=False, disposition="potentially_relevant")
        work = next(iter(CommonStore(self.root).snapshot()["records"]["work"]))
        spec = self.build_acceptance(relevant=True, read=True, search_tag="after-full-reading", resolved=[work])
        finding = spec["review"]["findings"]["openness"]
        previous = old["review"]["findings"]["openness"]
        finding["minimum"]["searches"].extend(previous["minimum"]["searches"])
        finding["evidence"] = list(dict.fromkeys(finding["evidence"] + previous["evidence"]))
        self.search("accept", spec, target="checkpoint-000001", revision=2)

    def test_imported_search_record_still_delivers_its_captured_response(self):
        from search_controller.storage import canonical_bytes
        from tests.native_reviewer_support import observe_review
        self.setup_external()
        spec = self.build_acceptance()
        records = CommonStore(self.root).snapshot()["records"]
        search = next(iter(records["literature_search"].values()))
        raw = canonical_bytes(search)
        (self.root / "search-record.json").write_bytes(raw)
        spec["inputs"] = [{"path": "search-record.json", "kind": "artifact", "digest": digest(search)}]
        controller = Controller(self.root)
        subject = controller.status()["checkpoints"]["checkpoint-000001"]
        spec["review"] = observe_review(controller, spec["review"], subject, inputs=spec["inputs"])
        assignment_id = spec["review"]["reviewer"]["attestation_id"].split(":", 1)[1]
        assignment = CommonStore(self.root).snapshot()["records"]["review_assignment"][assignment_id]
        packet = json.loads(ArtifactStore(self.root).read(assignment["context"]["native_packet"]))
        delivered = {row["digest"] for row in packet["evidence"]}
        for response in search["responses"]:
            source = records["source"][response["source_id"]]
            self.assertIn(digest(source), delivered)
            self.assertIn(source["response"]["sha256"], delivered)

    def build_resolved_acceptance(self, disposition, *, duplicate_of=None, read=False):
        self.setup_external()
        old = self.build_acceptance(relevant=True, read=False, disposition="potentially_relevant")
        work = "arxiv:2601.99991v1"
        spec = self.build_acceptance(relevant=True, read=read, search_tag="resolved-hit",
                                     disposition=disposition, resolved=[work], duplicate_of=duplicate_of)
        finding = spec["review"]["findings"]["openness"]
        previous = old["review"]["findings"]["openness"]
        finding["minimum"]["searches"].extend(previous["minimum"]["searches"])
        finding["evidence"] = list(dict.fromkeys(finding["evidence"] + previous["evidence"]))
        return spec

    def test_explicit_out_of_scope_resolution_removes_the_uncertain_hits_reading_obligation(self):
        spec = self.build_resolved_acceptance("out_of_scope")
        self.search("accept", spec, target="checkpoint-000001", revision=2)
        records = CommonStore(self.root).snapshot()["records"]
        self.assertFalse(records.get("reading"))
        self.assertEqual(len(records["literature_search"]), 2)

    def test_duplicate_resolution_requires_only_its_fully_read_decisive_canonical_source(self):
        spec = self.build_resolved_acceptance("duplicate", duplicate_of=99992, read=True)
        self.search("accept", spec, target="checkpoint-000001", revision=2)
        readings = CommonStore(self.root).snapshot()["records"]["reading"].values()
        self.assertEqual({row["version_id"] for row in readings}, {"arxiv:2601.99992v1"})

    def test_duplicate_resolution_without_a_canonical_full_reading_remains_a_gap(self):
        spec = self.build_resolved_acceptance("duplicate", duplicate_of=99992)
        self.assert_acceptance_refused(spec, "openness_reading_gap")

    def test_duplicate_resolution_without_an_explicit_canonical_source_remains_a_gap(self):
        spec = self.build_resolved_acceptance("duplicate")
        self.assert_acceptance_refused(spec, "openness_reading_gap")

    def test_omitting_a_new_duplicate_search_cannot_hide_its_unread_canonical_source(self):
        self.setup_external()
        spec = self.build_acceptance()
        duplicate = self.build_acceptance(relevant=True, read=False, disposition="duplicate",
                                          search_tag="new-duplicate")
        searches = duplicate["review"]["findings"]["openness"]["minimum"]["searches"]
        self.assert_export_refused(spec, "openness_search_stale",
                                   {"search_ids": [self.find_search_id(searches[0]["search_digest"])]})

    def test_a_duplicate_search_recorded_after_the_export_cannot_hide_its_unread_canonical_source(self):
        self.setup_external()
        spec = self.build_acceptance()
        attest_spec(Controller(self.root), spec)
        self.build_acceptance(relevant=True, read=False, disposition="duplicate", search_tag="new-duplicate")
        self.assert_acceptance_refused(spec, "openness_search_stale")

    def test_manifest_shaped_artifact_cannot_hide_a_probe_digest_under_verification(self):
        self.initialize()
        spec = self.external_checkpoint()
        output = self.record_probe_output(b"A computational exploratory result.")
        wrapper = {"schema_version": 1, "kind": "analytical", "artifacts": [], "dependencies": [],
                   "verification": {"untrusted_result": output["sha256"]}}
        wrapped = self.artifact("wrapper.json", json.dumps(wrapper))
        manifest = json.loads((self.root / "manifest.json").read_text())
        manifest["artifacts"][0].update(path=wrapped["path"], digest=wrapped["digest"])
        (self.root / "manifest.json").write_text(json.dumps(manifest))
        spec["inputs"][0] = wrapped
        # The referenced bytes are delivered too, exactly as a packet would do.
        spec["inputs"].append(self.artifact("probe.txt", "A computational exploratory result."))
        spec["inputs"][-2]["digest"] = digest(manifest)
        spec["checkpoint"]["evidence_digests"] = [digest(manifest)]
        result = self.search("checkpoint", spec, revision=1, success=False)
        self.assertEqual(result["error"]["code"], "exploratory_evidence")

    def test_arbitrary_scientific_json_can_use_a_list_valued_run_id(self):
        self.initialize()
        spec = self.external_checkpoint()
        artifact = self.artifact("proof.json", json.dumps({"verification": {"run_id": ["trial-a", "trial-b"]}}))
        manifest = json.loads((self.root / "manifest.json").read_text())
        manifest["artifacts"][0].update(path=artifact["path"], digest=artifact["digest"])
        (self.root / "manifest.json").write_text(json.dumps(manifest))
        spec["inputs"][0] = artifact
        spec["inputs"][-1]["digest"] = digest(manifest)
        spec["checkpoint"]["evidence_digests"] = [digest(manifest)]
        self.search("checkpoint", spec, revision=1)

    def test_late_raw_probe_origin_cannot_escape_a_canonical_blob_identity(self):
        self.setup_external()
        self.record_probe_output((self.root / "manifest.json").read_bytes())
        checkpoint = Controller(self.root).status()["checkpoints"]["checkpoint-000001"]
        spec = {"obligation_id": "obligation-000001", "outcome": "proof", "dependency_ids": [],
                "route_bindings": [], "review": self.result_review(checkpoint, checkpoint["claim"]), "inputs": []}
        self.assert_acceptance_refused(spec, "exploratory_evidence")

    def assert_strategy_acceptance_origin_refused(self, *, through_checkpoint=False):
        from search_controller.errors import SearchError
        from search_controller.provenance import audit_strategy_inputs
        from search_controller.storage import ContentSink
        self.setup_external()
        controller = Controller(self.root)
        checkpoint = controller.status()["checkpoints"]["checkpoint-000001"]
        raw = (self.root / "proof.md").read_bytes()
        spec = {"obligation_id": "obligation-000001", "outcome": "proof", "dependency_ids": [],
                "route_bindings": [], "review": self.result_review(checkpoint, checkpoint["claim"]), "inputs": []}
        self.search("accept", spec, target=checkpoint["id"], revision=2)
        reference = "acceptance:acceptance-000001"
        if through_checkpoint:
            spec = self.external_checkpoint()
            artifact = self.artifact("bridge.md", "A new analytical deduction from the accepted premise.")
            manifest = json.loads((self.root / "manifest.json").read_text())
            manifest["artifacts"][0].update(path=artifact["path"], digest=artifact["digest"])
            manifest["conclusion"]["dependency_ids"] = ["acceptance-000001"]
            (self.root / "manifest.json").write_text(json.dumps(manifest))
            spec["inputs"][0] = artifact
            spec["inputs"][-1]["digest"] = digest(manifest)
            spec["checkpoint"]["evidence_digests"] = [digest(manifest)]
            spec["checkpoint"]["kind"] = "hypothesis"
            self.search("checkpoint", spec, revision=3)
            reference = "checkpoint:checkpoint-000002"
        self.record_probe_output(raw)
        before = controller.store.read()
        with self.assertRaises(SearchError) as caught:
            audit_strategy_inputs(self.root, controller.status(), {"evidence_ids": [reference]}, ContentSink(controller.store))
        self.assertEqual(caught.exception.code, "exploratory_evidence")
        self.assertEqual(controller.store.read(), before)

    def test_strategy_acceptance_reference_rechecks_its_probe_origins(self):
        self.assert_strategy_acceptance_origin_refused()

    def test_strategy_checkpoint_rechecks_accepted_premises_in_its_manifest_conclusion(self):
        self.assert_strategy_acceptance_origin_refused(through_checkpoint=True)

    def test_probe_origin_recorded_after_build_blocks_acceptance_under_the_common_guard(self):
        from search_controller.errors import SearchError
        self.setup_external()
        controller = Controller(self.root)
        checkpoint = controller.status()["checkpoints"]["checkpoint-000001"]
        spec = {"obligation_id": "obligation-000001", "outcome": "proof", "dependency_ids": [],
                "route_bindings": [], "review": self.result_review(checkpoint, checkpoint["claim"]), "inputs": []}
        attest_spec(controller, spec)
        before, original = controller.store.read(), controller._build
        def build_then_record(*args, **kwargs):
            candidate = original(*args, **kwargs)
            self.record_probe_output((self.root / "manifest.json").read_bytes())
            return candidate
        with patch.object(controller, "_build", side_effect=build_then_record):
            with self.assertRaises(SearchError) as caught:
                controller.command("accept", spec, 2, "accept-concurrent-probe", checkpoint["id"])
        self.assertEqual(caught.exception.code, "exploratory_evidence")
        self.assertEqual(controller.store.read(), before)

    def test_first_proposal_resolves_probe_origins_in_its_declared_common_ancestor(self):
        self.initialize()
        prepared = self.prepared_proposal()
        raw = b'{"imported": "canonical blob with a raw probe identity"}\n'
        native = self.root
        try:
            self.root = native.parent
            self.record_probe_output(raw)
        finally:
            self.root = native
        (native / "import.json").write_bytes(raw)
        identity = digest(json.loads(raw))
        prepared["inputs"].append({"path": "import.json", "kind": "blob", "digest": identity})
        prepared["review_workspace"] = ".."
        self.search("propose", prepared, revision=1)
        state = Controller(native).status()
        self.assertEqual(state["service"]["review_workspace"], "..")
        self.assertEqual(state["service"]["probe_origins"][identity][0]["probe_id"], "explore")


class ProbeNativeVerificationTests(WorkspaceTest):
    admit = certified_tests.CertifiedBridgeExecutionTests.admit
    result_review = certified_tests.CertifiedBridgeExecutionTests.result_review
    observed_policy_review = certified_tests.CertifiedBridgeExecutionTests.observed_policy_review
    record_result = certified_tests.CertifiedBridgeExecutionTests.record_result

    def write_native_input(self, relative, text, kind="artifact"):
        value = certified_tests.CertifiedBridgeExecutionTests.input_file(self, relative, text, kind)
        if relative.endswith("/certificate.txt"):
            CommonEvidenceTests.record_probe_output(self, text.encode())
        return value

    input_file = write_native_input

    def test_native_admission_verification_and_interpretation_can_validate_a_probe_certificate(self):
        self.root = self.attack_root
        certified_tests.CertifiedBridgeExecutionTests.test_certified_residue_premise_and_analytical_bridge_reach_the_full_claim(self)
        from research_harness.probe_provenance import record_probe_artifact_origin
        from search_controller.provenance import collect_probe_origins
        from search_controller.storage import ContentSink
        controller = Controller(self.root)
        state, content = controller.status(), ContentSink(controller.store)
        manifest = next(content.get_blob(identity) for checkpoint in state["checkpoints"].values()
                        for identity in checkpoint["evidence_digests"] if content.get_blob(identity)["kind"] == "certificate")
        output = ArtifactStore(self.root).put(b"A separate exploratory result hidden in a real receipt wrapper.", "text/plain")
        record_probe_artifact_origin(self.root, "explore", output["sha256"], "outputs")
        # A real execution receipt authenticates its logs, not extra scientific
        # references in an artifact that merely copies the receipt's shape.
        wrapper = copy.deepcopy(manifest)
        wrapper["conclusion"]["untrusted_result"] = output["sha256"]
        identity = content.put_artifact(json.dumps(wrapper).encode())
        origins = collect_probe_origins(self.root, state, [identity], content=content)
        self.assertIn(output["sha256"], {row["sha256"] for row in origins})


class ProbeComputationInputsTests(WorkspaceTest):
    def test_fresh_computation_amendment_cannot_use_probe_deduction_or_preflight(self):
        from search_controller.errors import SearchError
        from tests.search_execution_support import admit_workspace, invoke
        from tests import test_search_computation as computation_tests
        self.root = self.attack_root
        controller = admit_workspace(self.root, computational=True, historical=True)
        output = CommonEvidenceTests.record_probe_output(self, b"Exploratory decision evidence")
        entry = {"path": "exploration.txt", "kind": "artifact", "digest": output["sha256"]}
        (self.root / entry["path"]).write_bytes(ArtifactStore(self.root).read(output))
        for field in ("deduction_digest", "inspected_evidence", "encoding_digest"):
            with self.subTest(field=field):
                spec = computation_tests.ComputationHistoricalTests.amendment(self, controller)
                computation = spec["computation"]
                if field == "deduction_digest":
                    computation["basis"][field] = output["sha256"]
                elif field == "inspected_evidence":
                    computation["preflight"][field] = [output["sha256"]]
                else:
                    computation["domain"] = {"kind": "bounded_encoding", "encoding_digest": output["sha256"], "max_instances": 1}
                subject = {"node_id": "node-000001", "proposal_digest": spec["proposal_digest"], "computation": computation}
                spec["review"] = computation_tests.result_review(subject, controller.status()["nodes"]["node-000001"]["claim"])
                spec["inputs"] = [entry]
                before = controller.store.read()
                with self.assertRaises(SearchError) as caught:
                    invoke(controller, "amend-computation", spec)
                self.assertEqual(caught.exception.code, "exploratory_evidence")
                self.assertEqual(controller.store.read(), before)
