# SPEADE PDF Remediation Pipeline

An on-prem tool for the UCC SPEADE accessibility programme. It takes PDFs and
tags them toward WCAG 2.1 AA / PDF-UA, then hands each one to a person to check
and sign off before it goes back up to Canvas. The automation only produces
drafts. A human decides what actually gets uploaded. It's meant to assist the
Student Partner remediation workflow, not replace it.

Everything runs offline against local folders. There's no cloud service, no
Canvas or LMS integration, and no LLM anywhere in the pipeline. v1 handles PDFs
only (docx and pptx are out of scope), and the stack below is fixed for this
release.

## What it does

```
data/inbox → detect (born-digital vs scanned) → [OCR if scanned]
  → tag (structure + PDF/UA tagging) → draft in data/outbox   (awaiting review)
  → human + veraPDF gate → data/outbox/approved/   (ready to upload back to Canvas)
                         → data/outbox/rejected/   (needs manual rework)
```

Each stage takes a PDF and its sidecar JSON and returns an updated PDF and
sidecar. Stages are picked by name in `config.yaml`, so swapping a tool is a
config edit rather than a code change. The input PDF is never touched; work
happens on a copy. Sidecars live in `data/sidecars/` since they're internal
records and the outbox is kept for deliverables. Every run, metadata edit, and
gate decision gets appended to an audit log at `data/audit/audit.jsonl`, each
entry carrying a SHA-256 fingerprint.

Some things are fixed by design:

- Local folders are the only source of documents. Nothing reaches the network.
- No LLM or VLM, cloud or local. Figure alt-text and the document title and
  language are written by the reviewer, not generated.
- Nothing gets approved without a named reviewer. On approval, the exact bytes
  being signed off are re-hashed and re-checked by veraPDF.
- External tools (OpenDataLoader, Tesseract, veraPDF) run as subprocesses and
  are never imported in-process. CI enforces that, along with the licence rules.

## Tech stack

| Role | Tool |
|---|---|
| Language | Python 3.13 (`uv`, with `uv.lock` committed) |
| Document I/O | local folders (`data/inbox` → `data/outbox`) |
| Detect born-digital vs scanned | pypdf |
| OCR (scanned only) | Tesseract 5 in hOCR mode, with pypdfium2 rendering and pikepdf assembly |
| Structure + tagging | OpenDataLoader PDF (Java CLI) plus a pikepdf PDF/UA-1 finish |
| Alt-text | written by the reviewer at the gate |
| Validation | veraPDF (PDF/UA-1) |

## Quickstart

You'll need [`uv`](https://docs.astral.sh/uv/). The tag stage also wants a
Java 11+ JRE and the `opendataloader-pdf` CLI (`uv tool install
opendataloader-pdf`), and validation needs veraPDF. Tesseract 5 is only
required if you're processing scanned PDFs.

```bash
uv sync --all-extras

uv run python -m speade stages             # show the configured pipeline
uv run python -m speade run FILE.pdf       # remediate one PDF into data/outbox
uv run python -m speade run-batch          # sweep data/inbox (or run-batch FOLDER)

uv run python -m speade.desktop            # the review window
uv run python -m speade.web                # the review UI at http://127.0.0.1:8765

uv run python -m speade verify data/outbox/FILE.pdf --reviewer "Name" --approve
```

On Windows machines with App Control, run the tools as modules
(`uv run python -m pytest`, `uv run python -m speade`), because the
`.venv\Scripts\*.exe` launchers can be blocked. You don't activate the venv by
hand; `uv run` takes care of it.

## The review gate

There are two review clients that share one UI (`src/speade/desktop/ui/`, one
folder with two launchers):

- `python -m speade.desktop` opens a pywebview window over a js_api bridge.
- `python -m speade.web` serves the same UI from FastAPI, bound to 127.0.0.1
  only. No hosting, no auth, and it never leaves the machine.

Both go through the same engine, `src/speade/service.py`, where all the gate
logic lives. In the client the reviewer previews the draft, looks at its tag
structure and the veraPDF result, and edits the document title and reading
language. Anything deeper, like writing figure alt-text or fixing the tag tree,
happens in Acrobat through the "Open in viewer" hand-off, and a rejected draft
lands in `rejected/` for that manual work. Approving re-runs veraPDF on the
current bytes, records the machine result (the reviewer still has the final
say), writes an audit entry, and moves the PDF from the outbox root into
`approved/`.

## Repository layout

```
src/speade/
  service.py     # the engine every client calls; gate logic lives here
  pipeline/      # stage contract, registry, runner (runs on a copy)
  stages/        # noop, detect, tag, ocr
  validation/    # veraPDF scorer (subprocess)
  audit/         # append-only JSONL + SHA-256 trail
  desktop/       # pywebview client and the shared ui/ folder
  web/           # 127.0.0.1-only FastAPI serving of the same ui/
config.yaml      # stage selection and folder layout
tests/           # pytest suite
scripts/         # the banned-imports and licence guards
```

## Tests, lint, CI

```bash
uv run python -m pytest
uv run ruff check .
uv run python scripts/check_banned_imports.py   # no in-process AGPL (fitz/PyMuPDF)
uv run python scripts/check_licenses.py         # no GPL-family pip dependencies
```

CI runs on every push to `main`: ruff, both guards, pytest, a CycloneDX SBOM
(`bom.json`), and a gitleaks scan over the full history.

## Platform

Windows is the primary target. The shipped artifact is a PyInstaller build
(`speade-desktop.spec` produces `dist/speade-desktop/speade-desktop.exe`) that
runs on the UCC lab PCs. The code stays portable for Linux parity: `pathlib`
paths, lowercase filenames, and LF endings via `.gitattributes`. macOS isn't
supported.

## Licence

Apache-2.0 (see `LICENSE` and `NOTICE`). In-process dependencies have to be
permissive or weak-copyleft (MPL, which is why pikepdf is fine). GPL, AGPL, and
LGPL tools are only allowed as arms-length subprocesses, never as pip
dependencies. CI checks both.
