# SPEADE PDF Remediation Pipeline

Custom software for the UCC **SPEADE** accessibility programme: an on-prem pipeline that helps remediate PDFs to **WCAG 2.1 AA / PDF-UA**, with a **mandatory human verification gate** before anything is published. Built to assist and not replace the Student Partner remediation workflow, and to complement (not rebuild) Anthology Ally.

## What it will do (v1 = PDFs only)

A tool-agnostic, stage-based pipeline:

```
Canvas API fetch → detect (born-digital vs scanned) → [OCR if scanned]
  → structure + accessibility tagging → figure alt-text DRAFT
  → HUMAN + VeraPDF trust gate → Canvas API re-upload → Ally re-scores
```

Design principles (carried from the planning docs):
- **On-prem / self-hosted by default** (no cloud confirmed); cloud is a per-stage alternative pending sign-off.
- **Mandatory human gate** so nothing ships unverified; the automation produces *drafts*, a human approves.
- **Tool-agnostic stage interface** where each stage is a swappable module (`PDF + sidecar JSON → PDF + sidecar`), selected by config; copyleft / CLI tools run as arms-length subprocesses.
- **Licence-clean** permissive dependencies only; no AGPL imported in-process (see `LICENSE` / NOTICE).
- **docx & pptx** are out of scope for v1 (later, in native format).

## Planned stack (provisional and to be settled by a Phase-1 spike)

| Stage | Tool(s) |
|---|---|
| Language | Python |
| Canvas I/O | Canvas REST API (`canvasapi`) |
| Parse / detect | pypdf · pypdfium2 |
| OCR | OCRmyPDF · Tesseract |
| Structure | DOCLING |
| Tagging | pdfix SDK (candidate; benchmarked vs alternatives) |
| Alt-text (draft) | Qwen2.5-VL-7B (self-hosted) |
| Validation | VeraPDF |

## Cross-platform development (macOS + Windows)

The team develops on both macOSand Windows; production runs on Linux (Boole HPC). To keep that friction-free:

- **Line endings:** handled by `.gitattributes` (LF everywhere) so don't change `core.autocrlf` per-machine.
- **Pin one Python version** (e.g. `requires-python` in `pyproject.toml` + a `.python-version`) and use a **lockfile** (uv / Poetry / pip-tools) so both machines + Boole resolve identical dependencies.
- **Virtualenv activation differs** (`.venv\Scripts\activate` on Windows vs `source .venv/bin/activate` on macOS) so same venv, different command.
- **System (non-pip) tools install per-OS:** Tesseract, a JRE for VeraPDF, etc. and `brew install …` on macOS, `choco`/`scoop`/installer on Windows. Pin versions in the runbook. (Or run them via Docker for parity.)
- **Code conventions for the Linux target:** use `pathlib`, never hard-code `C:\…` or `/Users/…`; keep filenames consistently lower-case (macOS/Windows are case-insensitive, **Boole/Linux is case-sensitive** and a case-only import mismatch passes locally but breaks in production).
- **Prefer cross-platform task scripts** (Python / a runner like `nox` or `invoke`) over `.sh` (won't run on Windows cmd) or `.bat`/`.ps1` (won't run on macOS).
- **Apple Silicon note:** confirm any non-pip / SDK binary (e.g. pdfix) ships an `arm64` macOS build; most Python wheels (pypdfium2 etc.) already do.

## Licence

Provisionally **Apache-2.0** (see `LICENSE`) chosen for the explicit patent grant and to keep a future open-source / cross-university release open. This is a recommendation pending team ratification; do not treat it as final.
