"""A deterministic, offline runner used to test the whole pipeline."""

from __future__ import annotations

from skill_eval.models import RunResult, Skill


class FakeRunner:
    """Returns scripted RunResults. Never touches the network."""

    name = "fake"

    def __init__(
        self,
        responses: dict[str, RunResult] | None = None,
        default: RunResult | None = None,
    ) -> None:
        self._responses = responses or {}
        self._default = default

    def run(self, skill: Skill, task: str) -> RunResult:
        if task in self._responses:
            return self._responses[task]
        if self._default is not None:
            return self._default
        return RunResult(output=f"[fake] {skill.name} handled: {task}")
