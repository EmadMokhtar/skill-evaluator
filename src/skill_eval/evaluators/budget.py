"""Scoring efficiency: a skill that works but costs too much has a real problem."""

from __future__ import annotations

from skill_eval.models import BudgetSpec, EvalCase, EvalScore, RunResult


def _check(spec: BudgetSpec, result: RunResult) -> tuple[int, list[str]]:
    """Return (number of limits declared, one description per limit exceeded)."""
    limits: list[tuple[bool, str]] = []

    if spec.max_tokens is not None:
        limits.append(
            (
                result.tokens > spec.max_tokens,
                f"used {result.tokens} tokens, limit is {spec.max_tokens}",
            )
        )
    if spec.max_cost_usd is not None:
        limits.append(
            (
                result.cost_usd > spec.max_cost_usd,
                f"cost ${result.cost_usd:.6f}, limit is ${spec.max_cost_usd:.6f}",
            )
        )
    if spec.max_latency_ms is not None:
        limits.append(
            (
                result.latency_ms > spec.max_latency_ms,
                f"took {result.latency_ms}ms, limit is {spec.max_latency_ms}ms",
            )
        )

    return len(limits), [detail for exceeded, detail in limits if exceeded]


class BudgetEvaluator:
    """Every declared limit must hold; the score is the fraction that held."""

    name = "budget"

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        spec = case.budget
        total, failures = _check(spec, result) if spec is not None else (0, [])
        if spec is None or total == 0:
            return EvalScore(evaluator=self.name, passed=True, score=1.0, detail="no budget checks")
        detail = "within budget" if not failures else "; ".join(failures)
        return EvalScore(
            evaluator=self.name,
            passed=not failures,
            score=(total - len(failures)) / total,
            detail=detail,
        )
