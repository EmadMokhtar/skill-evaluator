"""Scoring efficiency: a skill that works but costs too much has a real problem."""

from __future__ import annotations

from skill_eval.models import BudgetSpec, CheckResult, EvalCase, EvalScore, RunResult


def _checks(spec: BudgetSpec, result: RunResult) -> tuple[list[CheckResult], int]:
    """One CheckResult per declared limit, plus how many were actually evaluated.

    A cost limit is declared but not evaluated when `result.cost_note` is
    non-empty: `calculate_cost` degrades to 0.0 for an unpriced model, and
    `0.0 > max_cost_usd` is always False, so evaluating it anyway would report
    "within budget" for a limit nobody checked. It becomes a *failing* check
    carrying the reason -- never a passing one.
    """
    checks: list[CheckResult] = []
    evaluated = 0

    if spec.max_tokens is not None:
        evaluated += 1
        held = result.tokens <= spec.max_tokens
        checks.append(
            CheckResult(
                id="max_tokens",
                passed=held,
                evidence=f"used {result.tokens} tokens, limit is {spec.max_tokens}",
            )
        )
    if spec.max_cost_usd is not None:
        if result.cost_note:
            checks.append(
                CheckResult(
                    id="max_cost_usd",
                    passed=False,
                    evidence=f"cost budget not evaluated: {result.cost_note}",
                )
            )
        else:
            evaluated += 1
            held = result.cost_usd <= spec.max_cost_usd
            checks.append(
                CheckResult(
                    id="max_cost_usd",
                    passed=held,
                    evidence=(f"cost ${result.cost_usd:.6f}, limit is ${spec.max_cost_usd:.6f}"),
                )
            )
    if spec.max_latency_ms is not None:
        evaluated += 1
        held = result.latency_ms <= spec.max_latency_ms
        checks.append(
            CheckResult(
                id="max_latency_ms",
                passed=held,
                evidence=f"took {result.latency_ms}ms, limit is {spec.max_latency_ms}ms",
            )
        )

    return checks, evaluated


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

        checks, evaluated = _checks(spec, result)
        if not checks:
            return EvalScore(evaluator=self.name, passed=True, score=1.0, detail="no budget checks")

        failures = [c.evidence for c in checks if not c.passed]
        if evaluated == 0:
            # Every declared limit was skipped (an unpriced cost limit was the
            # only one declared). Nothing was actually verified, so this must
            # not silently score 1.0 as though the limit held.
            return EvalScore(
                evaluator=self.name,
                passed=False,
                score=0.0,
                detail="; ".join(failures),
                checks=checks,
            )

        # `passed` keys on *all* failures -- a skipped cost limit still fails
        # the case -- while `score` counts only what was actually evaluated, so
        # an unpriced limit neither inflates nor deflates the fraction.
        skipped_ids = {c.id for c in checks if c.id == "max_cost_usd" and result.cost_note}
        real_failures = [c for c in checks if not c.passed and c.id not in skipped_ids]
        return EvalScore(
            evaluator=self.name,
            passed=not failures,
            score=(evaluated - len(real_failures)) / evaluated,
            detail="; ".join(failures) if failures else "within budget",
            checks=checks,
        )
