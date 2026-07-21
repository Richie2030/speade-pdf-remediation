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

import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from speade.audit.log import append_event, read_events, sha256_file
from speade.config import load_config
from speade.pipeline import registry, runner
from speade.pipeline.contract import Approval, ApprovalStatus, Sidecar
from speade.validation import verapdf
from speade.validation.structure import StructureSummary, summarize

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


def run_batch(
    folder: Path | None = None,
    config_path: Path = DEFAULT_CONFIG_PATH,
    progress: Callable[[int, int, str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> list[BatchItem]:
    """Sweep every *.pdf in `folder` (default: the configured inbox) through the
    pipeline, scoring each draft with veraPDF (see _score_draft). Per-file
    failures are captured as BatchItem.error so the rest of the batch still
    runs; a misconfigured pipeline (unknown stage) raises.

    `progress`, when given, is called as (done, total, current_filename) before
    each file and once as (processed, total, "") at the end -- the desktop
    client's per-file progress bar polls the state a callback like this maintains.

    `cancel`, when given, is polled BETWEEN documents (the Stop button): the
    file in flight always finishes, so nothing is left half-written, and the
    items processed so far are returned as normal."""
    ws = workspace(config_path)
    src_dir = Path(folder) if folder is not None else ws.inbox
    stages = [registry.get_stage(impl) for impl in ws.stages.values()]  # fail fast on config

    pdfs = sorted(p for p in src_dir.glob("*.pdf") if p.is_file())
    items: list[BatchItem] = []
    for done, pdf in enumerate(pdfs):
        if cancel is not None and cancel():
            break
        if progress is not None:
            progress(done, len(pdfs), pdf.name)
        try:
            sidecar = runner.run(pdf, stages, ws.outbox, ws.audit_log, sidecar_dir=ws.sidecars)
            sidecar = _score_draft(ws, sidecar)
            items.append(BatchItem(file=pdf.name, ok=True, sidecar=sidecar))
        except Exception as exc:  # per-file isolation: record, continue the sweep
            items.append(
                BatchItem(file=pdf.name, ok=False, error=f"{type(exc).__name__}: {str(exc)[:200]}")
            )
    if progress is not None:
        progress(len(items), len(pdfs), "")
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
