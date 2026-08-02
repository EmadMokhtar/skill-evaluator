"""A deterministic, offline judge used to test the whole pipeline."""

from __future__ import annotations

from skill_eval.models import JudgeRequest, JudgeVerdict


class FakeJudge:
    """Returns scripted verdicts. Never touches the network.

    Unscripted, it refuses to judge rather than inventing a pass. That is what
    makes `judge = "fake"` safe as the built-in default: a case declaring a
    rubric with no real judge configured comes back **errored**, never green.
    Same rule as an unpriceable cost limit in `BudgetEvaluator` — nothing was
    verified, so nothing may be reported as verified.
    """

    name = "fake"

    NOT_CONFIGURED = (
        "no judge is configured, so this case's rubric was never checked. "
        'Set judge = "pydantic-ai" in skill-eval.toml to grade it for real.'
    )

    def __init__(
        self,
        verdicts: dict[str, JudgeVerdict] | None = None,
        default: JudgeVerdict | None = None,
    ) -> None:
        self._verdicts = verdicts or {}
        self._default = default

    def judge(self, request: JudgeRequest) -> JudgeVerdict:
        if request.task in self._verdicts:
            return self._verdicts[request.task].model_copy(deep=True)
        if self._default is not None:
            return self._default.model_copy(deep=True)
        return JudgeVerdict(error=self.NOT_CONFIGURED)
