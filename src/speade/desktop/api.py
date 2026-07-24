"""The js_api bridge: what the review UI's JavaScript can call.

One thin class over speade.service -- the same engine the CLI uses -- returning
plain JSON-safe dicts/lists (pywebview serialises them across the bridge). No
pipeline or gate logic lives here (that is service.py's job), and no pywebview
import is needed at module level, so this file is unit-testable headless.

Bridge methods take FILE NAMES, never paths: every name is resolved under the
configured inbox/outbox, so the UI cannot reach outside the workspace.
"""

from __future__ import annotations

import base64
import getpass
import os
import shutil
import sys
import threading
from pathlib import Path

from speade import service, subproc
from speade.service import DEFAULT_CONFIG_PATH

# Above this size the embedded preview is refused (a data: URI would bloat 4/3x
# in memory); the reviewer uses "Open in viewer" instead -- same documents, no limit.
_MAX_EMBED_BYTES = 40 * 1024 * 1024


def _open_native(path: Path) -> None:
    """Open a file/folder with the OS default handler (the Acrobat hand-off)."""
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - deliberate: hand the doc to the system viewer
    elif sys.platform == "darwin":
        subproc.run(["open", str(path)], check=False)
    else:
        subproc.run(["xdg-open", str(path)], check=False)


class SpeadeApi:
    """Exposed to JS as `window.pywebview.api.<method>` (see ui/api.js)."""

    def __init__(self, config_path: Path | None = None) -> None:
        self._config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self._window = None  # set by app.py once the window exists (native dialogs)
        self._batch_lock = threading.Lock()
        self._batch: dict = {"running": False}  # polled by run_batch_status

    def attach_window(self, window) -> None:
        self._window = window

    def _resolve(self, file: str) -> Path | None:
        """A bare filename -> its current home in the outbox tree (root while a
        draft, approved/ or rejected/ once decided). Names only, never paths."""
        return service.find_output(Path(file).name, self._config_path)

    # ------------------------------------------------------------- read side
    def workspace(self) -> dict:
        ws = service.workspace(self._config_path)
        return {
            "inbox": str(ws.inbox),
            "outbox": str(ws.outbox),
            "stages": ws.stages,
            "verapdf_profile": ws.verapdf_profile,
        }

    def reviewer_default(self) -> str:
        """The OS login name -- on UCC lab PCs this IS the student number."""
        try:
            return getpass.getuser()
        except Exception:
            return ""

    def list_queue(self) -> list[dict]:
        return [item.model_dump(mode="json") for item in service.list_queue(self._config_path)]

    def load_pdf(self, file: str) -> dict:
        """The outbox draft as a data: URI for the embedded preview pane."""
        pdf = self._resolve(file)
        if pdf is None:
            return {"error": f"not found: {Path(file).name}"}
        data = pdf.read_bytes()
        if len(data) > _MAX_EMBED_BYTES:
            size_mb = len(data) // (1024 * 1024)
            return {"error": f"too large to embed ({size_mb} MB) - use Open in viewer"}
        encoded = base64.b64encode(data).decode("ascii")
        return {"data_uri": f"data:application/pdf;base64,{encoded}"}

    def structure(self, file: str) -> dict:
        """Plain counts of a draft's tag tree (headings, paragraphs, figures...)
        so the reviewer sees whether it is actually tagged without Acrobat."""
        pdf = self._resolve(file)
        if pdf is None:
            return {"error": f"not found: {Path(file).name}"}
        try:
            return service.structure_summary(pdf).model_dump(mode="json")
        except Exception as exc:  # unreadable file / missing pikepdf: a note, not a crash
            return {"error": f"structure unavailable: {str(exc)[:120]}"}

    def structure_tree(self, file: str) -> dict:
        """The full tag tree with page geometry -- the in-app tags panel."""
        pdf = self._resolve(file)
        if pdf is None:
            return {"error": f"not found: {Path(file).name}"}
        try:
            return service.structure_tree(pdf).model_dump(mode="json")
        except Exception as exc:  # unreadable file / missing engine: a note, not a crash
            return {"error": f"structure unavailable: {str(exc)[:120]}"}

    def page_image(self, file: str, index: int = 0) -> dict:
        """One page rendered as a PNG data: URI (plus its size in PDF points),
        the canvas the tags panel draws its highlight boxes over."""
        pdf = self._resolve(file)
        if pdf is None:
            return {"error": f"not found: {Path(file).name}"}
        try:
            import io

            import pypdfium2 as pdfium

            doc = pdfium.PdfDocument(str(pdf))
            try:
                if not 0 <= index < len(doc):
                    return {"error": f"no page {index + 1}"}
                page = doc[index]
                width, height = page.get_size()
                scale = min(1400 / max(width, 1), 2.0)  # ~1400px wide: crisp, not huge
                image = page.render(scale=scale).to_pil()
                buf = io.BytesIO()
                image.save(buf, format="PNG")
                encoded = base64.b64encode(buf.getvalue()).decode("ascii")
                return {
                    "data_uri": f"data:image/png;base64,{encoded}",
                    "width": width,
                    "height": height,
                    "pages": len(doc),
                }
            finally:
                doc.close()
        except Exception as exc:
            return {"error": f"page image unavailable: {str(exc)[:120]}"}

    def audit_log(self, limit: int = 200) -> list[dict]:
        """The audit trail, newest first -- the History view."""
        return service.audit_events(self._config_path, limit=limit)

    # ------------------------------------------------------------ write side
    def run_batch(self) -> list[dict]:
        """Sweep the configured inbox; one bad file never kills the batch.
        Synchronous variant (blocks until done) -- the UI uses run_batch_start
        + run_batch_status for its progress bar instead."""
        return [item.model_dump(mode="json") for item in service.run_batch(None, self._config_path)]

    def list_pending(self) -> list[str]:
        """Inbox files still waiting to be processed -- the sidebar's 'Waiting
        to process' section (already-done documents are not listed again)."""
        return service.list_pending(self._config_path)

    def run_batch_start(self, reprocess: bool = False) -> dict:
        """Kick off a batch on a worker thread; the UI polls run_batch_status.
        pywebview runs each bridge call on its own thread, so status polls keep
        flowing while the worker grinds through the inbox. Already-processed,
        unchanged files are skipped unless `reprocess` is set."""
        with self._batch_lock:
            if self._batch.get("running"):
                return {"error": "a batch is already running"}
            self._batch = {"running": True, "done": 0, "total": 0, "current": ""}

        def progress(done: int, total: int, current: str) -> None:
            self._batch.update(done=done, total=total, current=current)

        def worker() -> None:
            try:
                items = service.run_batch(
                    None,
                    self._config_path,
                    progress=progress,
                    cancel=lambda: bool(self._batch.get("cancel")),
                    reprocess=reprocess,
                )
                self._batch["items"] = [item.model_dump(mode="json") for item in items]
                self._batch["cancelled"] = bool(self._batch.get("cancel"))
            except Exception as exc:  # config errors etc. -- surfaced to the UI
                self._batch["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            finally:
                self._batch["running"] = False

        threading.Thread(target=worker, name="speade-batch", daemon=True).start()
        return {"started": True}

    def run_batch_cancel(self) -> dict:
        """The Stop button: ask the running batch to halt BETWEEN documents --
        the file in flight always finishes, so nothing is left half-written."""
        self._batch["cancel"] = True
        return {"cancelling": True}

    def run_batch_status(self) -> dict:
        """The polled state of the running (or last) batch: running / done /
        total / current file, plus items or error once finished."""
        return dict(self._batch)

    def doc_metadata(self, file: str) -> dict:
        """The draft's current title + reading language for the editable fields."""
        pdf = self._resolve(file)
        if pdf is None:
            return {"error": f"not found: {Path(file).name}"}
        try:
            return service.doc_metadata(pdf)
        except ImportError:
            return {"error": "metadata editing needs the tag extra (pikepdf) installed"}
        except Exception as exc:  # unreadable file: a note for the UI, not a crash
            return {"error": f"metadata unavailable: {str(exc)[:120]}"}

    def set_doc_metadata(self, file: str, title: str, lang: str) -> dict:
        """Apply the reviewer's title + reading language to the draft (the
        human-authored half of the metadata; see service.set_doc_metadata)."""
        pdf = self._resolve(file)
        if pdf is None:
            return {"error": f"not found: {Path(file).name}"}
        try:
            return service.set_doc_metadata(pdf, title, lang, config_path=self._config_path)
        except ImportError:
            return {"error": "metadata editing needs the tag extra (pikepdf) installed"}
        except Exception as exc:
            return {"error": f"could not save: {str(exc)[:120]}"}

    def decide(self, file: str, reviewer: str, approve: bool) -> dict:
        """The human gate: veraPDF verdict + the reviewer's decision (service.decide)."""
        pdf = self._resolve(file) or service.workspace(self._config_path).outbox / Path(file).name
        sidecar = service.decide(
            pdf, reviewer=reviewer, approve=approve, config_path=self._config_path
        )
        return {
            "file": pdf.name,
            "verapdf_passed": sidecar.verapdf_passed,
            "failed_clauses": sidecar.verapdf_failed_clauses,
            "status": sidecar.approval.status.value,
            "reviewer": sidecar.approval.reviewer,
        }

    def add_pdfs(self) -> dict:
        """Native file picker -> copy the chosen PDFs into the inbox."""
        if self._window is None:
            return {"error": "window not ready"}
        import webview  # lazy: only the running app has a window anyway

        picks = (
            self._window.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=True, file_types=("PDF files (*.pdf)",)
            )
            or ()
        )
        inbox = service.workspace(self._config_path).inbox
        inbox.mkdir(parents=True, exist_ok=True)
        copied = []
        for pick in picks:
            dest = inbox / Path(pick).name
            shutil.copy2(pick, dest)
            copied.append(dest.name)
        return {"copied": copied}

    def open_output(self, file: str) -> bool:
        """Open a draft in the system PDF viewer (Acrobat correction round-trip)."""
        pdf = self._resolve(file)
        if pdf is None:
            return False
        _open_native(pdf)
        return True

    def open_inbox(self) -> bool:
        """Open the input folder in the file explorer (drop PDFs in directly)."""
        inbox = service.workspace(self._config_path).inbox
        if not inbox.is_dir():
            return False
        _open_native(inbox)
        return True

    def open_outbox(self) -> bool:
        outbox = service.workspace(self._config_path).outbox
        if not outbox.is_dir():
            return False
        _open_native(outbox)
        return True
