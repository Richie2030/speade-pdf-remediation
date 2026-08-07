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
        self._batch_thread: threading.Thread | None = None  # joined by shutdown()
        self._renderer_lock = threading.Lock()
        self._page_renderer = None  # lazy: speade.render_worker.PageRenderer

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
        the canvas the tags panel draws its highlight boxes over.

        Rendered in the SACRIFICIAL worker process (speade.render_worker), not
        in-process: the recorded desktop crashes were native pdfium faults
        during preview renders, and no in-process discipline survives those.
        A poison page now costs one image, not the review session. (This is
        also why PDFIUM_LOCK is not taken here -- pdfium runs in the child.)"""
        pdf = self._resolve(file)
        if pdf is None:
            return {"error": f"not found: {Path(file).name}"}
        try:
            result = self._renderer().render(pdf, int(index))
        except Exception as exc:
            return {"error": f"page image unavailable: {str(exc)[:120]}"}
        if "error" in result:
            return {"error": result["error"]}
        encoded = base64.b64encode(result["png"]).decode("ascii")
        return {
            "data_uri": f"data:image/png;base64,{encoded}",
            "width": result["width"],
            "height": result["height"],
            "pages": result["pages"],
        }

    def _renderer(self):
        """The lazy singleton render worker (started on first page image)."""
        with self._renderer_lock:
            if self._page_renderer is None:
                from speade.render_worker import PageRenderer

                self._page_renderer = PageRenderer()
            return self._page_renderer

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

        thread = threading.Thread(target=worker, name="speade-batch", daemon=True)
        self._batch_thread = thread
        thread.start()
        return {"started": True}

    def run_batch_cancel(self) -> dict:
        """The Stop button: ask the running batch to halt BETWEEN documents --
        the file in flight always finishes, so nothing is left half-written."""
        self._batch["cancel"] = True
        return {"cancelling": True}

    def shutdown(self, timeout_s: float = 600.0) -> None:
        """App-exit hook (called after the window closes): stop the batch and
        WAIT for the in-flight document to finish before the interpreter exits.

        Without this, interpreter shutdown races the daemon batch worker: atexit
        runs pypdfium2's weakref finalizers on the main thread -- closing pdfium
        objects unlocked -- while the worker is still inside a native pdfium
        call. That use-after-free is one of the recorded 0xc0000374 heap-
        corruption crashes. The timeout matches the tag engine's own ceiling,
        so only a truly hung external tool can still leave the race open.
        """
        self._batch["cancel"] = True
        thread = self._batch_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout_s)
        if self._page_renderer is not None:
            self._page_renderer.close()  # the render child, politely

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

    def tag_types(self) -> list[str]:
        """The tag types a reviewer may assign in-app (the editor dropdown)."""
        return list(service.EDITABLE_TAG_TYPES)

    def set_tag_type(self, file: str, node_id: int, new_type: str) -> dict:
        """Retag one element -- e.g. promote a Paragraph to Heading 2."""
        pdf = self._resolve(file)
        if pdf is None:
            return {"error": f"not found: {Path(file).name}"}
        try:
            return service.set_tag_type(pdf, int(node_id), new_type, self._config_path)
        except (ValueError, LookupError) as exc:
            return {"error": str(exc)}
        except Exception as exc:  # a broken file must not kill the UI
            return {"error": f"could not change the tag: {str(exc)[:120]}"}

    def set_figure_alt(self, file: str, node_id: int, alt: str) -> dict:
        """Save the human-authored image description (/Alt) onto one element."""
        pdf = self._resolve(file)
        if pdf is None:
            return {"error": f"not found: {Path(file).name}"}
        try:
            return service.set_figure_alt(pdf, int(node_id), alt, self._config_path)
        except LookupError as exc:
            return {"error": str(exc)}
        except Exception as exc:
            return {"error": f"could not save the description: {str(exc)[:120]}"}

    def make_decorative(self, file: str, node_id: int) -> dict:
        """Mark one element as decoration (out of the reading order, no
        description needed) -- the answer for decorative images."""
        pdf = self._resolve(file)
        if pdf is None:
            return {"error": f"not found: {Path(file).name}"}
        try:
            return service.make_decorative(pdf, int(node_id), self._config_path)
        except LookupError as exc:
            return {"error": str(exc)}
        except Exception as exc:
            return {"error": f"could not mark it decorative: {str(exc)[:120]}"}

    def move_tag(self, file: str, node_id: int, delta: int) -> dict:
        """Move one tag earlier (-1) or later (+1) in the reading order."""
        pdf = self._resolve(file)
        if pdf is None:
            return {"error": f"not found: {Path(file).name}"}
        try:
            return service.move_tag(pdf, int(node_id), int(delta), self._config_path)
        except (ValueError, LookupError) as exc:
            return {"error": str(exc)}
        except Exception as exc:
            return {"error": f"could not move the tag: {str(exc)[:120]}"}

    def bulk_edit(
        self, file: str, action: str, node_ids: list, new_type: str | None = None
    ) -> dict:
        """Apply one action to a drag-selection of tags (merge / retag / decorative)."""
        pdf = self._resolve(file)
        if pdf is None:
            return {"error": f"not found: {Path(file).name}"}
        try:
            return service.bulk_edit(pdf, action, list(node_ids), new_type, self._config_path)
        except (ValueError, LookupError) as exc:
            return {"error": str(exc)}
        except Exception as exc:
            return {"error": f"could not apply the change: {str(exc)[:120]}"}

    def carve_tag(self, file: str, picks: list, new_type: str) -> dict:
        """Carve selected lines out of their tags into one new tag."""
        pdf = self._resolve(file)
        if pdf is None:
            return {"error": f"not found: {Path(file).name}"}
        try:
            return service.carve_tag(pdf, list(picks), new_type, self._config_path)
        except (ValueError, LookupError) as exc:
            return {"error": str(exc)}
        except Exception as exc:
            return {"error": f"could not carve the new tag: {str(exc)[:120]}"}

    def tag_untagged(self, file: str, page: int, indexes: list, new_type: str) -> dict:
        """Give untagged content a brand-new tag (the drag tool's third grain)."""
        pdf = self._resolve(file)
        if pdf is None:
            return {"error": f"not found: {Path(file).name}"}
        try:
            return service.tag_untagged(pdf, int(page), list(indexes), new_type, self._config_path)
        except (ValueError, LookupError) as exc:
            return {"error": str(exc)}
        except Exception as exc:
            return {"error": f"could not tag the selection: {str(exc)[:120]}"}

    def unwrap_tag(self, file: str, node_id: int) -> dict:
        """Remove one tag but keep its contents (unwrap a bogus wrapper)."""
        pdf = self._resolve(file)
        if pdf is None:
            return {"error": f"not found: {Path(file).name}"}
        try:
            return service.unwrap_tag(pdf, int(node_id), self._config_path)
        except (ValueError, LookupError) as exc:
            return {"error": str(exc)}
        except Exception as exc:
            return {"error": f"could not remove the tag: {str(exc)[:120]}"}

    def undo_last_edit(self, file: str) -> dict:
        """Step back one edit (the snapshot taken before it)."""
        pdf = self._resolve(file)
        if pdf is None:
            return {"error": f"not found: {Path(file).name}"}
        try:
            return service.undo_last_edit(pdf, self._config_path)
        except LookupError as exc:
            return {"error": str(exc)}
        except Exception as exc:
            return {"error": f"could not undo: {str(exc)[:120]}"}

    def remove_all_tags(self, file: str) -> dict:
        """Strip the whole tag structure (start over in Acrobat)."""
        pdf = self._resolve(file)
        if pdf is None:
            return {"error": f"not found: {Path(file).name}"}
        try:
            return service.remove_all_tags(pdf, self._config_path)
        except Exception as exc:
            return {"error": f"could not remove the tags: {str(exc)[:120]}"}

    def export_history(self) -> dict:
        """Copy the full history out of the hidden audit folder as a CSV in the
        outbox, for admin collection. Returns the created file's path."""
        try:
            path = service.export_history(self._config_path)
            return {"path": str(path), "name": path.name}
        except Exception as exc:
            return {"error": f"could not export the history: {str(exc)[:120]}"}

    def reprocess(self, file: str) -> dict:
        """Undo every edit by re-running the original inbox document."""
        try:
            sidecar = service.reprocess(Path(file).name, self._config_path)
            return {"ok": True, "verapdf_passed": sidecar.verapdf_passed}
        except FileNotFoundError as exc:
            return {"error": str(exc)}
        except Exception as exc:
            return {"error": f"could not reprocess: {str(exc)[:120]}"}

    def decide(self, file: str, reviewer: str, approve: bool) -> dict:
        """The human gate: veraPDF verdict + the reviewer's decision (service.decide)."""
        pdf = self._resolve(file) or service.workspace(self._config_path).outbox / Path(file).name
        try:
            sidecar = service.decide(
                pdf, reviewer=reviewer, approve=approve, config_path=self._config_path
            )
        except FileNotFoundError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # the gate must never take the whole UI down
            return {"error": f"could not record the decision: {str(exc)[:160]}"}
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
