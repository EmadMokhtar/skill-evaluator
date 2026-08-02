"""The Runner protocol — the seam every agent framework plugs into."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from skill_eval.models import EvalCase, RunResult, Skill


@runtime_checkable
class Runner(Protocol):
    """Runs a case against a skill and reports what happened."""

    name: str

    def run(self, skill: Skill, case: EvalCase) -> RunResult:
        """Execute `case` with `skill` loaded, returning a RunResult.

        Takes the whole case, not just its task string, because a runner also
        builds the environment the case declares (its mock tools).

        Implementations must not raise for provider failures; they set
        RunResult.error instead so the orchestrator can mark the case errored.
        """
        ...
