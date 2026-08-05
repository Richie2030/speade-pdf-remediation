# Decision record: tagging-engine cost (G4 / PDFix vs free)

- **Status:** **CONFIRMED (2026-08-05)** — the spike ran and the free path won.
  OpenDataLoader PDF (Apache-2.0, Java CLI) plus a pikepdf PDF/UA-1 finish
  passes veraPDF UA-1 on real documents, so **v1 ships at zero licence cost and
  PDFix was never purchased**. The engine choice itself is recorded in
  [`pdf-engines.md`](pdf-engines.md); versions in `../tech-stack.md`.
- **Date:** 2026-06-28 (confirmed 2026-08-05)
- **Relates to:** G4 (pdfix licence/cost), the `tag` stage (`src/speade/stages/tag.py`),
  the veraPDF scorer (`src/speade/validation/verapdf.py`), `config.yaml` `tag:` selector

## Question

Does building the PDF tagging stage necessarily cost UCC money? The project's
premise is "on-prem = no new per-use cost," but automatic PDF/UA tagging is the
one stage where a paid proprietary engine (PDFix) is the obvious candidate.

## Context — separate two kinds of "cost"

1. **New licence spend** (money UCC is not already paying). This is what G4 is
   about, and it is **avoidable**.
2. **Costs that exist regardless** of engine choice — human reviewer time
   (the dominant cost, which this project aims to *reduce*) and ongoing student
   maintenance. These are not licence fees and are out of scope for this record.
   (There is no VLM/alt-text stage — LLMs are ruled out — so no GPU/Boole
   inference cost is incurred for it.)

**Key fact that reframes G4 (confirmed 2026-06-28):** UCC's Acrobat Pro licence
**already covers the Student Partners** — there is a university room with ~20
Acrobat Pro seats that any student can use year-round. Tooling UCC already owns
adds **zero new cost**, so the Acrobat path is a first-class *free-to-UCC*
baseline, not a paid option. (The ~20 seats ≈ the ~20-SP cohort, so it scales
with the cohort but is a real concurrency ceiling for Acrobat-based tagging —
feed this into the reviewer-capacity model, SCALE1.)

**Priority (the value lens):** structural tagging is **~80–90% of Student-Partner
remediation effort** — it is *the* workload to attack, not a stage to punt to
manual work. So the lead metric is **reviewer-minutes-saved on tagging**, and
cost is a *bounded constraint, not the chooser*: across ~200→1,000 modules the
SP-labour saved by a materially better auto-tagger can dwarf any subscription.
Don't reflexively pick the cheapest engine — pick the one that cuts the most
correction time, then confirm its cost is fundable. **Caveat:** "automate
tagging" means auto-generating the best **starting tree** so correction drops
from hours to minutes — *not* removing the human gate (a wrong-but-valid reading
order still passes veraPDF). The 80–90% is **reduced, not zeroed**.

**Baseline (confirmed 2026-06-28):** SPs already run **Acrobat auto-tag → manual
fix**. So Acrobat's auto-tag is the **incumbent baseline**, and the bulk of the
80–90% is the *manual correction* after it (reading order, tables, headings,
alt-text — the human gate's irreducible work). The pipeline therefore adds value
only by: **(i)** a cleaner starting tree that needs *less correction than
Acrobat's*, and/or **(ii)** triage + batch handling + faster correction tooling.
The spike must measure **correction-time vs the Acrobat-auto-tag baseline**,
over-sampling complex docs (tables / multi-column / scanned) where Acrobat's
auto-tag is weakest.

**EULA caveat (shapes the architecture):** Adobe forbids "service-bureau" /
headless server-side automation. Acrobat may be used **on the SP's own machine,
human-in-the-loop** (matches the mandatory human gate and the current workflow),
but **cannot** be the engine inside a fully-automated headless pipeline.

## Options and their cost to UCC

| Path | New licence cost | Notes |
|---|---|---|
| **Acrobat Pro auto-tag on the SP's machine** + free veraPDF + free Python pipeline | **None** (already owned) | Auto-tag is ~60% scaffolding, **not** guaranteed PDF/UA; human verifies. Desktop/human-in-loop only — not headless. |
| **opendataloader-pdf free tier** (Apache-2.0, headless auto-tag) + veraPDF + human gate | **None** *if* its free Tagged-PDF output passes veraPDF / the gold-set | The free tier emits a *generic* Tagged PDF; the **guaranteed PDF/UA export is a paid Enterprise tier**. Whether the free output is good enough is **unproven — must be tested on our corpus**. |
| **pikepdf** | None | **Cannot auto-tag** — low-level primitives only. Ruled out as a tagger. |
| **PDFix SDK (Enterprise, offline single-server)** | **New subscription** (quote-only) | True headless automation at scale. See "PDFix specifics" below. |
| Cloud APIs (Adobe Auto-Tag API, Textract, …) | New per-use cost + GDPR gate | Off the table unless the cloud gate clears (budget + GDPR sign-off). |

## Decision

1. **Automating the tagging first pass is the priority** — it is ~80–90% of SP
   effort, so it is where the pipeline must save time, not a stage to punt to
   manual work. Choose the `tag` engine by **reviewer-minutes-saved on a real
   corpus** (including correction time), with veraPDF (+ a second
   independent-lineage check) as the structural gate — not isolated conformance.
2. **Test the zero-new-cost engines first** (Acrobat auto-tag; opendataloader-free
   if its output passes veraPDF) — they cost nothing and may already capture most
   of the win. But **do not pre-favour cheapest over materially better**: if
   PDFix (paid) cuts substantially more correction time, paying is justified
   because SP labour is the dominant cost.
3. **PDFix is the paid candidate-to-beat,** earning a `config.yaml` slot only if
   the spike shows it beats the free baselines by more than its fee is worth in
   saved SP hours.
4. **G4 stays worth doing** (get the PDFix number on record for Fergal/Aaron);
   the build proceeds behind the swappable `tag` interface so the winning engine
   drops in by config once the spike decides.

## PDFix specifics (verified 2026-06-28)

- The product for programmatic on-prem tagging is the **PDFix SDK** (Python),
  **not** PDFix Desktop (a per-seat GUI, ~EUR 1,000/user/yr).
- Auto-tagging is **Enterprise-tier only**; pricing is **quote-only**
  (no public SDK prices), **subscription** (no perpetual option).
- The on-prem SKU to request: a **flat-fee licence bound to a single server —
  unlimited pages, up to 4 concurrent processes**. **Avoid the per-page
  "volume" model.**
- **Offline caveat:** auto-tag's local AI engines are **IBM Docling + Paddle**
  (run via Docker); **Amazon Textract and OpenAI require cloud** — a fully
  offline deployment must use the local engines only.
- Unlicensed output is watermarked and substitutes `*` for characters.
- Contact: `support@pdfix.net` (EU) or the form at `pdfix.net/about-us/contact-us/`.

## Open questions to confirm

- ~~Acrobat seat coverage~~ — **CONFIRMED 2026-06-28:** covered, ~20-seat
  Acrobat Pro room, any student year-round. The cheapest v1 is genuinely available.
- **opendataloader-free quality:** does its free Tagged-PDF output pass veraPDF /
  the gold-set without the paid PDF/UA export? (Test in the spike.)
- **PDFix quote:** the actual annual figure for the offline single-server SKU
  (G4 deliverable to Fergal/Aaron).

## Consequences

- **The goal is to automate the tagging first pass** (the dominant 80–90% of SP
  effort), not just the periphery. A zero-new-cost route exists as a *floor*
  (Acrobat auto-tag on a lab seat + verify; or opendataloader-free if it proves
  out), but the target is whichever engine cuts the most correction time.
- A no-new-licence v1 is **achievable**, so "on-prem = no new per-use cost" stays
  defensible — but cost is the constraint, not the chooser.
- Paying for PDFix becomes a deliberate, evidence-backed upgrade justified by SP
  hours saved — not an assumed dependency, and not pre-rejected on price.
- **Risk (name it honestly):** the spike may find Acrobat's auto-tag is already
  competitive on our corpus — then the tagging win is *not* an engine swap but
  **triage + batch handling + faster correction tooling** (plus the things
  Acrobat can't do). That is a legitimate, defensible outcome (the plan already
  anticipates "Acrobat may win").

## Sources

- PDFix SDK / pricing / terms: https://pdfix.net/products/pdfix-sdk/ ·
  https://pdfix.net/pricing/sdk/ · https://pdfix.net/terms/ · https://pdfix.net/pdfix-sdk-auto-tagging/
- Adobe Acrobat accessibility auto-tag: https://helpx.adobe.com/acrobat/using/cloud-auto-tagging-accessibility-pdfs.html ·
  pricing https://www.adobe.com/acrobat/pricing.html
- pikepdf tagged-PDF status: https://github.com/pikepdf/pikepdf/issues/461
- opendataloader-pdf free vs paid: https://github.com/opendataloader-project/opendataloader-pdf
- veraPDF (validator/gate): https://verapdf.org/
