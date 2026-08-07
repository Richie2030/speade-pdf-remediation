# SPEADE — what we need from IT to run on managed UCC machines

**In one line:** SPEADE is an offline PDF-accessibility tool, built and tested and
ready to use, but it cannot run on a managed UCC device as a standard user. Two
things — both IT-controlled — are blocking it. This page is the exact ask.

## What blocks it today (confirmed on a real UCC laptop)

1. **The app is an unsigned executable, so App Control (WDAC) refuses it.** A
   standard user has no override.
2. **A standard user cannot install the tools SPEADE needs** (no admin rights),
   so the run-from-source alternative is blocked too.

Both are permissions/policy, not software faults — the app itself works.

## What we need — Option A (recommended)

Run the shipped `.exe` on managed machines. Four steps, all one-time per machine
(or per image):

1. **Code-sign or allowlist the build.** Sign `speade-desktop.exe` (and ideally
   the `_internal\*.dll` / `*.pyd`) with a UCC code-signing certificate — a
   signer rule then covers future rebuilds. If signing isn't available,
   hash-allowlist this specific build in the App Control policy (must be redone
   each rebuild). *Details: `deployment.md`.*
2. **Install the four system tools machine-wide** (a standard user can't). Exact
   pinned versions below; `scripts\setup-machine.ps1`, run as administrator, does
   all four and verifies them.
3. **Confirm the WebView2 Runtime is present** on the image (it draws the window;
   standard on Windows 11).
4. **Allow a writable data location** — the app writes its working folders
   (inbox/outbox/records) next to itself, so install it somewhere a standard user
   can write (e.g. under the user profile), **not** `Program Files`. Keep that
   path **local**, not redirected to OneDrive (the tool is deliberately offline).

Then a Student Partner double-clicks the exe and works entirely locally.

## Option B (fallback, if signing isn't possible)

Run from source instead of the exe: install **uv + Python 3.13** and the same
four tools machine-wide; users launch with a command or shortcut
(`python -m speade.desktop`). Same install burden as Option A minus the signing,
but a worse experience (a command, not an icon) and it doesn't ship the tested
artifact. We prefer Option A.

## Why this is low-risk to approve

- **Fully offline.** No network calls, no server, no database, no inbound ports,
  no cloud. Nothing leaves the machine. (`security-and-data.md`.)
- **No accounts, no data collection.** Documents and records stay in local
  folders on the one PC.
- **Standard vendor tools**, installed by their own signed installers.
- **Source is available** for review; licensing is permissive / arms-length
  (`maintainability.md`, `tech-stack.md`).

## The pinned versions (frozen v1 — do not substitute)

| Tool | Version | Installs the… |
|---|---|---|
| Temurin JRE | **11.0.29** | Java runtime for the two Java tools |
| OpenDataLoader PDF | **2.5.0** | tagging engine |
| veraPDF | **1.30.2** | PDF/UA accessibility check |
| Tesseract | **5.5.0** | OCR for scanned PDFs |
| (app internals) | Python 3.13.5, PyInstaller 6.21.0 | bundled in the exe — no separate install |

Keep the four installers archived offline; nothing here should auto-update.

## What we hand over

- The built `speade-desktop\` folder (the app; self-contained apart from the four
  tools above).
- `scripts\setup-machine.ps1` — installs and verifies the four tools.
- `deployment.md` (signing/allowlisting detail) and `runbook.md` (manual setup).

## The decision we need

**Can UCC IT code-sign / allowlist this build and install the four tools on the
target machines (Option A)?** If not, can you enable Option B? Either unblocks
the pilot; without one of them the tool cannot run on managed devices.
