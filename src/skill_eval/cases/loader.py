"""Discover and parse eval case YAML files."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from skill_eval.models import EvalCase, Skill
from skill_eval.yaml_loading import safe_load

EVALS_DIRNAME = "evals"
EVAL_SUFFIX = ".eval.yaml"


class CaseParseError(Exception):
    """Raised when an eval file is missing or cannot be parsed."""


def parse_cases_file(path: Path) -> list[EvalCase]:
    """Parse one YAML file into EvalCase models."""
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
        try:
            case = EvalCase.model_validate(raw)
        except ValidationError as exc:
            fields = ", ".join(str(e["loc"][0]) for e in exc.errors() if e["loc"])
            raise CaseParseError(f"{path}: case #{index + 1} invalid ({fields}): {exc}") from exc
        _validate_cross_references(path, case)
        cases.append(case)
    return cases


def _validate_cross_references(path: Path, case: EvalCase) -> None:
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
    if case.trajectory is None:
        return
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
        cases.extend(parse_cases_file(path))
    return cases
