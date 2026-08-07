"""Crash forensics (src/speade/diagnostics.py): the faulthandler side of the
evidence pair (setup-machine.ps1's WER LocalDumps is the native side)."""

from __future__ import annotations

import faulthandler

from speade import diagnostics


def test_enable_crash_log_writes_a_session_header(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "crash_log_dir", lambda: tmp_path / "speade")
    try:
        path = diagnostics.enable_crash_log("test-component")

        assert path == tmp_path / "speade" / "crash-tracebacks.log"
        text = path.read_text(encoding="utf-8")
        assert "test-component started" in text
        assert faulthandler.is_enabled()
    finally:
        faulthandler.disable()  # never leave the suite's stderr rewired


def test_enable_crash_log_failure_is_silent(tmp_path, monkeypatch):
    # diagnostics must never stop the app from starting: an unwritable home
    # returns None, no exception.
    def broken() -> object:
        raise OSError("read-only profile")

    monkeypatch.setattr(diagnostics, "crash_log_dir", broken)

    assert diagnostics.enable_crash_log("test-component") is None
