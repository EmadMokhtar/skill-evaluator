"""The skill-eval command line interface."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated

import typer

from skill_eval import __version__
from skill_eval.cases.loader import (
    EVAL_SUFFIX,
    EVALS_DIRNAME,
    UNFILLED_SENTINEL,
    CaseParseError,
    load_cases_for_skill,
)
from skill_eval.config import ConfigError, load_config
from skill_eval.evaluators.assertion import InvalidAssertionValue, UnknownAssertionKind
from skill_eval.gating import EXIT_OK, evaluate_gate
from skill_eval.judges.fake import FakeJudge
from skill_eval.judges.pydantic_ai import PydanticAIJudge
from skill_eval.orchestrator import run_evals
from skill_eval.reporters.console import render_console
from skill_eval.reporters.json_reporter import render_json
from skill_eval.runners.fake import FakeRunner
from skill_eval.runners.preflight import MissingAPIKey, check_api_key
from skill_eval.runners.pydantic_ai import PydanticAIRunner, RunnerDependencyError
from skill_eval.scaffold import render_scaffold
from skill_eval.skills.loader import SKILL_FILENAME, SkillParseError, load_skills, parse_skill_file

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


def _require_a_model(flag: str, model: str) -> None:
    """Reject a blank model id before it can reach a provider.

    A blank id has no provider prefix, so `check_api_key` finds nothing to
    check and waves it through; the run then dies deep inside the adapter as
    `UserError: Unknown model:` and is reported as an *errored case* -- exit 1,
    the code that means "the run broke", when the truth is a mistyped flag.
    Exit codes are the CI contract, so a user error has to surface as 2 here.
    Checked on the resolved value so a blank in `skill-eval.toml` is caught too,
    not only a blank on the command line.
    """
    if not model.strip():
        raise typer.BadParameter(f"{flag} is empty; name a model such as openai:gpt-4o-mini")


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
        str | None,
        typer.Option(help='Model id for the judge; judge = "..." in skill-eval.toml picks it.'),
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
            _require_a_model("--model", model_name)
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
            _require_a_model("--judge-model", resolved_judge_model)
            check_api_key(resolved_judge_model, os.environ)
            active_judge = judge_class(
                model=resolved_judge_model,
                temperature=settings.judge_temperature,
                retries=settings.retries,
                retry_backoff_seconds=settings.retry_backoff_seconds,
            )
        else:
            active_judge = judge_class()
        report = run_evals(skills, [active_runner], evals_path=evals, tag=tag, judge=active_judge)
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


def _eval_filename(name: str) -> str:
    """A safe file name for a skill's eval suite.

    The name comes from user-supplied frontmatter, so it is not automatically
    a safe path component: `name: ../../x` would otherwise write outside the
    directory init was pointed at.
    """
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "skill"
    return f"{safe}{EVAL_SUFFIX}"


@app.command()
def init(
    path: Annotated[Path, typer.Argument(help="A skill directory containing SKILL.md.")],
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing eval file.")
    ] = False,
) -> None:
    """Write a starter eval suite beside a skill."""
    skill_md = path / SKILL_FILENAME
    if not skill_md.is_file():
        typer.echo(f"no {SKILL_FILENAME} in {path}; point init at a skill directory")
        raise typer.Exit(code=2)
    try:
        skill = parse_skill_file(skill_md)
    except SkillParseError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc

    target = path / EVALS_DIRNAME / _eval_filename(skill.name)
    if target.exists() and not force:
        typer.echo(f"{target} already exists; pass --force to overwrite it")
        raise typer.Exit(code=2)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_scaffold(skill), encoding="utf-8")
    except OSError as exc:
        typer.echo(f"cannot write {target}: {exc}")
        raise typer.Exit(code=2) from exc

    typer.echo(f"Wrote {target}")
    typer.echo(f"Fill in every {UNFILLED_SENTINEL}, then run: skill-eval list {path}")
