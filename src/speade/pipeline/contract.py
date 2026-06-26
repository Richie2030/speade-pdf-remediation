"""The tool-agnostic stage contract.

Every pipeline stage conforms to ONE fixed shape:

    (input PDF + sidecar) -> (output PDF + updated sidecar)

The sidecar is pipeline-internal metadata that travels alongside the PDF. It is
NOT a tracker input and it terminates at the human gate (plan §1d / ARCH3).
Stages speak only this neutral contract -- never each other's tool-native types
(pdfix objects, pdfium page handles, ...) -- so any implementation is swappable
by config. Engine adapters shell out to copyleft/CLI tools at arms length; they
never `import` them in-process (licence rule L2 / arms-length copyleft rule).
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class Route(StrEnum):
    """How a document should flow after type detection (Stage 2)."""

    UNKNOWN = "unknown"
    BORN_DIGITAL = "born_digital"
    SCANNED = "scanned"


class Sidecar(BaseModel):
    """Pipeline-internal metadata carried with a PDF between stages."""

    source_path: str
    source_sha256: str | None = None
    route: Route = Route.UNKNOWN
    stages_applied: list[str] = Field(default_factory=list)
    flags: dict[str, str] = Field(default_factory=dict)

    def with_stage(self, name: str) -> Sidecar:
        """Return a deep copy with `name` appended to the applied-stages trail."""
        return self.model_copy(
            deep=True,
            update={"stages_applied": [*self.stages_applied, name]},
        )


class StageResult(BaseModel):
    """What a stage hands back: the (possibly rewritten) PDF and updated sidecar."""

    output_pdf: Path
    sidecar: Sidecar


@runtime_checkable
class Stage(Protocol):
    """A swappable processing step.

    Implementations operate on a working copy (the runner guarantees the original
    is never mutated) and return a :class:`StageResult`.
    """

    name: str

    def run(
        self,
        pdf: Path,
        sidecar: Sidecar,
        options: dict | None = None,
    ) -> StageResult: ...
