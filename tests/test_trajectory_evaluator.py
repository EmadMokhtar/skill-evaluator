"""Scoring what the agent did, not what it said."""

from skill_eval.evaluators.trajectory import TrajectoryEvaluator
from skill_eval.models import EvalCase, RunResult, ToolCall, TrajectorySpec

EVALUATOR = TrajectoryEvaluator()


def case(**trajectory) -> EvalCase:
    return EvalCase(name="c", task="t", trajectory=TrajectorySpec(**trajectory))


def result(*names: str) -> RunResult:
    return RunResult(tool_calls=[ToolCall(name=name) for name in names])


def test_no_trajectory_block_is_a_vacuous_pass():
    score = EVALUATOR.evaluate(EvalCase(name="c", task="t"), result("anything"))
    assert score.passed is True
    assert score.score == 1.0
    assert "no trajectory checks" in score.detail


def test_called_passes_when_every_listed_tool_was_used():
    score = EVALUATOR.evaluate(case(called=["lookup_order"]), result("lookup_order"))
    assert score.passed is True


def test_called_fails_when_a_listed_tool_was_never_used():
    score = EVALUATOR.evaluate(case(called=["lookup_order"]), result("issue_refund"))
    assert score.passed is False
    assert "lookup_order" in score.detail


def test_forbidden_fails_when_a_banned_tool_was_used():
    score = EVALUATOR.evaluate(case(forbidden=["issue_refund"]), result("issue_refund"))
    assert score.passed is False
    assert "issue_refund" in score.detail


def test_forbidden_passes_when_the_banned_tool_was_avoided():
    assert EVALUATOR.evaluate(case(forbidden=["issue_refund"]), result("lookup_order")).passed


def test_order_allows_unrelated_calls_in_between():
    # "Order" means relative subsequence: a model taking a sensible extra step
    # between the two required ones has still done them in the right order.
    score = EVALUATOR.evaluate(
        case(order=["lookup_order", "issue_refund"]),
        result("lookup_order", "check_policy", "issue_refund"),
    )
    assert score.passed is True


def test_order_fails_when_the_sequence_is_inverted():
    score = EVALUATOR.evaluate(
        case(order=["lookup_order", "issue_refund"]),
        result("issue_refund", "lookup_order"),
    )
    assert score.passed is False
    assert "order" in score.detail


def test_order_fails_when_a_listed_tool_is_missing_entirely():
    score = EVALUATOR.evaluate(case(order=["lookup_order", "issue_refund"]), result("lookup_order"))
    assert score.passed is False


def test_max_calls_catches_a_loop():
    score = EVALUATOR.evaluate(case(max_calls=2), result("a", "a", "a"))
    assert score.passed is False
    assert "3" in score.detail


def test_max_calls_passes_at_the_limit():
    assert EVALUATOR.evaluate(case(max_calls=2), result("a", "a")).passed is True


def test_score_is_the_fraction_of_checks_that_held():
    # called (fails) + forbidden (holds) => 1 of 2.
    score = EVALUATOR.evaluate(
        case(called=["lookup_order"], forbidden=["issue_refund"]), result("check_policy")
    )
    assert score.score == 0.5
    assert score.passed is False


def test_evaluator_reports_its_name():
    assert EVALUATOR.evaluate(case(called=["a"]), result("a")).evaluator == "trajectory"


def triggering_case(expected: bool) -> EvalCase:
    return EvalCase(
        name="c",
        task="t",
        mode="offered",
        trajectory=TrajectorySpec(skill_triggered=expected),
    )


def test_a_skill_that_triggered_when_it_should_have_passes():
    score = TrajectoryEvaluator().evaluate(triggering_case(True), RunResult(skill_triggered=True))
    assert score.passed is True
    assert score.errored is False


def test_a_skill_that_did_not_trigger_when_it_should_have_fails():
    score = TrajectoryEvaluator().evaluate(triggering_case(True), RunResult(skill_triggered=False))
    assert score.passed is False
    assert score.errored is False
    assert "not triggered" in score.detail


def test_a_negative_control_fails_when_the_skill_fires_anyway():
    # A positives-only suite scores a skill that fires on everything at 100%.
    score = TrajectoryEvaluator().evaluate(triggering_case(False), RunResult(skill_triggered=True))
    assert score.passed is False
    assert "should not" in score.detail


def test_a_negative_control_passes_when_the_skill_stays_out_of_it():
    score = TrajectoryEvaluator().evaluate(triggering_case(False), RunResult(skill_triggered=False))
    assert score.passed is True


def test_a_runner_that_reported_no_decision_errors_rather_than_failing():
    # None is "this runner does not do offered mode", which is infra, not a
    # signal about the skill -- it must not read as a skill that misfired.
    score = TrajectoryEvaluator().evaluate(triggering_case(True), RunResult(skill_triggered=None))
    assert score.errored is True
    assert score.passed is False
    assert "does not support" in score.detail


def test_a_negative_control_on_an_unsupported_runner_errors_too():
    # A truthy guard would misreport this as a plain failure (did not trigger
    # when expected), not an infra error (runner does not support offered mode).
    score = TrajectoryEvaluator().evaluate(triggering_case(False), RunResult(skill_triggered=None))
    assert score.errored is True
    assert score.passed is False
    assert "does not support" in score.detail


def test_the_triggering_check_counts_toward_the_score_fraction():
    case = EvalCase(
        name="c",
        task="t",
        mode="offered",
        trajectory=TrajectorySpec(max_calls=5, skill_triggered=True),
    )
    score = TrajectoryEvaluator().evaluate(case, RunResult(skill_triggered=False))
    assert score.score == 0.5
