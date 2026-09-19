"""Current domain fixtures shared by CLI, transport and native boundary tests."""

import copy
import json
from pathlib import Path
import sys

from development_fixtures import DevelopmentCase
from research_harness.artifacts import ArtifactStore
from research_harness.principles import preparation_policy
from research_harness.storage import Store


def build_review_core(decision="accept"):
    """The fixture's blind review core: every score below 4 names the change that would bring it to 4."""
    return {"summary": "The authored finite result is explicitly scoped.", "strengths": ["All four integers are enumerated."],
            "weaknesses": ["The result establishes no unbounded generalization."], "soundness": 3,
            "presentation": 3, "contribution": 3, "overall": 6, "decision": decision,
            "changes_for_maximum": {
                "soundness": ["Section 2: state the enumeration's input range beside the claimed bound."],
                "presentation": ["Section 1: name the finite range in the first sentence."],
                "contribution": ["Establish the bound for every bounded input sequence, not only for n in [0, 3]."]}}


def build_manuscript_review(case, bundle, assessor, decision="accept"):
    """One blind reviewer's unchanged rubric JSON, wrapped for `manuscript-review` on the exact bundle."""
    return {"id": assessor, "bundle_digest": bundle["digest"], "assessor": {"id": assessor, "kind": "agent",
            "provenance": case.artifacts.put(("Authored independent context " + assessor).encode(), "text/plain"),
            "relationship": "A separate fixture assessor.",
            "independence_basis": "A new blind context received the manuscript and exact evidence bytes."},
            "review": case.artifacts.put(json.dumps(build_review_core(decision)).encode(), "application/json"), "blind": True}


def build_prediction(case, bundle, assessor, percentile=30, band=(20, 40)):
    """One blind assessor's cohort prediction on the bundle, in the study fixture's cohort."""
    return {"id": assessor + "-prediction", "bundle_digest": bundle["digest"], "blind": True,
            "assessor": {"id": assessor, "kind": "agent",
                         "provenance": case.artifacts.put(("Blind context " + assessor).encode(), "text/plain"),
                         "relationship": "A separate fixture assessor.", "independence_basis": "A blind context received the exact manuscript."},
            "prediction": {"corpus": "arxiv", "category": "cs.LG", "windowStart": "2026-01-01", "windowEnd": "2026-01-31",
                           "percentile": percentile, "band": {"best": band[0], "worst": band[1]}},
            "reasons": ["The authored fixture states a narrow finite result."]}


def record_measurement(case, bundle, suffix, percentiles=(30, 25, 40)):
    """Three blind reviews and three predictions on the bundle, as one complete measurement."""
    from research_harness import predictions, publication
    for number, percentile in enumerate(percentiles, 1):
        assessor = "measure-" + suffix + "-" + str(number)
        case.mutate(publication.record_manuscript_review, build_manuscript_review(case, bundle, assessor))
        case.mutate(predictions.record_prediction, build_prediction(case, bundle, assessor, percentile))


def build_contribution_analysis(case, bundle, suffix):
    """The analysis of a measured bundle: one next-round step that adopts every reviewer contribution change."""
    from research_harness import predictions
    reviews = predictions.select_measurement_reviews(case.store.snapshot()["records"], bundle) or []
    claims = json.loads(case.artifacts.read(bundle["files"]["claims"]["artifact"]))
    current_claim_ids = [claim["id"] for claim in claims if "superseded" not in claim]
    step = {"id": "step-" + suffix, "statement": "Extend the finite bound to every bounded input sequence (" + suffix + ").",
            "criterion_ids": ["rc-general"], "direction": "vertical", "reach": "next_round", "builds_on": current_claim_ids[:1],
            "community": {"who": "Authors of bounded-sequence proofs", "capability": "Apply the bound without a new enumeration.",
                          "evidence": [case.source_evidence()]},
            "risks": ["An unbounded input may violate the bound."], "evidence": [case.source_evidence()]}
    return {"id": "analysis-" + suffix, "bundle_digest": bundle["digest"], "searches": ["gc-" + suffix],
            "position": {"criterion_ids": ["rc-finite"], "established": "The bound holds on the stated finite range.",
                         "remaining": "Every bounded input sequence beyond the finite range.", "evidence": [case.source_evidence()]},
            "reviewer_changes": [{"review_id": saved["id"], "change": change, "disposition": "adopted", "step_id": step["id"],
                                  "reason": "The step pursues the reviewer's requirement for the highest contribution."}
                                 for saved in reviews for change in saved["core"].get("changes_for_maximum", {}).get("contribution", [])],
            "steps": [step]}


def record_contribution_analysis(case, bundle, suffix):
    """A grand_challenge search after the pin, then the contribution analysis of the measured bundle."""
    from research_harness import contribution
    case.record_purpose("grand_challenge", "gc-" + suffix)
    return case.mutate(contribution.record_contribution_analysis, build_contribution_analysis(case, bundle, suffix))["result"]


def prepare_verification(root):
    from test_research_synthesis import SynthesisCase
    case = SynthesisCase(methodName="runTest")
    case.root = Path(root)
    case.store = Store(case.root, create=True)
    case.artifacts = ArtifactStore(case.root)
    case.sequence = 1000
    work = case.configured("verification")
    case.linked = case.read_source()
    case.complete_foundation(work, "verification")
    case.mutate(case.api().record_standards, case.standards(case.linked, "verification"))
    case.assertTrue(case.api().synthesis_report(case.store, "verification")["ready"])
    return case


def pin_native_research(root, proposal, inputs, original):
    from research_harness.native_export import export_native
    import uuid
    objective = {"kind": "objective", "id": "native-complete-objective", "statement": original["original_claim"]["statement"]}
    case = prepare_research(root, objective)
    delivery = export_native(case.store, root, Path(root) / "research/native-inputs" / uuid.uuid4().hex)
    proposal.update(schema_version=3, computation=proposal.get("computation"), foundation=delivery["foundation"])
    inputs.extend(delivery["inputs"])
    return delivery


def prepare_research(root, objective=None, *, candidate=False):
    """Use actual acquisition, reading and synthesis services, never a gate mock."""
    case = DevelopmentCase(methodName="runTest")
    case.root = Path(root)
    case.store = Store(case.root, create=True)
    case.artifacts = ArtifactStore(case.root)
    case.sequence = case.store.revision + 1000
    case.objective = objective or {"kind": "objective", "id": "finite-square-bound",
        "statement": "For every integer n in [0, 3], n squared is at most 9, with equality at n = 3."}
    records = case.store.snapshot()["records"]
    config = records.get("configuration", {}).get("research")
    # A study the product CLI initialized records the research default, lineage-v1; these integration tests prepare under the legacy policy.
    recorded_policy = preparation_policy(records)
    if recorded_policy != "exhaustive-v1":
        case.mutate(case.api("principles").change_policy, {"previous": recorded_policy, "policy": "exhaustive-v1",
                                                           "reason": "The integration fixture prepares the study under the legacy policy."})
    if config is None:
        case.mutate(case.api("principles").initialize_research, {"profile": "research", "target": case.objective, "preparation_policy": "exhaustive-v1"})
    elif config["target"] is None:
        case.mutate(case.api("principles").set_target, {"target": case.objective, "reason": "Fix the complete fixture objective before planning."})
    else:
        case.assertEqual(config["target"], case.objective)
    work = case.metadata()
    case.scope([work])
    case.links = [case.read_source(n) for n in range(1, 7)]
    case.complete_foundation(work)
    api = case.api()
    case.mutate(api.record_standards, case.standards(case.links[0]))
    case.mutate(api.record_rationale, case.rationale(case.links[0]))
    case.mutate(api.record_context, case.context(case.links[0]))
    cases = [case.case(case.links[0], 0, "within_field")]
    cases.extend(case.case(link, n) for n, link in enumerate(case.links[1:], 1))
    case.mutate(api.record_innovation, case.innovation(cases))
    case.mutate(case.api("challenge").record_grand_challenge, case.grand_challenge(case.links[0]))
    case.assertTrue(api.synthesis_report(case.store, "research")["ready"])
    if candidate:
        observed_candidate(case)
    return case


OBSERVED_PROGRAM = '''import json
from pathlib import Path
values = [n * n for n in range(4)]
data = {"result": {"values": values, "bound": 9},
        "validation": {"passed": max(values) == 9 and all(v <= 9 for v in values)}}
for name in ("result", "validation"):
    Path("results/" + name + ".json").write_text(json.dumps(data))
print(json.dumps({"metric": max(values)}))
'''


def observe_run(case, *, plan=None, script="code/program.py", run_id="lab-run", request_id="fixture-observed-run"):
    """Admit, bind and launch one managed run of the finite program through the actual launcher; returns its execution payload."""
    from research_harness.execution import launch_execution
    admission = admit_lab(case, script, body=OBSERVED_PROGRAM, run_id=run_id, plan=plan, outputs=[
        {"id": "result", "requirement_id": "measurements", "path": "results/result.json", "media_type": "application/json"},
        {"id": "validation", "requirement_id": "checks", "path": "results/validation.json", "media_type": "application/json"}])
    result = launch_execution(case.store, admission["id"], expected_revision=case.store.revision, request_id=request_id)
    case.assertTrue(result["ok"])
    records = case.store.snapshot()["records"]
    return records["execution"][records["execution_outcome"][admission["id"]]["execution_id"]]["payload"]


def observed_candidate(case):
    """Create a full candidate through the actual managed producer and observer."""
    execution = observe_run(case)
    plan = case.store.snapshot()["records"]["cycle_plan"][execution["cycle_id"]]["payload"]
    case.execution_payload = execution
    case.mutate(case.development().assess_cycle, case.assessment(plan, execution))
    case.save_checkpoint()
    case.mutate(case.development().record_readiness_review, case.review(execution))
    return execution


def prepare_manuscript(case, *, pdf="draft/paper.pdf", sources=None, stop=False):
    """Pin the existing manuscript and record two actual independent reviews."""
    from research_harness import publication
    claims = case.root / "evidence/claims.json"
    claims.parent.mkdir(parents=True, exist_ok=True)
    claims.write_text(json.dumps([{"id": "bound", "claim": "The maximum is 9."}]))
    identifier = "publication-" + str(case.store.revision)
    bundle = case.mutate(publication.prepare_publication, {"id": identifier,
        "files": {"pdf": pdf, "abstract": "draft/abstract.txt", "bibliography": "draft/references.bib",
                  "claims": "evidence/claims.json", "sources": sources},
        "claim_evidence": [{"claim_id": "bound", "evidence": [case.result_evidence(case.execution_payload)]}]})["result"]
    for number in (1, 2):
        case.mutate(publication.record_manuscript_review,
                    build_manuscript_review(case, bundle, identifier + "-reviewer-" + str(number)))
    case.assertTrue(publication.publication_report(case.store)["ready"])
    if stop:
        approve_publication_stop(case, bundle)
    return bundle


def approve_publication_stop(case, bundle):
    """Close a publication fixture through the real independent round review.

    The decision needs the bundle's complete measurement and contribution analysis: the fixture records
    them when the bundle has none, and lists the analysis's steps as deferred candidates."""
    from research_harness import contribution, predictions, rounds
    suffix = "stop-" + bundle["id"]
    if predictions.select_measurement_reviews(case.store.snapshot()["records"], bundle) is None:
        record_measurement(case, bundle, suffix)
    analysis = contribution.find_analysis(case.store.snapshot()["records"], bundle["digest"]) \
        or record_contribution_analysis(case, bundle, suffix)
    evidence = [case.result_evidence(case.execution_payload)]
    deferred = [{"id": step["id"], "direction": step["direction"], "statement": step["statement"], "disposition": "deferred",
                 "reason": "The fixture keeps the Grand Challenge step for a later study.", "evidence": evidence}
                for step in analysis["payload"]["steps"]]
    decision = case.mutate(rounds.record_round, {
        "id": "stop-" + bundle["id"], "closes": rounds.current_number(case.store.snapshot()["records"]),
        "decision": "stop", "bundle_digest": bundle["digest"],
        "candidates": [{"id": "wider-range", "direction": "vertical", "statement": "Extend the finite range.",
                        "disposition": "rejected", "reason": "The fixture supports the stated finite result only.",
                        "evidence": evidence}] + deferred,
        "carried": [], "next": None, "reason": "The fixture records its final bounded contribution."})["result"]
    return case.mutate(rounds.record_round_review, {
        "id": "review-" + decision["id"], "round_id": decision["id"], "round_digest": decision["digest"],
        "assessor": {"id": "publication-round-assessor", "kind": "agent",
                     "provenance": case.artifacts.put(b"Separate fixture round assessor.", "text/plain"),
                     "relationship": "Independent fixture assessor.",
                     "independence_basis": "A separate context received the round packet."},
        "verdict": "approved", "checks": [
            {"kind": kind, "status": "passed", "reason": "The fixture supports the final finite result.", "evidence": evidence}
            for kind in ("stop", "demand")],
        "limitations": ["Synthetic fixture evidence."]})["result"]


def admit_lab(case, script="code/program.py", *, body=None, run_id="lab-run", backend="local", timeout=5, seed=None,
              outputs=None, usage_unit="execution", reserved_units=1, max_units=8, plan=None):
    path = case.root / "experiment" / script
    if body is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    plan = plan or case.plan(max_units=max_units)
    plan["resource_limits"]["unit"] = usage_unit
    case.mutate(case.development().plan_cycle, plan)
    pinned = case.store.snapshot()["records"]["cycle_plan"][plan["id"]]
    admission = {"id": run_id, "cycle_id": plan["id"], "plan_digest": pinned["digest"], "reserved_units": reserved_units,
                 "command": {"argv": [sys.executable, str(path)],
                    "program": case.artifacts.put(path.read_bytes(), "text/x-python"), "inputs": [],
                    "versions": {"python": sys.version.split()[0]}, "seed": seed,
                    "seed_reason": "Deterministic fixture." if seed is None else None}}
    result = case.mutate(case.development().admit_execution, admission)["result"]
    import importlib
    launch = importlib.import_module("research_harness.execution")
    binding = {"admission_id": run_id, "script": script, "backend": backend, "timeout_seconds": timeout,
               "inputs": [], "outputs": outputs or [{"id": "result", "requirement_id": "measurements", "path": "stdout", "media_type": "text/plain"}],
               "usage_unit": usage_unit}
    case.mutate(launch.bind_execution, binding)
    return result
