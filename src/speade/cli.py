"""The one-command runner (plan F1 DoD).

uv run speade stages              # list available stage implementations
uv run speade run FILE.pdf        # run the config's stages on one PDF (offline)
uv run speade run FILE.pdf -s noop -s detect
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from speade.audit.log import record
from speade.config import Config
from speade.pipeline import registry, runner

app = typer.Typer(
    help="SPEADE PDF remediation pipeline (offline core).",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def stages() -> None:
    """List available stage implementations."""
    for name in registry.available():
        typer.echo(name)


@app.command()
def run(
    pdf: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    stage: Annotated[
        list[str] | None,
        typer.Option("--stage", "-s", help="Stage impl(s) to run, in order. Defaults to config."),
    ] = None,
    config: Annotated[Path, typer.Option("--config", "-c")] = Path("config.yaml"),
) -> None:
    """Run a PDF through one or more stages. The original is never mutated."""
    cfg = Config.load(config)
    impls = stage or list(cfg.pipeline.stages.values()) or ["noop"]

    result = runner.run_pipeline(pdf, impls, cfg.io.local.outbox)

    record(
        {
            "event": "run",
            "source": str(pdf),
            "output": str(result.output_pdf),
            "source_sha256": result.sidecar.source_sha256,
            "stages": result.sidecar.stages_applied,
            "route": result.sidecar.route.value,
        },
        cfg.audit.log_path,
    )

    typer.echo(f"OK  {pdf}  ->  {result.output_pdf}")
    typer.echo(f"    stages: {result.sidecar.stages_applied}")
    typer.echo(f"    route : {result.sidecar.route.value}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
