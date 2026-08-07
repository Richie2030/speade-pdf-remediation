"""`python -m speade.web` -- serve the review UI at http://127.0.0.1:<port>/.

Localhost only, by design: the bind address is hard-coded to 127.0.0.1, so no
other machine can reach the app. Closing this process is closing the app.
"""

from __future__ import annotations

import argparse
import threading
import webbrowser
from pathlib import Path

import uvicorn

from speade.web.server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m speade.web",
        description="SPEADE review UI in your browser (this machine only).",
    )
    parser.add_argument("--port", type=int, default=8765, help="port on 127.0.0.1 (default 8765)")
    parser.add_argument("--config", type=Path, default=None, help="path to config.yaml")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = parser.parse_args()

    from speade.diagnostics import enable_crash_log

    enable_crash_log("web")  # a native crash must leave a stack behind

    app = create_app(args.config)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"SPEADE review UI: {url}  (Ctrl+C to quit)")
    if not args.no_browser:
        # a beat after startup so uvicorn is listening before the tab opens.
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    # Ctrl+C lands here: stop any running batch and wait for the in-flight
    # document BEFORE interpreter exit, or the atexit pdfium finalizers race
    # the worker's native call (the recorded heap-corruption crash mechanism).
    app.state.bridge.shutdown()


if __name__ == "__main__":
    main()
