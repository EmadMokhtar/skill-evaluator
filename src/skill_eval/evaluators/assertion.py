"""Deterministic, rule-based scoring of a run's final output."""

from __future__ import annotations

import re

from skill_eval.models import AssertionSpec, EvalCase, EvalScore, RunResult


class UnknownAssertionKind(Exception):
    """Raised when an eval file uses an assertion kind we do not support."""


class InvalidAssertionValue(Exception):
    """Raised when an assertion's value is malformed (e.g. an invalid regex)."""


def _check(spec: AssertionSpec, output: str) -> bool:
    if spec.kind == "contains":
        return spec.value in output
    if spec.kind == "not_contains":
        return spec.value not in output
    if spec.kind == "regex":
        try:
            return re.search(spec.value, output) is not None
        except re.error as exc:
            raise InvalidAssertionValue(f"invalid regex pattern {spec.value!r}: {exc}") from exc
    if spec.kind == "equals":
        return output.strip() == spec.value
    raise UnknownAssertionKind(f"unknown assertion kind: {spec.kind!r}")


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
