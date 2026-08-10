"""Crash forensics (src/speade/diagnostics.py): the faulthandler side of the
evidence pair (setup-machine.ps1's WER LocalDumps is the native side)."""

from __future__ import annotations

import faulthandler
import sys
import threading

import pytest

from speade import diagnostics


@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """Point the error log at tmp_path and put the process hooks back after --
    these tests install a real sys.excepthook."""
    monkeypatch.setattr(diagnostics, "crash_log_dir", lambda: tmp_path / "speade")
    main_hook, thread_hook = sys.excepthook, threading.excepthook
    try:
        yield diagnostics.enable_crash_log("test-component")
    finally:
        faulthandler.disable()  # never leave the suite's stderr rewired
        sys.excepthook, threading.excepthook = main_hook, thread_hook
        diagnostics._handle = None


def test_enable_crash_log_writes_a_session_header(isolated_log, tmp_path):
    assert isolated_log == tmp_path / "speade" / diagnostics.LOG_NAME
    assert "test-component started" in isolated_log.read_text(encoding="utf-8")
    assert faulthandler.is_enabled()


def test_unhandled_errors_are_logged_on_any_thread(isolated_log):
    # a windowed app has NO stderr: without these hooks an unhandled error
    # closes the window and leaves the reviewer nothing to report.
    try:
        raise ValueError("main thread boom")
    except ValueError:
        sys.excepthook(*sys.exc_info())

    def explode():
        raise RuntimeError("bridge thread boom")

    worker = threading.Thread(target=explode, name="speade-test-thread")
    worker.start()
    worker.join(timeout=5)

    text = isolated_log.read_text(encoding="utf-8")
    assert "UNHANDLED ERROR (main thread)" in text
    assert "main thread boom" in text
    assert "UNHANDLED ERROR (thread speade-test-thread)" in text
    assert "bridge thread boom" in text


def test_log_line_records_context_and_never_raises(isolated_log):
    diagnostics.log_line("page renderer died rendering page 3 of doc.pdf")
    assert "page renderer died rendering page 3" in isolated_log.read_text(encoding="utf-8")

    diagnostics._handle = None  # logging unavailable: still a no-op, not a crash
    diagnostics.log_line("swallowed")


def test_enable_crash_log_failure_is_silent(tmp_path, monkeypatch):
    # diagnostics must never stop the app from starting: an unwritable home
    # returns None, no exception.
    def broken() -> object:
        raise OSError("read-only profile")

    monkeypatch.setattr(diagnostics, "crash_log_dir", broken)

    assert diagnostics.enable_crash_log("test-component") is None
