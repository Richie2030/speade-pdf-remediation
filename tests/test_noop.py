"""Tests for NoopStage (src/speade/stages/noop.py).

Locks in the two guarantees the passthrough must never break: it satisfies the Stage
protocol, and it does not mutate (or even touch) the input PDF -- the do-not-degrade /
reversibility invariant every stage inherits.
"""

from __future__ import annotations

from speade.pipeline.contract import Sidecar, Stage, StageResult
from speade.stages.noop import NoopStage


def _sidecar(**overrides) -> Sidecar:
    base = {"source_path": "data/inbox/sample.pdf", "source_sha256": "abc123"}
    return Sidecar(**{**base, **overrides})


def test_noop_satisfies_stage_protocol():
    assert isinstance(NoopStage(), Stage)


def test_noop_does_not_mutate_the_input_bytes(tmp_path):
    pdf = tmp_path / "working.pdf"
    original = b"%PDF-1.7\n...bytes...\n%%EOF\n"
    pdf.write_bytes(original)

    NoopStage().run(pdf, _sidecar())

    assert pdf.read_bytes() == original  # untouched


def test_noop_passes_the_pdf_through_and_records_itself(tmp_path):
    pdf = tmp_path / "working.pdf"
    pdf.write_bytes(b"%PDF-1.7\n%%EOF\n")

    result = NoopStage().run(pdf, _sidecar())

    assert isinstance(result, StageResult)
    assert result.stage == "noop"
    assert result.output == pdf  # same working copy, passed straight through
    assert result.changed is False
    assert result.sidecar.stages_applied == ["noop"]  # recorded that it ran
