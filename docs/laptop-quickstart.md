# First run on a university laptop

Get SPEADE running on a fresh UCC laptop and confirm it works — one page, follow
top to bottom. For depth: `runbook.md` (full setup), `deployment.md` (the exe and
signing), `self-test-checklist.md` (the thorough test).

**Do the run-from-source path first** (`python -m speade.desktop`). It skips the
one big blocker — the unsigned `.exe` is refused by App Control on managed UCC
machines with no user override — and still tests everything that matters on this
laptop: does it run on the UCC image, is WebView2 there, do the tools work, does
the review flow behave. Test the `.exe` later, after IT signs it.

---

## Step 0 — the make-or-break question

**Can you install software on this laptop (admin rights, or winget allowed)?**

- **Yes →** continue below.
- **No / locked down →** you cannot install uv, Java, or the tools yourself. Stop
  here and involve IT — this *is* the deployment conversation, and what you learn
  (WebView2 present? installs allowed? App Control policy?) is exactly the
  evidence the signing request needs.

---

## Step 1 — get the code onto the laptop

Clone it, or copy the project folder from a USB drive. Put it somewhere you can
write to — your **Documents** or Desktop, **not** Program Files.

```powershell
git clone <the repo url> speade
cd speade
```

## Step 2 — install uv

```powershell
winget install astral-sh.uv
```

Open a **new** terminal afterwards so `uv` is on the PATH.

## Step 3 — install the four system tools (as Administrator)

Right-click PowerShell → **Run as administrator**, then from the project folder:

```powershell
.\scripts\setup-machine.ps1
```

It installs Java 11, OpenDataLoader, veraPDF and Tesseract machine-wide, then
verifies each by running it and prints one verdict. If any tool fails, the app
still runs — those documents just get flagged instead of tagged.

## Step 4 — install the app's Python side and launch

```powershell
uv sync --all-extras
uv run python -m speade.desktop
```

The review window should open. (Data folders — inbox, outbox, sidecars, audit —
are created automatically inside the project's `data\` folder on first launch.)

---

## Step 5 — smoke test (5 minutes)

- [ ] The **window opens** (no crash). → If it doesn't, the laptop is probably
  missing **WebView2** — see Troubleshooting.
- [ ] Click **Add PDFs…**, pick 2–3 real PDFs (include one scan if you can).
- [ ] Click **Process PDFs**. → The progress bar runs and finishes; each document
  gets an automatic-check result.
- [ ] Open a document. → The tag tree shows on the left, the pages with boxes on
  the right; clicking one highlights the other.
- [ ] Make one edit (retag a paragraph, or write an image description). → The
  automatic check re-runs; **Undo last change** appears.
- [ ] Enter your name, click **Approve — ready to share**. → It moves to the
  `approved` folder; **History** shows the decision.

If all six pass, the software works on this laptop. Anything that fails is a bug
or an environment gap — note which step and the exact message.

---

## Step 6 — the four environment checks (the "worked on my PC" traps)

These are the reasons a laptop test exists. Confirm each:

1. **WebView2** — the window opened in Step 5, so it's present. (If not, see below.)
2. **Data location is writable** — you cloned to Documents/Desktop, so `data\`
   is writable. *If you later install the `.exe` into `Program Files`*, repoint
   the data folders: edit `config.yaml` to use an absolute path under your
   profile, e.g. `inbox: C:/Users/<you>/Documents/SPEADE/inbox` (and outbox,
   sidecars, `audit/audit.jsonl`).
3. **OneDrive / folder redirection** — if this laptop redirects **Documents** to
   OneDrive and your `data\` lands there, processed PDFs sync to the cloud, which
   breaks the "fully offline, nothing leaves the machine" promise. Keep the data
   folders on a **local** path (check where `data\` actually is).
4. **OpenDataLoader on the *system* PATH** — `setup-machine.ps1` handles this, so
   a second Windows login on a shared laptop still finds it. Verify with
   `opendataloader-pdf --help` from a plain (non-admin) terminal.

---

## Build once, transfer the folder to other devices

You can build the `.exe` folder on one machine and copy it to others — you do
**not** repeat the Python setup (Steps 1–2, 4) on each device. The bundle is
self-contained for the app: the Python runtime and every library (pikepdf,
pypdfium2, pywebview…) are inside `_internal\`. This is the intended deployment
model. But **two things do not travel in the folder**, so it is not "just copy
the folder":

**Build it (on this machine):**

```powershell
uv run --all-extras --with "pyinstaller==6.21.0" python -m PyInstaller --noconfirm speade-desktop.spec
copy config.yaml dist\speade-desktop\
```

The current `dist\` is stale (predates the recent UI work) — rebuild before
copying. `--noconfirm` wipes the old `dist\`, including any `data\` (test PDFs,
audit log) left in it, so the copy stays clean. (If you skip the rebuild, delete
`dist\speade-desktop\data\` before copying so your test data does not travel.)

**On each target device, two things still must be done:**

1. **Install the four system tools** — they are *not* in the folder (by design).
   Run `scripts\setup-machine.ps1` once per device (Step 3). Without them the app
   still opens, but every document comes back flagged "not tagged / OCR
   unavailable / validator unavailable" — degraded, not crashing.
2. **Get past App Control** — the build is **unsigned**. On a managed UCC device,
   App Control for Business refuses it no matter how complete the folder is, with
   no user override. Signing is done **once per build by IT**, not per device.
   - *Real deployment / managed laptops:* the folder is blocked until IT signs it
     — this is the deployment conversation (`deployment.md`).
   - *Testing on devices you control* (App Control off / not enforced): copy the
     folder, run `setup-machine.ps1`, done — a closer-to-real test than
     run-from-source.

**Also true of any transferred copy:** Windows x64 only; each device needs
**WebView2** (present on nearly all Win 11); and put the folder somewhere
writable — not `Program Files` — or repoint `config.yaml`'s data paths (Step 6.2)
so `data\` can be created.

**So the per-device recipe is:** copy the folder · install the four tools ·
handle the signature (IT once, or a device you control). No Python, no uv, no
`uv sync` on the target.

---

## What to record (for the IT / signing conversation)

- Did the window open? (WebView2 present?)
- Could you install uv and the four tools, or did it need IT?
- Where did `data\` land — local, or redirected to OneDrive?
- Did the `.exe` run, or did App Control block it?
- Any tool that setup-machine.ps1 couldn't install or verify.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| The window never opens / blank | **WebView2 Runtime missing.** Install "Microsoft Edge WebView2 Runtime" (Evergreen). Most Win 11 images have it; older Win 10 may not. |
| "opendataloader-pdf not found" / documents flagged "not tagged" | The tagging engine isn't on the PATH, or App Control blocked its `.exe`. Re-run `setup-machine.ps1` (it installs a `.cmd` launcher that gets past App Control). |
| Scans flagged "text recognition is not installed" | Tesseract missing — re-run `setup-machine.ps1`. |
| Automatic check says "validator unavailable" | veraPDF missing or blocked — re-run `setup-machine.ps1`. |
| The `.exe` won't start on a UCC laptop | Unsigned build refused by App Control — needs IT signing (`deployment.md`). Use the run-from-source path instead for now. |
| Can't write to the data folder | The app (or exe) is in a location standard users can't write to — move it under your profile, or repoint `config.yaml` (Step 6.2). |
