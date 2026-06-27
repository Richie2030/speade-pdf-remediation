"""Config-driven stage selection: map an implementation name (from config.yaml)
to a Stage instance. Registering an engine is a one-line addition here; selecting
one is a config edit. Define the registry mapping plus get_stage/available here.
"""

from __future__ import annotations

from speade.pipeline.contract import Stage
from speade.stages.detect import DetectStage
from speade.stages.noop import NoopStage
from speade.stages.ocr import OcrStage
from speade.stages.tag import TagStage
from speade.stages.vlm import VlmStage
