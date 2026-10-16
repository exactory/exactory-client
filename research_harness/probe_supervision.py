"""Kernel process identities and a launch gate that closes with its worker."""

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
        except FileNotFoundError:
            return None
        fields = data[data.rfind(")") + 2:].split()
        if fields[0] == "Z":
            return None
        return {"pid": pid, "process_group": int(fields[2]), "start_identity": [int(fields[19])]}
    raise OSError(errno.ENOSYS, "Probe process identities require Darwin or Linux")


def run_probe_program(descriptor, ready, owner, root, directory, config_sha256, argv):
    try:
        os.write(ready, b"1")
    finally:
        os.close(ready)
    try:
        released = os.read(descriptor, 1) == b"1"
    finally:
        os.close(descriptor)
    if released:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        try:
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
