"""Append-only audit log + content hashing (deliverable A1).

Define a streaming SHA-256 file hasher and an append-only JSONL recorder here --
one line per run / per gate sign-off. The source+output SHA-256 pair proves Ally
re-scored the exact bytes a human approved.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
