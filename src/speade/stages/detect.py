"""Stage 2 -- born-digital vs scanned router.

Decide how a PDF flows downstream: a born-digital PDF (real text) goes straight
to tagging; a scanned (image-only) PDF must go through OCR first; ambiguous/mixed
routes conservatively to scanned. Heuristic: per-page text-char count (pypdf).
Deps: `uv sync --extra detect`.
"""

from __future__ import annotations

from pathlib import Path

import pypdf

from speade.pipeline.contract import Route, Sidecar, StageResult


class DetectStage:
    name = "detect"

    # passthrough for the bytes, exactly like noop: detect only reads.
    def run(self, pdf: Path, sidecar: Sidecar) -> StageResult:
        reader = pypdf.PdfReader(pdf)  # open once; used by both helpers
        sidecar.route = self._classify(reader)
        sidecar.already_tagged = self._is_tagged(reader)
        sidecar.applied(self.name)
        return StageResult(
            stage=self.name,
            output=pdf,  # detect only reads, never changes the PDF
            sidecar=sidecar,
            changed=False,
        )

    def _classify(self, reader: pypdf.PdfReader) -> Route:
        texty_pages = 0  # how many pages are "texty"
        total_pages = 0  # total pages
        for page in reader.pages:  # iterate pages
            total_pages += 1
            text = page.extract_text() or ""  # pull text - may return None, so or ""
            if len(text.strip()) > 100:  # our threshold # need to be tweaked to 1?
                texty_pages += 1

        if total_pages == 0:
            return Route.UNKNOWN

        ratio = texty_pages / total_pages  # ratio of texty pages to total pages
        if ratio >= 0.8:  # will need to be tweaked later to optimize
            return Route.BORN_DIGITAL
        elif ratio <= 0.1:  # will need to be tweaked later to optimize
            return Route.SCANNED
        else:
            return Route.UNKNOWN

    def _is_tagged(self, reader: pypdf.PdfReader) -> bool:
        """True if the PDF already carries a structure tree -> already tagged.
        A cheap catalog read; presence of /StructTreeRoot is enough to refuse
        re-tagging (do-not-degrade). See docs/decisions/already-tagged-handling.md."""
        return "/StructTreeRoot" in reader.root_object


# NOTE: pypdf.PdfReader(pdf) raises if the file is corrupt or password-encrypted.
# Right now that exception propagates up and crashes the run -- arguably fine for
# v1 (better to fail loudly than mislabel), but eventually catch it and route to
# UNKNOWN + add a flags note like "unreadable" (the kind of non-fatal signal the
# Sidecar.flags field exists for). Worth a mental note; not worth code today.
