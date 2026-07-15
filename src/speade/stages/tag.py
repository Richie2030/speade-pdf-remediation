"""Tagging stage -- writes the PDF/UA structure (tags, reading order, headings,
tables, language, title). The hardest, make-or-break component.

Engine (D5): OpenDataLoader PDF (Apache-2.0) runs as an arms-length CLI (rule L2:
run, never import) -- it auto-tags an untagged PDF into a Tagged PDF. A cheap
PDF/UA-1 conformance finish (MarkInfo + the pdfuaid identifier) is then stamped with
pikepdf (MPL, a permitted in-process dependency). The objective veraPDF gate + the
human reviewer judge the result -- this stage only has to produce the draft.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from speade.pipeline.contract import Route, Sidecar, StageResult

# Arms-length tagging engine. It is a Java CLI (system install, like veraPDF's JRE),
# NOT a pip dependency. `--format tagged-pdf` emits an accessible Tagged PDF into
# --output-dir; `{input}`/`{outdir}` fill per run.
OPENDATALOADER_CMD = [
    "opendataloader-pdf",
    "{input}",
    "--output-dir",
    "{outdir}",
    "--format",
    "tagged-pdf",
]

# OpenDataLoader can be slow on large scans; fail loud rather than hang forever.
_TAG_TIMEOUT_S = 600

# Default document language stamped into /Lang when the engine leaves it unset (this
# is what closes veraPDF UA-1 clause 7.2-34/7.2-22). UCC content is predominantly
# English; per-document language detection is a future refinement, and the human
# reviewer can correct it at the gate.
DEFAULT_LANG = "en"


class TagStage:
    """Write PDF/UA structure tags. Unlike detect/noop, this stage CHANGES the PDF:
    it writes a new tagged copy and never edits its input in place. The engine runs
    as an arms-length subprocess -- never imported (rule L2).
    """

    name = "tag"

    def run(self, pdf: Path, sidecar: Sidecar) -> StageResult:
        # unreadable inputs (encrypted / corrupt -- flagged by detect) cannot be
        # tagged; they flow to the gate untouched, reason already on the sidecar.
        if any(flag.startswith("unreadable-") for flag in sidecar.flags):
            sidecar.flags.append("tag-skipped-unreadable")
            sidecar.applied(self.name)
            return StageResult(stage=self.name, output=pdf, sidecar=sidecar, changed=False)

        # never clobber an existing structure tree (do-not-degrade): an already
        # tagged PDF skips the engine and flows to the gate untouched.
        if sidecar.already_tagged:
            sidecar.flags.append(
                "tag-skipped-already-tagged"
            )  # Goes straight to human verification
            sidecar.applied(self.name)
            return StageResult(
                stage=self.name,
                output=pdf,
                sidecar=sidecar,
                changed=False,
            )

        # tagging needs real text: a scanned (image-only) doc must be OCR'd first.
        if sidecar.route == Route.SCANNED:
            sidecar.flags.append("tag-skipped-needs-ocr")  # Goes to OCR as tagging needs it
            sidecar.applied(self.name)
            return StageResult(stage=self.name, output=pdf, sidecar=sidecar, changed=False)

        # policy: an UNKNOWN (mixed text+image) doc is tagged anyway -- its real
        # text pages gain structure now; any image-only pages surface at the gate,
        # where this flag tells the reviewer to check coverage.
        if sidecar.route == Route.UNKNOWN:
            sidecar.flags.append("tag-ran-on-unknown-route")

        out = pdf.with_name(f"{pdf.stem}.tagged.pdf")  # NEW file, never mutate input
        self._tag(pdf, out)

        sidecar.applied(self.name)
        return StageResult(stage=self.name, output=out, sidecar=sidecar, changed=True)

    def _tag(self, pdf: Path, out: Path) -> None:
        """Auto-tag `pdf` with OpenDataLoader (arms-length CLI), then stamp the
        PDF/UA-1 identifier, writing the result to `out`. Fails loud (raises) if the
        engine is missing or produces nothing -- the runner treats that as a failure.
        """
        with tempfile.TemporaryDirectory(prefix="speade_tag_") as tmp:
            outdir = Path(tmp)
            cmd = [part.format(input=str(pdf), outdir=str(outdir)) for part in OPENDATALOADER_CMD]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_TAG_TIMEOUT_S)
            except FileNotFoundError as exc:
                raise RuntimeError(
                    "opendataloader-pdf not found on PATH -- install the tagging engine "
                    "(needs a system Java 11+); see spikes/README.md."
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"opendataloader-pdf timed out after {_TAG_TIMEOUT_S}s on {pdf.name}."
                ) from exc

            if proc.returncode != 0:
                raise RuntimeError(
                    f"opendataloader-pdf failed (exit {proc.returncode}): {proc.stderr[:300]}"
                )

            produced = sorted(p for p in outdir.rglob("*.pdf"))
            if not produced:
                raise RuntimeError(
                    "opendataloader-pdf produced no tagged PDF -- check the OPENDATALOADER_CMD "
                    "flags against `opendataloader-pdf --help`."
                )

            self._finish_pdf_ua(produced[0], out, title=pdf.stem)

    @staticmethod
    def _finish_pdf_ua(tagged: Path, out: Path, title: str, lang: str = DEFAULT_LANG) -> None:
        """Stamp with pikepdf the PDF/UA-1 conformance bits the free tagger leaves off:
        MarkInfo, the catalog language (/Lang), ViewerPreferences/DisplayDocTitle, and the
        XMP pdfuaid identifier + dc:title. These close the metadata/language veraPDF UA-1
        clauses (7.1-9, 7.1-10, 7.2-22, 7.2-34) that OpenDataLoader's structure tags omit.

        Best-effort: if pikepdf is unavailable or cannot process the file, ship the raw
        tagged PDF and let the veraPDF gate flag any residual gap -- the structure tags are
        what matter, the metadata stamp is the finish.
        """
        try:
            import pikepdf

            with pikepdf.open(tagged) as doc:
                doc.Root.MarkInfo = pikepdf.Dictionary({"/Marked": True})
                doc.Root.Lang = pikepdf.String(lang)  # 7.2-34 / 7.2-22: determinable language
                # 7.1-10: viewers must display the title, not the filename.
                doc.Root.ViewerPreferences = pikepdf.Dictionary({"/DisplayDocTitle": True})
                with doc.open_metadata() as meta:
                    meta["pdfuaid:part"] = "1"
                    if not meta.get("dc:title"):
                        meta["dc:title"] = title  # 7.1-9: catalog metadata needs a dc:title
                doc.save(out)
        except Exception:
            shutil.copyfile(tagged, out)
