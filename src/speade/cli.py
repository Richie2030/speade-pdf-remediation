"""Command-line entry point: the `speade` runner.

Define the Typer app and its commands here -- `stages` (list the configured
stage roles), `run FILE.pdf` (one PDF), `run-batch [FOLDER]` (sweep a folder,
default the configured inbox), and `verify FILE.pdf` (the human gate). Every
command is a THIN SHELL over speade.service -- the engine shared with the
desktop client -- so the CLI owns argument parsing and echoing, never pipeline
or gate logic. Expose `main()` for the console script.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from speade import service
from speade.service import DEFAULT_CONFIG_PATH

app = typer.Typer(add_completion=False, help="SPEADE PDF remediation pipeline")

ConfigOption = Annotated[Path, typer.Option("--config", "-c", help="Path to config.yaml")]


@app.command()
def stages(config: ConfigOption = DEFAULT_CONFIG_PATH) -> None:
    """List the configured stage roles and their implementation names."""

    try:
        mapping = service.stage_mapping(config)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    typer.echo(f"Config: {config}")
    for role, implementation in mapping.items():
        typer.echo(f"{role}: {implementation}")


@app.command()
def run(
    pdf: Annotated[Path, typer.Argument(help="PDF to process")],
    config: ConfigOption = DEFAULT_CONFIG_PATH,
) -> None:
    """Run the configured pipeline on one PDF and write results to the outbox."""

    if not pdf.exists():
        raise typer.BadParameter(f"Input PDF does not exist: {pdf}")
    if not pdf.is_file():
        raise typer.BadParameter(f"Input path is not a file: {pdf}")

    try:
        sidecar = service.run_one(pdf, config)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    workspace = service.workspace(config)
    output_pdf = workspace.outbox / pdf.name
    typer.echo(f"Output: {output_pdf}")
    typer.echo(f"Sidecar: {workspace.sidecars / (pdf.name + '.sidecar.json')}")
    typer.echo(f"Stages applied: {', '.join(sidecar.stages_applied) or '(none)'}")


@app.command("run-batch")
def run_batch(
    folder: Annotated[
        Path | None,
        typer.Argument(help="Folder of PDFs to process (default: the configured inbox)"),
    ] = None,
    config: ConfigOption = DEFAULT_CONFIG_PATH,
) -> None:
    """Sweep a folder of PDFs through the pipeline -- the batch front door both
    clients call. One bad file is reported and skipped, never kills the batch."""

    if folder is not None and not folder.is_dir():
        raise typer.BadParameter(f"Not a folder: {folder}")

    try:
        items = service.run_batch(folder, config)
        workspace = service.workspace(config)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    if not items:
        typer.echo(f"No PDFs found in {folder or workspace.inbox}")
        return

    for item in items:
        if item.skipped:
            typer.echo(f"  skip  {item.file} (already processed, unchanged)")
        elif item.ok and item.sidecar is not None:
            flags = f"  [{', '.join(item.sidecar.flags)}]" if item.sidecar.flags else ""
            vera = {True: "  veraPDF: pass", False: "  veraPDF: FAIL"}.get(
                item.sidecar.verapdf_passed, ""
            )
            typer.echo(f"  ok    {item.file}{flags}{vera}")
        else:
            typer.echo(f"  FAIL  {item.file}: {item.error}")
    processed = sum(1 for item in items if item.ok)
    typer.echo(f"{processed}/{len(items)} processed -> {workspace.outbox}")


@app.command()
def verify(
    pdf: Annotated[Path, typer.Argument(help="Remediated PDF in the outbox to sign off")],
    reviewer: Annotated[str, typer.Option("--reviewer", "-r", help="Name of the human reviewer")],
    approve: Annotated[bool, typer.Option("--approve", help="Approve this document")] = False,
    reject: Annotated[bool, typer.Option("--reject", help="Reject this document")] = False,
    config: ConfigOption = DEFAULT_CONFIG_PATH,
) -> None:
    """Human verification gate: run veraPDF, record the machine result, then set the
    reviewer's approve/reject decision on the sidecar. Nothing ships unverified: only
    a human moves a draft to APPROVED/REJECTED (the mandatory-gate invariant).
    """

    if approve == reject:
        raise typer.BadParameter("choose exactly one of --approve / --reject")
    if not pdf.is_file():
        raise typer.BadParameter(f"PDF not found: {pdf}")

    try:
        sidecar = service.decide(pdf, reviewer=reviewer, approve=approve, config_path=config)
        profile = service.workspace(config).verapdf_profile
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    verdict = "passed" if sidecar.verapdf_passed else "FAILED"
    clauses = ", ".join(sidecar.verapdf_failed_clauses) or "no failures"
    typer.echo(f"veraPDF ({profile}): {verdict} ({clauses})")
    typer.echo(f"Decision: {sidecar.approval.status.value} by {reviewer}")
    sidecar_path = service.workspace(config).sidecars / (pdf.name + ".sidecar.json")
    typer.echo(f"Sidecar updated: {sidecar_path}")


def main() -> None:
    """Console-script entry point."""

    app()
