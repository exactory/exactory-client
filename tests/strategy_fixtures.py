"""Synthetic scientific records for contribution-decision contract tests."""

import copy
import importlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.storage import Store


class StrategyCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "study"
        self.store = Store(self.root, create=True)
        self.artifacts = ArtifactStore(self.root)
        self.evidence = self.artifacts.put(b"A synthetic exact derivation with an explicit scope.", "text/plain")
        self.put("local_artifact", "instruction", {
            "id": "instruction", "path": "context/instruction.md", "media_type": "text/plain",
            "artifact": self.artifacts.put(b"Investigate the complete problem and submit the supported result.", "text/plain")})

    def module(self, name):
        self.assertIsNotNone(importlib.util.find_spec("research_harness." + name),
                             "The specified scientific decision domain has not been implemented")
        return importlib.import_module("research_harness." + name)

    def put(self, kind, key, value):
        return self.store.mutate("fixture.record", {"kind": kind, "key": key, "value": value},
                                 lambda tx: tx.put(kind, key, value),
                                 expected_revision=self.store.revision,
                                 request_id="fixture-" + str(self.store.revision))

    def mutate(self, operation, payload, **identity):
        return operation(self.store, copy.deepcopy(payload),
                         expected_revision=identity.get("revision", self.store.revision),
                         request_id=identity.get("request", "request-" + str(self.store.revision)))

    def error(self, code, action):
        revision = self.store.revision
        with self.assertRaises(ResearchError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.store.revision, revision, "A rejected mutation must not publish partial state")
        return caught.exception

    def intent(self, identifier="intent-1"):
        return {"id": identifier, "previous": None, "task_kind": "open_research",
                "instruction": "instruction", "recorded_at": "2026-09-30T18:00:00Z",
                "full_objective": "Distinguish the two mechanisms over the complete target range.",
                "branch": {"statement": "Distinguish the mechanisms on the first target.", "relation": "partial",
                           "remaining_obligations": ["Other targets remain unresolved."]},
                "objective": {"kind": "proposed", "id": "proposal-1", "statement": "Resolve the first discriminator.",
                              "scope": {"targets": ["first"]}, "instruction": "instruction"},
                "user_standard": "unspecified", "deliverables": ["Supported result report"],
                "publication": {"instruction": "instruction", "endpoint": "submit", "required": True,
                                "quality_condition": None},
                "resources": [{"kind": "compute", "limit": None, "unit": "seconds", "authorization": None}],
                "scope_changes": [], "authors": ["author"]}

    def candidate(self, identifier="candidate-a", method="Discriminator"):
        return {"id": identifier, "question": "Which mechanism survives the deciding observation?",
                "target": "first target", "scope": "the first target only", "relation": "partial", "method": method,
                "outcomes": {"positive": "Separate mechanism A", "negative": "Exclude mechanism A",
                             "inconclusive": "Keep both explanations open"},
                "prior_result": "The held comparator has no discriminator.", "addition": "A deciding observation.",
                "transfer": {"kind": "direct", "target_inference": "The exact first target is tested.",
                             "plan": "Observe both mechanisms on the same target.", "evidence": [self.evidence]},
                "adequacy": {"distinguishes": ["mechanism A", "mechanism B"], "limits": ["first target"],
                             "evidence": [self.evidence]},
                "assumptions": [{"id": "assumption-1", "statement": "The measurement identifies the response.",
                                 "check": "Measure the known control.", "scope_boundary": "Only calibrated inputs."}],
                "resources": {"estimates": [{"unit": "seconds", "amount": 10, "uncertainty": "Approximately ten seconds."}],
                              "available": ["Local CPU"]},
                "next_test": {"question": "Does the control separate mechanisms?", "method": method,
                              "success": "Distinct response", "failure": "Indistinguishable response", "prerequisites": []},
                "evidence": [self.evidence]}

    def dossier(self, identifier="dossier-1"):
        return {"id": identifier, "previous": None, "intent_id": "intent-1", "phase": "prospective",
                "objective": self.intent()["objective"], "candidates": [StrategyCase.candidate(self), StrategyCase.candidate(self, "candidate-b", "Alternative estimator")],
                "selected_candidate": "candidate-a", "no_branch": "Do not spend resources if no method distinguishes the targets.",
                "single_candidate": None, "claims": [], "comparators": [], "dependencies": [], "work_items": [],
                "leads": [], "failures": [], "continuity": [], "material_change": None, "reconsideration": None,
                "tranche": {"id": "tranche-1", "question": "Does the proposed discriminator work?", "method": "Discriminator",
                            "limit": {"unit": "seconds", "amount": 10}, "end_condition": "Control assessed",
                            "failure_signal": "Responses cannot be separated", "expected_evidence": "Exact observed controls",
                            "predecessor": None, "previous_outcome": None},
                "recommendation": {"action": "investigate", "reason": "The deciding control is available."}, "bundle_digest": None}

    def setup_dossier(self):
        strategy = self.module("strategy")
        self.mutate(strategy.record_intent, self.intent())
        return self.mutate(strategy.record_strategy, self.dossier())["result"]

    def work_item(self, identifier="work-1"):
        return {"id": identifier, "previous": None, "item_id": "discriminator", "intent_id": "intent-1",
                "requests": [{"origin": "review", "request_id": "request-a", "reviewer": "reviewer-a",
                              "statement": "Test the deciding discriminator."}],
                "classification": "consequence_critical", "execution_state": "planned", "claim_ids": ["claim-a"],
                "disposition": "active", "reason": "Needed to distinguish mechanisms.", "evidence": [],
                "resource_implications": "Uses the bounded first tranche.", "reopen_trigger": None, "cycle_ids": []}

    def provider_response(self, output):
        return {"id": "synthetic-provider-return", "model": "fixture-model", "status": "completed",
                "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": json.dumps(output)}]}],
                "usage": {"input_tokens": 37, "output_tokens": 19}}

    def route(self):
        protocol = self.module("review_protocol")
        self.mutate(protocol.record_route, {"id": "route", "adapter": "openai_responses_v1", "model": "fixture-model",
                    "endpoint": "https://api.openai.com/v1/responses", "configuration": {"max_output_tokens": 1500, "timeout_seconds": 5}})

        def probe(route, request, credential):
            packet = json.loads(request["input"][0]["content"])
            return self.provider_response({"allowed_control": packet["allowed_control"], "evidence_control": packet["evidence_control"],
                                           "excluded_controls": []})

        with patch("research_harness.review_transport.send_request", side_effect=probe):
            protocol.probe_route(self.store, {"id": "probe", "route_id": "route"}, credential="fixture-only",
                                 expected_revision=self.store.revision, request_id="probe-route")

    def review_response(self, stage="slate", *, status="sufficient", objections=None):
        kinds = ("impact", "demand", "novelty_risk", "feasibility", "distinctness", "continuity", "grand_challenge", "stop")
        return {"stage": stage, "support": None, "value": {
            "status": status, "consequence": "Resolve the deciding inference at its stated scope.",
            "bar_ids": [] if stage == "bar" else ["bar-a", "bar-b"],
            "objective_adequacy": "not_assessed" if stage == "bar" else "adequate",
            "method_adequacy": "not_assessed" if stage == "bar" else "adequate",
            "reason": "The exact target inference is scientifically consequential.", "evidence": [self.evidence],
            "work_items": [], "objection_findings": [],
            "assurances": [] if stage == "bar" else [{"kind": kind, "status": "passed", "reason": "The corresponding claim is supported by the exact supplied evidence.",
                                                       "evidence": [self.evidence]} for kind in kinds]},
            "objections": [] if objections is None else objections, "limitations": ["Synthetic scientific judgments test software contracts only."]}

    def record_response(self, identifier, reviewer, response, *, dossier_id="dossier-1"):
        protocol, decisions = self.module("review_protocol"), self.module("research_decisions")
        assignment = "assignment-" + identifier
        self.mutate(protocol.record_assignment, {"id": assignment, "route_id": "route", "dossier_id": dossier_id,
                    "role": response["stage"], "reviewer_id": reviewer, "author_id": "author", "context": {}})
        with patch("research_harness.review_transport.send_request", return_value=self.provider_response(response)):
            protocol.invoke_assignment(self.store, {"id": "attempt-" + identifier, "assignment_id": assignment},
                                       expected_revision=self.store.revision, request_id="invoke-" + identifier, credential="fixture-only")
        return self.mutate(decisions.record_value_review, dict(response, id=identifier, dossier_id=dossier_id, assignment_id=assignment))["result"]

    def reviewed_dossier(self, *, response_a=None, response_b=None, dossier_payload=None):
        if dossier_payload is None:
            dossier = self.setup_dossier()
        else:
            self.mutate(self.module("strategy").record_intent, self.intent())
            dossier = self.mutate(self.module("strategy").record_strategy, dossier_payload)["result"]
        self.put("work", "prior-source", {"id": "prior-source", "title": "Synthetic primary comparator", "abstract": self.evidence})
        self.route()
        self.record_response("bar-a", "reviewer-a", self.review_response("bar"))
        self.record_response("bar-b", "reviewer-b", self.review_response("bar"))
        self.record_response("slate-a", "reviewer-a", self.review_response() if response_a is None else response_a)
        self.record_response("slate-b", "reviewer-b", self.review_response() if response_b is None else response_b)
        return dossier

    def decision(self, identifier="decision-1", *, action="investigate"):
        return {"id": identifier, "dossier_id": "dossier-1", "phase": "prospective", "action": action,
                "artifact_disposition": "none", "goal_status": "open", "review_ids": ["slate-a", "slate-b"],
                "bar_review_ids": ["bar-a", "bar-b"], "objections": [], "work_items": [],
                "source_impact": {"sources": [], "aspects": {"nearest_work": "unchanged", "scope": "unchanged", "transfer": "unchanged", "bar": "unchanged"}},
                "reason": "The first bounded test can resolve the critical assumption.", "remaining_objective": ["Other targets remain unresolved."],
                "next_action": "Run the bounded deciding control.", "goal_evidence": [], "support_candidate_digest": None}
