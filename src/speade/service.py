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

import json
import os
import re
import shutil
import sys
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
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

# ---------------------------------------------------------------- module codes
# Each module gets its own folder inside inbox/outbox/sidecars, so Student
# Partners work per-module and same-named PDFs from different modules can never
# collide. A document's identity everywhere outside this file is its REL-ID:
# "MG2001/notes.pdf" (module document) or "notes.pdf" (legacy flat root).

_RESERVED_DIRS = {"approved", "rejected"}
# folder-name-safe, no dots (rules out "..", hidden dirs, and name confusion
# with *.pdf files); 32 chars is far beyond any real module code.
_MODULE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,31}")


def normalize_module(code: str) -> str:
    """Validate a typed module code -> the folder name used for it.
    Raises ValueError for anything that is not a safe single folder name."""
    code = str(code).strip()
    if not _MODULE_RE.fullmatch(code) or code.lower() in _RESERVED_DIRS:
        raise ValueError(f"not a valid module code: {code!r}")
    return code


def split_id(file: str) -> tuple[str | None, str]:
    """A rel-id -> (module | None, filename). The ONLY parser of client-supplied
    document ids: one optional validated module folder plus a bare filename,
    so no id can ever reach outside the workspace tree."""
    parts = PurePosixPath(str(file).replace("\\", "/")).parts
    if not parts:
        raise ValueError("empty document id")
    name = Path(parts[-1]).name
    if not name:
        raise ValueError(f"not a document id: {file!r}")
    if len(parts) == 1:
        return None, name
    if len(parts) == 2:
        return normalize_module(parts[0]), name
    raise ValueError(f"not a document id: {file!r}")


def make_id(module: str | None, name: str) -> str:
    """(module, filename) -> the rel-id clients hold."""
    return f"{module}/{name}" if module else name


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

    file: str  # the rel-id ("MG2001/notes.pdf", or "notes.pdf" for the flat root)
    module: str | None = None  # module folder, None for the legacy flat root
    name: str = ""  # bare filename, what the UI displays
    # when this document's record last changed (sidecar mtime): processing,
    # an edit, or a decision -- the queue sorts newest-first on it.
    updated_at: float = 0.0
    route: str
    stages_applied: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    verapdf_passed: bool | None = None
    verapdf_failed_clauses: list[str] = Field(default_factory=list)
    # the document has been edited since that verdict was measured (the
    # reviewer has the automatic check switched off) -- the UI must not
    # present the old verdict as current.
    verapdf_stale: bool = False
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


def _module_base(ws: Workspace, module: str | None) -> Path:
    """The outbox subtree a module's documents live in (the root when None)."""
    return ws.outbox / module if module else ws.outbox


def _side_dir(ws: Workspace, module: str | None) -> Path:
    """Where a module's sidecar records live (the flat dir when None)."""
    return ws.sidecars / module if module else ws.sidecars


def _side_path(ws: Workspace, module: str | None, name: str) -> Path:
    return _side_dir(ws, module) / (name + ".sidecar.json")


def _module_of(ws: Workspace, pdf: Path) -> str | None:
    """Which module folder an inbox/outbox path belongs to (None = flat root,
    or a path outside the workspace, e.g. a CLI run on an arbitrary file)."""
    resolved = pdf.resolve()
    for root in (ws.outbox.resolve(), ws.inbox.resolve()):
        try:
            parts = resolved.relative_to(root).parts
        except ValueError:
            continue
        if len(parts) >= 2 and parts[0] not in _RESERVED_DIRS:
            try:
                return normalize_module(parts[0])
            except ValueError:  # a hand-made folder no module code could name
                return None
        return None
    return None


def list_modules(config_path: Path = DEFAULT_CONFIG_PATH) -> list[str]:
    """Every module folder that exists in the workspace (inbox or outbox) --
    the suggestion list behind the UI's module-code box."""
    ws = workspace(config_path)
    found: set[str] = set()
    for base in (ws.inbox, ws.outbox, ws.sidecars):
        for child in base.iterdir() if base.is_dir() else ():
            if child.is_dir() and child.name not in _RESERVED_DIRS:
                with suppress(ValueError):
                    found.add(normalize_module(child.name))
    return sorted(found)


def _find_output(ws: Workspace, module: str | None, name: str) -> Path | None:
    """Locate an output PDF within its module tree: module root first (a fresh
    draft shadows any older decided copy), then approved/ and rejected/."""
    base = _module_base(ws, module)
    for folder in (base, base / "approved", base / "rejected"):
        candidate = folder / name
        if candidate.is_file():
            return candidate
    return None


def find_output(file: str, config_path: Path = DEFAULT_CONFIG_PATH) -> Path | None:
    """Public wrapper for clients that hold only a rel-id (the bridge, the web
    app): where in the outbox tree does this document currently live?"""
    module, name = split_id(file)
    return _find_output(workspace(config_path), module, name)


def sidecar_path_for(pdf: Path, config_path: Path = DEFAULT_CONFIG_PATH) -> Path:
    """The sidecar record path for an output PDF (module-aware) -- for clients
    that report where the record lives (the CLI's verify echo)."""
    ws = workspace(config_path)
    return _side_path(ws, _module_of(ws, pdf), pdf.name)


def _score_draft(ws: Workspace, sidecar: Sidecar, module: str | None = None) -> Sidecar:
    """Run the veraPDF gate on a freshly written draft and persist the verdict,
    so the review queue shows machine feedback IMMEDIATELY after processing --
    the reviewer must not need Acrobat just to learn whether tagging worked.
    Advisory only: decide() re-runs veraPDF at sign-off on the bytes being
    approved, which stays the authoritative check."""
    name = Path(sidecar.source_path).name
    out_pdf = _module_base(ws, module) / name
    vera = verapdf.validate(out_pdf, ws.verapdf_profile, cli=ws.verapdf_cli)
    sidecar.verapdf_passed = vera.passed
    sidecar.verapdf_failed_clauses = vera.failed_clauses
    sidecar.verapdf_stale = False  # freshly measured on the bytes just written
    sidecar.verapdf_sha256 = sha256_file(out_pdf)
    _side_path(ws, module, name).write_text(sidecar.model_dump_json(), encoding="utf-8")
    return sidecar


def _run_dirs(ws: Workspace, module: str | None) -> tuple[Path, Path]:
    """(outbox dir, sidecar dir) a run for `module` writes into, created."""
    out_dir, side_dir = _module_base(ws, module), _side_dir(ws, module)
    out_dir.mkdir(parents=True, exist_ok=True)
    side_dir.mkdir(parents=True, exist_ok=True)
    return out_dir, side_dir


def run_one(
    pdf: Path, config_path: Path = DEFAULT_CONFIG_PATH, module: str | None = None
) -> Sidecar:
    """Run one PDF through the configured pipeline into the outbox (the given
    module's folder, or the flat root), then score the draft with veraPDF
    (advisory; see _score_draft).

    Raises KeyError for an unknown stage implementation and lets pipeline
    failures propagate -- single-file callers want the real error.
    """
    ws = workspace(config_path)
    stages = [registry.get_stage(impl) for impl in ws.stages.values()]
    if module is None:
        # CLI parity: `speade run inbox/MG2001/x.pdf` must land in MG2001's
        # outbox, not fork a second flat-root identity for the same document.
        module = _module_of(ws, pdf)
    out_dir, side_dir = _run_dirs(ws, module)
    sidecar = runner.run(
        pdf,
        stages,
        out_dir,
        ws.audit_log,
        sidecar_dir=side_dir,
        audit_extra={"module": module} if module else None,
    )
    return _score_draft(ws, sidecar, module)


def _needs_processing(ws: Workspace, pdf: Path, module: str | None = None) -> bool:
    """Does this inbox file still need a run? False only when a sidecar exists
    whose recorded source fingerprint matches the file's current bytes AND its
    output still exists somewhere in the outbox tree -- i.e. this exact document
    was already processed. A replaced/edited source (new hash) processes again."""
    side_path = _side_path(ws, module, pdf.name)
    if not side_path.is_file():
        return True
    try:
        sidecar = Sidecar.model_validate_json(side_path.read_text(encoding="utf-8"))
    except Exception:  # a mangled record: reprocess rather than trust it
        return True
    if sidecar.source_sha256 != sha256_file(pdf):
        return True
    return _find_output(ws, module, pdf.name) is None


def _inbox_sweep(ws: Workspace, module: str | None) -> list[tuple[Path, str | None]]:
    """(pdf, module) pairs a batch would consider: one module's folder when
    given, otherwise the flat root plus EVERY module folder (CLI parity)."""
    if module is not None:
        folder = ws.inbox / module
        return [(p, module) for p in sorted(folder.glob("*.pdf")) if p.is_file()]
    pairs = [(p, None) for p in sorted(ws.inbox.glob("*.pdf")) if p.is_file()]
    for child in sorted(ws.inbox.iterdir()):
        if child.is_dir() and child.name not in _RESERVED_DIRS:
            with suppress(ValueError):
                code = normalize_module(child.name)
                pairs += [(p, code) for p in sorted(child.glob("*.pdf")) if p.is_file()]
    return pairs


def list_pending(config_path: Path = DEFAULT_CONFIG_PATH) -> list[str]:
    """Inbox files still waiting to be processed (the sidebar's 'Waiting to
    process' entries), as rel-ids across the flat root and every module."""
    ws = workspace(config_path)
    return [
        make_id(module, pdf.name)
        for pdf, module in _inbox_sweep(ws, None)
        if _needs_processing(ws, pdf, module)
    ]


def run_batch(
    folder: Path | None = None,
    config_path: Path = DEFAULT_CONFIG_PATH,
    progress: Callable[[int, int, str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    reprocess: bool = False,
    module: str | None = None,
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
    items processed so far are returned as normal.

    `module`, when given, restricts the sweep to that module's inbox folder --
    the "students only process their own module's batch" rule. Without it (and
    without an explicit `folder`) the flat root AND every module folder are
    swept, each document landing in its own module's outbox."""
    ws = workspace(config_path)
    stages = [registry.get_stage(impl) for impl in ws.stages.values()]  # fail fast on config

    if folder is not None:
        # an explicit folder is a CLI affair. Pointing it AT a module's inbox
        # folder must not fork those documents into the flat root -- infer the
        # module from the folder's place in the workspace (None if outside).
        folder_module = _module_of(ws, Path(folder) / "_probe.pdf")
        pairs = [(p, folder_module) for p in sorted(Path(folder).glob("*.pdf")) if p.is_file()]
    else:
        pairs = _inbox_sweep(ws, normalize_module(module) if module else None)
    todo = [(p, m) for p, m in pairs if reprocess or _needs_processing(ws, p, m)]
    items: list[BatchItem] = [
        BatchItem(file=make_id(m, p.name), ok=True, skipped=True)
        for p, m in pairs
        if (p, m) not in todo
    ]
    done = 0
    for pdf, mod in todo:
        if cancel is not None and cancel():
            break
        if progress is not None:
            progress(done, len(todo), make_id(mod, pdf.name))
        try:
            out_dir, side_dir = _run_dirs(ws, mod)
            sidecar = runner.run(
                pdf,
                stages,
                out_dir,
                ws.audit_log,
                sidecar_dir=side_dir,
                audit_extra={"module": mod} if mod else None,
            )
            sidecar = _score_draft(ws, sidecar, mod)
            items.append(BatchItem(file=make_id(mod, pdf.name), ok=True, sidecar=sidecar))
        except Exception as exc:  # per-file isolation: record, continue the sweep
            items.append(
                BatchItem(
                    file=make_id(mod, pdf.name),
                    ok=False,
                    error=f"{type(exc).__name__}: {str(exc)[:200]}",
                )
            )
        done += 1
    if progress is not None:
        progress(done, len(todo), "")
    return items


def list_queue(config_path: Path = DEFAULT_CONFIG_PATH) -> list[QueueItem]:
    """Summarise every outbox draft (its sidecar) for a review-queue listing,
    across the flat root and every module folder."""
    ws = workspace(config_path)
    modules: list[str | None] = [None]
    if ws.sidecars.is_dir():
        for child in sorted(ws.sidecars.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                with suppress(ValueError):
                    modules.append(normalize_module(child.name))
    items: list[QueueItem] = []
    for module in modules:
        for side_path in sorted(_side_dir(ws, module).glob("*.pdf.sidecar.json")):
            pdf_name = side_path.name.removesuffix(".sidecar.json")
            rel_id = make_id(module, pdf_name)
            updated_at = side_path.stat().st_mtime
            try:
                sidecar = Sidecar.model_validate_json(side_path.read_text(encoding="utf-8"))
            except Exception:  # a mangled sidecar must not hide the rest of the queue
                items.append(
                    QueueItem(
                        file=rel_id,
                        module=module,
                        name=pdf_name,
                        updated_at=updated_at,
                        route="unknown",
                        status="invalid-sidecar",
                    )
                )
                continue
            # compare the bytes on disk with the recorded fingerprint, so the UI
            # can say "edited since processing" (an Acrobat fix) in plain language.
            out_pdf = _find_output(ws, module, pdf_name)
            changed: bool | None = None
            if out_pdf is not None and sidecar.output_sha256:
                changed = sha256_file(out_pdf) != sidecar.output_sha256
            items.append(
                QueueItem(
                    file=rel_id,
                    module=module,
                    name=pdf_name,
                    updated_at=updated_at,
                    route=sidecar.route.value,
                    stages_applied=sidecar.stages_applied,
                    flags=sidecar.flags,
                    verapdf_passed=sidecar.verapdf_passed,
                    verapdf_failed_clauses=sidecar.verapdf_failed_clauses,
                    verapdf_stale=sidecar.verapdf_stale,
                    status=sidecar.approval.status.value,
                    reviewer=sidecar.approval.reviewer,
                    output_changed=changed,
                    ocr_layered=sidecar.ocr_layered,
                    undo_depth=len(_undo_snapshots(ws, module, pdf_name)),
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
    tmp = pdf.with_name(f"{pdf.name}.{os.getpid()}.meta.tmp")
    with _EDIT_LOCK:  # another whole-file read-modify-write: never concurrent
        with pikepdf.open(pdf) as doc:
            if lang:
                doc.Root.Lang = pikepdf.String(lang)
            if doc.Root.get("/ViewerPreferences") is None:
                doc.Root.ViewerPreferences = pikepdf.Dictionary()
            doc.Root.ViewerPreferences.DisplayDocTitle = True  # 7.1-10: title, not filename
            if title:
                with doc.open_metadata() as meta:
                    meta["dc:title"] = title
            doc.save(tmp)
        tmp.replace(pdf)

    new_sha = sha256_file(pdf)
    module = _module_of(ws, pdf)
    side_path = _side_path(ws, module, pdf.name)
    if side_path.is_file():
        try:
            sidecar = Sidecar.model_validate_json(side_path.read_text(encoding="utf-8"))
            sidecar.output_sha256 = new_sha
            # title and language are themselves UA-1 clauses (7.1-9, 7.2): the
            # bytes just changed, so the recorded verdict no longer describes
            # this file. This step never ran veraPDF; now it says so.
            sidecar.verapdf_stale = True
            side_path.write_text(sidecar.model_dump_json(indent=2), encoding="utf-8", newline="\n")
        except Exception:  # a mangled sidecar: the metadata edit itself still stands
            pass

    append_event(
        ws.audit_log,
        {
            "event": "edit-metadata",
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "file": pdf.name,
            **({"module": module} if module else {}),
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


def _undo_dir(ws: Workspace, module: str | None) -> Path:
    """Where per-document edit snapshots live (inside the hidden sidecar area,
    so reviewers browsing data/ are not tempted to poke at them). Per module,
    so two modules' same-named documents keep separate histories."""
    return _side_dir(ws, module) / ".undo"


def _undo_snapshots(ws: Workspace, module: str | None, name: str) -> list[Path]:
    """This document's snapshots, oldest first."""
    folder = _undo_dir(ws, module)
    if not folder.is_dir():
        return []
    return sorted(folder.glob(f"{name}.*.bak"), key=lambda p: int(p.suffixes[-2].lstrip(".")))


def undo_depth(file: str, config_path: Path = DEFAULT_CONFIG_PATH) -> int:
    """How many single-step undos are available for this document (rel-id)."""
    module, name = split_id(file)
    return len(_undo_snapshots(workspace(config_path), module, name))


def _snapshot(ws: Workspace, pdf: Path) -> Path | None:
    """Copy the document aside so the edit about to happen can be undone one
    step. Returns the snapshot path, or None if it could not be written --
    failure to snapshot must not block the edit itself."""
    module = _module_of(ws, pdf)
    folder = _undo_dir(ws, module)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        existing = _undo_snapshots(ws, module, pdf.name)
        for stale in existing[: max(0, len(existing) + 1 - _UNDO_DEPTH)]:
            stale.unlink(missing_ok=True)
        nxt = 1 + max((int(p.suffixes[-2].lstrip(".")) for p in existing), default=0)
        target = folder / f"{pdf.name}.{nxt}.bak"
        shutil.copy2(pdf, target)
        return target
    except OSError:
        return None


# Every in-app edit is a read-modify-write of the whole PDF (open with pikepdf,
# mutate, save to a staging file, atomic replace). Two of them running at once
# LOSE ONE: the second opened the pre-first bytes, so its save silently drops
# the first edit -- while the audit log, already appended, claims both landed.
# Both clients dispatch concurrently (pywebview runs each bridge call on its own
# thread; every web endpoint is a sync def, so Starlette uses its threadpool),
# and a drag now applies on release, so a second gesture during a save is easy.
# One process-wide lock serialises the mutation window; it is held for well
# under a second and never around veraPDF.
_EDIT_LOCK = threading.RLock()


@contextmanager
def _edit_guard(ws: Workspace, pdf: Path):
    """Serialise the edit, snapshot for undo, and DISCARD that snapshot if the
    edit fails -- a refused edit must not leave a no-op step in the history."""
    with _EDIT_LOCK:
        snapshot = _snapshot(ws, pdf)
        try:
            yield
        except BaseException:
            if snapshot is not None:
                with suppress(OSError):
                    snapshot.unlink(missing_ok=True)
            raise


def clear_undo(ws: Workspace, module: str | None, name: str) -> None:
    """Drop a document's snapshots -- after a reprocess or a decision the older
    versions are no longer meaningful history to step back through."""
    for snapshot in _undo_snapshots(ws, module, Path(name).name):
        with suppress(OSError):
            snapshot.unlink(missing_ok=True)


def undo_last_edit(
    pdf: Path, config_path: Path = DEFAULT_CONFIG_PATH, check: bool = True
) -> dict[str, Any]:
    """Restore the document to the state before the most recent edit (one step).
    Raises LookupError when there is nothing left to undo."""
    ws = workspace(config_path)
    module = _module_of(ws, pdf)
    with _EDIT_LOCK:  # same read-modify-write window as any other edit
        snapshots = _undo_snapshots(ws, module, pdf.name)
        if not snapshots:
            raise LookupError("nothing to undo for this document")
        latest = snapshots[-1]
        tmp = pdf.with_name(f"{pdf.name}.{os.getpid()}.undoing")
        shutil.copy2(latest, tmp)
        tmp.replace(pdf)
        with suppress(OSError):
            latest.unlink(missing_ok=True)
    result = _after_edit(ws, pdf, {"event": "edit-undo"}, check=check)
    result["undo_depth"] = len(_undo_snapshots(ws, module, pdf.name))
    return result


def _after_edit(
    ws: Workspace, pdf: Path, event: dict[str, Any], check: bool = True
) -> dict[str, Any]:
    """Shared tail for every in-app edit: record it in the audit trail and --
    when `check` is on -- re-score the changed document so the reviewer sees
    the effect immediately. `output_sha256` is deliberately NOT updated: the
    document now differs from what the pipeline produced ("edited since
    processing"), and the approval step is what re-pins the shipped bytes.

    veraPDF is a Java subprocess costing seconds per run, which is punishing
    when a reviewer writes twenty alt texts in a row. With `check` off the
    edit still lands (and is still audited and undoable); only the scoring is
    deferred to check_now() or to the gate, and the sidecar is marked stale so
    no UI can show a verdict that predates the edit.
    """
    module = _module_of(ws, pdf)
    side_path = _side_path(ws, module, pdf.name)
    vera = verapdf.validate(pdf, ws.verapdf_profile, cli=ws.verapdf_cli) if check else None
    if side_path.is_file():
        sidecar = Sidecar.model_validate_json(side_path.read_text(encoding="utf-8"))
        if vera is not None:
            sidecar.verapdf_passed = vera.passed
            sidecar.verapdf_failed_clauses = vera.failed_clauses
            sidecar.verapdf_stale = False
            sidecar.verapdf_sha256 = sha256_file(pdf)
        else:
            sidecar.verapdf_stale = True
        side_path.write_text(sidecar.model_dump_json(), encoding="utf-8")
    append_event(
        ws.audit_log,
        {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "file": pdf.name,
            **({"module": module} if module else {}),
            **event,
        },
    )
    return {
        "ok": True,
        "verapdf_passed": vera.passed if vera is not None else None,
        "failed_clauses": vera.failed_clauses if vera is not None else [],
        "checked": vera is not None,
        **event,
    }


def check_now(pdf: Path, config_path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Run the veraPDF gate on demand and persist the verdict -- the button
    behind a deferred (auto-check off) editing session. Read-only with respect
    to the document itself: it measures, it never edits."""
    ws = workspace(config_path)
    module = _module_of(ws, pdf)
    # veraPDF takes seconds and the UI stays live, so the reviewer can edit
    # WHILE it runs. Fingerprint the bytes being measured: if they changed by
    # the time the verdict lands, it describes a file that no longer exists --
    # record it but leave the document marked stale rather than resurrecting a
    # verdict for superseded bytes.
    measured = sha256_file(pdf)
    vera = verapdf.validate(pdf, ws.verapdf_profile, cli=ws.verapdf_cli)
    superseded = sha256_file(pdf) != measured
    side_path = _side_path(ws, module, pdf.name)
    if side_path.is_file():
        sidecar = Sidecar.model_validate_json(side_path.read_text(encoding="utf-8"))
        sidecar.verapdf_passed = vera.passed
        sidecar.verapdf_failed_clauses = vera.failed_clauses
        sidecar.verapdf_stale = superseded
        sidecar.verapdf_sha256 = measured
        side_path.write_text(sidecar.model_dump_json(), encoding="utf-8")
    return {
        "ok": True,
        "checked": True,
        "verapdf_passed": vera.passed,
        "failed_clauses": vera.failed_clauses,
        "superseded": superseded,
    }


def set_tag_type(
    pdf: Path,
    node_id: int,
    new_type: str,
    config_path: Path = DEFAULT_CONFIG_PATH,
    check: bool = True,
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
        ws,
        pdf,
        {"event": "edit-tag", "node": node_id, "from": old_type, "to": new_type},
        check=check,
    )


def set_figure_alt(
    pdf: Path,
    node_id: int,
    alt: str,
    config_path: Path = DEFAULT_CONFIG_PATH,
    check: bool = True,
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
        check=check,
    )


def make_decorative(
    pdf: Path, node_id: int, config_path: Path = DEFAULT_CONFIG_PATH, check: bool = True
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
        check=check,
    )


def move_tag(
    pdf: Path,
    node_id: int,
    delta: int,
    config_path: Path = DEFAULT_CONFIG_PATH,
    check: bool = True,
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
        snapshots = _undo_snapshots(ws, _module_of(ws, pdf), pdf.name)
        if snapshots:
            with suppress(OSError):
                snapshots[-1].unlink(missing_ok=True)
        return {"ok": True, "moved": False, "note": "already at the end of its group"}
    return _after_edit(
        ws,
        pdf,
        {"event": "edit-order", "node": node_id, "direction": "earlier" if delta < 0 else "later"},
        check=check,
    )


def bulk_edit(
    pdf: Path,
    action: str,
    node_ids: list[int],
    new_type: str | None = None,
    config_path: Path = DEFAULT_CONFIG_PATH,
    check: bool = True,
) -> dict[str, Any]:
    """One drag-selection's worth of edits as a SINGLE operation: one undo
    snapshot, one atomic save, one veraPDF re-check, one audit event.

    Actions: 'merge' (the selected tags become one tag of `new_type`, contents
    in reading order), 'retag' (every selected tag becomes `new_type`),
    'decorative' (every selected tag leaves the reading order). Raises
    ValueError for a bad action/type, LookupError when ids do not resolve.
    """
    ids = sorted({int(n) for n in node_ids})
    if not ids:
        raise ValueError("nothing selected")
    if action in ("merge", "retag"):
        if new_type not in EDITABLE_TAG_TYPES:
            raise ValueError(f"not an editable tag type: {new_type}")
    elif action != "decorative":
        raise ValueError(f"unknown bulk action: {action}")

    ws = workspace(config_path)
    with _edit_guard(ws, pdf):
        if action == "merge":
            result = structure.merge_elements(pdf, ids, new_type)
        elif action == "retag":
            result = structure.retag_elements(pdf, ids, new_type)
        else:
            result = structure.make_decorative_many(pdf, ids)
    out = _after_edit(
        ws,
        pdf,
        {"event": "edit-bulk", "action": action, "count": len(ids), "type": new_type},
        check=check,
    )
    out.update(result)
    return out


def carve_tag(
    pdf: Path,
    picks: list[dict],
    new_type: str,
    config_path: Path = DEFAULT_CONFIG_PATH,
    check: bool = True,
) -> dict[str, Any]:
    """Carve selected LINES out of their tags into one new tag of `new_type`
    (each pick = {"node_id", "mcid"}) -- the drag tool's finer grain, for a
    heading the engine swallowed into a paragraph. One snapshot, one save, one
    veraPDF re-check, one audit event, one undo step."""
    if not picks:
        raise ValueError("nothing selected")
    if new_type not in EDITABLE_TAG_TYPES:
        raise ValueError(f"not an editable tag type: {new_type}")
    ws = workspace(config_path)
    with _edit_guard(ws, pdf):
        result = structure.carve_lines(pdf, picks, new_type)
    out = _after_edit(
        ws,
        pdf,
        {"event": "edit-carve", "lines": result["carved"], "type": new_type},
        check=check,
    )
    out.update(result)
    return out


def tag_untagged(
    pdf: Path,
    page: int,
    object_indexes: list[int],
    new_type: str,
    config_path: Path = DEFAULT_CONFIG_PATH,
    check: bool = True,
) -> dict[str, Any]:
    """Wrap untagged drawn content (engine-missed text, or content previously
    marked decorative) in one brand-new tag of `new_type`. One snapshot, one
    save, one veraPDF re-check, one audit event, one undo step."""
    if new_type not in EDITABLE_TAG_TYPES:
        raise ValueError(f"not an editable tag type: {new_type}")
    ws = workspace(config_path)
    with _edit_guard(ws, pdf):
        result = structure.tag_objects(pdf, int(page), object_indexes, new_type)
    out = _after_edit(
        ws,
        pdf,
        {"event": "edit-tag-new", "pieces": result["tagged"], "type": new_type},
        check=check,
    )
    out.update(result)
    return out


def remove_all_tags(
    pdf: Path, config_path: Path = DEFAULT_CONFIG_PATH, check: bool = True
) -> dict[str, Any]:
    """Strip the entire tag structure (the start-over escape hatch). The
    document stays visually identical and can be reprocessed or tagged from
    scratch in Acrobat."""
    ws = workspace(config_path)
    with _edit_guard(ws, pdf):
        result = structure.remove_all_tags(pdf)
    return _after_edit(
        ws, pdf, {"event": "edit-remove-tags", "had_tags": result["had_tags"]}, check=check
    )


def unwrap_tag(
    pdf: Path, node_id: int, config_path: Path = DEFAULT_CONFIG_PATH, check: bool = True
) -> dict[str, Any]:
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
        check=check,
    )


def reprocess(file: str, config_path: Path = DEFAULT_CONFIG_PATH) -> Sidecar:
    """Discard in-app/Acrobat edits by running the ORIGINAL inbox document
    through the pipeline again -- the undo for the whole editing session.
    Takes a rel-id; raises FileNotFoundError when the source is no longer in
    its inbox folder."""
    ws = workspace(config_path)
    module, name = split_id(file)
    src = (ws.inbox / module if module else ws.inbox) / name
    if not src.is_file():
        raise FileNotFoundError(f"original not in the inbox: {src.name}")
    # a decided copy would otherwise shadow the fresh draft in find_output()
    base = _module_base(ws, module)
    for folder in (base / "approved", base / "rejected"):
        with suppress(OSError):
            (folder / src.name).unlink(missing_ok=True)
    clear_undo(ws, module, src.name)  # the old step-by-step history no longer applies
    stages = [registry.get_stage(impl) for impl in ws.stages.values()]
    out_dir, side_dir = _run_dirs(ws, module)
    sidecar = runner.run(
        src,
        stages,
        out_dir,
        ws.audit_log,
        sidecar_dir=side_dir,
        audit_extra={"module": module} if module else None,
    )
    return _score_draft(ws, sidecar, module)


def audit_events(config_path: Path = DEFAULT_CONFIG_PATH, limit: int = 200) -> list[dict[str, Any]]:
    """The audit trail, newest first, capped at `limit` -- the History view.
    Read-only: the JSONL log itself stays append-only."""
    ws = workspace(config_path)
    events = read_events(ws.audit_log)
    return list(reversed(events[-limit:]))


# CSV columns every export carries; anything an event records beyond these
# lands intact in the final "details" column, so no information is dropped.
_EXPORT_COLUMNS = (
    "time",
    "event",
    "module",
    "document",
    "reviewer",
    "decision",
    "verapdf_passed",
    "source_sha256",
    "output_sha256",
)
_EXPORT_KEYS = {"ts": "time", "file": "document"}  # log field -> column name


def export_history(config_path: Path = DEFAULT_CONFIG_PATH) -> Path:
    """Write the FULL audit trail as a timestamped CSV into the outbox -- the
    visible shelf -- so an admin can collect machine histories without touching
    the hidden audit folder. Excel-friendly (UTF-8 BOM); chronological order so
    exports from several machines merge by simple concatenation. Read-only with
    respect to the log itself."""
    import csv

    ws = workspace(config_path)
    events = read_events(ws.audit_log)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = ws.outbox / f"speade-history-{stamp}.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([*_EXPORT_COLUMNS, "details"])
        for event in events:
            row = {_EXPORT_KEYS.get(k, k): v for k, v in event.items()}
            rest = {k: v for k, v in row.items() if k not in _EXPORT_COLUMNS}
            writer.writerow(
                [row.get(column, "") for column in _EXPORT_COLUMNS]
                + [json.dumps(rest) if rest else ""]
            )
    return out


def _replace_with_retry(src: Path, dest: Path, attempts: int = 5) -> None:
    """`os.replace` with a short backoff for Windows sharing violations: a
    preview render or an Acrobat window holds a native read handle on the draft
    for a beat, and the atomic move surfaces that as PermissionError (WinError
    5/32). Transient in practice -- retry briefly before giving up."""
    delay = 0.1
    for attempt in range(attempts):
        try:
            src.replace(dest)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay *= 2


def decide(
    pdf: Path,
    reviewer: str,
    approve: bool,
    config_path: Path = DEFAULT_CONFIG_PATH,
    check: bool = True,
) -> Sidecar:
    """The human gate: record veraPDF's machine verdict, then the human's
    APPROVED/REJECTED decision, on the sidecar next to `pdf`.

    The machine gate is advisory (it fails closed to a flag when no veraPDF
    runner exists); the human decision is authoritative. Returns the updated
    sidecar, which is also persisted and audit-logged.

    `check` controls the re-check at decision time, which costs seconds of Java
    on every single approval. Either way the trail stays honest, because the
    verdict is stored with the fingerprint of the bytes it describes:

      * the recorded verdict already describes THESE bytes -> reused, no run
        (a re-run is deterministic, so it would only cost time);
      * otherwise with `check` -> veraPDF runs and the verdict is current;
      * otherwise -> the old verdict is kept, still marked stale, and the audit
        line records `verapdf_current: false`. Nothing is ever presented as a
        verdict on the approved bytes when it is not one.
    """
    ws = workspace(config_path)
    if not pdf.is_file():
        # a decision needs bytes to pin -- fail clearly instead of recording a
        # verdict about a document that is not there.
        raise FileNotFoundError(f"PDF not found: {pdf}")
    module = _module_of(ws, pdf)
    side_path = _side_path(ws, module, pdf.name)
    if not side_path.is_file():
        raise FileNotFoundError(f"sidecar not found for this PDF: {side_path}")
    sidecar = Sidecar.model_validate_json(side_path.read_text(encoding="utf-8"))

    # the approval pins the bytes as they are NOW -- the reviewer may have
    # corrected the draft (e.g. in Acrobat) since the pipeline wrote it, so
    # re-fingerprint before recording the decision: approved == shipped.
    sidecar.output_sha256 = sha256_file(pdf)

    if check:
        # asked for a measurement: take one, never infer it from bookkeeping
        vera = verapdf.validate(pdf, ws.verapdf_profile, cli=ws.verapdf_cli)
        sidecar.verapdf_passed = vera.passed
        sidecar.verapdf_failed_clauses = vera.failed_clauses
        sidecar.verapdf_sha256 = sidecar.output_sha256
        verdict_is_current = True
    else:
        # no run: the verdict on record still stands IF it was measured on
        # exactly these bytes (nothing changed since), otherwise it is openly
        # recorded as predating this version of the document.
        verdict_is_current = (
            sidecar.verapdf_sha256 is not None and sidecar.verapdf_sha256 == sidecar.output_sha256
        )
    sidecar.verapdf_stale = not verdict_is_current

    status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
    clear_undo(ws, module, pdf.name)  # the decision closes this editing session
    sidecar.approval = Approval(status=status, reviewer=reviewer, decided_at=datetime.now(UTC))

    # sort the decided document into its status subfolder FIRST, so the audit
    # line can record where the bytes actually ended up: each module's outbox
    # folder reads as a workflow (root = awaiting review, approved/ = ready to
    # ship, rejected/ = needs manual rework). Only files inside the outbox tree
    # move -- a decision on a PDF elsewhere (CLI, odd path) records but stays put.
    # A move the retries cannot land (the draft open in Acrobat, a render mid-
    # flight) does NOT void the decision: it is recorded, the file stays put,
    # and a sidecar flag tells the reviewer/admin to move it.
    moved_to: str | None = None
    move_failed = False
    # this decision supersedes any earlier failed move: drop the stale flag so
    # a successful re-decide (Acrobat closed) does not read as still stuck.
    if "decide-move-failed-file-in-use" in sidecar.flags:
        sidecar.flags.remove("decide-move-failed-file-in-use")
    base = _module_base(ws, module)
    dest_dir = base / ("approved" if approve else "rejected")
    outbox_tree = (base, base / "approved", base / "rejected")
    if pdf.resolve().parent in outbox_tree and pdf.resolve().parent != dest_dir:
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            _replace_with_retry(pdf.resolve(), dest_dir / pdf.name)
            moved_to = f"outbox/{module}/{dest_dir.name}" if module else f"outbox/{dest_dir.name}"
        except PermissionError:
            move_failed = True
            sidecar.flags.append("decide-move-failed-file-in-use")

    # trust trail BEFORE state: the audit line lands before the sidecar claims
    # the new status, so no document can ever read APPROVED/REJECTED without
    # its audit event. The inverse failure (audit written, sidecar write dies)
    # just resurfaces the document for a repeat decision -- a duplicate line in
    # an append-only log, never a missing one.
    append_event(
        ws.audit_log,
        {
            "event": "verify",
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "file": pdf.name,
            **({"module": module} if module else {}),
            "reviewer": reviewer,
            "decision": status.value,
            "verapdf_passed": sidecar.verapdf_passed,
            # False = the verdict above predates this version of the document
            # (the reviewer has the decision-time check switched off). The
            # decision still stands; the trail simply does not overstate it.
            "verapdf_current": verdict_is_current,
            "output_sha256": sidecar.output_sha256,
            **({"moved_to": moved_to} if moved_to else {}),
            **({"move_failed": True} if move_failed else {}),
        },
    )
    side_path.write_text(sidecar.model_dump_json(indent=2), encoding="utf-8", newline="\n")
    return sidecar
