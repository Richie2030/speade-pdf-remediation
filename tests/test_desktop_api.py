"""Tests for the desktop js_api bridge (src/speade/desktop/api.py).

Headless: SpeadeApi is plain Python over speade.service, so no pywebview and no
window are needed (window-bound methods like add_pdfs are exercised only for
their no-window guard). Every bridge return value must survive json.dumps --
that IS the bridge contract: pywebview serialises it to the JS side."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from speade import service
from speade.desktop.api import SpeadeApi
from speade.validation.verapdf import VeraResult

PDF_BYTES = b"%PDF-1.7\n%%EOF\n"


@pytest.fixture(autouse=True)
def _hermetic_verapdf(monkeypatch):
    """run_batch scores every draft with veraPDF now -- keep the bridge tests
    hermetic and fast with a canned pass."""
    monkeypatch.setattr(
        service.verapdf,
        "validate",
        lambda pdf, profile="ua1", cli=None: VeraResult(passed=True, profile=profile),
    )


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


def test_list_pending_shows_waiting_then_clears(tmp_path):
    api = _api(tmp_path)
    (tmp_path / "inbox" / "a.pdf").write_bytes(PDF_BYTES)

    assert api.list_pending() == ["a.pdf"]  # the "Waiting to process" section
    api.run_batch()
    assert api.list_pending() == []


def test_batch_start_and_status_drive_the_progress_bar(tmp_path):
    api = _api(tmp_path)
    for name in ("a.pdf", "b.pdf"):
        (tmp_path / "inbox" / name).write_bytes(PDF_BYTES)

    started = api.run_batch_start()
    assert started == {"started": True}

    deadline = time.monotonic() + 10
    status = api.run_batch_status()
    while status.get("running") and time.monotonic() < deadline:
        time.sleep(0.02)
        status = api.run_batch_status()

    json.dumps(status)  # the bridge contract
    assert status["running"] is False
    assert status["done"] == status["total"] == 2
    assert "error" not in status
    assert [(i["file"], i["ok"]) for i in status["items"]] == [("a.pdf", True), ("b.pdf", True)]


def test_shutdown_stops_the_batch_and_waits_for_the_worker(tmp_path, monkeypatch):
    # the app-exit hook: without cancel+join, interpreter shutdown races the
    # daemon batch worker inside a native pdfium call (the recorded
    # heap-corruption crash mechanism). shutdown() must flag the stop and not
    # return until the worker has actually finished.
    import threading

    api = _api(tmp_path)
    started = threading.Event()

    def slow_batch(folder, config, progress=None, cancel=None, reprocess=False, module=None):
        started.set()
        while not cancel():  # grinds until someone asks it to stop
            time.sleep(0.01)
        return []

    monkeypatch.setattr(service, "run_batch", slow_batch)
    api.run_batch_start()
    assert started.wait(timeout=5)  # the worker is genuinely running

    api.shutdown(timeout_s=10)

    assert api._batch["cancel"] is True
    assert not api._batch_thread.is_alive()  # joined, not abandoned
    assert api._batch["running"] is False


def test_shutdown_without_a_batch_is_a_no_op(tmp_path):
    api = _api(tmp_path)
    api.shutdown(timeout_s=1)  # must not raise with no thread ever started


def test_module_round_trip_through_the_bridge(tmp_path):
    # the whole Student-Partner flow by rel-id: waiting -> scoped batch ->
    # queue (module-grouped fields) -> preview -> approve into the module tree.
    api = _api(tmp_path)
    folder = tmp_path / "inbox" / "MG2001"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(PDF_BYTES)

    assert api.modules() == ["MG2001"]
    assert api.list_pending() == ["MG2001/a.pdf"]

    api.run_batch(module="MG2001")

    queue = api.list_queue()
    json.dumps(queue)  # the bridge contract
    assert [(q["file"], q["module"], q["name"]) for q in queue] == [
        ("MG2001/a.pdf", "MG2001", "a.pdf")
    ]
    assert api.load_pdf("MG2001/a.pdf")["data_uri"].startswith("data:application/pdf")

    decided = api.decide("MG2001/a.pdf", reviewer="s1", approve=True)
    assert decided["status"] == "approved"
    assert (tmp_path / "outbox" / "MG2001" / "approved" / "a.pdf").is_file()


def test_malformed_ids_are_errors_not_crashes(tmp_path):
    api = _api(tmp_path)
    assert "error" in api.load_pdf("../../etc/passwd.pdf")
    assert "error" in api.decide("a/../b.pdf", reviewer="x", approve=True)
    assert "error" in api.run_batch_start(module="..")


def test_structure_reports_errors_as_data_not_crashes(tmp_path):
    api = _api(tmp_path)
    (tmp_path / "inbox" / "a.pdf").write_bytes(PDF_BYTES)  # too minimal to parse
    api.run_batch()

    result = api.structure("a.pdf")

    json.dumps(result)
    assert "error" in result  # unreadable/unparseable: a note for the UI, not a crash
    assert api.structure("nope.pdf") == {"error": "not found: nope.pdf"}


def test_structure_tree_and_page_image_are_json_safe(tmp_path):
    pikepdf = pytest.importorskip("pikepdf", reason="needs --extra tag")
    pytest.importorskip("pypdfium2", reason="needs --extra ocr")
    api = _api(tmp_path)
    doc = pikepdf.Pdf.new()
    doc.add_blank_page(page_size=(612, 792))
    doc.save(tmp_path / "inbox" / "a.pdf")
    api.run_batch()

    tree = api.structure_tree("a.pdf")
    json.dumps(tree)  # the bridge contract
    assert tree["tagged"] is False  # a blank page has no tags yet

    image = api.page_image("a.pdf", 0)
    json.dumps(image)
    assert image["data_uri"].startswith("data:image/png;base64,")
    assert image["pages"] == 1
    assert "error" in api.page_image("a.pdf", 5)  # out of range: a note, not a crash
    assert api.structure_tree("nope.pdf") == {"error": "not found: nope.pdf"}
    assert api.page_image("nope.pdf") == {"error": "not found: nope.pdf"}


def test_audit_log_is_json_safe_and_newest_first(tmp_path, monkeypatch):
    api = _api(tmp_path)
    (tmp_path / "inbox" / "a.pdf").write_bytes(PDF_BYTES)
    api.run_batch()
    api.decide("a.pdf", reviewer="s123456", approve=True)

    events = api.audit_log()

    json.dumps(events)
    assert [e["event"] for e in events[:2]] == ["verify", "run"]  # newest first
    assert events[0]["file"] == "a.pdf"
    assert events[0]["ts"]


def test_decide_records_and_returns_json_safe(tmp_path, monkeypatch):
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

    # a traversal id is REJECTED outright (split_id refuses it) -- stricter
    # than the old silent Path(...).name stripping, and nothing gets decided.
    assert "error" in result
    assert api.list_queue()[0]["status"] == "draft"  # untouched


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


def test_load_pdf_still_resolves_after_a_decision_moves_the_file(tmp_path, monkeypatch):
    # decide() sorts the PDF into outbox/approved -- every name-taking bridge
    # method must keep finding it there (the reviewer can still preview it).
    api = _api(tmp_path)
    (tmp_path / "inbox" / "a.pdf").write_bytes(PDF_BYTES)
    api.run_batch()
    api.decide("a.pdf", reviewer="s123456", approve=True)

    assert not (tmp_path / "outbox" / "a.pdf").exists()
    assert (tmp_path / "outbox" / "approved" / "a.pdf").is_file()
    assert api.load_pdf("a.pdf")["data_uri"].startswith("data:application/pdf;base64,")


def test_run_batch_cancel_marks_the_finished_batch(tmp_path, monkeypatch):
    # deterministic Stop: a fake batch that idles until the cancel flag goes up,
    # exactly like a real batch polling `cancel` between documents.
    api = _api(tmp_path)

    def fake_run_batch(
        folder, config_path, progress=None, cancel=None, reprocess=False, module=None
    ):
        deadline = time.monotonic() + 5
        while not cancel() and time.monotonic() < deadline:
            time.sleep(0.01)
        return []

    monkeypatch.setattr(service, "run_batch", fake_run_batch)
    api.run_batch_start()
    assert api.run_batch_cancel() == {"cancelling": True}

    deadline = time.monotonic() + 10
    while api.run_batch_status().get("running") and time.monotonic() < deadline:
        time.sleep(0.02)

    status = api.run_batch_status()
    json.dumps(status)
    assert status["running"] is False
    assert status["cancelled"] is True


def test_doc_metadata_errors_are_data_not_crashes(tmp_path):
    api = _api(tmp_path)
    assert api.doc_metadata("nope.pdf") == {"error": "not found: nope.pdf"}
    assert api.set_doc_metadata("nope.pdf", "t", "en") == {"error": "not found: nope.pdf"}
