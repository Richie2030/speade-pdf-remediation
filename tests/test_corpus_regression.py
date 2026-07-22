"""Corpus regression harness -- the derived fixtures through the REAL pipeline.

Sweeps every available datasets/derived/ fixture through the same stage objects
the CLI runs (registry -> detect -> tag -> runner) and asserts routing, skip
logic, flags, and the trust-trail invariants (original never mutated; output,
persisted sidecar, and audit line all agree). This is the safety net that
protects core behaviour while the UI work happens.

Prerequisites are local-only, so the whole module SKIPS itself on machines
without them (CI has neither the corpus nor the Java toolchain -- that's fine,
this suite is the local safety net, not a CI gate):

  - the corpus:  uv run --extra detect --extra tag --with pypdfium2 \
                     python datasets/build_corpus.py
  - the engine:  opendataloader-pdf on PATH (+ Java 11+)

Default run adds ~1 minute (three real engine runs). Opt-in extras:

  SPEADE_SLOW=1  also push the 150-page fixture through the engine (minutes)
  SPEADE_GATE=1  also score one tagged output with veraPDF (needs the CLI/Docker)
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

pypdf = pytest.importorskip("pypdf", reason="needs --extra detect")
pytest.importorskip("pikepdf", reason="needs --extra tag")

from speade.audit.log import sha256_file  # noqa: E402
from speade.pipeline import registry, runner  # noqa: E402
from speade.pipeline.contract import Sidecar  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "datasets" / "derived"

if not (DERIVED / "manifest.csv").is_file():
    pytest.skip(
        "derived corpus not built -- run datasets/build_corpus.py first",
        allow_module_level=True,
    )
if not shutil.which("opendataloader-pdf"):
    pytest.skip("opendataloader-pdf CLI not on PATH", allow_module_level=True)


def _run(src: Path, tmp_path: Path) -> tuple[Sidecar, Path, Path]:
    """Run `src` through detect -> tag exactly as the CLI would; return
    (final sidecar, output pdf path, audit log path)."""
    stages = [registry.get_stage("detect"), registry.get_stage("tag")]
    outbox = tmp_path / "outbox"
    audit = tmp_path / "audit.jsonl"
    sidecar = runner.run(src, stages, outbox, audit)
    return sidecar, outbox / src.name, audit


# fixture -> (expected route, expected flags, does the tagging engine run?)
#
# mixed_text_image carries the seed's structure tree (already_tagged=True) AND
# routes unknown -- do-not-degrade beats tag-anyway, so the already-tagged skip
# wins. The tag-anyway-on-unknown policy is covered by the stripped-tags test
# below (test_untagged_unknown_route_is_tagged_anyway).
CASES = [
    ("untagged_twin.pdf", "born_digital", [], True),
    ("rotated_90.pdf", "born_digital", ["tag-skipped-already-tagged"], False),
    ("ocred_untagged.pdf", "born_digital", [], True),
    ("tagged_ocred.pdf", "born_digital", ["tag-skipped-already-tagged"], False),
    ("scanned_nothing.pdf", "scanned", ["tag-skipped-needs-ocr"], False),
    ("mixed_text_image.pdf", "unknown", ["tag-skipped-already-tagged"], False),
]


@pytest.mark.parametrize(("name", "route", "flags", "engine"), CASES, ids=[c[0] for c in CASES])
def test_fixture_flows_through_the_pipeline(name, route, flags, engine, tmp_path):
    src = DERIVED / name
    if not src.is_file():
        pytest.skip(f"{name} not in the built corpus")
    hash_before = sha256_file(src)

    sidecar, out, audit = _run(src, tmp_path)

    # invariant: the original is NEVER mutated, and its hash is the recorded source.
    assert sha256_file(src) == hash_before == sidecar.source_sha256

    # routing + skip logic.
    assert sidecar.route == route
    assert sidecar.stages_applied == ["detect", "tag"]
    assert sidecar.flags == flags

    # trust trail: output, persisted sidecar, and audit line must all agree.
    assert out.is_file()
    assert sidecar.output_sha256 == sha256_file(out)
    side_path = out.with_name(out.name + ".sidecar.json")
    persisted = Sidecar.model_validate_json(side_path.read_text(encoding="utf-8"))
    assert persisted.output_sha256 == sidecar.output_sha256
    event = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
    assert event["source_sha256"] == sidecar.source_sha256
    assert event["output_sha256"] == sidecar.output_sha256

    if engine:
        # the engine really tagged it, and the pikepdf finish stamped the
        # conformance bits (structure tree + MarkInfo + language).
        root = pypdf.PdfReader(out).root_object
        assert "/StructTreeRoot" in root
        assert "/MarkInfo" in root
        assert "/Lang" in root
    else:
        # a skipped document must flow to the gate byte-identical.
        assert sidecar.output_sha256 == sidecar.source_sha256


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("encrypted_userpw.pdf", "unreadable-encrypted-password-required"),
        ("corrupt_truncated.pdf", "unreadable-corrupt"),
    ],
)
def test_unreadable_inputs_flow_to_the_gate_with_a_reason(name, reason, tmp_path):
    """P1.4 policy: unreadable inputs are neither crashed on nor guessed at.
    The run completes, the reason is on the sidecar, tag skips, and the human
    gate -- not the pipeline -- decides what happens to the document."""
    src = DERIVED / name
    if not src.is_file():
        pytest.skip(f"{name} not in the built corpus")

    sidecar, out, _ = _run(src, tmp_path)

    assert sidecar.route == "unknown"
    assert any(flag.startswith(reason) for flag in sidecar.flags)
    assert "tag-skipped-unreadable" in sidecar.flags
    assert sidecar.stages_applied == ["detect", "tag"]
    assert out.is_file()
    # the document reaches the gate byte-identical -- nothing pretended to fix it.
    assert sidecar.output_sha256 == sidecar.source_sha256


def test_untagged_unknown_route_is_tagged_anyway(tmp_path):
    """P1.4 policy: a readable mixed doc (route unknown) WITHOUT existing tags is
    tagged anyway -- text pages gain structure now -- and flagged so the reviewer
    checks coverage of the image-only pages."""
    import pikepdf

    src = DERIVED / "mixed_text_image.pdf"
    if not src.is_file():
        pytest.skip("mixed_text_image.pdf not in the built corpus")

    untagged = tmp_path / "mixed_untagged.pdf"
    with pikepdf.open(src) as doc:
        for key in ("/StructTreeRoot", "/MarkInfo"):
            if key in doc.Root:
                del doc.Root[key]
        doc.save(untagged)

    sidecar, out, _ = _run(untagged, tmp_path)

    assert sidecar.route == "unknown"
    assert "tag-ran-on-unknown-route" in sidecar.flags
    assert "/StructTreeRoot" in pypdf.PdfReader(out).root_object


def test_scanned_doc_ocr_then_tag_full_chain(tmp_path):
    """P1.5: the full detect -> ocr -> tag chain on an image-only doc. OCR flips
    the route to born_digital, tag runs, and the final output is a tagged PDF
    with real extractable text -- the complete scanned-document story."""
    pytest.importorskip("pypdfium2", reason="needs --extra ocr")
    from speade.stages.ocr import find_tesseract

    if find_tesseract() is None:
        pytest.skip("tesseract not installed")
    src = DERIVED / "scanned_nothing.pdf"
    if not src.is_file():
        pytest.skip("scanned_nothing.pdf not in the built corpus")

    stages = [registry.get_stage(name) for name in ("detect", "ocr", "tag")]
    outbox = tmp_path / "outbox"
    sidecar = runner.run(src, stages, outbox, tmp_path / "audit.jsonl")
    out = outbox / src.name

    assert sidecar.stages_applied == ["detect", "ocr", "tag"]
    assert sidecar.route == "born_digital"  # flipped by ocr on success
    assert sidecar.flags == []  # nothing skipped, nothing flagged
    reader = pypdf.PdfReader(out)
    assert "/StructTreeRoot" in reader.root_object
    text = "".join((page.extract_text() or "") for page in reader.pages)
    assert len(text.strip()) > 100  # the text layer is real, not vestigial

    # the Figure-box fix: page scans are background artifacts, never content --
    # a screen reader must not hear "figure" announced on every scanned page.
    from speade.validation.structure import summarize

    summary = summarize(out)
    assert summary.tagged is True
    assert summary.figures == 0


@pytest.mark.skipif(not os.environ.get("SPEADE_SLOW"), reason="150pp engine run; SPEADE_SLOW=1")
def test_long_document_tags_end_to_end(tmp_path):
    src = DERIVED / "long_150pp.pdf"
    if not src.is_file():
        pytest.skip("long_150pp.pdf not in the built corpus")

    sidecar, out, _ = _run(src, tmp_path)

    assert sidecar.route == "born_digital"
    assert sidecar.flags == []
    assert "/StructTreeRoot" in pypdf.PdfReader(out).root_object
    assert len(pypdf.PdfReader(out).pages) == 150


@pytest.mark.skipif(not os.environ.get("SPEADE_GATE"), reason="veraPDF scoring; SPEADE_GATE=1")
def test_tagging_fixes_the_untagged_content_clause(tmp_path):
    """The tag stage must at least cure 6.2-1 (untagged content) -- the clause the
    raw untagged fixture fails. Full UA-1 passes are doc-dependent (fonts/forms),
    so this asserts the improvement, not perfection."""
    from speade.validation import verapdf

    src = DERIVED / "untagged_twin.pdf"
    if not src.is_file():
        pytest.skip("untagged_twin.pdf not in the built corpus")

    _, out, _ = _run(src, tmp_path)
    result = verapdf.validate(out)

    assert "verapdf-unavailable" not in result.failed_clauses, "no veraPDF runner found"
    assert "verapdf-bad-report" not in result.failed_clauses
    assert "6.2-1" not in result.failed_clauses
