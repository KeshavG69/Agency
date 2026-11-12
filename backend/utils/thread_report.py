"""What threads are alive in the container, and who they are: to find what keeps taking them.

Production fails with "can't start new thread" after running for a day or more, while a fresh
deploy survives bursts and a full proposal write gives every thread back (measured: 11 -> 12 ->
10). So something grows with uptime. This report shows, for the container as a whole and for this
process, how many threads exist, grouped by name, each group with what one of its threads is doing
right now. Read it a few times over a day: the group that keeps growing is the leak.

It reads only /proc, /sys and the interpreter's own tables: no database, no network and no new
thread, so it still answers when the container has no threads left.
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
import traceback
from collections import defaultdict

_STARTED = time.time()


def _read(path: str) -> str | None:
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


def _field(status: str, name: str) -> str | None:
    m = re.search(rf"^{name}:\s*(.+)$", status, re.M)
    return m.group(1).strip() if m else None


def _group(name: str) -> str:
    """'ThreadPoolExecutor-3_12' and 'ThreadPoolExecutor-7_0' are the same kind of thread."""
    return re.sub(r"(?:[-_ ]?\d+)+$", "", name or "") or "?"


def _processes() -> list[dict]:
    """Every process in the container (the API, the Celery worker and beat share one) with its
    thread count. Empty off Linux."""
    out = []
    for pid in os.listdir("/proc") if os.path.isdir("/proc") else []:
        if not pid.isdigit():
            continue
        status = _read(f"/proc/{pid}/status") or ""
        threads = _field(status, "Threads")
        if not threads:
            continue
        cmd = (_read(f"/proc/{pid}/cmdline") or "").replace("\x00", " ").strip()
        out.append({"pid": int(pid), "name": _field(status, "Name") or "", "threads": int(threads),
                    "rss_mb": round(int((_field(status, "VmRSS") or "0 kB").split()[0]) / 1024),
                    "cmd": cmd[:160]})
    return sorted(out, key=lambda p: -p["threads"])


def _container_limit() -> dict:
    """The cap the error is about: the cgroup's task limit (v2, then v1) and the process limit."""
    current = _read("/sys/fs/cgroup/pids.current") or _read("/sys/fs/cgroup/pids/pids.current")
    maximum = _read("/sys/fs/cgroup/pids.max") or _read("/sys/fs/cgroup/pids/pids.max")
    limits = _read("/proc/self/limits") or ""
    nproc = re.search(r"^Max processes\s+(\S+)\s+(\S+)", limits, re.M)
    return {"tasks_now": current, "tasks_max": maximum,
            "max_processes": nproc.group(1) if nproc else None,
            "memory_now_mb": _mb(_read("/sys/fs/cgroup/memory.current")),
            "memory_max_mb": _mb(_read("/sys/fs/cgroup/memory.max"))}


def _mb(raw: str | None) -> int | str | None:
    return round(int(raw) / 1024 / 1024) if raw and raw.isdigit() else raw


def report() -> dict:
    frames = sys._current_frames()
    groups: dict[str, dict] = defaultdict(lambda: {"count": 0, "doing": None})
    for t in threading.enumerate():
        g = groups[_group(t.name)]
        g["count"] += 1
        if g["doing"] is None and t.ident in frames:
            stack = traceback.extract_stack(frames[t.ident])[-4:]
            g["doing"] = [f"{os.path.basename(f.filename)}:{f.lineno} {f.name}" for f in stack]
    status = _read("/proc/self/status") or ""
    return {
        "uptime_minutes": round((time.time() - _STARTED) / 60),
        "service_role": os.environ.get("SERVICE_ROLE", "all"),
        "container": _container_limit(),
        "processes": _processes(),
        "this_process": {
            "pid": os.getpid(),
            "os_threads": int(_field(status, "Threads") or 0) or None,
            "python_threads": threading.active_count(),
            "groups": dict(sorted(groups.items(), key=lambda kv: -kv[1]["count"])),
        },
    }
