"""OCR stage (Stage 3, STRETCH) -- add a searchable text layer to a scanned PDF
before tagging.

Engine: Tesseract invoked at arms length (rule L2), but in **hOCR mode**, not its
own PDF renderer. Pages are rendered with pypdfium2, Tesseract reports the line
boxes (`tesseract <img> <stem> hocr`), and the output page is assembled here with
pikepdf: the scan image drawn as an /Artifact (background, not content) plus ONE
invisible text run per recognised line, words joined by real spaces.

Why not Tesseract's `pdf` output (the earlier design)? Its layer emits each WORD
as a separately positioned run in a metrics-less glyphless font. The downstream
tagging engine (OpenDataLoader) clusters text by geometry, and that shape breaks
it two ways, measured on the live test corpus (block-2 spike, 2026-07-16):

  - paragraphs shattered into one tag per line/word (28 P on a one-page story);
  - extracted text lost its word boundaries ("AminhaamigaRita...").

The line-level layer built here, with per-page font-size quantisation (OCR jitter
otherwise reads as "different styles" and blocks paragraph merging), tags the
same page into its real structure (6 P + real H1/H2 headings) -- and PASSES
veraPDF UA-1 where the old layer FAILED (clause 7.21.7-1, a glyphless-font
defect). The invisible text (render mode 3) uses non-embedded Helvetica, which
UA-1 permits: the embedding requirement applies to *rendered* text only.
Rotated lines (hOCR `textangle`) are dropped -- vertical margin text OCRs to
gibberish fragments.

OCR is optional (stretch), so a missing engine or a failed run never crashes a
batch: the problem is flagged on the sidecar and the doc flows on -- tag will
skip it (needs-ocr) and the human gate sees exactly why.

Deps: `uv sync --extra ocr` (pypdfium2 + pikepdf, whose Pillow renders/encodes
the page images) plus a system Tesseract >=5.
"""

from __future__ import annotations

import re
import shutil
import statistics
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from speade import subproc
from speade.pipeline.contract import Route, Sidecar, StageResult

# Default Windows installer location, probed when tesseract isn't on PATH (the
# UCC lab-PC case: machine-wide install, per-user PATH untouched). Harmless
# elsewhere -- on Linux/macOS PATH is the only lookup that matters.
_WIN_TESSERACT = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")

_DPI = 200  # render resolution: the OCR-quality vs speed/size balance point
_JPEG_QUALITY = 80  # page-image size vs legibility for the human reviewer
_PAGE_TIMEOUT_S = 300  # per page; a stuck engine must not hang a batch forever
_SCALE = 72.0 / _DPI  # hOCR pixel coordinates -> PDF points

# a line >= this multiple of the page's median size is a real heading and keeps
# its measured size; everything below is snapped to the median (see _quantize).
_HEADING_RATIO = 1.25
# ...but only when the line also LOOKS like a heading: confidently recognised
# and not a full sentence (noisy scans otherwise promote mid-paragraph lines
# with inflated x_size to fake Heading tags -- live-testing finding).
_HEADING_MIN_CONF = 65
_HEADING_MAX_WORDS = 20  # textbook instruction headings run long; 20 words still
#                        # excludes runaway body lines promoted by size noise

# Tesseract reports a 0-100 confidence per word (hOCR x_wconf). Measured on the
# corpus scans: pure noise (stains, ruled lines read as "ae ee ee") sits below
# 20, real-but-faint words go as low as ~38. So: drop only near-certain-noise
# WORDS, then drop LINES whose surviving words still average below 40 -- the
# fragments that were polluting the tag tree as junk paragraphs.
_WORD_MIN_CONF = 20
_LINE_MIN_CONF = 40

# Standard Helvetica advance widths (AFM, per mille of the font size). The
# invisible layer is stretched so each line's text spans EXACTLY the printed
# line's measured box -- an estimated average width leaves the run short and
# the last words of a line untagged (live-testing finding). Characters outside
# the table fall back to their unaccented base letter, then to 556 (avg glyph).
_HELVETICA_WIDTHS = {
    " ": 278,
    "!": 278,
    '"': 355,
    "#": 556,
    "$": 556,
    "%": 889,
    "&": 667,
    "'": 191,
    "(": 333,
    ")": 333,
    "*": 389,
    "+": 584,
    ",": 278,
    "-": 333,
    ".": 278,
    "/": 278,
    ":": 278,
    ";": 278,
    "<": 584,
    "=": 584,
    ">": 584,
    "?": 556,
    "@": 1015,
    "[": 278,
    "\\": 278,
    "]": 278,
    "^": 469,
    "_": 556,
    "`": 333,
    "{": 334,
    "|": 260,
    "}": 334,
    "~": 584,
    **dict.fromkeys("0123456789", 556),
    "A": 667,
    "B": 667,
    "C": 722,
    "D": 722,
    "E": 667,
    "F": 611,
    "G": 778,
    "H": 722,
    "I": 278,
    "J": 500,
    "K": 667,
    "L": 556,
    "M": 833,
    "N": 722,
    "O": 778,
    "P": 667,
    "Q": 778,
    "R": 722,
    "S": 667,
    "T": 611,
    "U": 722,
    "V": 667,
    "W": 944,
    "X": 667,
    "Y": 667,
    "Z": 611,
    "a": 556,
    "b": 556,
    "c": 500,
    "d": 556,
    "e": 556,
    "f": 278,
    "g": 556,
    "h": 556,
    "i": 222,
    "j": 222,
    "k": 500,
    "l": 222,
    "m": 833,
    "n": 556,
    "o": 556,
    "p": 556,
    "q": 556,
    "r": 333,
    "s": 500,
    "t": 278,
    "u": 556,
    "v": 500,
    "w": 722,
    "x": 500,
    "y": 500,
    "z": 500,
}

_BBOX_RE = re.compile(r"bbox (\d+) (\d+) (\d+) (\d+)")
_XSIZE_RE = re.compile(r"x_size ([\d.]+)")
_BASELINE_RE = re.compile(r"baseline ([-\d.]+) ([-\d.]+)")
_ANGLE_RE = re.compile(r"textangle ([-\d.]+)")
_WCONF_RE = re.compile(r"x_wconf (\d+)")


def find_tesseract() -> str | None:
    """Locate the Tesseract executable: PATH first, then the Windows default dir."""
    return shutil.which("tesseract") or (str(_WIN_TESSERACT) if _WIN_TESSERACT.exists() else None)


@dataclass
class OcrLine:
    """One recognised text line: its words joined by spaces + page-pixel geometry."""

    text: str
    x0: int
    y0: int
    x1: int
    y1: int
    size: float  # font size in page pixels (hOCR x_size)
    baseline_dy: float  # baseline offset from the box bottom (usually negative)
    conf: float = 100.0  # mean Tesseract confidence of the surviving words


def parse_hocr(hocr: str) -> list[OcrLine]:
    """Collect the text lines of one hOCR page (Tesseract quotes line titles with
    double quotes and word titles with single quotes -- a real HTML parse, not a
    regex, keeps both working). Rotated lines (`textangle`) are dropped."""
    from html.parser import HTMLParser

    lines: list[OcrLine] = []

    class Collector(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.line: dict | None = None  # the ocr_line being collected
            self.depth = 0  # span nesting below the current line
            self.in_word = False

        def handle_starttag(self, tag: str, attrs) -> None:
            if tag != "span":
                return
            a = dict(attrs)
            cls, title = a.get("class", ""), a.get("title", "")
            if cls in ("ocr_line", "ocr_header", "ocr_textfloat", "ocr_caption"):
                bbox = _BBOX_RE.search(title)
                if bbox is None or _ANGLE_RE.search(title):
                    self.line = None  # unusable or rotated: skip this line
                    return
                x0, y0, x1, y1 = map(int, bbox.groups())
                xs = _XSIZE_RE.search(title)
                base = _BASELINE_RE.search(title)
                self.line = {
                    "parts": [],
                    "confs": [],
                    "x0": x0,
                    "y0": y0,
                    "x1": x1,
                    "y1": y1,
                    "size": float(xs.group(1)) if xs else (y1 - y0) * 0.75,
                    "baseline_dy": float(base.group(2)) if base else 0.0,
                }
                self.depth = 0
            elif self.line is not None:
                self.depth += 1
                if cls == "ocrx_word":
                    self.in_word = True
                    self.line["parts"].append("")
                    conf = _WCONF_RE.search(title)
                    self.line["confs"].append(int(conf.group(1)) if conf else 100)

        def handle_endtag(self, tag: str) -> None:
            if tag != "span" or self.line is None:
                return
            if self.depth > 0:
                self.depth -= 1
                self.in_word = False
            else:  # the line span itself closed: filter noise, then keep it
                words = [
                    (p.strip(), c)
                    for p, c in zip(self.line["parts"], self.line["confs"], strict=False)
                    if p.strip() and c >= _WORD_MIN_CONF
                ]
                text = " ".join(w for w, _ in words)
                conf = statistics.mean(c for _, c in words) if words else 0.0
                keep = bool(text) and conf >= _LINE_MIN_CONF and any(ch.isalnum() for ch in text)
                if keep:
                    parts = self.line
                    lines.append(
                        OcrLine(
                            text=text,
                            x0=parts["x0"],
                            y0=parts["y0"],
                            x1=parts["x1"],
                            y1=parts["y1"],
                            size=parts["size"],
                            baseline_dy=parts["baseline_dy"],
                            conf=conf,
                        )
                    )
                self.line = None

        def handle_data(self, data: str) -> None:
            if self.line is not None and self.in_word:
                self.line["parts"][-1] += data

    Collector().feed(hocr)
    return quantize_sizes(lines)


def quantize_sizes(lines: list[OcrLine]) -> list[OcrLine]:
    """Snap body-line font sizes to the page median. OCR x_size jitters line to
    line; the tagging engine reads that as 'different styles' and refuses to
    merge lines into paragraphs (and promotes slightly-larger lines to fake
    headings). Genuinely larger lines -- real headings -- keep their size, but
    ONLY when they also look like headings: confidently recognised and not a
    full sentence (noisy scans inflate x_size on mid-paragraph lines)."""
    if not lines:
        return lines
    median = statistics.median(line.size for line in lines)
    for line in lines:
        looks_like_heading = (
            line.size >= _HEADING_RATIO * median
            and line.conf >= _HEADING_MIN_CONF
            and len(line.text.split()) <= _HEADING_MAX_WORDS
        )
        if not looks_like_heading:
            line.size = median
    return lines


def text_width(text: str, size_pt: float) -> float:
    """The natural (unstretched) Helvetica width of `text` in points."""

    def char_width(ch: str) -> int:
        w = _HELVETICA_WIDTHS.get(ch)
        if w is None:  # accented letter: measure its unaccented base
            base = unicodedata.normalize("NFD", ch)[:1]
            w = _HELVETICA_WIDTHS.get(base, 556)
        return w

    return sum(char_width(ch) for ch in text) / 1000.0 * size_pt


def _escape(text: str) -> bytes:
    """A PDF literal string: escape delimiters; Helvetica is Latin-1 territory,
    so anything outside it degrades to '?' rather than corrupting the layer."""
    escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return escaped.encode("latin-1", errors="replace")


def page_content(lines: list[OcrLine], w_px: int, h_px: int) -> bytes:
    """The content stream for one OCR'd page: the scan image as an /Artifact
    (decorative background -- NOT document content, so the tagging engine has no
    text-bearing Figure to wrap), then one invisible (render mode 3) text run
    per line at its measured baseline, stretched to the line's box width."""
    w_pt, h_pt = w_px * _SCALE, h_px * _SCALE
    ops = [
        b"/Artifact BMC",
        b"q",
        f"{w_pt:.2f} 0 0 {h_pt:.2f} 0 0 cm".encode(),
        b"/Im0 Do",
        b"Q",
        b"EMC",
        b"BT",
        b"3 Tr",
    ]
    for line in lines:
        size_pt = max(line.size * _SCALE, 4.0)
        x_pt = line.x0 * _SCALE
        # hOCR y1 is the box bottom in top-left pixel coords; baseline_dy shifts
        # from there to the true baseline. Flip into PDF bottom-left points.
        y_pt = (h_px - (line.y1 + line.baseline_dy)) * _SCALE
        # stretch the run across the measured line width -- with REAL Helvetica
        # metrics, so the tagged region reaches the line's actual right edge.
        natural = max(text_width(line.text, size_pt), 1.0)
        target = (line.x1 - line.x0) * _SCALE
        tz = max(min(target / natural * 100.0, 400.0), 20.0)
        ops.append(f"/F1 {size_pt:.2f} Tf".encode())
        ops.append(f"{tz:.1f} Tz".encode())
        ops.append(f"1 0 0 1 {x_pt:.2f} {y_pt:.2f} Tm".encode())
        ops.append(b"(" + _escape(line.text) + b") Tj")
    ops.append(b"ET")
    return b"\n".join(ops)


def add_page(pdf, image, lines: list[OcrLine]) -> None:
    """Append one page to `pdf` (a pikepdf.Pdf): `image` (a PIL image of the
    scan) as a JPEG XObject plus the invisible text layer for `lines`."""
    import io

    import pikepdf

    w_px, h_px = image.width, image.height
    page = pdf.add_blank_page(page_size=(w_px * _SCALE, h_px * _SCALE))

    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=_JPEG_QUALITY)
    xobj = pikepdf.Stream(pdf, buf.getvalue())
    xobj.Type = pikepdf.Name.XObject
    xobj.Subtype = pikepdf.Name.Image
    xobj.Width, xobj.Height = w_px, h_px
    xobj.ColorSpace = pikepdf.Name.DeviceRGB
    xobj.BitsPerComponent = 8
    xobj.Filter = pikepdf.Name.DCTDecode

    page.Resources = pikepdf.Dictionary(
        XObject=pikepdf.Dictionary(Im0=xobj),
        Font=pikepdf.Dictionary(
            F1=pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type1,
                BaseFont=pikepdf.Name.Helvetica,
            )
        ),
    )
    page.Contents = pdf.make_stream(page_content(lines, w_px, h_px))


class OcrStage:
    """Add an OCR text layer to scanned (image-only) PDFs via Tesseract.

    Writes a NEW `<stem>.ocr.pdf`, never edits its input in place. On success the
    route flips to BORN_DIGITAL so the tag stage proceeds downstream. Docs that
    already have text (born-digital, and P1.4's tagged-anyway mixed docs) pass
    through untouched -- rasterising real text would degrade it (do-not-degrade).
    """

    name = "ocr"

    def run(self, pdf: Path, sidecar: Sidecar) -> StageResult:
        # unreadable inputs (encrypted / corrupt -- flagged by detect) cannot be
        # rendered, let alone OCR'd; they flow to the gate with the reason set.
        if any(flag.startswith("unreadable-") for flag in sidecar.flags):
            sidecar.flags.append("ocr-skipped-unreadable")
            sidecar.applied(self.name)
            return StageResult(stage=self.name, output=pdf, sidecar=sidecar, changed=False)

        # only image-only docs need OCR. BORN_DIGITAL already has real text, and
        # UNKNOWN (mixed) keeps its true text pages un-degraded -- it is tagged
        # as-is with a reviewer flag (see TagStage). Expected, so no flag noise.
        if sidecar.route != Route.SCANNED:
            sidecar.applied(self.name)
            return StageResult(stage=self.name, output=pdf, sidecar=sidecar, changed=False)

        problem = self._missing_dependency()
        if problem:
            sidecar.flags.append("ocr-unavailable")
            sidecar.applied(self.name)
            return StageResult(
                stage=self.name,
                output=pdf,
                sidecar=sidecar,
                changed=False,
                notes=[problem],
            )

        # NEW file, never in-place: keep the pre-OCR working copy intact
        # (reversibility / do-not-degrade), exactly like the tag stage.
        out = pdf.with_name(f"{pdf.stem}.ocr.pdf")
        try:
            self._ocr(pdf, out)
        except subprocess.TimeoutExpired:
            sidecar.flags.append("ocr-timeout")
            sidecar.applied(self.name)
            return StageResult(
                stage=self.name,
                output=pdf,
                sidecar=sidecar,
                changed=False,
                notes=[f"OCR timed out (> {_PAGE_TIMEOUT_S}s on one page); routed to human"],
            )
        except Exception as exc:  # engine/render/merge failure: flag, never crash the batch
            sidecar.flags.append("ocr-failed")
            sidecar.applied(self.name)
            return StageResult(
                stage=self.name,
                output=pdf,
                sidecar=sidecar,
                changed=False,
                notes=[f"OCR failed: {str(exc)[:200]}"],
            )

        # Success: the doc now carries a real text layer, so it can be tagged like
        # a born-digital PDF. Re-route so the tag stage proceeds instead of skipping.
        # ocr_layered tells tag that every page is OUR image+text build, so the
        # page scans are background -- not content to wrap in a Figure.
        sidecar.route = Route.BORN_DIGITAL
        sidecar.ocr_layered = True
        sidecar.applied(self.name)
        return StageResult(
            stage=self.name,
            output=out,
            sidecar=sidecar,
            changed=True,
            notes=["OCR text layer added"],
        )

    @staticmethod
    def _missing_dependency() -> str | None:
        """Name the missing piece of the OCR toolchain, or None when all present."""
        if find_tesseract() is None:
            return "tesseract not found (PATH or default install dir); install Tesseract >=5"
        try:
            import pikepdf  # noqa: F401
            import pypdfium2  # noqa: F401
        except ImportError as exc:
            return f"missing python package {exc.name!r}; run `uv sync --extra ocr`"
        return None

    def _ocr(self, pdf: Path, out: Path) -> None:
        """Render each page (~200 DPI, pypdfium2), OCR it with `tesseract <img>
        <stem> hocr`, and assemble the output page here: artifact background
        image + a line-level invisible text layer (see the module docstring)."""
        import pikepdf
        import pypdfium2 as pdfium

        tesseract = find_tesseract()
        merged = pikepdf.Pdf.new()
        with tempfile.TemporaryDirectory(prefix="speade_ocr_") as tmp:
            tmpd = Path(tmp)
            doc = pdfium.PdfDocument(str(pdf))
            try:
                for i in range(len(doc)):
                    png = tmpd / f"pg_{i:04d}.png"
                    image = doc[i].render(scale=_DPI / 72).to_pil()
                    image.save(png)
                    stem = tmpd / f"pg_{i:04d}"
                    proc = subproc.run(
                        [tesseract, str(png), str(stem), "hocr"],
                        capture_output=True,
                        text=True,
                        timeout=_PAGE_TIMEOUT_S,
                    )
                    if proc.returncode != 0:
                        raise RuntimeError(
                            f"tesseract failed on page {i + 1} "
                            f"(exit {proc.returncode}): {proc.stderr[:200]}"
                        )
                    hocr = stem.with_suffix(".hocr").read_text(encoding="utf-8")
                    add_page(merged, image, parse_hocr(hocr))
            finally:
                doc.close()
            merged.save(out)
