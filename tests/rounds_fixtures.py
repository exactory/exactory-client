"""Round fixtures: a measured manuscript, its blind reviews and predictions, and round records."""

import copy
import json

from development_fixtures import DevelopmentCase
from integration_fixtures import observed_candidate
from research_harness import publication, rounds
from test_research_publication import ResearchPublicationTests

DEVELOPMENT_SEARCHES = ("downstream", "next_step", "exemplars", "changes")


class RoundsCase(DevelopmentCase):
    # Borrowed rather than inherited: inheriting the test class would re-run its tests under every RoundsCase subclass.
    core = ResearchPublicationTests.core
    manuscript_review = ResearchPublicationTests.manuscript_review

    def setUp(self):
        super().setUp()
        self.prepared_study()
        self.execution_payload = observed_candidate(self)
        (self.root / "draft").mkdir()
        (self.root / "evidence").mkdir()
        (self.root / "draft/paper.pdf").write_bytes(b"%PDF-1.4\n% Round fixture.\n%%EOF")
        (self.root / "draft/abstract.txt").write_text("The exact finite bound was enumerated.")
        (self.root / "draft/references.bib").write_text("@article{bounded,title={Authored bound}}\n")

    def claims(self, *extra, revised=(), superseded=()):
        """Claim objects for evidence/claims.json: 'bound' plus the named extra claims."""
        items = [{"id": "bound", "claim": "The maximum is 9."}]
        items.extend({"id": claim, "claim": "Claim " + claim + " holds."} for claim in extra)
        for claim in revised:
            item = next(i for i in items if i["id"] == claim)
            item["revised"] = {"previous": item["claim"], "reason": "Sharpened after the round's evidence."}
        for claim in superseded:
            item = next(i for i in items if i["id"] == claim)
            item["superseded"] = {"reason": "Replaced by a wider claim."}
        return items

    def pin(self, claims=None, identifier=None, reviews=2):
        """Write claims.json, pin the bundle and record `reviews` accepting blind reviews."""
        claims = self.claims() if claims is None else claims
        (self.root / "evidence/claims.json").write_text(json.dumps(claims))
        identifier = identifier or ("paper-" + str(self.store.revision))
        evidence = [self.result_evidence(self.execution_payload)]
        bundle = self.mutate(publication.prepare_publication, {"id": identifier,
            "files": {"pdf": "draft/paper.pdf", "abstract": "draft/abstract.txt", "bibliography": "draft/references.bib",
                      "claims": "evidence/claims.json", "sources": None},
            "claim_evidence": [{"claim_id": c["id"], "evidence": evidence} for c in claims]})["result"]
        for number in range(1, reviews + 1):
            self.mutate(publication.record_manuscript_review, self.manuscript_review(bundle, identifier + "-gate-" + str(number)))
        return bundle

    def prediction_payload(self, bundle, assessor, percentile=30, band=(20, 40)):
        return {"id": assessor + "-prediction", "bundle_digest": bundle["digest"], "blind": True,
                "assessor": {"id": assessor, "kind": "agent",
                             "provenance": self.artifacts.put(("Blind context " + assessor).encode(), "text/plain"),
                             "relationship": "A separate fixture assessor.", "independence_basis": "A blind context received the exact manuscript."},
                "prediction": {"corpus": "arxiv", "category": "cs.LG", "windowStart": "2026-01-01", "windowEnd": "2026-01-31",
                               "percentile": percentile, "band": {"best": band[0], "worst": band[1]}},
                "reasons": ["The authored fixture states a narrow finite result."]}

    def measure(self, bundle, suffix, percentiles=(30, 25, 40)):
        """Three blind reviews and three predictions on the bundle, as one measurement."""
        from research_harness import predictions
        for number, percentile in enumerate(percentiles, 1):
            assessor = "measure-" + suffix + "-" + str(number)
            self.mutate(publication.record_manuscript_review, self.manuscript_review(bundle, assessor))
            self.mutate(predictions.record_prediction, self.prediction_payload(bundle, assessor, percentile))

    def round_evidence(self):
        return [self.source_evidence(), self.result_evidence(self.execution_payload)]

    def recandidate(self, suffix, *, alternative="next_round", author="cycle-author"):
        """Assess cycle-1 again with its alternative carried, checkpoint it and review readiness again."""
        api = self.development()
        plan = self.store.snapshot()["records"]["cycle_plan"]["cycle-1"]["payload"]
        payload = self.assessment(plan, self.execution_payload, identifier="assessment-" + suffix)
        payload["author"] = author
        payload["development"]["alternatives"][0].update(disposition=alternative, reason="Exceeds this round's admitted scope.")
        self.mutate(api.assess_cycle, payload)
        self.save_checkpoint("cycle-1", "assessment-" + suffix, "checkpoint-" + suffix)
        self.mutate(api.record_readiness_review, self.review(self.execution_payload, identifier="review-" + suffix))
        return "assessment-" + suffix

    def set_stage(self, stage):
        """Write the study projection at a stage directly; status tests need a stage, not a transition."""
        from research_harness.integration import export_workspace
        from research_harness.operations import prepared_mutation
        state = {"version": 2, "slug": "rounds", "stage": stage, "status": "pending", "autopilot": False, "waiting": None,
                 "loop": {"target": None, "budget": None, "notes": ""}, "created": "2026-09-12T00:00:00Z", "updated": "2026-09-12T00:00:00Z",
                 "research": {"store": ".exactory/research.sqlite3", "profile": "research"}}
        self.mutate(lambda store, payload, **identity: prepared_mutation(store, "test.stage", payload,
                    lambda records, value: ([("workspace", "study", state)], state), **identity), {})
        export_workspace(self.store)

    def write_record(self, kind, record):
        """Write one record of `kind` directly, as a stand-in for an operation that does not exist yet."""
        from research_harness.operations import prepared_mutation
        self.mutate(lambda store, payload, **identity: prepared_mutation(store, "test." + kind, payload,
                    lambda records, value: ([(kind, record["id"], record)], record), **identity), {})
        return record

    def write_round(self, decision, identifier, *, successful=None, bundle_digest=None):
        """Write the admission of the round `decision` proposed directly, with its assessment when `successful` is given.

        A stand-in for `admit_round` without the review; it carries the fields the decision and literature rules read."""
        from research_harness.evaluation import Evaluation
        proposal = decision["payload"]["next"]
        records = self.store.snapshot()["records"]
        opening = rounds._opening(records, Evaluation(records, self.artifacts), records["publication_bundle"][decision["bundle_id"]])
        admission = self.write_record("round_admission", {"id": identifier, "number": proposal["number"], "decision_id": decision["id"],
                                                          "goal": proposal["goal"], "opening": opening,
                                                          "admitted_revision": self.store.revision + 1})
        if successful is not None:
            self.write_round_assessment(identifier, successful, bundle_digest)
        return admission

    def write_round_assessment(self, round_id, successful, bundle_digest):
        """Write the assessment of an admitted round directly, bound to `bundle_digest`; a stand-in until `assess_round` exists."""
        return self.write_record("round_assessment", {"id": round_id + "-assessment", "round_id": round_id,
                                                      "bundle_digest": bundle_digest, "successful": successful})

    def goal(self, direction="vertical", statement="Extend the finite bound to every integer in [0, 5]."):
        return {"direction": direction, "field_change": None, "statement": statement,
                "contribution_delta": "Readers can apply the bound over the wider range without a new enumeration.",
                "beneficiaries": [{"who": "Authors of bounded-sequence proofs", "bottleneck": "The finite range stops at 3.",
                                   "evidence": self.round_evidence()}],
                "success_criteria": [{"id": "sc-wider", "kind": "claim", "statement": "The manuscript establishes the bound up to 5 with evidence."}],
                "stop_conditions": [{"id": "st-counterexample", "statement": "An integer in [4, 5] violates the bound."}],
                "continuity": "The round keeps every claim, reading and evidence of the current paper and extends the enumeration.",
                "route": "Plan one enumeration cycle over [4, 5] and assess it.", "risks": ["The wider enumeration may reveal a counterexample."],
                "evidence": self.round_evidence()}

    def decision_payload(self, bundle, closes=1, decision="continue", *, direction="vertical", statement=None,
                         objective=None, lineage=None, carried=(), candidates=None, reopening=None):
        goal = self.goal(direction, statement) if statement else self.goal(direction)
        pursued = {"id": "cand-goal", "direction": goal["direction"], "statement": goal["statement"],
                   "disposition": "pursue" if decision == "continue" else "rejected",
                   "reason": "The wider range is the paper's most valuable next step." if decision == "continue" else "Nothing remains to dig.",
                   "evidence": self.round_evidence()}
        rejected = {"id": "cand-transfer", "direction": "horizontal", "statement": "Transfer the bound to real inputs.",
                    "disposition": "rejected", "reason": "No evidenced demand.", "evidence": self.round_evidence()}
        payload = {"id": "decision-" + str(closes) + "-" + decision + "-" + str(self.store.revision), "closes": closes, "decision": decision,
                   "bundle_digest": bundle["digest"], "candidates": candidates if candidates is not None else [pursued, rejected],
                   "carried": list(carried), "next": None, "reason": "Recorded by the fixture."}
        if decision == "continue":
            payload["next"] = {"number": closes + 1, "objective": objective or copy.deepcopy(self.objective),
                               "objective_lineage": lineage, "goal": goal,
                               "resource_limits": {"literature": {"network_requests": 20, "readings": 10}, "experiment": {"wall_seconds": 600}},
                               "reopening": reopening}
        return payload

    def review_payload(self, decision, verdict="approved", assessor="round-assessor", checks=None):
        kinds = rounds.CHECKS_CONTINUE if decision["decision"] == "continue" else rounds.CHECKS_STOP
        return {"id": decision["id"] + "-review-" + assessor, "round_id": decision["id"], "round_digest": decision["digest"],
                "assessor": {"id": assessor, "kind": "agent",
                             "provenance": self.artifacts.put(("Round context " + assessor).encode(), "text/plain"),
                             "relationship": "A separate fixture assessor.", "independence_basis": "The round packet was delivered to a fresh context."},
                "verdict": verdict, "checks": checks if checks is not None else [
                    {"kind": kind, "status": "passed" if verdict == "approved" else "unresolved",
                     "reason": "The fixture goal names a concrete delta, beneficiaries and a route.", "evidence": self.round_evidence()}
                    for kind in kinds],
                "limitations": ["This authored receipt does not establish comprehension or impartiality."]}

    def field_change_payload(self, bundle, corpus, category):
        """A horizontal decision whose goal adds `category` of `corpus` to the study's field."""
        payload = self.decision_payload(bundle, direction="horizontal", statement="Transfer the bound to a neighbouring category.")
        payload["candidates"][0]["direction"] = "horizontal"
        payload["next"]["goal"]["field_change"] = {"corpus": corpus, "primaryCategory": category}
        return payload

    def approve(self, payload):
        """Record the decision `payload` and approve it; returns (decision, review)."""
        decision = self.mutate(rounds.record_round, payload)["result"]
        review = self.mutate(rounds.record_round_review, self.review_payload(decision))["result"]
        return decision, review

    def open_round(self, bundle=None, closes=1, **decision_kwargs):
        """Decide continue, approve it and admit the next round; returns (decision, review, admission)."""
        decision, review = self.approve(self.decision_payload(bundle or self.pin(), closes, **decision_kwargs))
        admission = self.mutate(rounds.admit_round, {"id": "round-" + str(decision["payload"]["next"]["number"]),
                                                     "round_id": decision["id"], "review_id": review["id"],
                                                     "reason": "The approved goal opens the round."})["result"]
        return decision, review, admission

    def development_searches(self, suffix):
        """Record the four consequence purposes with captured empty results, as `foundation_searches` does."""
        for purpose in DEVELOPMENT_SEARCHES:
            self.record_purpose(purpose, purpose + "-" + suffix)

    def round_literature(self, suffix):
        """The active round's literature work: an exemplar requirement, then every search judged against the widened
        frontier (the five purposes again and the four consequence purposes), then the sections re-recorded."""
        self.exemplar_requirement(suffix)
        self.foundation_searches(identifier_suffix="-" + suffix)
        self.development_searches(suffix)
        self.refresh_synthesis(suffix)

    def exemplar_requirement(self, suffix):
        from research_harness.reading import require_fulltext
        link = self.links[1]
        return self.mutate(require_fulltext, {"id": "exemplar-" + suffix, "profile": "research", "version_id": link["version_id"],
                                              "purpose": "exemplar", "reason": "A comparable first result developed into a larger contribution."})

    def assess_payload(self, admission, bundle, observed=True):
        goal = admission["goal"]
        status = "observed" if observed else "not_observed"
        return {"id": admission["id"] + "-assessment", "round_id": admission["id"], "bundle_digest": bundle["digest"],
                "criteria": [{"id": c["id"], "status": status, "explanation": "Judged from the round's bundle.",
                              "evidence": self.round_evidence()} for c in goal["success_criteria"]],
                "stop_conditions": [{"id": s["id"], "status": "not_observed", "explanation": "No counterexample appeared.",
                                     "evidence": self.round_evidence()} for s in goal["stop_conditions"]],
                "summary": "The round's outcome as recorded by the fixture."}
