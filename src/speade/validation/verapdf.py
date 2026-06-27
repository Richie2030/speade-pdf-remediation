"""veraPDF acceptance harness (deliverable F4) -- the objective PDF/UA scorer.

Shell out to the pinned veraPDF Docker image (ghcr.io/verapdf/cli) as an
arms-length subprocess (never import it); parse its JSON report into a pass/fail
+ failing-clause result. Pin the image tag; parse the report, don't gate on the
process exit code. This is the scorer the Phase-1 tagger spike is judged by.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field
