"""Append-only audit log + content hashing.

Seed of deliverable A1: one JSONL line per run / gate sign-off. The source+output
SHA-256 pair is what later proves Ally re-scored the exact bytes a human approved.
Kept deliberately minimal -- "hours of work, not a SIEM".
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_CHUNK = 1 << 16


def sha256_file(path: Path) -> str:
    """Streaming SHA-256 of a file's bytes."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def record(event: dict[str, Any], log_path: Path) -> None:
    """Append one timestamped JSON line to the audit log."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = {"ts": datetime.now(UTC).isoformat(), **event}
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")
