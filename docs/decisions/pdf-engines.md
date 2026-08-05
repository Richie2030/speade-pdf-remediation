# Decision record: PDF engine selection, per role

- **Status:** Accepted
- **Date:** 2026-07-15
- **Relates to:** D5 (`tagging-cost.md`), every stage under `src/speade/stages/`,
  `src/speade/validation/verapdf.py`, the `pyproject.toml` optional-dependency
  extras, and the v1 freeze (pin versions, archive installers)

## Question

"What PDF library does SPEADE use, and what are the alternatives?"

The question has a false premise worth retiring: **there is no single PDF
library.** SPEADE is a stage pipeline, and each role picks a different tool
against a different constraint. This record inventories what is actually wired
in per role, surveys the alternatives, and records why each incumbent stays.

It ratifies and extends D5, which resolved the `tag` engine only.

## The two distinctions that decide everything

Before the tables, the two axes that determine whether a tool is admissible at
all — both from `CLAUDE.md`'s invariants:

1. **In-process (pip) vs arms-length (subprocess).** Pip dependencies must be
   permissive (MIT/BSD/Apache) or weak-copyleft (MPL-2.0). GPL/AGPL/LGPL tools
   are permitted **only** as a subprocess or container, never `import`ed.
   PyMuPDF (`import fitz`) is banned outright and CI-guarded.
2. **Installed by default vs behind an extra.** The base dependency set is only
   pydantic / pyyaml / typer / python-dotenv. **Every PDF engine sits behind an
   optional extra or a system install** — deliberate, per Aaron's small-stack
   condition.

## What is currently used

| Role | Tool | Kind | Default? | Licence |
|---|---|---|---|---|
| **Tagging** (load-bearing) | **opendataloader-pdf** | Java CLI, subprocess | No — tool install + JRE 11+ | Apache-2.0 (MPL-2.0 pre-v2.0) |
| ↳ prerequisite | Java JRE 11+ (Temurin 21) | System install | No | — |
| ↳ UA-1 finish | **pikepdf** | pip, in-process | No — `tag` / `ocr` extras | MPL-2.0 (bundles Apache-2.0 QPDF) |
| **Detect / routing** | **pypdf** | pip, in-process | Dev checkout only (dev group); **not** in a production install | BSD-3-Clause |
| **OCR** (stretch) | **Tesseract**, called directly | System CLI, subprocess | No | Apache-2.0 |
| ↳ rendering | **pypdfium2** | pip, in-process | No — `ocr` extra | Apache-2.0 OR BSD-3-Clause |
| ↳ merge | pikepdf | pip, in-process | No — `ocr` extra | MPL-2.0 |
| ↳ *undeclared* | **Pillow** | pip, in-process (`.to_pil()`) | Arrives only transitively via pikepdf | MIT-CMU |
| **Validation gate** | **veraPDF CLI** → Docker fallback | Subprocess / container | No | GPL-3.0+ **or** MPL-2.0+ (dual) |
| **Corpus** (dev-only) | pikepdf, pypdf, pypdfium2, Pillow, Tesseract, ODL | Mixed | No | — |

**The load-bearing tool is `opendataloader-pdf`, and it is not a Python library.**
It is the only component that turns an untagged PDF into a Tagged PDF;
everything else supports it.

**pikepdf is not a tagger.** It stamps the four PDF/UA-1 conformance bits the
ODL free tier omits — `/MarkInfo /Marked`, `/Lang`,
`ViewerPreferences/DisplayDocTitle`, and XMP `pdfuaid:part` + `dc:title` —
closing veraPDF clauses 7.1-9, 7.1-10, 7.2-22 and 7.2-34. That ~10-line stamp in
`_finish_pdf_ua` is what substitutes for ODL's **paid Enterprise PDF/UA export**
(still paid as of ODL 2.5.0, 2026-07-14). It is load-bearing, not vestigial.

## Per role: incumbent vs alternatives

### Tagging — `tag` stage

| Candidate | Verdict | Buys | Costs |
|---|---|---|---|
| **opendataloader-pdf (free)** — incumbent | **Keep** | The only open-source end-to-end tagger; by the veraPDF authors (Dual Lab + PDF Association); reuses the JRE veraPDF already needs; D5 measured a zero-failure veraPDF UA-1 pass live | Unpinned; invariant compliance is **conditional** (below) |
| ODL **Enterprise** export | **Unverified — do not buy** | Vendor-guaranteed export; would delete `_finish_pdf_ua` | Quote-only; offline behaviour unconfirmed; if it ships as a proprietary *pip* package it breaks the licence rule |
| PDFix SDK Enterprise | Ruled out | — | D5 resolved against it; its differentiator (AI alt-text, MathML) is LLM-shaped and banned. What remains is what ODL does free |
| Nutrient Document Engine | Ruled out (decision rule, **not** invariant) | Self-hosted container *does* satisfy arms-length + offline; native autotag since v1.15.0 | Quote-only + heavyweight container, for a role a free Apache-2.0 CLI fills at zero cost |
| Docling | Ruled out (redundancy, **not** invariant) | — | Not a tagger — exports Markdown/HTML/JSON, cannot *write* a structure tree |
| PDFBox / iText / pikepdf-as-tagger / Ghostscript | Ruled out | — | None can synthesise a structure tree from layout; adopting any means hand-writing the auto-tagger |

**The incumbent's invariant compliance is conditional, not inherent.** ODL 2.5
ships a **hybrid mode** (routes complex pages to a docling-serve AI backend,
downloads 1–2 GB of weights on first run) and `--enrich-picture-description`
(SmolVLM 256M generates alt-text). Either would violate **no-LLM/VLM**,
**fully-offline**, *and* human-authored-alt-text simultaneously. SPEADE is safe
only because (a) both default off and (b) `tag.py` invokes a frozen
`OPENDATALOADER_CMD` of `--format tagged-pdf` with no hybrid flag.

**This VLM surface arrived between D5's validated version and shipping 2.5.0.**
The freeze must therefore pin **the version *and* the flag set** — there is no
future cohort to catch the drift.

### Detect / routing — `detect` stage

| Candidate | Verdict | Buys | Costs |
|---|---|---|---|
| **pypdf** — incumbent | **Keep** | The only library covering all three sub-roles (text ratio, `/StructTreeRoot`, `is_encrypted`) from one `PdfReader` open; pure Python, no JVM, no C++ blob | Slow text extraction (irrelevant — detect is not the bottleneck) |
| pypdfium2 | Viable, **not recommended** | Native `count_chars()`; `PdfiumError.err_code` gives a precise 3-way error mapping | Its `is_tagged()` wraps `FPDFCatalog_IsTagged`, which reads `/MarkInfo` and **never touches `/StructTreeRoot`** — a silent, untested narrowing (fixtures carry both, so nothing catches it) |
| pikepdf | Viable, **buys nothing** | qpdf-native encryption + corrupt detection | **No text extraction at all** — cannot do born-digital-vs-scanned |
| Poppler `pdfinfo` / `pdffonts` | Viable arms-length, not recommended | `pdfinfo` gives Pages + Encrypted + Tagged in one call | New system package (Windows builds lag), a spawn per file, a stringly-typed stdout contract |
| PDFBox 3 CLI | Viable arms-length, not recommended | Apache-2.0, JRE already required | ~0.5–1 s JVM startup *per file* for a millisecond question |
| pdfminer.six / pdfplumber / pdfrw / borb / docling | Ruled out | — | Redundant, heavier, dead, or AGPL |

**Correction on the record:** the claim that AES-encrypted PDFs are handled "by
accident" because `cryptography` is absent is **false**. `pypdf`'s
`is_encrypted` is `TK.ENCRYPT in self.trailer` — a structural trailer check,
independent of `cryptography` and of whether decryption succeeds. Either way an
AES file reaches `unreadable-encrypted-password-required`, and
`tests/test_detect.py` already pins that contract. The behaviour is
deterministic, not fragile.

### OCR + rendering — `ocr` stage (stretch)

| Candidate | Verdict | Buys | Costs |
|---|---|---|---|
| **Tesseract (direct) + pypdfium2 + pikepdf** — incumbent | **Keep** | `tesseract <img> <stem> pdf` emits a searchable one-page PDF via Tesseract's own renderer — this is what keeps `ocr.py` at ~40 lines. pypdfium2 is the only permissive, self-contained-wheel rasteriser | `find_tesseract()` checks existence only, never `--version`, despite the `>=5` claim |
| OCRmyPDF | Viable arms-length only | Deskew, rotate, clean, jbig2 optimisation; maintained text-layer placement | **Since v17 its own default rasteriser is pypdfium2 and Ghostscript is optional — it now runs SPEADE's exact chain.** A second Python runtime to pin and archive on every lab PC, for optimiser flags |
| RapidOCR / PaddleOCR / docTR / EasyOCR / Kraken | Ruled out | Better accuracy on noisy scans | **All emit boxes+text, not a searchable PDF** — adopting any means hand-writing an invisible-text-layer compositor (glyph positioning, font embedding), the correctness-critical part, on an *optional* stage. Plus torch/ONNX weight downloads against the offline story |
| tesserocr / pytesseract | Ruled out | tesserocr releases the GIL | tesserocr has no official Windows wheels (unpinnable); pytesseract wraps a subprocess to do what `ocr.py` already does in 10 lines |
| Surya | Ruled out | — | **Weights are not open source**: modified AI Pubs Open RAIL-M with a $5M revenue threshold **UCC exceeds**, plus a field-of-use restriction. Also a VLM. (Its *code* is clean Apache-2.0 — not a reason to reject) |
| ABBYY FineReader Engine | Ruled out | Accuracy ceiling; genuinely on-prem Linux | The ready-made **"ABBYY CLI OCR for Linux" hit EOL 2020-03-31**. Adopting it means licensing the SDK *and* maintaining a bespoke C++ CLI wrapper — disqualifying under v1-is-final |

### Validation gate — `validation/verapdf.py`

**veraPDF is correct and irreplaceable.** It is the only free, open-source,
cross-platform, clause-level PDF/UA-1 validator with a scriptable CLI and
machine-readable JSON. Every rival is Windows-GUI-only (PAC, CommonLook), cloud
(Adobe), or paid proprietary (axesPAC, pdfaPilot, PDFix — and PDFix *embeds*
veraPDF). It is also the reference ODL is measured against; swapping it breaks
the one external check that makes the tagger's claims meaningful.

Ruled out here:

- **axesPAC On-Prem** — the only genuine second engine, but its licensing agent
  **requires an internet connection** to `api.axes4.com`, and the documented
  offline Manual Activation path covers only axesWord/axesPDF. A hard offline
  violation, not a price problem; funding does not cure it.
- **PAC 2026** — Windows-GUI-only, no CLI (axes4's own FAQ). But it *is*
  offline-clean (local model, no internet), so it remains a legitimate free
  human cross-check at the gate — a runbook note, not a dependency. Do **not**
  substitute axesCheck: that one uploads to the cloud.
- **PDFBox Preflight** — validates **PDF/A-1b only**. No PDF/UA verdict exists
  to extract at any price.
- **pdfcpu / JHOVE / Arlington** — syntax and well-formedness checkers, a
  different question. Arlington models the ISO 32000-2 *object grammar*;
  PDF/UA-1 is ISO 14289-1 *structure semantics*.
- **avalpdf** — a thin MIT wrapper over the proprietary PDFix SDK;
  heuristic-only (no `{clause}-{testNumber}`), so it cannot satisfy the
  `VeraResult` contract.

### Low-level write / metadata finish

**pikepdf is right and nothing beats it.** MPL-2.0 is precisely the one
weak-copyleft licence `CLAUDE.md` permits in-process, wrapping Apache-2.0 QPDF —
a mature C++ writer with zero copyleft exposure. `open_metadata()` makes
`meta["pdfuaid:part"] = "1"` a one-liner; every rival hand-rolls RDF/XML.

| Candidate | Why it loses |
|---|---|
| pypdf | The only credible swap (already in the closure twice; `XmpInformation.create()` exists) — but its own docs implement pdfuaid by hand-manipulating the RDF DOM, and pure-Python pypdf has no QPDF repair path for malformed ODL output. Its real value is as a *non-silent fallback* |
| qpdf CLI | pikepdf **is** qpdf. No XMP API — you would hand-build pdfuaid RDF and inject a raw stream, for a licence exemption you don't need |
| PDFBox | Its ~14 shipped CLIs write no catalog entries and no XMP. You would author, build, sign and pin a bespoke jar to replace ten lines of Python |
| ExifTool | Writes XMP but **cannot touch the catalog** — `/MarkInfo`, `/Lang`, `/ViewerPreferences` are out of reach, so you still need pikepdf, plus a Perl runtime |
| pypdfium2 | Already installed via `ocr`, so it *looks* free to reuse — but PDFium "does not provide access to the raw PDF data structure" and exposes no dictionary/stream APIs. **Every single thing `_finish_pdf_ua` does is a dictionary or XMP write.** It can do none of them |
| PDFix SDK | Ruled out on the **licence invariant** (an in-process proprietary pip import fails the permissive/MPL rule), plus D5. **Not** on offline or price grounds — PDFix is on-prem by design and has a free Lite tier; both of those kills are refutable |

### Corpus tooling (dev-only, `datasets/`)

**Keep pikepdf + pypdf + pypdfium2 + Pillow.** `build_corpus.py` deliberately
labels fixtures via SPEADE's own `DetectStage._classify/_is_tagged` — that
dogfooding is the design. Swapping in a second parser would make the corpus
assert what *pdfminer* thinks rather than what SPEADE thinks.

**`img2pdf` is not a licence violation.** An earlier reading of this was wrong
and should not be repeated: `CATALOG.md` heads its tool list with "all
arms-length subprocess / permissive", img2pdf ships a real CLI, and an LGPL CLI
as a subprocess is exactly what the invariant permits — identical standing to
veraPDF and qpdf in the same list. It is simply not adopted because Pillow
already covers the need in-process.

## Ruled out — the one-liners

| Expected candidate | Why it loses |
|---|---|
| **PyMuPDF / `import fitz`** | AGPL-3.0 as a pip dep; CI-guarded. No arms-length escape — it is a library with no tagging CLI, so subprocess isolation is not a workaround. Also has no structure-authoring API |
| **Adobe PDF Services / Auto-Tag API** | Cloud-only REST — fails fully-offline outright. *Not* quote-only: ~$0.50/page. A separate product from UCC's Acrobat seats; the seat licence buys no relief |
| **Adobe Acrobat Pro (in-pipeline)** | Over-determined: the EULA bars "automated server processing", **and** there is no Linux build while the deploy target is Linux. Stays a human-in-the-loop desk tool at the gate |
| **AWS Textract / Google Document AI** | Cloud-only, and neither emits a Tagged PDF — layout JSON only |
| **Azure Document Intelligence** | ⚠️ **Not** ruled out by offline — it ships genuinely air-gapped containers. Ruled out by **v1-is-final**: strategic-customer gating and a licence file that "may fail to start the container even if it worked with the previous version" — a time-bombed vendor key inside a frozen artifact |
| **borb** | AGPL-3.0 pip-only library with no CLI — no arms-length form exists. Commercial tier is metered per document: an unrenewable liability on a frozen v1 |
| **iText Core 9.x** | Generates tagged PDFs *from source*; does not auto-tag an existing untagged PDF. No off-the-shelf CLI, so a wrapper would itself be an AGPL derivative |
| **Ghostscript** | Legal arms-length, but pdfwrite **ignores structure pdfmarks and destroys existing tags** (10.x discards the structure tree). Would shred the tags the stage just wrote |
| **Poppler / pdftoppm** | GPL-2.0-or-later; re-introduces the system-renderer install pypdfium2 eliminated, with no gain at 200 DPI. `pdf2image` is ~2 years dormant |
| **CommonLook PDF Validator** | Windows-only Acrobat plugin (inherits every Acrobat bar, plus platform). Its web successor is cloud + AI — more ruled out, not less |
| **ReportLab** | The BSD toolkit **cannot tag** — tagging lives in proprietary `rlextra`, and enabling it without that "will silently continue and create a simple, un-tagged PDF": a fixture generator that lies |
| **pdfrw** | Dead since 2018, Python ≤3.6 declared against a 3.13 pin |

## Decision

1. **Every incumbent stays. Not one swap is warranted.** Each tool is the
   correct choice for its role, and the survey found no candidate that beats one
   without violating an invariant or importing more maintenance than it removes.
2. **v1 is final, so churn is a cost paid once and never recovered.** The
   remaining work in this area is packaging, pinning and honesty — not
   replacement.
3. **Do not buy ODL Enterprise.** D5's answer holds: the free tier + the pikepdf
   finish passes UA-1 with zero failures.
4. **The freeze must pin the ODL flag set, not only the version** — `--hybrid`
   and `--enrich-picture-description` are invariant violations that shipped into
   2.5.0 after D5 validated the engine.

## Consequences

- The pipeline's accessibility outcome rests on a **subprocess + JRE**, not a
  pip dependency. `uv sync` alone cannot produce a tagged PDF, and `uv.lock`
  cannot pin the tool that does the tagging. The archive must carry the ODL
  release, the JRE and Tesseract as artifacts.
- **Arms-length is a live constraint, not a formality.** It is the sole reason
  veraPDF (dual GPL) and Tesseract are admissible, and the sole reason PyMuPDF,
  borb and PDFix are not — even where they are technically better.
- **Two invariants have no automated guard.** Nothing checks that ODL is invoked
  without its AI flags, and `check_licenses.py` is a GPL *denylist*, not the
  permissive *allowlist* its docstring claims — so a proprietary or unlicensed
  dependency passes green. The written invariant is not the one enforced.
- A deployment note that escapes every table: Acrobat's desktop **"Enable
  cloud-based auto-tagging for accessibility"** preference ships documents to
  Adobe's cloud. The ~20-seat Acrobat room is the sanctioned human-correction
  path — but if a reviewer clicks Autotag and accepts the prompt, **student
  coursework leaves the building through the human path, where no invariant
  check is looking.** That preference must be verified off, ideally
  policy-locked, on every machine in that room.

## Claims that remain low-confidence

| Claim | Status |
|---|---|
| ODL Enterprise runs fully offline | **Unverified.** No primary source; a quote-only add-on plausibly requires licence-key activation. Also unknown whether it ships as a CLI/JAR (fine) or a proprietary pip package (breaks the licence rule). The uncertainty strengthens don't-buy |
| Typst `--pdf-standard ua-1` output passes SPEADE's gate | Plausible and vendor-supported ("failures are considered bugs in Typst"), but **not confirmed against our gate**. Spike it the way D5 was spiked before relying on it for fixtures |
| ABBYY FRE 12 pricing / FRE 11 exact EOL | **Medium.** Only stale 2017 pricing is published; `support.abbyy.com` 403s automated fetch. Does not move the verdict — don't quote figures |
| callas pdfaPilot maintenance status | **Medium.** Actively sold, but no primary release date is obtainable; release-note URLs 404 |
| Nutrient self-hosted autotag is fully local | Unconfirmed — "documents never pass through Nutrient's servers" is suggestive but not endpoint-specific. Irrelevant while ruled out |
| pikepdf macOS wheels link GnuTLS (LGPL-2.1+) | Unverified for current wheels. macOS is out of scope (Linux deploy, Windows dev), so it does not bite — but it is a latent in-process LGPL dep if a macOS build is ever cut |
| Adobe EULA clause lettering | Substance corroborated across Adobe's own KB and General Terms, but `adobe.com` refused automated fetch. Re-read the live pages before quoting clause letters in an IT-facing document |

## Sources

- opendataloader-pdf: https://github.com/opendataloader-project/opendataloader-pdf
- veraPDF: https://verapdf.org/ · images: `ghcr.io/verapdf/cli`, `verapdf/cli` (Docker Hub)
- pikepdf (MPL-2.0) / QPDF: https://github.com/pikepdf/pikepdf · tagged-PDF status: https://github.com/pikepdf/pikepdf/issues/461
- pypdf: https://github.com/py-pdf/pypdf · pypdfium2: https://github.com/pypdf/pypdfium2
- Tesseract: https://github.com/tesseract-ocr/tesseract · OCRmyPDF: https://github.com/ocrmypdf/OCRmyPDF
- PDFix SDK: https://pdfix.net/products/pdfix-sdk/ · Adobe auto-tag: https://helpx.adobe.com/acrobat/using/cloud-auto-tagging-accessibility-pdfs.html
- axes4 / PAC: https://pac.pdf-accessibility.org/ · Surya licence: https://github.com/datalab-to/surya
- Prior art in-repo: [`tagging-cost.md`](tagging-cost.md) (D5), [`already-tagged-handling.md`](already-tagged-handling.md)
