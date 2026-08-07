# SPEADE PDF Remediation Pipeline

An on-prem tool for the UCC SPEADE accessibility programme. It takes PDFs and
tags them toward WCAG 2.1 AA / PDF-UA, then hands each one to a person to check
and sign off before it is shared. The automation only produces drafts. A human
decides what actually ships. It's meant to assist the Student Partner
remediation workflow, not replace it.

Everything runs offline against local folders. There's no cloud service, no
Canvas or LMS integration, and no LLM anywhere in the pipeline. v1 handles PDFs
only (docx and pptx are out of scope), and the stack below is fixed for this
release.

## What it does

```
data/inbox → detect (born-digital vs scanned) → [OCR if scanned]
  → tag (structure + PDF/UA tagging) → draft in data/outbox   (awaiting review)
  → human + veraPDF gate → data/outbox/approved/   (ready to share)
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
logic lives. The reviewer sees the tag tree beside the rendered pages with a box
around every tagged region, plus the veraPDF result in plain language, and sets
the document title and reading language.

Most corrections happen **in the app**: change a tag's type, write an image
description, mark content decorative, remove a wrapper tag, move a tag in the
reading order, or drag a rectangle over the page to merge, retag, carve out or
newly tag a whole selection. Every edit is re-checked by veraPDF, recorded in
the audit log, and undoable one step at a time. Acrobat remains for what the app
deliberately doesn't do (`docs/limitations.md`), and a rejected draft lands in
`rejected/` for that work. Approving re-runs veraPDF on the current bytes,
records the machine result (the reviewer still has the final say), writes an
audit entry, and moves the PDF into `approved/`.

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
scripts/         # guards (banned imports, licences) + setup-machine.ps1
docs/            # the handover documentation set (see below)
```

## Documentation — start here

v1 is frozen and no cohort continues it, so the docs are the handover. Find your
audience:

| You are | Read |
|---|---|
| a **reviewer** using the app | [`docs/user-guide.md`](docs/user-guide.md), the printable [`quick-reference.md`](docs/quick-reference.md), and [`acrobat-guide.md`](docs/acrobat-guide.md) for the corrections the app leaves to Acrobat |
| **IT**, deploying it | [`docs/it-support-request.md`](docs/it-support-request.md) — the one-page ask (start here), then [`deployment.md`](docs/deployment.md) (signing, install, admin oversight), [`runbook.md`](docs/runbook.md) (machine setup — `scripts/setup-machine.ps1` automates it), [`security-and-data.md`](docs/security-and-data.md), [`maintainability.md`](docs/maintainability.md) |
| **leadership**, wanting the shape of it | [`docs/overview.md`](docs/overview.md), [`limitations.md`](docs/limitations.md), and [`pilot-report.md`](docs/pilot-report.md) — the evaluation template (fill after a pilot run) + live demo script |
| a **developer** picking this up cold | [`docs/architecture.md`](docs/architecture.md) first, then [`tech-stack.md`](docs/tech-stack.md) for pinned versions and [`docs/decisions/`](docs/decisions/) for why things are the way they are |
| **testing** the whole app before sign-off | [`docs/laptop-quickstart.md`](docs/laptop-quickstart.md) to get it running on a fresh/university machine, then [`docs/self-test-checklist.md`](docs/self-test-checklist.md) — a full manual QA pass, every feature and edge case with its exact pass condition |

Two references worth knowing exist: [`verapdf-clauses.md`](docs/verapdf-clauses.md)
decodes every PDF/UA rule code the app can show you, and
[`screen-reader-check.md`](docs/screen-reader-check.md) is the NVDA spot-check
no software can do for you.

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
