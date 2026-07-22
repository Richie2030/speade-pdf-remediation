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
from speade.stages.ocr import (
    OcrLine,
    OcrStage,
    page_content,
    parse_hocr,
    quantize_sizes,
    text_width,
)

_PDF_BYTES = b"%PDF-1.7\n%%EOF\n"

# Realistic Tesseract 5 hOCR: line titles use DOUBLE quotes, word titles use
# SINGLE quotes; the second line is rotated margin text (textangle) that OCRs
# to gibberish and must be dropped.
_HOCR = """
<div class='ocr_carea'>
 <p class='ocr_par'>
  <span class='ocr_line' id='line_1_1' title="bbox 100 200 900 260; baseline 0.002 -12; x_size 40">
   <span class='ocrx_word' id='word_1_1' title='bbox 100 200 260 260; x_wconf 95'>My</span>
   <span class='ocrx_word' id='word_1_2' title='bbox 280 200 520 260; x_wconf 93'>friend</span>
   <span class='ocrx_word' id='word_1_3' title='bbox 540 200 900 260; x_wconf 96'>Rita</span>
  </span>
  <span class='ocr_line' id='line_1_2' title="bbox 30 100 80 900; textangle 90; x_size 40">
   <span class='ocrx_word' id='word_1_4' title='bbox 30 100 80 400; x_wconf 40'>Aepnwi</span>
  </span>
  <span class='ocr_line' id='line_1_3' title="bbox 100 300 880 360; baseline 0.0 -11; x_size 42">
   <span class='ocrx_word' id='word_1_5' title='bbox 100 300 400 360; x_wconf 91'>works</span>
   <span class='ocrx_word' id='word_1_6' title='bbox 420 300 880 360; x_wconf 90'>here</span>
  </span>
 </p>
</div>
"""


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


def test_parse_hocr_joins_words_and_drops_rotated_lines():
    lines = parse_hocr(_HOCR)

    # words joined by REAL spaces (the whole point of the hOCR layer -- the
    # tagging engine and screen readers otherwise see "MyfriendRita").
    assert [line.text for line in lines] == ["My friend Rita", "works here"]
    # the rotated (textangle) margin line is gone -- it OCRs to gibberish.
    assert lines[0].x0 == 100 and lines[0].y1 == 260
    assert lines[0].baseline_dy == -12.0


def test_quantize_sizes_snaps_body_jitter_but_keeps_headings():
    def line(size: float) -> OcrLine:
        return OcrLine(text="x", x0=0, y0=0, x1=100, y1=30, size=size, baseline_dy=0)

    lines = quantize_sizes([line(39.0), line(40.0), line(41.5), line(80.0)])

    # jittered body sizes all snap to the median; the genuinely larger heading
    # keeps its measured size (that is what makes it a heading downstream).
    assert [ln.size for ln in lines] == [40.75, 40.75, 40.75, 80.0]


def test_text_width_uses_real_helvetica_metrics():
    # live-testing finding: an averaged char width leaves the stretched line
    # SHORT of its printed right edge, so the last words go untagged. Real AFM
    # widths: 'W' (944) is wide, 'i' (222) narrow, space is 278 -- never 500 each.
    assert text_width("Hi", 10.0) == pytest.approx((722 + 222) / 100)
    assert text_width("W W", 10.0) == pytest.approx((944 + 278 + 944) / 100)
    assert text_width("é", 10.0) == text_width("e", 10.0)  # accents measure as base
    assert text_width("iii", 10.0) < text_width("WWW", 10.0)


def test_page_content_artifacts_the_scan_and_hides_the_text():
    lines = parse_hocr(_HOCR)

    content = page_content(lines, w_px=1000, h_px=1400)

    # the scan image is BACKGROUND (artifact), not tagged document content.
    assert content.startswith(b"/Artifact BMC")
    assert b"/Im0 Do" in content.split(b"EMC")[0]
    # the text layer is invisible (render mode 3) and line-level with spaces.
    assert b"3 Tr" in content
    assert b"(My friend Rita) Tj" in content
    assert b"(works here) Tj" in content


def test_add_page_round_trips_extractable_text(tmp_path):
    pikepdf = pytest.importorskip("pikepdf", reason="needs --extra ocr")
    PIL_Image = pytest.importorskip("PIL.Image", reason="needs Pillow (via pikepdf)")
    pypdf = pytest.importorskip("pypdf", reason="needs --extra detect")
    from speade.stages.ocr import add_page

    pdf = pikepdf.Pdf.new()
    image = PIL_Image.new("RGB", (1000, 1400), "white")
    add_page(pdf, image, parse_hocr(_HOCR))
    out = tmp_path / "page.pdf"
    pdf.save(out)

    text = pypdf.PdfReader(out).pages[0].extract_text() or ""
    assert "My friend Rita" in text
    assert "works here" in text
    assert "Aepnwi" not in text  # rotated margin gibberish filtered out


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
    assert result.sidecar.ocr_layered is True  # tag treats page scans as background
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
