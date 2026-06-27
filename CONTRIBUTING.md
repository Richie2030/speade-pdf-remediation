# Contributing to SPEADE

A small, handover-by-design project. These conventions exist so a rotating
student can pick it up cold, and so two people on different OSes (macOS +
Windows, deploying to Linux/Boole) don't trip over each other. Keep it light.

## 1. Setup

We use [uv](https://docs.astral.sh/uv/) for the environment and lockfile.

```bash
# install uv once (macOS/Linux)
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

uv sync                     # creates .venv/ + installs (auto-fetches Python 3.13)
cp .env.example .env        # placeholders only — never commit real tokens
```

- **Python is pinned to 3.13** (`.python-version`); the exact dependency set is
  in `uv.lock` (committed). Run `uv sync` after every pull so your env matches.
- **You never activate the venv by hand** — prefix commands with `uv run`.
- Some stages need system tools installed per-OS (not via uv): **Docker** (runs
  the veraPDF validator image) and **Tesseract** (OCR). Install those when you
  reach the component that needs them.

### Windows note (App Control)
On a locked-down Windows machine, the per-package `.exe` launchers in
`.venv\Scripts\` can be blocked. Run tools as modules instead:
`uv run python -m pytest`, `uv run python -m speade` — not `uv run pytest` /
`uv run speade`. `uv run ruff` and `uv run python ...` work fine.

## 2. Everyday commands

```bash
uv run python -m speade run FILE.pdf   # run the pipeline on one PDF (offline)
uv run python -m pytest                # tests
uv run ruff check .                     # lint
uv run ruff format .                    # format
uv run python scripts/check_banned_imports.py   # no in-process AGPL (fitz/PyMuPDF)
uv run python scripts/check_licenses.py         # dependency licence allowlist
```

Optional but recommended — install the git hooks so lint/format/secret-scan run
on commit:

```bash
uv run pre-commit install
```

## 3. Branching & pull requests (kept lightweight)

We use PRs, but without the heavyweight ceremony — they're here for **CI before
merge**, a **handover record**, and **visibility**, not gatekeeping.

- **One short-lived branch per component/change**, off an up-to-date `main`:
  ```bash
  git checkout main && git pull
  git checkout -b feat/stage-contract
  ```
- **Open a PR** for any real component or behavioural change. CI runs on it; the
  PR description is where you explain *why* (the next cohort reads these).
- **No mandatory approval.** Either person can merge once CI is green. Ask for a
  review when you want eyes — it's a courtesy, not a gate.
- **Trivial changes** (typo, docs, a config tweak) can skip the PR — commit to a
  branch and merge fast, or push straight to `main`.
- **Keep `main` green.** It's the branch David and Boole pull from; don't land a
  red build there. (A branch can be red mid-implementation — that's fine.)
- Delete the branch after it merges.

## 4. Commit messages

[Conventional Commits](https://www.conventionalcommits.org).

```
<type>(<optional-scope>): <subject>

<why this change — root cause / motivation first, wrapped ~72 cols>

- concrete change 1
- concrete change 2
```

- **Types:** `feat`, `fix`, `chore`, `build`, `ci`, `docs`, `refactor`, `test`.
- **Scope** is optional and names the area (`detect`, `runner`, `verapdf`,
  `config`, …); drop it for broad changes.
- Subject is lowercase, imperative (`add`, `fix`, `pin`), **no trailing period**.
- Write a body for anything non-trivial; explain the *why*, then bullet the *what*.

## 5. Code conventions

- **Tool-agnostic stage contract.** Every pipeline stage implements the same
  shape — `(PDF + sidecar) -> (PDF + sidecar)` (`speade.pipeline.contract`).
  Stages speak only that neutral contract, never each other's tool-native types,
  so they stay swappable by config. Register a stage in
  `speade.pipeline.registry`; select it in `config.yaml`.
- **Arms-length copyleft rule.** Copyleft / CLI tools (veraPDF, OCRmyPDF, etc.)
  run as **subprocesses / containers** — never `import`ed in-process. **Never
  add PyMuPDF / `import fitz`** (AGPL); CI fails the build if you do.
- **Never mutate the input.** The runner works on a copy; the original PDF is
  always preserved (reversibility / do-not-degrade).
- **Secrets never touch the repo.** Non-secret config goes in `config.yaml`;
  tokens resolve from the environment / git-ignored `.env` at runtime.
- **Cross-platform (deploy target is Linux).** Use `pathlib`, never hard-code
  `C:\...` or `/Users/...`; keep filenames lowercase (Linux is case-sensitive);
  line endings are LF (handled by `.gitattributes`).

## 6. Tests

- Tests live in `tests/`, run with `uv run python -m pytest`.
- A component being built can ship a spec test marked
  `@pytest.mark.xfail` so the suite stays green; **delete the xfail** once it
  passes.
- Add a test with each component — at minimum, factor pure logic out of I/O so it
  can be unit-tested without a real PDF / Docker.

## 7. Licensing

Code is **Apache-2.0**. Dependencies must be permissive or weak-copyleft (MPL);
GPL/AGPL/LGPL tools are allowed only at arms length (subprocess), never as a pip
dependency. The licence gate (`scripts/check_licenses.py`) enforces this on the
shipped runtime set.
