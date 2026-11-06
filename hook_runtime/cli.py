"""Install the stable runtime protocol, publish releases and inspect bindings."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from .bundles import (collect_entries, discard_stage, freeze_release,
                      publish_release, verify_release)
from .policy import render_requirements
from .state import (RuntimeRefusal, initialize_session_state, lock_registry,
                    lock_sessions, read_binding, read_json, read_registry,
                    require_directory, select_session_state, sync_directory,
                    write_json)


def install_runtime(prefix):
    """Install v1 once; a later plugin publication never replaces its code."""
    prefix.mkdir(parents=True, exist_ok=True)
    require_directory(prefix)
    (prefix / "releases").mkdir(exist_ok=True)
    require_directory(prefix / "releases")
    (prefix / "state").mkdir(exist_ok=True)
    require_directory(prefix / "state")
    descriptor = os.open(prefix / "state/runtime.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    os.close(descriptor)
    stage = Path(tempfile.mkdtemp(prefix=".install-", dir=str(prefix)))
    try:
        package = stage / "hook_runtime"
        package.mkdir()
        for source in sorted(Path(__file__).parent.iterdir()):
            if source.suffix in (".py", ".json"):
                shutil.copyfile(source, package / source.name)
        shutil.copyfile(Path(__file__).parent / "dispatcher.py", stage / "dispatch.py")
        interpreter = str(Path(sys.executable).resolve())
        write_json(stage / "interpreter.json", interpreter)
        launcher = stage / "exactory-runtime"
        launcher.write_text(
            "#!" + interpreter + " -I\n"
            "from pathlib import Path\nimport sys\n"
            "sys.dont_write_bytecode = True\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
            "from hook_runtime.cli import main\n"
            "sys.exit(main())\n", encoding="utf-8")
        os.chmod(launcher, 0o755)
        freeze_release(stage)
        with lock_registry(prefix):
            try:
                (prefix / "v1").lstat()
            except FileNotFoundError:
                os.replace(stage, prefix / "v1")
                sync_directory(prefix)
            else:
                if collect_entries(stage) != collect_entries(prefix / "v1"):
                    raise RuntimeRefusal("Installed v1 differs; deploy a reviewed dispatcher protocol upgrade explicitly")
            try:
                read_registry(prefix)
            except FileNotFoundError:
                write_json(prefix / "state/registry.json", {"schema": 1, "approved": [], "default": None})
    finally:
        try:
            stage.lstat()
        except FileNotFoundError:
            pass
        else:
            discard_stage(stage)


def describe_status(prefix, state_dir=None):
    directory = select_session_state(prefix, state_dir)
    with lock_sessions(directory):
        registry = read_registry(prefix)
        releases = {}
        for digest in registry["approved"]:
            try:
                verify_release(prefix, digest)
            except (RuntimeRefusal, OSError) as error:
                releases[digest] = {"valid": False, "error": str(error)}
            else:
                releases[digest] = {"valid": True}
        bindings = []
        for path in sorted((directory / "sessions").glob("*.json")):
            binding = read_json(path)
            bindings.append(read_binding(directory, binding.get("session_id")))
        return {**registry, "releases": releases, "bindings": bindings,
                "session_state_dir": str(directory)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "publish", "status", "init-state"):
        command = commands.add_parser(name)
        command.add_argument("--prefix", type=Path, required=True)
        if name == "publish":
            command.add_argument("plugin_root", type=Path)
        if name in ("status", "init-state"):
            command.add_argument("--state-dir", type=Path)
    args = parser.parse_args()
    prefix = args.prefix.resolve()
    try:
        if args.command == "install":
            install_runtime(prefix)
            print(render_requirements(prefix, read_json(prefix / "v1/interpreter.json")), end="")
        elif args.command == "publish":
            print(publish_release(prefix, args.plugin_root))
        elif args.command == "init-state":
            directory = select_session_state(prefix, args.state_dir)
            initialize_session_state(directory)
            print(directory)
        else:
            print(json.dumps(describe_status(prefix, args.state_dir), indent=2))
        return 0
    except (RuntimeRefusal, OSError) as error:
        print("[exactory-runtime] " + str(error), file=sys.stderr)
        return 1
