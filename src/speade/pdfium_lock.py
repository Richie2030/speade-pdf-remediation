"""One process-wide lock for ALL pdfium (pypdfium2) calls.

PDFium is not thread-safe. The desktop bridge serves every js_api call on its
own thread, the batch worker renders OCR pages on another, and the tags view
requests page images concurrently while scrolling -- unserialised, those calls
corrupt pdfium's global state. Live-tested symptom: perfectly good documents
start failing to load ("PDFium: Data format error") until the app restarts.

Every call site that touches pypdfium2 wraps the pdfium work in

    with PDFIUM_LOCK:
        ...

Hold the lock around short operations (open, render one page, read geometry),
never around subprocess waits -- the OCR loop releases it while Tesseract runs
so the UI stays responsive during a batch. A scan test pins the rule: any
module importing pypdfium2 must reference this lock.
"""

from __future__ import annotations

import threading

PDFIUM_LOCK = threading.Lock()
