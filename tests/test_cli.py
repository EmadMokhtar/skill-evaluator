import json

from typer.testing import CliRunner

from skill_eval.cli import app

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
    _make_skill(tmp_path, cases=None)
    result = runner.invoke(app, ["run", str(tmp_path)])
    assert "Skipped" in result.stdout
    assert result.exit_code == 0


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
