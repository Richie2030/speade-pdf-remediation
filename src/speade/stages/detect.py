"""Stage 2 -- born-digital vs scanned router.

STUB. The real heuristic (text-character density vs per-page image-XObject
coverage) needs pypdf + pypdfium2, which arrive with the detector implementation
(`uv sync --extra detect`), not in the F1 skeleton. For now it records
`route=unknown` so the contract runs end-to-end and the swap seam is real.
"""

from __future__ import annotations

from pathlib import Path

from speade.pipeline.contract import Route, Sidecar, StageResult


class DetectStage:
    name = "detect"

    def run(
        self,
        pdf: Path,
        sidecar: Sidecar,
        options: dict | None = None,
    ) -> StageResult:
        updated = sidecar.with_stage(self.name)
        updated.route = Route.UNKNOWN
        updated.flags["detect"] = "stub: heuristic not yet implemented (Stage 2)"
        return StageResult(output_pdf=Path(pdf), sidecar=updated)
