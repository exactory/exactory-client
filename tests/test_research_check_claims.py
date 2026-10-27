"""Read-only claim evidence checks before independent readiness review."""

import copy
import json
from pathlib import Path
import subprocess
import sys

from development_fixtures import DevelopmentCase
from integration_fixtures import account_fixture_citations, observe_run
from managed_strategy_fixtures import result_decision


PLUGIN = Path(__file__).resolve().parents[1]


class ResearchCheckClaimsTests(DevelopmentCase):
    def setUp(self):
        super().setUp()
        self.prepared_study()
        self.execution_payload = observe_run(self)
        plan = self.store.snapshot()["records"]["cycle_plan"][self.execution_payload["cycle_id"]]["payload"]
        self.mutate(self.development().assess_cycle, self.assessment(plan, self.execution_payload))
        self.save_checkpoint()
        self.current = self.result_evidence(self.execution_payload)

    def run_cli(self, command, payload, *arguments):
        path = self.root / "claim-evidence.json"
        path.write_text(json.dumps(payload))
        return subprocess.run([sys.executable, str(PLUGIN / "bin/exactory-research"), command,
                               "--workspace", str(self.root), "--file", str(path), *arguments],
                              cwd=self.root, capture_output=True, text=True)

    def read_stored_files(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in (self.root / ".exactory").rglob("*") if path.is_file()}

    def build_mismatched_ledger(self):
        source = {"kind": "source", "link": copy.deepcopy(self.links[-1])}
        changed_locator = copy.deepcopy(self.current)
        changed_locator["locator"]["pointer"] = "/result/values"
        return [{"claim_id": "z-first", "evidence": [self.current, source, changed_locator]},
                {"claim_id": "a-second", "evidence": [changed_locator, self.current]}]

    def build_manuscript_payload(self, ledger):
        (self.root / "draft").mkdir()
        (self.root / "draft/paper.pdf").write_bytes(b"%PDF-1.4\n% Claim evidence fixture.\n%%EOF")
        (self.root / "draft/abstract.txt").write_text("The maximum of the finite enumeration is nine.")
        (self.root / "draft/references.bib").write_text("@article{bounded,title={Authored bound}}\n")
        claims = [{"id": link["claim_id"], "claim": "The finite enumeration is bounded by nine."} for link in ledger]
        (self.root / "draft/claims.json").write_text(json.dumps(claims))
        return {"id": "paper-1", "files": {"pdf": "draft/paper.pdf", "abstract": "draft/abstract.txt",
                "bibliography": "draft/references.bib", "claims": "draft/claims.json", "sources": None},
                "claim_evidence": ledger, "citation_accounting": account_fixture_citations(self)}

    def test_check_lists_every_outside_reference_with_its_claim_without_changing_store(self):
        ledger = self.build_mismatched_ledger()
        before, files = self.store.snapshot(), self.read_stored_files()
        result = self.run_cli("check-claims", ledger)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.read_stored_files(), files)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        error = json.loads(result.stderr)["error"]
        self.assertEqual(error["code"], "publication_evidence_mismatch")
        self.assertEqual(error["details"]["mismatches"], [
            {"claim_id": "z-first", "evidence": ledger[0]["evidence"][1]},
            {"claim_id": "z-first", "evidence": ledger[0]["evidence"][2]},
            {"claim_id": "a-second", "evidence": ledger[1]["evidence"][0]}])

    def test_check_accepts_candidate_references_before_review_without_changing_store(self):
        self.assertFalse(self.store.snapshot()["records"].get("readiness_review"))
        ledger = [{"claim_id": "bound", "evidence": [dict(reversed(list(self.current.items())))]}]
        before, files = self.store.snapshot(), self.read_stored_files()
        result = self.run_cli("check-claims", ledger)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.read_stored_files(), files)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), {"revision": before["revision"], "mismatches": []})

    def test_check_reads_the_same_manuscript_input_file(self):
        payload = self.build_manuscript_payload([{"claim_id": "bound", "evidence": [self.current]}])
        before, files = self.store.snapshot(), self.read_stored_files()
        result = self.run_cli("check-claims", payload)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.read_stored_files(), files)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"revision": before["revision"], "mismatches": []})

    def test_manuscript_refusal_names_the_first_outside_claim_and_reference(self):
        self.mutate(self.development().record_readiness_review, self.review(self.execution_payload))
        result_decision(self)
        ledger = self.build_mismatched_ledger()
        payload = self.build_manuscript_payload(ledger)
        before = self.store.snapshot()
        result = self.run_cli("manuscript", payload, "--expected-revision", str(before["revision"]),
                              "--request-id", "outside-claim-evidence")
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(result.returncode, 1, result.stderr)
        error = json.loads(result.stderr)["error"]
        self.assertEqual(error["code"], "publication_evidence_mismatch")
        self.assertIn("z-first", error["message"])
        self.assertIn(json.dumps(ledger[0]["evidence"][1], sort_keys=True), error["message"])
