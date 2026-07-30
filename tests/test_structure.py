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


def test_make_decorative_removes_it_and_artifacts_the_content(tmp_path):
    # the "decorative image needs no description" answer: out of the reading
    # order AND its page content re-marked /Artifact, so nothing announces it.
    pytest.importorskip("pypdfium2", reason="needs --extra ocr")
    from speade.validation.structure import make_decorative, structure_tree

    out = _tagged_pdf(tmp_path, "deco.pdf")
    before = structure_tree(out)
    para = before.root[0].kids[0]  # the /P that owns MCID 0

    result = make_decorative(out, para.id)

    assert result["was"] == "P"
    assert result["artifacts"] == 1  # one BDC rewritten to /Artifact BMC
    after = structure_tree(out)
    assert [k.type for k in after.root[0].kids] == ["Figure"]  # gone from the tree
    with pikepdf.open(out) as doc:
        content = doc.pages[0].Contents.read_bytes()
        assert b"/Artifact BMC" in content
        assert b"/MCID" not in content  # the only marked content was reclassified
        assert b"(A heading)" in content  # still drawn: visually identical


def test_move_element_reorders_siblings(tmp_path):
    pytest.importorskip("pypdfium2", reason="needs --extra ocr")
    from speade.validation.structure import move_element, structure_tree

    out = _tagged_pdf(tmp_path, "order.pdf")
    tree = structure_tree(out)
    assert [k.type for k in tree.root[0].kids] == ["P", "Figure"]
    figure = tree.root[0].kids[1]

    moved = move_element(out, figure.id, -1)  # the Figure should read first

    assert moved == {"moved": True, "from": 1, "to": 0}
    assert [k.type for k in structure_tree(out).root[0].kids] == ["Figure", "P"]
    # at the edge it reports no-op rather than silently wrapping around
    first = structure_tree(out).root[0].kids[0]
    assert move_element(out, first.id, -1)["moved"] is False


def test_remove_all_tags_strips_the_tree(tmp_path):
    pytest.importorskip("pypdfium2", reason="needs --extra ocr")
    from speade.validation.structure import remove_all_tags, structure_tree

    out = _tagged_pdf(tmp_path, "strip.pdf")
    with pikepdf.open(out, allow_overwriting_input=True) as doc:
        doc.Root.MarkInfo = pikepdf.Dictionary({"/Marked": True})
        doc.save(out.with_suffix(".marked.pdf"))
    marked = out.with_suffix(".marked.pdf")

    result = remove_all_tags(marked)

    assert result["had_tags"] is True
    assert structure_tree(marked).tagged is False
    with pikepdf.open(marked) as doc:
        assert "/StructTreeRoot" not in doc.Root
        assert "/MarkInfo" not in doc.Root  # no longer claims to be tagged
        assert b"(A heading)" in doc.pages[0].Contents.read_bytes()  # content intact


def test_unwrap_keeps_contents_but_refuses_content_bearing_tags(tmp_path):
    # "remove this tag" is only legitimate for a WRAPPER: deleting a tag that
    # holds page content would leave that content untagged (a PDF/UA failure),
    # so the engine must refuse and point at retag / decorative instead.
    pytest.importorskip("pypdfium2", reason="needs --extra ocr")
    from speade.validation.structure import structure_tree, unwrap_element

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
        b"/P <</MCID 0>> BDC BT /F1 12 Tf 72 700 Td (item text) Tj ET EMC"
    )
    body = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name.P, K=0, Pg=page.obj))
    bogus_list = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name.L, K=body))
    doc_elem = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name("/Document"), K=bogus_list))
    pdf.Root.StructTreeRoot = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.StructTreeRoot, K=doc_elem)
    )
    out = tmp_path / "unwrap.pdf"
    pdf.save(out)

    tree = structure_tree(out)
    the_list = tree.root[0].kids[0]
    the_para = the_list.kids[0]

    # the paragraph owns the page content: refuse, with a useful message
    with pytest.raises(ValueError, match="would leave that content untagged"):
        unwrap_element(out, the_para.id)

    # the bogus List wraps it: unwrap promotes the paragraph in its place
    result = unwrap_element(out, the_list.id)

    assert result == {"was": "L", "promoted": 1}
    after = structure_tree(out)
    assert [k.type for k in after.root[0].kids] == ["P"]
    assert after.root[0].kids[0].text.startswith("item text")


def test_summarize_reports_heading_level_skips(tmp_path):
    from speade.validation.structure import summarize

    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    kids = pikepdf.Array(
        [
            pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name("/H1"))),
            pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name("/H3"))),  # skip
            pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name("/H4"))),  # fine
        ]
    )
    doc_elem = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name("/Document"), K=kids))
    pdf.Root.StructTreeRoot = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.StructTreeRoot, K=doc_elem)
    )
    out = tmp_path / "skips.pdf"
    pdf.save(out)

    summary = summarize(out)

    assert summary.headings == 3
    assert summary.heading_skips == ["H1 to H3"]  # veraPDF 7.4.2-1


def _two_para_pdf(tmp_path, name="pair.pdf"):
    """Document > (P 'first line' MCID 0, P 'second line' MCID 1) with a real
    /ParentTree, the way a conforming producer writes it -- so tests can prove
    the content-to-tag lookup table is maintained through merges/removals."""
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
        b"/P <</MCID 0>> BDC BT /F1 12 Tf 72 700 Td (first line) Tj ET EMC\n"
        b"/P <</MCID 1>> BDC BT /F1 12 Tf 72 680 Td (second line) Tj ET EMC"
    )
    first = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name.P, K=0, Pg=page.obj))
    second = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name.P, K=1, Pg=page.obj))
    doc_elem = pdf.make_indirect(
        pikepdf.Dictionary(S=pikepdf.Name("/Document"), K=pikepdf.Array([first, second]))
    )
    page.StructParents = 0
    pdf.Root.StructTreeRoot = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.StructTreeRoot,
            K=doc_elem,
            ParentTree=pdf.make_indirect(
                pikepdf.Dictionary(Nums=pikepdf.Array([0, pikepdf.Array([first, second])]))
            ),
        )
    )
    out = tmp_path / name
    pdf.save(out)
    return out


def _parent_tree_entries(doc):
    return list(doc.Root.StructTreeRoot.ParentTree.Nums[1])


def test_merge_elements_combines_content_and_repoints_parent_tree(tmp_path):
    pytest.importorskip("pypdfium2", reason="needs --extra ocr")
    from speade.validation.structure import merge_elements, structure_tree

    out = _two_para_pdf(tmp_path)
    tree = structure_tree(out)
    ids = [k.id for k in tree.root[0].kids]

    result = merge_elements(out, ids, "P")

    assert result == {"merged": 2, "type": "P"}
    after = structure_tree(out)
    (merged,) = after.root[0].kids
    assert merged.type == "P"
    assert "first line" in merged.text and "second line" in merged.text
    with pikepdf.open(out) as doc:
        survivor = doc.Root.StructTreeRoot.K.K[0]
        assert [int(k) for k in survivor.K] == [0, 1]  # both MCIDs, in order
        # the content-to-tag lookup table now points BOTH entries at the survivor
        entries = _parent_tree_entries(doc)
        assert all(e.objgen == survivor.objgen for e in entries)

    with pytest.raises(LookupError, match="at least two"):
        merge_elements(out, [0], "P")


def test_merge_into_a_figure_retags_the_survivor(tmp_path):
    pytest.importorskip("pypdfium2", reason="needs --extra ocr")
    from speade.validation.structure import merge_elements, structure_tree

    out = _two_para_pdf(tmp_path, "fig.pdf")
    ids = [k.id for k in structure_tree(out).root[0].kids]
    merge_elements(out, ids, "Figure")
    (merged,) = structure_tree(out).root[0].kids
    assert merged.type == "Figure"


def test_retag_elements_changes_many_in_one_save(tmp_path):
    pytest.importorskip("pypdfium2", reason="needs --extra ocr")
    from speade.validation.structure import retag_elements, structure_tree

    out = _two_para_pdf(tmp_path, "retag.pdf")
    ids = [k.id for k in structure_tree(out).root[0].kids]

    result = retag_elements(out, ids, "H2")

    assert result == {"changed": 2, "was": ["P", "P"], "type": "H2"}
    assert [k.type for k in structure_tree(out).root[0].kids] == ["H2", "H2"]


def test_make_decorative_many_ignores_nested_ids_and_clears_parent_tree(tmp_path):
    pytest.importorskip("pypdfium2", reason="needs --extra ocr")
    from speade.validation.structure import make_decorative_many, structure_tree

    out = _two_para_pdf(tmp_path, "deco2.pdf")
    tree = structure_tree(out)
    document = tree.root[0]
    first = document.kids[0]

    # selecting a parent AND its child must not double-apply: the outer wins
    result = make_decorative_many(out, [document.id, first.id])

    assert result == {"removed": 1, "artifacts": 2}  # the Document, once
    assert structure_tree(out).root == []
    with pikepdf.open(out) as doc:
        content = doc.pages[0].Contents.read_bytes()
        assert content.count(b"/Artifact BMC") == 2
        assert b"/MCID" not in content
        # artifact content has no structure parent: entries cleared, not dangling
        for entry in _parent_tree_entries(doc):
            assert not isinstance(entry, pikepdf.Dictionary)


def test_make_decorative_clears_its_parent_tree_entry(tmp_path):
    # regression: the single-tag path used to leave the lookup table pointing
    # at the deleted element -- a document that looks right but maps wrong.
    pytest.importorskip("pypdfium2", reason="needs --extra ocr")
    from speade.validation.structure import make_decorative, structure_tree

    out = _two_para_pdf(tmp_path, "deco3.pdf")
    first = structure_tree(out).root[0].kids[0]

    make_decorative(out, first.id)

    with pikepdf.open(out) as doc:
        cleared, kept = _parent_tree_entries(doc)
        assert not isinstance(cleared, pikepdf.Dictionary)  # no dangling ref
        survivor = doc.Root.StructTreeRoot.K.K[0]
        assert kept.objgen == survivor.objgen


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
