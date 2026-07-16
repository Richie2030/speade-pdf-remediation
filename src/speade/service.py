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

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from speade.audit.log import append_event, sha256_file
from speade.config import load_config
from speade.pipeline import registry, runner
from speade.pipeline.contract import Approval, ApprovalStatus, Sidecar
from speade.validation import verapdf

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
    ws.sidecars.mkdir(parents=True, exist_ok=True)
    ws.audit_log.parent.mkdir(parents=True, exist_ok=True)
    return ws


def stage_mapping(config_path: Path = DEFAULT_CONFIG_PATH) -> dict[str, str]:
    """The configured stage_role -> implementation mapping, in pipeline order."""
    return workspace(config_path).stages


def run_one(pdf: Path, config_path: Path = DEFAULT_CONFIG_PATH) -> Sidecar:
    """Run one PDF through the configured pipeline into the outbox.

    Raises KeyError for an unknown stage implementation and lets pipeline
    failures propagate -- single-file callers want the real error.
    """
    ws = workspace(config_path)
    stages = [registry.get_stage(impl) for impl in ws.stages.values()]
    return runner.run(pdf, stages, ws.outbox, ws.audit_log, sidecar_dir=ws.sidecars)


def run_batch(
    folder: Path | None = None, config_path: Path = DEFAULT_CONFIG_PATH
) -> list[BatchItem]:
    """Sweep every *.pdf in `folder` (default: the configured inbox) through the
    pipeline. Per-file failures are captured as BatchItem.error so the rest of
    the batch still runs; a misconfigured pipeline (unknown stage) raises."""
    ws = workspace(config_path)
    src_dir = Path(folder) if folder is not None else ws.inbox
    stages = [registry.get_stage(impl) for impl in ws.stages.values()]  # fail fast on config

    items: list[BatchItem] = []
    for pdf in sorted(p for p in src_dir.glob("*.pdf") if p.is_file()):
        try:
            sidecar = runner.run(pdf, stages, ws.outbox, ws.audit_log, sidecar_dir=ws.sidecars)
            items.append(BatchItem(file=pdf.name, ok=True, sidecar=sidecar))
        except Exception as exc:  # per-file isolation: record, continue the sweep
            items.append(
                BatchItem(file=pdf.name, ok=False, error=f"{type(exc).__name__}: {str(exc)[:200]}")
            )
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
            )
        )
    return items


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

    append_event(
        ws.audit_log,
        {
            "event": "verify",
            "reviewer": reviewer,
            "decision": status.value,
            "verapdf_passed": vera.passed,
            "output_sha256": sidecar.output_sha256,
        },
    )
    return sidecar
