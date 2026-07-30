"""The Runner protocol — the seam every agent framework plugs into."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from skill_eval.models import RunResult, Skill


@runtime_checkable
class Runner(Protocol):
    """Runs a task against a skill and reports what happened."""

    name: str

    def run(self, skill: Skill, task: str) -> RunResult:
        """Execute `task` with `skill` loaded, returning a RunResult.

        Implementations must not raise for provider failures; they set
        RunResult.error instead so the orchestrator can mark the case errored.
        """
        ...
