"""`python -m speade.desktop` -> the review window."""

import sys

from speade.desktop.app import main

if __name__ == "__main__":
    # FIRST, before anything else: in the PyInstaller exe the sacrificial page
    # renderer is this same exe re-invoked with --render-worker -- divert that
    # into the worker loop instead of opening a second review window.
    if "--render-worker" in sys.argv:
        from speade.render_worker import worker_main_stdio

        worker_main_stdio()
        sys.exit(0)
    raise SystemExit(main())
