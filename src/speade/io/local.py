"""Local-folder stand-in for the (token-gated) Canvas client: read source PDFs
from an inbox, write remediated copies to an outbox. Lets the whole offline core
run with no API access. Implements the io.base.DocumentClient interface.
"""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import List

from speade.io.base import DocRef


class LocalFolderClient:
	"""Minimal filesystem-backed DocumentClient for local testing.

	- `source_dir` holds input PDFs the pipeline should process.
	- `out_dir` receives remediated copies written by `put()`.

	This implementation is intentionally small and synchronous; it's suitable
	for local development and unit tests.
	"""

	def __init__(self, source_dir: Path, out_dir: Path | None = None):
		self.source_dir = Path(source_dir)
		self.out_dir = Path(out_dir) if out_dir is not None else self.source_dir / "outbox"
		self.out_dir.mkdir(parents=True, exist_ok=True)

	def list_documents(self) -> List[DocRef]:
		"""Return a list of `DocRef` for every PDF in `source_dir`.

		Files are returned in sorted order by name to make runs deterministic.
		"""
		docs: List[DocRef] = []
		for p in sorted(self.source_dir.glob("*.pdf")):
			docs.append(DocRef(id=p.name, source_path=p))
		return docs

	def fetch(self, ref: DocRef, workdir: Path) -> Path:
		"""Ensure a local copy of `ref` exists in `workdir` and return its Path.

		The local copy is created with `shutil.copy2` to preserve metadata.
		"""
		workdir = Path(workdir)
		workdir.mkdir(parents=True, exist_ok=True)
		src = Path(ref.source_path)
		dest = workdir / src.name
		shutil.copy2(src, dest)
		return dest

	def put(self, original: DocRef, output_pdf: Path) -> str:
		"""Store the remediated `output_pdf` in `out_dir` and return its path.

		The file is copied into `out_dir` using the original id as filename so
		callers can correlate source -> remediated easily.
		"""
		dst = self.out_dir / Path(original.id).name
		shutil.copy2(output_pdf, dst)
		return str(dst.resolve())
