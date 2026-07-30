"""Tests for the tag-structure summariser (src/speade/validation/structure.py).

Builds tiny tag trees directly with pikepdf -- no engine needed -- and asserts
the plain-language counts the review UI shows (headings, paragraphs, figures
missing alt text)."""

from __future__ import annotations

import pytest

pikepdf = pytest.importorskip("pikepdf", reason="needs --extra tag")

from speade.validation.structure import summarize  # noqa: E402


def _blank_pdf(path):
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    return pdf, path


def test_untagged_pdf_reports_not_tagged(tmp_path):
    pdf, out = _blank_pdf(tmp_path / "untagged.pdf")
    pdf.save(out)

    summary = summarize(out)

    assert summary.tagged is False
    assert summary.total == 0


def test_structure_tree_has_geometry_and_text(tmp_path):
    # the tags panel: a marked-content paragraph must come back with its page,
    # a sane bounding box (pdfium's content marks), and a text snippet.
    pytest.importorskip("pypdfium2", reason="needs --extra ocr")
    from speade.validation.structure import structure_tree

    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(
            F1=pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type1,
                BaseFont=pikepdf.Name.Helvetica,
            )
        )
    )
    page.Contents = pdf.make_stream(
        b"/P <</MCID 0>> BDC BT /F1 24 Tf 72 700 Td (Hello structure) Tj ET EMC"
    )
    p_elem = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name.P, K=0, Pg=page.obj))
    doc_elem = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name("/Document"), K=p_elem))
    pdf.Root.StructTreeRoot = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.StructTreeRoot, K=doc_elem)
    )
    out = tmp_path / "tree.pdf"
    pdf.save(out)

    tree = structure_tree(out)

    assert tree.tagged is True
    assert tree.truncated is False
    assert len(tree.pages) == 1
    assert tree.pages[0].width == pytest.approx(612)
    (doc_node,) = tree.root
    assert doc_node.type == "Document"
    (p,) = doc_node.kids
    assert p.type == "P"
    assert p.page == 0
    assert "Hello structure" in p.text
    x0, y0, x1, y1 = p.box
    assert 60 < x0 < 80  # text starts at 72pt
    assert 150 < x1 < 400  # ...and spans the phrase's width
    assert 680 < y0 < 702  # baseline at 700pt, descender below
    assert 705 < y1 < 745  # cap height above
    # the container inherits its child's geometry so clicking it highlights all
    assert doc_node.page == 0
    assert doc_node.box == p.box


def _tagged_pdf(tmp_path, name="edit.pdf"):
    """A real tagged page: Document > (H1 with content, Figure)."""
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(
            F1=pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type1,
                BaseFont=pikepdf.Name.Helvetica,
            )
        )
    )
    page.Contents = pdf.make_stream(
        b"/P <</MCID 0>> BDC BT /F1 24 Tf 72 700 Td (A heading) Tj ET EMC"
    )
    heading = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name.P, K=0, Pg=page.obj))
    figure = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name("/Figure"), Pg=page.obj))
    doc_elem = pdf.make_indirect(
        pikepdf.Dictionary(S=pikepdf.Name("/Document"), K=pikepdf.Array([heading, figure]))
    )
    pdf.Root.StructTreeRoot = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.StructTreeRoot, K=doc_elem)
    )
    out = tmp_path / name
    pdf.save(out)
    return out


def test_element_ids_match_the_tree(tmp_path):
    # THE editing invariant: a StructureNode.id addresses the same element in
    # _walk_elements. If the two traversals ever drift, edits land on the wrong
    # tag -- so pin the pairing (types in pre-order must agree).
    pytest.importorskip("pypdfium2", reason="needs --extra ocr")
    from speade.validation.structure import _walk_elements, structure_tree

    out = _tagged_pdf(tmp_path)
    tree = structure_tree(out)
    flat = []

    def walk(nodes):
        for n in nodes:
            flat.append(n)
            walk(n.kids)

    walk(tree.root)

    with pikepdf.open(out) as doc:
        elements = list(_walk_elements(doc, pikepdf))
        assert [i for i, _ in elements] == [n.id for n in flat]
        assert [str(e.get("/S")).lstrip("/") for _, e in elements] == [n.type for n in flat]


def test_edit_element_retags_and_writes_alt(tmp_path):
    pytest.importorskip("pypdfium2", reason="needs --extra ocr")
    from speade.validation.structure import edit_element, structure_tree

    out = _tagged_pdf(tmp_path)
    tree = structure_tree(out)
    heading = tree.root[0].kids[0]  # the /P that should be a heading
    figure = tree.root[0].kids[1]

    old = edit_element(out, heading.id, lambda el, pk: el.__setattr__("S", pk.Name("/H2")))
    assert old == "P"
    edit_element(out, figure.id, lambda el, pk: el.__setattr__("Alt", pk.String("a chart")))

    after = structure_tree(out)
    assert after.root[0].kids[0].type == "H2"
    assert after.root[0].kids[1].alt == "a chart"

    with pytest.raises(LookupError):
        edit_element(out, 999, lambda el, pk: None)
    assert not list(tmp_path.glob("*.editing"))  # no temp file left behind


def test_structure_tree_untagged_pdf(tmp_path):
    pytest.importorskip("pypdfium2", reason="needs --extra ocr")
    from speade.validation.structure import structure_tree

    pdf, out = _blank_pdf(tmp_path / "untagged.pdf")
    pdf.save(out)

    tree = structure_tree(out)

    assert tree.tagged is False
    assert tree.root == []
    assert len(tree.pages) == 1


def test_counts_headings_paragraphs_and_alt_less_figures(tmp_path):
    pdf, out = _blank_pdf(tmp_path / "tagged.pdf")

    def elem(s: str, **extra):
        d = pikepdf.Dictionary(S=pikepdf.Name(s))
        for key, value in extra.items():
            d[pikepdf.Name("/" + key)] = value
        return d

    doc = elem("/Document")
    doc.K = pdf.make_indirect(
        pikepdf.Array(
            [
                elem("/H1"),
                elem("/P"),
                elem("/P"),
                elem("/L"),
                elem("/Figure"),  # no alt text -> the reviewer must write it
                elem("/Figure", Alt=pikepdf.String("a described image")),
            ]
        )
    )
    pdf.Root.StructTreeRoot = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.StructTreeRoot, K=doc)
    )
    pdf.save(out)

    summary = summarize(out)

    assert summary.tagged is True
    assert summary.total == 7
    assert summary.headings == 1
    assert summary.paragraphs == 2
    assert summary.lists == 1
    assert summary.figures == 2
    assert summary.figures_missing_alt == 1
    assert summary.counts["/Document"] == 1
