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

# Pydantic used for the JSON
from pydantic import BaseModel, Field


class Route(StrEnum):
    """How a PDF flows downstream: set by the `detect` stage, read by later stages."""

    # Not running everything through both OCR and Tagging: it degrades quality
    # and is slower / more expensive for no gain.

    BORN_DIGITAL = "born_digital"  # real text -> straight to tagging
    SCANNED = "scanned"  # image-only -> OCR before tagging
    # UNKNOWN is kept as a distinct value so human reviewers can see the ambiguity.
    UNKNOWN = "unknown"  # ambiguous/mixed -> treat conservatively as scanned


class ApprovalStatus(StrEnum):
    """Human-gate decision. Automation only ever produces DRAFT; a human sets the rest."""

    # Automation does not set APPROVED or REJECTED; only a human can.
    DRAFT = "draft"  # Automation sets DRAFT
    APPROVED = "approved"  # Human sets APPROVED
    REJECTED = "rejected"  # Human sets REJECTED


class Approval(BaseModel):
    """The human verification gate's record on a sidecar (the mandatory-gate invariant)."""

    # Updates status, reviewer, and decided_at when a human signs off.
    status: ApprovalStatus = ApprovalStatus.DRAFT
    reviewer: str | None = None  # who signed off (set by `speade verify`)
    decided_at: datetime | None = None
    # TODO: decide whether the sign-off should also pin the output_sha256 it approved.


class Sidecar(BaseModel):
    """Metadata connected to the file, updated at every stage.

    Pipeline-internal: it threads through every stage alongside the PDF. NOT a
    tracker input; it terminates at the human gate. Stages read and update it,
    but speak only this neutral type -- never each other's tool-native types. That is
    what keeps every stage swappable by config. Persisted as `<pdf>.sidecar.json`.

    It starts nearly empty (just the source path + fingerprint) and fills up as it
    travels: detect writes route, tag adds to stages_applied, veraPDF fills the gate
    fields, a human fills approval -- by the end it's a full story of what happened.
    """

    # provenance / trust trail (hashes prove the exact bytes in and out)
    source_path: str  # where the original came from; store POSIX for the Linux target
    source_sha256: str  # "fingerprint" of the original's exact bytes (any change flips it)
    # "fingerprint" of the finished file; with source_sha256 it proves approved == shipped
    output_sha256: str | None = None  # set once the run writes an output copy

    # routing + what has run
    route: Route = Route.UNKNOWN  # set by detect: BORN_DIGITAL / SCANNED / UNKNOWN
    already_tagged: bool = False  # detect found an existing structure tree -> don't re-tag

    # running list of which stages have run, e.g. ["detect", "tag"] -- a trail of work done
    stages_applied: list[str] = Field(default_factory=list)
    # non-fatal warning notes a human should see, e.g. "garbage-ocr"
    flags: list[str] = Field(default_factory=list)

    # gates
    verapdf_passed: bool | None = None  # did the automated PDF/UA checker pass
    verapdf_failed_clauses: list[str] = Field(default_factory=list)  # the failed PDF/UA rules
    approval: Approval = Field(default_factory=Approval)  # human sign-off (who, when, status)
    # TODO: flat verapdf_* fields (above) vs storing the full VeraResult. Keep this module
    # free of a `validation.verapdf` import so contract.py stays the dependency root.

    def applied(self, name: str) -> None:
        """Record that a stage ran, by appending its impl name to stages_applied."""
        self.stages_applied.append(name)


class StageResult(BaseModel):
    """What one Stage returns: the (possibly new) output PDF + the updated sidecar.

    Mirrors the contract `(pdf, sidecar) -> (pdf, sidecar)`: the runner feeds `output`
    and `sidecar` into the next stage.
    """

    # The name of the stage that produced this result.
    stage: str  # impl name that ran (e.g. "noop", "detect")
    output: Path  # the working-copy PDF after this stage
    sidecar: Sidecar  # updated sidecar to thread onward
    changed: bool = False  # did this stage modify the PDF bytes?
    notes: list[str] = Field(default_factory=list)

    # Shape confirmed against runner.py: `output` may differ from the input (tag
    # writes a new file; the runner renames it back). Failures propagate as
    # exceptions (fail-loud) -- there is no failure field in v1.


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
