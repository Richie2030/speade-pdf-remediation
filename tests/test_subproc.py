"""Tests for the hidden-console subprocess wrapper (src/speade/subproc.py).

The wrapper exists for one reason: the desktop exe is a windowed app, and on
Windows every engine call (tesseract per PAGE, opendataloader-pdf, verapdf)
otherwise flashes up its own terminal window. These tests pin the contract:
CREATE_NO_WINDOW is injected on Windows, nothing is injected elsewhere, and an
explicit caller value is never overridden.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from speade import subproc


def _recorder(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return seen


def test_run_forwards_command_and_kwargs(monkeypatch):
    seen = _recorder(monkeypatch)

    proc = subproc.run(["engine", "arg"], capture_output=True, text=True, timeout=9)

    assert proc.returncode == 0
    assert seen["cmd"] == ["engine", "arg"]
    assert seen["kwargs"]["capture_output"] is True
    assert seen["kwargs"]["timeout"] == 9


def test_run_hides_the_console_window_on_windows_only(monkeypatch):
    seen = _recorder(monkeypatch)

    subproc.run(["engine"])

    if sys.platform == "win32":
        assert seen["kwargs"]["creationflags"] == subprocess.CREATE_NO_WINDOW
    else:
        assert "creationflags" not in seen["kwargs"]


@pytest.mark.skipif(sys.platform != "win32", reason="creationflags are Windows-only")
def test_run_never_overrides_explicit_creationflags(monkeypatch):
    seen = _recorder(monkeypatch)

    subproc.run(["engine"], creationflags=0)

    assert seen["kwargs"]["creationflags"] == 0  # setdefault, not overwrite


def test_popen_hides_the_console_window_on_windows_only(monkeypatch):
    # the long-lived render worker spawns through popen(): same contract as
    # run(), or the flashing terminal returns with every worker start.
    seen = {}

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            seen["cmd"] = cmd
            seen["kwargs"] = kwargs

    monkeypatch.setattr(subprocess, "Popen", FakePopen)

    subproc.popen(["worker"])

    assert seen["cmd"] == ["worker"]
    if sys.platform == "win32":
        assert seen["kwargs"]["creationflags"] == subprocess.CREATE_NO_WINDOW
    else:
        assert "creationflags" not in seen["kwargs"]


def test_every_call_site_uses_the_wrapper():
    # pin the fix itself: a stage/validator reverting to bare subprocess.run
    # would reintroduce the flashing terminal windows with every test green.
    # (subprocess may still be IMPORTED for TimeoutExpired etc. -- only direct
    # process spawns are banned outside the wrapper.)
    src_root = Path(__file__).resolve().parents[1] / "src" / "speade"
    banned = ("subprocess.run(", "subprocess.Popen(", "subprocess.call(", "os.system(")
    offenders = [
        f"{path.relative_to(src_root)}: {pattern}"
        for path in sorted(src_root.rglob("*.py"))
        if path.name != "subproc.py"
        for pattern in banned
        if pattern in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"spawn processes via speade.subproc.run, not: {offenders}"
