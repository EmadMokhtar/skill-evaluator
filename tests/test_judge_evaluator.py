"""Rubric scoring. The judge is scripted, so every test here is free."""

from skill_eval.evaluators.base import Evaluator
from skill_eval.evaluators.judge import JudgeEvaluator, build_request
from skill_eval.judges.fake import FakeJudge
from skill_eval.models import CheckResult, EvalCase, JudgeSpec, JudgeVerdict, RunResult

RESULT = RunResult(output="The return window is 30 days.")


def judged_case(*rubric: str, expected: str = "") -> EvalCase:
    return EvalCase(
        name="c",
        task="Why can't I return this?",
        judge=JudgeSpec(expected=expected, rubric=list(rubric)),
    )


def verdict(*checks: CheckResult, **kwargs) -> JudgeVerdict:
    return JudgeVerdict(checks=list(checks), **kwargs)


def evaluator(v: JudgeVerdict) -> JudgeEvaluator:
    return JudgeEvaluator(FakeJudge(default=v))


def test_it_satisfies_the_evaluator_protocol():
    assert isinstance(JudgeEvaluator(FakeJudge()), Evaluator)


def test_a_case_with_no_judge_block_is_a_vacuous_pass():
    score = JudgeEvaluator(FakeJudge()).evaluate(EvalCase(name="c", task="t"), RESULT)
    assert score.passed is True
    assert score.score == 1.0
    assert score.detail == "no judge checks"


def test_ids_are_generated_positionally_from_the_rubric():
    request = build_request(judged_case("states the window", "avoids jargon"), RESULT)
    assert [(c.id, c.text) for c in request.checks] == [
        ("r1", "states the window"),
        ("r2", "avoids jargon"),
    ]
    assert request.task == "Why can't I return this?"
    assert request.output == "The return window is 30 days."


def test_every_check_passing_with_evidence_passes_the_case():
    score = evaluator(
        verdict(
            CheckResult(id="r1", passed=True, evidence="'30 days'"),
            CheckResult(id="r2", passed=True, evidence="no jargon present"),
        )
    ).evaluate(judged_case("states the window", "avoids jargon"), RESULT)
    assert score.passed is True
    assert score.score == 1.0
    assert len(score.checks) == 2


def test_the_score_is_the_fraction_of_checks_that_held():
    score = evaluator(
        verdict(
            CheckResult(id="r1", passed=True, evidence="'30 days'"),
            CheckResult(id="r2", passed=False, evidence="says 'RMA'"),
        )
    ).evaluate(judged_case("states the window", "avoids jargon"), RESULT)
    assert score.passed is False
    assert score.score == 0.5
    assert "1 of 2" in score.detail


def test_a_pass_with_no_evidence_is_recorded_as_a_failure():
    # An unsupported PASS is the judge's characteristic failure mode, so it
    # gets a mechanical defence rather than a prompt asking nicely.
    score = evaluator(verdict(CheckResult(id="r1", passed=True, evidence="   "))).evaluate(
        judged_case("states the window"), RESULT
    )
    assert score.passed is False
    assert score.checks[0].passed is False
    assert "no evidence" in score.checks[0].evidence


def test_a_judge_failure_errors_rather_than_failing():
    # A judge endpoint returning 500 must not look like a skill that got worse.
    score = evaluator(verdict(error="ModelHTTPError: 500")).evaluate(
        judged_case("states the window"), RESULT
    )
    assert score.errored is True
    assert score.passed is False
    assert "500" in score.detail


def test_an_unconfigured_judge_errors_rather_than_passing():
    score = JudgeEvaluator(FakeJudge()).evaluate(judged_case("states the window"), RESULT)
    assert score.errored is True
    assert score.passed is False


def test_verdicts_for_the_wrong_ids_error_rather_than_failing():
    # Structured output that does not match the rubric is the harness
    # misbehaving, not evidence about the skill.
    score = evaluator(verdict(CheckResult(id="r9", passed=True, evidence="x"))).evaluate(
        judged_case("states the window"), RESULT
    )
    assert score.errored is True
    assert "r1" in score.detail


def test_a_missing_verdict_errors_even_when_the_rest_are_present():
    score = evaluator(verdict(CheckResult(id="r1", passed=True, evidence="x"))).evaluate(
        judged_case("states the window", "avoids jargon"), RESULT
    )
    assert score.errored is True


def test_duplicate_verdicts_for_one_id_error():
    score = evaluator(
        verdict(
            CheckResult(id="r1", passed=True, evidence="x"),
            CheckResult(id="r1", passed=False, evidence="y"),
        )
    ).evaluate(judged_case("states the window"), RESULT)
    assert score.errored is True


def test_judge_spend_is_carried_on_the_score_not_the_run():
    score = evaluator(
        verdict(CheckResult(id="r1", passed=True, evidence="x"), cost_usd=0.002)
    ).evaluate(judged_case("states the window"), RESULT)
    assert score.cost_usd == 0.002


def test_a_rubric_with_no_checks_errors_rather_than_passing_vacuously():
    # The case loader rejects this, but the evaluator is a public seam and must
    # not report a pass for a rubric it never checked.
    case = EvalCase(name="c", task="t", judge=JudgeSpec(rubric=[]))
    score = JudgeEvaluator(FakeJudge()).evaluate(case, RESULT)
    assert score.errored is True
