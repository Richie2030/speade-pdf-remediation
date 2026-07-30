"""Tests for the service layer (src/speade/service.py) -- the engine every
client (CLI, desktop UI) calls. Hermetic: the noop stage does the pipeline
work and veraPDF is mocked, so no external tools are needed."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from speade import service
from speade.pipeline.contract import ApprovalStatus, Sidecar
from speade.validation.verapdf import VeraResult

PDF_BYTES = b"%PDF-1.7\n%%EOF\n"


@pytest.fixture(autouse=True)
def _hermetic_verapdf(monkeypatch):
    """run_one/run_batch score every draft with veraPDF now -- keep the suite
    hermetic and fast with a canned pass. Tests override per-case as needed."""
    monkeypatch.setattr(
        service.verapdf,
        "validate",
        lambda pdf, profile="ua1", cli=None: VeraResult(passed=True, profile=profile),
    )


def _write_config(tmp_path: Path) -> Path:
    """A noop-pipeline config with RELATIVE data paths (resolution under test)."""
    config = tmp_path / "config.yaml"
    config.write_text(
        "io:\n  local:\n    inbox: inbox\n    outbox: outbox\n    sidecars: sidecars\n"
        "pipeline:\n  stages:\n    passthrough: noop\n"
        "audit:\n  log_path: audit/audit.jsonl\n",
        encoding="utf-8",
    )
    (tmp_path / "inbox").mkdir()
    return config


def test_workspace_resolves_relative_paths_against_the_config_dir(tmp_path):
    # deployment-critical: paths must anchor to the config file, not the CWD.
    ws = service.workspace(_write_config(tmp_path))

    assert ws.inbox == (tmp_path / "inbox").resolve()
    assert ws.outbox == (tmp_path / "outbox").resolve()
    assert ws.sidecars == (tmp_path / "sidecars").resolve()
    assert ws.audit_log == (tmp_path / "audit" / "audit.jsonl").resolve()
    assert ws.stages == {"passthrough": "noop"}
    assert ws.verapdf_profile == "ua1"
    assert ws.verapdf_cli is None


def test_workspace_creates_the_data_folders_up_front(tmp_path):
    # tester feedback: a fresh install must have inbox/outbox/audit ready the
    # moment any client starts -- not materialising lazily on first use.
    config = tmp_path / "config.yaml"
    config.write_text(
        "io:\n  local:\n    inbox: inbox\n    outbox: outbox\n"
        "pipeline:\n  stages:\n    passthrough: noop\n"
        "audit:\n  log_path: audit/audit.jsonl\n",
        encoding="utf-8",
    )

    ws = service.workspace(config)

    assert ws.inbox.is_dir()
    assert ws.outbox.is_dir()
    assert ws.sidecars.is_dir()  # the default (data/sidecars) also materialises
    assert ws.audit_log.parent.is_dir()


def test_run_batch_sweeps_the_configured_inbox(tmp_path):
    config = _write_config(tmp_path)
    for name in ("a.pdf", "b.pdf"):
        (tmp_path / "inbox" / name).write_bytes(PDF_BYTES)
    (tmp_path / "inbox" / "notes.txt").write_text("not a pdf", encoding="utf-8")

    items = service.run_batch(None, config)

    assert [(item.file, item.ok) for item in items] == [("a.pdf", True), ("b.pdf", True)]
    for name in ("a.pdf", "b.pdf"):
        assert (tmp_path / "outbox" / name).read_bytes() == PDF_BYTES
        assert (tmp_path / "sidecars" / f"{name}.sidecar.json").is_file()
    # the outbox itself holds deliverable PDFs only -- no sidecar clutter
    # (approved/ and rejected/ status folders are part of the workspace contract).
    outbox_entries = sorted(p.name for p in (tmp_path / "outbox").iterdir())
    assert outbox_entries == ["a.pdf", "approved", "b.pdf", "rejected"]
    audit = (tmp_path / "audit" / "audit.jsonl").read_text(encoding="utf-8")
    assert len(audit.splitlines()) == 2  # one audit line per file


def test_run_batch_explicit_folder_overrides_the_inbox(tmp_path):
    config = _write_config(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "c.pdf").write_bytes(PDF_BYTES)

    items = service.run_batch(elsewhere, config)

    assert [(item.file, item.ok) for item in items] == [("c.pdf", True)]
    assert (tmp_path / "outbox" / "c.pdf").is_file()


def test_run_batch_isolates_per_file_failures(tmp_path, monkeypatch):
    config = _write_config(tmp_path)
    for name in ("bad.pdf", "good.pdf"):
        (tmp_path / "inbox" / name).write_bytes(PDF_BYTES)

    real_run = service.runner.run

    def flaky(src, stages, outbox, audit_log, sidecar_dir=None):
        if src.name == "bad.pdf":
            raise RuntimeError("engine exploded")
        return real_run(src, stages, outbox, audit_log, sidecar_dir=sidecar_dir)

    monkeypatch.setattr(service.runner, "run", flaky)

    items = service.run_batch(None, config)

    by_name = {item.file: item for item in items}
    assert by_name["good.pdf"].ok is True
    assert by_name["bad.pdf"].ok is False
    assert "engine exploded" in by_name["bad.pdf"].error  # reported, not raised


def test_run_batch_empty_inbox_returns_nothing(tmp_path):
    assert service.run_batch(None, _write_config(tmp_path)) == []


def test_list_queue_summarises_outbox_sidecars(tmp_path):
    config = _write_config(tmp_path)
    (tmp_path / "inbox" / "a.pdf").write_bytes(PDF_BYTES)
    service.run_batch(None, config)

    queue = service.list_queue(config)

    assert len(queue) == 1
    item = queue[0]
    assert item.file == "a.pdf"
    assert item.status == "draft"  # automation never approves (mandatory gate)
    assert item.stages_applied == ["noop"]
    assert item.verapdf_passed is True  # scored right after processing (advisory)
    assert item.reviewer is None


def test_run_batch_skips_already_processed_unchanged_files(tmp_path):
    # live-testing finding: process 2, add 1, process again -- the old 2 must
    # NOT run again (wasted minutes, re-drafted documents). A replaced source
    # (new bytes) and reprocess=True do run again.
    config = _write_config(tmp_path)
    for name in ("a.pdf", "b.pdf"):
        (tmp_path / "inbox" / name).write_bytes(PDF_BYTES)
    service.run_batch(None, config)
    (tmp_path / "inbox" / "c.pdf").write_bytes(PDF_BYTES)

    items = {i.file: i for i in service.run_batch(None, config)}

    assert items["a.pdf"].skipped and items["b.pdf"].skipped
    assert items["c.pdf"].ok and not items["c.pdf"].skipped

    (tmp_path / "inbox" / "a.pdf").write_bytes(PDF_BYTES + b"% new version\n")
    again = {i.file: i for i in service.run_batch(None, config)}
    assert not again["a.pdf"].skipped  # replaced source: processed again
    assert again["b.pdf"].skipped and again["c.pdf"].skipped

    forced = service.run_batch(None, config, reprocess=True)
    assert all(not i.skipped for i in forced)


def test_list_pending_reflects_unprocessed_inbox_files(tmp_path):
    config = _write_config(tmp_path)
    (tmp_path / "inbox" / "a.pdf").write_bytes(PDF_BYTES)

    assert service.list_pending(config) == ["a.pdf"]
    service.run_batch(None, config)
    assert service.list_pending(config) == []


def test_run_batch_reports_per_file_progress(tmp_path):
    config = _write_config(tmp_path)
    for name in ("a.pdf", "b.pdf"):
        (tmp_path / "inbox" / name).write_bytes(PDF_BYTES)
    seen: list[tuple[int, int, str]] = []

    service.run_batch(None, config, progress=lambda d, t, f: seen.append((d, t, f)))

    # before each file, then a final "all done" tick -- the progress bar's feed.
    assert seen == [(0, 2, "a.pdf"), (1, 2, "b.pdf"), (2, 2, "")]


def test_run_batch_persists_the_advisory_verdict(tmp_path, monkeypatch):
    # the reviewer must see the machine verdict right after processing, without
    # opening Acrobat or waiting for the decision step.
    config = _write_config(tmp_path)
    (tmp_path / "inbox" / "a.pdf").write_bytes(PDF_BYTES)
    monkeypatch.setattr(
        service.verapdf,
        "validate",
        lambda pdf, profile="ua1", cli=None: VeraResult(
            passed=False, failed_clauses=["6.2-1"], profile=profile
        ),
    )

    items = service.run_batch(None, config)

    assert items[0].sidecar.verapdf_passed is False
    persisted = Sidecar.model_validate_json(
        (tmp_path / "sidecars" / "a.pdf.sidecar.json").read_text(encoding="utf-8")
    )
    assert persisted.verapdf_passed is False
    assert persisted.verapdf_failed_clauses == ["6.2-1"]


def test_audit_events_newest_first_with_time_and_file(tmp_path):
    config = _write_config(tmp_path)
    for name in ("a.pdf", "b.pdf"):
        (tmp_path / "inbox" / name).write_bytes(PDF_BYTES)
    service.run_batch(None, config)

    events = service.audit_events(config)

    assert [e["file"] for e in events] == ["b.pdf", "a.pdf"]  # newest first
    assert all(e["ts"] for e in events)  # the History view needs timestamps


def test_list_queue_survives_a_mangled_sidecar(tmp_path):
    config = _write_config(tmp_path)
    sidecars = tmp_path / "sidecars"
    sidecars.mkdir()
    (sidecars / "broken.pdf.sidecar.json").write_text("{not json", encoding="utf-8")

    queue = service.list_queue(config)

    assert [(item.file, item.status) for item in queue] == [("broken.pdf", "invalid-sidecar")]


@pytest.mark.parametrize("approve", [True, False])
def test_decide_records_machine_verdict_and_human_decision(approve, tmp_path, monkeypatch):
    config = _write_config(tmp_path)
    (tmp_path / "inbox" / "a.pdf").write_bytes(PDF_BYTES)
    service.run_batch(None, config)
    monkeypatch.setattr(
        service.verapdf,
        "validate",
        lambda pdf, profile="ua1", cli=None: VeraResult(
            passed=False, failed_clauses=["7.1-1"], profile=profile
        ),
    )

    sidecar = service.decide(
        tmp_path / "outbox" / "a.pdf", reviewer="s123456", approve=approve, config_path=config
    )

    expected = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
    assert sidecar.approval.status == expected
    assert sidecar.approval.reviewer == "s123456"
    assert sidecar.approval.decided_at is not None
    assert sidecar.verapdf_passed is False
    assert sidecar.verapdf_failed_clauses == ["7.1-1"]
    # persisted in the sidecars folder, and audit-logged
    persisted = Sidecar.model_validate_json(
        (tmp_path / "sidecars" / "a.pdf.sidecar.json").read_text(encoding="utf-8")
    )
    assert persisted.approval.status == expected
    events = [
        json.loads(line)
        for line in (tmp_path / "audit" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events[-1]["event"] == "verify"
    assert events[-1]["decision"] == expected.value


def test_decide_refingerprints_the_bytes_being_approved(tmp_path, monkeypatch):
    # the reviewer may correct the draft (e.g. in Acrobat) between the run and
    # the sign-off: the approval must pin the bytes as they are NOW, so the
    # audit trail keeps proving approved == shipped.
    config = _write_config(tmp_path)
    (tmp_path / "inbox" / "a.pdf").write_bytes(PDF_BYTES)
    service.run_batch(None, config)
    monkeypatch.setattr(
        service.verapdf,
        "validate",
        lambda pdf, profile="ua1", cli=None: VeraResult(passed=True, profile=profile),
    )
    corrected = b"%PDF-1.7\n% corrected in acrobat\n%%EOF\n"
    out = tmp_path / "outbox" / "a.pdf"
    out.write_bytes(corrected)

    sidecar = service.decide(out, reviewer="s123456", approve=True, config_path=config)

    assert sidecar.output_sha256 == hashlib.sha256(corrected).hexdigest()
    events = [
        json.loads(line)
        for line in (tmp_path / "audit" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events[-1]["event"] == "verify"
    assert events[-1]["output_sha256"] == sidecar.output_sha256


def _tagged_source(tmp_path, name="doc.pdf"):
    """A minimal tagged PDF in the inbox: Document > P(with content) + Figure."""
    pikepdf = pytest.importorskip("pikepdf", reason="needs --extra tag")
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(
            F1=pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type1,
                BaseFont=pikepdf.Name.Helvetica,
            )
        )
    )
    page.Contents = pdf.make_stream(
        b"/P <</MCID 0>> BDC BT /F1 24 Tf 72 700 Td (Chapter One) Tj ET EMC"
    )
    para = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name.P, K=0, Pg=page.obj))
    figure = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name("/Figure"), Pg=page.obj))
    doc_elem = pdf.make_indirect(
        pikepdf.Dictionary(S=pikepdf.Name("/Document"), K=pikepdf.Array([para, figure]))
    )
    pdf.Root.StructTreeRoot = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.StructTreeRoot, K=doc_elem)
    )
    out = tmp_path / "inbox" / name
    pdf.save(out)
    return out


def test_set_tag_type_retags_rescores_and_audits(tmp_path):
    pytest.importorskip("pypdfium2", reason="needs --extra ocr")
    config = _write_config(tmp_path)
    _tagged_source(tmp_path)
    service.run_batch(None, config)
    out = tmp_path / "outbox" / "doc.pdf"
    tree = service.structure_tree(out)
    para = tree.root[0].kids[0]

    result = service.set_tag_type(out, para.id, "H2", config)

    assert result["ok"] and result["from"] == "P" and result["to"] == "H2"
    assert service.structure_tree(out).root[0].kids[0].type == "H2"
    # the edit is in the trust trail, and the document now reads as edited
    events = service.audit_events(config)
    assert events[0]["event"] == "edit-tag" and events[0]["file"] == "doc.pdf"
    assert service.list_queue(config)[0].output_changed is True

    with pytest.raises(ValueError, match="not an editable tag type"):
        service.set_tag_type(out, para.id, "Artifact", config)


def test_set_figure_alt_writes_and_clears_the_description(tmp_path):
    pytest.importorskip("pypdfium2", reason="needs --extra ocr")
    config = _write_config(tmp_path)
    _tagged_source(tmp_path)
    service.run_batch(None, config)
    out = tmp_path / "outbox" / "doc.pdf"
    figure = service.structure_tree(out).root[0].kids[1]

    service.set_figure_alt(out, figure.id, "  a bar chart of pass rates  ", config)
    assert service.structure_tree(out).root[0].kids[1].alt == "a bar chart of pass rates"

    service.set_figure_alt(out, figure.id, "", config)
    assert service.structure_tree(out).root[0].kids[1].alt is None
    assert service.audit_events(config)[0]["event"] == "edit-alt"


def test_make_decorative_and_move_and_remove_are_audited(tmp_path):
    pytest.importorskip("pypdfium2", reason="needs --extra ocr")
    config = _write_config(tmp_path)
    _tagged_source(tmp_path)
    service.run_batch(None, config)
    out = tmp_path / "outbox" / "doc.pdf"

    # reading order: the Figure moves ahead of the paragraph
    figure = service.structure_tree(out).root[0].kids[1]
    moved = service.move_tag(out, figure.id, -1, config)
    assert moved["ok"] and moved["direction"] == "earlier"
    assert [k.type for k in service.structure_tree(out).root[0].kids] == ["Figure", "P"]
    assert service.audit_events(config)[0]["event"] == "edit-order"
    with pytest.raises(ValueError, match="delta must be"):
        service.move_tag(out, figure.id, 3, config)

    # decorative: gone from the tree, no description needed
    again = service.structure_tree(out).root[0].kids
    deco = service.make_decorative(out, again[0].id, config)
    assert deco["ok"] and deco["was"] == "Figure"
    assert [k.type for k in service.structure_tree(out).root[0].kids] == ["P"]
    assert service.audit_events(config)[0]["event"] == "edit-decorative"

    # the escape hatch
    stripped = service.remove_all_tags(out, config)
    assert stripped["ok"] and stripped["had_tags"] is True
    assert service.structure_tree(out).tagged is False
    assert service.audit_events(config)[0]["event"] == "edit-remove-tags"


def test_undo_last_edit_steps_back_one_change_at_a_time(tmp_path):
    pytest.importorskip("pypdfium2", reason="needs --extra ocr")
    config = _write_config(tmp_path)
    _tagged_source(tmp_path)
    service.run_batch(None, config)
    out = tmp_path / "outbox" / "doc.pdf"
    para = service.structure_tree(out).root[0].kids[0]

    assert service.undo_depth("doc.pdf", config) == 0
    with pytest.raises(LookupError, match="nothing to undo"):
        service.undo_last_edit(out, config)

    service.set_tag_type(out, para.id, "H2", config)
    service.set_tag_type(out, para.id, "H3", config)
    assert service.undo_depth("doc.pdf", config) == 2
    assert service.list_queue(config)[0].undo_depth == 2

    result = service.undo_last_edit(out, config)  # H3 -> H2
    assert result["ok"] and result["undo_depth"] == 1
    assert service.structure_tree(out).root[0].kids[0].type == "H2"

    service.undo_last_edit(out, config)  # H2 -> P (the original)
    assert service.structure_tree(out).root[0].kids[0].type == "P"
    assert service.undo_depth("doc.pdf", config) == 0
    assert service.audit_events(config)[0]["event"] == "edit-undo"

    # a decision closes the session: no stale history to step into
    service.set_tag_type(out, para.id, "H2", config)
    assert service.undo_depth("doc.pdf", config) == 1
    service.decide(out, reviewer="s1", approve=True, config_path=config)
    assert service.undo_depth("doc.pdf", config) == 0


def test_unwrap_tag_removes_a_wrapper_and_is_audited(tmp_path):
    pytest.importorskip("pypdfium2", reason="needs --extra ocr")
    pikepdf = pytest.importorskip("pikepdf", reason="needs --extra tag")
    config = _write_config(tmp_path)
    # a paragraph wrapped in a bogus List, as the tagging engine sometimes does
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(
            F1=pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type1,
                BaseFont=pikepdf.Name.Helvetica,
            )
        )
    )
    page.Contents = pdf.make_stream(
        b"/P <</MCID 0>> BDC BT /F1 12 Tf 72 700 Td (not a list) Tj ET EMC"
    )
    body = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name.P, K=0, Pg=page.obj))
    wrapper = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name.L, K=body))
    pdf.Root.StructTreeRoot = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.StructTreeRoot,
            K=pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name("/Document"), K=wrapper)),
        )
    )
    pdf.save(tmp_path / "inbox" / "wrapped.pdf")
    service.run_batch(None, config)
    out = tmp_path / "outbox" / "wrapped.pdf"

    the_list = service.structure_tree(out).root[0].kids[0]
    result = service.unwrap_tag(out, the_list.id, config)

    assert result["ok"] and result["was"] == "L" and result["promoted"] == 1
    assert [k.type for k in service.structure_tree(out).root[0].kids] == ["P"]
    assert service.audit_events(config)[0]["event"] == "edit-unwrap"
    # a REFUSED edit must not leave a no-op step in the undo history
    depth = service.undo_depth("wrapped.pdf", config)
    para = service.structure_tree(out).root[0].kids[0]
    with pytest.raises(ValueError, match="would leave that content untagged"):
        service.unwrap_tag(out, para.id, config)
    assert service.undo_depth("wrapped.pdf", config) == depth
    # and the undo stack can put the wrapper back
    service.undo_last_edit(out, config)
    assert [k.type for k in service.structure_tree(out).root[0].kids] == ["L"]


def test_reprocess_undoes_edits_from_the_original(tmp_path):
    pytest.importorskip("pypdfium2", reason="needs --extra ocr")
    config = _write_config(tmp_path)
    _tagged_source(tmp_path)
    service.run_batch(None, config)
    out = tmp_path / "outbox" / "doc.pdf"
    para = service.structure_tree(out).root[0].kids[0]
    service.set_tag_type(out, para.id, "H2", config)
    assert service.list_queue(config)[0].output_changed is True

    service.reprocess("doc.pdf", config)

    assert service.structure_tree(out).root[0].kids[0].type == "P"  # edit undone
    assert service.list_queue(config)[0].output_changed is False  # matches again

    with pytest.raises(FileNotFoundError):
        service.reprocess("nope.pdf", config)


def test_decide_requires_a_sidecar(tmp_path):
    config = _write_config(tmp_path)
    (tmp_path / "outbox").mkdir()
    orphan = tmp_path / "outbox" / "orphan.pdf"
    orphan.write_bytes(PDF_BYTES)

    with pytest.raises(FileNotFoundError):
        service.decide(orphan, reviewer="x", approve=True, config_path=config)


def test_decide_requires_the_pdf_itself(tmp_path):
    # a decision needs bytes to pin: a sidecar whose PDF has vanished must be a
    # clear error, never a recorded approval of a document that is not there.
    config = _write_config(tmp_path)
    (tmp_path / "inbox" / "a.pdf").write_bytes(PDF_BYTES)
    service.run_batch(None, config)
    missing = tmp_path / "outbox" / "a.pdf"
    missing.unlink()  # sidecar stays behind

    with pytest.raises(FileNotFoundError, match="PDF not found"):
        service.decide(missing, reviewer="x", approve=True, config_path=config)


def test_decide_moves_the_pdf_into_its_status_folder(tmp_path):
    # the outbox reads as a workflow: root = awaiting review, approved/ =
    # ready to ship, rejected/ = needs manual rework.
    config = _write_config(tmp_path)
    (tmp_path / "inbox" / "a.pdf").write_bytes(PDF_BYTES)
    service.run_batch(None, config)

    service.decide(tmp_path / "outbox" / "a.pdf", reviewer="s1", approve=True, config_path=config)

    assert not (tmp_path / "outbox" / "a.pdf").exists()
    assert (tmp_path / "outbox" / "approved" / "a.pdf").read_bytes() == PDF_BYTES
    events = [
        json.loads(line)
        for line in (tmp_path / "audit" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events[-1]["moved_to"] == "outbox/approved"

    # a change of mind moves it between status folders, not back to root.
    service.decide(
        tmp_path / "outbox" / "approved" / "a.pdf", reviewer="s1", approve=False, config_path=config
    )
    assert not (tmp_path / "outbox" / "approved" / "a.pdf").exists()
    assert (tmp_path / "outbox" / "rejected" / "a.pdf").is_file()


def test_find_output_checks_the_status_folders(tmp_path):
    config = _write_config(tmp_path)
    (tmp_path / "inbox" / "a.pdf").write_bytes(PDF_BYTES)
    service.run_batch(None, config)

    assert service.find_output("a.pdf", config) == (tmp_path / "outbox" / "a.pdf").resolve()
    service.decide(tmp_path / "outbox" / "a.pdf", reviewer="s1", approve=True, config_path=config)
    assert (
        service.find_output("a.pdf", config)
        == (tmp_path / "outbox" / "approved" / "a.pdf").resolve()
    )
    assert service.find_output("nope.pdf", config) is None


def test_run_batch_cancel_stops_between_documents(tmp_path):
    # the Stop button's contract: polled between files, the file in flight
    # finishes, everything after is skipped.
    config = _write_config(tmp_path)
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        (tmp_path / "inbox" / name).write_bytes(PDF_BYTES)
    processed: list[str] = []

    def cancel_after_one() -> bool:
        return len(processed) >= 1

    items = service.run_batch(
        None,
        config,
        progress=lambda d, t, f: processed.append(f) if f else None,
        cancel=cancel_after_one,
    )

    assert [item.file for item in items] == ["a.pdf"]
    assert not (tmp_path / "outbox" / "b.pdf").exists()


def test_list_queue_reports_whether_the_output_changed(tmp_path):
    # the UI shows this as plain language ("edited since processing"), never
    # the raw hash -- but the comparison is hash-backed.
    config = _write_config(tmp_path)
    (tmp_path / "inbox" / "a.pdf").write_bytes(PDF_BYTES)
    service.run_batch(None, config)

    assert service.list_queue(config)[0].output_changed is False

    (tmp_path / "outbox" / "a.pdf").write_bytes(b"%PDF-1.7\n% acrobat fix\n%%EOF\n")
    assert service.list_queue(config)[0].output_changed is True


def test_set_doc_metadata_applies_title_and_language(tmp_path):
    pikepdf = pytest.importorskip("pikepdf")
    config = _write_config(tmp_path)
    src = tmp_path / "inbox" / "a.pdf"
    with pikepdf.new() as doc:
        doc.add_blank_page()
        doc.save(src)
    service.run_batch(None, config)
    out = tmp_path / "outbox" / "a.pdf"

    result = service.set_doc_metadata(out, "Week 3 Notes", "en-IE", config_path=config)

    assert result == {"title": "Week 3 Notes", "lang": "en-IE"}
    assert service.doc_metadata(out) == {"title": "Week 3 Notes", "lang": "en-IE"}
    # an app-made edit refreshes the sidecar fingerprint, so the queue does not
    # report it as an external change.
    assert service.list_queue(config)[0].output_changed is False
    events = [
        json.loads(line)
        for line in (tmp_path / "audit" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events[-1]["event"] == "edit-metadata"
    assert events[-1]["title"] == "Week 3 Notes"

    # empty fields leave existing values untouched (the human edits one at a time).
    service.set_doc_metadata(out, "", "", config_path=config)
    assert service.doc_metadata(out) == {"title": "Week 3 Notes", "lang": "en-IE"}


def test_run_one_unknown_stage_fails_fast(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "pipeline:\n  stages:\n    tag: does-not-exist\n",
        encoding="utf-8",
    )
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(PDF_BYTES)

    with pytest.raises(KeyError, match="does-not-exist"):
        service.run_one(pdf, config)
