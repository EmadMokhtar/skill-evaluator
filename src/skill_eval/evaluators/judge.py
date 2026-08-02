"""Scoring what only a model can score: was the answer actually any good?

This module holds rubric logic and nothing else. The `Judge` arrives by
constructor injection, so no agent framework enters `evaluators/`.
"""

from __future__ import annotations

from skill_eval.judges.base import Judge
from skill_eval.models import (
    CheckResult,
    EvalCase,
    EvalScore,
    JudgeRequest,
    RubricCheck,
    RunResult,
)

NO_EVIDENCE = "recorded as failed: passed with no evidence"


def build_request(case: EvalCase, result: RunResult) -> JudgeRequest:
    """Turn a case's judge block into a request, numbering the rubric r1..rN.

    Ids are positional so authors never have to invent them, and each verdict
    still maps back to the check it graded by id rather than by the order the
    model happened to emit them in.
    """
    spec = case.judge
    if spec is None:
        return JudgeRequest(task=case.task, output=result.output)
    return JudgeRequest(
        task=case.task,
        output=result.output,
        expected=spec.expected,
        checks=[
            RubricCheck(id=f"r{index}", text=text)
            for index, text in enumerate(spec.rubric, start=1)
        ],
    )


def _settle(check: CheckResult) -> CheckResult:
    """A pass with no evidence is recorded as a failure."""
    if check.passed and not check.evidence.strip():
        return CheckResult(id=check.id, passed=False, evidence=NO_EVIDENCE)
    return check


class JudgeEvaluator:
    """Every rubric check must hold; the score is the fraction that held.

    skill-eval derives `passed` and `score` from the per-check verdicts. The
    judge is never asked for a blended number, because an unsupported PASS
    hidden inside one is the failure mode this evaluator exists to catch.
    """

    name = "judge"

    def __init__(self, judge: Judge) -> None:
        self._judge = judge

    def _errored(self, detail: str, cost_usd: float = 0.0) -> EvalScore:
        return EvalScore(
            evaluator=self.name,
            passed=False,
            errored=True,
            score=0.0,
            detail=detail,
            cost_usd=cost_usd,
        )

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        if case.judge is None:
            return EvalScore(evaluator=self.name, passed=True, score=1.0, detail="no judge checks")

        request = build_request(case, result)
        if not request.checks:
            return self._errored("a judge block was declared with an empty rubric")

        verdict = self._judge.judge(request)
        if verdict.error is not None:
            return self._errored(f"judge failed: {verdict.error}", verdict.cost_usd)

        wanted = [check.id for check in request.checks]
        got = [check.id for check in verdict.checks]
        if sorted(got) != sorted(wanted):
            return self._errored(
                f"judge returned verdicts for {got or 'nothing'}, expected exactly {wanted}",
                verdict.cost_usd,
            )

        by_id = {check.id: check for check in verdict.checks}
        checks = [_settle(by_id[check_id]) for check_id in wanted]
        held = [check for check in checks if check.passed]
        detail = (
            f"all {len(checks)} rubric checks held"
            if len(held) == len(checks)
            else f"{len(held)} of {len(checks)} rubric checks held"
        )
        return EvalScore(
            evaluator=self.name,
            passed=len(held) == len(checks),
            score=len(held) / len(checks),
            detail=detail,
            checks=checks,
            cost_usd=verdict.cost_usd,
        )
