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
"""

from __future__ import annotations

import os  # noqa: F401 - used by Task 6
import platform
import shutil
import signal  # noqa: F401 - used by Task 6
import subprocess
import tempfile  # noqa: F401 - used by Task 6
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field  # noqa: F401 - used by Task 6
from pathlib import Path
from typing import Any

from skill_lens.bundle import SkillBundle, script_extension  # noqa: F401 - used by Task 6
from skill_lens.models import SandboxBackend, SandboxMode, Skill  # noqa: F401 - used by Task 6
from skill_lens.workspace import PathRefused, Workspace  # noqa: F401 - used by Task 6

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
    """What reaches a runner: the policy plus the once-per-run sandbox decision."""

    policy: ScriptPolicy
    sandbox: SandboxStatus


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


def macos_profile(workspace: Path, scratch: Path, tempdir: Path) -> str:
    """The `sandbox-exec` profile: allow by default, deny the two things that
    matter, then re-allow the places the script must reach. Later rules win.

    The read denial on the temp directory is what stops a script reading the
    baseline arm's workspace under --concurrency: every workspace and scratch
    directory lives there, and only our own two are allowed back.
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
            f"(allow file-read* (subpath {_quoted(workspace)}) (subpath {_quoted(scratch)}))",
        ]
    )


def bwrap_argv(workspace: Path, scratch: Path, tempdir: Path, argv: Sequence[str]) -> list[str]:
    """The `bwrap` command line: the whole filesystem read-only, the temp
    directory replaced by an empty tmpfs so sibling workspaces vanish, our two
    writable directories bound back in, no network, and `--die-with-parent`
    so a killed harness takes the script with it. Order matters: the tmpfs
    must be mounted before the binds it would otherwise hide.
    """
    return [
        "bwrap",
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", str(tempdir),
        "--bind", str(workspace), str(workspace),
        "--bind", str(scratch), str(scratch),
        "--unshare-net",
        "--unshare-pid",
        "--die-with-parent",
        "--new-session",
        "--",
        *argv,
    ]  # fmt: skip


def wrap(
    backend: SandboxBackend, argv: Sequence[str], workspace: Path, scratch: Path, tempdir: Path
) -> list[str]:
    """argv, wrapped in the backend's launcher -- or unchanged for `none`."""
    if backend == "sandbox-exec":
        return ["sandbox-exec", "-p", macos_profile(workspace, scratch, tempdir), *argv]
    if backend == "bwrap":
        return bwrap_argv(workspace, scratch, tempdir, argv)
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
        argv = ["sandbox-exec", "-p", "(version 1)(allow default)(deny network*)", "/usr/bin/true"]
        return _probe("sandbox-exec", argv, run)
    if system == "Linux":
        if which("bwrap") is None:
            return SandboxStatus("none", "bwrap not found on PATH")
        return _probe("bwrap", _bwrap_probe_argv(), run)
    return SandboxStatus("none", f"no sandbox backend on {system}")
