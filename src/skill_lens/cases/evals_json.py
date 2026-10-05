"""Read the shared `evals/evals.json` format into raw case mappings.

Both the Agent Skills evaluation guide (agentskills.io) and Anthropic's
`skill-creator` keep test cases in `evals/evals.json`. This module turns one
parsed document into the same raw mappings the YAML loader produces, so
`EvalCase.model_validate` and the loader's `_validate_*` checks are the only
validators: a JSON case and a YAML case meet identical rules.

The reading is strict. A key this module does not know is an authoring error
that names the key, for the reason every authored model forbids extras: a
misspelled `assertion:` must not drop the checks and pass.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from skill_lens.cases.errors import CaseParseError
from skill_lens.models import Skill

EVALS_JSON_FILENAME = "evals.json"

_TOP_LEVEL_KEYS = frozenset({"skill_name", "evals"})
_CASE_KEYS = frozenset({"id", "prompt", "expected_output", "files", "assertions", "expectations"})
# The guide says `assertions`; skill-creator says `expectations`. One list, two names.
_STATEMENT_KEYS = ("assertions", "expectations")


def evals_json_to_raw_cases(path: Path, data: object, skill: Skill | None) -> list[dict[str, Any]]:
    """The raw case mappings for one parsed `evals.json`.

    `skill` is optional because a file can be parsed on its own; it is needed
    only for the `skill_name` comparison and to resolve input `files`.
    """
    if not isinstance(data, dict):
        raise CaseParseError(f"{path}: expected a JSON object with an 'evals' list")
    _reject_unknown(path, "the top level", data, _TOP_LEVEL_KEYS)
    if "skill_name" in data:
        _check_skill_name(path, data["skill_name"], skill)
    entries = data.get("evals")
    if not isinstance(entries, list):
        raise CaseParseError(f"{path}: 'evals' must be a list")
    if not entries:
        raise CaseParseError(f"{path}: 'evals' is empty; there is nothing to run")
    seen: set[str] = set()
    return [
        _convert(path, position, entry, skill, seen)
        for position, entry in enumerate(entries, start=1)
    ]


def _reject_unknown(path: Path, where: str, mapping: dict, allowed: frozenset[str]) -> None:
    unknown = sorted(key for key in mapping if key not in allowed)
    if unknown:
        names = ", ".join(repr(key) for key in unknown)
        known = ", ".join(sorted(allowed))
        raise CaseParseError(
            f"{path}: {where} has unknown key(s) {names}; the keys skill-lens reads are {known}"
        )


def _check_skill_name(path: Path, declared: object, skill: Skill | None) -> None:
    if not isinstance(declared, str):
        raise CaseParseError(f"{path}: 'skill_name' must be a string, got {declared!r}")
    if skill is not None and declared != skill.name:
        raise CaseParseError(
            f"{path}: 'skill_name' is {declared!r} but this skill is "
            f"{skill.name!r}; the file looks like it belongs to another skill"
        )


def _convert(
    path: Path, position: int, entry: object, skill: Skill | None, seen: set[str]
) -> dict[str, Any]:
    where = f"eval #{position}"
    if not isinstance(entry, dict):
        raise CaseParseError(f"{path}: {where} must be a JSON object")
    _reject_unknown(path, where, entry, _CASE_KEYS)
    name = _case_name(path, where, entry, seen)
    where = f"{where} ({name})"
    prompt = entry.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise CaseParseError(f"{path}: {where} 'prompt' must be a non-empty string")
    expected = entry.get("expected_output", "")
    if not isinstance(expected, str):
        raise CaseParseError(f"{path}: {where} 'expected_output' must be a string")
    rubric = _statements(path, where, entry)
    if not rubric:
        if not expected.strip():
            raise CaseParseError(
                f"{path}: {where} has nothing to grade; give it "
                "'assertions' (or 'expectations'), or an 'expected_output' "
                "the judge can check"
            )
        rubric = [f"The output satisfies: {expected.strip()}"]
    case: dict[str, Any] = {
        "name": name,
        "task": prompt,
        "judge": {"expected": expected, "rubric": rubric},
    }
    files = _read_files(path, where, entry.get("files", []), skill)
    if files:
        case["workspace"] = {"files": files}
    return case


def _case_name(path: Path, where: str, entry: dict, seen: set[str]) -> str:
    if "id" not in entry:
        raise CaseParseError(f"{path}: {where} has no 'id'")
    ident = entry["id"]
    # `bool` is an `int` in Python; without this, `true` would become id 1.
    valid = isinstance(ident, int | str) and not isinstance(ident, bool)
    if not valid or (isinstance(ident, str) and not ident.strip()):
        raise CaseParseError(
            f"{path}: {where} 'id' must be an integer or a non-empty string, got {ident!r}"
        )
    name = f"eval-{ident}"
    if name in seen:
        raise CaseParseError(f"{path}: {where} repeats id {ident!r}; ids must be unique")
    seen.add(name)
    return name


def _statements(path: Path, where: str, entry: dict) -> list[str]:
    present = [key for key in _STATEMENT_KEYS if key in entry]
    if len(present) == 2:
        raise CaseParseError(
            f"{path}: {where} has both 'assertions' and 'expectations'; "
            "they are the same list under two names, so keep one"
        )
    if not present:
        return []
    key = present[0]
    value = entry[key]
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise CaseParseError(f"{path}: {where} '{key}' must be a list of non-empty strings")
    return list(value)


def _read_files(path: Path, where: str, raw: object, skill: Skill | None) -> dict[str, str]:
    """Input files, keyed by the path as written. Filled in by the next task."""
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise CaseParseError(f"{path}: {where} 'files' must be a list of paths")
    if raw:
        raise CaseParseError(f"{path}: {where} input files are not supported yet")
    return {}
