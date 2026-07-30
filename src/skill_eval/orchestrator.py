"""Build and run the skill x case x runner matrix."""

from __future__ import annotations

from pathlib import Path

from skill_eval.cases.loader import load_cases_for_skill
from skill_eval.evaluators.assertion import AssertionEvaluator
from skill_eval.evaluators.base import Evaluator
from skill_eval.models import CaseOutcome, EvalCase, RunReport, Skill
from skill_eval.runners.base import Runner


def _run_one(
    skill: Skill, case: EvalCase, runner: Runner, evaluators: list[Evaluator]
) -> CaseOutcome:
    """Run a single combination and score it, keeping errored distinct from failed."""
    result = runner.run(skill, case.task)
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
    status = "passed" if all(s.passed for s in scores) else "failed"
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
) -> RunReport:
    """Run every (skill, case, runner) combination and aggregate the results.

    Evaluator exceptions (e.g. ``UnknownAssertionKind``, ``InvalidAssertionValue``
    from `skill_eval.evaluators.assertion`) propagate out of this function by
    design: a malformed assertion is an authoring error in the eval YAML, not a
    skill failure, so the run aborts rather than silently reporting a red eval.
    """
    evaluators = evaluators if evaluators is not None else [AssertionEvaluator()]
    outcomes: list[CaseOutcome] = []
    skipped: list[str] = []
    for skill in skills:
        cases = load_cases_for_skill(skill, evals_path=evals_path)
        if tag is not None:
            cases = [c for c in cases if tag in c.tags]
        if not cases:
            skipped.append(skill.name)
            continue
        for case in cases:
            for runner in runners:
                outcomes.append(_run_one(skill, case, runner, evaluators))
    return RunReport(outcomes=outcomes, skipped_skills=skipped)
