"""Deterministic, rule-based scoring of a run's final output."""

from __future__ import annotations

import re
from collections.abc import Callable

from skill_eval.models import AssertionSpec, EvalCase, EvalScore, RunResult


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
    """Every assertion must hold; the score is the fraction that held."""

    name = "assertion"

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        if not case.assertions:
            return EvalScore(evaluator=self.name, passed=True, score=1.0, detail="no assertions")
        failures: list[str] = []
        for spec in case.assertions:
            if not _check(spec, result.output):
                failures.append(f"{spec.kind}({spec.value!r})")
        passed_count = len(case.assertions) - len(failures)
        detail = "all assertions held" if not failures else "failed: " + ", ".join(failures)
        return EvalScore(
            evaluator=self.name,
            passed=not failures,
            score=passed_count / len(case.assertions),
            detail=detail,
        )
