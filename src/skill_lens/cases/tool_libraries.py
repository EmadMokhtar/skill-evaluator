"""Tool libraries: mock tools declared once and imported by many eval files.

A library is a YAML file holding one top-level `tools:` list -- the block a
case's `tools:` already takes, and exactly what `skill-lens mcp-import`
prints. An eval file names the libraries it imports under `tool_libraries:`
(paths relative to the eval file) and a case pulls a tool in with
`- ref: <name>`. The library owns the tool's contract; the case may set only
`returns`, the scenario.

Everything here raises `ToolLibraryError` naming the library file; the case
loader wraps it with the eval file that did the importing, so a message
always names both the file to look at and the file to fix.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from skill_lens.cases.checks import UNFILLED_SENTINEL, check_tool_schema, find_unfilled
from skill_lens.models import ToolSpec
from skill_lens.yaml_loading import safe_load

TOOL_LIBRARIES_KEY = "tool_libraries"
LIBRARY_SUFFIXES = frozenset({".yaml", ".yml"})


class ToolLibraryError(Exception):
    """A library that cannot be imported: a missing or malformed file, a tool
    the loader would refuse inline, a name declared twice, or a ref no library
    declares. An authoring error, so the run exits 2."""


@dataclass(frozen=True)
class ToolLibrary:
    """Every tool an eval file's imports declare, by name.

    `declared` is False when the eval file has no `tool_libraries:` key at
    all, so the first `ref:` can say "add the key" rather than "unknown name".
    """

    specs: Mapping[str, ToolSpec] = field(default_factory=dict)
    sources: Mapping[str, Path] = field(default_factory=dict)
    declared: bool = False

    def resolve(self, ref: str) -> ToolSpec:
        """The tool `ref` names, or a ToolLibraryError saying what to fix."""
        if not self.declared:
            raise ToolLibraryError(
                f"references tool {ref!r} but the file declares no {TOOL_LIBRARIES_KEY}:"
            )
        try:
            return self.specs[ref]
        except KeyError:
            names = ", ".join(sorted(self.specs)) or "nothing"
            raise ToolLibraryError(
                f"references tool {ref!r}, which no imported library declares; "
                f"the imports declare: {names}"
            ) from None


EMPTY_LIBRARY = ToolLibrary()


def parse_tool_library(path: Path) -> list[ToolSpec]:
    """Parse one library file into the tools it declares, in file order.

    Every tool is checked as an inline one would be -- the scaffold sentinel
    first, so the message names the field to fill in; then `ToolSpec` itself
    (the name rule, unknown keys); then the schema rules -- and each refusal
    names this file and the tool's position, because this is the file to fix.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ToolLibraryError(f"cannot read tool library {path}: {exc}") from exc
    try:
        data = safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ToolLibraryError(f"invalid YAML in tool library {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("tools"), list):
        raise ToolLibraryError(
            f"tool library {path}: expected a top-level 'tools' list -- the block "
            f"`skill-lens mcp-import` prints"
        )
    extra = sorted(str(key) for key in data if key != "tools")
    if extra:
        raise ToolLibraryError(
            f"tool library {path}: expected only a top-level 'tools' list, but found "
            f"{', '.join(repr(key) for key in extra)}"
        )
    specs: list[ToolSpec] = []
    positions: dict[str, int] = {}
    for index, raw in enumerate(data["tools"]):
        where = f"tool library {path}: tool #{index + 1}"
        trail = find_unfilled(raw)
        if trail is not None:
            raise ToolLibraryError(
                f"{where} still has the scaffold placeholder {UNFILLED_SENTINEL} at "
                f"{trail or 'tool'}. Fill it in -- an unfinished mock cannot stand in "
                f"for anything."
            )
        try:
            spec = ToolSpec.model_validate(raw)
        except ValidationError as exc:
            fields = ", ".join(str(e["loc"][0]) for e in exc.errors() if e["loc"])
            raise ToolLibraryError(f"{where} invalid ({fields}): {exc}") from exc
        try:
            check_tool_schema(spec)
        except ValueError as exc:
            raise ToolLibraryError(f"{where} {spec.name!r} {exc}") from exc
        if spec.name in positions:
            raise ToolLibraryError(
                f"tool library {path} declares tool {spec.name!r} twice "
                f"(tools #{positions[spec.name] + 1} and #{index + 1})"
            )
        positions[spec.name] = index
        specs.append(spec)
    return specs


def load_tool_libraries(entries: object, *, relative_to: Path) -> ToolLibrary:
    """Import every library an eval file's `tool_libraries:` names.

    `entries` is the key's raw YAML value; `relative_to` is the eval file's
    directory, the one location the file can rely on -- so the same file
    resolves identically under discovery, `--evals`, `list` and from any
    working directory. An absolute path would break on the next checkout and
    is refused. A file entry is used as written; a directory entry imports
    its `.yaml` / `.yml` files, sorted, without descending further, like
    `evals/`. Every tool name must be declared exactly once across the
    imports: ambiguity is never resolved by position.
    """
    if not isinstance(entries, list):
        raise ToolLibraryError(
            f"{TOOL_LIBRARIES_KEY} must be a list of paths relative to the eval file, "
            f"got {type(entries).__name__}"
        )
    files: list[Path] = []
    origin: dict[Path, str] = {}
    for position, entry in enumerate(entries):
        where = f"{TOOL_LIBRARIES_KEY}[{position}]"
        if not isinstance(entry, str) or not entry.strip():
            raise ToolLibraryError(
                f"{where} must be a path relative to the eval file, got {entry!r}"
            )
        candidate = Path(entry)
        # Three checks, not one, as in workspace.check_relative_path: on
        # Windows "C:foo" is drive-relative but not absolute, and "\\foo" has
        # a root and no drive. `..` is allowed -- the library sits above the
        # skill by design -- which is why that function is not reused here.
        if candidate.is_absolute() or candidate.drive or candidate.root:
            raise ToolLibraryError(
                f"{where} {entry!r} is not a relative path; a library path is relative "
                f"to the eval file so the suite loads from any checkout"
            )
        resolved = (relative_to / candidate).resolve()
        if resolved.is_dir():
            expanded = sorted(
                p for p in resolved.iterdir() if p.is_file() and p.suffix in LIBRARY_SUFFIXES
            )
            if not expanded:
                raise ToolLibraryError(
                    f"{where} {entry!r} names a directory with no YAML files in it ({resolved})"
                )
        elif resolved.is_file():
            expanded = [resolved]
        else:
            raise ToolLibraryError(f"{where} {entry!r} does not exist (looked at {resolved})")
        for file in expanded:
            if file in origin:
                raise ToolLibraryError(
                    f"{where} {entry!r} imports {file} again; {origin[file]} already did"
                )
            origin[file] = where
            files.append(file)
    specs: dict[str, ToolSpec] = {}
    sources: dict[str, Path] = {}
    for file in files:
        for spec in parse_tool_library(file):
            if spec.name in sources:
                raise ToolLibraryError(
                    f"tool {spec.name!r} is declared by both {sources[spec.name]} and "
                    f"{file}; a ref can only mean one of them"
                )
            specs[spec.name] = spec
            sources[spec.name] = file
    return ToolLibrary(specs=specs, sources=sources, declared=True)
