"""A stored exploratory certificate reaches a problem's accounts by native review."""

import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

from research_harness import cli
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.probe import launch_probe
from research_harness.storage import Store
from search_controller.errors import SearchError
from search_controller.provenance import collect_probe_origins
from search_controller.service import Controller
from search_controller.storage import ContentSink
from tests.native_reviewer_support import observe_review
from tests.search_execution_support import begin_spec, computation_contract, invoke, review_native_inputs
from tests.search_fixtures import contract, digest, proposal, review
from tests.support import (ALL_YES, FIXTURE_STRATEGIES, OPENING, WorkspaceTest,
                           make_move, make_preconditions, make_problem)
from tests import test_search_execution_proof as certified_tests


PLUGIN = Path(__file__).resolve().parents[4]
sys.path.append(str(PLUGIN / "tests"))

from problem_accounts_fixtures import build_accounts


class CertificatePathExampleTests(WorkspaceTest):
    input_file = certified_tests.CertifiedBridgeExecutionTests.input_file
    result_review = certified_tests.CertifiedBridgeExecutionTests.result_review

    def setUp(self):
        super().setUp()
        # Every provider response is supplied by observe_review. A missed stub
        # must fail locally rather than contact a reviewer.
        for target in ("socket.socket.connect", "socket.getaddrinfo"):
            blocker = patch(target, side_effect=AssertionError("Unexpected network access"))
            blocker.start()
            self.addCleanup(blocker.stop)
        self.common = self.attack_root.parent
        self.common_store = Store(self.common, create=True)
        self.examples = json.loads((PLUGIN / "docs/research-cli-examples.json").read_text())
        self.invoke_common_command("problem-init", dict(
            self.examples["problem-init"], slug="square-residues", area="math.NT",
            field="Residue classes occupied by integer squares", authors=["native-fixture-author"]))
        self.invoke_common_command("problem-statement", {
            "id": "statement-1", "title": "The residue classes of squares", "area": "math.NT",
            "statement": "Which residue classes can an integer square occupy modulo $m$?",
            "resolution_criteria": "Characterize the possible classes for every integer $m \\geq 2$.",
            "neighbours": [], "formal_statement": None})
        self.artifacts = ArtifactStore(self.common)
        self.controller = Controller(self.attack_root, FIXTURE_STRATEGIES)
        self.reviews = {}

    def invoke_common_command(self, name, payload):
        path = self.common / (name + ".json")
        path.write_text(json.dumps(payload))
        revision = self.common_store.revision
        args = cli.build_parser().parse_args([
            name, "--workspace", str(self.common), "--file", str(path),
            "--expected-revision", str(revision), "--request-id", name + "-" + str(revision)])
        return cli.run(args)

    def record_probe(self):
        program = self.common / "produce.py"
        program.write_text("from pathlib import Path\n"
                           "rows = ''.join('{} {}\\n'.format(r, r*r % 4) for r in range(4))\n"
                           "Path('certificate.txt').write_text(rows)\n")
        receipt = launch_probe(self.common_store, identifier="probe-residues",
            question="Which square residues occur modulo four?", script=program, timeout=10,
            observation="A four-row certificate, still exploratory until independently checked.")
        record = receipt["result"]
        self.assertEqual(record["exit_status"], 0)
        self.assertEqual(len(record["outputs"]), 1)
        self.probe_output = record["outputs"][0]
        self.probe_bytes = self.artifacts.read(self.probe_output)
        self.assertEqual(self.probe_bytes, b"0 0\n1 1\n2 0\n3 1\n")
        records = self.common_store.snapshot()["records"]
        self.assertEqual(set(records["probe_intent"]), {"probe-residues"})
        self.assertEqual(set(records["probe"]), {"probe-residues"})
        self.assertEqual(records["probe_intent"]["probe-residues"]["program"],
                         self.examples["probe-intent"]["program"])
        self.assertEqual(self.probe_output, self.examples["probe"]["outputs"][0])
        self.assertFalse(self.controller.store.tree_path.exists())
        # Retrying the managed command replays the same receipt without a producer.
        with patch("research_harness.probe.subprocess.Popen", side_effect=AssertionError("Producer ran twice")):
            self.assertEqual(launch_probe(self.common_store, identifier="probe-residues",
                question="Which square residues occur modulo four?", script=program, timeout=10), receipt)

    def prepare_proposal(self):
        self.record_probe()
        original = contract()
        claim = dict(original["original_claim"],
            statement="For every r in {0,1,2,3}, r squared modulo 4 is 0 or 1",
            quantifiers="For each of the four residues", proof_policy="certificate",
            scope={"kind": "case_ids", "case_ids": ["0", "1", "2", "3"]})
        original.update(original_claim=claim, proof_policy="certificate", root_attack_slug=self.slug)
        self.controller.command("init", {"contract": original}, 0, "initialize")
        candidate = proposal()
        candidate.update(schema_version=3, foundation=None, claim=claim, attack_slug=self.slug, method=OPENING,
            task={"kind": "finite_proof", "purpose": "Check the stored residue certificate",
                  "input_domain": "The four residues modulo four"})
        candidate["anchor"]["digest"] = digest(original)
        candidate["contribution"]["necessity"] = {
            "obligation_id": "obligation-000001",
            "omission_consequence": "The stored certificate would have no admitted independent check",
            "domain_justification": "The four residues are the complete scope of this objective",
            "outcomes": [{"outcome": "verified", "next_action": "Review the certificate result"},
                         {"outcome": "rejected", "next_action": "Leave the claim unproved"}],
            "stopping_condition": "Stop after one complete pass of the independent checker"}
        inputs = []
        for name, text in [("problem", claim["statement"]),
                           ("novelty", "A classical modular fact used as a software fixture; no novelty is claimed."),
                           (OPENING, "Check all four residues with exact integer arithmetic.")]:
            item = self.input_file("studies/" + name + ".md", text)
            inputs.append(item)
            if name == OPENING:
                candidate["studies"]["strategies"] = [{"method": OPENING, "digest": item["digest"]}]
            else:
                candidate["studies"][name] = item["digest"]
        deduction = self.input_file("checker-soundness.md",
            "The checker requires rows numbered 0, 1, 2, 3 in order and exactly four rows. "
            "For each row it recomputes r*r modulo 4 by exact integer arithmetic and checks that "
            "the value is not 2. A successful check of a certificate in this form establishes "
            "the stated four-case claim; no exploratory output is a premise.\n")
        inputs.append(deduction)
        candidate["computation"] = computation_contract(deduction["digest"])
        candidate["computation"]["domain"] = claim["scope"]
        candidate["computation"]["basis"]["kind"] = "root_finite_scope"
        candidate["computation"]["preflight"].update(
            uncertainty="Whether the certificate stored by probe-residues passes the independent checker",
            cheapest_sufficient_check="Check the stored certificate once, without running its producer again",
            failure_signal="A rejected row or missing case leaves the claim unproved")
        candidate["computation"]["verification_plan"].update(
            certificate_shape="Exactly four ordered pairs r, r squared modulo 4",
            checker_method="The shell checker recomputes every row and checks complete coverage")
        self.candidate, self.proposal_inputs = candidate, inputs
        self.assertFalse(candidate["computation"]["preflight"]["already_determined"])
        required = [candidate["computation"]["basis"]["deduction_digest"]]
        required += candidate["computation"]["preflight"]["inspected_evidence"]
        self.assertEqual(set(required), {deduction["digest"]})
        self.assertNotIn(self.probe_output["sha256"], required)
        self.assertEqual(collect_probe_origins(self.attack_root, self.controller.status(), required,
            common_workspace=self.common, content=ContentSink(self.controller.store)), [])

    def propose_and_review(self):
        invoke(self.controller, "propose", {"proposal": self.candidate, "inputs": self.proposal_inputs,
               "review_workspace": ".."}, None)
        approved = observe_review(self.controller, review(self.candidate), self.candidate)
        self.reviews["proposal"] = approved
        invoke(self.controller, "review", {"proposal_id": "proposal-000001", "review": approved, "inputs": []}, None)

    def admit_proposal(self):
        self.prepare_proposal()
        self.propose_and_review()
        invoke(self.controller, "admit", {}, "proposal-000001")
        state = self.controller.status()
        self.assertEqual(state["service"]["review_workspace"], "..")
        recorded = state["proposals"][state["nodes"]["node-000001"]["proposal_id"]]["record"]
        self.assertEqual(recorded["role"], "research")
        self.assertIsNone(recorded["foundation"])
        problem = make_problem()
        problem["claim"] = self.candidate["claim"]["statement"]
        self.input_file(self.slug + "/problem.json", json.dumps(problem))
        self.input_file(self.slug + "/study/problem.md", problem["claim"])
        self.input_file(self.slug + "/study/" + OPENING + ".md", "Check the four residues with exact arithmetic.")
        self.input_file(self.slug + "/preconditions.json", json.dumps(make_preconditions(ALL_YES)))
        self.assert_cli_passes("plan", self.slug)
        openings = json.loads((self.workspace / "openings.json").read_text())["openings"]
        openings.sort(key=lambda row: row["strategy"] != OPENING)
        self.input_file(self.slug + "/ranking.json", json.dumps({"generated_from": "openings.json", "order": [
            {"strategy": row["strategy"], "cites": ["shape.objects"], "reason": "The studied opening applies"}
            for row in openings]}))

    def assert_cli_passes(self, *argv):
        result = self.run_cli(*argv)
        self.assertEqual((result[0], result[2]), (0, ""), result)

    def verify_certificate(self):
        self.admit_proposal()
        step = self.workspace / "deterministic/residues"
        step.mkdir(parents=True)
        certificate = step / "certificate.txt"
        certificate.write_bytes(self.probe_bytes)
        self.assertEqual(hashlib.sha256(certificate.read_bytes()).hexdigest(), self.probe_output["sha256"])
        self.input_file(self.slug + "/deterministic/residues/check.sh",
            "#!/bin/sh\nset -eu\ncount=0\nwhile read -r r square; do\n"
            "  [ \"$r\" -eq \"$count\" ]\n  [ \"$square\" -eq \"$((r*r % 4))\" ]\n"
            "  [ \"$square\" -ne 2 ]\n  count=$((count+1))\n"
            "done < certificate.txt\n[ \"$count\" -eq 4 ]\nprintf 'All four residue rows verified\\n'\n")
        (step / "check.sh").chmod(0o755)
        invoke(self.controller, "begin", begin_spec())
        self.reviews["input"] = review_native_inputs(self.controller, self.slug, "residues")
        self.assert_cli_passes("verify", "certificate", self.slug, "residues")
        self.assert_cli_passes("journal", "add", self.slug, "--json", json.dumps(make_move(1, steps=["residues"])))
        state = self.controller.status()
        self.assertEqual(len(state["runs"]), 1)
        self.run = state["runs"]["run-000001"]
        self.assertEqual(self.run["node_id"], "node-000001")
        self.assertEqual(self.run["kind"], "certificate")
        self.assertIsNotNone(self.run["computation_digest"])
        self.assertEqual(self.run["computation_digest"], digest(self.candidate["computation"]))
        self.assertFalse(any(row["kind"] == "command" for row in state["runs"].values()))

    def interpret_run(self):
        invoke(self.controller, "interpret", {
            "result_digest": self.run["result_digest"], "computation_digest": self.run["computation_digest"],
            "outcome": "verified", "inconclusive_reason": None, "classification": "proof_candidate",
            "root_decision": {"kind": "proof_candidate",
                              "reason": "Every residue row passed the complete checker"},
            "remaining_obligation_ids": ["obligation-000001"], "next_action": "Review the certificate result"}, self.run["id"])

    def build_manifest(self):
        run_inputs = self.controller.store.get_blob(self.run["input_digest"])
        self.reviews["policy"] = observe_review(self.controller,
            self.result_review(self.run["result_digest"], digest(self.candidate["claim"])),
            self.controller.store.get_blob(self.run["result_digest"]))
        return dict({key: run_inputs[key] for key in ("claim_digest", "artifacts", "external_dependencies")},
            schema_version=1, kind="certificate", dependencies=[],
            conclusion={"outcome": "proof", "dependency_ids": [], "route_bindings": []},
            verification={"run_id": self.run["id"], "result_digest": self.run["result_digest"],
                          "policy_review": self.reviews["policy"],
                          "requested_declaration": None, "requested_type_digest": None})

    def build_checkpoint(self, manifest):
        item = self.input_file(self.slug + "/manifest.json", json.dumps(manifest), "blob")
        receipt = self.controller.status()["service"]["journal_receipts"]["node-000001:1"]
        return {"inputs": [item], "checkpoint": {
            "schema_version": 1, "kind": "proof", "claim": self.candidate["claim"],
            "origin": {"kind": "journal_move", "node_id": "node-000001", "move": 1,
                       "journal_prefix_digest": receipt["journal_prefix_digest"]},
            "evidence_digests": [item["digest"]], "what_changed": "The stored certificate passed its checker",
            "remaining_obligation_ids": [], "next_hypothesis": "Review the exact finite conclusion",
            "milestone_id": "root-proof"}}

    def accept_certificate(self):
        self.verify_certificate()
        self.interpret_run()
        manifest = self.build_manifest()
        spec = self.build_checkpoint(manifest)
        # Import for provenance inspection, before the checkpoint's fresh audit.
        from search_controller.evidence import import_inputs
        content = ContentSink(self.controller.store)
        import_inputs(self.attack_root, spec["inputs"], content, common_workspace=self.common)
        origins = collect_probe_origins(self.attack_root, self.controller.status(),
            spec["checkpoint"]["evidence_digests"], content=content)
        self.assertEqual({row["probe_id"] for row in origins}, {"probe-residues"})
        self.assertIn(self.probe_output["sha256"], {row["sha256"] for row in origins})
        self.assertEqual({row["role"] for row in manifest["artifacts"]}, {"certificate", "checker"})
        invoke(self.controller, "checkpoint", spec)
        checkpoint = self.controller.status()["checkpoints"]["checkpoint-000001"]
        approved = self.result_review(digest(checkpoint), digest(self.candidate["claim"]),
            openness_evidence=[self.candidate["studies"]["novelty"], spec["inputs"][0]["digest"]])
        approved = observe_review(self.controller, approved, checkpoint)
        self.reviews["result"] = approved
        invoke(self.controller, "accept", dict(manifest["conclusion"], obligation_id="obligation-000001",
            review=approved, inputs=[]), checkpoint["id"])
        state = self.controller.status(full_audit=True)
        self.assertEqual(state["freshness"]["status"], "fresh")
        self.assertEqual(state["acceptances"]["acceptance-000001"]["standard"], "certificate")
        self.assertEqual(state["totals"]["used_runs"], 1)
        assignments = self.common_store.snapshot()["records"]["review_assignment"]
        self.assertEqual(len(assignments), 4)
        self.assertEqual(set(self.reviews), {"proposal", "input", "policy", "result"})
        self.assertEqual({"review-assignment:" + identifier for identifier in assignments},
                         {row["reviewer"]["attestation_id"] for row in self.reviews.values()})

    def build_value(self, evidence):
        accounts = build_accounts()
        accounts["boundaries"] = [{
            "id": "mod-four-boundary", "fact": "No square has residue two modulo four.",
            "scope": "The four residue classes modulo four.", "date": "unknown", "evidence": [evidence],
            "evidence_ids": [], "argument": "The complete residue table excludes the class two when m is four."}]
        return {"id": "certificate-value", "statement_id": "statement-1", "accounts": accounts,
            "motivation": [{"text": "A classification must explain the excluded classes already proved modulo four.",
                            "evidence": [{"kind": "account", "id": "mod-four-boundary"}]}],
            "current_state": [{"text": "The modulo-four boundary has an accepted certificate result.",
                               "evidence": [{"kind": "account", "id": "mod-four-boundary"}]}]}

    def test_probe_certificate_reaches_delivered_boundary_accounts_with_four_assignments(self):
        self.accept_certificate()
        delivered = invoke(self.controller, "deliver-attempt", {}, None)
        self.assertEqual(delivered["workspace"], str(self.common))
        reference = {"kind": "result", "native_attempt_digest": delivered["native_attempt_digest"],
            "acceptance_id": "acceptance-000001", "purpose": "Locate the excluded square class modulo four",
            "extension_reason": None}
        saved = self.invoke_common_command("problem-value", self.build_value(reference))["result"]
        self.assertEqual(saved["accounts"]["boundaries"][0]["evidence"], [reference])
        self.assertIn("certificate-value", self.common_store.snapshot()["records"]["problem_value"])
        native = self.common_store.snapshot()["records"]["native_attempt"][delivered["native_attempt_digest"]]
        self.assertEqual(native["acceptances"][0]["standard"], "certificate")
        self.assertEqual(native["acceptances"][0]["status"], "accepted")
        self.assertEqual(len(self.common_store.snapshot()["records"]["review_assignment"]), 4)

    def assert_admission_refused(self, field, code):
        self.prepare_proposal()
        if field == "already_determined":
            self.candidate["computation"]["preflight"][field] = True
        else:
            item = self.input_file("probe-copy.txt", self.probe_bytes.decode())
            self.proposal_inputs.append(item)
            if field == "inspected_evidence":
                self.candidate["computation"]["preflight"][field] = [item["digest"]]
            else:
                self.candidate["computation"]["basis"][field] = item["digest"]
        self.propose_and_review()
        before = self.controller.store.read()
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "admit", {}, "proposal-000001")
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.controller.store.read(), before)

    def test_probe_digest_in_preflight_is_refused_at_admit(self):
        self.assert_admission_refused("inspected_evidence", "exploratory_evidence")

    def test_probe_digest_as_deduction_is_refused_at_admit(self):
        self.assert_admission_refused("deduction_digest", "exploratory_evidence")

    def test_already_determined_research_is_refused(self):
        self.assert_admission_refused("already_determined", "already_determined")

    def assert_checkpoint_refused(self, manifest, code):
        spec = self.build_checkpoint(manifest)
        before = self.controller.store.read()
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "checkpoint", spec)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.controller.store.read(), before)

    def test_analytical_wrapper_cannot_credit_the_probe_certificate(self):
        self.verify_certificate()
        self.interpret_run()
        manifest = self.build_manifest()
        manifest.update(kind="analytical", verification=None)
        manifest["artifacts"] = [dict(row, role="proof") for row in manifest["artifacts"]]
        self.assert_checkpoint_refused(manifest, "exploratory_evidence")

    def test_uninterpreted_certificate_is_refused_at_checkpoint(self):
        self.verify_certificate()
        self.assert_checkpoint_refused(self.build_manifest(), "exploratory_evidence")

    def test_probe_reference_is_refused_in_boundary_accounts(self):
        self.record_probe()
        before = self.common_store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            self.invoke_common_command("problem-value", self.build_value({"kind": "probe", "id": "probe-residues"}))
        self.assertEqual(caught.exception.code, "problem_boundary_evidence_missing")
        self.assertEqual(self.common_store.snapshot(), before)
