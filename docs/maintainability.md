# Maintainability — for UCC IT

The head-of-IT approval condition, written down: what the stack is, why its
licensing is safe, and which automated guards keep it that way. SPEADE v1 is a
frozen product — "maintainability" here means *auditable and re-buildable*,
not "actively developed".

## Stack inventory

One canonical list with exact pinned versions and licences:
**`docs/tech-stack.md`**. Summary: a Python 3.13 application over four
separately-installed engines (Tesseract, OpenDataLoader, veraPDF, on a Temurin
JRE), packaged with PyInstaller, tested with pytest. Python dependencies are
locked byte-exactly in `uv.lock`.

## Licence policy and the firewall

- **The application code is Apache-2.0.** UCC may use, modify, and
  redistribute it freely.
- **Imported libraries must be permissive or weak-copyleft (MPL).** The one
  MPL import (pikepdf) is licence-compatible with in-process use.
- **Strong copyleft (GPL/AGPL) never enters the process.** veraPDF (GPL) runs
  as a separate OS process — copyleft obligations do not cross a process
  boundary. `PyMuPDF/fitz` (AGPL, the tempting importable alternative) is
  banned outright.
- PyInstaller (GPL-with-exception) is build-time only; it ships nothing GPL.

## The automated guards (run in CI on every push)

| guard | what it prevents |
|---|---|
| `scripts/check_banned_imports.py` | any `import fitz`/PyMuPDF entering the codebase |
| `scripts/check_licenses.py` | any new pip dependency outside the permissive/MPL allowlist |
| `tests/test_subproc.py` call-site scan | engine calls bypassing the managed subprocess wrapper |
| `tests/test_pdfium_lock.py` call-site scan | pdfium use outside the thread-safety lock |
| the pytest suite (160+ tests) | behavioural regressions, incl. the trust-trail invariants |

## SBOM

A CycloneDX software bill of materials can be generated from the locked
environment at any time (the `cyclonedx-bom` tool is in the dev dependencies):

```powershell
uv run cyclonedx-py environment > sbom.json
```

Generate one from the final release environment and archive it with the
installers.

## Rebuild-from-source (the escape hatch)

If the exe folder is ever lost, the product is reproducible from the
repository: `uv sync --all-extras` restores the exact locked environment;
the build command in `runbook.md` §"Building the desktop .exe" reproduces the
folder; the archived tool installers restore the engines. No other
infrastructure exists.
