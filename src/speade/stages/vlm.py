"""Figure alt-text DRAFT stage (Stage 5) -- DEFERRED / OPTIONAL, built last.
Draft-only behind the mandatory human gate; gated on the AI mandate (G1). Talks
to a local Qwen2.5-VL-7B over an HTTP endpoint (Ollama).
"""

from __future__ import annotations

from pathlib import Path

from speade.pipeline.contract import Sidecar, StageResult
