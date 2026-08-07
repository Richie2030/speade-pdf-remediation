# Self-test checklist — full manual QA of SPEADE v1

A complete pass over everything the app does, for a human tester to run before
sign-off. Every item is one action and its exact pass condition, derived from
the real code (not a wish-list). Tick each box; anything that fails is a bug to
report.

**Priority key:** **[core]** = must pass to ship · **[imp]** = important, fix
before pilot · **[edge]** = corner case, note if it fails.

## 0. Before you start

- Run on a machine where the four tools work (`scripts/setup-machine.ps1`), so a
  failure means a real bug, not a missing tool. Test the *missing-tool* cases
  (§7) deliberately, on purpose, one at a time.
- Launch with `uv run python -m speade.desktop` (the window) — the `.exe` is
  blocked by Smart App Control on the dev PC. Repeat §8 with
  `uv run python -m speade.web` (browser at `http://127.0.0.1:8765`).
- Have ready: a normal text PDF, a scanned (image-only) PDF, a mixed one, an
  already-tagged PDF (a tagged Word/Acrobat export), a password-protected PDF,
  and a junk file renamed to `.pdf`. A folder of ~500 mixed PDFs for §2.
- "Simulate a missing tool" means: rename its folder off the PATH (and, for
  Tesseract, make sure it is not at `C:\Program Files\Tesseract-OCR\`), test,
  then put it back.

---

## 1. Processing PDFs

- [ ] **[core]** Click *Add PDFs…*, pick two. → Status "Added: …"; a "Waiting to
  process (2)" section appears; the button reads "Process 2 PDFs"; the originals
  are copied to the inbox, never moved.
- [ ] **[imp]** Drop a PDF into the inbox folder in Explorer, press *Refresh
  list*. → It appears under "Waiting to process".
- [ ] **[edge]** Put a `.docx` and a `.txt` in the inbox, press Process. → They
  are ignored entirely (only `.pdf` is swept).
- [ ] **[core]** Press *Process PDFs* with N waiting. → Process disables, a Stop
  button appears, progress counts "1 of N — filename", ends "N of N — done",
  bar fills then hides, status "N processed." Queue shows the new drafts.
- [ ] **[core]** Hash an inbox PDF, process, hash again. → The inbox original is
  byte-identical (all work happens on a copy).
- [ ] **[core]** Process a scanned PDF, then look in the outbox folder. → Exactly
  one PDF per input, named like the input — no `.ocr.pdf` / `.tagged.pdf`
  intermediates; sidecars live in the hidden `sidecars` folder, not the outbox.
- [ ] **[core]** The instant a batch finishes, look at a queue row (don't open
  it). → It already shows an automatic-check chip ("auto-check: passed" or
  "auto-check: N issue(s)") — the check ran at processing time.

**Routing — the right path per document:**

- [ ] **[core]** Process a normal text PDF. → "Processed" line: "Digital
  document, tagged"; no note chips; a real tag tree.
- [ ] **[core]** Process an image-only scan (Tesseract installed). → "Scanned
  document, text recognised (OCR) and tagged"; the page still *looks* like the
  scan but its text is now selectable; the tree has real P/H tags, not one
  Figure per page.
- [ ] **[imp]** Process a mixed text+image PDF. → Tagged anyway, with the chip
  "Mixed content, check every part got tagged".
- [ ] **[core]** Process an already-tagged PDF. → Chip "Already had tags, kept
  them (standards metadata topped up)"; the output's structure tree is the
  *original* one (never re-tagged), but the file is *not* byte-identical: the
  finish stamps Tabs/Lang/DisplayDocTitle/pdfuaid on top; it goes straight to
  the gate.
- [ ] **[core]** Open a document with images → an "Image descriptions…" button
  appears top right (with a count of missing ones). It opens the *Set alternate
  text* stepper: "Image 1 of N", the page scrolls to each image, ◀ ▶ move
  (saving any change first), *Decorative figure* removes the image from the
  reading order (tree reloads, count shrinks), *Save &amp; Close* keeps the
  current text, *Cancel* discards only the unsaved one. Every save re-runs the
  automatic check and lands in History as an image-description edit.

**Skip / re-run logic:**

- [ ] **[core]** Process N files, immediately press Process again. → Finishes
  near-instantly, "N already done (skipped)"; the drafts are untouched.
- [ ] **[core]** With 10 processed, add 1 new PDF, press Process. → Progress
  shows "1 of 1"; status "1 processed, 10 already done (skipped)".
- [ ] **[imp]** Overwrite an already-processed inbox PDF with different content
  (same name), Refresh. → It returns to "Waiting to process" and re-runs.
- [ ] **[imp]** Delete a draft from the outbox root (leave its sidecar), Refresh.
  → Its inbox source counts as pending again; Process regenerates it.
- [ ] **[imp]** Approve a document, then press Process again. → It stays skipped
  (the app finds it in `approved/`); deciding never causes a re-draft.

**Failure isolation & Stop:**

- [ ] **[core]** Batch several files where one fails hard mid-pipeline. → Every
  other file completes; the status ends "Problems — bad.pdf: …"; the failed file
  leaves no draft and no sidecar and returns to "Waiting to process".
- [ ] **[core]** Start a 5-file batch, press *Stop* while file 2 is running. →
  "Stopping after the current document…"; file 2 *finishes* (never a half-written
  PDF); files 3–5 stay waiting; status "Stopped early. 2 processed…".
- [ ] **[edge]** Press Process with an empty inbox. → "No PDFs in the input
  folder."; nothing lingers.
- [ ] **[edge]** Click a document that is currently being processed. → "This
  document is being processed right now — it will appear when processing
  finishes." (not an error).

---

## 2. Batch size — the everyday rhythm and the headroom check

**The working batch is ~50.** Review (not processing) is the real work, so ~50
is a sitting's worth; more than that is done in several batches at no cost
(already-done files are skipped). The app *nudges* past a large batch but never
blocks. Memory is flat regardless of size, so the ceiling is time, not
stability:

| batch of 50, all… | rough total |
|---|---|
| born-digital (text) | ~3 minutes |
| two-page scans (OCR) | ~30 minutes |
| mixed | ~10–20 minutes |

**Everyday batch:**

- [ ] **[core]** Process a realistic batch (~50 mixed PDFs). → Runs to
  completion, one row per file; lands in the "15–40 s per document" envelope
  (scans slowest).
- [ ] **[imp]** *Stop* halfway, then Process again. → It resumes, skipping
  everything already done — the first half is never re-done.
- [ ] **[imp]** Seed the batch with a junk / password / corrupt file. → They come
  back flagged (§7); none of them stops the rest.

**The large-batch nudge:**

- [ ] **[core]** Add more than 100 PDFs and press Process. → A dismissible
  message notes "You are about to process N PDFs… you can process them in smaller
  batches (around 50)…". Clicking OK processes all N; Cancel stops so you can
  trim the inbox. It never hard-blocks.
- [ ] **[imp]** Add ~50–80 PDFs and press Process. → **No** nudge — normal
  batches run straight through.

**One-time headroom check (developer reassurance, not everyday use):**

- [ ] **[edge]** Once, process a few hundred PDFs and watch memory in Task
  Manager. → It stays roughly flat (~200 MB) start to finish — confirms batch
  size limits time, never stability. *Record the wall-clock time and
  per-document average — they feed the pilot report and the showcase deck.*

---

## 3. Reviewing a document

**The tag tree (left):**

- [ ] **[core]** Open a tagged PDF. → One row per tag, plain-language type
  (Paragraph, Heading 1, Image…) then its text; containers show a "▾" caret.
- [ ] **[core]** Open a document with an undescribed image. → The Figure row
  reads "(no description yet)", is highlighted with a "needs description" badge,
  and the Structure line counts "…M still need a description".
- [ ] **[imp]** Click a container's caret, then again. → Children collapse ("▸")
  and expand; clicking the caret does *not* open the editor.
- [ ] **[imp]** Open a citation-heavy paper, tick "Show N links". → Link tags are
  hidden by default from both tree and page boxes; ticking reveals them;
  unticking hides them; no "edited" chip (view filter only).
- [ ] **[edge]** Open a document with no tags. → "No tags yet…" plus, if there is
  untaggable-but-drawable content, "Drag a rectangle over content on the pages
  to start tagging it."

**The pages (right) and the link between them:**

- [ ] **[core]** Look at the pages column. → One band per page ("Page 1"…) at the
  right shape; every leaf tag drawn as a box with a small type label at its
  top-left corner; hovering a box shows a tooltip.
- [ ] **[imp]** Open a 50+ page document, scroll fast. → Page images load only as
  they approach view; the layout never jumps.
- [ ] **[core]** Click a box on a page. → Its tree row highlights and the tree
  scrolls to it; the page stays put; the editor opens for that tag.
- [ ] **[core]** Click a row in the tree. → The editor opens; a strong box
  appears over its content; the pages scroll to centre it.
- [ ] **[imp]** Select tag A, then tag B. → Only B is highlighted; A's highlight
  and box are gone (single selection).

**Keyboard (the accessibility round):**

- [ ] **[imp]** Tab to a queue item, press Enter (or Space). → It opens for
  review; each row announces "Open <file> for review".
- [ ] **[core]** Tab into the tree, use ↑/↓, press Enter. → One Tab enters the
  tree; arrows move row to row; Enter opens the focused tag; one more Tab leaves
  the tree.
- [ ] **[imp]** With a marquee selection or the Help overlay open, press Escape.
  → Escape clears the selection / closes Help.
- [ ] **[edge]** Turn on the OS "reduce motion" setting, click around. → Sync
  scrolling jumps instantly instead of animating.

---

## 4. Editing tags

**Single-tag edits:**

- [ ] **[core]** Select a Paragraph that should be a heading, pick "Heading 2",
  press *Change*. → "Changed to Heading 2 — automatic check…"; the tree rebuilds
  with the same tag re-selected; the "edited" chip and banner appear.
- [ ] **[edge]** Press *Change* with the type unchanged. → Nothing happens (no
  save, no edited state).
- [ ] **[core]** Select an undescribed Figure, type a description, press *Save
  description* (also try Enter in the field). → "Description saved…"; the "needs
  description" badge disappears; the Structure count drops.
- [ ] **[imp]** Clear a description and save. → "Description cleared…"; the badge
  returns.
- [ ] **[imp]** Select in turn a Paragraph, a Figure, a Formula, a described
  tag. → The "Image description" row is hidden for the Paragraph, shown for the
  other three.
- [ ] **[core]** *Mark as decorative* on a tag containing real text. → A confirm
  quotes the text, warns a screen reader won't read it, and names the recovery
  route; OK removes it from the tree while the content stays on the page.
- [ ] **[imp]** *Mark as decorative* on a page number or rule (≤3 chars). → No
  confirmation — applies immediately.
- [ ] **[core]** Select a *wrapper* tag, then a *content* tag. → "Remove tag,
  keep contents" is shown on the wrapper and hidden on the content tag (you are
  never offered a button the backend would refuse).
- [ ] **[core]** *Remove tag, keep contents* on a bogus List around plain
  paragraphs. → Its children take its place in reading order; History reads
  "…tag removed, contents kept".
- [ ] **[core]** *↑ Earlier* on a misplaced caption. → It swaps one place with
  its sibling; the tree order changes.
- [ ] **[imp]** *↑ Earlier* on a tag already first among its siblings. → "Already
  at the start of its group…"; no edit, no undo step added.
- [ ] **[core]** *Remove all tags*, confirm. → All boxes vanish, page images
  unchanged, "No tags yet" message; one *Undo last change* restores everything.

**Drag-select (marquee) and bulk actions:**

- [ ] **[imp]** Press and release without moving (~<5px). → Treated as a click,
  not a drag; no bulk bar.
- [ ] **[core]** Drag over 2+ whole tag boxes. → A rectangle draws; on release,
  boxes ~40%+ inside are selected (page boxes *and* tree rows highlight); the
  bar reads "N tags selected" with Merge / Change all / Mark all decorative
  enabled.
- [ ] **[core]** Drag over some lines inside a paragraph. → Each line ~half-inside
  gets a yellow highlight; the count includes "M lines"; "New tag from lines"
  enables.
- [ ] **[core]** Drag over untagged content (a decorated or missed region). →
  Dashed-red highlights; count includes "K untagged"; only "Tag untagged"
  enables.
- [ ] **[imp]** Drag tightly around exactly one whole tag. → No bulk bar; the
  ordinary editor opens for that tag.
- [ ] **[edge]** Start a drag on one page, move onto the next. → The rectangle
  stays clipped to the starting page.
- [ ] **[core]** Select 3 fragments of one paragraph → *Merge into one*. → They
  become one tag, contents in order; "3 tags merged into one…"; one *Undo*
  reverses all three.
- [ ] **[core]** Select several tags → *Change all*. → All become the chosen
  type in one step; one undo covers the batch.
- [ ] **[core]** Select tags including text → *Mark all decorative*. → Confirm
  states how many contain text; OK removes them all; one undo step.
- [ ] **[core]** Drag the first line of a paragraph (a swallowed heading) → *New
  tag from lines* as Heading 2. → The line becomes a new H2 placed *before* the
  paragraph; a donor emptied of all lines disappears; one undo restores.
- [ ] **[core]** Drag untagged content → *Tag untagged*. → It gains a box, a
  chip, and a tree row in reading order; on a fully untagged document this
  creates the first tag (so *Remove all tags* is reversible by hand).
- [ ] **[edge]** *Tag untagged* on a stale selection / an unusual page. → It
  refuses cleanly with a message ("…reload and try again" / "…tag it in
  Acrobat") and changes nothing.

**Undo and re-check:**

- [ ] **[core]** After any edit, read the result line and the queue chip. →
  veraPDF re-ran: "automatic check now passes" / "…N issue(s) left"; the queue
  chip updates; issues are listed as "code – plain meaning".
- [ ] **[core]** Make three edits, press *Undo last change* repeatedly. → The
  button reads "(3 available)"; each press steps back one edit; after the last,
  the button hides and the "edited" state clears.
- [ ] **[edge]** Make >20 edits. → Only the 20 most recent are undoable.
- [ ] **[core]** With edits present, press *Undo all edits (reprocess)*. →
  Rebuilds the draft from the untouched inbox original; the edited state and the
  step history clear. (If the inbox original was deleted: "original not in the
  inbox…" and nothing changes.)
- [ ] **[imp]** Do one of each edit, open *History*. → Each reads in plain
  language ("retagged Paragraph as Heading 2", "image description added", "a tag
  moved earlier…", "all tags removed", "last change undone").

---

## 5. The decision gate + title / language

- [ ] **[core]** Open a doc with an existing title and language. → Title box and
  Language dropdown are pre-filled; the fields and *Save* enable once loaded.
- [ ] **[edge]** Open a doc whose language code isn't in the list (e.g. `cy`). →
  Dropdown shows "Other…" with `cy` in the free-text box; a doc with no language
  shows "(not set)".
- [ ] **[core]** Type a title, pick "English (Ireland)", *Save title &
  language*. → "Saved…"; re-selecting shows the values; the PDF now carries the
  title, `/Lang = en-IE`, and DisplayDocTitle (the title, not filename, shows in
  a viewer's tab).
- [ ] **[imp]** After a metadata save, check the queue row. → *No* "edited" chip
  — a metadata save refreshes the fingerprint (an app edit isn't tampering).
- [ ] **[edge]** Clear the Title and save; then save with language "(not set)". →
  Blank fields leave the existing PDF values untouched (blanks never erase).
- [ ] **[core]** Leave the reviewer box empty, click Approve. → "Enter your
  student number first."; the file does not move.
- [ ] **[core]** Enter a reviewer, click *Approve — ready to share*. → "Running
  the final automatic check…"; the PDF moves from the outbox root to
  `approved/`; message "Approved — moved to the approved folder…"; Status shows
  "Approved by <reviewer>".
- [ ] **[core]** Edit a draft in Acrobat, then approve it. Hash the file now in
  `approved/`. → Its SHA-256 equals the sidecar's and the audit event's
  `output_sha256` — the approval pins the *edited* bytes, not the pipeline's.
- [ ] **[core]** Break a passing draft in Acrobat (remove tags), approve without
  reprocessing. → The gate reports "automatic check found issues…" — veraPDF
  re-ran on the current bytes; the human decision still records as Approved.
- [ ] **[core]** Enter a reviewer, click *Reject*. → The PDF moves to
  `rejected/`; Status "Rejected by <reviewer>".
- [ ] **[core]** Fix a rejected PDF in Acrobat (save over it in `rejected/`),
  come back, Approve. → It moves `rejected/` → `approved/`; History shows a
  *second* decision row while the first is unchanged (decisions append).
- [ ] **[imp]** Make an in-app edit (undo button visible), then decide. → After
  the refresh the "Undo last change" button is gone — a decision closes the
  editing session.
- [ ] **[edge]** Delete the outbox PDF, click Approve. → "Error: …"; the busy
  status clears and both buttons re-enable (no stuck disabled buttons).

---

## 6. History & audit export

- [ ] **[core]** Click *History*, then click it again. → Shows the trail (newest
  first: When / Document / What happened); second click restores the previous
  view.
- [ ] **[imp]** Generate one of each event (process, save metadata, retag, add
  alt, approve). → Rows read in plain language, local-time stamped, never raw
  event names.
- [ ] **[imp]** Between actions, re-open History and look at the audit file in
  Explorer. → Earlier rows never change; the log only grows; the sidecar and
  audit folders are *hidden* in Explorer.
- [ ] **[core]** Click *Export history*. → Status "History saved as
  speade-history-<timestamp>.csv in the output folder"; the outbox opens with
  that CSV present.
- [ ] **[core]** Open the CSV. → Header: `time, event, document, reviewer,
  decision, verapdf_passed, source_sha256, output_sha256, details`; extra
  fields land intact in the `details` column; nothing is dropped.
- [ ] **[imp]** Compare CSV order to the screen. → CSV is oldest-first
  (chronological), so exports from several machines concatenate into one sortable
  record.
- [ ] **[edge]** Save an Irish-language title, export, open the CSV in Excel. →
  Columns split correctly and accented characters render (UTF-8 with BOM).
- [ ] **[core]** Process a PDF and select it without touching the file. → No
  "edited" chip, no banner, no reprocess button (bytes match the fingerprint).
- [ ] **[core]** Modify the outbox draft outside the app, Refresh. → The "edited"
  chip and the "Edited since the app processed it" banner appear, plus the "Undo
  all edits" button.

---

## 7. Robustness & bad inputs (do these deliberately)

- [ ] **[core]** Process a password-protected PDF. → No crash, no password
  prompt: chips "Password-protected, cannot be processed" + the skipped-OCR/tag
  chips; "Could not be processed, the file is unreadable"; it reaches the gate
  for a human to reject.
- [ ] **[imp]** Rename a junk file (JPEG or truncated) to `.pdf`, process. → No
  stack trace, no batch abort: chip "File is damaged and cannot be read"; the
  rest of the batch is fine.
- [ ] **[core]** Simulate **veraPDF missing**, process and decide. → Never a
  silent pass: chip "auto-check: 1 issue" (`verapdf-unavailable`) at both
  processing and decision; processing still succeeds; the human can still decide
  (recorded with the fail verdict).
- [ ] **[core]** Simulate **OpenDataLoader missing**, process a text PDF. → Not a
  batch failure: the doc reaches the gate flagged "Not tagged, the tagging engine
  is missing or blocked on this PC"; the rest of the batch continues.
- [ ] **[imp]** With OpenDataLoader present but blocked by App Control, process. →
  Same graceful path, but the note names the *blocked* case and points at
  `setup-machine.ps1` (it must not read as "not installed").
- [ ] **[core]** Simulate **Tesseract missing**, process a scan. → No crash: chip
  "Text recognition is not installed on this PC"; the tag stage skips; the doc
  reaches the gate.
- [ ] **[imp]** Open a very large document (hundreds of pages) and scroll. → It
  opens promptly; page images lazy-load; it stays responsive.
- [ ] **[imp]** Open a pathological document with >3000 tags. → The tree shows the
  first 3000 with "…tree shortened (very large document)"; the UI does not
  freeze.
- [ ] **[core]** Process a batch, approve one, reject one, edit a third, then
  **close and relaunch the app**. → Everything rebuilds from disk: statuses,
  verdicts, flags, the edited state (and its remaining undo depth), and the full
  History are all intact; nothing re-processes.
- [ ] **[edge]** Hand-corrupt one sidecar JSON, open the app. → The rest of the
  queue renders; the damaged one shows "Record damaged" instead of hiding the
  list; its source counts as needing processing again.

---

## 8. Two clients behave identically

- [ ] **[core]** Run the same flow (process, select, edit a tag, set
  title/language, decide) in the desktop window **and** in the web client
  (`python -m speade.web`, browser at 127.0.0.1). → Behaviour is identical; a
  decision made in one shows in the other after Refresh (same files on disk).
- [ ] **[imp]** In the web client, the *Add PDFs* is a file upload and the
  preview loads in the browser — the only two permitted differences.
- [ ] **[core]** Try to reach the web client from another machine on the network
  (`http://<this-pc-ip>:8765`). → It must **not** be reachable — it binds
  127.0.0.1 only, with no login.

---

## 9. Tagging-accuracy spot check (the "how good is it, really" question)

veraPDF passing means the tags are *well-formed*, not *semantically correct* —
whether a heading is really tagged as a heading is exactly what a human judges.
To get an honest reliability number rather than a conformance one:

- [ ] Take ~10 representative documents (mix of simple and multi-column).
- [ ] For each, open the tags view and count, per document: tags that are
  **right**, tags that are **wrong** (wrong type, wrong reading order, missed
  content), and **images needing a description**.
- [ ] Record "% of tags correct" and "minutes to fix in the app / Acrobat" per
  document. → This is the real accuracy figure, and it fills the pilot report
  and the showcase deck's "% needing Acrobat" and "minutes per document" numbers.

Expect (from earlier observation): titles, section headings, paragraphs, lists
and captioned figures tag well; sub-section headings and complex multi-column
layouts need the most correction. There is no head-to-head benchmark against
other taggers because OpenDataLoader is the only free, offline, open-source
end-to-end tagger — see `decisions/pdf-engines.md`.
