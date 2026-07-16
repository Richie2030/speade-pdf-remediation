"""OCR stage (Stage 3, STRETCH) -- add a searchable text layer to a scanned PDF
before tagging.

Engine: Tesseract invoked DIRECTLY (arms-length subprocess, rule L2) -- it can
emit a searchable one-page PDF itself, which drops the ocrmypdf + Ghostscript
system stack an earlier draft needed. Pages are rendered with pypdfium2 (a
permissive, self-contained pip wheel -- no system renderer install) and merged
with pikepdf. This exact chain was proven end-to-end by datasets/build_corpus.py
before being promoted here.

OCR is optional (stretch), so a missing engine or a failed run never crashes a
batch: the problem is flagged on the sidecar and the doc flows on -- tag will
skip it (needs-ocr) and the human gate sees exactly why.

Deps: `uv sync --extra ocr` (pypdfium2 + pikepdf) plus a system Tesseract >=5.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from speade import subproc
from speade.pipeline.contract import Route, Sidecar, StageResult

# Default Windows installer location, probed when tesseract isn't on PATH (the
# UCC lab-PC case: machine-wide install, per-user PATH untouched). Harmless
# elsewhere -- on Linux/macOS PATH is the only lookup that matters.
_WIN_TESSERACT = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")

_DPI = 200  # render resolution: the OCR-quality vs speed/size balance point
_PAGE_TIMEOUT_S = 300  # per page; a stuck engine must not hang a batch forever


def find_tesseract() -> str | None:
    """Locate the Tesseract executable: PATH first, then the Windows default dir."""
    return shutil.which("tesseract") or (str(_WIN_TESSERACT) if _WIN_TESSERACT.exists() else None)


class OcrStage:
    """Add an OCR text layer to scanned (image-only) PDFs via Tesseract.

    Writes a NEW `<stem>.ocr.pdf`, never edits its input in place. On success the
    route flips to BORN_DIGITAL so the tag stage proceeds downstream. Docs that
    already have text (born-digital, and P1.4's tagged-anyway mixed docs) pass
    through untouched -- rasterising real text would degrade it (do-not-degrade).
    """

    name = "ocr"

    def run(self, pdf: Path, sidecar: Sidecar) -> StageResult:
        # unreadable inputs (encrypted / corrupt -- flagged by detect) cannot be
        # rendered, let alone OCR'd; they flow to the gate with the reason set.
        if any(flag.startswith("unreadable-") for flag in sidecar.flags):
            sidecar.flags.append("ocr-skipped-unreadable")
            sidecar.applied(self.name)
            return StageResult(stage=self.name, output=pdf, sidecar=sidecar, changed=False)

        # only image-only docs need OCR. BORN_DIGITAL already has real text, and
        # UNKNOWN (mixed) keeps its true text pages un-degraded -- it is tagged
        # as-is with a reviewer flag (see TagStage). Expected, so no flag noise.
        if sidecar.route != Route.SCANNED:
            sidecar.applied(self.name)
            return StageResult(stage=self.name, output=pdf, sidecar=sidecar, changed=False)

        problem = self._missing_dependency()
        if problem:
            sidecar.flags.append("ocr-unavailable")
            sidecar.applied(self.name)
            return StageResult(
                stage=self.name,
                output=pdf,
                sidecar=sidecar,
                changed=False,
                notes=[problem],
            )

        # NEW file, never in-place: keep the pre-OCR working copy intact
        # (reversibility / do-not-degrade), exactly like the tag stage.
        out = pdf.with_name(f"{pdf.stem}.ocr.pdf")
        try:
            self._ocr(pdf, out)
        except subprocess.TimeoutExpired:
            sidecar.flags.append("ocr-timeout")
            sidecar.applied(self.name)
            return StageResult(
                stage=self.name,
                output=pdf,
                sidecar=sidecar,
                changed=False,
                notes=[f"OCR timed out (> {_PAGE_TIMEOUT_S}s on one page); routed to human"],
            )
        except Exception as exc:  # engine/render/merge failure: flag, never crash the batch
            sidecar.flags.append("ocr-failed")
            sidecar.applied(self.name)
            return StageResult(
                stage=self.name,
                output=pdf,
                sidecar=sidecar,
                changed=False,
                notes=[f"OCR failed: {str(exc)[:200]}"],
            )

        # Success: the doc now carries a real text layer, so it can be tagged like
        # a born-digital PDF. Re-route so the tag stage proceeds instead of skipping.
        sidecar.route = Route.BORN_DIGITAL
        sidecar.applied(self.name)
        return StageResult(
            stage=self.name,
            output=out,
            sidecar=sidecar,
            changed=True,
            notes=["OCR text layer added"],
        )

    @staticmethod
    def _missing_dependency() -> str | None:
        """Name the missing piece of the OCR toolchain, or None when all present."""
        if find_tesseract() is None:
            return "tesseract not found (PATH or default install dir); install Tesseract >=5"
        try:
            import pikepdf  # noqa: F401
            import pypdfium2  # noqa: F401
        except ImportError as exc:
            return f"missing python package {exc.name!r}; run `uv sync --extra ocr`"
        return None

    def _ocr(self, pdf: Path, out: Path) -> None:
        """Render each page (~200 DPI, pypdfium2), OCR it with `tesseract <img>
        <stem> pdf` into a one-page searchable PDF, then merge with pikepdf."""
        import pikepdf
        import pypdfium2 as pdfium

        tesseract = find_tesseract()
        with tempfile.TemporaryDirectory(prefix="speade_ocr_") as tmp:
            tmpd = Path(tmp)
            page_pdfs: list[Path] = []
            doc = pdfium.PdfDocument(str(pdf))
            try:
                for i in range(len(doc)):
                    png = tmpd / f"pg_{i:04d}.png"
                    doc[i].render(scale=_DPI / 72).to_pil().save(png)
                    stem = tmpd / f"pg_{i:04d}"
                    proc = subproc.run(
                        [tesseract, str(png), str(stem), "pdf"],
                        capture_output=True,
                        text=True,
                        timeout=_PAGE_TIMEOUT_S,
                    )
                    if proc.returncode != 0:
                        raise RuntimeError(
                            f"tesseract failed on page {i + 1} "
                            f"(exit {proc.returncode}): {proc.stderr[:200]}"
                        )
                    page_pdfs.append(stem.with_suffix(".pdf"))
            finally:
                doc.close()

            merged = pikepdf.Pdf.new()
            for page_pdf in page_pdfs:
                with pikepdf.open(page_pdf) as page_doc:
                    merged.pages.extend(page_doc.pages)
            merged.save(out)
