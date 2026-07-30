from skill_eval.gating import EXIT_FAILED, EXIT_OK, evaluate_gate
from skill_eval.models import CaseOutcome, RunReport


def _report(*statuses_by_skill):
    outcomes = [
        CaseOutcome(skill_name=skill, case_name=f"c{i}", runner="fake", status=status)
        for i, (skill, status) in enumerate(statuses_by_skill)
    ]
    return RunReport(outcomes=outcomes)


def test_all_passing_meets_the_gate():
    gate = evaluate_gate(_report(("a", "passed"), ("a", "passed")))
    assert gate.passed is True
    assert gate.exit_code == EXIT_OK


def test_pass_rate_below_threshold_fails():
    gate = evaluate_gate(_report(("a", "passed"), ("a", "failed")), min_pass_rate=0.9)
    assert gate.passed is False
    assert gate.exit_code == EXIT_FAILED
    assert any("pass rate" in r for r in gate.reasons)


def test_pass_rate_at_threshold_passes():
    assert (
        evaluate_gate(_report(("a", "passed"), ("a", "failed")), min_pass_rate=0.5).passed is True
    )


def test_errored_case_fails_the_gate_by_default():
    gate = evaluate_gate(_report(("a", "errored")), min_pass_rate=0.0)
    assert gate.passed is False
    assert any("errored" in r for r in gate.reasons)


def test_fail_on_error_can_be_disabled():
    assert (
        evaluate_gate(_report(("a", "errored")), min_pass_rate=0.0, fail_on_error=False).passed
        is True
    )


def test_per_skill_threshold_fails_only_the_offending_skill():
    gate = evaluate_gate(
        _report(("a", "passed"), ("b", "failed")),
        min_pass_rate=0.0,
        per_skill_min={"b": 1.0},
    )
    assert gate.passed is False
    assert any("b" in r for r in gate.reasons)


def test_empty_report_passes_and_is_not_an_error():
    gate = evaluate_gate(RunReport())
    assert gate.passed is True
    assert gate.exit_code == EXIT_OK
