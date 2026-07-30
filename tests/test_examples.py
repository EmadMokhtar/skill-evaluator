from pathlib import Path

from typer.testing import CliRunner

from skill_eval.cli import app

runner = CliRunner()
EXAMPLES = Path(__file__).parent.parent / "examples"


def test_examples_directory_exists():
    assert EXAMPLES.is_dir()


def test_example_skills_are_discoverable():
    result = runner.invoke(app, ["list", str(EXAMPLES)])
    assert result.exit_code == 0
    assert "greeting" in result.stdout


def test_examples_run_green_end_to_end():
    result = runner.invoke(app, ["run", str(EXAMPLES)])
    assert result.exit_code == 0, result.stdout
    assert "1 passed" in result.stdout
