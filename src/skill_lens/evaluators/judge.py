"""Scoring what only a model can score: was the answer actually any good?

This module holds rubric logic and nothing else. The `Judge` arrives by
constructor injection, so no agent framework enters `evaluators/`.
"""

from __future__ import annotations

from skill_lens.judges.base import Judge
from skill_lens.models import (
    CheckResult,
    EvalCase,
    EvalScore,
    JudgeRequest,
    JudgeSpec,
    RubricCheck,
    RunResult,
)
from skill_lens.workspace import PathRefused, Workspace

NO_EVIDENCE = "recorded as failed: passed with no evidence"

# Per file and across all files. Unbounded artifact content in a judge prompt
# is both a cost hazard and an accuracy one: a grader handed a large volume of
# irrelevant text grades worse, not better.
MAX_ARTIFACT_BYTES = 20_000
MAX_ARTIFACTS_TOTAL_BYTES = 60_000

# Absences are rendered, never raised. Both are facts about the skill, not
# about the harness, so the rubric fails honestly instead of the case erroring.
NOT_PRODUCED = "(not produced)"
NOT_TEXT = "(not valid UTF-8 text)"
BUDGET_EXHAUSTED = "(omitted, artifact budget exhausted)"

# Room reserved out of `budget`, not added on top of it, for the marker
# `_truncate` appends. 64 bytes comfortably fits the marker's fixed text plus
# a comma-grouped byte count even into the billions; `_artifacts` relies on
# `_truncate`'s *output* never exceeding the budget it was given, or the
# running total it tracks across artifacts could creep past
# `MAX_ARTIFACTS_TOTAL_BYTES`.
_TRUNCATION_MARKER_RESERVE = 64


def _truncate(text: str, budget: int) -> str:
    """Cut to `budget` bytes, marking the cut visibly.

    Silent truncation would let a judge fail a check on evidence that was cut,
    with nothing in the prompt saying so. `errors="ignore"` drops a partial
    multi-byte character at the boundary rather than raising. The marker's own
    bytes are reserved out of `budget` up front -- not appended after cutting
    to it. That alone is enough whenever `budget` comfortably exceeds the
    reserve, but `_artifacts` can call this with a `budget` smaller than
    `_TRUNCATION_MARKER_RESERVE` (the running total near its own exhaustion),
    where the reserved slice is negative-clamped to empty and the marker text
    alone could still outgrow `budget`. The final hard cut is the actual
    guarantee: the return value's encoded length never exceeds `budget`,
    which is what keeps `_artifacts`'s running total inside
    `MAX_ARTIFACTS_TOTAL_BYTES` for any combination of files.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= budget:
        return text
    content_budget = max(budget - _TRUNCATION_MARKER_RESERVE, 0)
    kept = encoded[:content_budget].decode("utf-8", errors="ignore")
    omitted = len(encoded) - len(kept.encode("utf-8"))
    rendered = f"{kept}\n... [truncated, {omitted:,} bytes omitted]"
    rendered_encoded = rendered.encode("utf-8")
    if len(rendered_encoded) <= budget:
        return rendered
    return rendered_encoded[: max(budget, 0)].decode("utf-8", errors="ignore")


def _artifacts(spec: JudgeSpec, result: RunResult) -> dict[str, str]:
    """The named files, in the author's order, deduplicated and capped."""
    names = list(dict.fromkeys(spec.artifacts))
    if not names:
        return {}
    if result.workspace is None:
        return {name: NOT_PRODUCED for name in names}
    workspace = Workspace(root=result.workspace)
    remaining = MAX_ARTIFACTS_TOTAL_BYTES
    artifacts: dict[str, str] = {}
    for name in names:
        if remaining <= 0:
            artifacts[name] = BUDGET_EXHAUSTED
            continue
        try:
            content = workspace.read(name)
        except UnicodeDecodeError:
            artifacts[name] = NOT_TEXT
            continue
        except (PathRefused, OSError):
            artifacts[name] = NOT_PRODUCED
            continue
        rendered = _truncate(content, min(MAX_ARTIFACT_BYTES, remaining))
        artifacts[name] = rendered
        remaining -= len(rendered.encode("utf-8"))
    return artifacts


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
        artifacts=_artifacts(spec, result),
    )


def _settle(check: CheckResult) -> CheckResult:
    """A pass with no evidence is recorded as a failure."""
    if check.passed and not check.evidence.strip():
        return CheckResult(id=check.id, passed=False, evidence=NO_EVIDENCE)
    return check


class JudgeEvaluator:
    """Every rubric check must hold; the score is the fraction that held.

    skill-lens derives `passed` and `score` from the per-check verdicts. The
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
            # Also an authoring error in cases/loader.py (exit 2) for cases
            # loaded from YAML, so this condition is unreachable via that
            # path. This branch is a deliberate second guard for an EvalCase
            # built programmatically, bypassing the loader's validation.
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
