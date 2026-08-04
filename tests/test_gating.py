from skill_eval.comparison import ArmStats, CaseStats, Delta
from skill_eval.gating import EXIT_FAILED, EXIT_OK, evaluate_gate
from skill_eval.models import BaselineNote, CaseOutcome, RunReport


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


def _delta(pass_rate_delta: float, *, comparable: bool = True) -> Delta:
    return Delta(
        baseline_kind="none",
        pass_rate_delta=pass_rate_delta,
        cases=[
            CaseStats(
                skill_name="pdf",
                case_name="c",
                runner="fake",
                candidate=ArmStats(runs=1, passed=1, pass_rate=1.0),
                baseline=ArmStats(runs=1),
                comparable=comparable,
                exclusion_reason="" if comparable else "no baseline run",
            )
        ],
    )


def test_a_delta_at_or_above_the_bar_passes():
    gate = evaluate_gate(_report(("pdf", "passed")), min_delta=0.2, delta=_delta(0.2))
    assert gate.passed is True
    assert gate.exit_code == EXIT_OK


def test_a_delta_below_the_bar_fails():
    gate = evaluate_gate(_report(("pdf", "passed")), min_delta=0.2, delta=_delta(0.05))
    assert gate.passed is False
    assert gate.exit_code == EXIT_FAILED
    assert any("delta" in reason for reason in gate.reasons)


def test_a_negative_delta_fails_a_must_not_regress_bar():
    gate = evaluate_gate(_report(("pdf", "passed")), min_delta=0.0, delta=_delta(-0.25))
    assert gate.passed is False


def test_gating_on_a_delta_with_nothing_comparable_fails():
    # A check that verified nothing must never report a pass.
    gate = evaluate_gate(
        _report(("pdf", "passed")), min_delta=0.0, delta=_delta(0.0, comparable=False)
    )
    assert gate.passed is False
    assert any("comparable" in reason for reason in gate.reasons)


def test_gating_on_a_delta_with_no_delta_at_all_fails():
    gate = evaluate_gate(_report(("pdf", "passed")), min_delta=0.0, delta=None)
    assert gate.passed is False


def test_an_unresolved_baseline_fails_a_delta_gate():
    report = RunReport(
        outcomes=[CaseOutcome(skill_name="pdf", case_name="c", runner="fake", status="passed")],
        baseline_kind="previous",
        baseline_notes=[BaselineNote(skill_name="pdf", kind="unavailable", reason="no repo")],
    )
    gate = evaluate_gate(report, min_delta=0.0, delta=_delta(0.5))
    assert gate.passed is False
    assert any("no repo" in reason for reason in gate.reasons)


def test_a_deliberately_skipped_baseline_is_not_a_gate_reason():
    # Nothing went wrong: `mode: offered` has nothing to offer under
    # `--baseline none`. It still excludes the case from the delta.
    report = RunReport(
        outcomes=[CaseOutcome(skill_name="pdf", case_name="c", runner="fake", status="passed")],
        baseline_kind="none",
        baseline_notes=[
            BaselineNote(skill_name="pdf", case_name="c", kind="skipped", reason="offered")
        ],
    )
    gate = evaluate_gate(report, min_delta=0.0, delta=_delta(0.5))
    assert gate.passed is True


def test_without_min_delta_the_delta_is_reported_but_not_gated():
    gate = evaluate_gate(_report(("pdf", "passed")), delta=_delta(-0.9))
    assert gate.passed is True
