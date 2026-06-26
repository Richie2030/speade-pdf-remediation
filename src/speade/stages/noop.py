"""Passthrough stage -- proves the contract runs end-to-end with zero deps."""

from __future__ import annotations

from pathlib import Path

from speade.pipeline.contract import Sidecar, StageResult


class NoopStage:
    name = "noop"

    def run(
        self,
        pdf: Path,
        sidecar: Sidecar,
        options: dict | None = None,
    ) -> StageResult:
        return StageResult(output_pdf=Path(pdf), sidecar=sidecar.with_stage(self.name))
