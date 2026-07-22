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

from speade import subproc
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

# The exact shape ocr.page_content gives every OCR-built page: the scan image
# drawn inside one leading artifact block, then the invisible text.
_ARTIFACT_PREFIX = b"/Artifact BMC"


def _strip_page_backgrounds(src: Path, dst: Path) -> bool:
    """Write a copy of `src` with every page's leading artifact block (the scan
    image) removed -- the text-only view the tagging engine gets to see. Returns
    False (writing nothing) unless EVERY page has the exact shape ocr.py builds,
    so an unexpected document is tagged as-is instead of half-stripped."""
    import pikepdf

    with pikepdf.open(src) as doc:
        contents = []
        for page in doc.pages:
            raw = page.obj.get("/Contents")
            if not isinstance(raw, pikepdf.Stream):
                return False
            data = raw.read_bytes()
            head, sep, rest = data.partition(b"EMC")
            if not data.startswith(_ARTIFACT_PREFIX) or not sep:
                return False
            contents.append(rest.lstrip())
        for page, text_only in zip(doc.pages, contents, strict=True):
            page.obj.Contents = doc.make_stream(text_only)
            xobjects = page.obj.get("/Resources", pikepdf.Dictionary()).get("/XObject")
            if xobjects is not None and "/Im0" in xobjects:
                del xobjects.Im0
        doc.save(dst)
    return True


def _restore_page_backgrounds(tagged: Path, original: Path, dst: Path) -> None:
    """Composite each page's scan image (from `original`, the OCR-built file)
    back UNDER the engine-tagged content, still wrapped as an untagged artifact:
    visually identical to the pre-strip page, structurally pure background.
    Fails loud -- shipping a text-only page (blank to sighted readers) would be
    far worse than a failed batch item."""
    import pikepdf

    with pikepdf.open(original) as orig, pikepdf.open(tagged) as doc:
        if len(orig.pages) != len(doc.pages):
            raise RuntimeError(
                "tagging engine changed the page count; cannot restore page backgrounds"
            )
        for orig_page, tagged_page in zip(orig.pages, doc.pages, strict=True):
            data = orig_page.obj.Contents.read_bytes()
            head, sep, _rest = data.partition(b"EMC")
            if not data.startswith(_ARTIFACT_PREFIX) or not sep:
                raise RuntimeError("page background missing from the OCR layer")
            block = head + sep + b"\n"
            image = orig_page.obj.Resources.XObject.Im0

            resources = tagged_page.obj.get("/Resources")
            if resources is None:
                resources = pikepdf.Dictionary()
                tagged_page.obj.Resources = resources
            xobjects = resources.get("/XObject")
            if xobjects is None:
                xobjects = pikepdf.Dictionary()
                resources.XObject = xobjects
            name = "/Im0"
            if name in xobjects:  # the engine used the name: pick our own
                name = "/SpeadeBg"
                block = block.replace(b"/Im0", name.encode())
            xobjects[pikepdf.Name(name)] = doc.copy_foreign(image)
            tagged_page.contents_add(block, prepend=True)
        doc.save(dst)


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
        # dc:title is what viewers display (DisplayDocTitle): use the ORIGINAL
        # filename from the sidecar, not the working copy's stem (post-OCR the
        # working file is `X.ocr.pdf`, which must not become the visible title).
        # ocr_layered: the page scans are backdrops for OUR text layer -- keep
        # them out of the engine's sight so no per-page Figure tag is created.
        self._tag(
            pdf,
            out,
            title=Path(sidecar.source_path).stem,
            strip_background=sidecar.ocr_layered,
        )

        sidecar.applied(self.name)
        return StageResult(stage=self.name, output=out, sidecar=sidecar, changed=True)

    def _tag(self, pdf: Path, out: Path, title: str, strip_background: bool = False) -> None:
        """Auto-tag `pdf` with OpenDataLoader (arms-length CLI), then stamp the
        PDF/UA-1 identifier + `title`, writing the result to `out`. Fails loud
        (raises) if the engine is missing or produces nothing -- the runner treats
        that as a failure.

        `strip_background` (OCR'd scans): the engine is shown a copy WITHOUT the
        page-scan images, so it cannot wrap each page in a meaningless Figure tag
        (screen readers would announce "figure" on every page); the images are
        composited back underneath afterwards as untagged background artifacts.
        """
        with tempfile.TemporaryDirectory(prefix="speade_tag_") as tmp:
            outdir = Path(tmp)
            engine_input = pdf
            if strip_background:
                stripped = outdir / f"stripped_{pdf.name}"
                if _strip_page_backgrounds(pdf, stripped):
                    engine_input = stripped
                else:  # unexpected page shape: tag as-is (Figure stays -- degraded, not broken)
                    strip_background = False
            cmd = [
                part.format(input=str(engine_input), outdir=str(outdir))
                for part in OPENDATALOADER_CMD
            ]
            try:
                proc = subproc.run(cmd, capture_output=True, text=True, timeout=_TAG_TIMEOUT_S)
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

            produced = sorted(p for p in outdir.rglob("*.pdf") if p.name != f"stripped_{pdf.name}")
            if not produced:
                raise RuntimeError(
                    "opendataloader-pdf produced no tagged PDF -- check the OPENDATALOADER_CMD "
                    "flags against `opendataloader-pdf --help`."
                )

            tagged = produced[0]
            if strip_background:
                restored = outdir / f"restored_{pdf.name}"
                _restore_page_backgrounds(tagged, pdf, restored)
                tagged = restored

            self._finish_pdf_ua(tagged, out, title=title)

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
