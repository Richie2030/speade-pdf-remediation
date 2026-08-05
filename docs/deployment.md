# Deployment guide — for UCC IT

How the SPEADE desktop app is built, signed, installed, updated, and removed.
Audience: the IT staff who approve, sign, and distribute software to managed
UCC Windows machines (library PCs and UCC-managed laptops). Companion
documents: `runbook.md` (tool installation detail), `tech-stack.md` (the
complete pinned inventory), `security-and-data.md` (data/network posture),
`maintainability.md` (licence policy and guards).

## What you are deploying

A self-contained folder, `speade-desktop\` (~75 MB): one unsigned executable
(`speade-desktop.exe`), a `_internal\` support folder, and a `config.yaml`.
It is a fully offline PDF accessibility-remediation tool: no server, no
network calls, no accounts, no installer — deployment is copying the folder.

Four system tools must exist on the machine (installed once, machine-wide;
exact versions and install commands in `runbook.md` / `tech-stack.md`):
Temurin JRE, OpenDataLoader PDF, veraPDF, and Tesseract. Adobe Acrobat Pro is
required wherever reviewers perform corrections.

## Why the tools are separate programs, not bundled

Two of the four are Java and one is C++ — not embeddable in a Python
application in principle. More importantly, veraPDF is GPL-licensed: running
it as a separate process keeps UCC's tool cleanly Apache-2.0 (copyleft does
not cross a process boundary), which is the project's licence firewall. The
separation also means you can patch/verify/allowlist each tool independently.

## Signing and allowlisting — required, not optional

The build is unsigned. Modern Windows will block it:

- **Enterprise-managed machines** (WDAC/App Control): blocked until IT signs
  and/or allowlists it — your normal process.
- **Windows 11 consumer machines** (Smart App Control): unsigned unknown
  binaries are blocked **with no user override at all**. This was observed
  first-hand during development. "Copy the folder to any laptop" is not a
  supported path; the signed build through managed machines is.

Request: code-sign `speade-desktop.exe` (and ideally the `_internal\*.dll`/
`*.pyd` set) with the university certificate, then distribute the folder
through your standard mechanism.

## Installation checklist (per machine)

1. Install the four system tools at their pinned versions, machine-wide, on the
   system PATH: run `scripts\setup-machine.ps1` **as Administrator** (it does
   `runbook.md` §2–5 in one pass and verifies each tool by running it). Use
   `-OfflineDir <folder>` to build out from the archived installer set with no
   network. Manual steps remain in `runbook.md` §2–5 for troubleshooting.
2. Copy the signed `speade-desktop\` folder to a fixed location
   (e.g. `C:\Program Files\SPEADE\` or per your convention).
3. Optionally edit `config.yaml` so the data folders live in the user profile
   (e.g. `Documents/SPEADE/...`) — then each Windows login gets its own
   workspace that follows a synced profile. Defaults create `data\` beside the
   exe.
4. Smoke test: launch the exe, add any PDF, Process, confirm it appears in the
   list with an automatic-check verdict.

## Collecting audit histories centrally (admin oversight)

Each installation keeps its own permanent history: `data\audit\audit.jsonl`
(hidden folder) — one line per processing run and per approve/reject, with
timestamps, SHA-256 fingerprints of the exact bytes approved, and the
reviewer's ID on every decision. On a shared PC, all users of that install
write to the same log; attribution comes from the recorded reviewer ID, not
the Windows login. Nothing is sent anywhere — collection is the admin's job,
and there are three ways to do it:

1. **In-app export (no admin tooling).** The **History → Export history**
   button writes the complete log as a timestamped, Excel-ready CSV into the
   visible output folder (`speade-history-YYYYMMDD-HHMMSS.csv`). Ask reviewers
   to export at collection time and gather the CSVs; they are chronological,
   so files from several machines merge by simple concatenation.
2. **Collect the raw logs.** Copy each machine's `data\audit\audit.jsonl`
   (manually or with a scheduled `robocopy` to a network share). Plain
   one-event-per-line JSON; concatenating files from many machines yields one
   valid merged log.
3. **Write logs to a share at deploy time.** `audit.log_path` in `config.yaml`
   accepts any path, so each install can log straight to a network location.
   Give every machine its OWN file (e.g.
   `\\server\speade-audit\LIB-PC-01.jsonl`) — multiple machines appending to
   one shared file is untested and risks interleaved writes.

Integrity note for the records conversation: the log is append-only by
convention and hidden from casual editing, but it is not cryptographically
sealed — file permissions are what protect it. The tamper-*evident* part is
the document trail itself: every approval records the SHA-256 of the approved
bytes, so a swapped or edited PDF can always be detected against its recorded
fingerprint.

## Updates

There are none by design. SPEADE v1 is a **frozen product** — no development
continues after hand-off. Keep the signed folder and the four tool installers
archived offline; a "reinstall" is a re-copy. Do not upgrade the system tools
underneath it (versions are pinned in `tech-stack.md`; nobody will adapt the
code to newer tool behaviour).

## Uninstall

Delete the `speade-desktop\` folder. If the data folders were configured into
user profiles, those remain (they contain the audit records and reviewed
documents — archive per your retention policy before removal). The four
system tools uninstall through their normal mechanisms if nothing else uses
them.

## Support boundaries

- The exe runs *only* on machines with the four tools installed; missing tools
  degrade gracefully (documents are flagged, nothing crashes) but the workflow
  is incomplete.
- Everything is per-Windows-login: queues, records, and history do not merge
  across users or machines.
- All data stays on the machine. There is nothing to firewall, no telemetry,
  no cloud dependency — see `security-and-data.md`.
