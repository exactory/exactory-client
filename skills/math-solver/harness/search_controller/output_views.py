"""Generated final/ and intermediate/ views of one controller root.

The workspace root is the controller root's parent, and <rel> is the controller root's path relative to it. This
module is the only owner of final/math/<rel>/ (the results that the controller currently relies on),
final/history/math/<rel>/ (earlier results that a later record superseded or that lost their standing) and
intermediate/math/<rel>/ (every other output). STATE_GROUPS and OPERATION_GROUPS classify every replayed state
collection and every inner operation kind by the view group that shows it.

A render reads the event document, the controller's content stores and the node workspaces, and writes only these
folders and its marker and lock in .search/. It runs once per command, after the command's last commit and outside
every controller lock. It skips when the marker already shows the document's last event under the installed
plugin version; otherwise it builds the whole model, writes the files whose bytes differ, and removes the owned files
that the model no longer produces. A failed render never changes the command's result: it prints one warning, and
search status and search next report output_views_stale.
"""

import errno
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import time

from . import schema as s
from .errors import SearchError
from .evidence import manifest_closure
from .model import replay
from .render import replace_bytes
from .reviewer import PREFIX, common_root, reviews
from .runtime import read_runtime_identity
from .storage import canonical_bytes, safe_path, _strict_json


MARKER_NAME = "output-views.json"
LOCK_NAME = "output-views.lock"
LOCK_TIMEOUT_SECONDS = 30.0
COPY_LIMIT_BYTES = 1 << 20
STALE_VIEWS_ACTION = "exactory-math search render"
_MARKER_SCHEMA = "exactory-controller-output-views-v1"
_INDEX_SCHEMA = "exactory-controller-output-index-v1"
_NODE_INDEX_SCHEMA = "exactory-controller-node-index-v1"
# The folder of each placement status: final/ for current results, final/history/ for superseded and historical ones,
# intermediate/ for invalidated results, records that were never selected, and outputs that are intermediate by kind.
_TREES = {"current": "final/math/", "superseded": "final/history/math/", "historical": "final/history/math/",
          "invalidated": "intermediate/math/", "not_selected": "intermediate/math/", "intermediate": "intermediate/math/"}

# Every replayed state collection (control.* and service.* name the members of those two records) and the view group
# that shows it. None marks scheduling, budget and recovery bookkeeping that no view shows.
STATE_GROUPS = {
    "schema_version": None, "objective_id": "objective", "contract": "objective", "contract_digest": "objective",
    "revision": None, "obligations": "objective", "routes": "objective", "proof_status": "objective",
    "execution_status": "objective", "publication_status": "objective", "proposals": "admissions",
    "reviews": "admissions", "nodes": "nodes", "accounts": None, "checkpoints": "checkpoints",
    "acceptances": "results", "runs": "runs", "totals": None, "next_ids": None, "requests": None,
    "control.active_node_id": None, "control.closure": "closure", "control.deferred_node_ids": None,
    "control.focus": None, "control.focus_record": None, "control.main_external_block": None,
    "control.node_facts": "nodes", "control.nonprogress_replans": None, "control.obligation_orders": None,
    "control.pause_reason": None, "control.pending_moves": None, "control.progress_fingerprints": None,
    "control.resume_ids": None, "control.resume_record": None, "control.retreat_node_id": None,
    "control.retreats": "retreats", "control.selected_routes": None, "control.side_instruction_ids": None,
    "control.side_interval": None, "control.state_error": None, "control.stop_count": None,
    "control.stop_decision": None, "control.stop_deliveries": None, "control.strategy_refresh": "reassessments",
    "control.summary_issued": None,
    "service.effects": None, "service.imports": "imports", "service.import_versions": "imports",
    "service.adoption_allowances": "imports", "service.journal_receipts": None, "service.moves": None,
    "service.legacy_intents": None, "service.native_intents": None, "service.native_receipts": "nodes",
    "service.discovery_intents": None, "service.computation_amendments": "amendments",
    "service.run_interpretations": "runs", "service.foundation_amendments": "amendments",
    "service.foundation_selection": "amendments", "service.journal_observations": None,
    "service.review_workspace": None, "service.probe_origins": None, "service.import_identities": None,
}

# Every inner operation kind of model.EVENT_HANDLERS and the view group that shows the record it writes. None marks
# kinds that write only bookkeeping: intents, reservations, control actions, routing and provenance.
OPERATION_GROUPS = {
    "proposal_recorded": "admissions", "review_recorded": "admissions", "proposal_admitted": "admissions",
    "strategies_reassessed": "reassessments", "result_accepted": "results", "evidence_invalidated": "results",
    "objective_completed": "closure", "legacy_imported": "imports", "legacy_import_version_recorded": "imports",
    "adoption_allowance_recorded": "imports", "checkpoint_recorded": "checkpoints", "replan_recorded": "replans",
    "node_retreated": "retreats", "computation_amended": "amendments", "foundation_amended": "amendments",
    "run_reserved": "runs", "run_launched": "runs", "run_finished": "runs", "run_interpreted": "runs",
    "journal_acknowledged": "nodes", "native_acknowledged": "nodes", "node_facts_recorded": "nodes",
    "journal_intended": None, "native_intended": None, "move_reserved": None, "control_recorded": None,
    "discovery_recorded": None, "probe_origins_recorded": None, "review_workspace_recorded": None,
    "strategy_policy_enabled": None, "service_operation": None,
}


def encode_view(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def iter_operations(document):
    """Every inner operation with its event sequence, including archived events recorded without a service envelope."""
    for event in document["events"]:
        if event["kind"] == "service_operation":
            for operation in event["payload"]["operations"]:
                yield event["sequence"], operation["kind"], operation["payload"]
        else:
            yield event["sequence"], event["kind"], event["payload"]


def list_state_paths(state):
    paths = {key for key in state if key not in {"control", "service"}}
    return paths | {"control." + key for key in state["control"]} | {"service." + key for key in state["service"]}


class Views:
    """The files of one render, keyed by their path relative to the workspace root."""

    def __init__(self, root, store, document, state):
        self.root, self.store, self.document, self.state = Path(root), store, document, state
        self.workspace = self.root.parent
        self.rel = self.root.relative_to(self.workspace).as_posix()
        self.files = {}
        self.adoptions = []

    def find_base(self, status):
        return _TREES[status] + self.rel

    def add_record(self, status, relative, value, kind, identity):
        path = self.find_base(status) + "/" + relative
        self.files[path] = (encode_view(value), {"kind": kind, "id": identity, "status": status})
        return path

    def copy_artifact(self, status, relative, digest, identity):
        """Copy a stored artifact of at most COPY_LIMIT_BYTES to relative; list a larger, missing or corrupt one, or one
        whose path another artifact of the same view already holds, by digest with the reason."""
        path = self.find_base(status) + "/" + relative
        if path in self.files:
            if hashlib.sha256(self.files[path][0]).hexdigest() == digest:
                return {"digest": digest, "view": path}
            return {"digest": digest, "view": None, "reason": "another artifact holds this view path"}
        try:
            s.digest_string(digest)
            size = safe_path(self.store.root, "artifacts/" + digest).stat().st_size
        except (OSError, SearchError) as error:
            return {"digest": digest, "view": None, "reason": "missing: " + describe_error(error)}
        if size > COPY_LIMIT_BYTES:
            return {"digest": digest, "size": size, "view": None, "reason": "larger than the 1 MiB copy limit"}
        try:
            raw = self.store.get_artifact(digest)
        except SearchError as error:
            return {"digest": digest, "size": size, "view": None, "reason": describe_error(error)}
        self.files[path] = (raw, {"kind": "artifact", "id": identity, "status": status})
        return {"digest": digest, "size": size, "view": path}

    def read_blob(self, digest):
        try:
            return self.store.get_blob(digest), None
        except SearchError as error:
            return None, describe_error(error)

    def adopt(self, value, kind, identity, status, path):
        """Record every observed native review that the record relies on, with the assignment of its attestation."""
        for review in reviews(value):
            attestation = review["reviewer"]["attestation_id"]
            if attestation.startswith(PREFIX):
                entry = {"review_id": s.digest(review), "assignment_id": attestation[len(PREFIX):],
                         "adopting_record": {"kind": kind, "id": identity, "status": status, "path": path}}
                if entry not in self.adoptions:
                    self.adoptions.append(entry)


def build_objective(views):
    state = views.state
    value = {key: state[key] for key in ("objective_id", "contract", "contract_digest", "proof_status",
                                         "execution_status", "publication_status", "obligations", "routes")}
    views.add_record("current", "objective.json", value, "objective", state["objective_id"])


def build_admissions(views):
    """Admitted proposals in final/, every other proposal in intermediate/, each with its recorded reviews."""
    state = views.state
    for identity, proposal in state["proposals"].items():
        admitted = proposal["status"] == "admitted"
        status = "current" if admitted else "not_selected"
        node = next((node["id"] for node in state["nodes"].values() if node["proposal_id"] == identity), None)
        value = {"proposal": proposal, "node_id": node,
                 "reviews": [review for review in state["reviews"].values() if review["proposal_id"] == identity]}
        kind = "admission" if admitted else "proposal"
        path = views.add_record(status, ("admissions/" if admitted else "proposals/") + identity + ".json",
                                value, kind, identity)
        for review in value["reviews"]:
            views.adopt(review["record"], kind + "_review", review["id"], status, path)


def build_reassessments(views):
    records = views.state["control"]["strategy_refresh"]["assessments"]
    for position, record in enumerate(records):
        status = "current" if position == len(records) - 1 else "superseded"
        path = views.add_record(status, "reassessments/" + record["id"] + ".json", record, "strategy_assessment",
                                record["id"])
        views.adopt(record["review"], "strategy_assessment", record["id"], status, path)


def list_evidence(views, status, folder, digests, identity):
    """The evidence manifests of a result with their artifacts, copied under folder/artifacts/<manifest path>."""
    try:
        manifests = manifest_closure(digests, views.store)
    except SearchError as error:
        return [{"digests": digests, "error": describe_error(error)}]
    return [{"manifest_digest": digest, "manifest": manifest,
             "artifacts": [dict(views.copy_artifact(status, folder + "/artifacts/" + item["path"], item["digest"], identity),
                                path=item["path"], role=item["role"]) for item in manifest["artifacts"]]}
            for digest, manifest in manifests.items()]


def build_results(views):
    """Accepted results in final/results/, invalidated ones in intermediate/invalidated/, with their artifacts."""
    state = views.state
    for identity, acceptance in state["acceptances"].items():
        status = "current" if acceptance["status"] == "accepted" else "invalidated"
        folder = ("results/" if status == "current" else "invalidated/") + identity
        value = {"acceptance": acceptance, "checkpoint": state["checkpoints"].get(acceptance["checkpoint_id"]),
                 "evidence": list_evidence(views, status, folder, acceptance["audit"]["evidence_digests"], identity)}
        path = views.add_record(status, folder + "/acceptance.json", value, "acceptance", identity)
        views.adopt(value, "acceptance", identity, status, path)


def build_closure(views):
    """The current closure in final/closure/; a closure that later evidence reopened in final/history/; a closure
    whose accepted support was invalidated in intermediate/invalidated/. Each lists its deliverables."""
    state = views.state
    closures = [(sequence, payload["closure"]) for sequence, kind, payload in iter_operations(views.document)
                if kind == "objective_completed"]
    for position, (sequence, closure) in enumerate(closures):
        identity = "closure-r{:06d}".format(sequence)
        if any(state["acceptances"].get(aid, {}).get("status") == "invalidated"
               for aid in closure["subject"]["acceptance_ids"]):
            status, folder = "invalidated", "invalidated/" + identity
        elif position == len(closures) - 1 and state["proof_status"] in {"proved", "disproved"}:
            status, folder = "current", "closure"
        else:
            status, folder = "historical", "closure/" + identity
        deliverables = [dict(views.copy_artifact(status, "{}/deliverables/{:02d}".format(folder, number), item["digest"],
                                                 identity), requirement=item["requirement"])
                        for number, item in enumerate(closure["subject"]["deliverables"], start=1)]
        value = {"recorded_revision": sequence, "closure": closure, "deliverables": deliverables}
        path = views.add_record(status, folder + "/closure.json", value, "closure", identity)
        views.adopt(closure, "closure", identity, status, path)


def build_imports(views):
    service = views.state["service"]
    for identity, record in service["imports"].items():
        value = {"import": record,
                 "versions": [item for item in service["import_versions"] if item["import_id"] == identity],
                 "allowances": [item for item in service["adoption_allowances"] if item["import_id"] == identity]}
        path = views.add_record("current", "imports/" + identity + ".json", value, "import", identity)
        views.adopt(value, "import", identity, "current", path)


def build_checkpoints(views):
    for identity, checkpoint in views.state["checkpoints"].items():
        views.add_record("intermediate", "checkpoints/" + identity + ".json", checkpoint, "checkpoint", identity)


def build_replans(views):
    for sequence, kind, payload in iter_operations(views.document):
        if kind == "replan_recorded":
            identity = "replan-r{:06d}".format(sequence)
            views.add_record("intermediate", "replans/" + identity + ".json",
                             {"recorded_revision": sequence, "replan": payload}, "replan", identity)


def build_retreats(views):
    for number, retreat in enumerate(views.state["control"]["retreats"], start=1):
        identity = "retreat-{:06d}".format(number)
        views.add_record("intermediate", "retreats/" + identity + ".json", retreat, "retreat", identity)


def build_amendments(views):
    service = views.state["service"]
    for node_id, payload in service["computation_amendments"].items():
        identity = "computation-" + node_id
        path = views.add_record("intermediate", "amendments/" + identity + ".json", payload, "amendment", identity)
        views.adopt(payload, "amendment", identity, "intermediate", path)
    for digest, payload in service["foundation_amendments"].items():
        identity = "foundation-" + digest
        value = dict(payload, selected=service["foundation_selection"].get(payload["subject"]["node_id"]) == digest)
        path = views.add_record("intermediate", "amendments/" + identity + ".json", value, "amendment", identity)
        views.adopt(payload, "amendment", identity, "intermediate", path)


def build_runs(views):
    """Each run's record, interpretation, and stdout, stderr and outputs copied up to COPY_LIMIT_BYTES each."""
    for identity, run in views.state["runs"].items():
        folder = "runs/" + identity
        spec, spec_error = views.read_blob(run["spec_digest"])
        result, result_error = views.read_blob(run["result_digest"]) if run["result_digest"] else (None, None)
        streams = []
        for number, command in enumerate(result["commands"] if result else []):
            for stream in ("stdout", "stderr"):
                streams.append(dict(views.copy_artifact("intermediate", "{}/{}.{}".format(folder, number, stream),
                                                        command[stream + "_digest"], identity),
                                    command=number, stream=stream))
        outputs = [dict(views.copy_artifact("intermediate", folder + "/output/" + item["path"], item["digest"], identity),
                        path=item["path"]) for item in run.get("outputs") or []]
        value = {"run": run, "spec": spec, "result": result, "streams": streams, "outputs": outputs,
                 "errors": [error for error in (spec_error, result_error) if error]}
        path = views.add_record("intermediate", folder + "/run.json", value, "run", identity)
        views.adopt(spec, "run", identity, "intermediate", path)
        interpretation = views.state["service"]["run_interpretations"].get(identity)
        if interpretation is not None:
            views.add_record("intermediate", folder + "/interpretation.json", interpretation, "run_interpretation",
                             identity)


def describe_file(views, path):
    raw = path.read_bytes()
    return {"path": path.relative_to(views.workspace).as_posix(), "digest": hashlib.sha256(raw).hexdigest(),
            "size": len(raw)}, raw


def list_files(views, directory, excluded=()):
    found = []
    for parent, folders, names in os.walk(str(directory)) if is_real_directory(directory) else []:
        folders.sort()
        for name in sorted(names):
            path = Path(parent) / name
            if is_real_file(path) and path.relative_to(directory).as_posix() not in excluded:
                found.append(describe_file(views, path)[0])
    return found


def is_real_file(path):
    return path.is_file() and not path.is_symlink()


def is_real_directory(path):
    return path.is_dir() and not path.is_symlink()


def build_nodes(views):
    """One index per node of its native outputs by path and digest, read from its workspace without writing there."""
    state = views.state
    for identity, node in state["nodes"].items():
        workspace = views.root / node["attack_slug"]
        value = {"schema": _NODE_INDEX_SCHEMA, "node": node, "facts": state["control"]["node_facts"].get(identity),
                 "native_commands": [receipt for receipt in state["service"]["native_receipts"].values()
                                     if receipt["node_id"] == identity],
                 "workspace": workspace.relative_to(views.workspace).as_posix(), "workspace_present": False,
                 "journal": None, "deterministic": [], "units": [], "finish": None}
        if is_real_directory(workspace):
            value["workspace_present"] = True
            journal = workspace / "journal.jsonl"
            if is_real_file(journal):
                listing, raw = describe_file(views, journal)
                listing["moves"] = [describe_move(line) for line in raw.splitlines() if line.strip()]
                value["journal"] = listing
            deterministic = workspace / "deterministic"
            steps = sorted(deterministic.iterdir()) if is_real_directory(deterministic) else []
            for step in filter(is_real_directory, steps):
                files = [describe_file(views, step / name)[0] for name in ("result.json", "inspection.log")
                         if is_real_file(step / name)]
                if files:
                    value["deterministic"].append({"step": step.name, "files": files})
            value["units"] = list_files(views, workspace / "units", excluded={"FINISHED.json"})
            if is_real_file(workspace / "HANDOFF.md"):
                value["units"].append(describe_file(views, workspace / "HANDOFF.md")[0])
            if is_real_file(workspace / "units/FINISHED.json"):
                value["finish"] = describe_file(views, workspace / "units/FINISHED.json")[0]
        views.add_record("intermediate", "nodes/" + node["attack_slug"] + "/index.json", value, "node_index", identity)


def describe_move(line):
    listing = {"digest": hashlib.sha256(line).hexdigest()}
    try:
        move = json.loads(line)
    except ValueError as error:
        return dict(listing, error="not JSON: " + str(error))
    if not isinstance(move, dict):
        return dict(listing, error="not a JSON object")
    return dict(listing, **{key: move.get(key) for key in ("move", "pass", "strategy", "entry", "closes")})


VIEW_GROUPS = (("objective", build_objective), ("admissions", build_admissions),
               ("reassessments", build_reassessments), ("results", build_results), ("closure", build_closure),
               ("imports", build_imports), ("checkpoints", build_checkpoints), ("replans", build_replans),
               ("retreats", build_retreats), ("amendments", build_amendments), ("runs", build_runs),
               ("nodes", build_nodes))


def build_views(root, store, document, state):
    """The whole model: every owned file except the two indexes, which describe the files on disk as well."""
    unregistered = sorted(list_state_paths(state) - set(STATE_GROUPS))
    s.require(not unregistered, "Controller state has collections without a view placement: " + ", ".join(unregistered),
              "output_views_unregistered")
    views = Views(root, store, document, state)
    s.require(views.rel.split("/")[0] not in {"final", "intermediate"},
              "A controller root cannot sit where its views go", "unsafe_path")
    for _, build in VIEW_GROUPS:
        build(views)
    return views


def build_index(views, folder, unowned):
    """The index of final/math/<rel>/ (with final/history/math/<rel>/ and the adoptions) or intermediate/math/<rel>/."""
    bases = ("final/math/", "final/history/math/") if folder == "final" else ("intermediate/math/",)
    prefixes = tuple(base + views.rel + "/" for base in bases)
    value = {"schema": _INDEX_SCHEMA, "folder": folder, "controller_root": views.rel,
             "objective_id": views.state["objective_id"], "paths": "relative to the workspace root",
             "files": [dict(entry, path=path) for path, (_, entry) in sorted(views.files.items())
                       if path.startswith(prefixes)],
             "unowned": sorted(path for path in unowned if path.startswith(prefixes))}
    if folder == "final":
        value["adoptions"] = views.adoptions
    return value


def read_marker(store):
    """The marker of the last render, or an empty one when it is missing or unreadable (ownership starts over)."""
    path = safe_path(store.root, MARKER_NAME)
    try:
        marker = _strict_json(path.read_bytes(), "corrupt_state")
        if (isinstance(marker, dict) and marker.get("schema") == _MARKER_SCHEMA
                and isinstance(marker.get("files"), dict)):
            return marker
    except (OSError, SearchError):
        pass
    return {"schema": _MARKER_SCHEMA, "revision": 0, "event_digest": None, "plugin_version": None, "files": {},
            "failure": None}


def write_marker(store, marker):
    replace_bytes(safe_path(store.root, MARKER_NAME), canonical_bytes(marker))


def check_view_path(workspace, relative):
    """The path below the workspace root, refusing symlinks on the way."""
    path = safe_path(workspace, relative)
    parts = Path(relative).parts
    for depth in range(1, len(parts) + 1):
        s.require(not workspace.joinpath(*parts[:depth]).is_symlink(), "A view path cannot pass through a symlink",
                  "unsafe_path")
    return path


def is_unchanged(path, owned, digest):
    if owned is None or owned.get("digest") != digest:
        return False
    try:
        info = path.stat()
    except FileNotFoundError:
        return False
    return (info.st_size, info.st_mtime_ns) == (owned["size"], owned["mtime_ns"])


def describe_stat(path, digest):
    info = path.stat()
    return {"digest": digest, "size": info.st_size, "mtime_ns": info.st_mtime_ns}


def remove_view(workspace, relative):
    """Remove an owned file, then each directory below its tree (final/math, final/history/math, intermediate/math)
    that it leaves empty."""
    path = check_view_path(workspace, relative)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    tree = next(tree for tree in sorted(set(_TREES.values()), key=len, reverse=True) if relative.startswith(tree))
    parent = Path(relative).parent
    while len(parent.as_posix()) > len(tree.rstrip("/")):
        try:
            (workspace / parent).rmdir()
        except OSError as error:
            if error.errno in {errno.ENOTEMPTY, errno.EEXIST, errno.ENOENT}:
                return
            raise
        parent = parent.parent


def list_unowned(workspace, bases, known):
    unowned = []
    for base in bases:
        directory = workspace / base
        if directory.is_symlink() or not directory.is_dir():
            continue
        for parent, folders, names in os.walk(str(directory)):
            for name in names + [folder for folder in folders if (Path(parent) / folder).is_symlink()]:
                relative = (Path(parent) / name).relative_to(workspace).as_posix()
                if relative not in known:
                    unowned.append(relative)
    return unowned


def write_views(views, marker):
    """Write the model and return the owned files with their stat. A file in the way that the renderer does not own
    and whose bytes differ stops the render."""
    workspace = views.workspace
    # Only paths in the three trees are ever owned, whatever an edited marker lists.
    owned = {relative: item for relative, item in marker["files"].items() if relative.startswith(tuple(_TREES.values()))}
    bases = [views.find_base(status) for status in ("current", "superseded", "intermediate")]
    indexes = {"final": bases[0] + "/index.json", "intermediate": bases[2] + "/index.json"}
    unowned = list_unowned(workspace, bases, set(views.files) | set(owned) | set(indexes.values()))
    for folder, path in indexes.items():
        views.files[path] = (encode_view(build_index(views, folder, unowned)),
                             {"kind": "index", "id": folder, "status": "current"})
    files, pending = {}, {}
    for relative, (data, _) in views.files.items():
        digest = hashlib.sha256(data).hexdigest()
        path = check_view_path(workspace, relative)
        if is_unchanged(path, owned.get(relative), digest):
            files[relative] = owned[relative]
        elif is_real_file(path) and path.read_bytes() == data:
            files[relative] = describe_stat(path, digest)
        elif relative in owned or not (path.exists() or path.is_symlink()):
            pending[relative] = (path, data, digest)
        else:
            raise SearchError("output_view_conflict", "A file that the harness did not write is where a view goes; "
                              "move it away, then run " + STALE_VIEWS_ACTION, {"path": str(path)})
    claimed = set(pending) - set(owned)
    if claimed:
        # Record the ownership of every new file before writing it, so that an interrupted render resumes on it.
        write_marker(views.store, dict(marker, files=dict(owned, **dict.fromkeys(claimed))))
    for relative, (path, data, digest) in pending.items():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            replace_bytes(path, data)
        except OSError as error:
            raise SearchError("output_view_unwritable", "A view could not be written",
                              {"path": str(path), "reason": str(error)}) from error
        files[relative] = describe_stat(path, digest)
    for relative in sorted(set(owned) - set(views.files)):
        remove_view(workspace, relative)
    return files


def hold_render_lock(store):
    """An exclusive lock that serializes the renders of every process on this controller root."""
    store._check_root()
    path = safe_path(store.root, LOCK_NAME)
    descriptor = os.open(str(path), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return descriptor
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EAGAIN) or time.monotonic() >= deadline:
                os.close(descriptor)
                raise SearchError("lock_timeout", "The output views lock is held by another render") from error
            time.sleep(0.05)


def render_views(root, store, replayed=None):
    """Render the views when the marker does not show the document's last event; return the replayed state, or None
    when the views were already current. replayed is a (document, state) pair the caller already holds; it is used
    when no later event was committed since."""
    descriptor = hold_render_lock(store)
    try:
        marker = read_marker(store)
        try:
            document = store.read()
            events = document["events"]
            identity = {"revision": len(events), "event_digest": s.digest(events[-1]) if events else None,
                        "plugin_version": read_runtime_identity()["plugin_version"]}
            if all(marker.get(key) == value for key, value in identity.items()):
                return None
            if replayed is not None and replayed[0]["events"][-1:] == events[-1:] and len(replayed[0]["events"]) == len(events):
                state = replayed[1]
            else:
                state = replay(document)
            files = write_views(build_views(root, store, document, state), marker)
            write_marker(store, dict(identity, schema=_MARKER_SCHEMA, files=files, failure=None))
            return state
        except Exception as error:
            details = error.details if isinstance(error, SearchError) and isinstance(error.details, dict) else {}
            failure = {"path": details.get("path", str(Path(root).parent / "final/math" / Path(root).name)),
                       "error": describe_error(error)}
            try:
                write_marker(store, dict(read_marker(store), failure=failure))
            except (OSError, SearchError):
                pass
            raise
    finally:
        os.close(descriptor)


def describe_error(error):
    if isinstance(error, SearchError):
        return error.code + ": " + error.message
    return type(error).__name__ + ": " + " ".join(str(error).split())


def find_research_renderer():
    """The research store renderer of research_harness.output_views, when the installed research harness has one."""
    try:
        module = importlib.import_module("research_harness.output_views")
    except ModuleNotFoundError as error:
        if error.name != "research_harness.output_views":
            raise
        return None
    return getattr(module, "render_workspace_views", None)


def render_after_command(controller, replayed=None, *, research=True):
    """Render after a command's last commit, outside every lock: the controller views, then (for controller commands)
    the research views of the common review store. A failure prints one warning line and changes nothing else."""
    state = None
    try:
        state = render_views(controller.root, controller.store, replayed)
    except Exception as error:
        warn_unrendered(error)
    if not research:
        return
    try:
        render = find_research_renderer()
        if render is not None:
            if state is None:
                state = replayed[1] if replayed is not None else replay(controller.store.read())
            render(common_root(controller.root, state))
    except Exception as error:
        warn_unrendered(error)


def warn_unrendered(error):
    print("Output views were not rendered (" + describe_error(error) + "); the command's result stands. Run "
          + STALE_VIEWS_ACTION + " after fixing the cause.", file=sys.stderr)


def find_stale_views(controller, state):
    """The advisory output_views_stale while the views do not show the controller's current revision. It never gates
    work and never replaces the next scientific action."""
    marker = read_marker(controller.store)
    plugin_version = read_runtime_identity()["plugin_version"]
    if marker["revision"] == state["revision"] and marker["plugin_version"] == plugin_version:
        return []
    failure = marker.get("failure") or {}
    return [{"kind": "output_views_stale", "action": STALE_VIEWS_ACTION,
             "path": failure.get("path", str(controller.root.parent / "final/math" / controller.root.name)),
             "error": failure.get("error", "The views show revision {} of {}".format(marker["revision"],
                                                                                    state["revision"])),
             "rendered_revision": marker["revision"], "revision": state["revision"]}]
