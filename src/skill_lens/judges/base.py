"""The Judge protocol — the seam every LLM-as-judge implementation plugs into."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from skill_lens.models import JudgeRequest, JudgeVerdict


@runtime_checkable
class Judge(Protocol):
    """Grades one output against a list of checks.

    A judge may also define an optional `preflight()` and an optional
    `unpriced() -> str`, the second as `Runner` describes it: why its spend
    can never be priced, asked only under a spend ceiling.
    """

    name: str

    def judge(self, request: JudgeRequest) -> JudgeVerdict:
        """Return one verdict per check in `request`, with evidence for each.

        Implementations must not raise for provider failures; they set
        `JudgeVerdict.error` instead, so `JudgeEvaluator` can report an infra
        problem (errored) rather than a low score (failed).

        Implementations must not decide the overall verdict or a blended score:
        skill-lens derives both from the per-check results.
        """
        ...
