"""Tests for the stage registry (src/speade/pipeline/registry.py).

The key property is LAZY resolution: importing the registry and resolving `noop` must
work even though detect/tag/ocr are still docstring-only scaffolds -- i.e. the
offline default does not depend on the deferred stages existing.
"""

from __future__ import annotations

import pytest

from speade.pipeline import registry
from speade.pipeline.contract import Stage
from speade.stages.noop import NoopStage


def test_available_lists_names_without_importing_engines():
    names = registry.available()
    assert "noop" in names
    assert names == sorted(names)  # stable, sorted order


def test_get_stage_resolves_noop_to_a_stage_instance():
    stage = registry.get_stage("noop")
    assert isinstance(stage, NoopStage)
    assert isinstance(stage, Stage)  # satisfies the protocol


def test_get_stage_rejects_unknown_name():
    with pytest.raises(KeyError):
        registry.get_stage("does-not-exist")
