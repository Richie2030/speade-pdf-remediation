"""Local-folder stand-in for the (token-gated, not-yet-available) Canvas client.

Reads source PDFs from an inbox and writes remediated copies to an outbox, so
the whole pipeline runs offline with no API access.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from speade.io.base import DocRef


class LocalFolderClient:
    def __init__(self, inbox: Path, outbox: Path) -> None:
        self.inbox = Path(inbox)
        self.outbox = Path(outbox)

    def list_documents(self) -> list[DocRef]:
        self.inbox.mkdir(parents=True, exist_ok=True)
        return [DocRef(id=p.name, name=p.name) for p in sorted(self.inbox.glob("*.pdf"))]

    def fetch(self, ref: DocRef, dest: Path) -> Path:
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / ref.name
        shutil.copy2(self.inbox / ref.id, out)
        return out

    def put(self, path: Path, ref: DocRef) -> None:
        self.outbox.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, self.outbox / ref.name)
