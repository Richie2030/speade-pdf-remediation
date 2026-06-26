#!/usr/bin/env python
"""Fail if any banned in-process import appears in the source tree.

Enforces licence rule L2: PyMuPDF (`import fitz`) is AGPL-3.0; importing it
in-process would force this whole codebase to AGPL and kill both the permissive
release and the resale futures. Copyleft tools run as arms-length subprocesses
instead -- never imported. This is the CI guard that keeps that rule honest.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

BANNED = {"fitz", "pymupdf"}
SCAN_DIRS = ("src", "tests", "scripts")


def banned_in(path: Path) -> list[str]:
    hits: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0].lower() in BANNED:
                    hits.append(f"{path}:{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0].lower()
            if mod in BANNED:
                hits.append(f"{path}:{node.lineno}: from {node.module} import ...")
    return hits


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for d in SCAN_DIRS:
        for py in sorted((root / d).rglob("*.py")):
            offenders.extend(banned_in(py))

    if offenders:
        print("BANNED IMPORTS FOUND (in-process AGPL -- see licence rule L2):")
        for o in offenders:
            print("  " + o)
        return 1

    print("OK: no banned imports.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
