"""Tests for the OCR stage (src/speade/stages/ocr.py) -- STRETCH.

The engine chain (pypdfium2 render -> tesseract -> pikepdf merge) is mocked at
the `_ocr` / `_missing_dependency` seams, so no real Tesseract is needed here
(the corpus regression harness covers the real chain end-to-end). Covers the
pipeline handoffs that matter:

  - only SCANNED docs are OCR'd: born-digital and unknown (mixed) pass through
    untouched -- rasterising real text would degrade it (do-not-degrade);
  - unreadable docs (flagged by detect) skip with their own flag;
  - success re-routes to BORN_DIGITAL (so tagging proceeds) via a NEW file;
  - a missing engine or a failed run is FLAGGED and left unchanged for the
    human queue -- OCR problems never crash a batch.
"""

from __future__ import annotations

import pytest

from speade.pipeline.contract import Route, Sidecar
from speade.stages.ocr import OcrStage

_PDF_BYTES = b"%PDF-1.7\n%%EOF\n"


def _sidecar(route: Route = Route.SCANNED) -> Sidecar:
    return Sidecar(source_path="inbox/scan.pdf", source_sha256="abc", route=route)


def _stage(monkeypatch, ocr=None, missing=None):
    """An OcrStage with the engine seams stubbed: `ocr` fakes _ocr(pdf, out);
    `missing` fakes the _missing_dependency() answer (default: all present)."""
    stage = OcrStage()
    monkeypatch.setattr(stage, "_missing_dependency", lambda: missing)
    if ocr is not None:
        monkeypatch.setattr(stage, "_ocr", ocr)
    return stage


def test_success_reroutes_to_born_digital(monkeypatch, tmp_path):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(_PDF_BYTES)
    calls = []

    def fake_ocr(src, out):
        calls.append((src, out))
        out.write_bytes(b"%PDF-1.7\nocr\n%%EOF\n")

    result = _stage(monkeypatch, ocr=fake_ocr).run(pdf, _sidecar())

    assert result.changed is True
    assert result.sidecar.route == Route.BORN_DIGITAL  # the detect->ocr->tag handoff
    assert result.output == tmp_path / "scan.ocr.pdf"  # new file, not in-place
    assert result.output.exists()
    assert calls == [(pdf, result.output)]
    assert pdf.read_bytes() == _PDF_BYTES  # input untouched
    assert result.sidecar.stages_applied == ["ocr"]


@pytest.mark.parametrize("route", [Route.BORN_DIGITAL, Route.UNKNOWN])
def test_docs_with_real_text_pass_through_untouched(route, monkeypatch, tmp_path):
    # born-digital has text; unknown (mixed) keeps its true text pages -- P1.4
    # tags it as-is. Neither may be rasterised. Expected, so no flag noise.
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(_PDF_BYTES)
    calls = []

    result = _stage(monkeypatch, ocr=lambda s, o: calls.append(s)).run(pdf, _sidecar(route))

    assert result.changed is False
    assert result.output == pdf
    assert result.sidecar.route == route  # never re-routed without OCR
    assert result.sidecar.flags == []
    assert result.sidecar.stages_applied == ["ocr"]
    assert calls == []  # engine never invoked


def test_unreadable_input_skips_with_flag(monkeypatch, tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(_PDF_BYTES)
    sidecar = _sidecar()
    sidecar.flags.append("unreadable-encrypted-password-required")
    calls = []

    result = _stage(monkeypatch, ocr=lambda s, o: calls.append(s)).run(pdf, sidecar)

    assert result.changed is False
    assert "ocr-skipped-unreadable" in result.sidecar.flags
    assert calls == []


def test_missing_engine_flags_and_leaves_unchanged(monkeypatch, tmp_path):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(_PDF_BYTES)

    result = _stage(monkeypatch, missing="tesseract not found").run(pdf, _sidecar())

    assert result.changed is False
    assert result.output == pdf
    assert result.sidecar.route == Route.SCANNED  # untouched on failure
    assert "ocr-unavailable" in result.sidecar.flags
    assert result.notes == ["tesseract not found"]


def test_engine_failure_flags_and_leaves_unchanged(monkeypatch, tmp_path):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(_PDF_BYTES)

    def boom(src, out):
        raise RuntimeError("tesseract failed on page 1 (exit 1): boom")

    result = _stage(monkeypatch, ocr=boom).run(pdf, _sidecar())

    assert result.changed is False
    assert result.output == pdf
    assert result.sidecar.route == Route.SCANNED
    assert "ocr-failed" in result.sidecar.flags
    assert "tesseract failed on page 1" in result.notes[0]
