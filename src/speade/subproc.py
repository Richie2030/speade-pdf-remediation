"""Shared subprocess runner for the arms-length engine calls (rule L2).

Every engine this pipeline shells out to (Tesseract, OpenDataLoader, veraPDF,
docker) is a console program. The desktop client is a WINDOWED app (PyInstaller
`console=False`), and on Windows a windowed parent spawning a console child
flashes a terminal window up for every call -- one per OCR'd page at worst.
CREATE_NO_WINDOW suppresses those windows without affecting stdout/stderr
capture; on other platforms this is exactly `subprocess.run`.
"""

from __future__ import annotations

import subprocess
import sys


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """`subprocess.run` with the child's console window hidden on Windows."""
    if sys.platform == "win32":
        kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
    return subprocess.run(cmd, **kwargs)


def popen(cmd: list[str], **kwargs) -> subprocess.Popen:
    """`subprocess.Popen` with the same hidden-console default, for the one
    long-lived child (the sacrificial page renderer) that `run` cannot model."""
    if sys.platform == "win32":
        kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
    return subprocess.Popen(cmd, **kwargs)
