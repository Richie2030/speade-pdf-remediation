# Screen-reader spot check (NVDA)

veraPDF checks that a document *follows the rules*; it cannot tell you what the
document is actually *like to listen to*. This 15-minute check closes that gap.
Run it once on 2-3 approved documents before the first real distribution, and
again on a small sample each term. No special skills needed.

## One-time setup

1. Install **NVDA** (free, from nvaccess.org) on any Windows PC — no admin
   licence questions, it is open source.
2. Use **Adobe Acrobat Reader** (or Acrobat Pro) to open the PDFs — its screen
   reader support is the reference behaviour students will get.
3. Useful NVDA keys: `Insert+Down` read continuously · `H` next heading ·
   `K` next link · `G` next graphic · `Ctrl` stop speech · `Insert+Q` quit NVDA.

## The check — per document

Open an **approved** PDF from `outbox\approved\` with NVDA running, then:

| # | Listen for | Pass looks like |
|---|---|---|
| 1 | The window/document title when the file opens | A human title ("Week 3 Lecture Notes"), NOT a filename like "scan_0047.pdf" |
| 2 | Press `Insert+Down` — continuous reading from the top | Reading starts with real content; words are separate ("my friend Rita", never "myfriendrita"); sentences flow in the right order |
| 3 | Press `H` repeatedly | Jumps land on the document's actual headings, in order |
| 4 | A bulleted/numbered section | Announced as a list ("list with 5 items"), items read one by one |
| 5 | Press `G` (next graphic) | Real illustrations are announced with their description (the alt text written at review); no announcement of a meaningless full-page "figure" on every scanned page |
| 6 | Any table | Announced as a table; rows/columns navigable with Ctrl+Alt+arrows |
| 7 | Pronunciation sanity | The language sounds right — a Portuguese document read with Portuguese pronunciation means the language setting is correct |

## Recording the result

Note pass/fail per row in the review notes for that document (or a shared
sheet). Anything that fails goes back through Acrobat like any other
correction — then re-approve in the app so the audit trail stays true.

## Why this matters

A document can pass every automated rule and still be exhausting to listen
to — wrong reading order, junk between paragraphs, a "figure" announced on
every page. Five minutes of listening catches what no validator can.
