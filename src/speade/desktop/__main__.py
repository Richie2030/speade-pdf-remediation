"""`python -m speade.desktop` -> the review window."""

import multiprocessing

from speade.desktop.app import main

if __name__ == "__main__":
    # FIRST, before anything else: the sacrificial page renderer uses
    # multiprocessing spawn, and in the PyInstaller exe the respawned child
    # re-enters THIS entry point -- freeze_support() is what diverts it into
    # the worker instead of opening a second review window.
    multiprocessing.freeze_support()
    raise SystemExit(main())
