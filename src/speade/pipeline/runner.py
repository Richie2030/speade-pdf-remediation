"""Run a PDF through one or more stages.

Implement here the invariant that the ORIGINAL file is never mutated: copy it
into the output dir once, run each stage on that working copy threading the
sidecar through, and persist the sidecar JSON next to the output.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path

from speade.audit.log import append_event, sha256_file
from speade.pipeline.contract import Sidecar, Stage, StageResult


def run(
    src: Path,
    stages: Iterable[Stage],
    outbox: Path,
    audit_log: Path | None = None,
) -> Sidecar:
    """Run `src` through `stages` into `outbox`, never mutating the original.
    Returns the final sidecar (also persisted next to the output)."""

    # 1. NEVER MUTATE THE ORIGINAL: copy it into the outbox, work on the copy.
    outbox.mkdir(parents=True, exist_ok=True)
    working = outbox / src.name
    shutil.copy2(src, working)

    # 2. seed the sidecar with the source hash (the trust trail).
    sidecar = Sidecar(
        source_path=src.as_posix(),  # POSIX string for the Linux target
        source_sha256=sha256_file(src),
    )

    # 3. thread the working copy + sidecar through each stage in turn.
    output = working
    for stage in stages:
        result: StageResult = stage.run(output, sidecar)
        output = result.output  # may be a NEW file (tag's .tagged.pdf)
        sidecar = result.sidecar

    # 3b. normalize the final artifact back to the original name — ONCE, after all stages.
    final = outbox / src.name
    if output != final:
        output.replace(final)  # overwrite the untagged working copy
        output = final

    # 4. record the output hash + persist the sidecar NEXT TO the output
    sidecar.output_sha256 = sha256_file(output)
    side_path = output.with_name(output.name + ".sidecar.json")
    side_path.write_text(sidecar.model_dump_json(), encoding="utf-8")

    # 5. append ONE audit line linking source -> output (only if a log was given)
    if audit_log is not None:
        append_event(
            audit_log,
            {
                "event": "run",
                "source_sha256": sidecar.source_sha256,
                "output_sha256": sidecar.output_sha256,
                "stages_applied": sidecar.stages_applied,
            },
        )

    return sidecar
