# SPEADE desktop — user guide

How to remediate PDFs with `speade-desktop.exe`, from opening the app to an
approved document. Written for Student Partners — no technical background
needed. (Setting up a new PC is a different document: `docs/runbook.md`.)

**What SPEADE does:** it takes course PDFs and automatically produces a *draft*
accessible version — structure tags, reading order, OCR text for scans, a
PDF/UA check. **You** are the important part: nothing counts as accessible
until a human reviews the draft and approves it. The software prepares; you
decide.

---

## Before you start

The PC needs a one-time setup (normally done by IT / the project team): Java,
OpenDataLoader, veraPDF, and Tesseract installed, plus the `speade-desktop`
folder itself. If the checklist in `docs/runbook.md` has been run on the
machine, you are ready.

## Step 1 — open the app

Double-click **`speade-desktop.exe`** (inside the `speade-desktop` folder).

A window opens with:

- a **toolbar** — *Add PDFs…*, *Process PDFs*, *Refresh list*, *Open input
  folder*, *Open output folder*, *History*;
- a **document list** down the left;
- a **detail pane** on the right — everything about the selected document.

Hover the folder buttons to see the full folder paths on this PC.

## Step 2 — add the PDFs you want to remediate

Two ways, same result:

- click **Add PDFs…** and pick the files, **or**
- click **Open input folder** and copy files in yourself.

Added files appear immediately in the list under **"Waiting to process"**, and
the button changes to **"Process 3 PDFs"** so you can see exactly what will
run. Your original files are **never modified** — SPEADE always works on
copies.

## Step 3 — press Process PDFs

The pipeline runs each waiting PDF: it works out whether the document is
digital text or a scan, recognises the text of scans (OCR), finds photo areas,
and writes the accessibility structure (headings, paragraphs, lists, reading
order). A progress bar counts "2 of 5 — filename", and a **Stop** button halts
the batch after the current document if you need to.

Useful to know:

- **Already-processed files are skipped.** Add two PDFs today and one
  tomorrow — tomorrow's run processes only the new one ("1 processed, 2
  already done").
- **One bad file never stops the rest.** A password-protected or damaged PDF
  shows up in the list with a plain-language note; the others carry on.
- Each document takes roughly 15–40 seconds (scans take longest). A very large
  batch simply takes time — the progress bar and Stop button are your friends.
- Right after processing, every document already shows its **automatic check**
  result (the official PDF/UA validator) — you can see which documents passed
  before you even open them.

## Step 4 — review a document

Click a document in the list. The detail pane shows, top to bottom:

**The facts** — plain-language answers: what was done ("Scanned document —
text recognised (OCR) and tagged"), what the automatic check found ("2 issues:
fonts not embedded properly — fix in Acrobat, or use your judgement"), the
structure at a glance ("Tagged: 3 headings, 56 paragraphs, 5 lists"), and its
current status. The check is veraPDF, the official PDF/UA validator; the codes
in brackets (like `7.3-1`) are looked up in `docs/verapdf-clauses.md`. If the
file has been changed since the app made it, a yellow **Edited** banner says
so — see below.

**The tags view** — the heart of the review. The left side is the **tag tree**:
every heading, paragraph, list, and image in reading order, in plain language.
The right side shows the **actual pages**, scrolling top to bottom, with a box
drawn around every tagged region and a small label naming each one (P, H1,
Figure…) — just like Acrobat's tag view. They are linked: click a tree entry
and its box lights up yellow on the page; click a box and its tree entry is
highlighted. **Anything with no box is untagged** — that's the fastest way to
spot a problem.

What to check, in order:

1. Do the headings in the tree match the document's real headings?
2. Does the tree's top-to-bottom order match the sensible reading order?
3. Are lists and tables tagged as lists and tables, not paragraphs?
4. Does every meaningful image show as **Image**, and does it have a
   description? ("(no description yet)" means you write one — in Acrobat.)
5. Is any visible content missing a box entirely?

**Fixing tags in the app** — select any tag (in the tree or by clicking its box)
and the editor below the pages lets you do the two most common corrections
without leaving SPEADE:

- **Change the tag type** — the fix for a heading the app called a paragraph:
  pick "Heading 2" and click *Change tag*.
- **Write an image description** — select an Image and type what it shows and
  why it matters, then *Save description*. (Never machine-generated: this is
  the human step.)
- **Mark as decorative** — for anything that carries no information (a border,
  a flourish, a page number, a decorative image): it leaves the reading order
  entirely and needs no description, so screen readers skip it. Use this instead
  of writing "image" as a description. If the tag contains real text the app
  asks you to confirm, because hiding text from screen readers is rarely right.
- **Remove tag, keep contents** — deletes a wrapper the app invented (for
  example paragraphs bundled into a list that is not a list) while leaving what
  was inside it in the reading order. The app refuses this for a tag that holds
  text or an image directly, because deleting that tag would leave the content
  untagged, which is worse: retag it or mark it decorative instead.
- **Move earlier / Move later** — reading order is the order of the tree, so
  these buttons fix a caption or heading that reads in the wrong place. They
  move a tag within its own group; bigger rearrangements are an Acrobat job.

**Selecting many tags at once** — hold the mouse down on a page and **drag a
rectangle**, just like Acrobat: every tag box inside it is selected (highlighted
on the page and in the tree), and a bar appears above the pages with actions for
the whole selection:

- **Merge into one** — the most common fix for scans: text the app broke into
  many small paragraphs becomes ONE tag of the type you pick (a paragraph, or a
  Figure with its caption). Contents stay in reading order.
- **Change all** — retag the whole selection in one go.
- **Mark all decorative** — clear a page's worth of noise (page furniture,
  scan borders) in one action; the app asks first if any of it contains text.
- **New tag from lines** — the finer grain: drag over *part* of a tag (say a
  heading the app swallowed into the top of a paragraph, or the last two lines
  that are really a caption) and the highlighted **lines** are carved out into
  one new tag of the type you pick. Carved from a tag's opening line, the new
  tag lands *above* it; otherwise below — matching how it reads.
- **Tag untagged** — for content with **no tag at all** (dashed **red**
  highlight when you drag over it): text the app missed, or something that was
  marked decorative. It gets one brand-new tag of the type you pick, placed
  beside its neighbours in reading order. This also means decorative is not a
  one-way door — drag over decorated content to make it real content again.
  (Rarely, on a page drawn in an unusual way, the app will refuse and point
  you to Acrobat rather than guess.)
- **Clear** (or press Escape) — drop the selection.

One drag is one saved change: a single *Undo last change* reverses the whole
action. A short drag still counts as an ordinary click on a single tag.
- **Remove all tags** (top right) — the start-over escape hatch when the
  automatic tagging is worse than nothing: the document stays readable but
  loses all structure, ready to be tagged from scratch in Acrobat (or restored
  with *Undo all edits*).

Each save writes into the PDF and re-runs the automatic check, so you see the
issue count change immediately. The document then shows an **Edited** banner —
expected. Two levels of undo sit at the top right: **Undo last change** steps
back one edit at a time (up to 20 within a session), and **Undo all edits
(reprocess)** starts over from the untouched original. Everything else (splitting merged
paragraphs, table header cells, complex reading order) is still Acrobat's job.

**Title and language** — screen readers announce both. Fill in a human title
(what a student should *hear* — "Week 3 Lecture Notes", not "scan_0047"), pick
the reading language, and click **Save title & language**. Do this before you
decide.

## Step 5 — decide: Approve or Reject

At the bottom of the pane: check the **reviewer** box has your student number,
then click **Approve — ready to share** or **Reject — needs more work**.

- **Approve** re-runs the automatic check on the file *as it is right now*,
  records your decision permanently (who, when, what the validator said), and
  moves the PDF to the **`approved`** folder — the ready-to-share shelf.
- **Reject** moves it to the **`rejected`** folder — the to-fix shelf.

The validator's verdict is advice; **your click is the decision**. A document
can fail a rule you've judged acceptable, and you may approve it; when unsure,
reject — a wrong approval ships an inaccessible document with your name on it,
a wrong rejection just gets a second look.

## Step 6 — fix rejected documents in Acrobat, then come back

Automation drafts; two things are always yours: **image descriptions**
(alt-text is written by a person, never generated) and **judgement calls**
(reading order on complex layouts, table structure, decorative vs meaningful
images).

The working rhythm:

1. Open the **`rejected`** folder (via *Open output folder*).
2. Fix each PDF in **Adobe Acrobat Pro** — Reading Order tool, alt-text,
   table editor — and **save over the same file**.
3. Back in SPEADE, click the document again. The facts row will say *"Edited
   since processing"* — that's expected and fine.
4. Click **Approve**. The app re-checks the *fixed* bytes, records the
   decision, and moves it to `approved`.

A document only ever counts as done when it sits in `approved` with your
approval recorded — fixing without re-approving leaves it rejected.

## History

The **History** button shows everything the app has ever done, newest first —
when each document was processed, and every approve/reject with the reviewer's
name. This record cannot be edited; it is the project's proof that every
shipped document was human-approved. **Export history** (top of the History
view) saves the complete record as a spreadsheet (CSV) in the output folder —
use it when an administrator asks for this PC's records.

## Where everything lives

Inside the `speade-desktop` folder:

| folder | contents |
|---|---|
| `data\inbox` | the source PDFs you added — untouched, ever |
| `data\outbox` | drafts awaiting review — PDFs only |
| `data\outbox\approved` | approved documents — the ready-to-share shelf |
| `data\outbox\rejected` | rejected documents — fix in Acrobat, then re-approve |
| `data\sidecars` *(hidden)* | one record file per document — its full history |
| `data\audit` *(hidden)* | the permanent log — one line per run and decision |

The two hidden folders are the app's internal records — leave them alone. When
an approved document is sent onward, **send the PDF only**.

## If something goes wrong

| symptom | likely cause / fix |
|---|---|
| the exe won't start | the app isn't signed/allowlisted on this machine — contact IT (see `docs/deployment.md`) |
| every scan says "text recognition is not installed" or checks say the validator is unavailable | a system tool is missing on this PC — point IT at `docs/runbook.md` |
| documents say "Not tagged, the tagging engine is missing or blocked on this PC" | the tagging tool is absent, or Windows security is blocking it — IT can fix it by re-running `scripts\setup-machine.ps1`; documents still reach you for review meanwhile |
| a document says "being processed right now" | a batch is running — it will appear when processing finishes |
| a document says "password-protected or damaged" | SPEADE never guesses passwords and can't fix corrupt files — get a usable copy from the document owner and add it again |
| the list seems stale | click **Refresh list** |
| something looks wrong mid-review | close and reopen the app; nothing is lost (everything lives in files, not the app) |

## The three rules

1. **Originals are sacred** — SPEADE never edits your source PDF, and neither
   should you; corrections happen on the outbox draft.
2. **Nothing ships without a human** — a draft is not an accessible document
   until someone clicks Approve.
3. **When unsure, reject and ask** — the queue can wait; students relying on
   the document can't re-read it.

One more check that no software can do: how the document *sounds*. A short
screen-reader spot check on a few approved documents each term keeps everyone
honest — see `docs/screen-reader-check.md`.
