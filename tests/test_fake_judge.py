"""The offline judge that keeps the zero-cost tier honest."""

from skill_eval.judges.base import Judge
from skill_eval.judges.fake import FakeJudge
from skill_eval.models import CheckResult, JudgeRequest, JudgeVerdict, RubricCheck


def request(task: str = "t") -> JudgeRequest:
    return JudgeRequest(task=task, output="o", checks=[RubricCheck(id="r1", text="x")])


def test_it_satisfies_the_judge_protocol():
    assert isinstance(FakeJudge(), Judge)


def test_an_unscripted_judge_refuses_to_judge_rather_than_inventing_a_pass():
    # This is what makes judge = "fake" safe as the built-in default: a rubric
    # with no real judge configured errors, it never scores green.
    verdict = FakeJudge().judge(request())
    assert verdict.errored is True
    assert "no judge is configured" in verdict.error


def test_a_scripted_verdict_is_returned_for_its_task():
    scripted = JudgeVerdict(checks=[CheckResult(id="r1", passed=True, evidence="said it")])
    verdict = FakeJudge({"t": scripted}).judge(request("t"))
    assert verdict.checks[0].passed is True


def test_a_default_verdict_covers_every_other_task():
    scripted = JudgeVerdict(checks=[CheckResult(id="r1", passed=False, evidence="no")])
    verdict = FakeJudge(default=scripted).judge(request("anything"))
    assert verdict.checks[0].passed is False


def test_a_caller_cannot_corrupt_the_scripted_state():
    scripted = JudgeVerdict(checks=[CheckResult(id="r1", passed=True, evidence="said it")])
    judge = FakeJudge({"t": scripted})
    judge.judge(request("t")).checks[0].evidence = "tampered"
    assert judge.judge(request("t")).checks[0].evidence == "said it"
