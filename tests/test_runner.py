"""Tests for the runner (src/speade/pipeline/runner.py).

The end-to-end smoke test for the offline core: run a PDF through the noop stage and
assert the three contract outputs -- an unmodified original + an outbox copy, a sidecar
with the source/output hashes, and exactly one audit line linking them. Plus the
outbox-hygiene rules from live testing: one final PDF + sidecar per input (no stray
`X.ocr.pdf` intermediates), and a failed run leaving nothing behind.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from speade.audit.log import read_events
from speade.pipeline import runner
from speade.pipeline.contract import Sidecar, StageResult
from speade.stages.noop import NoopStage

PDF_BYTES = b"%PDF-1.7\n%%EOF\n"


class RewriteStage:
    """A stage that writes a NEW suffixed working file, exactly like ocr/tag do."""

    def __init__(self, name: str, suffix: str) -> None:
        self.name = name
        self.suffix = suffix

    def run(self, pdf: Path, sidecar: Sidecar) -> StageResult:
        out = pdf.with_name(f"{pdf.stem}.{self.suffix}.pdf")
        out.write_bytes(pdf.read_bytes() + f"% {self.name}\n".encode())
        sidecar.applied(self.name)
        return StageResult(stage=self.name, output=out, sidecar=sidecar, changed=True)


class BoomStage:
    """A stage that dies mid-pipeline (a missing/broken engine)."""

    name = "boom"

    def run(self, pdf: Path, sidecar: Sidecar) -> StageResult:
        raise RuntimeError("engine exploded")


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
    assert events[0]["file"] == "sample.pdf"  # the History view names the document
    assert events[0]["ts"]  # ...and shows when it happened
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


def test_intermediate_stage_files_are_swept_from_the_outbox(tmp_path):
    # live-testing finding: the outbox held `doc.pdf` AND the orphaned
    # `doc.ocr.pdf`. Reviewers must only ever see one final PDF + its sidecar.
    src = tmp_path / "inbox" / "doc.pdf"
    src.parent.mkdir()
    src.write_bytes(PDF_BYTES)
    outbox = tmp_path / "outbox"
    stages = [RewriteStage("ocr", "ocr"), RewriteStage("tag", "tagged")]

    sidecar = runner.run(src, stages, outbox, tmp_path / "audit.jsonl")

    assert sorted(p.name for p in outbox.iterdir()) == ["doc.pdf", "doc.pdf.sidecar.json"]
    final = (outbox / "doc.pdf").read_bytes()
    assert final.endswith(b"% tag\n")  # the LAST stage's output became the final
    assert sidecar.output_sha256 == hashlib.sha256(final).hexdigest()
    assert src.read_bytes() == PDF_BYTES  # original untouched, as ever


def test_failed_run_leaves_no_partial_drafts(tmp_path):
    # a mid-pipeline engine failure must not leave half-processed files for the
    # review queue to trip over -- the outbox stays clean, the error propagates.
    src = tmp_path / "inbox" / "doc.pdf"
    src.parent.mkdir()
    src.write_bytes(PDF_BYTES)
    outbox = tmp_path / "outbox"
    stages = [RewriteStage("ocr", "ocr"), BoomStage()]

    with pytest.raises(RuntimeError, match="engine exploded"):
        runner.run(src, stages, outbox, tmp_path / "audit.jsonl")

    assert list(outbox.iterdir()) == []  # no drafts, no sidecar, nothing
    assert src.read_bytes() == PDF_BYTES  # original untouched
    assert not (tmp_path / "audit.jsonl").exists()  # no run was completed


def test_sidecars_can_live_in_their_own_folder(tmp_path):
    # the service passes sidecar_dir (config io.local.sidecars) so the outbox
    # holds deliverable PDFs only; direct callers omitting it keep the old
    # beside-the-output default.
    src = tmp_path / "inbox" / "doc.pdf"
    src.parent.mkdir()
    src.write_bytes(PDF_BYTES)
    outbox = tmp_path / "outbox"
    side_dir = tmp_path / "sidecars"

    runner.run(src, [NoopStage()], outbox, tmp_path / "audit.jsonl", sidecar_dir=side_dir)

    assert [p.name for p in outbox.iterdir()] == ["doc.pdf"]
    assert (side_dir / "doc.pdf.sidecar.json").is_file()


def test_failed_rerun_cleans_the_sidecar_folder_too(tmp_path):
    src = tmp_path / "inbox" / "doc.pdf"
    src.parent.mkdir()
    src.write_bytes(PDF_BYTES)
    outbox = tmp_path / "outbox"
    side_dir = tmp_path / "sidecars"
    runner.run(src, [NoopStage()], outbox, sidecar_dir=side_dir)  # first run succeeds

    with pytest.raises(RuntimeError, match="engine exploded"):
        runner.run(src, [BoomStage()], outbox, sidecar_dir=side_dir)  # re-run fails

    assert list(outbox.iterdir()) == []
    assert list(side_dir.iterdir()) == []  # no phantom sidecar in its folder either


def test_failed_rerun_does_not_leave_a_phantom_sidecar(tmp_path):
    # re-running a previously processed file overwrites its outbox copy; if that
    # re-run FAILS, the old sidecar must not survive as a queue entry pointing
    # at a PDF the failure cleanup removed.
    src = tmp_path / "inbox" / "doc.pdf"
    src.parent.mkdir()
    src.write_bytes(PDF_BYTES)
    outbox = tmp_path / "outbox"
    runner.run(src, [NoopStage()], outbox)  # first run succeeds
    assert (outbox / "doc.pdf.sidecar.json").is_file()

    with pytest.raises(RuntimeError, match="engine exploded"):
        runner.run(src, [BoomStage()], outbox)  # re-run fails

    assert list(outbox.iterdir()) == []  # PDF and stale sidecar both gone
