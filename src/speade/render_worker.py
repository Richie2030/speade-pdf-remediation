"""Sacrificial page renderer: pdfium in a disposable child process.

The recorded desktop crashes were native faults inside pdfium.dll (heap
corruption / CHECK-aborts) triggered by rendering -- most plausibly a
pathological input PDF. No locking discipline or except-clause survives a
native abort in-process, so the preview render runs at arm's length instead:
a small worker process owns pdfium, and when it dies the review session loses
ONE page image, not the reviewer's work. The worker is restarted on demand; a
page that kills it twice is blacklisted and reported instead of retried.

Threading note (the pdfium serialization rule, speade.pdfium_lock): the worker
process is single-threaded and owns its own pdfium instance, so PDFIUM_LOCK is
deliberately NOT taken here -- the lock serializes THIS process's pdfium, and
this module's whole point is that the parent never touches pdfium for page
previews at all. Requests are serialized by PageRenderer's own mutex instead.

Packaging note: uses multiprocessing spawn, so the PyInstaller entry point
(speade.desktop.__main__) must call multiprocessing.freeze_support() first.
"""

from __future__ import annotations

import multiprocessing as mp
import queue as queue_mod
import threading
import time
from contextlib import suppress
from pathlib import Path

# Preview geometry (kept identical to the old in-process render): ~1400px wide
# is crisp for the tags view, capped at 2x for tiny pages.
_TARGET_WIDTH_PX = 1400
_MAX_SCALE = 2.0

# A healthy render is <3s; the first request also pays the child's interpreter
# start (a few seconds under PyInstaller). A hung worker past this is killed.
_RENDER_TIMEOUT_S = 60.0
# A native crash is detected in one poll tick, not at the full timeout.
_POLL_S = 0.2
# After this many worker deaths on the SAME page, stop sacrificing workers.
_MAX_CRASHES_PER_PAGE = 2


def _worker_main(requests: mp.Queue, responses: mp.Queue) -> None:
    """The child: render loop until the None sentinel. One document is kept
    open between requests (the review view walks one document's pages), keyed
    by (path, mtime, size) so an edited draft is re-read, and opened from
    BYTES so no file handle lingers to fight the edit-saves' atomic replace."""
    import io

    import pypdfium2 as pdfium

    doc = None
    doc_key: tuple | None = None
    while True:
        req = requests.get()
        if req is None:
            break
        try:
            path = Path(req["path"])
            stat = path.stat()
            key = (str(path), stat.st_mtime_ns, stat.st_size)
            if doc is None or key != doc_key:
                if doc is not None:
                    doc.close()
                # reset BEFORE the open: if it raises (garbage file), the stale
                # closed handle must not be mistaken for the cached document.
                doc, doc_key = None, None
                doc = pdfium.PdfDocument(path.read_bytes())
                doc_key = key
            pages = len(doc)
            index = int(req["index"])
            if not 0 <= index < pages:
                responses.put({"error": f"no page {index + 1}"})
                continue
            page = doc[index]
            try:
                width, height = page.get_size()
                scale = min(_TARGET_WIDTH_PX / max(width, 1), _MAX_SCALE)
                image = page.render(scale=scale).to_pil()
            finally:
                page.close()
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            responses.put({"png": buf.getvalue(), "width": width, "height": height, "pages": pages})
        except Exception as exc:  # a bad request/file is an answer, not a death
            responses.put({"error": f"page image unavailable: {str(exc)[:120]}"})


class PageRenderer:
    """Owns the worker process; restarts it when a render kills it."""

    def __init__(self) -> None:
        self._mutex = threading.Lock()  # one outstanding request at a time
        self._proc: mp.process.BaseProcess | None = None
        self._requests: mp.Queue | None = None
        self._responses: mp.Queue | None = None
        self._crashes: dict[tuple, int] = {}  # (path, mtime, index) -> deaths

    # ------------------------------------------------------------ lifecycle
    def _ensure_worker(self) -> None:
        if self._proc is not None and self._proc.is_alive():
            return
        ctx = mp.get_context("spawn")  # never fork: a forked pdfium is UB
        self._requests = ctx.Queue()
        self._responses = ctx.Queue()
        self._proc = ctx.Process(
            target=_worker_main,
            args=(self._requests, self._responses),
            name="speade-render",
            daemon=True,  # never blocks interpreter exit; close() is the polite path
        )
        self._proc.start()

    def _reap(self) -> None:
        """Kill and forget the worker (fresh queues next time: no stale replies)."""
        if self._proc is not None:
            with suppress(Exception):
                self._proc.kill()
                self._proc.join(timeout=5)
        self._proc = None
        self._requests = None
        self._responses = None

    def close(self) -> None:
        """Polite shutdown (app exit): sentinel, short join, then the axe."""
        with self._mutex:
            if self._proc is None:
                return
            with suppress(Exception):
                self._requests.put(None)
                self._proc.join(timeout=5)
            self._reap()

    # -------------------------------------------------------------- render
    def render(self, pdf: Path, index: int) -> dict:
        """One page as PNG bytes: {"png", "width", "height", "pages"} or
        {"error": reason}. A worker death costs this request only."""
        pdf = Path(pdf)
        try:
            stat = pdf.stat()
        except OSError:
            return {"error": f"not found: {pdf.name}"}
        page_key = (str(pdf), stat.st_mtime_ns, index)
        with self._mutex:
            if self._crashes.get(page_key, 0) >= _MAX_CRASHES_PER_PAGE:
                return {
                    "error": "this page crashes the renderer - the document may be "
                    "damaged; open it in a PDF viewer instead"
                }
            try:
                self._ensure_worker()
                self._requests.put({"path": str(pdf), "index": int(index)})
            except Exception as exc:  # spawn refused (exotic environment)
                self._reap()
                return {"error": f"page renderer unavailable: {str(exc)[:120]}"}

            deadline = time.monotonic() + _RENDER_TIMEOUT_S
            while time.monotonic() < deadline:
                try:
                    return self._responses.get(timeout=_POLL_S)
                except queue_mod.Empty:
                    if not self._proc.is_alive():
                        # the sacrifice happened: a native fault killed the
                        # child. Log the page, restart lazily on the next call.
                        with suppress(queue_mod.Empty):  # a reply that raced death
                            return self._responses.get_nowait()
                        self._crashes[page_key] = self._crashes.get(page_key, 0) + 1
                        self._reap()
                        return {
                            "error": "page render crashed and was restarted - "
                            "try again, or open the document in a PDF viewer"
                        }
            self._crashes[page_key] = self._crashes.get(page_key, 0) + 1
            self._reap()
            return {"error": "page render timed out - the renderer was restarted"}
