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


def test_efficiency_deltas_stay_means_when_the_arms_ran_different_counts():
    report = RunReport(
        baseline_kind="none",
        repeat=2,
        outcomes=[
            _outcome("passed", index=0, tokens=100),
            _outcome("passed", index=1, tokens=300),
            _outcome("passed", arm="baseline", index=0, tokens=50),
        ],
    )
    delta = build_delta(report)
    # candidate mean = (100 + 300) / 2 = 200; baseline mean = 50 / 1 = 50.
    # A summation regression would compute (100 + 300) - 50 = 350 instead of 150.
    assert delta.tokens_delta == 150.0


def test_a_check_that_passes_in_both_arms_is_low_signal():
    report = RunReport(
        baseline_kind="none",
        outcomes=[
            _outcome("passed", checks=(("contains[0]", True), ("contains[1]", True))),
            _outcome(
                "failed", arm="baseline", checks=(("contains[0]", True), ("contains[1]", False))
            ),
        ],
    )
    delta = build_delta(report)
    assert [c.check_id for c in delta.low_signal] == ["contains[0]"]
    assert delta.cases[0].low_signal == ["contains[0]"]


def test_a_check_that_fails_anywhere_is_not_low_signal():
    report = RunReport(
        baseline_kind="none",
        repeat=2,
        outcomes=[
            _outcome("passed", index=0, checks=(("contains[0]", True),)),
            _outcome("failed", index=1, checks=(("contains[0]", False),)),
            _outcome("passed", arm="baseline", index=0, checks=(("contains[0]", True),)),
            _outcome("passed", arm="baseline", index=1, checks=(("contains[0]", True),)),
        ],
    )
    assert build_delta(report).low_signal == []


def test_an_excluded_case_contributes_no_low_signal_checks():
    # Without a baseline half there is nothing to compare against, so calling a
    # check "low signal" would be an unsupported claim.
    report = RunReport(
        baseline_kind="none",
        outcomes=[
            _outcome("passed", case="lonely", checks=(("contains[0]", True),)),
            _outcome("passed", case="paired", checks=(("contains[0]", True),)),
            _outcome("failed", arm="baseline", case="paired", checks=(("contains[0]", False),)),
        ],
    )
    assert build_delta(report).low_signal == []


def test_disagreeing_repetitions_flag_a_high_variance_case():
    report = RunReport(
        baseline_kind="none",
        repeat=2,
        outcomes=[
            _outcome("passed", index=0),
            _outcome("failed", index=1),
            _outcome("failed", arm="baseline", index=0),
            _outcome("failed", arm="baseline", index=1),
        ],
    )
    delta = build_delta(report)
    assert [(r.arm, r.pass_rate) for r in delta.high_variance] == [("candidate", 0.5)]
    assert delta.high_variance[0].stddev == 0.5
    assert delta.cases[0].high_variance is True


def test_unanimous_repetitions_are_not_flagged():
    report = RunReport(
        baseline_kind="none",
        repeat=2,
        outcomes=[
            _outcome("passed", index=0),
            _outcome("passed", index=1),
            _outcome("failed", arm="baseline", index=0),
            _outcome("failed", arm="baseline", index=1),
        ],
    )
    assert build_delta(report).high_variance == []
