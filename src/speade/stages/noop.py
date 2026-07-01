"""Passthrough stage -- the simplest Stage implementation and a worked reference
to copy for the others: it changes nothing, just records that it ran.
"""

from __future__ import annotations

from pathlib import Path

from speade.pipeline.contract import Sidecar, StageResult


class NoopStage:
    """Passthrough: change nothing, just record that this stage ran.

    The worked reference every other stage copies. It satisfies the Stage protocol
    structurally (a matching `run`) -- no base class to inherit. The registry maps the
    config name "noop" to this class.
    """

    name = "noop"

    def run(self, pdf: Path, sidecar: Sidecar) -> StageResult:
        # The runner already works on a COPY, so a stage never touches the original.
        # Noop goes further and touches nothing at all: no file I/O, bytes unchanged.
        sidecar.stages_applied.append(self.name)
        # TODO(design): mutate the passed sidecar in place (as here) vs return a copy?
        # If contract.Sidecar grows an `applied()` helper, use sidecar.applied(self.name).
        return StageResult(stage=self.name, output=pdf, sidecar=sidecar, changed=False)
