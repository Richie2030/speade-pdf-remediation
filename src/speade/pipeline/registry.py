"""Config-driven stage selection: map an implementation name (from config.yaml)
to a Stage instance. Registering an engine is a one-line addition to _REGISTRY;
selecting one is a config edit. Public surface: get_stage() and available().

Resolution is LAZY: an engine module is imported only when its name is selected, so
the offline default (`noop`) never drags in deferred stages (detect/ocr) or their heavy
extras (pypdf, ...). This keeps `import registry` cheap, and lets the core
stay importable while detect/tag/ocr are still docstring-only scaffolds.
"""

from __future__ import annotations

from importlib import import_module

from speade.pipeline.contract import Stage  # Pure types - cheap, imports no engine

_REGISTRY: dict[str, str] = {
    "noop": "speade.stages.noop:NoopStage",
    "detect": "speade.stages.detect:DetectStage",
    "ocr": "speade.stages.ocr:OcrStage",
    "tag": "speade.stages.tag:TagStage",
}


def available() -> list[str]:
    return sorted(_REGISTRY)  # Pure dict op - imports nothing


def get_stage(name: str) -> Stage:
    try:
        target = _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown stage {name!r}; available: {available()}") from None
    module_name, class_name = target.split(":")
    stage_cls = getattr(import_module(module_name), class_name)
    return stage_cls()
