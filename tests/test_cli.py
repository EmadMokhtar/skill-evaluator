import json

from typer.testing import CliRunner

from skill_eval.cli import app
from skill_eval.judges.pydantic_ai import PydanticAIJudge
from skill_eval.runners.pydantic_ai import PydanticAIRunner

runner = CliRunner()

SKILL_MD = """---
name: pdf
description: Work with PDFs
---

Use pdfplumber.
"""

CASES_YAML = """cases:
  - name: mentions the skill
    task: anything
    assertions:
      - kind: contains
        value: pdf
"""

FAILING_CASES_YAML = """cases:
  - name: cannot pass
    task: anything
    assertions:
      - kind: contains
        value: definitely-not-in-output
"""

UNKNOWN_KIND_CASES_YAML = """cases:
  - name: bad assertion
    task: anything
    assertions:
      - kind: nonsense
        value: pdf
"""


def _make_skill(tmp_path, name="pdf", cases=CASES_YAML):
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(SKILL_MD.replace("name: pdf", f"name: {name}"))
    if cases is not None:
        (skill_dir / f"{name}.eval.yaml").write_text(cases)
    return skill_dir


def test_version_flag_prints_a_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip()


def test_run_exits_zero_when_all_cases_pass(tmp_path):
    _make_skill(tmp_path)
    result = runner.invoke(app, ["run", str(tmp_path)])
    assert result.exit_code == 0
    assert "1 passed" in result.stdout


def test_run_exits_one_when_a_case_fails(tmp_path):
    _make_skill(tmp_path, cases=FAILING_CASES_YAML)
    result = runner.invoke(app, ["run", str(tmp_path)])
    assert result.exit_code == 1
    assert "Gate FAILED" in result.stdout


def test_min_pass_rate_flag_can_tolerate_failures(tmp_path):
    _make_skill(tmp_path, cases=FAILING_CASES_YAML)
    result = runner.invoke(app, ["run", str(tmp_path), "--min-pass-rate", "0"])
    assert result.exit_code == 0


def test_json_report_is_written_to_file(tmp_path):
    _make_skill(tmp_path)
    out = tmp_path / "report.json"
    result = runner.invoke(app, ["run", str(tmp_path), "--json-output", str(out)])
    assert result.exit_code == 0
    assert json.loads(out.read_text())["summary"]["total"] == 1


def test_run_on_missing_path_exits_with_error(tmp_path):
    result = runner.invoke(app, ["run", str(tmp_path / "nope")])
    assert result.exit_code != 0
    assert "does not exist" in result.stdout


def test_skill_without_cases_is_reported_as_skipped(tmp_path):
    """Item 1: a run where every skill is skipped executed zero cases, so it
    must fail the gate rather than silently exiting 0.
    """
    _make_skill(tmp_path, cases=None)
    result = runner.invoke(app, ["run", str(tmp_path)])
    assert "Skipped" in result.stdout
    assert result.exit_code == 1
    assert "Gate FAILED" in result.stdout


def test_list_command_shows_skills_and_case_counts(tmp_path):
    _make_skill(tmp_path)
    result = runner.invoke(app, ["list", str(tmp_path)])
    assert result.exit_code == 0
    assert "pdf" in result.stdout
    assert "1" in result.stdout


def test_run_with_unknown_assertion_kind_exits_with_error_not_traceback(tmp_path):
    """An unknown assertion `kind:` is an authoring error, not a crash.

    Tasks 6/7 decided this aborts the whole run; the CLI must catch it and
    print a clean message with exit code 2, not let it propagate as a raw
    traceback.
    """
    _make_skill(tmp_path, cases=UNKNOWN_KIND_CASES_YAML)
    result = runner.invoke(app, ["run", str(tmp_path)])
    assert result.exit_code == 2
    assert "nonsense" in result.stdout


def test_json_output_to_nested_nonexistent_directory_creates_it(tmp_path):
    """--json-output to a nested non-existent path creates parent dirs and writes JSON.

    When the parent directory of the JSON output file does not exist, the CLI
    should create it (including intermediate directories) and write the report,
    rather than crashing with a traceback.
    """
    _make_skill(tmp_path / "skills")
    out = tmp_path / "reports" / "nested" / "report.json"
    result = runner.invoke(app, ["run", str(tmp_path / "skills"), "--json-output", str(out)])
    assert result.exit_code == 0
    assert out.exists()
    assert json.loads(out.read_text())["summary"]["total"] == 1


def test_json_output_to_path_with_file_as_parent_exits_with_error(tmp_path):
    """--json-output whose parent is a regular file exits with code 2 and prints message.

    When the parent of the JSON output path is a regular file (not a directory),
    directory creation must fail. The CLI should print a clear error message
    naming the path and exit with code 2, not raise a raw traceback.
    """
    _make_skill(tmp_path / "skills")
    # Create a file where we want a directory
    blocking_file = tmp_path / "blocking"
    blocking_file.write_text("I am a file")
    out = blocking_file / "report.json"
    result = runner.invoke(app, ["run", str(tmp_path / "skills"), "--json-output", str(out)])
    assert result.exit_code == 2
    assert str(out) in result.stdout


def test_json_write_failure_does_not_mask_a_failing_gate(tmp_path):
    """Item 9: if the gate already failed (exit 1) and the JSON write also
    fails, the process must still exit 1. Exit codes are the CI contract --
    a failing gate must stay visible, not get masked by an unrelated write
    problem turning into exit 2. The write problem is still reported.
    """
    _make_skill(tmp_path / "skills", cases=FAILING_CASES_YAML)
    blocking_file = tmp_path / "blocking"
    blocking_file.write_text("I am a file")
    out = blocking_file / "report.json"
    result = runner.invoke(app, ["run", str(tmp_path / "skills"), "--json-output", str(out)])
    assert result.exit_code == 1
    assert "Failed to write JSON report" in result.stdout
    assert "Gate FAILED" in result.stdout


def test_json_output_with_failing_gate_writes_report_and_exits_one(tmp_path):
    """--json-output combined with a failing gate writes the report and exits 1.

    When the gate fails (exit code 1), the JSON report should still be written
    before the exit. Previously this combination was untested.
    """
    _make_skill(tmp_path / "skills", cases=FAILING_CASES_YAML)
    out = tmp_path / "report.json"
    result = runner.invoke(app, ["run", str(tmp_path / "skills"), "--json-output", str(out)])
    assert result.exit_code == 1
    assert out.exists()
    report = json.loads(out.read_text())
    assert report["summary"]["total"] == 1
    assert report["summary"]["failed"] == 1


def test_unknown_runner_is_a_user_error(tmp_path):
    skill_dir = _make_skill(tmp_path)
    result = runner.invoke(app, ["run", str(skill_dir), "--runner", "nope"])
    assert result.exit_code == 2


def test_the_real_runner_is_registered(tmp_path):
    skill_dir = _make_skill(tmp_path)
    result = runner.invoke(
        app,
        ["run", str(skill_dir), "--runner", "pydantic-ai", "--model", "openai:gpt-4o-mini"],
        env={"OPENAI_API_KEY": ""},
    )
    # No key, so preflight stops it before any spend.
    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in result.output


def test_preflight_names_the_missing_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    skill_dir = _make_skill(tmp_path)
    result = runner.invoke(
        app,
        [
            "run",
            str(skill_dir),
            "--runner",
            "pydantic-ai",
            "--model",
            "anthropic:claude-sonnet-4-6",
        ],
    )
    assert result.exit_code == 2
    assert "ANTHROPIC_API_KEY" in result.output
    assert "skill-eval.toml" in result.output


def test_the_fake_runner_needs_no_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    skill_dir = _make_skill(tmp_path)
    result = runner.invoke(app, ["run", str(skill_dir), "--runner", "fake"])
    assert result.exit_code in (0, 1)  # gate verdict, not a preflight refusal
    assert "OPENAI_API_KEY" not in result.output


def test_model_flag_beats_the_config_file(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    skill_dir = _make_skill(tmp_path)
    config_file = tmp_path / "skill-eval.toml"
    config_file.write_text('model = "anthropic:claude-sonnet-4-6"\n', encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "run",
            str(skill_dir),
            "--runner",
            "pydantic-ai",
            "--model",
            "openai:gpt-4o-mini",
            "--config",
            str(config_file),
        ],
    )
    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in result.output
    assert "ANTHROPIC_API_KEY" not in result.output


def test_an_unknown_judge_in_config_is_a_user_error(tmp_path):
    skill_dir = _make_skill(tmp_path)
    (tmp_path / "skill-eval.toml").write_text('judge = "psychic"\n', encoding="utf-8")
    result = runner.invoke(
        app, ["run", str(skill_dir), "--config", str(tmp_path / "skill-eval.toml")]
    )
    assert result.exit_code == 2
    assert "psychic" in result.output


def test_a_real_judge_without_its_api_key_fails_preflight(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    skill_dir = _make_skill(tmp_path)
    (tmp_path / "skill-eval.toml").write_text(
        'judge = "pydantic-ai"\njudge_model = "openai:gpt-4o-mini"\n', encoding="utf-8"
    )
    result = runner.invoke(
        app, ["run", str(skill_dir), "--config", str(tmp_path / "skill-eval.toml")]
    )
    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in result.output


def test_the_judge_model_falls_back_to_the_run_model(tmp_path, monkeypatch):
    # An empty judge_model must not reach the provider as an empty model id.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    skill_dir = _make_skill(tmp_path)
    (tmp_path / "skill-eval.toml").write_text(
        'judge = "pydantic-ai"\nmodel = "anthropic:claude-haiku-4-5-20251001"\n',
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["run", str(skill_dir), "--config", str(tmp_path / "skill-eval.toml")]
    )
    assert result.exit_code == 2
    assert "ANTHROPIC_API_KEY" in result.output


def test_judge_temperature_is_independent_of_the_runner_temperature(tmp_path, monkeypatch):
    """The judge must be constructed at `judge_temperature` (default 0.0 for
    determinism), never at the runner's `temperature` -- a team raising
    `temperature` to exercise the runner under sampling must not silently make
    every rubric verdict nondeterministic too. See Config.judge_temperature.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    skill_dir = _make_skill(tmp_path)
    (tmp_path / "skill-eval.toml").write_text(
        'judge = "pydantic-ai"\njudge_model = "openai:gpt-4o-mini"\ntemperature = 0.7\n',
        encoding="utf-8",
    )
    captured: dict = {}

    def _capture(self, **kwargs):
        captured.update(kwargs)
        raise AssertionError("stop before any network call")

    monkeypatch.setattr(PydanticAIJudge, "__init__", _capture)
    runner.invoke(app, ["run", str(skill_dir), "--config", str(tmp_path / "skill-eval.toml")])

    assert captured["temperature"] == 0.0


def test_judge_temperature_unset_reaches_the_judge(tmp_path, monkeypatch):
    """A reasoning judge model needs `judge_temperature = "unset"`, independent
    of the runner's `temperature`.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    skill_dir = _make_skill(tmp_path)
    (tmp_path / "skill-eval.toml").write_text(
        'judge = "pydantic-ai"\njudge_model = "openai:gpt-4o-mini"\njudge_temperature = "unset"\n',
        encoding="utf-8",
    )
    captured: dict = {}

    def _capture(self, **kwargs):
        captured.update(kwargs)
        raise AssertionError("stop before any network call")

    monkeypatch.setattr(PydanticAIJudge, "__init__", _capture)
    runner.invoke(app, ["run", str(skill_dir), "--config", str(tmp_path / "skill-eval.toml")])

    assert captured["temperature"] == "unset"


def test_json_output_with_non_ascii_is_written_as_utf8(tmp_path):
    # Regression test: the JSON report is a machine-readable CI artifact and must
    # be UTF-8 regardless of the platform's default encoding, or non-ASCII skill
    # names and assertion details come back as mojibake (or fail to encode).
    skill_dir = tmp_path / "café"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: café\ndescription: accented\n---\n\nBody.\n", encoding="utf-8"
    )
    (skill_dir / "café.eval.yaml").write_text(
        "cases:\n  - name: 日本語 case\n    task: anything\n"
        "    assertions:\n      - kind: contains\n        value: café\n",
        encoding="utf-8",
    )
    out = tmp_path / "report.json"
    result = runner.invoke(app, ["run", str(tmp_path), "--json-output", str(out)])
    assert result.exit_code == 0, result.stdout
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["outcomes"][0]["skill_name"] == "café"
    assert data["outcomes"][0]["case_name"] == "日本語 case"


def test_runner_preflight_wins_the_race_against_construction(tmp_path, monkeypatch):
    """A missing key must be caught before `PydanticAIRunner(...)` ever runs.

    If `check_api_key` moved to *after* construction, a future runner whose
    `__init__` does real work (builds a client, etc.) would spend before the
    key check ever fires. Pin the ordering directly: make construction itself
    blow up, and prove the CLI still reports the missing key rather than the
    construction crash.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    skill_dir = _make_skill(tmp_path)

    def _boom(self, *args, **kwargs):
        raise AssertionError("PydanticAIRunner constructed before preflight")

    monkeypatch.setattr(PydanticAIRunner, "__init__", _boom)
    result = runner.invoke(
        app,
        ["run", str(skill_dir), "--runner", "pydantic-ai", "--model", "openai:gpt-4o-mini"],
    )
    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in result.output
    assert "constructed before preflight" not in result.output


def test_judge_preflight_wins_the_race_against_construction(tmp_path, monkeypatch):
    """Same guarantee as above, for the judge: preflight must run before
    `PydanticAIJudge(...)` is ever called.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    skill_dir = _make_skill(tmp_path)
    (tmp_path / "skill-eval.toml").write_text(
        'judge = "pydantic-ai"\njudge_model = "openai:gpt-4o-mini"\n', encoding="utf-8"
    )

    def _boom(self, *args, **kwargs):
        raise AssertionError("PydanticAIJudge constructed before preflight")

    monkeypatch.setattr(PydanticAIJudge, "__init__", _boom)
    result = runner.invoke(
        app, ["run", str(skill_dir), "--config", str(tmp_path / "skill-eval.toml")]
    )
    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in result.output
    assert "constructed before preflight" not in result.output


def test_a_blank_model_is_a_user_error_not_a_broken_run(tmp_path):
    # A blank id has no provider prefix, so preflight finds nothing to check and
    # the run used to die inside the adapter as an errored case (exit 1 -- "the
    # run broke") for what is really a mistyped flag. Exit codes are the CI
    # contract: a user error is 2.
    skill_dir = _make_skill(tmp_path)
    result = runner.invoke(app, ["run", str(skill_dir), "--runner", "pydantic-ai", "--model", ""])
    assert result.exit_code == 2
    assert "--model is empty" in result.output


def test_a_blank_judge_model_is_a_user_error_not_a_broken_run(tmp_path):
    skill_dir = _make_skill(tmp_path)
    config = tmp_path / "skill-eval.toml"
    config.write_text('judge = "pydantic-ai"\n', encoding="utf-8")
    result = runner.invoke(
        app, ["run", str(skill_dir), "--config", str(config), "--judge-model", "   "]
    )
    assert result.exit_code == 2
    assert "--judge-model is empty" in result.output


def test_a_blank_model_in_the_config_file_is_caught_too(tmp_path):
    # Checked on the resolved value, so a blank in skill-eval.toml is rejected
    # exactly like a blank flag.
    skill_dir = _make_skill(tmp_path)
    config = tmp_path / "skill-eval.toml"
    config.write_text('default_runner = "pydantic-ai"\nmodel = ""\n', encoding="utf-8")
    result = runner.invoke(app, ["run", str(skill_dir), "--config", str(config)])
    assert result.exit_code == 2
    assert "--model is empty" in result.output
