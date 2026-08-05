# SPEADE — quick reference (print me)

## The six steps

1. **Open** `speade-desktop.exe`.
2. **Add PDFs…** — they appear under *Waiting to process*. Aim for **~50 at a
   time** — a comfortable amount to review; do several batches if there are more.
3. **Process PDFs** — wait for the progress bar; already-done files are
   skipped automatically, so splitting into batches costs nothing.
4. **Review** — click a document: check the tag tree and the boxes on the
   pages (heading order → reading order → lists/tables → image descriptions →
   anything with no box). Set the **title and language**, save.
5. **Decide** — *Approve* (→ `approved` folder, done) or *Reject*
   (→ `rejected` folder).
6. **Fix and return** — repair rejected PDFs in Adobe Acrobat Pro (reading
   order, tables, image descriptions), save over the same file, come back,
   **Approve**. Not approved = not done.

## The three rules

1. **Originals are sacred** — corrections happen on the draft, never the
   source file.
2. **Nothing ships without a human** — only your Approve makes it accessible.
3. **When unsure, reject and ask** — the queue can wait; students can't.

## What the machine cannot do (always yours)

- Write image descriptions (alt-text).
- Judge reading order on complex layouts.
- Decide decorative vs meaningful images.
- Hear the document — run the NVDA spot check now and then
  (`docs/screen-reader-check.md`).

## Where things are

`data\inbox` sources · `data\outbox` awaiting review ·
`data\outbox\approved` ready to share · `data\outbox\rejected` fix in Acrobat.
Send onward: **the PDF only**.
