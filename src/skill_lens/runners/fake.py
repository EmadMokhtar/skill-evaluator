"""A deterministic, offline runner used to test the whole pipeline."""

from __future__ import annotations

from skill_lens.models import EvalCase, RunResult, Skill
from skill_lens.workspace import Workspace


class FakeRunner:
    """Returns scripted RunResults. Never touches the network.

    `baseline_responses` lets a test script the two arms differently -- the
    only way a zero-cost test can express "this skill helps". It is consulted
    via `skill.variant` and falls back to `responses`, so existing single-arm
    scripts keep working unchanged.

    `writes` and `baseline_writes` are the same idea for artifacts: a mapping
    of task to {path: content}, written into the workspace through the real
    containment code so the offline tier exercises the same path a real run
    does. A scripted write that the workspace refuses raises rather than being
    skipped -- this is a test helper, so an escaping path is a bug in the test
    and must be loud.
    """

    name = "fake"

    def __init__(
        self,
        responses: dict[str, RunResult] | None = None,
        default: RunResult | None = None,
        baseline_responses: dict[str, RunResult] | None = None,
        writes: dict[str, dict[str, str]] | None = None,
        baseline_writes: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self._responses = responses or {}
        self._default = default
        self._baseline_responses = baseline_responses or {}
        self._writes = writes or {}
        self._baseline_writes = baseline_writes or {}

    def _scripted_writes(self, skill: Skill, case: EvalCase) -> dict[str, str]:
        if skill.variant == "baseline" and case.task in self._baseline_writes:
            return self._baseline_writes[case.task]
        return self._writes.get(case.task, {})

    def run(
        self,
        skill: Skill,
        case: EvalCase,
        workspace: Workspace | None = None,
        scripts: object = None,
    ) -> RunResult:
        """`scripts` is accepted for protocol symmetry and ignored: a scripted runner runs
        nothing."""
        if workspace is not None:
            for name, content in self._scripted_writes(skill, case).items():
                workspace.write(name, content)
        if skill.variant == "baseline" and case.task in self._baseline_responses:
            return self._baseline_responses[case.task].model_copy(deep=True)
        if case.task in self._responses:
            return self._responses[case.task].model_copy(deep=True)
        if self._default is not None:
            return self._default.model_copy(deep=True)
        return RunResult(output=f"[fake] {skill.name} handled: {case.task}")
