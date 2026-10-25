"""Draft metadata, marker obligations, and the study's draft layout."""

from .errors import ResearchError
from .graph import obligation
from .workspace import checked_parent, read_file, strict_json


_MARKER = ".exactory/draft.json"
_METADATA = ("title", "category")


def _metadata(records):
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
    metadata = _metadata(records)
    if len(metadata) != len(_METADATA):
        return []
    state = dict(metadata, version=2, created=study["updated"],
                 research={"store": ".exactory/research.sqlite3", "profile": "research"})
    return [("workspace", "draft", state)]


def draft_obligations(records, root):
    workspace = records.get("workspace", {})
    if workspace.get("study", {}).get("stage") != "write":
        return []
    draft = workspace.get("draft")
    if draft is not None:
        try:
            if strict_json(read_file(root, _MARKER)) == draft:
                return []
        except ResearchError as error:
            if error.code not in ("artifact_missing", "invalid_json", "unsafe_path"):
                raise
        message = "Export the recorded draft marker with exactory-research export."
        missing = []
    elif (root / _MARKER).exists() or (root / _MARKER).is_symlink():
        message = "Explicitly adopt the existing draft marker before using it as the study's draft."
        missing = []
    else:
        missing = [key for key in _METADATA if key not in _metadata(records)]
        message = ("Supply the missing objective " + " and ".join(missing) + " with exactory-draft init." if missing
                   else "Create the draft marker with exactory-lab state set --stage write.")
    return [obligation("draft_marker_missing", message, stage="write", path=_MARKER, missing_fields=missing)]


def export_draft(store):
    with checked_parent(store.root, "draft/paper.tex", create=True):
        pass
