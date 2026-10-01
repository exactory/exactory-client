"""Real shared review records with only the external provider substituted."""

import copy
import json
import uuid
from unittest.mock import patch

from research_harness import review_protocol
from research_harness.storage import Store


def response(output):
    return {"id": "native-fixture-response", "model": "fixture-model", "status": "completed",
        "output": [{"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": json.dumps(output)}]}],
        "usage": {"input_tokens": 37, "output_tokens": 12}}


def mutate(store, function, payload, **options):
    return function(store, payload, expected_revision=store.revision,
                    request_id="native-fixture-" + uuid.uuid4().hex, **options)


def observe_review(controller, value, subject, *, inputs=(), alter_packet=None, output=None):
    """Return the exact observed native review; never mock its acceptance checks."""
    identifier = "native-" + uuid.uuid4().hex
    reviewed = copy.deepcopy(value)
    reviewed["reviewer"]["source"] = "host"
    reviewed["reviewer"]["attestation_id"] = "review-assignment:" + identifier
    kind = "proposal" if "claim_digest" in reviewed and "schema_version" in reviewed else (
        "result" if "claim_digest" in reviewed else "recovery")
    exported = controller.command("review-packet", {"kind": kind, "subject": subject,
        "claim_digest": reviewed.get("claim_digest"), "reviewer": reviewed["reviewer"],
        "inputs": list(inputs)}, None, None)
    store = Store(exported["workspace"])
    route_id = "native-fixture-route"
    if route_id not in store.snapshot()["records"].get("review_route", {}):
        mutate(store, review_protocol.record_route, {"id": route_id, "adapter": "openai_responses_v1",
            "model": "fixture-model", "endpoint": "https://api.openai.com/v1/responses",
            "configuration": {"max_output_tokens": 10000, "timeout_seconds": 5}})
        def positive(route, request, credential):
            packet = json.loads(request["input"][0]["content"])
            return response({"allowed_control": packet["allowed_control"],
                "evidence_control": packet["evidence_control"], "excluded_controls": []})
        with patch("research_harness.review_transport.send_request", side_effect=positive):
            mutate(store, review_protocol.probe_route, {"id": "native-fixture-probe", "route_id": route_id},
                   credential="fixture")
    if alter_packet:
        from research_harness.artifacts import ArtifactStore
        artifacts = ArtifactStore(store.root)
        packet = json.loads(artifacts.read(exported["native_packet"]))
        alter_packet(packet)
        exported["native_packet"] = artifacts.put(json.dumps(packet).encode(), "application/json")
    mutate(store, review_protocol.record_assignment, {"id": identifier, "route_id": route_id,
        "dossier_id": None, "role": "native_math", "reviewer_id": reviewed["reviewer"]["actor_id"],
        "author_id": "native-fixture-author", "context": {"native_packet": exported["native_packet"]}})
    with patch("research_harness.review_transport.send_request", return_value=response(reviewed if output is None else output)):
        mutate(store, review_protocol.invoke_assignment, {"id": identifier + "-attempt",
            "assignment_id": identifier}, credential="fixture")
    return reviewed


def attest_spec(controller, spec):
    """Prepare explicit fixture review observations before a real command."""
    from search_controller.reviewer import find_subject, reviews, objects
    from search_controller.schema import digest
    from search_controller.storage import ContentSink
    from search_controller.evidence import import_inputs
    pending = [value for value in reviews(spec)
               if not value["reviewer"]["attestation_id"].startswith("review-assignment:")]
    if not pending:
        return spec
    state = controller.status()
    content = ContentSink(controller.store)
    inputs = spec.get("inputs", [])
    import_inputs(controller.root, inputs, content)
    subjects = list(objects(spec))
    review_inputs = list(inputs)
    if {"proposal_digest", "computation", "review"} <= spec.keys():
        for node in state["nodes"].values():
            subjects.append({"node_id": node["id"], "proposal_digest": spec["proposal_digest"],
                             "computation": spec["computation"]})
    for mapping in spec.get("mappings", []):
        from search_controller.adoption import snapshot_workspace
        snapshot = snapshot_workspace(controller.root, mapping["attack_slug"], mapping["snapshot_paths"], content)
        content.put_blob(snapshot)
        subject = {key: value for key, value in mapping.items() if key not in {"review", "verification"}}
        subjects.append(subject)
        verification = mapping.get("verification")
        if verification:
            imported = next((row for row in state["service"]["imports"].values()
                             if row["subject"]["attack_slug"] == mapping["attack_slug"]), None)
            identity = imported["id"] if imported else "import-{:06d}".format(len(state["service"]["imports"]) + 1)
            candidate = verification["proposal"]
            content.put_blob(candidate)
            subjects.append({"import_id": identity, "snapshot_digest": mapping["snapshot_digest"],
                "claim_digest": digest(candidate["claim"]), "proposal_digest": digest(candidate), "limits": candidate["limits"]})
    if spec.get("mappings"):
        directory = controller.root / "review-fixture-inputs"
        directory.mkdir(exist_ok=True)
        for (namespace, identity), raw in content.staged.items():
            path = directory / (namespace + "-" + identity)
            path.write_bytes(raw)
            item = {"path": str(path.relative_to(controller.root)), "digest": identity,
                    "kind": "blob" if namespace == "blobs" else "artifact"}
            if not any(row["kind"] == item["kind"] and row["digest"] == identity for row in review_inputs):
                review_inputs.append(item)
    for value in pending:
        subject = find_subject(value, state, content, subjects)
        observed = observe_review(controller, value, subject, inputs=review_inputs)
        value.clear()
        value.update(observed)
    return spec
