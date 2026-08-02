"""Scoring efficiency: a skill that works but costs too much has a real problem."""

from __future__ import annotations

from skill_eval.models import BudgetSpec, EvalCase, EvalScore, RunResult


def _check(spec: BudgetSpec, result: RunResult) -> tuple[int, list[tuple[bool, str]], list[str]]:
    """Return (limits declared, (exceeded, detail) per limit actually evaluated, skip notes).

    The cost limit is declared but not evaluated when `result.cost_note` is
    non-empty: `calculate_cost` degrades to 0.0 for an unpriced model, and
    `0.0 > max_cost_usd` is always False, so evaluating it anyway would report
    "within budget" for a limit nobody actually checked.
    """
    declared = 0
    limits: list[tuple[bool, str]] = []
    skips: list[str] = []

    if spec.max_tokens is not None:
        declared += 1
        limits.append(
            (
                result.tokens > spec.max_tokens,
                f"used {result.tokens} tokens, limit is {spec.max_tokens}",
            )
        )
    if spec.max_cost_usd is not None:
        declared += 1
        if result.cost_note:
            skips.append(f"cost budget not evaluated: {result.cost_note}")
        else:
            limits.append(
                (
                    result.cost_usd > spec.max_cost_usd,
                    f"cost ${result.cost_usd:.6f}, limit is ${spec.max_cost_usd:.6f}",
                )
            )
    if spec.max_latency_ms is not None:
        declared += 1
        limits.append(
            (
                result.latency_ms > spec.max_latency_ms,
                f"took {result.latency_ms}ms, limit is {spec.max_latency_ms}ms",
            )
        )

    return declared, limits, skips


class BudgetEvaluator:
    """Every limit that was actually evaluated must hold; the score is the fraction
    of *evaluated* limits that held. A limit whose cost could not be priced is
    skipped rather than counted as passed, so an unpriced model cannot earn a
    vacuous "within budget" verdict.
    """

    name = "budget"

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        spec = case.budget
        if spec is None:
            return EvalScore(evaluator=self.name, passed=True, score=1.0, detail="no budget checks")

        declared, limits, skips = _check(spec, result)
        if declared == 0:
            return EvalScore(evaluator=self.name, passed=True, score=1.0, detail="no budget checks")

        evaluated = len(limits)
        if evaluated == 0:
            # Every declared limit was skipped (an unpriced cost limit was the
            # only one declared). Nothing was actually verified, so this must
            # not silently score 1.0 as though the limit held.
            return EvalScore(evaluator=self.name, passed=False, score=0.0, detail="; ".join(skips))

        failures = [detail for exceeded, detail in limits if exceeded]
        detail_parts = failures if failures else ["within budget"]
        detail_parts = [*detail_parts, *skips]
        return EvalScore(
            evaluator=self.name,
            passed=not failures,
            score=(evaluated - len(failures)) / evaluated,
            detail="; ".join(detail_parts),
        )
