"""The service layer -- the one engine every client calls.

The CLI (cli.py) today and the desktop UI's pywebview bridge (speade.desktop,
next) are THIN SHELLS over these functions. No client owns pipeline or gate
logic of its own: the mandatory-human-gate invariant lives here, once, so it
cannot drift between front doors.

Every function loads the typed Config and resolves relative data paths against
the CONFIG FILE'S directory -- not the process CWD and not the repo checkout --
so a deployed copy (lab PC, packaged .exe) behaves the same wherever it is
launched from. Return values are pydantic models: JSON-serialisable for free,
which is exactly what the JS bridge will need.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from speade.audit.log import append_event, read_events, sha256_file
from speade.config import load_config
from speade.pipeline import registry, runner
from speade.pipeline.contract import Approval, ApprovalStatus, Sidecar
from speade.validation import structure, verapdf
from speade.validation.structure import (
    StructureSummary,
    StructureTree,
    summarize,
)
from speade.validation.structure import (
    structure_tree as _structure_tree,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


class Workspace(BaseModel):
    """The resolved working environment one config file describes."""

    stages: dict[str, str]  # stage_role -> implementation name, in pipeline order
    inbox: Path
    outbox: Path
    sidecars: Path  # per-document records live here, keeping the outbox deliverables-only
    audit_log: Path
    verapdf_profile: str
    verapdf_cli: str | None


class BatchItem(BaseModel):
    """One file's outcome in a batch sweep -- one bad file never kills a batch."""

    file: str
    ok: bool
    skipped: bool = False  # already processed and unchanged: not run again
    error: str | None = None
    sidecar: Sidecar | None = None


class QueueItem(BaseModel):
    """One outbox draft, summarised for a review-queue listing."""

    file: str  # pdf filename in the outbox
    route: str
    stages_applied: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    verapdf_passed: bool | None = None
    verapdf_failed_clauses: list[str] = Field(default_factory=list)
    status: str = "draft"  # draft / approved / rejected
    reviewer: str | None = None
    # do the bytes on disk still match the recorded fingerprint? None = no file /
    # no fingerprint to compare. The UI shows this as plain language ("edited since
    # processing"), never the raw hash -- the hashes themselves stay in the sidecar
    # and audit log, which are the trust trail.
    output_changed: bool | None = None
    # did OCR actually build this document's pages? (stages_applied lists every
    # stage that RAN, including ones that skipped themselves -- the UI needs the
    # honest "text was recognised" signal, not the itinerary)
    ocr_layered: bool = False
    undo_depth: int = 0  # single-step undos available (see undo_last_edit)


def _hide_dir(path: Path) -> None:
    """Mark an app-internal folder hidden on Windows so reviewers browsing the
    data folder are not tempted to edit records by hand. Polish, not security:
    the append-only audit log + decide()'s re-fingerprint are what make
    tampering detectable. No-op off Windows and on any attribute failure."""
    if sys.platform != "win32":
        return
    import ctypes

    hidden = 0x2  # FILE_ATTRIBUTE_HIDDEN
    attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
    if attrs not in (-1, 0xFFFFFFFF) and not (attrs & hidden):
        ctypes.windll.kernel32.SetFileAttributesW(str(path), attrs | hidden)


def workspace(config_path: Path = DEFAULT_CONFIG_PATH) -> Workspace:
    """Load `config_path`, resolve its relative paths against its own folder,
    and ensure the data folders exist."""
    config = load_config(config_path)
    base = Path(config_path).resolve().parent

    def resolve(path: Path) -> Path:
        return path if path.is_absolute() else (base / path).resolve()

    ws = Workspace(
        stages=dict(config.pipeline.stages),
        inbox=resolve(config.io.local.inbox),
        outbox=resolve(config.io.local.outbox),
        sidecars=resolve(config.io.local.sidecars),
        audit_log=resolve(config.audit.log_path),
        verapdf_profile=config.validation.verapdf.profile,
        verapdf_cli=config.validation.verapdf.path,
    )
    # the folders are part of the workspace contract: every client (CLI, exe)
    # finds them ready at startup, so a fresh install has an inbox to drop
    # files into before the first run -- nothing materialises lazily.
    ws.inbox.mkdir(parents=True, exist_ok=True)
    ws.outbox.mkdir(parents=True, exist_ok=True)
    # decided documents are sorted into status subfolders so the outbox itself
    # is self-explanatory: root = awaiting review, approved/ = ready to ship,
    # rejected/ = needs manual (Acrobat) rework.
    (ws.outbox / "approved").mkdir(parents=True, exist_ok=True)
    (ws.outbox / "rejected").mkdir(parents=True, exist_ok=True)
    ws.sidecars.mkdir(parents=True, exist_ok=True)
    ws.audit_log.parent.mkdir(parents=True, exist_ok=True)
    _hide_dir(ws.sidecars)
    _hide_dir(ws.audit_log.parent)
    return ws


def stage_mapping(config_path: Path = DEFAULT_CONFIG_PATH) -> dict[str, str]:
    """The configured stage_role -> implementation mapping, in pipeline order."""
    return workspace(config_path).stages


def _find_output(ws: Workspace, name: str) -> Path | None:
    """Locate an output PDF by bare filename: outbox root first (a fresh draft
    shadows any older decided copy), then the approved/ and rejected/ subfolders."""
    for folder in (ws.outbox, ws.outbox / "approved", ws.outbox / "rejected"):
        candidate = folder / name
        if candidate.is_file():
            return candidate
    return None


def find_output(name: str, config_path: Path = DEFAULT_CONFIG_PATH) -> Path | None:
    """Public wrapper for clients that hold only a filename (the bridge, the web
    app): where in the outbox tree does this document currently live?"""
    return _find_output(workspace(config_path), Path(name).name)


def _score_draft(ws: Workspace, sidecar: Sidecar) -> Sidecar:
    """Run the veraPDF gate on a freshly written draft and persist the verdict,
    so the review queue shows machine feedback IMMEDIATELY after processing --
    the reviewer must not need Acrobat just to learn whether tagging worked.
    Advisory only: decide() re-runs veraPDF at sign-off on the bytes being
    approved, which stays the authoritative check."""
    out_pdf = ws.outbox / Path(sidecar.source_path).name
    vera = verapdf.validate(out_pdf, ws.verapdf_profile, cli=ws.verapdf_cli)
    sidecar.verapdf_passed = vera.passed
    sidecar.verapdf_failed_clauses = vera.failed_clauses
    side_path = ws.sidecars / (out_pdf.name + ".sidecar.json")
    side_path.write_text(sidecar.model_dump_json(), encoding="utf-8")
    return sidecar


def run_one(pdf: Path, config_path: Path = DEFAULT_CONFIG_PATH) -> Sidecar:
    """Run one PDF through the configured pipeline into the outbox, then score
    the draft with veraPDF (advisory; see _score_draft).

    Raises KeyError for an unknown stage implementation and lets pipeline
    failures propagate -- single-file callers want the real error.
    """
    ws = workspace(config_path)
    stages = [registry.get_stage(impl) for impl in ws.stages.values()]
    sidecar = runner.run(pdf, stages, ws.outbox, ws.audit_log, sidecar_dir=ws.sidecars)
    return _score_draft(ws, sidecar)


def _needs_processing(ws: Workspace, pdf: Path) -> bool:
    """Does this inbox file still need a run? False only when a sidecar exists
    whose recorded source fingerprint matches the file's current bytes AND its
    output still exists somewhere in the outbox tree -- i.e. this exact document
    was already processed. A replaced/edited source (new hash) processes again."""
    side_path = ws.sidecars / (pdf.name + ".sidecar.json")
    if not side_path.is_file():
        return True
    try:
        sidecar = Sidecar.model_validate_json(side_path.read_text(encoding="utf-8"))
    except Exception:  # a mangled record: reprocess rather than trust it
        return True
    if sidecar.source_sha256 != sha256_file(pdf):
        return True
    return _find_output(ws, pdf.name) is None


def list_pending(config_path: Path = DEFAULT_CONFIG_PATH) -> list[str]:
    """Inbox files still waiting to be processed (the sidebar's 'Waiting to
    process' section): everything in the inbox minus already-done documents."""
    ws = workspace(config_path)
    return [
        p.name for p in sorted(ws.inbox.glob("*.pdf")) if p.is_file() and _needs_processing(ws, p)
    ]


def run_batch(
    folder: Path | None = None,
    config_path: Path = DEFAULT_CONFIG_PATH,
    progress: Callable[[int, int, str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    reprocess: bool = False,
) -> list[BatchItem]:
    """Sweep every *.pdf in `folder` (default: the configured inbox) through the
    pipeline, scoring each draft with veraPDF (see _score_draft). Per-file
    failures are captured as BatchItem.error so the rest of the batch still
    runs; a misconfigured pipeline (unknown stage) raises.

    Files already processed and unchanged (see _needs_processing) are SKIPPED --
    adding one new PDF to an inbox of ten must not re-run (and re-draft) the
    other nine. `reprocess=True` forces everything through again (e.g. after a
    pipeline upgrade).

    `progress`, when given, is called as (done, total, current_filename) before
    each file and once as (processed, total, "") at the end, counting only the
    files actually being run -- the per-file progress bar's feed.

    `cancel`, when given, is polled BETWEEN documents (the Stop button): the
    file in flight always finishes, so nothing is left half-written, and the
    items processed so far are returned as normal."""
    ws = workspace(config_path)
    src_dir = Path(folder) if folder is not None else ws.inbox
    stages = [registry.get_stage(impl) for impl in ws.stages.values()]  # fail fast on config

    pdfs = sorted(p for p in src_dir.glob("*.pdf") if p.is_file())
    todo = [p for p in pdfs if reprocess or _needs_processing(ws, p)]
    items: list[BatchItem] = [
        BatchItem(file=p.name, ok=True, skipped=True) for p in pdfs if p not in todo
    ]
    done = 0
    for pdf in todo:
        if cancel is not None and cancel():
            break
        if progress is not None:
            progress(done, len(todo), pdf.name)
        try:
            sidecar = runner.run(pdf, stages, ws.outbox, ws.audit_log, sidecar_dir=ws.sidecars)
            sidecar = _score_draft(ws, sidecar)
            items.append(BatchItem(file=pdf.name, ok=True, sidecar=sidecar))
        except Exception as exc:  # per-file isolation: record, continue the sweep
            items.append(
                BatchItem(file=pdf.name, ok=False, error=f"{type(exc).__name__}: {str(exc)[:200]}")
            )
        done += 1
    if progress is not None:
        progress(done, len(todo), "")
    return items


def list_queue(config_path: Path = DEFAULT_CONFIG_PATH) -> list[QueueItem]:
    """Summarise every outbox draft (its sidecar) for a review-queue listing."""
    ws = workspace(config_path)
    items: list[QueueItem] = []
    for side_path in sorted(ws.sidecars.glob("*.pdf.sidecar.json")):
        pdf_name = side_path.name.removesuffix(".sidecar.json")
        try:
            sidecar = Sidecar.model_validate_json(side_path.read_text(encoding="utf-8"))
        except Exception:  # a mangled sidecar must not hide the rest of the queue
            items.append(QueueItem(file=pdf_name, route="unknown", status="invalid-sidecar"))
            continue
        # compare the bytes on disk with the recorded fingerprint, so the UI can
        # say "edited since processing" (e.g. an Acrobat fix) in plain language.
        out_pdf = _find_output(ws, pdf_name)
        changed: bool | None = None
        if out_pdf is not None and sidecar.output_sha256:
            changed = sha256_file(out_pdf) != sidecar.output_sha256
        items.append(
            QueueItem(
                file=pdf_name,
                route=sidecar.route.value,
                stages_applied=sidecar.stages_applied,
                flags=sidecar.flags,
                verapdf_passed=sidecar.verapdf_passed,
                verapdf_failed_clauses=sidecar.verapdf_failed_clauses,
                status=sidecar.approval.status.value,
                reviewer=sidecar.approval.reviewer,
                output_changed=changed,
                ocr_layered=sidecar.ocr_layered,
                undo_depth=len(_undo_snapshots(ws, pdf_name)),
            )
        )
    return items


def doc_metadata(pdf: Path) -> dict[str, str]:
    """The draft's current display title (dc:title) and reading language (/Lang)
    -- what the review UI shows in its editable metadata fields. Requires the
    `tag` extra (pikepdf); the caller turns an ImportError into a UI note."""
    import pikepdf  # lazy: an optional extra, like the tag stage's finish step

    with pikepdf.open(pdf) as doc:
        lang = str(doc.Root.get("/Lang", "") or "")
        with doc.open_metadata() as meta:
            title = str(meta.get("dc:title", "") or "")
    return {"title": title, "lang": lang}


def set_doc_metadata(
    pdf: Path,
    title: str,
    lang: str,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> dict[str, str]:
    """Apply the REVIEWER'S title and reading language to an outbox draft -- the
    human-gate half of the metadata story (the tag stage only stamps defaults).
    Title and language are judgment calls (7.1-9 dc:title, 7.2 /Lang), so they
    are human-authored here, never auto-detected.

    Writes via a temp file then replaces, refreshes the sidecar fingerprint
    (an app-made edit must not read as external tampering in the queue), and
    audit-logs the change. Empty fields leave the existing value untouched."""
    import pikepdf  # lazy: an optional extra, like the tag stage's finish step

    ws = workspace(config_path)
    title, lang = title.strip(), lang.strip()
    tmp = pdf.with_name(pdf.name + ".meta.tmp")
    with pikepdf.open(pdf) as doc:
        if lang:
            doc.Root.Lang = pikepdf.String(lang)
        if doc.Root.get("/ViewerPreferences") is None:
            doc.Root.ViewerPreferences = pikepdf.Dictionary()
        doc.Root.ViewerPreferences.DisplayDocTitle = True  # 7.1-10: show title, not filename
        if title:
            with doc.open_metadata() as meta:
                meta["dc:title"] = title
        doc.save(tmp)
    tmp.replace(pdf)

    new_sha = sha256_file(pdf)
    side_path = ws.sidecars / (pdf.name + ".sidecar.json")
    if side_path.is_file():
        try:
            sidecar = Sidecar.model_validate_json(side_path.read_text(encoding="utf-8"))
            sidecar.output_sha256 = new_sha
            side_path.write_text(sidecar.model_dump_json(indent=2), encoding="utf-8", newline="\n")
        except Exception:  # a mangled sidecar: the metadata edit itself still stands
            pass

    append_event(
        ws.audit_log,
        {
            "event": "edit-metadata",
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "file": pdf.name,
            "title": title or None,
            "lang": lang or None,
            "output_sha256": new_sha,
        },
    )
    return doc_metadata(pdf)


def structure_summary(pdf: Path) -> StructureSummary:
    """Summarise the tag structure of an outbox draft (see validation.structure):
    the in-app answer to "is it actually tagged, and roughly how well"."""
    return summarize(pdf)


def structure_tree(pdf: Path) -> StructureTree:
    """The full tag tree with page geometry (see validation.structure): the
    in-app tags panel with Acrobat-style highlight boxes."""
    return _structure_tree(pdf)


# The tag types a reviewer may assign in-app. Deliberately the everyday set:
# structural containers (Part/Sect/Art) and table internals are left to
# Acrobat, where the surrounding structure can be seen and repaired properly.
EDITABLE_TAG_TYPES = (
    "H1",
    "H2",
    "H3",
    "H4",
    "H5",
    "H6",
    "P",
    "L",
    "LI",
    "Lbl",
    "LBody",
    "Caption",
    "Figure",
    "Table",
    "TR",
    "TH",
    "TD",
    "BlockQuote",
    "Note",
    "Span",
)


# How many single-step undos are kept per document. Snapshots are whole PDFs,
# so this is a memory-of-disk tradeoff: deep enough for a real editing session,
# bounded so a big batch cannot fill a lab PC.
_UNDO_DEPTH = 20


def _undo_dir(ws: Workspace) -> Path:
    """Where per-document edit snapshots live (inside the hidden sidecar area,
    so reviewers browsing data/ are not tempted to poke at them)."""
    return ws.sidecars / ".undo"


def _undo_snapshots(ws: Workspace, name: str) -> list[Path]:
    """This document's snapshots, oldest first."""
    folder = _undo_dir(ws)
    if not folder.is_dir():
        return []
    return sorted(folder.glob(f"{name}.*.bak"), key=lambda p: int(p.suffixes[-2].lstrip(".")))


def undo_depth(name: str, config_path: Path = DEFAULT_CONFIG_PATH) -> int:
    """How many single-step undos are available for this document."""
    return len(_undo_snapshots(workspace(config_path), Path(name).name))


def _snapshot(ws: Workspace, pdf: Path) -> Path | None:
    """Copy the document aside so the edit about to happen can be undone one
    step. Returns the snapshot path, or None if it could not be written --
    failure to snapshot must not block the edit itself."""
    folder = _undo_dir(ws)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        existing = _undo_snapshots(ws, pdf.name)
        for stale in existing[: max(0, len(existing) + 1 - _UNDO_DEPTH)]:
            stale.unlink(missing_ok=True)
        nxt = 1 + max((int(p.suffixes[-2].lstrip(".")) for p in existing), default=0)
        target = folder / f"{pdf.name}.{nxt}.bak"
        shutil.copy2(pdf, target)
        return target
    except OSError:
        return None


@contextmanager
def _edit_guard(ws: Workspace, pdf: Path):
    """Snapshot for undo, and DISCARD that snapshot if the edit fails -- a
    refused edit must not leave a no-op step in the undo history."""
    snapshot = _snapshot(ws, pdf)
    try:
        yield
    except BaseException:
        if snapshot is not None:
            with suppress(OSError):
                snapshot.unlink(missing_ok=True)
        raise


def clear_undo(ws: Workspace, name: str) -> None:
    """Drop a document's snapshots -- after a reprocess or a decision the older
    versions are no longer meaningful history to step back through."""
    for snapshot in _undo_snapshots(ws, Path(name).name):
        with suppress(OSError):
            snapshot.unlink(missing_ok=True)


def undo_last_edit(pdf: Path, config_path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Restore the document to the state before the most recent edit (one step).
    Raises LookupError when there is nothing left to undo."""
    ws = workspace(config_path)
    snapshots = _undo_snapshots(ws, pdf.name)
    if not snapshots:
        raise LookupError("nothing to undo for this document")
    latest = snapshots[-1]
    tmp = pdf.with_name(pdf.name + ".undoing")
    shutil.copy2(latest, tmp)
    tmp.replace(pdf)
    with suppress(OSError):
        latest.unlink(missing_ok=True)
    result = _after_edit(ws, pdf, {"event": "edit-undo"})
    result["undo_depth"] = len(_undo_snapshots(ws, pdf.name))
    return result


def _after_edit(ws: Workspace, pdf: Path, event: dict[str, Any]) -> dict[str, Any]:
    """Shared tail for every in-app edit: re-score the changed document so the
    reviewer sees the effect immediately, persist that verdict, and record the
    edit in the audit trail. `output_sha256` is deliberately NOT updated -- the
    document now differs from what the pipeline produced ("edited since
    processing"), and the approval step is what re-pins the shipped bytes."""
    side_path = ws.sidecars / (pdf.name + ".sidecar.json")
    vera = verapdf.validate(pdf, ws.verapdf_profile, cli=ws.verapdf_cli)
    if side_path.is_file():
        sidecar = Sidecar.model_validate_json(side_path.read_text(encoding="utf-8"))
        sidecar.verapdf_passed = vera.passed
        sidecar.verapdf_failed_clauses = vera.failed_clauses
        side_path.write_text(sidecar.model_dump_json(), encoding="utf-8")
    append_event(
        ws.audit_log,
        {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "file": pdf.name,
            **event,
        },
    )
    return {
        "ok": True,
        "verapdf_passed": vera.passed,
        "failed_clauses": vera.failed_clauses,
        **event,
    }


def set_tag_type(
    pdf: Path, node_id: int, new_type: str, config_path: Path = DEFAULT_CONFIG_PATH
) -> dict[str, Any]:
    """Retag one element (e.g. a Paragraph the engine should have made a
    Heading 2). Raises ValueError for a type outside EDITABLE_TAG_TYPES."""
    if new_type not in EDITABLE_TAG_TYPES:
        raise ValueError(f"not an editable tag type: {new_type}")
    ws = workspace(config_path)

    def mutate(element, pikepdf) -> None:
        element.S = pikepdf.Name("/" + new_type)

    with _edit_guard(ws, pdf):
        old_type = structure.edit_element(pdf, node_id, mutate)
    return _after_edit(
        ws, pdf, {"event": "edit-tag", "node": node_id, "from": old_type, "to": new_type}
    )


def set_figure_alt(
    pdf: Path, node_id: int, alt: str, config_path: Path = DEFAULT_CONFIG_PATH
) -> dict[str, Any]:
    """Write the human-authored description (/Alt) onto one element -- the
    alt-text step that is never machine-generated. An empty string clears it."""
    ws = workspace(config_path)
    text = alt.strip()

    def mutate(element, pikepdf) -> None:
        if text:
            element.Alt = pikepdf.String(text)
        elif "/Alt" in element:
            del element.Alt

    with _edit_guard(ws, pdf):
        kind = structure.edit_element(pdf, node_id, mutate)
    return _after_edit(
        ws,
        pdf,
        {"event": "edit-alt", "node": node_id, "tag": kind, "described": bool(text)},
    )


def make_decorative(
    pdf: Path, node_id: int, config_path: Path = DEFAULT_CONFIG_PATH
) -> dict[str, Any]:
    """Mark one element as decoration: out of the reading order, its content
    re-marked as an artifact. This is the "decorative image needs no
    description" answer -- and why /Artifact is not in EDITABLE_TAG_TYPES, since
    it is a removal, not a retag."""
    ws = workspace(config_path)
    with _edit_guard(ws, pdf):
        result = structure.make_decorative(pdf, node_id)
    return _after_edit(
        ws,
        pdf,
        {
            "event": "edit-decorative",
            "node": node_id,
            "was": result["was"],
            "artifacts": result["artifacts"],
        },
    )


def move_tag(
    pdf: Path, node_id: int, delta: int, config_path: Path = DEFAULT_CONFIG_PATH
) -> dict[str, Any]:
    """Move one tag earlier/later among its siblings. Reading order is tree
    order, so this is the in-app reading-order fix."""
    if delta not in (-1, 1):
        raise ValueError("delta must be -1 (earlier) or +1 (later)")
    ws = workspace(config_path)
    with _edit_guard(ws, pdf):
        result = structure.move_element(pdf, node_id, delta)
    if not result["moved"]:
        # nothing changed: drop the snapshot so "undo" never becomes a no-op
        snapshots = _undo_snapshots(ws, pdf.name)
        if snapshots:
            with suppress(OSError):
                snapshots[-1].unlink(missing_ok=True)
        return {"ok": True, "moved": False, "note": "already at the end of its group"}
    return _after_edit(
        ws,
        pdf,
        {"event": "edit-order", "node": node_id, "direction": "earlier" if delta < 0 else "later"},
    )


def remove_all_tags(pdf: Path, config_path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Strip the entire tag structure (the start-over escape hatch). The
    document stays visually identical and can be reprocessed or tagged from
    scratch in Acrobat."""
    ws = workspace(config_path)
    with _edit_guard(ws, pdf):
        result = structure.remove_all_tags(pdf)
    return _after_edit(ws, pdf, {"event": "edit-remove-tags", "had_tags": result["had_tags"]})


def unwrap_tag(pdf: Path, node_id: int, config_path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Remove one tag but KEEP its contents in the reading order -- the fix for a
    wrapper the engine invented (paragraphs bundled into a bogus List). Raises
    ValueError when the element holds content directly, since deleting that tag
    would leave the content untagged (retag or mark decorative instead)."""
    ws = workspace(config_path)
    with _edit_guard(ws, pdf):
        result = structure.unwrap_element(pdf, node_id)
    return _after_edit(
        ws,
        pdf,
        {
            "event": "edit-unwrap",
            "node": node_id,
            "was": result["was"],
            "promoted": result["promoted"],
        },
    )


def reprocess(name: str, config_path: Path = DEFAULT_CONFIG_PATH) -> Sidecar:
    """Discard in-app/Acrobat edits by running the ORIGINAL inbox document
    through the pipeline again -- the undo for the whole editing session.
    Raises FileNotFoundError when the source is no longer in the inbox."""
    ws = workspace(config_path)
    src = ws.inbox / Path(name).name
    if not src.is_file():
        raise FileNotFoundError(f"original not in the inbox: {src.name}")
    # a decided copy would otherwise shadow the fresh draft in find_output()
    for folder in (ws.outbox / "approved", ws.outbox / "rejected"):
        with suppress(OSError):
            (folder / src.name).unlink(missing_ok=True)
    clear_undo(ws, src.name)  # the old step-by-step history no longer applies
    stages = [registry.get_stage(impl) for impl in ws.stages.values()]
    sidecar = runner.run(src, stages, ws.outbox, ws.audit_log, sidecar_dir=ws.sidecars)
    return _score_draft(ws, sidecar)


def audit_events(config_path: Path = DEFAULT_CONFIG_PATH, limit: int = 200) -> list[dict[str, Any]]:
    """The audit trail, newest first, capped at `limit` -- the History view.
    Read-only: the JSONL log itself stays append-only."""
    ws = workspace(config_path)
    events = read_events(ws.audit_log)
    return list(reversed(events[-limit:]))


def decide(
    pdf: Path,
    reviewer: str,
    approve: bool,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> Sidecar:
    """The human gate: record veraPDF's machine verdict, then the human's
    APPROVED/REJECTED decision, on the sidecar next to `pdf`.

    The machine gate is advisory (it fails closed to a flag when no veraPDF
    runner exists); the human decision is authoritative. Returns the updated
    sidecar, which is also persisted and audit-logged.
    """
    ws = workspace(config_path)
    if not pdf.is_file():
        # a decision needs bytes to pin -- fail clearly instead of recording a
        # verdict about a document that is not there.
        raise FileNotFoundError(f"PDF not found: {pdf}")
    side_path = ws.sidecars / (pdf.name + ".sidecar.json")
    if not side_path.is_file():
        raise FileNotFoundError(f"sidecar not found for this PDF: {side_path}")
    sidecar = Sidecar.model_validate_json(side_path.read_text(encoding="utf-8"))

    # the approval pins the bytes as they are NOW -- the reviewer may have
    # corrected the draft (e.g. in Acrobat) since the pipeline wrote it, so
    # re-fingerprint before recording the decision: approved == shipped.
    sidecar.output_sha256 = sha256_file(pdf)

    vera = verapdf.validate(pdf, ws.verapdf_profile, cli=ws.verapdf_cli)
    sidecar.verapdf_passed = vera.passed
    sidecar.verapdf_failed_clauses = vera.failed_clauses

    status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
    clear_undo(ws, pdf.name)  # the decision closes this editing session
    sidecar.approval = Approval(status=status, reviewer=reviewer, decided_at=datetime.now(UTC))
    side_path.write_text(sidecar.model_dump_json(indent=2), encoding="utf-8", newline="\n")

    # sort the decided document into its status subfolder, so the outbox itself
    # reads as a workflow: root = awaiting review, approved/ = ready to ship,
    # rejected/ = needs manual rework. Only files inside the outbox tree move --
    # a decision on a PDF elsewhere (CLI with an odd path) records but stays put.
    moved_to: str | None = None
    dest_dir = ws.outbox / ("approved" if approve else "rejected")
    outbox_tree = (ws.outbox, ws.outbox / "approved", ws.outbox / "rejected")
    if pdf.resolve().parent in outbox_tree and pdf.resolve().parent != dest_dir:
        dest_dir.mkdir(parents=True, exist_ok=True)
        pdf.resolve().replace(dest_dir / pdf.name)
        moved_to = f"outbox/{dest_dir.name}"

    append_event(
        ws.audit_log,
        {
            "event": "verify",
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "file": pdf.name,
            "reviewer": reviewer,
            "decision": status.value,
            "verapdf_passed": vera.passed,
            "output_sha256": sidecar.output_sha256,
            **({"moved_to": moved_to} if moved_to else {}),
        },
    )
    return sidecar
