"""Real admission and native stage fixtures for metered execution tests."""

import hashlib
import json

from search_controller.service import Controller
from tests.search_fixtures import contract, proposal, review, digest
from tests.support import ALL_YES, OPENING, FIXTURE_STRATEGIES, make_problem, make_preconditions, run, write_study


def admit_workspace(root, slug="sample", max_runs=24, computational=False, exact_claim="True holds.", historical=False, configure=None):
    controller = Controller(root, FIXTURE_STRATEGIES)
    workspace = root / slug
    problem = make_problem()
    problem["claim"] = exact_claim
    problem["quadruple"]["statement"] = "True"
    (workspace / "problem.json").write_text(json.dumps(problem))
    (workspace / "novelty.md").write_text("This fixture verifies the basic proposition True; no novelty is claimed.\n")
    write_study(workspace, "problem", "The proposition is True, proved by its constructor.\n")
    write_study(workspace, OPENING, "Test the stated proposition; counterexamples would refute it.\n")
    original = contract()
    original["original_claim"].update(statement=exact_claim, quantifiers="The exact quantifiers of the declared fixture claim",
                                      scope={"kind": "named", "name": exact_claim})
    original["root_attack_slug"] = slug
    candidate = proposal()
    candidate.update(attack_slug=slug, claim=original["original_claim"], method=OPENING)
    candidate["anchor"]["digest"] = digest(original)
    candidate["limits"]["max_runs"] = max_runs
    if computational:
        candidate["task"] = {"kind": "finite_decision", "purpose": "Check the complete supplied finite fixture inputs", "input_domain": "The exact supplied fixture files"}
        candidate["contribution"]["necessity"] = {"obligation_id": "obligation-000001",
            "omission_consequence": "The fixture's explicit computational premise would remain unchecked",
            "domain_justification": "The fixture contains its complete finite input domain",
            "outcomes": [{"outcome": "verified", "next_action": "Review the exact correspondence with the proposition"},
                         {"outcome": "rejected", "next_action": "Record failure without proof acceptance"}],
            "stopping_condition": "Stop after the bounded command terminates"}
    inputs = []
    for name, relative in [("problem", "study/problem.md"), ("novelty", "novelty.md"), ("strategy", "study/" + OPENING + ".md")]:
        value = hashlib.sha256((workspace / relative).read_bytes()).hexdigest()
        inputs.append({"path": slug + "/" + relative, "digest": value, "kind": "artifact"})
        if name == "strategy":
            candidate["studies"]["strategies"] = [{"method": OPENING, "digest": value}]
        else:
            candidate["studies"][name] = value
    if computational and not historical:
        candidate.update(schema_version=2, computation=computation_contract(candidate["studies"]["problem"]))
    if configure is not None:
        configure(candidate)
        candidate["anchor"]["digest"] = digest(original)
    controller.command("init", {"contract": original}, 0, "initialize")
    if historical:
        # Replay an archived v1 admission, not a new public admission or theorem.
        from search_controller.evidence import import_inputs
        from search_controller.model import apply_event, replay
        import_inputs(root, inputs, controller.store)
        approved = review(candidate)
        controller.store.put_blob(candidate)
        controller.store.put_blob(approved)
        for kind, payload in [
            ("proposal_recorded", {"proposal": candidate, "digest": digest(candidate)}),
            ("review_recorded", {"proposal_id": "proposal-000001", "review": approved, "digest": digest(approved)}),
            ("proposal_admitted", {"proposal_id": "proposal-000001"}),
        ]:
            revision = controller.status()["revision"]
            controller.store.append(kind, payload, revision, "archived-" + kind,
                                    validate=lambda document, event: apply_event(replay(document), event))
    else:
        from tests.research_support import pin_research
        pin_research(root, candidate, inputs)
        controller.command("propose", {"proposal": candidate, "inputs": inputs}, 1, "proposal")
        controller.command("review", {"proposal_id": "proposal-000001", "review": review(candidate), "inputs": []}, 2, "review")
        revision = 3
        if candidate["category"] == "standalone":
            controller.command("review", {"proposal_id": "proposal-000001", "review": review(candidate, "reviewer-two"), "inputs": []}, 3, "second-review")
            revision = 4
        controller.command("admit", {}, revision, "admit", "proposal-000001")
    if historical:
        from tests.research_support import pin_research, amendment_spec
        import copy
        prepared = copy.deepcopy(candidate)
        foundation_inputs = []
        delivery = pin_research(root, prepared, foundation_inputs)
        amendment = amendment_spec(controller, "node-000001", delivery["foundation"], foundation_inputs)
        controller.command("amend-foundation", amendment, controller.status()["revision"], "fixture-foundation-amendment", "node-000001")
    (workspace / "preconditions.json").write_text(json.dumps(make_preconditions(ALL_YES)))
    status, out, err = run(["plan", slug], root)
    if status:
        raise AssertionError(err)
    openings = json.loads((workspace / "openings.json").read_text())["openings"]
    ranking = {"generated_from": "openings.json", "order": [
        {"strategy": item["strategy"], "cites": ["shape.objects"], "reason": "The studied fixture order applies"}
        for item in openings]}
    (workspace / "ranking.json").write_text(json.dumps(ranking))
    return controller


def computation_contract(evidence_digest):
    return {"schema_version": 1,
        "domain": {"kind": "case_ids", "case_ids": ["supplied-fixture"]},
        "basis": {"kind": "diagnostic", "deduction_digest": evidence_digest, "dependencies": [],
                  "completeness_acceptance_id": None, "bound_acceptance_id": None},
        "preflight": {"uncertainty": "Whether the actual supplied fixture command succeeds",
                      "inspected_evidence": [evidence_digest], "already_determined": False,
                      "cheapest_sufficient_check": "Execute the exact bounded fixture once",
                      "failure_signal": "A nonzero exit or a timeout leaves the fixture undecided"},
        "verification_plan": {"certificate_shape": "Exact fixture result and captured logs",
                              "checker_method": "Independent review of the exact fixture output",
                              "producer_seconds": 2, "checker_seconds": 2, "checker_cap_seconds": 300,
                              "fallback": "Keep the question undecided and redesign the check",
                              "max_input_bytes": 67108864}}


def begin_spec():
    return {"strategy": OPENING, "entry": "test-strengthenings-by-counterexample", "pass": 1,
            "trigger_features": ["shape.target_quantity"], "step_cites": []}


def invoke(controller, name, spec, target="node-000001"):
    revision = controller.status()["revision"]
    return controller.command(name, spec, revision, "execution-%d-%s" % (revision, name), target)


def command_spec(workspace, argv, timeout=2):
    step = workspace / "deterministic" / "job"
    step.mkdir(exist_ok=True)
    artifacts = [{"path": str(path.relative_to(workspace.parent)),
                  "digest": hashlib.sha256(path.read_bytes()).hexdigest(), "role": "input"}
                 for path in sorted(step.rglob("*")) if path.is_file()]
    return {"kind": "command", "step_dir": "job", "argv": argv, "artifacts": artifacts,
            "external_dependencies": [], "environment": {}, "dependency_enumeration": "All local job files, no additional project dependencies",
            "timeout_seconds": timeout, "expected_outputs": []}


def review_native_inputs(controller, slug, step_name, kind="certificate", reviewer="input-reviewer"):
    from argparse import Namespace
    from search_controller.execution import native_spec, verification_entry_subject
    from tests.search_fixtures import provenance
    node = next(node for node in controller.status()["nodes"].values() if node["attack_slug"] == slug)
    args = Namespace(attack_root=controller.root, slug=slug, step_dir=step_name, verify_command=kind)
    spec = native_spec(controller, node, args)
    value = {"subject_digest": digest(verification_entry_subject(node, spec)), "claim_digest": node["claim_digest"],
        "reviewer": provenance(reviewer), "decision": "approve", "findings": {
            "statement": "The frozen fixture inputs target the exact admitted proposition",
            "assumptions": "There are no additional assumptions in this test fixture",
            "scope": "The checker verifies the complete declared fixture domain",
            "dependencies": "All local fixture inputs and explicitly declared dependencies are pinned",
            "policy": "This review authorizes input-bound verification, with result acceptance still pending"}}
    (controller.root / slug / "deterministic" / step_name / "verification-review.json").write_text(json.dumps(value))
    return value
