"""Command-line entry point: the `speade` runner.

Define the Typer app and its commands here -- `stages` (list the configured stage
roles), `run FILE.pdf` (run the configured stages on one PDF, offline, never
mutating the original), and `verify FILE.pdf` (the human gate: run veraPDF + record
an approve/reject decision). Expose `main()` for the console script.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
import yaml

from speade.audit.log import append_event
from speade.pipeline import registry, runner
from speade.pipeline.contract import Approval, ApprovalStatus, Sidecar
from speade.validation import verapdf

app = typer.Typer(add_completion=False, help="SPEADE PDF remediation pipeline")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def _load_config(config_path: Path) -> dict:
    """Load the small YAML config file used by the offline core."""

    with config_path.open("r", encoding="utf-8") as file_handle:
        data = yaml.safe_load(file_handle) or {}
    if not isinstance(data, dict):
        raise typer.BadParameter(f"Config file must contain a mapping: {config_path}")
    return data


def _stage_mapping_from_config(config: dict) -> dict[str, str]:
    """Return the configured stage-role -> implementation-name mapping."""

    pipeline = config.get("pipeline", {})
    stages = pipeline.get("stages", {})
    if not isinstance(stages, dict):
        raise typer.BadParameter("config.yaml: pipeline.stages must be a mapping")
    return {str(role): str(impl) for role, impl in stages.items()}


def _output_dir_from_config(config: dict) -> Path:
    """Return the configured local outbox path."""

    io_section = config.get("io", {})
    local_section = io_section.get("local", {}) if isinstance(io_section, dict) else {}
    outbox = local_section.get("outbox", "./data/outbox")
    return (PROJECT_ROOT / Path(outbox)).resolve()


def _audit_log_from_config(config: dict) -> Path:
    """Return the configured audit-log path (the trust trail, deliverable A1)."""

    audit_section = config.get("audit", {})
    log_path = (
        audit_section.get("log_path", "./data/audit/audit.jsonl")
        if isinstance(audit_section, dict)
        else "./data/audit/audit.jsonl"
    )
    return (PROJECT_ROOT / Path(log_path)).resolve()


def _verapdf_profile_from_config(config: dict) -> str:
    """Return the configured PDF/UA profile for the veraPDF gate (default 'ua1')."""

    validation = config.get("validation", {})
    verapdf_section = validation.get("verapdf", {}) if isinstance(validation, dict) else {}
    return str(verapdf_section.get("profile", "ua1"))


def _verapdf_cli_from_config(config: dict) -> str | None:
    """Return the configured veraPDF CLI path, or None to auto-discover (PATH/Docker)."""

    validation = config.get("validation", {})
    verapdf_section = validation.get("verapdf", {}) if isinstance(validation, dict) else {}
    path = verapdf_section.get("path")
    return str(path) if path else None


@app.command()
def stages(
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Path to config.yaml"),
    ] = DEFAULT_CONFIG_PATH,
) -> None:
    """List the configured stage roles and their implementation names."""

    config_data = _load_config(config)
    stage_mapping = _stage_mapping_from_config(config_data)

    typer.echo(f"Config: {config}")
    for role, implementation in stage_mapping.items():
        typer.echo(f"{role}: {implementation}")


@app.command()
def run(
    pdf: Annotated[Path, typer.Argument(help="PDF to process")],
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Path to config.yaml"),
    ] = DEFAULT_CONFIG_PATH,
) -> None:
    """Run the configured pipeline on one PDF and write results to the outbox."""

    if not pdf.exists():
        raise typer.BadParameter(f"Input PDF does not exist: {pdf}")
    if not pdf.is_file():
        raise typer.BadParameter(f"Input path is not a file: {pdf}")

    config_data = _load_config(config)
    stage_mapping = _stage_mapping_from_config(config_data)
    out_dir = _output_dir_from_config(config_data)
    audit_log = _audit_log_from_config(config_data)

    # Resolve each configured implementation to a Stage, in YAML order.
    try:
        stages = [registry.get_stage(impl) for impl in stage_mapping.values()]
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc

    # Delegate the work: copy-on-write, thread the sidecar, persist it, and
    # append the audit line -- all owned by the runner (never mutate the input).
    sidecar = runner.run(pdf, stages, out_dir, audit_log)

    output_pdf = out_dir / pdf.name
    sidecar_path = output_pdf.with_name(output_pdf.name + ".sidecar.json")
    typer.echo(f"Output: {output_pdf}")
    typer.echo(f"Sidecar: {sidecar_path}")
    typer.echo(f"Stages applied: {', '.join(sidecar.stages_applied) or '(none)'}")


@app.command()
def verify(
    pdf: Annotated[Path, typer.Argument(help="Remediated PDF in the outbox to sign off")],
    reviewer: Annotated[str, typer.Option("--reviewer", "-r", help="Name of the human reviewer")],
    approve: Annotated[bool, typer.Option("--approve", help="Approve this document")] = False,
    reject: Annotated[bool, typer.Option("--reject", help="Reject this document")] = False,
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Path to config.yaml"),
    ] = DEFAULT_CONFIG_PATH,
) -> None:
    """Human verification gate: run veraPDF, record the machine result, then set the
    reviewer's approve/reject decision on the sidecar. Nothing ships unverified: only
    a human moves a draft to APPROVED/REJECTED (the mandatory-gate invariant).
    """

    if approve == reject:
        raise typer.BadParameter("choose exactly one of --approve / --reject")
    if not pdf.is_file():
        raise typer.BadParameter(f"PDF not found: {pdf}")

    sidecar_path = pdf.with_name(pdf.name + ".sidecar.json")
    if not sidecar_path.is_file():
        raise typer.BadParameter(f"Sidecar not found next to the PDF: {sidecar_path}")

    sidecar = Sidecar.model_validate_json(sidecar_path.read_text(encoding="utf-8"))

    config_data = _load_config(config)
    profile = _verapdf_profile_from_config(config_data)
    audit_log = _audit_log_from_config(config_data)

    # Machine gate (advisory): record veraPDF's verdict. It fails closed via a flag
    # when no veraPDF runner is available rather than blocking the human reviewer.
    vera = verapdf.validate(pdf, profile, cli=_verapdf_cli_from_config(config_data))
    sidecar.verapdf_passed = vera.passed
    sidecar.verapdf_failed_clauses = vera.failed_clauses

    # Human gate (authoritative): only a person can APPROVE or REJECT.
    status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
    sidecar.approval = Approval(status=status, reviewer=reviewer, decided_at=datetime.now(UTC))

    sidecar_path.write_text(sidecar.model_dump_json(indent=2), encoding="utf-8", newline="\n")

    append_event(
        audit_log,
        {
            "event": "verify",
            "reviewer": reviewer,
            "decision": status.value,
            "verapdf_passed": vera.passed,
            "output_sha256": sidecar.output_sha256,
        },
    )

    verdict = "passed" if vera.passed else "FAILED"
    clauses = ", ".join(vera.failed_clauses) or "no failures"
    typer.echo(f"veraPDF ({profile}): {verdict} ({clauses})")
    typer.echo(f"Decision: {status.value} by {reviewer}")
    typer.echo(f"Sidecar updated: {sidecar_path}")


def main() -> None:
    """Console-script entry point."""

    app()
