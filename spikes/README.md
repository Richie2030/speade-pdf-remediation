# spikes/ — throwaway experiments (not shipped)

Exploratory scripts that answer an open decision with data, then get deleted. They
are **not** part of `src/speade/`, carry **no** committed dependencies (run with
`uv run --with ...` so nothing enters `uv.lock` / the licence closure), and may be
rough. Delete a spike once its decision is recorded.

## `tag_engine_spike.py` — resolves **D5** (which tagging engine)

Measures whether the **free** OpenDataLoader tier can clear SPEADE's own veraPDF
PDF/UA-1 gate, so the tagging-engine choice is made by failed-clause counts, not
argument.

```bash
# tag + finish + score a folder of born-digital PDFs
uv run --with pikepdf --with opendataloader-pdf python spikes/tag_engine_spike.py path/to/pdfs

# same, but score the raw OpenDataLoader output (no pikepdf PDF/UA finish)
uv run --with pikepdf --with opendataloader-pdf python spikes/tag_engine_spike.py path/to/pdfs --no-finish
```

Needs a system **Java 11+** (OpenDataLoader engine) and **Docker** (veraPDF image);
the script preflights both and reports what's missing instead of crashing.

**Decision rule:** most files PASS → the free tier (+ pikepdf finish) is enough →
ship a fully-free/open tag stage. Most fail → read the common failed clauses; you
likely need the paid PDF/UA export (OpenDataLoader enterprise, same tool) or PDFix SDK.
