"""OCR stage (Stage 3, STRETCH) -- add a searchable text layer to a scanned PDF
before tagging. Shell out to OCRmyPDF + Tesseract; flag garbage OCR and route
failures to the human queue rather than shipping corrupted text.
"""

from __future__ import annotations

"""
"Arms‑length, no in‑process import" means you run the OCR tool as an external program (CLI) via something like subprocess.run instead of importing its Python module and calling its functions inside your process.

Why do that?

Licensing/isolation: avoids pulling GPL/AGPL or heavy deps into your app process. - slop
Stability/safety: external crashes or memory leaks can't crash your process.
Dependency management: different tools/versions can be installed system-wide.
Security/sandboxing: easier to restrict what the external process can access.
Tradeoffs:

Slight overhead (process spawn, I/O).
Less direct API control and error semantics — you parse stdout/stderr and exit codes."""
import subprocess
from pathlib import Path

from speade.pipeline.contract import Sidecar, StageResult


class OcrStage:
    """Add OCR text layer to scanned PDFs using ocrmypdf (Tesseract backend).

    Shells out to ocrmypdf at arms length (never imports it, rule L2).
    Flags suspected garbage OCR; routes failures to human queue gracefully.

    Dependencies: ocrmypdf, Tesseract >=5.5 (system install).
    Install with: `uv sync --extra ocr`
    """

    name = "ocr"

    def run(self, pdf: Path, sidecar: Sidecar) -> StageResult:
        """Add searchable text layer. Return updated sidecar and working-copy path."""

        # Run ocrmypdf: adds OCR layer without modifying existing content
        # --quiet: suppress progress spam
        # --skip-text: skip pages that already have text (born-digital mixed in)
        # --deskew: fix rotated scans for better accuracy
        # --remove-background: cleaner output on low-quality scans
        try:
            subprocess.run(
                [
                    "ocrmypdf",
                    "--quiet",
                    "--skip-text",
                    "--deskew",
                    str(pdf),
                    str(pdf),  # in-place (working copy, never touches original)
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=300,  # 5 min per PDF; long scans can take time - this can be deleted 
            )
        except subprocess.TimeoutExpired:
            sidecar.flags.append("ocr-timeout")
            sidecar.stages_applied.append(self.name)
            # Return unchanged; let human decide 
            #if the ocr fails it flags it and lets the human queue handle it
            return StageResult(
                stage=self.name,
                output=pdf,
                sidecar=sidecar,
                changed=False,
                notes=["OCR timed out after 5 minutes; routed to human queue"],
            )
        except subprocess.CalledProcessError as e:
            sidecar.flags.append("ocr-failed")
            sidecar.stages_applied.append(self.name)
            # ocrmypdf fails on corrupt PDFs, encrypted files, etc.
            # Log the error, flag, and route to human queue
            return StageResult(
                stage=self.name,
                output=pdf,
                sidecar=sidecar,
                changed=False,
                notes=[f"OCR failed: {e.stderr[:200]}"],  # first 200 chars of error
            )
        except FileNotFoundError as e:
            # ocrmypdf or Tesseract not installed
            sidecar.flags.append("ocr-unavailable")
            sidecar.stages_applied.append(self.name)
            return StageResult(
                stage=self.name,
                output=pdf,
                sidecar=sidecar,
                changed=False,
                notes=["ocrmypdf or Tesseract not found; install and retry"],
            )

        # Success: OCR layer added. Mark stage as applied and return.
        sidecar.stages_applied.append(self.name)
        return StageResult(
            stage=self.name,
            output=pdf,
            sidecar=sidecar,
            changed=True,
            notes=["OCR text layer added"],
        )



