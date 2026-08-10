"""Error log: keep evidence of why the app died, for a machine you cannot visit.

The review client is a WINDOWED app (PyInstaller `console=False`), which means
it has no stderr: an unhandled Python error would otherwise vanish with the
window and leave the Student Partner with nothing to report. Everything that
can explain a crash is funnelled into ONE per-user text file:

  * `faulthandler` -- native faults (the recorded crashes were inside
    pdfium.dll, where no Python except-clause ever fires) write every thread's
    Python stack at the moment of the fault;
  * `sys.excepthook` / `threading.excepthook` -- unhandled Python errors on the
    main thread AND on the bridge/worker threads, with full tracebacks;
  * the page-render child's stderr, so a poison-PDF death says what pdfium
    complained about;
  * `log_line()` -- anything the app wants to record on the way down.

Pairs with the WER LocalDumps registration in scripts/setup-machine.ps1 (the
native half: a minidump naming the faulting module).

The log carries no document content -- file paths at most -- and lives outside
the workspace, so it never ships with a delivery or lands in the outbox. Every
function here is best-effort: diagnostics must never stop the app from
starting, so failures are swallowed and the app runs exactly as before.
"""

from __future__ import annotations

import faulthandler
import os
import sys
import threading
import traceback
from datetime import UTC, datetime
from pathlib import Path

# faulthandler holds the raw fd, and the subprocess renderer inherits it: the
# file object must outlive the process, so it is kept at module scope.
_handle = None

LOG_NAME = "error-log.txt"


def crash_log_dir() -> Path:
    """Per-user, per-OS home for the error log (outside the workspace)."""
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "speade"
    return Path.home() / ".speade"


def error_log_path() -> Path:
    """Where the error log lives (whether or not it has been written yet)."""
    return crash_log_dir() / LOG_NAME


def log_handle():
    """The open log file, or None when logging could not be started."""
    return _handle


def log_line(text: str) -> None:
    """Append one timestamped line (best-effort, never raises)."""
    if _handle is None:
        return
    try:
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        _handle.write(f"[{stamp}] {text}\n")
        _handle.flush()
    except Exception:
        pass


def _log_exception(kind: str, exc_type, exc_value, exc_tb) -> None:
    log_line(f"UNHANDLED ERROR ({kind}):")
    if _handle is None:
        return
    try:
        traceback.print_exception(exc_type, exc_value, exc_tb, file=_handle)
        _handle.flush()
    except Exception:
        pass


def enable_crash_log(component: str) -> Path | None:
    """Start the error log for this process and capture everything into it:
    native faults, unhandled errors on any thread, and explicit log lines.
    Returns the log path, or None if logging could not be started."""
    global _handle
    try:
        log_dir = crash_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / LOG_NAME
        _handle = path.open("a", encoding="utf-8")
        started = datetime.now(UTC).isoformat(timespec="seconds")
        _handle.write(f"\n--- {component} started {started} (pid {os.getpid()}) ---\n")
        _handle.flush()
        faulthandler.enable(file=_handle, all_threads=True)
    except Exception:
        _handle = None
        return None

    # A windowed app has no stderr: without these hooks an unhandled error
    # closes the window and leaves no trace at all for the person reporting it.
    previous_hook = sys.excepthook

    def hook(exc_type, exc_value, exc_tb):
        _log_exception("main thread", exc_type, exc_value, exc_tb)
        previous_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = hook

    def thread_hook(args):
        # pywebview runs every bridge call on its own thread, and the batch
        # runs on another: an error there is invisible without this.
        _log_exception(
            f"thread {args.thread.name if args.thread else '?'}",
            args.exc_type,
            args.exc_value,
            args.exc_traceback,
        )

    threading.excepthook = thread_hook
    return path
