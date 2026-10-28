"""Offline deposit archives of frozen executions, evidence and retained records."""

import hashlib
import json
import os
from pathlib import Path
import stat
from urllib.parse import quote
import uuid
import zipfile

from .artifacts import ArtifactStore, _filesystem_error, _relative_parts, validate_reference
from .errors import ResearchError
from .evaluation import Evaluation
from .publication import find_selected_bundle
from .scientific_json import is_json_media_type
from .workspace import checked_parent, read_file, strict_json


RECORD_KINDS = (
    "cycle", "cycle_plan", "development_scope", "strategy_account", "execution_admission", "execution_binding",
    "execution_prelaunch", "execution_claim", "execution", "execution_observation", "execution_outcome",
    "cycle_assessment", "checkpoint", "development_selection", "bundle_selection", "readiness_review",
    "manuscript_review", "manuscript_prediction", "contribution_analysis", "round_decision", "round_review",
    "source_deferral", "workspace_decision", "publication_bundle", "publication_selection", "local_artifact",
)
_SUFFIXES = {"application/json": ".json", "image/svg+xml": ".svg", "image/png": ".png",
             "application/pdf": ".pdf", "text/plain": ".txt"}


def _encode_json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _iter_stored_references(value):
    if isinstance(value, dict):
        if {"path", "sha256", "size", "media_type"} <= value.keys():
            # Scientific JSON can describe ordinary files without referring to stored objects.
            try:
                validate_reference(value)
            except ResearchError:
                pass
            else:
                yield value
        for child in value.values():
            yield from _iter_stored_references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_stored_references(child)


def _quote_component(value):
    return quote(value, safe="")


class _Archive:
    def __init__(self, archive, artifacts):
        self.archive = archive
        self.artifacts = artifacts
        self.index = {}
        self.members = {}
        self.directories = set()
        self.pending = []
        self.followed = set()

    def reserve_parents(self, name):
        parts = _relative_parts(name)
        parents = {"/".join(parts[:index]) for index in range(1, len(parts))}
        if parents.intersection(self.members):
            raise ResearchError("package_member_conflict", "Archive files must not replace directories", {"path": name})
        self.directories.update(parents)

    def write(self, name, data, *, supplement=False):
        self.reserve_parents(name)
        if name in self.directories:
            raise ResearchError("package_member_conflict", "Archive files must not replace directories", {"path": name})
        content_digest = hashlib.sha256(data).hexdigest()
        if name in self.members:
            if supplement or self.members[name] != content_digest:
                raise ResearchError("package_member_conflict", "Archive members must have distinct names",
                                    {"path": name})
            return
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        self.archive.writestr(info, data)
        self.members[name] = content_digest

    def write_directory(self, name):
        self.reserve_parents(name)
        if name in self.members:
            raise ResearchError("package_member_conflict", "Archive files must not replace directories", {"path": name})
        if name + "/" in self.members:
            return
        self.directories.add(name)
        info = zipfile.ZipInfo(name + "/", date_time=(1980, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = (0o40755 << 16) | 0x10
        self.archive.writestr(info, b"")
        self.members[name + "/"] = hashlib.sha256(b"").hexdigest()

    def write_artifact(self, ref, name=None):
        data = self.artifacts.read(ref)
        if ref["path"] not in self.index:
            self.index[ref["path"]] = name or "data/objects/" + ref["sha256"]
            self.write(self.index[ref["path"]], data)
        if name is not None:
            self.write(name, data)
        key = (ref["sha256"], ref["media_type"])
        if key not in self.followed:
            self.followed.add(key)
            self.pending.append((ref, data))
        return data

    def write_references(self, value):
        for ref in _iter_stored_references(value):
            self.write_artifact(ref)

    def follow(self):
        # Packet representations belong to their producer; only the common artifact boundary is used here.
        while self.pending:
            ref, data = self.pending.pop()
            if not is_json_media_type(ref["media_type"]):
                continue
            try:
                structured = strict_json(data)
            except ResearchError as error:
                if error.code != "invalid_json":
                    raise
                # Historical outputs retain their bytes even when their declared JSON is not strict JSON.
                continue
            self.write_references(structured)


def _select_bundle(records, selector):
    if selector is None:
        bundle = find_selected_bundle(records)
    else:
        bundles = records.get("publication_bundle", {})
        bundle = bundles.get(selector)
        if bundle is None:
            matching = [item for item in bundles.values() if item.get("digest") == selector]
            bundle = matching[0] if len(matching) == 1 else None
    if bundle is None:
        raise ResearchError("publication_bundle_missing", "Select a pinned manuscript bundle for the archive")
    return bundle


def _find_cited_executions(records, links):
    selected = {}
    for link in links:
        for evidence in link["evidence"]:
            if evidence.get("kind") != "result":
                continue
            identifier = evidence["execution_id"]
            record = records.get("execution", {}).get(identifier)
            execution = None if record is None else record["payload"]
            output = next((item for item in execution["outputs"] if item["id"] == evidence["output_id"]),
                          None) if execution else None
            if output is None or output["artifact"] != evidence["artifact"]:
                raise ResearchError("result_evidence_mismatch", "Claims must cite their recorded execution output",
                                    {"claim_id": link["claim_id"], "execution_id": identifier})
            selected[identifier] = execution
    return [selected[key] for key in sorted(selected)]


def _read_frozen_run(records, execution):
    identifier = execution["origin"]["admission_id"]
    admission = records.get("execution_admission", {}).get(identifier)
    binding = records.get("execution_binding", {}).get(identifier)
    if admission is None or binding is None:
        raise ResearchError("package_binding_missing", "Cited managed executions require their recorded frozen paths",
                            {"execution_id": execution["id"], "admission_id": identifier})
    command = admission["command"]
    pairs = [(binding["script"], command["program"])] + [(item["path"], item["artifact"])
                                                            for item in binding["inputs"]]
    paths = [path for path, _ in pairs]
    for path in paths:
        _relative_parts(path)
    if (binding["admission_id"] != identifier or binding["admission_digest"] != admission["digest"]
            or execution["command"] != command or len(paths) != len(set(paths))
            or [ref for _, ref in pairs[1:]] != command["inputs"]):
        raise ResearchError("execution_identity_mismatch", "Archive paths must match the admitted program and inputs",
                            {"admission_id": identifier})
    return admission, binding, pairs


def _write_run(archive, root, records, execution):
    origin = execution["origin"]
    run = {"execution_id": execution["id"], "cycle_id": execution["cycle_id"], "origin": origin,
           "command": execution["command"], "admission_id": None, "directory": None, "argv": None, "outputs": []}
    if origin["kind"] == "managed":
        admission, binding, pairs = _read_frozen_run(records, execution)
        directory = "runs/run-" + _quote_component(admission["id"])
        replacements = {str(root / "experiment" / path): path for path, _ in pairs}
        argv = [replacements.get(argument, argument) for argument in admission["command"]["argv"]]
        argv[0], argv[1] = binding["runtime"]["path"], binding["script"]
        run.update(admission_id=admission["id"], directory=directory, argv=argv, runtime=binding["runtime"],
                   script=binding["script"], inputs=binding["inputs"], declared_outputs=binding["outputs"])
        for path, ref in pairs:
            archive.write(directory + "/" + path, archive.write_artifact(ref))
        for name in ("logs", "results", "plots"):
            archive.write_directory(directory + "/" + name)
    for output in sorted(execution["outputs"], key=lambda item: item["id"]):
        ref = output["artifact"]
        media_type = ref["media_type"].split(";", 1)[0].strip().lower()
        suffix = _SUFFIXES.get(media_type, ".bin")
        name = ("data/execution-" + _quote_component(execution["id"]) + "/output-" + _quote_component(output["id"])
                + suffix)
        archive.write_artifact(ref, name)
        run["outputs"].append(dict(output, archive=name))
    return run


def _iter_supplement_files(root, relative):
    parts = _relative_parts(relative)
    with checked_parent(root, relative) as (directory, name):
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if stat.S_ISREG(info.st_mode):
        yield relative, read_file(root, relative)
    elif stat.S_ISDIR(info.st_mode):
        from .artifacts import _Workspace
        with _Workspace(root).directory("/".join(parts)) as directory:
            names = sorted(os.listdir(directory))
        for name in names:
            yield from _iter_supplement_files(root, relative + "/" + name)
    else:
        raise ResearchError("unsafe_path", "Supplementary paths must contain only regular files and directories")


def _write_package(stream, root, snapshot, bundle, supplements):
    records = snapshot["records"]
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        archive = _Archive(zipped, Evaluation(records, ArtifactStore(root)))
        claims = bundle.get("files", {}).get("claims")
        links = bundle.get("claim_evidence")
        if claims is None or not isinstance(links, list):
            raise ResearchError("publication_claims_missing", "Package the pinned claim registry and its evidence map")
        archive.write("README.txt", b"Read package.json for frozen runs and records, "
                      b"and data/INDEX.json for object names.\n")
        archive.write("claims.json", archive.write_artifact(claims["artifact"]))
        archive.write("claim-evidence.json", _encode_json_bytes(links))
        manifest = {"schema": 1, "revision": snapshot["revision"],
                    "bundle": {"id": bundle["id"], "digest": bundle["digest"]},
                    "runs": [_write_run(archive, root, records, execution)
                             for execution in _find_cited_executions(records, links)],
                    "records": {}, "supplements": []}
        archive.write_references(links)
        for kind in RECORD_KINDS:
            group = {}
            for key, value in sorted(records.get(kind, {}).items()):
                name = "managed-record/" + kind + "/" + _quote_component(key) + ".json"
                archive.write(name, _encode_json_bytes(value))
                archive.write_references(value)
                group[key] = name
            if group:
                manifest["records"][kind] = group
        archive.follow()
        for path, data in supplements:
            if path in ("package.json", "data/INDEX.json") or path.startswith("runs/"):
                raise ResearchError("package_member_conflict",
                                    "Supplementary files must stay outside frozen runs and metadata")
            archive.write(path, data, supplement=True)
            manifest["supplements"].append({"path": path, "sha256": hashlib.sha256(data).hexdigest(),
                                            "size": len(data)})
        archive.write("data/INDEX.json", _encode_json_bytes(archive.index))
        archive.write("package.json", _encode_json_bytes(manifest))
        return {"revision": snapshot["revision"], "bundle_digest": bundle["digest"], "runs": len(manifest["runs"]),
                "artifacts": len(archive.index), "members": len(archive.members), "mechanical_only": True}


def build_package(store, destination, *, bundle=None, includes=()):
    """Write a new ZIP from one authenticated snapshot without changing the study."""
    snapshot = store.snapshot()
    selected = _select_bundle(snapshot["records"], bundle)
    destination = Path(destination).absolute()
    destination = destination.parent.resolve() / destination.name
    try:
        supplements = [item for path in includes for item in _iter_supplement_files(store.root, path)]
        with checked_parent(destination.parent, destination.name) as (directory, name):
            try:
                os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ResearchError("package_destination_exists", "Write the archive to a new destination")
            temporary = ".package-" + uuid.uuid4().hex
            descriptor = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
            try:
                with os.fdopen(descriptor, "w+b") as stream:
                    result = _write_package(stream, store.root, snapshot, selected, supplements)
                    stream.flush()
                    os.fsync(stream.fileno())
                    size = os.fstat(stream.fileno()).st_size
                try:
                    os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                except FileExistsError as error:
                    raise ResearchError("package_destination_exists",
                                        "Write the archive to a new destination") from error
                os.fsync(directory)
            finally:
                os.unlink(temporary, dir_fd=directory)
        return dict(result, path=str(destination), size=size)
    except OSError as error:
        raise _filesystem_error(error) from error
