"""The pdfium serialization rule (src/speade/pdfium_lock.py).

PDFium is not thread-safe, and the app touches it from bridge threads, the
batch worker, and the web server at once. Live-tested failure when this rule
is broken: perfectly good documents start failing to load ("PDFium: Data
format error") until the app restarts. The scan pins the rule: any module
that imports pypdfium2 must reference PDFIUM_LOCK.
"""

from __future__ import annotations

import threading
from pathlib import Path

from speade.pdfium_lock import PDFIUM_LOCK


def test_every_pdfium_call_site_references_the_lock():
    src_root = Path(__file__).resolve().parents[1] / "src" / "speade"
    offenders = []
    for path in sorted(src_root.rglob("*.py")):
        if path.name == "pdfium_lock.py":
            continue
        source = path.read_text(encoding="utf-8")
        if "pypdfium2" in source and "PDFIUM_LOCK" not in source:
            offenders.append(str(path.relative_to(src_root)))
    assert offenders == [], f"serialize pdfium via speade.pdfium_lock in: {offenders}"


def test_the_lock_is_a_real_process_wide_lock():
    assert isinstance(PDFIUM_LOCK, type(threading.Lock()))
