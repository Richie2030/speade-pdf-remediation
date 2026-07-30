"""Tag-structure inspection -- what the reviewer sees BEFORE opening Acrobat.

Two read-only views over a tagged PDF's structure tree:

- `summarize` reduces it to plain counts (headings, paragraphs, figures and how
  many lack alt text) for the one-line answer "is it tagged, roughly how well";
- `structure_tree` returns the full nested tree WITH page geometry -- the
  Acrobat-style tags panel. pikepdf (MPL, permitted in-process) walks the tree;
  pdfium (via pypdfium2's raw API) supplies each marked-content id's bounding
  box from the page objects that carry it, plus a text snippet per node. That
  is exactly how a viewer computes tag highlights -- no renderer of our own.

Read-only: this module never modifies a PDF.
"""

from __future__ import annotations

import ctypes
from collections import Counter
from contextlib import suppress
from pathlib import Path

from pydantic import BaseModel, Field

from speade.pdfium_lock import PDFIUM_LOCK

_HEADING_TYPES = {"/H", "/H1", "/H2", "/H3", "/H4", "/H5", "/H6"}

_MAX_NODES = 3000  # a pathological tree must not freeze the UI
_SNIPPET_CHARS = 120


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
    # heading levels that jump (H1 straight to H3): veraPDF clause 7.4.2-1, and
    # the reviewer fixes it by retagging -- so surface it rather than hide it.
    heading_skips: list[str] = Field(default_factory=list)


def summarize(pdf: Path) -> StructureSummary:
    """Summarise `pdf`'s structure tree. Raises on an unreadable file; returns
    tagged=False when the document simply has no tags yet."""
    import pikepdf

    counts: Counter[str] = Counter()
    missing_alt = 0
    levels: list[int] = []  # heading levels in reading order, for skip detection

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
            if str(s) in _HEADING_TYPES and str(s) != "/H":
                levels.append(int(str(s)[2:]))
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

    skips = [
        f"H{previous} to H{level}"
        for previous, level in zip(levels, levels[1:], strict=False)
        if level > previous + 1
    ]
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
        heading_skips=sorted(set(skips)),
    )


# --------------------------------------------------------------- the full tree


class PageSize(BaseModel):
    width: float  # page points
    height: float


class StructureNode(BaseModel):
    """One tag in the tree, with enough geometry to highlight it on the page."""

    id: int = -1  # pre-order position; how an editor addresses this tag
    type: str  # structure type without the slash, e.g. "P", "H1", "Figure"
    alt: str | None = None  # /Alt if present (figures)
    page: int | None = None  # 0-based page its content sits on (first found)
    box: list[float] | None = None  # [x0, y0, x1, y1] page points, y bottom-up
    text: str = ""  # snippet of the node's own marked content
    kids: list[StructureNode] = Field(default_factory=list)


class StructureTree(BaseModel):
    """The whole document's tag tree + page sizes -- the in-app tags panel."""

    tagged: bool
    pages: list[PageSize] = Field(default_factory=list)
    root: list[StructureNode] = Field(default_factory=list)
    truncated: bool = False  # _MAX_NODES hit: the tree shown is a prefix


def _is_element(node, pikepdf) -> bool:
    """Is this /K entry a real structure element (a tree row), or content?"""
    return (
        isinstance(node, pikepdf.Dictionary)
        and node.get("/Type") != pikepdf.Name("/MCR")
        and node.get("/S") is not None
    )


def _walk_elements(doc, pikepdf):
    """Yield (id, element dict) over an OPEN document in the SAME pre-order the
    tree uses, so a `StructureNode.id` addresses the same element here.

    These traversal rules MUST match `structure_tree`'s `walk`; the pairing is
    pinned by tests/test_structure.py::test_element_ids_match_the_tree.
    """
    root = doc.Root.get("/StructTreeRoot")
    if root is None:
        return

    def walk(node, state):
        if isinstance(node, pikepdf.Array):
            for kid in node:
                yield from walk(kid, state)
            return
        if not _is_element(node, pikepdf):
            return
        if state["count"] >= _MAX_NODES:
            return
        node_id = state["count"]
        state["count"] += 1
        yield node_id, node
        kids = node.get("/K")
        if kids is None:
            return
        for kid in kids if isinstance(kids, pikepdf.Array) else [kids]:
            yield from walk(kid, state)

    yield from walk(root.get("/K"), {"count": 0})


def _find_with_parent(doc, pikepdf, node_id: int):
    """(element, parent_element_or_None) for `node_id`. The parent is what holds
    it in /K, which sibling reordering and removal both need. None means the
    element hangs directly off /StructTreeRoot."""
    root = doc.Root.get("/StructTreeRoot")
    if root is None:
        return None, None
    found: list = [None, None]

    def walk(node, parent, state):
        if isinstance(node, pikepdf.Array):
            for kid in node:
                if walk(kid, parent, state):
                    return True
            return False
        if not _is_element(node, pikepdf) or state["count"] >= _MAX_NODES:
            return False
        if state["count"] == node_id:
            found[0], found[1] = node, parent
            return True
        state["count"] += 1
        kids = node.get("/K")
        if kids is not None:
            for kid in kids if isinstance(kids, pikepdf.Array) else [kids]:
                if walk(kid, node, state):
                    return True
        return False

    walk(root.get("/K"), None, {"count": 0})
    return found[0], found[1]


def _kid_list(holder, pikepdf, root_fallback):
    """The /K array of `holder` (or of the struct tree root), forced to an array
    so entries can be moved and removed. Returns (array, owner)."""
    owner = holder if holder is not None else root_fallback
    kids = owner.get("/K")
    if not isinstance(kids, pikepdf.Array):
        kids = pikepdf.Array([kids] if kids is not None else [])
        owner.K = kids
    return kids, owner


def _mark_content_as_artifact(doc, pikepdf, element) -> int:
    """Re-mark this element's page content as an /Artifact in the content
    stream, so viewers and screen readers treat it as decoration. Returns how
    many marked-content sequences were rewritten.

    The rewrite is textual on the content stream tokens: `/P <</MCID n>> BDC`
    (or any tag) becomes `/Artifact BMC` for the MCIDs this element owns, which
    is exactly what makes the content non-content without disturbing drawing
    operators. Content this element does not own is untouched.
    """
    # page objgen -> the MCIDs this element owns on that page
    mcids: dict[tuple[int, int], set[int]] = {}

    def key(page_ref):
        try:
            return page_ref.objgen
        except Exception:
            return None

    def collect(node, page_ref):
        pg = node.get("/Pg") or page_ref
        kids = node.get("/K")
        if kids is None:
            return
        for kid in kids if isinstance(kids, pikepdf.Array) else [kids]:
            if isinstance(kid, int):
                if key(pg) is not None:
                    mcids.setdefault(key(pg), set()).add(int(kid))
            elif isinstance(kid, pikepdf.Dictionary) and kid.get("/Type") == pikepdf.Name("/MCR"):
                target = kid.get("/Pg") or pg
                if key(target) is not None and kid.get("/MCID") is not None:
                    mcids.setdefault(key(target), set()).add(int(kid.get("/MCID")))
            elif _is_element(kid, pikepdf):
                collect(kid, pg)

    collect(element, element.get("/Pg"))
    if not mcids:
        return 0

    rewritten = 0
    for page in doc.pages:
        wanted = mcids.get(key(page.obj))
        if not wanted:
            continue
        out: list[bytes] = []
        changed_here = False
        for operands, operator in pikepdf.parse_content_stream(page):
            if str(operator) == "BDC" and len(operands) == 2:
                props = operands[1]
                if (
                    isinstance(props, pikepdf.Dictionary)
                    and props.get("/MCID") is not None
                    and int(props.get("/MCID")) in wanted
                ):
                    out.append(b"/Artifact BMC")
                    rewritten += 1
                    changed_here = True
                    continue
            out.append(pikepdf.unparse_content_stream([(operands, operator)]))
        if changed_here:
            page.Contents = doc.make_stream(b"\n".join(out))
    return rewritten


def edit_element(pdf: Path, node_id: int, mutate) -> str:
    """Apply `mutate(element_dict, pikepdf)` to the tag addressed by `node_id`
    and save the document in place. Returns the element's structure type as it
    was BEFORE the mutation (so a retag can report what it replaced).

    Writes via a temp file + atomic replace: a half-written PDF must never be
    what a reviewer opens. Raises LookupError when the id is not in the tree.
    """
    import pikepdf

    tmp = pdf.with_name(pdf.name + ".editing")
    with pikepdf.open(pdf) as doc:
        target = None
        for eid, element in _walk_elements(doc, pikepdf):
            if eid == node_id:
                target = element
                break
        if target is None:
            raise LookupError(f"no tag with id {node_id} in {pdf.name}")
        was = str(target.get("/S")).lstrip("/")
        mutate(target, pikepdf)
        doc.save(tmp)
    tmp.replace(pdf)
    return was


def _save_over(doc, pdf: Path) -> None:
    """Atomic in-place save: temp file, then replace (never a half-written PDF)."""
    tmp = pdf.with_name(pdf.name + ".editing")
    doc.save(tmp)
    doc.close()
    tmp.replace(pdf)


def make_decorative(pdf: Path, node_id: int) -> dict:
    """Turn one tagged element into decoration: drop it out of the reading order
    (removed from its parent's /K) and re-mark its page content as /Artifact, so
    screen readers skip it and it needs no description.

    Returns {"was": type, "artifacts": n}. Raises LookupError for a bad id.
    """
    import pikepdf

    doc = pikepdf.open(pdf)
    try:
        element, parent = _find_with_parent(doc, pikepdf, node_id)
        if element is None:
            raise LookupError(f"no tag with id {node_id} in {pdf.name}")
        was = str(element.get("/S")).lstrip("/")
        artifacts = _mark_content_as_artifact(doc, pikepdf, element)
        kids, _owner = _kid_list(parent, pikepdf, doc.Root.StructTreeRoot)
        for i, kid in enumerate(kids):
            try:
                same = kid.objgen == element.objgen
            except Exception:
                same = kid is element
            if same:
                del kids[i]
                break
        _save_over(doc, pdf)
    except BaseException:
        doc.close()
        raise
    return {"was": was, "artifacts": artifacts}


def move_element(pdf: Path, node_id: int, delta: int) -> dict:
    """Move one tag earlier (-1) or later (+1) among its siblings -- reading
    order IS tree order, so this is how a misplaced caption or heading gets put
    right. Returns {"moved": bool, "from": i, "to": j}."""
    import pikepdf

    doc = pikepdf.open(pdf)
    try:
        element, parent = _find_with_parent(doc, pikepdf, node_id)
        if element is None:
            raise LookupError(f"no tag with id {node_id} in {pdf.name}")
        kids, _owner = _kid_list(parent, pikepdf, doc.Root.StructTreeRoot)
        index = None
        for i, kid in enumerate(kids):
            try:
                same = kid.objgen == element.objgen
            except Exception:
                same = kid is element
            if same:
                index = i
                break
        target = None if index is None else index + delta
        if index is None or target is None or not (0 <= target < len(kids)):
            doc.close()
            return {"moved": False, "from": index, "to": target}
        entry = kids[index]
        del kids[index]
        kids.insert(target, entry)
        _save_over(doc, pdf)
    except BaseException:
        doc.close()
        raise
    return {"moved": True, "from": index, "to": target}


def _owns_content(element, pikepdf) -> bool:
    """Does this element hold page content DIRECTLY (an MCID or a content ref)?
    Such an element cannot simply be deleted: its content would become untagged,
    which is itself a PDF/UA failure (clause 7.1-1). Retag it or artifact it."""
    kids = element.get("/K")
    if kids is None:
        return False
    for kid in kids if isinstance(kids, pikepdf.Array) else [kids]:
        if isinstance(kid, int):
            return True
        if isinstance(kid, pikepdf.Dictionary) and kid.get("/Type") == pikepdf.Name("/MCR"):
            return True
    return False


def unwrap_element(pdf: Path, node_id: int) -> dict:
    """Remove one tag while KEEPING its contents in the reading order: its child
    elements take its place in the parent (the fix for a wrapper the engine
    invented, e.g. paragraphs bundled into a bogus List). An element with no
    contents at all is simply deleted.

    Refuses (ValueError) when the element holds page content directly -- that
    content would be left untagged, a PDF/UA violation; retag it or mark it
    decorative instead. Raises LookupError for an unknown id.
    """
    import pikepdf

    doc = pikepdf.open(pdf)
    try:
        element, parent = _find_with_parent(doc, pikepdf, node_id)
        if element is None:
            raise LookupError(f"no tag with id {node_id} in {pdf.name}")
        was = str(element.get("/S")).lstrip("/")
        if _owns_content(element, pikepdf):
            raise ValueError(
                f"{was} contains page content, so removing its tag would leave that "
                "content untagged. Change its type instead, or mark it decorative."
            )
        promoted = [
            kid
            for kid in (
                element.get("/K")
                if isinstance(element.get("/K"), pikepdf.Array)
                else ([element.get("/K")] if element.get("/K") is not None else [])
            )
            if _is_element(kid, pikepdf)
        ]
        kids, owner = _kid_list(parent, pikepdf, doc.Root.StructTreeRoot)
        index = None
        for i, kid in enumerate(kids):
            try:
                same = kid.objgen == element.objgen
            except Exception:
                same = kid is element
            if same:
                index = i
                break
        if index is None:
            raise LookupError(f"tag {node_id} is not held by its parent's /K")
        del kids[index]
        for offset, kid in enumerate(promoted):
            kids.insert(index + offset, kid)
            with suppress(Exception):  # keep /P pointing at the new parent
                kid.P = owner
        _save_over(doc, pdf)
    except BaseException:
        doc.close()
        raise
    return {"was": was, "promoted": len(promoted)}


def remove_all_tags(pdf: Path) -> dict:
    """Strip the whole structure tree and the tagged-PDF declaration, leaving an
    untagged (but visually identical) document -- the escape hatch when the
    automatic tagging is worse than starting again in Acrobat. Marked content in
    the page streams is left alone: harmless without a tree, and removing it
    would risk the page's appearance."""
    import pikepdf

    doc = pikepdf.open(pdf)
    try:
        had = "/StructTreeRoot" in doc.Root
        for key in ("/StructTreeRoot", "/MarkInfo"):
            if key in doc.Root:
                del doc.Root[key]
        _save_over(doc, pdf)
    except BaseException:
        doc.close()
        raise
    return {"had_tags": had}


def _mcid_boxes(doc) -> list[dict[int, tuple[float, float, float, float]]]:
    """Per page: MCID -> union bounding box, read from the content marks pdfium
    exposes on every page object (the same data a viewer uses for highlights)."""
    import pypdfium2.raw as raw

    per_page: list[dict[int, tuple[float, float, float, float]]] = []
    for page in doc:
        boxes: dict[int, tuple[float, float, float, float]] = {}
        for i in range(raw.FPDFPage_CountObjects(page.raw)):
            obj = raw.FPDFPage_GetObject(page.raw, i)
            left = ctypes.c_float()
            bottom = ctypes.c_float()
            right = ctypes.c_float()
            top = ctypes.c_float()
            if not raw.FPDFPageObj_GetBounds(
                obj,
                ctypes.byref(left),
                ctypes.byref(bottom),
                ctypes.byref(right),
                ctypes.byref(top),
            ):
                continue
            for m in range(raw.FPDFPageObj_CountMarks(obj)):
                mark = raw.FPDFPageObj_GetMark(obj, m)
                mcid = ctypes.c_int(-1)
                if not raw.FPDFPageObjMark_GetParamIntValue(mark, b"MCID", ctypes.byref(mcid)):
                    continue
                box = (left.value, bottom.value, right.value, top.value)
                prev = boxes.get(mcid.value)
                boxes[mcid.value] = (
                    box
                    if prev is None
                    else (
                        min(prev[0], box[0]),
                        min(prev[1], box[1]),
                        max(prev[2], box[2]),
                        max(prev[3], box[3]),
                    )
                )
        per_page.append(boxes)
    return per_page


def _union(a: list[float] | None, b: list[float] | None) -> list[float] | None:
    if a is None:
        return list(b) if b else None
    if b is None:
        return a
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def structure_tree(pdf: Path) -> StructureTree:
    """The document's full tag tree with per-node page + box + text snippet.
    Raises on an unreadable file; returns tagged=False for an untagged one.

    Holds PDFIUM_LOCK for the whole read: pdfium is not thread-safe, and the
    geometry/snippet reads interleave with the tree walk (speade.pdfium_lock).
    """
    import pikepdf
    import pypdfium2 as pdfium

    with PDFIUM_LOCK:
        return _structure_tree_unlocked(pdf, pikepdf, pdfium)


def _structure_tree_unlocked(pdf: Path, pikepdf, pdfium) -> StructureTree:
    fpdf = pdfium.PdfDocument(str(pdf))
    try:
        geo = _mcid_boxes(fpdf)
        sizes = [PageSize(width=p.get_size()[0], height=p.get_size()[1]) for p in fpdf]
        textpages = [p.get_textpage() for p in fpdf]

        with pikepdf.open(pdf) as doc:
            root = doc.Root.get("/StructTreeRoot")
            if root is None:
                return StructureTree(tagged=False, pages=sizes)
            page_index = {}
            for i, page in enumerate(doc.pages):
                page_obj = getattr(page, "obj", page)
                page_index[page_obj.objgen] = i

            state = {"count": 0, "truncated": False}

            def content_page(elem, default: int | None) -> int | None:
                pg = elem.get("/Pg")
                if pg is None:
                    return default
                try:
                    return page_index.get(pg.objgen, default)
                except Exception:
                    return default

            def walk(node, default_page: int | None) -> list[StructureNode]:
                if isinstance(node, pikepdf.Array):
                    out: list[StructureNode] = []
                    for kid in node:
                        out.extend(walk(kid, default_page))
                    return out
                if not isinstance(node, pikepdf.Dictionary):
                    return []  # bare MCIDs are handled by their parent element
                if node.get("/Type") == pikepdf.Name("/MCR") or node.get("/S") is None:
                    return []  # content refs / role-less nodes: no tree row
                if state["count"] >= _MAX_NODES:
                    state["truncated"] = True
                    return []
                # pre-order id: the editor addresses this element by it, so the
                # rules here and in _walk_elements must stay identical.
                node_id = state["count"]
                state["count"] += 1

                own_page = content_page(node, default_page)
                own_box: list[float] | None = None
                mcids: list[tuple[int | None, int]] = []
                kids_nodes: list[StructureNode] = []

                kids = node.get("/K")
                for kid in _as_list(kids):
                    if isinstance(kid, int):
                        mcids.append((own_page, kid))
                    elif isinstance(kid, pikepdf.Dictionary) and kid.get("/Type") == pikepdf.Name(
                        "/MCR"
                    ):
                        mcids.append((content_page(kid, own_page), int(kid.get("/MCID", -1))))
                    else:
                        kids_nodes.extend(walk(kid, own_page))

                node_page: int | None = None
                for pg, mcid in mcids:
                    if pg is None or not (0 <= pg < len(geo)):
                        continue
                    box = geo[pg].get(mcid)
                    if box is not None:
                        own_box = _union(own_box, list(box))
                        node_page = pg if node_page is None else node_page
                # a container's page/box comes from its children when it has no
                # direct content of its own (so clicking "List" highlights it all).
                box_all = own_box
                for kid in kids_nodes:
                    if kid.page is not None and node_page is None:
                        node_page = kid.page
                    if kid.page == node_page and kid.box is not None:
                        box_all = _union(box_all, kid.box)

                text = ""
                if own_box is not None and node_page is not None:
                    try:
                        text = textpages[node_page].get_text_bounded(
                            left=own_box[0],
                            bottom=own_box[1],
                            right=own_box[2],
                            top=own_box[3],
                        )
                        text = " ".join(text.split())[:_SNIPPET_CHARS]
                    except Exception:
                        text = ""

                alt = node.get("/Alt")
                return [
                    StructureNode(
                        id=node_id,
                        type=str(node.get("/S")).lstrip("/"),
                        alt=str(alt) if alt is not None else None,
                        page=node_page,
                        box=box_all,
                        text=text,
                        kids=kids_nodes,
                    )
                ]

            def _as_list(kids):
                if kids is None:
                    return []
                if isinstance(kids, pikepdf.Array):
                    return list(kids)
                return [kids]

            tree = walk(root.get("/K"), None)
            return StructureTree(tagged=True, pages=sizes, root=tree, truncated=state["truncated"])
    finally:
        fpdf.close()
