"""Authored common searches and full readings for native openness fixtures."""

import copy
import json
from pathlib import Path
import sys
import uuid

PLUGIN = Path(__file__).resolve().parents[4]
sys.path.append(str(PLUGIN / "tests"))

from literature_fixtures import LiteratureCase
from research_harness.acquisition import import_response
from research_harness.artifacts import ArtifactStore
from research_harness.literature import import_bundle, record_search
from research_harness.reading import record_reading
from research_harness.storage import Store
from search_controller.reviewer import common_root
from search_controller.schema import digest


def open_common_case(controller):
    state = controller.status()
    common = common_root(controller.root, state)
    case = LiteratureCase()
    case.root, case.store, case.artifacts = common, Store(common, create=True), ArtifactStore(common)
    case.sequence = int(uuid.uuid4().hex[:12], 16)
    return case


def attach_minimum(controller, review, *, subject=None, neighbours=(), relevant=False, read=True,
                   search_tag="", disposition="relevant", resolved=(), duplicate_of=None):
    value = copy.deepcopy(review)
    state = controller.status()
    case = open_common_case(controller)
    finding = value["findings"]["openness"]
    from search_controller.reviewer import find_subject
    from search_controller.storage import ContentSink
    subject = subject if subject is not None else find_subject(value, state, ContentSink(controller.store))
    claim = subject.get("claim", state["contract"]["original_claim"])
    relevant = relevant or finding["outcome"] == "already_solved"
    found, readings = [], []
    if relevant:
        work = case.metadata(99991)
        found = [work]
        if duplicate_of is not None:
            found.append(case.metadata(duplicate_of))
        decisive = found[-1]
        records = case.store.snapshot()["records"]
        if read and not any(row["version_id"] == decisive and row["depth"] == "fulltext" for row in records.get("reading", {}).values()):
            bundle = case.bundle(decisive, bundle_id="native-openness-body")
            case.mutate(import_bundle, bundle)
            case.mutate(record_reading, case.full_note(bundle, note_id="native-openness-reading"))
        readings = [digest(row) for row in case.store.snapshot()["records"].get("reading", {}).values()
                    if row["version_id"] == decisive and row["depth"] == "fulltext"]
    searches = []
    for formulation in [claim["statement"], *neighbours]:
        identifier = "native-openness-" + digest([formulation, relevant, search_tag, disposition, list(resolved), duplicate_of])[:24]
        if identifier not in case.store.snapshot()["records"].get("literature_search", {}):
            data = {"query": formulation, "results": [{"id": work, "title": "Authored mathematical source"} for work in found]}
            case.sequence += 1
            capture = import_response(case.store, "mcp", json.dumps(data).encode(), source_url="https://example.org/native-search",
                captured_at="2026-10-15T12:00:00Z", media_type="application/json",
                mappings=[{"id": "/results/%d/id" % i, "title": "/results/%d/title" % i} for i in range(len(found))],
                expected_revision=case.store.revision, request_id="native-search-" + str(case.sequence))
            case.mutate(record_search, {"id": identifier, "profile": "research", "purpose": "direct",
                "question": formulation, "scope": formulation, "queries": [formulation],
                "responses": [{"source_id": capture["source_ids"][0], "query": formulation,
                               "query_locator": {"kind": "json", "pointer": "/query", "value": formulation}, "results_pointer": "/results"}],
                "captured_at": "2026-10-15T12:00:00Z", "found_work_ids": found, "verdict": "nothing-new",
                "cited_work_ids": [], "dispositions": [{"work_id": work, "disposition": disposition if i == 0 else "relevant",
                    "reason": "A decisive authored prior source for this software fixture."} for i, work in enumerate(found)],
                "resolved": [{"work_id": work, "reason": "Full reading resolved the earlier uncertain relevance."} for work in resolved],
                "impact": "Bound the exact statement search used in native review.", "gaps": []})
        search = case.store.snapshot()["records"]["literature_search"][identifier]
        searches.append({"formulation": formulation, "search_digest": digest(search)})
    finding["minimum"] = {"schema_version": 1, "statement": claim["statement"], "neighbours": list(neighbours),
                          "searches": searches, "reading_digests": readings,
                          "settling_work_id": found[0] if finding["outcome"] == "already_solved" else None}
    if duplicate_of is not None:
        finding["minimum"]["duplicate_resolutions"] = [
            {"search_digest": row["search_digest"], "work_id": found[0], "canonical_work_id": found[1]}
            for row in searches]
    finding["evidence"] = list(dict.fromkeys(finding["evidence"] + [row["search_digest"] for row in searches] + readings))
    return value
