"""The desktop shell: a pywebview window hosting ui/index.html.

This file is deliberately tiny -- per the decision record, the .exe is "mostly a
packaging exercise": the UI is static HTML/JS, the logic is speade.service, and
this module only creates the native window and connects the two. PyInstaller's
target for the packaged build (--onedir; see the frontend-delivery record).
"""

from __future__ import annotations

import sys
from pathlib import Path

from speade.desktop.api import SpeadeApi

UI_INDEX = Path(__file__).resolve().parent / "ui" / "index.html"


def main() -> int:
    """Open the review window; blocks until the reviewer closes it."""
    import webview  # lazy: needs the `desktop` extra (pywebview, BSD)

    from speade.diagnostics import enable_crash_log

    enable_crash_log("desktop")  # a native crash must leave a stack behind

    config = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    api = SpeadeApi(config)
    window = webview.create_window(
        "SPEADE Review",
        str(UI_INDEX),
        js_api=api,
        width=1200,
        height=800,
        min_size=(900, 600),
    )
    api.attach_window(window)

    def _on_closing() -> None:
        # closing the window mid-batch: flag the stop NOW (the batch halts
        # after the in-flight document) so the post-start() wait is short.
        # Returning None never blocks the close.
        api.run_batch_cancel()

    window.events.closing += _on_closing
    webview.start()
    # the window is gone but a batch worker may still be inside a native call:
    # wait for it before the interpreter's exit finalizers run (see shutdown).
    api.shutdown()
    return 0
