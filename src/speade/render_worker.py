"""Sacrificial page renderer: pdfium in a disposable child process.

The recorded desktop crashes were native faults inside pdfium.dll (heap
corruption / CHECK-aborts) triggered by rendering -- most plausibly a
pathological input PDF. No locking discipline or except-clause survives a
native abort in-process, so the preview render runs at arm's length instead:
a small worker process owns pdfium, and when it dies the review session loses
ONE page image, not the reviewer's work. The worker is restarted on demand; a
page that kills it twice is blacklisted and reported instead of retried.

Plumbing: a plain subprocess speaking JSON lines over stdin/stdout (PNG bytes
travel base64, which the UI needs for its data: URI anyway). Deliberately NOT
multiprocessing: mp spawn always launches the console-subsystem python.exe --
a terminal window flashing up per worker start (reported live) -- and its
bootstrap hangs under pythonw. A plain Popen gets CREATE_NO_WINDOW like every
other engine call (see speade.subproc), and the child's death is detected the
instant its stdout pipe hits EOF. In the PyInstaller exe the child is the exe
itself re-invoked with --render-worker (diverted in speade.desktop.__main__).

Threading note (the pdfium serialization rule, speade.pdfium_lock): the worker
process is single-threaded and owns its own pdfium instance, so PDFIUM_LOCK is
deliberately NOT taken here -- the lock serializes THIS process's pdfium, and
this module's whole point is that the parent never touches pdfium for page
previews at all. Requests are serialized by PageRenderer's own mutex instead.
"""

from __future__ import annotations

import base64
import json
import queue as queue_mod
import subprocess  # for PIPE/DEVNULL only; spawning goes through speade.subproc
import sys
import threading
from contextlib import suppress
from pathlib import Path

from speade import subproc

# Preview geometry (kept identical to the old in-process render): ~1400px wide
# is crisp for the tags view, capped at 2x for tiny pages.
_TARGET_WIDTH_PX = 1400
_MAX_SCALE = 2.0

# A healthy render is <3s; the first request also pays the child's interpreter
# start. A hung worker past this is killed. (A CRASHED worker is noticed the
# moment its pipe closes -- this ceiling is only for genuine hangs.)
_RENDER_TIMEOUT_S = 60.0
# After this many worker deaths on the SAME page, stop sacrificing workers.
_MAX_CRASHES_PER_PAGE = 2


def worker_main_stdio() -> None:
    """The child: serve JSON-line render requests on stdin until EOF. One
    document is kept open between requests (the review view walks one
    document's pages), keyed by (path, mtime, size) so an edited draft is
    re-read, and opened from BYTES so no file handle lingers to fight the
    edit-saves' atomic replace."""
    import io

    import pypdfium2 as pdfium

    doc = None
    doc_key: tuple | None = None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
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
                reply: dict = {"error": f"no page {index + 1}"}
            else:
                page = doc[index]
                try:
                    width, height = page.get_size()
                    scale = min(_TARGET_WIDTH_PX / max(width, 1), _MAX_SCALE)
                    image = page.render(scale=scale).to_pil()
                finally:
                    page.close()
                buf = io.BytesIO()
                image.save(buf, format="PNG")
                reply = {
                    "png_b64": base64.b64encode(buf.getvalue()).decode("ascii"),
                    "width": width,
                    "height": height,
                    "pages": pages,
                }
        except Exception as exc:  # a bad request/file is an answer, not a death
            reply = {"error": f"page image unavailable: {str(exc)[:120]}"}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


def _worker_command() -> list[str]:
    """How to start the child: the exe re-invokes itself (diverted by
    speade.desktop.__main__); a normal install runs this module."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--render-worker"]
    return [sys.executable, "-m", "speade.render_worker"]


class PageRenderer:
    """Owns the worker process; restarts it when a render kills it."""

    def __init__(self) -> None:
        self._mutex = threading.Lock()  # one outstanding request at a time
        self._proc: subprocess.Popen | None = None
        self._replies: queue_mod.Queue | None = None  # reader thread; None = EOF
        self._crashes: dict[tuple, int] = {}  # (path, mtime, index) -> deaths

    # ------------------------------------------------------------ lifecycle
    def _ensure_worker(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        # speade.subproc hides the child's console window (the flashing
        # terminal reported live); the wrapper is also what the repo scan pins.
        self._proc = subproc.popen(
            _worker_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )
        replies: queue_mod.Queue = queue_mod.Queue()
        proc = self._proc

        def pump() -> None:
            for reply_line in proc.stdout:
                replies.put(reply_line)
            replies.put(None)  # EOF: the child is gone -- noticed immediately

        threading.Thread(target=pump, name="speade-render-reader", daemon=True).start()
        self._replies = replies

    def _reap(self) -> None:
        """Kill and forget the worker (a fresh one next time: no stale replies)."""
        if self._proc is not None:
            with suppress(Exception):
                self._proc.kill()
                self._proc.wait(timeout=5)
        self._proc = None
        self._replies = None

    def close(self) -> None:
        """Polite shutdown (app exit): stdin EOF ends the loop, then the axe."""
        with self._mutex:
            if self._proc is None:
                return
            with suppress(Exception):
                self._proc.stdin.close()
                self._proc.wait(timeout=5)
            self._reap()

    # -------------------------------------------------------------- render
    def render(self, pdf: Path, index: int) -> dict:
        """One page: {"png_b64", "width", "height", "pages"} or {"error":
        reason}. A worker death costs this request only."""
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
                self._proc.stdin.write(json.dumps({"path": str(pdf), "index": int(index)}) + "\n")
                self._proc.stdin.flush()
            except Exception as exc:  # spawn refused, or the pipe already broken
                self._reap()
                return {"error": f"page renderer unavailable: {str(exc)[:120]}"}
            try:
                reply_line = self._replies.get(timeout=_RENDER_TIMEOUT_S)
            except queue_mod.Empty:  # a genuine hang, not a crash
                self._crashes[page_key] = self._crashes.get(page_key, 0) + 1
                self._reap()
                return {"error": "page render timed out - the renderer was restarted"}
            if reply_line is None:
                # EOF mid-request: the sacrifice happened (a native fault
                # killed the child). Log the page; restart lazily on the next call.
                self._crashes[page_key] = self._crashes.get(page_key, 0) + 1
                self._reap()
                return {
                    "error": "page render crashed and was restarted - "
                    "try again, or open the document in a PDF viewer"
                }
            try:
                return json.loads(reply_line)
            except ValueError:
                self._reap()
                return {"error": "page renderer sent an unreadable reply - it was restarted"}


if __name__ == "__main__":
    worker_main_stdio()
