"""The skill-eval command line interface."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from skill_eval import __version__
from skill_eval.cases.loader import CaseParseError, load_cases_for_skill
from skill_eval.config import ConfigError, load_config
from skill_eval.evaluators.assertion import (
    AssertionEvaluator,
    InvalidAssertionValue,
    UnknownAssertionKind,
)
from skill_eval.evaluators.budget import BudgetEvaluator
from skill_eval.evaluators.judge import JudgeEvaluator
from skill_eval.evaluators.trajectory import TrajectoryEvaluator
from skill_eval.gating import EXIT_OK, evaluate_gate
from skill_eval.judges.fake import FakeJudge
from skill_eval.judges.pydantic_ai import PydanticAIJudge
from skill_eval.orchestrator import run_evals
from skill_eval.reporters.console import render_console
from skill_eval.reporters.json_reporter import render_json
from skill_eval.runners.fake import FakeRunner
from skill_eval.runners.preflight import MissingAPIKey, check_api_key
from skill_eval.runners.pydantic_ai import PydanticAIRunner, RunnerDependencyError
from skill_eval.skills.loader import SkillParseError, load_skills

app = typer.Typer(help="Run evaluations on Agent Skills (SKILL.md).", no_args_is_help=True)

_RUNNERS = {"fake": FakeRunner, "pydantic-ai": PydanticAIRunner}
_JUDGES = {"fake": FakeJudge, "pydantic-ai": PydanticAIJudge}

# Authoring errors: bad skill/case/config files, or a malformed assertion in an
# eval YAML (Tasks 6/7 decided the latter aborts the whole run rather than
# being silently swallowed as a failed case). Missing keys and missing optional
# extras are user errors too -- all get the same clean "print the message, exit
# 2" treatment instead of a raw traceback.
_AUTHORING_ERRORS = (
    SkillParseError,
    CaseParseError,
    ConfigError,
    UnknownAssertionKind,
    InvalidAssertionValue,
    MissingAPIKey,
    RunnerDependencyError,
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
    model: Annotated[str | None, typer.Option(help="Model id, e.g. openai:gpt-4o-mini.")] = None,
    judge_model: Annotated[
        str | None, typer.Option(help="Model id for the LLM judge; defaults to --model.")
    ] = None,
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
        runner_class = _RUNNERS[runner_name]
        model_name = model if model is not None else settings.model
        if getattr(runner_class, "needs_api_key", False):
            check_api_key(model_name, os.environ)
            active_runner = runner_class(
                model=model_name,
                temperature=settings.temperature,
                retries=settings.retries,
                retry_backoff_seconds=settings.retry_backoff_seconds,
            )
        else:
            active_runner = runner_class()
        judge_name = settings.judge
        if judge_name not in _JUDGES:
            raise typer.BadParameter(f"unknown judge: {judge_name}")
        judge_class = _JUDGES[judge_name]
        # An empty judge_model means "grade with the same model you run with",
        # so a project opting into real judging only has to name one model.
        resolved_judge_model = (
            judge_model if judge_model is not None else (settings.judge_model or model_name)
        )
        if getattr(judge_class, "needs_api_key", False):
            check_api_key(resolved_judge_model, os.environ)
            active_judge = judge_class(
                model=resolved_judge_model,
                temperature=settings.temperature,
                retries=settings.retries,
                retry_backoff_seconds=settings.retry_backoff_seconds,
            )
        else:
            active_judge = judge_class()
        evaluators = [
            AssertionEvaluator(),
            TrajectoryEvaluator(),
            BudgetEvaluator(),
            JudgeEvaluator(active_judge),
        ]
        report = run_evals(
            skills, [active_runner], evals_path=evals, tag=tag, evaluators=evaluators
        )
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
            json_output.write_text(render_json(report, gate=gate), encoding="utf-8")
        except OSError as exc:
            typer.echo(f"Failed to write JSON report to {json_output}: {exc}")
            # Exit codes are the CI contract: a gate that already failed (1)
            # must stay visible rather than being masked by an unrelated
            # write problem escalating to 2. Only elevate to 2 when the gate
            # itself passed, so the write failure doesn't silently look like
            # success.
            if gate.exit_code == EXIT_OK:
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
