"""Run a case through an agent product's own command-line interface.

The two framework runners drive a generic agent loop with skill-lens's own
prompt and tools. A product runner starts the product the skill is actually
shipped to -- GitHub Copilot CLI, Claude Code, or any command named in
`skill-lens.toml` -- in its non-interactive mode, with the skill placed
where that product discovers skills, and reads the result from the product's
own trace. The skill is measured under the product's system prompt, tools
and loading mechanics, none of which skill-lens can emulate from outside.

The trust model is the product's, not skill-lens's: permission prompts are
disabled (the product cannot run non-interactively otherwise) and the full
environment is inherited (the product needs its own auth). Nothing here is
sandboxed; `ProductStatus.trust` says so on every report. Naming a product
runner is the decision.

Imports no agent framework: a product is an executable and a trace grammar.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from skill_lens.bundle import BUNDLE_DIRS
from skill_lens.models import EvalCase, RunResult, Skill
from skill_lens.process import group_kwargs, read_capped_handle, reap_and_kill_group
from skill_lens.runners.traces import Trace, parse_claude_code, parse_copilot
from skill_lens.skills.loader import SKILL_FILENAME
from skill_lens.workspace import PathRefused, Workspace, check_relative_path

PROMPT_PLACEHOLDER = "{prompt}"
DEFAULT_SKILLS_DIR = ".agents/skills"
DEFAULT_INVOKE = "{task}"
DEFAULT_TIMEOUT_SECONDS = 600.0
DEFAULT_MAX_OUTPUT_BYTES = 8_000_000
# Linux caps one argv element at 128 KiB; the prompt travels as one element.
MAX_PROMPT_BYTES = 100 * 1024
VERSION_PROBE_TIMEOUT_SECONDS = 30.0
STDERR_CAP_BYTES = 20_000
STDERR_TAIL_CHARS = 500
PRIVATE_PREFIX = "skill-lens-product-"
SCRATCH_PREFIX = "skill-lens-product-capture-"
TRUST_NOTE = (
    "runs with permission prompts disabled and the full environment; no skill-lens "
    "sandbox applies; bundled scripts are reachable through the product's own tools"
)


class ProductSetupError(Exception):
    """A product runner cannot run here: raised only from `preflight`, before any
    case, and turned into exit 2 by `cli.py`."""


@dataclass(frozen=True)
class Product:
    """One agent product: how to start it, where it finds skills, how to read it.

    `argv` holds exactly one element equal to `PROMPT_PLACEHOLDER`, replaced
    as a whole element -- never through a shell. `invoke` is how `mode:
    loaded` asks the product to load the skill (`{name}`, `{task}`);
    `parse` is None for a generic product, whose stdout is the output.
    """

    name: str
    argv: tuple[str, ...]
    skills_dir: str
    invoke: str
    parse: Callable[[str], Trace] | None
    version_command: tuple[str, ...] | None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES


PRESETS: dict[str, Product] = {
    "copilot": Product(
        name="copilot",
        # --allow-all-tools: required for -p. --output-format json: the trace.
        # --no-custom-instructions: nothing from ~/.copilot or a stray
        # AGENTS.md shapes the run. --no-auto-update: no network hop before
        # the prompt.
        argv=(
            "copilot",
            "-p",
            PROMPT_PLACEHOLDER,
            "--allow-all-tools",
            "--output-format",
            "json",
            "--no-custom-instructions",
            "--no-auto-update",
        ),
        skills_dir=".agents/skills",
        invoke="/{name} {task}",
        parse=parse_copilot,
        version_command=("copilot", "--version"),
    ),
    "claude-code": Product(
        name="claude-code",
        # --verbose is what stream-json needs in print mode. --setting-sources
        # project and --strict-mcp-config keep the user's own hooks, plugins
        # and MCP servers out of the eval (verified: the project skill is
        # still discovered and OAuth still works); --no-session-persistence
        # writes nothing under ~/.claude.
        argv=(
            "claude",
            "-p",
            PROMPT_PLACEHOLDER,
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
            "--setting-sources",
            "project",
            "--strict-mcp-config",
            "--no-session-persistence",
        ),
        skills_dir=".claude/skills",
        invoke="/{name} {task}",
        parse=parse_claude_code,
        version_command=("claude", "--version"),
    ),
}


@dataclass(frozen=True)
class Invocation:
    """What one product process did. `error` is set only when it could not start."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    timed_out: bool = False
    truncated: bool = False
    error: str | None = None


def invoke(product: Product, prompt: str, cwd: Path) -> Invocation:
    """Start the product once with `prompt`, in `cwd`; never raises.

    Output goes to files in a scratch directory and is read back through the
    handles the harness opened (see `process.read_capped_handle`). The
    process group is killed after every exit, timeout or not.
    """
    argv = [prompt if element == PROMPT_PLACEHOLDER else element for element in product.argv]
    try:
        scratch = Path(tempfile.mkdtemp(prefix=SCRATCH_PREFIX)).resolve()
    except OSError as exc:
        return Invocation(error=f"cannot create a capture directory: {exc}")
    try:
        with ExitStack() as stack:
            try:
                stdout_handle = stack.enter_context((scratch / "stdout").open("w+b"))
                stderr_handle = stack.enter_context((scratch / "stderr").open("w+b"))
            except OSError as exc:
                return Invocation(error=f"cannot create the capture files: {exc}")
            try:
                process = subprocess.Popen(  # noqa: S603 - argv list, shell=False, prompt is one element
                    argv,
                    cwd=cwd,
                    # The whole environment, on purpose: the product needs its
                    # own auth. The opposite of `scripts.script_environment`.
                    env=os.environ.copy(),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    **group_kwargs(),
                )
            except (OSError, ValueError) as exc:
                return Invocation(error=f"cannot start {product.argv[0]}: {exc}")
            exit_code, timed_out = reap_and_kill_group(process, product.timeout_seconds)
            truncated = os.fstat(stdout_handle.fileno()).st_size > product.max_output_bytes
            return Invocation(
                stdout=read_capped_handle(stdout_handle, product.max_output_bytes),
                stderr=read_capped_handle(stderr_handle, STDERR_CAP_BYTES),
                exit_code=exit_code,
                timed_out=timed_out,
                truncated=truncated,
            )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _stderr_tail(stderr: str) -> str:
    text = " ".join(stderr.strip().splitlines()[-5:]).strip()
    return text[-STDERR_TAIL_CHARS:]


def read_trace(product: Product, invocation: Invocation) -> Trace:
    """The invocation as a `Trace`: parsed when the product has a parser, else
    stdout as the output. A failure of any kind is `Trace.error`."""
    if invocation.error is not None:
        return Trace(error=invocation.error)
    if invocation.timed_out:
        return Trace(error=f"{product.name} timed out after {product.timeout_seconds:g}s")
    if invocation.truncated:
        return Trace(
            error=(
                f"{product.name} output exceeded {product.max_output_bytes} bytes; "
                f"raise [runners.{product.name}] max_output_bytes"
            )
        )
    exited = f"{product.name} exited with code {invocation.exit_code}"
    if product.parse is None:
        if invocation.exit_code != 0:
            return Trace(error=f"{exited}: {_stderr_tail(invocation.stderr)}".rstrip(": "))
        return Trace(
            output=invocation.stdout.removesuffix("\n"),
            usage_note=f"the {product.name} runner does not report token usage",
            cost_note=f"the {product.name} runner does not report cost",
        )
    trace = product.parse(invocation.stdout)
    if invocation.exit_code != 0 and (trace.error is None or not trace.complete):
        # A complete trace carrying the product's own error message is the
        # best explanation there is; otherwise the exit code and stderr are.
        return replace(trace, error=f"{exited}: {_stderr_tail(invocation.stderr)}".rstrip(": "))
    return trace


def deliver_skill(skill: Skill, cwd: Path, skills_dir: str) -> bool:
    """Write the skill where the product discovers it. False when there is nothing to write.

    `SKILL.md` is `skill.markdown` byte for byte; beside it go `scripts/`,
    `references/` and `assets/` from the bundle -- those three and nothing
    else, so an eval file beside `SKILL.md` never reaches the product.
    Symlinks are copied as symlinks, as `git archive` preserved them.
    """
    if not skill.markdown:
        return False
    target = cwd / skills_dir / skill.name
    target.mkdir(parents=True, exist_ok=True)
    (target / SKILL_FILENAME).write_text(skill.markdown, encoding="utf-8")
    if skill.bundle_root is not None:
        for name in BUNDLE_DIRS:
            source = skill.bundle_root / name
            if source.is_dir():
                shutil.copytree(source, target / name, symlinks=True, dirs_exist_ok=True)
    return True


def _check_skill_name(name: str) -> None:
    """A skill name must be one directory name; the product's discovery depends on it.

    Both separators are refused on every platform: a backslash is a legal
    POSIX filename character, but the same skill directory must also be
    discoverable on Windows.
    """
    try:
        check_relative_path(name)
    except PathRefused as exc:
        raise ProductSetupError(f"skill name {name!r} cannot be a directory name: {exc}") from exc
    if "/" in name or "\\" in name or len(Path(name).parts) != 1:
        raise ProductSetupError(
            f"skill name {name!r} cannot be a directory name: it contains a path separator"
        )


class ProductRunner:
    """Runs a case through one product, behind the framework-agnostic protocol."""

    needs_api_key = False

    def __init__(self, product: Product) -> None:
        self._product = product
        self.name = product.name

    @property
    def product(self) -> Product:
        return self._product

    def run(
        self,
        skill: Skill,
        case: EvalCase,
        workspace: Workspace | None = None,
        scripts: Any = None,
    ) -> RunResult:
        """`scripts` is accepted for protocol symmetry and ignored: the product
        brings its own tools, and its shell can already reach the bundle."""
        product = self._product
        started = time.monotonic()

        def elapsed() -> int:
            return int((time.monotonic() - started) * 1000)

        private: Path | None = None
        try:
            if workspace is not None:
                cwd = workspace.root
            else:
                private = Path(tempfile.mkdtemp(prefix=PRIVATE_PREFIX)).resolve()
                cwd = private
            delivered = deliver_skill(skill, cwd, product.skills_dir)
            prompt = case.task
            if delivered and case.mode == "loaded":
                prompt = product.invoke.format(name=skill.name, task=case.task)
            size = len(prompt.encode("utf-8"))
            if size > MAX_PROMPT_BYTES:
                return RunResult(
                    error=(
                        f"prompt is {size} bytes; a product runner sends at most "
                        f"{MAX_PROMPT_BYTES} bytes as one argument"
                    ),
                    latency_ms=elapsed(),
                )
            trace = read_trace(product, invoke(product, prompt, cwd))
        except OSError as exc:
            return RunResult(error=f"{type(exc).__name__}: {exc}", latency_ms=elapsed())
        finally:
            if private is not None:
                shutil.rmtree(private, ignore_errors=True)

        if trace.error is not None:
            return RunResult(error=trace.error, latency_ms=elapsed(), model=trace.model)
        return RunResult(
            output=trace.output,
            tool_calls=list(trace.tool_calls),
            transcript=list(trace.transcript),
            input_tokens=trace.input_tokens,
            output_tokens=trace.output_tokens,
            latency_ms=elapsed(),
            cost_usd=trace.cost_usd,
            cost_note=trace.cost_note,
            usage_note=trace.usage_note,
            model=trace.model,
            skill_triggered=(
                (skill.name in trace.invoked_skills) if case.mode == "offered" else None
            ),
        )
