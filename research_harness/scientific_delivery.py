"""Bounded typed derivatives for source-limited independent review delivery.

Every artifact is classified before bytes are emitted. Internal roles dominate
author declarations. Projection metadata contains scalar origin hashes, never
an original descriptor that the generic artifact walker might copy. Scientific
sufficiency and truthful declarations remain independent review questions.
"""

import hashlib
import io
import json
import tarfile
import zipfile

from .artifacts import describe_artifact, validate_reference
from .errors import ResearchError
from .evidence import digest
from .operations import fields, text
from .source_links import read_locator, captured_source, complete_original
from .scientific_json import scientific_json
from .workspace import strict_json


_CODE = "invalid_scientific_delivery"
_PRIVATE = "private_mixed_artifact_required"
_MAX_BYTES = 64 * 1024 * 1024
_MAX_MEMBERS = 1000
_MAX_NUMPY_MEMBERS = 20000
_MAX_OUTPUT_BYTES = 512 * 1024 * 1024
_MAX_DEPTH = 40
_REF_KEYS = {"path", "sha256", "size", "media_type"}


def _references(value):
    found = {}
    def visit(item):
        if isinstance(item, dict):
            if _REF_KEYS <= item.keys():
                found[item["sha256"]] = item
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
    visit(value)
    return found


def _sha(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ResearchError(_CODE, "Supply an exact lowercase original content SHA-256")


def _projection_shape(value, depth=0):
    if depth > 8:
        raise ResearchError(_CODE, "Scientific archive projection nesting is bounded")
    fields(value, ("kind", "context"), ("locators", "members", "source_sha256", "start", "end"), code=_CODE)
    text(value["context"], "Scientific projection context", code=_CODE)
    if value["kind"] != "source_component" and any(k in value for k in ("source_sha256", "start", "end")):
        raise ResearchError(_CODE, "Source byte spans belong only to exact source components")
    if value["kind"] in ("json_locators", "text_spans"):
        if "members" in value or not isinstance(value.get("locators"), list) or not value["locators"]:
            raise ResearchError(_CODE, "Declare nonempty typed scientific locators")
        kinds = {"json"} if value["kind"] == "json_locators" else {"text", "span"}
        if any(not isinstance(locator, dict) or locator.get("kind") not in kinds for locator in value["locators"]):
            raise ResearchError(_CODE, "Use the declared projection's locator type")
    elif value["kind"] == "npy_array":
        if set(value) != {"kind", "context"}:
            raise ResearchError(_CODE, "A numerical projection retains the complete array")
    elif value["kind"] == "source_component":
        if set(value) != {"kind", "context", "source_sha256", "start", "end"}:
            raise ResearchError(_CODE, "A source component requires its exact original source byte span")
        _sha(value["source_sha256"])
        if type(value["start"]) is not int or type(value["end"]) is not int or not 0 <= value["start"] < value["end"]:
            raise ResearchError(_CODE, "Source byte spans require ordered nonnegative integer offsets")
    elif value["kind"] == "archive_members":
        if "locators" in value or not isinstance(value.get("members"), list) or not 1 <= len(value["members"]) <= _MAX_NUMPY_MEMBERS:
            raise ResearchError(_CODE, "Declare bounded scientific archive members")
        if len(value["members"]) > _MAX_MEMBERS and not _all_numerical(value["members"]):
            raise ResearchError(_CODE, "Only complete numerical populations may exceed the generic archive member bound")
        seen = set()
        for member in value["members"]:
            fields(member, ("path", "sha256", "media_type", "projection"), code=_CODE)
            _safe_member(member["path"])
            _sha(member["sha256"])
            text(member["media_type"], "Member media type", code=_CODE)
            if member["path"] in seen:
                raise ResearchError(_CODE, "Archive member names must be unique")
            seen.add(member["path"])
            _projection_shape(member["projection"], depth + 1)
    else:
        raise ResearchError(_CODE, "Use JSON locators, text spans or checked archive members")


def validate_declarations(values):
    if not isinstance(values, list):
        raise ResearchError(_CODE, "Scientific delivery declarations must be an array")
    seen = set()
    for value in values:
        fields(value, ("original_sha256", "disposition", "projection"), code=_CODE)
        _sha(value["original_sha256"])
        if value["original_sha256"] in seen or value["disposition"] not in ("public", "project", "internal"):
            raise ResearchError(_CODE, "Declare each artifact once with an explicit disposition")
        seen.add(value["original_sha256"])
        if value["disposition"] == "project":
            _projection_shape(value["projection"])
        elif value["projection"] is not None:
            raise ResearchError(_CODE, "Only projected artifacts carry projection instructions")


def _safe_member(name):
    if (not isinstance(name, str) or not name or "\\" in name or "\x00" in name or name.startswith("/")
            or any(part in ("", ".", "..") for part in name.split("/"))):
        raise ResearchError(_CODE, "Archive members require canonical safe relative paths")


def _all_numerical(members):
    return all(isinstance(m, dict) and isinstance(m.get("path"), str) and m["path"].endswith(".npy")
               and isinstance(m.get("projection"), dict) and m["projection"].get("kind") == "npy_array" for m in members)


def _read_members(data, max_members):
    result, total = {}, 0
    try:
        if zipfile.is_zipfile(io.BytesIO(data)):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members = archive.infolist()
                if len(members) > max_members:
                    raise ResearchError(_CODE, "Archive member count exceeds the delivery bound")
                for member in members:
                    if member.is_dir():
                        continue
                    _safe_member(member.filename)
                    mode = member.external_attr >> 16
                    if (mode & 0o170000) == 0o120000 or member.filename in result:
                        raise ResearchError(_CODE, "Archive links and duplicate names cannot be delivered")
                    total += member.file_size
                    if total > _MAX_BYTES:
                        raise ResearchError(_CODE, "Expanded archive exceeds the delivery byte bound")
                    result[member.filename] = archive.read(member)
        else:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
                for index, member in enumerate(archive):
                    if index >= max_members:
                        raise ResearchError(_CODE, "Archive member count exceeds the delivery bound")
                    if member.isdir():
                        continue
                    _safe_member(member.name)
                    if not member.isfile() or member.name in result:
                        raise ResearchError(_CODE, "Archive links and duplicate names cannot be delivered")
                    total += member.size
                    if total > _MAX_BYTES:
                        raise ResearchError(_CODE, "Expanded archive exceeds the delivery byte bound")
                    result[member.name] = archive.extractfile(member).read()
    except (tarfile.TarError, zipfile.BadZipFile, OSError, RuntimeError) as error:
        raise ResearchError(_CODE, "The declared scientific archive cannot be read safely") from error
    return result


def _scientific_record(value):
    """The typed service-record projection excludes its administrative fields."""
    private = {"author", "authors", "request_id", "token", "authorization", "scientific_preparers",
               "independence_basis", "assessor", "human_goals", "private_context"}
    if isinstance(value, dict):
        if _REF_KEYS <= value.keys():
            return value
        return {k: _scientific_record(v) for k, v in value.items()
                if k not in private and not k.endswith("_revision")}
    if isinstance(value, list):
        return [_scientific_record(v) for v in value]
    return value


class ScientificDelivery:
    def __init__(self, records, artifacts, contract, manifest, *, manuscript_files=(), corrective=False):
        self.records, self.artifacts = records, artifacts
        declarations = contract["payload"].get("scientific_delivery", [])
        validate_declarations(declarations)
        self.declarations = {d["original_sha256"]: d for d in declarations}
        self.derived, self.mapped, self.visiting = {}, {}, set()
        self._parsed_locator_mappings = {}
        self.required, self.private, self.generated = {}, {}, {}
        self.output_bytes, self.output_hashes, self.numerical_elements = 0, set(), 0
        self.exact_files = {ref["sha256"] for ref in manuscript_files}
        self.public, self.validated_public = {}, set()
        def register(ref, source_id, work=None, capture=None):
            if ref is not None:
                self.public.setdefault(ref["sha256"], []).append((ref, source_id, work, capture))
        for work in records.get("work", {}).values():
            for capture in work.get("fulltexts", []):
                for key in ("original", "text"):
                    register(capture.get(key), capture["source_id"], work, capture)
            for abstract in work.get("abstracts", []):
                register(abstract["artifact"], abstract["source_id"], work)
        for source in records.get("source", {}).values():
            register(source.get("response"), source["id"])
        for deferred in records.get("source_deferral", {}).values():
            self.private.update(_references([deferred["authorization"], deferred["acquisition_evidence"]]))
        protected = set(self.private)
        for saved in records.get("publication_scope", {}).values():
            payload = saved["payload"]
            response = payload["correction"]["response"] if payload["correction"] is not None else None
            private_roles = _references([payload["authorization"], payload["scientific_preparers"]])
            protected.update(private_roles)
            self.private.update(private_roles)
            self.private.update(_references(response))
        for kind in ("readiness_review", "scoped_readiness_review", "manuscript_review", "manuscript_prediction", "round_review"):
            for saved in records.get(kind, {}).values():
                private_roles = _references([saved.get("artifact"), saved.get("review"),
                                             saved.get("assessor"), saved.get("payload", {}).get("assessor")])
                protected.update(private_roles)
                self.private.update(private_roles)
        if corrective and contract["payload"]["correction"] is not None:
            # Only this explicitly requested response can be projected, and only
            # for identified corrective review. Unrelated private context stays private.
            response = contract["payload"]["correction"]["response"]
            if response["sha256"] not in protected:
                self.private.pop(response["sha256"], None)
        self.private_bytes = tuple(artifacts.read(ref) for ref in self.private.values())
        self.minimum_private_bytes = min((len(data) for data in self.private_bytes if data), default=0)
        for checkpoint in records.get("checkpoint", {}).values():
            self.generated[checkpoint["artifact"]["sha256"]] = _scientific_record({k: v for k, v in checkpoint.items() if k != "artifact"})
        for claim in records.get("execution_claim", {}).values():
            self.generated[claim["config_artifact"]["sha256"]] = _scientific_record(claim["config"])
        for observation in records.get("execution_observation", {}).values():
            terminal = observation["terminal"]
            self.generated[terminal["sha256"]] = _scientific_record(strict_json(artifacts.read(terminal)))
        self._inventory(manifest)

    def _public_reference(self, ref, *, original_pdf=False):
        if ref["sha256"] in self.private:
            raise ResearchError(_PRIVATE, "An internal artifact cannot become a public source by alias")
        key = (digest(ref), original_pdf)
        if key in self.validated_public:
            return
        candidates = [entry for entry in self.public.get(ref["sha256"], [])
                      if entry[0] == ref and (not original_pdf or
                          entry[3] is not None and entry[3].get("original") == ref)]
        captures = [entry for entry in candidates if entry[3] is not None]
        if captures:
            # Capture-owned bytes cannot fall back to an unbound raw response.
            # Earlier failed attempts stay historical when the exact bytes have
            # a later available capture, whose binding is checked below.
            candidates = [entry for entry in captures if entry[3]["availability"] == "available"]
            if not candidates:
                raise ResearchError(_PRIVATE, "A pending source capture cannot establish public provenance")
        if not candidates:
            raise ResearchError(_PRIVATE, "Nested provenance must exactly identify an established public source artifact")
        for _, source_id, work, capture in candidates:
            source = captured_source(self.records, self.artifacts, source_id)
            if source["response"]["sha256"] in self.private:
                raise ResearchError(_PRIVATE, "A public extraction cannot declassify its internally classified original")
            self._check_bytes(self.artifacts.read(source["response"]))
            if capture is not None:
                from .components import validate_binding
                validate_binding(self.records, self.artifacts, work["id"], capture)
            if original_pdf:
                if not complete_original({"source": source, "capture": capture}):
                    raise ResearchError(_PRIVATE, "Source components require a complete verified HTTP original")
                inferred = (ref["media_type"] == "application/octet-stream"
                            and capture.get("extraction", {}).get("media_type") == "application/pdf"
                            and capture.get("extraction", {}).get("format_detection") == "pdf_signature_from_generic_binary")
                if ref["media_type"] != "application/pdf" and not inferred:
                    raise ResearchError(_CODE, "A JPEG source component requires an original PDF")
        # Validate all eligible native bindings so an invalid available alias
        # cannot be hidden by record order. Integrity errors are never skipped.
        self.artifacts.read(ref)
        self.validated_public.add(key)

    def _public_nested(self, value, depth=0):
        if depth > _MAX_DEPTH:
            raise ResearchError(_CODE, "Scientific source provenance nesting exceeds its bound")
        if isinstance(value, dict):
            if _REF_KEYS <= value.keys():
                self._public_reference(value)
            for child in value.values():
                self._public_nested(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                self._public_nested(child, depth + 1)

    def _inventory(self, value):
        if isinstance(value, dict):
            if isinstance(value.get("artifact"), dict) and isinstance(value.get("locator"), dict):
                ref = value["artifact"]
                self.required.setdefault(ref["sha256"], {})[digest(value["locator"])] = value["locator"]
            for child in value.values():
                self._inventory(child)
        elif isinstance(value, list):
            for child in value:
                self._inventory(child)

    def _check_bytes(self, data, *, artifact=True):
        if artifact and len(data) > _MAX_BYTES:
            raise ResearchError(_CODE, "Scientific delivery artifact exceeds the byte bound")
        if not self.minimum_private_bytes or len(data) < self.minimum_private_bytes:
            return
        if any(private and private in data for private in self.private_bytes):
            raise ResearchError(_PRIVATE, "Delivered bytes contain an internally classified artifact's content")

    def _emit(self, content):
        content = self.walk(content)
        data = (json.dumps(content, ensure_ascii=False, indent=2) + "\n").encode()
        self._check_bytes(data)
        ref = describe_artifact(data, "application/json")
        self._count_output(ref, data)
        self.derived[ref["path"]] = data
        return ref

    def _count_output(self, ref, data):
        if ref["sha256"] not in self.output_hashes:
            self.output_bytes += len(data)
            self.output_hashes.add(ref["sha256"])
            if self.output_bytes > _MAX_OUTPUT_BYTES:
                raise ResearchError(_CODE, "The total scientific delivery exceeds its 512 MiB output bound")

    def _project(self, ref, projection, data=None):
        data = self.artifacts.read(ref) if data is None else data
        if len(data) > _MAX_BYTES:
            raise ResearchError(_CODE, "Scientific source exceeds the projection byte bound")
        entries = []
        if projection["kind"] == "source_component":
            candidates = self.public.get(projection["source_sha256"], [])
            originals = [r for r, _, _, capture in candidates if capture is not None and capture.get("original") == r]
            if not originals:
                raise ResearchError(_PRIVATE, "An exact source component requires an established original capture")
            source_ref = originals[0]
            self._public_reference(source_ref, original_pdf=True)
            source = self.artifacts.read(source_ref)
            self._check_bytes(source)
            self._check_bytes(data)
            if (ref["media_type"] != "image/jpeg" or not source.startswith(b"%PDF-")
                    or projection["end"] > len(source) or source[projection["start"]:projection["end"]] != data):
                raise ResearchError(_CODE, "The JPEG must equal the exact declared byte span of its original PDF")
            from .html_visuals import validate_visual
            validate_visual(data, "image/jpeg")
            self.derived[ref["path"]] = data
            self._count_output(ref, data)
            return self._emit({"derivative": True, "original_sha256": ref["sha256"],
                "projection_kind": "source_component", "context": projection["context"],
                "source_sha256": source_ref["sha256"], "byte_span": {k: projection[k] for k in ("start", "end")},
                "artifact": ref, "locator_mapping": []}), []
        if projection["kind"] == "npy_array":
            from .numerical_projection import decode_npy, MAX_ELEMENTS
            self._check_bytes(data)
            array = decode_npy(data, remaining_elements=MAX_ELEMENTS - self.numerical_elements)
            self.numerical_elements += array["element_count"]
            return self._emit({"derivative": True, "original_sha256": ref["sha256"], "projection_kind": "npy_array",
                               "context": projection["context"], "array": array, "locator_mapping": []}), []
        if projection["kind"] == "archive_members":
            numerical = _all_numerical(projection["members"])
            members = _read_members(data, _MAX_NUMPY_MEMBERS if numerical else _MAX_MEMBERS)
            if len(members) > _MAX_MEMBERS and set(members) != {m["path"] for m in projection["members"]}:
                raise ResearchError("scientific_projection_incomplete", "The numerical projection must retain the complete archive population")
            for member in projection["members"]:
                content = members.get(member["path"])
                if content is None or hashlib.sha256(content).hexdigest() != member["sha256"]:
                    raise ResearchError(_CODE, "An archive projection must retain exact declared member bytes")
                member_ref = describe_artifact(content, member["media_type"])
                if member_ref["sha256"] in self.private:
                    raise ResearchError(_PRIVATE, "The required archive member has an internal role")
                projected, _ = self._project(member_ref, member["projection"], content)
                entries.append({"member": member["path"], "original_sha256": member["sha256"], "artifact": projected})
            mapping = []
        else:
            # Use the same source locator validator on an immutable byte view.
            class Bytes:
                def read(self, reference):
                    if reference != ref:
                        raise ResearchError(_CODE, "A projection cannot read another artifact implicitly")
                    return data
            mapping = []
            selected = {digest(l): l for l in projection["locators"]}
            if not set(self.required.get(ref["sha256"], {})) <= set(selected):
                raise ResearchError("scientific_projection_incomplete", "Retain every required scientific locator in the typed derivative")
            for index, locator in enumerate(selected.values()):
                value = read_locator(Bytes(), ref, locator)
                self._public_nested(value)
                # Inspect actual preserved values. A nested private descriptor
                # fails rather than being copied through a JSON value.
                value = self.walk(value)
                entries.append({"original_locator": locator, "value": value})
                mapping.append({"original_locator": locator, "derived_pointer": "/entries/" + str(index) + "/value"})
        content = {"derivative": True, "original_sha256": ref["sha256"], "projection_kind": projection["kind"],
                   "context": projection["context"], "entries": entries, "locator_mapping": mapping}
        return self._emit(content), mapping

    def reference(self, ref):
        identifier = ref["sha256"]
        if identifier in self.private:
            raise ResearchError(_PRIVATE, "A required scientific reference has an internal private role", {"sha256": identifier})
        if identifier in self.mapped:
            return self.mapped[identifier]
        if identifier in self.visiting:
            raise ResearchError(_CODE, "Scientific artifact references must not form a recursive byte closure")
        self.visiting.add(identifier)
        try:
            data = self.artifacts.read(ref)
            if identifier in self.generated:
                mapped = self._emit({"derivative": True, "original_sha256": identifier,
                                     "projection_kind": "service_record", "value": self.generated[identifier]})
            elif not data:
                mapped = self._emit({"derivative": True, "original_sha256": identifier,
                                     "projection_kind": "empty_observation", "value": ""})
            elif identifier in self.public or identifier in self.exact_files:
                # Exact manuscript/source bytes retain their identity. JSON or
                # archives cannot smuggle nested internal references through it.
                self._check_bytes(data)
                if identifier in self.public:
                    self._public_reference(ref)
                is_json, structured = scientific_json(data, ref["media_type"])
                if is_json:
                    self._public_nested(structured)
                    self.walk(structured)
                if zipfile.is_zipfile(io.BytesIO(data)) or ref["media_type"] in ("application/x-tar", "application/zip"):
                    raise ResearchError(_PRIVATE, "Authored archives require an explicit typed member projection")
                mapped = ref
                self._count_output(ref, data)
            else:
                declaration = self.declarations.get(identifier)
                if declaration is None or declaration["disposition"] != "project":
                    raise ResearchError(_PRIVATE, "A required authored artifact needs a checked typed scientific projection", {"sha256": identifier})
                mapped, _ = self._project(ref, declaration["projection"])
            self.mapped[identifier] = mapped
            return mapped
        finally:
            self.visiting.remove(identifier)

    def _read_derived_locator_mapping(self, ref):
        validate_reference(ref)
        key = tuple(ref[field] for field in ("path", "sha256", "size", "media_type"))
        data = self.derived.get(ref["path"])
        if not isinstance(data, bytes):
            raise ResearchError("artifact_corrupt", "A derived mapping requires its immutable generated bytes")
        cached = self._parsed_locator_mappings.get(key)
        if cached is None or cached[0] is not data:
            if len(data) != ref["size"] or hashlib.sha256(data).hexdigest() != ref["sha256"]:
                raise ResearchError("artifact_corrupt", "Derived mapping bytes must match their exact descriptor")
            mapping = strict_json(data).get("locator_mapping", [])
            cached = (data, mapping)
            self._parsed_locator_mappings[key] = cached
        return cached[1]

    def walk(self, value, depth=0):
        if depth > _MAX_DEPTH:
            raise ResearchError(_CODE, "Scientific delivery JSON nesting exceeds its bound")
        if isinstance(value, dict):
            if _REF_KEYS <= value.keys():
                if value["path"] in self.derived:
                    return value
                return self.reference(value)
            if isinstance(value.get("artifact"), dict) and isinstance(value.get("locator"), dict):
                original, locator = value["artifact"], value["locator"]
                result = {k: self.walk(v, depth + 1) for k, v in value.items() if k not in ("artifact", "locator")}
                mapped = self.reference(original)
                result.update(artifact=mapped, locator=locator)
                if mapped != original:
                    found = next((item for item in self._read_derived_locator_mapping(mapped) if item["original_locator"] == locator), None)
                    if found is None:
                        raise ResearchError("scientific_projection_incomplete", "A required scientific locator has no derivative mapping")
                    exact_value = read_locator(self.artifacts, original, locator)
                    result.update(original_sha256=original["sha256"], original_locator=locator, derivative=True,
                                  locator={"kind": "json", "pointer": found["derived_pointer"], "value": exact_value})
                return result
            return {self.walk(key, depth + 1): self.walk(child, depth + 1) for key, child in value.items()}
        if isinstance(value, list):
            return [self.walk(child, depth + 1) for child in value]
        if isinstance(value, str):
            self._check_bytes(value.encode())
            # Preserve the exact string while inspecting every decoded level.
            try:
                is_json, nested = scientific_json(value.encode(), encoded_string=True)
            except ResearchError as error:
                if error.code != "invalid_json":
                    raise
                return value
            if is_json:
                if _references(nested):
                    raise ResearchError(_PRIVATE, "Encoded nested artifact references require a typed scientific value")
                self.walk(nested, depth + 1)
        return value


def project_delivery(records, artifacts, contract, manifest, *, manuscript_files=(), corrective=False):
    projector = ScientificDelivery(records, artifacts, contract, manifest,
                                   manuscript_files=manuscript_files, corrective=corrective)
    projected = projector.walk(manifest)
    manifest_bytes = (json.dumps(projected, ensure_ascii=False, indent=2) + "\n").encode()
    projector._check_bytes(manifest_bytes, artifact=False)
    projector._count_output(describe_artifact(manifest_bytes, "application/json"), manifest_bytes)
    return projected, projector.derived
