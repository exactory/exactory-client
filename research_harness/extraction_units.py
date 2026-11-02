"""Page extraction over one complete retained original (pdf-pages-v1).

The injected store implements read_committed_units(job_id) and
commit_unit(job_id, unit). The first unit has kind=manifest and pins page_count;
subsequent kind=page units have one-based page, status=extracted and text refs.
Every unit repeats the exact binding. Store commits must be durable and
idempotent for (job_id, kind, page); no record kinds are registered here.

pdf-pages-v1 removes one trailing form feed from each pdftotext page, replaces
OCR form feeds with newlines, and appends exactly one UTF-8 form feed after
EVERY page, including empty and final pages. Its hash is independent of legacy
whole-document extraction. The engine returns capture-shaped descriptors,
never reading or reviewer-delivery records.
"""

from contextlib import contextmanager
import codecs
import copy
import errno
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import uuid

from .artifacts import validate_reference
from .errors import ResearchError
from .finishing import retry_store_write
from .probe_supervision import find_process_group_members, find_process_identity, read_probe_clock
from .workspace import checked_parent, json_projection, open_file, read_file, strict_json, write_projection


PROTOCOL = "pdf-pages-v1"
TEXT_MEDIA_TYPE = "text/plain; charset=utf-8"


def _hash_value(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


@contextmanager
def _hold_owner(root, relative):
    with checked_parent(root, relative + "/owner.lock", create=True) as (directory, name):
        descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield None
        else:
            yield descriptor
    finally:
        os.close(descriptor)


def _confirm_stopped(root, operation):
    try:
        supervision = strict_json(read_file(root, operation + "/supervision.json"))
    except ResearchError as error:
        if error.code != "artifact_missing":
            raise
        return
    program = supervision["program"]
    if (find_process_identity(program["pid"]) == program
            or find_process_group_members(program["process_group"])):
        raise ResearchError("fulltext_job_pending", "The prior extraction tool has not terminated")


class _SupervisedTools:
    """Retain only unfinished unit runs; inherit the job lock through the launch gate."""

    def __init__(self, root, directory, owner, binding, unit_timeout):
        self.root, self.directory, self.owner = root, directory, owner
        self.binding, self.unit_timeout = binding, unit_timeout
        self.scope, self.cursor = None, 0

    def begin_scope(self, scope, *, deadline_kind="invocation"):
        self.scope, self.cursor = scope, 0
        self.scope_started, self.deadline_kind = read_probe_clock(), deadline_kind

    def discard_scope(self):
        path = self.root / self.directory / _hash_value(self.scope)
        # All entries here were created in a checked private job directory.
        if path.exists():
            shutil.rmtree(path)

    def __call__(self, argv, limit, deadline, *, include_stderr=False):
        from .probe_worker import run_probe_process
        operation = self.directory + "/" + _hash_value(self.scope) + "/" + str(self.cursor)
        self.cursor += 1
        try:
            config = strict_json(read_file(self.root, operation + "/config.json"))
        except ResearchError as error:
            if error.code != "artifact_missing":
                raise
            config = None
        if config is not None:
            if (_hash_value(config.get("binding")) != _hash_value(self.binding) or config.get("limit") != limit
                    or config.get("include_stderr") != include_stderr):
                raise ResearchError("record_conflict", "Unfinished extraction tool configuration changed")
            supervision = self._read_optional(operation + "/supervision.json")
            if supervision:
                _confirm_stopped(self.root, operation)
            elif (self.root / operation / "stdout").exists():
                # An interrupted launch before the gate opened earned no output.
                self._discard_operation(operation)
                config = None
        if config is None:
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                elapsed = read_probe_clock() - self.scope_started
                allowance = self.unit_timeout if self.deadline_kind == "unit" else max(0, elapsed + seconds)
                raise ResearchError("extraction_timeout", "The extraction operation has no time remaining",
                                    {"usage": {"elapsed_seconds": elapsed},
                                     "limit": {"elapsed_seconds": allowance}, "deadline_kind": self.deadline_kind})
            frozen_deadline = read_probe_clock() + seconds
            with checked_parent(self.root, operation + "/work/.directory", create=True):
                pass
            config = {"directory": operation, "binding": self.binding, "limit": limit,
                      "include_stderr": include_stderr, "timeout_seconds": seconds,
                      "deadline": frozen_deadline, "deadline_kind": self.deadline_kind,
                      "unit_started": self.scope_started,
                      "unit_timeout_seconds": self.unit_timeout if self.deadline_kind == "unit" else frozen_deadline - self.scope_started,
                      "tool_argv": list(argv)}
            # The inner bounded reader enforces per-page output/image allowances;
            # the existing owner, gate and watchdog supervise the whole group.
            config["argv"] = [sys.executable, "-c",
                              "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
                              "from research_harness.extraction_units import run_bounded_tool; "
                              "run_bounded_tool(Path(sys.argv[2]), sys.argv[3])",
                              str(Path(__file__).resolve().parents[1]), str(self.root), operation]
            config_hash = _hash_value(config)
            json_projection(self.root, operation + "/config.json", config)
            try:
                terminal = run_probe_process(self.root, config, self.owner,
                                             find_process_identity(os.getpid()), config_hash)
            except OSError as error:
                raise ResearchError("extraction_failed", "Extraction tool launch failed") from error
            terminal["config_sha256"] = config_hash
            json_projection(self.root, operation + "/terminal.json", terminal)
        _confirm_stopped(self.root, operation)
        config_hash = _hash_value(config)
        terminal = self._read_optional(operation + "/terminal.json")
        if terminal is None:
            terminal = self._read_optional(operation + "/supervisor-terminal.json")
        execution = self._read_optional(operation + "/program-exit.json")
        if terminal is not None and terminal.get("config_sha256") != config_hash:
            raise ResearchError("record_conflict", "Extraction termination identity changed")
        if terminal is not None and terminal.get("exit_status") == "timed_out":
            supervision = self._read_optional(operation + "/supervision.json") or {}
            elapsed = terminal["elapsed_seconds"] + supervision.get("started", config["unit_started"]) - config["unit_started"]
            raise ResearchError("extraction_timeout", "The supervised extraction tool timed out",
                                {"usage": {"elapsed_seconds": elapsed},
                                 "limit": {"elapsed_seconds": config["unit_timeout_seconds"]},
                                 "deadline_kind": config["deadline_kind"]})
        if terminal is not None and terminal.get("exit_status") == "output_bound":
            from .probe_worker import STREAM_LIMIT
            measured = 0
            for name in ("stdout", "stderr"):
                with open_file(self.root, operation + "/" + name) as source:
                    measured = max(measured, os.fstat(source.fileno()).st_size)
            raise ResearchError("extraction_too_large", "The supervised extraction output exceeded its limit",
                                {"usage": {"output_bytes": measured},
                                 "limit": {"output_bytes": min(limit, STREAM_LIMIT)}})
        succeeded = (terminal is not None and type(terminal.get("exit_status")) is int
                     and terminal["exit_status"] == 0)
        if terminal is None and execution is not None:
            succeeded = execution.get("config_sha256") == config_hash and execution.get("returncode") == 0
        if not succeeded:
            self._discard_operation(operation)
            raise ResearchError("extraction_failed", "The supervised extraction tool did not complete")
        result = self._read_optional(operation + "/tool-result.json")
        if not isinstance(result, dict) or result.get("config_sha256") != config_hash:
            raise ResearchError("extraction_failed", "The extraction tool has no authenticated terminal result")
        if result["status"] != "extracted":
            error = result["error"]
            raise ResearchError(error["code"], error["message"], error.get("details"))
        with open_file(self.root, operation + "/tool-output") as source:
            data = source.read(limit + 1)
        if len(data) > limit:
            raise ResearchError("extraction_too_large", "The extraction output exceeded its byte limit",
                                {"usage": {"output_bytes": len(data)}, "limit": {"output_bytes": limit}})
        expected = result.get("output", {})
        if len(data) != expected.get("size") or hashlib.sha256(data).hexdigest() != expected.get("sha256"):
            raise ResearchError("artifact_corrupt", "The retained extraction tool output changed before capture")
        return data

    def _read_optional(self, relative):
        try:
            return strict_json(read_file(self.root, relative))
        except ResearchError as error:
            if error.code != "artifact_missing":
                raise
            return None

    def _discard_operation(self, operation):
        shutil.rmtree(self.root / operation)


class ExtractionEngine:
    def __init__(self, artifacts, units, state_directory, extractor):
        self.artifacts, self.units, self.extractor = artifacts, units, extractor
        self.root = Path(state_directory).absolute()

    def resume(self, job_id, *, original, work_id, version, tool_versions, options,
               max_pages=None, max_seconds=60):
        validate_reference(original)
        if (any(not isinstance(value, str) or not value.strip() for value in (job_id, work_id))
                or version is None and work_id.startswith("arxiv:")
                or version is not None and (not isinstance(version, str) or not version.strip())):
            raise ResearchError("invalid_input", "Extraction requires a job, exact work and applicable version")
        if (not isinstance(tool_versions, dict) or not tool_versions
                or any(not isinstance(v, str) or not v for v in tool_versions.values())):
            raise ResearchError("invalid_input", "Extraction requires pinned tool versions")
        if _hash_value(options) != _hash_value(self.extractor.get_page_options()):
            raise ResearchError("record_conflict", "Extraction options differ from the pinned job")
        if type(max_seconds) not in (int, float) or not math.isfinite(max_seconds) or max_seconds <= 0:
            raise ResearchError("invalid_input", "Invocation time must be positive and finite")
        if max_pages is None:
            max_pages = getattr(self.extractor, "max_pages", 200)
        if type(max_pages) is not int or max_pages <= 0:
            raise ResearchError("invalid_input", "Invocation page allowance must be a positive integer")
        binding = copy.deepcopy({"original": original, "work_id": work_id, "version": version,
                                 "tool_versions": tool_versions, "options": options,
                                 "extractor": self.extractor.name, "protocol": PROTOCOL})
        directory = _hash_value(job_id)
        deadline = time.monotonic() + max_seconds
        with _hold_owner(self.root, directory) as owner:
            if owner is None:
                count, pages = self._read_units(job_id, binding)
                return self._build_result(binding, count, pages, {"code": "fulltext_job_pending",
                                          "recovery_action": "resume_after_worker_termination"})
            return self._resume_owned(job_id, binding, directory, owner, max_pages, deadline)

    def _read_units(self, job_id, binding):
        manifest, pages = None, {}
        for unit in self.units.read_committed_units(job_id):
            if not isinstance(unit, dict) or _hash_value(unit.get("binding")) != _hash_value(binding):
                raise ResearchError("record_conflict", "Extraction unit belongs to a different original, identity or tool configuration")
            if unit.get("kind") == "manifest" and manifest is None:
                count = unit.get("page_count")
                if type(count) is not int or count <= 0:
                    raise ResearchError("record_conflict", "Invalid extraction page count")
                manifest = unit
            elif unit.get("kind") == "page":
                page = unit.get("page")
                if (type(page) is not int or page <= 0 or page in pages or unit.get("status") != "extracted"):
                    raise ResearchError("record_conflict", "Invalid, duplicate or unsuccessful extraction page")
                validate_reference(unit.get("text"))
                if unit["text"]["media_type"] != TEXT_MEDIA_TYPE:
                    raise ResearchError("record_conflict", "An extraction page must be UTF-8 text")
                pages[page] = unit
            else:
                raise ResearchError("record_conflict", "Invalid or duplicate extraction manifest")
        count = manifest["page_count"] if manifest else None
        if pages and (count is None or max(pages) > count
                      or any(unit.get("page_count") != count for unit in pages.values())):
            raise ResearchError("record_conflict", "Extraction pages exceed the pinned document coverage")
        return count, pages

    def _resume_owned(self, job_id, binding, directory, owner, max_pages, deadline):
        count, pages = self._read_units(job_id, binding)
        runner = _SupervisedTools(self.root, directory + "/runs", owner, binding, self.extractor.timeout)
        stage, page, started = "tools", None, time.monotonic()
        try:
            for path in (self.root / directory / "runs").rglob("supervision.json"):
                _confirm_stopped(self.root, path.parent.relative_to(self.root).as_posix())
            for path in (self.root / directory).iterdir():
                if path.name.startswith((".artifact-", "assembly-")):
                    shutil.rmtree(path)
            if count is not None:
                runner.begin_scope("page_count")
                runner.discard_scope()
            for committed_page in pages:
                runner.begin_scope("page-" + str(committed_page))
                runner.discard_scope()
            runner.begin_scope("versions-" + uuid.uuid4().hex)
            versions = self.extractor.read_page_tool_versions(run=runner, deadline=deadline)
            runner.discard_scope()
            if versions != binding["tool_versions"]:
                raise ResearchError("record_conflict", "Installed tool versions differ from the pinned extraction job")
            with self.artifacts.materialize_verified(binding["original"], self.root / directory) as original:
                if count is None:
                    stage = "page_count"
                    runner.begin_scope("page_count")
                    count = self.extractor.count_pages(original, run=runner, deadline=deadline)
                    manifest = {"kind": "manifest", "binding": binding, "page_count": count}
                    retry_store_write(lambda: self.units.commit_unit(job_id, manifest))
                    runner.discard_scope()
                processed = 0
                for page in range(1, count + 1):
                    if page in pages:
                        continue
                    if processed >= max_pages or time.monotonic() >= deadline:
                        return self._build_result(binding, count, pages, {"code": "fulltext_job_pending",
                                                  "page": page, "recovery_action": "resume_extraction"})
                    stage, started = "page", time.monotonic()
                    unit_deadline = started + self.extractor.timeout
                    runner.begin_scope("page-" + str(page),
                                       deadline_kind="invocation" if deadline < unit_deadline else "unit")
                    text = self.extractor.extract_page(original, page, run=runner, deadline=min(deadline, unit_deadline))
                    artifact = self.artifacts.put(text.encode("utf-8"), TEXT_MEDIA_TYPE)
                    unit = {"kind": "page", "binding": binding, "page_count": count,
                            "page": page, "status": "extracted", "text": artifact}
                    retry_store_write(lambda: self.units.commit_unit(job_id, unit))
                    pages[page] = unit
                    runner.discard_scope()
                    processed += 1
            return self._assemble_text(binding, count, pages, directory, deadline)
        except ResearchError as error:
            if error.code == "storage_io" and (error.details or {}).get("errno") in (errno.ENOSPC, errno.EDQUOT):
                return self._build_result(binding, count, pages, {"code": "fulltext_storage_limit",
                                          "page": page, "recovery_action": "restore_storage_and_resume"})
            if error.code in ("record_conflict", "artifact_corrupt", "artifact_missing", "unsafe_path"):
                raise
            if error.code in ("extraction_timeout", "extraction_too_large"):
                details = dict(error.details or {})
                usage, limits = dict(details.get("usage", {})), dict(details.get("limit", {}))
                if error.code == "extraction_timeout":
                    # The page allowance covers all page subprocesses together.
                    usage["elapsed_seconds"] = max(time.monotonic() - started, usage.get("elapsed_seconds", 0))
                    limits.setdefault("elapsed_seconds", self.extractor.timeout)
                pending = {"code": "fulltext_unit_limit", "cause": error.code, "page": page,
                           "stage": stage, "usage": usage, "limit": limits,
                           "retryable_with_same_configuration": False,
                           "recovery_action": "increase_unit_allowance_or_use_another_representation"}
                if error.code == "extraction_timeout" and (details.get("deadline_kind") == "invocation"
                        or time.monotonic() >= deadline):
                    pending = {"code": "fulltext_job_pending", "page": page, "stage": stage,
                               "usage": usage, "limit": {"invocation_seconds": limits.get("elapsed_seconds", max(0, deadline - started))},
                               "recovery_action": "resume_extraction"}
            else:
                pending = {"code": error.code, "page": page, "stage": stage,
                           "recovery_action": "resume_extraction"}
            if error.code in ("extraction_timeout", "extraction_too_large", "extraction_failed",
                              "extractor_missing", "malformed_pdf"):
                runner.discard_scope()
            return self._build_result(binding, count, pages, pending)
        except (OSError, UnicodeError) as error:
            code = "fulltext_storage_limit" if isinstance(error, OSError) and error.errno in (errno.ENOSPC, errno.EDQUOT) else "extraction_failed"
            return self._build_result(binding, count, pages, {"code": code, "page": page,
                                      "recovery_action": "restore_storage_and_resume" if code == "fulltext_storage_limit" else "resume_extraction"})

    def _assemble_text(self, binding, count, pages, directory, deadline):
        if set(pages) != set(range(1, count + 1)):
            return self._build_result(binding, count, pages, {"code": "fulltext_job_pending",
                                      "recovery_action": "resume_extraction"})
        characters, whitespace, longest, line, size, nonempty = 0, 0, 0, 0, 0, False
        with tempfile.TemporaryDirectory(prefix="assembly-", dir=self.root / directory) as temporary:
            target_path = Path(temporary) / "text"
            with target_path.open("wb") as target:
                for page in range(1, count + 1):
                    reference = pages[page]["text"]
                    decoder = codecs.getincrementaldecoder("utf-8")()
                    with self.artifacts.open_verified(reference) as frozen:
                        for block in iter(lambda: frozen.stream.read(65536), b""):
                            if time.monotonic() >= deadline:
                                return self._build_result(binding, count, pages, {"code": "fulltext_job_pending",
                                                          "stage": "assembly", "recovery_action": "resume_extraction"})
                            target.write(block)
                            size += len(block)
                            text = decoder.decode(block)
                            characters += len(text)
                            for character in text:
                                whitespace += character.isspace()
                                nonempty = nonempty or not character.isspace()
                                if character in "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029":
                                    longest, line = max(longest, line), 0
                                else:
                                    line += 1
                        decoder.decode(b"", final=True)
                    target.write(b"\f")
                    size, characters, whitespace = size + 1, characters + 1, whitespace + 1
                    longest, line = max(longest, line), 0
                target.flush()
                os.fsync(target.fileno())
            result = self._build_result(binding, count, pages, None)
            result["extraction"].update(text_bytes=size, max_line_length=longest,
                                       whitespace_fraction=round(whitespace / characters, 4) if characters else 0.0,
                                       expansion_ratio=round(size / binding["original"]["size"], 3) if binding["original"]["size"] else None)
            if nonempty:
                result.update(text=self.artifacts.put_file(target_path, TEXT_MEDIA_TYPE),
                              availability="available", extraction_status="extracted")
            else:
                result.update(extraction_status="empty_text", pending={"code": "empty_text",
                              "recovery_action": "use_another_representation_or_inspect_original"})
            return result

    def _build_result(self, binding, count, pages, pending):
        return {"original": binding["original"], "text": None, "availability": "pending",
                "requested_version_id": binding["work_id"], "version": binding["version"],
                "extraction_status": pending["code"] if pending else "fulltext_job_pending",
                "includes_abstract": None, "visual_inspection_required": True,
                "completed_pages": len(pages), "page_count": count, "pending": pending,
                "extraction": {"protocol": PROTOCOL, "extractor": binding["extractor"],
                               "version": "; ".join(name + "=" + value for name, value in sorted(binding["tool_versions"].items())),
                               "tool_versions": binding["tool_versions"], "options": binding["options"],
                               "media_type": "application/pdf", "format_detection": "retained_original",
                               "page_count": count}}


def run_bounded_tool(root, operation):
    """Internal supervised worker entry point; it cannot commit extraction units."""
    from .ocr import run_bounded
    config = strict_json(read_file(root, operation + "/config.json"))
    result = {"config_sha256": _hash_value(config)}
    try:
        remaining = config["deadline"] - read_probe_clock()
        if remaining <= 0:
            raise ResearchError("extraction_timeout", "The extraction deadline expired before tool launch")
        data = run_bounded(config["tool_argv"], config["limit"],
                           time.monotonic() + remaining,
                           include_stderr=config["include_stderr"])
        write_projection(root, operation + "/tool-output", data)
        output = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        if read_probe_clock() >= config["deadline"]:
            raise ResearchError("extraction_timeout", "The extraction deadline expired before output capture")
        result.update(status="extracted", output=output)
    except ResearchError as error:
        if error.code == "extraction_timeout":
            error.details = {"usage": {"elapsed_seconds": read_probe_clock() - config["unit_started"]},
                             "limit": {"elapsed_seconds": config["unit_timeout_seconds"]},
                             "deadline_kind": config["deadline_kind"]}
        result.update(status="failed", error=error.as_dict())
    except OSError as error:
        code = "extractor_missing" if error.errno == errno.ENOENT else "storage_io"
        result.update(status="failed", error={"code": code, "message": str(error), "details": {"errno": error.errno}})
    json_projection(root, operation + "/tool-result.json", result)
