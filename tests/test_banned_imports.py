"""Tests for the banned-import guard (scripts/check_banned_imports.py).

Turns the AGPL / PyMuPDF ban (rule L2) into a live assertion: the detector is
checked against synthetic snippets, and then enforced over the real source tree so
that an `import fitz` which ever lands makes the suite go red.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GUARD_PATH = _REPO_ROOT / "scripts" / "check_banned_imports.py"


def _load_guard():
    """Load the standalone guard script (scripts/ is not an importable package)."""
    spec = importlib.util.spec_from_file_location("check_banned_imports", _GUARD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load_guard()


@pytest.mark.parametrize(
    ("source", "expected_module"),
    [
        ("import fitz", "fitz"),
        ("import fitz as f", "fitz"),
        ("import pymupdf", "pymupdf"),
        ("from fitz import open", "fitz"),
        ("from pymupdf import Document", "pymupdf"),
        ("import fitz.utils", "fitz.utils"),  # submodule -> top-level fitz is banned
        ("from fitz.table import find", "fitz.table"),
    ],
)
def test_flags_banned_import(source: str, expected_module: str):
    hits = guard.scan_source(source, "example.py")
    assert [module for _, _, module in hits] == [expected_module]


@pytest.mark.parametrize(
    "source",
    [
        "import os",
        "from pathlib import Path",
        "import fitzgerald",  # whole-name match, not a substring of 'fitz'
        "from fitzgerald import thing",
        "x = 'import fitz'",  # a string literal, not an import statement
        "# import fitz",  # a comment
        '"""a docstring that mentions import fitz"""',
    ],
)
def test_ignores_non_banned_source(source: str):
    assert guard.scan_source(source, "example.py") == []


def test_reports_filename_and_line_number():
    hits = guard.scan_source("import os\nimport fitz\n", "sample.py")
    assert hits == [("sample.py", 2, "fitz")]


def test_source_tree_has_no_banned_imports():
    """The standing invariant: no in-process PyMuPDF/fitz anywhere in src/tests/scripts."""
    roots = [guard.REPO_ROOT / name for name in guard.SCAN_ROOTS]
    assert guard.find_violations(roots) == []


def test_main_exits_zero_on_the_clean_repo(capsys):
    assert guard.main() == 0
    assert "OK" in capsys.readouterr().out
