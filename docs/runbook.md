# runbook — setting up a SPEADE machine

Everything a new machine (or a new cohort member) needs to run the full
pipeline. Windows-first (the deployment reality: UCC lab PCs); Linux notes at
the end. **No Docker, no servers, no databases** — the stack is Python, one
JRE, two Java CLIs, and Tesseract.

| tool | pinned version (frozen v1) | why | install |
|---|---|---|---|
| uv + Python 3.13 | Python 3.13.5 | runs speade | `winget install astral-sh.uv` |
| Temurin JRE 11 | **11.0.29** (the tested pin) | runs BOTH Java tools below | `winget install EclipseAdoptium.Temurin.11.JRE` |
| OpenDataLoader PDF | **2.5.0** | the tagging engine (tag stage) | `uv tool install opendataloader-pdf` |
| veraPDF | **1.30.2** | the PDF/UA machine gate | unattended install, §4 |
| Tesseract 5 | **5.5.0** | OCR for scanned PDFs (ocr stage) | `winget install tesseract-ocr.tesseract` |

Canonical version + licence inventory: `docs/tech-stack.md`. **JRE note:** the
frozen pin is **Temurin 11.0.29** (all development testing ran on it). `winget`
fetches the latest Temurin 11 point release, which is fine to run; for an exact
reproducible build install from the **archived 11.0.29 installer** kept offline
with the other pinned installers. Do not upgrade to a newer major (21) without
re-running the smoke test.

## 0. the quick path: one script does sections 2–5

```powershell
# as Administrator, from the repo (or anywhere the scripts folder was copied)
.\scripts\setup-machine.ps1                       # networked machine
.\scripts\setup-machine.ps1 -OfflineDir D:\speade-installers   # air-gapped
```

Installs all four tools **machine-wide** at their pinned versions, puts them on
the **system** PATH (so every login on a shared PC finds them), then verifies
each one by running it and prints a single verdict. It also, for a delivered
`speade-desktop` folder it finds beside itself: **unblocks** the files if they
arrived by download/ZIP (otherwise the exe cannot load its .NET bridge — see
*Troubleshooting* below), **checks** the .NET Framework is 4.7.2+, and
**registers crash dumps** so a native crash leaves evidence. Re-runnable:
anything already done is reported and skipped. Sections 2–5 below are the
manual equivalent, kept for reference and troubleshooting.

Two things the script handles that a manual install easily gets wrong:

- **Per-user vs machine-wide.** `uv tool install opendataloader-pdf` puts its
  launcher in *one user's* profile, so a second student logging into the same
  PC gets "opendataloader-pdf not found". The script installs the engine into a
  machine-wide location instead.
- **App Control blocking the engine's `.exe`.** Observed live on a managed
  Windows 11 machine: the generated `opendataloader-pdf.exe` launcher was
  refused with `WinError 4551` even though the file was present, which stopped
  tagging entirely. The script installs a `.cmd` launcher that calls Python
  directly, which App Control allows. (SPEADE resolves tools with `shutil.which`,
  so a `.cmd`/`.bat` launcher is found normally — as veraPDF's `verapdf.bat`
  already is.) If tagging ever stops with *"installed but Windows would not run
  it"*, this is the cause and re-running the script is the fix.

## 1. project

```powershell
git clone https://github.com/Richie2030/speade-pdf-remediation.git
cd speade-pdf-remediation
uv sync --all-extras          # creates .venv with detect/tag/ocr deps
```

**App Control note:** on managed Windows machines the `.venv\Scripts\*.exe`
launchers can be blocked. Always run tools as modules — `uv run python -m speade`,
`uv run python -m pytest` — never the bare `.exe` names.

## 2. Java (JRE 11+)

```powershell
winget install EclipseAdoptium.Temurin.11.JRE
java -version                  # new terminal; expect 11.0.x
```

## 3. OpenDataLoader (tagging engine)

```powershell
uv tool install opendataloader-pdf
opendataloader-pdf --version   # uv-tool shims are not App-Control-blocked
```

## 4. veraPDF (PDF/UA gate) — unattended install

Download <https://software.verapdf.org/releases/verapdf-installer.zip>, unzip,
and in the unzipped folder save this as `auto-install.xml` (adjust the path):

```xml
<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<AutomatedInstallation langpack="eng">
    <com.izforge.izpack.panels.htmlhello.HTMLHelloPanel id="welcome"/>
    <com.izforge.izpack.panels.target.TargetPanel id="install_dir">
        <installpath>C:\Users\YOURUSER\verapdf</installpath>
    </com.izforge.izpack.panels.target.TargetPanel>
    <com.izforge.izpack.panels.packs.PacksPanel id="sdk_pack_select"/>
    <com.izforge.izpack.panels.install.InstallPanel id="install"/>
    <com.izforge.izpack.panels.finish.FinishPanel id="finish"/>
</AutomatedInstallation>
```

```powershell
java -jar verapdf-izpack-installer-<version>.jar auto-install.xml
```

Then either add the install folder to PATH (preferred — `verapdf` is then
auto-discovered) or point `config.yaml` → `validation.verapdf.path` at
`verapdf.bat`. Without either, the gate falls back to the Docker image if
Docker happens to exist, else it **fails closed** (`verapdf-unavailable` on the
sidecar) — it never silently passes.

## 5. Tesseract (OCR)

```powershell
winget install tesseract-ocr.tesseract
```

The default install dir (`C:\Program Files\Tesseract-OCR\`) is probed
automatically even when not on PATH. If Tesseract is missing, scanned PDFs are
flagged `ocr-unavailable` and flow to the human gate — the batch never crashes.

## 6. smoke test

```powershell
uv run python -m speade stages                    # detect / ocr / tag
uv run python -m speade run-batch                 # sweep the whole inbox, every
                                                  #   module folder included
uv run python -m speade run some.pdf              # or one file (a file inside
                                                  #   inbox\MODULE lands in that
                                                  #   module's outbox folder)
uv run python -m speade verify data/outbox/some.pdf --reviewer "you" --approve
uv run python -m speade.desktop                   # the review window (GUI)
uv run python -m pytest                            # full suite; corpus tests
                                                   # auto-skip if tools are absent
```

Optional: build the labelled test corpus (fixtures are derived locally, never
committed): `uv run --extra detect --extra tag --extra ocr python datasets/fetch_seeds.py`
then `... python datasets/build_corpus.py --gate`.

## Troubleshooting a delivered `speade-desktop` folder

### The .exe does nothing, or dies with "Failed to resolve Python.Runtime.Loader.Initialize"

The full error looks like this:

```
RuntimeError: Failed to resolve Python.Runtime.Loader.Initialize from
  ...\speade-desktop\_internal\pythonnet\runtime\Python.Runtime.dll
```

**Cause: mark-of-the-web, not App Control and not a bug in the app.** A folder
that arrived by email, Teams, OneDrive or a ZIP carries Windows' "downloaded
from the internet" mark on *every* file. .NET refuses to load a managed
assembly from an untrusted zone, so the window's .NET bridge (pywebview →
pythonnet) cannot start. Reported live on a tester's laptop, 2026-08-14.

**Fix**, in PowerShell in the delivered folder:

```powershell
Get-ChildItem -Recurse | Unblock-File
.\speade-desktop.exe
```

`setup-machine.ps1` now does this automatically for a `speade-desktop` folder
it finds beside (or around) itself, so a fresh machine set up with the script
never hits it. **Best habit when sending the folder:** zip it, and tell the
recipient to right-click the ZIP → Properties → tick **Unblock** *before*
extracting — nothing inside then inherits the mark.

### It still fails the same way after unblocking

Check the .NET Framework version:

```powershell
(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full').Release
```

**461808 or higher** is fine (4.7.2+). Lower, or a missing key, means the
review window cannot start: install the .NET Framework 4.8 runtime and reboot.
`setup-machine.ps1` reports this as a FAIL rather than installing it, because
Windows 10/11 ship 4.8 already.

### Nothing happens at all, and no error log appears

If `%LOCALAPPDATA%\speade\error-log.txt` does **not** exist, the process never
started — Windows blocked it before any of our code ran. That is Smart App
Control, SmartScreen or antivirus, and it is expected for an unsigned build
(see `deployment.md` on code signing). Note that turning Smart App Control off
is **permanent** — it cannot be re-enabled without reinstalling Windows — so
prefer the signed build, or run from source:

```powershell
winget install --id astral-sh.uv --scope machine
uv sync --all-extras
uv run python -m speade.desktop
```

### Anything else

`%LOCALAPPDATA%\speade\error-log.txt` holds unhandled errors, native crash
stacks and the page renderer's own output. In the app: **Settings → Open error
log**. Ask for that file first; it usually names the cause outright.

## Building the desktop .exe (release packaging)

```powershell
uv run --all-extras --with "pyinstaller==6.21.0" python -m PyInstaller --noconfirm speade-desktop.spec
copy config.yaml dist\speade-desktop\      # the exe reads config from beside itself
```

Produces `dist\speade-desktop\` (~75 MB): `speade-desktop.exe` + `_internal\`.
Deploy = copy that folder to the target PC (plus the system tools above) and
double-click the exe; the `data\` folders (inbox, outbox with its approved/
rejected shelves, sidecars, audit) are created next to it on first launch
(edit `config.yaml` to point them elsewhere, e.g. `Documents\SPEADE`).
IT-facing deployment detail (signing, updates, uninstall): `deployment.md`.
Notes:

- PyInstaller is **build-time only** (GPL-with-exception) — run via `--with`,
  never added as a project dependency; it ships nothing GPL into the product.
- `--onedir` on purpose, never `--onefile` (the temp-unpacking pattern App
  Control and AV hate — see docs/decisions/frontend-delivery.md).
- The binary is unsigned: on managed UCC PCs hand it to IT for signing /
  allowlisting before distribution.

## Linux (deploy parity)

```bash
sudo apt-get install default-jre tesseract-ocr    # JRE + OCR
uv tool install opendataloader-pdf                # tagger
# veraPDF: same installer zip + auto-install.xml; or use the Docker image
uv sync --all-extras && uv run python -m speade stages
```
