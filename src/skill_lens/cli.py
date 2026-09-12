"""The skill-lens command line interface."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from skill_lens import __version__
from skill_lens.cases.loader import (
    UNFILLED_SENTINEL,
    CaseParseError,
    discover_eval_paths,
    load_cases_for_skill,
)
from skill_lens.comparison import build_delta
from skill_lens.config import ConfigError, load_config
from skill_lens.evaluators.assertion import InvalidAssertionValue, UnknownAssertionKind
from skill_lens.gating import EXIT_OK, evaluate_gate
from skill_lens.judges.fake import FakeJudge
from skill_lens.judges.langchain import LangChainJudge
from skill_lens.judges.pydantic_ai import PydanticAIJudge
from skill_lens.models import Skill
from skill_lens.orchestrator import run_evals
from skill_lens.reporters.console import render_console
from skill_lens.reporters.failure_context import OUTPUT_LIMIT
from skill_lens.reporters.json_reporter import render_json
from skill_lens.reporters.junit import render_junit
from skill_lens.reporters.markdown import render_markdown
from skill_lens.runners.base import RunnerDependencyError
from skill_lens.runners.fake import FakeRunner
from skill_lens.runners.langchain import LangChainRunner
from skill_lens.runners.preflight import MissingAPIKey, check_api_key
from skill_lens.runners.pydantic_ai import PydanticAIRunner
from skill_lens.scaffold import render_scaffold, scaffold_target
from skill_lens.skills.loader import SKILL_FILENAME, SkillParseError, load_skills, parse_skill_file
from skill_lens.workspace import WorkspaceLimits

app = typer.Typer(help="Run evaluations on Agent Skills (SKILL.md).", no_args_is_help=True)

_RUNNERS = {"fake": FakeRunner, "pydantic-ai": PydanticAIRunner, "langchain": LangChainRunner}
_JUDGES = {"fake": FakeJudge, "pydantic-ai": PydanticAIJudge, "langchain": LangChainJudge}

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
    Checked on the resolved value so a blank in `skill-lens.toml` is caught too,
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
    """skill-lens — evaluate Agent Skills."""


def _write_report(path: Path, text: str, label: str) -> str | None:
    """Write one report file. Returns an error message, or None on success."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        return f"Failed to write {label} report to {path}: {exc}"
    return None


@app.command()
def run(
    path: Annotated[Path, typer.Argument(help="A skill directory, or a directory of skills.")],
    evals: Annotated[Path | None, typer.Option(help="Explicit eval file or directory.")] = None,
    runner: Annotated[str | None, typer.Option(help="Runner to use.")] = None,
    model: Annotated[str | None, typer.Option(help="Model id, e.g. openai:gpt-4o-mini.")] = None,
    judge_model: Annotated[
        str | None,
        typer.Option(help='Model id for the judge; judge = "..." in skill-lens.toml picks it.'),
    ] = None,
    tag: Annotated[str | None, typer.Option(help="Only run cases with this tag.")] = None,
    case: Annotated[
        str | None,
        typer.Option(
            "--case",
            help="Only run cases whose name contains this text (case-insensitive).",
        ),
    ] = None,
    min_pass_rate: Annotated[float | None, typer.Option(help="Required pass rate.")] = None,
    json_output: Annotated[Path | None, typer.Option(help="Write a JSON report here.")] = None,
    config: Annotated[Path | None, typer.Option(help="Path to skill-lens.toml.")] = None,
    baseline: Annotated[
        str | None,
        typer.Option(help="Compare against a baseline: none (no skill) or previous."),
    ] = None,
    repeat: Annotated[int | None, typer.Option(help="Sample each arm this many times.")] = None,
    min_delta: Annotated[
        float | None,
        typer.Option(help="Required improvement over the baseline; needs --baseline."),
    ] = None,
    junit_output: Annotated[
        Path | None, typer.Option(help="Write a JUnit XML report here.")
    ] = None,
    markdown_output: Annotated[
        Path | None, typer.Option(help="Write a Markdown summary here.")
    ] = None,
    markdown_max_chars: Annotated[
        int | None,
        typer.Option(help="Truncate the Markdown summary to this many characters."),
    ] = None,
    concurrency: Annotated[int | None, typer.Option(help="Run this many cases at once.")] = None,
    keep_workspace: Annotated[
        bool | None,
        typer.Option(
            "--keep-workspace/--no-keep-workspace",
            help="Keep each case's temporary directory instead of deleting it.",
        ),
    ] = None,
    full_output: Annotated[
        bool | None,
        typer.Option(
            "--full-output/--no-full-output",
            help="Print a failing case's whole output instead of the first 500 characters.",
        ),
    ] = None,
) -> None:
    """Discover skills, run their eval cases, and gate on the results."""
    try:
        settings = load_config(path=config)
        skills = load_skills(path)
        baseline_kind = baseline if baseline is not None else settings.baseline
        if baseline_kind not in ("", "none", "previous"):
            raise typer.BadParameter(f"unknown baseline: {baseline_kind}")
        resolved_repeat = repeat if repeat is not None else settings.repeat
        if resolved_repeat < 1:
            raise typer.BadParameter("--repeat must be at least 1")
        resolved_concurrency = concurrency if concurrency is not None else settings.concurrency
        if resolved_concurrency < 1:
            raise typer.BadParameter("--concurrency must be at least 1")
        resolved_keep_workspace = (
            keep_workspace if keep_workspace is not None else settings.keep_workspace
        )
        resolved_full_output = full_output if full_output is not None else settings.full_output
        # None means "no cap" to every reporter.
        output_limit = None if resolved_full_output else OUTPUT_LIMIT
        workspace_limits = WorkspaceLimits(
            max_file_bytes=settings.max_file_bytes,
            max_files=settings.max_files,
            max_total_bytes=settings.max_total_bytes,
        )
        if markdown_max_chars is not None:
            if markdown_max_chars < 1:
                raise typer.BadParameter("--markdown-max-chars must be at least 1")
            if markdown_output is None:
                # A flag that silently does nothing hides a mistake rather than
                # reporting it -- the same reason --min-delta requires --baseline.
                raise typer.BadParameter("--markdown-max-chars requires --markdown-output")
        resolved_min_delta = min_delta if min_delta is not None else settings.min_delta
        # Checked against resolved values so a baseline in skill-lens.toml
        # satisfies a --min-delta on the command line. A gate that verified
        # nothing must never report a pass, so this is an error, not a warning.
        if resolved_min_delta is not None and not baseline_kind:
            raise typer.BadParameter("--min-delta requires --baseline none or --baseline previous")
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
        if getattr(runner_class, "needs_api_key", False):
            # A ceiling, not a forecast. The tag and case filters are applied
            # here because `run_evals` applies them too and ignoring them can
            # overstate the total wildly -- but the baseline arm is also
            # dropped per-case for `mode: offered` under --baseline none, and
            # per-skill when a previous version cannot be resolved. Both only
            # ever *reduce* the count, and reproducing them here would mean
            # duplicating the orchestrator's discovery (and its git calls)
            # just to print a line.
            arms = 2 if baseline_kind else 1
            case_count = 0
            for candidate_skill in skills:
                cases = load_cases_for_skill(candidate_skill, evals_path=evals)
                if tag is not None:
                    cases = [c for c in cases if tag in c.tags]
                if case is not None:
                    needle = case.casefold()
                    cases = [c for c in cases if needle in c.name.casefold()]
                case_count += len(cases)
            typer.echo(
                f"Plan: up to {arms} arm(s) x {resolved_repeat} repeat(s) x "
                f"{case_count} case(s) = {arms * resolved_repeat * case_count} runs"
            )
        report = run_evals(
            skills,
            [active_runner],
            evals_path=evals,
            tag=tag,
            case_filter=case,
            judge=active_judge,
            baseline=baseline_kind or None,
            repeat=resolved_repeat,
            concurrency=resolved_concurrency,
            keep_workspace=resolved_keep_workspace,
            workspace_limits=workspace_limits,
        )
    except _AUTHORING_ERRORS as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc

    delta = build_delta(report)
    gate = evaluate_gate(
        report,
        min_pass_rate=min_pass_rate if min_pass_rate is not None else settings.min_pass_rate,
        fail_on_error=settings.fail_on_error,
        per_skill_min=settings.per_skill_min,
        min_delta=resolved_min_delta,
        delta=delta,
    )

    typer.echo(render_console(report, gate=gate, delta=delta, output_limit=output_limit))
    # One loop over every requested report. A write failure escalates to exit 2
    # only when the gate itself passed -- exit codes are the CI contract, and an
    # already-red gate must stay visible rather than being masked by an
    # unrelated write problem.
    writes = (
        (json_output, "JSON", lambda: render_json(report, gate=gate, delta=delta)),
        (
            junit_output,
            "JUnit",
            lambda: render_junit(report, gate=gate, delta=delta, output_limit=output_limit),
        ),
        (
            markdown_output,
            "Markdown",
            lambda: render_markdown(
                report,
                gate=gate,
                delta=delta,
                max_chars=markdown_max_chars,
                output_limit=output_limit,
            ),
        ),
    )
    write_failed = False
    for target, label, render in writes:
        if target is None:
            continue
        error = _write_report(target, render(), label)
        if error is not None:
            typer.echo(error)
            write_failed = True
    if write_failed and gate.exit_code == EXIT_OK:
        raise typer.Exit(code=2)

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


def _write_scaffold(target: Path, skill: Skill) -> None:
    """Write one scaffold, or exit 2 naming the file."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_scaffold(skill), encoding="utf-8")
    except OSError as exc:
        typer.echo(f"cannot write {target}: {exc}")
        raise typer.Exit(code=2) from exc


def _init_one(path: Path, force: bool) -> None:
    """The original `init`: exactly one skill directory, `--force` allowed."""
    try:
        skill = parse_skill_file(path / SKILL_FILENAME)
    except SkillParseError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc

    target = scaffold_target(skill)
    if target.exists() and not force:
        typer.echo(f"{target} already exists; pass --force to overwrite it")
        raise typer.Exit(code=2)
    _write_scaffold(target, skill)
    typer.echo(f"Wrote {target}")
    typer.echo(f"Fill in every {UNFILLED_SENTINEL}, then run: skill-lens list {path}")


def _init_many(path: Path) -> None:
    """Batch mode: scaffold every skill under `path` that has no suite.

    Skips any skill with an eval file already -- batch init exists to fill in
    the *missing* suites and must never rewrite one that is there to build on.
    """
    try:
        skills = load_skills(path) if path.is_dir() else []
    except SkillParseError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc
    if not skills:
        typer.echo(
            f"no {SKILL_FILENAME} under {path}; point init at a skill directory "
            "or a directory of skill directories"
        )
        raise typer.Exit(code=2)

    wrote = 0
    for skill in skills:
        existing = discover_eval_paths(skill)
        if existing:
            typer.echo(f"Skipped {skill.name}: already has {len(existing)} eval file(s)")
            continue
        target = scaffold_target(skill)
        _write_scaffold(target, skill)
        typer.echo(f"Wrote {target}")
        wrote += 1
    if wrote:
        typer.echo(f"Fill in every {UNFILLED_SENTINEL}, then run: skill-lens list {path}")
    else:
        typer.echo(f"Nothing to do: every skill under {path} already has an eval suite")


@app.command()
def init(
    path: Annotated[
        Path, typer.Argument(help="A skill directory, or a directory of skill directories.")
    ],
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing eval file (one skill only).")
    ] = False,
) -> None:
    """Write a starter eval suite beside a skill, or beside every skill that has none."""
    if (path / SKILL_FILENAME).is_file():
        _init_one(path, force)
        return
    if force:
        # Rewriting every suite in a repository must never be one flag away.
        typer.echo("--force applies to one skill; point init at that skill's directory")
        raise typer.Exit(code=2)
    _init_many(path)
