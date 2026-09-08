"""Actual common acquisition, reading and synthesis fixtures for native gates."""

import json
from pathlib import Path
import sys
import uuid

PLUGIN = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PLUGIN))
sys.path.append(str(PLUGIN / "tests"))

from integration_fixtures import prepare_research
from research_harness.native_export import export_native
from research_harness.storage import Store
from search_controller.service import Controller


def pin_research(root, candidate, inputs, *, original=None):
    root = Path(root)
    if original is None:
        original = Controller(root).status()["contract"]
    objective = {"kind": "objective", "id": "native-complete-objective", "statement": original["original_claim"]["statement"]}
    if not (root / ".exactory/research.sqlite3").exists():
        store = prepare_research(root, objective).store
    else:
        store = Store(root)
    delivery = export_native(store, root, root / "research/native-inputs" / uuid.uuid4().hex)
    candidate.update(schema_version=3, computation=candidate.get("computation"), foundation=delivery["foundation"])
    inputs.extend(delivery["inputs"])
    return delivery


def amendment_spec(controller, node_id, foundation, inputs, reason="Adopt complete current preparation before more work."):
    from search_controller.research import effective_foundation
    from tests.search_fixtures import provenance, digest
    state = controller.status()
    node = state["nodes"][node_id]
    previous = effective_foundation(state, node)
    subject = {"node_id": node_id, "proposal_digest": node["admission"]["proposal_digest"],
               "previous_snapshot_digest": previous["snapshot_digest"] if previous else None,
               "foundation": foundation, "reason": reason}
    review = {"subject_digest": digest(subject), "claim_digest": node["claim_digest"],
              "reviewer": provenance("foundation-reviewer"), "decision": "approve",
              "findings": {key: "The exact native claim, source bytes and current preparation were independently assessed."
                           for key in ("statement", "assumptions", "scope", "dependencies", "policy")}}
    return {"subject": subject, "review": review, "inputs": inputs}
