"""Paired comparison of the two arms."""

from __future__ import annotations

from skill_eval.comparison import build_delta
from skill_eval.models import (
    BaselineNote,
    CaseOutcome,
    CheckResult,
    EvalScore,
    RunReport,
    RunResult,
)


def _outcome(
    status, arm="candidate", index=0, *, case="c", tokens=100, cost=0.001, latency=500, checks=()
):
    return CaseOutcome(
        skill_name="pdf",
        case_name=case,
        runner="fake",
        status=status,
        arm=arm,
        repeat_index=index,
        scores=[
            EvalScore(
                evaluator="assertion",
                passed=status == "passed",
                checks=[CheckResult(id=cid, passed=ok, evidence="e") for cid, ok in checks],
            )
        ],
        result=RunResult(input_tokens=tokens, output_tokens=0, cost_usd=cost, latency_ms=latency),
    )


def test_no_baseline_arm_means_no_delta():
    report = RunReport(outcomes=[_outcome("passed")])
    assert build_delta(report) is None


def test_the_delta_is_the_candidate_minus_the_baseline():
    report = RunReport(
        baseline_kind="none",
        outcomes=[_outcome("passed"), _outcome("failed", arm="baseline")],
    )
    delta = build_delta(report)
    assert delta.pass_rate_candidate == 1.0
    assert delta.pass_rate_baseline == 0.0
    assert delta.pass_rate_delta == 1.0


def test_efficiency_deltas_are_per_run_means():
    report = RunReport(
        baseline_kind="none",
        outcomes=[
            _outcome("passed", tokens=100, cost=0.002, latency=400),
            _outcome("passed", arm="baseline", tokens=150, cost=0.003, latency=600),
        ],
    )
    delta = build_delta(report)
    assert delta.tokens_delta == -50.0
    assert round(delta.cost_usd_delta, 6) == -0.001
    assert delta.latency_ms_delta == -200.0


def test_a_case_with_no_baseline_run_is_excluded_from_both_halves():
    report = RunReport(
        baseline_kind="none",
        outcomes=[
            _outcome("passed", case="paired"),
            _outcome("failed", arm="baseline", case="paired"),
            _outcome("failed", case="lonely"),  # candidate only
        ],
    )
    delta = build_delta(report)
    lonely = next(c for c in delta.cases if c.case_name == "lonely")
    assert lonely.comparable is False
    assert lonely.exclusion_reason
    # The lonely failure must not drag the candidate rate: only `paired` counts.
    assert delta.pass_rate_candidate == 1.0


def test_an_errored_arm_invalidates_the_pair():
    report = RunReport(
        baseline_kind="none",
        outcomes=[_outcome("passed"), _outcome("errored", arm="baseline")],
    )
    delta = build_delta(report)
    assert delta.cases[0].comparable is False
    assert "errored" in delta.cases[0].exclusion_reason


def test_errored_repetitions_are_dropped_from_the_rate_not_counted_as_failures():
    report = RunReport(
        baseline_kind="none",
        repeat=2,
        outcomes=[
            _outcome("passed", index=0),
            _outcome("errored", index=1),
            _outcome("failed", arm="baseline", index=0),
            _outcome("failed", arm="baseline", index=1),
        ],
    )
    delta = build_delta(report)
    assert delta.cases[0].candidate.pass_rate == 1.0
    assert delta.cases[0].candidate.errored == 1


def test_the_baseline_notes_travel_with_the_delta():
    report = RunReport(
        baseline_kind="previous",
        baseline_notes=[BaselineNote(skill_name="pdf", kind="unavailable", reason="no repo")],
        outcomes=[_outcome("passed"), _outcome("passed", arm="baseline")],
    )
    assert build_delta(report).notes == ["pdf: baseline unavailable — no repo"]
