"""Finite observed evidence and a real unavailable supplement, entirely local."""

import copy
import json

from development_fixtures import DevelopmentCase
from integration_fixtures import observe_run
from test_research_source_deferrals import DeferralCase
from research_harness.evidence import digest


class SourceLimitedCase(DeferralCase, DevelopmentCase):
    debt = "Compare the finite bound with the required external dataset."

    def prepare_source_limited(self):
        from integration_fixtures import prepare_research
        prepared = prepare_research(self.root, {"kind": "objective", "id": "finite-bound-and-external-comparison",
            "statement": "Establish the finite square bound for n = 0, 1, 2, 3 and compare it with the required external dataset."})
        self.objective, self.links, self.sequence = prepared.objective, prepared.links, prepared.sequence
        self.gap = self.metadata(90)
        self.require_source(self.gap)
        self.add_missing_supplement(self.gap)
        deferred = self.build_deferral(self.gap)
        deferred["dependent_claims"] = [self.debt]
        self.deferral = self.mutate(self.get_operation("defer-source"), deferred)["result"]
        self.foundation_searches(identifier_suffix="-deferred")
        self.refresh_synthesis("deferred")
        plan = self.plan()
        plan["scope"] = {"id": "supported-finite", "kind": "partial",
                         "statement": "The finite bound holds without an external-data comparison.",
                         "assumptions": plan["scope"]["assumptions"], "remaining_obligations": [self.debt]}
        plan["literature"]["scope"] = copy.deepcopy(plan["scope"])
        self.execution_payload = observe_run(self, plan=plan)
        assessment = self.assessment(plan, self.execution_payload)
        assessment.update(objective_status="open", disposition="continue")
        self.mutate(self.development().assess_cycle, assessment)
        self.save_checkpoint()
        self.assessment_payload = assessment
        return self.scope_payload()

    def scope_api(self):
        from research_harness import publication_scope
        return publication_scope

    def scope_payload(self, identifier="scope-1"):
        candidate = self.candidate()
        return {"id": identifier, "policy": "source-limited-v1",
                "objective_digest": digest(self.objective), "checkpoint_id": candidate["checkpoint_id"],
                "assessment_id": candidate["assessment_id"], "assessment_digest": candidate["assessment_digest"],
                "authorization": self.deferral["authorization"],
                "scientific_preparers": [{"id": "scope-preparer", "kind": "agent",
                    "provenance": self.artifacts.put(b"PRIVATE-SCOPE-PREPARER", "text/plain"),
                    "contributions": ["claim_selection", "gap_classification"]}],
                "scope": {"id": "supported", "statement": "Finite squares with no external dataset comparison.",
                          "assumptions": self.assessment_payload["assumptions"]},
                "supported_claims": [{"id": "bound", "statement": "The maximum over n = 0, 1, 2, 3 is 9.",
                    "polarity": "positive", "assumptions": self.assessment_payload["assumptions"],
                    "evidence": [self.result_evidence(self.execution_payload)], "limitation_ids": ["gap"]}],
                "public_limitations": [{"id": "gap", "statement": "The external comparison remains unavailable and untested.",
                                        "version_ids": [self.gap]}],
                "deferred_objective_obligations": [{"obligation": self.debt, "deferral_id": self.deferral["id"],
                    "version_id": self.gap, "dependency_digest": self.deferral["dependency_digest"],
                    "dependent_claim": self.debt, "reason": "The required external supplement remains unavailable."}],
                "correction": None}

    def record_scope(self, payload=None):
        return self.mutate(self.scope_api().record_publication_scope, payload or self.scope_payload())["result"]

    def manuscript_readiness(self):
        return self.scope_api().manuscript_readiness_report(self.store)

    def scope_review(self, identifier="scope-review", verdict="ready", assessor="scope-reviewer"):
        report = self.manuscript_readiness()
        review = self.review(self.execution_payload, identifier, verdict)
        review["candidate_digest"] = report["candidate_digest"]
        review["target"] = {"kind": "source_limited_manuscript", "contract_id": report["contract"]["id"],
                            "scientific_target_digest": report["scientific_target_digest"]}
        review["assessor"]["id"] = assessor
        review["checks"].append(dict(review["checks"][0], kind="source_limits"))
        if report["contract"]["payload"]["correction"] is not None:
            review["checks"].append(dict(review["checks"][0], kind="corrections"))
        return review

    def accept_scope(self):
        return self.mutate(self.scope_api().record_scoped_readiness_review, self.scope_review())["result"]

    def scoped_manuscript(self, identifier="paper-1"):
        from research_harness.publication import prepare_publication
        payload = self.manuscript_readiness()["contract"]["payload"]
        files = {"pdf": "draft/paper.pdf", "abstract": "draft/abstract.txt", "bibliography": "draft/references.bib",
                 "claims": "evidence/claims.json", "sources": None}
        claims = [{"id": c["id"], "claim": c["statement"], "polarity": c["polarity"],
                   "assumptions": c["assumptions"], "limitation_ids": c["limitation_ids"],
                   "public_limitations": [l for l in payload["public_limitations"] if l["id"] in c["limitation_ids"]]}
                  for c in payload["supported_claims"]]
        for key, content in {"pdf": b"%PDF-1.4\nAuthored finite fixture.", "abstract": b"Finite bound; external comparison unavailable.",
                             "bibliography": b"Authored references.", "claims": json.dumps(claims).encode()}.items():
            path = self.root / files[key]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return self.mutate(prepare_publication, {"id": identifier, "files": files,
            "claim_evidence": [{"claim_id": c["id"], "evidence": c["evidence"]} for c in payload["supported_claims"]]})["result"]
