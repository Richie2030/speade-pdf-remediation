# veraPDF clause reference: every PDF/UA-1 failure code

What the codes in the automatic check mean. When veraPDF finds a problem it
reports a `clause` and a `testNumber` from its PDF/UA-1 validation profile;
SPEADE joins them as `clause-testNumber`. So `7.18.3-1` is clause 7.18.3 of
ISO 14289-1 (the PDF/UA-1 standard), first test. The clause numbers are the
section numbers of that standard, which is why they start at 5 and are not
contiguous.

Source: the `PDFUA-1.xml` validation profile shipped inside veraPDF 1.30.2
(the pinned validator, see `docs/tech-stack.md`). It contains 106 rules;
all 106 are listed below. Gaps in the numbering (no 7.2-35, nothing under
7.6, 7.8, 7.12 to 7.14, 7.17 or 7.19) are real gaps in the profile: veraPDF
defines no machine-checkable test there.

The "who fixes it" column is a triage guide, not a rule:

- **Automatic (SPEADE)**: the pipeline stamps this itself (metadata, language,
  title, tagged-PDF flags). Seeing one of these on SPEADE output means a
  pipeline step failed; re-run the document.
- **Reviewer in Acrobat**: a tagging or content judgement (alt text, headings,
  table structure). Fixed in Adobe Acrobat Pro, per `docs/acrobat-guide.md`.
- **Source document / IT**: baked into the original file (fonts, encryption,
  multimedia). Fix the source and re-export, or escalate.

## Families at a glance

| Family | Theme |
|---|---|
| 5 | PDF/UA identification metadata (the `pdfuaid` stamp) |
| 6.1 | File format basics (the PDF header) |
| 6.2 | Declared as a tagged PDF (MarkInfo) |
| 7.1 | Tagging fundamentals: structure tree, real content vs artifacts, title |
| 7.2 | Natural language declarations; table, list and TOC nesting rules |
| 7.3 | Figures need alt text |
| 7.4 | Heading levels and heading structure |
| 7.5 | Table header cells and scope |
| 7.7 | Formulas need alt text |
| 7.9 | Notes (footnotes/endnotes) need unique IDs |
| 7.10 | Optional content (layers) |
| 7.11 | Embedded files |
| 7.15 | Dynamic XFA forms (banned) |
| 7.16 | Encryption must not block assistive technology |
| 7.18 | Annotations: tab order, links, form widgets, multimedia |
| 7.20 | Form XObjects |
| 7.21 | Fonts: embedding, encodings, Unicode mapping |

## The full rule table

| Code | What it checks | Who fixes it |
|---|---|---|
| 5-1 | XMP metadata includes the PDF/UA identification schema (`pdfuaid`) | Automatic (SPEADE) |
| 5-2 | `pdfuaid:part` is 1 (the file claims PDF/UA-1, not another part) | Automatic (SPEADE) |
| 5-3 | The "part" property uses the `pdfuaid` namespace prefix | Automatic (SPEADE) |
| 5-4 | The "amd" property uses the `pdfuaid` namespace prefix | Automatic (SPEADE) |
| 5-5 | The "corr" property uses the `pdfuaid` namespace prefix | Automatic (SPEADE) |
| 6.1-1 | File header is `%PDF-1.n` (n = 0 to 7) followed by one line ending | Automatic (SPEADE) |
| 6.2-1 | Catalog has a MarkInfo dictionary with Marked = true (declares a tagged PDF) | Automatic (SPEADE) |
| 7.1-1 | Content marked as decorative (Artifact) does not sit inside tagged real content | Reviewer in Acrobat |
| 7.1-2 | Tagged real content does not sit inside content marked as decorative | Reviewer in Acrobat |
| 7.1-3 | Every piece of page content is either an Artifact or tagged real content | Reviewer in Acrobat |
| 7.1-4 | The Suspects flag is false (no "tagging may be wrong" marker left set) | Automatic (SPEADE) |
| 7.1-5 | Every custom tag name is role-mapped to a standard structure type | Reviewer in Acrobat |
| 7.1-6 | The role map has no circular mappings | Reviewer in Acrobat |
| 7.1-7 | Standard tag types are not remapped to something else | Reviewer in Acrobat |
| 7.1-8 | The catalog contains a proper XMP metadata stream | Automatic (SPEADE) |
| 7.1-9 | Document metadata contains a `dc:title` (set in the app's title editor) | Automatic (SPEADE) |
| 7.1-10 | DisplayDocTitle is true, so viewers show the title instead of the filename | Automatic (SPEADE) |
| 7.1-11 | The document has a structure tree (StructTreeRoot); i.e. it is actually tagged | Automatic (SPEADE) |
| 7.1-12 | Every structure element records its parent (P entry); tag tree integrity | Reviewer in Acrobat |
| 7.2-2 | A language is determinable for bookmark (outline) text | Automatic (SPEADE) |
| 7.2-3 | A Table contains only TR, THead, TBody, TFoot and Caption children | Reviewer in Acrobat |
| 7.2-4 | Every TR (table row) sits inside Table, THead, TBody or TFoot | Reviewer in Acrobat |
| 7.2-5 | Every THead sits inside a Table | Reviewer in Acrobat |
| 7.2-6 | Every TBody sits inside a Table | Reviewer in Acrobat |
| 7.2-7 | Every TFoot sits inside a Table | Reviewer in Acrobat |
| 7.2-8 | Every TH (header cell) sits inside a TR | Reviewer in Acrobat |
| 7.2-9 | Every TD (data cell) sits inside a TR | Reviewer in Acrobat |
| 7.2-10 | A TR contains only TH and TD cells | Reviewer in Acrobat |
| 7.2-11 | A Table has at most one THead | Reviewer in Acrobat |
| 7.2-12 | A Table has at most one TFoot | Reviewer in Acrobat |
| 7.2-13 | A Table with a TFoot also has at least one TBody | Reviewer in Acrobat |
| 7.2-14 | A Table with a THead also has at least one TBody | Reviewer in Acrobat |
| 7.2-15 | Table cells do not overlap each other | Reviewer in Acrobat |
| 7.2-16 | A table Caption is the first or last child of its Table | Reviewer in Acrobat |
| 7.2-17 | Every LI (list item) sits inside an L (list) | Reviewer in Acrobat |
| 7.2-18 | Every LBody sits inside an LI | Reviewer in Acrobat |
| 7.2-19 | An L contains only L, LI and Caption children | Reviewer in Acrobat |
| 7.2-20 | An LI contains only Lbl and LBody children | Reviewer in Acrobat |
| 7.2-21 | A language is determinable for ActualText replacement text | Automatic (SPEADE) |
| 7.2-22 | A language is determinable for Alt text | Automatic (SPEADE) |
| 7.2-23 | A language is determinable for E (abbreviation expansion) text | Automatic (SPEADE) |
| 7.2-24 | A language is determinable for annotation descriptions (Contents) | Automatic (SPEADE) |
| 7.2-25 | A language is determinable for form-field tooltips (TU) | Automatic (SPEADE) |
| 7.2-26 | Every TOCI (contents entry) sits inside a TOC | Reviewer in Acrobat |
| 7.2-27 | A TOC contains only TOC, TOCI and Caption children | Reviewer in Acrobat |
| 7.2-28 | A TOC Caption is its first child | Reviewer in Acrobat |
| 7.2-29 | Any Lang value is a valid language tag (e.g. `en-GB`, RFC 3066) | Automatic (SPEADE) |
| 7.2-30 | A language is determinable for ActualText on inline Span content | Automatic (SPEADE) |
| 7.2-31 | A language is determinable for Alt text on inline Span content | Automatic (SPEADE) |
| 7.2-32 | A language is determinable for E text on inline Span content | Automatic (SPEADE) |
| 7.2-33 | A language is declared for the document metadata (title) | Automatic (SPEADE) |
| 7.2-34 | A language is determinable for page text (the document /Lang) | Automatic (SPEADE) |
| 7.2-36 | A THead contains only TR children | Reviewer in Acrobat |
| 7.2-37 | A TBody contains only TR children | Reviewer in Acrobat |
| 7.2-38 | A TFoot contains only TR children | Reviewer in Acrobat |
| 7.2-39 | A Table has at most one Caption | Reviewer in Acrobat |
| 7.2-40 | An L (list) Caption is its first child | Reviewer in Acrobat |
| 7.2-41 | All table columns span the same number of rows (row spans counted) | Reviewer in Acrobat |
| 7.2-42 | All table rows span the same number of columns (column spans counted) | Reviewer in Acrobat |
| 7.2-43 | Same check as 7.2-42, reported with the differing column counts | Reviewer in Acrobat |
| 7.3-1 | Every Figure has alt text or replacement text (human-authored in SPEADE) | Reviewer in Acrobat |
| 7.4.2-1 | Heading levels do not skip (no H1 straight to H3) | Reviewer in Acrobat |
| 7.4.4-1 | A tag-tree node has at most one child H (generic heading) tag | Reviewer in Acrobat |
| 7.4.4-2 | The document does not mix generic H tags with numbered Hn tags | Reviewer in Acrobat |
| 7.4.4-3 | The document does not mix numbered Hn tags with generic H tags | Reviewer in Acrobat |
| 7.5-1 | Every data cell can be matched to its header cells (Headers/IDs or TH Scope) | Reviewer in Acrobat |
| 7.5-2 | TD Headers references point at header IDs that actually exist | Reviewer in Acrobat |
| 7.7-1 | Every Formula has Alt or ActualText describing the maths | Reviewer in Acrobat |
| 7.9-1 | Every Note (footnote/endnote) has an ID | Reviewer in Acrobat |
| 7.9-2 | Note IDs are unique across the document | Reviewer in Acrobat |
| 7.10-1 | Every optional-content (layer) configuration has a Name | Source document / IT |
| 7.10-2 | Optional-content configurations do not use the AS (auto-state) key | Source document / IT |
| 7.11-1 | Embedded-file specifications carry both F and UF filenames | Source document / IT |
| 7.15-1 | No dynamic XFA forms | Source document / IT |
| 7.16-1 | If encrypted, permissions allow assistive technology to read the content | Source document / IT |
| 7.18.1-1 | Annotations (other than Widget/PrinterMark/Link) sit inside an Annot tag | Reviewer in Acrobat |
| 7.18.1-2 | Visible annotations have a description (Contents or an Alt on their tag) | Reviewer in Acrobat |
| 7.18.1-3 | Form fields have a tooltip (TU) or their widgets have Alt descriptions | Reviewer in Acrobat |
| 7.18.2-1 | No TrapNet (prepress trapping) annotations | Source document / IT |
| 7.18.3-1 | Pages with annotations set tab order to follow the structure (Tabs = S) | Automatic (SPEADE) |
| 7.18.4-1 | Every Widget (form control) annotation sits inside a Form tag | Reviewer in Acrobat |
| 7.18.4-2 | A Form tag without a Role attribute wraps exactly its one widget | Reviewer in Acrobat |
| 7.18.5-1 | Every Link annotation sits inside a Link tag | Reviewer in Acrobat |
| 7.18.5-2 | Every Link annotation has an alternate description (Contents) | Reviewer in Acrobat |
| 7.18.6.2-1 | Multimedia clips declare their content type (CT key) | Source document / IT |
| 7.18.6.2-2 | Multimedia clips have an Alt description | Source document / IT |
| 7.18.8-1 | PrinterMark annotations are artifacts, never in the structure tree | Source document / IT |
| 7.20-1 | No reference XObjects (imported pages from other PDFs) | Source document / IT |
| 7.20-2 | A Form XObject with tagged content is not reused on multiple pages | Source document / IT |
| 7.21.3.1-1 | Composite (Type 0) font encoding and CIDSystemInfo are consistent | Source document / IT |
| 7.21.3.2-1 | Embedded Type 2 CID fonts have a valid CIDToGIDMap | Source document / IT |
| 7.21.3.3-1 | Non-standard CMaps (CJK encodings) are embedded in the file | Source document / IT |
| 7.21.3.3-2 | An embedded CMap's writing mode matches its dictionary | Source document / IT |
| 7.21.3.3-3 | CMaps do not reference other non-standard CMaps | Source document / IT |
| 7.21.4.1-1 | Every font used for rendering is embedded in the file | Source document / IT |
| 7.21.4.1-2 | Embedded fonts contain every glyph the document uses | Source document / IT |
| 7.21.4.2-1 | A Type 1 font's CharSet lists exactly the glyphs in the font program | Source document / IT |
| 7.21.4.2-2 | A CID font's CIDSet identifies exactly the glyphs in the embedded subset | Source document / IT |
| 7.21.5-1 | Glyph widths in the font dictionary and the font program agree | Source document / IT |
| 7.21.6-1 | Non-symbolic TrueType fonts contain usable character-map (cmap) tables | Source document / IT |
| 7.21.6-2 | Non-symbolic TrueType encodings map cleanly to standard glyph names | Source document / IT |
| 7.21.6-3 | Symbolic TrueType fonts do not declare an Encoding entry | Source document / IT |
| 7.21.6-4 | Symbolic TrueType cmap has one encoding, or includes Microsoft Symbol | Source document / IT |
| 7.21.7-1 | Every character maps to Unicode (ToUnicode). Old glyphless OCR text layers fail this; SPEADE rebuilds the layer on scans, so a hit here means a born-digital font problem | Source document / IT |
| 7.21.7-2 | Unicode mappings are real characters (not 0, U+FEFF or U+FFFE) | Source document / IT |
| 7.21.8-1 | Text never prints the .notdef (missing-glyph) placeholder | Source document / IT |

## Reading a failure honestly

Not every rule above can be triggered by SPEADE's own output; most of the
metadata and language rows exist so the stamp steps are verifiable, and the
font and multimedia rows describe problems SPEADE inherits, never creates.
The common codes on real documents are the tagging-structure families (7.1
to 7.5) and fonts (7.21).

The validator is advisory. A PDF/UA pass does not prove the document reads
well (a wrong-but-well-formed tag tree passes), and a residual failure does
not always block shipping a materially improved document. The human gate is
authoritative over the validator, in both directions.
