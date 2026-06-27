"""Tagging stage -- writes the PDF/UA structure (tags, reading order, headings,
tables, language, title). The hardest, make-or-break component. The engine
(free Acrobat vs pikepdf vs pdfix) is chosen by the Phase-1 spike; copyleft/CLI
engines shell out, never imported (rule L2).
"""

from __future__ import annotations

from pathlib import Path

from speade.pipeline.contract import Sidecar, StageResult
