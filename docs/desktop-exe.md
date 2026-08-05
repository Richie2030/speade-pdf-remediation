# The desktop exe: rebuilding, moving, and folder locations

Three practical questions about the packaged review client (`speade-desktop.exe`),
answered in one place.

## 1. How to rebuild the exe

From the repo root:

```
uv run --all-extras --with pyinstaller python -m PyInstaller --noconfirm speade-desktop.spec
copy config.yaml dist\speade-desktop\
```

The result is the folder `dist\speade-desktop\` — that **whole folder is the
app**: `speade-desktop.exe` plus its `_internal` support folder plus
`config.yaml`.

Don't skip the `copy config.yaml` second step. The exe reads its config from
beside itself, and a rebuild recreates the folder **without** it.

Why it's built this way:

- `--onedir` on purpose, never `--onefile`: a single-file exe self-extracts to
  `%TEMP%` on every launch, which is exactly what App Control / antivirus flags
  (see `docs/decisions/frontend-delivery.md`).
- PyInstaller is a build-time tool only (`--with pyinstaller`); it is never a
  shipped dependency.
- The system engines — Java 11+ with OpenDataLoader, Tesseract, veraPDF — are
  **not** bundled. They must be installed on the machine that runs the exe
  (see `docs/runbook.md`).

## 2. Can I open the exe from any directory? Yes

Two different things people mean by this, and both work:

**Launching from anywhere** — double-click, a desktop shortcut, a terminal in
some other directory: all fine. The app deliberately anchors every path to the
`config.yaml` sitting next to the exe, never to the "current directory" it was
launched from, so behaviour is identical no matter how it is started.

**Moving the app** — also fine. Copy the entire `speade-desktop` folder
anywhere (a USB stick, `C:\SPEADE\`, the library-room PCs) and run it from
there. The only rule: **keep the folder intact**. The exe does not work
separated from its `_internal` folder and `config.yaml`. And the system engines
above still need to be installed on whatever machine it lands on.

One extra trick: the exe accepts a config path as an argument
(`speade-desktop.exe C:\somewhere\else\config.yaml`), so one install can serve
different folder setups from different shortcuts.

## 3. Changing where inbox / outbox / sidecars / audit live

Edit `config.yaml` next to the exe — no rebuild needed. These are the lines:

```yaml
io:
  local:
    inbox: ./data/inbox        # source PDFs go in here
    outbox: ./data/outbox      # remediated PDFs come out here (deliverables only)
    sidecars: ./data/sidecars  # per-document records (app-internal)
audit:
  log_path: ./data/audit/audit.jsonl
```

Two ways to use them:

- **Relative paths** (the defaults) are resolved against the folder
  `config.yaml` is in — so out of the box the folders appear inside the app
  folder as `speade-desktop\data\inbox` and so on.
- **Absolute paths** put them anywhere you like, for example:

  ```yaml
  io:
    local:
      inbox: C:/Users/richa/Documents/SPEADE/inbox
      outbox: C:/Users/richa/Documents/SPEADE/outbox
      sidecars: C:/Users/richa/Documents/SPEADE/sidecars
  audit:
    log_path: C:/Users/richa/Documents/SPEADE/audit/audit.jsonl
  ```

  Use forward slashes even on Windows — YAML and the app both handle them, and
  it avoids backslash-escaping headaches.

All folders are created automatically the moment the app starts, wherever the
config points them (you never need to create them by hand) — including the
`approved\` and `rejected\` shelves inside the outbox, where decided documents
are sorted. Note that `log_path` names the audit **file**; its folder is what
gets created.
