"""Scoring the tool-call trajectory: what the agent did to reach its answer."""

from __future__ import annotations

from skill_eval.models import EvalCase, EvalScore, RunResult, TrajectorySpec


def _is_subsequence(required: list[str], actual: list[str]) -> bool:
    """True when `required` appears in `actual` in order, gaps allowed."""
    remaining = iter(actual)
    return all(name in remaining for name in required)


def _check(spec: TrajectorySpec, called: list[str]) -> list[str]:
    """Return one failure description per check that did not hold."""
    failures: list[str] = []

    missing = [name for name in spec.called if name not in called]
    if missing:
        failures.append(f"never called: {', '.join(missing)}")

    used = [name for name in spec.forbidden if name in called]
    if used:
        failures.append(f"called forbidden tool: {', '.join(used)}")

    if spec.order and not _is_subsequence(spec.order, called):
        failures.append(f"order {' -> '.join(spec.order)} not followed, got {called}")

    if spec.max_calls is not None and len(called) > spec.max_calls:
        failures.append(f"made {len(called)} tool calls, limit is {spec.max_calls}")

    return failures


def _total_checks(spec: TrajectorySpec) -> int:
    return sum(
        [
            bool(spec.called),
            bool(spec.forbidden),
            bool(spec.order),
            spec.max_calls is not None,
        ]
    )


class TrajectoryEvaluator:
    """Every declared check must hold; the score is the fraction that held."""

    name = "trajectory"

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        spec = case.trajectory
        total = _total_checks(spec) if spec is not None else 0
        if spec is None or total == 0:
            return EvalScore(
                evaluator=self.name, passed=True, score=1.0, detail="no trajectory checks"
            )
        called = [call.name for call in result.tool_calls]
        failures = _check(spec, called)
        detail = "all trajectory checks held" if not failures else "; ".join(failures)
        return EvalScore(
            evaluator=self.name,
            passed=not failures,
            score=(total - len(failures)) / total,
            detail=detail,
        )
