from speade.pipeline.contract import Sidecar, Stage
from speade.stages.detect import DetectStage
from speade.stages.noop import NoopStage


def test_with_stage_appends_without_mutating_original():
    s = Sidecar(source_path="x.pdf")
    s2 = s.with_stage("noop")
    assert s.stages_applied == []
    assert s2.stages_applied == ["noop"]


def test_with_stage_deep_copies_flags():
    s = Sidecar(source_path="x.pdf")
    s2 = s.with_stage("detect")
    s2.flags["k"] = "v"
    assert s.flags == {}  # original untouched


def test_stages_satisfy_protocol():
    assert isinstance(NoopStage(), Stage)
    assert isinstance(DetectStage(), Stage)
