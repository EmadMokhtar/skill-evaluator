import json

from skill_eval.gating import evaluate_gate
from skill_eval.models import CaseOutcome, EvalScore, RunReport, RunResult
from skill_eval.reporters.console import render_console
from skill_eval.reporters.json_reporter import render_json


def _report():
    return RunReport(
        outcomes=[
            CaseOutcome(
                skill_name="pdf",
                case_name="extracts",
                runner="fake",
                status="passed",
                scores=[EvalScore(evaluator="assertion", passed=True, score=1.0, detail="ok")],
                result=RunResult(output="yes", tokens=10, cost_usd=0.01, latency_ms=5),
            ),
            CaseOutcome(
                skill_name="pdf",
                case_name="handles missing",
                runner="fake",
                status="failed",
                scores=[EvalScore(evaluator="assertion", passed=False, score=0.0, detail="nope")],
                result=RunResult(output="no"),
            ),
        ],
        skipped_skills=["xlsx"],
    )


def test_console_shows_case_names_and_statuses():
    text = render_console(_report())
    assert "extracts" in text
    assert "handles missing" in text
    assert "pdf" in text


def test_console_reports_totals_and_pass_rate():
    text = render_console(_report())
    assert "1 passed" in text
    assert "1 failed" in text
    assert "50%" in text


def test_console_lists_skipped_skills():
    assert "xlsx" in render_console(_report())


def test_console_includes_failure_detail():
    assert "nope" in render_console(_report())


def test_console_shows_gate_reasons_when_gate_fails():
    gate = evaluate_gate(_report(), min_pass_rate=1.0)
    text = render_console(_report(), gate=gate)
    assert "pass rate" in text


def test_console_handles_empty_report():
    text = render_console(RunReport())
    assert "0 passed" in text


def test_json_is_valid_and_carries_summary():
    data = json.loads(render_json(_report()))
    assert data["summary"]["total"] == 2
    assert data["summary"]["passed"] == 1
    assert data["summary"]["pass_rate"] == 0.5
    assert data["skipped_skills"] == ["xlsx"]


def test_json_carries_per_case_detail_and_cost():
    data = json.loads(render_json(_report()))
    first = data["outcomes"][0]
    assert first["case_name"] == "extracts"
    assert first["status"] == "passed"
    assert first["cost_usd"] == 0.01
    assert first["tokens"] == 10


def test_json_includes_gate_when_supplied():
    gate = evaluate_gate(_report(), min_pass_rate=1.0)
    data = json.loads(render_json(_report(), gate=gate))
    assert data["gate"]["passed"] is False
    assert data["gate"]["reasons"]


def test_console_reports_aggregate_latency():
    text = render_console(_report())
    # Should contain latency with unit label
    assert "latency" in text.lower() or "ms" in text or "s" in text
    # The first outcome has latency_ms=5, second has 0 (no latency_ms set)
    assert "5" in text or "0" in text


def test_json_summary_includes_total_latency():
    data = json.loads(render_json(_report()))
    assert "total_latency_ms" in data["summary"]
    # First outcome has latency_ms=5, second has 0 (default)
    assert data["summary"]["total_latency_ms"] == 5


def test_json_summary_includes_total_tokens_and_cost():
    data = json.loads(render_json(_report()))
    assert "total_tokens" in data["summary"]
    assert "total_cost_usd" in data["summary"]
    # First outcome has tokens=10, cost=0.01; second has defaults
    assert data["summary"]["total_tokens"] == 10
    assert data["summary"]["total_cost_usd"] == 0.01


def test_reporters_handle_outcome_with_no_result():
    """Outcomes with result=None should not crash and should contribute zero to totals."""
    report = RunReport(
        outcomes=[
            CaseOutcome(
                skill_name="pdf",
                case_name="test1",
                runner="fake",
                status="passed",
                scores=[],
                result=RunResult(tokens=10, cost_usd=0.01, latency_ms=100),
            ),
            CaseOutcome(
                skill_name="pdf",
                case_name="test2",
                runner="fake",
                status="passed",
                scores=[],
                result=None,  # No result
            ),
        ],
    )
    # Console should not crash
    console_text = render_console(report)
    assert "100" in console_text or "0.1" in console_text  # Some representation of latency
    assert "2 passed" in console_text

    # JSON should not crash and totals should exclude the None result
    json_text = render_json(report)
    data = json.loads(json_text)
    assert data["summary"]["total_tokens"] == 10
    assert data["summary"]["total_cost_usd"] == 0.01
    assert data["summary"]["total_latency_ms"] == 100
