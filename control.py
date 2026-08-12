#!/usr/bin/env python3
"""Start, stop and inspect the watcher process.

Same shape as the radar's controller, minus everything to do with profiles:
this tool never owns a browser profile, it attaches to one you drive.

    python control.py start | stop | restart | status
"""

import json
import os
import shutil
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil

PROJECT_DIR = Path(__file__).resolve().parent
WATCHER = PROJECT_DIR / "watch.py"
PID_FILE = PROJECT_DIR / ".watcher.pid"
LOG_FILE = PROJECT_DIR / "watcher.log"
IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

STOP_GRACE_SECONDS = 15  # monitor closes tabs and flushes state on shutdown


def _python() -> str:
    """The interpreter running us — works inside a venv without activation."""
    return sys.executable


def read_pid():
    """PID of our running monitor, or None. Cleans up a stale PID file."""
    try:
        pid = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        proc = psutil.Process(pid)
        # Guard against PID reuse: it must actually be our monitor.
        if "watch.py" in " ".join(proc.cmdline()):
            return pid
    except Exception:      # incl. bare PermissionError from the OS
        pass
    PID_FILE.unlink(missing_ok=True)
    return None


_FOREIGN_CACHE = {"at": -1e9, "value": []}
FOREIGN_CACHE_SECONDS = 30


def foreign_instances(max_age: float = FOREIGN_CACHE_SECONDS):
    """Other monitor.py processes outside this project.

    Two radars driving separate browsers from one IP doubles the traffic
    signature — which is what tripped Cloudflare before. Refuse to add to it.

    Cached: this walks every process on the machine (~10ms) and the panel asks
    for status every 2.5 seconds, all day. It also reads other users' command
    lines, which is where the macOS PermissionError came from. Nothing here
    changes second to second.
    """
    if time.time() - _FOREIGN_CACHE["at"] < max_age:
        return _FOREIGN_CACHE["value"]
    found = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        # Broad catch on purpose: reading another user's process can raise a
        # plain PermissionError from the OS (macOS KERN_PROCARGS2), not just
        # psutil's own exceptions — and one unreadable process must never take
        # down /api/status.
        try:
            argv = proc.info["cmdline"] or []
        except Exception:
            continue
        # Identify by the SCRIPT path, not the whole command line: our venv's
        # interpreter lives under PROJECT_DIR, so a substring test on the full
        # cmdline wrongly claims any process launched with it as ours.
        script = next((a for a in argv if a.endswith("watch.py")), None)
        if script is None or "watch" not in argv:
            continue
        try:
            is_ours = Path(script).resolve() == WATCHER
        except OSError:
            is_ours = False
        if not is_ours:
            found.append((proc.info["pid"], " ".join(argv)))
    _FOREIGN_CACHE.update(at=time.time(), value=found)
    return found


def start(force: bool = False) -> str:
    if (pid := read_pid()):
        return f"already running (pid {pid})"
    # max_age=0: starting is exactly when a stale answer would matter.
    if not force and (others := foreign_instances(max_age=0)):
        listed = "; ".join(f"pid {p}" for p, _ in others)
        return ("refusing to start: another radar is running outside this "
                f"project ({listed}). Two instances double the traffic to "
                "Whatnot from one IP. Stop the other one first, or use "
                "--force if you're sure.")
    if not (PROJECT_DIR / "config.json").exists():
        return "config.json missing — copy config.example.json and fill it in"

    cmd = [_python(), str(WATCHER), "watch"]

    kwargs = {}
    if IS_WINDOWS:
        # New process group so we can send CTRL_BREAK for a graceful stop.
        kwargs["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                   | subprocess.DETACHED_PROCESS)
    else:
        kwargs["start_new_session"] = True  # survive the parent shell closing

    with open(LOG_FILE, "a", buffering=1, encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                                cwd=str(PROJECT_DIR), **kwargs)
    PID_FILE.write_text(str(proc.pid))

    time.sleep(3)
    if read_pid() is None:
        return f"failed to start — see {LOG_FILE.name}"
    return f"started (pid {proc.pid})"


def _is_monitor(proc) -> bool:
    """True if this process is our monitor.py itself (not a wrapper)."""
    try:
        argv = proc.cmdline() or []
    except Exception:
        return False
    script = next((a for a in argv if a.endswith("watch.py")), None)
    try:
        return script is not None and Path(script).resolve() == WATCHER
    except OSError:
        return False


def stop() -> str:
    """Stop the radar and everything it started.

    Waiting on the monitor alone is not enough. Measured 2026-08-10: the
    monitor takes SIGTERM and exits cleanly, proc.wait() returns, we report
    "stopped" — and its browser child is still alive holding whatnot-profile/
    open, which is what makes the folder undeletable afterwards ("in use by
    another process"). Wrappers make it worse: xvfb-run on a headless Linux box
    sits between us and the monitor. So collect the whole tree up front, signal
    the monitor itself, and wait on all of it before declaring the radar
    stopped.
    """
    pid = read_pid()
    if pid is None:
        return "not running"
    try:
        proc = psutil.Process(pid)
        tree = proc.children(recursive=True)
    except psutil.Error:
        PID_FILE.unlink(missing_ok=True)
        return "not running"

    # Graceful first: the monitor closes tabs and flushes state on SIGTERM.
    for target in [proc] + [p for p in tree if _is_monitor(p)]:
        try:
            target.send_signal(signal.CTRL_BREAK_EVENT if IS_WINDOWS
                               else signal.SIGTERM)
        except (psutil.Error, OSError):
            pass

    _gone, alive = psutil.wait_procs([proc] + tree, timeout=STOP_GRACE_SECONDS)
    outcome = "stopped"
    if alive:
        # Name which part misbehaved: a lingering browser is routine, a monitor
        # that ignores SIGTERM is a bug worth seeing in the log.
        outcome = ("force-killed (the radar did not exit gracefully)"
                   if any(p.pid == proc.pid for p in alive)
                   else f"stopped ({len(alive)} leftover browser processes killed)")
        for survivor in alive:
            try:
                survivor.kill()
            except psutil.Error:
                pass
        psutil.wait_procs(alive, timeout=5)
    PID_FILE.unlink(missing_ok=True)
    return outcome


def restart() -> str:
    return f"{stop()}; {start()}"


def status(log_lines: int = 5) -> dict:
    pid = read_pid()
    info = {"running": pid is not None, "pid": pid,
            "log_file": str(LOG_FILE), "foreign": foreign_instances()}
    if pid:
        try:
            info["uptime_seconds"] = int(time.time() - psutil.Process(pid).create_time())
        except psutil.Error:
            pass
    if LOG_FILE.exists():
        info["recent_log"] = LOG_FILE.read_text(
            encoding="utf-8", errors="replace").splitlines()[-log_lines:]
    return info


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    force = "--force" in sys.argv
    if action == "start":
        print(start(force=force))
    elif action == "stop":
        print(stop())
    elif action == "restart":
        print(restart())
    elif action == "status":
        st = status()
        print("RUNNING" if st["running"] else "STOPPED",
              f"(pid {st['pid']})" if st["pid"] else "")
        for line in st.get("recent_log", []):
            print("  ", line)
        for pid, cmd in st["foreign"]:
            print(f"  ! other radar running: pid {pid} — {cmd[:80]}")
    elif action == "--json":
        print(json.dumps(status(), indent=2))
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
