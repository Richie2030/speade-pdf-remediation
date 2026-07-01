"""The tool-agnostic stage contract: the shared (PDF + sidecar) -> (PDF + sidecar)
shape every stage implements, so stages are swappable by config.

Define here: the Route enum (born-digital / scanned / unknown), the Sidecar and
StageResult models, and the Stage protocol. Engine adapters shell out to
copyleft/CLI tools at arms length -- never import a GPL/AGPL library (rule L2).

Pure types only: no file I/O, no subprocess/Docker, no engine imports. This module
is the root of the dependency graph -- keeping it dependency-free (stdlib + pydantic)
is what lets every stage/runner/registry import it without pulling in heavy engines.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class Route(StrEnum):
    """How a PDF flows downstream: set by the `detect` stage, read by later stages."""

    BORN_DIGITAL = "born_digital"  # real text -> straight to tagging
    SCANNED = "scanned"  # image-only -> OCR before tagging
    UNKNOWN = "unknown"  # ambiguous/mixed -> treat conservatively as scanned


class ApprovalStatus(StrEnum):
    """Human-gate decision. Automation only ever produces DRAFT; a human sets the rest."""

    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"


class Approval(BaseModel):
    """The human verification gate's record on a sidecar (the mandatory-gate invariant)."""

    status: ApprovalStatus = ApprovalStatus.DRAFT
    reviewer: str | None = None  # who signed off (set by `speade verify`)
    decided_at: datetime | None = None
    # TODO: decide whether the sign-off should also pin the output_sha256 it approved.


class Sidecar(BaseModel):
    """Metadata connected to the file, updated at every stage.

    Pipeline-internal: it threads through every stage alongside the PDF. NOT a
    tracker/Canvas input; it terminates at the human gate. Stages read and update it,
    but speak only this neutral type -- never each other's tool-native types. That is
    what keeps every stage swappable by config. Persisted as `<pdf>.sidecar.json`.
    """

    # provenance / trust trail (hashes prove the exact bytes in and out)
    source_path: str  # store POSIX (Path.as_posix()) for the Linux target
    source_sha256: str
    output_sha256: str | None = None  # set once the run writes an output copy

    # routing + what has run
    route: Route = Route.UNKNOWN
    stages_applied: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)  # non-fatal notes, e.g. "garbage-ocr"

    # gates
    verapdf_passed: bool | None = None
    verapdf_failed_clauses: list[str] = Field(default_factory=list)
    approval: Approval = Field(default_factory=Approval)
    # TODO: flat verapdf_* fields (above) vs storing the full VeraResult. Keep this module
    # free of a `validation.verapdf` import so contract.py stays the dependency root.

    # TODO: add a helper to record a stage run, e.g.
    #   def applied(self, name: str) -> None: self.stages_applied.append(name)


class StageResult(BaseModel):
    """What one Stage returns: the (possibly new) output PDF + the updated sidecar.

    Mirrors the contract `(pdf, sidecar) -> (pdf, sidecar)`: the runner feeds `output`
    and `sidecar` into the next stage.
    """

    stage: str  # impl name that ran (e.g. "noop", "detect")
    output: Path  # the working-copy PDF after this stage
    sidecar: Sidecar  # updated sidecar to thread onward
    changed: bool = False  # did this stage modify the PDF bytes?
    notes: list[str] = Field(default_factory=list)
    # TODO: confirm this shape once runner.py is written -- e.g. how failures are
    # represented, and whether `output` can differ from the input for an in-place engine.


@runtime_checkable
class Stage(Protocol):
    """The swappable step every stage implements: (input PDF + sidecar) -> StageResult.

    A Protocol (structural typing): any object with a matching `run` is a Stage -- no base
    class to inherit. `registry.get_stage(name)` returns one of these.
    """

    def run(self, pdf: Path, sidecar: Sidecar) -> StageResult:
        """Run this stage on `pdf` (a working copy, never the original) and return the
        updated (PDF + sidecar). Must not mutate the input in place (reversibility)."""
        ...
