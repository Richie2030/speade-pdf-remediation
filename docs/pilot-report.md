# Pilot evaluation report — SPEADE v1

> **STATUS: TEMPLATE.** Fill the blanks (`____`) after a pilot run. Every number
> here is obtainable from one measurement session — §2 is the procedure, §3 the
> definitions and result tables. The same figures fill the Partner Showcase deck.

**Purpose.** Show, with numbers, whether SPEADE actually helps: how much more
accessible the documents become, how reliable the automatic tagging is, and how
much reviewer time it takes versus tagging by hand in Acrobat. Written for
programme leadership deciding whether the tool earns its place.

**One honest framing up front.** There are two different "accuracy" questions and
this report answers both separately, because conflating them overstates the
result: **conformance** (does veraPDF pass — machine-checkable) and **semantic
correctness** (are the tags actually right — only a human can judge). §3a is
conformance; §3b is correctness. A high veraPDF pass rate does *not* mean the
tags are semantically right.

---

## 1. Pilot setup (record before you start)

| | |
|---|---|
| Document sample (where from) | ____ (e.g. one Canvas module's PDFs) |
| Number of documents (N) | ____ |
| Mix | ____ born-digital / ____ scanned / ____ mixed |
| Reviewer(s) | ____ |
| Dates | ____ |
| Machine (CPU, RAM, OS) | ____ |
| Pinned tool versions | Java ____ · OpenDataLoader ____ · veraPDF ____ · Tesseract ____ (from `tech-stack.md`) |

Keep the sample realistic — around 50 documents from a real module is ideal (a
believable batch, big enough for percentages to mean something).

---

## 2. How to run the pilot (produces every number in §3)

1. **Baseline the originals.** Before processing, run veraPDF over the *untagged*
   originals to capture the "before" conformance. veraPDF has a folder mode; run
   it against the inbox and record how many pass PDF/UA-1 and the average failed
   clauses per document. (Clause codes are decoded in `verapdf-clauses.md`.)
2. **Process the batch and time it.** Note the start and end clock time of the
   `Process PDFs` run and the document count → machine minutes per document. The
   app's auto-check gives each draft's "after (pre-correction)" veraPDF verdict
   for free (visible per row; also in `Export history`).
3. **Review each document, timed.** For each, start a stopwatch when you open it
   and stop when you decide. Record: review minutes; whether it needed any
   correction; how many tags you fixed; whether it needed Acrobat (something the
   app couldn't do); and — for the accuracy sample — a tag-correctness count.
4. **Capture the "after (approved)" conformance.** Each approval re-runs veraPDF
   on the final bytes; read the pass/fail from the gate result or the exported
   history CSV.
5. **One manual baseline.** Time yourself tagging *one* representative document
   fully by hand in Acrobat, start to finish. This is the comparison SPEADE's
   time-saving is measured against — one honest data point beats a guessed one.

Everything below is then arithmetic on what you recorded.

---

## 3. Results

### 3a. Accessibility conformance — veraPDF (machine-checkable)

**Definition.** Share of documents that pass veraPDF PDF/UA-1, and the average
number of failed clauses per document, at three stages: original (untagged),
SPEADE draft (before human correction), and final approved.

| | Original | SPEADE draft | Approved |
|---|---|---|---|
| Documents passing PDF/UA-1 | ____ / N | ____ / N | ____ / N |
| Pass rate | ____ % | ____ % | ____ % |
| Avg. failed clauses / doc | ____ | ____ | ____ |
| Most common failing clauses | ____ | ____ | ____ |

*Expected shape:* originals almost all fail (they are untagged); the SPEADE draft
closes most machine-checkable clauses automatically; approval closes the rest a
human addressed. **This is the strongest headline number — but pair it with 3b so
it is not mistaken for "the tags are correct".**

### 3b. Tagging accuracy — semantic correctness (human-judged)

**Definition.** On a sample of documents, the reviewer judges whether each tag is
*right* (correct type, sensible reading order, nothing meaningful left untagged).
This is the reliability figure that has never been benchmarked — there is no
head-to-head against other taggers because OpenDataLoader is the only free,
offline, open-source end-to-end tagger (`decisions/pdf-engines.md`).

**Procedure.** Sample ____ documents. For each, using the tags view, count: tags
**correct**, tags **wrong** (wrong type / wrong reading order / missed content),
and **images needing a description**.

| Sampled documents | ____ |
|---|---|
| Tags judged correct | ____ % of all tags |
| Documents needing *any* manual correction | ____ % |
| Average corrections applied per document | ____ |
| Documents where tagging was better started over (Remove all tags) | ____ |

*Expected shape (from earlier observation):* titles, section headings,
paragraphs, lists and captioned figures tag well; sub-section headings often come
out as ordinary paragraphs; complex multi-column layouts need the most work.

### 3c. Effort and throughput

**Definition.** Machine time and human time per document, and how much of the
work SPEADE removed.

| | Value |
|---|---|
| Machine minutes per document (born-digital) | ____ |
| Machine minutes per document (scanned) | ____ |
| Human review minutes per document (median) | ____ |
| Documents fully finished in-app (no Acrobat) | ____ % |
| Documents needing Acrobat | ____ % |
| Manual baseline: minutes to tag one doc by hand in Acrobat | ____ |
| **Estimated time saved per document** (baseline − review) | ____ |

*Reference envelope (measured):* ~15–40 s machine time per document, scans
slowest (`limitations.md`). Review time is the real cost — this is why the
working batch is ~50.

### 3d. Reliability at scale

| | Value |
|---|---|
| Batch completed without aborting | Yes / No |
| Documents flagged unreadable (password / corrupt) | ____ % |
| Documents flagged tag-unavailable / ocr-unavailable | ____ % |
| Memory stable across the batch (flat, not climbing) | Yes / No |
| Any crash or hang | ____ |

*Expected:* the batch never aborts on one bad file; memory is flat regardless of
size; unreadable inputs are flagged for the human, never guessed at.

---

## 4. Interpretation

- **Conformance (3a)** rising from near-zero to high is the accessibility win, but
  only meaningful alongside **correctness (3b)** — report them together.
- **% needing Acrobat (3c)** is the honest ceiling on automation: the lower it is,
  the more the app stands on its own; whatever remains is the human-judgement work
  the design deliberately keeps with a person.
- **Time saved (3c)** is the adoption argument: reviewer-minutes per document
  versus the manual-Acrobat baseline, across a module's worth of PDFs.
- **Reliability (3d)** is the "can we trust it on a real pile" answer.

State limitations plainly: the sample size, the document mix, and that semantic
accuracy is judged by one reviewer (not an inter-rater study).

---

## 5. Conclusion (write after filling §3)

> On a pilot of ____ documents from ____, SPEADE raised PDF/UA-1 conformance from
> ____ % to ____ %. ____ % of documents were finished entirely in the app; ____ %
> needed Acrobat for work the tool deliberately leaves to a person. Review took a
> median of ____ minutes per document against a manual-tagging baseline of ____
> minutes, a saving of about ____ minutes each. The batch of ____ processed
> without failure. [Recommendation: ____]

---

## 6. Live demo script (companion to the deck)

A tight walkthrough of the real software for the showcase — aim ~4 minutes.

1. **Drop a small batch in and press Process.** "Automation drafts; a human
   decides." Let the progress bar run; point out already-done files are skipped.
2. **Open a document.** Show the tag tree beside the pages with a box on every
   tagged region — "this is what a screen reader will follow." Point at the
   automatic-check result in plain language.
3. **Make one correction live.** Retag a paragraph that should be a heading, or
   drag to merge fragmented text — show the check re-run and the one-click Undo.
4. **Write an image description.** Emphasise this is never machine-generated — the
   human judgement the design protects.
5. **Set title and language, then Approve.** Show it move to `approved/`.
6. **Open History / Export.** "Every decision is recorded with who and when, and
   the exact bytes are fingerprinted" — the trust trail, in one screen.

Have a fallback screenshot of each step in case a live tool is blocked (App
Control has interrupted the tagger and the browser mid-session before).

---

## Appendix — the commands

```powershell
uv run python -m speade run-batch                 # process the inbox (times the batch)
uv run python -m speade.desktop                   # review window (or speade.web for the browser)
# baseline the originals with the veraPDF CLI (folder mode) for the "before" number
# clause codes -> docs/verapdf-clauses.md ; measured envelopes -> docs/limitations.md
```

The self-test checklist (`self-test-checklist.md`) §2 and §9 give the batch and
accuracy procedures in more operational detail.
