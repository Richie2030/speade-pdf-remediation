# Acrobat correction guide — the craft

What to actually *do* in Adobe Acrobat Pro when SPEADE sends a document to the
`rejected` folder. This is the human half of the pipeline: the app tells you
*what* needs attention (the tags view); this guide is *how* to fix it.
Audience: Student Partners. Time per document: typically 5–20 minutes.

The loop, always: fix in Acrobat → save over the same file in `rejected` →
back in SPEADE → **Approve**. Fixing without re-approving leaves the document
rejected.

## Set up Acrobat once

Open **View → Tools → Accessibility → Add**, and the same for **Tag** panel
(View → Show/Hide → Navigation Panes → Tags). The three tools you will live
in: the **Tags panel**, the **Reading Order tool** (in Accessibility), and
**Tags panel → right-click → Properties** (for alt-text). Turn on
"Highlight Content" in the Tags panel menu so clicking a tag outlines it on
the page — the same behaviour as SPEADE's tags view.

## Job 1 — write image descriptions (alt-text)

SPEADE marks every detected image; it never writes descriptions. In the Tags
panel, find each `<Figure>` → right-click → **Properties** → *Alternate Text*.

Writing good alt-text:

- **Say what it shows and why it's there**, in one or two sentences: "Bar
  chart: exam pass rates rising from 62% (2019) to 81% (2023)" — not "chart".
- **Don't** start with "image of…" — the screen reader already announces it's
  an image.
- **Charts/diagrams:** state the takeaway the sighted reader gets, plus key
  numbers if they matter.
- **Decorative images** (borders, flourishes, the scan of a coffee stain):
  don't describe them — mark them as **artifact** (Reading Order tool → select
  the region → *Background/Artifact*). Decoration read aloud is noise.
- Photos of text (a slide photographed into notes): transcribe the text.

## Job 2 — fix reading order

Listen-order equals the Tags panel's top-to-bottom order. If SPEADE's tree
order didn't match the sensible reading of the page (columns read across
instead of down, a caption before its section):

- Small fixes: **drag tags** up/down in the Tags panel.
- Bigger fixes: **Reading Order tool** — draw a box around content and assign
  it a type; the panel order follows.
- Multi-column pages are the classic case: order must be column 1 top-to-
  bottom, then column 2 — never left-to-right across both.

## Job 3 — fix wrong tags

- **Headings**: a heading tagged `<P>` → change the tag to `<H1>`/`<H2>`/…
  (Tags panel → double-click the tag name, or retag with the Reading Order
  tool). Keep levels hierarchical: H1 → H2 → H3, no skipping. This is the
  most common fix for typewriter-era documents where only bold marked a
  heading.
- **Lists** read as plain paragraphs → retag as `<L>` with `<LI>` items
  (Reading Order tool or manual retagging). Screen-reader users rely on
  "list with 5 items".
- **Tables**: use the Reading Order tool's **Table Editor** to mark header
  cells (`<TH>`) vs data cells (`<TD>`) and check row/column spans. A table
  without headers is a maze read aloud.
- **Junk tags** (OCR noise that survived, empty tags): delete the tag; if the
  content is real but decorative, artifact it.

## Job 4 — the final pass

1. Acrobat's own checker: **Accessibility → Full Check** — fix what it flags
   or note why it's acceptable.
2. Save (same filename, same `rejected` folder).
3. In SPEADE: select the document (it will say *"Edited since processing"* —
   expected), then **Approve**. The app re-validates the fixed file and moves
   it to `approved`.

## What not to spend time on

- Re-tagging documents SPEADE already tagged well — spot-check, don't redo.
- Perfecting decorative layout artefacts — artifact them and move on.
- Password-protected/corrupt sources — no amount of Acrobat fixes these;
  request a usable original.
- Alt-text novels — two good sentences beat two paragraphs.
