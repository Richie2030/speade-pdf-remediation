"""Tests for the service layer (src/speade/service.py) -- the engine every
client (CLI, desktop UI) calls. Hermetic: the noop stage does the pipeline
work and veraPDF is mocked, so no external tools are needed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from speade import service
from speade.pipeline.contract import ApprovalStatus, Sidecar
from speade.validation.verapdf import VeraResult

PDF_BYTES = b"%PDF-1.7\n%%EOF\n"


def _write_config(tmp_path: Path) -> Path:
    """A noop-pipeline config with RELATIVE data paths (resolution under test)."""
    config = tmp_path / "config.yaml"
    config.write_text(
        "io:\n  local:\n    inbox: inbox\n    outbox: outbox\n"
        "pipeline:\n  stages:\n    passthrough: noop\n"
        "audit:\n  log_path: audit/audit.jsonl\n",
        encoding="utf-8",
    )
    (tmp_path / "inbox").mkdir()
    return config


def test_workspace_resolves_relative_paths_against_the_config_dir(tmp_path):
    # deployment-critical: paths must anchor to the config file, not the CWD.
    ws = service.workspace(_write_config(tmp_path))

    assert ws.inbox == (tmp_path / "inbox").resolve()
    assert ws.outbox == (tmp_path / "outbox").resolve()
    assert ws.audit_log == (tmp_path / "audit" / "audit.jsonl").resolve()
    assert ws.stages == {"passthrough": "noop"}
    assert ws.verapdf_profile == "ua1"
    assert ws.verapdf_cli is None


def test_run_batch_sweeps_the_configured_inbox(tmp_path):
    config = _write_config(tmp_path)
    for name in ("a.pdf", "b.pdf"):
        (tmp_path / "inbox" / name).write_bytes(PDF_BYTES)
    (tmp_path / "inbox" / "notes.txt").write_text("not a pdf", encoding="utf-8")

    items = service.run_batch(None, config)

    assert [(item.file, item.ok) for item in items] == [("a.pdf", True), ("b.pdf", True)]
    for name in ("a.pdf", "b.pdf"):
        assert (tmp_path / "outbox" / name).read_bytes() == PDF_BYTES
        assert (tmp_path / "outbox" / f"{name}.sidecar.json").is_file()
    audit = (tmp_path / "audit" / "audit.jsonl").read_text(encoding="utf-8")
    assert len(audit.splitlines()) == 2  # one audit line per file


def test_run_batch_explicit_folder_overrides_the_inbox(tmp_path):
    config = _write_config(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "c.pdf").write_bytes(PDF_BYTES)

    items = service.run_batch(elsewhere, config)

    assert [(item.file, item.ok) for item in items] == [("c.pdf", True)]
    assert (tmp_path / "outbox" / "c.pdf").is_file()


def test_run_batch_isolates_per_file_failures(tmp_path, monkeypatch):
    config = _write_config(tmp_path)
    for name in ("bad.pdf", "good.pdf"):
        (tmp_path / "inbox" / name).write_bytes(PDF_BYTES)

    real_run = service.runner.run

    def flaky(src, stages, outbox, audit_log):
        if src.name == "bad.pdf":
            raise RuntimeError("engine exploded")
        return real_run(src, stages, outbox, audit_log)

    monkeypatch.setattr(service.runner, "run", flaky)

    items = service.run_batch(None, config)

    by_name = {item.file: item for item in items}
    assert by_name["good.pdf"].ok is True
    assert by_name["bad.pdf"].ok is False
    assert "engine exploded" in by_name["bad.pdf"].error  # reported, not raised


def test_run_batch_empty_inbox_returns_nothing(tmp_path):
    assert service.run_batch(None, _write_config(tmp_path)) == []


def test_list_queue_summarises_outbox_sidecars(tmp_path):
    config = _write_config(tmp_path)
    (tmp_path / "inbox" / "a.pdf").write_bytes(PDF_BYTES)
    service.run_batch(None, config)

    queue = service.list_queue(config)

    assert len(queue) == 1
    item = queue[0]
    assert item.file == "a.pdf"
    assert item.status == "draft"  # automation never approves (mandatory gate)
    assert item.stages_applied == ["noop"]
    assert item.verapdf_passed is None
    assert item.reviewer is None


def test_list_queue_survives_a_mangled_sidecar(tmp_path):
    config = _write_config(tmp_path)
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / "broken.pdf.sidecar.json").write_text("{not json", encoding="utf-8")

    queue = service.list_queue(config)

    assert [(item.file, item.status) for item in queue] == [("broken.pdf", "invalid-sidecar")]


@pytest.mark.parametrize("approve", [True, False])
def test_decide_records_machine_verdict_and_human_decision(approve, tmp_path, monkeypatch):
    config = _write_config(tmp_path)
    (tmp_path / "inbox" / "a.pdf").write_bytes(PDF_BYTES)
    service.run_batch(None, config)
    monkeypatch.setattr(
        service.verapdf,
        "validate",
        lambda pdf, profile="ua1", cli=None: VeraResult(
            passed=False, failed_clauses=["7.1-1"], profile=profile
        ),
    )

    sidecar = service.decide(
        tmp_path / "outbox" / "a.pdf", reviewer="s123456", approve=approve, config_path=config
    )

    expected = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
    assert sidecar.approval.status == expected
    assert sidecar.approval.reviewer == "s123456"
    assert sidecar.approval.decided_at is not None
    assert sidecar.verapdf_passed is False
    assert sidecar.verapdf_failed_clauses == ["7.1-1"]
    # persisted next to the pdf, and audit-logged
    persisted = Sidecar.model_validate_json(
        (tmp_path / "outbox" / "a.pdf.sidecar.json").read_text(encoding="utf-8")
    )
    assert persisted.approval.status == expected
    events = [
        json.loads(line)
        for line in (tmp_path / "audit" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events[-1]["event"] == "verify"
    assert events[-1]["decision"] == expected.value


def test_decide_requires_a_sidecar(tmp_path):
    config = _write_config(tmp_path)
    (tmp_path / "outbox").mkdir()
    orphan = tmp_path / "outbox" / "orphan.pdf"
    orphan.write_bytes(PDF_BYTES)

    with pytest.raises(FileNotFoundError):
        service.decide(orphan, reviewer="x", approve=True, config_path=config)


def test_run_one_unknown_stage_fails_fast(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "pipeline:\n  stages:\n    tag: does-not-exist\n",
        encoding="utf-8",
    )
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(PDF_BYTES)

    with pytest.raises(KeyError, match="does-not-exist"):
        service.run_one(pdf, config)
