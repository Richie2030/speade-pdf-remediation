from speade.io.base import DocumentClient
from speade.io.local import LocalFolderClient


def test_local_client_roundtrip(tmp_path):
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    (inbox / "a.pdf").write_bytes(b"%PDF a")

    client = LocalFolderClient(inbox, outbox)
    assert isinstance(client, DocumentClient)

    refs = client.list_documents()
    assert [r.name for r in refs] == ["a.pdf"]

    work = tmp_path / "work"
    fetched = client.fetch(refs[0], work)
    assert fetched.read_bytes() == b"%PDF a"

    client.put(fetched, refs[0])
    assert (outbox / "a.pdf").read_bytes() == b"%PDF a"
