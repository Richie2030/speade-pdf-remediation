"""I/O contract for document sources.

This module declares the small, dependency-light contract the pipeline uses to
interact with document sources. Keeping this contract minimal and protocol-
based lets the runner swap implementations (for example, a local-folder client
or a Canvas REST client) by config rather than code changes.

Design goals:
- Small surface area: only the fields and methods the pipeline needs.
- Runtime-checkable `Protocol` so implementations can be duck-typed in tests.
- No I/O or side-effects here: this file only defines types and docstrings.

Usage example (local client implements this interface):

		from speade.io.base import DocRef
		from speade.io.local import LocalFolderClient

		client = LocalFolderClient(Path("./data/inbox"))
		for ref in client.list_documents():
				local_path = client.fetch(ref, Path("./workdir"))
				# process local_path ...
				client.put(ref, Path("./workdir/remediated.pdf"))

Note: store the source path as a `Path` so callers may read it directly on
local development machines; when persisting references across machines, store
POSIX strings (Path.as_posix()) as needed by the environment.
"""

from __future__ import annotations 

from pathlib import Path 

from typing import Protocol, runtime_checkable 

from pydantic import BaseModel



class DocRef(BaseModel):
		"""Lightweight reference to one source document exposed by an I/O client.

		Attributes:
		- id: a stable identifier for the document within the source system.
			For a local folder client this can be the filename; for Canvas it will be
			a numeric or uuid-like id.
		- source_path: a `pathlib.Path` pointing to where the source PDF can be
			read from locally. Implementations that download from a remote service
			should set this to the local copy path returned by `fetch()`.
		"""

		id: str  # stable document identifier in the source system
		source_path: Path  # where the source PDF can be read from locally


@runtime_checkable
class DocumentClient(Protocol):
		"""Minimal I/O contract used by the pipeline runner.

		Implementations should be lightweight adapters that expose the three
		operations the runner needs:

		- `list_documents()` discovers available inputs and returns a list of
			`DocRef` objects describing them.
		- `fetch(ref, workdir)` ensures a local copy of the source PDF exists in
			`workdir` and returns the `Path` to that local copy.
		- `put(original, output_pdf)` stores the remediated output and returns a
			destination identifier (for local clients this can be the destination
			path; for remote systems this would be the remote upload id or URL).

		This is a `Protocol` (not an abstract base class) so test doubles and
		simple concrete clients can implement the shape without inheritance.
		"""
		

		def list_documents(self) -> list[DocRef]:
				"""Return available input documents to process.

				Returns a list of `DocRef` instances. Implementations should not mutate
				the returned objects; the runner may pass them to `fetch()` and later
				to `put()`.
				"""
				

		def fetch(self, ref: DocRef, workdir: Path) -> Path:
				"""Copy/download the source PDF into `workdir`, return local file path.

				The returned `Path` should point to a readable PDF file the pipeline can
				operate on. Implementations must create `workdir` if it does not exist.
				"""

		def put(self, original: DocRef, output_pdf: Path) -> str:
				"""Store the remediated PDF and return a destination identifier/path.

				The returned string is opaque to the runner: it may be a filesystem
				path, an upload id, or a URL depending on the implementation.
				"""


