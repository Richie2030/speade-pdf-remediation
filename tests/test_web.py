"""Tests for the localhost web delivery (src/speade/web/server.py).

Skipped wholesale when the `web` extra (fastapi) or httpx (the TestClient
transport) is not installed -- `uv sync --all-extras` exercises them. The web
app is a thin HTTP skin over the same SpeadeApi bridge the desktop uses, so
these tests cover the skin: routing, serialisation, and the two browser
substitutes (upload, PDF-by-URL) -- not the engine, which test_service covers.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from speade import service  # noqa: E402
from speade.validation.verapdf import VeraResult  # noqa: E402
from speade.web.server import create_app  # noqa: E402

PDF_BYTES = b"%PDF-1.7\n%%EOF\n"


@pytest.fixture(autouse=True)
def _hermetic_verapdf(monkeypatch):
    monkeypatch.setattr(
        service.verapdf,
        "validate",
        lambda pdf, profile="ua1", cli=None: VeraResult(passed=True, profile=profile),
    )


def _client(tmp_path: Path) -> TestClient:
    config = tmp_path / "config.yaml"
    config.write_text(
        "io:\n  local:\n    inbox: inbox\n    outbox: outbox\n"
        "pipeline:\n  stages:\n    passthrough: noop\n"
        "audit:\n  log_path: audit/audit.jsonl\n",
        encoding="utf-8",
    )
    (tmp_path / "inbox").mkdir()
    return TestClient(create_app(config))


def test_serves_the_shared_ui_with_the_web_api_seam(tmp_path):
    client = _client(tmp_path)

    index = client.get("/")
    assert index.status_code == 200
    assert "SPEADE" in index.text

    # THE seam: /api.js must be the fetch() implementation, not the desktop one.
    api_js = client.get("/api.js")
    assert api_js.status_code == 200
    assert "fetch(" in api_js.text
    assert "pywebview" not in api_js.text.replace("the pywebview js_api bridge", "")

    # the rest of the ui/ folder is served untouched from desktop/ui.
    assert client.get("/app.js").status_code == 200
    assert client.get("/style.css").status_code == 200


def test_upload_process_decide_round_trip(tmp_path):
    client = _client(tmp_path)

    up = client.post("/api/upload", files=[("files", ("a.pdf", PDF_BYTES, "application/pdf"))])
    assert up.json() == {"copied": ["a.pdf"]}

    start = client.post("/api/run_batch_start")
    assert start.json() == {"started": True}
    for _ in range(500):
        status = client.get("/api/run_batch_status").json()
        if not status.get("running"):
            break
        time.sleep(0.02)
    assert status["running"] is False

    queue = client.get("/api/list_queue").json()
    assert [(i["file"], i["status"]) for i in queue] == [("a.pdf", "draft")]

    pdf = client.get("/api/pdf", params={"file": "a.pdf"})
    assert pdf.status_code == 200
    assert pdf.content == PDF_BYTES

    decided = client.post(
        "/api/decide", json={"file": "a.pdf", "reviewer": "s123456", "approve": True}
    )
    assert decided.json()["status"] == "approved"
    # the moved file is still reachable by name (find_output checks approved/).
    assert client.get("/api/pdf", params={"file": "a.pdf"}).status_code == 200


def test_upload_strips_paths_and_non_pdfs(tmp_path):
    client = _client(tmp_path)

    up = client.post(
        "/api/upload",
        files=[
            ("files", ("../../evil.pdf", PDF_BYTES, "application/pdf")),
            ("files", ("notes.txt", b"nope", "text/plain")),
        ],
    )

    assert up.json() == {"copied": ["evil.pdf"]}
    assert (tmp_path / "inbox" / "evil.pdf").is_file()
    assert not (tmp_path / "inbox" / "notes.txt").exists()


def test_missing_pdf_is_a_json_404(tmp_path):
    client = _client(tmp_path)
    r = client.get("/api/pdf", params={"file": "nope.pdf"})
    assert r.status_code == 404
    assert r.json() == {"error": "not found: nope.pdf"}
