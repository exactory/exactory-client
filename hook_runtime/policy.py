"""Render managed Codex policy from the generated shared registrations."""
import json
from pathlib import Path
import shlex


def build_managed_config(prefix, interpreter):
    config = json.loads((Path(__file__).parent / "registrations.json").read_text(encoding="utf-8"))
    for groups in config["hooks"].values():
        for group in groups:
            for handler in group["hooks"]:
                substitutions = {"@PYTHON@": str(Path(interpreter).resolve()),
                                 "@DISPATCHER@": str(Path(prefix).resolve() / "v1/dispatch.py")}
                handler["command"] = shlex.join([
                    substitutions.get(part, part) for part in shlex.split(handler["command"])])
    return config


def render_requirements(prefix, interpreter):
    config = build_managed_config(prefix, interpreter)
    lines = ["[features]", "hooks = true", "", "[hooks]",
             "managed_dir = " + json.dumps(str(Path(prefix).resolve()), ensure_ascii=False)]
    for event, groups in config["hooks"].items():
        for group in groups:
            lines.extend(["", "[[hooks." + event + "]]"])
            if "matcher" in group:
                lines.append("matcher = " + json.dumps(group["matcher"]))
            for handler in group["hooks"]:
                lines.extend(["", "[[hooks." + event + ".hooks]]"])
                for key, value in handler.items():
                    lines.append(key + " = " + json.dumps(value, ensure_ascii=False))
    return "\n".join(lines) + "\n"
