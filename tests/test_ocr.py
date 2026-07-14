"""Tests for the OCR stage (src/speade/stages/ocr.py) -- STRETCH.

subprocess is mocked; no real ocrmypdf/Tesseract needed. Covers the two behaviours
that matter for the pipeline handoff: on success the doc is re-routed to
BORN_DIGITAL (so tagging proceeds downstream) and a NEW file is returned; when
ocrmypdf is missing the run is flagged and left unchanged for the human queue.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from speade.pipeline.contract import Route, Sidecar
from speade.stages import ocr as ocr_module
from speade.stages.ocr import OcrStage


def _scanned_sidecar() -> Sidecar:
    return Sidecar(source_path="inbox/scan.pdf", source_sha256="abc", route=Route.SCANNED)


def test_success_reroutes_to_born_digital(monkeypatch, tmp_path):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.7\n%%EOF\n")

    def fake_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"%PDF-1.7\nocr\n%%EOF\n")  # ocrmypdf writes its output arg
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(ocr_module.subprocess, "run", fake_run)

    result = OcrStage().run(pdf, _scanned_sidecar())

    assert result.changed is True
    assert result.sidecar.route == Route.BORN_DIGITAL  # the detect->ocr->tag handoff fix
    assert result.output != pdf and result.output.exists()  # new file, not in-place
    assert "ocr" in result.sidecar.stages_applied


def test_missing_ocrmypdf_flags_and_leaves_unchanged(monkeypatch, tmp_path):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.7\n%%EOF\n")

    def boom(*args, **kwargs):
        raise FileNotFoundError("ocrmypdf")

    monkeypatch.setattr(ocr_module.subprocess, "run", boom)

    result = OcrStage().run(pdf, _scanned_sidecar())

    assert result.changed is False
    assert result.output == pdf
    assert result.sidecar.route == Route.SCANNED  # untouched on failure
    assert "ocr-unavailable" in result.sidecar.flags
