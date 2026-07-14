"""Local-folder document client: read source PDFs from an inbox and write
remediated copies + sidecars to an outbox. This is the offline core's only
document source -- there is no remote/API client.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from speade.io.base import DocRef
from speade.pipeline.contract import Sidecar


class LocalFolderClient:
    """Minimal filesystem-backed client for the local inbox/outbox.

    - `source_dir` holds input PDFs the pipeline should process.
    - `out_dir` receives remediated copies + sidecars written by `put()`.

    Intentionally small and synchronous; suited to local runs and unit tests.
    Structurally satisfies `speade.io.base.DocumentClient`.
    """

    def __init__(self, source_dir: Path, out_dir: Path | None = None):
        self.source_dir = Path(source_dir)
        self.out_dir = Path(out_dir) if out_dir is not None else self.source_dir / "outbox"
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def list_documents(self) -> list[DocRef]:
        """Return a `DocRef` for every PDF in `source_dir`, sorted by filename."""
        return [DocRef(id=p.name, name=p.name) for p in sorted(self.source_dir.glob("*.pdf"))]

    def fetch(self, ref: DocRef) -> Path:
        """Return the inbox path of `ref`'s source PDF; raise if it is missing."""
        src = self.source_dir / ref.name
        if not src.is_file():
            raise FileNotFoundError(f"source PDF not found in inbox: {src}")
        return src

    def put(self, ref: DocRef, output_pdf: Path, sidecar: Sidecar) -> Path:
        """Write the remediated `output_pdf` + its sidecar JSON into `out_dir`.

        The sidecar is written with LF line endings (the Linux/Boole target),
        regardless of the host OS. Returns the output PDF path.
        """
        out_pdf = self.out_dir / ref.name
        shutil.copy2(output_pdf, out_pdf)
        side_path = out_pdf.with_name(out_pdf.name + ".sidecar.json")
        side_path.write_text(sidecar.model_dump_json(indent=2), encoding="utf-8", newline="\n")
        return out_pdf
