# Architecture (F1 skeleton) — SUPERSEDED

> **Status: superseded 2026-08-05.** This was the phase-1 sketch, written before
> the service layer, the review clients, the human gate and the in-app editor
> existed. It is kept as a record of the original shape, **not** as a guide.
>
> **Read [`../architecture.md`](../architecture.md) instead.**
>
> Specifically out of date below: the engine examples (`tag: pikepdf` →
> `tag: pdfix`; the real engine is OpenDataLoader, see
> [`pdf-engines.md`](pdf-engines.md)), the OCR references (OCRmyPDF/Ghostscript;
> the real one is Tesseract in hOCR mode), the "cloud/on-prem choice" (there is
> no cloud), the secrets section (no tokens exist in v1 — Canvas was removed),
> and the quickstart commands (use the `python -m` forms; the `.venv` console
> scripts can be blocked by Windows App Control).

The offline core of the SPEADE PDF-remediation pipeline. Runs fully offline
against a local inbox/outbox — there is no remote/API integration.

## The stage contract

Every stage conforms to one fixed shape:

```
(input PDF + sidecar JSON) -> (output PDF + updated sidecar JSON)
```

- **Sidecar** (`speade.pipeline.contract.Sidecar`) — pipeline-internal metadata
  (route, stages applied, flags, source hash). It is **not** a tracker input and
  terminates at the human gate. Stages speak only this neutral contract, never
  each other's tool-native types — that is what keeps every stage swappable.
- **Stage** (`Protocol`) — a swappable step. The runner guarantees the original
  file is never mutated (it copies to the outbox first), so reversibility and the
  do-not-degrade guarantee are structural, not bolted on.

## Config-driven selection

`config.yaml` maps each stage role to an implementation name. Swapping an engine
(e.g. `tag: pikepdf` → `tag: pdfix`) or the cloud/on-prem choice is a **config
edit, not a code edit**. Implementations are registered in
`speade.pipeline.registry`.

## I/O

`speade.io.local.LocalFolderClient` reads source PDFs from a local inbox and
writes remediated copies to an outbox. Local folders are the only document
source; there is no remote/API client.

## Arms-length copyleft rule (load-bearing)

Engine adapters **shell out** to copyleft / CLI tools (veraPDF, OCRmyPDF,
Ghostscript-if-ever) as arms-length subprocesses; we **never `import`** a
GPL/AGPL library in-process. The `scripts/check_banned_imports.py` CI guard
enforces the specific killer case — PyMuPDF (`import fitz`, AGPL-3.0).

## Secrets

Non-secret config → committed `config.yaml`. Secrets (any runtime tokens) →
runtime environment / git-ignored `.env` (see `.env.example`), never the repo.

## Decision records

Design/cost decisions live in `docs/decisions/`. See
[`tagging-cost.md`](tagging-cost.md) — why the `tag` engine defaults to
a zero-new-cost path (Acrobat / opendataloader-free) with PDFix as a paid
candidate-to-beat.

## Quickstart

```bash
uv sync                       # create .venv + install (uses Python 3.13)
uv run speade stages          # list stage implementations
uv run speade run FILE.pdf    # run the configured stages on one PDF (offline)
uv run pytest                 # tests
uv run ruff check . && uv run ruff format --check .
uv run python scripts/check_banned_imports.py
uv run python scripts/check_licenses.py
```
