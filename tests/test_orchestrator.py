from skill_eval.models import RunResult, Skill
from skill_eval.orchestrator import run_evals
from skill_eval.runners.fake import FakeRunner

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
