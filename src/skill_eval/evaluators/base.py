"""The Evaluator protocol — the seam every scoring strategy plugs into."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from skill_eval.models import EvalCase, EvalScore, RunResult


@runtime_checkable
class Evaluator(Protocol):
    """Scores a RunResult against an EvalCase."""

    name: str

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        """Return a pass/fail verdict with a numeric score and human detail."""
        ...
