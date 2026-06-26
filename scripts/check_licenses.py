#!/usr/bin/env python
"""Dependency-licence allowlist gate (licence rule L6).

Scope: the **runtime** dependency closure we actually ship (base + extras),
resolved via `uv export --no-dev`. Dev/CI-only tooling (ruff, pytest, the SBOM
generator, ...) is NOT distributed, so its transitive licences are out of scope.

Fails if any shipped package's licence is not on the permissive / weak-copyleft
(MPL) allowlist, or if it looks copyleft (GPL/AGPL/LGPL). GPL-family tools are
only permitted at arms length (subprocess/Docker), never as a pip dependency.
Verified false positives (metadata missing an SPDX classifier) are cleared
per-package in tools/licenses_exceptions.txt.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = ROOT / "tools" / "licenses_allowlist.txt"
EXCEPTIONS = ROOT / "tools" / "licenses_exceptions.txt"

# Fail closed on anything that looks copyleft, even if it also matches an allow token.
BANNED_TOKENS = ("gpl", "agpl", "lgpl", "gnugeneral", "gnuaffero", "gnulesser", "copyleft")

_REQ_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


def norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def load_tokens(path: Path) -> set[str]:
    if not path.exists():
        return set()
    lines = path.read_text().splitlines()
    return {norm(x) for x in lines if x.strip() and not x.startswith("#")}


def load_names(path: Path) -> set[str]:
    if not path.exists():
        return set()
    lines = path.read_text().splitlines()
    return {x.strip().lower() for x in lines if x.strip() and not x.startswith("#")}


def runtime_packages() -> set[str] | None:
    """Normalised names of the shipped runtime closure (base + extras, no dev)."""
    try:
        out = subprocess.run(
            ["uv", "export", "--no-dev", "--all-extras", "--no-hashes", "--no-emit-project"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None  # not a uv project / uv missing -> fall back to checking everything
    names: set[str] = set()
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        m = _REQ_NAME.match(line)
        if m:
            names.add(norm(m.group(1)))
    return names


def main() -> int:
    allow = load_tokens(ALLOWLIST)
    exceptions = load_names(EXCEPTIONS)
    runtime = runtime_packages()

    raw = subprocess.run(
        ["pip-licenses", "--format=json"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    packages = json.loads(raw)

    offenders: list[str] = []
    checked = 0
    for pkg in packages:
        name = pkg["Name"].lower()
        if runtime is not None and norm(name) not in runtime:
            continue  # dev/CI-only tool, not shipped
        if name in exceptions:
            continue
        checked += 1
        license_str = pkg.get("License", "") or ""
        nl = norm(license_str)
        tag = f"{pkg['Name']} {pkg['Version']}: {license_str or 'UNKNOWN'}"
        if any(b in nl for b in BANNED_TOKENS):
            offenders.append(f"{tag}  [copyleft]")
        elif not nl or nl == "unknown":
            offenders.append(f"{tag}  [no licence metadata]")
        elif not any(a in nl for a in allow):
            offenders.append(f"{tag}  [not on allowlist]")

    if offenders:
        print("DISALLOWED LICENCES (runtime closure):")
        for o in offenders:
            print("  " + o)
        print(f"\nAllowlist: {ALLOWLIST}")
        print(f"Clear a verified false positive by adding the package name to {EXCEPTIONS}.")
        return 1

    scope = "runtime closure" if runtime is not None else "full environment"
    print(f"OK: all {checked} packages ({scope}) pass the licence allowlist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
