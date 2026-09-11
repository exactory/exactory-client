"""Read-only export of the current unread abstracts as coordinator batches.

The files name the exact version, its saved abstract text and link, and the
notes shape a reader returns. Nothing here reads a paper or records a reading:
a coordinator dispatches readers over the files and submits their notes with
read-batch, which validates every item against the saved bytes.
"""

import json
from pathlib import Path

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evaluation import Evaluation
from .gates import gate_state
from .literature import foundation_state
from .reading import NOTE_FIELDS
from .source_links import span_locator


NOTES_SHAPE = {"id": "batch-001", "depth": "abstract",
               "items": [{"version_id": "EXACT_VERSION_ID", "note": "Where the abstract was inspected.",
                          "notes": {name: {"text": "What the abstract establishes about " + name + ".", "status": "present"}
                                    for name in NOTE_FIELDS}}]}


def _entry(records, evaluation, version_id):
    work = records["work"][version_id]
    abstract = next((a for a in work["abstracts"] if a["completeness"] == "complete"), None)
    if abstract is None:
        return None
    content = evaluation.text(abstract["artifact"])
    return {"version_id": work["id"], "title": work.get("title"), "authors": work.get("authors", []),
            "published": work.get("publication_date"), "categories": work.get("categories", []), "text": content,
            "link": {"version_id": work["id"], "source_id": abstract["source_id"], "artifact": abstract["artifact"],
                     "locator": span_locator(content, 0, len(content))},
            "extraction": None}


def unread_abstracts(records, evaluation, profile):
    """Current unread abstracts: cohort members first, then Tier 3 references."""
    versions = []
    cohort = gate_state(records, evaluation, "cohort", profile=profile)
    for item in cohort.get("inventory", []):
        if item["reading_id"] is None and item["artifact"] is not None:
            versions.append(item["version_id"])
    if records.get("literature_scope", {}).get(profile):
        for item in foundation_state(records, evaluation, profile)["obligations"]:
            if item["code"] == "abstract_reading_missing" and item["version_id"] not in versions:
                versions.append(item["version_id"])
    return versions


def export_batches(store, *, depth="abstract", size=60, destination, profile=None):
    if depth != "abstract":
        raise ResearchError("invalid_input", "Batches export abstract readings")
    if type(size) is not int or not 1 <= size <= 100:
        raise ResearchError("invalid_input", "Batch size must be between 1 and 100")
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ResearchError("review_destination_exists", "Export batches into a new directory")
    snapshot = store.snapshot()
    records = snapshot["records"]
    config = records.get("configuration", {}).get("research")
    if config is None:
        raise ResearchError("migration_required", "Initialize or adopt the research contract before exporting batches")
    profile = profile or config["profile"]
    evaluation = Evaluation(records, ArtifactStore(store.root))
    entries = [e for e in (_entry(records, evaluation, v) for v in unread_abstracts(records, evaluation, profile)) if e]
    destination.mkdir(parents=True, mode=0o700)
    files = []
    for number, start in enumerate(range(0, len(entries), size), 1):
        name = "batch-%03d.json" % number
        content = {"id": "batch-%03d" % number, "depth": depth, "revision": snapshot["revision"],
                   "items": entries[start:start + size]}
        (destination / name).write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        files.append(str(destination / name))
    (destination / "README.json").write_text(json.dumps({"notes_shape": NOTES_SHAPE,
        "submit": "exactory-research read-batch --file NOTES.json --expected-revision REVISION --request-id ID"},
        indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"revision": snapshot["revision"], "entries": len(entries), "files": files, "mechanical_only": True}
