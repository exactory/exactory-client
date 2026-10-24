"""Kernel process identities and a launch gate that closes with its worker."""

import json
import os
import signal
import sys
import time


def read_probe_clock():
    # macOS Python 3.9's time.monotonic() has a per-process origin.
    return time.clock_gettime(time.CLOCK_MONOTONIC)


def find_probe_process_identity(pid):
    """A PID alone cannot authorize signalling: compare its kernel start identity too."""
    import errno
    if sys.platform == "darwin":
        import ctypes
        class ProcessInfo(ctypes.Structure):
            # Darwin's proc_bsdinfo (sys/proc_info.h, PROC_PIDTBSDINFO).
            _fields_ = [(name, ctypes.c_uint32) for name in (
                "flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid", "rgid", "svuid", "svgid", "reserved")]
            _fields_ += [("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32)]
            _fields_ += [(name, ctypes.c_uint32) for name in ("nfiles", "pgid", "pjobc", "tdev", "tpgid", "nice")]
            _fields_ += [("start_seconds", ctypes.c_uint64), ("start_microseconds", ctypes.c_uint64)]
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        library.proc_pidinfo.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int)
        library.proc_pidinfo.restype = ctypes.c_int
        info = ProcessInfo()
        ctypes.set_errno(0)
        size = library.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
        if size != ctypes.sizeof(info):
            error = ctypes.get_errno()
            if error in (errno.ESRCH, errno.ENOENT):
                return None
            raise OSError(error or errno.EIO, "Cannot identify the probe process", pid)
        if info.status == 5:  # SZOMB: a zombie no longer executes or retains open descriptors.
            return None
        return {"pid": pid, "process_group": info.pgid, "start_identity": [info.start_seconds, info.start_microseconds]}
    if sys.platform.startswith("linux"):
        from pathlib import Path
        try:
            data = (Path("/proc") / str(pid) / "stat").read_text()
        except (FileNotFoundError, ProcessLookupError):
            # ENOENT once the entry is gone; ESRCH when the process is reaped between the open and the read.
            return None
        fields = data[data.rfind(")") + 2:].split()
        if fields[0] == "Z":
            return None
        return {"pid": pid, "process_group": int(fields[2]), "start_identity": [int(fields[19])]}
    raise OSError(errno.ENOSYS, "Probe process identities require Darwin or Linux")


def find_probe_group_members(group):
    if sys.platform == "darwin":
        import ctypes
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        library.proc_listpgrppids.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_int)
        library.proc_listpgrppids.restype = ctypes.c_int
        capacity = 32
        while True:
            buffer = (ctypes.c_int * capacity)()
            # xnu's libsyscall/wrappers/libproc/libproc.c returns 0 and sets errno on failure.
            ctypes.set_errno(0)
            count = library.proc_listpgrppids(group, buffer, ctypes.sizeof(buffer))
            error = ctypes.get_errno()
            if count == 0 and error:
                raise OSError(error, "Cannot enumerate the probe process group", group)
            if count < capacity:
                candidates = list(buffer[:count])
                break
            capacity *= 2
    elif sys.platform.startswith("linux"):
        from pathlib import Path
        candidates = [int(path.name) for path in Path("/proc").iterdir() if path.name.isdigit()]
    else:
        raise OSError("Probe process groups require Darwin or Linux")
    members = []
    for pid in candidates:
        identity = find_probe_process_identity(pid)
        if identity is not None and identity["process_group"] == group:
            members.append(identity)
    return members


def enforce_probe_deadline(root, directory, supervision):
    """An independent session keeps the frozen bound if the normal worker disappears."""
    from research_harness.workspace import json_projection
    program, worker = supervision["program"], supervision["worker"]
    if find_probe_process_identity(program["pid"]) != program:
        return
    identity = find_probe_process_identity(os.getpid())
    json_projection(root, directory + "/watchdog.json", {"config_sha256": supervision["config_sha256"], "identity": identity})
    while find_probe_process_identity(program["pid"]) == program:
        remaining = supervision["deadline"] - read_probe_clock()
        if remaining <= 0 and find_probe_process_identity(worker["pid"]) != worker:
            break
        time.sleep(min(0.02, remaining) if remaining > 0 else 0.02)
    else:
        return
    terminal = {key: supervision[key] for key in ("config_sha256", "program", "started", "deadline")}
    terminal.update(supervisor=identity, elapsed_seconds=read_probe_clock() - supervision["started"])
    json_projection(root, directory + "/supervisor-timeout.json", terminal)
    if find_probe_process_identity(program["pid"]) == program:
        try:
            os.killpg(program["process_group"], signal.SIGKILL)
        except ProcessLookupError:
            pass
    while find_probe_group_members(program["process_group"]):
        time.sleep(0.01)
    terminal.update(exit_status="timed_out", elapsed_seconds=read_probe_clock() - supervision["started"])
    json_projection(root, directory + "/supervisor-terminal.json", terminal)


def run_probe_program(descriptor, ready, owner, root, directory, config_sha256, argv):
    try:
        os.write(ready, b"1")
    finally:
        os.close(ready)
    try:
        blocks = []
        while True:
            block = os.read(descriptor, 4096)
            if not block:
                break
            blocks.append(block)
    finally:
        os.close(descriptor)
    if blocks:
        supervision = json.loads(b"".join(blocks))
        if supervision["config_sha256"] != config_sha256 or supervision["program"]["pid"] != os.getpid():
            raise ValueError("Probe launch gate differs from the frozen supervisor identity")
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        try:
            watchdog = os.fork()
            if watchdog == 0:
                try:
                    os.setsid()
                    os.close(owner)
                    with open(os.devnull, "rb+", buffering=0) as sink:
                        for descriptor in (0, 1, 2):
                            os.dup2(sink.fileno(), descriptor)
                    enforce_probe_deadline(root, directory, supervision)
                finally:
                    os._exit(0)
            pid = os.fork()
            if pid == 0:
                try:
                    os.execvpe(argv[0], argv, env)
                except OSError as error:
                    os.write(2, (str(error) + "\n").encode("utf-8"))
                    os._exit(127)
            _, status = os.waitpid(pid, 0)
            from research_harness.workspace import json_projection
            returncode = os.waitstatus_to_exitcode(status)
            json_projection(root, directory + "/program-exit.json", {"config_sha256": config_sha256, "returncode": returncode})
        finally:
            # Retain an identifiable group leader until cleanup, even if the script exits first or closes its descriptors.
            os.killpg(os.getpgrp(), signal.SIGKILL)


if __name__ == "__main__":
    run_probe_program(int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5], sys.argv[6], sys.argv[7:])
