"""Tests for DetectStage (src/speade/stages/detect.py).

Assert the born-digital vs scanned router actually discriminates: a text-bearing
PDF routes BORN_DIGITAL, a blank/image one SCANNED, and a mixed one UNKNOWN.
Sample PDFs are built in-memory by `_pdf` -- detect only reads text, so a page
with no content stream stands in for a "scanned" (image-only) page.
"""

from speade.pipeline.contract import Route, Sidecar
from speade.stages.detect import DetectStage

# a paragraph well over detect's 100-char "texty" threshold
_TEXT = (
    b"This is genuine born-digital selectable text, comfortably more than one "
    b"hundred characters so the page counts as texty for the detector."
)


def _pdf(pages: list[bool], tagged: bool = False) -> bytes:
    """Build a minimal valid PDF. Each item in `pages`: True = a text page,
    False = a blank (no-text) page standing in for a scanned image page.
    tagged=True adds /StructTreeRoot + /MarkInfo so the doc reads as already tagged."""
    objs = [
        b"__CATALOG__",  # catalog, patched once we know the struct-tree object number
        b"PLACEHOLDER",  # pages tree, patched once page objects are numbered
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    page_nums: list[int] = []
    for has_text in pages:
        if has_text:
            content = b"BT /F1 12 Tf 72 720 Td (" + _TEXT + b") Tj ET"
            objs.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content))
            contents_num = len(objs)
            objs.append(
                (
                    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    "/Resources << /Font << /F1 3 0 R >> >> "
                    f"/Contents {contents_num} 0 R >>"
                ).encode()
            )
        else:
            objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << >> >>")
        page_nums.append(len(objs))

    kids = " ".join(f"{n} 0 R" for n in page_nums)
    objs[1] = f"<< /Type /Pages /Count {len(page_nums)} /Kids [{kids}] >>".encode()

    catalog = "<< /Type /Catalog /Pages 2 0 R"
    if tagged:
        objs.append(b"<< /Type /StructTreeRoot >>")
        catalog += f" /MarkInfo << /Marked true >> /StructTreeRoot {len(objs)} 0 R"
    catalog += " >>"
    objs[0] = catalog.encode()

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    count = len(objs) + 1
    out += f"xref\n0 {count}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        b"trailer\n"
        + f"<< /Size {count} /Root 1 0 R >>\n".encode()
        + b"startxref\n"
        + f"{xref_pos}\n".encode()
        + b"%%EOF\n"
    )
    return bytes(out)


def test_born_digital_pdf_routes_to_born_digital(tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(_pdf([True] * 5))  # 5 text pages -> ratio 1.0
    sidecar = Sidecar(source_path="doc.pdf", source_sha256="0")

    result = DetectStage().run(pdf, sidecar)

    assert result.sidecar.route == Route.BORN_DIGITAL
    assert result.sidecar.stages_applied == ["detect"]
    assert result.changed is False  # detect never rewrites the PDF
    assert result.output == pdf


def test_scanned_pdf_routes_to_scanned(tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(_pdf([False] * 5))  # 5 blank/image-only pages -> ratio 0.0
    sidecar = Sidecar(source_path="doc.pdf", source_sha256="0")

    result = DetectStage().run(pdf, sidecar)

    assert result.sidecar.route == Route.SCANNED
    assert result.sidecar.stages_applied == ["detect"]
    assert result.changed is False
    assert result.output == pdf


def test_mixed_pdf_routes_to_unknown(tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(_pdf([True, False, True, False, False]))  # 2/5 texty -> ratio 0.4
    sidecar = Sidecar(source_path="doc.pdf", source_sha256="0")

    result = DetectStage().run(pdf, sidecar)

    assert result.sidecar.route == Route.UNKNOWN
    assert result.sidecar.stages_applied == ["detect"]
    assert result.changed is False
    assert result.output == pdf


def test_empty_pdf_routes_to_unknown(tmp_path):
    # zero pages: the classify loop never runs and the total_pages == 0 guard fires
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(_pdf([]))
    sidecar = Sidecar(source_path="doc.pdf", source_sha256="0")

    result = DetectStage().run(pdf, sidecar)

    assert result.sidecar.route == Route.UNKNOWN


def test_untagged_pdf_is_not_flagged_already_tagged(tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(_pdf([True] * 3))  # no /StructTreeRoot
    sidecar = Sidecar(source_path="doc.pdf", source_sha256="0")

    result = DetectStage().run(pdf, sidecar)

    assert result.sidecar.already_tagged is False


def test_tagged_pdf_is_flagged_already_tagged(tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(_pdf([True] * 3, tagged=True))  # has /StructTreeRoot
    sidecar = Sidecar(source_path="doc.pdf", source_sha256="0")

    result = DetectStage().run(pdf, sidecar)

    assert result.sidecar.already_tagged is True
    # tag-state is independent of the text-based route
    assert result.sidecar.route == Route.BORN_DIGITAL


def test_thresholds_are_boundary_inclusive(tmp_path):
    # ratio exactly 0.8 must count as born-digital (>= 0.8), not fall to unknown
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(_pdf([True, True, True, True, False]))  # 4/5 texty -> ratio 0.8
    sidecar = Sidecar(source_path="doc.pdf", source_sha256="0")

    assert DetectStage().run(pdf, sidecar).sidecar.route == Route.BORN_DIGITAL
