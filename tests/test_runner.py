import json

from speade.pipeline import runner


def test_pipeline_does_not_mutate_original_and_writes_sidecar(tmp_path):
    src = tmp_path / "in.pdf"
    src.write_bytes(b"%PDF-1.7 fake")
    out_dir = tmp_path / "out"

    result = runner.run_pipeline(src, ["noop", "detect"], out_dir)

    # original untouched (reversibility / do-not-degrade seed)
    assert src.read_bytes() == b"%PDF-1.7 fake"
    # output is a separate file
    assert result.output_pdf.exists()
    assert result.output_pdf.resolve() != src.resolve()
    # stage trail recorded in order
    assert result.sidecar.stages_applied == ["noop", "detect"]
    assert result.sidecar.source_sha256 is not None

    # sidecar persisted next to the output
    sc = runner.sidecar_path(result.output_pdf)
    assert sc.exists()
    data = json.loads(sc.read_text(encoding="utf-8"))
    assert data["stages_applied"] == ["noop", "detect"]


def test_unknown_stage_raises(tmp_path):
    src = tmp_path / "in.pdf"
    src.write_bytes(b"%PDF")
    try:
        runner.run_pipeline(src, ["does-not-exist"], tmp_path / "out")
    except KeyError as exc:
        assert "does-not-exist" in str(exc)
    else:
        raise AssertionError("expected KeyError for unknown stage")
