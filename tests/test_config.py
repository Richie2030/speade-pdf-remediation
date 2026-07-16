"""Tests for the config schema + loader (src/speade/config.py).

Guards that the schema matches the committed config.yaml, that defaults are sane,
and the SEC1 invariant: v1 is fully offline, so the config model carries no
secret-bearing field (nothing that could leak a credential into config.yaml).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from speade.config import Config, load_config

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config.yaml"


def test_loads_the_committed_config_yaml():
    cfg = load_config(REPO_CONFIG)
    assert cfg.io.client == "local"
    assert cfg.io.local.inbox == Path("data/inbox")
    assert cfg.io.local.outbox == Path("data/outbox")
    assert cfg.io.local.sidecars == Path("data/sidecars")
    assert cfg.pipeline.stages == {"detect": "detect", "ocr": "ocr", "tag": "tag"}
    assert cfg.validation.verapdf.profile == "ua1"
    assert cfg.audit.log_path == Path("data/audit/audit.jsonl")


def test_defaults_are_sane():
    cfg = Config()
    assert cfg.io.client == "local"
    assert cfg.pipeline.stages == {}
    assert cfg.validation.verapdf.profile == "ua1"


def test_no_secret_bearing_fields_sec1():
    """SEC1: non-secret config only. v1 is fully offline (no Canvas/API tokens),
    so no field anywhere in the model tree may look like a credential."""

    def field_names(model: type[BaseModel]):
        for name, info in model.model_fields.items():
            yield name
            annotation = info.annotation
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                yield from field_names(annotation)

    joined = " ".join(field_names(Config)).lower()
    for banned in ("token", "secret", "password", "credential", "apikey"):
        assert banned not in joined, f"config exposes a {banned!r} field (SEC1)"
