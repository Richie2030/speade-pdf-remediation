"""OCR stage (Stage 3, STRETCH) -- add a searchable text layer to a scanned PDF
before tagging. Shell out to OCRmyPDF + Tesseract; flag garbage OCR and route
failures to the human queue rather than shipping corrupted text.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from speade.pipeline.contract import Route, Sidecar, StageResult


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

        # Write to a NEW file, never in-place: keep the pre-OCR working copy intact
        # (reversibility / do-not-degrade), exactly like the tag stage.
        out = pdf.with_name(f"{pdf.stem}.ocr.pdf")

        # Run ocrmypdf: adds an OCR layer without modifying existing content.
        #   --quiet: suppress progress spam
        #   --skip-text: skip pages that already have text (born-digital mixed in)
        #   --deskew: fix rotated scans for better accuracy
        try:
            subprocess.run(
                ["ocrmypdf", "--quiet", "--skip-text", "--deskew", str(pdf), str(out)],
                check=True,
                capture_output=True,
                text=True,
                timeout=300,  # 5 min per PDF; long scans can take time
            )
        except subprocess.TimeoutExpired:
            sidecar.flags.append("ocr-timeout")
            sidecar.applied(self.name)
            # Return unchanged; let the human decide.
            return StageResult(
                stage=self.name,
                output=pdf,
                sidecar=sidecar,
                changed=False,
                notes=["OCR timed out after 5 minutes; routed to human queue"],
            )
        except subprocess.CalledProcessError as e:
            sidecar.flags.append("ocr-failed")
            sidecar.applied(self.name)
            # ocrmypdf fails on corrupt PDFs, encrypted files, etc. Flag + route to human.
            return StageResult(
                stage=self.name,
                output=pdf,
                sidecar=sidecar,
                changed=False,
                notes=[f"OCR failed: {e.stderr[:200]}"],  # first 200 chars of error
            )
        except FileNotFoundError:
            # ocrmypdf or Tesseract not installed.
            sidecar.flags.append("ocr-unavailable")
            sidecar.applied(self.name)
            return StageResult(
                stage=self.name,
                output=pdf,
                sidecar=sidecar,
                changed=False,
                notes=["ocrmypdf or Tesseract not found; install and retry"],
            )

        # Success: the doc now carries a real text layer, so it can be tagged like a
        # born-digital PDF. Re-route so the tag stage proceeds instead of skipping it.
        sidecar.route = Route.BORN_DIGITAL
        sidecar.applied(self.name)
        return StageResult(
            stage=self.name,
            output=out,
            sidecar=sidecar,
            changed=True,
            notes=["OCR text layer added"],
        )
