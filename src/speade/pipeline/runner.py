"""Run a PDF through one or more stages.

Implement here the invariant that the ORIGINAL file is never mutated: copy it
into the output dir once, run each stage on that working copy threading the
sidecar through, and persist the sidecar JSON next to the output.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from speade.pipeline.contract import StageResult
