# Decision record: reviewer front-end delivery (browser client + desktop .exe)

- **Status:** Accepted — **two clients planned** (browser + desktop), both as thin
  shells over one shared core/service. Provisional on the IT confirmations in
  *Open questions*.
- **Date:** 2026-06-30
- **Relates to:** the reviewer-facing front-end (none built yet); `src/speade/cli.py`
  + `src/speade/__main__.py` (the existing entry points); the mandatory human gate
  and the append-only audit log (`src/speade/audit/log.py`, `config.yaml` `audit.`);
  the tool-agnostic stage core (`docs/architecture.md`); the Acrobat EULA caveat in
  [`tagging-cost.md`](tagging-cost.md).
- **Update (2026-07-13):** Canvas and Ally have been removed from the project. The
  optional **Canvas LTI** front door (was decision 4) and the Canvas-token open
  question are void; the browser-vs-desktop decision is otherwise unchanged.
- **Update (2026-08-05) — what actually shipped.** Both clients exist, sharing
  one `ui/` folder as planned. Two things below did **not** survive contact with
  the offline pivot, so read them as history:
  1. **There is no shared backend service.** The "*Context*" section argues a
     multi-reviewer queue is inherently server-shaped; v1 is instead **fully
     local per install** — each PC has its own inbox/outbox, sidecars and audit
     log, and reviewer attribution comes from the name recorded at the gate, not
     from authentication. Admins collect histories by exporting/merging them
     (`../deployment.md`). The web client is consequently **127.0.0.1 only, with
     no hosting and no auth** — a second local window, not a hub.
  2. **Acrobat is no longer the only correction path.** Decision 3 said neither
     client corrects in-app. Most everyday corrections (retagging, image
     descriptions, decorative marking, reading order, drag-select bulk edits,
     tagging missed content) are now done **in the app**; Acrobat remains for
     what the app deliberately cannot do (see `../limitations.md`).
  The *Open questions* about hosting and SSO are void — nothing is hosted. The
  signing/allowlisting question remains open and is the live deployment blocker.

## Question

How do Student Partners drive the pipeline and work the human verification gate?
Two delivery models were on the table — an **internal website** and a
**downloadable .exe on UCC PCs**. Decision: build **both**, so there are two final
solutions. This record captures why that is cheap to do, what each path depends on,
and the entry-point convention so the next cohort doesn't reinvent it.

## Decision

1. **Build both a browser client and a desktop `.exe` client**, each a thin shell
   over the **same shared core library and the same shared backend service**. They
   are not two codebases — they are two front doors onto one engine.
2. **Build the review UI once** (HTML/JS over the backend) and deliver it two ways:
   served to the browser (web client), and wrapped in a **permissive webview shell**
   (e.g. `pywebview`, BSD) packaged as the `.exe`. One UI, two deliveries — the
   `.exe` becomes mostly a packaging/signing exercise, not a second UI to maintain.
3. **The EULA-bound Acrobat manual-correction step stays on the SP's own desktop**
   in both solutions (see [`tagging-cost.md`](tagging-cost.md)). Neither client does
   correction in-app; both orchestrate the *download draft → correct in Acrobat →
   re-upload* round-trip and record it in the audit log.
4. **Do not let this block the core.** The front-end is a separable shell; build the
   offline core first (see *Consequences*).

## Context — the choice is smaller than "web vs exe"

Both solutions decompose into the **same two inevitable pieces** plus one real choice:

- **A shared backend service — needed either way.** The human gate is a *shared,
  multi-reviewer queue + approval state + append-only SHA-256 audit trail* across the
  ~20-SP cohort. That is inherently server-shaped. A desktop `.exe` with a purely
  local `audit.jsonl` cannot coordinate 20 reviewers or hold a single trust trail —
  so the backend exists in both worlds.
- **A desktop Acrobat step — needed either way.** Adobe's EULA forbids headless /
  service-bureau Acrobat; correction is human-in-the-loop on the SP's own machine.
  No front-end can move it server-side.
- **The actual choice is the *client*** — browser vs packaged desktop app — sitting
  on top of that same backend and handing off to that same desktop Acrobat. Because
  we are reusing one HTML/JS UI for both, building the second client is incremental,
  which is what makes "ship both" affordable.

**The core does not change either way.** The pipeline is a tool-agnostic,
config-driven stage core (`contract → stages → runner`) with a local I/O
seam and an audit log — all UI-independent. Every client consumes the same
`(PDF + sidecar) → (PDF + sidecar)` contract and reads the same audit JSONL. The
decision is reversible and deferrable.

## Architecture — three front doors over one core (no `main.py`)

`main.py` is a filename convention, not a Python mechanism, and is **not used**. The
core is a library with *no* entry point of its own; each client supplies its own.

```
src/speade/
  __main__.py          # python -m speade          → CLI
  cli.py               # Typer app + main()         (CLI client; also the engine all clients call)
  pipeline/ stages/ io/ audit/ validation/   # ← shared, UI-agnostic CORE (imported, never an entry point)
  web/                 # browser client
    app.py             #   app = FastAPI()         ← uvicorn serves this object (the entry point IS the app object)
    __main__.py        #   optional: python -m speade.web   → uvicorn.run(app, ...)
  desktop/             # .exe client
    app.py             #   pywebview window loading the same UI; calls into the core   ← PyInstaller's target script
    __main__.py        #   optional: python -m speade.desktop
```

Entry points are declared by `pyproject.toml` console scripts and module runners —
not by any `main.py`:

```toml
[project.scripts]
speade         = "speade.cli:main"       # exists today
speade-web     = "speade.web.app:main"   # optional: a thin wrapper that calls uvicorn.run
speade-desktop = "speade.desktop.app:main"
```

- **Web client** is served via `uvicorn speade.web.app:app` (or the `speade-web`
  wrapper); the ASGI `app` object is the entry point.
- **Desktop client**: PyInstaller is pointed at `src/speade/desktop/app.py` (the
  target script's name is free — it need not be `main.py`). The resulting `.exe`
  faces the signing/allowlisting step in *Open questions*.

**Carry-over fix (do not replicate the bug):** today `src/speade/__main__.py` only
does `from speade.cli import main` — it imports `main` but never calls it, and
`cli.main` isn't defined yet, so `python -m speade` currently raises `ImportError`.
Each entry point must both define *and invoke* its `main`, e.g.
`if __name__ == "__main__": raise SystemExit(main())`.

## Options and how they score against the constraints

| | Browser client (web) | Desktop `.exe` client |
|---|---|---|
| **Windows App Control** | Nothing to allowlist — the browser is already trusted | Conditional: needs IT to sign + allowlist the binary (see *Open questions*) |
| **Shared gate/queue/audit** | Native fit (it *is* the backend) | Still needs the same backend behind it |
| **Acrobat correction** | Download → correct → re-upload handoff | Co-located with Acrobat; can collapse the round-trip |
| **Large / scanned PDFs** | Browser upload/render limits on big files | Local disk, no upload, no render limit |
| **Offline** (a stated v1 value) | Needs the server reachable | Works fully offline |
| **OS spread (macOS + Windows)** | One artifact, all OSes | Windows-only; a Mac SP needs a separate build |
| **Licensing (permissive-only rule)** | HTML/JS on a permissive web framework — clean | GUI-toolkit trap (below); a webview shell stays clean |
| **Maintenance for a rotating team** | Central deploy, but an always-on authenticated service (host, TLS, SSO, patching) is real ops load | Packaging/signing/update drift across ~20 PCs; no server to run |
| **GDPR data surface** | Aggregates all PDFs on one server (larger single surface) | Per-desktop processing (smaller central surface) |

The IT dependency is **symmetric**, not one-sided: the `.exe` needs a one-time
signer rule (or per-build hash allowlisting); the web hub needs a provisioned
host/VM, network/firewall, TLS, SSO, and ongoing patching. Neither is free.

### Constraints that shape this (honest confidence)

- **App Control / WDAC.** The repo documents that `.venv` console-script `.exe`s
  *can be* blocked on "a locked-down Windows machine" (`CLAUDE.md`,
  `CONTRIBUTING.md`). It does **not** name the mechanism (WDAC default-deny vs
  AppLocker vs SmartScreen/AV) or say "already". So the `.exe` path is *conditional*:
  if UCC runs a default-deny allowlist, a custom binary won't run until IT allowlists
  it — by a **signer rule** (one-time, if UCC holds an org code-signing cert / PKI)
  or by **file hash** (re-issued by IT on every release, the painful path).
- **PyInstaller friction.** `--onefile` temp-unpacking can trip AV/SmartScreen and
  collides with policies that block execution from user-writable temp/AppData dirs;
  prefer `--onedir`. SmartScreen "reputation" concerns are largely moot on a
  *managed* 20-seat fleet where SmartScreen is policy-controllable.
- **GUI-toolkit licensing.** Under the permissive-only / no-in-process-copyleft rule,
  **PyQt (GPL)** and **PySide6 (LGPL)** are both imported in-process and fail the
  rule. Only **Tkinter** (PSF, bundled) is cleanly compliant as a *native* toolkit —
  but that means a second, dated UI. A **webview shell** (`pywebview` BSD + the same
  HTML/JS) is permissive *and* reuses the web UI — which is why we chose it.
- **Cohort & handover.** ~20 internal SPs (≈ the ~20 Acrobat lab seats); a
  handover-by-design, rotating-student team. Favours reusing one UI and minimising
  per-machine drift.

## Open questions to confirm (the decision-flippers — owners named)

- **Can Boole (an HPC/batch cluster) host an always-on authenticated web service, or
  does the web client need a separate UCC VM?** The repo never claims Boole hosts a
  web service. *Owner:* UCC IT / David. *If no internal web host exists at all*, the
  desktop client becomes the primary path.
- **Will UCC IT (a) provision SSO/auth for an internal site, and (b) sign +
  allowlist a custom binary?** Both are IT-owned and gate the two clients
  respectively. *Owner:* UCC IT.
- **Where exactly does Acrobat correction happen** — the SP's own machine, the
  ~20-seat lab room, or both — and does the licensing permit it for this workflow?
  *Owner:* SPEADE programme lead (Adobe EULA + UCC Acrobat terms).

## Consequences

- **Build the core first.** The offline core (stage pipeline + veraPDF gate + human
  gate + audit log) is unchanged by this decision and must land before either client
  is useful. Keep the core UI-agnostic; do not import any web/GUI dependency into it.
- **The backend is shared and comes before either client.** Stand up the FastAPI
  service that owns the queue, approval state, and the append-only SHA-256 audit
  trail; both clients are thin shells on it.
- **One UI, two deliveries.** Building the review UI once (HTML/JS) and wrapping it in
  a `pywebview` shell for the `.exe` keeps the second client incremental. **Caveat,
  named honestly:** a `pywebview` `.exe` is still a distributed binary, so it faces
  the same signing/allowlisting step as any other `.exe` — the webview approach buys
  *licence-cleanliness and UI reuse*, not an escape from App Control.
- **New dependencies arrive in optional extras** (per the small-stack rule): e.g. a
  `web` extra (FastAPI/uvicorn) and a `desktop` extra (pywebview, PyInstaller as a
  build-time tool). Keep them out of the base install; re-lock when added.
- **Risk to accept:** maintaining *two* clients is more surface for a rotating team.
  Mitigated by the shared-UI/shared-backend design, but the `.exe`'s signing/release
  cadence and the web service's ops load are both ongoing and IT-coupled.

## Sources

- Windows App Control / WDAC (default-deny, signer vs hash rules):
  https://learn.microsoft.com/en-us/windows/security/application-security/application-control/app-control-for-business/appcontrol
- WDAC/AppLocker hash-allowlisting burden:
  https://www.mrgtech.net/implementing-wdac-and-applocker/
- PyInstaller AV false positives / onefile vs onedir:
  https://github.com/pyinstaller/pyinstaller/issues/6754
- SmartScreen reputation (EV no longer auto-bypasses):
  https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation
- PyQt (GPL) vs PySide6 (LGPL) licensing:
  https://www.pythonguis.com/faq/licensing-differences-between-pyqt6-and-pyside6/
- pywebview licence (BSD): https://github.com/r0x0r/pywebview/blob/master/LICENSE
- Python / Tkinter (PSF) licence: https://docs.python.org/3/license.html
- Acrobat EULA caveat: [`tagging-cost.md`](tagging-cost.md)
