"""Scoring the tool-call trajectory: what the agent did to reach its answer."""

from __future__ import annotations

from skill_eval.models import CheckResult, EvalCase, EvalScore, RunResult, TrajectorySpec


def _is_subsequence(required: list[str], actual: list[str]) -> bool:
    """True when `required` appears in `actual` in order, gaps allowed."""
    remaining = iter(actual)
    return all(name in remaining for name in required)


def _checks(spec: TrajectorySpec, called: list[str], triggered: bool | None) -> list[CheckResult]:
    """One CheckResult per declared check, in a stable order.

    Ids come from the spec, never from the result, so the same ids appear in
    both arms of a comparative run.
    """
    checks: list[CheckResult] = []

    for name in spec.called:
        held = name in called
        checks.append(
            CheckResult(
                id=f"called:{name}",
                passed=held,
                evidence=f"{name} was {'called' if held else 'never called'}",
            )
        )

    for name in spec.forbidden:
        held = name not in called
        checks.append(
            CheckResult(
                id=f"forbidden:{name}",
                passed=held,
                evidence=f"forbidden tool {name} was {'not called' if held else 'called'}",
            )
        )

    if spec.order:
        held = _is_subsequence(spec.order, called)
        arrow = " -> ".join(spec.order)
        checks.append(
            CheckResult(
                id="order",
                passed=held,
                evidence=(
                    f"order {arrow} followed"
                    if held
                    else f"order {arrow} not followed, got {called}"
                ),
            )
        )

    if spec.max_calls is not None:
        held = len(called) <= spec.max_calls
        checks.append(
            CheckResult(
                id="max_calls",
                passed=held,
                evidence=f"made {len(called)} tool calls, limit is {spec.max_calls}",
            )
        )

    if spec.skill_triggered is not None:
        held = triggered == spec.skill_triggered
        checks.append(
            CheckResult(
                id="skill_triggered",
                passed=held,
                evidence=("skill was triggered" if triggered else "skill was not triggered")
                + f"; expected {spec.skill_triggered}",
            )
        )

    return checks


class TrajectoryEvaluator:
    """Every declared check must hold; the score is the fraction that held."""

    name = "trajectory"

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        spec = case.trajectory
        if spec is None:
            return EvalScore(
                evaluator=self.name, passed=True, score=1.0, detail="no trajectory checks"
            )
        if spec.skill_triggered is not None and result.skill_triggered is None:
            # The runner reported no triggering decision at all. That is an
            # infra fact about the runner, not a signal about the skill, so it
            # must not read as a skill that failed to fire -- and there is no
            # verdict to record as a check.
            return EvalScore(
                evaluator=self.name,
                passed=False,
                errored=True,
                score=0.0,
                detail=(
                    "trajectory.skill_triggered was declared but the runner reported no "
                    "triggering decision; this runner does not support 'mode: offered'"
                ),
            )
        called = [call.name for call in result.tool_calls]
        checks = _checks(spec, called, result.skill_triggered)
        if not checks:
            return EvalScore(
                evaluator=self.name, passed=True, score=1.0, detail="no trajectory checks"
            )
        failures = [c.evidence for c in checks if not c.passed]
        detail = "all trajectory checks held" if not failures else "; ".join(failures)
        return EvalScore(
            evaluator=self.name,
            passed=not failures,
            score=(len(checks) - len(failures)) / len(checks),
            detail=detail,
            checks=checks,
        )
