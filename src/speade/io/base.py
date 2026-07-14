"""Local document-I/O types: the DocRef handle and the DocumentClient protocol.

`DocRef` is the small (id + filename) handle the reader uses to describe one source
PDF. `DocumentClient` is the structural interface the offline core speaks -- there
is exactly one implementation in v1 (LocalFolderClient); no remote/API client.
Pure types only -- no I/O or side-effects here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from speade.pipeline.contract import Sidecar


class DocRef(BaseModel):
    """A reference to one source document read from a local folder."""

    id: str  # stable document identifier (the filename, for the local reader)
    name: str  # the document's filename in the inbox / outbox


@runtime_checkable
class DocumentClient(Protocol):
    """The offline document-I/O surface: list the inbox, fetch a source, put a result.

    A Protocol (structural typing): any object with these three methods is a
    DocumentClient -- no base class. LocalFolderClient is the only v1 implementation.
    """

    def list_documents(self) -> list[DocRef]:
        """Return a DocRef for every source PDF available to process."""
        ...

    def fetch(self, ref: DocRef) -> Path:
        """Return the local path of `ref`'s source PDF (raise if missing)."""
        ...

    def put(self, ref: DocRef, output_pdf: Path, sidecar: Sidecar) -> Path:
        """Write the remediated PDF + its sidecar to the outbox; return the PDF path."""
        ...
