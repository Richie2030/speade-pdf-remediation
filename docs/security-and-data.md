# Security and data — for UCC IT

What SPEADE does with data, where it goes, and what it can never do.
Short version: **everything is local files on the one machine; there is no
network path at all.**

## Network posture

- The application makes **zero network calls** — no update checks, no
  telemetry, no cloud APIs. All processing (OCR, tagging, validation) runs as
  local processes on local files.
- The optional browser variant (`speade.web`) binds **hard-coded to
  127.0.0.1** — it cannot be reached from another machine and has no
  authentication because it never faces one.
- Nothing to firewall; nothing phones home. Network monitoring of the app
  should show nothing.

## Data locations (all local, all files)

| location | contents | sensitivity |
|---|---|---|
| `data\inbox` | source course PDFs, byte-untouched | whatever the course documents contain |
| `data\outbox` (+ `approved\`, `rejected\`) | remediated copies | same as the sources |
| `data\sidecars` (hidden) | one JSON record per document: processing steps, validator verdicts, reviewer decision (name/student number + timestamp) | reviewer identity |
| `data\audit\audit.jsonl` (hidden) | append-only event log: every run and decision, with SHA-256 fingerprints | reviewer identity |

The only personal data the system itself creates is the **reviewer identifier**
(the Windows login, typically a student number) with decision timestamps —
recorded deliberately, as the accountability trail. Retention of the data
folders is a university policy matter; archiving/deleting the folder is the
entire data lifecycle.

## Integrity model (the trust trail)

The project's core promise: *the exact file a human approved is the exact file
that ships.* Mechanically:

- Every document is fingerprinted (SHA-256) at intake and at every output.
- Approval re-fingerprints the file **at the moment of the decision** — so
  post-processing corrections in Acrobat are captured, and any later change to
  an approved file is detectable by comparing against the recorded hash.
- The audit log is append-only JSONL: events are added, never rewritten.
- The record folders are hidden as a nudge, not a control — the integrity
  guarantee comes from the hashes, not from hiding.

## Failure security

- Password-protected PDFs are **rejected, never opened** — the system does not
  attempt passwords (not even the empty one).
- Corrupt files are flagged and passed to the human untouched.
- The PDF/UA validator failing to run **fails closed** ("unavailable" verdict,
  never a silent pass).
- Engines run as separate OS processes with timeouts: a crashing or hanging
  engine affects one document, not the application.

## GDPR posture, in one paragraph

SPEADE processes course materials locally and records reviewer identifiers
locally. It transmits nothing, stores nothing off-machine, and has no accounts
or profiles. Data subject requests reduce to ordinary file operations on the
machine's data folder. The one deliberate personal-data record (who approved
what, when) exists to evidence the university's accessibility review process.
