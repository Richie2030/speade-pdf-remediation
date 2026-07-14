"""Tests for LocalFolderClient (src/speade/io/local.py).

Covers the offline inbox -> outbox round-trip and DocumentClient conformance, plus
the LF-only sidecar guarantee (the Linux/Boole cross-platform invariant).
"""

from __future__ import annotations

import pytest

from speade.io.base import DocRef, DocumentClient
from speade.io.local import LocalFolderClient
from speade.pipeline.contract import Sidecar

PDF_BYTES = b"%PDF-1.7\n%%EOF\n"


def _client(tmp_path):
    inbox, outbox = tmp_path / "inbox", tmp_path / "outbox"
    inbox.mkdir()
    return LocalFolderClient(inbox, outbox), inbox, outbox


def test_satisfies_documentclient_protocol(tmp_path):
    client, _, _ = _client(tmp_path)
    assert isinstance(client, DocumentClient)


def test_list_documents_returns_sorted_pdfs_only(tmp_path):
    client, inbox, _ = _client(tmp_path)
    (inbox / "b.pdf").write_bytes(PDF_BYTES)
    (inbox / "a.pdf").write_bytes(PDF_BYTES)
    (inbox / "notes.txt").write_text("ignore me")
    assert [r.name for r in client.list_documents()] == ["a.pdf", "b.pdf"]


def test_fetch_locates_source_and_missing_raises(tmp_path):
    client, inbox, _ = _client(tmp_path)
    (inbox / "sample.pdf").write_bytes(PDF_BYTES)
    assert client.fetch(DocRef(id="sample.pdf", name="sample.pdf")) == inbox / "sample.pdf"
    with pytest.raises(FileNotFoundError):
        client.fetch(DocRef(id="missing.pdf", name="missing.pdf"))


def test_put_writes_pdf_and_sidecar_to_outbox(tmp_path):
    client, _, outbox = _client(tmp_path)
    remediated = tmp_path / "work" / "sample.pdf"
    remediated.parent.mkdir()
    remediated.write_bytes(b"%PDF-1.7\nremediated\n%%EOF\n")
    sidecar = Sidecar(source_path="inbox/sample.pdf", source_sha256="abc123")

    out = client.put(DocRef(id="sample.pdf", name="sample.pdf"), remediated, sidecar)

    out_pdf = outbox / "sample.pdf"
    out_sidecar = outbox / "sample.pdf.sidecar.json"
    assert out_pdf.read_bytes() == remediated.read_bytes()
    assert Sidecar.model_validate_json(out_sidecar.read_text()) == sidecar
    assert b"\r\n" not in out_sidecar.read_bytes()  # LF only (Linux/Boole target)
    assert out.name == "sample.pdf"
