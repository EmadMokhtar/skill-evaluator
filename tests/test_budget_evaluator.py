"""Efficiency is a goal in its own right, not a footnote on the report."""

from skill_eval.evaluators.budget import BudgetEvaluator
from skill_eval.models import BudgetSpec, EvalCase, RunResult

EVALUATOR = BudgetEvaluator()


def case(**budget) -> EvalCase:
    return EvalCase(name="c", task="t", budget=BudgetSpec(**budget))


def test_no_budget_block_is_a_vacuous_pass():
    score = EVALUATOR.evaluate(EvalCase(name="c", task="t"), RunResult(input_tokens=10_000))
    assert score.passed is True
    assert score.score == 1.0
    assert "no budget checks" in score.detail


def test_token_budget_counts_input_plus_output():
    result = RunResult(input_tokens=600, output_tokens=500)
    score = EVALUATOR.evaluate(case(max_tokens=1000), result)
    assert score.passed is False
    assert "1100" in score.detail


def test_token_budget_passes_at_the_limit():
    result = RunResult(input_tokens=600, output_tokens=400)
    assert EVALUATOR.evaluate(case(max_tokens=1000), result).passed is True


def test_cost_budget_fails_when_exceeded():
    score = EVALUATOR.evaluate(case(max_cost_usd=0.01), RunResult(cost_usd=0.02))
    assert score.passed is False
    assert "cost" in score.detail


def test_latency_budget_fails_when_exceeded():
    score = EVALUATOR.evaluate(case(max_latency_ms=1000), RunResult(latency_ms=2500))
    assert score.passed is False
    assert "2500" in score.detail


def test_score_is_the_fraction_of_limits_respected():
    result = RunResult(input_tokens=5000, cost_usd=0.001, latency_ms=100)
    score = EVALUATOR.evaluate(
        case(max_tokens=1000, max_cost_usd=0.01, max_latency_ms=1000), result
    )
    assert score.score == 2 / 3
    assert score.passed is False


def test_a_blown_budget_is_a_failure_not_an_error():
    # An inefficient skill is an eval signal. Only the runner blowing up is
    # infra, and that is `RunResult.error` -- never this evaluator's business.
    result = RunResult(input_tokens=10_000)
    score = EVALUATOR.evaluate(case(max_tokens=10), result)
    assert score.passed is False
    assert result.errored is False


def test_evaluator_reports_its_name():
    assert EVALUATOR.evaluate(case(max_tokens=10), RunResult()).evaluator == "budget"


def test_a_zero_limit_is_still_a_declared_limit():
    # 0 is falsy. If declaredness were decided by truthiness instead of
    # `is not None`, these limits would silently vanish and the case would pass.
    result = RunResult(input_tokens=1, cost_usd=0.01, latency_ms=1)
    score = EVALUATOR.evaluate(case(max_tokens=0, max_cost_usd=0.0, max_latency_ms=0), result)
    assert score.passed is False
    assert score.score == 0.0


def test_a_zero_limit_passes_when_nothing_was_spent():
    score = EVALUATOR.evaluate(case(max_tokens=0, max_cost_usd=0.0, max_latency_ms=0), RunResult())
    assert score.passed is True
    assert score.score == 1.0


def test_cost_budget_passes_at_the_limit():
    assert EVALUATOR.evaluate(case(max_cost_usd=0.01), RunResult(cost_usd=0.01)).passed is True


def test_latency_budget_passes_at_the_limit():
    assert EVALUATOR.evaluate(case(max_latency_ms=1000), RunResult(latency_ms=1000)).passed is True


def test_unpriced_cost_budget_is_not_reported_as_within_budget():
    # calculate_cost degrades to (0.0, "no price data for ...") for an unpriced
    # model. 0.0 > max_cost_usd is always False, so the naive check would report
    # "within budget" for a limit that was never actually verified. The evaluator
    # must not silently score this 1.0 as though the limit held.
    result = RunResult(cost_usd=0.0, cost_note="no price data for groq:llama (KeyError)")
    score = EVALUATOR.evaluate(case(max_cost_usd=0.01), result)
    assert score.passed is False
    assert score.score == 0.0
    assert "not evaluated" in score.detail
    assert "no price data" in score.detail


def test_unpriced_cost_is_excluded_from_the_denominator_when_other_limits_pass():
    # Only the token limit is actually evaluated; the skipped cost check must
    # not inflate or deflate that fraction.
    result = RunResult(input_tokens=100, cost_usd=0.0, cost_note="no price data for x (KeyError)")
    score = EVALUATOR.evaluate(case(max_tokens=1000, max_cost_usd=0.01), result)
    assert score.passed is True
    assert score.score == 1.0
    assert "not evaluated" in score.detail


def test_unpriced_cost_is_excluded_from_the_denominator_when_other_limits_fail():
    result = RunResult(input_tokens=100, cost_usd=0.0, cost_note="no price data for x (KeyError)")
    score = EVALUATOR.evaluate(case(max_tokens=10, max_cost_usd=0.01), result)
    assert score.passed is False
    assert score.score == 0.0
    assert "not evaluated" in score.detail
    assert "100" in score.detail


def test_token_and_latency_budgets_are_unaffected_by_an_unrelated_cost_note():
    # A cost_note on the result must not touch token/latency checks that have
    # nothing to do with pricing.
    result = RunResult(
        input_tokens=600, output_tokens=500, latency_ms=2500, cost_note="no price data for x"
    )
    score = EVALUATOR.evaluate(case(max_tokens=1000, max_latency_ms=1000), result)
    assert score.passed is False
    assert "1100" in score.detail
    assert "2500" in score.detail
