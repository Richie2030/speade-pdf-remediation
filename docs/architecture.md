# Architecture (F1 skeleton)

The offline core of the SPEADE PDF-remediation pipeline. Built and proven
**without Canvas API access** — Canvas/Ally integration is the last step, swapped
in behind the same interface once tokens land.

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

## I/O adapter

`speade.io.base.DocumentClient` is the document-source seam. Today:
`LocalFolderClient` (inbox → outbox). When tokens land: a `CanvasClient` with the
same interface — no pipeline change.

## Arms-length copyleft rule (load-bearing)

Engine adapters **shell out** to copyleft / CLI tools (veraPDF, OCRmyPDF,
Ghostscript-if-ever) as arms-length subprocesses; we **never `import`** a
GPL/AGPL library in-process. The `scripts/check_banned_imports.py` CI guard
enforces the specific killer case — PyMuPDF (`import fitz`, AGPL-3.0).

## Secrets

Non-secret config → committed `config.yaml`. Secrets (Canvas/Ally tokens) →
runtime environment / git-ignored `.env` (see `.env.example`), never the repo.

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
