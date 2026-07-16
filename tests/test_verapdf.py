"""Tests for the veraPDF harness (src/speade/validation/verapdf.py).

The PARSER is tested against fixture JSON -- no veraPDF needed. validate() is
tested with a mocked subprocess + mocked runner discovery, so the runner
preference (local Java CLI -> Docker image -> fail closed) and the "gate on the
report, not the exit code" rule are covered hermetically; only a real end-to-end
run needs an actual veraPDF install.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from speade.validation import verapdf
from speade.validation.verapdf import VeraResult


def _report(compliant, rule_summaries):
    return {
        "report": {
            "jobs": [
                {
                    "validationResult": {
                        "profileName": "PDF/UA-1 validation profile",
                        "compliant": compliant,
                        "details": {"ruleSummaries": rule_summaries},
                    }
                }
            ]
        }
    }


def test_parses_a_compliant_report():
    result = VeraResult.from_report(_report(True, []), "ua1")
    assert result.passed is True
    assert result.failed_clauses == []
    assert "PDF/UA-1" in result.profile


def test_parses_failing_clauses():
    rules = [
        {"clause": "7.1", "testNumber": 1, "status": "failed"},
        {"clause": "7.2", "testNumber": 3, "status": "failed"},
        {"clause": "7.3", "testNumber": 1, "status": "passed"},
    ]
    result = VeraResult.from_report(_report(False, rules), "ua1")
    assert result.passed is False
    assert result.failed_clauses == ["7.1-1", "7.2-3"]


def test_validationresult_may_be_a_list():
    report = _report(True, [])
    job = report["report"]["jobs"][0]
    job["validationResult"] = [job["validationResult"]]  # some releases emit an array
    assert VeraResult.from_report(report, "ua1").passed is True


def test_missing_validation_result_is_a_failure():
    result = VeraResult.from_report({"report": {"jobs": []}}, "ua1")
    assert result.passed is False
    assert result.failed_clauses == ["no-validation-result"]


def _run_recorder(monkeypatch, report, returncode=0):
    """Mock subprocess.run to record the command and return a canned report."""
    seen = {}
    fake = subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=json.dumps(report), stderr=""
    )

    def record(cmd, **kwargs):
        seen["cmd"] = cmd
        return fake

    # verapdf shells out via speade.subproc (the hidden-console wrapper), which
    # forwards to stdlib subprocess.run -- patch that single choke point.
    monkeypatch.setattr(subprocess, "run", record)
    return seen


def test_validate_gates_on_report_not_exit_code(monkeypatch):
    # veraPDF exits non-zero for a non-compliant file; validate must still parse it.
    rules = [{"clause": "7.1", "testNumber": 1, "status": "failed"}]
    monkeypatch.setattr(verapdf, "_local_cli", lambda: "verapdf")
    _run_recorder(monkeypatch, _report(False, rules), returncode=1)

    result = verapdf.validate(Path("work/sample.pdf"))
    assert result.passed is False
    assert result.failed_clauses == ["7.1-1"]


def test_validate_prefers_the_local_java_cli(monkeypatch):
    # A verapdf on PATH wins: direct invocation, no Docker anywhere in the command.
    monkeypatch.setattr(verapdf, "_local_cli", lambda: "/opt/verapdf/verapdf")
    seen = _run_recorder(monkeypatch, _report(True, []))

    result = verapdf.validate(Path("work/sample.pdf"))
    assert result.passed is True
    assert seen["cmd"][0] == "/opt/verapdf/verapdf"
    assert "docker" not in seen["cmd"]


def test_validate_explicit_cli_path_overrides_discovery(monkeypatch):
    # config validation.verapdf.path beats PATH discovery.
    monkeypatch.setattr(verapdf, "_local_cli", lambda: "/wrong/one")
    seen = _run_recorder(monkeypatch, _report(True, []))

    verapdf.validate(Path("work/sample.pdf"), cli="C:/tools/verapdf.bat")
    assert seen["cmd"][0] == "C:/tools/verapdf.bat"


def test_validate_falls_back_to_docker(monkeypatch):
    # No local CLI -> the pinned Docker image runs the same arguments.
    monkeypatch.setattr(verapdf, "_local_cli", lambda: None)
    monkeypatch.setattr(verapdf.shutil, "which", lambda name: "docker")
    seen = _run_recorder(monkeypatch, _report(True, []))

    result = verapdf.validate(Path("work/sample.pdf"))
    assert result.passed is True
    assert seen["cmd"][0] == "docker"
    assert verapdf.VERAPDF_IMAGE in seen["cmd"]


def test_validate_fails_closed_with_no_runner(monkeypatch):
    # Neither a local CLI nor docker: fail closed WITHOUT spawning anything.
    monkeypatch.setattr(verapdf, "_local_cli", lambda: None)
    monkeypatch.setattr(verapdf.shutil, "which", lambda name: None)

    def boom(*args, **kwargs):  # pragma: no cover - only fires on regression
        raise AssertionError("no subprocess may run when no runner exists")

    monkeypatch.setattr(subprocess, "run", boom)

    result = verapdf.validate(Path("work/sample.pdf"))
    assert result.passed is False
    assert result.failed_clauses == ["verapdf-unavailable"]
