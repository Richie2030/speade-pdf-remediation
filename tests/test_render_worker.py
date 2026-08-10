"""The sacrificial page renderer (src/speade/render_worker.py).

The whole point of the worker is surviving what cannot be tested by causing it
for real (a native pdfium fault), so the crash path is exercised by killing
the worker process directly -- from the manager's point of view an outside
kill and a pdfium abort look identical (the child is suddenly dead)."""

from __future__ import annotations

import pytest

pikepdf = pytest.importorskip("pikepdf", reason="needs --extra tag")
pytest.importorskip("pypdfium2", reason="needs --extra detect/ocr")

from speade.render_worker import PageRenderer  # noqa: E402


@pytest.fixture
def sample_pdf(tmp_path):
    pdf = tmp_path / "doc.pdf"
    doc = pikepdf.Pdf.new()
    doc.add_blank_page(page_size=(200, 100))
    doc.add_blank_page(page_size=(200, 100))
    doc.save(pdf)
    return pdf


@pytest.fixture
def renderer():
    r = PageRenderer()
    yield r
    r.close()  # never leak worker processes out of a test


def test_renders_a_page_to_png_with_geometry(renderer, sample_pdf):
    import base64

    result = renderer.render(sample_pdf, 0)

    assert "error" not in result
    assert base64.b64decode(result["png_b64"])[:8] == b"\x89PNG\r\n\x1a\n"
    assert (result["width"], result["height"]) == (200, 100)
    assert result["pages"] == 2


def test_bad_requests_are_answers_not_deaths(renderer, sample_pdf, tmp_path):
    assert "error" in renderer.render(sample_pdf, 99)  # out of range
    assert "error" in renderer.render(tmp_path / "nope.pdf", 0)  # missing
    garbage = tmp_path / "garbage.pdf"
    garbage.write_bytes(b"not a pdf at all")
    assert "error" in renderer.render(garbage, 0)  # unparseable
    # and the same worker still renders fine afterwards
    assert "error" not in renderer.render(sample_pdf, 1)


def test_a_dead_worker_costs_one_request_then_recovers(renderer, sample_pdf):
    # a real render first, so a worker exists to murder
    assert "error" not in renderer.render(sample_pdf, 0)

    renderer._proc.kill()  # stand-in for a native pdfium fault
    renderer._proc.wait(timeout=5)

    # the next call finds the corpse: restart + render succeeds (the request
    # itself never sees a crash because the death happened BETWEEN requests).
    result = renderer.render(sample_pdf, 1)
    assert "error" not in result
    assert renderer._proc.poll() is None  # a fresh worker is alive


def test_a_page_that_keeps_killing_the_worker_is_blacklisted(renderer, sample_pdf):
    # the manager counts deaths per (path, mtime, page); at the cap it answers
    # immediately instead of feeding workers to the shredder forever.
    import speade.render_worker as rw

    key = (str(sample_pdf), sample_pdf.stat().st_mtime_ns, 0)
    renderer._crashes[key] = rw._MAX_CRASHES_PER_PAGE

    result = renderer.render(sample_pdf, 0)

    assert "error" in result
    assert "crashes the renderer" in result["error"]
    # other pages of the same document still render normally
    assert "error" not in renderer.render(sample_pdf, 1)


def test_worker_command_matches_the_delivery(monkeypatch):
    # normal install: this module run by the current interpreter; the frozen
    # exe re-invokes ITSELF with --render-worker (speade.desktop.__main__
    # diverts it). Getting this wrong in the exe would open a second review
    # window per page render.
    import sys

    from speade import render_worker as rw

    assert rw._worker_command() == [sys.executable, "-m", "speade.render_worker"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert rw._worker_command() == [sys.executable, "--render-worker"]


def test_close_is_idempotent_and_safe_without_a_worker(sample_pdf):
    r = PageRenderer()
    r.close()  # never started: no-op
    assert "error" not in r.render(sample_pdf, 0)
    r.close()
    r.close()  # double close: still fine
