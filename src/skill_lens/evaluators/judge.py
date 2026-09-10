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
# irrelevant text grades worse, not better. The bound is on that content: the
# small, fixed sentinel strings below (rendered in place of content that was
# never read, or omitted once the budget runs out) are harness-authored
# status labels, not model-supplied content, and their own tiny, constant
# cost is not what this budget exists to police -- see `_artifacts`.
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
        # A block is only started when its truncation could still be
        # announced. At `remaining <= 0` alone, a run near total exhaustion
        # (say remaining == 3) would still call `_truncate` with that tiny
        # budget: the marker text itself cannot fit, and `_truncate`'s hard
        # cut shreds it down to something like "\n.." -- no "truncated", no
        # byte count, so a rubric fails on evidence that was cut with
        # nothing saying so. Skipping straight to BUDGET_EXHAUSTED once
        # `remaining` can no longer hold the marker keeps every truncation
        # that *does* happen visibly marked.
        if remaining < _TRUNCATION_MARKER_RESERVE:
            # BUDGET_EXHAUSTED is rendered whole, not sliced to fit whatever
            # sliver of `remaining` triggered this branch (which can be as
            # small as 0, the common case once several full-budget files
            # have landed exactly on the cap). A notice fragment nobody can
            # read ("(om") would defeat the entire point of having a
            # constant, recognisable sentinel here -- callers and tests alike
            # match on the exact string. Its own fixed cost (well under
            # `_TRUNCATION_MARKER_RESERVE`) is the one bounded, known
            # exception to "never exceed the total": unlike attacker-supplied
            # file content, it cannot grow, so it can never turn into the
            # kind of unbounded-cost-and-accuracy hazard this budget exists
            # to prevent (see the module docstring above).
            artifacts[name] = BUDGET_EXHAUSTED
            remaining -= len(BUDGET_EXHAUSTED.encode("utf-8"))
            continue
        try:
            content = workspace.read(name)
        except UnicodeDecodeError:
            artifacts[name] = NOT_TEXT
            remaining -= len(NOT_TEXT.encode("utf-8"))
            continue
        except (PathRefused, OSError, ValueError):
            # ValueError alongside PathRefused/OSError: a name carrying an
            # unpaired UTF-16 surrogate raises UnicodeEncodeError on the way
            # to the filesystem, which is a ValueError, not an OSError.
            # create_workspace catches it for the same reason.
            artifacts[name] = NOT_PRODUCED
            remaining -= len(NOT_PRODUCED.encode("utf-8"))
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
