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

    def attach_window(self, window) -> None:
        self._window = window

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
        pdf = service.workspace(self._config_path).outbox / Path(file).name
        if not pdf.is_file():
            return {"error": f"not found: {pdf.name}"}
        data = pdf.read_bytes()
        if len(data) > _MAX_EMBED_BYTES:
            size_mb = len(data) // (1024 * 1024)
            return {"error": f"too large to embed ({size_mb} MB) — use Open in viewer"}
        encoded = base64.b64encode(data).decode("ascii")
        return {"data_uri": f"data:application/pdf;base64,{encoded}"}

    # ------------------------------------------------------------ write side
    def run_batch(self) -> list[dict]:
        """Sweep the configured inbox; one bad file never kills the batch."""
        return [item.model_dump(mode="json") for item in service.run_batch(None, self._config_path)]

    def decide(self, file: str, reviewer: str, approve: bool) -> dict:
        """The human gate: veraPDF verdict + the reviewer's decision (service.decide)."""
        pdf = service.workspace(self._config_path).outbox / Path(file).name
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
        pdf = service.workspace(self._config_path).outbox / Path(file).name
        if not pdf.is_file():
            return False
        _open_native(pdf)
        return True

    def open_outbox(self) -> bool:
        outbox = service.workspace(self._config_path).outbox
        if not outbox.is_dir():
            return False
        _open_native(outbox)
        return True
