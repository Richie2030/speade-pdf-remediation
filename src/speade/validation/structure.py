"""Tag-structure summary -- what the reviewer sees BEFORE opening Acrobat.

Walks a tagged PDF's structure tree (pikepdf, MPL -- a permitted in-process
dependency) and reduces it to plain counts: headings, paragraphs, lists, tables,
figures (and how many figures lack alt text). The review UI shows this next to
the preview so the human can tell "is it actually tagged, and roughly how well"
without the round-trip to Acrobat. Read-only: this module never modifies a PDF.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

_HEADING_TYPES = {"/H", "/H1", "/H2", "/H3", "/H4", "/H5", "/H6"}


class StructureSummary(BaseModel):
    """The plain-language shape of one document's tag tree."""

    tagged: bool  # does a structure tree exist at all
    total: int = 0  # struct elements overall
    headings: int = 0
    paragraphs: int = 0
    lists: int = 0
    tables: int = 0
    figures: int = 0
    figures_missing_alt: int = 0  # each needs human-authored alt text at the gate
    counts: dict[str, int] = Field(default_factory=dict)  # raw /S type -> count


def summarize(pdf: Path) -> StructureSummary:
    """Summarise `pdf`'s structure tree. Raises on an unreadable file; returns
    tagged=False when the document simply has no tags yet."""
    import pikepdf

    counts: Counter[str] = Counter()
    missing_alt = 0

    def walk(node) -> None:
        nonlocal missing_alt
        if isinstance(node, pikepdf.Array):
            for kid in node:
                walk(kid)
            return
        if not isinstance(node, pikepdf.Dictionary):
            return  # an MCID number -- leaf content, not an element
        s = node.get("/S")
        if s is not None:
            counts[str(s)] += 1
            if str(s) == "/Figure" and "/Alt" not in node:
                missing_alt += 1
        kids = node.get("/K")
        if kids is not None:
            walk(kids)

    with pikepdf.open(pdf) as doc:
        root = doc.Root.get("/StructTreeRoot")
        if root is None:
            return StructureSummary(tagged=False)
        kids = root.get("/K")
        if kids is not None:
            walk(kids)

    return StructureSummary(
        tagged=True,
        total=sum(counts.values()),
        headings=sum(n for s, n in counts.items() if s in _HEADING_TYPES),
        paragraphs=counts.get("/P", 0),
        lists=counts.get("/L", 0),
        tables=counts.get("/Table", 0),
        figures=counts.get("/Figure", 0),
        figures_missing_alt=missing_alt,
        counts=dict(counts),
    )
