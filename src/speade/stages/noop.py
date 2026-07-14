"""Passthrough stage -- the simplest Stage implementation and a worked reference
to copy for the others: it changes nothing, just records that it ran.
"""

from __future__ import annotations

from pathlib import Path

from speade.pipeline.contract import Sidecar, StageResult


class NoopStage:
    name = "noop"

    def run(self, pdf: Path, sidecar: Sidecar) -> StageResult:
        sidecar.applied(self.name)  # record that noop ran
        return StageResult(
            stage=self.name,  # who produced this
            output=pdf,  # same bytes in/out -- noop changes nothing
            sidecar=sidecar,  # sidecar that just updated, threaded onto the next stage
            changed=False,  # this means the PDF's bytes wont be changed if "False"
        )
