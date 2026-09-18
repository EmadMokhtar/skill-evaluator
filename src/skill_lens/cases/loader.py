"""Discover and parse eval case YAML files."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import ValidationError

from skill_lens.cases.checks import UNFILLED_SENTINEL, check_tool_schema, find_unfilled
from skill_lens.cases.tool_libraries import (
    EMPTY_LIBRARY,
    TOOL_LIBRARIES_KEY,
    ToolLibrary,
    ToolLibraryError,
    load_tool_libraries,
)
from skill_lens.models import EvalCase, Skill, ToolRef
from skill_lens.runners.tools import BUILTIN_TOOL_NAMES, skill_tool_name
from skill_lens.workspace import PathRefused, check_relative_path
from skill_lens.yaml_loading import safe_load

EVALS_DIRNAME = "evals"
EVAL_SUFFIX = ".eval.yaml"


class CaseParseError(Exception):
    """Raised when an eval file is missing or cannot be parsed."""


def _reject_unfilled(path: Path, index: int, raw: object) -> None:
    """Refuse a case still carrying scaffold placeholders.

    Runs before schema validation so the message names the field to fill in
    rather than complaining about the type of a value nobody meant to keep.
    An unfilled scaffold says something about the author's progress, not
    about the skill, so it aborts the run as an authoring error instead of
    scoring as a failure. The walk itself is `checks.find_unfilled` -- keys as
    well as values, cycle-safe -- shared with tool libraries.
    """
    trail = find_unfilled(raw)
    if trail is not None:
        raise CaseParseError(
            f"{path}: case #{index + 1} still has the scaffold placeholder "
            f"{UNFILLED_SENTINEL} at {trail or 'case'}. Fill it in -- an "
            f"unfinished eval cannot say anything about the skill."
        )


def _load_tool_libraries(path: Path, data: dict) -> ToolLibrary:
    """The tools this file's `tool_libraries:` imports; `EMPTY_LIBRARY` when
    the key is absent, so a `ref:` can say "add the key".

    Paths resolve against the eval file's own directory, never the working
    directory. A library error is re-raised naming this file too: the
    library is the file to fix, this is the file that imported it.
    """
    if TOOL_LIBRARIES_KEY not in data:
        return EMPTY_LIBRARY
    try:
        return load_tool_libraries(data[TOOL_LIBRARIES_KEY], relative_to=path.parent)
    except ToolLibraryError as exc:
        raise CaseParseError(f"{path}: {exc}") from exc


def _resolve_tool_refs(path: Path, index: int, raw: object, library: ToolLibrary) -> object:
    """Replace every `- ref: <name>` in the case's `tools:` with the tool the
    library declares, keeping the case's own `returns:` when it set one.

    Runs on the raw mapping, before `EvalCase.model_validate`: `ToolSpec`
    forbids unknown keys and requires a name, so a ref is not a ToolSpec and
    must never become one half-built. Resolving here is what keeps
    `EvalCase.tools` a list of `ToolSpec` for every runner, evaluator and
    preflight downstream. Nothing is mutated: a YAML anchor can alias one
    `tools:` list into several cases, so the result is a new mapping with a
    new list. Anything that is not a mapping with a `ref` key is kept as
    written for the model to judge.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("tools"), list):
        return raw
    tools: list[object] = []
    for position, entry in enumerate(raw["tools"]):
        if not isinstance(entry, dict) or "ref" not in entry:
            tools.append(entry)
            continue
        where = f"{path}: case #{index + 1} tool #{position + 1}"
        try:
            ref = ToolRef.model_validate(entry)
        except ValidationError as exc:
            fields = ", ".join(str(e["loc"][0]) for e in exc.errors() if e["loc"])
            raise CaseParseError(
                f"{where}: invalid ref: entry ({fields}): a ref: may carry only returns: "
                f"beside it; the library declares the tool's name, description and schema."
            ) from exc
        try:
            spec = library.resolve(ref.ref)
        except ToolLibraryError as exc:
            raise CaseParseError(f"{where} {exc}") from exc
        resolved = copy.deepcopy(spec.model_dump())
        if ref.returns is not None:
            resolved["returns"] = ref.returns
        tools.append(resolved)
    return {**raw, "tools": tools}


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
    library = _load_tool_libraries(path, data)
    cases: list[EvalCase] = []
    for index, raw in enumerate(raw_cases):
        _reject_unfilled(path, index, raw)
        raw = _resolve_tool_refs(path, index, raw, library)
        try:
            case = EvalCase.model_validate(raw)
        except ValidationError as exc:
            fields = ", ".join(str(e["loc"][0]) for e in exc.errors() if e["loc"])
            raise CaseParseError(f"{path}: case #{index + 1} invalid ({fields}): {exc}") from exc
        _validate_cross_references(path, case, skill)
        _validate_workspace(path, case)
        _validate_assertions(path, case)
        _validate_tools(path, case)
        cases.append(case)
    return cases


# Per kind: which of `value` / `file` / `json_schema` it requires, and which
# it merely allows. Anything not listed is forbidden for that kind, so a
# `value:` on a `file-produced` -- which would read as a content check but
# assert nothing -- is caught rather than ignored.
#
# This is deliberately a second table, not a reuse of the evaluator's
# `_CHECKS`: that one maps a kind to a predicate, this one maps a kind to its
# field requirements. Two different facts about the same kinds. They are
# pinned together by a test, not by an import.
_ASSERTION_FIELDS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "contains": (frozenset({"value"}), frozenset({"file"})),
    "not_contains": (frozenset({"value"}), frozenset({"file"})),
    "regex": (frozenset({"value"}), frozenset({"file"})),
    "equals": (frozenset({"value"}), frozenset({"file"})),
    "file-produced": (frozenset({"file"}), frozenset()),
    "json-schema": (frozenset({"json_schema"}), frozenset({"file"})),
}

_ASSERTION_FIELD_NAMES = ("value", "file", "json_schema")


def _validate_assertions(path: Path, case: EvalCase) -> None:
    """Check every assertion against the requirements table.

    Runs at load time rather than at evaluate time so a mistake aborts before
    any case runs. `AssertionEvaluator` keeps its own kind check for an
    EvalCase built programmatically, bypassing this loader.
    """
    for position, spec in enumerate(case.assertions, start=1):
        where = f"{path}: case {case.name!r} assertion #{position}"
        try:
            required, optional = _ASSERTION_FIELDS[spec.kind]
        except KeyError:
            known = ", ".join(sorted(_ASSERTION_FIELDS))
            raise CaseParseError(
                f"{where} has unknown kind {spec.kind!r}. Known kinds: {known}."
            ) from None
        present = {name for name in _ASSERTION_FIELD_NAMES if getattr(spec, name) is not None}
        missing = sorted(required - present)
        if missing:
            raise CaseParseError(
                f"{where} ({spec.kind}) requires {', '.join(missing)}, which is missing."
            )
        extra = sorted(present - required - optional)
        if extra:
            raise CaseParseError(
                f"{where} ({spec.kind}) does not accept {', '.join(extra)}. "
                f"It would assert nothing, so it is a mistake rather than a check."
            )
        if spec.file is not None and case.workspace is None:
            raise CaseParseError(
                f"{where} targets file {spec.file!r}, but the case declares no "
                f"'workspace:' block. There would be no file to look at, so the "
                f"assertion could never hold."
            )
        if spec.json_schema is not None:
            try:
                Draft202012Validator.check_schema(spec.json_schema)
            except SchemaError as exc:
                raise CaseParseError(f"{where} has an invalid json_schema: {exc.message}") from exc


def _validate_tools(path: Path, case: EvalCase) -> None:
    """Check each mock tool's declared schema at load time.

    The rules are `checks.check_tool_schema`'s, shared with tool libraries;
    here each refusal names the file, the case and the tool. All three
    mistakes are the author's, so they abort before any case runs rather than
    surface as an errored case.
    """
    for tool in case.tools:
        try:
            check_tool_schema(tool)
        except ValueError as exc:
            raise CaseParseError(f"{path}: case {case.name!r} tool {tool.name!r} {exc}") from exc


def _validate_workspace(path: Path, case: EvalCase) -> None:
    """Reject a seeded path, a named judge artifact, or an assertion's `file:`
    target that could ever leave the workspace root.

    `judge.artifacts` gets no schema-level shape check on the string itself
    (it is "any list of names an eval author wants graded"), so a typo like
    `../escape.txt` would otherwise pass straight through to `_artifacts` at
    run time, render as `(not produced)`, and fail the rubric -- blaming the
    skill for a path no agent could ever have produced. `workspace.files`
    keys and assertion `file:` values get exactly this same check below; an
    authoring mistake in any of the three must abort the run (exit 2), never
    score as a failure. `AssertionEvaluator` keeps its own `PathRefused`
    handling too -- reachable for an `EvalCase` built programmatically,
    bypassing this loader -- so a case running that way still gets an
    authoring-error verdict rather than a raw `UnicodeEncodeError`; it is
    just no longer the only place a YAML-authored case's escape is caught,
    and now catches it before the runner has spent anything on the case.
    """
    if case.workspace is not None:
        for name in case.workspace.files:
            try:
                check_relative_path(name)
            except PathRefused as exc:
                raise CaseParseError(
                    f"{path}: case {case.name!r} declares workspace file {name!r}: {exc}"
                ) from exc
    if case.judge is not None:
        for name in case.judge.artifacts:
            try:
                check_relative_path(name)
            except PathRefused as exc:
                raise CaseParseError(
                    f"{path}: case {case.name!r} names judge artifact {name!r}: {exc}"
                ) from exc
    for position, spec in enumerate(case.assertions, start=1):
        if spec.file is None:
            continue
        try:
            check_relative_path(spec.file)
        except PathRefused as exc:
            raise CaseParseError(
                f"{path}: case {case.name!r} assertion #{position} targets file "
                f"{spec.file!r}: {exc}"
            ) from exc


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

    if case.workspace is not None:
        for tool in case.tools:
            if tool.name in BUILTIN_TOOL_NAMES:
                raise CaseParseError(
                    f"{path}: case {case.name!r} declares a tool named {tool.name!r}, "
                    f"which collides with a built-in workspace tool. Rename the "
                    f"case's tool, or drop the 'workspace:' block."
                )

    declared = {tool.name for tool in case.tools}
    if case.workspace is not None:
        # The built-ins are real tools the agent can call, so a trajectory may
        # name them -- but only in a case that actually has them.
        declared |= set(BUILTIN_TOOL_NAMES)

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
        if case.judge.artifacts and case.workspace is None:
            raise CaseParseError(
                f"{path}: case {case.name!r} names judge artifacts "
                f"{case.judge.artifacts}, but declares no 'workspace:' block. "
                f"There would be no files to read."
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
                hint = (
                    " Built-in workspace and bundle tools only exist in a case with a "
                    "'workspace:' block."
                    if name in BUILTIN_TOOL_NAMES
                    else ""
                )
                raise CaseParseError(
                    f"{path}: case {case.name!r} trajectory.{field_name} names "
                    f"{name!r}, which is not declared in this case's tools.{hint}"
                )


def discover_eval_paths(skill: Skill) -> list[Path]:
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
        paths = discover_eval_paths(skill)
    cases: list[EvalCase] = []
    for path in paths:
        cases.extend(parse_cases_file(path, skill))
    return cases
