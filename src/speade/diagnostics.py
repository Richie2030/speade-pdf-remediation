"""Crash forensics: keep evidence when the process dies where Python cannot see.

The recorded desktop crashes were native faults inside pdfium.dll -- no Python
except-clause fires and nothing reaches the audit log. `faulthandler` writes
every thread's Python stack into a per-user log at the moment of a fatal
signal, so the NEXT crash on any machine names the Python frame that entered
the native call. Pairs with the WER LocalDumps registration in
scripts/setup-machine.ps1 (the native half of the same evidence).

The log carries no document content -- file paths at most -- and lives outside
the workspace, so it never ships with a delivery or lands in the outbox.
"""

from __future__ import annotations

import faulthandler
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# faulthandler holds the raw fd: the file object must outlive the process, so
# it is kept at module scope, never closed.
_handle = None


def crash_log_dir() -> Path:
    """Per-user, per-OS home for the crash log (outside the workspace)."""
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "speade"
    return Path.home() / ".speade"


def enable_crash_log(component: str) -> Path | None:
    """Turn on faulthandler-to-file for this process; returns the log path.

    Best-effort by design: diagnostics must never stop the app from starting,
    so any failure (read-only profile, odd ACLs) returns None and the app runs
    exactly as before -- just without the extra evidence.
    """
    global _handle
    try:
        log_dir = crash_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "crash-tracebacks.log"
        _handle = path.open("a", encoding="utf-8")
        started = datetime.now(UTC).isoformat(timespec="seconds")
        _handle.write(f"\n--- {component} started {started} (pid {os.getpid()}) ---\n")
        _handle.flush()
        faulthandler.enable(file=_handle, all_threads=True)
        return path
    except Exception:
        return None
