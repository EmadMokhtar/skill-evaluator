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
                result=RunResult(output="yes", output_tokens=10, cost_usd=0.01, latency_ms=5),
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
    # The first outcome has latency_ms=5, second has latency_ms defaulting to 0
    # Total is 5ms, which should appear as the exact substring from the console implementation
    assert "Total latency: 5ms" in text


def test_json_summary_includes_total_latency():
    data = json.loads(render_json(_report()))
    assert "total_latency_ms" in data["summary"]
    # First outcome has latency_ms=5, second has 0 (default)
    assert data["summary"]["total_latency_ms"] == 5


def test_json_summary_includes_total_tokens_and_cost():
    data = json.loads(render_json(_report()))
    assert "total_tokens" in data["summary"]
    assert "total_cost_usd" in data["summary"]
    # First outcome has output_tokens=10, cost=0.01; second has defaults
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
                result=RunResult(output_tokens=10, cost_usd=0.01, latency_ms=100),
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
    # Assert the exact latency substring: a loose check like `"100" in text` also
    # matches the "pass rate 100%" line, so it would pass with no latency output.
    assert "Total latency: 100ms" in console_text
    assert "2 passed" in console_text

    # JSON should not crash and totals should exclude the None result
    json_text = render_json(report)
    data = json.loads(json_text)
    assert data["summary"]["total_tokens"] == 10
    assert data["summary"]["total_cost_usd"] == 0.01
    assert data["summary"]["total_latency_ms"] == 100


def test_console_notes_when_pricing_degraded_even_at_zero_total_cost():
    # total_cost is 0.0 whether the run genuinely cost nothing or pricing simply
    # failed for every outcome. `if total_cost:` is falsy either way, so the one
    # visual cue for degraded pricing disappeared exactly when it mattered most.
    report = RunReport(
        outcomes=[
            CaseOutcome(
                skill_name="pdf",
                case_name="extracts",
                runner="pydantic-ai",
                status="passed",
                scores=[],
                result=RunResult(cost_usd=0.0, cost_note="no price data for groq:llama (KeyError)"),
            ),
        ],
    )
    text = render_console(report)
    assert "Total cost: not priced (see per-case cost_note in the JSON report)" in text


def test_console_totals_line_stays_silent_when_cost_is_genuinely_zero():
    report = RunReport(
        outcomes=[
            CaseOutcome(
                skill_name="pdf",
                case_name="extracts",
                runner="fake",
                status="passed",
                scores=[],
                result=RunResult(cost_usd=0.0, cost_note=""),
            ),
        ],
    )
    text = render_console(report)
    assert "Total cost" not in text
    # cost_usd and latency_ms are both zero here, so the totals line has nothing
    # to report at all -- not even a latency figure -- and must not appear.
    assert "Total latency" not in text
    assert "not priced" not in text.lower()


def test_json_carries_model_and_cost_note_per_outcome():
    # The adapter goes to real trouble to capture the dated snapshot name the
    # provider actually served, and cost_note is the only visible signal that
    # pricing degraded (e.g. an unpriced Groq/Mistral model). Neither is worth
    # anything if the JSON artifact drops them on the floor.
    report = RunReport(
        outcomes=[
            CaseOutcome(
                skill_name="pdf",
                case_name="extracts",
                runner="pydantic-ai",
                status="passed",
                scores=[],
                result=RunResult(
                    output="yes",
                    model="gpt-4o-mini-2024-07-18",
                    cost_note="no price data for groq:llama (KeyError)",
                ),
            ),
            CaseOutcome(
                skill_name="pdf",
                case_name="no result",
                runner="fake",
                status="passed",
                scores=[],
                result=None,
            ),
        ],
    )
    data = json.loads(render_json(report))
    first = data["outcomes"][0]
    assert first["model"] == "gpt-4o-mini-2024-07-18"
    assert first["cost_note"] == "no price data for groq:llama (KeyError)"

    second = data["outcomes"][1]
    assert second["model"] == ""
    assert second["cost_note"] == ""


def test_json_includes_tag_filtered_skills():
    # The console reporter distinguishes "skipped (no eval cases)" from "no cases
    # matched --tag"; the JSON payload must carry the same field so CI tooling can
    # explain why a run executed zero cases.
    report = RunReport(outcomes=[], skipped_skills=["pdf"], tag_filtered_skills=["xlsx"])
    data = json.loads(render_json(report))
    assert data["skipped_skills"] == ["pdf"]
    assert data["tag_filtered_skills"] == ["xlsx"]
