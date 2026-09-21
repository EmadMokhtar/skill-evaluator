import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from typer.testing import CliRunner

from skill_lens.cli import app
from skill_lens.judges.pydantic_ai import PydanticAIJudge
from skill_lens.runners.pydantic_ai import PydanticAIRunner

runner = CliRunner()

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_BOX = str.maketrans("", "", "│╭╮╯╰─")


def plain(output: str) -> str:
    """Strip Rich's terminal rendering so an assertion tests the message itself.

    Typer renders `BadParameter` through Rich, which styles the flag name and
    line-wraps the text to the terminal width. Both vary by environment: with
    colour on, `--model` arrives as `-` and `-model` with escape codes between
    them, so a plain `in` check silently fails somewhere that has colour (CI)
    while passing somewhere that does not (a piped local run). Removing escapes
    and box-drawing characters and collapsing whitespace rejoins a wrapped
    message into one line, so the assertion is about what we said, not how the
    terminal drew it.
    """
    return " ".join(_ANSI.sub("", output).translate(_BOX).split())


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

WORKSPACE_CASES_YAML = """cases:
  - name: mentions the skill
    task: anything
    workspace: {}
    assertions:
      - kind: contains
        value: pdf
"""

FAKE_PRODUCT = Path(__file__).parent / "fake_product.py"
PRODUCT_FIXTURES = Path(__file__).parent / "fixtures" / "products"

PRODUCT_CASES_YAML = """cases:
  - name: pongs
    task: Please ping.
    assertions:
      - kind: contains
        value: PONG
"""

TOOLS_CASES_YAML = """cases:
  - name: uses a mock tool
    task: anything
    tools:
      - name: lookup
    assertions:
      - kind: contains
        value: x
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


def test_the_langchain_runner_is_registered(tmp_path):
    skill_dir = _make_skill(tmp_path)
    result = runner.invoke(
        app,
        ["run", str(skill_dir), "--runner", "langchain", "--model", "openai:gpt-4o-mini"],
        env={"OPENAI_API_KEY": ""},
    )
    # No key, so preflight stops it before any spend -- the same contract as
    # the PydanticAI runner.
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
    assert "skill-lens.toml" in result.output


def test_the_fake_runner_needs_no_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    skill_dir = _make_skill(tmp_path)
    result = runner.invoke(app, ["run", str(skill_dir), "--runner", "fake"])
    assert result.exit_code in (0, 1)  # gate verdict, not a preflight refusal
    assert "OPENAI_API_KEY" not in result.output


def test_model_flag_beats_the_config_file(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    skill_dir = _make_skill(tmp_path)
    config_file = tmp_path / "skill-lens.toml"
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
    (tmp_path / "skill-lens.toml").write_text('judge = "psychic"\n', encoding="utf-8")
    result = runner.invoke(
        app, ["run", str(skill_dir), "--config", str(tmp_path / "skill-lens.toml")]
    )
    assert result.exit_code == 2
    assert "psychic" in result.output


def test_a_real_judge_without_its_api_key_fails_preflight(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    skill_dir = _make_skill(tmp_path)
    (tmp_path / "skill-lens.toml").write_text(
        'judge = "pydantic-ai"\njudge_model = "openai:gpt-4o-mini"\n', encoding="utf-8"
    )
    result = runner.invoke(
        app, ["run", str(skill_dir), "--config", str(tmp_path / "skill-lens.toml")]
    )
    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in result.output


def test_the_langchain_judge_is_registered(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    skill_dir = _make_skill(tmp_path)
    (tmp_path / "skill-lens.toml").write_text('judge = "langchain"\n', encoding="utf-8")
    result = runner.invoke(
        app, ["run", str(skill_dir), "--config", str(tmp_path / "skill-lens.toml")]
    )
    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in result.output


def test_the_judge_model_falls_back_to_the_run_model(tmp_path, monkeypatch):
    # An empty judge_model must not reach the provider as an empty model id.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    skill_dir = _make_skill(tmp_path)
    (tmp_path / "skill-lens.toml").write_text(
        'judge = "pydantic-ai"\nmodel = "anthropic:claude-haiku-4-5-20251001"\n',
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["run", str(skill_dir), "--config", str(tmp_path / "skill-lens.toml")]
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
    (tmp_path / "skill-lens.toml").write_text(
        'judge = "pydantic-ai"\njudge_model = "openai:gpt-4o-mini"\ntemperature = 0.7\n',
        encoding="utf-8",
    )
    captured: dict = {}

    def _capture(self, **kwargs):
        captured.update(kwargs)
        raise AssertionError("stop before any network call")

    monkeypatch.setattr(PydanticAIJudge, "__init__", _capture)
    runner.invoke(app, ["run", str(skill_dir), "--config", str(tmp_path / "skill-lens.toml")])

    assert captured["temperature"] == 0.0


def test_judge_temperature_unset_reaches_the_judge(tmp_path, monkeypatch):
    """A reasoning judge model needs `judge_temperature = "unset"`, independent
    of the runner's `temperature`.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    skill_dir = _make_skill(tmp_path)
    (tmp_path / "skill-lens.toml").write_text(
        'judge = "pydantic-ai"\njudge_model = "openai:gpt-4o-mini"\njudge_temperature = "unset"\n',
        encoding="utf-8",
    )
    captured: dict = {}

    def _capture(self, **kwargs):
        captured.update(kwargs)
        raise AssertionError("stop before any network call")

    monkeypatch.setattr(PydanticAIJudge, "__init__", _capture)
    runner.invoke(app, ["run", str(skill_dir), "--config", str(tmp_path / "skill-lens.toml")])

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
    (tmp_path / "skill-lens.toml").write_text(
        'judge = "pydantic-ai"\njudge_model = "openai:gpt-4o-mini"\n', encoding="utf-8"
    )

    def _boom(self, *args, **kwargs):
        raise AssertionError("PydanticAIJudge constructed before preflight")

    monkeypatch.setattr(PydanticAIJudge, "__init__", _boom)
    result = runner.invoke(
        app, ["run", str(skill_dir), "--config", str(tmp_path / "skill-lens.toml")]
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
    assert "--model is empty" in plain(result.output)


def test_a_blank_judge_model_is_a_user_error_not_a_broken_run(tmp_path):
    skill_dir = _make_skill(tmp_path)
    config = tmp_path / "skill-lens.toml"
    config.write_text('judge = "pydantic-ai"\n', encoding="utf-8")
    result = runner.invoke(
        app, ["run", str(skill_dir), "--config", str(config), "--judge-model", "   "]
    )
    assert result.exit_code == 2
    assert "--judge-model is empty" in plain(result.output)


def test_a_blank_model_in_the_config_file_is_caught_too(tmp_path):
    # Checked on the resolved value, so a blank in skill-lens.toml is rejected
    # exactly like a blank flag.
    skill_dir = _make_skill(tmp_path)
    config = tmp_path / "skill-lens.toml"
    config.write_text('default_runner = "pydantic-ai"\nmodel = ""\n', encoding="utf-8")
    result = runner.invoke(app, ["run", str(skill_dir), "--config", str(config)])
    assert result.exit_code == 2
    assert "--model is empty" in plain(result.output)


def test_min_delta_without_a_baseline_is_a_user_error(tmp_path):
    _make_skill(tmp_path)
    result = runner.invoke(app, ["run", str(tmp_path), "--min-delta", "0.1"])
    assert result.exit_code == 2
    assert "--baseline" in plain(result.output)


def test_a_repeat_below_one_is_a_user_error(tmp_path):
    _make_skill(tmp_path)
    result = runner.invoke(app, ["run", str(tmp_path), "--repeat", "0"])
    assert result.exit_code == 2


def test_an_unknown_baseline_kind_is_a_user_error(tmp_path):
    _make_skill(tmp_path)
    result = runner.invoke(app, ["run", str(tmp_path), "--baseline", "yesterday"])
    assert result.exit_code == 2


def test_min_delta_is_satisfied_by_a_baseline_from_config(tmp_path):
    # The check runs against resolved values, so a baseline in skill-lens.toml
    # satisfies a --min-delta passed on the command line.
    _make_skill(tmp_path)
    config = tmp_path / "skill-lens.toml"
    config.write_text('baseline = "none"\n', encoding="utf-8")
    result = runner.invoke(
        app, ["run", str(tmp_path), "--config", str(config), "--min-delta", "0.0"]
    )
    assert result.exit_code != 2


def test_the_run_plan_is_not_printed_for_the_offline_runner(tmp_path):
    _make_skill(tmp_path)
    result = runner.invoke(app, ["run", str(tmp_path), "--baseline", "none", "--repeat", "2"])
    assert "Plan:" not in result.stdout


def test_the_run_plan_is_a_ceiling_not_a_forecast(tmp_path, monkeypatch):
    # `mode: offered` under --baseline none and unresolvable baselines both drop
    # the baseline arm, so the printed total can only ever overstate. Saying
    # "up to" is what makes it honest.
    #
    # The skill deliberately has no eval cases: the plan line prints before
    # `run_evals` does anything, so this exercises the plan arithmetic without
    # the runner ever being called. A version of this test with real cases sent
    # four live requests to the provider on every run of the default test tier.
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-key-for-parsing")
    _make_skill(tmp_path, cases=None)
    result = runner.invoke(
        app,
        ["run", str(tmp_path), "--runner", "pydantic-ai", "--baseline", "none", "--repeat", "2"],
    )
    assert "Plan: up to 2 arm(s) x 2 repeat(s) x 1 runner(s)" in plain(result.stdout)


def test_the_run_plan_counts_only_cases_the_tag_filter_keeps(tmp_path, monkeypatch):
    # run_evals applies --tag after discovery. A plan that ignores it can print a
    # nonzero total for a run in which nothing at all will execute.
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-key-for-parsing")
    _make_skill(tmp_path)
    result = runner.invoke(
        app, ["run", str(tmp_path), "--runner", "pydantic-ai", "--tag", "no-such-tag"]
    )
    assert "x 1 runner(s) x 0 case(s) = 0 runs" in plain(result.stdout)


def test_junit_output_writes_parseable_xml(tmp_path):
    _make_skill(tmp_path / "skills")
    out = tmp_path / "reports" / "junit.xml"
    result = runner.invoke(app, ["run", str(tmp_path / "skills"), "--junit-output", str(out)])
    assert result.exit_code == 0
    root = ET.fromstring(out.read_text(encoding="utf-8"))
    assert root.tag == "testsuites"
    assert root.get("tests") == "1"


def test_markdown_output_writes_a_summary(tmp_path):
    _make_skill(tmp_path / "skills")
    out = tmp_path / "summary.md"
    result = runner.invoke(app, ["run", str(tmp_path / "skills"), "--markdown-output", str(out)])
    assert result.exit_code == 0
    assert "gate passed" in out.read_text(encoding="utf-8")


def test_markdown_max_chars_truncates(tmp_path):
    _make_skill(tmp_path / "skills")
    out = tmp_path / "summary.md"
    result = runner.invoke(
        app,
        [
            "run",
            str(tmp_path / "skills"),
            "--markdown-output",
            str(out),
            "--markdown-max-chars",
            "80",
        ],
    )
    assert result.exit_code == 0
    assert len(out.read_text(encoding="utf-8")) <= 80


def test_markdown_max_chars_below_one_is_a_user_error(tmp_path):
    _make_skill(tmp_path / "skills")
    result = runner.invoke(
        app,
        [
            "run",
            str(tmp_path / "skills"),
            "--markdown-output",
            str(tmp_path / "summary.md"),
            "--markdown-max-chars",
            "0",
        ],
    )
    assert result.exit_code == 2
    assert "--markdown-max-chars must be at least 1" in plain(result.output)


def test_markdown_max_chars_without_markdown_output_is_a_user_error(tmp_path):
    """A flag that silently does nothing hides a mistake instead of reporting it."""
    _make_skill(tmp_path / "skills")
    result = runner.invoke(app, ["run", str(tmp_path / "skills"), "--markdown-max-chars", "500"])
    assert result.exit_code == 2
    assert "--markdown-max-chars requires --markdown-output" in plain(result.output)


def test_a_junit_write_failure_does_not_mask_a_failing_gate(tmp_path):
    """Exit codes are the CI contract: a red gate must stay visible rather
    than being escalated to 2 by an unrelated write problem."""
    _make_skill(tmp_path / "skills", cases=FAILING_CASES_YAML)
    blocking = tmp_path / "blocking"
    blocking.write_text("I am a file")
    out = blocking / "junit.xml"
    result = runner.invoke(app, ["run", str(tmp_path / "skills"), "--junit-output", str(out)])
    assert result.exit_code == 1
    assert "Failed to write JUnit report" in result.stdout


def test_a_markdown_write_failure_with_a_passing_gate_exits_two(tmp_path):
    _make_skill(tmp_path / "skills")
    blocking = tmp_path / "blocking"
    blocking.write_text("I am a file")
    out = blocking / "summary.md"
    result = runner.invoke(app, ["run", str(tmp_path / "skills"), "--markdown-output", str(out)])
    assert result.exit_code == 2
    assert "Failed to write Markdown report" in result.stdout


def test_concurrency_below_one_is_a_user_error(tmp_path):
    _make_skill(tmp_path / "skills")
    result = runner.invoke(app, ["run", str(tmp_path / "skills"), "--concurrency", "0"])
    assert result.exit_code == 2
    # typer.BadParameter renders through Click's UsageError handling, which
    # writes to stderr -- result.output is the combined stream, matching every
    # other BadParameter-message assertion in this file (e.g. --model, --baseline).
    assert "--concurrency must be at least 1" in plain(result.output)


def test_concurrency_above_one_runs_the_suite(tmp_path):
    _make_skill(tmp_path / "skills")
    result = runner.invoke(app, ["run", str(tmp_path / "skills"), "--concurrency", "4"])
    assert result.exit_code == 0


def test_keep_workspace_flag_wins_over_a_false_config(tmp_path, monkeypatch):
    # This test deliberately keeps a workspace (--keep-workspace) and never
    # deletes it. tempfile.mkdtemp writes to the system temp directory by
    # default, which tests/conftest.py's chdir-into-tmp_path fixture does not
    # touch -- left alone, every run of this test strands another
    # skill-lens-* directory outside pytest's own cleanup. Redirecting
    # tempfile.tempdir into tmp_path puts the kept workspace where pytest
    # will garbage-collect it.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    skill_dir = _make_skill(tmp_path, cases=WORKSPACE_CASES_YAML)
    result = runner.invoke(app, ["run", str(skill_dir), "--keep-workspace"])
    assert result.exit_code in (0, 1)
    assert "Kept workspaces" in result.stdout


def test_no_keep_workspace_flag_wins_over_a_true_config(tmp_path):
    skill_dir = _make_skill(tmp_path, cases=WORKSPACE_CASES_YAML)
    config = tmp_path / "skill-lens.toml"
    config.write_text("keep_workspace = true\n", encoding="utf-8")
    result = runner.invoke(
        app, ["run", str(skill_dir), "--config", str(config), "--no-keep-workspace"]
    )
    assert "Kept workspaces" not in result.stdout


def test_the_config_alone_turns_keeping_on(tmp_path, monkeypatch):
    # Printing only under the flag would let a committed keep_workspace = true
    # fill a disk with nothing on screen connecting the two. This test also
    # keeps its workspace and never deletes it, so redirect tempfile.tempdir
    # into tmp_path for the same reason as
    # test_keep_workspace_flag_wins_over_a_false_config above -- otherwise
    # mkdtemp strands it in the system temp directory, which pytest never
    # cleans up.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    skill_dir = _make_skill(tmp_path, cases=WORKSPACE_CASES_YAML)
    config = tmp_path / "skill-lens.toml"
    config.write_text("keep_workspace = true\n", encoding="utf-8")
    result = runner.invoke(app, ["run", str(skill_dir), "--config", str(config)])
    assert "Kept workspaces" in result.stdout


# A task long enough that the fake runner's echo of it exceeds OUTPUT_LIMIT.
LONG_TASK = "word " * 200

VERBOSE_FAILING_CASES_YAML = f"""cases:
  - name: cannot pass
    task: {LONG_TASK.strip()}
    assertions:
      - kind: contains
        value: definitely-not-in-output
"""


def test_a_failing_case_prints_its_output_cut_by_default(tmp_path):
    skill_dir = _make_skill(tmp_path, cases=VERBOSE_FAILING_CASES_YAML)
    result = runner.invoke(app, ["run", str(skill_dir)])
    assert result.exit_code == 1
    assert "output: [fake] pdf handled: word word" in result.stdout
    assert "more characters; --full-output prints them" in result.stdout


def test_full_output_prints_everything(tmp_path):
    skill_dir = _make_skill(tmp_path, cases=VERBOSE_FAILING_CASES_YAML)
    result = runner.invoke(app, ["run", str(skill_dir), "--full-output"])
    assert "more characters" not in result.stdout
    assert LONG_TASK.strip() in result.stdout


def test_no_full_output_flag_wins_over_a_true_config(tmp_path):
    skill_dir = _make_skill(tmp_path, cases=VERBOSE_FAILING_CASES_YAML)
    config = tmp_path / "skill-lens.toml"
    config.write_text("full_output = true\n", encoding="utf-8")
    result = runner.invoke(
        app, ["run", str(skill_dir), "--config", str(config), "--no-full-output"]
    )
    assert "more characters" in result.stdout


def test_the_config_alone_lifts_the_cap(tmp_path):
    skill_dir = _make_skill(tmp_path, cases=VERBOSE_FAILING_CASES_YAML)
    config = tmp_path / "skill-lens.toml"
    config.write_text("full_output = true\n", encoding="utf-8")
    result = runner.invoke(app, ["run", str(skill_dir), "--config", str(config)])
    assert "more characters" not in result.stdout


def test_a_passing_run_prints_no_output_lines(tmp_path):
    skill_dir = _make_skill(tmp_path)
    result = runner.invoke(app, ["run", str(skill_dir)])
    assert result.exit_code == 0
    assert "output:" not in result.stdout


TWO_CASES_YAML = """cases:
  - name: mentions the skill
    task: anything
    assertions:
      - kind: contains
        value: pdf
  - name: also fine
    task: anything else
    assertions:
      - kind: contains
        value: pdf
"""


def test_case_flag_runs_only_matching_cases(tmp_path):
    skill_dir = _make_skill(tmp_path, cases=TWO_CASES_YAML)
    result = runner.invoke(app, ["run", str(skill_dir), "--case", "MENTIONS"])
    assert result.exit_code == 0, result.stdout
    assert "mentions the skill" in result.stdout
    assert "also fine" not in result.stdout


def test_a_case_flag_matching_nothing_fails_the_gate_naming_the_flag(tmp_path):
    skill_dir = _make_skill(tmp_path, cases=TWO_CASES_YAML)
    result = runner.invoke(app, ["run", str(skill_dir), "--case", "no-such-case"])
    assert result.exit_code == 1
    assert "no cases matched --case filter" in result.stdout
    assert "the --case filter matched no case for skill(s): pdf" in result.stdout


def test_the_run_plan_counts_only_cases_the_case_filter_keeps(tmp_path, monkeypatch):
    # The plan line is printed before run_evals; stubbing run_evals keeps this
    # test offline while still proving the count reflects the --case filter.
    # (A version of this test that let the one surviving case run reached the
    # provider on every run of the default tier.)
    from skill_lens import cli as cli_module
    from skill_lens.models import RunReport

    monkeypatch.setenv("OPENAI_API_KEY", "dummy-key-for-parsing")
    monkeypatch.setattr(cli_module, "run_evals", lambda *args, **kwargs: RunReport())
    _make_skill(tmp_path, cases=TWO_CASES_YAML)
    result = runner.invoke(app, ["run", str(tmp_path), "--runner", "pydantic-ai", "--case", "also"])
    assert "1 case(s) = 1 runs" in plain(result.stdout)


def test_allow_scripts_flags_override_the_config_in_both_directions(tmp_path):
    # Three states, like --keep-workspace: with allow_scripts = true committed
    # there must still be a way to get a scripts-off run without editing the
    # file. The scripts line is printed only when execution is on.
    skill_dir = _make_skill(tmp_path, cases=WORKSPACE_CASES_YAML)
    config = tmp_path / "skill-lens.toml"
    config.write_text('allow_scripts = true\nscript_sandbox = "off"\n', encoding="utf-8")

    on = runner.invoke(app, ["run", str(skill_dir), "--config", str(config)])
    assert "scripts: on, sandbox: none" in on.stdout

    off = runner.invoke(app, ["run", str(skill_dir), "--config", str(config), "--no-allow-scripts"])
    assert "scripts: on" not in off.stdout

    flag_on = runner.invoke(app, ["run", str(skill_dir), "--allow-scripts"])
    assert "scripts: on, sandbox:" in flag_on.stdout


# --- the runner matrix -------------------------------------------------------

from skill_lens import cli as cli_module  # noqa: E402
from skill_lens.runners.fake import FakeRunner  # noqa: E402


class SecondFake(FakeRunner):
    """A second offline runner, so a matrix can be exercised with no key."""

    name = "fake-2"


def test_the_same_runner_twice_is_a_user_error(tmp_path):
    skill_dir = _make_skill(tmp_path)
    result = runner.invoke(app, ["run", str(skill_dir), "--runner", "fake", "--runner", "fake"])
    assert result.exit_code == 2
    assert "fake given twice" in plain(result.output)


def test_an_unknown_runner_in_a_list_is_named(tmp_path):
    skill_dir = _make_skill(tmp_path)
    result = runner.invoke(app, ["run", str(skill_dir), "--runner", "fake", "--runner", "nope"])
    assert result.exit_code == 2
    assert "unknown runner: nope" in plain(result.output)


def test_a_runner_list_in_config_is_honoured(tmp_path, monkeypatch):
    # The keyed runner in the list trips preflight, proving the list reached
    # the CLI and every runner in it is constructed.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    skill_dir = _make_skill(tmp_path)
    config = tmp_path / "skill-lens.toml"
    config.write_text('default_runner = ["fake", "pydantic-ai"]\n', encoding="utf-8")
    result = runner.invoke(app, ["run", str(skill_dir), "--config", str(config)])
    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in result.output


def test_the_runner_flag_replaces_the_config_list_rather_than_appending(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    skill_dir = _make_skill(tmp_path)
    config = tmp_path / "skill-lens.toml"
    config.write_text('default_runner = ["fake", "pydantic-ai"]\n', encoding="utf-8")
    result = runner.invoke(
        app, ["run", str(skill_dir), "--config", str(config), "--runner", "fake"]
    )
    assert result.exit_code in (0, 1)  # a gate verdict, not a preflight refusal
    assert "OPENAI_API_KEY" not in result.output


def test_every_case_runs_through_every_runner_and_each_outcome_names_its_runner(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cli_module, "_RUNNER_NAMES", (*cli_module._RUNNER_NAMES, "fake-2"))
    original_build_runner = cli_module._build_runner

    def _build_runner(name, settings, model_name):
        if name == "fake-2":
            return SecondFake()
        return original_build_runner(name, settings, model_name)

    monkeypatch.setattr(cli_module, "_build_runner", _build_runner)
    skill_dir = _make_skill(tmp_path)
    out = tmp_path / "report.json"
    result = runner.invoke(
        app,
        [
            "run",
            str(skill_dir),
            "--runner",
            "fake",
            "--runner",
            "fake-2",
            "--json-output",
            str(out),
            "--min-pass-rate",
            "0",
        ],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(out.read_text(encoding="utf-8"))
    outcomes = report["outcomes"]
    assert len(outcomes) == 2  # one case x two runners
    assert sorted(o["runner"] for o in outcomes) == ["fake", "fake-2"]
    assert {o["case_name"] for o in outcomes} == {"mentions the skill"}


def test_the_run_plan_multiplies_by_the_runner_count(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-key-for-parsing")
    _make_skill(tmp_path, cases=None)
    result = runner.invoke(
        app,
        [
            "run",
            str(tmp_path),
            "--runner",
            "pydantic-ai",
            "--runner",
            "langchain",
            "--baseline",
            "none",
            "--repeat",
            "2",
        ],
    )
    assert "Plan: up to 2 arm(s) x 2 repeat(s) x 2 runner(s) x 0 case(s) = 0 runs" in plain(
        result.stdout
    )


def test_a_single_runner_plan_line_still_states_the_runner_factor(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-key-for-parsing")
    _make_skill(tmp_path, cases=None)
    result = runner.invoke(app, ["run", str(tmp_path), "--runner", "pydantic-ai"])
    assert "x 1 runner(s) x" in plain(result.stdout)


def _product_config(tmp_path, name="copilot", judge: str | None = None) -> Path:
    command = [sys.executable, str(FAKE_PRODUCT), "-p", "{prompt}"]
    # A key after a [table] header would belong to that table, so the judge
    # line -- when there is one -- goes first.
    prefix = f'judge = "{judge}"\n' if judge is not None else ""
    body = f"{prefix}[runners.{name}]\ncommand = {command!r}\n".replace("'", '"')
    path = tmp_path / "skill-lens.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_product_runner_runs_the_case_and_names_the_product(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(PRODUCT_FIXTURES / "copilot-trigger.jsonl"))
    skill_dir = _make_skill(tmp_path, cases=PRODUCT_CASES_YAML)
    config = _product_config(tmp_path)
    result = runner.invoke(
        app, ["run", str(skill_dir), "--runner", "copilot", "--config", str(config)]
    )
    assert result.exit_code == 0, result.output
    # `command` names the interpreter, not `copilot`, so the preset's
    # `--version` probe is skipped and the line carries no version.
    assert "product copilot (" in result.output
    assert "product copilot Python" not in result.output
    assert "permission prompts disabled" in result.output
    assert "Plan: up to 1 arm(s) x 1 repeat(s) x 1 runner(s) x 1 case(s) = 1 runs" in result.output
    assert "pdf :: pongs (copilot)" in result.output


def test_a_product_that_is_not_installed_is_exit_2_before_any_case(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # nothing on it
    skill_dir = _make_skill(tmp_path, cases=PRODUCT_CASES_YAML)
    result = runner.invoke(app, ["run", str(skill_dir), "--runner", "copilot"])
    assert result.exit_code == 2
    assert "runner copilot: 'copilot' is not on PATH" in plain(result.output)


def test_cli_without_a_command_table_is_exit_2_naming_the_key(tmp_path):
    skill_dir = _make_skill(tmp_path, cases=PRODUCT_CASES_YAML)
    result = runner.invoke(app, ["run", str(skill_dir), "--runner", "cli"])
    assert result.exit_code == 2
    assert "[runners.cli] command" in plain(result.output)


def test_a_case_with_mock_tools_under_a_wrapped_product_is_exit_2(tmp_path, monkeypatch):
    # `command` names the interpreter, not `copilot`: a wrapper is not known
    # to take the MCP config flag, so `tools:` is refused before any case.
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(PRODUCT_FIXTURES / "copilot-trigger.jsonl"))
    skill_dir = _make_skill(tmp_path, cases=TOOLS_CASES_YAML)
    config = _product_config(tmp_path)
    result = runner.invoke(
        app, ["run", str(skill_dir), "--runner", "copilot", "--config", str(config)]
    )
    assert result.exit_code == 2
    assert "declares tools:" in plain(result.output)
    assert "[runners.copilot] command names another executable" in plain(result.output)


BRIDGED_CASES_YAML = """cases:
  - name: looks the order up
    task: What is the status of order A-17?
    tools:
      - name: lookup_order
        description: Look up an order.
        parameters:
          order_id: string
        returns: '{"order_id": "A-17", "status": "shipped"}'
      - name: cancel_order
        parameters:
          order_id: string
        returns: cancelled
    trajectory:
      called: [lookup_order]
      forbidden: [cancel_order]
    assertions:
      - kind: contains
        value: shipped
"""


@pytest.mark.skipif(os.name == "nt", reason="puts a shell shim on PATH")
def test_a_case_with_mock_tools_runs_through_the_bridge_under_a_preset(tmp_path, monkeypatch):
    # The preset's own argv reaches the fake through a `claude` shim on PATH,
    # so `Config.product` keeps the bridge (same executable), preflight
    # probes it, and the run hands the product the config -- end to end.
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    shim = shim_dir / "claude"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_PRODUCT}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(PRODUCT_FIXTURES / "claude-code-mcp.jsonl"))
    monkeypatch.setenv("FAKE_PRODUCT_MCP_CALL", "lookup_order")
    skill_dir = _make_skill(tmp_path, cases=BRIDGED_CASES_YAML)
    out = tmp_path / "report.json"
    result = runner.invoke(
        app, ["run", str(skill_dir), "--runner", "claude-code", "--json-output", str(out)]
    )
    assert result.exit_code == 0, result.output
    report = json.loads(out.read_text(encoding="utf-8"))
    (outcome,) = report["outcomes"]
    assert outcome["status"] == "passed"
    assert outcome["output"] == "Order A-17 is shipped."
    # The trace named the tool `mcp__skill-lens__lookup_order`; the trajectory
    # read it under the name the case declared, or `called:` could not pass.
    scores = {score["evaluator"]: score for score in outcome["scores"]}
    assert scores["trajectory"]["passed"] is True
    assert scores["assertion"]["passed"] is True
    (product,) = report["products"]
    assert product["name"] == "claude-code" and product["version"] == "fake 1.2.3"


def test_model_with_nothing_that_reads_it_is_a_user_error(tmp_path):
    skill_dir = _make_skill(tmp_path)
    result = runner.invoke(app, ["run", str(skill_dir), "--runner", "fake", "--model", "gpt-5.2"])
    assert result.exit_code == 2
    assert "--model is read by pydantic-ai and langchain only" in plain(result.output)
    assert "[runners.<name>] args" in plain(result.output)


def test_model_is_allowed_when_a_keyed_judge_falls_back_to_it(tmp_path):
    skill_dir = _make_skill(tmp_path)
    (tmp_path / "skill-lens.toml").write_text('judge = "pydantic-ai"\n', encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "run",
            str(skill_dir),
            "--runner",
            "fake",
            "--model",
            "openai:gpt-4o-mini",
            "--config",
            str(tmp_path / "skill-lens.toml"),
        ],
        env={"OPENAI_API_KEY": "k"},
    )
    assert result.exit_code == 0, result.output  # no judge: block, so nothing is spent


def test_judge_model_with_a_judge_that_does_not_read_it_is_a_user_error(tmp_path):
    skill_dir = _make_skill(tmp_path)
    result = runner.invoke(app, ["run", str(skill_dir), "--judge-model", "gpt-5.2"])
    assert result.exit_code == 2
    assert '--judge-model is read by judge = "pydantic-ai" or "langchain" only' in plain(
        result.output
    )


def test_a_product_runner_and_a_keyed_runner_share_one_invocation(tmp_path, monkeypatch):
    # --model reaches the keyed runner; the product ignores it. Preflight for
    # the keyed runner (no key) stops the run first, which is enough to prove
    # both names resolve.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    skill_dir = _make_skill(tmp_path, cases=PRODUCT_CASES_YAML)
    config = _product_config(tmp_path)
    result = runner.invoke(
        app,
        [
            "run",
            str(skill_dir),
            "--runner",
            "copilot",
            "--runner",
            "pydantic-ai",
            "--model",
            "openai:gpt-4o-mini",
            "--config",
            str(config),
        ],
    )
    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in result.output


JUDGED_CASES_YAML = """cases:
  - name: pongs
    task: Please ping.
    judge:
      rubric:
        - greets by name
        - asks a question
"""


def test_a_product_judge_grades_a_rubric(tmp_path, monkeypatch):
    # `JudgeEvaluator` numbers a two-entry rubric r1/r2 positionally; the
    # shared fixture is scripted with c1/c2 (test_product_judge.py builds its
    # JudgeRequest directly with those ids, bypassing that numbering), so a
    # private copy with the ids renamed is what makes this a real grade
    # rather than an id-mismatch error. The shared fixture itself stays
    # untouched -- test_product_judge.py's unit tests pin c1/c2.
    fixture_text = (PRODUCT_FIXTURES / "claude-code-verdict.jsonl").read_text(encoding="utf-8")
    verdict_path = tmp_path / "verdict-r.jsonl"
    verdict_path.write_text(
        fixture_text.replace('\\"c1\\"', '\\"r1\\"').replace('\\"c2\\"', '\\"r2\\"'),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(verdict_path))
    skill_dir = _make_skill(tmp_path, cases=JUDGED_CASES_YAML)
    config = _product_config(tmp_path, name="claude-code", judge="claude-code")
    # The same fake serves runner and judge here: the runner reads a verdict
    # trace as its output (fine -- assertions are not what this test checks)
    # and the judge reads the two-check verdict.
    result = runner.invoke(
        app, ["run", str(skill_dir), "--runner", "claude-code", "--config", str(config)]
    )
    assert result.exit_code == 1, result.output  # r2 failed in the fixture verdict
    assert "0 errored" in result.output  # the case failed; it did not error
    assert "r2: no name given" in result.output  # the check's evidence line the console renders
    assert "product claude-code" in result.output


def test_a_product_judge_needs_no_key_and_no_judge_model(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(PRODUCT_FIXTURES / "claude-code-verdict.jsonl"))
    skill_dir = _make_skill(tmp_path)  # no judge: block, so the judge is never called
    config = _product_config(tmp_path, name="claude-code", judge="claude-code")
    result = runner.invoke(app, ["run", str(skill_dir), "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert "OPENAI_API_KEY" not in result.output


def test_judge_model_with_a_product_judge_is_a_user_error(tmp_path):
    skill_dir = _make_skill(tmp_path)
    (tmp_path / "skill-lens.toml").write_text('judge = "copilot"\n', encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "run",
            str(skill_dir),
            "--judge-model",
            "x",
            "--config",
            str(tmp_path / "skill-lens.toml"),
        ],
    )
    assert result.exit_code == 2
    assert "--judge-model is read by" in plain(result.output)


def test_a_product_judge_that_is_not_installed_is_exit_2_before_any_case(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    skill_dir = _make_skill(tmp_path)
    (tmp_path / "skill-lens.toml").write_text('judge = "copilot"\n', encoding="utf-8")
    result = runner.invoke(
        app, ["run", str(skill_dir), "--config", str(tmp_path / "skill-lens.toml")]
    )
    assert result.exit_code == 2
    assert "judge copilot: 'copilot' is not on PATH" in plain(result.output)


def test_model_with_a_product_judge_and_the_fake_runner_is_a_user_error(tmp_path):
    # --model is rejected before run_evals ever preflights the judge, so this
    # is a pure argument-parsing error: no copilot executable needs to exist.
    skill_dir = _make_skill(tmp_path)
    (tmp_path / "skill-lens.toml").write_text('judge = "copilot"\n', encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "run",
            str(skill_dir),
            "--runner",
            "fake",
            "--model",
            "x",
            "--config",
            str(tmp_path / "skill-lens.toml"),
        ],
    )
    assert result.exit_code == 2
    assert "--model is read by pydantic-ai and langchain only" in plain(result.output)
