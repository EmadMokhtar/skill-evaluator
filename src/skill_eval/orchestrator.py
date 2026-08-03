"""Build and run the skill x case x runner matrix."""

from __future__ import annotations

from pathlib import Path

from skill_eval.cases.loader import load_cases_for_skill
from skill_eval.evaluators.assertion import AssertionEvaluator
from skill_eval.evaluators.base import Evaluator
from skill_eval.evaluators.budget import BudgetEvaluator
from skill_eval.evaluators.judge import JudgeEvaluator
from skill_eval.evaluators.trajectory import TrajectoryEvaluator
from skill_eval.judges.base import Judge
from skill_eval.judges.fake import FakeJudge
from skill_eval.models import CaseOutcome, EvalCase, RunReport, Skill
from skill_eval.runners.base import Runner


def _run_one(
    skill: Skill, case: EvalCase, runner: Runner, evaluators: list[Evaluator]
) -> CaseOutcome:
    """Run a single combination and score it, keeping errored distinct from failed."""
    result = runner.run(skill, case)
    if result.errored:
        return CaseOutcome(
            skill_name=skill.name,
            case_name=case.name,
            runner=runner.name,
            status="errored",
            scores=[],
            result=result,
        )
    scores = [evaluator.evaluate(case, result) for evaluator in evaluators]
    if any(score.errored for score in scores):
        # An evaluator that blew up (a judge endpoint returning 500, structured
        # output that did not match the rubric) is an infra signal, exactly like
        # a runner that blew up. It must not read as a skill that got worse.
        status = "errored"
    else:
        status = "passed" if all(score.passed for score in scores) else "failed"
    return CaseOutcome(
        skill_name=skill.name,
        case_name=case.name,
        runner=runner.name,
        status=status,
        scores=scores,
        result=result,
    )


def run_evals(
    skills: list[Skill],
    runners: list[Runner],
    evals_path: Path | None = None,
    evaluators: list[Evaluator] | None = None,
    tag: str | None = None,
    judge: Judge | None = None,
) -> RunReport:
    """Run every (skill, case, runner) combination and aggregate the results.

    Evaluator exceptions (e.g. ``UnknownAssertionKind``, ``InvalidAssertionValue``
    from `skill_eval.evaluators.assertion`) propagate out of this function by
    design: a malformed assertion is an authoring error in the eval YAML, not a
    skill failure, so the run aborts rather than silently reporting a red eval.

    `run_evals` owns the default evaluator composition -- callers (the CLI, in
    particular) must not build their own copy of that list, or the two can
    silently drift apart. `judge` lets a caller swap in a configured judge
    (e.g. `PydanticAIJudge`) without reaching into the default list at all; it
    is only meaningful when `evaluators` is left as None, since an explicit
    `evaluators` list already fully determines scoring. Passing both is
    rejected rather than silently ignoring `judge` -- a caller doing that has
    a contradictory request, not a preference we should guess at.
    """
    if evaluators is not None and judge is not None:
        raise ValueError(
            "run_evals() received both `evaluators` and `judge`; pass an explicit "
            "JudgeEvaluator inside `evaluators` instead of also passing `judge`."
        )
    evaluators = (
        evaluators
        if evaluators is not None
        else [
            AssertionEvaluator(),
            TrajectoryEvaluator(),
            BudgetEvaluator(),
            # The offline judge by default: M3 must never start spending money
            # on its own. Unscripted it errors rather than passing, so a rubric
            # with no real judge configured is never a vacuous green.
            JudgeEvaluator(judge if judge is not None else FakeJudge()),
        ]
    )
    outcomes: list[CaseOutcome] = []
    skipped: list[str] = []
    tag_filtered: list[str] = []
    for skill in skills:
        cases = load_cases_for_skill(skill, evals_path=evals_path)
        if not cases:
            skipped.append(skill.name)
            continue
        if tag is not None:
            cases = [c for c in cases if tag in c.tags]
            if not cases:
                tag_filtered.append(skill.name)
                continue
        for case in cases:
            for runner in runners:
                outcomes.append(_run_one(skill, case, runner, evaluators))
    return RunReport(outcomes=outcomes, skipped_skills=skipped, tag_filtered_skills=tag_filtered)
