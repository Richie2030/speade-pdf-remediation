"""Config-driven stage selection.

Maps an implementation name (from `config.yaml`) to a concrete :class:`Stage`.
Adding an engine = registering it here; selecting one = a config edit, not a
code edit.
"""

from __future__ import annotations

from speade.pipeline.contract import Stage
from speade.stages.detect import DetectStage
from speade.stages.noop import NoopStage

_REGISTRY: dict[str, type[Stage]] = {
    "noop": NoopStage,
    "detect": DetectStage,
}


def get_stage(impl: str) -> Stage:
    """Instantiate the stage registered under `impl`."""
    try:
        return _REGISTRY[impl]()
    except KeyError as exc:
        known = ", ".join(sorted(_REGISTRY))
        raise KeyError(f"Unknown stage implementation {impl!r}. Known: {known}") from exc


def available() -> list[str]:
    """Names of all registered stage implementations."""
    return sorted(_REGISTRY)
