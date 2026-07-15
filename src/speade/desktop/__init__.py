"""speade.desktop -- the desktop client (docs/decisions/frontend-delivery.md).

A pywebview (BSD) native window hosting the HTML/JS review UI in `ui/`, wired
to the shared core through `api.SpeadeApi` (pywebview's js_api bridge). No web
framework, no server, no ports: UI buttons call service-layer functions
directly. Launch: `uv run python -m speade.desktop` (needs `--extra desktop`).
"""
