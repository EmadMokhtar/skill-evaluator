"""A deterministic, offline runner used to test the whole pipeline."""

from __future__ import annotations

from skill_eval.models import EvalCase, RunResult, Skill


class FakeRunner:
    """Returns scripted RunResults. Never touches the network.

    `baseline_responses` lets a test script the two arms differently -- the
    only way a zero-cost test can express "this skill helps". It is consulted
    via `skill.variant` and falls back to `responses`, so existing single-arm
    scripts keep working unchanged.
    """

    name = "fake"

    def __init__(
        self,
        responses: dict[str, RunResult] | None = None,
        default: RunResult | None = None,
        baseline_responses: dict[str, RunResult] | None = None,
    ) -> None:
        self._responses = responses or {}
        self._default = default
        self._baseline_responses = baseline_responses or {}

    def run(self, skill: Skill, case: EvalCase) -> RunResult:
        if skill.variant == "baseline" and case.task in self._baseline_responses:
            return self._baseline_responses[case.task].model_copy(deep=True)
        if case.task in self._responses:
            return self._responses[case.task].model_copy(deep=True)
        if self._default is not None:
            return self._default.model_copy(deep=True)
        return RunResult(output=f"[fake] {skill.name} handled: {case.task}")
