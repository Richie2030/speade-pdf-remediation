"""Command-line entry point: the `speade` runner.

Define the Typer app and its commands here -- `stages` (list available stage
implementations) and `run FILE.pdf` (run the configured stages on one PDF,
offline, never mutating the original). Expose `main()` for the console script.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
