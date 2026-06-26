"""Run a PDF through one or more stages.

Invariant: the ORIGINAL file is never mutated. The runner copies it to the
output directory once and every stage operates on that working copy -- the seed
of the reversibility / do-not-degrade guarantee (plan SEC4 / testing blind spot).
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from speade.audit.log import sha256_file
from speade.pipeline.contract import Sidecar, StageResult
from speade.pipeline.registry import get_stage


def sidecar_path(pdf: Path) -> Path:
    """Where the sidecar JSON for `pdf` lives (next to it)."""
    return pdf.with_suffix(pdf.suffix + ".sidecar.json")


def run_pipeline(
    pdf: Path,
    impls: Iterable[str],
    out_dir: Path,
    options: dict | None = None,
) -> StageResult:
    """Copy `pdf` into `out_dir`, run each stage in order, persist the sidecar."""
    pdf = Path(pdf)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    working = out_dir / pdf.name
    working.write_bytes(pdf.read_bytes())

    sidecar = Sidecar(source_path=str(pdf), source_sha256=sha256_file(pdf))
    result = StageResult(output_pdf=working, sidecar=sidecar)

    for impl in impls:
        stage = get_stage(impl)
        result = stage.run(result.output_pdf, result.sidecar, options)

    sidecar_path(result.output_pdf).write_text(
        result.sidecar.model_dump_json(indent=2), encoding="utf-8"
    )
    return result
