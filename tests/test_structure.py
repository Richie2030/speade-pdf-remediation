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
