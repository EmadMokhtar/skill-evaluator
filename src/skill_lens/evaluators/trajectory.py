"""Scoring the tool-call trajectory: what the agent did to reach its answer."""

from __future__ import annotations

import json
from typing import Any

from skill_lens.models import (
    CallArgsSpec,
    CheckResult,
    EvalCase,
    EvalScore,
    RunResult,
    ToolCall,
    TrajectorySpec,
)

# How many characters of rendered arguments one evidence line shows. A
# `write_file` call can carry a whole document; the cut is announced with a
# count rather than made silently, so a clipped line never implies the
# arguments it shows were all of them. The failure-context excerpt every
# reporter prints for a non-passing case carries the full calls.
_ARGUMENTS_LIMIT = 800


def _is_subsequence(required: list[str], actual: list[str]) -> bool:
    """True when `required` appears in `actual` in order, gaps allowed."""
    remaining = iter(actual)
    return all(name in remaining for name in required)


def _same_scalar(expected: Any, actual: Any) -> bool:
    """Equality without Python's bool-is-an-int rule.

    `True == 1` in Python, so an author who wrote `limit: 1` would otherwise
    pass on a call that sent `true`. A bool only ever equals a bool. Every
    other scalar compares as JSON would: `"1"` never equals `1`, and `1`
    equals `1.0`.
    """
    if isinstance(expected, bool) or isinstance(actual, bool):
        return isinstance(expected, bool) and isinstance(actual, bool) and expected == actual
    return expected == actual


def _matches(expected: Any, actual: Any, *, exact: bool) -> bool:
    """Structural match of `expected` against a call's recorded arguments.

    A mapping matches when every key it names is present with a matching
    value; under `exact` it must also name every key the call carried. A
    list matches element by element at the same length -- containment is
    not subsetting, so `[bug]` does not match `[bug, urgent]`. Anything else
    is a scalar and must be equal.
    """
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        if exact and set(expected) != set(actual):
            return False
        return all(
            key in actual and _matches(value, actual[key], exact=exact)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(_matches(e, a, exact=exact) for e, a in zip(expected, actual, strict=True))
        )
    if isinstance(actual, (dict, list)):
        return False
    return _same_scalar(expected, actual)


def _render(value: Any) -> str:
    """Arguments as one line of JSON, keys sorted so the same dict always reads the same."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _cut(text: str) -> str:
    if len(text) <= _ARGUMENTS_LIMIT:
        return text
    return f"{text[:_ARGUMENTS_LIMIT]}... (+{len(text) - _ARGUMENTS_LIMIT} more characters)"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _call_args_check(index: int, entry: CallArgsSpec, tool_calls: list[ToolCall]) -> CheckResult:
    """One verdict on the arguments `entry.tool` was called with.

    By default at least one call must match; under `every` all of them must.
    A tool that was never called fails either way: "every call matched" over
    zero calls would be a vacuous pass.
    """
    check_id = f"call_args[{index}]"
    tool = entry.tool
    if entry.contains is not None:
        wanted, exact = f"contains {_render(entry.contains)}", False
        expected: dict[str, Any] = entry.contains
    else:
        # The model guarantees one of the two is set.
        wanted, exact = f"equals {_render(entry.equals)}", True
        expected = entry.equals or {}

    calls = [call for call in tool_calls if call.name == tool]
    if not calls:
        return CheckResult(id=check_id, passed=False, evidence=f"{tool} was never called")
    total = len(calls)
    verdicts = [_matches(expected, call.arguments, exact=exact) for call in calls]

    if entry.every:
        for position, (call, held) in enumerate(zip(calls, verdicts, strict=True), start=1):
            if not held:
                return CheckResult(
                    id=check_id,
                    passed=False,
                    evidence=(
                        f"call {position} of {total} to {tool} did not match {wanted}: "
                        f"got {_cut(_render(call.arguments))}"
                    ),
                )
        return CheckResult(
            id=check_id,
            passed=True,
            evidence=f"all {_plural(total, 'call')} to {tool} matched {wanted}",
        )

    for position, held in enumerate(verdicts, start=1):
        if held:
            return CheckResult(
                id=check_id,
                passed=True,
                evidence=f"call {position} of {total} to {tool} matched {wanted}",
            )
    seen = _cut(_render([call.arguments for call in calls]))
    return CheckResult(
        id=check_id,
        passed=False,
        evidence=f"no call to {tool} matched {wanted}; arguments seen: {seen}",
    )


def _checks(
    spec: TrajectorySpec, tool_calls: list[ToolCall], triggered: bool | None
) -> list[CheckResult]:
    """One CheckResult per declared check, in a stable order.

    Ids come from the spec, never from the result, so the same ids appear in
    both arms of a comparative run.
    """
    called = [call.name for call in tool_calls]
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

    for index, entry in enumerate(spec.call_args):
        checks.append(_call_args_check(index, entry, tool_calls))

    return checks


class TrajectoryEvaluator:
    """Every declared check must hold; the score is the fraction that held.

    `called` / `forbidden` / `order` / `max_calls` read the sequence of tool
    names; `call_args` reads the arguments a named tool was called with.
    """

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
        checks = _checks(spec, result.tool_calls, result.skill_triggered)
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
