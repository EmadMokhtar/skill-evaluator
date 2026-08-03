"""Discover and parse eval case YAML files."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from skill_eval.models import EvalCase, Skill
from skill_eval.runners.tools import skill_tool_name
from skill_eval.yaml_loading import safe_load

EVALS_DIRNAME = "evals"
EVAL_SUFFIX = ".eval.yaml"

# The placeholder `skill-eval init` writes into every field the author has to
# fill in. Living here rather than in scaffold.py makes it the *loader's*
# guarantee: a hand-written stub is refused exactly like a generated one.
UNFILLED_SENTINEL = "TODO(skill-eval)"


class CaseParseError(Exception):
    """Raised when an eval file is missing or cannot be parsed."""


def _reject_unfilled(path: Path, index: int, raw: object, trail: str = "") -> None:
    """Refuse a case still carrying scaffold placeholders.

    Runs before schema validation so the message names the field to fill in
    rather than complaining about the type of a value nobody meant to keep.
    An unfilled scaffold says something about the author's progress, not about
    the skill, so it aborts the run as an authoring error instead of scoring
    as a failure.
    """
    if isinstance(raw, str):
        if UNFILLED_SENTINEL in raw:
            raise CaseParseError(
                f"{path}: case #{index + 1} still has the scaffold placeholder "
                f"{UNFILLED_SENTINEL} at {trail or 'case'}. Fill it in -- an "
                f"unfinished eval cannot say anything about the skill."
            )
    elif isinstance(raw, dict):
        for key, value in raw.items():
            _reject_unfilled(path, index, value, f"{trail}.{key}" if trail else str(key))
    elif isinstance(raw, list):
        for position, value in enumerate(raw):
            _reject_unfilled(path, index, value, f"{trail}[{position}]")


def parse_cases_file(path: Path, skill: Skill | None = None) -> list[EvalCase]:
    """Parse one YAML file into EvalCase models.

    `skill` is optional because a case file can be parsed on its own; it is
    only needed for the checks that depend on what the skill would be offered
    as (see `_validate_cross_references`).
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CaseParseError(f"cannot read {path}: {exc}") from exc
    try:
        data = safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise CaseParseError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict) or "cases" not in data:
        raise CaseParseError(f"{path}: expected a top-level 'cases' list")
    raw_cases = data["cases"]
    if not isinstance(raw_cases, list):
        raise CaseParseError(f"{path}: 'cases' must be a list")
    cases: list[EvalCase] = []
    for index, raw in enumerate(raw_cases):
        _reject_unfilled(path, index, raw)
        try:
            case = EvalCase.model_validate(raw)
        except ValidationError as exc:
            fields = ", ".join(str(e["loc"][0]) for e in exc.errors() if e["loc"])
            raise CaseParseError(f"{path}: case #{index + 1} invalid ({fields}): {exc}") from exc
        _validate_cross_references(path, case, skill)
        cases.append(case)
    return cases


def _validate_cross_references(path: Path, case: EvalCase, skill: Skill | None = None) -> None:
    """Catch case-file mistakes that pass schema validation but can never be
    honoured at run time: they are authoring errors, not signals about the
    skill under test, and must abort the run rather than score as a failure.
    """
    seen: set[str] = set()
    for tool in case.tools:
        if tool.name in seen:
            raise CaseParseError(
                f"{path}: case {case.name!r} declares tool {tool.name!r} more than once"
            )
        seen.add(tool.name)

    declared = {tool.name for tool in case.tools}

    if case.judge is not None:
        if not case.judge.rubric:
            raise CaseParseError(
                f"{path}: case {case.name!r} declares a judge block with an empty rubric. "
                f"Give the judge something to check, or remove the block -- an "
                f"unchecked rubric would score as a pass nobody verified."
            )
        for position, entry in enumerate(case.judge.rubric, start=1):
            if not entry.strip():
                raise CaseParseError(
                    f"{path}: case {case.name!r} declares a judge block whose rubric "
                    f"entry {position} is blank. Give the judge something to check, or "
                    f"remove the entry -- a check that verifies nothing would score as "
                    f"a pass nobody verified."
                )

    if case.mode == "offered" and skill is not None:
        offered = skill_tool_name(skill.name)
        if offered in declared:
            raise CaseParseError(
                f"{path}: case {case.name!r} declares a tool named {offered!r}, which "
                f"collides with the name skill {skill.name!r} is offered under in "
                f"mode: offered. Rename the case's tool."
            )

    if case.trajectory is None:
        return

    if case.trajectory.skill_triggered is not None and case.mode != "offered":
        raise CaseParseError(
            f"{path}: case {case.name!r} sets trajectory.skill_triggered but runs in "
            f"mode {case.mode!r}. A loaded skill is always in force, so the check "
            f"could never be false -- set 'mode: offered'."
        )

    for field_name, names in (
        ("called", case.trajectory.called),
        ("forbidden", case.trajectory.forbidden),
        ("order", case.trajectory.order),
    ):
        for name in names:
            if name not in declared:
                raise CaseParseError(
                    f"{path}: case {case.name!r} trajectory.{field_name} names "
                    f"{name!r}, which is not declared in this case's tools"
                )


def _discover_paths(skill: Skill) -> list[Path]:
    """Find eval files beside a skill: an evals/ dir, then *.eval.yaml."""
    evals_dir = skill.path / EVALS_DIRNAME
    if evals_dir.is_dir():
        return sorted(p for p in evals_dir.iterdir() if p.suffix in {".yaml", ".yml"})
    return sorted(skill.path.glob(f"*{EVAL_SUFFIX}"))


def load_cases_for_skill(skill: Skill, evals_path: Path | None = None) -> list[EvalCase]:
    """Load a skill's eval cases, honouring an explicit override path."""
    if evals_path is not None:
        evals_path = Path(evals_path)
        if not evals_path.exists():
            raise CaseParseError(f"evals path does not exist: {evals_path}")
        paths = (
            sorted(p for p in evals_path.iterdir() if p.suffix in {".yaml", ".yml"})
            if evals_path.is_dir()
            else [evals_path]
        )
    else:
        paths = _discover_paths(skill)
    cases: list[EvalCase] = []
    for path in paths:
        cases.extend(parse_cases_file(path, skill))
    return cases
