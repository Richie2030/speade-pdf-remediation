"""CI guard -- fail if any banned in-process import (PyMuPDF / fitz, AGPL) appears
in the source tree (rule L2). Walk src/tests/scripts and inspect import statements.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from pathlib import Path

# PyMuPDF exposes both names; either in-process import is an AGPL violation (rule L2).
BANNED = {"fitz", "pymupdf"}

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = ("src", "tests", "scripts")


def scan_source(text: str, filename: str = "<src>") -> list[tuple[str, int, str]]:
    """Return (filename, lineno, module) for every banned import in `text`.

    Uses the AST (not a text match) so a banned name in a string or comment is
    ignored, while `import fitz`, `import fitz as x`, and `from fitz.utils import ...`
    are all caught -- the top-level module is what counts.
    """
    hits: list[tuple[str, int, str]] = []
    for node in ast.walk(ast.parse(text, filename=filename)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in BANNED:
                    hits.append((filename, node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            # `from . import x` has module=None; only absolute imports can be banned.
            if node.module and node.module.split(".")[0] in BANNED:
                hits.append((filename, node.lineno, node.module))
    return hits


def iter_py_files(roots: Iterable[Path]) -> Iterator[Path]:
    """Yield every .py file under each existing root, in sorted order."""
    for root in roots:
        if root.exists():
            yield from sorted(root.rglob("*.py"))


def find_violations(roots: Iterable[Path]) -> list[tuple[str, int, str]]:
    """Scan every .py file under `roots` and return all banned-import hits."""
    violations: list[tuple[str, int, str]] = []
    for path in iter_py_files(roots):
        violations.extend(scan_source(path.read_text(encoding="utf-8"), str(path)))
    return violations


def main() -> int:
    violations = find_violations(REPO_ROOT / name for name in SCAN_ROOTS)
    for filename, lineno, module in violations:
        print(f"BANNED import '{module}' at {filename}:{lineno} (rule L2: no in-process AGPL)")
    if violations:
        print(f"\n{len(violations)} banned import(s) found.")
        return 1
    print("check_banned_imports: OK (no in-process PyMuPDF/fitz).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
