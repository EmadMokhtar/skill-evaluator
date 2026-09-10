"""Rubric scoring. The judge is scripted, so every test here is free."""

from skill_lens.evaluators.base import Evaluator
from skill_lens.evaluators.judge import (
    BUDGET_EXHAUSTED,
    MAX_ARTIFACT_BYTES,
    NOT_PRODUCED,
    NOT_TEXT,
    JudgeEvaluator,
    build_request,
)
from skill_lens.judges.fake import FakeJudge
from skill_lens.models import (
    CheckResult,
    EvalCase,
    JudgeSpec,
    JudgeVerdict,
    RunResult,
    WorkspaceSpec,
)

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


def _case(*artifacts: str) -> EvalCase:
    return EvalCase(
        name="n",
        task="t",
        workspace=WorkspaceSpec(),
        judge=JudgeSpec(rubric=["it has a total"], artifacts=list(artifacts)),
    )


def test_a_case_naming_no_artifacts_sends_none(tmp_path):
    request = build_request(_case(), RunResult(output="o", workspace=tmp_path))
    assert request.artifacts == {}


def test_a_named_artifact_is_read_from_the_workspace(tmp_path):
    (tmp_path / "report.md").write_text("north 120", encoding="utf-8")
    request = build_request(_case("report.md"), RunResult(output="o", workspace=tmp_path))
    assert request.artifacts == {"report.md": "north 120"}


def test_a_file_the_agent_never_wrote_is_rendered_not_raised(tmp_path):
    # A rubric like "the report states a total" then fails honestly, which is
    # the verdict a skill that produced nothing deserves.
    request = build_request(_case("report.md"), RunResult(output="o", workspace=tmp_path))
    assert request.artifacts == {"report.md": NOT_PRODUCED}


def test_a_non_utf8_artifact_is_rendered_not_raised(tmp_path):
    (tmp_path / "blob.dat").write_bytes(b"\xff\xfe\x00")
    request = build_request(_case("blob.dat"), RunResult(output="o", workspace=tmp_path))
    assert request.artifacts == {"blob.dat": NOT_TEXT}


def test_an_escaping_artifact_name_is_rendered_not_raised(tmp_path):
    request = build_request(_case("../escape.txt"), RunResult(output="o", workspace=tmp_path))
    assert request.artifacts == {"../escape.txt": NOT_PRODUCED}


def test_a_large_artifact_is_truncated_visibly(tmp_path):
    (tmp_path / "big.md").write_text("x" * (MAX_ARTIFACT_BYTES + 500), encoding="utf-8")
    request = build_request(_case("big.md"), RunResult(output="o", workspace=tmp_path))
    body = request.artifacts["big.md"]
    assert "truncated" in body
    assert len(body.encode("utf-8")) < MAX_ARTIFACT_BYTES + 200


def test_the_total_budget_is_enforced_across_artifacts(tmp_path):
    names = [f"f{index}.md" for index in range(5)]
    for name in names:
        (tmp_path / name).write_text("y" * MAX_ARTIFACT_BYTES, encoding="utf-8")
    request = build_request(_case(*names), RunResult(output="o", workspace=tmp_path))
    assert BUDGET_EXHAUSTED in request.artifacts.values()


def test_a_repeated_artifact_name_is_read_once(tmp_path):
    (tmp_path / "report.md").write_text("body", encoding="utf-8")
    request = build_request(
        _case("report.md", "report.md"), RunResult(output="o", workspace=tmp_path)
    )
    assert request.artifacts == {"report.md": "body"}
