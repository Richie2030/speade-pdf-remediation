"""The localhost web delivery of the review client.

The SAME static ui/ the desktop window hosts, served from a FastAPI app bound
to 127.0.0.1 only -- the browser is just a different window onto the same
speade.service engine. Nothing is hosted and nothing leaves the machine, so
there is no auth, no TLS, and no IT service to run.

One UI, two launchers: the only frontend file that differs from the desktop
delivery is api.js (fetch() here, the pywebview js_api bridge there). app.js,
index.html, and style.css are served from speade/desktop/ui unchanged.

Run with:  uv run python -m speade.web   (needs the `web` extra)
"""
