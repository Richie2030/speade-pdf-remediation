"""OCR stage (Stage 3, STRETCH) -- add a searchable text layer to a scanned PDF
before tagging. Shell out to OCRmyPDF + Tesseract; flag garbage OCR and route
failures to the human queue rather than shipping corrupted text.
"""

from __future__ import annotations

from pathlib import Path

from speade.pipeline.contract import Sidecar, StageResult
