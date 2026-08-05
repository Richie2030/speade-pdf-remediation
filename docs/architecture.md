# Architecture — how SPEADE fits together

The design of the shipped v1, written for whoever picks this up after the
project ends. It describes what the code **is**, not what was once planned; the
reasoning behind each choice lives in `decisions/`, and the exact tool versions
in `tech-stack.md`.

One sentence: **a config-driven pipeline turns a PDF into a tagged draft, and a
human decides whether that draft ships.** Everything else is detail in service
of those two clauses.

## The shape

```
data/inbox/  (your originals — never modified)
     |
     v
  detect  ──► born-digital | scanned | unknown          ─┐
     |                                                   │ each stage:
  [ocr]   ──► scanned only: Tesseract hOCR text layer    │ (PDF + sidecar)
     |                                                   │      ->
   tag    ──► OpenDataLoader structure + PDF/UA finish   │ (PDF + sidecar)
     |                                                  ─┘
     v
data/outbox/  draft + veraPDF verdict            data/sidecars/  (one record per doc)
     |                                           data/audit/     (append-only JSONL)
     v
  THE HUMAN GATE — review, correct in-app, approve or reject
     |
     ├──► outbox/approved/   ready to share
     └──► outbox/rejected/   needs work (Acrobat), then re-approve
```

Nothing in that diagram touches a network. There is no server, no database, no
queue, and no cloud service: the "state" is files on one PC.

## The four layers

### 1. Core pipeline (`pipeline/`, `stages/`)

Every stage implements one contract:

```
(input PDF + sidecar) -> (output PDF + updated sidecar)
```

- **`contract.py`** — `Sidecar` (route, stages applied, flags, hashes, gate
  verdict, approval), `StageResult`, `Route`, and the `Stage` protocol. Stages
  speak only this neutral shape, never each other's tool-native types. That is
  what makes any stage swappable.
- **`registry.py`** — maps an implementation name from `config.yaml` to a stage
  object. Selecting or replacing an engine is a **config edit, not a code edit**.
- **`runner.py`** — runs the stages **on a copy**. The inbox original is never
  mutated, so reversibility is structural rather than a promise.
- **`stages/`** — `detect` (born-digital vs scanned, via pypdf), `ocr`
  (Tesseract in hOCR mode → an invisible line-level text layer assembled with
  pikepdf), `tag` (OpenDataLoader + a pikepdf PDF/UA-1 finish), `noop`.

A stage that cannot do its job **flags the document and passes it on** rather
than failing the batch — a missing Tesseract yields `ocr-unavailable`, a missing
or blocked tagging engine yields `tag-unavailable`. One bad document never costs
you the other ninety-nine, and nothing silently pretends to have succeeded.

### 2. Service layer (`service.py`) — the engine every client calls

`run_one`, `run_batch`, `list_queue`, `decide`, the in-app editing operations,
the undo stack, and history export all live here. **The gate logic exists in
exactly one place**, so it cannot drift between the CLI, the desktop window and
the browser client. Clients are thin shells; if you are tempted to put logic in
one, it belongs here instead.

Path handling matters for deployment: `workspace()` resolves the data folders
relative to **the config file's own directory**, never the process working
directory, so a copied install behaves identically wherever it is launched.

### 3. Clients — three front doors, one engine

| Client | Entry point | Notes |
|---|---|---|
| CLI | `python -m speade` | Typer app; batch and scripted use |
| Desktop | `python -m speade.desktop` | pywebview window over a `js_api` bridge; the shipped `.exe` |
| Web | `python -m speade.web` | FastAPI bound to **127.0.0.1 only** — no hosting, no auth |

The desktop and web clients share **one** UI folder (`desktop/ui/`). The single
seam between them is `api.js`: the desktop copy calls the pywebview bridge, the
web copy calls `fetch`. Never fork the UI — one `ui/`, two launchers.

### 4. Trust trail (`audit/`, `validation/`)

- **`audit/log.py`** — append-only JSONL, one line per run, edit and decision,
  each carrying SHA-256 fingerprints. This is the project's evidence that every
  shipped document was human-approved.
- **`validation/verapdf.py`** — shells out to the veraPDF CLI for the PDF/UA-1
  verdict. It **fails closed**: if veraPDF is missing, blocked or times out, the
  document is marked unverified, never silently passed.
- **`validation/structure.py`** — reads the tag tree with pikepdf and its page
  geometry with pdfium, and implements every in-app edit. This is the largest
  module and the one to read first if you are changing editing behaviour.

## The invariants (break these and the product stops being trustworthy)

1. **Arms-length copyleft.** Copyleft and non-Python engines (veraPDF,
   OpenDataLoader, Tesseract) run as **subprocesses, never imported**. pikepdf
   (MPL) is the one permitted in-process PDF library. `import fitz` (PyMuPDF,
   AGPL) is banned outright and CI enforces it.
2. **Never mutate the input.** All work happens on a copy.
3. **The human gate is mandatory.** Automation produces drafts. There is no
   auto-approve, and the machine verdict is advisory to the person deciding.
4. **Fail closed, never silently pass.** An unavailable checker is an
   unverified document, not a passing one.
5. **Offline.** No network calls anywhere in the pipeline. No LLM or VLM: figure
   descriptions are written by the reviewer.
6. **Tools are discovered by `shutil.which`**, never invoked by bare name — a
   `.cmd`/`.bat` launcher must work (veraPDF ships as `verapdf.bat`, and a
   `.cmd` shim is the way past an App-Control-blocked `.exe`).

## In-app editing (what the reviewer can fix without Acrobat)

All of it is implemented in `validation/structure.py`, routed through
`service.py` so each edit is atomic, re-checked by veraPDF, audit-logged, and
undoable:

- change a tag's type; write an image description;
- mark content decorative (it leaves the reading order and its page content is
  re-marked as background — the true inverse operation is "Tag untagged", so
  this is reversible);
- remove a wrapper tag while keeping its contents;
- move a tag earlier/later in the reading order;
- drag a rectangle on the page to select many tags and merge / retag / decorate
  them, carve chosen **lines** out into a new tag, or tag content that has no
  tag at all;
- step back one edit at a time (whole-file snapshots, 20 deep), or discard
  everything by reprocessing the untouched original.

Two mechanisms make this safe and are worth understanding before changing it:
`StructureNode.id` is a **pre-order position** that must be assigned identically
by `structure_tree` and `_walk_elements` (pinned by a test — if they drift,
edits land on the wrong tag), and the PDF's **parent tree** (the content→tag
lookup) must be maintained whenever elements are merged or removed, or the file
looks right while mapping wrong.

## Configuration

`config.yaml` (beside the executable in a deployed install) selects the stage
implementations and the folder layout. Secrets are not part of this product —
there are no tokens or credentials anywhere, because there is nothing to
authenticate to.

## Deliberately absent

No Canvas/LMS integration, no Ally, no cloud, no LLM, no database, no Docker
requirement (veraPDF can use a Docker image if one happens to exist, but a local
Java install is the supported path), and no auto-approval. Several of these were
once in scope and were removed; see `decisions/` for the trail.

## Where to look next

| Question | File |
|---|---|
| Exact tool versions and licences | `tech-stack.md` |
| Setting up a machine | `runbook.md` |
| Shipping it to UCC | `deployment.md` |
| What it deliberately cannot do | `limitations.md` |
| Why an engine was chosen | `decisions/pdf-engines.md` |
| Why two clients exist | `decisions/frontend-delivery.md` |
