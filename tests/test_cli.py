"""Tests for the CLI (src/speade/cli.py) via Typer's CliRunner."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from speade import service
from speade.cli import app
from speade.validation.verapdf import VeraResult

cli = CliRunner()
PDF_BYTES = b"%PDF-1.7\n%%EOF\n"


@pytest.fixture(autouse=True)
def _hermetic_verapdf(monkeypatch):
    """run/run-batch score every draft with veraPDF now -- keep the CLI tests
    hermetic and fast with a canned pass."""
    monkeypatch.setattr(
        service.verapdf,
        "validate",
        lambda pdf, profile="ua1", cli=None: VeraResult(passed=True, profile=profile),
    )


def test_stages_lists_available_impls():
    result = cli.invoke(app, ["stages"])
    assert result.exit_code == 0
    assert "detect" in result.stdout
    assert "ocr" in result.stdout
    assert "tag" in result.stdout


def test_run_remediates_one_pdf_end_to_end(tmp_path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(PDF_BYTES)
    outbox = tmp_path / "outbox"
    audit = tmp_path / "audit" / "audit.jsonl"
    config = tmp_path / "config.yaml"
    config.write_text(
        "io:\n"
        "  client: local\n"
        "  local:\n"
        f"    inbox: {(tmp_path / 'inbox').as_posix()}\n"
        f"    outbox: {outbox.as_posix()}\n"
        "pipeline:\n"
        "  stages:\n"
        "    passthrough: noop\n"
        "audit:\n"
        f"  log_path: {audit.as_posix()}\n"
    )

    result = cli.invoke(app, ["run", str(pdf), "--config", str(config)])

    assert result.exit_code == 0, result.output
    assert (outbox / "sample.pdf").read_bytes() == PDF_BYTES  # remediated copy written
    assert pdf.read_bytes() == PDF_BYTES  # original untouched
    assert audit.is_file()  # audit line recorded


def test_run_batch_sweeps_a_folder(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for name in ("a.pdf", "b.pdf"):
        (inbox / name).write_bytes(PDF_BYTES)
    outbox = tmp_path / "outbox"
    config = tmp_path / "config.yaml"
    config.write_text(
        "io:\n"
        "  local:\n"
        f"    inbox: {inbox.as_posix()}\n"
        f"    outbox: {outbox.as_posix()}\n"
        "pipeline:\n"
        "  stages:\n"
        "    passthrough: noop\n"
        "audit:\n"
        f"  log_path: {(tmp_path / 'audit' / 'audit.jsonl').as_posix()}\n"
    )

    result = cli.invoke(app, ["run-batch", "--config", str(config)])

    assert result.exit_code == 0, result.output
    assert "2/2 processed" in result.stdout
    assert "veraPDF: pass" in result.stdout  # the advisory verdict, right in the sweep
    for name in ("a.pdf", "b.pdf"):
        assert (outbox / name).read_bytes() == PDF_BYTES
        # sidecars land in the configured folder (default: data/sidecars beside
        # the config), keeping the outbox deliverables-only.
        assert (tmp_path / "data" / "sidecars" / f"{name}.sidecar.json").is_file()


def test_verify_records_the_human_gate_decision(tmp_path, monkeypatch):
    from speade.pipeline.contract import ApprovalStatus, Sidecar
    from speade.validation import verapdf
    from speade.validation.verapdf import VeraResult

    # Arrange an outbox pdf + sidecar, as `run` would have left them: the pdf
    # anywhere, its sidecar in the configured sidecars folder (default
    # data/sidecars, resolved beside the config file).
    out_pdf = tmp_path / "sample.pdf"
    out_pdf.write_bytes(PDF_BYTES)
    sidecar = Sidecar(source_path="inbox/sample.pdf", source_sha256="abc", output_sha256="def")
    sidecar_path = tmp_path / "data" / "sidecars" / "sample.pdf.sidecar.json"
    sidecar_path.parent.mkdir(parents=True)
    sidecar_path.write_text(sidecar.model_dump_json(indent=2), encoding="utf-8")

    audit = tmp_path / "audit" / "audit.jsonl"
    config = tmp_path / "config.yaml"
    config.write_text(
        f"validation:\n  verapdf:\n    profile: ua1\naudit:\n  log_path: {audit.as_posix()}\n"
    )

    # veraPDF mocked -> no Docker needed; the human gate is what we're testing.
    monkeypatch.setattr(
        verapdf,
        "validate",
        lambda pdf, profile="ua1", cli=None: VeraResult(passed=True, profile=profile),
    )

    result = cli.invoke(
        app, ["verify", str(out_pdf), "--reviewer", "Alice", "--approve", "--config", str(config)]
    )

    assert result.exit_code == 0, result.output
    updated = Sidecar.model_validate_json(sidecar_path.read_text())
    assert updated.approval.status == ApprovalStatus.APPROVED
    assert updated.approval.reviewer == "Alice"
    assert updated.approval.decided_at is not None
    assert updated.verapdf_passed is True
    assert audit.is_file()  # verify event recorded


def test_verify_requires_exactly_one_decision(tmp_path):
    out_pdf = tmp_path / "sample.pdf"
    out_pdf.write_bytes(PDF_BYTES)
    out_pdf.with_name("sample.pdf.sidecar.json").write_text("{}")
    # Neither --approve nor --reject is an error (no silent default at the gate).
    result = cli.invoke(app, ["verify", str(out_pdf), "--reviewer", "Alice"])
    assert result.exit_code != 0
