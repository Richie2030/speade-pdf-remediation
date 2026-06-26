import subprocess
import sys
from pathlib import Path


def test_no_banned_imports():
    """The repo must contain no in-process AGPL imports (PyMuPDF / fitz)."""
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "check_banned_imports.py")],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
