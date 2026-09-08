"""Authored prospective studies using real storage, sources and local execution.

The finite enumeration is a test fixture, not a research or proof-acceptance
claim. Review records model documented assessments, not independent reviewers.
"""

import copy
import importlib
import json
import subprocess
import sys

from test_research_synthesis import SynthesisCase


PROGRAM = b'''import json
values = [n * n for n in range(4)]
print(json.dumps({"result": {"values": values, "bound": 9},
                  "validation": {"passed": max(values) == 9 and all(v <= 9 for v in values)}}))
'''


class DevelopmentCase(SynthesisCase):
    def development(self):
        return importlib.import_module("research_harness.development")

    def prepared_study(self):
        work = self.metadata()
        self.objective = {"kind": "objective", "id": "finite-square-bound",
                          "statement": "For every integer n in [0, 3], n squared is at most 9, with equality at n = 3."}
        self.mutate(self.api("principles").initialize_research, {"profile": "research", "target": self.objective})
        self.scope([work])
        self.links = [self.read_source(n) for n in range(1, 7)]
        self.complete_foundation(work)
        synthesis = self.api()
        self.mutate(synthesis.record_standards, self.standards(self.links[0]))
        self.mutate(synthesis.record_rationale, self.rationale(self.links[0]))
        self.mutate(synthesis.record_context, self.context(self.links[0]))
        cases = [self.case(self.links[0], 0, "within_field")]
        cases.extend(self.case(link, n) for n, link in enumerate(self.links[1:], 1))
        self.mutate(synthesis.record_innovation, self.innovation(cases))
        self.assertTrue(synthesis.synthesis_report(self.store, "research")["ready"])

    def result_scope(self, partial=False):
        return {"id": "n-zero" if partial else self.objective["id"], "kind": "partial" if partial else "full",
                "statement": "At n = 0 the square is at most 9." if partial else self.objective["statement"],
                "assumptions": ["n is an integer in the stated finite range."],
                "remaining_obligations": ["Establish the bound for n = 1, 2, 3 and equality at 3."] if partial else []}

    def refresh_synthesis(self, suffix):
        api = self.api()
        for kind in ("standards", "rationale", "context", "innovation"):
            records = self.store.snapshot()["records"]
            identifier = records["synthesis_selection"]["research:" + kind]["id"]
            payload = copy.deepcopy(records["synthesis"][identifier]["payload"])
            payload["id"] = kind + "-" + suffix
            self.mutate(getattr(api, "record_" + kind), payload)

    def source_evidence(self):
        return {"kind": "source", "link": copy.deepcopy(self.links[0])}

    def plan(self, identifier="cycle-1", *, partial=False, max_executions=4, max_units=8):
        scope = self.result_scope(partial)
        return {"id": identifier, "author": "cycle-author", "objective": self.objective, "scope": scope,
                "hypothesis": scope["statement"], "question": "Does exhaustive enumeration attain the proposed finite bound?",
                "strategy": {"kind": "tightness_limits", "mechanism": "Exhaustive finite enumeration.",
                             "why": "Every admissible integer can be tested and the extremum identified."},
                "predecessor": None, "inheritance": [], "reopening": [],
                "distinguishing_test": "Enumerate all integers in the scope and compare each square with 9.",
                "expected_outcomes": [{"id": "bound", "statement": "Every value is bounded, and the stated extremum is attained."}],
                "failure_signals": [{"id": "counterexample", "statement": "An admissible input violates the claimed bound."}],
                "evidence_requirements": [{"id": "measurements", "kind": "result", "description": "The enumerated inputs and values."},
                                          {"id": "checks", "kind": "validation", "description": "The comparison against the proposed bound."}],
                "literature": {"scope": scope, "foundation_digest": self.api().synthesis_report(self.store, "research")["foundation"]["digest"],
                               "comparison": "The authored source describes bounded cases; this test checks the exact finite claim.",
                               "sources": [self.links[0]], "gaps": []},
                "resource_limits": {"max_executions": max_executions, "max_units": max_units, "unit": "fixture_step"}}

    def admit(self, cycle_id="cycle-1", identifier="run-1", units=1, *, program_data=PROGRAM, inputs=()):
        api = self.development()
        program = self.artifacts.put(program_data, "text/x-python; charset=utf-8")
        directory = self.root / "research" / "fixture-runs"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (identifier + ".py")
        path.write_bytes(program_data)
        command = {"argv": [sys.executable, str(path)], "program": program, "inputs": list(inputs),
                   "versions": {"python": sys.version.split()[0]}, "seed": None,
                   "seed_reason": "The finite enumeration is deterministic."}
        plan = self.store.snapshot()["records"]["cycle_plan"][cycle_id]
        payload = {"id": identifier, "cycle_id": cycle_id, "plan_digest": plan["digest"],
                   "command": command, "reserved_units": units}
        return self.mutate(api.admit_execution, payload)["result"]

    def execution(self, admission, identifier=None, *, status="completed", exit_code=0, used_units=1):
        run = subprocess.run(admission["command"]["argv"], capture_output=True, check=True)
        artifact = self.artifacts.put(run.stdout, "application/json")
        return {"id": identifier or ("execution-" + admission["id"]), "cycle_id": admission["cycle_id"],
                "origin": {"kind": "managed", "admission_id": admission["id"]},
                "command": admission["command"], "status": status, "exit_code": exit_code,
                "usage": {"units": used_units, "reason": "One finite enumeration completed."},
                "outputs": [{"id": "result", "requirement_id": "measurements", "artifact": artifact},
                            {"id": "validation", "requirement_id": "checks", "artifact": artifact}],
                "notes": "The fixture actually ran the saved program with the admitted command."}

    def run_cycle(self, plan=None, *, run_id="run-1"):
        api = self.development()
        plan = plan or self.plan()
        self.mutate(api.plan_cycle, plan)
        admission = self.admit(plan["id"], run_id)
        execution = self.execution(admission)
        self.mutate(api.record_execution, execution)
        return plan, execution

    def result_evidence(self, execution, validation=False):
        output = execution["outputs"][1 if validation else 0]
        data = json.loads(self.artifacts.read(output["artifact"]))
        key = "validation" if validation else "result"
        return {"kind": "result", "execution_id": execution["id"], "output_id": output["id"],
                "artifact": output["artifact"], "locator": {"kind": "json", "pointer": "/" + key, "value": data[key]}}

    def assessment(self, plan, execution, identifier="assessment-1", *, partial=False, development=True):
        result = self.result_evidence(execution)
        check = self.result_evidence(execution, True)
        source = self.source_evidence()
        scope = self.result_scope(True) if partial else copy.deepcopy(plan["scope"])
        return {"id": identifier, "cycle_id": plan["id"], "author": "cycle-author", "scope": scope,
                "execution_ids": [execution["id"]], "result": {"statement": scope["statement"], "evidence": [result]},
                "validity_checks": [{"id": "exhaustive", "question": "Was every admissible input tested against the bound?",
                                     "method": "Compare the enumeration and its maximum with the claimed finite bound.",
                                     "status": "passed", "explanation": "The enumerated values and the extremum agree.", "evidence": [check]}],
                "outcomes": [{"outcome_id": "bound", "status": "observed", "explanation": "The bounded finite result was observed.", "evidence": [result]}],
                "failures": [{"signal_id": "counterexample", "target_claim": plan["hypothesis"], "status": "not_observed",
                              "explanation": "No input in the specified scope violates the bound.", "evidence": [check]}],
                "findings": [], "assumptions": scope["assumptions"],
                "remaining_obligations": scope["remaining_obligations"], "objective_status": "open" if partial else "achieved",
                "disposition": "continue" if partial else "complete",
                "development": {"bottleneck_change": "The exact finite range and its extremum have been checked.",
                    "next_question": None, "strategy": "none", "reason": "No useful unresolved branch remains within this complete finite objective.",
                    "novelty": {"scope": scope, "foundation_digest": self.api().synthesis_report(self.store, "research")["foundation"]["digest"],
                                "comparison": "The evidence is specific to the stated finite target and does not claim an unbounded theorem.",
                                "evidence": [source, result], "gaps": []},
                    "contribution": {"and": "Finite domains permit exhaustive checks.", "but": "The exact bound still needed testing.",
                                     "therefore": "The enumeration establishes this finite instance.", "evidence": [source, result]},
                    "alternatives": [{"strategy": "generalization", "question": "Does the bound extend beyond n = 3?",
                                      "disposition": "not_useful", "reason": "That extension is outside this fixed finite objective.", "evidence": [result]}],
                    "branches": [{"cycle_id": plan["id"], "disposition": "resolved", "reason": "The entire planned finite scope is covered.",
                                  "evidence": [result]}]} if development else None}

    def save_checkpoint(self, cycle_id="cycle-1", assessment_id="assessment-1", identifier="checkpoint-1", *, select=True):
        return self.mutate(self.development().checkpoint,
            {"id": identifier, "cycle_id": cycle_id, "assessment_id": assessment_id, "reason": "Preserve the assessed result and its remaining obligations.",
             "next_hypothesis": None, "select_for_readiness": select})["result"]

    def candidate(self):
        return self.development().readiness_report(self.store)["candidate"]

    def review(self, execution, identifier="review-1", verdict="ready"):
        evidence = [self.source_evidence(), self.result_evidence(execution), self.result_evidence(execution, True)]
        candidate = self.candidate()
        for reference in candidate["evidence"]:
            if reference not in evidence:
                evidence.append(copy.deepcopy(reference))
        provenance = self.artifacts.put(b"Authored test review in a separate declared context; no real reviewer is claimed.\n", "text/plain")
        return {"id": identifier, "candidate_digest": candidate["digest"],
                "assessor": {"id": "independent-assessor", "kind": "agent", "provenance": provenance,
                             "relationship": "The fixture assessor is distinct from the cycle author.",
                             "independence_basis": "A separately declared review context received the exact candidate and evidence."},
                "verdict": verdict, "checks": [{"kind": kind, "status": "passed" if verdict == "ready" else "unresolved",
                    "reason": "The authored candidate documents the exact finite claim, evidence and limits.", "evidence": evidence}
                    for kind in ("validity", "scope", "novelty", "contribution", "development", "branches")],
                "limitations": ["This authored receipt does not establish comprehension, impartiality or scientific novelty."]}

    def prepared_candidate(self):
        self.prepared_study()
        plan, execution = self.run_cycle()
        assessment = self.assessment(plan, execution)
        self.mutate(self.development().assess_cycle, assessment)
        self.save_checkpoint()
        return plan, execution, assessment

    def readiness_codes(self):
        return {o["code"] for o in self.development().readiness_report(self.store)["obligations"]}
