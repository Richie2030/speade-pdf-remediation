"""Passthrough stage -- the simplest Stage implementation and a worked reference
to copy for the others: it changes nothing, just records that it ran.
"""

from __future__ import annotations

from pathlib import Path

from speade.pipeline.contract import Sidecar, StageResult
