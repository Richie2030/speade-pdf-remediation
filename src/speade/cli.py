"""Command-line entry point: the `speade` runner.

Define the Typer app and its commands here -- `stages` (list available stage
implementations) and `run FILE.pdf` (run the configured stages on one PDF,
offline, never mutating the original). Expose `main()` for the console script.
"""

from __future__ import annotations

import hashlib
from importlib import import_module
from pathlib import Path
from typing import Annotated

import typer
import yaml

from speade.pipeline.contract import Sidecar


app = typer.Typer(add_completion=False, help="SPEADE PDF remediation pipeline")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def _sha256(path: Path) -> str:
	"""Return the SHA-256 digest of a file."""

	digest = hashlib.sha256()
	with Path(path).open("rb") as file_handle:
		for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
			digest.update(chunk)
	return digest.hexdigest()


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


def _resolve_stage(implementation_name: str):
	"""Resolve a stage implementation.

	First tries the registry module if it is available. If the registry or a
	stage is still unfinished, falls back to the local noop stage so the CLI
	still works for the current skeleton.
	"""

	try:
		registry = import_module("speade.pipeline.registry")
		get_stage = getattr(registry, "get_stage")
		return get_stage(implementation_name)
	except Exception:
		if implementation_name == "noop":
			from speade.stages.noop import NoopStage

			return NoopStage()
		raise typer.BadParameter(
			f"Stage implementation '{implementation_name}' is not available yet. "
			"This repository currently only guarantees 'noop'."
		)


def _sidecar_for_input(pdf: Path) -> dict:
	"""Create the minimal sidecar payload expected by the contract model."""

	return {
		"source_path": pdf.as_posix(),
		"source_sha256": _sha256(pdf),
		"route": "unknown",
		"stages_applied": [],
		"flags": [],
	}


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
	out_dir.mkdir(parents=True, exist_ok=True)

	output_pdf = out_dir / pdf.name
	typer.echo(f"Copying {pdf} -> {output_pdf}")
	output_pdf.write_bytes(pdf.read_bytes())

	sidecar_data = _sidecar_for_input(pdf)
	sidecar_model = None

	# Run the configured stages in YAML order. The current skeleton only
	# guarantees noop, but this structure will keep working as more stages land.
	for role, implementation_name in stage_mapping.items():
		stage = _resolve_stage(implementation_name)
		if sidecar_model is None:
			from speade.pipeline.contract import Sidecar as SidecarModel

			sidecar_model = SidecarModel(**sidecar_data)
		result = stage.run(output_pdf, sidecar_model)
		output_pdf = result.output
		sidecar_model = result.sidecar
		typer.echo(f"{role}: {implementation_name} -> {result.stage} (changed={result.changed})")

	if sidecar_model is None:
		from speade.pipeline.contract import Sidecar as SidecarModel

		sidecar_model = SidecarModel(**sidecar_data)

	sidecar_model.output_sha256 = _sha256(output_pdf)
	sidecar_path = output_pdf.with_suffix(output_pdf.suffix + ".sidecar.json")
	sidecar_path.write_text(sidecar_model.model_dump_json(indent=2), encoding="utf-8")

	typer.echo(f"Output: {output_pdf}")
	typer.echo(f"Sidecar: {sidecar_path}")


def main() -> None:
	"""Console-script entry point."""

	app()

