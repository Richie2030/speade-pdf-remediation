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
import os
import uuid
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


class LineRef(BaseModel):
    """One marked-content sequence (one text line, in practice) a tag owns --
    the unit the carve tool works at: fine enough to grab 'the last two lines
    of this paragraph', never finer than the content is actually marked."""

    page: int
    mcid: int
    box: list[float]  # [x0, y0, x1, y1] page points, y bottom-up


class StructureNode(BaseModel):
    """One tag in the tree, with enough geometry to highlight it on the page."""

    id: int = -1  # pre-order position; how an editor addresses this tag
    type: str  # structure type without the slash, e.g. "P", "H1", "Figure"
    alt: str | None = None  # /Alt if present (figures)
    page: int | None = None  # 0-based page its content sits on (first found)
    box: list[float] | None = None  # [x0, y0, x1, y1] page points, y bottom-up
    text: str = ""  # snippet of the node's own marked content
    lines: list[LineRef] = Field(default_factory=list)  # per-line geometry
    kids: list[StructureNode] = Field(default_factory=list)


class UntaggedRef(BaseModel):
    """One drawn piece of content that belongs to NO tag -- text the engine
    missed, or content a reviewer marked decorative. The drag tool can wrap it
    in a brand-new tag (tag_objects); `index` is its position in the page's
    drawing order, which tag_objects re-derives and validates before writing."""

    page: int
    index: int  # pdfium page-object index == paint-operator ordinal
    box: list[float]
    kind: str  # "text" | "image" | "form"


class StructureTree(BaseModel):
    """The whole document's tag tree + page sizes -- the in-app tags panel."""

    tagged: bool
    pages: list[PageSize] = Field(default_factory=list)
    root: list[StructureNode] = Field(default_factory=list)
    truncated: bool = False  # _MAX_NODES hit: the tree shown is a prefix
    untagged: list[UntaggedRef] = Field(default_factory=list)
    # pdfium could not read this document's page geometry (a damaged file can
    # fault inside the native library). The tag tree below is still complete --
    # it comes from pikepdf -- but there are no highlight boxes to draw and no
    # untagged content to find, so the UI says so instead of pretending.
    geometry_error: str | None = None


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


def _elements_by_ids(doc, pikepdf, node_ids: list[int]) -> dict:
    """Resolve several pre-order ids to (element, parent) in ONE walk. Bulk
    operations must resolve every id BEFORE mutating anything: removals shift
    the pre-order numbering, so a second lookup would hit the wrong tag."""
    wanted = set(node_ids)
    found: dict[int, tuple] = {}
    root = doc.Root.get("/StructTreeRoot")
    if root is None:
        return found

    def walk(node, parent, state):
        if isinstance(node, pikepdf.Array):
            for kid in node:
                walk(kid, parent, state)
            return
        if not _is_element(node, pikepdf) or state["count"] >= _MAX_NODES:
            return
        if state["count"] in wanted:
            found[state["count"]] = (node, parent)
        state["count"] += 1
        kids = node.get("/K")
        if kids is not None:
            for kid in kids if isinstance(kids, pikepdf.Array) else [kids]:
                walk(kid, node, state)

    walk(root.get("/K"), None, {"count": 0})
    return found


def _objgen(obj):
    try:
        return obj.objgen
    except Exception:
        return None


def _drop_nested(found: dict, pikepdf) -> dict:
    """Drop ids whose element sits INSIDE another selected element -- operating
    on both a parent and its descendant would double-apply (or cycle, for a
    merge). The outermost selection wins. Walks down from each selected element
    rather than trusting /P links, which not every producer writes."""
    inside: set = set()
    selected = {_objgen(el) for el, _ in found.values()} - {None}

    def mark_descendants(node) -> None:
        kids = node.get("/K")
        if kids is None:
            return
        for kid in kids if isinstance(kids, pikepdf.Array) else [kids]:
            if _is_element(kid, pikepdf):
                key = _objgen(kid)
                if key in selected:
                    inside.add(key)
                mark_descendants(kid)

    for element, _parent in found.values():
        mark_descendants(element)
    return {nid: pair for nid, pair in found.items() if _objgen(pair[0]) not in inside}


def _remove_from_parent(kids, element) -> bool:
    """Delete `element` from a /K array; True if it was found there."""
    for i, kid in enumerate(kids):
        try:
            same = kid.objgen == element.objgen
        except Exception:
            same = kid is element
        if same:
            del kids[i]
            return True
    return False


def _element_and_descendants(element, pikepdf) -> list:
    """Objgens of this element and every structure element inside it. When a
    container leaves the tree, its whole subtree dies with it -- the parent-tree
    entries of ALL of them must be dealt with, not just the container's."""
    keys = []

    def walk(node) -> None:
        key = _objgen(node)
        if key is not None:
            keys.append(key)
        kids = node.get("/K")
        if kids is None:
            return
        for kid in kids if isinstance(kids, pikepdf.Array) else [kids]:
            if _is_element(kid, pikepdf):
                walk(kid)

    walk(element)
    return keys


def _repoint_parent_tree(doc, pikepdf, replacements: dict) -> int:
    """Update /StructTreeRoot /ParentTree -- the lookup table that maps page
    content back to its owning tag -- after elements were merged or removed.
    `replacements` maps an old element's objgen to its replacement element, or
    to None to clear the entry (content that became an artifact). Without this,
    the table keeps pointing at dead objects: a document that LOOKS right but
    whose content-to-tag mapping is broken. Returns entries updated."""
    root = doc.Root.get("/StructTreeRoot")
    tree = root.get("/ParentTree") if root is not None else None
    if tree is None:
        return 0
    null = pikepdf.Object.parse(b"null")
    updated = 0

    def fix_value(value):
        nonlocal updated
        if isinstance(value, pikepdf.Array):
            for i, entry in enumerate(value):
                key = _objgen(entry)
                if key is not None and key in replacements:
                    new = replacements[key]
                    value[i] = null if new is None else new
                    updated += 1
            return value
        key = _objgen(value)
        if key is not None and key in replacements:
            new = replacements[key]
            updated += 1
            return null if new is None else new
        return value

    def walk(node) -> None:
        nums = node.get("/Nums")
        if isinstance(nums, pikepdf.Array):
            for i in range(1, len(nums), 2):
                nums[i] = fix_value(nums[i])
        kids = node.get("/Kids")
        if isinstance(kids, pikepdf.Array):
            for kid in kids:
                walk(kid)

    walk(tree)
    return updated


def edit_element(pdf: Path, node_id: int, mutate) -> str:
    """Apply `mutate(element_dict, pikepdf)` to the tag addressed by `node_id`
    and save the document in place. Returns the element's structure type as it
    was BEFORE the mutation (so a retag can report what it replaced).

    Writes via a temp file + atomic replace: a half-written PDF must never be
    what a reviewer opens. Raises LookupError when the id is not in the tree.
    """
    import pikepdf

    tmp = _staging_path(pdf)
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


def _staging_path(pdf: Path) -> Path:
    """A PRIVATE staging name for an atomic save. Unique per call: a fixed
    `<name>.editing` is shared state, so two saves of the same document (the
    desktop window and the localhost web app open at once) could consume each
    other's half-written file. service serialises edits within one process;
    this closes the cross-process case too."""
    return pdf.with_name(f"{pdf.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.editing")


def _save_over(doc, pdf: Path) -> None:
    """Atomic in-place save: temp file, then replace (never a half-written PDF)."""
    tmp = _staging_path(pdf)
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
        _remove_from_parent(kids, element)
        # artifact content has no structure parent: clear the lookup entries of
        # the element AND of everything that just left the tree inside it
        _repoint_parent_tree(
            doc, pikepdf, dict.fromkeys(_element_and_descendants(element, pikepdf))
        )
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


def merge_elements(pdf: Path, node_ids: list[int], new_type: str) -> dict:
    """Merge several tags into ONE of type `new_type`: the first (in reading
    order) survives and receives every other tag's contents, in order -- the fix
    for one paragraph fragmented into many, or a figure split from its caption.

    All in one open->atomic-save, so a failure changes nothing. Ids nested
    inside other selected ids are ignored (the outermost tag wins). Raises
    LookupError when fewer than two of the ids resolve.
    """
    import pikepdf

    doc = pikepdf.open(pdf)
    try:
        found = _drop_nested(_elements_by_ids(doc, pikepdf, node_ids), pikepdf)
        if len(found) < 2:
            raise LookupError("select at least two tags to merge")
        ordered = [found[nid] for nid in sorted(found)]
        survivor, _ = ordered[0]
        survivor_pg = survivor.get("/Pg")
        merged_kids, _owner = _kid_list(survivor, pikepdf, doc.Root.StructTreeRoot)
        repoint: dict = {}
        for element, parent in ordered[1:]:
            source_pg = element.get("/Pg")
            same_page = (
                source_pg is not None
                and survivor_pg is not None
                and _objgen(source_pg) == _objgen(survivor_pg)
            )
            for kid in list(_kid_list(element, pikepdf, doc.Root.StructTreeRoot)[0]):
                if isinstance(kid, int) and not same_page and source_pg is not None:
                    # a bare MCID means "on my element's /Pg" -- moving it to a
                    # tag on another page must keep that page explicit
                    kid = pikepdf.Dictionary(Type=pikepdf.Name("/MCR"), Pg=source_pg, MCID=kid)
                merged_kids.append(kid)
                if _is_element(kid, pikepdf):
                    with suppress(Exception):
                        kid.P = survivor
            parent_kids, _po = _kid_list(parent, pikepdf, doc.Root.StructTreeRoot)
            _remove_from_parent(parent_kids, element)
            if _objgen(element) is not None:
                repoint[_objgen(element)] = survivor
        survivor.S = pikepdf.Name("/" + new_type)
        _repoint_parent_tree(doc, pikepdf, repoint)
        _save_over(doc, pdf)
    except BaseException:
        doc.close()
        raise
    return {"merged": len(ordered), "type": new_type}


def retag_elements(pdf: Path, node_ids: list[int], new_type: str) -> dict:
    """Set several tags to `new_type` in one atomic save (bulk retag)."""
    import pikepdf

    doc = pikepdf.open(pdf)
    try:
        found = _elements_by_ids(doc, pikepdf, node_ids)
        if not found:
            raise LookupError("none of the selected tags were found")
        was = []
        for nid in sorted(found):
            element, _parent = found[nid]
            was.append(str(element.get("/S")).lstrip("/"))
            element.S = pikepdf.Name("/" + new_type)
        _save_over(doc, pdf)
    except BaseException:
        doc.close()
        raise
    return {"changed": len(was), "was": was, "type": new_type}


def make_decorative_many(pdf: Path, node_ids: list[int]) -> dict:
    """Mark several tags decorative in one atomic save. Ids nested inside other
    selected ids are ignored (removing the outer tag covers them)."""
    import pikepdf

    doc = pikepdf.open(pdf)
    try:
        found = _drop_nested(_elements_by_ids(doc, pikepdf, node_ids), pikepdf)
        if not found:
            raise LookupError("none of the selected tags were found")
        artifacts = 0
        repoint: dict = {}
        for nid in sorted(found):
            element, parent = found[nid]
            artifacts += _mark_content_as_artifact(doc, pikepdf, element)
            kids, _owner = _kid_list(parent, pikepdf, doc.Root.StructTreeRoot)
            _remove_from_parent(kids, element)
            repoint.update(dict.fromkeys(_element_and_descendants(element, pikepdf)))
        _repoint_parent_tree(doc, pikepdf, repoint)
        _save_over(doc, pdf)
    except BaseException:
        doc.close()
        raise
    return {"removed": len(found), "artifacts": artifacts}


def _page_parent_array(doc, pikepdf, page):
    """The /ParentTree value array for `page` -- indexed BY MCID, so carving a
    line out of a tag must re-point the entry at that line's index. None when
    the document has no parent tree or the page no key into it."""
    root = doc.Root.get("/StructTreeRoot")
    tree = root.get("/ParentTree") if root is not None else None
    key = page.get("/StructParents")
    if tree is None or key is None:
        return None
    key = int(key)

    def find(node):
        nums = node.get("/Nums")
        if isinstance(nums, pikepdf.Array):
            for i in range(0, len(nums) - 1, 2):
                if int(nums[i]) == key:
                    value = nums[i + 1]
                    return value if isinstance(value, pikepdf.Array) else None
        kids = node.get("/Kids")
        if isinstance(kids, pikepdf.Array):
            for kid in kids:
                hit = find(kid)
                if hit is not None:
                    return hit
        return None

    return find(tree)


def _owns_mcid(kid, mcid: int, page, pikepdf) -> bool:
    """Is this /K entry the marked-content reference for `mcid` on `page`?"""
    if isinstance(kid, int):
        return int(kid) == mcid
    if isinstance(kid, pikepdf.Dictionary) and kid.get("/Type") == pikepdf.Name("/MCR"):
        same_page = kid.get("/Pg") is None or _objgen(kid.get("/Pg")) == _objgen(page)
        return same_page and kid.get("/MCID") is not None and int(kid.get("/MCID")) == mcid
    return False


def carve_lines(pdf: Path, picks: list[dict], new_type: str) -> dict:
    """Carve chosen LINES out of their tags into one NEW tag of `new_type` --
    the fix for a heading the engine swallowed into the paragraph below it.
    Each pick is {"node_id": tag, "mcid": line}; all must sit on one page.

    The new tag lands next to the first donor in reading order -- before it when
    the donor's opening line was carved (a heading belongs above its paragraph),
    after it otherwise. A donor left with nothing is removed. The per-line
    parent-tree entries are re-pointed at the new tag; without that the file
    would look right but map its content to the wrong tags.

    One open -> atomic save. Raises LookupError / ValueError on bad picks.
    """
    import pikepdf

    doc = pikepdf.open(pdf)
    try:
        by_donor: dict[int, list[int]] = {}
        for pick in picks:
            by_donor.setdefault(int(pick["node_id"]), []).append(int(pick["mcid"]))
        found = _elements_by_ids(doc, pikepdf, list(by_donor))
        if len(found) < len(by_donor):
            raise LookupError("some of the selected tags were not found")

        pages = {
            _objgen(found[nid][0].get("/Pg"))
            for nid in by_donor
            if found[nid][0].get("/Pg") is not None
        }
        if len(pages) > 1:
            raise ValueError("select lines on one page at a time")

        moved: list[tuple[int, object]] = []  # (mcid, the /K entry), for ordering
        emptied: list[tuple] = []  # (element, parent) drained of everything
        first_id = min(by_donor)
        first_donor, first_parent = found[first_id]
        carve_page = first_donor.get("/Pg")
        opening_line_carved = False

        for nid in sorted(by_donor):
            element, parent = found[nid]
            kids, _owner = _kid_list(element, pikepdf, doc.Root.StructTreeRoot)
            content = [
                (i, kid)
                for i, kid in enumerate(kids)
                if isinstance(kid, int)
                or (
                    isinstance(kid, pikepdf.Dictionary) and kid.get("/Type") == pikepdf.Name("/MCR")
                )
            ]
            for mcid in sorted(set(by_donor[nid])):
                hit = next(
                    (
                        (i, kid)
                        for i, kid in content
                        if _owns_mcid(kid, mcid, element.get("/Pg"), pikepdf)
                    ),
                    None,
                )
                if hit is None:
                    raise LookupError(f"tag {nid} does not contain line {mcid}")
                if nid == first_id and content and hit[0] == content[0][0]:
                    opening_line_carved = True
                moved.append((mcid, hit[1]))
            for mcid in sorted(set(by_donor[nid]), reverse=True):
                for i in range(len(kids) - 1, -1, -1):
                    if _owns_mcid(kids[i], mcid, element.get("/Pg"), pikepdf):
                        del kids[i]
                        break
            if len(kids) == 0:
                emptied.append((element, parent))

        moved.sort(key=lambda pair: pair[0])  # content order = MCID order
        new_element = doc.make_indirect(
            pikepdf.Dictionary(
                S=pikepdf.Name("/" + new_type),
                K=pikepdf.Array([kid for _mcid, kid in moved]),
            )
        )
        if carve_page is not None:
            new_element.Pg = carve_page

        home_kids, home = _kid_list(first_parent, pikepdf, doc.Root.StructTreeRoot)
        index = len(home_kids)
        for i, kid in enumerate(home_kids):
            if _objgen(kid) == _objgen(first_donor):
                index = i if opening_line_carved else i + 1
                break
        home_kids.insert(index, new_element)
        new_element.P = home

        for element, parent in emptied:
            kids, _owner = _kid_list(parent, pikepdf, doc.Root.StructTreeRoot)
            _remove_from_parent(kids, element)

        if carve_page is not None:
            parents = _page_parent_array(doc, pikepdf, carve_page)
            if parents is not None:
                for mcid, _kid in moved:
                    if 0 <= mcid < len(parents):
                        parents[mcid] = new_element
        _save_over(doc, pdf)
    except BaseException:
        doc.close()
        raise
    return {
        "carved": len(moved),
        "from": len(by_donor),
        "emptied": len(emptied),
        "type": new_type,
    }


# paint operators, in the sense pdfium counts page objects: one object per
# text-showing / path-painting / XObject / shading / inline-image operator.
_PAINT_OPS = {
    "Tj", "TJ", "'", '"',  # text
    "S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n",  # paths
    "Do", "sh", "INLINE IMAGE",  # xobjects / shading / inline images
}  # fmt: skip


def _ensure_parent_entry(doc, pikepdf, page, element, mcids: list[int]) -> None:
    """Record `element` as the owner of `mcids` in the page's parent-tree
    array, creating the machinery (array, number-tree entry, /StructParents
    key) when the document lacks it."""
    root = doc.Root.StructTreeRoot
    tree = root.get("/ParentTree")
    if tree is None:
        tree = doc.make_indirect(pikepdf.Dictionary(Nums=pikepdf.Array([])))
        root.ParentTree = tree
    if page.get("/StructParents") is None:
        nums = tree.get("/Nums")
        used = (
            [int(nums[i]) for i in range(0, len(nums) - 1, 2)]
            if isinstance(nums, pikepdf.Array)
            else []
        )
        page.StructParents = max(used, default=-1) + 1
    parents = _page_parent_array(doc, pikepdf, page)
    if parents is None:
        parents = pikepdf.Array([])
        key = int(page.StructParents)
        nums = tree.get("/Nums")
        if not isinstance(nums, pikepdf.Array):
            nums = pikepdf.Array([])
            tree.Nums = nums
        spot = len(nums)
        for i in range(0, len(nums) - 1, 2):
            if int(nums[i]) > key:
                spot = i
                break
        nums.insert(spot, key)
        nums.insert(spot + 1, doc.make_indirect(parents))
        parents = _page_parent_array(doc, pikepdf, page)
    null = pikepdf.Object.parse(b"null")
    for mcid in mcids:
        while len(parents) <= mcid:
            parents.append(null)
        parents[mcid] = element


def tag_objects(pdf: Path, page_index: int, object_indexes: list[int], new_type: str) -> dict:
    """Wrap UNTAGGED drawn content in a brand-new tag: the chosen page objects
    (by drawing order) get fresh marked-content ids, one new structure element
    of `new_type` owning them all, and parent-tree entries -- how a reviewer
    tags text the engine missed, or brings decorated content back.

    Wrapping is insertion-only: BDC/EMC pairs go AROUND whole operators, so no
    operator is split and no graphics state is touched. An enclosing /Artifact
    span is closed before the wrap and reopened after it, so the new content is
    not nested inside background. Safety: pdfium's object list must match the
    stream's paint operators 1:1 (validated here, per page); on any mismatch
    the operation refuses rather than guessing -- Acrobat is the fallback.

    Works on a fully untagged document too (creates the structure root), which
    is how "Remove all tags" stops being a one-way door.
    """
    import pikepdf
    import pypdfium2 as pdfium

    wanted = sorted({int(i) for i in object_indexes})
    if not wanted:
        raise ValueError("nothing selected")

    with PDFIUM_LOCK:
        import pypdfium2.raw as raw

        fdoc = pdfium.PdfDocument(pdf.read_bytes())  # bytes: no lingering file handle
        try:
            fpage = fdoc[page_index]
            marked: list[bool] = []
            for i in range(raw.FPDFPage_CountObjects(fpage.raw)):
                obj = raw.FPDFPage_GetObject(fpage.raw, i)
                has = False
                for m in range(raw.FPDFPageObj_CountMarks(obj)):
                    mark = raw.FPDFPageObj_GetMark(obj, m)
                    v = ctypes.c_int(-1)
                    if raw.FPDFPageObjMark_GetParamIntValue(mark, b"MCID", ctypes.byref(v)):
                        has = True
                        break
                marked.append(has)
        finally:
            fdoc.close()
    if wanted[-1] >= len(marked):
        raise LookupError("the selection no longer matches the document - reload it")
    if any(marked[i] for i in wanted):
        raise ValueError("part of the selection is already tagged - reload and try again")

    doc = pikepdf.open(pdf)
    try:
        page = doc.pages[page_index]
        with suppress(Exception):
            page.contents_coalesce()
        instructions = list(pikepdf.parse_content_stream(page))
        paint_at = [i for i, (_ops, op) in enumerate(instructions) if str(op) in _PAINT_OPS]
        if len(paint_at) != len(marked):
            raise ValueError(
                "this page's drawing is too unusual to tag safely here - tag it in Acrobat"
            )
        chosen = {paint_at[i] for i in wanted}
        first_chosen = min(chosen)

        # fresh MCIDs continue after the page's highest existing one
        next_mcid = -1
        for operands, op in instructions:
            if str(op) == "BDC" and len(operands) == 2:
                props = operands[1]
                if isinstance(props, pikepdf.Dictionary) and props.get("/MCID") is not None:
                    next_mcid = max(next_mcid, int(props.get("/MCID")))
        next_mcid += 1

        # map out every marked-content span first: an /Artifact span whose WHOLE
        # painted content was chosen (the shape "mark as decorative" leaves) is
        # converted back into a content marking in place -- the exact inverse of
        # decorating, with no re-nesting. Only partially-chosen spans need the
        # step-out/step-in treatment.
        spans: list[dict] = []
        open_spans: list[dict] = []
        prev_mcid = None  # the last tagged line BEFORE the selection, in stream order
        next_existing = None  # ...and the first one AFTER it
        for idx, (operands, op) in enumerate(instructions):
            s = str(op)
            if s in ("BDC", "BMC"):
                is_mcid = (
                    s == "BDC"
                    and len(operands) == 2
                    and isinstance(operands[1], pikepdf.Dictionary)
                    and operands[1].get("/MCID") is not None
                )
                if is_mcid:
                    value = int(operands[1].get("/MCID"))
                    if idx < first_chosen:
                        prev_mcid = value
                    elif next_existing is None:
                        next_existing = value
                span = {"start": idx, "is_mcid": is_mcid, "paint": set(), "depth": len(open_spans)}
                spans.append(span)
                open_spans.append(span)
            elif s == "EMC" and open_spans:
                open_spans.pop()
            elif idx in chosen:
                if any(sp["is_mcid"] for sp in open_spans):
                    raise ValueError(
                        "part of the selection is already tagged - reload and try again"
                    )
                for sp in open_spans:
                    sp["paint"].add(idx)
        absorbed: dict[int, int] = {}  # span start idx -> its fresh MCID
        covered: set[int] = set()
        for span in spans:  # outermost first (list is in open order)
            if span["is_mcid"] or not span["paint"] or not span["paint"] <= chosen:
                continue
            if span["start"] in covered:
                continue  # nested inside an already-absorbed span
            absorbed[span["start"]] = next_mcid
            next_mcid += 1
            covered |= span["paint"]
            covered |= {
                s2["start"]
                for s2 in spans
                if s2["depth"] > span["depth"] and s2["paint"] and s2["paint"] <= span["paint"]
            }

        out: list[bytes] = []
        stack: list[tuple[bool, bytes]] = []  # every open span: (absorbed?, its opener)
        new_mcids: list[int] = sorted(absorbed.values())
        for idx, (operands, op) in enumerate(instructions):
            token = pikepdf.unparse_content_stream([(operands, op)])
            s = str(op)
            if s in ("BDC", "BMC"):
                if idx in absorbed:
                    out.append(f"/{new_type} <</MCID {absorbed[idx]}>> BDC".encode())
                    stack.append((True, token))
                else:
                    out.append(token)
                    stack.append((False, token))
                continue
            if s == "EMC":
                out.append(token)
                if stack:
                    stack.pop()
                continue
            if idx in chosen and idx not in covered:
                # a chosen-but-not-covered op is never inside an absorbed span,
                # so everything open here is a kept /Artifact span to step out of
                reopen = [opener for _absorbed, opener in stack]
                out.extend([b"EMC"] * len(reopen))
                out.append(f"/{new_type} <</MCID {next_mcid}>> BDC".encode())
                out.append(token)
                out.append(b"EMC")
                out.extend(reopen)
                new_mcids.append(next_mcid)
                next_mcid += 1
            else:
                out.append(token)
        page.Contents = doc.make_stream(b"\n".join(out))

        # a document stripped of all tags gets its structure root back here
        root = doc.Root.get("/StructTreeRoot")
        if root is None:
            document = doc.make_indirect(pikepdf.Dictionary(S=pikepdf.Name("/Document")))
            root = doc.make_indirect(
                pikepdf.Dictionary(Type=pikepdf.Name.StructTreeRoot, K=document)
            )
            doc.Root.StructTreeRoot = root
            doc.Root.MarkInfo = pikepdf.Dictionary(Marked=True)
            document.P = root

        element = doc.make_indirect(
            pikepdf.Dictionary(
                S=pikepdf.Name("/" + new_type),
                Pg=page.obj,
                K=pikepdf.Array(new_mcids),
            )
        )

        # reading-order placement: right after the tag that owns the line just
        # BEFORE the selection in drawing order (or before the one just after)
        anchor_mcid, place_after = (
            (prev_mcid, True) if prev_mcid is not None else (next_existing, False)
        )
        # containers with a STRICT content model: a bare paragraph placed inside
        # one is itself a PDF/UA violation (e.g. 7.2-20: LI holds only Lbl and
        # LBody), so the new tag climbs out to where a block element is legal.
        strict = {"/L", "/LI", "/Lbl", "/Table", "/TR", "/THead", "/TBody", "/TFoot"}
        strict |= {"/TOC", "/Ruby", "/Warichu"}
        anchor = anchor_parent = None
        if anchor_mcid is not None:
            for _nid, candidate in _walk_elements(doc, pikepdf):
                kids = candidate.get("/K")
                if not isinstance(kids, pikepdf.Array):
                    kids = [kids] if kids is not None else []
                on_page = candidate.get("/Pg") is None or _objgen(candidate.get("/Pg")) == _objgen(
                    page.obj
                )
                if on_page and any(_owns_mcid(kid, anchor_mcid, page.obj, pikepdf) for kid in kids):
                    anchor = candidate
                    break
            if anchor is not None:
                _found, anchor_parent = _find_with_parent_element(doc, pikepdf, anchor)
                for _climb in range(50):
                    if anchor_parent is None or str(anchor_parent.get("/S")) not in strict:
                        break
                    anchor = anchor_parent
                    _found, anchor_parent = _find_with_parent_element(doc, pikepdf, anchor)
        if anchor is not None:
            kids, owner = _kid_list(anchor_parent, pikepdf, root)
            index = len(kids)
            for i, kid in enumerate(kids):
                if _objgen(kid) == _objgen(anchor):
                    index = i + 1 if place_after else i
                    break
            kids.insert(index, element)
            element.P = owner
        else:
            holder = root.get("/K")
            if _is_element(holder, pikepdf):  # a Document container: go inside it
                kids, owner = _kid_list(holder, pikepdf, root)
            else:
                kids, owner = _kid_list(None, pikepdf, root)
            kids.append(element)
            element.P = owner

        _ensure_parent_entry(doc, pikepdf, page, element, new_mcids)
        _save_over(doc, pdf)
    except BaseException:
        doc.close()
        raise
    return {"tagged": len(new_mcids), "type": new_type}


def _find_with_parent_element(doc, pikepdf, element):
    """(element, parent) for an element we already hold (matched by objgen)."""
    root = doc.Root.get("/StructTreeRoot")
    target = _objgen(element)

    def walk(node, parent):
        if isinstance(node, pikepdf.Array):
            for kid in node:
                hit = walk(kid, parent)
                if hit is not None:
                    return hit
            return None
        if not _is_element(node, pikepdf):
            return None
        if _objgen(node) == target:
            return node, parent
        return walk(node.get("/K"), node) if node.get("/K") is not None else None

    hit = walk(root.get("/K"), None) if root is not None else None
    return hit if hit is not None else (None, None)


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


# pdfium page-object types worth offering for tagging (paths/shading are
# almost always graphic furniture; text, images and form XObjects are content)
_TAGGABLE_KINDS = {1: "text", 3: "image", 5: "form"}


def _page_geometry(doc):
    """Per page, from pdfium's object list (drawing order): the MCID -> union
    bounding box map every highlight uses, AND the objects that carry NO MCID
    mark at all -- the untagged content the drag tool can wrap in a new tag."""
    import pypdfium2.raw as raw

    mcid_boxes: list[dict[int, tuple[float, float, float, float]]] = []
    untagged: list[list[UntaggedRef]] = []
    for pageno, page in enumerate(doc):
        boxes: dict[int, tuple[float, float, float, float]] = {}
        loose: list[UntaggedRef] = []
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
            box = (left.value, bottom.value, right.value, top.value)
            has_mcid = False
            for m in range(raw.FPDFPageObj_CountMarks(obj)):
                mark = raw.FPDFPageObj_GetMark(obj, m)
                mcid = ctypes.c_int(-1)
                if not raw.FPDFPageObjMark_GetParamIntValue(mark, b"MCID", ctypes.byref(mcid)):
                    continue
                has_mcid = True
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
            kind = _TAGGABLE_KINDS.get(raw.FPDFPageObj_GetType(obj))
            if not has_mcid and kind is not None and box[2] > box[0] and box[3] > box[1]:
                loose.append(UntaggedRef(page=pageno, index=i, box=list(box), kind=kind))
        mcid_boxes.append(boxes)
        untagged.append(loose)
    return mcid_boxes, untagged


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


def _page_sizes_from_pikepdf(doc) -> list[PageSize]:
    """Page sizes read from /MediaBox instead of pdfium -- the fallback when
    the native library cannot read this document, so the pages still lay out."""
    sizes: list[PageSize] = []
    for page in doc.pages:
        obj = getattr(page, "obj", page)
        try:
            box = [float(v) for v in obj.get("/MediaBox", [0, 0, 612, 792])]
            width, height = abs(box[2] - box[0]), abs(box[3] - box[1])
            if int(obj.get("/Rotate", 0) or 0) % 180 == 90:  # landscape display
                width, height = height, width
        except Exception:
            width, height = 612.0, 792.0  # US Letter: a sane band to draw
        sizes.append(PageSize(width=width or 612.0, height=height or 792.0))
    return sizes


def _structure_tree_unlocked(pdf: Path, pikepdf, pdfium) -> StructureTree:
    # pdfium contributes the page GEOMETRY: highlight boxes, the untagged
    # content the drag tool finds, page sizes and text snippets. A damaged
    # document can fault inside the native library (reported live as
    # "exception: access violation reading 0xFFFFFFFFFFFFFFFF"), and losing the
    # whole document over that is a bad trade -- the tag tree itself comes from
    # pikepdf and is unaffected. So: geometry is best-effort, the tree is not.
    fpdf = None
    geo: list = []
    loose: list = []
    sizes: list[PageSize] = []
    textpages: list = []
    geometry_error: str | None = None
    try:
        fpdf = pdfium.PdfDocument(pdf.read_bytes())  # bytes: no lingering handle
        geo, untagged_pages = _page_geometry(fpdf)
        loose = [ref for page_refs in untagged_pages for ref in page_refs]
        sizes = [PageSize(width=p.get_size()[0], height=p.get_size()[1]) for p in fpdf]
        textpages = [p.get_textpage() for p in fpdf]
    except Exception as exc:  # incl. OSError from a native access violation
        geometry_error = f"{type(exc).__name__}: {str(exc)[:100]}"
        geo, loose, textpages = [], [], []
        if fpdf is not None:
            with suppress(Exception):
                fpdf.close()
            fpdf = None

    try:
        with pikepdf.open(pdf) as doc:
            if not sizes:  # pdfium failed: lay the pages out from /MediaBox
                sizes = _page_sizes_from_pikepdf(doc)
            root = doc.Root.get("/StructTreeRoot")
            if root is None:
                return StructureTree(
                    tagged=False,
                    pages=sizes,
                    untagged=loose,
                    geometry_error=geometry_error,
                )
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
                lines: list[LineRef] = []
                for pg, mcid in mcids:
                    if pg is None or not (0 <= pg < len(geo)):
                        continue
                    box = geo[pg].get(mcid)
                    if box is not None:
                        own_box = _union(own_box, list(box))
                        node_page = pg if node_page is None else node_page
                        lines.append(LineRef(page=pg, mcid=mcid, box=list(box)))
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
                        lines=lines,
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
            return StructureTree(
                tagged=True,
                pages=sizes,
                root=tree,
                truncated=state["truncated"],
                untagged=loose,
                geometry_error=geometry_error,
            )
    finally:
        if fpdf is not None:
            fpdf.close()
