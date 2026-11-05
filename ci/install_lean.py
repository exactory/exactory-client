#!/usr/bin/env python3
"""Install the smoke fixture's pinned Lean release with bounded retries."""

import argparse
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time


ELAN_INIT_URL = "https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh"
PINNED_RELEASE_PATTERN = r"leanprover/lean4:v4\.[0-9]+\.[0-9]+(?:-rc[0-9]+)?"


def check_installation(elan_home, toolchain_bin, deadline):
    """Check executable files and probe the pinned binaries without downloads."""
    for path in (elan_home / "bin" / "elan", elan_home / "bin" / "lake",
                 elan_home / "bin" / "lean", toolchain_bin / "lake",
                 toolchain_bin / "lean"):
        try:
            mode = path.stat().st_mode
        except FileNotFoundError:
            return "missing executable: " + str(path)
        if not stat.S_ISREG(mode) or not os.access(path, os.X_OK):
            return "not an executable file: " + str(path)
    for executable in (elan_home / "bin" / "elan", toolchain_bin / "lean",
                       toolchain_bin / "lake"):
        failure = run_command([str(executable), "--version"], deadline)
        if failure is not None:
            return failure
    return None


def run_command(command, deadline, environment=None):
    """Return a failure description, killing the process group on timeout."""
    label = shlex.join([str(argument) for argument in command])
    remaining_seconds = deadline - time.monotonic()
    if remaining_seconds <= 0:
        return label + " ran out of time"
    try:
        process = subprocess.Popen(command, start_new_session=True, env=environment)
    except OSError as error:
        return label + " could not start: " + str(error)
    with process:
        try:
            exit_status = process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            return label + " ran out of time"
    if exit_status != 0:
        return label + " exited with exit status " + str(exit_status)
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--attempts", type=int, default=4)
    parser.add_argument("--attempt-seconds", type=float, default=600)
    parser.add_argument("--wait-seconds", type=float, default=30)
    args = parser.parse_args()
    if args.attempts < 1:
        parser.error("--attempts must be positive")
    if not math.isfinite(args.attempt_seconds) or args.attempt_seconds <= 0:
        parser.error("--attempt-seconds must be finite and positive")
    if not math.isfinite(args.wait_seconds) or args.wait_seconds < 0:
        parser.error("--wait-seconds must be finite and nonnegative")

    toolchain_file = args.package / "lean-toolchain"
    toolchain = toolchain_file.read_text(encoding="utf-8").strip()
    if re.fullmatch(PINNED_RELEASE_PATTERN, toolchain) is None:
        print("%s: expected a pinned Lean 4 release, got %r" %
              (toolchain_file, toolchain), file=sys.stderr)
        return 2

    elan_home = (Path(os.environ["ELAN_HOME"]) if "ELAN_HOME" in os.environ
                 else Path.home() / ".elan")
    toolchain_directory = toolchain.replace("/", "--").replace(":", "---")
    toolchain_bin = elan_home / "toolchains" / toolchain_directory / "bin"
    if elan_home.exists():
        failure = check_installation(elan_home, toolchain_bin, time.monotonic() + 60)
        if failure is None:
            print("Using the existing %s in %s; nothing was downloaded" %
                  (toolchain, elan_home))
            return 0
        warning = "%s: %s; installing Lean again" % (elan_home, failure)
        warning = warning.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print("::warning::" + warning, flush=True)

    for attempt in range(1, args.attempts + 1):
        deadline = time.monotonic() + args.attempt_seconds
        for path in (toolchain_bin.parent, elan_home / "tmp"):
            try:
                mode = path.lstat().st_mode
            except FileNotFoundError:
                continue
            if stat.S_ISDIR(mode):
                shutil.rmtree(path)
            else:
                path.unlink()
        with tempfile.TemporaryDirectory(prefix="ci-lean-") as directory:
            environment = dict(os.environ, TMPDIR=directory)
            installer = Path(directory) / "elan-init.sh"
            install_toolchain = [str(elan_home / "bin" / "elan"),
                                 "toolchain", "install", toolchain]
            commands = [
                ["curl", "-sSfL", ELAN_INIT_URL, "-o", str(installer)],
                ["sh", str(installer), "-y", "--default-toolchain", "none",
                 "--no-modify-path"],
                install_toolchain,
            ]
            for command in commands:
                failure = run_command(command, deadline, environment)
                if failure is not None:
                    break
            if failure is None:
                failure = check_installation(elan_home, toolchain_bin, deadline)
                if failure is not None:
                    failure = (shlex.join(install_toolchain) +
                               " exited with exit status 0 but " + failure)
        if failure is None:
            print("Installed %s in %s on attempt %s" %
                  (toolchain, elan_home, attempt))
            return 0
        print("Attempt %s/%s failed: %s" % (attempt, args.attempts, failure),
              file=sys.stderr, flush=True)
        if attempt < args.attempts:
            time.sleep(args.wait_seconds * attempt)
    return 1


if __name__ == "__main__":
    sys.exit(main())
