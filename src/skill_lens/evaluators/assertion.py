"""Deterministic, rule-based scoring of a run's final output and its artifacts."""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaViolation

from skill_lens.models import AssertionSpec, CheckResult, EvalCase, EvalScore, RunResult
from skill_lens.workspace import PathRefused, Workspace, check_relative_path


class UnknownAssertionKind(Exception):
    """Raised when an eval file uses an assertion kind we do not support."""


class InvalidAssertionValue(Exception):
    """Raised when an assertion's value is malformed (e.g. an invalid regex)."""


# Text predicates only. `file-produced` asks about existence and `json-schema`
# parses before it compares, so neither is a (value, text) -> bool function.
_CHECKS: dict[str, Callable[[str, str], bool]] = {
    "contains": lambda value, output: value in output,
    "not_contains": lambda value, output: value not in output,
    "regex": lambda value, output: re.search(value, output) is not None,
    "equals": lambda value, output: output.strip() == value,
}

# The single exported source of truth. tests/test_docs.py enumerates it and
# tests/test_shipped_skill.py pins the skill's syntax reference to it, so a
# kind cannot be added in one place and forgotten in the others.
ASSERTION_KINDS: tuple[str, ...] = (*_CHECKS, "file-produced", "json-schema")

# How many workspace entries a failure detail lists before eliding. The tail
# goes behind a truthful "+N more" count rather than being cut silently: a
# clipped list must never imply the entries it shows were all of them.
_LISTING_LIMIT = 20


def _workspace_of(result: RunResult) -> Workspace:
    """The run's workspace, rebuilt from its path.

    Limits are irrelevant here -- they constrain writes, and an evaluator only
    reads -- which is why `Workspace` defaults them and this stays a
    one-argument call.
    """
    if result.workspace is None:
        raise InvalidAssertionValue(
            "an assertion targets a file, but this run had no workspace. Add a "
            "'workspace:' block to the case."
        )
    return Workspace(root=result.workspace)


def _listing(workspace: Workspace) -> str:
    entries = workspace.listing()
    if not entries:
        return "the workspace was empty"
    shown = ", ".join(entries[:_LISTING_LIMIT])
    if len(entries) > _LISTING_LIMIT:
        shown = f"{shown}, +{len(entries) - _LISTING_LIMIT} more"
    return f"workspace held {shown}"


def _describe(spec: AssertionSpec) -> str:
    if spec.kind == "file-produced":
        return f"file-produced({spec.file!r})"
    if spec.kind == "json-schema":
        return f"json-schema({spec.file!r})" if spec.file else "json-schema(output)"
    target = f", file={spec.file!r}" if spec.file else ""
    return f"{spec.kind}({spec.value!r}{target})"


def _authored_path(spec: AssertionSpec) -> str:
    """The `file:` the author wrote, or raise if it could never name a workspace file.

    An empty, absolute or `..`-bearing path is a mistake in the eval file, so
    it aborts the run as an authoring error. Everything *about the target*
    -- what the run actually put at a well-formed path -- is judged later,
    by `Workspace`, and is the skill's doing rather than the author's.
    """
    try:
        check_relative_path(spec.file or "")
    except PathRefused as exc:
        raise InvalidAssertionValue(str(exc)) from exc
    return spec.file or ""


def _subject_text(spec: AssertionSpec, result: RunResult) -> tuple[str, str]:
    """The text this assertion looks at, or ('', why it could not be read).

    A file that is missing or unreadable makes the assertion **fail**, not
    error: the skill produced something the eval cannot use, which is a fact
    about the skill. So does a well-formed path the workspace refuses to
    read -- a symlink the run planted that points outside, a FIFO, a file
    over `max_file_bytes` -- because a bundled script can create any of
    those. Only a malformed *authored* path raises.
    """
    if spec.file is None:
        return result.output, ""
    workspace = _workspace_of(result)
    file = _authored_path(spec)
    try:
        return workspace.read(file), ""
    except PathRefused as exc:
        return "", f"expected {file}; {exc}"
    except UnicodeDecodeError:
        return "", f"{file} is not valid UTF-8 text"
    except OSError:
        return "", f"expected {file}; {_listing(workspace)}"


def _check(spec: AssertionSpec, result: RunResult) -> tuple[bool, str]:
    """(held, why). `why` is '' when it held, and explains the failure otherwise."""
    if spec.kind == "file-produced":
        workspace = _workspace_of(result)
        file = _authored_path(spec)
        try:
            target = workspace.resolve(file)
        except PathRefused as exc:
            return False, f"expected {file}; {exc}"
        if target.is_file():
            return True, ""
        return False, f"expected {file}; {_listing(workspace)}"

    text, unreadable = _subject_text(spec, result)
    if unreadable:
        return False, unreadable

    if spec.kind == "json-schema":
        if spec.json_schema is None:
            # Only reachable from an EvalCase built programmatically: the
            # loader's requirements table makes json_schema mandatory for this
            # kind. Guarded anyway, because Draft202012Validator(None) raises a
            # bare AttributeError -- a crash outside the failed/errored/authoring
            # taxonomy entirely, which is the one outcome this project has no
            # place for.
            raise InvalidAssertionValue(
                "a json-schema assertion has no json_schema to validate against"
            )
        try:
            document = json.loads(text)
        except ValueError as exc:
            return False, f"not valid JSON: {exc}"
        try:
            Draft202012Validator(spec.json_schema).validate(document)
        except SchemaViolation as exc:
            at = ".".join(str(part) for part in exc.absolute_path) or "the root"
            return False, f"schema violation at {at}: {exc.message}"
        return True, ""

    try:
        predicate = _CHECKS[spec.kind]
    except KeyError:
        raise UnknownAssertionKind(f"unknown assertion kind: {spec.kind!r}") from None
    try:
        held = predicate(spec.value or "", text)
    except re.error as exc:
        raise InvalidAssertionValue(f"invalid regex pattern {spec.value!r}: {exc}") from exc
    return held, "" if held else "did not hold"


class AssertionEvaluator:
    """Every assertion must hold; the score is the fraction that held.

    Each assertion also comes back as its own `CheckResult`. Ids are positional
    and derived from the *case*, never from the result, so the same ids appear
    in both arms of a comparative run and can be paired -- which is what makes
    "this assertion passes with or without the skill" detectable.
    """

    name = "assertion"

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        if not case.assertions:
            return EvalScore(evaluator=self.name, passed=True, score=1.0, detail="no assertions")
        checks: list[CheckResult] = []
        failures: list[str] = []
        for index, spec in enumerate(case.assertions):
            held, why = _check(spec, result)
            description = _describe(spec)
            if not held:
                failures.append(f"{description}: {why}")
            checks.append(
                CheckResult(
                    id=f"{spec.kind}[{index}]",
                    passed=held,
                    evidence=f"{description} {'held' if held else why}",
                )
            )
        passed_count = len(case.assertions) - len(failures)
        detail = "all assertions held" if not failures else "failed: " + "; ".join(failures)
        return EvalScore(
            evaluator=self.name,
            passed=not failures,
            score=passed_count / len(case.assertions),
            detail=detail,
            checks=checks,
        )
