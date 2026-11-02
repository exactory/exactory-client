"""Read-only export of the current unread abstracts as coordinator batches.

The files name the exact version, its saved abstract text and link, and the
notes shape a reader returns. Nothing here reads a paper or records a reading:
a coordinator dispatches readers over the files and submits their notes with
read-batch, which validates every item against the saved bytes.
"""

import json
from itertools import chain
from pathlib import Path

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evaluation import Evaluation
from .gates import gate_state
from .lineage import resolve_innovation_candidates, LINEAGE, LOOP_SOURCES, loop_readings
from .literature import DISPOSITIONS, foundation_state
from .operations import strings
from .principles import preparation_policy
from .reading import NOTE_FIELDS, selected_abstract
from .sampling import POSITIONS, SAMPLED
from .screening import SCREENED
from .source_links import span_locator
from .workspace import json_projection, write_projection


NOTES_SHAPE = {"id": "batch-001", "depth": "abstract",
               "items": [{"version_id": "EXACT_VERSION_ID", "note": "Where the abstract was inspected.",
                          "notes": {name: {"text": "What the abstract establishes about " + name + ".", "status": "present"}
                                    for name in NOTE_FIELDS}}]}


def _entry(records, evaluation, version_id):
    work = records["work"][version_id]
    abstract = selected_abstract(work)
    if abstract is None:
        return None
    content = evaluation.text(abstract["artifact"])
    return {"version_id": work["id"], "title": work.get("title"), "authors": work.get("authors", []),
            "published": work.get("publication_date"), "categories": work.get("categories", []), "text": content,
            "link": {"version_id": work["id"], "source_id": abstract["source_id"], "artifact": abstract["artifact"],
                     "locator": span_locator(content, 0, len(content))}}


def unread_abstracts(records, evaluation, profile, *, screen=False, loop=False, candidates=()):
    """Current unread (or unscreened) abstracts: cohort members, then Tier 3 references; under sampled-v1 the
    unread sampled members; under lineage-v1 with loop, the selected searches' found works without a loop reading
    plus the caller's candidates."""
    versions = []
    if loop:
        read = {r["version_id"] for r in loop_readings(records)}
        selections = records.get("search_selection", {})
        for key in sorted(selections):
            if not key.startswith(profile + ":"):
                continue
            search = records.get("literature_search", {}).get(selections[key]["search_id"])
            for version in (search or {}).get("found_work_ids", []):
                if version not in read and version not in versions and version in records.get("work", {}):
                    versions.append(version)
        for version in candidates:
            if version not in versions and version in records.get("work", {}):
                versions.append(version)
        return versions
    cohort = gate_state(records, evaluation, "cohort", profile=profile)
    counts = cohort.get("counts", {})
    wanted = {"screening_missing"} if screen else {"cohort_abstract_reading_missing", "screening_audit_reading_missing", "sample_reading_missing"}
    pending = {o.get("version_id") for o in cohort.get("obligations", []) if o["code"] in wanted}
    # Screened and sampled preparation each name the members they owe; every other policy reads the cohort whole.
    read_everything = not screen and not counts.get("screening") and not counts.get("sample")
    for item in cohort.get("inventory", []):
        if item["version_id"] in pending or (read_everything and item["reading_id"] is None):
            versions.append(item["version_id"])
    if records.get("literature_scope", {}).get(profile):
        for item in foundation_state(records, evaluation, profile)["obligations"]:
            if item["code"] in wanted | ({"abstract_reading_missing"} if not screen else set()) and item.get("version_id") and item["version_id"] not in versions:
                versions.append(item["version_id"])
    return versions


SCREEN_SHAPE = {"id": "screen-001", "screener": {"kind": "agent", "model": None},
                "items": [{"collection_id": "COLLECTION_ID_OR_NULL", "work_id": "FAMILY_ID", "version_id": "EXACT_VERSION_ID",
                           "disposition": "promote|doctrine|exclude|pending", "promotion_reasons": ["prior_art"],
                           "relevance": "none|weak|strong", "reason": "Why.", "conventions": [], "context": "Citation context for a reference."}]}


def _build_notes_shape(screen, loop, policy):
    """The item template a coordinator fills: the screening shape, or NOTES_SHAPE plus the key this export's readings carry."""
    if screen:
        return SCREEN_SHAPE
    if loop:
        extra = {"loop": {"purposes": ["direct"], "disposition": "|".join(DISPOSITIONS), "source": "|".join(LOOP_SOURCES)},
                 "innovation_candidate": True}
    elif policy == SAMPLED:
        extra = {"placement": {"position": "|".join(POSITIONS), "reason": "Why."}}
    else:
        return NOTES_SHAPE
    return dict(NOTES_SHAPE, items=[dict(NOTES_SHAPE["items"][0], **extra)])


def export_batches(store, *, depth="abstract", size=60, destination, profile=None, screen=False, loop=False, candidates=(),
                   max_input_bytes=262144, max_output_tokens=None, estimated_output_tokens_per_item=None):
    if depth != "abstract":
        raise ResearchError("invalid_input", "Batches export abstract readings")
    if type(size) is not int or not 1 <= size <= 100:
        raise ResearchError("invalid_input", "Batch size must be between 1 and 100")
    if type(max_input_bytes) is not int or max_input_bytes < 1:
        raise ResearchError("invalid_input", "Maximum input bytes must be a positive integer")
    if (max_output_tokens is None) != (estimated_output_tokens_per_item is None):
        raise ResearchError("invalid_input", "Output allowance and estimated tokens per item must be supplied together")
    item_limit = size
    if max_output_tokens is not None:
        if (type(max_output_tokens) is not int or max_output_tokens < 1
                or type(estimated_output_tokens_per_item) is not int or estimated_output_tokens_per_item < 1):
            raise ResearchError("invalid_input", "Output allowance and estimated tokens per item must be positive integers")
        item_limit = min(size, max_output_tokens // estimated_output_tokens_per_item)
        if item_limit < 1:
            raise ResearchError("batch_output_budget_too_small", "The output allowance is below one item's estimate",
                                {"max_output_tokens": max_output_tokens,
                                 "estimated_output_tokens_per_item": estimated_output_tokens_per_item})
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ResearchError("review_destination_exists", "Export batches into a new directory")
    snapshot = store.snapshot()
    records = snapshot["records"]
    config = records.get("configuration", {}).get("research")
    if config is None:
        raise ResearchError("migration_required", "Initialize or adopt the research contract before exporting batches")
    profile = profile or config["profile"]
    policy = preparation_policy(records)
    if screen and policy != SCREENED:
        raise ResearchError("policy_inapplicable", "Screening batches need the screened-v1 preparation policy")
    if loop and policy != LINEAGE:
        raise ResearchError("policy_inapplicable", "Loop batches need the lineage-v1 preparation policy")
    if policy == LINEAGE and not loop:
        raise ResearchError("policy_inapplicable", "Under lineage-v1 export the loop's abstracts with --loop")
    candidates = strings(list(candidates), "Candidates")
    evaluation = Evaluation(records, ArtifactStore(store.root))
    versions = unread_abstracts(records, evaluation, profile, screen=screen, loop=loop, candidates=candidates)
    selected = set(versions)
    skipped = [version for version in candidates if version not in selected]
    destination.mkdir(parents=True, mode=0o700)
    manifest = {"version": 1, "revision": snapshot["revision"], "complete": False,
                "counts": {"candidate": len(versions) + len(skipped), "exported": 0, "missing": 0, "skipped": len(skipped)},
                "files": [],
                "selection_coverage": {"profile": profile, "preparation_policy": policy, "screen": screen, "loop": loop,
                                       "requested_candidates": candidates, "version_ids": versions,
                                       "missing_version_ids": [], "skipped_candidate_ids": skipped},
                "capacity": {"max_items": size, "effective_max_items": item_limit, "max_input_bytes": max_input_bytes,
                             "max_output_tokens": max_output_tokens,
                             "estimated_output_tokens_per_item": estimated_output_tokens_per_item,
                             "output_capacity": "unknown" if max_output_tokens is None else "estimated"}}
    json_projection(destination, "manifest.json", manifest)
    files = []
    entries, encoded = [], b""
    prefix = "screen-%03d" if screen else "batch-%03d"
    number = 1
    # The sentinel publishes the final partial batch through the same atomic path.
    for version in chain(versions, (None,)):
        entry = None if version is None else _entry(records, evaluation, version)
        if entry is None and version is not None:
            manifest["counts"]["missing"] += 1
            manifest["selection_coverage"]["missing_version_ids"].append(version)
            json_projection(destination, "manifest.json", manifest)
            continue
        if entry is not None and screen:
            work = records["work"][version]
            entry["work_id"] = work["work_id"]
            entry["collection_id"] = next((m["collection_id"] for m in records.get("cohort_member", {}).values()
                                           if m["work_id"] == work["work_id"]), None)
        while True:
            if entry is not None:
                proposed = (json.dumps({"id": prefix % number, "depth": depth, "revision": snapshot["revision"],
                                        "screen": screen, "items": entries + [entry]},
                                       indent=2, ensure_ascii=False) + "\n").encode("utf-8")
                if len(proposed) <= max_input_bytes:
                    entries.append(entry)
                    encoded, proposed, entry = proposed, None, None
                    if len(entries) < item_limit:
                        break
                elif not entries:
                    parameters = {"size": 1, "max_input_bytes": len(proposed)}
                    if max_output_tokens is not None:
                        parameters.update(max_output_tokens=max_output_tokens,
                                          estimated_output_tokens_per_item=estimated_output_tokens_per_item)
                    raise ResearchError("batch_item_too_large", "The complete encoded singleton exceeds the input byte bound",
                                        {"version_id": version, "input_bytes": len(proposed),
                                         "max_input_bytes": max_input_bytes, "parameters": parameters})
                # A byte overflow publishes the preceding batch, then retries this entry with the next file's ID.
                proposed = None
            if entries:
                name = prefix % number + ".json"
                write_projection(destination, name, encoded)
                files.append(str(destination / name))
                manifest["files"].append({"path": name, "id": prefix % number, "entries": len(entries),
                                          "input_bytes": len(encoded), "estimated_output_tokens": None
                                          if estimated_output_tokens_per_item is None
                                          else len(entries) * estimated_output_tokens_per_item})
                manifest["counts"]["exported"] += len(entries)
                json_projection(destination, "manifest.json", manifest)
                entries, encoded = [], b""
                number += 1
            if entry is None:
                break
    readme = {"notes_shape": _build_notes_shape(screen, loop, policy),
              "submit": "exactory-research " + ("screen-batch" if screen else "read-batch") + " --file NOTES.json --expected-revision REVISION --request-id ID"}
    if loop:
        readme["innovation_candidate"] = ("Set innovation_candidate to true on a reading that names one of the "
                                          + str(resolve_innovation_candidates(records)) + " innovation candidates; leave the key out otherwise.")
    json_projection(destination, "README.json", readme)
    manifest["complete"] = True
    json_projection(destination, "manifest.json", manifest)
    return {"revision": snapshot["revision"], "entries": manifest["counts"]["exported"], "files": files,
            "manifest": str(destination / "manifest.json"), "mechanical_only": True}
