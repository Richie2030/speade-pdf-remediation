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


def test_structure_tree_and_page_image_endpoints(tmp_path):
    pikepdf = pytest.importorskip("pikepdf", reason="needs --extra tag")
    pytest.importorskip("pypdfium2", reason="needs --extra ocr")
    client = _client(tmp_path)
    doc = pikepdf.Pdf.new()
    doc.add_blank_page(page_size=(612, 792))
    doc.save(tmp_path / "inbox" / "a.pdf")
    client.post("/api/run_batch_start")
    deadline = time.monotonic() + 10
    while client.get("/api/run_batch_status").json().get("running"):
        assert time.monotonic() < deadline, "batch never finished"
        time.sleep(0.02)

    tree = client.get("/api/structure_tree", params={"file": "a.pdf"}).json()
    assert tree["tagged"] is False  # blank page: no tags yet, but the shape flows

    image = client.get("/api/page_image", params={"file": "a.pdf", "index": 0}).json()
    assert image["data_uri"].startswith("data:image/png;base64,")
    assert image["pages"] == 1


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


def test_module_upload_process_decide_round_trip(tmp_path):
    # the same flow scoped to a module: upload lands in the module's inbox,
    # the batch start takes the module, and the rel-id works everywhere.
    client = _client(tmp_path)

    up = client.post(
        "/api/upload",
        data={"module": "MG2001"},
        files=[("files", ("a.pdf", PDF_BYTES, "application/pdf"))],
    )
    assert up.json() == {"copied": ["MG2001/a.pdf"]}
    assert (tmp_path / "inbox" / "MG2001" / "a.pdf").read_bytes() == PDF_BYTES
    assert client.get("/api/modules").json() == {"modules": ["MG2001"]}

    start = client.post("/api/run_batch_start", json={"module": "MG2001"})
    assert start.json() == {"started": True}
    for _ in range(500):
        status = client.get("/api/run_batch_status").json()
        if not status.get("running"):
            break
        time.sleep(0.02)
    assert status["running"] is False

    queue = client.get("/api/list_queue").json()
    assert [(q["file"], q["module"]) for q in queue] == [("MG2001/a.pdf", "MG2001")]
    assert client.get("/api/pdf", params={"file": "MG2001/a.pdf"}).status_code == 200

    decided = client.post(
        "/api/decide", json={"file": "MG2001/a.pdf", "reviewer": "s1", "approve": True}
    )
    assert decided.json()["status"] == "approved"
    assert (tmp_path / "outbox" / "MG2001" / "approved" / "a.pdf").is_file()


def test_pdf_endpoint_maps_malformed_ids_to_404_not_500(tmp_path):
    # review-caught regression: find_output now RAISES on malformed rel-ids,
    # and this raw endpoint bypassed the bridge's ValueError -> not-found path.
    client = _client(tmp_path)
    for bad in ("a/b/c.pdf", "approved/x.pdf", "../x.pdf"):
        assert client.get("/api/pdf", params={"file": bad}).status_code == 404


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
