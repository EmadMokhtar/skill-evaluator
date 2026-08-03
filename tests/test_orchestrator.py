import pytest

from skill_eval.evaluators.assertion import UnknownAssertionKind
from skill_eval.judges.fake import FakeJudge
from skill_eval.models import (
    CheckResult,
    EvalCase,
    EvalScore,
    JudgeVerdict,
    RunResult,
    Skill,
    ToolCall,
)
from skill_eval.orchestrator import run_evals
from skill_eval.runners.fake import FakeRunner
from skill_eval.skills.loader import load_skills

CASES_YAML = """cases:
  - name: passes
    task: good
    tags: [smoke]
    assertions:
      - kind: contains
        value: yes
  - name: fails
    task: bad
    assertions:
      - kind: contains
        value: never-there
"""


def _skill_with_cases(tmp_path, name="pdf", yaml_text=CASES_YAML):
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / f"{name}.eval.yaml").write_text(yaml_text)
    return Skill(name=name, description="", instructions="", path=skill_dir)


def _runner():
    return FakeRunner(
        responses={
            "good": RunResult(output="yes it worked"),
            "bad": RunResult(output="nope"),
            "explodes": RunResult(error="provider 500"),
        }
    )


def test_runs_all_cases_and_marks_pass_and_fail(tmp_path):
    report = run_evals([_skill_with_cases(tmp_path)], [_runner()])
    assert report.total == 2
    assert report.passed == 1
    assert report.failed == 1


def test_runner_error_is_marked_errored_not_failed(tmp_path):
    yaml_text = "cases:\n  - name: boom\n    task: explodes\n"
    report = run_evals([_skill_with_cases(tmp_path, yaml_text=yaml_text)], [_runner()])
    assert report.errored == 1
    assert report.failed == 0
    assert report.outcomes[0].status == "errored"


def test_skill_with_no_cases_is_reported_as_skipped(tmp_path):
    empty_dir = tmp_path / "bare"
    empty_dir.mkdir()
    skill = Skill(name="bare", description="", instructions="", path=empty_dir)
    report = run_evals([skill], [_runner()])
    assert report.skipped_skills == ["bare"]
    assert report.total == 0


def test_matrix_covers_every_skill_case_runner_combination(tmp_path):
    skills = [_skill_with_cases(tmp_path, "pdf"), _skill_with_cases(tmp_path, "xlsx")]
    runners = [_runner(), FakeRunner(default=RunResult(output="yes"))]
    report = run_evals(skills, runners)
    assert report.total == 8  # 2 skills x 2 cases x 2 runners


def test_outcome_records_skill_case_and_runner_names(tmp_path):
    report = run_evals([_skill_with_cases(tmp_path)], [_runner()])
    outcome = report.outcomes[0]
    assert outcome.skill_name == "pdf"
    assert outcome.case_name == "passes"
    assert outcome.runner == "fake"


def test_tag_filter_selects_matching_cases(tmp_path):
    report = run_evals([_skill_with_cases(tmp_path)], [_runner()], tag="smoke")
    assert report.total == 1
    assert report.outcomes[0].case_name == "passes"


def test_tag_filter_excluding_all_cases_is_tag_filtered_not_skipped(tmp_path):
    """A skill that HAS eval cases, none matching --tag, is not the same as a
    skill with zero eval cases at all. Item 2: distinguish the two so the
    console/gate can report the real cause instead of a misleading "skipped
    (no eval cases)" message.
    """
    yaml_text = "cases:\n  - name: no tags here\n    task: good\n"
    report = run_evals(
        [_skill_with_cases(tmp_path, yaml_text=yaml_text)], [_runner()], tag="nonexistent-tag"
    )
    assert report.total == 0
    assert report.skipped_skills == []
    assert report.tag_filtered_skills == ["pdf"]


def test_errored_case_still_records_the_result(tmp_path):
    yaml_text = "cases:\n  - name: boom\n    task: explodes\n"
    report = run_evals([_skill_with_cases(tmp_path, yaml_text=yaml_text)], [_runner()])
    assert report.outcomes[0].result.error == "provider 500"


def test_evaluator_is_not_run_for_errored_cases(tmp_path):
    yaml_text = (
        "cases:\n  - name: boom\n    task: explodes\n"
        "    assertions:\n      - kind: contains\n        value: never\n"
    )
    report = run_evals([_skill_with_cases(tmp_path, yaml_text=yaml_text)], [_runner()])
    assert report.outcomes[0].scores == []


def test_unknown_assertion_kind_aborts_the_run(tmp_path):
    """Characterization test: a malformed assertion aborts run_evals by design.

    An unknown `kind:` in an eval YAML is an authoring error in the user's
    eval file, not a skill failure. The owner decided this should abort the
    whole matrix (propagate out of run_evals) rather than be caught and
    reported as a red eval outcome, so the orchestrator deliberately has no
    try/except around evaluator.evaluate(...). This test locks in that
    behavior; the CLI is expected to turn this exception into a clean exit
    code in a later task.
    """
    yaml_text = (
        "cases:\n  - name: bad kind\n    task: good\n"
        "    assertions:\n      - kind: nonsense\n        value: whatever\n"
    )
    with pytest.raises(UnknownAssertionKind):
        run_evals([_skill_with_cases(tmp_path, yaml_text=yaml_text)], [_runner()])


def test_default_evaluators_include_trajectory_and_budget(tmp_path):
    skill_dir = tmp_path / "s"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: s\n---\nbody\n", encoding="utf-8")
    (skill_dir / "s.eval.yaml").write_text(
        "cases:\n"
        "  - name: c\n"
        "    task: t\n"
        "    tools:\n"
        "      - name: lookup_order\n"
        "    trajectory:\n"
        "      called: [lookup_order]\n"
        "    budget:\n"
        "      max_tokens: 100\n",
        encoding="utf-8",
    )
    skills = load_skills(skill_dir)
    runner = FakeRunner(
        default=RunResult(tool_calls=[ToolCall(name="lookup_order")], input_tokens=10)
    )
    report = run_evals(skills, [runner])
    # Task 7 widens the default evaluator list to include the offline judge;
    # this case has no `judge:` block so JudgeEvaluator vacuous-passes and
    # still appends a "judge" score.
    assert [score.evaluator for score in report.outcomes[0].scores] == [
        "assertion",
        "trajectory",
        "budget",
        "judge",
    ]
    assert report.outcomes[0].status == "passed"


class ErroringEvaluator:
    name = "boom"

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        return EvalScore(evaluator=self.name, passed=False, errored=True, detail="judge died")


class PassingEvaluator:
    name = "fine"

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        return EvalScore(evaluator=self.name, passed=True, score=1.0)


def test_an_errored_evaluator_errors_the_case_rather_than_failing_it(tmp_path):
    # A judge endpoint returning 500 must not read as a skill that got worse.
    yaml_text = "cases:\n  - name: c\n    task: t\n"
    report = run_evals(
        [_skill_with_cases(tmp_path, yaml_text=yaml_text)],
        [FakeRunner()],
        evaluators=[PassingEvaluator(), ErroringEvaluator()],
    )
    assert report.outcomes[0].status == "errored"
    assert report.errored == 1
    assert report.failed == 0


def test_a_merely_failing_evaluator_still_fails_the_case(tmp_path):
    class FailingEvaluator:
        name = "nope"

        def evaluate(self, case, result):
            return EvalScore(evaluator=self.name, passed=False, score=0.0)

    yaml_text = "cases:\n  - name: c\n    task: t\n"
    report = run_evals(
        [_skill_with_cases(tmp_path, yaml_text=yaml_text)],
        [FakeRunner()],
        evaluators=[FailingEvaluator()],
    )
    assert report.outcomes[0].status == "failed"


def test_the_default_evaluators_include_a_judge(tmp_path):
    # Default judging is the offline FakeJudge, so this stays free -- and a
    # case with no judge block is a vacuous pass.
    yaml_text = "cases:\n  - name: c\n    task: t\n"
    report = run_evals([_skill_with_cases(tmp_path, yaml_text=yaml_text)], [FakeRunner()])
    assert "judge" in [score.evaluator for score in report.outcomes[0].scores]


def test_an_unjudged_rubric_errors_under_the_default_judge(tmp_path):
    yaml_text = "cases:\n  - name: c\n    task: t\n    judge:\n      rubric:\n        - is polite\n"
    skill = _skill_with_cases(tmp_path, yaml_text=yaml_text)
    report = run_evals([skill], [FakeRunner()])
    assert report.outcomes[0].status == "errored"


def test_a_trajectory_violation_fails_the_case(tmp_path):
    skill_dir = tmp_path / "s"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: s\n---\nbody\n", encoding="utf-8")
    (skill_dir / "s.eval.yaml").write_text(
        "cases:\n"
        "  - name: c\n"
        "    task: t\n"
        "    tools:\n"
        "      - name: issue_refund\n"
        "    trajectory:\n"
        "      forbidden: [issue_refund]\n",
        encoding="utf-8",
    )
    runner = FakeRunner(default=RunResult(tool_calls=[ToolCall(name="issue_refund")]))
    report = run_evals(load_skills(skill_dir), [runner])
    assert report.outcomes[0].status == "failed"
    assert report.outcomes[0].result.errored is False


def test_the_caller_supplied_judge_is_actually_used(tmp_path):
    """`judge=` must reach `JudgeEvaluator`, not be silently discarded.

    An unscripted default `FakeJudge()` always errors a rubric-bearing case
    (see `FakeJudge.NOT_CONFIGURED`). Passing a `FakeJudge` scripted to pass
    the rubric makes the outcome flip to "passed" -- a status a default judge
    could never produce here. That makes the two outcomes unmistakable: if
    `judge` were ignored in favor of a fresh `FakeJudge()`, this would go
    "errored" instead.
    """
    yaml_text = "cases:\n  - name: c\n    task: t\n    judge:\n      rubric:\n        - is polite\n"
    skill = _skill_with_cases(tmp_path, yaml_text=yaml_text)
    configured_judge = FakeJudge(
        default=JudgeVerdict(checks=[CheckResult(id="r1", passed=True, evidence="polite tone")])
    )
    report = run_evals([skill], [FakeRunner()], judge=configured_judge)
    assert report.outcomes[0].status == "passed"


def test_passing_both_evaluators_and_judge_raises(tmp_path):
    """`evaluators` and `judge` are mutually exclusive -- pin the guard."""
    yaml_text = "cases:\n  - name: c\n    task: t\n"
    skill = _skill_with_cases(tmp_path, yaml_text=yaml_text)
    with pytest.raises(ValueError) as excinfo:
        run_evals(
            [skill],
            [FakeRunner()],
            evaluators=[PassingEvaluator()],
            judge=FakeJudge(),
        )
    assert "evaluators" in str(excinfo.value)
    assert "judge" in str(excinfo.value)
