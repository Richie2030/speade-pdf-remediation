"""The document-source abstraction.

`LocalFolderClient` today; a `CanvasClient` (same interface) when API tokens
land. Swapping one for the other is a config edit, so the entire offline core is
built and proven without Canvas access.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class DocRef(BaseModel):
    """A handle to a source document, independent of where it lives."""

    id: str
    name: str


@runtime_checkable
class DocumentClient(Protocol):
    def list_documents(self) -> list[DocRef]: ...

    def fetch(self, ref: DocRef, dest: Path) -> Path: ...

    def put(self, path: Path, ref: DocRef) -> None: ...
