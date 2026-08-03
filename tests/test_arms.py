"""Two-armed runs: candidate vs baseline, sampled N times."""

from __future__ import annotations

import subprocess

import pytest

from skill_eval.models import RunResult, Skill
from skill_eval.orchestrator import run_evals
from skill_eval.runners.fake import FakeRunner

CASES_YAML = """cases:
  - name: passes
    task: good
    assertions:
      - kind: contains
        value: yes
"""

OFFERED_YAML = """cases:
  - name: triggers
    task: good
    mode: offered
    trajectory:
      skill_triggered: true
"""


def _skill(tmp_path, yaml_text=CASES_YAML, name="pdf"):
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / f"{name}.eval.yaml").write_text(yaml_text, encoding="utf-8")
    return Skill(name=name, description="d", instructions="i", path=skill_dir)


def _runner():
    """Passes with the skill, fails without it."""
    return FakeRunner(
        responses={"good": RunResult(output="yes it worked", skill_triggered=True)},
        baseline_responses={"good": RunResult(output="nope", skill_triggered=False)},
    )


def test_without_a_baseline_nothing_changes(tmp_path):
    report = run_evals([_skill(tmp_path)], [_runner()])
    assert report.total == 1
    assert report.baseline_kind is None
    assert report.baseline_outcomes == []
    assert report.outcomes[0].arm == "candidate"
    assert report.outcomes[0].repeat_index == 0


def test_a_baseline_runs_every_case_twice(tmp_path):
    report = run_evals([_skill(tmp_path)], [_runner()], baseline="none")
    assert len(report.outcomes) == 2
    assert {o.arm for o in report.outcomes} == {"candidate", "baseline"}
    assert report.baseline_kind == "none"


def test_baseline_outcomes_do_not_touch_the_gate_numbers(tmp_path):
    report = run_evals([_skill(tmp_path)], [_runner()], baseline="none")
    # One candidate pass, one baseline fail -- the pass rate is about the
    # candidate arm alone, so a weak baseline must not drag it below 100%.
    assert report.total == 1
    assert report.passed == 1
    assert report.failed == 0
    assert report.pass_rate == 1.0
    assert report.pass_rate_by_skill() == {"pdf": 1.0}


def test_the_baseline_arm_gets_a_skill_with_nothing_to_say(tmp_path):
    seen: list[Skill] = []

    class Recorder(FakeRunner):
        def run(self, skill, case):
            seen.append(skill)
            return super().run(skill, case)

    run_evals([_skill(tmp_path)], [Recorder()], baseline="none")
    baseline = next(s for s in seen if s.variant == "baseline")
    assert baseline.description == ""
    assert baseline.instructions == ""
    assert baseline.version == ""


def test_repeat_samples_each_arm_n_times(tmp_path):
    report = run_evals([_skill(tmp_path)], [_runner()], baseline="none", repeat=3)
    assert len(report.outcomes) == 6
    assert sorted(o.repeat_index for o in report.candidate_outcomes) == [0, 1, 2]
    assert report.repeat == 3


def test_repeat_without_a_baseline_still_samples(tmp_path):
    report = run_evals([_skill(tmp_path)], [_runner()], repeat=4)
    assert report.total == 4
    assert report.baseline_outcomes == []


def test_a_repeat_below_one_is_a_programming_error(tmp_path):
    with pytest.raises(ValueError, match="repeat must be at least 1"):
        run_evals([_skill(tmp_path)], [_runner()], repeat=0)


def test_an_offered_case_skips_the_baseline_under_none(tmp_path):
    report = run_evals([_skill(tmp_path, yaml_text=OFFERED_YAML)], [_runner()], baseline="none")
    assert report.baseline_outcomes == []
    assert [n.kind for n in report.baseline_notes] == ["skipped"]
    assert report.baseline_notes[0].case_name == "triggers"


def test_an_offered_case_runs_both_arms_under_previous(tmp_path):
    repo = tmp_path
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    skill_dir = repo / "pdf"
    skill_dir.mkdir()
    (skill_dir / "pdf.eval.yaml").write_text(OFFERED_YAML, encoding="utf-8")
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("---\nname: pdf\nversion: 1.0.0\n---\n\nold\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "feat: v1"], cwd=repo, check=True)
    skill_md.write_text("---\nname: pdf\nversion: 1.1.0\n---\n\nnew\n", encoding="utf-8")

    from skill_eval.skills.loader import load_skills

    report = run_evals(load_skills(skill_dir), [_runner()], baseline="previous")

    assert len(report.baseline_outcomes) == 1
    assert report.baseline_notes == []


def test_an_unresolvable_baseline_is_a_note_not_a_crash(tmp_path):
    report = run_evals([_skill(tmp_path)], [_runner()], baseline="previous")
    assert report.baseline_outcomes == []
    assert [n.kind for n in report.baseline_notes] == ["unavailable"]
    assert report.baseline_notes[0].skill_name == "pdf"


def test_an_errored_baseline_run_is_counted_apart_from_errored(tmp_path):
    runner = FakeRunner(
        responses={"good": RunResult(output="yes it worked")},
        baseline_responses={"good": RunResult(error="provider 500")},
    )
    report = run_evals([_skill(tmp_path)], [runner], baseline="none")
    assert report.errored == 0
    assert report.baseline_errored == 1
