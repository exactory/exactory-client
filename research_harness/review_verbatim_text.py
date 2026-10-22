"""Lossless visible UTF-8 evidence blocks for an explicitly versioned route."""

import hashlib
import json

from .artifacts import describe_artifact
from .errors import ResearchError
from .operations import fields
from .workspace import strict_json

MARKER = "openness-verbatim-evidence-v1"
PREFIX = "EXACTORY VERBATIM EVIDENCE v1\n"
BLOCK_ENCODING = "verbatim-utf8-v1"
ERROR = "review_verbatim_evidence_invalid"


def require(condition, message):
    if not condition:
        raise ResearchError(ERROR, message)


def _canonical(packet):
    from .review_transport import PACKET_LABEL
    return PACKET_LABEL + json.dumps(packet, sort_keys=True, ensure_ascii=False)


def _mode(packet):
    if not isinstance(packet, dict):
        return None
    if set(packet) == {"allowed_control", "evidence_control", "evidence"} and all(isinstance(value, str) for value in packet.values()):
        return "probe"
    native = packet.get("native")
    context = native.get("scientific_context") if isinstance(native, dict) else None
    if not isinstance(context, dict) or "review_text_encoding" not in context:
        return None
    require(context["review_text_encoding"] == MARKER and packet.get("role") == "native_math"
            and native.get("protocol") == "native-math-review-v1" and native.get("kind") == "result"
            and isinstance(context.get("source_evidence"), list) and bool(context["source_evidence"]),
            "Verbatim framing requires the versioned native openness source-evidence contract")
    return "native"


def _check_body(row, body):
    reference = row.get("artifact")
    require(isinstance(reference, dict) and isinstance(body, str), "Each evidence block needs its exact artifact descriptor")
    data = body.encode("utf-8")
    require(type(reference.get("size")) is int and len(data) == reference["size"]
            and hashlib.sha256(data).hexdigest() == reference.get("sha256"),
            "A complete evidence body must equal its declared byte size and SHA-256")


def render(messages):
    """Return None for legacy input, otherwise prove exact reconstruction before use."""
    from .review_transport import PACKET_LABEL
    candidates = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, str) or not content.startswith(PACKET_LABEL):
            continue
        packet = strict_json(content[len(PACKET_LABEL):].encode("utf-8"))
        mode = _mode(packet)
        if mode is not None:
            candidates.append((packet, mode))
    if not candidates:
        return None
    require(len(messages) == 1 and len(candidates) == 1 and messages[0].get("role") == "user",
            "Verbatim framing accepts one canonical user packet without history")
    packet, mode = candidates[0]
    require(messages[0]["content"] == _canonical(packet), "Retain the exact canonical packet serialization")
    if mode == "probe":
        body = json.dumps(packet, sort_keys=True, ensure_ascii=False)
        projected = {"artifact": describe_artifact(body.encode("utf-8"), "application/json"),
                     "encoding": "utf-8", "content": body}
        entries = [projected]
    else:
        projected = packet
        entries = packet.get("evidence")
        require(isinstance(entries, list), "Native evidence must remain an explicit array")
    blocks = []
    for row in entries:
        require(isinstance(row, dict) and row.get("encoding") != BLOCK_ENCODING,
                "Only original evidence entries may be framed")
        body = row.get("content")
        # Explicit omitted or partial deliveries keep their existing representation and scope.
        if row.get("encoding") != "utf-8" or not body or row.get("delivery") is not None:
            continue
        _check_body(row, body)
        index = len(blocks)
        blocks.append("\nEVIDENCE %d %d %s\n%s\n" % (index, len(body), row["artifact"]["sha256"], body))
        row.update(encoding=BLOCK_ENCODING, content={"block": index})
    require(bool(blocks), "Verbatim framing requires at least one complete visible evidence body")
    header = {"mode": mode, "messages": [dict(messages[0], content=projected)],
              "message_sha256": hashlib.sha256(messages[0]["content"].encode("utf-8")).hexdigest()}
    rendered = PREFIX + json.dumps(header, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n" + "".join(blocks)
    require(restore(rendered) == messages, "Verbatim framing must reconstruct the exact original message")
    return rendered


def restore(rendered):
    """Read length-bound blocks, never scan inside source bodies for delimiters."""
    require(isinstance(rendered, str) and rendered.startswith(PREFIX), "Use the exact versioned evidence framing")
    boundary = rendered.find("\n", len(PREFIX))
    require(boundary >= 0, "The framing header must be complete")
    header = strict_json(rendered[len(PREFIX):boundary].encode("utf-8"))
    fields(header, ("mode", "messages", "message_sha256"), code=ERROR)
    messages = header["messages"]
    require(header["mode"] in {"native", "probe"} and isinstance(messages, list) and len(messages) == 1
            and isinstance(messages[0], dict) and messages[0].get("role") == "user",
            "The framing header must retain its one original user message")
    projected = messages[0].get("content")
    require(isinstance(projected, dict), "The framed packet must be a structured object")
    entries = [projected] if header["mode"] == "probe" else projected.get("evidence")
    require(isinstance(entries, list), "Retain every evidence descriptor in the framing header")
    cursor, index = boundary + 1, 0
    for row in entries:
        require(isinstance(row, dict), "Evidence descriptors must remain structured objects")
        if row.get("encoding") != BLOCK_ENCODING:
            continue
        require(row.get("content") == {"block": index}, "Evidence blocks must retain their exact order and references")
        require(rendered[cursor:cursor + 1] == "\n", "An evidence block header is missing")
        end = rendered.find("\n", cursor + 1)
        require(end >= 0, "An evidence block header is incomplete")
        parts = rendered[cursor + 1:end].split(" ")
        require(len(parts) == 4 and parts[:2] == ["EVIDENCE", str(index)]
                and len(parts[2]) <= len(str(len(rendered))) and parts[2].isascii() and parts[2].isdigit(),
                "An evidence block has an invalid identity or character length")
        count = int(parts[2])
        require(count > 0 and str(count) == parts[2] and end + 1 + count < len(rendered),
                "The evidence block must have its exact nonempty character extent")
        body = rendered[end + 1:end + 1 + count]
        _check_body(row, body)
        require(parts[3] == row["artifact"]["sha256"] and rendered[end + 1 + count] == "\n",
                "The evidence block must retain its declared hash and final separator")
        row.update(encoding="utf-8", content=body)
        cursor, index = end + 2 + count, index + 1
    require(index > 0 and cursor == len(rendered), "No evidence blocks or extra bytes may be omitted from reconstruction")
    packet = strict_json(projected["content"].encode("utf-8")) if header["mode"] == "probe" else projected
    require(_mode(packet) == header["mode"], "Reconstruction must retain the original packet contract")
    messages[0]["content"] = _canonical(packet)
    require(hashlib.sha256(messages[0]["content"].encode("utf-8")).hexdigest() == header["message_sha256"],
            "The reconstructed original message must retain its exact SHA-256")
    return messages
