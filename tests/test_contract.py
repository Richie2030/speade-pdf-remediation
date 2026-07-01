"""Tests for the stage contract (src/speade/pipeline/contract.py).

Exercises contract.py in ISOLATION -- no stage implementation is needed yet, so this
file is green as soon as contract.py exists. The NoopStage-specific conformance check
belongs in test_noop.py once that stage lands.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from speade.pipeline.contract import (
    Approval,
    ApprovalStatus,
    Route,
    Sidecar,
    Stage,
    StageResult,
)


def _sidecar(**overrides) -> Sidecar:
    """A minimal valid Sidecar; pass overrides to vary a field."""
    base = {"source_path": "data/inbox/sample.pdf", "source_sha256": "abc123"}
    return Sidecar(**{**base, **overrides})


def test_sidecar_defaults():
    sc = _sidecar()
    assert sc.route is Route.UNKNOWN
    assert sc.output_sha256 is None
    assert sc.stages_applied == []
    assert sc.flags == []
    assert sc.verapdf_passed is None
    assert sc.verapdf_failed_clauses == []
    assert sc.approval.status is ApprovalStatus.DRAFT
    assert sc.approval.reviewer is None


def test_sidecar_requires_source_fields():
    # source_sha256 is required -- the trust trail cannot be optional.
    with pytest.raises(ValidationError):
        Sidecar(source_path="data/inbox/sample.pdf")


def test_sidecar_list_defaults_are_independent():
    # Guards the Field(default_factory=list) choice: no shared mutable default leaks
    # between two Sidecars.
    a, b = _sidecar(), _sidecar()
    a.flags.append("garbage-ocr")
    a.stages_applied.append("noop")
    assert b.flags == []
    assert b.stages_applied == []


def test_sidecar_round_trips_through_json():
    # The blueprint <-> instance relationship: model -> JSON -> model is lossless.
    sc = _sidecar(route=Route.BORN_DIGITAL, output_sha256="def456")
    sc.stages_applied.append("noop")
    sc.approval = Approval(status=ApprovalStatus.APPROVED, reviewer="alice")
    restored = Sidecar.model_validate_json(sc.model_dump_json())
    assert restored == sc


def test_route_is_string_enum():
    assert Route.BORN_DIGITAL == "born_digital"
    # Serializes to the plain string in the sidecar JSON.
    assert '"route":"scanned"' in _sidecar(route=Route.SCANNED).model_dump_json()


def test_stage_result_carries_pdf_and_sidecar():
    result = StageResult(stage="noop", output=Path("data/outbox/sample.pdf"), sidecar=_sidecar())
    assert result.changed is False
    assert result.notes == []
    restored = StageResult.model_validate_json(result.model_dump_json())
    assert restored.stage == "noop"
    assert restored.output == Path("data/outbox/sample.pdf")


class _DummyStage:
    """A structurally-valid Stage: it has a matching run(). No base class to inherit."""

    def run(self, pdf: Path, sidecar: Sidecar) -> StageResult:
        return StageResult(stage="dummy", output=pdf, sidecar=sidecar)


class _NotAStage:
    """Missing run() -- must NOT satisfy the protocol."""


def test_stage_protocol_is_structural():
    # @runtime_checkable Protocol: conformance is by shape (has run), not inheritance.
    assert isinstance(_DummyStage(), Stage)
    assert not isinstance(_NotAStage(), Stage)
