"""The skill-eval command line interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from skill_eval import __version__
from skill_eval.cases.loader import CaseParseError, load_cases_for_skill
from skill_eval.config import ConfigError, load_config
from skill_eval.evaluators.assertion import InvalidAssertionValue, UnknownAssertionKind
from skill_eval.gating import evaluate_gate
from skill_eval.orchestrator import run_evals
from skill_eval.reporters.console import render_console
from skill_eval.reporters.json_reporter import render_json
from skill_eval.runners.fake import FakeRunner
from skill_eval.skills.loader import SkillParseError, load_skills

app = typer.Typer(help="Run evaluations on Agent Skills (SKILL.md).", no_args_is_help=True)

_RUNNERS = {"fake": FakeRunner}

# Authoring errors: bad skill/case/config files, or a malformed assertion in an
# eval YAML (Tasks 6/7 decided the latter aborts the whole run rather than
# being silently swallowed as a failed case). All of these get the same clean
# "print the message, exit 2" treatment instead of a raw traceback.
_AUTHORING_ERRORS = (
    SkillParseError,
    CaseParseError,
    ConfigError,
    UnknownAssertionKind,
    InvalidAssertionValue,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version_callback, is_eager=True)
    ] = False,
) -> None:
    """skill-eval — evaluate Agent Skills."""


@app.command()
def run(
    path: Annotated[Path, typer.Argument(help="A skill directory, or a directory of skills.")],
    evals: Annotated[Path | None, typer.Option(help="Explicit eval file or directory.")] = None,
    runner: Annotated[str | None, typer.Option(help="Runner to use.")] = None,
    tag: Annotated[str | None, typer.Option(help="Only run cases with this tag.")] = None,
    min_pass_rate: Annotated[float | None, typer.Option(help="Required pass rate.")] = None,
    json_output: Annotated[Path | None, typer.Option(help="Write a JSON report here.")] = None,
    config: Annotated[Path | None, typer.Option(help="Path to skill-eval.toml.")] = None,
) -> None:
    """Discover skills, run their eval cases, and gate on the results."""
    try:
        settings = load_config(path=config)
        skills = load_skills(path)
        runner_name = runner if runner is not None else settings.default_runner
        if runner_name not in _RUNNERS:
            raise typer.BadParameter(f"unknown runner: {runner_name}")
        report = run_evals(skills, [_RUNNERS[runner_name]()], evals_path=evals, tag=tag)
    except _AUTHORING_ERRORS as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc

    gate = evaluate_gate(
        report,
        min_pass_rate=min_pass_rate if min_pass_rate is not None else settings.min_pass_rate,
        fail_on_error=settings.fail_on_error,
        per_skill_min=settings.per_skill_min,
    )

    typer.echo(render_console(report, gate=gate))
    if json_output is not None:
        try:
            json_output.parent.mkdir(parents=True, exist_ok=True)
            json_output.write_text(render_json(report, gate=gate))
        except OSError as exc:
            typer.echo(f"Failed to write JSON report to {json_output}: {exc}")
            raise typer.Exit(code=2) from exc

    raise typer.Exit(code=gate.exit_code)


@app.command("list")
def list_skills(
    path: Annotated[Path, typer.Argument(help="A skill directory, or a directory of skills.")],
    evals: Annotated[Path | None, typer.Option(help="Explicit eval file or directory.")] = None,
) -> None:
    """Show the skills that would be evaluated and how many cases each has."""
    try:
        skills = load_skills(path)
        for skill in skills:
            count = len(load_cases_for_skill(skill, evals_path=evals))
            typer.echo(f"{skill.name}\t{count} case(s)\t{skill.path}")
    except (SkillParseError, CaseParseError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc
