"""Tests for the tag stage (src/speade/stages/tag.py).

The engine is OpenDataLoader (Apache-2.0) run as an arms-length CLI. These tests
cover the routing + orchestration without needing the real engine or Java:

  - a scanned doc (no text yet) is SKIPPED and flagged -- the engine is never
    invoked, and the PDF is left untouched;
  - an unreadable doc (encrypted/corrupt, `unreadable-*` flagged by detect) is
    SKIPPED the same way -- reason already on the sidecar, human gate decides;
  - an unknown (mixed) doc is tagged ANYWAY, with a reviewer flag (P1.4 policy);
  - a born-digital doc invokes the engine and returns a NEW tagged file, never
    mutating its input in place;
  - a missing engine fails loud (RuntimeError), never a silent untagged "success".

`_tag` is monkeypatched to a fake to exercise `run`'s orchestration; a separate
test fakes `subprocess.run` to exercise the real `_tag` -- mirroring test_verapdf.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from speade.pipeline.contract import Route, Sidecar, Stage
from speade.stages import tag as tag_module
from speade.stages.tag import TagStage

_PDF_BYTES = b"%PDF-1.7\n%%EOF\n"


def _sidecar(route: Route) -> Sidecar:
    return Sidecar(source_path="doc.pdf", source_sha256="0", route=route)


def test_tagstage_conforms_to_stage_protocol():
    # structural typing: TagStage is a Stage without inheriting anything.
    assert isinstance(TagStage(), Stage)


def test_scanned_route_skips_engine_and_flags(tmp_path, monkeypatch):
    # A doc with no real text (scanned) can't be tagged until OCR runs -- tag
    # must skip it, flag it, and NOT touch the engine.
    calls = []
    stage = TagStage()
    monkeypatch.setattr(
        stage, "_tag", lambda pdf, out, title, strip_background=False: calls.append((pdf, out))
    )

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(_PDF_BYTES)

    result = stage.run(pdf, _sidecar(Route.SCANNED))

    assert result.changed is False
    assert "tag-skipped-needs-ocr" in result.sidecar.flags
    assert result.sidecar.stages_applied == ["tag"]
    assert result.output == pdf  # passthrough: same file, untouched
    assert calls == []  # engine never invoked on a textless doc
    assert pdf.read_bytes() == _PDF_BYTES  # input bytes unchanged


def test_unknown_route_tags_anyway_with_reviewer_flag(tmp_path, monkeypatch):
    # P1.4 policy: a mixed/ambiguous doc IS tagged -- its text pages gain
    # structure now -- and the flag sends the reviewer to check coverage.
    calls = []

    def fake_tag(pdf, out, title, strip_background=False):
        calls.append((pdf, out))
        out.write_bytes(b"%PDF-1.7 tagged\n%%EOF\n")

    stage = TagStage()
    monkeypatch.setattr(stage, "_tag", fake_tag)

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(_PDF_BYTES)

    result = stage.run(pdf, _sidecar(Route.UNKNOWN))

    assert result.changed is True
    assert "tag-ran-on-unknown-route" in result.sidecar.flags
    assert "tag-skipped-needs-ocr" not in result.sidecar.flags
    assert calls == [(pdf, result.output)]  # engine invoked exactly once


def test_unreadable_input_skips_engine_and_flags(tmp_path, monkeypatch):
    # An input detect flagged unreadable (encrypted/corrupt) must never reach
    # the engine -- it flows to the gate untouched, reason already recorded.
    calls = []
    stage = TagStage()
    monkeypatch.setattr(
        stage, "_tag", lambda pdf, out, title, strip_background=False: calls.append((pdf, out))
    )

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(_PDF_BYTES)
    sidecar = _sidecar(Route.UNKNOWN)
    sidecar.flags.append("unreadable-encrypted-password-required")

    result = stage.run(pdf, sidecar)

    assert result.changed is False
    assert "tag-skipped-unreadable" in result.sidecar.flags
    assert result.output == pdf
    assert calls == []  # engine never invoked on an unreadable doc
    assert pdf.read_bytes() == _PDF_BYTES


def test_already_tagged_pdf_skips_engine_and_flags(tmp_path, monkeypatch):
    # never clobber an existing structure tree: even a born-digital doc must skip
    # the engine when it is already tagged, and be left untouched.
    calls = []
    stage = TagStage()
    monkeypatch.setattr(
        stage, "_tag", lambda pdf, out, title, strip_background=False: calls.append((pdf, out))
    )

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(_PDF_BYTES)
    sidecar = Sidecar(
        source_path="doc.pdf", source_sha256="0", route=Route.BORN_DIGITAL, already_tagged=True
    )

    result = stage.run(pdf, sidecar)

    assert result.changed is False
    assert "tag-skipped-already-tagged" in result.sidecar.flags
    assert result.sidecar.stages_applied == ["tag"]
    assert result.output == pdf
    assert calls == []  # engine never invoked on an already-tagged doc
    assert pdf.read_bytes() == _PDF_BYTES


def test_born_digital_route_invokes_engine_and_writes_new_file(tmp_path, monkeypatch):
    # A born-digital doc goes to the engine, which writes a NEW tagged copy.
    calls = []

    def fake_tag(pdf, out, title, strip_background=False):
        calls.append((pdf, out))
        out.write_bytes(b"%PDF-1.7 tagged\n%%EOF\n")  # pretend the engine tagged it

    stage = TagStage()
    monkeypatch.setattr(stage, "_tag", fake_tag)

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(_PDF_BYTES)

    result = stage.run(pdf, _sidecar(Route.BORN_DIGITAL))

    assert result.changed is True
    assert result.output == tmp_path / "doc.tagged.pdf"
    assert result.output.exists()
    assert result.sidecar.stages_applied == ["tag"]
    assert "tag-skipped-needs-ocr" not in result.sidecar.flags
    # the engine was called exactly once, with the input and the new output path.
    assert calls == [(pdf, result.output)]


def test_born_digital_never_mutates_the_input(tmp_path, monkeypatch):
    # The do-not-degrade invariant: tag writes a new file and leaves its input
    # byte-for-byte intact, even though this stage changes the PDF.
    stage = TagStage()
    monkeypatch.setattr(
        stage, "_tag", lambda pdf, out, title, strip_background=False: out.write_bytes(b"tagged")
    )

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(_PDF_BYTES)

    result = stage.run(pdf, _sidecar(Route.BORN_DIGITAL))

    assert result.output != pdf  # a distinct file, not the input
    assert pdf.read_bytes() == _PDF_BYTES  # input preserved


def test_title_comes_from_the_original_name_not_the_working_copy(tmp_path, monkeypatch):
    # dc:title is what viewers display (DisplayDocTitle). Post-OCR the working
    # copy is `doc.ocr.pdf` -- the stamped title must still be the ORIGINAL
    # filename stem from the sidecar, never the working copy's stem.
    titles = []

    def fake_tag(pdf, out, title, strip_background=False):
        titles.append(title)
        out.write_bytes(b"%PDF-1.7 tagged\n%%EOF\n")

    stage = TagStage()
    monkeypatch.setattr(stage, "_tag", fake_tag)

    pdf = tmp_path / "doc.ocr.pdf"  # the working copy ocr leaves behind
    pdf.write_bytes(_PDF_BYTES)
    sidecar = Sidecar(source_path="inbox/doc.pdf", source_sha256="0", route=Route.BORN_DIGITAL)

    stage.run(pdf, sidecar)

    assert titles == ["doc"]


def test_tag_shells_out_to_engine_and_returns_new_file(tmp_path, monkeypatch):
    # The REAL _tag: OpenDataLoader is faked via subprocess.run writing a tagged PDF
    # into --output-dir; _tag finishes it (or copies) to the .tagged.pdf output.
    def fake_run(cmd, **kwargs):
        outdir = Path(cmd[cmd.index("--output-dir") + 1])
        (outdir / "doc.tagged.pdf").write_bytes(b"%PDF-1.7\ntagged\n%%EOF\n")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(tag_module.subprocess, "run", fake_run)

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(_PDF_BYTES)

    result = TagStage().run(pdf, _sidecar(Route.BORN_DIGITAL))

    assert result.changed is True
    assert result.output == tmp_path / "doc.tagged.pdf"
    assert result.output.exists()
    assert pdf.read_bytes() == _PDF_BYTES  # input untouched


def test_finish_stamps_conformance_bits_and_original_title(tmp_path, monkeypatch):
    # end-to-end through the REAL _tag + _finish_pdf_ua (engine faked at the
    # subprocess seam, emitting a genuine PDF): the stamped dc:title must be the
    # ORIGINAL stem ("doc"), not the working copy's ("doc.ocr"), alongside the
    # MarkInfo / Lang / pdfuaid conformance bits.
    pikepdf = pytest.importorskip("pikepdf", reason="needs --extra tag")

    def fake_run(cmd, **kwargs):
        outdir = Path(cmd[cmd.index("--output-dir") + 1])
        engine_out = pikepdf.Pdf.new()
        engine_out.add_blank_page()
        engine_out.save(outdir / "doc.ocr.tagged.pdf")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(tag_module.subprocess, "run", fake_run)

    pdf = tmp_path / "doc.ocr.pdf"  # the working copy ocr leaves behind
    pdf.write_bytes(_PDF_BYTES)
    sidecar = Sidecar(source_path="inbox/doc.pdf", source_sha256="0", route=Route.BORN_DIGITAL)

    result = TagStage().run(pdf, sidecar)

    with pikepdf.open(result.output) as doc:
        assert bool(doc.Root.MarkInfo.Marked) is True
        assert str(doc.Root.Lang) == "en"
        with doc.open_metadata() as meta:
            assert meta["dc:title"] == "doc"
            assert meta["pdfuaid:part"] == "1"


def test_ocr_layered_pages_keep_their_background_but_hide_it_from_the_engine(tmp_path, monkeypatch):
    # The Figure-box fix: for OCR-built pages the engine must be shown a copy
    # WITHOUT the scan image (so no per-page Figure tag is created), and the
    # final output must carry the image again as an untagged background.
    pikepdf = pytest.importorskip("pikepdf", reason="needs --extra tag")
    PIL_Image = pytest.importorskip("PIL.Image", reason="needs Pillow (via pikepdf)")
    from speade.stages.ocr import OcrLine, add_page

    # a genuine OCR-shaped page: scan image in an artifact block + one text line
    src_pdf = pikepdf.Pdf.new()
    line = OcrLine(text="Hello scan", x0=100, y0=200, x1=900, y1=260, size=40, baseline_dy=-8)
    add_page(src_pdf, PIL_Image.new("RGB", (1000, 1400), "white"), [line])
    pdf = tmp_path / "doc.ocr.pdf"
    src_pdf.save(pdf)

    engine_saw = {}

    def fake_run(cmd, **kwargs):
        engine_input = Path(cmd[1])
        with pikepdf.open(engine_input) as seen:
            data = seen.pages[0].obj.Contents.read_bytes()
            xobjects = seen.pages[0].obj.get("/Resources", {}).get("/XObject")
            engine_saw["artifact"] = data.startswith(b"/Artifact BMC")
            engine_saw["has_image"] = xobjects is not None and "/Im0" in xobjects
        outdir = Path(cmd[cmd.index("--output-dir") + 1])
        shutil.copyfile(engine_input, outdir / "doc.ocr_tagged.pdf")  # "tagged"
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(tag_module.subprocess, "run", fake_run)

    sidecar = Sidecar(
        source_path="inbox/doc.pdf",
        source_sha256="0",
        route=Route.BORN_DIGITAL,
        ocr_layered=True,
    )
    result = TagStage().run(pdf, sidecar)

    # the engine never saw the scan image or the artifact block...
    assert engine_saw == {"artifact": False, "has_image": False}
    # ...but the output has both back: image as background, text intact.
    # (contents_add makes /Contents an array of streams -- coalesce to read.)
    with pikepdf.open(result.output) as doc:
        page = doc.pages[0]
        page.contents_coalesce()
        data = page.obj.Contents.read_bytes()
        assert data.startswith(b"/Artifact BMC")
        assert b"(Hello scan)" in data
        assert "/Im0" in page.obj.Resources.XObject


def test_missing_engine_fails_loud(tmp_path, monkeypatch):
    # The born-digital path must fail loud when the engine is absent -- never a
    # silent "success" with an untagged file.
    def boom(*args, **kwargs):
        raise FileNotFoundError("opendataloader-pdf")

    monkeypatch.setattr(tag_module.subprocess, "run", boom)

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(_PDF_BYTES)

    with pytest.raises(RuntimeError, match="opendataloader-pdf not found"):
        TagStage().run(pdf, _sidecar(Route.BORN_DIGITAL))
