"""Stage 2 -- born-digital vs scanned router.

Decide how a PDF flows downstream: a born-digital PDF (real text) goes straight
to tagging; a scanned (image-only) PDF must go through OCR first; ambiguous/mixed
routes conservatively to scanned. Heuristic: per-page text-char count (pypdf) vs
image coverage (pypdfium2). Deps: `uv sync --extra detect`.
"""

from __future__ import annotations

from pathlib import Path

from speade.pipeline.contract import Route, Sidecar, StageResult
