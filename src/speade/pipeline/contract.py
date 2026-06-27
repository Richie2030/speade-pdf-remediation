"""The tool-agnostic stage contract: the shared (PDF + sidecar) -> (PDF + sidecar)
shape every stage implements, so stages are swappable by config.

Define here: the Route enum (born-digital / scanned / unknown), the Sidecar and
StageResult models, and the Stage protocol. Engine adapters shell out to
copyleft/CLI tools at arms length -- never import a GPL/AGPL library (rule L2).
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field
