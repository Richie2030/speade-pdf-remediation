# Decision record: handling already-OCR'd / already-tagged PDFs

- **Status:** **CONFIRMED (2026-08-05), amended same day** — `detect`, `tag` and
  the runner are implemented and behave as decided: an already-tagged document
  skips the **engine** (its structure tree is kept verbatim), carrying
  `tag-skipped-already-tagged`. Amendment (from the Weird_PDFs round): the
  pikepdf **metadata finish now still runs** on these documents — `/Tabs`,
  `/Lang`, `DisplayDocTitle`, `pdfuaid` — because real publisher-tagged journal
  PDFs failed veraPDF UA-1 on exactly those stampable clauses; so the output is
  **no longer byte-identical** to the source (the tags are untouched, the
  catalog/metadata is topped up). Pinned by `tests/test_corpus_regression.py`
  (the `finish` action) and `tests/test_tag.py`. One addition since: the reviewer
  can deliberately override the no-re-tag rule in the app (*Remove all tags*,
  then re-tag), which is a human decision at the gate, not automation
  overwriting structure.
- **Date:** 2026-07-09 (confirmed 2026-08-05)
- **Relates to:** the `detect` stage (`src/speade/stages/detect.py`), the `tag`
  stage (`src/speade/stages/tag.py`), `Route` / `Sidecar` (`src/speade/pipeline/contract.py`),
  the runner (`src/speade/pipeline/runner.py`), the **never-mutate / do-not-degrade**
  invariant, and the already-tagged branch in the routing decision tree
  (`docs/diagrams/D02-routing-flags.drawio`).

## Question

What should the pipeline do when an incoming PDF is **already tagged** (it already
carries a PDF/UA structure tree)? This includes a document a Student Partner (or a
previous pipeline run) has already remediated, and a born-tagged export. The design
intends to route it past OCR and tagging straight to the gate — but the current
code does not.

## Context — a do-not-degrade gap, not a nice-to-have

**What happens today.** `detect` classifies on **text only**: an already-tagged PDF
has real text, so it routes `BORN_DIGITAL`; `tag` then calls the engine
**unconditionally** and **re-tags** it. `detect` never inspects the structure tree,
so it has no idea the document is already tagged.

**Why that is dangerous.** An already-tagged PDF, in this programme, is very often
one a **human already remediated** (correct reading order, real alt-text, fixed
tables). Blindly re-tagging writes a fresh auto-tag tree over that work — the
pipeline would make an *accessible* PDF *less* accessible. That is a direct breach
of the **never-mutate / do-not-degrade** invariant and breaks **idempotency**
(running the pipeline twice must never worsen a document). So refusing to clobber
existing tags is an **invariant-level requirement for v1**, not an optional feature.

**The design already intends this.** Diagram 1 has an explicit `detect → validate`
edge labelled *"If OCR'd and Tagged"* that bypasses both OCR and tagging. So the
already-tagged path is a **code gap, not a design gap** — this record just pins how
to represent it.

**Nuance: tagged ≠ compliant.** A structure tree can be present but wrong (bad
reading order, empty alt-text). And by inspection alone the system **cannot tell a
human-verified tree from raw auto-tag output**. So the safe rule is *"detect tags,
do not clobber, let the gate judge"* — not *"tagged, therefore done."* The existing
veraPDF + human gate already covers "are these tags actually good?", so an
already-tagged doc still flows through the gate; it just must not be re-tagged first.

## Options

| Option | How already-tagged is represented | Notes |
|---|---|---|
| **A. New `Route` value** (`Route.ALREADY_TAGGED`) | `detect` returns it; runner routes it straight to validate | Matches Diagram 1's 3-way branch, but **conflates two axes**: `Route` today means *text nature* (born-digital vs scanned); tag-state is orthogonal (a doc is texty **and** possibly tagged). Overloads the enum. |
| **B. Separate sidecar signal + `tag` guard** (chosen) | `detect` records an `already_tagged` signal on the `Sidecar`; `tag` skips + flags when set | Keeps `Route` as the clean text axis. In the linear `detect → tag → validate` pipeline, a skipping `tag` **is** the "straight to gate" path. Layered: detection is recorded (audit trail), protection lives where the destructive action is. |
| **C. `tag`-guard only** | `tag` inspects the PDF itself each run | Works, but hides the signal from the sidecar/audit trail and re-opens the PDF in `tag` when `detect` already had it open. |

## Decision

1. **`detect` detects and records; `tag` guards.** `detect` (which already opens the
   PDF) also checks for an existing structure tree and records an **`already_tagged`
   signal on the `Sidecar`** — as a dedicated boolean field, or minimally a
   `flags` entry (`"already-tagged"`). `Route` stays the **text-nature** axis
   (`BORN_DIGITAL` / `SCANNED` / `UNKNOWN`) — we do **not** add `ALREADY_TAGGED` to
   it (Option A rejected: orthogonal concerns).
2. **Never clobber.** `tag` skips the engine when `already_tagged` is set (symmetric
   with its existing `SCANNED`/`UNKNOWN` "needs OCR" guard), leaving the tags
   intact and adding a flag (e.g. `"tag-skipped-already-tagged"`). In the linear
   pipeline this delivers Diagram 1's *"straight to the gate"* behaviour.
3. **The gate still judges.** The already-tagged document still passes through
   veraPDF + the human gate, which decide whether the *existing* tags are good.
   `detect`/`tag` do **not** decide compliance — only "is a tree present, and don't
   destroy it."
4. **v1 minimum bar = don't clobber.** Detecting existing tags and refusing to
   re-tag is **required for v1** (do-not-degrade). Smarter routing (e.g. auto-run
   veraPDF first and short-circuit to the gate only if it already passes) is a
   **deferred** refinement the runner can own later.

## Detection mechanism

A tagged PDF is identifiable from the catalog, which `pypdf` exposes without extra
work (`detect` already has the reader open):

- **`/StructTreeRoot`** present in the document catalog → a structure tree exists.
- **`/MarkInfo`** with `/Marked true` → it claims to be a tagged PDF.
- *(stronger, optional)* an XMP **PDF/UA identifier** in metadata → it claims
  PDF/UA conformance.

Treat presence of `/StructTreeRoot` (ideally **and** `/Marked true`) as
"already tagged." This is a cheap catalog read, not a structural analysis — it
answers "are there tags to protect?", which is all the guard needs.

## Consequences

- The pipeline becomes **idempotent** for tagged inputs: a second run no longer
  degrades a remediated PDF.
- **Human-verified tags are protected** — the dominant, irreducible cost the whole
  programme is trying to preserve.
- `contract.py` gains one signal (`Sidecar.already_tagged` or a reserved flag
  string); `Route` stays clean. `detect` gains a catalog check; `tag` gains one
  guard clause; the runner is unaffected for v1.
- Diagram 1's *"If OCR'd and Tagged"* branch becomes **implemented**, not just drawn.

## Open questions

- **Field vs flag:** a typed `Sidecar.already_tagged: bool` (explicit, queryable)
  vs a `flags` string (no schema change). Lean typed field, since the runner and the
  gate both branch on it.
- **How strict:** require `/StructTreeRoot` **and** `/Marked true`, or is
  `/StructTreeRoot` alone enough? (A tree without `/Marked` is malformed but still
  risky to clobber — lean "tree alone is enough to refuse re-tagging.")
- **Deferred smart path:** should the runner auto-run veraPDF on an already-tagged
  doc and skip straight to the human sign-off when it already passes? Out of scope
  for the minimum bar; revisit when the runner grows route-based sequencing.
