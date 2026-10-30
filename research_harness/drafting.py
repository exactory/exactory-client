"""Draft metadata, marker obligations, and the study's draft layout."""

from .errors import ResearchError
from .graph import obligation
from .workspace import checked_parent, find_entry_kind, parse_workspace_marker, read_file, strict_json


_MARKER = ".exactory/draft.json"
_METADATA = ("title", "category")
# The explanation of each state that the draft-marker obligation reports, with its one next action. The record
# messages are keyed by the kind of entry at the marker path.
_OBJECTIVE_COMPLETE_MSG = ("No draft is recorded. Record it from the objective's title and category "
                           "with exactory-lab state set --stage write.")
_OBJECTIVE_COMPLETE_STUDY_DONE_MSG = ("No draft is recorded and the study is done. "
                                      "Create it with exactory-draft init and the objective's title and category.")
_RECORD_MARKER_MSGS = {
    "absent": ("The draft record exists, but .exactory/draft.json is missing. "
               "Write it with exactory-research export."),
    "regular": ("The draft record exists, but .exactory/draft.json differs from it. "
                "Write the file again with exactory-research export."),
    "link": ("The draft record exists, but .exactory/draft.json is a symbolic link. "
             "Run exactory-research export to replace it."),
    "other": ("The draft record exists, but .exactory/draft.json is not a file. "
              "Move it to an unused name in .exactory/, then export.")}
_UNRECORDED_MARKER_MSGS = {
    "supported": ("No draft is recorded, but .exactory/draft.json exists. "
                  "Adopt the file with exactory-research adopt, naming it in files."),
    "regular": ("No draft is recorded and adopt refuses .exactory/draft.json. "
                "Move it to an unused name in .exactory/, then run status."),
    "link": ("No draft is recorded and .exactory/draft.json is a link. "
             "Move it to an unused name in .exactory/, then run status again."),
    "other": ("No draft is recorded and .exactory/draft.json is not a file. "
              "Move it to an unused name in .exactory/, then run status.")}


def _resolve_draft_metadata(records):
    target = records.get("configuration", {}).get("research", {}).get("target") or {}
    return {key: target[key] for key in _METADATA
            if isinstance(target.get(key), str) and target[key].strip()}


def prepare_draft(records, root, study):
    if study["stage"] != "write" or "draft" in records.get("workspace", {}):
        return []
    marker = root / _MARKER
    # Legacy marker bytes require explicit adoption, never implicit replacement.
    if marker.exists() or marker.is_symlink():
        return []
    metadata = _resolve_draft_metadata(records)
    if len(metadata) != len(_METADATA):
        return []
    state = dict(metadata, version=2, created=study["updated"],
                 research={"store": ".exactory/research.sqlite3", "profile": "research"})
    return [("workspace", "draft", state)]


def _read_marker(root, parse):
    """The regular marker as `parse` reads it, or None when it vanished, changed kind or holds refused content."""
    try:
        return parse(read_file(root, _MARKER))
    except ResearchError as error:
        if error.code not in ("artifact_missing", "invalid_json", "migration_required", "unsafe_path"):
            raise
        return None


def find_draft_obligations(records, root):
    workspace = records.get("workspace", {})
    study = workspace.get("study", {})
    if study.get("stage") != "write":
        return []
    kind = find_entry_kind(root, _MARKER)
    draft = workspace.get("draft")
    missing = []
    if draft is not None:
        if kind == "regular" and _read_marker(root, strict_json) == draft:
            return []
        # Export replaces a missing, different or linked marker; it cannot replace another kind of entry.
        message = _RECORD_MARKER_MSGS[kind]
    elif kind != "absent":
        # Adoption refuses a link, another kind of entry and unsupported content; moving such an entry aside keeps
        # its bytes and leaves the absent-marker state.
        supported = kind == "regular" and _read_marker(root, parse_workspace_marker) is not None
        message = _UNRECORDED_MARKER_MSGS["supported" if supported else kind]
    else:
        missing = [key for key in _METADATA if key not in _resolve_draft_metadata(records)]
        if missing:
            message = ("The objective has no " + " and no ".join(missing) + ". "
                       "Create the draft with exactory-draft init --title TITLE --category CATEGORY.")
        else:
            # A same-stage update of a completed study checks the stage's completion gate first.
            message = _OBJECTIVE_COMPLETE_STUDY_DONE_MSG if study.get("status") == "done" else _OBJECTIVE_COMPLETE_MSG
    return [obligation("draft_marker_missing", message, stage="write", path=_MARKER, missing_fields=missing)]


def export_draft(store):
    with checked_parent(store.root, "draft/paper.tex", create=True):
        pass
