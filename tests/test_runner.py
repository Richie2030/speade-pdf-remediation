"""Tests for the runner (src/speade/pipeline/runner.py).

The end-to-end smoke test for the offline core: run a PDF through the noop stage and
assert the three contract outputs -- an unmodified original + an outbox copy, a sidecar
with the source/output hashes, and exactly one audit line linking them.
"""

from __future__ import annotations

from speade.audit.log import read_events
from speade.pipeline import runner
from speade.pipeline.contract import Sidecar
from speade.stages.noop import NoopStage

PDF_BYTES = b"%PDF-1.7\n%%EOF\n"


def test_run_produces_outbox_copy_sidecar_and_audit_line(tmp_path):
    src = tmp_path / "inbox" / "sample.pdf"
    src.parent.mkdir()
    src.write_bytes(PDF_BYTES)
    outbox = tmp_path / "outbox"
    audit = tmp_path / "audit" / "audit.jsonl"

    sidecar = runner.run(src, [NoopStage()], outbox, audit_log=audit)

    # 1) outbox copy exists and byte-equals the input; the original is untouched.
    out_pdf = outbox / "sample.pdf"
    assert out_pdf.read_bytes() == PDF_BYTES
    assert src.read_bytes() == PDF_BYTES

    # 2) sidecar persisted next to the output, with the source + output hashes (LF-only).
    side_path = outbox / "sample.pdf.sidecar.json"
    persisted = Sidecar.model_validate_json(side_path.read_text())
    assert persisted.stages_applied == ["noop"]
    assert persisted.source_sha256 and persisted.output_sha256
    assert persisted.source_sha256 == persisted.output_sha256  # noop changed nothing
    assert b"\r\n" not in side_path.read_bytes()

    # 3) exactly one audit line, linking source -> output by hash.
    events = read_events(audit)
    assert len(events) == 1
    assert events[0]["event"] == "run"
    assert events[0]["source_sha256"] == persisted.source_sha256
    assert events[0]["output_sha256"] == persisted.output_sha256
    assert events[0]["stages_applied"] == ["noop"]

    assert sidecar.output_sha256 == persisted.output_sha256  # returned == persisted


def test_run_without_audit_writes_no_log_and_keeps_original(tmp_path):
    src = tmp_path / "sample.pdf"
    src.write_bytes(PDF_BYTES)
    outbox = tmp_path / "outbox"

    runner.run(src, [NoopStage()], outbox)  # audit_log unset

    assert not (tmp_path / "audit").exists()
    assert src.read_bytes() == PDF_BYTES
