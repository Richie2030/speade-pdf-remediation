"""Append-only audit log + content hashing (deliverable A1).

Define a streaming SHA-256 file hasher and an append-only JSONL recorder here --
one line per run / per gate sign-off. The source+output SHA-256 pair proves the
exact bytes a human approved are the exact bytes that shipped.
"""


# The pipeline's receipt book and tamper-evident seal. Two small, separate jobs:
#   1. Fingerprint a file -- reduce a whole PDF to a short code that changes if
#      even one byte changes.
#   2. Keep an add-only logbook -- one line per event, never rewriting history.
# The project's promise is "the exact file a human approved is the exact file that
# ships." Proving that needs (a) a fingerprint of the bytes and (b) a permanent
# record -- which is what these three functions provide.

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:  # Streaming hash so a huge PDF doesn't load into memory
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
        return h.hexdigest()


def append_event(log: Path, event: dict[str, Any]) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(event) + "\n")


def read_events(log: Path) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [
        json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
