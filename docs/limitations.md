# Limitations — permanent product constraints

What SPEADE v1 deliberately does not do, and the boundaries users should
expect. These are design decisions and physics, not bugs; none will change
(v1 is frozen).

## By design (scope decisions)

- **No network, no integrations.** No Canvas/Ally connection, no cloud, no AI
  text generation. Everything is local files.
- **Image descriptions are never machine-written.** Alt-text is authored by
  the human reviewer — a deliberate quality decision. It is written **in the
  app** (the *Alt Text* stepper walks every image), not only in Acrobat.
- **In-app editing covers the common corrections, not all of them.** You can
  retag an element, write an image description, mark something decorative,
  remove a wrapper tag while keeping its contents, move a tag earlier/later in
  the reading order, strip all tags to start over, drag a rectangle on the page
  to select many tags and merge / retag / decorate them in one action, and step
  back through edits one at a time (20-deep per session, cleared by a decision
  or a reprocess). Deleting a tag that holds content directly is refused by
  design: it would leave untagged content, a worse defect than a wrong tag type.
  The drag tool works at three grains: whole tags (merge / retag / decorate),
  lines within a tag (drag over part of a tag - the fix for a heading swallowed
  into a paragraph), and content with no tag at all (drag over it — text the
  engine missed, or decorated content being brought back; on a page drawn in
  an unusual way this refuses safely and defers to Acrobat). What still needs
  Acrobat: selecting part of a single *line* (a word or phrase — content is
  marked line-by-line and the tools never cut finer than the marking), table
  header/scope editing, and moving a tag between different groups.
- **Password-protected PDFs are rejected, never opened.** SPEADE does not try
  passwords. Obtain an unlocked copy.
- **Per-Windows-login working data.** Queues, records, and history belong to
  one login on one machine; there is no shared queue or multi-reviewer
  coordination.
- **A human decision is mandatory.** There is no "auto-approve"; the validator
  is advisory by design.

## Accessibility of the app itself

The review UI is designed to WCAG-informed standards: all text measures ≥5:1
contrast (most ≥11:1), 16px base text, 40px+ click targets, visible two-tone
focus indicators, and plain-language labels throughout. The document queue and
the tag tree are keyboard-operable (Tab to reach them, arrow keys to move,
Enter to open), status and edit results are announced to screen readers, and
animated scrolling respects the OS "reduce motion" setting.

Honest boundary: the heart of the review — comparing the drawn page against
its tag boxes, and the drag-select bulk actions — is inherently visual and
mouse-driven. A blind reviewer cannot perform the visual-comparison task
itself; every *decision and correction* on a selected tag is reachable by
keyboard, but drag-only actions (merge, carve, tag-untagged) have no keyboard
equivalent in v1.

## Automation boundaries (what the pipeline can and cannot see)

- **Heading detection needs a visible signal.** Larger type is detected;
  ALL-CAPS body-sized headings are detected; a heading marked *only* by bold
  or underline at body size cannot be recognised from OCR — promote it in
  Acrobat.
- **Photo detection in scans is heuristic.** Clear photographs are found and
  tagged as images; faint or unusual figures can be missed (they were never
  tagged before either), and occasionally a non-photo region may be boxed —
  the reviewer sees both cases in the tags view.
- **OCR quality is bounded by scan quality.** Clean scans read essentially
  perfectly; noisy typewriter scans will contain recognition errors, and
  handwriting is not supported. Obvious garbage is filtered conservatively —
  faint-but-real text is kept, so some noise can survive on poor scans.
- **Mixed documents:** pages with real text are tagged as-is; image-only pages
  inside an otherwise-digital document are not OCR'd (the document is flagged
  for the reviewer to check coverage).
- **Born-digital documents get the tagging engine's judgement, unaltered.**
  SPEADE improves the *scanned* path (it builds that text layer itself); for a
  PDF that already has real text, the structure is whatever the engine derives.
  Measured on a two-column academic paper (18 pages, Frontiers journal): the
  title, section headings, paragraphs, lists and the figure with its caption
  were tagged, but most *sub*-section headings came out as ordinary paragraphs
  and needed promoting in Acrobat. Expect complex multi-column layouts to need
  the most human correction.
- **Hyperlink-heavy documents** (academic papers with cited references) produce
  hundreds of Link tags — real content, but noise while reviewing structure.
  The tags view hides them behind a "Show N links" checkbox; they remain in the
  PDF either way.
- **Default document language is English** and is stamped automatically; the
  reviewer sets the correct language per document in the app (title and
  language editor) before approving.

## Operational envelopes (measured)

- Throughput: roughly 15–40 seconds per document (scans slowest; ~15–18 s per
  scanned page). 100 two-page scans ≈ one hour. Stability is not batch-size
  limited (memory is flat); only time is.
- The automatic check adds ~3 s per document (Java startup).
- Very large single documents work but render lazily in the tags view; the
  tree is capped at 3000 entries for pathological files (a note appears).
