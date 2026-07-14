"""SPIKE (throwaway) -- resolve D5: does the FREE OpenDataLoader tier clear the
veraPDF PDF/UA-1 gate?

NOT shipped. This measures, rather than argues, whether SPEADE can tag for free:
it runs OpenDataLoader (Apache-2.0) over a folder of born-digital PDFs, optionally
stamps the PDF/UA-1 identifier + MarkInfo with pikepdf (the cheap "conformance
finish"), then scores each result with SPEADE's OWN veraPDF gate and prints the
per-file failed-clause counts. Delete `spikes/` once D5 is decided.

Run with EPHEMERAL deps (nothing is added to pyproject / uv.lock, so the shipped
licence closure stays clean):

    uv run --with pikepdf --with opendataloader-pdf \
        python spikes/tag_engine_spike.py PDF_DIR [--no-finish]

`--no-finish` scores the raw OpenDataLoader output (no pikepdf) so you can see how
much of the gate the free Tagged-PDF alone clears vs. what the finish buys you.

System tools required (the script preflights them and tells you what's missing
instead of crashing): Java 11+ (OpenDataLoader engine) and Docker (veraPDF image).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

# --- config you may need to tweak ONCE against your installed version ---------
# The CLI that turns an untagged PDF into a Tagged PDF inside --output-dir.
# `--format tagged-pdf` is the accessibility output; `{input}`/`{outdir}` fill per file.
OPENDATALOADER_CMD = [
    "opendataloader-pdf",
    "{input}",
    "--output-dir",
    "{outdir}",
    "--format",
    "tagged-pdf",
]
VERAPDF_PROFILE = "ua1"
# -----------------------------------------------------------------------------

# Reuse SPEADE's real gate as the judge -- the whole point is to score against the
# exact same veraPDF harness the pipeline ships with.
try:
    from speade.validation.verapdf import validate as verapdf_validate
except ImportError as exc:  # pragma: no cover - spike guard
    sys.exit(f"cannot import SPEADE's veraPDF gate ({exc}); run from the repo root via `uv run`.")

try:
    import pikepdf

    HAS_PIKEPDF = True
except ImportError:
    HAS_PIKEPDF = False


def preflight(want_finish: bool) -> list[str]:
    """Return human-readable messages for any missing tool (empty == all present)."""
    missing: list[str] = []
    if shutil.which("opendataloader-pdf") is None:
        missing.append(
            "opendataloader-pdf CLI not on PATH -- run via "
            "`uv run --with opendataloader-pdf ...` (also needs a system Java 11+)."
        )
    if shutil.which("java") is None:
        missing.append("java not on PATH -- OpenDataLoader's engine needs a JRE 11+.")
    if shutil.which("docker") is None:
        missing.append(
            "docker not on PATH -- veraPDF gate returns 'verapdf-unavailable' for every file."
        )
    if want_finish and not HAS_PIKEPDF:
        missing.append(
            "pikepdf not importable -- run via `uv run --with pikepdf ...`, or pass --no-finish."
        )
    return missing


def run_opendataloader(pdf: Path, outdir: Path) -> Path | None:
    """Run OpenDataLoader on `pdf`; return the tagged PDF it produced (or None)."""
    outdir.mkdir(parents=True, exist_ok=True)
    cmd = [part.format(input=str(pdf), outdir=str(outdir)) for part in OPENDATALOADER_CMD]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        print("    ! opendataloader-pdf not found on PATH (see preflight); skipping.")
        return None
    # Find a PDF in the output dir (name convention varies by version -> glob).
    produced = sorted(p for p in outdir.rglob("*.pdf") if p.resolve() != pdf.resolve())
    if not produced:
        tail = (proc.stderr or proc.stdout or "").strip()[-300:]
        print(f"    ! no tagged PDF produced (exit {proc.returncode}). check OPENDATALOADER_CMD.")
        if tail:
            print(f"    ! engine said: {tail}")
        return None
    return produced[0]


def finish_pdf_ua(src: Path, dst: Path) -> bool:
    """Stamp the PDF/UA-1 identifier + MarkInfo (the cheap conformance finish).

    Best-effort: the structural bits OpenDataLoader's free tier leaves off before
    a paid PDF/UA export. Returns True if `dst` was written.
    """
    try:
        with pikepdf.open(src) as pdf:
            pdf.Root.MarkInfo = pikepdf.Dictionary({"/Marked": True})
            try:
                with pdf.open_metadata() as meta:
                    meta["pdfuaid:part"] = "1"
            except Exception as meta_exc:  # namespace quirks across pikepdf versions
                print(f"    ~ pikepdf XMP pdfuaid stamp skipped ({meta_exc}); MarkInfo still set.")
            pdf.save(dst)
        return True
    except Exception as exc:
        print(f"    ! pikepdf finish failed ({exc}); scoring the raw tagged PDF instead.")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_dir", type=Path, help="folder of born-digital PDFs to tag + score")
    parser.add_argument(
        "--no-finish",
        dest="finish",
        action="store_false",
        help="score raw OpenDataLoader output without the pikepdf PDF/UA finish",
    )
    parser.add_argument("--limit", type=int, default=0, help="only process the first N PDFs")
    parser.add_argument(
        "--keep", action="store_true", help="keep the temp outputs for manual inspection"
    )
    args = parser.parse_args()

    if not args.pdf_dir.is_dir():
        sys.exit(f"not a directory: {args.pdf_dir}")
    pdfs = sorted(args.pdf_dir.glob("*.pdf"))
    if args.limit:
        pdfs = pdfs[: args.limit]
    if not pdfs:
        sys.exit(f"no PDFs in {args.pdf_dir}")

    problems = preflight(args.finish)
    if problems:
        print("preflight warnings:")
        for p in problems:
            print(f"  - {p}")
        print()

    workdir = Path(tempfile.mkdtemp(prefix="speade_tag_spike_"))
    rows: list[tuple[str, str, str, int, str]] = []
    clause_counter: Counter[str] = Counter()
    passed = 0
    mode = "on" if args.finish else "off"

    print(f"scoring {len(pdfs)} PDF(s) | finish={mode} | work={workdir}\n")
    for pdf in pdfs:
        print(f"* {pdf.name}")
        outdir = workdir / f"{pdf.stem}.out"
        tagged = run_opendataloader(pdf, outdir)
        if tagged is None:
            rows.append((pdf.name, "no", "-", -1, "opendataloader produced no pdf"))
            continue

        target = tagged
        if args.finish and HAS_PIKEPDF:
            finished = workdir / f"{pdf.stem}.ua.pdf"
            if finish_pdf_ua(tagged, finished):
                target = finished

        result = verapdf_validate(target, VERAPDF_PROFILE)
        nfail = len(result.failed_clauses)
        clause_counter.update(result.failed_clauses)
        if result.passed:
            passed += 1
        verdict = "PASS" if result.passed else "fail"
        sample = ", ".join(result.failed_clauses[:4]) + (" ..." if nfail > 4 else "")
        rows.append((pdf.name, "yes", verdict, nfail, sample or "-"))
        print(f"    -> tagged=yes  ua1={verdict}  failed_clauses={nfail}")

    # --- summary table --------------------------------------------------------
    print("\n" + "=" * 78)
    print(f"{'file':<32} {'tagged':<7} {'ua-1':<5} {'#fail':<6} sample clauses")
    print("-" * 78)
    for name, tagged_s, ua, nfail, sample in rows:
        nfail_s = "-" if nfail < 0 else str(nfail)
        print(f"{name[:31]:<32} {tagged_s:<7} {ua:<5} {nfail_s:<6} {sample}")
    print("=" * 78)

    scored = sum(1 for r in rows if r[1] == "yes")
    print(f"\nRESULT: {passed}/{scored} scored file(s) PASS PDF/UA-1 (finish={mode}).")
    if clause_counter:
        print("most common failed clauses (what a paid PDF/UA export would need to close):")
        for clause, count in clause_counter.most_common(8):
            print(f"  {count:>3}x  {clause}")
    print(
        "\nread it as:\n"
        "  most PASS -> the FREE tier (+ pikepdf finish) clears your gate; ship free/open.\n"
        "  most fail -> inspect the clauses above; likely need the paid PDF/UA export\n"
        "               (OpenDataLoader enterprise, same tool) or PDFix SDK."
    )

    if args.keep:
        print(f"\ntemp outputs kept at: {workdir}")
    else:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
