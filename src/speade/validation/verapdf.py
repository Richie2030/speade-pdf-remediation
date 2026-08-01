"""veraPDF acceptance harness (deliverable F4) -- the objective PDF/UA scorer.

Shell out to veraPDF as an arms-length subprocess (never import it); parse its
JSON report into a pass/fail + failing-clause result. Two interchangeable runners,
picked automatically: a locally installed veraPDF CLI (a plain Java app -- the JRE
is already required for the tag engine, so no Docker is needed; the school-PC
path), falling back to the pinned Docker image. Parse the report, don't gate on
the process exit code. This is the scorer the Phase-1 tagger spike is judged by.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from speade import subproc

# Arms-length CLI image (rule L2: run, never import). TODO: pin to a specific
# published tag before a live run -- `:latest` is a placeholder, not reproducible.
VERAPDF_IMAGE = "ghcr.io/verapdf/cli:latest"

# Our config profile key -> veraPDF `--flavour` token. Kept explicit so a config
# rename never silently changes the flavour veraPDF is asked to validate against.
_FLAVOUR = {"ua1": "ua1"}

# A hung validator must not freeze a batch or a decision forever; a validation
# that cannot finish in this window fails closed like a missing runner would.
_TIMEOUT_S = 300


class VeraResult(BaseModel):
    """The parsed verdict of one veraPDF run: did it pass, and which clauses failed."""

    passed: bool
    failed_clauses: list[str] = Field(default_factory=list)
    profile: str = ""

    @classmethod
    def from_report(cls, report: dict, profile: str) -> VeraResult:
        """Parse a veraPDF JSON report into a VeraResult.

        Tolerates the two shapes real releases emit -- `validationResult` as either a
        single object or a one-element array -- and treats a missing result as a
        failure (we never assume "no result" means "passed").
        """
        jobs = report.get("report", {}).get("jobs", [])
        vr = jobs[0].get("validationResult") if jobs else None
        if isinstance(vr, list):
            vr = vr[0] if vr else None
        if not vr:
            return cls(passed=False, failed_clauses=["no-validation-result"], profile=profile)

        summaries = vr.get("details", {}).get("ruleSummaries", [])
        failed = [
            f"{s['clause']}-{s['testNumber']}" for s in summaries if s.get("status") == "failed"
        ]
        return cls(
            passed=bool(vr.get("compliant", False)),  # trust veraPDF's own verdict flag
            failed_clauses=failed,
            profile=vr.get("profileName", profile),
        )


def _local_cli() -> str | None:
    """Locate a locally installed veraPDF CLI (`verapdf` / `verapdf.bat` on PATH)."""
    return shutil.which("verapdf")


def validate(pdf: Path, profile: str = "ua1", cli: str | None = None) -> VeraResult:
    """Validate `pdf` against a PDF/UA `profile` via the veraPDF CLI.

    Runner preference: an explicit `cli` path (config `validation.verapdf.path`),
    else a `verapdf` on PATH (the Java CLI -- no Docker), else the pinned Docker
    image. Gates on the parsed report, NOT the exit code: veraPDF exits non-zero
    for a non-compliant file, which is a result, not an error. With no runner
    available the gate fails closed -- nothing ships unverified.
    """
    pdf = Path(pdf).resolve()
    flavour = _FLAVOUR.get(profile, profile)
    runner = cli or _local_cli()
    if runner:
        cmd = [runner, "--format", "json", "--flavour", flavour, str(pdf)]
    elif shutil.which("docker"):
        cmd = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{pdf.parent}:/data:ro",
            VERAPDF_IMAGE,
            "--format",
            "json",
            "--flavour",
            flavour,
            f"/data/{pdf.name}",
        ]
    else:
        # no veraPDF CLI and no docker -- can't verify, fail closed for the human gate.
        return VeraResult(passed=False, failed_clauses=["verapdf-unavailable"], profile=profile)
    try:
        # no check=True: see docstring
        proc = subproc.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT_S)
        report = json.loads(proc.stdout)
    except OSError:
        # Missing after all, or present but refused by Windows App Control
        # (WinError 4551 on a managed PC). Either way we cannot verify: the
        # fail-closed rule stands, never a silent pass.
        return VeraResult(passed=False, failed_clauses=["verapdf-unavailable"], profile=profile)
    except subprocess.TimeoutExpired:
        return VeraResult(passed=False, failed_clauses=["verapdf-timeout"], profile=profile)
    except json.JSONDecodeError:
        return VeraResult(passed=False, failed_clauses=["verapdf-bad-report"], profile=profile)
    return VeraResult.from_report(report, profile)
