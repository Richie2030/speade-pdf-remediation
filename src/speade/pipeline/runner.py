"""Run a PDF through one or more stages.

Implement here the invariant that the ORIGINAL file is never mutated: copy it
into the output dir once, run each stage on that working copy threading the
sidecar through, and persist the sidecar JSON next to the output.

Outbox hygiene: reviewers only ever see ONE final PDF + sidecar per input.
Intermediate stage files (ocr's `X.ocr.pdf`, tag's `X.tagged.pdf`) are removed
once the final artifact is normalized back to the original name, and a failed
run cleans up after itself instead of leaving half-processed drafts.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from speade.audit.log import append_event, sha256_file
from speade.pipeline.contract import Sidecar, Stage, StageResult


def run(
    src: Path,
    stages: Iterable[Stage],
    outbox: Path,
    audit_log: Path | None = None,
    sidecar_dir: Path | None = None,
) -> Sidecar:
    """Run `src` through `stages` into `outbox`, never mutating the original.
    Returns the final sidecar, persisted into `sidecar_dir` (default: beside
    the output in the outbox) as `<pdf name>.sidecar.json`."""

    # 1. NEVER MUTATE THE ORIGINAL: copy it into the outbox, work on the copy.
    outbox.mkdir(parents=True, exist_ok=True)
    side_dir = sidecar_dir if sidecar_dir is not None else outbox
    side_dir.mkdir(parents=True, exist_ok=True)
    working = outbox / src.name
    shutil.copy2(src, working)

    # 2. seed the sidecar with the source hash (the trust trail).
    sidecar = Sidecar(
        source_path=src.as_posix(),  # POSIX string for the Linux target
        source_sha256=sha256_file(src),
    )

    # 3. thread the working copy + sidecar through each stage in turn, tracking
    # every file a stage writes so the outbox can be swept clean afterwards.
    final = outbox / src.name
    produced = [working]
    output = working
    try:
        for stage in stages:
            result: StageResult = stage.run(output, sidecar)
            output = result.output  # may be a NEW file (ocr's .ocr.pdf, tag's .tagged.pdf)
            produced.append(output)
            sidecar = result.sidecar

        # 3b. normalize the final artifact back to the original name — ONCE,
        # after all stages (inside the guard: a failed rename must clean up too).
        if output != final:
            output.replace(final)  # overwrite the untagged working copy
            output = final
    except Exception:
        # a failed run leaves NO partial drafts behind (the inbox original is
        # untouched) -- including any stale sidecar a PREVIOUS successful run of
        # the same file left behind, which would otherwise be a phantom queue
        # entry pointing at a PDF this cleanup deletes. Best-effort, so cleanup
        # never masks the stage's real error.
        for path in [*produced, side_dir / (final.name + ".sidecar.json")]:
            with suppress(OSError):
                path.unlink(missing_ok=True)
        raise

    # 3c. drop intermediate stage files (e.g. `X.ocr.pdf`): the outbox holds one
    # final PDF per input plus its sidecar, never every stage's working file.
    for path in produced:
        if path != final:
            with suppress(OSError):
                path.unlink(missing_ok=True)

    # 4. record the output hash + persist the sidecar into `side_dir`, keyed by
    # the output's filename (default: right next to the output)
    sidecar.output_sha256 = sha256_file(output)
    side_path = side_dir / (output.name + ".sidecar.json")
    side_path.write_text(sidecar.model_dump_json(), encoding="utf-8")

    # 5. append ONE audit line linking source -> output (only if a log was given)
    if audit_log is not None:
        append_event(
            audit_log,
            {
                "event": "run",
                "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                "file": src.name,
                "source_sha256": sidecar.source_sha256,
                "output_sha256": sidecar.output_sha256,
                "stages_applied": sidecar.stages_applied,
            },
        )

    return sidecar
