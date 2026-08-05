# Tech stack — the complete tool inventory

The single canonical list of everything SPEADE is built from, with the exact
versions of the frozen v1 ship set (read from the development machine,
2026-07-23). Per the frozen-product rule: these versions are **pinned** — keep
the installers archived offline, and change nothing after sign-off.

## At a glance

| Layer | Tool | Version | Licence | How it runs |
|---|---|---|---|---|
| Language | Python | 3.13.5 (uv-managed) | PSF | the app itself |
| Project manager | uv | 0.9.26 | MIT/Apache-2.0 | dev machine only |
| Data models | pydantic | 2.13.4 | MIT | imported |
| Config parsing | PyYAML | 6.0.3 | MIT | imported |
| CLI framework | typer | 0.26.8 | MIT | imported |
| Env loading | python-dotenv | 1.2.2 | BSD-3 | imported |
| PDF inspection (detect) | pypdf | 6.14.2 | BSD-3 | imported |
| PDF assembly/finish | pikepdf | 10.10.0 | **MPL-2.0** (weak copyleft — import OK) | imported |
| Page rendering | pypdfium2 | 5.11.0 | Apache-2.0/BSD-3 | imported |
| Image handling | Pillow | 12.3.0 | HPND (permissive) | imported (via pikepdf) |
| Desktop shell | pywebview | 6.2.1 | BSD-3 | imported |
| Web client | FastAPI / uvicorn | 0.139.2 / 0.51.0 | MIT / BSD-3 | imported (web extra) |
| OCR engine | Tesseract | 5.5.0.20241111 | Apache-2.0 | **subprocess** |
| Tagging engine | OpenDataLoader PDF | 2.5.0 | Apache-2.0 | **subprocess** (Java CLI) |
| PDF/UA validator | veraPDF | 1.30.2 | GPLv3+/MPL dual | **subprocess** (Java CLI) |
| Java runtime | Temurin OpenJDK | 11.0.29+7 | GPLv2+CE | hosts the two Java tools |
| Packaging | PyInstaller | via `--with` at build time | GPL-with-exception | **build-time only**, never shipped |
| Tests | pytest | 9.1.1 | MIT | dev only |
| Lint/format | ruff | 0.15.20 | MIT | dev only |

## The import / subprocess rule

Imported libraries must be permissive or weak-copyleft (MPL). Strong-copyleft
tools (GPL/AGPL) and non-Python engines run as **separate processes** — the
arms-length rule. This is a legal firewall (copyleft does not cross a process
boundary) plus crash isolation (a dying engine fails one document, not the
batch). Two of the engines are Java and one is C++, so importing them is not
even possible; the subprocess boundary is how languages talk. `import fitz`
(PyMuPDF, AGPL) is banned outright and CI enforces it
(`scripts/check_banned_imports.py`); a licence allowlist covers the rest
(`scripts/check_licenses.py`).

## Where each piece is declared

| What | Where |
|---|---|
| Python dependencies + extras (detect/tag/ocr/desktop/web) | `pyproject.toml`, locked in `uv.lock` |
| Which engine each pipeline stage uses | `config.yaml` (swap by config, not code) |
| System-tool install steps | `docs/runbook.md` |
| What sits where on a deployed machine | `docs/deployment.md` |
| The licence rules and rationale | `CLAUDE.md` invariants + `docs/decisions/` |

## Open items before final sign-off

- **Java version mismatch:** `runbook.md` installs Temurin **21**; this machine
  runs Temurin **11.0.29** (both satisfy the tools' Java 11+ requirement). Pick
  one — recommend pinning what is actually tested (11.0.29) or upgrading and
  re-testing once — and make this table, the runbook, `scripts/setup-machine.ps1`
  and the archived installer all agree.
- **PyInstaller is unpinned:** the build uses `--with pyinstaller` (latest at
  build time). Before the final signed build, pin it
  (`--with "pyinstaller==X.Y"`) and record the version here.
