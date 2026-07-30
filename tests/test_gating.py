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


def test_empty_report_fails_the_gate():
    """Item 1: a run where zero cases executed must FAIL the gate.

    Previously ``if report.total and ...`` guarded the pass-rate check, so a
    run that executed nothing silently exited 0. A mistyped path, a moved
    directory, or a --tag matching nothing must not report success.
    """
    gate = evaluate_gate(RunReport())
    assert gate.passed is False
    assert gate.exit_code == EXIT_FAILED
    assert any("no eval cases ran" in r or "no cases ran" in r for r in gate.reasons)


def test_empty_report_with_no_skills_at_all_names_the_cause():
    gate = evaluate_gate(RunReport())
    assert gate.passed is False
    assert any("no skills were found" in r for r in gate.reasons)


def test_empty_report_because_all_skills_skipped_names_the_cause():
    report = RunReport(outcomes=[], skipped_skills=["pdf", "xlsx"])
    gate = evaluate_gate(report)
    assert gate.passed is False
    assert any(
        "skipped" in r and "no eval cases" in r and "pdf" in r and "xlsx" in r for r in gate.reasons
    )


def test_empty_report_because_tag_filtered_everything_names_the_cause():
    report = RunReport(outcomes=[], tag_filtered_skills=["pdf"])
    gate = evaluate_gate(report)
    assert gate.passed is False
    assert any("--tag" in r and "pdf" in r for r in gate.reasons)


def test_per_skill_min_with_no_outcomes_fails_the_gate():
    """A skill named in per_skill_min but with no outcomes should fail the gate."""
    gate = evaluate_gate(
        _report(("a", "passed")),
        min_pass_rate=0.0,
        per_skill_min={"nonexistent": 0.5},
    )
    assert gate.passed is False
    assert any("nonexistent" in r and "no results" in r for r in gate.reasons)


def test_per_skill_min_with_skipped_skill_fails_the_gate():
    """A skill named in per_skill_min that was skipped should fail with a skipped indicator."""
    report = RunReport(
        outcomes=[CaseOutcome(skill_name="a", case_name="c0", runner="fake", status="passed")],
        skipped_skills=["b"],
    )
    gate = evaluate_gate(
        report,
        min_pass_rate=0.0,
        per_skill_min={"b": 0.5},
    )
    assert gate.passed is False
    assert any("b" in r and "skipped" in r for r in gate.reasons)


def test_singular_case_errored_message():
    """A single errored case should use singular form."""
    gate = evaluate_gate(_report(("a", "errored")), fail_on_error=True, min_pass_rate=0.0)
    assert gate.passed is False
    assert any("1 case errored" in r for r in gate.reasons)
