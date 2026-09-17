"""Build the framework-neutral tools an agent may call.

Nothing here knows about any agent framework: an AgentTool is a name, a JSON
schema and a callable, which every adapter can register in its own way.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from skill_lens.bundle import SkillBundle
from skill_lens.models import Skill, ToolSpec
from skill_lens.scripts import ScriptResult, ScriptRuntime, run_script
from skill_lens.workspace import PathRefused, Workspace


@dataclass(frozen=True)
class AgentTool:
    """A tool the agent may call: a name, a JSON schema and a callable.

    Covers both the canned tools built from a case's `tools:` block, which
    have no side effects, and the built-in workspace tools, which do. The
    common contract is narrower than "no side effects" and is what every
    adapter relies on: **calling one never raises.** A refusal comes back as
    an ordinary string result, because a model that called a tool wrongly is
    an eval signal and an exception would surface it as an infra failure.
    """

    name: str
    description: str
    json_schema: dict[str, Any]
    call: Callable[..., str]


def build_mock_tool(spec: ToolSpec) -> AgentTool:
    """Turn a declared ToolSpec into a callable plus its JSON schema.

    Two shapes, deliberately asymmetric. The `parameters:` shorthand is closed
    (`additionalProperties: false`, every key required) because the author
    wrote every key. A declared `input_schema` is passed verbatim -- deep
    copied so an adapter cannot mutate the case -- because fidelity to the
    server it stands in for is its reason to exist. Types in the shorthand
    are already constrained by `ToolSpec`, and the loader has already checked
    a declared schema, so nothing here can be rejected.
    """
    if spec.input_schema is not None:
        json_schema: dict[str, Any] = copy.deepcopy(spec.input_schema)
    else:
        properties = {name: {"type": type_name} for name, type_name in spec.parameters.items()}
        json_schema = {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }
    returns = spec.returns

    def call(**_arguments: Any) -> str:
        """Return the canned value, whatever the model passed in."""
        return returns

    return AgentTool(
        name=spec.name,
        description=spec.description,
        json_schema=json_schema,
        call=call,
    )


def _empty_schema() -> dict[str, Any]:
    """A fresh, parameter-free JSON schema.

    Built inline per call -- like `build_mock_tool` already does for its own
    schema -- so no two built tools ever share a mutable `properties` dict or
    `required` list. A dict literal, however it was copied, does not protect
    against mutating what's *inside* it.
    """
    return {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }


def skill_tool_name(skill_name: str) -> str:
    """The identifier a skill is offered under: 'order-support' -> 'order_support'.

    Deterministic, because both the runner (which registers the tool) and the
    case loader (which rejects a case tool that would collide with it) have to
    agree on the answer without talking to each other.

    Must be total: every possible input has to yield a valid Python
    identifier. `char.isalnum()` / `char.isdigit()` are not safe tests for
    this -- both return True for Unicode "Other Number" (No) characters
    (superscripts, circled digits, vulgar fractions) that are nonetheless
    illegal in an identifier in any position. Asking Python directly avoids
    that trap: `f"a{char}".isidentifier()` is exactly "valid in a non-leading
    position", and `char.isidentifier()` is exactly "valid in the leading
    position".

    Distinct names can collapse to the same identifier (e.g. "a-b", "a_b" and
    "a b" all become "a_b"). That's an accepted, deliberate tradeoff: only one
    skill is offered as a tool per run, so there's never a same-run collision
    to resolve.
    """
    cleaned = "".join(char if f"a{char}".isidentifier() else "_" for char in skill_name)
    if not cleaned.strip("_"):
        return "skill"
    if not cleaned[0].isidentifier():
        cleaned = f"skill_{cleaned}"
    return cleaned


def build_skill_tool(skill: Skill) -> AgentTool:
    """The skill itself, offered as a tool the agent may decline to use.

    Calling it returns the skill's instructions, so an offered run only has the
    skill once the agent chose it -- and the rest of the run proceeds
    realistically with it loaded, rather than the agent acting on instructions
    it never received.
    """
    instructions = skill.instructions

    def call(**_arguments: Any) -> str:
        return instructions

    return AgentTool(
        name=skill_tool_name(skill.name),
        description=skill.description,
        json_schema=_empty_schema(),
        call=call,
    )


# The names the built-in tools are registered under. `cases/loader.py` reads
# BUILTIN_TOOL_NAMES to reject a case tool that would collide with one, and to
# accept these names in a trajectory block -- both need the answer without
# asking a runner. All six are reserved in every workspace case, bundle or
# not: a name that is sometimes free is a name nobody can rely on.
WORKSPACE_TOOL_NAMES: tuple[str, ...] = ("list_files", "read_file", "write_file")
BUNDLE_TOOL_NAMES: tuple[str, ...] = ("list_skill_files", "read_skill_file", "run_script")
BUILTIN_TOOL_NAMES: tuple[str, ...] = WORKSPACE_TOOL_NAMES + BUNDLE_TOOL_NAMES


def _path_schema() -> dict[str, Any]:
    """A fresh one-argument schema. Built per call, like `_empty_schema()`.

    Not a module constant copied with `dict(...)`: that copies only the top
    level, so every toolset would go on sharing the same `required` list and
    the same nested property dicts. Under `--concurrency N` one adapter
    mutating a schema in place would then corrupt unrelated cases' tools.
    """
    return {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    }


def _write_schema() -> dict[str, Any]:
    """A fresh two-argument schema. See `_path_schema` for why it is a function."""
    return {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
        "additionalProperties": False,
    }


def build_workspace_tools(workspace: Workspace) -> list[AgentTool]:
    """The three real tools, bound to one workspace.

    Every callable below catches rather than raises. `PathRefused` already
    carries a message written for the model; `OSError` and `UnicodeError`
    (a lone UTF-16 surrogate can raise `UnicodeEncodeError` on the way in,
    before any decoding happens, not just `UnicodeDecodeError` on the way out)
    are turned into one here. Arguments are accepted positionally-optional and
    coerced, because a model may omit a required argument, send an extra one,
    or send the wrong type -- none of which may raise.
    """

    def list_files(**_extra: Any) -> str:
        try:
            entries = workspace.listing()
        except OSError as exc:  # pragma: no cover - a directory we just made
            return f"refused: cannot list the working directory: {exc}"
        return "\n".join(entries) if entries else "(empty)"

    def read_file(path: Any = "", **_extra: Any) -> str:
        target = str(path)
        try:
            return workspace.read(target)
        except PathRefused as exc:
            return str(exc)
        except UnicodeDecodeError:
            # The realistic case: the file exists but its bytes are not UTF-8.
            # Say so about the CONTENT, so the model does not go looking for a
            # problem with the path it asked for.
            return f"refused: the content of {target} is not valid UTF-8 text"
        except UnicodeError:
            # Backstop. A lone UTF-16 surrogate in the path used to reach here
            # as UnicodeEncodeError from os.path.realpath; check_relative_path
            # now refuses those first, as PathRefused. Kept so that any future
            # surprise from the path layer still returns a message rather than
            # breaking the never-raise rule.
            return f"refused: {target} could not be handled as UTF-8 text"
        except OSError as exc:
            return f"refused: cannot read {target}: {exc}"

    def write_file(path: Any = "", content: Any = "", **_extra: Any) -> str:
        target = str(path)
        try:
            written = workspace.write(target, str(content))
        except PathRefused as exc:
            return str(exc)
        except UnicodeEncodeError:
            # Only the CONTENT can trip this now: a lone UTF-16 surrogate --
            # what a model emits when it produces a malformed \uXXXX escape --
            # cannot be encoded as UTF-8. A surrogate in the path is refused
            # earlier, by check_relative_path, as PathRefused.
            return (
                f"refused: the content for {target} contains characters that "
                "cannot be encoded as UTF-8"
            )
        except UnicodeError:
            # Backstop for any other Unicode failure, so the tool never raises.
            return f"refused: {target} could not be handled as UTF-8 text"
        except OSError as exc:
            return f"refused: cannot write {target}: {exc}"
        return f"wrote {target} ({written:,} bytes)"

    return [
        AgentTool(
            name="list_files",
            description=("List every file in the working directory, one relative path per line."),
            json_schema=_empty_schema(),
            call=list_files,
        ),
        AgentTool(
            name="read_file",
            description=("Read a text file from the working directory. `path` is relative to it."),
            json_schema=_path_schema(),
            call=read_file,
        ),
        AgentTool(
            name="write_file",
            description=(
                "Create or replace a text file in the working directory. `path` is relative to it."
            ),
            json_schema=_write_schema(),
            call=write_file,
        ),
    ]


def _run_script_schema() -> dict[str, Any]:
    """A fresh schema for run_script. Built per call, like `_path_schema`."""
    return {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "args": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["path"],
        "additionalProperties": False,
    }


def render_script_result(result: ScriptResult, timeout_seconds: float) -> str:
    """What the model reads after a script call.

    A refusal passes through as-is. Otherwise: the exit line, then both
    streams (each already capped by `run_script`), then a workspace warning
    if the script wrote past a cap. `(empty)` rather than nothing, so the
    model can tell "no output" from "the tool broke".
    """
    if result.refused is not None:
        return result.refused
    head = (
        f"stopped after {timeout_seconds:g} s (script_timeout_seconds)"
        if result.timed_out
        else f"exit code: {result.exit_code}"
    )
    lines = [head, "stdout:", result.stdout or "(empty)", "stderr:", result.stderr or "(empty)"]
    if result.workspace_warning is not None:
        lines.append(result.workspace_warning)
    return "\n".join(lines)


def build_bundle_tools(
    bundle: SkillBundle, workspace: Workspace, runtime: ScriptRuntime | None
) -> list[AgentTool]:
    """The bundle tools, bound to one skill's bundle and one workspace.

    The two read tools are always built. `run_script` is built only when the
    bundle actually has something under `scripts/` AND the run enabled
    execution (`runtime` is not None) -- a tool that could only ever refuse
    would cost prompt tokens and teach the model nothing.

    Descriptions say what the tools are for without naming the skill or its
    files: `SKILL.md` is where "run scripts/count.py" comes from, and that is
    the thing under measurement.

    Every callable catches, like the workspace tools: a model asking for a bad
    path is an eval signal, and an exception would surface it as an infra
    error.
    """

    def list_skill_files(**_extra: Any) -> str:
        try:
            entries = bundle.listing()
        except OSError as exc:  # pragma: no cover - a directory the loader just saw
            return f"refused: cannot list the skill's files: {exc}"
        return "\n".join(entries) if entries else "(empty)"

    def read_skill_file(path: Any = "", **_extra: Any) -> str:
        target = str(path)
        try:
            return bundle.read(target)
        except PathRefused as exc:
            return str(exc)
        except UnicodeDecodeError:
            return f"refused: the content of {target} is not valid UTF-8 text"
        except UnicodeError:
            return f"refused: {target} could not be handled as UTF-8 text"
        except OSError as exc:
            return f"refused: cannot read {target}: {exc}"

    tools = [
        AgentTool(
            name="list_skill_files",
            description=(
                "List the files bundled with the loaded skill, one path per line, relative "
                "to the skill's directory (scripts/, references/, assets/)."
            ),
            json_schema=_empty_schema(),
            call=list_skill_files,
        ),
        AgentTool(
            name="read_skill_file",
            description=(
                "Read a file bundled with the loaded skill. `path` is relative to the "
                "skill's directory, for example `references/style.md`."
            ),
            json_schema=_path_schema(),
            call=read_skill_file,
        ),
    ]
    if runtime is None or not bundle.scripts():
        return tools

    def run_script_tool(path: Any = "", args: Any = (), **_extra: Any) -> str:
        # A model may send args as a string, a number, null, or nothing. None
        # of those may raise: coerce to a list of strings and let the script
        # see what the model meant.
        if isinstance(args, (list, tuple)):
            arguments: list[object] = list(args)
        elif args in ("", None):
            arguments = []
        else:
            arguments = [args]
        result = run_script(bundle, workspace, str(path), arguments, runtime)
        return render_script_result(result, runtime.policy.timeout_seconds)

    tools.append(
        AgentTool(
            name="run_script",
            description=(
                "Run a script bundled with the loaded skill, with the working directory as "
                "its current directory. `path` is relative to the skill's directory, for "
                "example `scripts/count.py`; `args` are passed as command-line arguments. "
                "Returns the exit code, stdout and stderr."
            ),
            json_schema=_run_script_schema(),
            call=run_script_tool,
        )
    )
    return tools
