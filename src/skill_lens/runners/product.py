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
from skill_lens.models import EvalCase, ProductStatus, RunResult, Skill
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
    """A product runner cannot run here: raised from `preflight` (exit 2 via
    `cli.py`) and from `deliver_skill`, where `run` turns it into
    `RunResult.error` for the arm it concerns."""


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
    """The last few lines of stderr, joined and capped. A cut is never silent:
    when the joined text is longer than the cap, the kept part is prefixed
    with `"... "` so a reader cannot mistake it for the whole thing."""
    text = " ".join(stderr.strip().splitlines()[-5:]).strip()
    if len(text) > STDERR_TAIL_CHARS:
        return "... " + text[-STDERR_TAIL_CHARS:]
    return text


def _exit_note(exited: str, stderr: str) -> str:
    """`exited` plus the stderr tail, or `exited` alone when there is no tail.

    Not `f"{exited}: {tail}".rstrip(": ")`: that also strips a trailing colon
    that belonged to the product's own message (`"...Error:"` -> `"...Error"`).
    """
    tail = _stderr_tail(stderr)
    return f"{exited}: {tail}" if tail else exited


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
            return Trace(error=_exit_note(exited, invocation.stderr))
        return Trace(
            output=invocation.stdout.removesuffix("\n"),
            usage_note=f"the {product.name} runner does not report token usage",
            cost_note=f"the {product.name} runner does not report cost",
        )
    trace = product.parse(invocation.stdout)
    if invocation.exit_code != 0 and (trace.error is None or not trace.complete):
        # A complete trace carrying the product's own error message is the
        # best explanation there is; otherwise the exit code and stderr are.
        return replace(trace, error=_exit_note(exited, invocation.stderr))
    return trace


def deliver_skill(skill: Skill, cwd: Path, skills_dir: str) -> bool:
    """Write the skill where the product discovers it. False when there is nothing to write.

    `SKILL.md` is `skill.markdown` byte for byte -- `newline=""` so no
    platform's text-mode translation turns a `\\n` into a `\\r\\n` on the way
    out; beside it go `scripts/`, `references/` and `assets/` from the bundle
    -- those three and nothing else, so an eval file beside `SKILL.md` never
    reaches the product. Symlinks are copied as symlinks, as `git archive`
    preserved them.

    `skill.name` is checked here, not trusted from the caller: under
    `--baseline previous` it comes from a historical `SKILL.md`'s
    frontmatter, which never passes the once-per-run preflight a candidate
    skill's name does. Raises `ProductSetupError` before writing anything
    when the name cannot be one directory entry.
    """
    if not skill.markdown:
        return False
    _check_skill_name(skill.name)
    target = cwd / skills_dir / skill.name
    target.mkdir(parents=True, exist_ok=True)
    (target / SKILL_FILENAME).write_text(skill.markdown, encoding="utf-8", newline="")
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


def _install_hint(product: Product) -> str:
    if product.name == "cli":
        return "set [runners.cli] command in skill-lens.toml to a command on PATH"
    return f"install the product, or set [runners.{product.name}] command in skill-lens.toml"


def probe_version(product: Product, executable: str, role: str = "runner") -> str:
    """Run the product's version command; the first line of what it prints.

    Executed, not merely found: a product on PATH that cannot start should be
    exit 2 up front, not one errored case per work item. `role` ("runner" or
    "judge") names the seat in the message. Raises `ProductSetupError`;
    returns "" when the product has no version command.
    """
    if product.version_command is None:
        return ""
    argv = [executable, *product.version_command[1:]]
    spoken = " ".join(argv)
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv from a preset or config, no shell
            argv,
            capture_output=True,
            timeout=VERSION_PROBE_TIMEOUT_SECONDS,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProductSetupError(f"{role} {product.name}: {spoken} could not run: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip().splitlines()
        first = detail[0] if detail else ""
        exited = f"exited with code {completed.returncode}"
        message = f"{role} {product.name}: {spoken} {exited}: {first}"
        raise ProductSetupError(message.rstrip(": "))
    lines = completed.stdout.decode("utf-8", errors="replace").strip().splitlines()
    return lines[0].strip() if lines else ""


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

        # The size refusal is checked before anything is created, so an
        # oversized task never spends a directory, a delivery or a process.
        delivered = bool(skill.markdown)
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

        private: Path | None = None
        try:
            if workspace is not None:
                cwd = workspace.root
            else:
                private = Path(tempfile.mkdtemp(prefix=PRIVATE_PREFIX)).resolve()
                cwd = private
            deliver_skill(skill, cwd, product.skills_dir)
            trace = read_trace(product, invoke(product, prompt, cwd))
        except ProductSetupError as exc:
            return RunResult(error=str(exc), latency_ms=elapsed())
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
            # None, not False, for a generic product: with no parser,
            # `trace.invoked_skills` is always empty, so "the skill did not
            # fire" would be a claim the runner has no way to back up.
            skill_triggered=(
                (skill.name in trace.invoked_skills)
                if case.mode == "offered" and product.parse is not None
                else None
            ),
        )

    def preflight(
        self, skills: list[Skill], cases_by_skill: dict[str, list[EvalCase]]
    ) -> ProductStatus:
        """Refuse, before any case runs, everything this runner cannot do.

        Called once per run by the orchestrator with the candidate-arm skills
        and cases that will run under this runner -- compatibility is a
        property of (case, runner), unlike the sandbox decision, which is
        run-wide. Raises `ProductSetupError`; returns the status the report
        carries.
        """
        product = self._product
        executable = shutil.which(product.argv[0])
        if executable is None:
            raise ProductSetupError(
                f"runner {product.name}: {product.argv[0]!r} is not on PATH; "
                f"{_install_hint(product)}"
            )
        version = probe_version(product, executable)
        for skill in skills:
            _check_skill_name(skill.name)
            for case in cases_by_skill.get(skill.name, []):
                where = f"case {case.name!r} of skill {skill.name!r}"
                if case.tools:
                    raise ProductSetupError(
                        f"{where} declares tools:, which the {product.name} runner cannot "
                        "provide -- mock tools reach only pydantic-ai and langchain"
                    )
                if product.parse is None:
                    if case.trajectory is not None:
                        raise ProductSetupError(
                            f"{where} declares trajectory:, but the cli runner records no "
                            "tool calls; use a preset (copilot, claude-code)"
                        )
                    if case.mode == "offered":
                        raise ProductSetupError(
                            f"{where} is mode: offered, but the cli runner cannot observe "
                            "whether a skill was loaded; use a preset or mode: loaded"
                        )
        return ProductStatus(
            name=product.name, executable=executable, version=version, trust=TRUST_NOTE
        )
