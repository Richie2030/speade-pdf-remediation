"""Tests for the desktop js_api bridge (src/speade/desktop/api.py).

Headless: SpeadeApi is plain Python over speade.service, so no pywebview and no
window are needed (window-bound methods like add_pdfs are exercised only for
their no-window guard). Every bridge return value must survive json.dumps --
that IS the bridge contract: pywebview serialises it to the JS side."""

from __future__ import annotations

import json
from pathlib import Path

from speade.desktop.api import SpeadeApi
from speade.validation.verapdf import VeraResult

PDF_BYTES = b"%PDF-1.7\n%%EOF\n"


def _api(tmp_path: Path) -> SpeadeApi:
    config = tmp_path / "config.yaml"
    config.write_text(
        "io:\n  local:\n    inbox: inbox\n    outbox: outbox\n"
        "pipeline:\n  stages:\n    passthrough: noop\n"
        "audit:\n  log_path: audit/audit.jsonl\n",
        encoding="utf-8",
    )
    (tmp_path / "inbox").mkdir()
    return SpeadeApi(config)


def test_workspace_is_json_safe_strings(tmp_path):
    ws = _api(tmp_path).workspace()

    json.dumps(ws)  # the bridge contract
    assert ws["inbox"] == str((tmp_path / "inbox").resolve())
    assert ws["stages"] == {"passthrough": "noop"}


def test_reviewer_default_is_the_os_login(tmp_path):
    reviewer = _api(tmp_path).reviewer_default()
    assert isinstance(reviewer, str) and reviewer  # lab PCs: the student number


def test_run_batch_then_list_queue_round_trip(tmp_path):
    api = _api(tmp_path)
    (tmp_path / "inbox" / "a.pdf").write_bytes(PDF_BYTES)

    batch = api.run_batch()
    queue = api.list_queue()

    json.dumps(batch)
    json.dumps(queue)
    assert [(item["file"], item["ok"]) for item in batch] == [("a.pdf", True)]
    assert [(item["file"], item["status"]) for item in queue] == [("a.pdf", "draft")]


def test_decide_records_and_returns_json_safe(tmp_path, monkeypatch):
    from speade import service

    api = _api(tmp_path)
    (tmp_path / "inbox" / "a.pdf").write_bytes(PDF_BYTES)
    api.run_batch()
    monkeypatch.setattr(
        service.verapdf,
        "validate",
        lambda pdf, profile="ua1", cli=None: VeraResult(passed=True, profile=profile),
    )

    result = api.decide("a.pdf", reviewer="s123456", approve=True)

    json.dumps(result)
    assert result == {
        "file": "a.pdf",
        "verapdf_passed": True,
        "failed_clauses": [],
        "status": "approved",
        "reviewer": "s123456",
    }
    assert [item["status"] for item in api.list_queue()] == ["approved"]


def test_decide_resolves_names_under_the_outbox_only(tmp_path, monkeypatch):
    """The bridge takes file NAMES; path components must not escape the outbox."""
    from speade import service

    api = _api(tmp_path)
    (tmp_path / "inbox" / "a.pdf").write_bytes(PDF_BYTES)
    api.run_batch()
    monkeypatch.setattr(
        service.verapdf,
        "validate",
        lambda pdf, profile="ua1", cli=None: VeraResult(passed=True, profile=profile),
    )

    result = api.decide("../../inbox/a.pdf", reviewer="x", approve=True)

    assert result["file"] == "a.pdf"  # Path(...).name stripped the traversal


def test_load_pdf_returns_a_data_uri(tmp_path):
    api = _api(tmp_path)
    (tmp_path / "inbox" / "a.pdf").write_bytes(PDF_BYTES)
    api.run_batch()

    loaded = api.load_pdf("a.pdf")

    assert loaded["data_uri"].startswith("data:application/pdf;base64,")
    missing = api.load_pdf("nope.pdf")
    assert "error" in missing


def test_add_pdfs_without_a_window_is_a_clean_error(tmp_path):
    result = _api(tmp_path).add_pdfs()
    assert result == {"error": "window not ready"}
