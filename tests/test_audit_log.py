"""Tests for the audit log + hasher (src/speade/audit/log.py).

Locks in the A1 trust-trail primitives: a correct streaming SHA-256, and an
append-only JSONL recorder that never rewrites history and stays LF-only.
"""

from __future__ import annotations

import hashlib

from speade.audit.log import append_event, read_events, sha256_file


def test_sha256_file_matches_hashlib(tmp_path):
    pdf = tmp_path / "sample.pdf"
    data = b"%PDF-1.7\n...bytes...\n%%EOF\n"
    pdf.write_bytes(data)
    assert sha256_file(pdf) == hashlib.sha256(data).hexdigest()


def test_append_event_is_append_only_and_ordered(tmp_path):
    log = tmp_path / "audit" / "audit.jsonl"  # parent dir does not exist yet
    append_event(log, {"event": "run", "n": 1})
    append_event(log, {"event": "gate", "n": 2})

    events = read_events(log)
    assert events == [{"event": "run", "n": 1}, {"event": "gate", "n": 2}]  # order preserved


def test_log_is_lf_only(tmp_path):
    log = tmp_path / "audit.jsonl"
    append_event(log, {"event": "run"})
    assert b"\r\n" not in log.read_bytes()  # LF only (Linux/Boole target)


def test_read_events_empty_when_missing(tmp_path):
    assert read_events(tmp_path / "nope.jsonl") == []
