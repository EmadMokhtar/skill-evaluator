"""Run a script bundled with the skill under test, under guards and a sandbox.

Framework-neutral. Nothing here knows about tools or agents; it turns a policy
and a path into a `ScriptResult`, and `runners/tools.py` renders that for the
model.

Two layers of protection, and the report always says which applied:

* **Portable guards**, on every platform: the environment is rebuilt from an
  allowlist (the key that pays for the run is never in it), temporary files go
  to a scratch directory outside the workspace, a wall-clock timeout kills the
  whole process group, and output is read from files and capped with a visible
  cut.
* **An OS sandbox where one exists**: `sandbox-exec` on macOS, `bwrap` on
  Linux. It blocks the network, writes outside the workspace and the scratch
  directory, and reads of every *other* skill-lens temporary directory.

What the sandbox does not do: hide the rest of the filesystem. A script can
read what the CI user can read, and what it prints reaches the model and the
report. The sandbox is defence in depth; the `allow_scripts` opt-in is the
decision.

The allowlist stops *inheritance*; it does not stop a same-user process
asking the kernel for the harness's own environment. On Linux that is
`/proc/<pid>/environ`, which `harden_process` closes by marking the harness
non-dumpable (root ignores that; `bwrap` also puts the harness in another
PID namespace). On macOS it is the `kern.procargs2` sysctl behind `ps -E`,
which `sandbox-exec` does not gate at all -- verified against a blanket
`(deny sysctl-read)` -- so nothing here can close it.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from skill_lens.bundle import SkillBundle, script_extension
from skill_lens.models import SandboxBackend, SandboxMode, Skill
from skill_lens.process import group_kwargs, read_capped_handle, reap_and_kill_group
from skill_lens.workspace import PathRefused, Workspace

DEFAULT_INTERPRETERS: Mapping[str, tuple[str, ...]] = {"py": ("python3",), "sh": ("bash",)}
DEFAULT_TIMEOUT_SECONDS = 30.0
# Equal to evaluators/judge.py's MAX_ARTIFACT_BYTES on purpose: one number for
# "how much untrusted output reaches a model".
DEFAULT_MAX_OUTPUT_BYTES = 20_000
SCRATCH_PREFIX = "skill-lens-scratch-"
PROBE_TIMEOUT_SECONDS = 10.0

# What a script's environment may carry over from the harness. An allowlist,
# never os.environ with keys removed: the provider key skill-lens itself holds
# is absent by construction rather than by remembering to delete it.
_KEPT_ENV: tuple[str, ...] = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ")
# Python does not start on Windows without SystemRoot.
_KEPT_ENV_WINDOWS: tuple[str, ...] = ("SystemRoot", "COMSPEC", "PATHEXT", "USERPROFILE")

# prctl(2) option numbers, from <sys/prctl.h>.
_PR_SET_DUMPABLE = 4
_PR_GET_DUMPABLE = 3
HARDENING_NOTE = "harness environment hidden from same-user processes (PR_SET_DUMPABLE)"


def harden_process() -> str | None:
    """Hide this process's environment from same-user processes, where the OS can.

    The allowlist keeps the provider key out of a script's *inherited*
    environment, but on Linux any process with the same uid can read the
    harness's `/proc/<pid>/environ` -- unless the harness is non-dumpable,
    which makes its `/proc/<pid>/*` root-owned and refuses a same-user read.
    `prctl(PR_SET_DUMPABLE, 0)` does that; root (`CAP_SYS_PTRACE`) ignores
    it, and `bwrap` covers the case anyway by unsharing the PID namespace.
    The cost is that the harness can no longer be core-dumped or attached
    to by a same-user debugger, which is why it runs only when scripts are
    enabled. Best effort and never raises: a missing `ctypes`, a `prctl`
    that is absent or refuses, all read as "not applied". macOS has no
    equivalent -- see the module docstring -- so it returns None there.

    Returns the note the report prints, or None when nothing was applied.
    """
    if sys.platform != "linux":
        return None
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
            return None
        if libc.prctl(_PR_GET_DUMPABLE, 0, 0, 0, 0) != 0:
            return None
    except (ImportError, OSError, AttributeError):
        return None
    return HARDENING_NOTE


class ScriptSetupError(Exception):
    """Scripts were enabled but cannot run on this machine.

    A missing interpreter, or `script_sandbox = "required"` with no backend.
    Raised by `preflight` before any case runs -- and before any money is
    spent -- and reported as exit 2 by the CLI, like every other setup error.
    """


@dataclass(frozen=True)
class ScriptPolicy:
    """The repository's script settings, built from `Config` by the CLI."""

    sandbox: SandboxMode = "auto"
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    interpreters: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(DEFAULT_INTERPRETERS)
    )


@dataclass(frozen=True)
class SandboxStatus:
    """Which backend the probe found, and why -- or why not."""

    backend: SandboxBackend
    detail: str


@dataclass(frozen=True)
class ScriptRuntime:
    """What reaches a runner: the policy plus the once-per-run sandbox decision.

    `hardening` is `harden_process`'s note when it applied, else None; the
    console prints it after the sandbox detail so an operator can see from
    the log which protections this run actually had.
    """

    policy: ScriptPolicy
    sandbox: SandboxStatus
    hardening: str | None = None


@dataclass(frozen=True)
class ScriptResult:
    """One script call's outcome. Never an exception.

    `refused` set means nothing ran and the message says why, written for the
    model. Otherwise `exit_code` is the process's, or None with `timed_out`.
    `stdout` / `stderr` are already capped, marker included.
    """

    refused: str | None = None
    exit_code: int | None = None
    timed_out: bool = False
    stdout: str = ""
    stderr: str = ""
    workspace_warning: str | None = None


def _quoted(path: Path) -> str:
    """A path as a sandbox-profile string literal.

    A profile is a string, and a string is where injection lives: a `"` in a
    temp path is impossible on macOS, but the builder escapes anyway.
    """
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def macos_profile(workspace: Path, scratch: Path, tempdir: Path, bundle: Path) -> str:
    """The `sandbox-exec` profile: allow by default, deny the two things that
    matter, then re-allow the places the script must reach. Later rules win.

    The read denial on the temp directory is what stops a script reading the
    baseline arm's workspace under --concurrency: every workspace and scratch
    directory lives there, and only our own two are allowed back. The bundle
    is allowed back too, read-only: it needs no write access, but a baseline
    bundle is extracted under the temp directory, and under pytest so is the
    working bundle (`tmp_path`), so without an explicit allow the blanket
    temp-dir deny above would make the interpreter unable to read the very
    script it was asked to run.
    """
    return "\n".join(
        [
            "(version 1)",
            "(allow default)",
            "(deny network*)",
            "(deny file-write*)",
            f"(allow file-write* (subpath {_quoted(workspace)}) (subpath {_quoted(scratch)}))",
            '(allow file-write-data (literal "/dev/null"))',
            f"(deny file-read* (subpath {_quoted(tempdir)}))",
            f"(allow file-read* (subpath {_quoted(workspace)}) (subpath {_quoted(scratch)}) "
            f"(subpath {_quoted(bundle)}))",
        ]
    )


def bwrap_argv(
    workspace: Path, scratch: Path, tempdir: Path, argv: Sequence[str], bundle: Path
) -> list[str]:
    """The `bwrap` command line: the whole filesystem read-only, the temp
    directory replaced by an empty tmpfs so sibling workspaces vanish, our two
    writable directories and the read-only bundle bound back in, no network,
    and `--die-with-parent` so a killed harness takes the script with it.
    Order matters: the tmpfs must be mounted before the binds it would
    otherwise hide.
    """
    return [
        "bwrap",
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", str(tempdir),
        "--bind", str(workspace), str(workspace),
        "--bind", str(scratch), str(scratch),
        "--ro-bind", str(bundle), str(bundle),
        "--unshare-net",
        "--unshare-pid",
        "--die-with-parent",
        "--new-session",
        "--",
        *argv,
    ]  # fmt: skip


def wrap(
    backend: SandboxBackend,
    argv: Sequence[str],
    workspace: Path,
    scratch: Path,
    tempdir: Path,
    bundle: Path,
) -> list[str]:
    """argv, wrapped in the backend's launcher -- or unchanged for `none`."""
    if backend == "sandbox-exec":
        return [
            "sandbox-exec",
            "-p",
            macos_profile(workspace, scratch, tempdir, bundle),
            *argv,
        ]
    if backend == "bwrap":
        return bwrap_argv(workspace, scratch, tempdir, argv, bundle)
    return list(argv)


def _bwrap_probe_argv() -> list[str]:
    return [
        "bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
        "--unshare-net", "--unshare-pid", "--die-with-parent", "--", "/bin/true",
    ]  # fmt: skip


def _probe(backend: SandboxBackend, argv: list[str], run: Callable[..., Any]) -> SandboxStatus:
    """Run the backend once. Present is not the same as working."""
    try:
        completed = run(argv, capture_output=True, timeout=PROBE_TIMEOUT_SECONDS, check=False)  # noqa: S603 - argv is internally constructed, not user input
    except (OSError, subprocess.TimeoutExpired) as exc:
        return SandboxStatus("none", f"{backend} probe failed: {exc}")
    if completed.returncode != 0:
        lines = completed.stderr.decode("utf-8", errors="replace").strip().splitlines()
        reason = lines[0] if lines else f"exit {completed.returncode}"
        return SandboxStatus("none", f"{backend} probe failed: {reason}")
    return SandboxStatus(backend, f"{backend} probe succeeded")


def probe_sandbox(
    mode: SandboxMode,
    *,
    system: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., Any] = subprocess.run,
) -> SandboxStatus:
    """Decide the backend for this run, empirically.

    `system`, `which` and `run` are injectable so the decision table is
    testable on every platform; the defaults are the real thing.
    """
    if mode == "off":
        return SandboxStatus("none", 'script_sandbox = "off"')
    system = platform.system() if system is None else system
    if system == "Darwin":
        if which("sandbox-exec") is None:
            return SandboxStatus("none", "sandbox-exec not found on PATH")
        # A profile is a quoted string; a `"` in the temp path would close the
        # literal early and corrupt every rule after it. Checked before any
        # process is spawned, empirical probe included.
        if '"' in str(Path(tempfile.gettempdir()).resolve()):
            return SandboxStatus(
                "none",
                "temp directory path contains a double quote, which a sandbox-exec "
                "profile cannot express",
            )
        argv = ["sandbox-exec", "-p", "(version 1)(allow default)(deny network*)", "/usr/bin/true"]
        return _probe("sandbox-exec", argv, run)
    if system == "Linux":
        if which("bwrap") is None:
            return SandboxStatus("none", "bwrap not found on PATH")
        return _probe("bwrap", _bwrap_probe_argv(), run)
    return SandboxStatus("none", f"no sandbox backend on {system}")


def script_environment(
    scratch: Path, *, parent: Mapping[str, str] | None = None, windows: bool | None = None
) -> dict[str, str]:
    """The environment a script runs in: rebuilt from an allowlist.

    `parent` and `windows` are injectable for tests; the defaults are the real
    process environment and platform.
    """
    parent = os.environ if parent is None else parent
    windows = os.name == "nt" if windows is None else windows
    keys = _KEPT_ENV + (_KEPT_ENV_WINDOWS if windows else ())
    env = {key: parent[key] for key in keys if key in parent}
    env.update(
        TMPDIR=str(scratch),
        TMP=str(scratch),
        TEMP=str(scratch),
        # The bundle is read-only under the sandbox; without this every import
        # would fill stderr with __pycache__ write errors.
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONIOENCODING="utf-8",
    )
    return env


def preflight(
    skills: Sequence[Skill],
    policy: ScriptPolicy,
    *,
    system: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., Any] = subprocess.run,
) -> ScriptRuntime:
    """Once per run, after discovery, before any case: can scripts run here?

    Every candidate script whose extension is mapped needs its interpreter on
    PATH; `required` needs a backend. Both fail before any money is spent. A
    file under `scripts/` with an unmapped extension is not an error -- it is
    simply not runnable. Baseline bundles are resolved later, per case, so
    `run_script` looks an interpreter up again at call time and refuses (not
    raises) if it is gone. Last, `harden_process` hides the harness's own
    environment from same-user processes where the OS allows it, and the
    runtime records whether that applied.
    """
    for skill in skills:
        if skill.bundle_root is None:
            continue
        bundle = SkillBundle(skill.bundle_root)
        for relative in bundle.scripts():
            argv = policy.interpreters.get(script_extension(relative))
            if argv is None:
                continue
            if which(argv[0]) is None:
                raise ScriptSetupError(
                    f"{skill.bundle_root / relative} needs {argv[0]}, which is not on PATH"
                )
    status = probe_sandbox(policy.sandbox, system=system, which=which, run=run)
    if policy.sandbox == "required" and status.backend == "none":
        raise ScriptSetupError(
            f'script_sandbox = "required" but no sandbox is available: {status.detail}'
        )
    # Only now, once scripts are certain to run: hardening costs the harness
    # its core dumps and debugger access, so a run that stops here for a
    # missing sandbox or interpreter never pays it.
    return ScriptRuntime(policy=policy, sandbox=status, hardening=harden_process())


def run_script(
    bundle: SkillBundle,
    workspace: Workspace,
    candidate: str,
    args: Sequence[object],
    runtime: ScriptRuntime,
) -> ScriptResult:
    """Run one bundled script with the workspace as its working directory.

    Never raises: every outcome -- including an interpreter that will not
    start, an argument that embeds a NUL byte, or a scratch directory that
    cannot be written to -- is a `ScriptResult`. Output goes to files in the
    scratch directory, not into memory -- a script printing gigabytes inside
    the timeout must not take the harness down with it -- and is read back
    capped, through the same handle the child wrote through (see
    `read_capped_handle`).
    """
    policy = runtime.policy
    try:
        target = bundle.script(candidate, policy.interpreters)
    except PathRefused as exc:
        return ScriptResult(refused=str(exc))
    prefix = policy.interpreters[script_extension(candidate)]
    interpreter = shutil.which(prefix[0])
    if interpreter is None:
        return ScriptResult(
            refused=f"refused: {candidate.strip()} needs {prefix[0]}, which is not on PATH"
        )
    argv = [interpreter, *prefix[1:], str(target), *(str(argument) for argument in args)]

    try:
        scratch = Path(tempfile.mkdtemp(prefix=SCRATCH_PREFIX)).resolve()
    except OSError as exc:
        return ScriptResult(refused=f"refused: cannot create a scratch directory: {exc}")
    try:
        tempdir = Path(tempfile.gettempdir()).resolve()
        wrapped = wrap(runtime.sandbox.backend, argv, workspace.root, scratch, tempdir, bundle.root)
        stdout_path = scratch / "stdout"
        stderr_path = scratch / "stderr"
        with ExitStack() as stack:
            try:
                # w+b, not wb: read back through these same handles below,
                # never by reopening the path -- see _read_capped_handle.
                stdout_handle = stack.enter_context(stdout_path.open("w+b"))
                stderr_handle = stack.enter_context(stderr_path.open("w+b"))
            except OSError as exc:
                return ScriptResult(refused=f"refused: cannot create the capture files: {exc}")
            try:
                process = subprocess.Popen(  # noqa: S603 - argv list, shell=False, nothing joined
                    wrapped,
                    cwd=workspace.root,
                    env=script_environment(scratch),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    **group_kwargs(),
                )
            except (OSError, ValueError) as exc:
                # ValueError: an argv entry with an embedded NUL byte. A
                # model can put one in a tool-call argument; Popen rejects it
                # before anything is spawned.
                return ScriptResult(refused=f"refused: cannot start {prefix[0]}: {exc}")
            exit_code, timed_out = reap_and_kill_group(process, policy.timeout_seconds)
            return ScriptResult(
                exit_code=exit_code,
                timed_out=timed_out,
                stdout=read_capped_handle(stdout_handle, policy.max_output_bytes),
                stderr=read_capped_handle(stderr_handle, policy.max_output_bytes),
                workspace_warning=workspace.over_limit(),
            )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
