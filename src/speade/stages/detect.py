"""Stage 2 -- born-digital vs scanned router.

Decide how a PDF flows downstream: a born-digital PDF (real text) goes straight
to tagging; a scanned (image-only) PDF must go through OCR first; ambiguous/mixed
stays UNKNOWN (tagged anyway downstream, with a reviewer flag). Unreadable inputs
(encrypted / corrupt) are never crashed on or guessed at: the run completes with
the reason on the sidecar (`unreadable-*` flag) and the human gate decides.
Heuristic: per-page text-char count (pypdf). Deps: `uv sync --extra detect`.
"""

from __future__ import annotations

from pathlib import Path

import pypdf

from speade.pipeline.contract import Route, Sidecar, StageResult


class DetectStage:
    name = "detect"

    # passthrough for the bytes, exactly like noop: detect only reads.
    def run(self, pdf: Path, sidecar: Sidecar) -> StageResult:
        try:
            reader = pypdf.PdfReader(pdf)  # open once; used by both helpers
            if reader.is_encrypted:
                # policy: never guess passwords (not even the empty one). Reject
                # with the reason on the sidecar; downstream stages skip on the
                # `unreadable-` prefix and the human gate decides.
                sidecar.route = Route.UNKNOWN
                sidecar.flags.append("unreadable-encrypted-password-required")
            else:
                sidecar.route = self._classify(reader)
                sidecar.already_tagged = self._is_tagged(reader)
        except (pypdf.errors.DependencyError, pypdf.errors.FileNotDecryptedError):
            # encryption surfacing as an exception instead of is_encrypted: pypdf
            # auto-tries the empty password on open -- AES needs the optional
            # `cryptography` package (DependencyError), and a real password fails
            # outright (FileNotDecryptedError/WrongPassword). Same reject policy.
            sidecar.route = Route.UNKNOWN
            sidecar.flags.append("unreadable-encrypted-password-required")
        except Exception as exc:  # pypdf raises many types on malformed files
            # policy: a corrupt/malformed PDF is rejected with a clean reason on
            # the sidecar, not a stack trace -- the run still reaches the gate.
            sidecar.route = Route.UNKNOWN
            sidecar.flags.append(f"unreadable-corrupt: {str(exc)[:80]}")
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


# NOTE: unreadable handling above intentionally catches broad Exception -- pypdf
# raises many types on hostile input (PdfReadError, DependencyError, ValueError...)
# and ANY of them means the same thing here: this file cannot be classified, so it
# goes to the human gate with an `unreadable-corrupt` reason instead of a crash.
