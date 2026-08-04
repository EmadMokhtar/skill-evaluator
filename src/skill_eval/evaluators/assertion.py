"""Deterministic, rule-based scoring of a run's final output."""

from __future__ import annotations

import re
from collections.abc import Callable

from skill_eval.models import AssertionSpec, CheckResult, EvalCase, EvalScore, RunResult


class UnknownAssertionKind(Exception):
    """Raised when an eval file uses an assertion kind we do not support."""


class InvalidAssertionValue(Exception):
    """Raised when an assertion's value is malformed (e.g. an invalid regex)."""


# The single source of truth for supported assertion kinds. `_check` dispatches
# from this mapping and tests/test_docs.py enumerates it, so a kind cannot be
# added in one place and forgotten in the other.
_CHECKS: dict[str, Callable[[str, str], bool]] = {
    "contains": lambda value, output: value in output,
    "not_contains": lambda value, output: value not in output,
    "regex": lambda value, output: re.search(value, output) is not None,
    "equals": lambda value, output: output.strip() == value,
}

ASSERTION_KINDS: tuple[str, ...] = tuple(_CHECKS)


def _check(spec: AssertionSpec, output: str) -> bool:
    try:
        check = _CHECKS[spec.kind]
    except KeyError:
        raise UnknownAssertionKind(f"unknown assertion kind: {spec.kind!r}") from None
    try:
        return check(spec.value, output)
    except re.error as exc:
        raise InvalidAssertionValue(f"invalid regex pattern {spec.value!r}: {exc}") from exc


class AssertionEvaluator:
    """Every assertion must hold; the score is the fraction that held.

    Each assertion also comes back as its own `CheckResult`. Ids are positional
    and derived from the *case*, never from the result, so the same ids appear
    in both arms of a comparative run and can be paired -- which is what makes
    "this assertion passes with or without the skill" detectable.
    """

    name = "assertion"

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        if not case.assertions:
            return EvalScore(evaluator=self.name, passed=True, score=1.0, detail="no assertions")
        checks: list[CheckResult] = []
        failures: list[str] = []
        for index, spec in enumerate(case.assertions):
            held = _check(spec, result.output)
            description = f"{spec.kind}({spec.value!r})"
            if not held:
                failures.append(description)
            checks.append(
                CheckResult(
                    id=f"{spec.kind}[{index}]",
                    passed=held,
                    evidence=f"{description} {'held' if held else 'did not hold'}",
                )
            )
        passed_count = len(case.assertions) - len(failures)
        detail = "all assertions held" if not failures else "failed: " + ", ".join(failures)
        return EvalScore(
            evaluator=self.name,
            passed=not failures,
            score=passed_count / len(case.assertions),
            detail=detail,
            checks=checks,
        )
