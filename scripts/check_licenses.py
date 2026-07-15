"""CI guard -- dependency-licence gate (rule L6). Fail on any GPL/AGPL/LGPL package
in the *shipped runtime closure*; permissive and weak-copyleft (MPL) licences are
allowed. GPL-family tools may run only as arms-length subprocesses, never as deps.

The runtime closure is taken from `uv export --no-dev` (uv resolves it from the
committed uv.lock), so dev/build tooling is excluded and the result is deterministic.
Licence text is read from installed package metadata via the stdlib -- no
console-script .exe to allowlist on a locked-down Windows box.
"""

from __future__ import annotations

import re
import subprocess
from importlib import metadata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Optional-dependency groups that ship with the product (add e.g. "ocr" when that
# stage lands). Dev/build groups are excluded via `uv export --no-dev`.
SHIPPING_EXTRAS = ("detect", "tag", "ocr", "desktop")

# Canonical package names to exempt -- e.g. a dual-licensed dep that reports a GPL
# classifier but is used under its permissive option. Keep short; justify each entry.
ALLOWLIST: set[str] = set()


def canonical(name: str) -> str:
    """PEP 503 normalised distribution name (lower-case; runs of -_. -> single -)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def runtime_closure() -> set[str]:
    """Canonical names of the shipped runtime closure, per `uv export --no-dev`."""
    cmd = ["uv", "export", "--frozen", "--no-dev", "--no-hashes"]
    for extra in SHIPPING_EXTRAS:
        cmd += ["--extra", extra]
    out = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout
    names: set[str] = set()
    for raw in out.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-")):
            continue  # comment lines and the editable root (`-e .`)
        token = re.split(r"[=<>@;\s]", line, maxsplit=1)[0]
        if token:
            names.add(canonical(token))
    return names


def license_texts(dist: metadata.Distribution) -> list[str]:
    """Collect every licence signal for one distribution: the SPDX expression, the
    free-text License field, and any `License ::` trove classifiers."""
    md = dist.metadata
    texts: list[str] = []
    for key in ("License-Expression", "License"):
        value = md.get(key)
        if value:
            texts.append(value)
    texts.extend(c for c in md.get_all("Classifier", []) if c.startswith("License ::"))
    return texts


def is_denied(texts: list[str]) -> bool:
    """True if any licence signal names the GPL family (GPL / AGPL / LGPL).

    Matches both the SPDX/abbreviation forms (which contain `gpl`) and the spelled-out
    `... General Public License` names (which do not).
    """
    joined = " ".join(texts).lower()
    return "gpl" in joined or "general public license" in joined


def scan() -> tuple[list[tuple[str, str]], list[str]]:
    """Return (violations, unknowns) for the shipped runtime closure: denied
    (name, licence) pairs, plus closure names with no discoverable licence metadata."""
    installed = {
        canonical(d.metadata["Name"]): d for d in metadata.distributions() if d.metadata["Name"]
    }
    violations: list[tuple[str, str]] = []
    unknowns: list[str] = []
    for name in sorted(runtime_closure()):
        if name in ALLOWLIST:
            continue
        dist = installed.get(name)
        if dist is None:
            continue  # locked but not installed in this env -- nothing to inspect
        texts = license_texts(dist)
        if not texts:
            unknowns.append(name)
        elif is_denied(texts):
            violations.append((name, "; ".join(texts)))
    return violations, unknowns


def main() -> int:
    try:
        violations, unknowns = scan()
    except FileNotFoundError:
        print("check_licenses: ERROR -- `uv` not found on PATH; cannot resolve the closure.")
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"check_licenses: ERROR -- `uv export` failed:\n{exc.stderr}")
        return 2
    for name in sorted(set(unknowns)):
        print(f"WARNING: no licence metadata for '{name}' -- review manually.")
    for name, lic in sorted(set(violations)):
        print(f"DENIED: '{name}' is GPL-family ({lic}) -- rule L6: no in-process copyleft.")
    if violations:
        print(f"\n{len(violations)} disallowed licence(s) found.")
        return 1
    print("check_licenses: OK (no GPL/AGPL/LGPL in the shipped runtime closure).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
