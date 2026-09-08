"""Current domain fixtures shared by CLI, transport and native boundary tests."""

import copy
import json
from pathlib import Path
import sys

from development_fixtures import DevelopmentCase
from research_harness.artifacts import ArtifactStore
from research_harness.storage import Store


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
    config = case.store.snapshot()["records"].get("configuration", {}).get("research")
    if config is None:
        case.mutate(case.api("principles").initialize_research, {"profile": "research", "target": case.objective})
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
    case.assertTrue(api.synthesis_report(case.store, "research")["ready"])
    if candidate:
        observed_candidate(case)
    return case


def observed_candidate(case):
    """Create a full candidate through the actual managed producer and observer."""
    from research_harness.execution import launch_execution
    body = '''import json
from pathlib import Path
values = [n * n for n in range(4)]
data = {"result": {"values": values, "bound": 9},
        "validation": {"passed": max(values) == 9 and all(v <= 9 for v in values)}}
for name in ("result", "validation"):
    Path("results/" + name + ".json").write_text(json.dumps(data))
print(json.dumps({"metric": max(values)}))
'''
    admission = admit_lab(case, body=body, outputs=[
        {"id": "result", "requirement_id": "measurements", "path": "results/result.json", "media_type": "application/json"},
        {"id": "validation", "requirement_id": "checks", "path": "results/validation.json", "media_type": "application/json"}])
    result = launch_execution(case.store, admission["id"], expected_revision=case.store.revision, request_id="fixture-observed-run")
    case.assertTrue(result["ok"])
    records = case.store.snapshot()["records"]
    execution = records["execution"][records["execution_outcome"][admission["id"]]["execution_id"]]["payload"]
    plan = records["cycle_plan"][admission["cycle_id"]]["payload"]
    case.execution_payload = execution
    case.mutate(case.development().assess_cycle, case.assessment(plan, execution))
    case.save_checkpoint()
    case.mutate(case.development().record_readiness_review, case.review(execution))
    return execution


def prepare_manuscript(case, *, pdf="draft/paper.pdf", sources=None):
    """Pin the existing manuscript and record two actual independent reviews."""
    from research_harness import publication
    from test_research_publication import ResearchPublicationTests
    claims = case.root / "evidence/claims.json"
    claims.parent.mkdir(parents=True, exist_ok=True)
    claims.write_text(json.dumps([{"id": "bound", "claim": "The maximum is 9."}]))
    identifier = "publication-" + str(case.store.revision)
    bundle = case.mutate(publication.prepare_publication, {"id": identifier,
        "files": {"pdf": pdf, "abstract": "draft/abstract.txt", "bibliography": "draft/references.bib",
                  "claims": "evidence/claims.json", "sources": sources},
        "claim_evidence": [{"claim_id": "bound", "evidence": [case.result_evidence(case.execution_payload)]}]})["result"]
    for number in (1, 2):
        assessor = identifier + "-reviewer-" + str(number)
        core = ResearchPublicationTests.core(case)
        review = {"id": assessor, "bundle_digest": bundle["digest"], "assessor": {"id": assessor, "kind": "agent",
            "provenance": case.artifacts.put(("Independent fixture context " + assessor).encode(), "text/plain"),
            "relationship": "Independent fixture assessor.", "independence_basis": "Separate blind context received the exact manuscript and evidence."},
            "review": case.artifacts.put(json.dumps(core).encode(), "application/json"), "blind": True}
        case.mutate(publication.record_manuscript_review, review)
    case.assertTrue(publication.publication_report(case.store)["ready"])
    return bundle


def admit_lab(case, script="code/program.py", *, body=None, run_id="lab-run", backend="local", timeout=5, seed=None,
              outputs=None, usage_unit="execution", reserved_units=1, max_units=8):
    path = case.root / "experiment" / script
    if body is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    plan = case.plan(max_units=max_units)
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
